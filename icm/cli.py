"""python3 -m icm <command>

  worker                         run the job worker
  run --seed v02:ID [--auto]     create a project and advance it; --auto answers every decision with the
                                 suggested defaults (selection = pre-selected candidates, identities =
                                 defaults, phylogeny confirmed) - for tests and replays only
  replay PID                     advance an existing project once
  status [PID]                   list projects or show one
  sync [--server U@H:PATH]       commit and push the cache; with --server, first copy the server's cache here
  eval                           recall of the search against the diagram witnesses and the consensus sets
  build kg|noor|termdf|books     (re)build the laptop-side data files
"""
from __future__ import annotations

import argparse
import json
import sys

from . import cache, config, pipeline, project, search


def _auto(pid: str, user: str) -> dict:
    p = pipeline.advance(pid)
    while True:
        if p["step"] == "selection":
            S = project.run_stage(pid, "current", "search")
            sel = [S["seed"]["wid"]] + [c["wid"] for c in S["candidates"] if c["suggested"] in search.PRESELECTED]
            project.add_decision(pid, "selection", dict(selected=sel), user, "auto")
        elif p["step"] == "identities":
            project.add_decision(pid, "identities", dict(choices={}), user, "auto")
        elif p["step"] == "phylogeny":
            project.add_decision(pid, "phylogeny", dict(confirmed=p["graph_hash"]), user, "auto")
        else:
            return p
        p = pipeline.advance(pid)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="icm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("worker")
    r = sub.add_parser("run"); r.add_argument("--seed", required=True); r.add_argument("--auto", action="store_true")
    r.add_argument("--user", default="cli")
    rp = sub.add_parser("replay"); rp.add_argument("pid")
    st = sub.add_parser("status"); st.add_argument("pid", nargs="?")
    sy = sub.add_parser("sync"); sy.add_argument("--server", help="user@host:path of the server's cache directory; copied here first")
    sub.add_parser("eval")
    b = sub.add_parser("build"); b.add_argument("what", choices=["kg", "noor", "termdf", "books", "all"])
    a = ap.parse_args(argv)
    if a.cmd == "worker":
        from .worker import main as wmain
        wmain()
    elif a.cmd == "run":
        p = project.create(a.seed, a.user)
        p = _auto(p["pid"], a.user) if a.auto else pipeline.advance(p["pid"])
        print(json.dumps(dict(pid=p["pid"], status=p["status"], step=p["step"], run=p["current_run"])))
    elif a.cmd == "replay":
        p = pipeline.advance(a.pid)
        print(json.dumps(dict(pid=p["pid"], status=p["status"], step=p["step"], run=p["current_run"])))
    elif a.cmd == "status":
        for p in ([project.load(a.pid)] if a.pid else project.list_projects()):
            print(p["pid"], p["status"], p["step"], p["seed"], p["title"])
    elif a.cmd == "sync":
        if a.server:
            print(*cache.pull_from_server(a.server))
            from . import project as _p
            _p.refresh_indexes()
        else:
            print(cache.pull())
        print(*cache.commit_push("icm: sync from server" if a.server else "icm: sync"))
    elif a.cmd == "eval":
        from . import evaluate
        print(json.dumps(evaluate.sitta(), ensure_ascii=False, indent=1))
        print(json.dumps(evaluate.consensus_oracle(), indent=1))
    elif a.cmd == "build":
        from .build import books_meta, kg_extract, noor_relations, termdf
        for name, mod in (("books", books_meta), ("noor", noor_relations), ("kg", kg_extract), ("termdf", termdf)):
            if a.what in (name, "all"):
                mod.run()
    return 0
