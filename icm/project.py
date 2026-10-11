"""Projects: one per hadith family. Files under ``<cache>/projects/<pid>/``; nothing is overwritten.

    project.json                         seed, owner, status, step, current_run
    decisions/<step>/<nnnn>.json         append-only, full state each time (search, selection, identities, phylogeny)
    stages/<stage>/<hash>.json           stage outputs, addressed by the hash of their inputs
    runs/<run>/manifest.json             which stage outputs a combination of decisions produced
    log.json                             stage log

A *run* is a combination of decision versions and pipeline version. Stages whose inputs did not change
between two runs point at the same stage file, so changing an identity never recomputes the search.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
import time
from typing import Dict, List, Optional

from hadith_phylogeny.report import finite

from . import config

STEPS = ("search", "selection", "identities", "phylogeny")      # steps with decisions
WIZARD = ("seed", "search", "selection", "alignment", "identities", "phylogeny", "analysis", "done")


def h(*objs) -> str:
    return hashlib.sha256(json.dumps(objs, sort_keys=True, ensure_ascii=False, default=list).encode()).hexdigest()[:16]


def root() -> str:
    return os.path.join(config.CACHE_DIR, "projects")


def pdir(pid: str) -> str:
    if not pid.replace("-", "").isalnum():
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
def create(seed: str, owner: str, title: Optional[str] = None) -> dict:
    pid = h(seed, owner, time.time())[:10]
    p = dict(pid=pid, title=title or seed, seed=seed, owner=owner, created=now(), updated=now(),
             status="running", step="search", current_run=None)
    with lock(pid):
        atomic_write(os.path.join(pdir(pid), "project.json"), p)
    refresh_indexes()
    return p


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


# --- decisions ----------------------------------------------------------------------------------
def version(pid: str, step: str) -> int:
    d = os.path.join(pdir(pid), "decisions", step)
    if not os.path.isdir(d):
        return 0
    nums = [int(f[:-5]) for f in os.listdir(d) if f.endswith(".json") and f[:-5].isdigit()]
    return max(nums, default=0)


def versions(pid: str) -> Dict[str, int]:
    return {s: version(pid, s) for s in STEPS}


def decision(pid: str, step: str, n: Optional[int] = None) -> Optional[dict]:
    n = version(pid, step) if n is None else n
    return read(os.path.join(pdir(pid), "decisions", step, f"{n:04d}.json")) if n else None


def add_decision(pid: str, step: str, payload: dict, user: str, note: str = "") -> int:
    if step not in STEPS:
        raise ValueError(step)
    with lock(pid):
        n = version(pid, step) + 1
        rec = dict(payload, version=n, user=user, created=now(), note=note)
        path = os.path.join(pdir(pid), "decisions", step, f"{n:04d}.json")
        if os.path.exists(path):
            raise RuntimeError("decision version exists")
        atomic_write(path, rec)
    return n


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
    return ".".join(f"{vers[s]:04d}" for s in STEPS) + "-" + h(sorted(stage_hashes.items()))[:6]


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
    idx = [dict(pid=p["pid"], title=p["title"], seed=p["seed"], owner=p["owner"], status=p["status"],
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


def projects_of(wid: str) -> List[dict]:
    return (read(os.path.join(config.CACHE_DIR, "hadith_index.json"), {}) or {}).get(wid, [])
