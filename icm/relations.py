"""Recall signals from existing groupings (they propose candidates; they never decide).

S1  Noor: same ``group_id`` (exact-duplicate group), shared cluster, ``related_ahadith``
S2  v02 consensus set (exact-duplicate fold + alignment fold)
S3  hadith-kg: same meaning ``group_id`` / same ``takhrij`` id
"""
from __future__ import annotations

import struct
import zlib
from typing import Dict, List, Set

from . import config
from .corpus import conn, wid_parts


def noor_group(hid: int) -> Set[int]:
    c = conn("noor_rel")
    row = c.execute("select group_id from hadith where hid=?", (hid,)).fetchone()
    if not row or row[0] is None:
        return set()
    return {r[0] for r in c.execute("select hid from hadith where group_id=?", (row[0],))} - {hid}


def noor_cluster(hid: int, limit: int = 2000) -> Set[int]:
    c = conn("noor_rel")
    out: Set[int] = set()
    for (cid,) in c.execute("select cluster_id from cluster_member where hid=?", (hid,)).fetchall():
        out |= {r[0] for r in c.execute("select hid from cluster_member where cluster_id=? limit ?", (cid, limit))}
    return out - {hid}


def noor_related(hid: int) -> Set[int]:
    row = conn("noor_rel").execute("select ids from related where hid=?", (hid,)).fetchone()
    if not row:
        return set()
    raw = zlib.decompress(row[0])
    return {config.NOOR_OFFSET + i for i in struct.unpack("<%dI" % (len(raw) // 4), raw)}


def consensus_set(hid: int) -> Set[int]:
    c = conn("consensus")
    row = c.execute("select cid from membership where hid=?", (hid,)).fetchone()
    if not row:
        return set()
    return {r[0] for r in c.execute("select hid from membership where cid=?", (row[0],))} - {hid}


def kg_group(kid: int) -> Set[int]:
    c = conn("kg")
    row = c.execute("select group_id, takhrij from kg_hadith where id=?", (kid,)).fetchone()
    if not row:
        return set()
    out: Set[int] = set()
    if row[0] is not None:
        out |= {r[0] for r in c.execute("select id from kg_hadith where group_id=?", (row[0],))}
    if row[1] is not None:
        out |= {r[0] for r in c.execute("select id from kg_hadith where takhrij=?", (row[1],))}
    return out - {kid}


def signals(wid: str) -> Dict[str, Set[str]]:
    """Candidate wids proposed for ``wid`` by each relation source."""
    corpus, n = wid_parts(wid)
    out: Dict[str, Set[str]] = {}
    if corpus == "v02":
        out["consensus"] = {f"v02:{i}" for i in consensus_set(n)}
        if n >= config.NOOR_OFFSET:
            out["noor_group"] = {f"v02:{i}" for i in noor_group(n)}
            out["noor_cluster"] = {f"v02:{i}" for i in noor_cluster(n)}
            out["noor_related"] = {f"v02:{i}" for i in noor_related(n)}
    else:
        out["kg_group"] = {f"kg:{i}" for i in kg_group(n)}
    return {k: v for k, v in out.items() if v}
