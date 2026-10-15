"""Step 5: the recorded transmission graph, as the ``Family`` the model reads.

Input: the resolved identities (node id per narrator per chain), the alignment rows (what each
witness says), and the user's phylogeny decisions (date intervals, chain edits).

Chains are stored collector-first (as written); the model wants source -> collector, so they are
reversed here. The attributed speaker (the Prophet) is not in the chain: the chain starts at the
first narrator, as in the diagram run. Metadata M comes from the narrator database
(``narrators.facts.resolve``: death interval, cities; Noor adjacency as documented teachers) and,
for Sunni narrators, from the Hadith Knowledge Graph.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from narrators.facts import resolve as resolve_facts
from hadith_phylogeny.chronology import interpolate
from . import config, corpus, narrator_versions
from .identity import INFALLIBLE_IDS, _nconn
from .text import norm


def _group_key(w: dict, quoted: Optional[str]) -> str:
    title = quoted or w.get("title") or w.get("work")
    return "g:" + " ".join(norm(str(title)).split())


def run(alignment: dict, resolved: dict, phylogeny: Optional[dict] = None) -> dict:
    ph = phylogeny or {}
    wmeta = {w["wid"]: w for w in alignment["witnesses"]}
    X = alignment["X"]
    flags: List[dict] = []

    # --- chains -> node lists (source -> collector) ---------------------------------------------
    chains: List[dict] = []
    occ: Dict[str, List[str]] = {}                     # nid -> its places "aid|i" (step 4 opens a name by its place)
    for ch in resolved["chains"]:
        if ch["wid"] not in X:
            continue
        nids = [e["nid"] for e in reversed(ch["elems"]) if not e.get("dropped")]   # a dropped place is no narrator
        for e in ch["elems"]:
            if not e.get("dropped"):
                occ.setdefault(e["nid"], []).append(f"{ch['aid']}|{e['i']}")
        if ch["aid"] in ph.get("chain_edits", {}):
            nids = list(ph["chain_edits"][ch["aid"]])
        seen, dd = set(), []
        for n in nids:
            if n in seen:
                flags.append(dict(kind="repeated_node", ref=ch["aid"], wid=ch["wid"], nodes=[n], message=f"{n} appears twice in one chain; later occurrence dropped"))
                continue
            seen.add(n)
            dd.append(n)
        if len(dd) < 2:
            flags.append(dict(kind="chain_too_short", ref=ch["aid"], wid=ch["wid"], message="chain has fewer than two narrators; witness kept only if no other chain"))
        chains.append(dict(aid=ch["aid"], wid=ch["wid"], nids=dd, quoted=ch.get("quoted_work"), source=ch["source"]))
    by_wid = defaultdict(list)
    for c in chains:
        by_wid[c["wid"]].append(c)
    keep = []
    for wid, cs in by_wid.items():
        good = [c for c in cs if len(c["nids"]) >= 2]
        keep.extend(good or cs[:1])
        if not good:
            flags.append(dict(kind="witness_without_chain", ref=wid, wid=wid, message="no chain with two narrators; the witness is anchored at its collector only"))
    chains = keep

    # --- metadata ------------------------------------------------------------------------------
    all_nids = sorted({n for c in chains for n in c["nids"]})
    info: Dict[str, dict] = {n: dict(name=None, death=None, date_source=None, cities=[], imams=[], ids={}) for n in all_nids}
    # a person is every database id of its node (``ext``, from the narrator database version); facts are gathered over all of them
    ext: Dict[str, List[str]] = {}
    back: Dict[str, str] = {}                          # member id -> its node
    for n in all_nids:
        mem = list((resolved["nodes"].get(n) or {}).get("ext") or [])
        if n.startswith(("noor:", "kg:")) and n not in mem:
            mem.insert(0, n)
        ext[n] = sorted(mem, key=lambda x: (x != n, x.split(":")[0] != "noor", x))
        for m in ext[n]:
            back.setdefault(m, n)
    noor_ids = sorted({int(m.split(":")[1]) for ms in ext.values() for m in ms if m.startswith("noor:")})
    kg_ids = sorted({int(m.split(":")[1]) for ms in ext.values() for m in ms if m.startswith("kg:")})
    nrows: Dict[int, sqlite3.Row] = {}
    krows: Dict[int, sqlite3.Row] = {}
    nc = _nconn()
    facts: dict = {}
    if noor_ids:
        facts = resolve_facts(nc, noor_ids)
        for r in nc.execute("select ravi_id, name, mazhab, grade, tabaqa_json from ravi where ravi_id in (%s)" % ",".join("?" * len(noor_ids)), noor_ids):
            nrows[r["ravi_id"]] = r
    teachers: Dict[str, set] = defaultdict(set)
    if noor_ids:
        q = "select teacher, student from adjacency where n_exact >= 2 and teacher in (%s) and student in (%s)" % (",".join("?" * len(noor_ids)), ",".join("?" * len(noor_ids)))
        for r in nc.execute(q, noor_ids + noor_ids):
            a_, b_ = back.get(f"noor:{r[0]}"), back.get(f"noor:{r[1]}")
            if a_ and b_ and a_ != b_:
                teachers[b_].add(a_)
    nc.close()
    if kg_ids:
        kc = corpus.conn("kg")
        for r in kc.execute("select id,name,death_year,iqama,rank,tabaka from rawis where id in (%s)" % ",".join("?" * len(kg_ids)), kg_ids):
            krows[r["id"]] = r
        q = "select teacher, student from kg_adjacency where teacher in (%s) and student in (%s)" % (",".join("?" * len(kg_ids)), ",".join("?" * len(kg_ids)))
        for r in kc.execute(q, kg_ids + kg_ids):
            a_, b_ = back.get(f"kg:{r[0]}"), back.get(f"kg:{r[1]}")
            if a_ and b_ and a_ != b_:
                teachers[b_].add(a_)
    for n in all_nids:
        i = info[n]
        nm = [int(m.split(":")[1]) for m in ext[n] if m.startswith("noor:") and int(m.split(":")[1]) in nrows]
        km = [int(m.split(":")[1]) for m in ext[n] if m.startswith("kg:") and int(m.split(":")[1]) in krows]
        if nm:                                         # Noor's description first (name, madhhab, grade, imams); the KG adds cities and rank
            r = nrows[nm[0]]
            f = facts.get(nm[0], {})
            i.update(name=r["name"], mazhab=r["mazhab"], grade=r["grade"], imams=f.get("imams", []), cities=list(f.get("cities", [])))
        cities = []
        for k in km:
            k_ = krows[k]
            cities += [c.strip() for c in (k_["iqama"] or "").replace("،", ",").split(",") if c.strip()]
        if km:
            i.update(name=i["name"] or krows[km[0]]["name"], rank=krows[km[0]]["rank"])
            i["cities"] = list(dict.fromkeys(i["cities"] + cities))
        for m in nm:                                   # Noor's resolved death first, else a KG one
            if facts.get(m, {}).get("death"):
                i["death"], i["date_source"] = tuple(facts[m]["death"]), facts[m].get("death_source")
                break
        else:
            for k in km:
                if krows[k]["death_year"]:
                    i["death"] = (float(krows[k]["death_year"]),) * 2
                    i["date_source"] = "hadith-kg"
                    break
        ids = {}
        for kind in ("noor", "kg", "dirayah"):
            got = sorted(int(m.split(":")[1]) for m in ext[n] if m.startswith(kind + ":") and m.split(":")[1].isdigit())
            if got:
                ids[kind] = got[0] if len(got) == 1 else got
        i["ids"] = ids
    names = {n["id"]: n["names"][0] for n in resolved["nodes"].values()}
    for n in all_nids:
        info[n]["name"] = info[n]["name"] or names.get(n) or n
        info[n]["tiers"] = resolved["nodes"].get(n, {}).get("tiers", [])
        info[n]["occ"] = occ.get(n, [])
    # collector death from the work's author when the collector has no date
    for c in chains:
        w = wmeta.get(c["wid"], {})
        coll = c["nids"][-1]
        if info[coll]["death"] is None and w.get("death"):
            info[coll]["death"] = (float(w["death"]), float(w["death"]))
            info[coll]["date_source"] = "author-death"
    for nid, (lo, hi) in ph.get("dates", {}).items():
        if nid in info:
            info[nid]["death"] = (float(lo), float(hi))
            info[nid]["date_source"] = "user"

    # --- chronology -----------------------------------------------------------------------------
    def dated(n):
        d = info[n]["death"]
        return (d[0] + d[1]) / 2.0 if d else None
    years = interpolate([c["nids"] for c in chains], dated, first_default=60.0)

    narrators = {}
    for n in all_nids:
        d = info[n]["death"]
        ys = years.get(n)
        if d is not None:
            death, y = d, (d[0] + d[1]) / 2.0
        else:
            y = sum(ys) / len(ys) if ys else None
            death = (y - 5.0, y + 5.0) if y is not None else None
            info[n]["date_source"] = "interpolated" if ys else None
        narrators[n] = dict(name=info[n]["name"], death=list(death) if death else None,
                            active=[y - 45.0, y] if y is not None else None,
                            locations=sorted(set(info[n]["cities"])),
                            teachers=sorted(t for t in teachers.get(n, ()) if t in info), meetings=[], layer=None)

    # --- attestations ---------------------------------------------------------------------------
    atts = []
    for c in chains:
        w = wmeta[c["wid"]]
        g = _group_key(w, c.get("quoted"))
        atts.append(dict(id=c["aid"], wid=c["wid"], sanad=c["nids"], row=list(X[c["wid"]]), anchor=c["nids"][-1],
                         collection=w.get("title") or w.get("work"), independence_group=g))

    # --- consistency flags ----------------------------------------------------------------------
    for a in atts:
        s = a["sanad"]
        for u, v in zip(s, s[1:]):
            du, dv = narrators[u]["death"], narrators[v]["death"]
            if du and dv and info[u]["date_source"] not in ("interpolated", None) and info[v]["date_source"] not in ("interpolated", None) \
                    and du[0] > dv[1] + 5:
                flags.append(dict(kind="chronology", ref=a["id"], wid=a["wid"], nodes=[u, v], message=f"{narrators[u]['name']} (d. {du[0]:g}) is recorded as the teacher of "
                                                                          f"{narrators[v]['name']} (d. {dv[1]:g})"))
    for f in flags:
        f["title"] = (wmeta.get(f.get("wid")) or {}).get("title")
    undated = [n for n in all_nids if info[n]["date_source"] in (None, "interpolated")]
    spec_keys = ("name", "n_states", "cls", "kind", "labels")
    variants = [{k: u[k] for k in spec_keys} for u in alignment["units"]]
    return dict(
        name=alignment["seed"], narrators=narrators, attestations=atts, variants=variants,
        nodes=info_out(info), flags=flags, dropped=resolved.get("dropped", []),
        stats=dict(narrators=len(narrators), attestations=len(atts), witnesses=len({a["wid"] for a in atts}),
                   dated=len(narrators) - len(undated), name_only=sum(1 for n in all_nids if n.split(":")[0] in ("name", "rel", "anon")),
                   units=len(variants)))


def known_deaths(nids: Sequence[str], version=None) -> Dict[str, dict]:
    """{nid: {death: [lo, hi], source}} for the narrators the databases date (what ``run`` uses before the user's dates).
    A narrator the ``version`` merges takes Noor's death first, else the KG's, over all its ids."""
    version = version or narrator_versions.RAW
    members: Dict[str, List[str]] = {}
    for n in nids:
        members[n] = [m["id"] for m in version.members(n)] or [n]
    allm = {m for ms in members.values() for m in ms}
    noor = sorted({int(m.split(":")[1]) for m in allm if m.startswith("noor:") and m.split(":")[1].isdigit()})
    kg = sorted({int(m.split(":")[1]) for m in allm if m.startswith("kg:") and m.split(":")[1].isdigit()})
    got: Dict[str, dict] = {}
    if noor:
        nc = _nconn()
        for rid, f in resolve_facts(nc, noor).items():
            if f.get("death"):
                got[f"noor:{rid}"] = dict(death=list(f["death"]), source=f.get("death_source"))
        nc.close()
    if kg:
        kc = corpus.conn("kg")
        for r in kc.execute("select id, death_year from rawis where death_year is not null and id in (%s)" % ",".join("?" * len(kg)), kg):
            got[f"kg:{r[0]}"] = dict(death=[float(r[1]), float(r[1])], source="hadith-kg")
    out: Dict[str, dict] = {}
    for n, ms in members.items():
        for m in sorted(ms, key=lambda x: (x.split(":")[0] != "noor", x != n, x)):
            if m in got:
                out[n] = got[m]
                break
    return out


def info_out(info: Dict[str, dict]) -> Dict[str, dict]:
    out = {}
    for n, i in info.items():
        out[n] = dict(name=i["name"], date_source=i["date_source"], death=list(i["death"]) if i["death"] else None,
                      cities=i["cities"], imams=i["imams"], tiers=i.get("tiers", []), ids=i["ids"],
                      mazhab=i.get("mazhab"), grade=i.get("grade"), rank=i.get("rank"), occ=i.get("occ", []))
    return out
