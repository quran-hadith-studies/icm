# Copied from hadith-hub/src/msa.py (same project) so that icm has no path dependency on the
# hub. Change: char_ratio is memoised; it is called once per (column, token) cell and the same
# word pairs recur constantly; optional cheap end gaps (end_gap) for segments that may be cut short.
"""Generic similarity-aware progressive (star) multiple-sequence alignment.

Generalizes the word-level Needleman-Wunsch pileup in export_consensus_web.py to
ANY token type + similarity function, so the same machinery aligns whole SEGMENTS
across consensus members (build_segment_alignment.py) as well as words. The
algorithm is identical: longest sequence = spine, then each remaining sequence is
NW-aligned to the running column profile, with substitution = sim - sub_mid,
AFFINE gaps, and optional 2-into-1 / 1-into-2 merge moves for split/joined tokens.

AFFINE GAPS (Gotoh, added 2026-06-25): a gap costs gap_open to START a run and
gap_ext for each further column. With a single linear penalty, two alignments with
the same total gap COUNT but a different number of gap RUNS score identically, so a
witness's token could float in the middle of a gap region (e.g. a lone أعمى snapping
to the 2nd of two identical words, leaving leading gaps). Charging for opening a run
makes the alignment with FEWER, more contiguous gap runs win — keeping each witness's
words together where the evidence is equal. Three DP planes: M (col i aligned to seq
word j, incl. merges), X (column i is a gap in seq), Y (seq word j is an insertion).

A column is a dict key->token (a missing key means that witness has a gap there).
"""
import collections
import functools
from difflib import SequenceMatcher

NEG = float("-inf")


@functools.lru_cache(maxsize=1_000_000)
def _ratio(a, b):
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def char_ratio(a, b):
    """Char-level similarity of two strings in [0,1]; 1.0 on exact match."""
    if a == b:
        return 1.0
    if a > b:
        a, b = b, a
    return _ratio(a, b)


def _col_repr(col):
    """Representative token for a column = its most common value."""
    return collections.Counter(col.values()).most_common(1)[0][0]


def _align_to_profile(cols, seq, key, sim, sub_mid, gap_open, gap_ext, merge_min, join, end_gap=None, n_rows=None):
    reps = [_col_repr(c) for c in cols]
    # weighted gaps: skipping a column costs in proportion to how many rows have a word there
    occ = [min(1.0, len(c) / n_rows) for c in cols] if n_rows else [1.0] * len(cols)
    m, n = len(reps), len(seq)
    M = [[NEG] * (n + 1) for _ in range(m + 1)]   # i aligned to j (match/sub/merge)
    X = [[NEG] * (n + 1) for _ in range(m + 1)]   # gap in seq: column i unmatched ("U")
    Y = [[NEG] * (n + 1) for _ in range(m + 1)]   # gap in cols: seq word j inserted ("L")
    PM = [[None] * (n + 1) for _ in range(m + 1)]
    PX = [[None] * (n + 1) for _ in range(m + 1)]
    PY = [[None] * (n + 1) for _ in range(m + 1)]
    M[0][0] = 0.0
    eo, ee = (gap_open, gap_ext) if end_gap is None else (end_gap, end_gap)   # gaps before the first / after the last word
    for i in range(1, m + 1):
        X[i][0] = eo + ee * (i - 1); PX[i][0] = "X" if i > 1 else "M"
    for j in range(1, n + 1):
        Y[0][j] = eo + ee * (j - 1); PY[0][j] = "Y" if j > 1 else "M"

    def best3(i, j):                              # best end-state at (i,j) + which plane
        b = M[i][j]; s = "M"
        if X[i][j] > b: b, s = X[i][j], "X"
        if Y[i][j] > b: b, s = Y[i][j], "Y"
        return b, s

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            pv, ps = best3(i - 1, j - 1)
            bm, mv, prev = pv + (sim(reps[i - 1], seq[j - 1]) - sub_mid), "D", ps
            if merge_min is not None and j >= 2:           # two seq tokens -> one column
                s = sim(reps[i - 1], join(seq[j - 2], seq[j - 1]))
                if s >= merge_min:
                    pv2, ps2 = best3(i - 1, j - 2)
                    cand = pv2 + (s - sub_mid)
                    if cand > bm: bm, mv, prev = cand, "M", ps2
            if merge_min is not None and i >= 2:           # one seq token spans two columns
                s = sim(join(reps[i - 2], reps[i - 1]), seq[j - 1])
                if s >= merge_min:
                    pv3, ps3 = best3(i - 2, j - 1)
                    cand = pv3 + (s - sub_mid)
                    if cand > bm: bm, mv, prev = cand, "N", ps3
            M[i][j], PM[i][j] = bm, (mv, prev)
            go_, ge_ = (eo, ee) if j == n else (gap_open, gap_ext)
            xo, xe = M[i - 1][j] + go_ * occ[i - 1], X[i - 1][j] + ge_ * occ[i - 1]
            if xo >= xe: X[i][j], PX[i][j] = xo, "M"
            else: X[i][j], PX[i][j] = xe, "X"
            go_, ge_ = (eo, ee) if i == m else (gap_open, gap_ext)
            yo, ye = M[i][j - 1] + go_, Y[i][j - 1] + ge_
            if yo >= ye: Y[i][j], PY[i][j] = yo, "M"
            else: Y[i][j], PY[i][j] = ye, "Y"

    i, j = m, n
    _, st = best3(m, n)
    out = []
    while i > 0 or j > 0:
        if st == "M":
            mv, prev = PM[i][j]
            if mv == "D":
                c = cols[i - 1]; c[key] = seq[j - 1]; out.append(c); i -= 1; j -= 1
            elif mv == "M":
                c = cols[i - 1]; c[key] = join(seq[j - 2], seq[j - 1]); out.append(c); i -= 1; j -= 2
            else:  # "N"
                merged = {}
                for cc in (cols[i - 2], cols[i - 1]):
                    for kk, vv in cc.items():
                        merged[kk] = join(merged[kk], vv) if kk in merged else vv
                merged[key] = seq[j - 1]; out.append(merged); i -= 2; j -= 1
            st = prev
        elif st == "X":
            prev = PX[i][j]; out.append(cols[i - 1]); i -= 1; st = prev
        else:  # "Y"
            prev = PY[i][j]; out.append({key: seq[j - 1]}); j -= 1; st = prev
    out.reverse()
    return out


