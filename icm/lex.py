"""Runtime access to ``lex.sqlite`` (see ``icm.build.lex``): the word / root / sanad / book index, the root of a word form,
document frequencies, and the searches the query language (``icm.query``) and the wizard's step 2 make on it.

Everything here is read-only and degrades: ``available()`` is false when the file is missing and the callers keep to the
word index they had (``corpus.fts_*``).
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import config, corpus
from .text import norm

TABLES = {"v02": "v02_lex", "kg": "kg_lex"}
BM25 = "bm25({t}, 1.0, 0.3, 0.6, 0)"             # matn, sanad, root, book
STOP_DF = {"v02": 100_000, "kg": 70_000}         # a root in more documents than this is not distinctive (قول, کون, صلو …)
MIN_ROOT_WORD = 3                                # shorter words are never matched by root (their root is their letters)

# ---------------------------------------------------------------------------
# root keys (shared with icm.build.roots)
# ---------------------------------------------------------------------------
_WEAK = set("وياءأإآؤئىٱ#یى")
_KEY_STRIP = re.compile(r"[^ء-غف-يکی]")
ARABIC_FORM = re.compile(r"^[ء-غف-يکی]+$")      # letters of norm(); no گ چ پ ژ (Persian)


def is_arabic_form(t: str) -> bool:
    return len(t) >= 2 and ARABIC_FORM.match(t) is not None


def root_key(s: str) -> str:
    """The key of a root, in the letters of ``norm`` with every weak letter or hamza as «و»: «ص.ل.#», «ص-ل-و», «صلو»,
    «ص ل و» -> «صلو»; «ق-ي-م» -> «قوم». Empty when ``s`` is not a root (fewer than 2 letters)."""
    s = norm(s.replace("#", "و").replace(".", "").replace("-", "").replace("_", "")).replace(" ", "")
    s = _KEY_STRIP.sub("", s)
    s = "".join("و" if c in _WEAK else c for c in s)
    return s if len(s) >= 2 else ""


def root_tok(key: str) -> str:
    return "r" + key


# ---------------------------------------------------------------------------
# availability / connection
# ---------------------------------------------------------------------------
def available() -> bool:
    return os.path.exists(config.LEX_DB)           # (looked at every time: the file may be shipped while the worker runs)


def conn() -> sqlite3.Connection:
    return corpus.conn("lex")


def meta() -> dict:
    try:
        return {k: v for k, v in conn().execute("select k, v from meta")}
    except sqlite3.Error:
        return {}


def _q(sql: str, args: Sequence = ()) -> list:
    return conn().execute(sql, args).fetchall()


def _chunks(seq, n=500):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ---------------------------------------------------------------------------
# roots and frequencies
# ---------------------------------------------------------------------------
_root_cache: Dict[str, str] = {}
_cache_lock = threading.Lock()


def _root_of_cached(t: str) -> str:
    return _root_cache.get(t)


def roots(tokens: Iterable[str]) -> Dict[str, str]:
    """{token: root key} for the tokens that have a root (a form the table lacks borrows the root of itself without a
    leading و/ف or الـ, as the index did). Looked up in batches and remembered for the life of the process."""
    toks = list(dict.fromkeys(tokens))
    need = [t for t in toks if t not in _root_cache]
    if need:
        want = set(need)
        for t in need:
            if len(t) > 3:
                want.update({t[1:], t[2:], t[3:]})
        got: Dict[str, str] = {}
        for ch in _chunks(want):
            for term, root in _q("select term, root from term_root where term in (%s)" % ",".join("?" * len(ch)), ch):
                got[term] = root or ""
        with _cache_lock:
            for t in need:
                k = got.get(t)
                if k is None and len(t) > 3:
                    for cut in ((1,) if t[0] in "وف" else ()) + ((2,) if t.startswith("ال") else ()) + ((3,) if t[0] in "وف" and t[1:3] == "ال" else ()):
                        k = got.get(t[cut:])
                        if k:
                            break
                _root_cache[t] = k or ""
    return {t: _root_cache[t] for t in toks if _root_cache.get(t)}


@lru_cache(maxsize=4096)
def _root_df_one(key: str) -> Tuple[int, int]:
    r = _q("select v02, kg from root_df where root=?", (root_tok(key),))
    return (r[0][0] or 0, r[0][1] or 0) if r else (0, 0)


def root_df(keys: Iterable[str]) -> Dict[str, Tuple[int, int]]:
    """{root key: (documents in v02, documents in the KG)}."""
    return {k: _root_df_one(k) for k in dict.fromkeys(keys)}


def is_stop_root(key: str) -> bool:
    a, b = _root_df_one(key)
    return a > STOP_DF["v02"] or b > STOP_DF["kg"]


def df(terms: Iterable[str], col: str = "m") -> Dict[str, Tuple[int, int]]:
    """{word: (documents in v02, documents in the KG)} where the word is in the matn (``col='m'``) or the sanad (``'s'``);
    unknown words (0, 0)."""
    terms = list(dict.fromkeys(terms))
    out = {t: (0, 0) for t in terms}
    for ch in _chunks(terms):
        for t, a, b in _q(f"select term, v02_{col}, kg_{col} from df where term in (%s)" % ",".join("?" * len(ch)), ch):
            out[t] = (a or 0, b or 0)
    return out


def term_df(terms: Iterable[str]) -> Dict[str, Tuple[int, int]]:
    """Like ``corpus.term_df``, but over the matn only."""
    return df(terms, "m")


def forms_of(key: str, n: int = 40, corpus_name: Optional[str] = None) -> List[str]:
    """The word forms (of the matn) whose root is ``key``: those it is the primary root of and those a secondary analysis
    gives it, commonest first, at most ``n``."""
    col = {"v02": "v02", "kg": "kg", None: "v02+kg"}[corpus_name]
    rows = _q(f"select t.term, t.df from term_root t where t.root=? order by t.df desc limit ?", (key, n))
    out = [r[0] for r in rows]
    if len(out) < n:
        alt = _q("select a.term, t.df from term_alt a join term_root t on t.term=a.term where a.root=? order by t.df desc limit ?", (key, n))
        out += [r[0] for r in alt if r[0] not in out]
    out = out[:n]
    if not out:
        return out
    present = {t for t, (a, b) in df(out).items() if (a + b if corpus_name is None else (a if corpus_name == "v02" else b)) > 0}
    return [t for t in out if t in present]


def book_titles() -> Dict[str, Dict[str, str]]:
    """{corpus: {book token: title}} for resolving «کتاب:نام»."""
    global _titles
    if _titles is None:
        out: Dict[str, Dict[str, str]] = {"v02": {}, "kg": {}}
        bm = corpus.books_meta()
        kgb = {}
        try:
            kgb = {str(i): n for i, n in corpus.conn("kg").execute("select id, name from books")}
        except sqlite3.Error:
            pass
        for tok, co, code in _q("select tok, corpus, code from book_tok"):
            if co == "v02":
                m = bm.get(code) or {}
                out["v02"][tok] = " ".join(x for x in (m.get("title"), m.get("author"), code) if x)
            else:
                out["kg"][tok] = kgb.get(code) or ""
        _titles = out
    return _titles


_titles: Optional[Dict[str, Dict[str, str]]] = None


def books_matching(text: str) -> Dict[str, List[str]]:
    """{corpus: [book tokens]} of the books whose title / author / code contains every word of ``text``."""
    words = norm(text).split()
    out: Dict[str, List[str]] = {"v02": [], "kg": []}
    if not words:
        return out
    for co, d in book_titles().items():
        for tok, title in d.items():
            tn = norm(title)
            if all(w in tn for w in words):
                out[co].append(tok)
    return out


# ---------------------------------------------------------------------------
# clitics
# ---------------------------------------------------------------------------
_PRE = ["", "و", "ف", "ب", "ل", "ک", "وب", "ول", "وک", "فب", "فل", "فک"]
_DET = ["", "ال"]


def core_of(w: str) -> str:
    """``w`` without a leading و/ف, a preposition ب/ل/ک and الـ (only while a stem of 3 letters is left)."""
    c = w
    for p in ("و", "ف"):
        if c.startswith(p) and len(c) > 4:
            c = c[1:]
            break
    for p in ("ب", "ک", "ل"):
        if c.startswith(p) and len(c) > 4 and c[1:].startswith("ال"):
            c = c[1:]
            break
    if c.startswith("لل") and len(c) > 4:
        c = "ل" + c[2:]
    if c.startswith("ال") and len(c) > 4:
        c = c[2:]
    return c


def clitic_forms(w: str, corpus_name: Optional[str] = None, n: int = 48) -> List[str]:
    """``w`` and the forms that are it with a conjunction, preposition and article added or removed, as far as the
    corpora have them (commonest first). Keeps the search from missing «والصلاة» for «الصلاة» or «صلاة»."""
    core = core_of(w)
    cands = {w}
    for p in _PRE:
        for d in _DET:
            cands.add(p + d + core)
            if p.endswith("ل") and d == "ال":                       # ل + الـ is written لل
                cands.add(p + "ل" + core)
    cands = {c for c in cands if c}
    d = df(cands)
    ok = [(a + b if corpus_name is None else (a if corpus_name == "v02" else b), c) for c, (a, b) in d.items()]
    ok = sorted([x for x in ok if x[0] > 0], reverse=True)
    out = [c for _, c in ok][:n]
    return out if w in out else [w] + out[:n - 1]


# ---------------------------------------------------------------------------
# searching
# ---------------------------------------------------------------------------
class Budget(Exception):
    pass


def _run(sql: str, args: Sequence, budget: Optional[float]):
    c = conn()
    if budget:
        t0 = time.time()
        c.set_progress_handler(lambda: 1 if time.time() - t0 > budget else 0, 20000)
    try:
        return c.execute(sql, args).fetchall()
    except sqlite3.OperationalError as e:
        if "interrupt" in str(e).lower():
            raise Budget() from e
        raise
    finally:
        if budget:
            c.set_progress_handler(None, 0)


def search(corpus_name: str, expr: str, limit: int = 3000, offset: int = 0, rank=False, budget: float = 6.0, scores: bool = False) -> dict:
    """Ids of the hadiths of ``corpus_name`` (v02 / kg) matching the FTS expression ``expr``.

    ``rank``: True = best first (bm25); ``"auto"`` = in index order if there are at most ``limit`` hits (then all are
    returned and the order does not matter), best first otherwise. Ranking has to score every hit, which costs seconds
    for a common term, so a ranked search that runs over ``budget`` seconds is repeated unranked (the first ``limit``
    hits in index order): ``timed_out``.
    Returns ``{ids, capped, timed_out, ranked}`` (and ``scores``, bm25 as given by SQLite, smaller = better, when ``scores``
    and ranked); ``capped``: more hits exist than were returned."""
    t = TABLES[corpus_name]
    if not expr:
        return dict(ids=[], capped=False, timed_out=False, ranked=False)
    if rank == "auto":
        try:
            rows = _run(f"select rowid from {t} where {t} match ? limit ? offset ?", (expr, limit + 1, offset), 3 * budget)
        except Budget:
            return dict(ids=[], capped=False, timed_out=True, ranked=False)
        if len(rows) <= limit:
            return dict(ids=[r[0] for r in rows], capped=False, timed_out=False, ranked=False)
        rank = True
    timed_out = False
    if rank:
        try:
            b = BM25.format(t=t)
            rows = _run(f"select rowid, {b} from {t} where {t} match ? order by {b} limit ? offset ?", (expr, limit + 1, offset), budget)
            out = dict(ids=[r[0] for r in rows[:limit]], capped=len(rows) > limit, timed_out=False, ranked=True)
            if scores:
                out["scores"] = [r[1] for r in rows[:limit]]
            return out
        except Budget:
            timed_out = True
    try:
        rows = _run(f"select rowid from {t} where {t} match ? limit ? offset ?", (expr, limit + 1, offset), 3 * budget)
    except Budget:                                       # even the first hits in index order took too long: nothing, said so
        return dict(ids=[], capped=False, timed_out=True, ranked=False)
    return dict(ids=[r[0] for r in rows[:limit]], capped=len(rows) > limit, timed_out=timed_out, ranked=False)


def count(corpus_name: str, expr: str, cap: int = 10001, budget: float = 4.0) -> Tuple[int, bool]:
    """(number of hits, exact): counting stops at ``cap`` hits or after ``budget`` seconds."""
    t = TABLES[corpus_name]
    if not expr:
        return 0, True
    try:
        n = _run(f"select count(*) from (select 1 from {t} where {t} match ? limit ?)", (expr, cap), budget)[0][0]
    except Budget:
        return cap, False
    return n, n < cap


# ---------------------------------------------------------------------------
# root matching of two words (search scoring, alignment)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=300_000)
def same_root(a: str, b: str, loose: bool = False) -> bool:
    """Two different words (of ``norm``) of one root, both with at least ``MIN_ROOT_WORD`` letters. Strict (search, where
    the word is anywhere in a text): the root must be distinctive, in fewer documents than ``STOP_DF``. ``loose``
    (alignment, where the two words stand in the same place of two aligned texts): any root, as «یصلی» / «صلاه»."""
    if a == b or len(a) < MIN_ROOT_WORD or len(b) < MIN_ROOT_WORD:
        return False
    r = roots([a, b])
    ka, kb = r.get(a), r.get(b)
    return bool(ka) and ka == kb and (loose or not is_stop_root(ka))


def same_root_loose(a: str, b: str) -> bool:
    return same_root(a, b, True)


# ---------------------------------------------------------------------------
# match keys that know roots (``similarity.align_*(..., keyf=...)``)
# ---------------------------------------------------------------------------
def prime(tokens: Iterable[str]) -> None:
    """Look up the roots of ``tokens`` in one batch, so that ``search_key`` / ``loose_key`` are plain cache reads."""
    roots(tokens)


def _key_of(t: str, strict: bool) -> str:
    from .similarity import match_key
    k = _root_cache.get(t)
    if k is None:
        roots([t])
        k = _root_cache.get(t)
    if k and len(t) >= MIN_ROOT_WORD and not (strict and is_stop_root(k)):
        return "~" + k
    return match_key(t)


def search_key(t: str) -> str:
    """Match key for finding witnesses: words of one *distinctive* root are one key (a root in more than ``STOP_DF``
    documents, such as صلو or قول, keeps the word's own key)."""
    return _key_of(t, True)


def loose_key(t: str) -> str:
    """Match key for aligning two texts word by word: words of one root are one key, whatever the root."""
    return _key_of(t, False)


def root_set(tokens: Iterable[str], strict: bool = True) -> Set[str]:
    """The root keys of the words of ``tokens`` (distinctive roots only when ``strict``)."""
    r = roots(tokens)
    return {k for t, k in r.items() if len(t) >= MIN_ROOT_WORD and not (strict and is_stop_root(k))}
