"""Arabic number words → integers, for death years in rijāl prose.

Adapted from ``arabic_year_parser.py`` in the Itqan repository (Ali Bin Shahid,
github.com/R3GENESI5/Itqan, README states MIT). Differences: both sides are compared
after ``normalize_arabic`` (so ة/ه, أ/ا and Persian letters never matter), a verb before
«سنة» is not required (Noor's وفات field is a bare «سنة أربع و سبعين و مائتين»), and
«ثلاث مائة» written as two words is read as 300.

    parse_arabic_number("أربع و سبعين و مائتين")   -> 274
    parse_arabic_number("ثلاث مائة و سبع عشرة")     -> 317
    parse_arabic_number("احدى و ثلاثين و مائة")      -> 131
    parse_arabic_number("260")                       -> 260
"""
from __future__ import annotations

import re
from typing import Optional

from .common import ascii_digits, normalize_arabic

_UNITS = {
    "واحد": 1, "واحده": 1, "احدى": 1, "احد": 1, "إحدى": 1,
    "اثنين": 2, "اثنتين": 2, "اثنى": 2, "اثنتى": 2, "اثنتي": 2, "اثني": 2, "اثنان": 2, "اثنتان": 2,
    "ثلاث": 3, "ثلاثه": 3,
    "اربع": 4, "اربعه": 4,
    "خمس": 5, "خمسه": 5,
    "ست": 6, "سته": 6,
    "سبع": 7, "سبعه": 7,
    "ثمان": 8, "ثماني": 8, "ثمانيه": 8,
    "تسع": 9, "تسعه": 9,
}
_TENS = {
    "عشر": 10, "عشره": 10, "عشرين": 20, "عشرون": 20,
    "ثلاثين": 30, "ثلاثون": 30, "اربعين": 40, "اربعون": 40,
    "خمسين": 50, "خمسون": 50, "ستين": 60, "ستون": 60, "سبعين": 70, "سبعون": 70,
    "ثمانين": 80, "ثمانون": 80, "تسعين": 90, "تسعون": 90,
}
_HUNDRED = {"مائه": 100, "مئه": 100, "ماية": 100, "مايه": 100}
_TWO_HUNDRED = {"مائتين": 200, "مئتين": 200, "مائتان": 200, "مئتان": 200, "مايتين": 200}
_STOP = {"في", "من", "او", "وهو", "وله", "وقد", "وكان", "وقيل", "رحمه", "خلافه", "بين", "على",
         "قال", "ودفن", "وصلى", "تقريبا", "ه", "هجريه", "ق", "هق", "للهجره", "بمكه", "بالمدينه",
         "بالكوفه", "ببغداد", "بقم", "بالبصره"}


def _norm(s: str) -> str:
    t = normalize_arabic(ascii_digits(s))
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# the tables are compared after the same normalisation as the input (ى→ي, أ→ا, ة→ه)
_UNITS = {_norm(k): v for k, v in _UNITS.items()}
_TENS = {_norm(k): v for k, v in _TENS.items()}
_HUNDRED = {_norm(k): v for k, v in _HUNDRED.items()}
_TWO_HUNDRED = {_norm(k): v for k, v in _TWO_HUNDRED.items()}
_STOP = {_norm(k) for k in _STOP}


def parse_arabic_number(phrase: str) -> Optional[int]:
    """Read one Arabic number (digits or words) from the start of ``phrase``.

    Returns None when no number is found. «خمس أو ست وأربعين» takes the first alternative.
    """
    if not phrase:
        return None
    t = _norm(phrase)
    m = re.match(r"^(?:سنه\s+)?(\d{1,4})\b", t)
    if m:
        return int(m.group(1))
    words = t.split()
    nums = []
    skip = False
    for w in words:
        if skip:
            skip = False
            continue
        clean = w
        if clean.startswith("و") and len(clean) > 2 and clean[1:] not in ("هو", "له", "قد", "كان", "قيل"):
            clean = clean[1:]
        if clean == "سنه" and not nums:
            continue
        hundred_compound = any(clean.endswith(h) and clean != h for h in _HUNDRED)
        if clean in _UNITS or clean in _TENS or clean in _HUNDRED or clean in _TWO_HUNDRED or hundred_compound:
            nums.append(clean)
        elif w == "و":
            continue
        elif clean == "او":
            skip = True
        elif clean in _STOP or w in _STOP:
            if nums:
                break
        elif nums:
            break
    if not nums:
        return None
    total, i, pending_unit = 0, 0, None
    while i < len(nums):
        w = nums[i]
        nxt = nums[i + 1] if i + 1 < len(nums) else None
        if w in _HUNDRED or w in _TWO_HUNDRED:
            if pending_unit is not None:            # «ثلاث مائة» = 300
                total += pending_unit * 100 if w in _HUNDRED else pending_unit * 200
                pending_unit = None
            else:
                total += _HUNDRED.get(w) or _TWO_HUNDRED[w]
            i += 1
            continue
        compound = next((h for h in _HUNDRED if w.endswith(h) and w != h), None)
        if compound:                                 # «ثلاثمائة»
            prefix = w[: -len(compound)]
            total += (_UNITS.get(prefix, 1)) * 100
            i += 1
            continue
        if w in _UNITS:
            if nxt in ("عشر", "عشره"):               # teens
                total += _UNITS[w] + 10
                i += 2
                continue
            if nxt in _HUNDRED or nxt in _TWO_HUNDRED:
                pending_unit = _UNITS[w]
                i += 1
                continue
            total += _UNITS[w]
            i += 1
            continue
        if w in _TENS:
            total += _TENS[w]
            i += 1
            continue
        i += 1
    if pending_unit is not None:
        total += pending_unit
    return total if total > 0 else None


_DEATH_VERB = r"(?:مات|توفي|توفى|قبض|وفاته|هلك|استشهد|قتل|درگذشت|وفات)"
_DEATH_RE = re.compile(_DEATH_VERB + r"\s*(?:في\s*)?(?:سنه|عام|سال)\s*([\w\s]{1,80})")
_BARE_YEAR_RE = re.compile(r"^(?:سنه|عام|سال)?\s*([\w\s]{1,80})$")


def extract_death_year(text: str, require_verb: bool = True) -> Optional[int]:
    """Death year from a prose sentence (with verb) or a bare year phrase."""
    t = _norm(text)
    m = _DEATH_RE.search(t)
    if m:
        y = parse_arabic_number(m.group(1))
        if y and 1 <= y <= 1500:
            return y
    if not require_verb:
        m = _BARE_YEAR_RE.match(t)
        if m:
            y = parse_arabic_number(m.group(1))
            if y and 1 <= y <= 1500:
                return y
    return None
