"""SQLite job queue. The web process enqueues; one worker process claims and runs."""
from __future__ import annotations

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
  status text not null default 'queued',          -- queued | running | done | failed
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


def progress(job_id: int, stage: str, frac: float, msg: str = "") -> None:
    with _db() as c:
        c.execute("update jobs set progress_json=?, heartbeat=? where id=?",
                  (json.dumps(dict(stage=stage, frac=round(frac, 3), msg=msg), ensure_ascii=False), time.time(), job_id))


def finish(job_id: int, error: Optional[str] = None) -> None:
    with _db() as c:
        c.execute("update jobs set status=?, finished=?, error=? where id=?",
                  ("failed" if error else "done", _now(), error, job_id))


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
            return j
    return None


def forget(pid: str) -> int:
    """Drop a project's jobs (it was deleted); a running one is not touched (the caller refuses while one runs)."""
    with _db() as c:
        return c.execute("delete from jobs where pid=? and status!='running'", (pid,)).rowcount
