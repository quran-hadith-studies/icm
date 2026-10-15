"""Narrator cards: what is known about a candidate person, with the evidence.

    card("noor:2323") / card("kg:3795")  ->  dict for the identities and graph pages

Noor ids: the narrator row (grade, madhhab, tabaqa), every fact with its source, the clause it
came from and the matching pattern, the rijal entries the id is linked to (high/medium), and its
most frequent teachers and students. Which KG ids are the same person is the narrator database version's
business (``narrator_versions``; the API adds ``merged``), not this card's. KG ids: the narrator row, scholars' judgements, and the
frequent teachers and students in the KG's chains.
"""
from __future__ import annotations

import json
from typing import Optional

from . import corpus
from .identity import INFALLIBLE_IDS, _nconn


def card(nid: str, entry_chars: int = 1800) -> dict:
    kind, _, raw = nid.partition(":")
    if not raw.isdigit() or kind not in ("noor", "kg"):
        return dict(id=nid, kind="name-only", note="no biography is linked to this name")
    n = int(raw)
    return _noor(n, entry_chars) if kind == "noor" else _kg(n)


def _noor(rid: int, entry_chars: int) -> dict:
    c = _nconn()
    r = c.execute("select * from ravi where ravi_id=?", (rid,)).fetchone()
    if r is None:
        return dict(id=f"noor:{rid}", kind="noor", missing=True)
    j = lambda s: json.loads(s) if s else None
    out = dict(id=f"noor:{rid}", kind="noor", name=r["name"], mazhab=r["mazhab"], grade=r["grade"], kunya=r["kunya"],
               laqab=j(r["laqab_json"]), tabaqa=j(r["tabaqa_json"]), eval_summary=r["eval_summary"], jarh=j(r["jarh_json"]),
               tawthiq=r["tawthiq"], n_slots=r["n_slots"], n_hadith=r["n_hadith"], infallible=rid in INFALLIBLE_IDS)
    out["facts"] = [dict(kind=f["kind"], value=f["value"], lo=f["lo"], hi=f["hi"], source=f["source"], evidence=f["evidence"],
                         pattern=f["pattern"]) for f in c.execute(
        "select * from fact where ravi_id=? order by priority, fact_id limit 40", (rid,))]
    out["entries"] = [dict(book=e["book"], heading=e["heading"], vol=e["vol"], page=e["page_start"], confidence=e["confidence"],
                           method=e["method"], body=(e["body"] or "")[:entry_chars]) for e in c.execute(
        "select e.book, e.heading, e.vol, e.page_start, e.body, l.confidence, l.method from link l join entry e on "
        "l.target_kind='entry' and e.entry_id=cast(l.target_id as integer) where l.ravi_id=? and l.confidence in ('high','medium') "
        "order by case l.confidence when 'high' then 0 else 1 end limit 6", (rid,))]
    out["teachers"] = _neighbours(c, "select a.teacher as o, a.n_exact as n, r.name from adjacency a join ravi r on r.ravi_id=a.teacher "
                                     "where a.student=? and a.n_exact>=2 order by a.n_exact desc limit 8", rid)
    out["students"] = _neighbours(c, "select a.student as o, a.n_exact as n, r.name from adjacency a join ravi r on r.ravi_id=a.student "
                                     "where a.teacher=? and a.n_exact>=2 order by a.n_exact desc limit 8", rid)
    c.close()
    return out


def _neighbours(c, sql: str, rid: int):
    return [dict(id=f"noor:{r['o']}", name=r["name"], n=r["n"]) for r in c.execute(sql, (rid,))]


def _kg(rid: int) -> dict:
    c = corpus.conn("kg")
    r = c.execute("select * from rawis where id=?", (rid,)).fetchone()
    if r is None:
        return dict(id=f"kg:{rid}", kind="kg", missing=True)
    out = dict(id=f"kg:{rid}", kind="kg", name=r["name"], nickname=r["nickname"], rank=r["rank"], tabaka=r["tabaka"],
               birth=r["birth_year"], death=r["death_year"], iqama=r["iqama"], death_place=r["death_place"], nasab=r["nasab"],
               profession=r["profession"], has_tadlis=bool(r["has_tadlis"]), has_ikhtilat=bool(r["has_ikhtilat"]),
               narrations=r["riwaya_qty"])
    out["aqwal"] = [dict(alem=a["name"], death=a["death_year"], qawl=a["qawl"]) for a in c.execute(
        "select a.qawl, l.name, l.death_year from aqwal a join alems l on l.id=a.alem_id where a.rawi_id=? "
        "order by l.death_year limit 20", (rid,))]
    out["teachers"] = [dict(id=f"kg:{x['teacher']}", name=x["name"], n=x["n"]) for x in c.execute(
        "select a.teacher, a.n, r.name from kg_adjacency a join rawis r on r.id=a.teacher where a.student=? order by a.n desc limit 8", (rid,))]
    out["students"] = [dict(id=f"kg:{x['student']}", name=x["name"], n=x["n"]) for x in c.execute(
        "select a.student, a.n, r.name from kg_adjacency a join rawis r on r.id=a.student where a.teacher=? order by a.n desc limit 8", (rid,))]
    return out
