"""Give ICM projects the usernames the central users service gave their people.

    python3 scripts/rename_owners_to_central.py --mapping m.json [--guest-to <username>]            # dry run
    python3 scripts/rename_owners_to_central.py --mapping m.json [--guest-to <username>] --apply

Rewrites project.json (`owner`, each version's `user`) and every decision's `user`. `m.json` is the mapping
`python -m ebad_users.importer users` writes: `<source>:<old username>` entries with the new `username`. Work done as `guest`
(open mode) belongs to no account: with --guest-to it goes to that person, and project.json keeps `migrated_from: guest`."""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from icm import project  # noqa: E402


def renames_from(mapping: dict, guest_to: str | None) -> dict[str, str]:
    out = {k.split(":", 1)[1]: v["username"] for k, v in mapping.items() if ":" in k and v.get("username")}
    if guest_to:
        out["guest"] = guest_to
    return {old: new for old, new in out.items() if old != new}


def rewrite_project(pid: str, renames: dict[str, str], apply: bool) -> int:
    changed = 0
    p = project.load(pid)
    if p:
        before = json.dumps(p, sort_keys=True)
        if p.get("owner") in renames:
            if p["owner"] == "guest":
                p["migrated_from"] = "guest"
            p["owner"] = renames[p["owner"]]
        for b in p.get("branches") or []:
            if b.get("user") in renames:
                b["user"] = renames[b["user"]]
        if json.dumps(p, sort_keys=True) != before:
            changed += 1
            if apply:
                project.save(p)
    for path in glob.glob(os.path.join(project.pdir(pid), "decisions", "*", "*.json")):
        rec = project.read(path, None)
        if isinstance(rec, dict) and rec.get("user") in renames:
            changed += 1
            if apply:
                rec["user"] = renames[rec["user"]]
                project.atomic_write(path, rec)
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapping", required=True)
    ap.add_argument("--guest-to")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    with open(args.mapping, encoding="utf-8") as f:
        renames = renames_from(json.load(f), args.guest_to)
    total = 0
    for pid in sorted(os.listdir(project.root())) if os.path.isdir(project.root()) else []:
        n = rewrite_project(pid, renames, args.apply)
        if n:
            print(f"{pid}: {n} file(s)")
        total += n
    print(("rewrote" if args.apply else "dry run, would rewrite") + f" {total} file(s) for {len(renames)} name(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
