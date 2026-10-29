"""The semantic index of TEXT PARTS: one vector per distinct part of every hadith's matn (v02 and the KG), found by meaning.

    python -m icm build parts --model BAAI/bge-m3 [--limit N] [--out DIR] [--lists 8192] [--pca 256] [--pilot-only]

Laptop-side (torch + transformers: conda env dev3; an Apple GPU is used when there is one). Steps, each resumable:

  1. ``parts.sqlite``  every hadith cut into parts: hadith.ai's segments (v02 hadiths it covers), the rule cut
                       (``text.clauses``), the union of both (the cut step 2 proposes), the whole matn of a short hadith;
                       the same text (normalised) once (``part``), where it stands in which hadith (``occ``)
  2. ``pca.f32``       bge-m3 vectors of a sample of the parts -> principal axes: vectors are stored 256-d, not 1024-d
  3. ``raw/*.npz``     float16 projected vectors of the parts, longest last
  4. the index         int8 vectors with a scale each, in the order of the lists of an IVF (k-means over a sample):
                       ``vec.i8 scale.f16 key.i64 cent.f32 lists.i64 meta.json`` (``key`` = the part's id)
  5. ``model/``        the encoder for ``icm.embed_np`` on the server

Ship ``VEC_DIR`` (all but ``raw/`` and ``pilot.npz``): vectors + ``parts.sqlite`` + ``pca.f32`` + ``model/``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import shutil
import sqlite3
import time
import zlib
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .. import config
from ..text import clauses, strip_tags, token_offsets, union_cuts
from . import vec

MIN_TOK = 3                   # a part has at least this many words
WHOLE_MAX = 20                # a hadith of at most this many words is also one part as a whole
SRC = {"hai": 0, "rule": 1, "whole": 2, "union": 3}
SHARD = 32768
PILOT = 300_000


# ---------------------------------------------------------------------------
# 1. parts
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


def cut_hadith(key: int, matn: str, hai: Optional[tuple] = None) -> Tuple[List[str], List[Tuple[int, int, int]]]:
    """``(tokens, [(a, b, src)])``: the parts of a hadith as token ranges [a, b) of ``text.tokens(matn)`` (the words the wizard
    cuts). ``hai``: ``hadithai.parts(hid, tokens)`` of the hadith (None when it has none); looked up here for a v02 hadith when
    not given. A part seen by several cuts is kept once, under the first of: hadith.ai, rules, whole text, union."""
    tok, rule = clauses(matn)
    if len(tok) < MIN_TOK:
        return tok, []
    if hai is None and key > 0:
        from .. import hadithai
        hai = hadithai.parts(key, tok)
    found: Dict[Tuple[int, int], int] = {}

    def add(spans, src):
        for a, b in spans:
            if b - a >= MIN_TOK:
                found.setdefault((int(a), int(b)), SRC[src])
    if hai:
        add(hai[0], "hai")
    add(rule, "rule")
    if len(tok) <= WHOLE_MAX:
        add([(0, len(tok))], "whole")
    if hai:
        add(union_cuts(tok, rule, hai[0]), "union")
    return tok, sorted((a, b, s) for (a, b), s in found.items())


def _cut_batch(batch: Sequence[Tuple[int, str]]):
    """Worker: ``[(key, [(a, b, src, embedded text, sha1 of the normalised words)])]`` for a batch of hadiths."""
    out = []
    for key, matn in batch:
        try:
            tok, parts = cut_hadith(key, matn)
        except Exception:                                   # one broken matn must not stop the build
            continue
        if not parts:
            continue
        st, offs = strip_tags(matn), token_offsets(matn)
        row = []
        for a, b, src in parts:
            if b > len(offs):
                continue
            t = vec.emb_text(st[offs[a][0]:offs[b - 1][1]])
            row.append((a, b, src, t, hashlib.sha1(" ".join(tok[a:b]).encode()).digest()[:12]))
        out.append((key, row))
    return out


def _batches(rows: Iterable[Tuple[int, str]], n: int = 400):
    buf = []
    for r in rows:
        buf.append(r)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def make_parts(work: str, limit: Optional[int], procs: int = 6, rows: Optional[Iterable[Tuple[int, str]]] = None) -> int:
    path = os.path.join(work, "parts.sqlite")
    if os.path.exists(path):
        return sqlite3.connect(path).execute("select count(*) from part").fetchone()[0]
    con = sqlite3.connect(path + ".part")
    con.executescript("pragma journal_mode=off; pragma synchronous=off;"
                      "create table part(id integer primary key, text text, ntok integer);"
                      "create table occ(part integer, key integer, a integer, b integer, src integer, primary key(part, key, a)) without rowid;"
                      "create table meta(k text primary key, v text);")
    seen: Dict[bytes, int] = {}
    n = n_occ = n_h = 0
    pb: list = []
    ob: list = []
    t0 = time.time()

    def flush():
        con.executemany("insert into part values (?,?,?)", pb)
        con.executemany("insert or ignore into occ values (?,?,?,?,?)", ob)
        pb.clear()
        ob.clear()
    src_rows = rows if rows is not None else _rows(limit)
    pool = mp.get_context("fork").Pool(procs) if procs > 1 else None
    it = pool.imap(_cut_batch, _batches(src_rows), 2) if pool else map(_cut_batch, _batches(src_rows))
    for done in it:
        for key, row in done:
            n_h += 1
            for a, b, src, text, h in row:
                pid = seen.get(h)
                if pid is None:
                    pid = seen[h] = n
                    pb.append((n, text, b - a))
                    n += 1
                ob.append((pid, key, a, b, src))
                n_occ += 1
        if len(pb) >= 20000:
            flush()
        if n_h % 100000 < 400 and done:
            print(f"  cut {n_h} hadiths, {n} parts, {n_occ} occurrences, {time.time() - t0:.0f}s", flush=True)
    flush()
    if pool:
        pool.close()
        pool.join()
    con.execute("create index part_ntok on part(ntok, id)")
    con.executemany("insert into meta values (?,?)", [("n_hadiths", str(n_h)), ("n_parts", str(n)), ("n_occ", str(n_occ)),
                                                      ("min_tok", str(MIN_TOK)), ("whole_max", str(WHOLE_MAX)), ("built", time.strftime("%Y-%m-%d %H:%M"))])
    con.commit()
    con.execute("vacuum")
    con.close()
    os.replace(path + ".part", path)
    print(f"parts: {n} distinct in {n_h} hadiths ({n_occ} occurrences)", flush=True)
    return n


# ---------------------------------------------------------------------------
# 2. PCA
# ---------------------------------------------------------------------------
def fit_pca(work: str, enc, dim: int = 256, n: int = PILOT) -> Tuple[np.ndarray, np.ndarray]:
    """The principal axes of a sample of the parts' 1024-d vectors: ``(mean, W)`` with W (dim, 1024); kept in ``pca.f32``."""
    path = os.path.join(work, "pca.f32")
    if os.path.exists(path):
        a = np.fromfile(path, dtype=np.float32)
        d0 = len(a) // (dim + 1)
        return a[:d0], a[d0:].reshape(dim, d0)
    con = sqlite3.connect(os.path.join(work, "parts.sqlite"))
    total = con.execute("select count(*) from part").fetchone()[0]
    ids = np.sort(np.random.default_rng(0).choice(total, min(n, total), replace=False))
    pil = os.path.join(work, "pilot.npz")
    if os.path.exists(pil):
        V = np.load(pil)["v"]
    else:
        texts = [con.execute("select text from part where id=?", (int(i),)).fetchone()[0] for i in ids]
        order = np.argsort([len(t) for t in texts], kind="stable")
        V = np.zeros((len(texts), enc.m.config.hidden_size), dtype=np.float16)
        t0 = time.time()
        for s in range(0, len(order), 256):
            ix = order[s:s + 256]
            V[ix] = enc.encode([texts[j] for j in ix], enc.doc_prefix)
            if (s // 256) % 100 == 0:
                print(f"  pilot {s}/{len(order)} {time.time() - t0:.0f}s", flush=True)
        np.savez(pil, i=ids, v=V)
    X = V.astype(np.float32)
    mean = X.mean(0)
    X -= mean
    cov = X.T @ X / len(X)
    w, E = np.linalg.eigh(cov)
    top = np.argsort(w)[::-1][:dim]
    W = E[:, top].T.astype(np.float32)
    print(f"  pca: {dim} of {X.shape[1]} axes keep {w[top].sum() / w.sum():.3f} of the variance", flush=True)
    np.concatenate([mean.astype(np.float32), W.flatten()]).tofile(path)
    return mean.astype(np.float32), W


def pca_check(work: str, mean: np.ndarray, W: np.ndarray, nq: int = 2000, k: int = 40) -> dict:
    """How much of the exact top-``k`` (cosine, 1024-d) among the pilot vectors the 256-d vectors keep, for ``nq`` pilot vectors as queries."""
    V = np.load(os.path.join(work, "pilot.npz"))["v"].astype(np.float32)
    P = vec.project(V, mean, W)
    q = np.random.default_rng(1).choice(len(V), nq, replace=False)
    r = []
    for s in range(0, nq, 500):
        a = V[q[s:s + 500]] @ V.T
        b = P[q[s:s + 500]] @ P.T
        for x, y in zip(a, b):
            ta, tb = set(np.argpartition(-x, k + 1)[:k + 1]), set(np.argpartition(-y, k + 1)[:k + 1])
            r.append(len(ta & tb) / (k + 1))
    out = dict(recall_at_k=round(float(np.mean(r)), 3), k=k, queries=nq, pilot=len(V))
    print("  pca check:", out, flush=True)
    return out


# ---------------------------------------------------------------------------
# 3. vectors
# ---------------------------------------------------------------------------
def embed_parts(work: str, enc, n: int, mean: np.ndarray, W: np.ndarray) -> None:
    raw = os.path.join(work, "raw")
    os.makedirs(raw, exist_ok=True)
    con = sqlite3.connect(os.path.join(work, "parts.sqlite"))
    done = len([f for f in os.listdir(raw) if f.endswith(".npz")]) * SHARD
    t0 = time.time()
    cur = con.execute("select id, text, ntok from part order by ntok, id limit -1 offset ?", (done,))
    d0 = done
    while True:
        rows = cur.fetchmany(SHARD)
        if not rows:
            break
        ids = np.array([r[0] for r in rows], dtype=np.int64)
        out = np.zeros((len(rows), W.shape[0] if mean is not None else enc.m.config.hidden_size), dtype=np.float16)
        s = 0
        while s < len(rows):
            bs = 256 if rows[s][2] <= 12 else 128
            v = enc.encode([r[1] for r in rows[s:s + bs]], enc.doc_prefix).astype(np.float32)
            out[s:s + bs] = vec.project(v, mean, W)
            s += bs
        np.savez(os.path.join(raw, f"{done // SHARD:05d}.npz"), i=ids, v=out)
        done += len(rows)
        el = time.time() - t0
        print(f"  embedded {done}/{n} {el:.0f}s (left ~{el / max(done - d0, 1) * (n - done) / 3600:.1f} h)", flush=True)


# ---------------------------------------------------------------------------
# 3b. a part's text trimmed after the fact (``vec.emb_text`` now trims punctuation at both ends)
# ---------------------------------------------------------------------------
def retrim(work: str, enc, mean: np.ndarray, W: np.ndarray) -> int:
    """Bring a finished ``work`` folder (parts.sqlite + raw shards) up to the trimmed text: the parts whose ``vec.emb_text`` differs
    from the stored text are embedded again (kept in ``retrim/`` shard by shard, so a stopped run goes on), their rows in the raw
    shards are replaced, and the stored text is updated. Returns the number of parts changed; run ``build_index`` afterwards."""
    done_mark = os.path.join(work, "retrim.done")
    if os.path.exists(done_mark):
        return int(open(done_mark).read() or 0)
    db = os.path.join(work, "parts.sqlite")
    con = sqlite3.connect(db)
    changed = [(i, new) for i, t in con.execute("select id, text from part") for new in [vec.emb_text(t)] if new != t]
    print(f"retrim: {len(changed)} of the parts have punctuation at an end", flush=True)
    changed.sort(key=lambda r: (len(r[1]), r[0]))                    # same lengths together (fast batches), a fixed order (resumable)
    rd = os.path.join(work, "retrim")
    os.makedirs(rd, exist_ok=True)
    t0 = time.time()
    for k in range(0, len(changed), SHARD):
        f = os.path.join(rd, f"{k // SHARD:05d}.npz")
        if os.path.exists(f):
            continue
        rows = changed[k:k + SHARD]
        out = np.zeros((len(rows), W.shape[0] if mean is not None else enc.m.config.hidden_size), dtype=np.float16)
        s = 0
        while s < len(rows):
            bs = 256 if len(rows[s][1]) <= 60 else 128
            v = enc.encode([r[1] for r in rows[s:s + bs]], enc.doc_prefix).astype(np.float32)
            out[s:s + bs] = vec.project(v, mean, W)
            s += bs
        np.savez(f + ".part.npz", i=np.array([r[0] for r in rows], dtype=np.int64), v=out)
        os.replace(f + ".part.npz", f)
        el = time.time() - t0
        print(f"  retrimmed {k + len(rows)}/{len(changed)} {el:.0f}s", flush=True)
    new_v = {}
    for f in sorted(os.listdir(rd)):
        if f.endswith(".npz") and not f.endswith(".part.npz"):
            z = np.load(os.path.join(rd, f))
            new_v.update(zip(z["i"].tolist(), z["v"]))
    raw = os.path.join(work, "raw")
    for f in sorted(os.listdir(raw)):                                  # the rows in the shards: replaced (the same again if run twice)
        if not f.endswith(".npz"):
            continue
        z = np.load(os.path.join(raw, f))
        ids, v = z["i"], z["v"].copy()
        hit = [j for j, i in enumerate(ids.tolist()) if i in new_v]
        if hit:
            for j in hit:
                v[j] = new_v[int(ids[j])]
            np.savez(os.path.join(raw, f + ".part.npz"), i=ids, v=v)
            os.replace(os.path.join(raw, f + ".part.npz"), os.path.join(raw, f))
    con.executemany("update part set text=? where id=?", [(t, i) for i, t in changed])
    con.commit()
    con.close()
    open(done_mark, "w").write(str(len(changed)))
    return len(changed)


# ---------------------------------------------------------------------------
# 4. the index (streams the shards: the vectors are never all in memory)
# ---------------------------------------------------------------------------
def build_index(work: str, out: str, n_lists: Optional[int] = None) -> dict:
    raw = os.path.join(work, "raw")
    files = [os.path.join(raw, f) for f in sorted(os.listdir(raw)) if f.endswith(".npz")]
    sizes = [len(np.load(f)["i"]) for f in files]
    n = int(sum(sizes))
    d = int(np.load(files[0])["v"].shape[1])
    k = n_lists or int(min(8192, max(16, 2 * (n ** 0.5))))
    print(f"{n} vectors x {d}, {k} lists", flush=True)
    rng = np.random.default_rng(0)
    frac = min(1.0, 300000 / n)
    samp = np.concatenate([np.load(f)["v"][rng.random(sz) < frac].astype(np.float32) for f, sz in zip(files, sizes)])
    cent = vec.kmeans(samp, k)
    del samp
    import torch
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    C = torch.tensor(cent, device=dev)
    asg = np.empty(n, dtype=np.int32)
    scale = np.empty(n, dtype=np.float16)
    at = 0
    for f in files:                                                    # B: list of every vector, scale of every vector
        X = np.load(f)["v"].astype(np.float32)
        t = torch.tensor(X, device=dev)
        asg[at:at + len(X)] = np.concatenate([(t[s:s + 50000] @ C.T).argmax(1).cpu().numpy() for s in range(0, len(X), 50000)])
        scale[at:at + len(X)] = (np.abs(X).max(1) / 127.0).astype(np.float16)
        at += len(X)
    o = np.argsort(asg, kind="stable")
    pos = np.empty(n, dtype=np.int64)
    pos[o] = np.arange(n)
    lists = np.zeros(k + 1, dtype=np.int64)
    np.cumsum(np.bincount(asg, minlength=k), out=lists[1:])
    os.makedirs(out, exist_ok=True)
    mm = np.memmap(os.path.join(out, "vec.i8"), dtype=np.int8, mode="w+", shape=(n, d))
    km = np.memmap(os.path.join(out, "key.i64"), dtype=np.int64, mode="w+", shape=(n,))
    at = 0
    for f in files:                                                    # C: write each row at its place in list order
        z = np.load(f)
        X = z["v"].astype(np.float32)
        sc = scale[at:at + len(X)].astype(np.float32)
        p = pos[at:at + len(X)]
        mm[p] = np.round(X / np.maximum(sc, 1e-8)[:, None]).astype(np.int8)
        km[p] = z["i"]
        at += len(X)
    mm.flush()
    km.flush()
    scale[o].tofile(os.path.join(out, "scale.f16"))
    cent.astype(np.float32).tofile(os.path.join(out, "cent.f32"))
    lists.tofile(os.path.join(out, "lists.i64"))
    return dict(n=n, dim=d, lists=int(k))


def run(model: str, out: Optional[str] = None, limit: Optional[int] = None, work: Optional[str] = None, lists: Optional[int] = None,
        pca: int = 256, pilot_only: bool = False) -> None:
    out = out or config.VEC_DIR
    work = work or out + ".work"
    os.makedirs(work, exist_ok=True)
    t0 = time.time()
    n = make_parts(work, limit)
    enc = vec.TorchEncoder(model)
    mean, W = fit_pca(work, enc, pca) if pca else (None, None)
    chk = pca_check(work, mean, W) if pca else {}
    if pilot_only:
        return
    embed_parts(work, enc, n, mean, W)
    info = build_index(work, out, lists)
    os.makedirs(out, exist_ok=True)
    for f in ("parts.sqlite", "pca.f32"):
        if os.path.exists(os.path.join(work, f)):
            shutil.copy(os.path.join(work, f), os.path.join(out, f))
    pooling = "mean" if enc.kind == "e5" else "cls"
    src_model = os.path.join(config.DATA_DIR, "vec", "model")
    if os.path.exists(os.path.join(src_model, "weights.bin")) and not os.path.exists(os.path.join(out, "model")) and \
            json.load(open(os.path.join(src_model, "model.json"))).get("model") == model:
        shutil.copytree(src_model, os.path.join(out, "model"))        # the encoder is the same one: not exported again
    else:
        vec.export_model(model, os.path.join(out, "model"), pooling)
    m = sqlite3.connect(os.path.join(out, "parts.sqlite")).execute("select k, v from meta").fetchall()
    json.dump(dict(info, unit="parts", model=model, pooling=pooling, query_prefix=enc.query_prefix,
                   **({"pca": dict(**{"from": int(len(mean)), "to": int(W.shape[0])}, sample=PILOT, **chk)} if pca else {}),
                   parts=dict(m), sources=list(SRC), built=time.strftime("%Y-%m-%d %H:%M"), limit=limit or 0, seconds=round(time.time() - t0)),
              open(os.path.join(out, "meta.json"), "w"), indent=1)
    print(open(os.path.join(out, "meta.json")).read())


def retrim_run(model: str, out: Optional[str], work: str, lists: Optional[int] = None, pca: int = 256) -> None:
    """``retrim`` + ``build_index`` + the files of a finished index (``run`` without the cut and the embedding of everything)."""
    enc = vec.TorchEncoder(model)
    mean, W = fit_pca(work, enc, pca) if pca else (None, None)
    n = retrim(work, enc, mean, W)
    info = build_index(work, out, lists)
    for f in ("parts.sqlite", "pca.f32"):
        shutil.copy(os.path.join(work, f), os.path.join(out, f))
    old = os.path.join(config.DATA_DIR, "vec")
    if os.path.exists(os.path.join(old, "meta.json")):
        m = json.load(open(os.path.join(old, "meta.json")))
        if os.path.exists(os.path.join(old, "model")) and not os.path.exists(os.path.join(out, "model")):
            os.symlink(os.path.abspath(os.path.join(old, "model")), os.path.join(out, "model"))
        m.update(info, retrim=n, built=time.strftime("%Y-%m-%d %H:%M"))
        json.dump(m, open(os.path.join(out, "meta.json"), "w"), indent=1)
    print(open(os.path.join(out, "meta.json")).read())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="BAAI/bge-m3")
    ap.add_argument("--out")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--work")
    ap.add_argument("--lists", type=int)
    ap.add_argument("--pca", type=int, default=256)
    ap.add_argument("--pilot-only", action="store_true")
    ap.add_argument("--retrim", action="store_true", help="the work folder is finished: embed again only the parts whose text now differs (trimmed), then build the index again")
    a = ap.parse_args()
    if a.retrim:
        retrim_run(a.model, a.out, a.work, a.lists, a.pca)
    else:
        run(a.model, a.out, a.limit, a.work, a.lists, a.pca, a.pilot_only)
