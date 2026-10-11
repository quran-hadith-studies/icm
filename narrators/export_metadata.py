"""Stage 5: export narrator metadata for the model, and the full JSON dump for the MCP.

    python3 -m narrators export --ravis 203,3915,... --out meta.json --links links.json
    python3 -m narrators export --hadith-ids 106382,316143 --out meta.json --links links.json
    python3 -m narrators export --dump external/narrators.json

* ``meta.json`` is keyed by ``str(ravi_id)`` in the shape ``hadith_phylogeny.data.load_metadata``
  reads: name, death [lo, hi], locations, teachers (RaviIds with ``adjacency.n_exact ≥ 2``
  inside the exported set), meetings [], layer null. Tabaqa is **not** put in ``layer``:
  ``candidates.compatible`` rejects ``u.layer >= v.layer``, which would forbid two companions
  of one Imam from transmitting to each other.
* ``links.json`` has the shape ``extract/to_family.py`` reads (``confidence``,
  ``record.death`` as one number, ``record.iqama`` joined with «،», ``kg_pairs``).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional

from .common import EXTERNAL, open_db, write_json
from .facts import resolve


def ravis_for_hadiths(con, hadith_ids: List[int]) -> List[int]:
    q = "select distinct ravi_id from slot where ravi_id is not null and hadith_id in (%s)" % ",".join("?" * len(hadith_ids))
    return [r[0] for r in con.execute(q, hadith_ids)]


def best_links(con, ravi_ids: List[int]) -> Dict[int, dict]:
    q = """select l.*, e.book, e.heading from link l left join entry e on l.target_kind = 'entry' and e.entry_id = cast(l.target_id as integer)
           where l.ravi_id in (%s) and l.confidence in ('high', 'medium')
           order by l.ravi_id, case l.confidence when 'high' then 0 else 1 end, l.score desc""" % ",".join("?" * len(ravi_ids))
    out = {}
    for r in con.execute(q, ravi_ids):
        out.setdefault(r["ravi_id"], dict(r))
    return out


def family_metadata(con, ravi_ids: List[int]):
    """Return ``(meta, links)`` dicts for a set of RaviIds (see module docstring)."""
    ravi_ids = sorted(set(ravi_ids))
    if not ravi_ids:
        return {}, {"source": "analysis/external/narrators.sqlite", "narrators": {},
                    "edge_kg_chains": {}, "kg_pairs": [], "same_person": {}}
    res = resolve(con, ravi_ids)
    names = {r["ravi_id"]: r["name"] for r in con.execute(
        "select ravi_id, name from ravi where ravi_id in (%s)" % ",".join("?" * len(ravi_ids)), ravi_ids)}
    idset = set(ravi_ids)
    teachers: Dict[int, set] = {rid: set() for rid in ravi_ids}
    pairs = []
    for r in con.execute("select teacher, student, n_exact from adjacency where n_exact >= 2"):
        if r["teacher"] in idset and r["student"] in idset:
            teachers[r["student"]].add(r["teacher"])
            pairs.append([str(r["teacher"]), str(r["student"]), r["n_exact"]])
    meta = {}
    for rid in ravi_ids:
        x = res.get(rid, {})
        d = x.get("death")
        meta[str(rid)] = {
            "name": names.get(rid, str(rid)),
            "death": [float(d[0]), float(d[1])] if d else None,
            "locations": x.get("cities", []),
            "teachers": sorted(str(t) for t in teachers[rid]),
            "meetings": [],
            "layer": None,
            "death_source": x.get("death_source"),
            "imams": x.get("imams", []),
        }
    best = best_links(con, ravi_ids)
    narrators = {}
    for rid in ravi_ids:
        x = res.get(rid, {})
        l = best.get(rid)
        d = x.get("death")
        narrators[str(rid)] = {
            "confidence": (l["confidence"] if l else "none"),
            "name_score": (l["name_score"] if l else None),
            "context_links": (l["ctx"] if l else 0),
            "record": {
                "id": rid, "name": names.get(rid, ""),
                "death": (d[0] + d[1]) / 2.0 if d else None,
                "death_interval": list(d) if d else None,
                "iqama": "،".join(x.get("cities", [])),
                "tabaqa": x.get("imams", []),
                "source": (l["book"] or l["target_kind"]) if l else None,
                "heading": (l["heading"] if l else None),
            },
        }
    links = {"source": "analysis/external/narrators.sqlite", "narrators": narrators,
             "edge_kg_chains": {}, "kg_pairs": pairs, "same_person": {}}
    return meta, links


def export_family(con, ravi_ids: List[int], out_meta: str, out_links: Optional[str]) -> None:
    meta, links = family_metadata(con, ravi_ids)
    write_json(out_meta, meta)
    n_dated = sum(1 for m in meta.values() if m["death"])
    n_city = sum(1 for m in meta.values() if m["locations"])
    print(f"meta: {len(meta)} narrators, {n_dated} dated, {n_city} with a city → {out_meta}")
    if out_links:
        write_json(out_links, links)
        print(f"links: {out_links}")


def dump_all(con, path: str) -> None:
    """Full dump: ravi rows with resolved facts, all links, all facts, adjacency (n_exact ≥ 1)."""
    res = resolve(con)
    ravi = {}
    for r in con.execute("select * from ravi"):
        d = dict(r)
        for k in ("laqab_json", "tabaqa_json", "wafat_json", "wiladat_json", "jarh_json", "extra_json"):
            if d.get(k):
                d[k[:-5]] = json.loads(d[k])
            d.pop(k, None)
        d["resolved"] = res.get(r["ravi_id"])
        ravi[r["ravi_id"]] = d
    links = [dict(r) for r in con.execute("select * from link")]
    for l in links:
        if l.get("detail_json"):
            l["detail"] = json.loads(l.pop("detail_json"))
    facts = [dict(r) for r in con.execute("select * from fact")]
    adjacency = [[r[0], r[1], r[2], r[3], r[4]] for r in con.execute(
        "select teacher, student, n_exact, w_amb, n_hadith from adjacency where n_exact >= 1")]
    entries = {r["entry_id"]: dict(r) for r in con.execute(
        "select entry_id, book, entry_no, vol, page_start, page_end, imam, heading from entry")}
    write_json(path, {"schema": 1, "ravi": ravi, "links": links, "facts": facts, "adjacency": adjacency,
                      "entries": entries}, indent=None)
    print(f"dump: {len(ravi)} ravi, {len(links)} links, {len(facts)} facts → {path} ({os.path.getsize(path) / 1e6:.0f} MB)")
