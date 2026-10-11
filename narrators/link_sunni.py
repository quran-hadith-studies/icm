"""Stage 3 (Sunni): link Noor RaviIds to the Sunni narrator sets → ``link`` rows with
``target_kind`` kg | hawramani | muslimscholars | arsanad, and their facts → ``fact``.

* hadith-kg (``external/hadith-kg.db``): ``match_narrators.name_candidates`` (token
  coverage with the father/son guard), chronology from the narrator's resolved death year
  with the "interpolated" tolerances (+0.15 / 0 / −0.4 at 30 / 70 years), chain context
  = Noor neighbours whose KG candidates are adjacent in the KG isnāds. Same tiers as
  ``match_narrators.main``.
* hawramani / muslimscholars: through the Zenodo entity-resolution tables, keyed on the
  normalised Arabic name (HIGH tier rows only), plus a direct name match on
  ``hawramani_narrators.csv`` / ``muslimscholars_scholars.csv`` when the KG link fails.
* AR-Sanad: name match on ``arsanad_narrators.csv`` (all name variants), death year ±3
  when both sides have one.

Only narrators with ``n_slots ≥ min_slots`` are tried (companions, tābiʿūn and shared
early narrators are the target; most Imāmī narrators are absent from these sets).
"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from .common import DATASETS, EXTERNAL, insert_many, norm_key, name_tokens, open_db
from .facts import PRIORITY, resolve

sys.path.insert(0, EXTERNAL)
import match_narrators as mn  # noqa: E402

KG_DB = os.path.join(EXTERNAL, "hadith-kg.db")
ZENODO = os.path.join(DATASETS, "zenodo-narrator-er", "extracted", "zenodo")
ARSANAD = os.path.join(DATASETS, "itqan", "repo", "src", "arsanad_narrators.csv")
csv.field_size_limit(10 ** 9)


def _tokset(name: str) -> frozenset:
    return frozenset(name_tokens(name))


def _death_from(s: str) -> Optional[int]:
    m = re.search(r"(\d{1,4})", s or "")
    return int(m.group(1)) if m and 1 <= int(m.group(1)) <= 1450 else None


def link_kg(con, ravis: List[sqlite3.Row], res: Dict[int, dict]) -> Dict[int, int]:
    kg_con = sqlite3.connect(KG_DB)
    kg, index = mn.load_kg(kg_con)
    infall = {r[0] for r in con.execute("select ravi_id from infallible")}
    neigh = defaultdict(set)
    for r in con.execute("select teacher, student from adjacency where n_exact >= 2"):
        if r[0] not in infall and r[1] not in infall:
            neigh[r[1]].add(("teacher", r[0]))
            neigh[r[0]].add(("student", r[1]))
    cands = {r["ravi_id"]: mn.name_candidates(r["name"], kg, index) for r in ravis}
    all_ids = [rid for cs in cands.values() for _, rid in cs]
    adj = mn.adjacency(kg_con, all_ids)
    rows, linked = [], {}
    stats = defaultdict(int)
    for r in ravis:
        rid = r["ravi_id"]
        cs = cands[rid]
        if not cs:
            stats["none"] += 1
            continue
        d = (res.get(rid) or {}).get("death")
        fl = (d[0] + d[1]) / 2 if d else None
        scored = []
        for ns, kid in cs:
            k = kg[kid]
            ctx, det = 0, []
            for role, m in neigh.get(rid, ()):
                for _, mid in cands.get(m, []):
                    c = adj.get((mid, kid), 0) if role == "teacher" else adj.get((kid, mid), 0)
                    if c:
                        ctx += 1
                        det.append(dict(role=role, ravi=m, kg_id=mid, chains=c))
                        break
            chrono = 0.0
            if k["death"] and fl is not None:
                gap = abs(k["death"] - fl)
                chrono = 0.15 if gap <= 30 else (0.0 if gap <= 70 else -0.4)
            scored.append((ns + chrono + 0.35 * min(ctx, 3), ns, ctx, chrono, kid, det))
        scored.sort(key=lambda t: -t[0])
        total, ns, ctx, chrono, kid, det = scored[0]
        dated = kg[kid]["death"] is not None
        if ns >= 0.88 and ctx >= 1 and chrono >= 0 and (dated or ctx >= 2):
            conf = "high"
        elif ns >= 0.72 and (ctx >= 1 or chrono > 0) and chrono >= 0:
            conf = "medium"
        elif ns >= 0.6 and ctx >= 2:
            conf = "medium"
        else:
            conf = "low"
        stats[conf] += 1
        rec = {k2: v for k2, v in kg[kid].items() if not k2.startswith("_")}
        rows.append((rid, "kg", str(kid), "sunni", conf, round(total, 3), round(ns, 3), ctx,
                     json.dumps({"record": rec, "chrono": chrono, "context": det,
                                 "alternatives": [[t[4], kg[t[4]]["name"], round(t[0], 3)] for t in scored[1:4]]},
                                ensure_ascii=False)))
        if conf in ("high", "medium"):
            linked[rid] = kid
    insert_many(con, "link", ["ravi_id", "target_kind", "target_id", "method", "confidence", "score",
                              "name_score", "ctx", "detail_json"], rows)
    print(f"(e) hadith-kg: {len(ravis)} tried → {dict(stats)}")
    return linked, kg


def link_er_sets(con, ravis: List[sqlite3.Row], res: Dict[int, dict]) -> None:
    """hawramani / muslimscholars / AR-Sanad by normalised name (+ death-year agreement)."""
    by_name: Dict[str, Dict[str, List[dict]]] = {"hawramani": defaultdict(list), "muslimscholars": defaultdict(list),
                                                 "arsanad": defaultdict(list)}
    p = os.path.join(ZENODO, "hawramani_narrators.csv")
    if os.path.exists(p):
        for row in csv.DictReader(open(p, encoding="utf-8")):
            by_name["hawramani"][norm_key(row["name_ar"])].append(
                {"id": row["url"], "name": row["name_ar"], "death": _death_from(row.get("death_year_ah")),
                 "grades": row.get("grades")})
    p = os.path.join(ZENODO, "muslimscholars_scholars.csv")
    if os.path.exists(p):
        for row in csv.DictReader(open(p, encoding="utf-8")):
            nm = re.sub(r"\(.*", "", row.get("name_ar") or "").strip()
            if nm:
                by_name["muslimscholars"][norm_key(nm)].append(
                    {"id": row["id"], "name": nm, "name_en": row.get("name_en"), "death": _death_from(row.get("death_year_h")),
                     "places": row.get("places_of_stay"), "death_place": row.get("death_place"),
                     "teachers": row.get("teacher_ids"), "students": row.get("student_ids")})
    if os.path.exists(ARSANAD):
        for row in csv.DictReader(open(ARSANAD, encoding="utf-8")):
            variants = [row["name"]]
            try:
                variants += json.loads(row["namings"].replace("'", '"'))
            except Exception:  # noqa: BLE001
                pass
            rec = {"id": row["id"], "name": row["name"], "death": _death_from(row.get("death_year")),
                   "living_city": row.get("living_city"), "death_city": row.get("death_city"),
                   "journey_city": row.get("journey_city"), "tabaqa": row.get("tabaqa"),
                   "rank": row.get("Ibnhajar_rank")}
            for v in set(variants):
                by_name["arsanad"][norm_key(v)].append(rec)
    rows = []
    stats = defaultdict(int)
    for r in ravis:
        rid = r["ravi_id"]
        d = (res.get(rid) or {}).get("death")
        keys = {norm_key(r["name"])}
        for kind, table in by_name.items():
            hits = []
            for k in keys:
                hits.extend(table.get(k, []))
            if not hits:
                continue
            uniq = {h["id"]: h for h in hits}
            for h in uniq.values():
                agree = None
                if d and h.get("death"):
                    agree = abs(h["death"] - (d[0] + d[1]) / 2) <= 3
                conf = "high" if agree else ("medium" if agree is None and len(uniq) == 1 else "low")
                stats[(kind, conf)] += 1
                rows.append((rid, kind, str(h["id"]), "sunni_er", conf, 1.0, 1.0, 0,
                             json.dumps({"record": h, "death_agree": agree, "n_candidates": len(uniq)}, ensure_ascii=False)))
    insert_many(con, "link", ["ravi_id", "target_kind", "target_id", "method", "confidence", "score",
                              "name_score", "ctx", "detail_json"], rows)
    for kind in by_name:
        n = {c: v for (k, c), v in stats.items() if k == kind}
        if n:
            print(f"(e) {kind}: {n}")


def facts_from_sunni(con) -> None:
    con.execute("delete from fact where source in ('kg', 'hawramani', 'muslimscholars', 'arsanad')")
    from .places import Places
    pl = Places(con)
    rows = []
    for l in con.execute("""select ravi_id, target_kind, target_id, detail_json from link
                            where method in ('sunni', 'sunni_er') and confidence in ('high', 'medium')"""):
        rec = json.loads(l["detail_json"]).get("record") or {}
        src = l["target_kind"]
        death = rec.get("death")
        if death:
            rows.append((l["ravi_id"], "death", str(death), death, death, src, None, rec.get("name", ""), f"{src}:{l['target_id']}", PRIORITY.get(src, 9)))
        for field in ("iqama", "death_place", "living_city", "death_city", "journey_city", "places"):
            v = rec.get(field)
            if not v or v == "-":
                continue
            for part in re.split(r"[،,;/]| and ", str(v)):
                c = pl.from_toponym(part.strip())
                if c:
                    rows.append((l["ravi_id"], "city", c, None, None, src, None, str(v)[:120], f"{src}:{field}", PRIORITY.get(src, 9)))
    insert_many(con, "fact", ["ravi_id", "kind", "value", "lo", "hi", "source", "entry_id", "evidence", "pattern", "priority"], rows)
    print(f"(e) facts from Sunni sets: {len(rows)}")


def run(db_path: str, min_slots: int = 20) -> None:
    con = open_db(db_path)
    con.execute("delete from link where method in ('sunni', 'sunni_er')")
    infall = {r[0] for r in con.execute("select ravi_id from infallible")}
    ravis = [r for r in con.execute("select * from ravi where n_slots >= ? order by n_slots desc", (min_slots,))
             if r["ravi_id"] not in infall]
    res = resolve(con, [r["ravi_id"] for r in ravis])
    link_kg(con, ravis, res)
    link_er_sets(con, ravis, res)
    facts_from_sunni(con)
    con.commit()
