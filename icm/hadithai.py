"""The hadith.ai v2 segmentation and narrator tags of a v02 hadith (``hadithai.sqlite``, built by
``icm/build/hadithai.py`` from the hub books converted from api.hadith.ai).

    row(hid)                -> {segs, chain, speaker} or None
    parts(hid, tokens)      -> (spans, owners) of the matn's segments on ``tokens``, or None
    chain_elems(hid)        -> (elems, speaker) in the shape of ``sanad_parse.parse``, or None
    mentions(hid)           -> {position: mention row} (``mention``: certain Noor link or context suggestion)

A Noor hadith served from Jami' al-Ahadith (v02 id 800M + Noor id) is the same Noor record as the
hadith.ai one, so it is looked up by its Noor id. hadith.ai's texts are another edition of the same
record (spacing, diacritics, an occasional word), so segments are not taken by character offset: their
tokens are aligned with the tokens the wizard uses and the boundaries are carried over. When the two
texts do not agree well enough (``MIN_AGREE``) nothing is returned and the rule-based cut is used.

Everything here is a suggestion: the parts are the automatic segmentation the user corrects and confirms
(step 2), the chain the automatic reading of the isnad whose names step 4 identifies.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import zlib
from difflib import SequenceMatcher
from typing import List, Optional, Sequence, Tuple

from . import config, sanad_parse
from .similarity import match_key
from .text import norm, tokens

MIN_AGREE = 0.85          # share of tokens of either text that must align with the other
MIN_PART = 2              # a segment shorter than this joins its neighbour (as the rule-based cut does)
_local = threading.local()


def _conn() -> Optional[sqlite3.Connection]:
    c = getattr(_local, "c", False)
    if c is False:
        c = None
        if os.path.exists(config.HADITHAI_DB):
            c = sqlite3.connect(f"file:{config.HADITHAI_DB}?mode=ro&immutable=1", uri=True, check_same_thread=False)
        _local.c = c
    return c


def available() -> bool:
    return _conn() is not None


def key(hid: int) -> int:
    return hid - config.NOOR_OFFSET if hid >= config.NOOR_OFFSET else hid


def row(hid: int) -> Optional[dict]:
    c = _conn()
    if c is None:
        return None
    r = c.execute("select z from seg where hid=?", (key(int(hid)),)).fetchone()
    return json.loads(zlib.decompress(r[0])) if r else None


def mentions(hid: int) -> dict:
    c = _conn()
    if c is None:
        return {}
    try:
        rows = c.execute("select i, name, ravi_id, link, slot_pos, sugg_ravi, sugg, sugg_n from mention where hid=?", (key(int(hid)),)).fetchall()
    except sqlite3.OperationalError:                   # a hadithai.sqlite built before the mention table
        return {}
    return {r[0]: dict(name=r[1], ravi_id=r[2], link=r[3], slot_pos=r[4], sugg_ravi=r[5], sugg=r[6], sugg_n=r[7]) for r in rows}


def parts(hid: int, toks: Sequence[str]) -> Optional[Tuple[List[Tuple[int, int]], List[Optional[str]]]]:
    """The matn's hadith.ai segments as [start, end) spans of ``toks`` that cover it, and each one's speaker."""
    r = row(hid)
    if not r or not r["segs"] or not toks:
        return None
    seg_toks = [tokens(s["t"]) for s in r["segs"]]
    hai = [t for st in seg_toks for t in st]
    lab = [k for k, st in enumerate(seg_toks) for _ in st]
    if not hai:
        return None
    sm = SequenceMatcher(None, [match_key(t) for t in hai], [match_key(t) for t in toks], autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    m = sum(b.size for b in blocks)
    if m < MIN_AGREE * len(toks) or m < MIN_AGREE * len(hai):
        return None
    first = {}
    for b in blocks:
        for d in range(b.size):
            first.setdefault(lab[b.a + d], b.b + d)
    starts = sorted((j, k) for k, j in first.items())
    # each segment starts where its first aligned token is; the first covers the beginning of the text
    cuts = [0] + [j for j, _ in starts[1:]]
    spans, owners = [], []
    for n, (j, k) in enumerate(starts):
        a, b = cuts[n], (cuts[n + 1] if n + 1 < len(cuts) else len(toks))
        if b <= a:
            continue
        s = r["segs"][k]
        if spans and (b - a < MIN_PART or spans[-1][1] - spans[-1][0] < MIN_PART):
            spans[-1] = (spans[-1][0], b)
            continue
        spans.append((a, b))
        owners.append(s.get("oc") or s.get("o"))
    return (spans, owners) if spans else None


def chain_elems(hid: int):
    """The opening isnad as hadith.ai tagged it, one element per tagged name, classified by the same grammar
    as ``sanad_parse.parse`` (relatives, anonymous narrators, honorifics; the Prophet is the speaker, not an
    element; an Imam who ends the chain is also the speaker). An element whose mention is tied to a Noor
    narrator carries it as ``hai`` (``mentions``): a certain link or a context suggestion."""
    r = row(hid)
    if not r or not r["chain"]:
        return None
    elems: List[dict] = []
    speaker = None
    ment = mentions(hid)
    for ci, c in enumerate(r["chain"]):
        p = sanad_parse.parse("عن " + c["name"])
        term = norm(c.get("term") or "") or None
        if p["speaker"] and p["speaker"]["kind"] == "prophet" and not p["elems"]:
            speaker = dict(kind="prophet", raw=norm(c["name"]), term=term)
            continue
        got = [e for e in p["elems"] if e["kind"] not in ("gap", "prev")]
        m = ment.get(ci) if len(got) == 1 else None     # a tagged span the grammar splits in two is no one person
        for e in got:
            el = dict(e, term=term, tag=c.get("tag"))
            if m and (m["ravi_id"] or m["sugg_ravi"]):
                el["hai"] = m
            elems.append(el)
    if not elems:
        return None
    last = elems[-1]
    if speaker is None and last["kind"] == "name" and last.get("honorific") and (
            last.get("tag") == "Exporter" or sanad_parse._starts_with(last["name"].split(), sanad_parse.IMAM_KUNYA)):
        speaker = dict(kind="imam", raw=last["raw"], term=last["term"], elem=len(elems) - 1)
    return elems, speaker
