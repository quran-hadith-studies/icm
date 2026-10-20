"""Rule-based parser of isnad text.

    parse(text) -> {number, quoted_work, elems, speaker, flags}

``elems`` lists the narrators in the order of the text (collector side first). Each is
  {kind: name | relative | anon | gap | prev, raw, name, term, honorific, alts?}
``name`` is the normalised, honorific-free name; ``term`` the transmission word that
introduced it (حدثنا, عن, ...); ``honorific`` is true when «عليه السلام» etc. followed the
name (a hint that the person is an Imam). ``speaker`` is the person the matn is attributed
to when the chain ends in the Prophet or an Imam (``kind`` prophet | imam), else None; a
companion who ends a Sunni chain stays in ``elems``.

Everything is grammar: no name database. Resolving names to persons is ``identity.py``.
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

from .text import norm

_DATA = json.load(open(os.path.join(os.path.dirname(__file__), "data", "sanad_terms.json"), encoding="utf-8"))
TRANSMIT = {norm(x) for x in _DATA["transmit"]}
HONORIFIC = {norm(x) for x in _DATA["honorific_start"]}
RELATIVES = {norm(x) for x in _DATA["relatives"]}
ANON = [norm(x) for x in _DATA["anonymous"]]
GAPS = [norm(x) for x in _DATA["gap_markers"]]
PROPHET = [norm(x) for x in _DATA["prophet"]]
IMAM_KUNYA = [norm(x) for x in _DATA["imam_kunya"]]
NON_NAME = {norm(x) for x in _DATA.get("non_name", [])}
_PROCLITIC = {p + t for p in ("و", "ف") for t in TRANSMIT}
# «قرأت على X», «سمعت من X», «كتب إلي X»: the preposition belongs to the transmission phrase, not to the name
PREP = {norm(k): {norm(x) for x in v} for k, v in _DATA.get("transmit_prep", {}).items()}

_LEAD_NO = re.compile(r"^\s*\(?\d+\)?\s*[-–ـ.:]\s*")
_SIGLA = re.compile(r"^\s*([^\]\[،,:]{0,30})[،,]?\s*\[([^\]]{2,60})\]\s*[،,:]?\s*")
_PAREN = re.compile(r"\([^)]{0,40}\)|\[[^\]]{0,40}\]")


IMAM_HON = {"علیه", "علیها", "علیهما", "علیهم", "ع", "سلام", "صلوات"}
PROPHET_HON = {"صلی", "ص", "صلعم"}
# companions written "ابن X" with no father, which glue onto the previous name when «عن» is missing
GLUED = {("ابن", "عباس"), ("ابن", "عمر"), ("ابن", "مسعود"), ("ابن", "الزبیر"), ("ابن", "عمرو")}


def _strip_honorific(toks: List[str]):
    """Cut the name at the first honorific/blessing word.

    Returns (name tokens, kind) with kind in {None, 'imam', 'prophet', 'blessing'}: «عليه السلام»
    follows Imams (and prophets), «صلى الله عليه وآله» the Prophet, «رضي الله عنه» anyone."""
    for i, t in enumerate(toks):
        if t in HONORIFIC and (i > 0 or len(toks) > 1):
            if t in PROPHET_HON:
                return toks[:i], "prophet"
            if t in IMAM_HON:
                return toks[:i], "imam"
            return toks[:i], "blessing"
    return toks, None


def _starts_with(toks: List[str], phrases: List[str]) -> bool:
    s = " ".join(toks)
    return any(s == p or s.startswith(p + " ") for p in phrases)


def parse(text: str) -> dict:
    flags: List[str] = []
    raw = text or ""
    number = None
    m = _LEAD_NO.match(raw)
    if m:
        number = re.sub(r"\D", "", m.group(0)) or None
        raw = raw[m.end():]
    quoted = None
    m = _SIGLA.match(raw)
    if m:
        quoted = m.group(2).strip()
        raw = raw[m.end():]
    raw = _PAREN.sub(" ", raw)
    toks = norm(raw).split()
    # split into segments at transmission terms
    segs: List[Dict] = []
    cur: List[str] = []
    term = None
    for t in toks:
        if t in TRANSMIT:
            if cur:
                segs.append(dict(term=term, toks=cur))
            cur, term = [], t
        else:
            cur.append(t)
    if cur:
        segs.append(dict(term=term, toks=cur))
    for sg in segs:                  # the preposition joins the term; «علی بن …» stays a name; «… قراءة عليه» leaves nothing
        t0 = sg["toks"]
        if t0 and t0[0] in PREP.get(sg["term"], ()) and not (len(t0) > 1 and t0[1] in ("بن", "ابن")):
            sg["term"], sg["toks"] = f"{sg['term']} {t0[0]}", t0[1:]

    elems: List[dict] = []
    speaker = None
    k = -1
    while k + 1 < len(segs):
        k += 1
        sg = segs[k]
        toks_, term = sg["toks"], sg["term"]
        while toks_ and toks_[0] in ("و", "ف", "ثم"):
            toks_ = toks_[1:]
        toks_, hon_kind = _strip_honorific(toks_)
        honorific = hon_kind == "imam"
        while toks_ and toks_[-1] in ("و", "ف", "ثم"):
            toks_ = toks_[:-1]
        if not toks_:
            if hon_kind in ("prophet", "imam") and k > 0:       # "و صلى الله عليه وآله" after «عنه»
                pass
            continue
        # split a glued "… ابن عباس" companion
        for gi in range(2, len(toks_) - 1):
            if (toks_[gi], toks_[gi + 1]) in GLUED and len(toks_) == gi + 2:
                segs.insert(k + 1, dict(term=None, toks=toks_[gi:]))
                toks_ = toks_[:gi]
                flags.append("glued_companion")
                break
        last = (k == len(segs) - 1)
        s = " ".join(toks_)
        if _starts_with(toks_, PROPHET) or (toks_[0] in ("النبی", "رسول") and "الله" in toks_[:3]):
            if last or all(not segs[j]["toks"] or True for j in range(k + 1, len(segs))):
                speaker = dict(kind="prophet", raw=s, term=term)
                if last:
                    continue
            continue
        if any(s == g or s.startswith(g + " ") for g in GAPS):
            elems.append(dict(kind="gap", raw=s, name=s, term=term, honorific=False))
            continue
        if s in RELATIVES and s != "عنه":
            elems.append(dict(kind="relative", raw=s, name=s, term=term, honorific=honorific))
            continue
        if s == "عنه":
            if hon_kind == "prophet":
                speaker = dict(kind="prophet", raw=s, term=term, anaphoric=True)
            elif hon_kind == "imam":
                speaker = dict(kind="imam", raw=s, term=term, anaphoric=True)
            else:
                elems.append(dict(kind="prev", raw=s, name=s, term=term, honorific=False))
            continue
        if _starts_with(toks_, ANON) or toks_[0] in ("رجل", "رجال") or s.startswith("عده من") or s.startswith("بعض "):
            elems.append(dict(kind="anon", raw=s, name=s, term=term, honorific=False))
            continue
        # co-narrators joined by a bare «و»: "محمد بن یحیی و احمد بن ادریس"
        parts = _split_conj(toks_)
        if len(parts) > 1:
            elems.append(dict(kind="name", raw=s, name=" ".join(parts[0]), term=term, honorific=honorific,
                              blessing=hon_kind == "blessing", alts=[" ".join(p) for p in parts]))
            flags.append("co_narrators")
            continue
        elems.append(dict(kind="name", raw=s, name=s, term=term, honorific=honorific, blessing=hon_kind == "blessing"))

    # an Imam as the last element of an Imami chain is the speaker when nothing follows
    if speaker is None and elems and elems[-1]["kind"] == "name" and elems[-1]["honorific"] \
            and (_starts_with(elems[-1]["name"].split(), IMAM_KUNYA) or elems[-1]["name"].split()[0] in ("ابی", "الصادق", "الباقر")):
        speaker = dict(kind="imam", raw=elems[-1]["raw"], term=elems[-1]["term"], elem=len(elems) - 1)
    return dict(number=number, quoted_work=quoted, elems=elems, speaker=speaker, flags=flags)


def _split_conj(toks: List[str]) -> List[List[str]]:
    parts: List[List[str]] = [[]]
    for t in toks:
        if t == "و":
            parts.append([])
        else:
            parts[-1].append(t)
    parts = [p for p in parts if p]
    if len(parts) > 1 and all(len(p) >= 2 for p in parts):
        return parts
    return [[t for t in toks if t != "و"]] if len(parts) <= 1 else [toks]


def is_anon(name: str) -> bool:
    """True for an unnamed narrator: «رجل», «عدة من أصحابنا», «من ذكره», ..."""
    toks = norm(name).split()
    while toks and toks[0] in ("و", "ف", "عمن", "من"):
        if toks[0] == "من" and len(toks) > 1 and toks[1] in ("ذکره", "حدثه", "رواه", "سمع"):
            return True
        toks = toks[1:] if toks[0] != "من" else toks
        if toks and toks[0] == "من":
            break
    s = " ".join(toks)
    return bool(toks) and (_starts_with(toks, ANON) or toks[0] in ("رجل", "رجال", "ذکره", "بعض") or s.startswith("عده من"))


def strip_nonname(name: str) -> dict:
    """What remains of a parsed name once isnad words glued to it are cut off (for step-4 suggestions only).

    «عایشه ان رسول الله» -> core «عایشه», «سفیان الثوری باسناده» -> «سفیان الثوری», «علی بن الحسین یحدث» ->
    «علی بن الحسین». Returns {core, stripped: [words], reasons: [prophet | gap | connective]}."""
    toks = norm(name).split()
    stripped: List[str] = []
    reasons: List[str] = []
    while toks and (toks[0] in _PROCLITIC or toks[0] in TRANSMIT or toks[0] in ("و", "ف", "ثم")):
        stripped.append(toks.pop(0))
        reasons.append("connective")
    for i in range(len(toks)):
        rest = toks[i:]
        lead = 1 if rest[0] in ("ان", "انه", "قال", "عن") and len(rest) > 1 else 0
        if i and (_starts_with(rest[lead:], PROPHET) or (rest[lead] in ("النبی", "رسول") and "الله" in rest[lead:lead + 3])):
            stripped += rest
            reasons.append("prophet")
            toks = toks[:i]
            break
        if i and _starts_with(rest, GAPS):
            stripped += rest
            reasons.append("gap")
            toks = toks[:i]
            break
    while toks and (toks[-1] in NON_NAME or toks[-1] in TRANSMIT or toks[-1] in ("و", "ف", "ثم")):
        stripped.insert(0, toks.pop())
        reasons.append("connective")
    return dict(core=" ".join(toks), stripped=stripped, reasons=sorted(set(reasons)))


def not_a_name(name: str) -> bool:
    """True when a parsed "name" is isnad wording, not a person: «لما ثقل», «وقال رسول الله», «اقول», «فقد»."""
    toks = norm(name).split()
    if not toks:
        return True
    if toks[0] in ("لما", "اذا", "حین", "قال", "وقال", "فقال") or toks[0] in _PROCLITIC and len(toks) == 1:
        return True
    if _starts_with(toks, PROPHET) or (toks[0] in ("النبی", "رسول") and "الله" in toks[:3]):
        return False                                       # the speaker, handled by parse()
    words = NON_NAME | TRANSMIT | _PROCLITIC | HONORIFIC | {"و", "ف", "ثم"}
    return all(t in words for t in toks) or not strip_nonname(name)["core"]
