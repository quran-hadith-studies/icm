"""python3 -m icm helper --server https://ebadollah.ir --token-file ~/.icm_helper_token

Runs on a trusted machine (your laptop). Every ``--interval`` seconds it asks the server whether a
project waits for the helper; if so it downloads the confirmed graph, runs the model with all cores
(one process per candidate source at a time), uploads the result and asks again. It only makes
outbound requests; nothing listens on this machine.

The model is the same code the server and the browser run (``icm.client``)."""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import platform
import sys
import time
import urllib.error
import urllib.request

from . import client


def _http(server: str, path: str, token: str, body=None):
    req = urllib.request.Request(server.rstrip("/") + path, headers={"X-ICM-Helper": token, "Content-Type": "application/json", "X-ICM": "1"},
                                 data=None if body is None else json.dumps(body).encode(), method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def _init(payload: str):
    client.init(payload)


def _one(i: int):
    light = client.source(i)
    return i, light, client.graphs(i)


def compute(job: dict, workers: int, say=print) -> dict:
    payload = json.dumps(dict(graph=job["graph"], texts=job["texts"], preset=job["preset"], seed=job["seed"]))
    t0 = time.time()
    info = json.loads(client.init(payload))                    # in this process: needed for finish()
    n = len(info["sources"])
    say(f"{n} candidate sources, preset {info['preset']}, {workers} processes")
    ctx = mp.get_context("spawn")
    light, graphs = [None] * n, {}
    with ctx.Pool(workers, initializer=_init, initargs=(payload,)) as pool:
        for k, (i, lj, gj) in enumerate(pool.imap_unordered(_one, range(n)), 1):
            light[i] = json.loads(lj)
            graphs[i] = gj
            say(f"  {k}/{n} {info['sources'][i][:40]}  log Z {light[i]['log_Z']}")
    best = max(range(n), key=lambda i: -1e300 if light[i]["log_Z"] is None else light[i]["log_Z"])
    secs = time.time() - t0
    prov = dict(kind="helper", workers=workers, seconds=round(secs), platform=platform.platform()[:60])
    out = json.loads(client.finish(json.dumps(light), graphs[best], secs, json.dumps(prov)))
    return dict(key=job["key"], results=out["results"], viz=out["bundle"], provenance=prov)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="icm helper")
    ap.add_argument("--server", default="https://ebadollah.ir")
    ap.add_argument("--token")
    ap.add_argument("--token-file", default=os.path.expanduser("~/.icm_helper_token"))
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--once", action="store_true", help="handle at most one job, then exit")
    a = ap.parse_args(argv)
    token = a.token or os.environ.get("ICM_HELPER_TOKEN") or (open(a.token_file).read().strip() if os.path.exists(a.token_file) else "")
    if not token:
        print("no token: use --token, ICM_HELPER_TOKEN or --token-file", file=sys.stderr)
        return 2
    print(f"helper polling {a.server} every {a.interval}s with {a.workers} processes", flush=True)
    while True:
        try:
            job = _http(a.server, "/icm/api/helper/claim", token, {})
            if job:
                print(f"project {job['pid']}: computing", flush=True)
                res = compute(job, a.workers, say=lambda m: print(m, flush=True))
                _http(a.server, f"/icm/api/project/{job['pid']}/compute-result", token, res)
                print(f"project {job['pid']}: result uploaded", flush=True)
                if a.once:
                    return 0
                continue
        except urllib.error.HTTPError as e:
            print("server said", e.code, e.read()[:200], flush=True)
        except Exception as e:                                   # network down, server restarting, ...
            print("error:", repr(e), flush=True)
        if a.once:
            return 0
        time.sleep(a.interval)
