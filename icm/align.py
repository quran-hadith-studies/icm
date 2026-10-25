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
  show; leading and trailing gaps of a segment are NA (truncation is not absence). A hadith that refers to another
  text («مثله وزاد فيه …», ``pointers.py``) is aligned with the text its template builds, or the one the user wrote in
  step 3 (``parts.refs``); one marked approximate («نحوه» not yet written out) is NA everywhere and stays out of the grids.

The slot of a clause in a witness is its matched span, without an isolated edge match (the
"الله" of "قال رسول الله" matched to the clause's own "الله"), plus as many tokens on each side
as the clause has unmatched tokens there, plus up to SLACK more when the witness inserted a
word there ("سبعة لعنتهم لعنهم الله" for "ستة لعنهم الله"). Every unit records the grid
columns it comes from (``cols``, None for a whole clause) so the page can point at them.

All of this is a suggestion the user can correct (the ``parts`` decision, ``run(..., parts)``): drop an extra
clause, add one cut from a witness's text, and set a clause's slot in a witness ([start, end) in the tokens of
the text the search stage keeps, see ``text_tokens``) or say it is absent or unknown there. Corrections are
keyed by the clause's ``key`` (``part_keys``: its kind and text), so a re-cut clause starts unreviewed.

The units themselves can be edited too (``parts.units``: part key -> unit key -> {drop, keep, cls, states, def}): every unit
has a ``ukey`` that does not depend on column numbers, so a decision survives a new alignment. A user can exclude a unit
(``drop``), keep one the filter or the cap set aside (``keep``), fix a witness's state, or define a unit over a range of
columns (``def``, anchored on the words of the witnesses it was drawn on, not on column numbers). Columns whose
words differ only in spelling (لیس / لیست, لا / ولا: one cluster of ``cluster_tokens``) are units too (``ortho``), set aside
(``why="orthographic"``) unless the user keeps them.

Rows of X use ``text_model.NA = -1``.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

from .msa import char_ratio, star_align
from .similarity import FUZZ, align_clause, align_pair, conj_groups, match_key
from .text import norm, tokens
from narrators.arabic_numbers import parse_arabic_number

NA = -1
MAX_UNITS = 40
MAX_STATES = 6
FORMULA = set(norm("قال رسول الله صلی الله علیه وآله وسلم آله سلم النبی ص ع علیه السلام عن أنه قال انه ان و").split())
SLACK = 2           # extra witness tokens a slot may take beyond the clause's unmatched edge
END_GAP = -0.15     # cost per column of a leading/trailing gap in the grid: no opening cost (segments are cut)


def word_sim(a: str, b: str) -> float:
    """Similarity of two words for the grids: clitic/article variants are equal, number words
    (ستة / سبعة / خمسة) line up with each other."""
    if match_key(a) == match_key(b):
        return 1.0
    if parse_arabic_number(a) is not None and parse_arabic_number(b) is not None:
        return 0.85
    return char_ratio(a, b)


def _core(pairs: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Matched (clause, witness) pairs without isolated edge matches: an end pair whose witness
    distance to its neighbour exceeds the clause distance by more than 2."""
    p = sorted(pairs, key=lambda x: x[1])
    while len(p) > 2 and (p[1][1] - p[0][1]) - (p[1][0] - p[0][0]) > 2:
        p = p[1:]
    while len(p) > 2 and (p[-1][1] - p[-2][1]) - (p[-1][0] - p[-2][0]) > 2:
        p = p[:-1]
    return p


def join_conj(toks: Sequence[str]) -> List[str]:
    """A conjunction written apart ("و کل") is one word with the next ("وکل"); repeated ones count once and one left
    at the end (it belongs to the next clause) is dropped."""
    out: List[str] = []
    for x, y in conj_groups(toks):
        if toks[y - 1] in ("و", "ف"):
            continue
        out.append(toks[x] if y - x == 1 else toks[x] + toks[y - 1])
    return out


def _opens_clause(t: str) -> bool:
    return len(t) > 3 and (t.startswith("وال") or t.startswith("فال"))


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


def part_keys(defs: Sequence[dict]) -> List[str]:
    """A clause's stable name across runs: its kind and normalised text (``#2`` for a repeated text)."""
    seen: Counter = Counter()
    out = []
    for c in defs:
        k = f"{'s' if c['kind'] == 'seed' else 'x'}:{c['text']}"
        seen[k] += 1
        out.append(k if seen[k] == 1 else f"{k}#{seen[k]}")
    return out


def text_tokens(w: dict) -> List[str]:
    """The tokens user slots count in: the text the search stage keeps (the seed's whole matn, a candidate's first
    1,500 characters). ``w["w0"]`` is where the witness's window (the tokens aligned) starts in them."""
    if "full" not in w:
        w["full"] = tokens(w["matn"])
    return w["full"]


# ---------------------------------------------------------------------------
def run(search: dict, selected: Sequence[str], parts: Optional[dict] = None) -> dict:
    parts = parts or {}
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
                             copies=search["seed"].get("copies", []), w0=0, full=s_tok)]
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
                         cls=c["suggested"], copies=c.get("copies", []), w0=(c.get("window") or [0])[0]))
        if "seed" in (c.get("signals") or ()):          # a hadith of step 1's list is as much a source as the first: all of its text, not a window of it
            full = tokens(c["matn"])
            wits[-1].update(seed=True, tokens=full, w0=0, full=full)
        if c.get("ref"):                               # «مثله / نحوه …»: the words the template builds from the text referred to (step 3)
            r = dict(c["ref"])
            own = (parts.get("refs") or {}).get(c["wid"])
            if own and own.get("text"):                # the text the user confirmed or wrote in step 3
                txt = own["text"]
                wits[-1].update(tokens=tokens(txt), matn=txt, w0=0, full=tokens(txt))
                r.update(approx=bool(own.get("approx")), user=True, ref=own.get("ref") or r.get("ref"), tpl=own.get("tpl") or r.get("tpl"))
            wits[-1]["ref"] = {k: r.get(k) for k in ("ref", "tpl", "marker", "approx", "user", "of")}
    for w in wits:
        w["marker"] = find_marker(tokens(w["matn"]))

    # --- extra clauses: unmatched runs shared by >= 2 witnesses --------------------------------
    vague = {w["wid"] for w in wits if (w.get("ref") or {}).get("approx")}     # «نحوه» not yet written out in step 3: close, words unknown
    extras = _discover_extras(clause_defs, [w for w in wits if w["wid"] not in vague])
    wit_by = {w["wid"]: w for w in wits}
    for w in wits:
        for x in w.get("copies", []):
            wit_by.setdefault(x, w)
    slots = {k: dict(v) for k, v in (parts.get("slots") or {}).items()}
    more = [dict(kind="extra", text=" ".join(ex["tokens"]), tokens=ex["tokens"], support=ex["support"]) for ex in extras["clauses"]]
    for a in parts.get("extras_added") or []:             # a clause the user cut from a witness's text
        w = wit_by.get(a["wid"])
        toks = text_tokens(w)[a["span"][0]:a["span"][1]] if w else []
        if not toks:
            flags.append(dict(kind="part_unused", ref=a["wid"], message="a part added from a witness that is not selected (or an empty span)"))
            continue
        more.append(dict(kind="extra", text=" ".join(toks), tokens=toks, support=None, by="user",
                         source=dict(wid=w["wid"], span=list(a["span"]))))
    keys = part_keys(clause_defs + more)
    for c, k in zip(clause_defs, keys):
        c["key"] = k
    dropped = set(parts.get("extras_dropped") or [])
    for c, k in zip(more, keys[len(clause_defs):]):
        if k in dropped and c.get("by") != "user":
            continue
        c["key"], c["i"] = k, len(clause_defs)
        clause_defs.append(c)
        if c.get("by") == "user":                         # its own witness has it where the user cut it
            slots.setdefault(k, {}).setdefault(c["source"]["wid"], c["source"]["span"])

    # --- per clause: segment of every witness, grid, presence ------------------------------------
    segs: Dict[Tuple[int, str], dict] = {}
    for w in wits:
        found = {}
        for c in clause_defs:
            if w["wid"] == seed and c["kind"] == "seed":
                continue
            r = align_clause(c["tokens"], w["tokens"])
            core = _core([(i, j) for i, j in r["pairs"] if r["match_a"][i]]) if r["m"] else []
            found[c["i"]] = (r, core)
        cores = sorted((core[0][1], core[-1][1] + 1) for _, core in found.values() if core)
        for c in clause_defs:
            if c["i"] not in found:
                segs[(c["i"], w["wid"])] = dict(state="present", cov=1.0, tokens=list(c["tokens"]),
                                                aligned=list(c["tokens"]), span=None)
                continue
            r, core = found[c["i"]]
            n = max(len(c["tokens"]), 1)
            cov = sum(r["match_a"]) / n
            sub = sum(r["sub_a"])
            state = ("present" if cov >= 0.6 else "absent" if cov < 0.25 and sub < 2 else "unclear")
            seg_toks, span = [], None
            if core:
                # the slot of the clause: the matched core plus as many tokens on each side as the
                # clause has unmatched tokens there (a substituted word sits next to the match), and
                # up to SLACK more there, never into another clause's core
                toks = w["tokens"]
                c0, c1 = core[0][1], core[-1][1] + 1
                pad_l, pad_r = core[0][0], len(c["tokens"]) - 1 - core[-1][0]
                left = max([b for a, b in cores if b <= c0], default=0)
                right = min([a for a, b in cores if a >= c1], default=len(toks))
                lo = max(left, c0 - pad_l)
                hi = min(right, c1 + pad_r)
                if pad_l:
                    for _ in range(SLACK):
                        if lo > left and toks[lo - 1] not in FORMULA and not _opens_clause(toks[lo]):
                            lo -= 1
                if pad_r:
                    for _ in range(SLACK):
                        if hi < right and toks[hi] not in FORMULA and not _opens_clause(toks[hi]):
                            hi += 1
                while lo < c0 and toks[lo] in FORMULA:      # "قال رسول الله" is not part of the slot
                    lo += 1
                while hi > c1 and toks[hi - 1] in FORMULA:
                    hi -= 1
                seg_toks, span = toks[lo:hi], [lo, hi]
            segs[(c["i"], w["wid"])] = dict(
                state=state, cov=round(cov, 3), span=span, tokens=seg_toks,
                aligned=r["aligned_b"] if r["m"] else [None] * len(c["tokens"]))

    # --- the user's slots: where a clause is in a witness, or that it is absent / unknown there -----------------
    known_keys = {c["key"]: c for c in clause_defs}
    for k, by_wid in slots.items():
        c = known_keys.get(k)
        for wid, v in by_wid.items():
            w = wit_by.get(wid)
            if c is None or w is None or (w["wid"] == seed and c["kind"] == "seed"):
                flags.append(dict(kind="part_unused", ref=f"{k}|{wid}", message="a slot for a part or witness not in this alignment"))
                continue
            if v in ("absent", "unknown"):
                segs[(c["i"], w["wid"])] = dict(state="absent" if v == "absent" else "unclear", cov=0.0, span=None, tokens=[],
                                                aligned=[None] * len(c["tokens"]), user=True)
                continue
            toks = text_tokens(w)[v[0]:v[1]]
            if not toks:
                flags.append(dict(kind="part_unused", ref=f"{k}|{wid}", message="a slot outside the witness's text"))
                continue
            r = align_pair(c["tokens"], toks)
            segs[(c["i"], w["wid"])] = dict(state="present", cov=round(sum(r["match_a"]) / max(len(c["tokens"]), 1), 3),
                                            span=[v[0] - w["w0"], v[1] - w["w0"]], tokens=list(toks),
                                            aligned=r["aligned_b"], user=True)

    # --- the user's word corrections: part key -> wid -> {word key -> edit} (a copy's wid names its representative) ----
    word_edits: Dict[str, Dict[str, dict]] = {}
    for k, by_wid in (parts.get("words") or {}).items():
        for wid, m in by_wid.items():
            w = wit_by.get(wid)
            if k not in known_keys or w is None:
                flags.append(dict(kind="word_unused", ref=f"{k}|{wid}", message="a word correction for a part or witness not in this alignment"))
                continue
            fix = lambda ed: dict(to=[(wit_by.get(ed["to"][0]) or {"wid": ed["to"][0]})["wid"], ed["to"][1]]) if ed != "add" else ed
            word_edits.setdefault(k, {}).setdefault(w["wid"], {}).update({t: fix(ed) for t, ed in m.items()})
    unit_cls = parts.get("unit_cls") or {}
    units_dec = parts.get("units") or {}

    units: List[dict] = []
    grids: List[dict] = []
    dropped_orth = 0
    for c in clause_defs:
        ci = c["i"]
        pres: Dict[str, int] = {}
        for w in wits:
            sg = segs[(ci, w["wid"])]
            v = 1 if sg["state"] == "present" else 0 if sg["state"] == "absent" else NA
            if v == 0 and w["marker"] is not None and not sg.get("user"):
                v = NA                              # abbreviated: not showing a clause is not evidence (unless the user says it lacks it)
            if w["wid"] in vague and not sg.get("user"):
                v = NA                              # «نحوه»: the words it had are not known, so no reading counts
            pres[w["wid"]] = v
        # presence unit of the clause
        known = [v for v in pres.values() if v != NA]
        if len(set(known)) > 1:
            units.append(_binary_unit(f"c{ci}", c["text"], pres, "clause", ci, None))
        # grid + column units inside the clause
        holders = [w["wid"] for w in wits if pres[w["wid"]] == 1]
        num_toks = _number_tokens(c, segs, wits)
        info: dict = dict(flags=flags, user_cells=[])
        grid, col_units, orth = _clause_grid(c, holders, segs, wits, skip_tokens=num_toks, edits=word_edits.get(c["key"]), info=info)
        units.extend(_number_units(c, segs, wits, grid, seed))
        dropped_orth += orth
        grids.append(dict(clause=ci, kind=c["kind"], text=c["text"], witnesses=holders, columns=grid, user_cells=info["user_cells"]))
        units.extend(col_units)
        _unit_keys([u for u in units if u["clause"] == ci], grid, holders, seed)
        manual = sorted(((int(k[2:]), k, d) for k, d in (units_dec.get(c["key"]) or {}).items() if k.startswith("m:") and d.get("def")))
        for n, ukey, d in manual:                         # the user's own units over a range of columns
            u = _manual_unit(c, grid, holders, wit_by, ukey, n, d["def"], seed, flags)
            if u:
                units.append(u)
    for u in units:                                       # «نحوه»: no reading of it is known, number units included
        for w in vague:
            if w in u["states"]:
                u["states"][w] = NA
    _apply_unit_decisions(units, clause_defs, unit_cls, units_dec, wit_by, flags)

    # --- keep the most informative units -----------------------------------------------------------
    for u in units:
        vals = [v for v in u["states"].values() if v != NA]
        cnt = Counter(vals)
        u["n_known"] = len(vals)
        u["minor"] = len(vals) - max(cnt.values()) if cnt else 0
        ordered = sorted(cnt.values(), reverse=True)
        u["second"] = ordered[1] if len(ordered) > 1 else 0          # parsimony-informative: >= 2
        u["n_distinct"] = len(cnt)
    gone = [u for u in units if u.get("user_drop")]                   # the user's exclusions
    pool = [u for u in units if not u.get("user_drop")]
    constant = [u for u in pool if u["n_distinct"] < 2]               # one state (after the user's corrections): nothing to model
    for u in constant:
        if u.get("user_keep") or u.get("by") == "user":
            flags.append(dict(kind="unit_constant", ref=f"{_clause_key(clause_defs, u['clause'])}|{u['ukey']}",
                              message="a unit the user kept or defined has one state in every witness: it does not reach the model"))
    pool = [u for u in pool if u["n_distinct"] >= 2]
    forced = [u for u in pool if u.get("user_keep") or u.get("by") == "user"]
    rest = [u for u in pool if u not in forced]
    ortho = [u for u in rest if u.get("ortho")]                       # spelling only: offered, not used unless kept
    rest = [u for u in rest if not u.get("ortho")]
    auto = sorted([u for u in rest if u["second"] >= 2], key=lambda u: (-u["minor"], u["name"]))
    private = [u for u in rest if u["second"] < 2]
    capped: List[dict] = []
    if len(auto) > MAX_UNITS:                                         # the cap is for the automatic units: the user's come on top
        capped = auto[MAX_UNITS:]
        auto = auto[:MAX_UNITS]
        flags.append(dict(kind="units_capped", ref=None, message=f"only the {MAX_UNITS} most informative units are kept"))
    informative = sorted(forced + auto, key=lambda u: (-u["minor"], u["name"]))
    for k, u in enumerate(informative):
        u["k"] = k
    wids = [w["wid"] for w in wits]
    X = {w: [u["states"].get(w, NA) for u in informative] for w in wids}
    plain = lambda u: {k: v for k, v in u.items() if k not in ("minor", "n_known", "second", "n_distinct")}
    return dict(
        seed=seed, witnesses=[{k: v for k, v in w.items() if k not in ("tokens", "matn", "full")} | dict(n_tokens=len(w["tokens"]))
                              for w in wits],
        clauses=[{k: v for k, v in c.items() if k != "tokens"} for c in clause_defs],
        segments={f"{ci}|{w}": {k: v for k, v in sg.items() if k != "aligned"} for (ci, w), sg in segs.items()},
        grids=grids, units=[plain(u) for u in informative],
        X=X, private_variants=[plain(u) | dict(why=why) for why, us in (("private", private), ("capped", capped), ("orthographic", ortho), ("constant", constant)) for u in us],
        dropped_units=[plain(u) for u in gone],
        private_texts=extras["private"], dropped_orthographic=dropped_orth,
        n_units=len(informative), flags=flags, parts_in=parts)


