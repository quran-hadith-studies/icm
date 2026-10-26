"""python3 -m icm.worker: claims jobs and advances projects. One process, one job at a time."""
from __future__ import annotations

import os
import sys
import time
import traceback

# the SMC sampler iterates Python sets of strings: fix the hash seed so a run is reproducible
if os.environ.get("PYTHONHASHSEED") != "0":
    os.environ["PYTHONHASHSEED"] = "0"
    os.execv(sys.executable, [sys.executable, "-m", "icm.worker", *sys.argv[1:]])

from . import cache, jobs, pipeline, project


def run_job(job) -> None:
    pid = job["pid"]
    cache.pull()

    def cb(stage, frac, msg=""):
        jobs.progress(job["id"], stage, frac, msg)

    try:
        pipeline.advance(pid, progress=cb)
        jobs.finish(job["id"])
    except jobs.Cancelled:
        print("job", job["id"], "cancelled", flush=True)
    except Exception as e:
        traceback.print_exc()
        jobs.finish(job["id"], error=f"{type(e).__name__}: {e}")
    ok, msg = cache.commit_push(f"icm: {pid} {project.load(pid)['step']}")
    if not ok:
        print("cache sync:", msg, flush=True)


def main() -> None:
    print("icm worker started", flush=True)
    n = jobs.requeue_stale()
    if n:
        print(f"requeued {n} stale job(s)", flush=True)
    while True:
        job = jobs.claim()
        if job is None:
            time.sleep(2)
            continue
        print("job", job["id"], job["pid"], flush=True)
        run_job(job)


if __name__ == "__main__":
    main()
