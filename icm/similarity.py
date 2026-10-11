"""Fast pairwise matn comparison used to score search candidates.

Tokens are compared through a *match key* that removes the clitics which differ between
witnesses without changing the wording (leading و/ف, the article ال). The comparison is a
difflib opcode alignment of key sequences; inside replaced blocks, tokens that are close in
spelling (``char_ratio >= FUZZ``) still count as matches, the others as substitutions.

The star alignment of ``icm.msa`` is the production aligner for the selected witnesses
(stage 3); this module only has to be fast enough to score ~1000 candidates.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Dict, List, Sequence, Tuple

from .msa import char_ratio

FUZZ = 0.8


def match_key(t: str) -> str:
    if len(t) > 3 and t[0] in "وف":
        t = t[1:]
    if len(t) > 3 and t.startswith("ال"):
        t = t[2:]
    return t


def align_pair(a: Sequence[str], b: Sequence[str]) -> Dict:
    """Align token lists ``a`` (reference) and ``b`` (candidate).

    Returns counts and per-reference-token flags:
      m           matched tokens (exact key or fuzzy)
      s           substituted tokens strictly inside the matched span
      gap_a/gap_b tokens of a / b without partner inside the matched span
      match_a     bool per token of a: matched
      sub_a       bool per token of a: substituted (aligned to a different word)
      span_b      (j0, j1) half-open range of b between the first and last matched token
      pairs       [(i, j)] matched positions
      aligned_b   per token of a: the b token aligned to it (match or substitution) or None
    """
    ka = [match_key(t) for t in a]
    kb = [match_key(t) for t in b]
    sm = SequenceMatcher(None, ka, kb, autojunk=False)
    match_a = [False] * len(a)
    sub_a = [False] * len(a)
    pairs: List[Tuple[int, int]] = []
    sub_pairs: List[Tuple[int, int]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                match_a[i1 + k] = True
                pairs.append((i1 + k, j1 + k))
        elif tag == "replace":
            for k in range(min(i2 - i1, j2 - j1)):
                i, j = i1 + k, j1 + k
                if char_ratio(a[i], b[j]) >= FUZZ:
                    match_a[i] = True
                    pairs.append((i, j))
                else:
                    sub_a[i] = True
                    sub_pairs.append((i, j))
    m = len(pairs)
    if m == 0:
        return dict(m=0, s=0, gap_a=0, gap_b=0, match_a=match_a, sub_a=sub_a, span_b=(0, 0), pairs=[],
                    aligned_b=[None] * len(a))
    i0, i1_ = pairs[0][0], pairs[-1][0]
    j0, j1_ = pairs[0][1], pairs[-1][1]
    subs = [(i, j) for i, j in sub_pairs if i0 <= i <= i1_ and j0 <= j <= j1_]
    for i in range(len(a)):                      # substitutions outside the span are not mismatches
        if sub_a[i] and not (i0 <= i <= i1_):
            sub_a[i] = False
    s = len(subs)
    aligned_b: List = [None] * len(a)           # the candidate token aligned to each reference token
    for i, j in pairs:
        aligned_b[i] = b[j]
    for i, j in sub_pairs:                      # also substitutions at the clause ends (e.g. the count word)
        aligned_b[i] = b[j]
    gap_a = (i1_ - i0 + 1) - m - s
    gap_b = (j1_ - j0 + 1) - m - s
    return dict(m=m, s=s, gap_a=max(gap_a, 0), gap_b=max(gap_b, 0), match_a=match_a, sub_a=sub_a,
                span_b=(j0, j1_ + 1), pairs=pairs, aligned_b=aligned_b)


def align_clause(a: Sequence[str], b: Sequence[str], ka_cache=None) -> Dict:
    """Best local alignment of one reference clause ``a`` inside candidate tokens ``b``.

    Pass 1 finds the window of ``b`` holding the most key matches (width len(a)+4), so
    scattered one-word matches elsewhere in a long text do not count; pass 2 aligns the
    clause with that window. Returns ``align_pair`` fields with ``pairs``/``span_b`` in
    candidate coordinates, or an all-zero result when nothing matches.
    """
    ka = [match_key(t) for t in a]
    kb = ka_cache if ka_cache is not None else [match_key(t) for t in b]
    sm = SequenceMatcher(None, ka, kb, autojunk=False)
    js = sorted(blk_j + k for blk_i, blk_j, size in sm.get_matching_blocks() for k in range(size))
    if not js:
        return dict(m=0, s=0, gap_a=len(a), gap_b=0, match_a=[False] * len(a), sub_a=[False] * len(a),
                    span_b=(0, 0), pairs=[], aligned_b=[None] * len(a))
    width = len(a) + 4
    best, best_j, lo = 0, js[0], 0
    for hi, j in enumerate(js):
        while js[lo] <= j - width:
            lo += 1
        if hi - lo + 1 > best:
            best, best_j = hi - lo + 1, js[lo]
    w0 = max(0, best_j - 2)
    w1 = min(len(b), best_j + width + 2)
    res = align_pair(a, b[w0:w1])
    res["pairs"] = [(i, j + w0) for i, j in res["pairs"]]
    res["span_b"] = (res["span_b"][0] + w0, res["span_b"][1] + w0)
    return res


def align_clauses(a: Sequence[str], spans: Sequence[Tuple[int, int]], b: Sequence[str]) -> Dict:
    """Order-free comparison of candidate ``b`` against the clauses of reference ``a``.

    Witnesses of one hadith often list the same clauses in a different order; each clause is
    therefore located independently. Returns aggregate counts, per-clause ``parts`` and
    whether the clauses appear in a different order than in the reference.
    """
    kb = [match_key(t) for t in b]
    parts, windows = [], []
    m = s = 0
    matched_b = set()
    for c, (x, y) in enumerate(spans):
        r = align_clause(a[x:y], b, kb)
        n = max(y - x, 1)
        got = sum(r["match_a"])
        sub = sum(r["sub_a"])
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
                          window=list(r["span_b"]) if r["m"] else None))
        if r["m"]:
            m += r["m"]
            s += r["s"]
            matched_b.update(j for _, j in r["pairs"])
            if state in ("present", "partial"):
                windows.append(r["span_b"][0])
    reordered = any(windows[i] > windows[i + 1] for i in range(len(windows) - 1))
    return dict(m=m, s=s, n_matched_b=len(matched_b), parts=parts, reordered=reordered,
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
