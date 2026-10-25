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


def _ro():
    return sqlite3.connect(f"file:{config.DB}?mode=ro", uri=True, timeout=10)


def _death(c, pid: int):
    """[lo, hi] hijri death years of person ``pid`` from the current fields, or None."""
    v = {f: x for f, x in c.execute(
        "select field, value from person_field where person_id=? and status='current' and field in ('death_lo','death_hi') order by id", (pid,))}
    try:
        lo, hi = (float(v[k]) if k in v else None for k in ("death_lo", "death_hi"))
    except ValueError:
        return None
    lo, hi = (lo if lo is not None else hi), (hi if hi is not None else lo)
    return None if lo is None else [lo, hi]


def wiki_people(ids: List[str]) -> Dict[str, dict]:
    """``{"wiki:<token>": {person_id, name, death}}`` for the persons made by hand in the wiki (they have no Noor / KG
    id, so ICM names them by their ``wiki:`` anchor). Merged persons are followed; unknown / retired ids are absent."""
    out: Dict[str, dict] = {}
    try:
        toks = [i for i in dict.fromkeys(str(i) for i in ids) if i.startswith("wiki:")]
        if not toks or not available():
            return {}
        c = _ro()
        try:
            for nid in toks:
                r = c.execute("select person_id from person_ext where kind='wiki' and ext_id=?", (nid[5:],)).fetchone()
                pid, seen = (r[0] if r else None), set()
                while pid is not None and pid not in seen:
                    seen.add(pid)
                    p = c.execute("select display_name, status, merged_into from person where id=?", (pid,)).fetchone()
                    if p is None:
                        pid = None
                    elif p[1] == "merged" and p[2]:
                        pid = p[2]
                    else:
                        if p[1] == "active":
                            out[nid] = dict(person_id=pid, name=p[0], death=_death(c, pid))
                        break
        finally:
            c.close()
    except Exception:                                        # noqa: BLE001
        return {}
    return out


def search(q: str, limit: int = 15) -> List[dict]:
    """Active persons whose names (display name and alias fields) match ``q``, as
    ``{person_id, name, n_narr, ext: ["noor:12", ...], death}``; wiki anchors are left out of ``ext``. [] on any error."""
    try:
        if not available():
            return []
        from . import store                                  # lazy: pulls the name normaliser
        expr = store.fts_query(q)
        if not expr:
            return []
        c = _ro()
        try:
            ids = [r[0] for r in c.execute(
                "select p.id from person p join (select rowid as pid from person_fts where person_fts match ? limit 2000) f on f.pid=p.id "
                "where p.status='active' order by (p.name_key = ?) desc, coalesce(p.n_narr, 0) desc, p.id limit ?", (expr, store.name_key(q), int(limit)))]
            out = []
            for pid in ids:
                p = c.execute("select display_name, n_narr from person where id=?", (pid,)).fetchone()
                ext = [f"{k}:{e}" for k, e in c.execute("select kind, ext_id from person_ext where person_id=? order by kind, ext_id", (pid,))]
                out.append(dict(person_id=pid, name=p[0], n_narr=p[1] or 0, ext=[x for x in ext if not x.startswith("wiki:")],
                                wiki=[x for x in ext if x.startswith("wiki:")], death=_death(c, pid)))
            return out
        finally:
            c.close()
    except Exception:                                        # noqa: BLE001 - ICM must never fail because of the wiki
        return []
