"""twins.sqlite: for every hadith of the two corpora, the other hadiths that are the same text and carry narrator ids.

A hadith's *twin* is a hadith whose first ``KEY_WORDS`` normalised words of text are the same and which has ids in its isnad:
a Noor hadith (``narrators.sqlite`` slot rows, ids of persons) or a KG hadith whose isnad the graph tagged (``hadith_rawis``). The
preprocess finds them once; the narrator step reads the table (``icm/twins.py``) and aligns the twin's chain with the hadith's own
(``identity._fuzzy_twins``), which is where a wrong twin is rejected: its names must agree in order.

    twin(corp, hid, db, tid)   corp 'v02' | 'kg'; hid the hadith's own id (v02 id as a number, KG source id); db 'noor' | 'kg';
                               tid the twin's Noor hadith id | KG source id
    meta(k, v)                 built, counts

An opening that many hadiths share («قال رسول الله صلى الله عليه وآله وسلم من …») is no key: keys with more than ``MAX_SHARE`` hadiths
in a corpus are left out.

    python3 -m icm build twins
"""
from __future__ import annotations

import collections
import os
import sqlite3
import time

from .. import config
from ..text import tokens

KEY_WORDS = 9
MAX_SHARE = 30
MAX_TWINS = 6


def _key(matn: str):
    t = tokens(matn or "")
    return " ".join(t[:KEY_WORDS]) if len(t) >= KEY_WORDS else None


def run(dst: str = None, log=print) -> None:
    dst = dst or os.path.join(config.DATA_DIR, "twins.sqlite")
    t0 = time.time()
    noor = sqlite3.connect(f"file:{config.NARRATORS_DB}?mode=ro&immutable=1", uri=True)
    have = {r[0] for r in noor.execute("select distinct hadith_id from slot")}
    v = sqlite3.connect(f"file:{config.V02_DB}?mode=ro", uri=True)
    kc = sqlite3.connect(f"file:{config.KG_SRC}?mode=ro", uri=True)
    tagged = {r[0] for r in kc.execute("select distinct hadith_id from hadith_rawis")}
    # 1. what carries ids: Noor hadiths (by their v02 text) and tagged KG hadiths, by opening
    idx = {"noor": collections.defaultdict(list), "kg": collections.defaultdict(list)}
    own = []                                           # (corp, hid, key)
    n_v = n_k = 0
    for hid, m in v.execute("select id, matn from hadith"):
        k = _key(m)
        if not k:
            continue
        h = int(hid)
        own.append(("v02", h, k))
        n_v += 1
        nid = h - config.NOOR_OFFSET if h >= config.NOOR_OFFSET else h
        if len(str(hid)) == 9 and nid in have:
            idx["noor"][k].append(nid)
    log(f"  v02: {n_v} hadiths with an opening, {sum(len(x) for x in idx['noor'].values())} with Noor ids ({time.time() - t0:.0f}s)")
    for hid, nass, ms, me in kc.execute("select id, nass, matn_start, matn_end from hadiths where matn_start is not null"):
        k = _key(nass[ms:me])
        if not k:
            continue
        own.append(("kg", hid, k))
        n_k += 1
        if hid in tagged:
            idx["kg"][k].append(hid)
    log(f"  kg: {n_k} hadiths with an opening, {sum(len(x) for x in idx['kg'].values())} tagged ({time.time() - t0:.0f}s)")
    # 2. the pairs
    rows = []
    for corp, hid, k in own:
        for db in ("noor", "kg"):
            tids = idx[db].get(k)
            if not tids or len(tids) > MAX_SHARE:
                continue
            me = (hid - config.NOOR_OFFSET if hid >= config.NOOR_OFFSET else hid) if (corp == "v02" and db == "noor") else hid
            for t in tids[:MAX_TWINS + 1]:
                if not (t == me and ((corp == "v02" and db == "noor") or (corp == "kg" and db == "kg"))):
                    rows.append((corp, hid, db, t))
    tmp = dst + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    c = sqlite3.connect(tmp)
    c.executescript("create table twin(corp text, hid integer, db text, tid integer, primary key (corp, hid, db, tid)) without rowid;"
                    "create table meta(k text primary key, v text);")
    c.executemany("insert or ignore into twin values (?,?,?,?)", rows)
    c.executemany("insert into meta values (?,?)", [("built", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())), ("pairs", str(len(rows))),
                                                   ("hadiths", str(len({(r[0], r[1]) for r in rows})))])
    c.commit()
    c.close()
    os.replace(tmp, dst)
    log(f"  {len(rows)} pairs for {len({(r[0], r[1]) for r in rows})} hadiths -> {dst} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    run()
