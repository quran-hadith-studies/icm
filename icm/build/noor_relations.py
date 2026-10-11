"""noor_relations.sqlite: Noor's own relation data (group, clusters, related ids).

This data exists only in hadith-hub/books/afreshte*/hadiths.jsonl; neither hadith-db-v02
nor the served FTS database carries it. It is used as a *recall signal* by the search:
Noor's ``related_ahadith`` list (stored as raw Noor HadithIds, add NOOR_OFFSET for v02 ids)
is truncated and carries no relation type, so nothing
here decides class membership.

    python3 -m icm.build.noor_relations
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import struct
import time
import zlib

from .. import config


def run(dst: str = config.NOOR_REL_DB) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        os.remove(dst)
    d = sqlite3.connect(dst)
    d.execute("pragma journal_mode=off")
    d.execute("create table hadith (hid integer primary key, group_id integer, n_cluster_same integer)")
    d.execute("create table cluster_member (cluster_id integer, hid integer)")
    d.execute("create table related (hid integer primary key, ids blob)")   # zlib(uint32 little-endian array)
    t0, n = time.time(), 0
    for path in sorted(glob.glob(os.path.join(config.HUB_BOOKS, "afreshte*", "hadiths.jsonl"))):
        h, c, r = [], [], []
        with open(path, encoding="utf-8") as f:
            for line in f:
                o = json.loads(line)
                hid = int(o["id"])
                h.append((hid, o.get("group_id"), o.get("same_cluster_count")))
                for cid in o.get("cluster_ids") or []:
                    c.append((cid, hid))
                rel = o.get("related_ahadith") or []
                if isinstance(rel, dict):
                    rel = [rel]
                ids = sorted({int(x["HadithId"]) for x in rel if x.get("HadithId")})
                if ids:
                    r.append((hid, zlib.compress(struct.pack("<%dI" % len(ids), *ids), 6)))
        d.executemany("insert or ignore into hadith values (?,?,?)", h)
        d.executemany("insert into cluster_member values (?,?)", c)
        d.executemany("insert or ignore into related values (?,?)", r)
        n += len(h)
    d.execute("create index ix_h_group on hadith(group_id)")
    d.execute("create index ix_cm_cluster on cluster_member(cluster_id)")
    d.execute("create index ix_cm_hid on cluster_member(hid)")
    d.commit()
    d.execute("vacuum")
    print(f"{n} hadiths, {time.time() - t0:.0f}s, {os.path.getsize(dst) / 1e6:.0f} MB -> {dst}")


if __name__ == "__main__":
    run()