# ---------------------------------------------------------------------------
def _number_tokens(c: dict, segs, wits) -> set:
    """Number words in the segments of a clause whose seed text has one: they form number units,
    not word units."""
    if c["kind"] != "seed" or not any(parse_arabic_number(t) is not None for t in c["tokens"]):
        return set()
    return {t for w in wits for t in segs[(c["i"], w["wid"])]["tokens"] if parse_arabic_number(t) is not None}


def _number_units(c: dict, segs, wits, grid: List[dict], seed: str) -> List[dict]:
    """One unit per number word of the seed clause (e.g. ستة): the state is the number the witness
    has in the grid column of that word (سبعة, خمسة, ...), else at the position the clause
    alignment gives it."""
    units = []
    if c["kind"] != "seed":
        return units
    for i, tok in enumerate(c["tokens"]):
        if parse_arabic_number(tok) is None:
            continue
        j = next((j for j, col in enumerate(grid) if col.get(seed) == tok), None)
        vals: Dict[str, Optional[int]] = {}
        for w in wits:
            sg = segs[(c["i"], w["wid"])]
            tb = grid[j].get(w["wid"]) if j is not None else None
            if tb is None or parse_arabic_number(tb) is None:
                tb = sg["aligned"][i] if sg["state"] != "absent" and i < len(sg["aligned"]) else None
            vals[w["wid"]] = parse_arabic_number(tb) if tb else None
        distinct = [v for v, _ in Counter(v for v in vals.values() if v is not None).most_common()]
        if len(distinct) < 2:
            continue
        idx = {v: k for k, v in enumerate(distinct[:MAX_STATES])}
        states = {w: (idx.get(v, MAX_STATES - 1) if v is not None else NA) for w, v in vals.items()}
        units.append(dict(name=f"c{c['i']}n{i}:{tok}", kind="numeric", unit_kind="number", clause=c["i"], cls="numeric",
                          n_states=len(idx), labels=[str(v) for v in idx], states=states,
                          cols=[j, j] if j is not None else None, seed_key=f"{tok}#{c['tokens'][:i + 1].count(tok)}"))
    return units


