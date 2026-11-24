"""Book authors (collectors) as persons of the narrator databases - only the links that ``icm.build.authors`` is sure of.

    lookup(author, death)  -> {"ids": ["noor:5102"], "by": ..., "known": [...]} or None
    contradicts(pid, death) -> True when that person's recorded death year is several years from the book author's

The table (``authors.json``) is built by ``python3 -m icm build authors``: an author is linked only when name and death year
both agree with exactly one person of a database (or a person made by hand, see ``icm/data/author_overrides.json``). An
author that is not in the table is not known to be anybody; he stays a name for the user to decide.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections import defaultdict
from typing import Dict, Optional, Set

from narrators.common import name_tokens

from . import config

AUTHORS_JSON = os.environ.get("ICM_AUTHORS", os.path.join(config.DATA_DIR, "authors.json"))
AUTHOR_WIKI_JSON = os.environ.get("ICM_AUTHOR_WIKI", os.path.join(config.DATA_DIR, "author_wiki.json"))     # book authors the databases do not hold, as persons of the narrator wiki (narwiki.authors)
TITLES = {"شيخ", "سيد", "امام", "علامه", "حافظ", "ميرزا", "حاج", "ملا", "محقق", "ايه", "استاد", "دكتور", "شهيد", "قاضي", "فقيه", "مولي"}
# Noor's death years that came from an automatic link to another database are not used: a wrong link (al-Tusi 525, al-Tabrisi 523)
# would carry its error with it
LINKED_SOURCES = ("kg", "arsanad", "hawramani", "muslimscholars")

_lock = threading.Lock()
_table: Optional[Dict[str, dict]] = None
_noor: Optional[Dict[int, Set[float]]] = None
_key: list = [None, ""]


def toks(s: str) -> tuple:
    """The words of a name in order, without titles. A catalogue's «family، given» is turned into «given family»."""
    if "،" in s and "(" not in s.split("،")[0]:
        s = " ".join(reversed([x.strip() for x in s.split("،")]))
    return tuple(t for t in name_tokens(s) if t not in TITLES)


def key_of(author: str, death) -> str:
    """Order-insensitive key: «مفید، محمد بن محمد» and «محمد بن محمد مفید» are the same author."""
    return " ".join(sorted(set(toks(author)))) + "|" + str(int(death) if death else "")


def noor_deaths() -> Dict[int, Set[float]]:
    """{ravi_id: {death years}} from Noor itself, the rijal books and WikiShia."""
    global _noor
    with _lock:
        if _noor is None:
            d: Dict[int, Set[float]] = defaultdict(set)
            try:
                c = sqlite3.connect(f"file:{config.NARRATORS_DB}?mode=ro&immutable=1", uri=True)
                for rid, lo, hi in c.execute("select ravi_id, lo, hi from fact where kind = 'death' and lo is not null and hi - lo <= 1 "
                                             "and source not in (%s)" % ",".join("?" * len(LINKED_SOURCES)), LINKED_SOURCES):
                    d[rid].add((lo + hi) / 2)
                c.close()
            except sqlite3.Error:                    # no facts table: nothing contradicts anybody
                pass
            _noor = dict(d)
    return _noor


def table() -> Dict[str, dict]:
    global _table
    with _lock:
        if _table is None:
            try:
                _table = json.load(open(AUTHORS_JSON, encoding="utf-8"))["authors"]
            except FileNotFoundError:
                _table = {}
            try:                                                   # the wiki's persons for authors no database holds: their link, as a sure one
                for k, w in json.load(open(AUTHOR_WIKI_JSON, encoding="utf-8"))["authors"].items():
                    if k in _table and not _table[k].get("ids") and w.get("ids"):
                        _table[k] = dict(_table[k], ids=w["ids"], by="catalogue: " + w.get("by", "narrator wiki"))
            except (FileNotFoundError, KeyError, ValueError):
                pass
    return _table


def key() -> str:
    """Goes into the identify stage's key: a rebuilt table changes who a book's author is."""
    try:
        sts = [os.stat(p) for p in (AUTHORS_JSON, AUTHOR_WIKI_JSON) if os.path.exists(p)]
    except OSError:
        return ""
    if not sts:
        return ""
    sig = tuple((st.st_mtime_ns, st.st_size) for st in sts)
    if _key[0] != sig:
        h = hashlib.md5()
        for p in (AUTHORS_JSON, AUTHOR_WIKI_JSON):
            if os.path.exists(p):
                h.update(open(p, "rb").read())
        _key[:] = [sig, h.hexdigest()[:12]]
    return _key[1]


def lookup(author: Optional[str], death) -> Optional[dict]:
    """The sure link of an author as a catalogue wrote him with the death year it gives; None when there is none."""
    if not author or not death:
        return None
    rec = table().get(key_of(author, death))
    return rec if rec and rec.get("ids") else None


def name_link(author: Optional[str], death) -> Optional[str]:
    """The one person whose name begins with the author's full name and who has no death year of his own (so ``lookup`` could not
    check): a suggestion for the user to approve, never used on its own. None when there is none."""
    if not author or not death:
        return None
    ids = (table().get(key_of(author, death)) or {}).get("by_name") or []
    return ids[0] if len(ids) == 1 else None


def words_link(author: Optional[str], death) -> Optional[str]:
    """The one person who has every word of the author's name and a death year within three of the book's or none: a weaker suggestion than
    ``name_link`` (measured 94.7% with the years ignored)."""
    if not author or not death:
        return None
    ids = (table().get(key_of(author, death)) or {}).get("by_words") or []
    return ids[0] if len(ids) == 1 else None


def contradicts(pid: str, death, margin: int = 5) -> bool:
    """The person's own death year (when the database has one) is more than ``margin`` years from the book author's."""
    if not death:
        return False
    kind, _, raw = pid.partition(":")
    if kind == "noor" and raw.isdigit():
        ds = noor_deaths().get(int(raw))
        return bool(ds) and min(abs(d - death) for d in ds) > margin
    return False
