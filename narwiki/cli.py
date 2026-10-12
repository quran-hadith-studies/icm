"""python3 -m narwiki <command>

  seed [--db PATH] [--force]            build the wiki database from narrators.sqlite + kg.sqlite (laptop)
  similar [--db PATH] [--only K,K]      add merge suggestions: name_chrono (Noor-KG), noor_dup, kg_dup
  stats [--db PATH]                     counts of persons, ids, revisions, suggestions
  export-revisions [--db PATH] [-o F]   the revisions made after seeding (+ decided suggestions) as JSON
  import-revisions F [--db PATH]        replay such a dump onto this (re-seeded) database
  serve [--port 8098]                   the development server (uvicorn narwiki.devserver:app)

The database is NARWIKI_DB (default <hadith-project>/narwiki-data/wiki.sqlite).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m narwiki", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed"); s.add_argument("--db"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("similar"); s.add_argument("--db"); s.add_argument("--only", default="name_chrono,noor_dup,kg_dup")
    s = sub.add_parser("stats"); s.add_argument("--db")
    s = sub.add_parser("export-revisions"); s.add_argument("--db"); s.add_argument("-o", "--out")
    s = sub.add_parser("import-revisions"); s.add_argument("file"); s.add_argument("--db")
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8098); s.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)
    db = getattr(a, "db", None) or config.DB
    if a.cmd == "seed":
        from . import seed
        print(json.dumps(seed.run(db, force=a.force), ensure_ascii=False, indent=1))
    elif a.cmd == "similar":
        from . import similar
        only = [k for k in a.only.split(",") if k]
        bad = set(only) - set(similar.ALL)
        if bad:
            ap.error(f"unknown kinds {bad}")
        print(json.dumps(similar.run(db, only), ensure_ascii=False, indent=1))
    elif a.cmd == "stats":
        from . import store
        c = store.connect(db, readonly=True)
        out = store.stats(c)
        out["db"] = db
        out["db_mb"] = round(os.path.getsize(db) / 1e6, 1)
        print(json.dumps(out, ensure_ascii=False, indent=1))
    elif a.cmd == "export-revisions":
        from . import store
        data = store.export_revisions(store.connect(db, readonly=True))
        txt = json.dumps(data, ensure_ascii=False, indent=1)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(txt)
            print(f"{len(data['revisions'])} revisions, {len(data['suggestions'])} decided suggestions -> {a.out}", file=sys.stderr)
        else:
            print(txt)
    elif a.cmd == "import-revisions":
        from . import store
        data = json.load(open(a.file, encoding="utf-8"))
        print(json.dumps(store.import_revisions(store.connect(db), data), ensure_ascii=False, indent=1))
    elif a.cmd == "serve":
        import uvicorn
        os.environ.setdefault("NARWIKI_DB", db)
        uvicorn.run("narwiki.devserver:app", host=a.host, port=a.port)
    return 0
