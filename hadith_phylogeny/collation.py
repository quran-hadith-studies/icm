"""Matn normalisation, alignment and variant encoding (section 2)."""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Sequence, Tuple

from .text_model import NA, VariantSpec

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")


def normalize_arabic(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    t = _DIACRITICS.sub("", t)
    table = {
        "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
        "ة": "ه", "ۀ": "ه", "ھ": "ه", "ہ": "ه", "ه": "ه",
        "ى": "ي", "ی": "ي", "ئ": "ي", "ي": "ي",
        "ک": "ك", "ڪ": "ك", "گ": "ك",
        "ؤ": "و", "ٶ": "و",
        "پ": "ب", "چ": "ج", "ژ": "ز",
        "»": "", "«": "", "\u200c": "", "\u200f": "", "\u200e": "",
    }
    t = "".join(table.get(ch, ch) for ch in t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def squeeze(text: str) -> str:
    """Normalised text with all spaces removed.

    Glyph-level PDF extraction puts spaces at unpredictable places inside words,
    so clause matching is done on space-free strings.
    """
    return normalize_arabic(text).replace(" ", "")


def encode_clause_matrix(texts: Sequence[str], clauses: Sequence[Tuple[str, Sequence[str]]],
                         truncation_markers: Sequence[str] = ()
                         ) -> Tuple[List[List[int]], List[VariantSpec]]:
    """Presence/absence encoding of named clauses.

    A clause is 1 if any of its key phrases occurs, 0 if absent, and NA when the
    text is explicitly abridged (e.g. "وساق الحديث") - missing is never absence.
    """
    specs = [VariantSpec(name=name, n_states=2, cls="omission", kind="presence")
             for name, _ in clauses]
    rows: List[List[int]] = []
    for t in texts:
        n = squeeze(t)
        abridged = any(squeeze(m) in n for m in truncation_markers)
        row = []
        for _, keys in clauses:
            hit = any(squeeze(k) in n for k in keys)
            row.append(1 if hit else (NA if abridged else 0))
        rows.append(row)
    return rows, specs


def encode_categorical(texts: Sequence[str], name: str,
                       options: Sequence[Tuple[str, Sequence[str]]],
                       cls: str = "numeric") -> Tuple[List[int], VariantSpec]:
    """Categorical/numerical unit, e.g. the count word ستة / سبعة / خمسة."""
    labels = [o[0] for o in options]
    spec = VariantSpec(name=name, n_states=len(options), cls=cls,
                       kind="numeric", labels=tuple(labels))
    col = []
    for t in texts:
        n = squeeze(t)
        v = NA
        for j, (_, keys) in enumerate(options):
            if any(squeeze(k) in n for k in keys):
                v = j
                break
        col.append(v)
    return col, spec


def align_matns(texts: Sequence[str]) -> List[List[str]]:
    """Token-level alignment stub: whitespace tokens of the normalised text.

    The clause encoder above is the production path; this remains for
    exploratory collation of free text.
    """
    return [normalize_arabic(t).split() for t in texts]
