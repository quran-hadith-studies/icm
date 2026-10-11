"""Step 3: alignment of the selected witnesses and the variant units of the model.

Witnesses of one hadith reorder its clauses, so a single left-to-right multiple alignment
of whole matns is unreliable. Each *clause* (a part of the seed, plus clauses that two or
more witnesses share and the seed lacks) is aligned on its own:

* the witness segment for a clause is located order-free (``similarity.align_clause``);
* the segments of one clause are multiply aligned with ``msa.star_align`` -> a grid per clause;
* variant units come from the grids:
    - one presence unit per clause (the clause is in the witness or not),
    - inside a present clause: runs of adjacent columns with the same presence pattern
      (words some witnesses omit) and substitution columns (the witnesses' words differ).
  A witness that abbreviates ("وساق الحديث", "بنحوه") is NA for the clauses it does not
  show; leading and trailing gaps of a segment are NA (truncation is not absence).

Rows of X use ``text_model.NA = -1``.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

from .msa import char_ratio, star_align
from .similarity import FUZZ, align_clause, align_pair, match_key
from .text import norm, tokens
from narrators.arabic_numbers import parse_arabic_number

NA = -1
MAX_UNITS = 40
MAX_STATES = 6
FORMULA = set(norm("قال رسول الله صلی الله علیه وآله وسلم النبی ص ع علیه السلام عن أنه قال انه ان و").split())


def _markers() -> List[List[str]]:
    p = os.path.join(os.path.dirname(__file__), "data", "abridgement_markers.json")
    d = json.load(open(p, encoding="utf-8"))
    return [norm(x).split() for x in d["phrases"]]


def find_marker(toks: Sequence[str]) -> Optional[int]:
    """Token index where an abridgement marker starts, or None."""
    best = None
    for ph in _markers():
        n = len(ph)
        for i in range(len(toks) - n + 1):
            if list(toks[i:i + n]) == ph and (n > 1 or i >= 3):
                best = i if best is None else min(best, i)
    return best


def cluster_tokens(toks: Sequence[Optional[str]]) -> List[List[str]]:
    """Group tokens that differ only by clitics/spelling (frequency order)."""
    cnt = Counter(t for t in toks if t)
    clusters: List[List[str]] = []
    for t, _ in cnt.most_common():
        for cl in clusters:
            rep = cl[0]
            if match_key(t) == match_key(rep) or char_ratio(t, rep) >= FUZZ:
                cl.append(t)
                break
        else:
            clusters.append([t])
    return clusters


# ---------------------------------------------------------------------------
def run(search: dict, selected: Sequence[str]) -> dict:
    seed = search["seed"]["wid"]
    s_tok = search["tokens"]
    clause_defs = [dict(i=c["i"], kind="seed", text=c["text"], tokens=s_tok[c["span"][0]:c["span"][1]]) for c in search["clauses"]]
    cand = {c["wid"]: c for c in search["candidates"]}
    for c in list(cand.values()):
        for w in c.get("copies", []):
            cand.setdefault(w, c)
    wits: List[dict] = [dict(wid=seed, tokens=s_tok, matn=search["seed"]["matn"], title=search["seed"]["title"],
                             author=search["seed"]["author"], death=search["seed"]["death"],
                             tradition=search["seed"]["tradition"], work=search["seed"]["work"], cls="seed",
                             copies=search["seed"].get("copies", []))]
    flags: List[dict] = []
    for w in selected:
        if w == seed:
            continue
        c = cand.get(w)
        if c is None:
            flags.append(dict(kind="unknown_witness", ref=w, message="selected witness is not among the search candidates"))
            continue
        if any(x["wid"] == c["wid"] for x in wits):
            continue
        wits.append(dict(wid=c["wid"], tokens=c["window_text"].split(), matn=c["matn"], title=c["title"],
                         author=c["author"], death=c["death"], tradition=c["tradition"], work=c["work"],
                         cls=c["suggested"], copies=c.get("copies", [])))
    for w in wits:
        w["marker"] = find_marker(tokens(w["matn"]))

    # --- extra clauses: unmatched runs shared by >= 2 witnesses --------------------------------
    extras = _discover_extras(clause_defs, wits)
    for k, ex in enumerate(extras["clauses"]):
        clause_defs.append(dict(i=len(clause_defs), kind="extra", text=" ".join(ex["tokens"]), tokens=ex["tokens"],
                                support=ex["support"]))

    # --- per clause: segment of every witness, grid, presence ------------------------------------
    segs: Dict[Tuple[int, str], dict] = {}
    for c in clause_defs:
        for w in wits:
            if w["wid"] == seed and c["kind"] == "seed":
                segs[(c["i"], w["wid"])] = dict(state="present", cov=1.0, tokens=list(c["tokens"]),
                                                aligned=list(c["tokens"]), span=None)
                continue
            r = align_clause(c["tokens"], w["tokens"])
            n = max(len(c["tokens"]), 1)
            cov = sum(r["match_a"]) / n
            sub = sum(r["sub_a"])
            state = ("present" if cov >= 0.6 else "absent" if cov < 0.25 and sub < 2 else "unclear")
            seg_toks, span = [], None
            if r["m"]:
                # the slot of the clause: the matched span plus as many tokens on each side as the
                # clause has unmatched tokens there (a substituted word sits next to the match)
                mi = [i for i, f in enumerate(r["match_a"]) if f]
                pad_l, pad_r = mi[0], len(c["tokens"]) - 1 - mi[-1]
                lo = max(0, r["span_b"][0] - pad_l)
                hi = min(len(w["tokens"]), r["span_b"][1] + pad_r)
                while lo < r["span_b"][0] and w["tokens"][lo] in FORMULA:      # "قال رسول الله" is not part of the slot
                    lo += 1
                while hi > r["span_b"][1] and w["tokens"][hi - 1] in FORMULA:
                    hi -= 1
                seg_toks, span = w["tokens"][lo:hi], [lo, hi]
            segs[(c["i"], w["wid"])] = dict(
                state=state, cov=round(cov, 3), span=span, tokens=seg_toks,
                aligned=r["aligned_b"] if r["m"] else [None] * len(c["tokens"]))

    units: List[dict] = []
    grids: List[dict] = []
    dropped_orth = 0
    for c in clause_defs:
        ci = c["i"]
        pres: Dict[str, int] = {}
        for w in wits:
            sg = segs[(ci, w["wid"])]
            v = 1 if sg["state"] == "present" else 0 if sg["state"] == "absent" else NA
            if v == 0 and w["marker"] is not None:
                v = NA                              # abbreviated: not showing a clause is not evidence
            pres[w["wid"]] = v
        # presence unit of the clause
        known = [v for v in pres.values() if v != NA]
        if len(set(known)) > 1:
            units.append(_binary_unit(f"c{ci}", c["text"], pres, "clause", ci))
        # grid + column units inside the clause
        holders = [w["wid"] for w in wits if pres[w["wid"]] == 1]
        num_units, num_cols = _number_units(c, segs, wits)
        units.extend(num_units)
        grid, col_units, orth = _clause_grid(c, holders, segs, wits, skip_tokens=num_cols)
        dropped_orth += orth
        grids.append(dict(clause=ci, kind=c["kind"], text=c["text"], witnesses=holders, columns=grid))
        units.extend(col_units)

    # --- keep the most informative units -----------------------------------------------------------
    for u in units:
        vals = [v for v in u["states"].values() if v != NA]
        cnt = Counter(vals)
        u["n_known"] = len(vals)
        u["minor"] = len(vals) - max(cnt.values()) if cnt else 0
        ordered = sorted(cnt.values(), reverse=True)
        u["second"] = ordered[1] if len(ordered) > 1 else 0          # parsimony-informative: >= 2
    informative = [u for u in units if u["second"] >= 2]
    informative.sort(key=lambda u: (-u["minor"], u["name"]))
    private = [u for u in units if u["second"] < 2]
    if len(informative) > MAX_UNITS:
        private = private + informative[MAX_UNITS:]
        informative = informative[:MAX_UNITS]
        flags.append(dict(kind="units_capped", ref=None, message=f"only the {MAX_UNITS} most informative units are kept"))
    for k, u in enumerate(informative):
        u["k"] = k
    wids = [w["wid"] for w in wits]
    X = {w: [u["states"].get(w, NA) for u in informative] for w in wids}
    return dict(
        seed=seed, witnesses=[{k: v for k, v in w.items() if k not in ("tokens", "matn")} | dict(n_tokens=len(w["tokens"]))
                              for w in wits],
        clauses=[{k: v for k, v in c.items() if k != "tokens"} for c in clause_defs],
        segments={f"{ci}|{w}": {k: v for k, v in sg.items() if k != "aligned"} for (ci, w), sg in segs.items()},
        grids=grids, units=[{k: v for k, v in u.items() if k not in ("minor", "n_known", "second")} for u in informative],
        X=X, private_variants=[dict(name=u["name"], kind=u["kind"], states=u["states"]) for u in private],
        private_texts=extras["private"], dropped_orthographic=dropped_orth,
        n_units=len(informative), flags=flags)


# ---------------------------------------------------------------------------
def _number_units(c: dict, segs, wits) -> Tuple[List[dict], set]:
    """One unit per number word of the seed clause (e.g. ستة): the state is the number the witness
    has at the aligned position (سبعة, خمسة, ...). Returns the units and the set of seed tokens."""
    units, used = [], set()
    for i, tok in enumerate(c["tokens"]):
        if c["kind"] != "seed" or parse_arabic_number(tok) is None:
            continue
        vals: Dict[str, Optional[int]] = {}
        for w in wits:
            sg = segs[(c["i"], w["wid"])]
            tb = sg["aligned"][i] if sg["state"] != "absent" and i < len(sg["aligned"]) else None
            vals[w["wid"]] = parse_arabic_number(tb) if tb else None
        distinct = [v for v, _ in Counter(v for v in vals.values() if v is not None).most_common()]
        if len(distinct) < 2:
            continue
        idx = {v: k for k, v in enumerate(distinct[:MAX_STATES])}
        states = {w: (idx.get(v, MAX_STATES - 1) if v is not None else NA) for w, v in vals.items()}
        units.append(dict(name=f"c{c['i']}n{i}:{tok}", kind="numeric", unit_kind="number", clause=c["i"], cls="numeric",
                          n_states=len(idx), labels=[str(v) for v in idx], states=states))
        used.add(tok)
    # tokens that stand for the same slot in other witnesses must not form a second unit
    for w in wits:
        for t in segs[(c["i"], w["wid"])]["tokens"]:
            if parse_arabic_number(t) is not None:
                used.add(t)
    return units, used


def _binary_unit(uid: str, text: str, states: Dict[str, int], kind: str, ci: int) -> dict:
    known = [v for v in states.values() if v != NA]
    share = sum(known) / max(len(known), 1)
    return dict(name=f"{uid}:{' '.join(text.split()[:4])}", kind="presence", unit_kind=kind, clause=ci,
                cls="omission" if share >= 0.25 else "addition", n_states=2, labels=["absent", "present"],
                states=dict(states))


def _discover_extras(clause_defs: List[dict], wits: List[dict]) -> dict:
    """Unmatched runs of the witness windows, clustered across witnesses."""
    runs: List[Tuple[str, List[str]]] = []
    for w in wits:
        n = len(w["tokens"])
        covered = [False] * n
        for c in clause_defs:
            if w["wid"] == wits[0]["wid"]:
                continue
            r = align_clause(c["tokens"], w["tokens"])
            if r["m"]:
                mi = [i for i, f in enumerate(r["match_a"]) if f]
                lo = max(0, r["span_b"][0] - mi[0])
                hi = min(n, r["span_b"][1] + len(c["tokens"]) - 1 - mi[-1])
                for j in range(lo, hi):
                    covered[j] = True
        if w["wid"] == wits[0]["wid"]:
            continue
        j = 0
        while j < n:
            if covered[j]:
                j += 1
                continue
            k = j
            while k < n and not covered[k]:
                k += 1
            seg = w["tokens"][j:k]
            while seg and seg[0] in ("و", "ف"):
                seg = seg[1:]
            edge = j == 0 or k == n
            marker = w["marker"]
            if (len(seg) >= 3 and not (edge and len(seg) <= 3)
                    and sum(1 for t in seg if t in FORMULA) / len(seg) < 0.7
                    and (marker is None or j < marker)):
                runs.append((w["wid"], seg))
            j = k
    clusters: List[dict] = []
    for wid, seg in runs:
        for cl in clusters:
            r = align_pair(cl["rep"], seg)
            if min(r["m"] / len(cl["rep"]), r["m"] / len(seg)) >= 0.6:
                cl["members"][wid] = seg
                if len(seg) > len(cl["rep"]):
                    cl["rep"] = seg
                break
        else:
            clusters.append(dict(rep=seg, members={wid: seg}))
    def resembles_clause(rep):
        for c in clause_defs:
            r = align_pair(c["tokens"], rep)
            if r["m"] / max(min(len(c["tokens"]), len(rep)), 1) >= 0.5:
                return True
        return False
    keep = [dict(tokens=cl["rep"], support=len(cl["members"])) for cl in clusters
            if len(cl["members"]) >= 2 and not resembles_clause(cl["rep"])]
    private = [dict(wid=wid, text=" ".join(seg)) for cl in clusters if len(cl["members"]) < 2 for wid, seg in cl["members"].items()]
    # keep order stable: biggest support first
    keep.sort(key=lambda e: -e["support"])
    return dict(clauses=keep, private=private)


def _clause_grid(c: dict, holders: List[str], segs, wits, skip_tokens=frozenset()) -> Tuple[List[dict], List[dict], int]:
    """Multiple alignment of the segments of one clause and the units found in it."""
    if len(holders) < 2:
        return [], [], 0
    items = [(w, segs[(c["i"], w)]["tokens"]) for w in holders if segs[(c["i"], w)]["tokens"]]
    if len(items) < 2:
        return [], [], 0
    first = c["tokens"] if c["kind"] == "seed" else None
    order, cols = star_align(items, spine_first=False, merge_min=None, max_cols=3 * max(len(c["tokens"]), 6))
    placed = set(order)
    grid = [{w: t for w, t in col.items()} for col in cols]
    units: List[dict] = []
    orth = 0
    # row extents: leading/trailing gaps are NA
    ext = {}
    for w in order:
        idx = [j for j, col in enumerate(cols) if w in col]
        ext[w] = (idx[0], idx[-1]) if idx else (0, -1)
    j = 0
    ncol = len(cols)
    sub_runs: List[Tuple[int, dict]] = []
    pres_runs: List[Tuple[int, Tuple, str]] = []
    for j, col in enumerate(cols):
        vec = {}
        for w in order:
            lo, hi = ext[w]
            if j < lo or j > hi:
                vec[w] = NA
            else:
                vec[w] = 1 if w in col else 0
        known = [v for v in vec.values() if v != NA]
        toks = [col.get(w) for w in order if vec[w] == 1]
        if toks and Counter(toks).most_common(1)[0][0] in skip_tokens:
            continue                                  # a number word: covered by its own unit
        clusters = cluster_tokens(toks)
        if len(set(known)) > 1 and len(clusters) <= 1:
            pres_runs.append((j, tuple(vec.get(w, NA) for w in order), clusters[0][0] if clusters else ""))
        elif len(clusters) >= 2:
            if any(len({match_key(t) for t in cl}) == 1 and len(set(cl)) > 1 for cl in clusters):
                orth += 1
            sub_runs.append((j, dict(clusters=clusters, vec=vec, col=col)))
    # merge adjacent presence columns with identical vectors -> one unit per block
    block: List[Tuple[int, Tuple, str]] = []
    def flush():
        nonlocal block
        if block:
            vec = block[0][1]
            words = " ".join(b[2] for b in block)
            st = {w: v for w, v in zip(order, vec)}
            for w in holders:
                st.setdefault(w, NA)
            u = _binary_unit(f"c{c['i']}w{block[0][0]}", words, st, "words", c["i"])
            units.append(u)
        block = []
    for item in pres_runs:
        if block and (item[0] != block[-1][0] + 1 or item[1] != block[0][1]):
            flush()
        block.append(item)
    flush()
    # substitution columns: states = clusters; adjacent columns with identical partition merge
    prev_key, group = None, []
    def flush_sub():
        nonlocal group
        if not group:
            return
        j0, info = group[0]
        clusters = info["clusters"]
        reps = [cl[0] for cl in clusters]
        if len(reps) > MAX_STATES:
            reps = reps[:MAX_STATES - 1] + ["*other*"]
        lab = [r for r in reps]
        states = {}
        for w in holders:
            t = info["col"].get(w) if w in placed else None
            if info["vec"].get(w, NA) == NA or t is None:
                states[w] = NA
                continue
            for si, cl in enumerate(clusters):
                if t in cl:
                    states[w] = min(si, len(reps) - 1)
                    break
        numeric = all(parse_arabic_number(r) is not None for r in lab if r != "*other*")
        units.append(dict(name=f"c{c['i']}s{j0}:{'|'.join(lab)}", kind="numeric" if numeric else "lexical",
                          unit_kind="words", clause=c["i"], cls="numeric" if numeric else "lexical",
                          n_states=len(lab), labels=lab, states=states))
        group = []
    for j0, info in sub_runs:
        key = tuple(sorted((w, tuple(info["clusters"][0]) and next((si for si, cl in enumerate(info["clusters"]) if info["col"].get(w) in cl), -1))
                           for w in order if info["vec"].get(w) == 1))
        if group and (j0 != group[-1][0] + 1 or key != prev_key):
            flush_sub()
        group.append((j0, info))
        prev_key = key
    flush_sub()
    return grid, units, orth