def _binary_unit(uid: str, text: str, states: Dict[str, int], kind: str, ci: int, cols) -> dict:
    known = [v for v in states.values() if v != NA]
    share = sum(known) / max(len(known), 1)
    return dict(name=f"{uid}:{' '.join(text.split()[:4])}", kind="presence", unit_kind=kind, clause=ci,
                cls="omission" if share >= 0.25 else "addition", n_states=2, labels=["absent", "present"],
                states=dict(states), cols=cols, ukey=text, cls_user=False)


def _clause_key(clause_defs: Sequence[dict], ci: int) -> str:
    return next((c["key"] for c in clause_defs if c["i"] == ci), "")


def _unit_keys(mine: List[dict], grid: List[dict], holders: Sequence[str], seed: str) -> None:
    """Give every unit of one clause a ``ukey`` that does not depend on column numbers, so the user's decisions
    about it survive a new alignment: a presence unit is its words (the key it always had), a number unit is the
    number word of the seed ("ستة#1"), a substitution unit is the seed's words in its columns ("s:<word>#<n> ..."; the
    first witness that has a word there when the seed has none). A repeated key gets "#2", "#3" (the first stays)."""
    rk = {w: row_keys(grid, w) for w in holders}
    seen: Counter = Counter()
    for u in mine:
        if u["kind"] == "presence":
            base = u["ukey"]
        elif u["unit_kind"] == "number":
            base = "n:" + u.pop("seed_key", "?")
        else:
            j0, j1 = u["cols"]
            pre = "o:" if u.get("ortho") else "s:"
            base = pre + "?"
            for w in ([seed] if seed in rk else []) + [x for x in holders if x != seed]:
                keys = [k for j, k in rk.get(w, []) if j0 <= j <= j1]
                if keys:
                    base = pre + (" ".join(keys) if w == seed else f"{w}:" + " ".join(keys))
                    break
        u.pop("seed_key", None)
        seen[base] += 1
        u["ukey"] = base if seen[base] == 1 else f"{base}#{seen[base]}"


