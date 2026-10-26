"""lex.sqlite: the wizard's own full-text index, with what the served indexes (``hadith_fts``, ``kg_fts``) lack:

  v02_lex / kg_lex   FTS5, contentless, rowid = the hadith's id; columns
                       matn   the normalised words of the matn (``text.norm``, so a query goes through the same function)
                       sanad  the normalised words of the chain (words only: a narrator's name has no root worth matching)
                       root   per word of the matn, the token «r<root key>» of its primary root, «rz» when it has none, so
                              that a phrase or NEAR over roots counts positions like the words do
                       book   one token «b<n>» (``book_tok``), to restrict a search to books
  term_root / term_alt  root, lemma and part of speech of every word form; the other roots of its analyses
  df / root_df          document frequency of every word (per column) and root, from ``fts5vocab``
The served database is never touched. Roots come from ``icm.build.roots`` (run that first: ``term_root.tsv``).

    python -m icm build lex      [--limit N] [--out PATH]
"""
from __future__ import annotations

import multiprocessing as mp
import os
import sqlite3
import sys
import time
import zlib
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .. import config
from ..text import norm

ROOT_FILLER = "rz"


def create_schema(con: sqlite3.Connection) -> None:
    for t in ("v02_lex", "kg_lex"):
        con.execute(f"create virtual table {t} using fts5(matn, sanad, root, book, tokenize='unicode61 remove_diacritics 0', content='')")
    con.executescript("""
        create table term_root(term text primary key, root text, lemma text, pos text, src text, df integer) without rowid;
        create table term_alt(term text, root text, rank integer, primary key(term, root)) without rowid;
        create table df(term text primary key, v02_m integer default 0, v02_s integer default 0, kg_m integer default 0, kg_s integer default 0) without rowid;
        create table root_df(root text primary key, v02 integer default 0, kg integer default 0) without rowid;
        create table book_tok(tok text, corpus text, code text, primary key(corpus, tok)) without rowid;
        create table meta(k text primary key, v text) without rowid;
    """)


def fill_stats(con: sqlite3.Connection) -> None:
    """``df`` (documents per word, in the matn and in the sanad) and ``root_df`` from the indexes, by ``fts5vocab``."""
    for corpus in ("v02", "kg"):
        con.execute(f"create virtual table if not exists temp.v_{corpus} using fts5vocab('main', '{corpus}_lex', 'col')")
        for col, c in (("matn", "m"), ("sanad", "s")):
            con.execute(f"insert into df(term, {corpus}_{c}) select term, doc from temp.v_{corpus} where col=? "
                        f"on conflict(term) do update set {corpus}_{c}=excluded.{corpus}_{c}", (col,))
        con.execute(f"insert into root_df(root, {corpus}) select term, doc from temp.v_{corpus} where col='root' "
                    f"on conflict(root) do update set {corpus}=excluded.{corpus}")
    con.commit()


# ---------------------------------------------------------------------------
# roots of tokens
# ---------------------------------------------------------------------------
def load_roots(path: str) -> Dict[str, str]:
    """{form: root key} from term_root.tsv (forms without a root are absent)."""
    out = {}
    for ln in open(path, encoding="utf-8"):
        f = ln.rstrip("\n").split("\t")
        if len(f) >= 2 and f[1]:
            out[f[0]] = f[1]
    return out


def root_token(t: str, roots: Dict[str, str]) -> str:
    """The index token of word ``t``'s root. A form the table lacks borrows the root of itself without a leading و/ف
    (a clitic the analyser did not know with it) or without الـ."""
    k = roots.get(t)
    if k is None and len(t) > 3:
        for cut in ((1,) if t[0] in "وف" else ()) + ((2,) if t.startswith("ال") else ()) + ((3,) if t[0] in "وف" and t[1:3] == "ال" else ()):
            k = roots.get(t[cut:])
            if k:
                break
    return "r" + k if k else ROOT_FILLER


def prepare(rid: int, book: str, matn: str, sanad: str, roots: Dict[str, str]) -> tuple:
    m = norm(matn)
    return (rid, m, norm(sanad), " ".join(root_token(t, roots) for t in m.split()), book)


def index_rows(con: sqlite3.Connection, table: str, rows: Iterable[tuple]) -> None:
    """rows: ``prepare`` outputs (rid, matn, sanad, root, book token)."""
    con.executemany(f"insert into {table}(rowid, matn, sanad, root, book) values (?,?,?,?,?)", rows)


# ---------------------------------------------------------------------------
# parallel preparation
# ---------------------------------------------------------------------------
_ROOTS: Dict[str, str] = {}


def _init(path: str) -> None:
    global _ROOTS
    _ROOTS = load_roots(path)


