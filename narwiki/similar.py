"""Merge suggestions from names, dates and chain context (laptop side):

    python3 -m narwiki similar [--db PATH] [--only name_chrono,noor_dup,kg_dup]

* ``name_chrono`` (Noor ↔ KG): a port of ``narrators/link_sunni.py link_kg``. For every Noor narrator the KG
  narrators sharing its rarest name tokens are scored like ``match_narrators.name_candidates`` (token coverage,
  the father/son guard, «ابن X» bigrams, popularity, undated penalty); then chronology (+0.15 / 0 / −0.4 for a
  death gap ≤ 30 / ≤ 70 / > 70 years against Noor's resolved death or Imam period) and chain context (+0.35 per
  Noor teacher/student whose own KG candidates are adjacent to the candidate in the KG chains, at most 3).
  Tiers as in link_kg; only high and medium become suggestions. Imāmī narrators need at least one context hit
  (most of them are absent from the Sunni graph, and a common name with a plausible date is not enough).
* ``noor_dup`` / ``kg_dup``: two narrators of the same database with the same normalised name or the same set
  of name tokens whose death years agree within 5 years or are both unknown (groups of more than 8 namesakes
  are skipped: a name that common says nothing).

Nothing is merged: every pair becomes a ``merge_suggestion`` (status proposed) with the evidence the UI shows.
Pairs that already have a suggestion of any kind (also a dismissed one) are not suggested again; re-running
replaces only the still-proposed suggestions of the kinds it computes.
"""
from __future__ import annotations

import math
import sqlite3
import time
from collections import defaultdict
from itertools import combinations
from typing import Dict, List, Optional, Sequence, Tuple

from . import config, store

ALL = ("name_chrono", "noor_dup", "kg_dup")
MAX_GROUP = 8