def _extents(grid: List[dict], holders: Sequence[str]) -> Dict[str, Tuple[int, int]]:
    """First and last grid column in which a witness has a word (outside them its row is NA, not absent)."""
    out = {}
    for w in holders:
        idx = [j for j, col in enumerate(grid) if w in col]
        if idx:
            out[w] = (idx[0], idx[-1])
    return out


def _manual_unit(c: dict, grid: List[dict], holders: Sequence[str], wit_by: dict, ukey: str, n: int, d: dict,
                 seed: str, flags: List[dict]) -> Optional[dict]:
    """A unit the user defined over a range of columns of this clause's grid. ``d["anchors"]`` names, per witness, its
    first and last word in the range (``row_keys``), so the range follows the words when the alignment changes: it is
    the columns those words are in now. ``presence``: has a word in the range or not; ``lexical``: which words (groups
    of equal ``match_key`` forms) each witness has there. A word row outside a witness's extent is NA."""
    ref = f"{c['key']}|{ukey}"
    lo = hi = None
    rk = {w: {k: j for j, k in row_keys(grid, w)} for w in holders}
    for wid, (k0, k1) in (d.get("anchors") or {}).items():
        w = (wit_by.get(wid) or {}).get("wid")
        if w not in rk or k0 not in rk[w] or k1 not in rk[w] or rk[w][k0] > rk[w][k1]:
            continue
        a, b = rk[w][k0], rk[w][k1]
        lo, hi = (a if lo is None else min(lo, a)), (b if hi is None else max(hi, b))
    if lo is None:
        flags.append(dict(kind="unit_unused", ref=ref, message="a unit whose words are no longer in this alignment"))
        return None
    ext = _extents(grid, holders)
    seqs: Dict[str, List[str]] = {}
    states: Dict[str, int] = {}
    for w in holders:
        seq = [grid[j][w] for j in range(lo, hi + 1) if w in grid[j]]
        if seq:
            seqs[w] = seq
        elif w in ext and ext[w][0] <= lo and hi <= ext[w][1]:
            seqs[w] = []                                   # inside its row with nothing there: absent
        else:
            states[w] = NA                                 # outside its row, or not in the grid: unknown
    text = (d.get("text") or " ".join(next(iter(s for s in seqs.values() if s), []))).strip()
    if d.get("kind") == "presence":
        states.update({w: 1 if s else 0 for w, s in seqs.items()})
        u = _binary_unit(f"c{c['i']}u{n}", text, states, "words", c["i"], [lo, hi])
    else:
        form = lambda s: " ".join(match_key(t) for t in s)
        groups: Dict[str, List[str]] = {}
        for w, s in seqs.items():
            groups.setdefault(form(s), s)
        order = sorted(groups, key=lambda g: (-sum(1 for s in seqs.values() if form(s) == g), list(groups).index(g)))
        labels = [" ".join(groups[g]) or "absent" for g in order]
        if len(labels) > MAX_STATES:
            labels = labels[:MAX_STATES - 1] + ["*other*"]
        idx = {g: min(i, len(labels) - 1) for i, g in enumerate(order)}
        states.update({w: idx[form(s)] for w, s in seqs.items()})
        u = dict(name=f"c{c['i']}u{n}:{' '.join(text.split()[:4])}", kind="lexical", unit_kind="words", clause=c["i"], cls="lexical",
                 n_states=len(labels), labels=labels, states=states, cols=[lo, hi], cls_user=False)
    u.update(ukey=ukey, by="user")
    return u


