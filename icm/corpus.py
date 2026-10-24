"""Read-only access to the hadith corpora: hadith-db-v02 (served FTS database) and the
Sunni Hadith Knowledge Graph extract (kg.sqlite).

A hadith is addressed by a *witness id* ``wid``: ``"v02:800316143"`` or ``"kg:73789"``.
Every accessor returns plain dicts; connections are thread-local, opened read-only.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import zlib
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import config
from .text import norm

_local = threading.local()


def _connect(path: str, mmap: int = 256 * 1024 * 1024) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("pragma query_only=1")
    c.execute(f"pragma mmap_size={mmap}")
    return c


def conn(name: str) -> sqlite3.Connection:
    """name in {v02, consensus, kg, termdf, noor_rel}"""
    cs = getattr(_local, "cs", None)
    if cs is None:
        cs = _local.cs = {}
    if name not in cs:
        path = {"v02": config.V02_DB, "consensus": config.CONSENSUS_DB, "kg": config.KG_DB,
                "termdf": config.TERMDF_DB, "noor_rel": config.NOOR_REL_DB}[name]
        cs[name] = _connect(path, 64 * 1024 * 1024 if name != "v02" else 256 * 1024 * 1024)
    return cs[name]


_books_meta: Optional[dict] = None
_lock = threading.Lock()


def books_meta() -> dict:
    global _books_meta
    with _lock:
        if _books_meta is None:
            try:
                _books_meta = json.load(open(config.BOOKS_META, encoding="utf-8"))
            except FileNotFoundError:
                _books_meta = {}
    return _books_meta


_work_alias: Optional[dict] = None


def work_alias() -> dict:
    """{"v02:<code>"|"kg:<book_id>": work_key} - same work in both corpora shares a key."""
    global _work_alias
    with _lock:
        if _work_alias is None:
            import os
            p = os.path.join(os.path.dirname(__file__), "data", "work_aliases.json")
            _work_alias = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    return _work_alias


def wid_parts(wid: str) -> Tuple[str, int]:
    corpus, _, n = wid.partition(":")
    return corpus, int(n)


def work_key(corpus: str, book) -> str:
    k = f"{corpus}:{book}"
    return work_alias().get(k, k)


# ---------------------------------------------------------------------------
# single hadith
# ---------------------------------------------------------------------------
def _v02_record(r: sqlite3.Row) -> dict:
    code = r["book"]
    bm = books_meta().get(code, {})
    return dict(
        wid=f"v02:{r['id']}", corpus="v02", hid=int(r["id"]), book=code, work=work_key("v02", code),
        title=bm.get("title"), author=bm.get("author"), death=bm.get("death"),
        tradition=bm.get("tradition"), vol=r["vol"], page=r["page"], no=None,
        matn=r["matn"] or "", sanad=r["sanad"] or "", translation=r["translation"] or "",
        rowid=r["rowid"])


def _kg_record(r: sqlite3.Row, book: Optional[sqlite3.Row]) -> dict:
    return dict(
        wid=f"kg:{r['id']}", corpus="kg", hid=int(r["id"]), book=r["book_id"], work=work_key("kg", r["book_id"]),
        title=book["name"] if book else None, author=book["author_name"] if book else None,
        death=book["author_death_year"] if book else None, tradition="sunni",
        vol=None, page=r["page_no"], no=r["no_inbook"],
        matn=zlib.decompress(r["matn_z"]).decode(), sanad=zlib.decompress(r["sanad_z"]).decode(),
        translation="", group_id=r["group_id"], takhrij=r["takhrij"], hukm=r["type"])


def get_many(wids: Sequence[str]) -> Dict[str, dict]:
    """Records by witness id; «<id>~k» is reference k inside record <id> (``pointers.record``): its isnad and formula."""
    inner = [w for w in wids if "~" in w]
    if inner:
        from . import pointers
        out = get_many([w for w in wids if "~" not in w])
        hosts = get_many(sorted({w.split("~")[0] for w in inner}))
        for w in inner:
            h, k = pointers.pseudo(w)
            r = pointers.record(hosts[h], k) if h in hosts else None
            if r is not None:
                out[w] = r
        return out
    out: Dict[str, dict] = {}
    v02 = [wid_parts(w)[1] for w in wids if w.startswith("v02:")]
    kg = [wid_parts(w)[1] for w in wids if w.startswith("kg:")]
    for chunk in _chunks([str(i) for i in v02], 500):
        q = "select rowid,id,book,vol,page,matn,sanad,translation from hadith where id in (%s)" % ",".join("?" * len(chunk))
        for r in conn("v02").execute(q, chunk):
            rec = _v02_record(r)
            out[rec["wid"]] = rec
    if kg:
        books = {b["id"]: b for b in conn("kg").execute("select id,name,author_name,author_death_year from books")}
        for chunk in _chunks(kg, 500):
            q = ("select id,book_id,no_inbook,page_no,type,group_id,takhrij,sanad_z,matn_z from kg_hadith "
                 "where id in (%s)" % ",".join("?" * len(chunk)))
            for r in conn("kg").execute(q, chunk):
                rec = _kg_record(r, books.get(r["book_id"]))
                out[rec["wid"]] = rec
    return out


def get(wid: str) -> Optional[dict]:
    return get_many([wid]).get(wid)


def neighbours(wid: str, n: int = 4, after: bool = True) -> List[str]:
    """The ``n`` hadiths that follow ``wid`` in its book (``after=False``: that precede it, nearest first). v02 ids
    run in book order inside a book (Noor: one per hadith; other sources: gaps), the KG has the number in the book."""
    co, i = wid_parts(wid.split("~")[0])
    op, order = (">", "asc") if after else ("<", "desc")
    if co == "v02":
        r = conn("v02").execute("select book from hadith where id=?", (str(i),)).fetchone()
        if r is None:
            return []
        out = []                                        # walk the id index; the book ends where another begins
        q = f"select id, book from hadith where id {op} ? and length(id)=? order by id {order} limit ?"
        for x in conn("v02").execute(q, (str(i), len(str(i)), 3 * n + 3)):
            if x["book"] != r["book"] or len(out) == n:
                break
            out.append(f"v02:{x['id']}")
        return out
    if co == "kg":
        r = conn("kg").execute("select book_id, no_inbook from kg_hadith where id=?", (i,)).fetchone()
        if r is None or r["no_inbook"] is None:
            return []
        q = f"select id from kg_hadith where book_id=? and no_inbook {op} ? order by no_inbook {order} limit ?"
        return [f"kg:{x[0]}" for x in conn("kg").execute(q, (r["book_id"], r["no_inbook"], n))]
    return []


def _chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ---------------------------------------------------------------------------
# full-text search (matn index; the v02 index also holds translation/summary/topics)
# ---------------------------------------------------------------------------
def fts_query(terms: Sequence[str], mode: str = "and") -> str:
    """FTS5 expression: quoted normalised tokens joined by AND (all terms) or NEAR."""
    toks = [t for t in (norm(x) for x in terms) if t]
    toks = [w for t in toks for w in t.split()]
    if not toks:
        return ""
    return " AND ".join('"%s"' % t.replace('"', "") for t in toks)


def fts_v02(expr: str, limit: int = 3000) -> List[int]:
    """v02 hadith ids (int) whose indexed text matches ``expr``."""
    if not expr:
        return []
    rows = conn("v02").execute("select rowid from hadith_fts where hadith_fts match ? limit ?", (expr, limit)).fetchall()
    rids = [r[0] for r in rows]
    ids: List[int] = []
    for chunk in _chunks(rids, 500):
        q = "select id from hadith where rowid in (%s)" % ",".join("?" * len(chunk))
        ids.extend(int(r[0]) for r in conn("v02").execute(q, chunk))
    return ids


def fts_kg(expr: str, limit: int = 3000) -> List[int]:
    if not expr:
        return []
    return [r[0] for r in conn("kg").execute("select rowid from kg_fts where kg_fts match ? limit ?", (expr, limit))]


# ---------------------------------------------------------------------------
# term document frequencies
# ---------------------------------------------------------------------------
def term_df(terms: Iterable[str]) -> Dict[str, Tuple[int, int]]:
    """{term: (df_v02, df_kg)}; unknown terms -> (0, 0)."""
    terms = list(dict.fromkeys(terms))
    out = {t: (0, 0) for t in terms}
    for chunk in _chunks(terms, 500):
        q = "select term, v02, kg from termdf where term in (%s)" % ",".join("?" * len(chunk))
        for r in conn("termdf").execute(q, chunk):
            out[r[0]] = (r[1], r[2])
    return out