def _work(chunk: List[tuple]) -> List[tuple]:
    return [prepare(r, b, m, s, _ROOTS) for r, b, m, s in chunk]


def _chunks(it: Iterable, n: int):
    buf: List = []
    for x in it:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def _v02_rows(limit: Optional[int], book_tok: Dict[str, str]):
    con = sqlite3.connect(f"file:{config.V02_DB}?mode=ro", uri=True)
    q = "select id, book, matn, sanad from hadith where id glob '[0-9]*' and id not glob '*[^0-9]*'" + (f" limit {int(limit)}" if limit else "")
    for i, b, m, s in con.execute(q):
        yield int(i), book_tok.setdefault(b, "b%d" % (len(book_tok) + 1)), m or "", s or ""


def _kg_rows(limit: Optional[int], book_tok: Dict[str, str]):
    con = sqlite3.connect(f"file:{config.KG_DB}?mode=ro", uri=True)
    q = "select id, book_id, matn_z, sanad_z from kg_hadith" + (f" limit {int(limit)}" if limit else "")
    for i, b, mz, sz in con.execute(q):
        yield int(i), book_tok.setdefault(str(b), "b%d" % (len(book_tok) + 1)), zlib.decompress(mz).decode(), zlib.decompress(sz).decode()


def run(out: Optional[str] = None, limit: Optional[int] = None, procs: int = 8) -> None:
    out = out or config.LEX_DB
    tsv = os.path.join(config.DATA_DIR, "term_root.tsv")
    if not os.path.exists(tsv):
        raise SystemExit(f"{tsv} missing: run  python -m icm.build.roots  first (needs camel-tools)")
    t0 = time.time()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".part"
    for p in (tmp, tmp + "-journal"):
        if os.path.exists(p):
            os.remove(p)
    con = sqlite3.connect(tmp)
    con.execute("pragma journal_mode=off")
    con.execute("pragma synchronous=off")
    create_schema(con)
    book_tok: Dict[str, Dict[str, str]] = {"v02": {}, "kg": {}}
    with mp.Pool(procs, initializer=_init, initargs=(tsv,)) as pool:
        for corpus, rows in (("v02", _v02_rows), ("kg", _kg_rows)):
            n = 0
            for done in pool.imap(_work, _chunks(rows(limit, book_tok[corpus]), 4000)):
                index_rows(con, f"{corpus}_lex", done)
                n += len(done)
                if n % 100000 < 4000:
                    print(f"  {corpus} {n} rows {time.time() - t0:.0f}s", flush=True)
            con.commit()
            print(f"{corpus}: {n} rows {time.time() - t0:.0f}s", flush=True)
    for corpus in ("v02", "kg"):
        con.executemany("insert into book_tok values (?,?,?)", ((t, corpus, c) for c, t in book_tok[corpus].items()))
    # roots
    n_roots = 0
    for ln in open(tsv, encoding="utf-8"):
        f = ln.rstrip("\n").split("\t")
        if len(f) < 7:
            continue
        term, root, lemma, pos, src, alts, dfv = f[:7]
        con.execute("insert or replace into term_root values (?,?,?,?,?,?)", (term, root, lemma, pos, src, int(dfv or 0)))
        for i, a in enumerate(x for x in alts.split(",") if x):
            con.execute("insert or ignore into term_alt values (?,?,?)", (term, a, i + 1))
        n_roots += 1
    con.commit()
    print(f"term_root {n_roots} forms {time.time() - t0:.0f}s", flush=True)
    fill_stats(con)
    print(f"df {con.execute('select count(*) from df').fetchone()[0]} terms, {con.execute('select count(*) from root_df').fetchone()[0]} roots {time.time() - t0:.0f}s", flush=True)
    meta = dict(lex_version=str(config.LEX_VERSION), built=time.strftime("%Y-%m-%d %H:%M"), limit=str(limit or ""),
                n_v02=str(con.execute("select count(*) from v02_lex_docsize").fetchone()[0]),
                n_kg=str(con.execute("select count(*) from kg_lex_docsize").fetchone()[0]))
    try:
        meta["roots_meta"] = open(tsv + ".meta.json", encoding="utf-8").read()
    except OSError:
        pass
    con.executemany("insert or replace into meta values (?,?)", meta.items())
    con.commit()
    for corpus in ("v02", "kg"):
        con.execute(f"insert into {corpus}_lex({corpus}_lex) values('optimize')")
        print(f"optimized {corpus} {time.time() - t0:.0f}s", flush=True)
    con.commit()
    con.close()
    os.replace(tmp, out)
    print(f"-> {out} {os.path.getsize(out) / 1e9:.2f} GB, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--procs", type=int, default=8)
    a = ap.parse_args()
    run(a.out, a.limit, a.procs)
