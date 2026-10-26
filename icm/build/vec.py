"""The semantic index: one vector per chunk of every hadith's matn (v02 and the KG), found by meaning.

    python -m icm build vec --model intfloat/multilingual-e5-base [--limit N] [--out DIR] [--resume]

Laptop-side (needs torch + transformers: conda env dev2; an Apple GPU is used when there is one). Steps, each resumable:

  1. ``chunks.sqlite``  every hadith's matn (diacritics and tags removed, its own letters kept), the same text of several
                        hadiths once (``dup``), cut into chunks of at most ``CHUNK_WORDS`` words (stride ``STRIDE_WORDS``)
  2. ``raw/*.npz``      float16 vectors of the chunks, longest last, ``BATCH`` at a time
  3. the index          int8 vectors with a scale each, in the order of the lists of an IVF (k-means over a sample,
                        ``n_lists`` centroids): ``vec.i8 scale.f16 key.i64 cstart.u16 cent.f32 lists.i64 dups.npy meta.json``
  4. ``model/``         the encoder as ``weights.bin`` (float16) + tokenizer, for ``icm.embed_np`` on the server

``key`` is the hadith's number, negative for the KG. Ship ``VEC_DIR`` (all but ``raw/`` and ``chunks.sqlite``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import zlib
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

from .. import config
from ..text import norm, strip_tags

CHUNK_WORDS = 64
STRIDE_WORDS = 48
MAX_CHUNKS = 24
MAX_LEN = 256                 # tokens
BATCH = 64
_HAR = re.compile("[ً-ٰٟـۖ-ۭ]")


def emb_text(raw: str) -> str:
    """The text a model reads: tags, diacritics and tatweel removed, spaces collapsed; the letters stay as written (the
    models were trained on ordinary Arabic, not on ``norm``'s Persian-letter spelling)."""
    t = _HAR.sub("", strip_tags(raw or "")).replace("‌", " ")
    return re.sub(r"\s+", " ", t).strip()


def chunk_text(t: str) -> List[Tuple[int, str]]:
    """[(first word, text)] of ``t``: the whole text when it has at most ``CHUNK_WORDS`` words, else overlapping windows."""
    w = t.split(" ")
    if len(w) <= CHUNK_WORDS:
        return [(0, t)] if t else []
    out, s = [], 0
    while s < len(w) and len(out) < MAX_CHUNKS:
        out.append((s, " ".join(w[s:s + CHUNK_WORDS])))
        if s + CHUNK_WORDS >= len(w):
            break
        s += STRIDE_WORDS
    return out


# ---------------------------------------------------------------------------
# 1. chunks
# ---------------------------------------------------------------------------
def _rows(limit: Optional[int]) -> Iterable[Tuple[int, str]]:
    con = sqlite3.connect(f"file:{config.V02_DB}?mode=ro", uri=True)
    q = "select id, matn from hadith where id glob '[0-9]*' and id not glob '*[^0-9]*'" + (f" limit {int(limit)}" if limit else "")
    for i, m in con.execute(q):
        yield int(i), m or ""
    if os.path.exists(config.KG_DB):
        con = sqlite3.connect(f"file:{config.KG_DB}?mode=ro", uri=True)
        for i, z in con.execute("select id, matn_z from kg_hadith" + (f" limit {int(limit)}" if limit else "")):
            yield -int(i), zlib.decompress(z).decode()


def make_chunks(work: str, limit: Optional[int]) -> int:
    path = os.path.join(work, "chunks.sqlite")
    if os.path.exists(path):
        return sqlite3.connect(path).execute("select count(*) from ch").fetchone()[0]
    con = sqlite3.connect(path + ".part")
    con.execute("create table ch(i integer primary key, key integer, cstart integer, nwords integer, text text)")
    con.execute("create table dup(key integer, canon integer)")
    seen: Dict[bytes, int] = {}
    n = nd = 0
    buf: list = []
    for key, matn in _rows(limit):
        t = emb_text(matn)
        if len(t) < 8:
            continue
        h = hashlib.sha1(" ".join(norm(matn).split()).encode()).digest()[:12]
        if h in seen:
            con.execute("insert into dup values (?,?)", (key, seen[h]))
            nd += 1
            continue
        seen[h] = key
        for cs, ct in chunk_text(t):
            buf.append((n, key, cs, len(ct.split(" ")), ct))
            n += 1
        if len(buf) >= 20000:
            con.executemany("insert into ch values (?,?,?,?,?)", buf)
            buf = []
    con.executemany("insert into ch values (?,?,?,?,?)", buf)
    con.execute("create index ch_len on ch(nwords, i)")
    con.commit()
    con.close()
    os.replace(path + ".part", path)
    print(f"chunks: {n} (duplicates of another hadith's text: {nd})", flush=True)
    return n


# ---------------------------------------------------------------------------
# 2. vectors
# ---------------------------------------------------------------------------
class TorchEncoder:
    """The corpus-side encoder (torch). ``kind``: e5 (mean pooling, query:/passage: prefixes), cls (CLS pooling)."""

    def __init__(self, name: str):
        import torch
        from transformers import AutoModel, AutoTokenizer
        self.torch = torch
        self.name = name
        self.dev = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
        self.tok = AutoTokenizer.from_pretrained(name)
        self.m = AutoModel.from_pretrained(name, dtype=torch.float16 if self.dev != "cpu" else torch.float32).to(self.dev).eval()
        self.kind = "e5" if "e5" in name else "cls"
        self.doc_prefix = "passage: " if self.kind == "e5" else ""
        self.query_prefix = "query: " if self.kind == "e5" else ""

    def encode(self, texts: List[str], prefix: str = "") -> np.ndarray:
        torch = self.torch
        with torch.no_grad():
            b = self.tok([prefix + t for t in texts], padding=True, truncation=True, max_length=MAX_LEN, return_tensors="pt").to(self.dev)
            h = self.m(**b).last_hidden_state
            if self.kind == "e5":
                m = b["attention_mask"].unsqueeze(-1).to(h.dtype)
                v = (h * m).sum(1) / m.sum(1)
            else:
                v = h[:, 0]
            return torch.nn.functional.normalize(v.float(), dim=-1).cpu().numpy().astype(np.float16)


def embed_chunks(work: str, enc: TorchEncoder, n: int) -> None:
    raw = os.path.join(work, "raw")
    os.makedirs(raw, exist_ok=True)
    con = sqlite3.connect(os.path.join(work, "chunks.sqlite"))
    shard = 4096 * 8
    done = len([f for f in os.listdir(raw) if f.endswith(".npz")]) * shard
    t0 = time.time()
    cur = con.execute("select i, text from ch order by nwords, i limit -1 offset ?", (done,))
    while True:
        rows = cur.fetchmany(shard)
        if not rows:
            break
        ids = np.array([r[0] for r in rows], dtype=np.int64)
        out = np.zeros((len(rows), enc.m.config.hidden_size), dtype=np.float16)
        for s in range(0, len(rows), BATCH):
            out[s:s + BATCH] = enc.encode([r[1] for r in rows[s:s + BATCH]], enc.doc_prefix)
        np.savez(os.path.join(raw, f"{done // shard:05d}.npz"), i=ids, v=out)
        done += len(rows)
        print(f"  embedded {done}/{n} {time.time() - t0:.0f}s", flush=True)


# ---------------------------------------------------------------------------
# 3. the index
# ---------------------------------------------------------------------------
def kmeans(x: np.ndarray, k: int, iters: int = 20, seed: int = 0) -> np.ndarray:
    """Spherical k-means (unit vectors, cosine)."""
    import torch
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    rng = np.random.default_rng(seed)
    c = torch.tensor(x[rng.choice(len(x), k, replace=False)], device=dev, dtype=torch.float32)
    X = torch.tensor(x, device=dev, dtype=torch.float32)
    for it in range(iters):
        a = torch.cat([(X[s:s + 50000] @ c.T).argmax(1) for s in range(0, len(X), 50000)])
        s_ = torch.zeros_like(c).index_add_(0, a, X)
        cnt = torch.bincount(a, minlength=k)
        empty = cnt == 0
        s_[empty] = X[torch.randint(0, len(X), (int(empty.sum()),), device=dev)] if empty.any() else s_[empty]
        c = torch.nn.functional.normalize(s_, dim=-1)
        print(f"  kmeans {it + 1}/{iters}", flush=True)
    return c.cpu().numpy()


def build_index(work: str, out: str, n_lists: Optional[int] = None) -> dict:
    raw = os.path.join(work, "raw")
    parts = [np.load(os.path.join(raw, f)) for f in sorted(os.listdir(raw)) if f.endswith(".npz")]
    ids = np.concatenate([p["i"] for p in parts])
    V = np.concatenate([p["v"] for p in parts])
    order = np.argsort(ids)
    ids, V = ids[order], V[order]
    n, d = V.shape
    print(f"{n} vectors x {d}", flush=True)
    con = sqlite3.connect(os.path.join(work, "chunks.sqlite"))
    meta = {i: (k, c) for i, k, c in con.execute("select i, key, cstart from ch")}
    key = np.array([meta[int(i)][0] for i in ids], dtype=np.int64)
    cst = np.array([meta[int(i)][1] for i in ids], dtype=np.uint16)
    dups = np.array(con.execute("select key, canon from dup").fetchall(), dtype=np.int64).reshape(-1, 2)
    k = n_lists or int(min(4096, max(16, 2 * (n ** 0.5))))
    X = V.astype(np.float32)
    samp = X[np.random.default_rng(0).choice(n, min(n, 300000), replace=False)]
    cent = kmeans(samp, k)
    import torch
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    C = torch.tensor(cent, device=dev)
    asg = np.concatenate([(torch.tensor(X[s:s + 50000], device=dev) @ C.T).argmax(1).cpu().numpy() for s in range(0, n, 50000)])
    o = np.argsort(asg, kind="stable")
    lists = np.zeros(k + 1, dtype=np.int64)
    np.cumsum(np.bincount(asg, minlength=k), out=lists[1:])
    X = X[o]
    scale = np.abs(X).max(1) / 127.0
    q = np.round(X / scale[:, None]).astype(np.int8)
    os.makedirs(out, exist_ok=True)
    q.tofile(os.path.join(out, "vec.i8"))
    scale.astype(np.float16).tofile(os.path.join(out, "scale.f16"))
    key[o].tofile(os.path.join(out, "key.i64"))
    cst[o].tofile(os.path.join(out, "cstart.u16"))
    cent.astype(np.float32).tofile(os.path.join(out, "cent.f32"))
    lists.tofile(os.path.join(out, "lists.i64"))
    np.save(os.path.join(out, "dups.npy"), dups)
    return dict(n=int(n), dim=int(d), lists=int(k), dups=int(len(dups)))


# ---------------------------------------------------------------------------
# 4. the model for the server
# ---------------------------------------------------------------------------
def export_model(name: str, out: str, pooling: str) -> None:
    """``weights.bin`` (float16 tensors of the encoder, laid end to end) + ``weights.json`` {tensor: [offset, shape]},
    ``model.json`` (sizes, pooling, prefixes) and the tokenizer, for ``icm.embed_np.NpEncoder``."""
    import torch
    from huggingface_hub import hf_hub_download
    from transformers import AutoConfig, AutoModel
    os.makedirs(out, exist_ok=True)
    m = AutoModel.from_pretrained(name, dtype=torch.float16)
    cfg = AutoConfig.from_pretrained(name)
    idx, off = {}, 0
    with open(os.path.join(out, "weights.bin"), "wb") as f:
        for k, t in m.state_dict().items():
            if k.startswith("pooler") or "position_ids" in k:
                continue
            a = t.detach().cpu().numpy().astype(np.float16)
            f.write(a.tobytes())
            idx[k] = [off, list(a.shape)]
            off += a.nbytes
    json.dump(idx, open(os.path.join(out, "weights.json"), "w"))
    kind = "e5" if "e5" in name else "cls"
    json.dump(dict(hidden=cfg.hidden_size, layers=cfg.num_hidden_layers, heads=cfg.num_attention_heads, eps=cfg.layer_norm_eps,
                   pad_id=cfg.pad_token_id, arch="xlmr" if cfg.model_type == "xlm-roberta" else "bert", pooling=pooling, max_len=MAX_LEN,
                   query_prefix="query: " if kind == "e5" else "", model=name), open(os.path.join(out, "model.json"), "w"))
    shutil.copy(hf_hub_download(name, "tokenizer.json"), os.path.join(out, "tokenizer.json"))


def run(model: str, out: Optional[str] = None, limit: Optional[int] = None, work: Optional[str] = None) -> None:
    out = out or config.VEC_DIR
    work = work or out + ".work"
    os.makedirs(work, exist_ok=True)
    t0 = time.time()
    n = make_chunks(work, limit)
    enc = TorchEncoder(model)
    embed_chunks(work, enc, n)
    info = build_index(work, out)
    pooling = "mean" if enc.kind == "e5" else "cls"
    export_model(model, os.path.join(out, "model"), pooling)
    json.dump(dict(info, model=model, pooling=pooling, query_prefix=enc.query_prefix, chunk_words=CHUNK_WORDS, stride_words=STRIDE_WORDS,
                   built=time.strftime("%Y-%m-%d %H:%M"), limit=limit or 0, seconds=round(time.time() - t0)),
              open(os.path.join(out, "meta.json"), "w"), indent=1)
    print(open(os.path.join(out, "meta.json")).read())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--work")
    a = ap.parse_args()
    run(a.model, a.out, a.limit, a.work)
