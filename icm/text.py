"""Text normalisation, tokens and clause segmentation shared by every ICM stage.

``norm`` is a verbatim copy of ``ebadollah/lib/hadith_text.normalize``: the served
full-text index (``hadith_fts``) was built with it, so every query must go through the
same function (tests assert byte-identity when the site's module is importable).
"""
from __future__ import annotations

import re
from typing import List, Sequence, Tuple

from .msa import char_ratio  # noqa: F401  (re-exported)

_HARAKAT = set(
    list(range(0x064B, 0x0653))            # fathatan..sukun
    + [0x0670, 0x0640]                     # superscript alef, tatweel
    + list(range(0x06D6, 0x06ED))          # small Qur'anic annotation signs
    + [0x0653, 0x0654, 0x0655, 0x0656, 0x0657, 0x0658]
)
_TRANS = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ٲ": "ا", "ٳ": "ا",
    "ى": "ی", "ي": "ی",                    # alef maqsura / arabic ya -> persian ya
    "ك": "ک",                               # arabic kaf -> persian kaf
    "ة": "ه",                               # ta marbuta -> he
    "ؤ": "و", "ئ": "ی", "ـ": "",
    "ۀ": "ه", "ﻻ": "لا",
})
_TAG_RE = re.compile(r"<[^>]+>")
_NONWORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def strip_tags(s: str) -> str:
    return _TAG_RE.sub("", s or "")


def norm(s: str) -> str:
    """Diacritic-insensitive, orthography-unified, lowercased, tag-free text."""
    s = strip_tags(s)
    s = "".join(ch for ch in s if ord(ch) not in _HARAKAT)
    s = s.translate(_TRANS)
    s = _NONWORD_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip().lower()


def tokens(s: str) -> List[str]:
    return norm(s).split()


def squeeze(s: str) -> str:
    """Normalised text without spaces (for substring tests insensitive to word splits)."""
    return norm(s).replace(" ", "")


def grams(s: str, n: int = 8) -> set:
    """Character n-grams of the space-free normalised text."""
    t = squeeze(s)
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}


# ---------------------------------------------------------------------------
# clauses
# ---------------------------------------------------------------------------
_BOUNDARY = set("،؛.:!؟?;,\n—–")
_WORD = re.compile(r"[^\s،؛.:!؟?;,—–]+")
# a clause may also start at a conjunction followed by a verb-like/relative start; we only
# split on these particles when a clause is too long and has no punctuation to split on
_SPLIT_PARTICLES = {"و", "ف", "ثم", "حتی", "الذی", "التی", "الذین", "من", "ان", "انه", "لا", "ما"}


def _words_with_breaks(raw: str) -> Tuple[List[str], List[bool]]:
    """Normalised tokens of ``raw`` and, per token, whether punctuation follows it."""
    toks: List[str] = []
    brk: List[bool] = []
    pos = 0
    for m in _WORD.finditer(strip_tags(raw)):
        between = strip_tags(raw)[pos:m.start()]
        if toks and any(c in _BOUNDARY for c in between):
            brk[-1] = True
        pos = m.end()
        for w in norm(m.group(0)).split():
            toks.append(w)
            brk.append(False)
    return toks, brk


def clauses(raw: str, min_tokens: int = 2, max_tokens: int = 12) -> Tuple[List[str], List[Tuple[int, int]]]:
    """Segment a matn into clauses.

    Returns ``(tokens, spans)`` where ``spans[i] = (start, end)`` is the half-open token
    range of clause ``i``. Punctuation splits first; pieces shorter than ``min_tokens``
    are merged into their neighbour; pieces longer than ``max_tokens`` are cut at a
    particle (و/ف/ثم/…) nearest to the middle, or into equal parts if there is none.
    """
    toks, brk = _words_with_breaks(raw)
    n = len(toks)
    if n == 0:
        return toks, []
    # list-like matns are often unpunctuated: break before a conjunction that introduces
    # a definite noun phrase ("و ال…", "وال…"), which starts a new item of the list
    for i in range(1, n):
        t = toks[i]
        if ((t in ("و", "ف") and i + 1 < n and toks[i + 1].startswith("ال"))
                or (len(t) > 4 and t[0] in "وف" and t[1:3] == "ال")):
            brk[i - 1] = True
    cuts = [i + 1 for i in range(n - 1) if brk[i]]
    pieces: List[List[int]] = []
    start = 0
    for c in cuts + [n]:
        if c > start:
            pieces.append([start, c])
        start = c
    # split long pieces
    out: List[List[int]] = []
    for a, b in pieces:
        out.extend(_split_long(toks, a, b, max_tokens))
    # merge short pieces forward (or backward for the last one)
    merged: List[List[int]] = []
    for a, b in out:
        if merged and (merged[-1][1] - merged[-1][0] < min_tokens):
            merged[-1][1] = b
        else:
            merged.append([a, b])
    if len(merged) > 1 and merged[-1][1] - merged[-1][0] < min_tokens:
        merged[-2][1] = merged[-1][1]
        merged.pop()
    spans = []
    for a, b in merged:
        if toks[a] in ("و", "ف") and b - a > 2:     # a bare conjunction belongs to no clause
            a += 1
        spans.append((a, b))
    return toks, spans


def _split_long(toks: Sequence[str], a: int, b: int, max_tokens: int) -> List[List[int]]:
    if b - a <= max_tokens:
        return [[a, b]]
    mid = (a + b) // 2
    cands = [i for i in range(a + 3, b - 2) if toks[i] in _SPLIT_PARTICLES]
    cut = min(cands, key=lambda i: abs(i - mid)) if cands else mid
    return _split_long(toks, a, cut, max_tokens) + _split_long(toks, cut, b, max_tokens)