def _ro(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _cm():
    from narrators import common
    return common


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
def load_kg(kc) -> Tuple[dict, Dict[str, set]]:
    cm = _cm()
    kg, index = {}, defaultdict(set)
    for r in kc.execute("select id, name, nickname, death_year, riwaya_qty, rank, tabaka from rawis where is_stub=0"):
        full = cm.norm_name((r["name"] or "") + " " + (r["nickname"] or ""))
        tok = {cm.bare(t) for t in full.split() if t not in cm.STOP and len(t) > 1}
        own = [cm.bare(t) for t in cm.norm_name(r["name"] or "").split()]
        nick = [cm.bare(t) for t in cm.norm_name(r["nickname"] or "").split()]
        kg[r["id"]] = dict(id=r["id"], name=r["name"], nickname=r["nickname"], death=r["death_year"], q=r["riwaya_qty"] or 0,
                           rank=r["rank"], tabaka=r["tabaka"], full=full, tok=tok, own=own, nick=nick,
                           ntok=frozenset(cm.name_tokens(r["name"] or "")))
        for t in tok:
            index[t].add(r["id"])
    return kg, index


def name_candidates(name: str, kg: dict, index: Dict[str, set], k: int = 25, max_pool: int = 4000) -> List[Tuple[float, int, dict]]:
    """``match_narrators.name_candidates`` over the KG narrators sharing the query's two rarest tokens."""
    cm = _cm()
    ts = cm.name_tokens(name)
    if not ts:
        return []
    present = sorted({t for t in ts if t in index}, key=lambda t: len(index[t]))
    if not present or (len(ts) == 1 and len(index[present[0]]) > 50):
        return []
    pool = set()
    for t in present[:2]:
        pool |= index[t]
    if len(pool) > max_pool:
        return []
    nn = cm.norm_name(name)
    first = cm.bare(nn.split()[0]) if nn else ""
    qw = [cm.bare(t) for t in nn.split()]
    ibn = cm.bigrams_ibn(name)
    out = []
    for rid in pool:
        r = kg[rid]
        hit = sum(1 for t in ts if t in r["tok"])
        if len(ts) >= 2 and hit < 2:
            continue
        if first and first not in ("ابو", "ابن", "ام") and first not in r["tok"]:
            continue                                    # his own name must be there (identity._cov's guard)
        cov = hit / len(ts)
        notes = []
        if ibn and not all(b in r["full"] or b.replace("بن ال", "بن ") in r["full"] for b in ibn):
            cov *= 0.6
            notes.append("ibn")
        heads = {r["own"][0] if r["own"] else "", r["nick"][0] if r["nick"] else ""}
        own = r["own"]
        if first and first not in heads and first not in ("ابو", "ابن", "ام"):
            if any(own[i] == first and i > 0 and own[i - 1] in ("بن", "بنت") for i in range(len(own))):
                cov *= 0.4                              # our name is his father's
                notes.append("father")
        elif first == "عبد" and len(qw) > 1 and "father" not in notes:
            # compound «عبد X»: «عبد الرحمن بن عوف» must not match «عبد الله بن عبد الرحمن بن عوف»
            heads2 = {" ".join(r["own"][:2]), " ".join(r["nick"][:2])}
            q2 = f"عبد {qw[1]}"
            if q2 not in heads2 and any(" ".join(own[i:i + 2]) == q2 and i > 0 and own[i - 1] in ("بن", "بنت") for i in range(len(own))):
                cov *= 0.4
                notes.append("father")
        extra = max(0, len(r["tok"]) - hit)
        pop = 0.03 * math.log10(1 + r["q"])
        score = cov - 0.01 * extra + pop - (0.1 if r["death"] is None else 0.0)
        score = min(score, 1.0 + pop)
        out.append((score, rid, dict(cov=round(cov, 3), hit=hit, n_tokens=len(ts), extra=extra, notes=notes)))
    out.sort(key=lambda x: (-x[0], x[1]))
    return out[:k]


# ---------------------------------------------------------------------------
# Noor <-> KG
# ---------------------------------------------------------------------------
def name_chrono(c, nc, kc, log=print) -> int:
    from narrators.facts import resolve
    t0 = time.time()
    kg, index = load_kg(kc)
    infall = {r[0] for r in nc.execute("select ravi_id from infallible")}
    ravis = {r["ravi_id"]: dict(r) for r in nc.execute("select ravi_id, name, mazhab, grade, n_slots from ravi")}
    res = resolve(nc)
    neigh = defaultdict(set)
    for t, s in nc.execute("select teacher, student from adjacency where n_exact >= 2"):
        if t not in infall and s not in infall:
            neigh[s].add(("teacher", t))
            neigh[t].add(("student", s))
    cands = {rid: name_candidates(r["name"] or "", kg, index) for rid, r in ravis.items() if rid not in infall}
    log(f"  name candidates for {len(cands)} Noor narrators ({time.time() - t0:.0f}s)")
    adj = {}
    for s, t, n in kc.execute("select student, teacher, n from kg_adjacency"):
        adj[(t, s)] = n
    log(f"  KG adjacency {len(adj)} pairs ({time.time() - t0:.0f}s)")
    n_new = 0
    tiers = defaultdict(int)
    for rid, cs in cands.items():
        if not cs:
            continue
        d = (res.get(rid) or {}).get("death")
        dsrc = (res.get(rid) or {}).get("death_source")
        fl = (d[0] + d[1]) / 2 if d else None
        scored = []
        for ns, kid, nd in cs:
            k = kg[kid]
            ctx, det = 0, []
            for role, m in sorted(neigh.get(rid, ())):
                for _, mid, _ in cands.get(m, []):
                    n = adj.get((mid, kid), 0) if role == "teacher" else adj.get((kid, mid), 0)
                    if n:
                        ctx += 1
                        det.append(dict(role=role, ravi=m, ravi_name=ravis.get(m, {}).get("name"), kg_id=mid, kg_name=kg[mid]["name"], chains=n))
                        break
            chrono = 0.0
            gap = None
            if k["death"] and fl is not None:
                gap = abs(k["death"] - fl)
                chrono = 0.15 if gap <= 30 else (0.0 if gap <= 70 else -0.4)
            scored.append((ns + chrono + 0.35 * min(ctx, 3), ns, ctx, chrono, gap, kid, det, nd))
        scored.sort(key=lambda x: (-x[0], x[5]))
        for total, ns, ctx, chrono, gap, kid, det, nd in scored[:1]:        # the best candidate only, as link_kg
            dated = kg[kid]["death"] is not None
            if ns >= 0.88 and ctx >= 1 and chrono >= 0 and (dated or ctx >= 2):
                conf = "high"
            elif ns >= 0.72 and (ctx >= 1 or chrono > 0) and chrono >= 0:
                conf = "medium"
            elif ns >= 0.6 and ctx >= 2:
                conf = "medium"
            else:
                conf = "low"
            if conf != "low" and ctx == 0 and (ravis[rid]["mazhab"] or "") == "امامی":
                conf = "low"
            tiers[conf] += 1
            if conf == "low":
                continue
            a = store.by_ext(c, "noor", rid)
            b = store.by_ext(c, "kg", kid)
            if a is None or b is None or a == b or _has_any(c, a, b):
                continue
            k = kg[kid]
            ev = dict(method="name + chronology + chain context (port of narrators/link_sunni.py link_kg)", confidence=conf,
                      score=round(total, 3), name_score=round(ns, 3), name=nd, chrono=chrono, death_gap=round(gap, 1) if gap is not None else None,
                      ctx=ctx, context=det[:6],
                      noor=dict(id=f"noor:{rid}", name=ravis[rid]["name"], mazhab=ravis[rid]["mazhab"], grade=ravis[rid]["grade"],
                                death=list(d) if d else None, death_source=dsrc, n_slots=ravis[rid]["n_slots"]),
                      kg=dict(id=f"kg:{kid}", name=k["name"], nickname=k["nickname"], death=k["death"], rank=k["rank"], tabaka=k["tabaka"],
                              riwaya_qty=k["q"]),
                      alternatives=[[x[5], kg[x[5]]["name"], round(x[0], 3)] for x in scored if x[5] != kid][:3])
            n_new += store.add_suggestion(c, a, b, "name_chrono", total, ev)
    c.commit()
    log(f"  name_chrono tiers {dict(tiers)}; {n_new} new suggestions ({time.time() - t0:.0f}s)")
    return n_new


def _has_any(c, a: int, b: int) -> bool:
    a, b = min(a, b), max(a, b)
    return c.execute("select 1 from merge_suggestion where a=? and b=? limit 1", (a, b)).fetchone() is not None


# ---------------------------------------------------------------------------
# near-duplicates inside one database
# ---------------------------------------------------------------------------
def _head(name: str) -> tuple:
    """Own name and father's name, compound names («عبد الله», «أبو بكر») counted as one."""
    t = _cm().name_tokens(name or "")
    out, i = [], 0
    while i < len(t) and len(out) < 2:
        if t[i] in ("عبد", "ابو", "ام", "ابن") and i + 1 < len(t):
            out.append(t[i] + " " + t[i + 1]); i += 2
        else:
            out.append(t[i]); i += 1
    nn = _cm().norm_name(name or "").split()
    return (("ابن",) if nn and nn[0] == "ابن" else ()) + tuple(out)        # «ابن يحيى» is not «يحيى»


def _dups(c, kind: str, recs: Dict[int, dict], neigh: Dict[int, set], sug_kind: str, log=print,
          need_shared_if_undated: bool = False) -> int:
    """recs: ext id -> {name, key, toks, death (lo, hi) or None, info...}"""
    groups: Dict[Tuple[str, object], List[int]] = defaultdict(list)
    for i, r in recs.items():
        if len(r["toks"]) < 2:                         # a single name («معاوية») says nothing
            continue
        if r["key"]:
            groups[("key", r["key"])].append(i)
        groups[("toks", r["head"], r["toks"])].append(i)      # same tokens, same own name and father's name
    seen = set()
    n_new = skipped = 0
    for g, ids in groups.items():
        if len(ids) < 2:
            continue
        if len(ids) > MAX_GROUP:
            skipped += 1
            continue
        for x, y in combinations(sorted(ids), 2):
            if (x, y) in seen:
                continue
            seen.add((x, y))
            rx, ry = recs[x], recs[y]
            dx, dy = rx["death"], ry["death"]
            if dx and dy:
                gap = abs((dx[0] + dx[1]) / 2 - (dy[0] + dy[1]) / 2)
                if gap > 5:
                    continue
            elif dx or dy:
                continue
            else:
                gap = None
            same_key = rx["key"] == ry["key"]
            shared = sorted((neigh.get(x, set()) & neigh.get(y, set())))
            if gap is None and not shared and need_shared_if_undated:
                continue                                # two undated namesakes with nothing in common
            score = (0.6 if same_key else 0.5) + (0.2 if gap is not None else 0.0) + 0.1 * min(len(shared), 3) - 0.03 * (len(ids) - 2)
            a, b = store.by_ext(c, kind, x), store.by_ext(c, kind, y)
            if a is None or b is None or a == b or _has_any(c, a, b):
                continue
            ev = dict(method=f"same {'normalised name' if same_key else 'set of name tokens'} in {kind}, "
                             f"{'death years within 5' if gap is not None else 'both undated'}",
                      same_name_key=same_key, death_gap=gap, group_size=len(ids), shared_neighbours=len(shared),
                      shared=[dict(id=f"{kind}:{n}", name=recs.get(n, {}).get("name")) for n in shared[:6]],
                      a=dict(id=f"{kind}:{x}", **rx["info"]), b=dict(id=f"{kind}:{y}", **ry["info"]))
            n_new += store.add_suggestion(c, a, b, sug_kind, score, ev)
    c.commit()
    log(f"  {sug_kind}: {n_new} new suggestions ({skipped} name groups larger than {MAX_GROUP} skipped)")
    return n_new


def noor_dup(c, nc, log=print) -> int:
    from narrators.facts import resolve
    cm = _cm()
    res = resolve(nc)
    infall = {r[0] for r in nc.execute("select ravi_id from infallible")}
    recs = {}
    for r in nc.execute("select ravi_id, name, name_key, mazhab, grade, n_slots from ravi"):
        if r["ravi_id"] in infall:
            continue
        rs = res.get(r["ravi_id"]) or {}
        d = rs.get("death") if rs.get("death_source") != "imam" else None
        recs[r["ravi_id"]] = dict(name=r["name"], key=r["name_key"] or cm.norm_key(r["name"] or ""),
                                  toks=tuple(sorted(cm.name_tokens(r["name"] or ""))), head=_head(r["name"]), death=d,
                                  info=dict(name=r["name"], mazhab=r["mazhab"], grade=r["grade"], n_slots=r["n_slots"],
                                            death=list(d) if d else None, death_source=rs.get("death_source") if d else None))
    neigh = defaultdict(set)
    for t, s in nc.execute("select teacher, student from adjacency where n_exact >= 2"):
        neigh[s].add(t)
        neigh[t].add(s)
    return _dups(c, "noor", recs, neigh, "noor_dup", log)


def kg_dup(c, kc, log=print) -> int:
    cm = _cm()
    recs = {}
    for r in kc.execute("select id, name, nickname, death_year, rank, tabaka, riwaya_qty from rawis where is_stub=0"):
        d = (r["death_year"], r["death_year"]) if r["death_year"] else None
        recs[r["id"]] = dict(name=r["name"], key=cm.norm_key(r["name"] or ""), toks=tuple(sorted(cm.name_tokens(r["name"] or ""))), head=_head(r["name"]), death=d,
                             info=dict(name=r["name"], nickname=r["nickname"], death=r["death_year"], rank=r["rank"], tabaka=r["tabaka"],
                                       riwaya_qty=r["riwaya_qty"]))
    neigh = defaultdict(set)
    for s, t in kc.execute("select student, teacher from kg_adjacency where n >= 2"):
        neigh[s].add(t)
        neigh[t].add(s)
    return _dups(c, "kg", recs, neigh, "kg_dup", log, need_shared_if_undated=True)


def run(path: Optional[str] = None, only: Sequence[str] = ALL, log=print) -> dict:
    t0 = time.time()
    c = store.connect(path)
    with c:
        q = ",".join("?" * len(only))
        n_old = c.execute(f"delete from merge_suggestion where status='proposed' and kind in ({q})", tuple(only)).rowcount
    log(f"removed {n_old} proposed suggestions of {', '.join(only)}")
    nc, kc = _ro(config.narrators_db()), _ro(config.kg_db())
    out = {}
    if "name_chrono" in only:
        out["name_chrono"] = name_chrono(c, nc, kc, log)
    if "noor_dup" in only:
        out["noor_dup"] = noor_dup(c, nc, log)
    if "kg_dup" in only:
        out["kg_dup"] = kg_dup(c, kc, log)
    with c:
        c.execute("insert or replace into meta(k, v) values ('similar', ?)", (store.now(),))
    out["seconds"] = round(time.time() - t0, 1)
    c.close()
    return out
