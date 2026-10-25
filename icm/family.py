"""The family's parts: every source hadith (step 1's list) is cut on its own, then the cuts that are the same part of
the hadith are joined. No hadith is the main one: the parts are the same whatever order the list is in.

    src = source(wid, texts)                      # text, tokens, automatic cut of one hadith
    spans = {wid: cut(src, user_spans)}           # the user's cut, or the automatic one
    parts = build({wid: (src["tok"], spans[wid])})
    tok, spans = reference(parts)                 # the parts' words one after another: what candidates are compared with

A part is ``dict(i, text, tokens, members={wid: [start, end)})``: its words are those of its representative (the member most like
the others, then the longest) and ``members`` says where each hadith that has it keeps it. A hadith with a cut of its own
in a part where no other hadith has one makes a part alone (support one), which step 3 treats like any other part.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from . import corpus, hadithai, pointers
from .similarity import align_pair
from .text import clauses, union_cuts

JOIN = 0.6          # two cuts are one part when the words they share are this share of both


def source(wid: str, texts: Optional[dict] = None) -> Optional[dict]:
    """A source hadith's words and its automatic cut: ``rec`` (the corpus record, its ``matn`` the text used: the user's
    edited text, or the hadith's own words without the references inside it), ``tok``, ``auto`` (token spans),
    ``owners`` (the speaker of each automatic span, hadith.ai) and ``by``."""
    rec = corpus.get(wid)
    if rec is None:
        return None
    own = (texts or {}).get(wid)
    if own is not None:
        rec = dict(rec, matn=own)
    else:
        sp = pointers.split(rec)
        if sp["refs"] and sp["main"]:                  # «… حدثنا وكيع … بمثله، إلا أن وكيعا قال: …»: its own words, not the isnads after them
            rec = dict(rec, matn=sp["main"])
    tok, auto = clauses(rec["matn"])
    owners, by = None, "rules"
    hai = hadithai.parts(rec["hid"], tok) if own is None and rec["corpus"] == "v02" else None
    if hai:
        # hadith.ai's segments are semantic but coarse in lists («الزائد… والمكذب… والتارك…» is one segment), and a
        # part is the unit of presence: cut at the boundaries of both, each part keeping its segment's speaker
        h_spans, h_owners = hai
        auto = union_cuts(tok, auto, h_spans)
        owners = [next((o for (x, y), o in zip(h_spans, h_owners) if x <= a < y), None) for a, _ in auto]
        by = "hadith.ai+rules"
    return dict(wid=wid, rec=rec, tok=tok, auto=[tuple(s) for s in auto], owners=owners, by=by)


def valid(spans, n: int) -> bool:
    """Ordered, non-overlapping [start, end) token spans within ``n`` tokens."""
    return (isinstance(spans, (list, tuple)) and 0 < len(spans) <= 300 and all(
        isinstance(s, (list, tuple)) and len(s) == 2 and all(isinstance(t, int) and not isinstance(t, bool) for t in s) and 0 <= s[0] < s[1] <= n
        for s in spans) and all(a[1] <= b[0] for a, b in zip(spans, spans[1:])))


def cut(src: dict, user) -> Tuple[List[Tuple[int, int]], bool]:
    """The spans of a hadith and whether they are the user's: a cut that does not fit the text any more (it was edited)
    is the automatic one."""
    if user is not None and valid(user, len(src["tok"])):
        return [(int(a), int(b)) for a, b in user], True
    return list(src["auto"]), False


def _sim(a: Sequence[str], b: Sequence[str]) -> float:
    r = align_pair(a, b)
    return min(r["m"] / max(len(a), 1), r["m"] / max(len(b), 1))


def build(cuts: Dict[str, Tuple[Sequence[str], Sequence[Tuple[int, int]]]]) -> List[dict]:
    """The family's parts from every hadith's cut (``{wid: (tokens, spans)}``)."""
    items = sorted(((wid, a, b) for wid, (tok, sp) in cuts.items() for a, b in sp), key=lambda x: (-(x[2] - x[1]), x[0], x[1]))
    clusters: List[dict] = []
    for wid, a, b in items:
        seg = list(cuts[wid][0][a:b])
        for cl in clusters:
            if wid not in cl["members"] and _sim(cl["rep"], seg) >= JOIN:
                cl["members"][wid] = [a, b]
                break
        else:
            clusters.append(dict(rep=seg, members={wid: [a, b]}))
    for cl in clusters:
        if len(cl["members"]) > 2:                    # the member most like the others (then the longest) speaks for the part
            segs = {w: list(cuts[w][0][a:b]) for w, (a, b) in cl["members"].items()}
            best = max(segs, key=lambda w: (sum(_sim(segs[w], segs[o]) for o in segs if o != w), len(segs[w]), w))
            cl["rep"] = segs[best]
    def at(cl) -> float:                              # where in its hadiths the part usually is
        return sum(a / max(len(cuts[w][0]), 1) for w, (a, _) in cl["members"].items()) / len(cl["members"])
    clusters.sort(key=lambda cl: (at(cl), " ".join(cl["rep"])))
    return [dict(i=i, text=" ".join(cl["rep"]), tokens=cl["rep"], members=cl["members"]) for i, cl in enumerate(clusters)]


def reference(parts: Sequence[dict]) -> Tuple[List[str], List[Tuple[int, int]]]:
    """The parts' words one after another (the text candidates are compared with) and each part's span in it."""
    tok: List[str] = []
    spans: List[Tuple[int, int]] = []
    for p in parts:
        spans.append((len(tok), len(tok) + len(p["tokens"])))
        tok.extend(p["tokens"])
    return tok, spans


def canonical(sources: Dict[str, dict], parts: Sequence[dict]) -> str:
    """The hadith the page names the family by (its title, the names of the columns it fills): the one in most parts, then
    with most words in them, then the lowest id. It follows from the texts, not from the order of the list."""
    def rank(w: str):
        mine = [p for p in parts if w in p["members"]]
        return (-len(mine), -sum(p["members"][w][1] - p["members"][w][0] for p in mine), w)
    return min(sources, key=rank)


def labels(n: int, parts: Sequence[dict], wid: str) -> List[int]:
    """Per token of a hadith, the part it is in (-1 for none)."""
    lab = [-1] * n
    for p in parts:
        if wid in p["members"]:
            a, b = p["members"][wid]
            lab[a:b] = [p["i"]] * (b - a)
    return lab
