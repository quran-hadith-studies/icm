"""Stage 2c: ThaqalaynData narrators (CC0) → ``thaqalayn_narrator``, ``thaqalayn_adjacency``.

``https://thaqalayndata.netlify.app/people/narrators/index.json`` lists every narrator id
with its name and counts; ``people/narrators/<id>.json`` holds the sub-chains the narrator
occurs in (``subchains.<key>.narrator_ids`` is a chain fragment in collector → source
order, like Noor). Files are cached under ``external/datasets/thaqalayn/narrators/``.

Used only as an independent cross-check of Noor's chain resolution on the Four Books and
as an id bridge; it carries no biographies.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from collections import Counter, defaultdict

from .common import DATASETS, insert_many, open_db, reset_tables, table_count

BASE = "https://thaqalayndata.netlify.app/people/narrators/"
ROOT = os.path.join(DATASETS, "thaqalayn", "narrators")
UA = "hadith-project narrator pipeline/0.1"
TABLES = ["thaqalayn_narrator", "thaqalayn_adjacency"]


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch(delay: float = 0.25) -> None:
    os.makedirs(ROOT, exist_ok=True)
    idx_path = os.path.join(ROOT, "index.json")
    if not os.path.exists(idx_path):
        json.dump(_get(BASE + "index.json"), open(idx_path, "w"), ensure_ascii=False)
    index = json.load(open(idx_path))["data"]
    got = skipped = 0
    for tid in sorted(index, key=int):
        path = os.path.join(ROOT, f"{tid}.json")
        if os.path.exists(path):
            skipped += 1
            continue
        for attempt in range(3):
            try:
                d = _get(f"{BASE}{tid}.json")
                json.dump(d, open(path, "w"), ensure_ascii=False)
                got += 1
                break
            except Exception as e:  # noqa: BLE001
                print(f"  {tid}: {e}", file=sys.stderr)
                time.sleep(3 * (attempt + 1))
        time.sleep(delay)
        if got and got % 250 == 0:
            print(f"  {got} fetched, {skipped} cached", file=sys.stderr)
    print(f"thaqalayn: {got} fetched, {skipped} cached, {len(index)} in index")


def load(db_path: str) -> None:
    con = open_db(db_path)
    reset_tables(con, TABLES)
    index = json.load(open(os.path.join(ROOT, "index.json")))["data"]
    rows = [(int(tid), v["titles"].get("ar"), v["titles"].get("en"), v.get("narrations"))
            for tid, v in index.items()]
    insert_many(con, "thaqalayn_narrator", ["tq_id", "name_ar", "name_en", "n_narrations"], rows)
    adj = Counter()
    seen = set()
    last = Counter()
    for tid in index:
        path = os.path.join(ROOT, f"{tid}.json")
        if not os.path.exists(path):
            continue
        d = json.load(open(path))["data"]
        for key, sc in (d.get("subchains") or {}).items():
            ids = sc.get("narrator_ids") or []
            paths = tuple(sc.get("verse_paths") or [])
            sig = (tuple(ids), paths)
            if sig in seen:
                continue
            seen.add(sig)
            if ids:
                last[ids[-1]] += 1
            for a, b in zip(ids, ids[1:]):
                adj[(b, a)] += max(1, len(paths))     # teacher = next toward the source
    insert_many(con, "thaqalayn_adjacency", ["teacher", "student", "n"], [(t, s, n) for (t, s), n in adj.items()])
    con.commit()
    names = {int(t): v["titles"].get("ar") for t, v in index.items()}
    print(f"thaqalayn narrators {table_count(con, 'thaqalayn_narrator')}, adjacency {table_count(con, 'thaqalayn_adjacency')}")
    print("most frequent chain-final ids (should be Imams):", [(i, names.get(i), n) for i, n in last.most_common(6)])
