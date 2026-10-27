"""SQLite job queue. The web process enqueues; one worker process claims and runs."""
from __future__ import annotations

import calendar
import contextlib
import json
import os
import sqlite3
import time
from typing import Iterator, Optional

from . import config

SCHEMA = """
create table if not exists jobs (
  id integer primary key autoincrement, pid text not null, kind text not null default 'advance',
  status text not null default 'queued',          -- queued | running | done | failed | cancelled
  created text, started text, finished text, heartbeat real, progress_json text, error text, os_pid integer);
create index if not exists ix_jobs_status on jobs(status, id);
create index if not exists ix_jobs_pid on jobs(pid, id);
"""


def conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.JOBS_DB), exist_ok=True)
    c = sqlite3.connect(config.JOBS_DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


@contextlib.contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """A connection that commits (or rolls back) and is then CLOSED. `with sqlite3.Connection` alone never closes,
    and the worker polls every 2 s: leaked handles used up the 1024-fd limit and killed it every ~45 min."""
    c = conn()
    try:
        with c:
            yield c
    finally:
        c.close()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def enqueue(pid: str, kind: str = "advance") -> int:
    with _db() as c:
        row = c.execute("select id from jobs where pid=? and kind=? and status='queued'", (pid, kind)).fetchone()
        if row:
            return row["id"]
        return c.execute("insert into jobs(pid,kind,created) values (?,?,?)", (pid, kind, _now())).lastrowid


def claim() -> Optional[sqlite3.Row]:
    c = conn()
    try:
        c.execute("begin immediate")
        row = c.execute("select * from jobs where status='queued' order by id limit 1").fetchone()
        if row is None:
            c.execute("rollback")
            return None
        c.execute("update jobs set status='running', started=?, heartbeat=?, os_pid=? where id=?",
                  (_now(), time.time(), os.getpid(), row["id"]))
        c.execute("commit")
        return row
    finally:
        c.close()


class Cancelled(Exception):
    """The owner cancelled the job; raised inside the worker at its next progress report."""


def progress(job_id: int, stage: str, frac: float, msg: str = "") -> None:
    with _db() as c:
        if c.execute("select status from jobs where id=?", (job_id,)).fetchone()["status"] == "cancelled":
            raise Cancelled()
        c.execute("update jobs set progress_json=?, heartbeat=? where id=?",
                  (json.dumps(dict(stage=stage, frac=round(frac, 3), msg=msg), ensure_ascii=False), time.time(), job_id))


def finish(job_id: int, error: Optional[str] = None) -> None:
    with _db() as c:          # a cancelled job stays cancelled, whatever the worker reports afterwards
        c.execute("update jobs set status=?, finished=?, error=? where id=? and status!='cancelled'",
                  ("failed" if error else "done", _now(), error, job_id))


def cancel(pid: str) -> list:
    """Stop the project's queued and running jobs: a queued one never starts, a running one stops at its next progress
    report (or, when its worker is gone, simply stays cancelled). Returns the kinds of the jobs cancelled."""
    with _db() as c:
        kinds = [r["kind"] for r in c.execute("select kind from jobs where pid=? and status in ('queued','running')", (pid,))]
        c.execute("update jobs set status='cancelled', finished=? where pid=? and status in ('queued','running')", (_now(), pid))
    return kinds


def requeue_stale(max_age: float = 900.0) -> int:
    """Running jobs whose worker died (no heartbeat) go back to the queue; stages are idempotent."""
    with _db() as c:
        return c.execute("update jobs set status='queued' where status='running' and heartbeat < ?", (time.time() - max_age,)).rowcount


def for_project(pid: str, limit: int = 5):
    with _db() as c:
        rows = c.execute("select * from jobs where pid=? order by id desc limit ?", (pid, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["progress"] = json.loads(d.pop("progress_json")) if d.get("progress_json") else None
        out.append(d)
    return out


def active(pid: str) -> Optional[dict]:
    for j in for_project(pid, 3):
        if j["status"] in ("queued", "running"):
            # seconds without a sign of life: since the last progress report of a running job, since the request for a queued one
            t = j.get("heartbeat") if j["status"] == "running" else None
            if t is None and j.get("created"):
                t = calendar.timegm(time.strptime(j["created"], "%Y-%m-%dT%H:%M:%SZ"))
            j["idle"] = max(0, int(time.time() - t)) if t else None
            return j
    return None


def active_all() -> dict:
    """pid -> {kind, status} of every project with a queued or running job (one query: the project list's red dots)."""
    with _db() as c:
        rows = c.execute("select pid, kind, status from jobs where status in ('queued','running') order by id").fetchall()
    out: dict = {}
    for r in rows:
        if r["pid"] not in out or r["status"] == "running":
            out[r["pid"]] = dict(kind=r["kind"], status=r["status"])
    return out


def forget(pid: str) -> int:
    """Drop a project's jobs (it was deleted); a running one is not touched (the caller refuses while one runs)."""
    with _db() as c:
        return c.execute("delete from jobs where pid=? and status!='running'", (pid,)).rowcount