def _apply_unit_decisions(units: List[dict], clause_defs: Sequence[dict], unit_cls: dict, units_dec: dict,
                          wit_by: dict, flags: List[dict]) -> None:
    """The user's decisions about units (``parts.unit_cls`` and ``parts.units``), by part key and unit key: an
    addition / omission label, a witness's state (a label, a new label, or None = unknown), keep and drop."""
    by_key = {(_clause_key(clause_defs, u["clause"]), u["ukey"]): u for u in units}
    known = {c["key"] for c in clause_defs}
    for part, m in unit_cls.items():
        for ukey, lab in m.items():
            u = by_key.get((part, ukey))
            if u and u["kind"] == "presence" and lab:
                u.update(cls=lab, cls_user=True)
    for part, m in units_dec.items():
        for ukey, d in m.items():
            u = by_key.get((part, ukey))
            if u is None:
                if not (ukey.startswith("m:") and part in known):      # a manual unit that did not resolve is flagged when built
                    flags.append(dict(kind="unit_unused", ref=f"{part}|{ukey}", message="a unit decision for a unit not in this alignment"))
                continue
            if d.get("cls") and u["kind"] == "presence":
                u.update(cls=d["cls"], cls_user=True)
            for wid, v in (d.get("states") or {}).items():
                w = wit_by.get(wid)
                if w is None:
                    flags.append(dict(kind="unit_unused", ref=f"{part}|{ukey}|{wid}", message="a state for a witness not in this alignment"))
                    continue
                if v is None:
                    u["states"][w["wid"]] = NA
                else:
                    labels = u["labels"]
                    i = labels.index(v) if v in labels else next((k for k, x in enumerate(labels) if x != "*other*" and match_key(x) == match_key(v)), None)
                    if i is None and u["kind"] != "presence" and len(labels) < MAX_STATES:
                        labels.append(v)
                        i = len(labels) - 1
                    if i is None:
                        flags.append(dict(kind="unit_unused", ref=f"{part}|{ukey}|{wid}", message="a state this unit does not have"))
                        continue
                    u["states"][w["wid"]] = i
                u.setdefault("states_user", []).append(w["wid"])
            u["n_states"] = len(u["labels"])
            if d.get("keep"):
                u["user_keep"] = True
            if d.get("drop"):
                u["user_drop"] = True


