"""Projects: one per hadith family. Files under ``<cache>/projects/<pid>/``; nothing is overwritten.

    project.json                         seed, owner, status, step, current_run, branches (the versions), active
    decisions/<step>/<nnnn>.json         append-only, full state each time (search, selection, identities, phylogeny)
    stages/<stage>/<hash>.json           stage outputs, addressed by the hash of their inputs
    runs/<run>/manifest.json             which stage outputs a combination of decisions produced
    log.json                             stage log

A *run* is a combination of decision versions and pipeline version. Stages whose inputs did not change
between two runs point at the same stage file, so changing an identity never recomputes the search.

A *version* (a branch, ``project.json`` ``branches``) is a named set of decisions: for every step the number of
its current decision file (``heads``). The user works on the active one; a save writes a new decision file and
moves only the active version's head. A version made from another copies its heads, and since decision files
are never changed, later saves on either side never reach the other. The project's own status, step and
current_run are the active version's; the others keep theirs in ``state`` until they are activated again.
"""
from __future__ import annotations

import contextlib
import functools
import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from typing import Dict, List, Optional

from hadith_phylogeny.report import finite

from . import config, narrator_versions

STEPS = ("search", "selection", "identities", "phylogeny", "parts", "narrators", "go")      # steps with decisions (newer ones last: run ids keep their leading numbers)
WIZARD = ("seed", "search", "selection", "alignment", "identities", "phylogeny", "analysis", "done")


def h(*objs) -> str:
    return hashlib.sha256(json.dumps(objs, sort_keys=True, ensure_ascii=False, default=list).encode()).hexdigest()[:16]


def root() -> str:
    return os.path.join(config.CACHE_DIR, "projects")


_PID = re.compile(r"[0-9a-f]{6,20}")


def pdir(pid: str) -> str:
    if not _PID.fullmatch(pid or ""):
        raise ValueError("bad project id")
    return os.path.join(root(), pid)


