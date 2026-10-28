"""Finding hadiths by meaning: the semantic index of text PARTS built by ``icm.build.parts`` and the encoder that embeds a query.

``available()`` is true when ``config.VEC_DIR`` holds an index of parts (``meta.json`` with ``unit: "parts"``, ``parts.sqlite``,
the encoder in ``model/``). Queries are embedded by ``embed_np.NpEncoder`` over the exported copy of the corpus' own model and
projected on the principal axes the index was stored in (``pca.f32``), so vectors of the query and of the corpus come from the
same weights. A part is one row of ``parts.sqlite`` (``part``: its text; ``occ``: the hadiths it stands in, token range
[a, b) of ``text.tokens``). Everything degrades: when it is not available the callers keep to the word search.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import config, corpus
from .build.vec import emb_text, project

CHUNK_WORDS = 64                               # the windows of the first index (stage files of projects made with it)

_lock = threading.Lock()
_index: Optional["Index"] = None
_enc = None
_cache: "OrderedDict[str, np.ndarray]" = OrderedDict()
_local = threading.local()


def available() -> bool:
    d = config.VEC_DIR
    return (os.path.exists(os.path.join(d, "meta.json")) and os.path.exists(os.path.join(d, "model", "weights.bin"))
            and os.path.exists(os.path.join(d, "parts.sqlite")) and meta().get("unit") == "parts")


def meta() -> dict:
    try:
        return json.load(open(os.path.join(config.VEC_DIR, "meta.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Index:
    """int8 vectors in IVF order, memory-mapped; a query reads only the lists nearest to it. ``key`` is a part's id."""

    def __init__(self, d: str):
        m = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
        self.meta, n, dim, k = m, m["n"], m["dim"], m["lists"]
        self.n, self.dim = n, dim
        self.vec = np.memmap(os.path.join(d, "vec.i8"), dtype=np.int8, mode="r", shape=(n, dim))
        self.scale = np.memmap(os.path.join(d, "scale.f16"), dtype=np.float16, mode="r", shape=(n,))
        self.key = np.memmap(os.path.join(d, "key.i64"), dtype=np.int64, mode="r", shape=(n,))
        self.cent = np.fromfile(os.path.join(d, "cent.f32"), dtype=np.float32).reshape(k, dim)
        self.lists = np.fromfile(os.path.join(d, "lists.i64"), dtype=np.int64)
        self.mean = self.W = None
        pc = os.path.join(d, "pca.f32")
        if m.get("pca") and os.path.exists(pc):
            a = np.fromfile(pc, dtype=np.float32)
            d0 = len(a) // (dim + 1)
            self.mean, self.W = a[:d0], a[d0:].reshape(dim, d0)

    def search(self, q: np.ndarray, k: int = 100, nprobe: int = 48) -> List[Tuple[int, float]]:
        """[(part id, cosine)] best first, at most ``k``."""
        nprobe = min(nprobe, len(self.cent))
        probe = np.argpartition(-(self.cent @ q), nprobe - 1)[:nprobe]
        keys, sc = [], []
        for p in probe:
            a, b = int(self.lists[p]), int(self.lists[p + 1])
            if a == b:
                continue
            sc.append((np.asarray(self.vec[a:b], dtype=np.float32) @ q) * np.asarray(self.scale[a:b], dtype=np.float32))
            keys.append(np.asarray(self.key[a:b]))
        if not keys:
            return []
        keys, sc = np.concatenate(keys), np.concatenate(sc)
        top = np.argsort(-sc)[:k]
        return [(int(keys[j]), min(1.0, float(sc[j]))) for j in top]


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
    """Unit vectors of the texts as queries (cleaned as the parts of the corpus were, projected like them). Remembered for the next call."""
    prefix = meta().get("query_prefix", "")
    ix = index()
    out = np.zeros((len(texts), ix.dim), dtype=np.float32)
    for i, t in enumerate(texts):
        t = emb_text(t)
        if t in _cache:
            out[i] = _cache[t]
            _cache.move_to_end(t)
            continue
        v = np.asarray(encoder().encode([prefix + t]), dtype=np.float32)
        v = project(v, ix.mean, ix.W)[0]
        out[i] = v / max(float(np.linalg.norm(v)), 1e-12)
        _cache[t] = out[i]
        if len(_cache) > 512:
            _cache.popitem(last=False)
    return out


def _db() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    p = os.path.join(config.VEC_DIR, "parts.sqlite")
    if c is None or getattr(_local, "path", None) != p:
        c = _local.c = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True, check_same_thread=False)
        _local.path = p
    return c


def wid(key: int) -> str:
    return f"v02:{key}" if key > 0 else f"kg:{-key}"


