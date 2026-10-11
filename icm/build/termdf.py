"""termdf.sqlite: document frequency of every term in the served full-text index and in kg_fts.

The served database is opened read-only on the server, so the ``fts5vocab`` table cannot be
created there; the frequencies are dumped here instead. The search uses them to pick the
most distinctive terms of each clause (low DF) for its queries.

    python3 -m icm.build.termdf
"""
from __future__ import annotations

import os
import sqlite3
import time

from .. import config


def _vocab(path: str, fts: str):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.execute(f"create virtual table temp.v using fts5vocab('main', '{fts}', 'row')")
    return {t: df for t, df in con.execute("select term, doc from temp.v")}


def run(dst: str = config.TERMDF_DB) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        os.remove(dst)
    t0 = time.time()
    v02 = _vocab(config.V02_DB, "hadith_fts")
    print(f"v02 vocab {len(v02)} terms {time.time() - t0:.0f}s", flush=True)
    kg = _vocab(config.KG_DB, "kg_fts") if os.path.exists(config.KG_DB) else {}
    print(f"kg vocab {len(kg)} terms {time.time() - t0:.0f}s", flush=True)
    d = sqlite3.connect(dst)
    d.execute("create table termdf (term text primary key, v02 integer not null default 0, kg integer not null default 0) without rowid")
    terms = set(v02) | set(kg)
    d.executemany("insert into termdf values (?,?,?)", ((t, v02.get(t, 0), kg.get(t, 0)) for t in terms if len(t) >= 2))
    d.commit()
    d.execute("vacuum")
    print(f"{len(terms)} terms -> {dst} {os.path.getsize(dst) / 1e6:.0f} MB")


if __name__ == "__main__":
    run()
