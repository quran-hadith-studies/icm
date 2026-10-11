"""books_meta.json: per book code, title, author, author death year, tradition.

The served database and hadith-db-v02 keep neither the tradition nor the author's death
year; both come from hadith-hub/books/<code>/meta.json. Noor books appear as
``afreshteNNNNN`` in the hub and as ``angelNNNNN`` in the served database; both keys are
written.

    python3 -m icm.build.books_meta
"""
from __future__ import annotations

import glob
import json
import os

from .. import config


def run(dst: str = config.BOOKS_META) -> None:
    out = {}
    for p in sorted(glob.glob(os.path.join(config.HUB_BOOKS, "*", "meta.json"))):
        m = json.load(open(p, encoding="utf-8"))
        code = m.get("slug") or os.path.basename(os.path.dirname(p))
        trad = (m.get("tradition") or "").lower()
        rec = {
            "title": m.get("title_ar") or m.get("book_name"),
            "author": m.get("author_ar"),
            "death": int(m["author_death_ah"]) if str(m.get("author_death_ah") or "").isdigit() else None,
            "tradition": "sunni" if trad.startswith("sunni") else "shia" if trad.startswith("shia") else None,
            "source": (m.get("source") or {}).get("site") or m.get("source_site"),
            "part": m.get("part_number"),
        }
        out[code] = rec
        if code.startswith("afreshte"):
            out["angel" + code[len("afreshte"):]] = rec
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False)
    n_trad = sum(1 for v in out.values() if v["tradition"])
    print(f"{len(out)} book codes, {n_trad} with a tradition -> {dst}")


if __name__ == "__main__":
    run()