def neighbours(texts: Sequence[str], k: int = 60, nprobe: Optional[int] = None, min_cos: float = 0.0) -> list:
    """For each text, the parts nearest to it by meaning: [(part id, cosine)] best first."""
    ix = index()
    return [[(p, c) for p, c in ix.search(q, k, nprobe or config.SEM_NPROBE) if c >= min_cos] for q in embed(texts)]


def parts_of(ids: Sequence[int], exclude=(), occ_max: Optional[int] = None) -> Dict[int, dict]:
    """``{part id: dict(text, ntok, n, occ=[(wid, a, b, src)])}``: the parts' texts and where they stand (hadiths of ``exclude`` left
    out of ``occ``; ``occ_max``: at most that many, the earliest by hadith number; ``n``: how many hadiths have the part)."""
    ids = [int(i) for i in dict.fromkeys(ids)]
    db = _db()
    out: Dict[int, dict] = {}
    for s in range(0, len(ids), 500):
        part = ids[s:s + 500]
        q = ",".join("?" * len(part))
        for pid, text, ntok in db.execute(f"select id, text, ntok from part where id in ({q})", part):
            out[pid] = dict(text=text, ntok=ntok, n=0, occ=[])
        for pid, key, a, b, src in db.execute(f"select part, key, a, b, src from occ where part in ({q}) order by part, key", part):
            o = out.get(pid)
            if o is None:
                continue
            w = wid(key)
            if w in exclude:
                continue
            o["n"] += 1
            if occ_max is None or len(o["occ"]) < occ_max:
                o["occ"].append((w, a, b, src))
    return out


def hadith_hits(texts: Sequence[str], k: int = 40, min_cos: float = 0.0, exclude=(), occ_max: int = 100) -> list:
    """For each text, the hadiths that have a part near it: ``[(wid, cosine, [a, b], part id)]`` best first, one per hadith (its best
    part), from the ``k`` nearest parts (a part that stands in more than ``occ_max`` hadiths gives its first ``occ_max``)."""
    out = []
    for hits in neighbours(texts, k=k, min_cos=min_cos):
        got = parts_of([p for p, _ in hits], exclude, occ_max)
        best: Dict[str, tuple] = {}
        for p, cos in hits:
            for w, a, b, _ in (got.get(p) or {}).get("occ", ()):
                if w not in best or cos > best[w][0]:
                    best[w] = (cos, [a, b], p)
        out.append(sorted(((w, c, ab, p) for w, (c, ab, p) in best.items()), key=lambda x: -x[1]))
    return out


def word_spans(raw: str) -> List[Tuple[int, int]]:
    """Character range of every word of ``emb_text(raw)`` in ``strip_tags(raw)`` (the words a chunk is counted in)."""
    from .build.vec import _HAR
    from .text import strip_tags
    return [(m.start(), m.end()) for m in re.finditer(r"[^\s\u200c]+", strip_tags(raw or "")) if _HAR.sub("", m.group(0))]


def chunk_range(raw: str, first: int) -> Optional[Tuple[int, int]]:
    """Character range (in ``strip_tags(raw)``) of the chunk that starts at word ``first``."""
    sp = word_spans(raw)
    if first >= len(sp):
        return None
    return sp[first][0], sp[min(first + CHUNK_WORDS, len(sp)) - 1][1]


def find(q: str, scope: str, names: Sequence[str], limit: int, offset: int) -> dict:
    """``finder.find`` by meaning: the hadiths that have a part nearest to the text ``q`` (the part is coloured); ``names``: the corpora asked."""
    from . import finder
    from .text import token_offsets
    if not available():
        return dict(mode="semantic", hits=[], total=0, exact=True, parsed=None, warnings=["جستجوی معنایی در این نصب فراهم نیست"],
                    capped=False, timed_out=False, unavailable=True)
    t0 = time.time()
    k = min(offset + limit + 40, 400)
    hits = [h for h in hadith_hits([q], k=k, occ_max=50)[0] if h[0].split(":")[0] in names]
    page = hits[offset:offset + limit + 12]
    recs = corpus.get_many([h[0] for h in page])
    keep, copies = finder._fold([h[0] for h in page], recs)
    by = {h[0]: h for h in page}
    out = []
    for w in keep[:limit]:
        cos, (a, b), _ = by[w][1:]
        offs = token_offsets(recs[w]["matn"])
        mk = [[offs[a][0], offs[b - 1][1], 0]] if 0 <= a < b <= len(offs) else []
        out.append(finder._record(recs[w], None, round(cos, 3), copies[w], marks=mk))
    return dict(mode="semantic", hits=out, total=len(hits), exact=False, parsed=None, warnings=[], capped=len(hits) >= 400, timed_out=False,
                seconds=round(time.time() - t0, 2))
