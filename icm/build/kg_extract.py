"""Build kg.sqlite, a ~600 MB extract of the Hadith Knowledge Graph for the server.

Keeps what the wizard needs: books, narrator rows and statements, chains, and per hadith
the matn span (zlib), the sanad span (zlib) and a full-text index over the normalised matn.
The 1.6 GB original has no full-text index and carries diacritised full text twice.

    python3 -m icm.build.kg_extract
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
import zlib

from .. import config
from ..text import norm


def run(src: str = config.KG_SRC, dst: str = config.KG_DB) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        os.remove(dst)
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    d.execute("pragma journal_mode=off")
    d.execute("pragma synchronous=off")
    t0 = time.time()
    # plain copies of the small tables
    for t in ("books", "rawis", "aqwal", "alems"):
        cols = [r[1] for r in s.execute(f"pragma table_info({t})")]
        types = {r[1]: r[2] for r in s.execute(f"pragma table_info({t})")}
        d.execute(f"create table {t} ({', '.join(f'{c} {types[c]}' for c in cols)})")
        rows = s.execute(f"select * from {t}")
        d.executemany(f"insert into {t} values ({','.join('?' * len(cols))})", rows)
    d.execute("create index idx_aqwal_rawi on aqwal(rawi_id)")
    d.execute("create unique index idx_rawis_id on rawis(id)")
    # chains
    d.execute("create table sanads (id integer primary key, hadith_id integer, group_id integer, "
              "takhrij integer, length integer, hukm text)")
    d.executemany("insert into sanads values (?,?,?,?,?,?)", s.execute(
        "select id, hadith_id, group_id, takhrij, length, hukum from sanads"))
    d.execute("create index idx_sanads_hadith on sanads(hadith_id)")
    d.execute("create index idx_sanads_group on sanads(group_id)")
    d.execute("create table sanad_rawis (sanad_id integer, pos integer, rawi_id integer, "
              "primary key (sanad_id, pos)) without rowid")
    d.executemany("insert into sanad_rawis values (?,?,?)", s.execute(
        "select sanad_id, pos, rawi_id from sanad_rawis"))
    d.execute("create index idx_sr_rawi on sanad_rawis(rawi_id)")
    print(f"chains copied {time.time() - t0:.0f}s", flush=True)
    # hadith rows + fts
    d.execute("create table kg_hadith (id integer primary key, book_id integer, no_inbook integer, "
              "page_no integer, type text, group_id integer, takhrij integer, "
              "sanad_z blob, matn_z blob)")
    d.execute("create virtual table kg_fts using fts5(txt, tokenize='unicode61 remove_diacritics 0')")
    q = ("select id, book_id, no_inbook, page_no, type, group_id, takhrij, nass, matn_start, matn_end "
         "from hadiths order by id")
    batch, fts, n = [], [], 0
    for r in s.execute(q):
        id_, book, no, page, typ, grp, tak, nass, ms, me = r
        nass = nass or ""
        if ms is None or me is None or not (0 <= ms <= me <= len(nass)):
            ms, me = 0, len(nass)
        sanad, matn = nass[:ms], nass[ms:me]
        batch.append((id_, book, no, page, typ, grp, tak,
                      zlib.compress(sanad.encode(), 6), zlib.compress(matn.encode(), 6)))
        fts.append((id_, norm(matn)))
        n += 1
        if len(batch) >= 20000:
            d.executemany("insert into kg_hadith values (?,?,?,?,?,?,?,?,?)", batch)
            d.executemany("insert into kg_fts(rowid, txt) values (?,?)", fts)
            batch, fts = [], []
            print(f"  hadiths {n}  {time.time() - t0:.0f}s", flush=True)
    d.executemany("insert into kg_hadith values (?,?,?,?,?,?,?,?,?)", batch)
    d.executemany("insert into kg_fts(rowid, txt) values (?,?)", fts)
    d.execute("create index idx_kgh_group on kg_hadith(group_id)")
    d.execute("create index idx_kgh_takhrij on kg_hadith(takhrij)")
    d.execute("create index idx_kgh_book on kg_hadith(book_id, no_inbook)")
    # narrator name tokens (for name -> narrator candidate lookup)
    d.execute("create table kg_name_token (token text, rawi_id integer)")
    rows = []
    for rid, name, nick in s.execute("select id, name, nickname from rawis"):
        toks = set((norm(name or "") + " " + norm(nick or "")).split())
        rows.extend((t, rid) for t in toks if len(t) >= 2)
    d.executemany("insert into kg_name_token values (?,?)", rows)
    d.execute("create index idx_kgnt on kg_name_token(token)")
    d.commit()
    d.execute("pragma journal_mode=delete")
    d.execute("analyze")
    d.commit()
    d.execute("vacuum")
    d.close()
    print(f"done {time.time() - t0:.0f}s {os.path.getsize(dst) / 1e6:.0f} MB -> {dst}")


if __name__ == "__main__":
    run()
