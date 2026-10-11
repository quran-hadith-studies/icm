"""python3 -m narrators <stage> [--db PATH] [stage options]

Stages: noor, rijal, wikishia, thaqalayn, link, facts, sunni, export, eval.
"""
from __future__ import annotations

import argparse
import sys

from .common import DB_PATH


def main(argv=None):
    ap = argparse.ArgumentParser(prog="narrators")
    ap.add_argument("--db", default=DB_PATH)
    sub = ap.add_subparsers(dest="stage", required=True)

    p = sub.add_parser("noor", help="stage 1: Noor shards → ravi/slot/citation/adjacency")
    p.add_argument("--limit", type=int, help="process only the first N documents (smoke test)")

    p = sub.add_parser("rijal", help="stage 2: rijāl texts → entry/page_text")
    p.add_argument("--book", action="append", help="only this book key (repeatable)")

    p = sub.add_parser("fetch", help="stage 2a: fetch an eshia book into external/datasets/shia-rijal-eshia")
    p.add_argument("--book", type=int, required=True, help="lib.eshia.ir book id (14036 = Khoei)")
    p.add_argument("--vols", help="comma-separated volume numbers (default: all)")
    p.add_argument("--delay", type=float, default=0.6)

    p = sub.add_parser("wikishia", help="stage 2b: crawl WikiShia infoboxes (resumable) and load them")
    p.add_argument("--lang", action="append", help="fa | ar | en (repeatable; default all)")
    p.add_argument("--load-only", action="store_true")
    p.add_argument("--delay", type=float, default=1.0)

    p = sub.add_parser("thaqalayn", help="stage 2c: fetch ThaqalaynData narrators (resumable) and load them")
    p.add_argument("--load-only", action="store_true")
    p.add_argument("--delay", type=float, default=0.25)

    p = sub.add_parser("link", help="stage 3: link RaviIds to biographies (Shia)")
    p.add_argument("--steps", default="abcdt", help="subset of a (infallibles) b (cited) c (alphabetical) d (name) t (thaqalayn bridge)")
    p.add_argument("--min-slots", type=int, default=1)

    sub.add_parser("facts", help="stage 4: death years, Imam periods and cities from linked biographies")

    p = sub.add_parser("export", help="stage 5: metadata for the model / full JSON dump")
    p.add_argument("--ravis", help="comma-separated RaviIds")
    p.add_argument("--hadith-ids", help="comma-separated Noor HadithIds (their narrators are exported)")
    p.add_argument("--out", help="meta.json path (load_metadata shape)")
    p.add_argument("--links", help="links.json path (to_family shape)")
    p.add_argument("--dump", help="full narrators.json path")

    p = sub.add_parser("sunni", help="stage 3e: link to hadith-kg / hawramani / muslimscholars / AR-Sanad")
    p.add_argument("--min-slots", type=int, default=20)

    sub.add_parser("eval", help="evaluation protocol (prints the report)")

    args = ap.parse_args(argv)
    if args.stage == "sunni":
        from .link_sunni import run
        run(args.db, min_slots=args.min_slots)
    elif args.stage == "eval":
        from .evaluate import run
        run(args.db)
    elif args.stage == "export":
        from .export_metadata import dump_all, export_family, ravis_for_hadiths
        from .common import open_db
        con = open_db(args.db)
        if args.dump:
            dump_all(con, args.dump)
        if args.out:
            ids = [int(x) for x in args.ravis.split(",")] if args.ravis else []
            if args.hadith_ids:
                ids += ravis_for_hadiths(con, [int(x) for x in args.hadith_ids.split(",")])
            export_family(con, ids, args.out, args.links)
    elif args.stage == "facts":
        from .facts import run
        run(args.db)
    elif args.stage == "link":
        from .link_shia import run
        run(args.db, steps=args.steps, min_slots=args.min_slots)
    elif args.stage == "wikishia":
        from .wikishia_crawl import crawl, load
        if not args.load_only:
            crawl(langs=args.lang, delay=args.delay)
        load(args.db)
    elif args.stage == "thaqalayn":
        from .thaqalayn_fetch import fetch, load
        if not args.load_only:
            fetch(delay=args.delay)
        load(args.db)
    elif args.stage == "noor":
        from .noor_extract import run
        run(args.db, limit=args.limit)
    elif args.stage == "rijal":
        from .rijal_parse import run
        run(args.db, only=args.book)
    elif args.stage == "fetch":
        from .rijal_fetch import fetch_book
        vols = [int(v) for v in args.vols.split(",")] if args.vols else None
        fetch_book(args.book, vols=vols, delay=args.delay)
    else:
        print(f"stage {args.stage} not implemented yet", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
