"""Versions of the narrator database: which Noor / KG ids ICM treats as one person.

    available()      -> [{name, label_fa, ...}]   "raw" first, then every built version file
    get(name)        -> Version                    KeyError for an unknown name
    default_name()   -> str                        the configured default when its file exists, else "raw"

``raw`` keeps every source's ids apart (no file). A merged version is a read-only SQLite snapshot written by
``python3 -m narwiki version build`` into ``config.NARRATOR_VERSIONS_DIR``::

    meta(k, v)      name, label_fa, description_fa, built, wiki_state, counts_json, eval_json
    person(id, canon, name, n_ext)          one row per group; canon = the group's ICM id (noor:<min>, else kg:<min>)
    ext(kind, ext_id, person_id, rule, score)
    edge(a, b, rule, score, evidence_json)  every accepted merge edge, ids as "kind:id"

ICM names a group by its canon id; ``Version.canon`` maps any member id to it. The file is never written, so
it is opened ``immutable`` and cached; ``Version.key`` goes into the stage hashes so a rebuilt file recomputes.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import defaultdict
from typing import Dict, Iterable, List, Optional

from . import config

_NAME = re.compile(r"[A-Za-z0-9_.-]{1,40}")
_ICM_KINDS = ("noor", "kg")                      # kinds that are ICM node ids; a group may also hold dirayah ids
_CACHE: Dict[str, "_Data"] = {}


def _path(name: str) -> str:
    return os.path.join(config.NARRATOR_VERSIONS_DIR, f"{name}.sqlite")


class _Data:
    """One version file loaded into memory (tens of thousands of rows)."""

    def __init__(self, path: str):
        c = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        try:
            self.meta: Dict[str, str] = {k: v for k, v in c.execute("select k, v from meta")}
            self.person = {r[0]: dict(canon=r[1], name=r[2], n_ext=r[3]) for r in c.execute("select id, canon, name, n_ext from person")}
            self.of: Dict[str, int] = {}
            self.group: Dict[int, List[dict]] = defaultdict(list)
            for kind, ext_id, pid, rule, score in c.execute("select kind, ext_id, person_id, rule, score from ext order by kind, ext_id"):
                i = f"{kind}:{ext_id}"
                self.of[i] = pid
                self.group[pid].append(dict(id=i, kind=kind, rule=rule, score=score))
            self.edges: List[dict] = []
            for a, b, rule, score, ev in c.execute("select a, b, rule, score, evidence_json from edge"):
                self.edges.append(dict(a=a, b=b, rule=rule, score=score, evidence=json.loads(ev) if ev else None))
        finally:
            c.close()
        self.multi = {pid: g for pid, g in self.group.items() if sum(1 for m in g if m["kind"] in _ICM_KINDS) >= 2}
        self.canon_of = {pid: self.person[pid]["canon"] for pid in self.multi if pid in self.person}
        self.by_canon = {c_: pid for pid, c_ in self.canon_of.items()}


def _data(name: str) -> Optional[_Data]:
    path = _path(name)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    key = f"{path}@{mtime}"
    if key not in _CACHE:
        for old in [k for k in _CACHE if k.startswith(path + "@")]:
            del _CACHE[old]
        _CACHE[key] = _Data(path)
    return _CACHE[key]


class Version:
    """A narrator database version; ``raw`` (``d`` is None) merges nothing."""

    def __init__(self, name: str, d: Optional[_Data] = None):
        self.name, self._d = name, d
        self.meta: Dict[str, str] = dict(d.meta) if d else {}

    @property
    def raw(self) -> bool:
        return self._d is None

    @property
    def key(self) -> List[str]:
        """[name, state]: what a stage hash must contain (a rebuilt file changes ``wiki_state``)."""
        return [self.name, self.meta.get("wiki_state", "")] if self._d else ["raw", ""]

    def canon(self, ids: Iterable[str]) -> Dict[str, str]:
        """{id: canon id} for the ids that belong to a group of at least two Noor/KG ids (the canon itself maps to itself)."""
        d = self._d
        out: Dict[str, str] = {}
        if d is None:
            return out
        for i in ids:
            pid = d.of.get(i)
            if pid in d.canon_of:
                out[i] = d.canon_of[pid]
        return out

    def _pid(self, i: str) -> Optional[int]:
        d = self._d
        if d is None:
            return None
        return d.by_canon.get(i) if i in d.by_canon else d.of.get(i)

    def members(self, canon_id: str) -> List[dict]:
        """Every id of the group ``canon_id`` (or any member) names, canon first: [{id, kind, rule, score}]; [] for a single id."""
        d = self._d
        pid = self._pid(canon_id)
        if d is None or pid not in d.multi:
            return []
        canon = d.canon_of[pid]
        return [dict(m) for m in sorted(d.multi[pid], key=lambda m: m["id"] != canon)]       # the group's own id first

    def edges(self, ids: Iterable[str]) -> List[dict]:
        """The accepted merge edges touching these ids: [{a, b, rule, score, evidence}]."""
        d = self._d
        want = set(ids)
        return [dict(e) for e in d.edges if e["a"] in want or e["b"] in want] if d else []

    def info(self) -> dict:
        """What a stage file records about the version it was computed with."""
        return dict(name=self.name, label_fa=self.meta.get("label_fa"), built=self.meta.get("built"), state=self.key[1])


RAW = Version("raw")


def get(name: Optional[str]) -> Version:
    if not name or name == "raw":
        return RAW
    if not _NAME.fullmatch(name):
        raise KeyError(name)
    d = _data(name)
    if d is None:
        raise KeyError(name)
    return Version(name, d)


def _json(s: Optional[str]):
    try:
        return json.loads(s) if s else None
    except ValueError:
        return None


def available() -> List[dict]:
    """``raw`` first, then every readable ``*.sqlite`` in the versions directory with its meta."""
    out = [dict(name="raw", label_fa="خام (بدون ادغام)", description_fa="شناسه‌ی هر پایگاه جدا می‌ماند؛ هیچ ادغامی انجام نمی‌شود.",
                built=None, state="", n_groups=0, counts=None, eval=None)]
    try:
        files = sorted(f for f in os.listdir(config.NARRATOR_VERSIONS_DIR) if f.endswith(".sqlite"))
    except OSError:
        return out
    for f in files:
        name = f[:-len(".sqlite")]
        if name == "raw" or not _NAME.fullmatch(name):
            continue
        try:
            d = _data(name)
        except sqlite3.Error:
            continue
        if d is None:
            continue
        m = d.meta
        out.append(dict(name=name, label_fa=m.get("label_fa"), description_fa=m.get("description_fa"),
                        built=m.get("built"), state=m.get("wiki_state", ""), n_groups=len(d.multi),
                        counts=_json(m.get("counts_json")), eval=_json(m.get("eval_json"))))
    return out


def default_name() -> str:
    n = config.NARRATOR_VERSION_DEFAULT
    return n if n == "raw" or (_NAME.fullmatch(n or "") and os.path.exists(_path(n))) else "raw"
