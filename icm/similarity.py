"""Fast pairwise matn comparison used to score search candidates.

Tokens are compared through a *match key* that removes the clitics which differ between
witnesses without changing the wording (leading و/ف, the article ال). A conjunction written
apart ("و الصوم") is one word with the next ("والصوم"): ``conj_groups`` joins them before any
comparison, so a bare و is never matched (or missed) as a word of its own. The comparison is a
difflib opcode alignment of key sequences; inside replaced blocks, words that are close in
spelling (``char_ratio >= FUZZ``) still count as matches, the others as substitutions.

The star alignment of ``icm.msa`` is the production aligner for the selected witnesses
(stage 3); this module only has to be fast enough to score ~1000 candidates.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .msa import char_ratio

FUZZ = 0.8
CONJ = ("و", "ف")
# closed-class words that take a conjunction prefix: «ولا» is «لا», «وان» is «ان» (a longer word such as «ولد» is not touched)
_PARTICLES = frozenset("لا ما ان هو هی من لم لن فی له به کل لو قد هم عن بل مع کم اذ ذا".split())


def match_key(t: str) -> str:
    if len(t) > 3 and t[0] in "وف":
        t = t[1:]
    elif len(t) == 3 and t[0] in "وف" and t[1:] in _PARTICLES:
        t = t[1:]
    if len(t) > 3 and t.startswith("ال"):
        t = t[2:]
    return t


def conj_groups(toks: Sequence[str]) -> List[Tuple[int, int]]:
    """Half-open token ranges of the words of ``toks``: a bare conjunction (و / ف written apart) belongs to the word
    after it (repeated ones too). One left at the end belongs to nothing: it is a group of its own."""
    out: List[Tuple[int, int]] = []
    start = None
    for i, t in enumerate(toks):
        if t in CONJ:
            if start is None:
                start = i
            continue
        out.append((i if start is None else start, i + 1))
        start = None
    if start is not None:
        out.append((start, len(toks)))
    return out


def group_keys(toks: Sequence[str], groups: Sequence[Tuple[int, int]], tag: str, keyf: Callable[[str], str] = match_key) -> List[str]:
    """The match key of each group (its last token's); a conjunction left at the end gets a key nothing else has.
    ``keyf``: another key function, e.g. ``lex.search_key``, which gives words of one root one key."""
    return [f"<{tag}{k}>" if toks[g[1] - 1] in CONJ else keyf(toks[g[1] - 1]) for k, g in enumerate(groups)]


def align_pair(a: Sequence[str], b: Sequence[str], keyf: Callable[[str], str] = match_key) -> Dict:
    """Align token lists ``a`` (reference) and ``b`` (candidate), a bare conjunction counting with the word after it.

    Returns counts and per-reference-token flags:
      m           matched tokens of a (a bare conjunction counts with its word)
      s           substituted tokens of a strictly inside the matched span
      gap_a/gap_b tokens of a / b without partner inside the matched span
      match_a     bool per token of a: matched (all tokens of a matched word, its conjunction too)
      sub_a       bool per token of a: substituted (aligned to a different word)
      span_b      (j0, j1) half-open token range of b from the first matched word to the last
      pairs       [(i, j)] matched words, as the token index of the word itself (not of its conjunction)
      aligned_b   per token of a: for a word's own token, the b word aligned to it (match or substitution) or None
      r           matched tokens of a whose word is not the candidate's (not even spelt alike) but of its root (``keyf``)
    """
    ga, gb = conj_groups(a), conj_groups(b)
    ka, kb = group_keys(a, ga, "a", keyf), group_keys(b, gb, "b", keyf)
    sm = SequenceMatcher(None, ka, kb, autojunk=False)
    match_g = [False] * len(ga)
    sub_g = [False] * len(ga)
    pairs: List[Tuple[int, int]] = []          # group pairs
    sub_pairs: List[Tuple[int, int]] = []
    root_g = set()                             # matched through the root, not the word
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                match_g[i1 + k] = True
                pairs.append((i1 + k, j1 + k))
                if keyf is not match_key and ka[i1 + k][:1] == "~" and match_key(a[ga[i1 + k][1] - 1]) != match_key(b[gb[j1 + k][1] - 1]) \
                        and char_ratio(a[ga[i1 + k][1] - 1], b[gb[j1 + k][1] - 1]) < FUZZ:
                    root_g.add(i1 + k)
        elif tag == "replace":
            for k in range(min(i2 - i1, j2 - j1)):
                i, j = i1 + k, j1 + k
                if ka[i][0] == "<" or kb[j][0] == "<":
                    continue
                if char_ratio(a[ga[i][1] - 1], b[gb[j][1] - 1]) >= FUZZ:
                    match_g[i] = True
                    pairs.append((i, j))
                else:
                    sub_g[i] = True
                    sub_pairs.append((i, j))
    match_a = [False] * len(a)
    sub_a = [False] * len(a)
    for gi, (x, y) in enumerate(ga):
        for t in range(x, y):
            match_a[t], sub_a[t] = match_g[gi], sub_g[gi]
    if not pairs:
        return dict(m=0, s=0, r=0, gap_a=0, gap_b=0, match_a=match_a, sub_a=sub_a, span_b=(0, 0), pairs=[],
                    aligned_b=[None] * len(a))
    i0, i1_ = pairs[0][0], pairs[-1][0]
    j0, j1_ = pairs[0][1], pairs[-1][1]
    subs = [(i, j) for i, j in sub_pairs if i0 <= i <= i1_ and j0 <= j <= j1_]
    for gi, (x, y) in enumerate(ga):             # substitutions outside the span are not mismatches
        if sub_g[gi] and not (i0 <= gi <= i1_):
            for t in range(x, y):
                sub_a[t] = False
    n_a = lambda gs: sum(ga[i][1] - ga[i][0] for i, _ in gs)      # counts are in tokens (a bare conjunction counts with its word)
    n_b = lambda gs: sum(gb[j][1] - gb[j][0] for _, j in gs)
    m, s = n_a(pairs), n_a(subs)
    aligned_b: List = [None] * len(a)           # the candidate word aligned to each reference word
    for i, j in pairs + sub_pairs:              # (substitutions at the clause ends too, e.g. the count word)
        aligned_b[ga[i][1] - 1] = b[gb[j][1] - 1]
    gap_a = (ga[i1_][1] - ga[i0][0]) - m - s
    gap_b = (gb[j1_][1] - gb[j0][0]) - n_b(pairs) - n_b(subs)
    return dict(m=m, s=s, r=sum(ga[i][1] - ga[i][0] for i in root_g), gap_a=max(gap_a, 0), gap_b=max(gap_b, 0), match_a=match_a, sub_a=sub_a,
                span_b=(gb[j0][0], gb[j1_][1]), pairs=[(ga[i][1] - 1, gb[j][1] - 1) for i, j in pairs], aligned_b=aligned_b)


def align_clause(a: Sequence[str], b: Sequence[str], b_groups=None, keyf: Callable[[str], str] = match_key) -> Dict:
    """Best local alignment of one reference clause ``a`` inside candidate tokens ``b``.

    Pass 1 finds the window of ``b`` holding the most key matches (width: the clause's words + 4), so
    scattered one-word matches elsewhere in a long text do not count; pass 2 aligns the
    clause with that window. Returns ``align_pair`` fields with ``pairs``/``span_b`` in
    candidate coordinates, or an all-zero result when nothing matches. ``b_groups`` is
    ``(conj_groups(b), group_keys(b, ...))`` when the caller compares many clauses with one ``b``.
    """
    ga = conj_groups(a)
    ka = group_keys(a, ga, "a", keyf)
    gb, kb = b_groups if b_groups is not None else (lambda g: (g, group_keys(b, g, "b", keyf)))(conj_groups(b))
    sm = SequenceMatcher(None, ka, kb, autojunk=False)
    js = sorted(blk_j + k for blk_i, blk_j, size in sm.get_matching_blocks() for k in range(size))
    if not js:
        return dict(m=0, s=0, r=0, gap_a=len(a), gap_b=0, match_a=[False] * len(a), sub_a=[False] * len(a),
                    span_b=(0, 0), pairs=[], aligned_b=[None] * len(a))
    width = len(ga) + 4
    best, best_j, lo = 0, js[0], 0
    for hi, j in enumerate(js):
        while js[lo] <= j - width:
            lo += 1
        if hi - lo + 1 > best:
            best, best_j = hi - lo + 1, js[lo]
    g0 = max(0, best_j - 2)
    g1 = min(len(gb), best_j + width + 2)
    w0, w1 = gb[g0][0], gb[g1 - 1][1]
    res = align_pair(a, b[w0:w1], keyf)
    res["pairs"] = [(i, j + w0) for i, j in res["pairs"]]
    res["span_b"] = (res["span_b"][0] + w0, res["span_b"][1] + w0)
    return res


def align_clauses(a: Sequence[str], spans: Sequence[Tuple[int, int]], b: Sequence[str], keyf: Callable[[str], str] = match_key) -> Dict:
    """Order-free comparison of candidate ``b`` against the clauses of reference ``a``.

    Witnesses of one hadith often list the same clauses in a different order; each clause is
    therefore located independently. Returns aggregate counts, per-clause ``parts`` and
    whether the clauses appear in a different order than in the reference.
    """
    gb = conj_groups(b)
    kb = (gb, group_keys(b, gb, "b", keyf))
    parts, windows = [], []
    m = s = r = 0
    matched_b = set()
    for c, (x, y) in enumerate(spans):
        rc = align_clause(a[x:y], b, kb, keyf)
        n = max(y - x, 1)
        got = sum(rc["match_a"])
        sub = sum(rc["sub_a"])
        cov = got / n
        if cov >= 0.8:
            state = "present"
        elif cov >= 0.4:
            state = "partial"
        elif sub >= 2 and sub / n >= 0.3:
            state = "changed"
        else:
            state = "absent"
        parts.append(dict(clause=c, cov=round(cov, 3), state=state,
                          window=list(rc["span_b"]) if rc["m"] else None))
        if rc["m"]:
            m += rc["m"]
            s += rc["s"]
            r += rc["r"]
            matched_b.update(j for _, j in rc["pairs"])
            if state in ("present", "partial"):
                windows.append(rc["span_b"][0])
    reordered = any(windows[i] > windows[i + 1] for i in range(len(windows) - 1))
    return dict(m=m, s=s, r=r, n_matched_b=len(matched_b), parts=parts, reordered=reordered,
                matched_b=sorted(matched_b))


def classify(res: Dict, n_a: int, n_b: int) -> Tuple[str, Dict]:
    """Suggested class (shown to the user, never enforced) and the scores behind it.

    witness  the same text          abridged  a clean subset of the seed
    extended the seed inside a longer text     variant  most of the seed with own changes
    partial  shares some clauses    different  no usable resemblance
    witness/abridged/extended/variant are pre-selected in the UI; the user decides."""
    m, s = res["m"], res["s"]
    cov_a = m / max(n_a, 1)
    cov_b = res["n_matched_b"] / max(n_b, 1)
    mismatch = s / max(m + s, 1)
    m_min = min(6, max(3, round(0.6 * n_a)))
    present = sum(1 for p in res["parts"] if p["state"] == "present")
    score = dict(m=m, s=s, cov_seed=round(cov_a, 3), cov_cand=round(cov_b, 3),
                 mismatch=round(mismatch, 3), n_seed=n_a, n_cand=n_b,
                 clauses_present=present, reordered=res["reordered"])
    if res.get("r"):
        score["r"] = res["r"]               # matched words that are of the candidate's roots, not its words
    if m < m_min and present == 0:
        return "different", score
    if mismatch > 0.35:
        return ("partial" if present else "different"), score
    if cov_a >= 0.6 and cov_b >= 0.6 and mismatch <= 0.2 and m >= m_min:
        return "witness", score
    if cov_b >= 0.75 and mismatch <= 0.3 and cov_a < 0.6 and m >= m_min:
        return "abridged", score
    if cov_a >= 0.75 and mismatch <= 0.3 and cov_b < 0.6:
        return "extended", score
    if cov_a >= 0.5 and mismatch <= 0.3 and present >= 2 and m >= m_min:
        return "variant", score         # same hadith with its own additions/omissions/wording
    if present:
        return "partial", score
    return "different", score
