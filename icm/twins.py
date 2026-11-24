"""Twins: the other hadiths with the same text that carry narrator ids, found once by ``python -m icm build twins`` (``twins.sqlite``).

    available()            the file is there
    key()                  its build stamp: goes into the identify stage's key
    of(corp, hid)          [(db, id)]  corp 'v02' | 'kg'; db 'noor' (a Noor hadith id) | 'kg' (a KG source hadith id)
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import List, Tuple

from . import config

_local = threading.local()
_key: list = [None, ""]


def _conn():
    p = config.TWINS_DB
    c = getattr(_local, "c", None)
    if c is None or getattr(_local, "p", None) != p:
        try:
            c = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True, check_same_thread=False)
        except sqlite3.Error:
            return None
        _local.c, _local.p = c, p
    return c


def available() -> bool:
    return os.path.exists(config.TWINS_DB)


def key() -> str:
    try:
        st = os.stat(config.TWINS_DB)
    except OSError:
        return ""
    sig = (st.st_mtime_ns, st.st_size)
    if _key[0] != sig:
        try:
            r = sqlite3.connect(f"file:{config.TWINS_DB}?mode=ro&immutable=1", uri=True).execute("select v from meta where k='built'").fetchone()
        except sqlite3.Error:
            r = None
        _key[:] = [sig, (r[0] if r else str(st.st_size))]
    return _key[1]


def of(corp: str, hid: int) -> List[Tuple[str, int]]:
    c = _conn()
    if c is None:
        return []
    try:
        return [(r[0], r[1]) for r in c.execute("select db, tid from twin where corp=? and hid=?", (corp, int(hid)))]
    except sqlite3.Error:
        return []