def atomic_write(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        # JSON has no infinity: a source with zero evidence has log Z = -inf, stored as null (read as -inf)
        json.dump(finite(obj), f, ensure_ascii=False, separators=(",", ":"), default=float, allow_nan=False)
    os.replace(tmp, path)


def read(path: str, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except FileNotFoundError:
        return default


@contextlib.contextmanager
def lock(pid: str):
    os.makedirs(pdir(pid), exist_ok=True)
    with open(os.path.join(pdir(pid), ".lock"), "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --- projects -----------------------------------------------------------------------------------
@functools.lru_cache(maxsize=512)
def hadith_label(seed: str, words: int = 7) -> str:
    """A readable name for a project: the book and the first words of the seed's text (falls back to the id)."""
    try:
        from . import corpus
        r = corpus.get(seed)
        if r:
            txt = re.sub(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]", "", r.get("matn") or "")
            w = txt.split()
            head = " ".join(w[:words]) + ("…" if len(w) > words else "")
            return " — ".join(x for x in (r.get("title"), head) if x) or seed
    except Exception:
        pass
    return seed


def create(seed: str, owner: str, title: Optional[str] = None) -> dict:
    pid = h(seed, owner, time.time())[:10]
    p = dict(pid=pid, title=title or hadith_label(seed), seed=seed, owner=owner, created=now(), updated=now(),
             status="running", step="search", current_run=None, manual_search=True, manual_steps=True)
    with lock(pid):
        atomic_write(os.path.join(pdir(pid), "project.json"), p)
    refresh_indexes()
    return p


def anchor(p: dict, sdec: Optional[dict]) -> str:
    """The hadith the parts and the alignment are cut and measured on: the first of the project's seed hadiths. The user sees
    a list of equal hadiths; the project's own ``seed`` is the first until the search decision names another (``anchor``)."""
    return (sdec or {}).get("anchor") or p["seed"]


def delete(pid: str) -> None:
    """Remove a project with everything in it: decisions, stages, runs, log (and so every version). Not undoable."""
    d = pdir(pid)
    with lock(pid):
        shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(d, ignore_errors=True)                  # the lock file was re-made by the context's open
    refresh_indexes()


def load(pid: str) -> Optional[dict]:
    return read(os.path.join(pdir(pid), "project.json"))


def save(p: dict) -> None:
    p["updated"] = now()
    atomic_write(os.path.join(pdir(p["pid"]), "project.json"), p)


def list_projects() -> List[dict]:
    out = []
    if not os.path.isdir(root()):
        return out
    for pid in sorted(os.listdir(root())):
        p = load(pid)
        if p:
            out.append(p)
    return out


# --- versions (branches) ------------------------------------------------------------------------
STATE = ("status", "step", "current_run", "graph_hash", "awaiting", "error")       # the active version's, at the top of project.json


def _last(pid: str, step: str) -> int:
    """The highest decision number written for ``step`` by any version (the next one gets this + 1)."""
    d = os.path.join(pdir(pid), "decisions", step)
    if not os.path.isdir(d):
        return 0
    nums = [int(f[:-5]) for f in os.listdir(d) if f.endswith(".json") and f[:-5].isdigit()]
    return max(nums, default=0)


def _branches(pid: str, p: dict) -> List[dict]:
    """The project's versions; a project from before versions is one version over its latest decisions."""
    if not p.get("branches"):
        p["branches"] = [dict(id="1", name="نسخه‌ی ۱", created=p.get("created"), user=p.get("owner"), base=None,
                              heads={s: _last(pid, s) for s in STEPS}, state={})]
        p["active"] = "1"
    return p["branches"]


def _branch(pid: str, p: dict, bid: Optional[str] = None) -> dict:
    """A version (default: the active one)."""
    bs = _branches(pid, p)
    bid = bid or p.get("active")
    for b in bs:
        if b["id"] == bid:
            return b
    raise KeyError(bid)


def branches(pid: str) -> dict:
    """The versions as the page lists them: the active one with the project's own state."""
    p = load(pid) or {}
    out = []
    for b in _branches(pid, p):
        st = {k: p.get(k) for k in STATE} if b["id"] == p["active"] else (b.get("state") or {})
        out.append(dict({k: b.get(k) for k in ("id", "name", "created", "user", "base", "changed")}, heads=b["heads"],
                        status=st.get("status"), step=st.get("step"), current_run=st.get("current_run")))
    return dict(active=p.get("active"), versions=out)


def add_branch(pid: str, base: str, name: str, user: str) -> str:
    """A new version with the decisions of ``base`` (copied: later saves on either one never reach the other)."""
    with lock(pid):
        p = load(pid)
        src = _branch(pid, p, base)
        bid = str(1 + max(int(b["id"]) for b in p["branches"]))
        st = {k: p.get(k) for k in STATE} if src["id"] == p["active"] else dict(src.get("state") or {})
        p["branches"].append(dict(id=bid, name=name, created=now(), user=user, base=src["id"], heads=dict(src["heads"]), state=st))
        save(p)
    return bid


def activate(pid: str, bid: str) -> dict:
    """Make ``bid`` the version the project shows and edits: the current one's state goes into its entry, ``bid``'s comes out."""
    with lock(pid):
        p = load(pid)
        new, cur = _branch(pid, p, bid), _branch(pid, p)
        if new is not cur:
            cur["state"] = {k: p.get(k) for k in STATE}
            for k in STATE:
                p[k] = (new.get("state") or {}).get(k)
            p["status"] = p["status"] or "draft"
            new["state"] = {}
            p["active"] = new["id"]
            save(p)
    return p


def rename_branch(pid: str, bid: str, name: str) -> None:
    with lock(pid):
        p = load(pid)
        _branch(pid, p, bid)["name"] = name
        save(p)


def delete_branch(pid: str, bid: str) -> None:
    """Remove a version (its decision files stay on disk, like every file of a project). Never the active one."""
    with lock(pid):
        p = load(pid)
        b = _branch(pid, p, bid)
        if b["id"] == p["active"]:
            raise ValueError("the active version cannot be deleted")
        p["branches"] = [x for x in p["branches"] if x is not b]
        save(p)


# --- decisions ----------------------------------------------------------------------------------
def version(pid: str, step: str) -> int:
    """The number of the active version's current decision for ``step`` (0: none)."""
    return versions(pid)[step]


def versions(pid: str) -> Dict[str, int]:
    p = load(pid)
    if p is None:
        return {s: _last(pid, s) for s in STEPS}
    heads = _branch(pid, p)["heads"]
    return {s: int(heads.get(s) or 0) for s in STEPS}


def decision(pid: str, step: str, n: Optional[int] = None) -> Optional[dict]:
    n = version(pid, step) if n is None else n
    return read(os.path.join(pdir(pid), "decisions", step, f"{n:04d}.json")) if n else None


def history(pid: str, step: str, head: Optional[int] = None) -> List[dict]:
    """The decisions behind a version's current one, oldest first (each names the one it replaced; older files: n - 1)."""
    out, n = [], version(pid, step) if head is None else head
    while n > 0 and len(out) < 1000:
        d = decision(pid, step, n) or {}
        out.append(dict(version=n, **{k: d.get(k) for k in ("user", "created", "note")}))
        n = int(d.get("prev", n - 1) or 0)
    return out[::-1]


def add_decision(pid: str, step: str, payload: dict, user: str, note: str = "") -> int:
    if step not in STEPS:
        raise ValueError(step)
    with lock(pid):
        p = load(pid)
        b = _branch(pid, p) if p is not None else None
        n = _last(pid, step) + 1
        rec = dict(payload, version=n, user=user, created=now(), note=note, prev=int(b["heads"].get(step) or 0) if b else n - 1)
        path = os.path.join(pdir(pid), "decisions", step, f"{n:04d}.json")
        if os.path.exists(path):
            raise RuntimeError("decision version exists")
        atomic_write(path, rec)
        if b is not None:                                # only the active version moves on
            b["heads"][step] = n
            b["changed"] = rec["created"]
            save(p)
    return n


def narrator_version(pid: str) -> narrator_versions.Version:
    """The project's narrator database version: its ``narrators`` decision, else the configured default (``raw``
    when that file is absent). A decided version whose file has gone falls back to raw, logged."""
    name = (decision(pid, "narrators") or {}).get("name") or narrator_versions.default_name()
    try:
        return narrator_versions.get(name)
    except KeyError:
        log(pid, "identify", f"narrator database version {name!r} is not available; using raw", "warn")
        return narrator_versions.RAW


# --- stage files and runs -----------------------------------------------------------------------
def stage_path(pid: str, stage: str, hash_: str) -> str:
    return os.path.join(pdir(pid), "stages", stage, f"{hash_}.json")


def stage_get(pid: str, stage: str, hash_: str):
    return read(stage_path(pid, stage, hash_))


def stage_put(pid: str, stage: str, hash_: str, obj: dict) -> None:
    path = stage_path(pid, stage, hash_)
    if not os.path.exists(path):                        # content-addressed: never overwritten
        atomic_write(path, obj)


def run_id(vers: Dict[str, int], stage_hashes: Dict[str, str]) -> str:
    nums = [vers.get(s, 0) for s in STEPS]
    while len(nums) > 4 and not nums[-1]:               # trailing steps without a decision ("parts", "narrators"): the four-number run id from before they existed
        nums.pop()
    return ".".join(f"{n:04d}" for n in nums) + "-" + h(sorted(stage_hashes.items()))[:6]


def write_run(pid: str, vers: Dict[str, int], stage_hashes: Dict[str, str], extra: Optional[dict] = None) -> str:
    rid = run_id(vers, stage_hashes)
    path = os.path.join(pdir(pid), "runs", rid, "manifest.json")
    if not os.path.exists(path):
        atomic_write(path, dict(run=rid, versions=vers, stages=stage_hashes, created=now(), **(extra or {})))
    return rid


def runs(pid: str) -> List[dict]:
    d = os.path.join(pdir(pid), "runs")
    out = [read(os.path.join(d, r, "manifest.json")) for r in os.listdir(d)] if os.path.isdir(d) else []
    return sorted([r for r in out if r], key=lambda r: r["created"])


def run_hashes(pid: str, run: str = "current") -> Dict[str, str]:
    """The stage files (stage -> hash) a run is made of."""
    if run == "current":
        p = load(pid)
        run = p and p.get("current_run")
    m = read(os.path.join(pdir(pid), "runs", run or "", "manifest.json"))
    return dict((m or {}).get("stages") or {})


def run_stage(pid: str, run: str, stage: str):
    if run == "current":
        p = load(pid)
        run = p and p.get("current_run")
    m = read(os.path.join(pdir(pid), "runs", run or "", "manifest.json"))
    if not m or stage not in m["stages"]:
        return None
    return stage_get(pid, stage, m["stages"][stage])


def log(pid: str, stage: str, msg: str, level: str = "info") -> None:
    path = os.path.join(pdir(pid), "log.json")
    cur = read(path, [])
    cur.append(dict(ts=now(), stage=stage, level=level, msg=msg))
    atomic_write(path, cur[-500:])


# --- indexes ------------------------------------------------------------------------------------
def refresh_indexes() -> None:
    """index.json (all projects) and hadith_index.json (hadith -> projects that contain it)."""
    projs = list_projects()
    idx = [dict(pid=p["pid"], title=(hadith_label(p["seed"]) if p["title"] == p["seed"] else p["title"]), seed=p["seed"], owner=p["owner"], status=p["status"],
                step=p["step"], updated=p["updated"], current_run=p.get("current_run")) for p in projs]
    hidx: Dict[str, list] = {}
    for p in projs:
        wids = {p["seed"]}
        sel = decision(p["pid"], "selection")
        if sel:
            wids |= set(sel.get("selected", []))
        for w in wids:
            hidx.setdefault(w, []).append(dict(pid=p["pid"], status=p["status"], step=p["step"]))
    atomic_write(os.path.join(config.CACHE_DIR, "index.json"), dict(projects=sorted(idx, key=lambda x: x["updated"], reverse=True)))
    atomic_write(os.path.join(config.CACHE_DIR, "hadith_index.json"), hidx)
    atomic_write(os.path.join(config.CACHE_DIR, "narrator_index.json"), narrator_index(projs))


def narrator_index(projs) -> Dict[str, list]:
    """noor:/kg: id -> the projects whose current graph has that narrator (the narrator wiki's «پروژه‌های ICM»)."""
    out: Dict[str, list] = {}
    for p in projs:
        run = p.get("current_run")
        if not run:
            continue
        m = read(os.path.join(pdir(p["pid"]), "runs", run, "manifest.json"))
        key = m and m.get("stages", {}).get("graph")
        if not key:
            continue
        try:
            nodes = (stage_get(p["pid"], "graph", key) or {}).get("nodes", {})
        except Exception:                            # an unreadable stage file must not stop the index
            continue
        for nid in nodes:
            if nid.startswith(("noor:", "kg:")):
                out.setdefault(nid, []).append(dict(pid=p["pid"], title=p["title"], status=p["status"]))
    return out


def projects_of(wid: str) -> List[dict]:
    return (read(os.path.join(config.CACHE_DIR, "hadith_index.json"), {}) or {}).get(wid, [])
