"""Finding hadiths by meaning: the semantic index built by ``icm.build.vec`` and the encoder that embeds a query.

``available()`` is true when ``config.VEC_DIR`` holds an index (``meta.json``). Queries are embedded by ``embed_np.NpEncoder``
over the exported copy of the corpus' own model (``<VEC_DIR>/model``), so vectors of the query and of the corpus come
from the same weights. Everything degrades: when it is not available the callers keep to the word search.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import config, corpus
from .build.vec import chunk_text, emb_text      # the same cleaning and cutting as the corpus got

_lock = threading.Lock()
_index: Optional["Index"] = None
_enc = None
_cache: "OrderedDict[str, np.ndarray]" = OrderedDict()


def available() -> bool:
    return os.path.exists(os.path.join(config.VEC_DIR, "meta.json")) and os.path.exists(os.path.join(config.VEC_DIR, "model", "weights.bin"))


def meta() -> dict:
    try:
        return json.load(open(os.path.join(config.VEC_DIR, "meta.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Index:
    """int8 vectors in IVF order, memory-mapped; a query reads only the lists nearest to it."""

    def __init__(self, d: str):
        m = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
        self.meta, n, dim, k = m, m["n"], m["dim"], m["lists"]
        self.n, self.dim = n, dim
        self.vec = np.memmap(os.path.join(d, "vec.i8"), dtype=np.int8, mode="r", shape=(n, dim))
        self.scale = np.memmap(os.path.join(d, "scale.f16"), dtype=np.float16, mode="r", shape=(n,))
        self.key = np.memmap(os.path.join(d, "key.i64"), dtype=np.int64, mode="r", shape=(n,))
        self.cstart = np.memmap(os.path.join(d, "cstart.u16"), dtype=np.uint16, mode="r", shape=(n,))
        self.cent = np.fromfile(os.path.join(d, "cent.f32"), dtype=np.float32).reshape(k, dim)
        self.lists = np.fromfile(os.path.join(d, "lists.i64"), dtype=np.int64)
        dp = os.path.join(d, "dups.npy")
        self.dups: Dict[int, List[int]] = {}
        if os.path.exists(dp):
            for dup, canon in np.load(dp):
                self.dups.setdefault(int(canon), []).append(int(dup))

    def search(self, q: np.ndarray, k: int = 100, nprobe: int = 48) -> List[Tuple[int, float, int]]:
        """[(key, cosine, first word of the chunk)] best first, one per hadith (its best chunk), at most ``k``."""
        nprobe = min(nprobe, len(self.cent))
        probe = np.argpartition(-(self.cent @ q), nprobe - 1)[:nprobe]
        keys, sc, cs = [], [], []
        for p in probe:
            a, b = int(self.lists[p]), int(self.lists[p + 1])
            if a == b:
                continue
            s = (np.asarray(self.vec[a:b], dtype=np.float32) @ q) * np.asarray(self.scale[a:b], dtype=np.float32)
            keys.append(np.asarray(self.key[a:b]))
            cs.append(np.asarray(self.cstart[a:b]))
            sc.append(s)
        if not keys:
            return []
        keys, sc, cs = np.concatenate(keys), np.concatenate(sc), np.concatenate(cs)
        out, seen = [], set()
        for j in np.argsort(-sc)[: 4 * k + 50]:
            kk = int(keys[j])
            if kk in seen:
                continue
            seen.add(kk)
            out.append((kk, float(sc[j]), int(cs[j])))
            if len(out) >= k:
                break
        return out


def index() -> Index:
    global _index
    with _lock:
        if _index is None:
            _index = Index(config.VEC_DIR)
        return _index


def encoder():
    global _enc
    with _lock:
        if _enc is None:
            from .embed_np import NpEncoder
            _enc = NpEncoder(os.path.join(config.VEC_DIR, "model"))
        return _enc


def embed(texts: Sequence[str]) -> np.ndarray:
    """Unit vectors of the texts as queries (cleaned as the corpus was; a text over one chunk is cut into windows and the
    vector is the mean of theirs). Remembered for the next call."""
    prefix = meta().get("query_prefix", "")
    out = np.zeros((len(texts), index().dim), dtype=np.float32)
    for i, t in enumerate(texts):
        t = emb_text(t)
        if t in _cache:
            out[i] = _cache[t]
            _cache.move_to_end(t)
            continue
        ch = [c for _, c in chunk_text(t)][:6] or [t]
        v = encoder().encode([prefix + c for c in ch]).mean(0)
        out[i] = v / max(float(np.linalg.norm(v)), 1e-12)
        _cache[t] = out[i]
        if len(_cache) > 512:
            _cache.popitem(last=False)
    return out


def wid(key: int) -> str:
    return f"v02:{key}" if key > 0 else f"kg:{-key}"


def neighbours(texts: Sequence[str], k: int = 60, nprobe: Optional[int] = None, min_cos: float = 0.0) -> List[List[Tuple[str, float]]]:
    """For each text, the hadiths nearest to it by meaning: [(wid, cosine)] best first (with the hadiths of identical text)."""
    ix = index()
    out = []
    for q in embed(texts):
        hits = []
        for key, cos, _ in ix.search(q, k, nprobe or config.SEM_NPROBE):
            if cos < min_cos:
                continue
            hits.append((wid(key), cos))
            hits += [(wid(d), cos) for d in ix.dups.get(key, ())]
        out.append(hits)
    return out


def find(q: str, scope: str, names: Sequence[str], limit: int, offset: int) -> dict:
    """``finder.find`` by meaning: the hadiths nearest to the text ``q``; ``names``: the corpora asked."""
    from . import finder
    if not available():
        return dict(mode="semantic", hits=[], total=0, exact=True, parsed=None, warnings=["جستجوی معنایی در این نصب فراهم نیست"],
                    capped=False, timed_out=False, unavailable=True)
    t0 = time.time()
    hits = neighbours([q], k=min(offset + limit + 40, 400))[0]
    hits = [(w, c) for w, c in hits if w.split(":")[0] in names]
    page = hits[offset:offset + limit + 12]
    recs = corpus.get_many([w for w, _ in page])
    keep, copies = finder._fold([w for w, _ in page], recs)
    sc = dict(page)
    return dict(mode="semantic", hits=[finder._record(recs[w], None, round(sc[w], 3), copies[w]) for w in keep[:limit]], total=len(hits),
                exact=False, parsed=None, warnings=[], capped=len(hits) >= 400, timed_out=False, seconds=round(time.time() - t0, 2))
