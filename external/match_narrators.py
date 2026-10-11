"""Link the diagram's narrators to biographies in the Hadith Knowledge Graph.

Source: emadjumaah/hadith-kg (Hugging Face, CC-BY-4.0), table `rawis`
(49,819 narrator biographies) and `sanad_rawis` (577,024 isnads, 4.4M links).

Matching uses three signals:
  * name   - share of the diagram name's tokens found in the biography's name/nickname;
  * dates  - agreement between the biography's death year and our floruit;
  * chains - whether the candidate is attested in the KG's isnads as teacher or
             student of a candidate for one of our recorded neighbours.
A link is kept only when the evidence is strong enough to be called a match.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "extract"))
from hadith_phylogeny.collation import normalize_arabic

DB = os.path.join(HERE, "external", "hadith-kg.db")
STOP = {"بن", "بنت", "ابن"}           # ابن is handled as a "بن X" bigram
NOISE = re.compile(r"\([^)]*\)|[0-9٠-٩۰-۹]+|ق\b|[()،؛:.«»\"']")


def norm_name(s: str) -> str:
    s = NOISE.sub(" ", s)
    s = normalize_arabic(s)
    s = s.replace("ابي ", "ابو ").replace("ابى ", "ابو ")
    return re.sub(r"\s+", " ", s).strip()


def bare(t: str) -> str:
    """Article-insensitive token: الحسين and حسين are the same name."""
    return t[2:] if t.startswith("ال") and len(t) > 4 else t


def tokens(s: str) -> List[str]:
    return [bare(t) for t in norm_name(s).split() if t not in STOP and len(t) > 1]


def bigrams_ibn(s: str) -> List[str]:
    """'ابن وهب' -> 'بن وهب' so that it hits '... بن وهب' in a full name."""
    t = norm_name(s).split()
    return [f"بن {t[i + 1]}" for i in range(len(t) - 1) if t[i] in ("ابن", "بن")]


def load_kg(con):
    rows = con.execute("""select id, name, nickname, rank, tabaka, birth_year, death_year,
                          death_year_raw, iqama, death_place, riwaya_qty, is_bukhari, is_muslim,
                          nasab, profession, is_stub from rawis""").fetchall()
    kg = {}
    index = defaultdict(set)
    for r in rows:
        rid = r[0]
        full = norm_name(r[1] + " " + (r[2] or ""))
        kg[rid] = dict(id=rid, name=r[1], nickname=r[2], rank=r[3], tabaka=r[4],
                       birth=r[5], death=r[6], death_raw=r[7], iqama=r[8], death_place=r[9],
                       riwaya_qty=r[10], bukhari=bool(r[11]), muslim=bool(r[12]),
                       nasab=r[13], profession=r[14], stub=bool(r[15]),
                       _full=full, _tok=set(bare(t) for t in full.split() if t not in STOP))
        for t in kg[rid]["_tok"]:
            index[t].add(rid)
    return kg, index


def name_candidates(name: str, kg, index, k: int = 25) -> List[Tuple[float, int]]:
    ts = tokens(name)
    if not ts:
        return []
    pool = defaultdict(int)
    for t in ts:
        for rid in index.get(t, ()):
            pool[rid] += 1
    out = []
    ibn = bigrams_ibn(name)
    for rid, hit in pool.items():
        cov = hit / len(ts)
        if len(ts) >= 2 and hit < 2:
            continue
        if ibn and not all(b in kg[rid]["_full"] or b.replace("بن ال", "بن ") in kg[rid]["_full"]
                           for b in ibn):
            cov *= 0.6
        # our name must be the person's own name, not his father's: "انس بن مالك"
        # must not match "نضر بن انس بن مالك"
        own = [bare(t) for t in norm_name(kg[rid]["name"]).split()]
        nick = [bare(t) for t in norm_name(kg[rid]["nickname"] or "").split()]
        first = bare(norm_name(name).split()[0]) if norm_name(name) else ""
        heads = {own[0] if own else "", nick[0] if nick else ""}
        if first and first not in heads and first not in ("ابو", "ابن", "ام"):
            in_nasab = any(own[i] == first and i > 0 and own[i - 1] in ("بن", "بنت")
                           for i in range(len(own)))
            if in_nasab:
                cov *= 0.4
        # prefer biographies that do not drown our tokens in unrelated ones
        extra = max(0, len(kg[rid]["_tok"]) - hit)
        import math
        score = (cov - 0.01 * extra
                 + 0.03 * math.log10(1 + kg[rid]["riwaya_qty"])      # well-attested narrators
                 - (0.1 if kg[rid]["death"] is None else 0.0))       # undated stubs are weak
        score = min(score, 1.0 + 0.03 * math.log10(1 + kg[rid]["riwaya_qty"]))
        out.append((score, rid))
    out.sort(reverse=True)
    return out[:k]


def adjacency(con, ids: List[int]) -> Dict[Tuple[int, int], int]:
    """(teacher, student) -> number of KG isnads in which they are adjacent."""
    ids = list(set(ids))
    pos = {}
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        q = ",".join("?" * len(chunk))
        for sid, p, rid in con.execute(
                f"select sanad_id, pos, rawi_id from sanad_rawis where rawi_id in ({q})", chunk):
            pos[(sid, p)] = rid
    adj = defaultdict(int)
    for (sid, p), rid in pos.items():
        up = pos.get((sid, p + 1))          # pos ascends toward the companion = teacher
        if up is not None and up != rid:
            adj[(up, rid)] += 1
    return adj


def main():
    import to_family
    fam = to_family.build_family(os.path.join(HERE, "outputs", "diagram_graph.json"))
    con = sqlite3.connect(DB)
    kg, index = load_kg(con)

    neigh = defaultdict(set)
    for a in fam.attestations:
        for u, v in zip(a.sanad, a.sanad[1:]):
            neigh[v].add(("teacher", u))
            neigh[u].add(("student", v))

    cands = {n: name_candidates(fam.narrators[n].name, kg, index) for n in fam.narrators}
    all_ids = [rid for cs in cands.values() for _, rid in cs]
    adj = adjacency(con, all_ids)

    out = {}
    for n, cs in cands.items():
        nar = fam.narrators[n]
        fl = nar.activity()[1] if nar.activity() else None
        best = None
        scored = []
        for ns, rid in cs:
            r = kg[rid]
            ctx = 0
            ctx_detail = []
            for role, m in neigh[n]:
                for _, mid in cands.get(m, []):
                    c = adj.get((mid, rid), 0) if role == "teacher" else adj.get((rid, mid), 0)
                    if c:
                        ctx += 1
                        ctx_detail.append(dict(role=role, node=m, kg_id=mid, chains=c))
                        break
            chrono = 0.0
            if r["death"] and fl is not None:
                gap = abs(r["death"] - fl)
                if getattr(nar, "date_source", None) == "diagram":
                    lo, hi = nar.death
                    gap = 0 if lo - 3 <= r["death"] <= hi + 3 else min(abs(r["death"] - lo),
                                                                       abs(r["death"] - hi))
                    chrono = 0.3 if gap <= 3 else (0.0 if gap <= 8 else -0.6)
                else:
                    chrono = 0.15 if gap <= 30 else (0.0 if gap <= 70 else -0.4)
            total = ns + chrono + 0.35 * min(ctx, 3)
            scored.append((total, ns, ctx, chrono, rid, ctx_detail))
        scored.sort(key=lambda t: -t[0])
        if scored:
            total, ns, ctx, chrono, rid, det = scored[0]
            dated = kg[rid]["death"] is not None
            if ns >= 0.88 and ctx >= 1 and chrono >= 0 and (dated or ctx >= 2):
                conf = "high"
            elif ns >= 0.72 and (ctx >= 1 or chrono > 0) and chrono >= 0:
                conf = "medium"
            elif ns >= 0.6 and ctx >= 2:
                conf = "medium"
            else:
                conf = "low"
            r = {k: v for k, v in kg[rid].items() if not k.startswith("_")}
            out[n] = dict(confidence=conf, name_score=round(ns, 3), context_links=ctx,
                          chrono=chrono, record=r, context=det,
                          alternatives=[dict(id=t[4], name=kg[t[4]]["name"],
                                             death=kg[t[4]]["death"], score=round(t[0], 3))
                                        for t in scored[1:4]])
        else:
            out[n] = dict(confidence="none")

    # KG attestation of every recorded edge (teacher -> student)
    edges = {}
    for a in fam.attestations:
        for u, v in zip(a.sanad, a.sanad[1:]):
            mu, mv = out.get(u, {}), out.get(v, {})
            if mu.get("confidence") in ("high", "medium") and mv.get("confidence") in ("high", "medium"):
                edges[f"{u}>{v}"] = adj.get((mu["record"]["id"], mv["record"]["id"]), 0)
            else:
                edges[f"{u}>{v}"] = None
    linked = {n: v["record"]["id"] for n, v in out.items()
              if v.get("confidence") in ("high", "medium")}
    by_kg = defaultdict(list)
    for n, rid in linked.items():
        by_kg[rid].append(n)
    pairs = []
    for (t, st), c in adj.items():
        for u in by_kg.get(t, []):
            for v in by_kg.get(st, []):
                if u != v:
                    pairs.append([u, v, c])
    same_person = {n: [m for m in by_kg[rid] if m != n] for n, rid in linked.items()
                   if len(by_kg[rid]) > 1}
    res = dict(source="emadjumaah/hadith-kg (Hugging Face, CC-BY-4.0)", narrators=out,
               edge_kg_chains=edges, kg_pairs=pairs, same_person=same_person)
    json.dump(res, open(os.path.join(HERE, "outputs", "narrator_links.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    c = Counter(v["confidence"] for v in out.values())
    print("narrators", len(out), dict(c))
    att = [v for v in edges.values() if v is not None]
    print("recorded edges with both ends linked:", len(att), "of", len(edges),
          "| attested in KG isnads:", sum(1 for v in att if v > 0))


if __name__ == "__main__":
    main()