def star_align(items, sim=char_ratio, sub_mid=0.5, gap_open=-0.6, gap_ext=-0.15,
               merge_min=0.6, join=lambda a, b: a + " " + b, spine_first=False, max_cols=None, end_gap=None, refine=0, weighted_gaps=False):
    """Progressive star alignment. items: list of (key, [tokens]). Returns
    (order, columns) where order is the keys actually PLACED (spine first, in the
    order they were aligned) and each column is a dict key->token. sim(a,b)->[0,1];
    merge_min=None disables merge moves. Gaps are AFFINE: gap_open starts a run,
    gap_ext continues it.

    spine_first=True keeps items[0] as the spine instead of choosing the longest
    sequence (use when the caller wants a specific reference, e.g. a display member
    that defines the column structure). max_cols caps the alignment: once the column
    count exceeds it, no further sequences are added (a divergent member can otherwise
    explode the profile); the skipped keys are simply absent from `order` and the
    columns. end_gap (a small negative number) is the cost per column of a gap before a
    sequence's first or after its last word, so a segment that starts later than the others is
    not pulled leftwards. refine=k adds k passes in which every placed sequence is taken out and
    aligned again to the profile of all the others (a progressive alignment never revisits an
    early decision). weighted_gaps=True makes a gap in the new sequence cheaper at columns that
    few placed sequences fill. All default to the original behavior."""
    items = [it for it in items if it[1]]
    if not items:
        return [], []
    if not spine_first:
        items = sorted(items, key=lambda x: -len(x[1]))
    placed = [items[0][0]]
    cols = [{items[0][0]: t} for t in items[0][1]]
    for key, toks in items[1:]:
        if max_cols is not None and len(cols) > max_cols:
            break
        cols = _align_to_profile(cols, toks, key, sim, sub_mid, gap_open, gap_ext, merge_min, join, end_gap,
                                 len(placed) if weighted_gaps else None)
        placed.append(key)
    seqs = dict(items)
    for _ in range(refine):
        for key in placed:
            rest = [{k: v for k, v in c.items() if k != key} for c in cols]
            rest = [c for c in rest if c]
            if rest:
                cols = _align_to_profile(rest, seqs[key], key, sim, sub_mid, gap_open, gap_ext, merge_min, join, end_gap,
                                         len(placed) - 1 if weighted_gaps else None)
    return placed, cols