def _discover_extras(clause_defs: List[dict], wits: List[dict]) -> dict:
    """Unmatched runs of the witness windows, clustered across witnesses. A run of a hadith of step 1's list (all equal
    sources, ``w["seed"]``) needs no second witness: it is a part of the family like the first hadith's own parts."""
    runs: List[Tuple[str, List[str]]] = []
    seeds = {w["wid"] for w in wits if w.get("seed")}
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
            if (len(seg) >= 3 and not (edge and len(seg) <= 3 and w["wid"] not in seeds)
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
    keep = [dict(tokens=cl["rep"], support=len(cl["members"]), members=dict(cl["members"])) for cl in clusters
            if (len(cl["members"]) >= 2 or seeds & set(cl["members"])) and not resembles_clause(cl["rep"])]
    private = [dict(wid=wid, text=" ".join(seg)) for cl in clusters if len(cl["members"]) < 2 and not seeds & set(cl["members"])
               for wid, seg in cl["members"].items()]
    # keep order stable: biggest support first
    keep.sort(key=lambda e: -e["support"])
    return dict(clauses=keep, private=private)


def seed_extras(S: dict) -> dict:
    """What the hadiths of step 1's list add to the first one's parts, for step 1 to show (the same extra parts step 3
    makes of them): ``extras`` [{i, text, support, wids}] numbered after the parts, ``found`` wid -> [[i, start, end)] token
    ranges of each extra in that hadith's text (``search.with_marks`` turns them into character ranges)."""
    seeds = [c for c in S["candidates"] if "seed" in (c.get("signals") or ())]
    if not seeds:
        return dict(extras=[], found={})
    s_tok = S["tokens"]
    defs = [dict(tokens=s_tok[c["span"][0]:c["span"][1]]) for c in S["clauses"]]
    first = dict(wid=S["seed"]["wid"], tokens=s_tok, marker=None)
    toks = {c["wid"]: tokens(c["matn"]) for c in seeds}
    wits = [first] + [dict(wid=w, tokens=t, seed=True, marker=find_marker(t)) for w, t in toks.items()]
    out, found = [], {}
    for k, ex in enumerate(_discover_extras(defs, wits)["clauses"]):
        i = len(defs) + k
        out.append(dict(i=i, text=" ".join(ex["tokens"]), support=ex["support"], wids=sorted(ex["members"])))
        for wid, seg in ex["members"].items():
            t = toks[wid]
            at = next((j for j in range(len(t) - len(seg) + 1) if t[j:j + len(seg)] == seg), None)
            if at is not None:
                found.setdefault(wid, []).append([i, at, at + len(seg)])
    return dict(extras=out, found=found)


def row_keys(cols: Sequence[dict], w: str) -> List[Tuple[int, str]]:
    """A witness's words in a grid in order, each with its column and its key ``"<word>#<n>"`` (the n-th time the word is
    in that row): what the user's word corrections (``parts.words``) call a word, as columns move between runs."""
    seen: Counter = Counter()
    out = []
    for j, col in enumerate(cols):
        t = col.get(w)
        if t is not None:
            seen[t] += 1
            out.append((j, f"{t}#{seen[t]}"))
    return out


def _apply_word_edits(cols: List[dict], edits: Dict[str, dict], flags: List[dict], ref: str) -> Tuple[List[dict], List[list], List[int]]:
    """The user's per-word corrections of an automatic alignment (``edits``: wid -> {word key -> edit}).

    ``"add"``: the word is an addition: it leaves the column it was aligned in (opposite another witness's word) and stands
    alone, in a column right behind (shared with another witness's added column holding the same word), or right in
    front when the witness's next word is to move into that very column.
    ``{"to": [wid, key]}``: the word is a variant of the word that witness has: it moves into that word's column.
    The order of a witness's words never changes (a ``to`` target must lie between its neighbours), so keys stay valid.
    An edit that no longer fits (word gone, column taken, order) is skipped with a ``word_unused`` flag.
    Returns the columns, the cells touched ([column, wid]) and the columns made for added words."""
    cols = [dict(c) for c in cols]
    gone: List[dict] = []                                  # kept so that id() of a removed column is not reused
    touched: set = set()
    added: set = set()

    def find(w: str, key: str) -> Optional[int]:
        return next((j for j, k in row_keys(cols, w) if k == key), None)

    def unused(w, key, why):
        flags.append(dict(kind="word_unused", ref=f"{ref}|{w}|{key}", message=f"a correction of a word that no longer fits this alignment ({why})"))

    todo = sorted((0 if x == "add" else 1, w, key) for w, m in edits.items() for key, x in m.items())
    for _, w, key in todo:
        x = edits[w][key]
        j = find(w, key)
        if j is None:
            unused(w, key, "the word is not in this witness's row")
            continue
        tok = cols[j][w]
        if x == "add":
            if len(cols[j]) == 1:                          # alone already
                for k2, x2 in edits[w].items():            # ... but a later word of the witness may be due in an earlier column:
                    if isinstance(x2, dict) and x2["to"][0] != w:       # the addition then goes in front of that column
                        rt, jy = find(x2["to"][0], x2["to"][1]), find(w, k2)
                        if rt is not None and jy is not None and jy > j > rt and not any(w in cols[c] for c in range(rt, j)):
                            cols.insert(rt, cols.pop(j))
                            j = rt
                            break
                added.add(id(cols[j]))
                touched.add((id(cols[j]), w))
                continue
            # the word stands behind its column, unless a later word of the witness is to move into that column
            # (the substitution «X Y» -> addition X + variant Y): then it stands in front, so the order is kept
            into = [find(w, k2) for k2, x2 in edits[w].items() if isinstance(x2, dict) and x2["to"][0] != w and find(x2["to"][0], x2["to"][1]) == j]
            back = not any(jj is not None and jj > j for jj in into)
            del cols[j][w]
            k = j + 1 if back else j - 1
            while 0 <= k < len(cols) and id(cols[k]) in added and w not in cols[k]:
                if len(cluster_tokens(list(cols[k].values()) + [tok])) == 1:
                    cols[k][w] = tok
                    touched.add((id(cols[k]), w))
                    break
                k += 1 if back else -1
            else:
                new = {w: tok}
                cols.insert(j + 1 if back else j, new)
                added.add(id(new))
                touched.add((id(new), w))
            continue
        ref_w, ref_key = x["to"]
        rj = find(ref_w, ref_key) if ref_w != w else None
        if rj is None:
            unused(w, key, "the word to match is not there")
            continue
        if rj == j:                                        # already there: the user confirms the automatic place
            touched.add((id(cols[j]), w))
            continue
        others = [jj for jj, k in row_keys(cols, w) if k != key]
        if w in cols[rj] or not (max([jj for jj in others if jj < j], default=-1) < rj < min([jj for jj in others if jj > j], default=len(cols))):
            unused(w, key, "the column is taken or would reorder the words")
            continue
        del cols[j][w]
        cols[rj][w] = tok
        touched.add((id(cols[rj]), w))
        if not cols[j]:
            gone.append(cols.pop(j))
    cells = [[j, w] for j, col in enumerate(cols) for w in col if (id(col), w) in touched]
    return cols, cells, [j for j, col in enumerate(cols) if id(col) in added]


def _clause_grid(c: dict, holders: List[str], segs, wits, skip_tokens=frozenset(), edits=None, info=None) -> Tuple[List[dict], List[dict], int]:
    """Multiple alignment of the segments of one clause and the units found in it. ``edits`` are the user's word
    corrections for this clause (``_apply_word_edits``); ``info`` receives their ``flags`` and ``user_cells``."""
    if len(holders) < 2:
        return [], [], 0
    items = [(w, join_conj(segs[(c["i"], w)]["tokens"])) for w in holders if segs[(c["i"], w)]["tokens"]]
    if len(items) < 2:
        return [], [], 0
    first = c["tokens"] if c["kind"] == "seed" else None
    order, cols = star_align(items, sim=word_sim, spine_first=False, merge_min=None, max_cols=3 * max(len(c["tokens"]), 6),
                             end_gap=END_GAP, refine=2, weighted_gaps=True)
    placed = set(order)
    added_cols: List[int] = []
    if edits:
        cols, cells, added_cols = _apply_word_edits(cols, edits, info["flags"] if info is not None else [], c.get("key", ""))
        if info is not None:
            info["user_cells"] = cells
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
    ortho_cols: List[Tuple[int, dict, dict]] = []     # one word in every witness, spelled differently (لیس / لیست, لا / ولا)
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
        if len(clusters) == 1 and len(set(toks)) > 1:
            ortho_cols.append((j, vec, col))
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
            u = _binary_unit(f"c{c['i']}w{block[0][0]}", words, st, "words", c["i"], [block[0][0], block[-1][0]])
            if any(block[0][0] <= a <= block[-1][0] for a in added_cols):      # the user said these words are an addition
                u.update(cls="addition", cls_user=True)
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
                          n_states=len(lab), labels=lab, states=states, cols=[j0, group[-1][0]]))
        group = []
    for j0, info in sub_runs:
        key = tuple(sorted((w, tuple(info["clusters"][0]) and next((si for si, cl in enumerate(info["clusters"]) if info["col"].get(w) in cl), -1))
                           for w in order if info["vec"].get(w) == 1))
        if group and (j0 != group[-1][0] + 1 or key != prev_key):
            flush_sub()
        group.append((j0, info))
        prev_key = key
    flush_sub()
    # spelling-only columns: a unit too, set aside unless the user keeps it (``ortho``; states = the spellings)
    for j, vec, col in ortho_cols:
        cnt = Counter(col[w] for w in order if vec.get(w) == 1).most_common()
        reps = [t for t, _ in cnt]
        if len(reps) > MAX_STATES:
            reps = reps[:MAX_STATES - 1] + ["*other*"]
        idx = {t: min(i, len(reps) - 1) for i, (t, _) in enumerate(cnt)}
        units.append(dict(name=f"c{c['i']}o{j}:{'|'.join(reps)}", kind="lexical", unit_kind="words", clause=c["i"], cls="orthography",
                          n_states=len(reps), labels=reps, states={w: idx[col[w]] if w in placed and vec.get(w) == 1 else NA for w in holders},
                          cols=[j, j], ortho=True))
    return grid, units, orth
