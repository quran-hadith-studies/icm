"""Read-only questions ICM asks the wiki. Import-light on purpose: standard library + narwiki.config only.

    from narwiki import lookup
    if lookup.available():
        lookup.persons_for(["noor:12", "kg:5739"])
        # {"noor:12": {"person_id": 3, "name": "...", "also": ["kg:5739"]}, "kg:5739": {...}}

Ids that belong to no active person (or a missing / unreadable database) are simply absent from the result.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Dict, List

from . import config


def available() -> bool:
    """True when the wiki database file exists."""
    return os.path.isfile(config.DB)


def persons_for(ids: List[str]) -> Dict[str, dict]:
    """Map each ``"noor:N"`` / ``"kg:N"`` (any ``kind:id``) of an active wiki person to
    ``{"person_id": int, "name": str, "also": [the person's other "kind:id", wiki ids excluded]}``.
    Merged persons are followed to the person they were merged into. Returns {} on any error."""
    try:
        if not ids or not available():
            return {}
        c = sqlite3.connect(f"file:{config.DB}?mode=ro", uri=True, timeout=10)
        try:
            out: Dict[str, dict] = {}
            cache: Dict[int, dict] = {}
            for nid in dict.fromkeys(str(i) for i in ids):
                kind, _, ext = nid.partition(":")
                if not kind or not ext:
                    continue
                r = c.execute("select person_id from person_ext where kind=? and ext_id=?", (kind, ext)).fetchone()
                if r is None:
                    continue
                pid, seen = r[0], set()
                while pid not in seen:                       # follow merged_into
                    seen.add(pid)
                    p = c.execute("select status, merged_into from person where id=?", (pid,)).fetchone()
                    if p is None:
                        pid = None
                        break
                    if p[0] == "merged" and p[1]:
                        pid = p[1]
                    else:
                        break
                if pid is None:
                    continue
                if pid not in cache:
                    p = c.execute("select display_name, status from person where id=?", (pid,)).fetchone()
                    if p is None or p[1] != "active":
                        cache[pid] = None
                    else:
                        ext = [f"{k}:{e}" for k, e in c.execute(
                            "select kind, ext_id from person_ext where person_id=? and kind!='wiki' order by kind, ext_id", (pid,))]
                        cache[pid] = dict(person_id=pid, name=p[0], ext=ext)
                hit = cache[pid]
                if hit:
                    out[nid] = dict(person_id=hit["person_id"], name=hit["name"], also=[x for x in hit["ext"] if x != nid])
            return out
        finally:
            c.close()
    except Exception:                                        # noqa: BLE001 - ICM must never fail because of the wiki
        return {}
