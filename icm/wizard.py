"""The eleven steps of the wizard and where a project stands in them.

    staleness(pid) -> dict(current, stale_any, steps=[{n, slug, stage, state, changed_since, run, ...}])
    touch(pid)     -> writes the project's ``step`` (the slug of the first step that is not done) and ``stale``

Nothing here runs anything. A step's state is read from three things: the decisions now against the decisions the current
run was built from (what changed, and on which step), the stage keys the decisions give now (``pipeline.plan``) against
the stage files the current run is made of, and the job that is running.

States: ``done`` · ``idle`` (optional, not used) · ``todo`` (its run is current; the user's decision on it is missing) · ``needs_run`` (saved or never run: this page's button makes it current) ·
``stale`` (a step before it, or another page of its stage, changed since: its run is out of date) · ``running`` ·
``awaiting`` (the analysis waits for a computation outside the server) · ``unreached`` (what it needs is not there yet).
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from . import config, narrator_versions, pipeline, project

WIZARD = ("hadiths", "parts", "candidates", "semantic", "added", "slots", "alignment", "units", "narrators", "graph", "run")
STAGE_OF = ("search", "search", "search", "search", "search", "align", "align", "align", "identify", "graph", "infer")
TITLES = ("حدیث‌ها", "حدیث‌ها و بخش‌هایشان", "نامزدها", "هم‌معنا", "حدیث‌های افزوده", "فرازبندی بخش‌ها در شاهدها", "هم‌ترازی", "واحدهای تغییر", "راویان", "شجره", "اجرا")
# the run button of a step: (what, label, heavy: asks before it starts); step 1 has none (its changes are run by step 2)
RUN = {2: ("rescore", "سنجش بخش‌ها و نامزدها", False), 3: ("search", "جستجو", False), 4: ("semantic", "جستجوی معنایی", False), 5: ("rescore", "سنجش حدیث‌های افزوده", False),
       6: ("align", "ساخت هم‌ترازی", True), 7: ("align", "ساخت دوباره‌ی هم‌ترازی", True), 8: ("align", "ساخت دوباره‌ی هم‌ترازی", True),
       9: ("identify", "یافتن راویان", True), 10: ("graph", "ساخت شجره", False), 11: ("infer", "اجرای تحلیل", True)}

META = {"version", "user", "created", "note", "prev"}
# (decision step, field) -> (the wizard step it is the data of, the step whose button makes it count)
FIELDS = {
    ("search", "anchor"): (1, 2), ("search", "seeds"): (1, 2), ("search", "edits"): (1, 2), ("search", "part_query"): (2, 2),
    ("search", "ret"): (3, 3), ("search", "ret_sem"): (4, 4), ("search", "extra_ids"): (5, 5),
    ("parts", "cuts"): (2, 2), ("parts", "cuts_confirmed"): (2, 2), ("parts", "seed"): (2, 2), ("parts", "seed_confirmed"): (2, 2),
    ("parts", "join"): (2, 2), ("parts", "keep_ids"): (2, 2),
    ("parts", "extras_added"): (6, 6), ("parts", "extras_dropped"): (6, 6), ("parts", "slots"): (6, 6), ("parts", "confirmed"): (6, 6), ("parts", "refs"): (6, 6),
    ("parts", "words"): (7, 7), ("parts", "unit_cls"): (7, 7), ("parts", "units"): (8, 8), ("parts", "renames"): (9, 9), ("parts", "chain"): (9, 9),
    ("selection", "selected"): (3, 6), ("selection", "reset"): (3, 6),
    ("narrators", "name"): (9, 9),
    ("phylogeny", "dates"): (10, 10), ("phylogeny", "chain_edits"): (10, 10), ("phylogeny", "deleted"): (10, 10),
    ("phylogeny", "preset"): (11, 11), ("phylogeny", "compute"): (11, 11),
    ("phylogeny", "build"): None, ("phylogeny", "confirmed"): None,           # the run buttons of steps 10 and 11 write these: they are the run itself
}
DEFAULT = {"search": (1, 2), "parts": (6, 6), "selection": (3, 6), "identities": (9, 9), "narrators": (9, 9), "phylogeny": (10, 10)}

_PLANS: Dict[tuple, dict] = {}


def slug(n: int) -> str:
    return WIZARD[n - 1]


def _plan(pid: str, p: dict, vers: dict, D: dict) -> dict:
    """``pipeline.plan`` remembered for these decisions and this run (the page asks every few seconds)."""
    key = (pid, p.get("current_run"), tuple(sorted(vers.items())), narrator_versions.default_name(), config.DATA_VERSION)
    got = _PLANS.get(key)
    if got is None:
        if len(_PLANS) > 64:
            _PLANS.clear()
        got = _PLANS[key] = pipeline.plan(pid, D)
    return got


def changed(pid: str, vers: dict, D: dict, mv: dict) -> List[tuple]:
    """The edits made since the current run was built: ``[(n, runner)]`` for every field whose value differs between the
    decision the run used and the decision now."""
    out = set()
    for s in project.STEPS:
        if s == "go" or vers.get(s, 0) == (mv or {}).get(s, 0):
            continue
        old = project.decision(pid, s, mv.get(s)) if mv.get(s) else None
        new = D.get(s)
        old, new = {k: v for k, v in (old or {}).items() if k not in META}, {k: v for k, v in (new or {}).items() if k not in META}
        for k in set(old) | set(new):
            if old.get(k) != new.get(k):
                hit = FIELDS.get((s, k), DEFAULT.get(s))
                if hit:
                    out.add(hit)
    return sorted(out)


def staleness(pid: str, job: Optional[dict] = None) -> dict:
    p = project.load(pid) or {}
    vers, D = pipeline.snapshot(pid)
    pl = _plan(pid, p, vers, D)
    run = p.get("current_run")
    m = project.read(os.path.join(project.pdir(pid), "runs", run, "manifest.json")) if run else None
    mst, mv = (m or {}).get("stages") or {}, (m or {}).get("versions") or {}
    keys, cached, gate = pl["keys"], set(pl["cached"]), pl["gate"]
    sdec = D["search"] or {}
    origins = changed(pid, vers, D, mv) if m else []
    want = pipeline.want_of(job["kind"]) if job else False                  # False: nothing runs
    ready = {1: True, 2: True, 3: True, 4: bool(sdec.get("ret")), 5: True,
             6: "search" in cached and gate != "selection", 7: "search" in cached and gate != "selection", 8: "search" in cached and gate != "selection",
             9: "align" in cached, 10: "graph" in keys, 11: "graph" in cached}
    steps = []
    for n in range(1, 12):
        s = STAGE_OF[n - 1]
        key = keys.get(s)
        fresh = bool(m) and key is not None and mst.get(s) == key
        same = {o for o, r in origins if o < n or STAGE_OF[o - 1] == s}
        since = sorted(same - {n})
        mine = any(r == n for o, r in origins) and not fresh                    # a change this page's button makes count (a saved change that moves no key changes nothing)
        if n == 1:
            state = "needs_run" if any(o == 1 for o, r in origins) and not fresh else "done"
        elif not m or run is None:
            state = "needs_run" if ready[n] and n in (2, 3, 5) else ("idle" if n == 4 else "unreached")
            if not ready[n]:
                state = "unreached"
        elif n == 4 and not sdec.get("ret_sem") and not (sdec.get("ret") or {}).get("sem"):
            state = "idle"
        elif not ready[n]:
            state = "stale" if s in mst else "unreached"                          # what it had is out of date, and what it needs is not there yet
        elif gate == "awaiting" and n == 11:
            state = "awaiting"
        elif mine:
            state = "needs_run"
        elif n == 3 and not sdec.get("ret") and p.get("manual_search"):
            state = "needs_run"                                                   # «جستجو» was never pressed
        elif not fresh:
            state = "needs_run" if s not in mst else "stale"
        elif n == 4 and pl.get("sem_stale"):
            state = "stale"
            since = sorted(set(since) | {2})
        elif (n == 3 and gate == "selection") or (n == 9 and gate == "identities"):
            state = "todo"                                                         # run and current: the user's own decision on it is still missing
        else:
            state = "done"
        if state == "needs_run" and n == 4 and not sdec.get("ret_sem"):
            state = "idle"
        if want is not False and state not in ("done", "idle", "unreached") and (want is None or s in want):
            state = "running"
        steps.append(dict(n=n, slug=slug(n), title=TITLES[n - 1], stage=s, state=state, key=key, edited=bool(mine), in_run=bool(m) and mst.get(s) == key and key is not None, ready=bool(ready[n]),
                          changed_since=[dict(n=o, slug=slug(o), title=TITLES[o - 1]) for o in since] if state in ("stale", "needs_run") else [],
                          run=(dict(zip(("what", "label", "heavy"), RUN[n])) if n in RUN else None)))
    cur = next((x["n"] for x in steps if x["state"] not in ("done", "idle")), None)
    return dict(current=slug(cur) if cur else "done", current_n=cur, stale_any=any(x["state"] == "stale" or (x["state"] == "needs_run" and x["edited"]) for x in steps), steps=steps,
                gate=gate, sem_stale=bool(pl.get("sem_stale")))


def touch(pid: str) -> Optional[dict]:
    """Bring the project's ``step`` and ``stale`` up to date (after a save, a run, a version change)."""
    try:
        w = staleness(pid)
    except Exception:                                                              # never fail a save because the marks could not be read
        return None
    with project.lock(pid):
        p = project.load(pid)
        if p is None:
            return None
        p.update(step=w["current"], stale=w["stale_any"])
        project.save(p)
    project.refresh_indexes()
    return p
