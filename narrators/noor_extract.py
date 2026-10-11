"""Stage 1: one pass over the Noor (Jāmiʿ al-Aḥādīth 4) shards → ravi, citation, slot,
adjacency, hadith, noor_book.

Each hadith's decoded ``sanadInfo`` has two parts:

* ``RaviList`` (nested twice): per narrator, ``InfoList`` (لقب، کنيه، طبقه در ثمانیه،
  وفات، ولادت، اشتهار) and ``InfoList2`` (نتيجه ارزيابي، جمع بندي ارزيابي،
  الفاظ جرح و تعدیل، توثيقات عام، مذهب). Statements cite rijāl books, sometimes with
  «vol/page» appended («رجال النجاشي1/16») — those become ``citation`` rows.
* ``SanadList``: the parsed isnād(s); elements of ``Type`` 0/4 are narrator slots whose
  ``RaviList`` holds one or more candidate ``RaviId`` with a ``Hint`` «name,mazhab,grade».

Direction: Order 0 is the collector (e.g. الكليني) and the last slot the Infallible, so
``pos`` ascends toward the source and the teacher of slot k is slot k+1. That is checked,
not assumed: the share of sanads whose last slot is a معصوم is printed and must exceed 95%.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from .common import (NOOR_SHARDS, as_list, ascii_digits, data_file, insert_many, iter_ndjson_gz,
                     load_json, norm_key, open_db, reset_tables, table_count)

TABLES = ["ravi", "citation", "hadith", "slot", "adjacency", "noor_book"]
_CITE = re.compile(r"^(.*?)\s*(\d+)\s*/\s*(\d+)\s*$")

INFO_FIELDS = {
    "لقب": "laqab", "کنيه": "kunya", "كنيه": "kunya", "طبقه در ثمانیه": "tabaqa",
    "وفات": "wafat", "ولادت": "wiladat", "اشتهار": "ishtihar",
}
INFO2_FIELDS = {
    "نتيجه ارزيابي": "eval_result", "جمع بندي ارزيابي": "eval_summary",
    "الفاظ جرح و تعدیل": "jarh", "الفاظ جرح و تعديل": "jarh", "توثيقات عام": "tawthiq", "مذهب": "mazhab_info",
}


def parse_citation(label: str) -> Optional[Tuple[str, int, int]]:
    """«رجال النجاشي1/16» → ('رجال النجاشي', 1, 16); None when there is no vol/page."""
    m = _CITE.match(ascii_digits(label or ""))
    if not m or not m.group(1).strip():
        return None
    return m.group(1).strip(), int(m.group(2)), int(m.group(3))


def split_hint(hint: str) -> Tuple[str, Optional[str], Optional[str]]:
    parts = [p.strip() for p in (hint or "").split(",")]
    name = parts[0] if parts else ""
    mazhab = parts[1] if len(parts) > 1 and parts[1] else None
    grade = parts[2] if len(parts) > 2 and parts[2] else None
    return name, mazhab, grade


def _book_labels() -> Dict[str, str]:
    books = load_json(data_file("books.json"))["books"]
    out = {}
    for key, b in books.items():
        for lab in b.get("noor_labels", []):
            out[norm_key(lab)] = key
    return out


def _texts(block) -> List[dict]:
    """InfoList block ``Text`` → [{text, books:[{book, ravi_title, url}]}]."""
    out = []
    for t in as_list(block.get("Text")):
        if isinstance(t, dict):
            books = [{"book": b.get("BookName", ""), "ravi_title": b.get("RaviTitle"), "url": b.get("url")}
                     for b in as_list(t.get("BookName")) if isinstance(b, dict)]
            out.append({"text": (t.get("Text") or "").strip(), "books": books})
        elif isinstance(t, str):
            out.append({"text": t.strip(), "books": []})
    return out


class Profile:
    __slots__ = ("fields", "citations", "hints", "titles", "n_slots", "n_slots_unambig", "hadiths")

    def __init__(self):
        self.fields: Dict[str, object] = {}
        self.citations = {}
        self.hints = Counter()
        self.titles = Counter()
        self.n_slots = 0.0
        self.n_slots_unambig = 0
        self.hadiths = set()


def run(db_path: str, limit: Optional[int] = None, shards: str = NOOR_SHARDS) -> None:
    con = open_db(db_path)
    reset_tables(con, TABLES)
    labels = _book_labels()
    profiles: Dict[int, Profile] = defaultdict(Profile)
    adj: Dict[Tuple[int, int], List] = defaultdict(lambda: [0, 0.0, set()])
    books = defaultdict(lambda: [0, 0, 0])
    slot_rows, hadith_rows = [], []
    n_docs = n_sanad_docs = 0
    last_masum = first_masum = n_chains = 0
    ambiguous_examples = []

    def flush_slots():
        nonlocal slot_rows
        insert_many(con, "slot", ["hadith_id", "sanad_idx", "pos", "order_no", "title", "type",
                                  "term", "ravi_id", "cand_json"], slot_rows)
        slot_rows = []

    for doc in iter_ndjson_gz(shards):
        n_docs += 1
        if limit and n_docs > limit:
            break
        src = doc.get("_source") or {}
        book = src.get("BookShortTitle") or ""
        books[book][0] += 1
        si = (doc.get("_decoded") or {}).get("sanadInfo")
        if not si:
            continue
        n_sanad_docs += 1
        books[book][1] += 1
        hid = int(src["HadithId"])

        # --- narrator profiles -------------------------------------------------------
        for grp in as_list(si.get("RaviList")):
            for r in as_list(grp):
                if not isinstance(r, dict) or r.get("RaviId") is None:
                    continue
                p = profiles[int(r["RaviId"])]
                for blk in as_list(r.get("InfoList")):
                    if not isinstance(blk, dict):
                        continue
                    title = (blk.get("Title") or "").strip()
                    key = INFO_FIELDS.get(title, f"extra:{title}")
                    items = _texts(blk)
                    p.fields.setdefault(key, items)
                    for it in items:
                        for b in it["books"]:
                            c = parse_citation(b["book"])
                            if c:
                                p.citations[(b["book"], c[1], c[2], title, it["text"])] = (c, b)
                for blk in as_list(r.get("InfoList2")):
                    if not isinstance(blk, dict):
                        continue
                    title = (blk.get("Title") or "").strip()
                    key = INFO2_FIELDS.get(title, f"extra:{title}")
                    items = _texts(blk)
                    p.fields.setdefault(key, items)
                    for it in items:
                        for b in it["books"]:
                            c = parse_citation(b["book"])
                            if c:
                                p.citations[(b["book"], c[1], c[2], title, it["text"])] = (c, b)

        # --- isnāds ------------------------------------------------------------------
        sanads = as_list(si.get("SanadList"))
        hadith_rows.append((hid, book, src.get("GroupId"), src.get("BookCentury"), len(sanads)))
        for s_idx, s in enumerate(sanads):
            chain: List[List[int]] = []
            pos, term = 0, None
            for el in as_list((s or {}).get("Sanad")):
                if not isinstance(el, dict):
                    continue
                typ = el.get("Type")
                title = (el.get("Title") or "").strip()
                if typ == 1:
                    term = title
                    continue
                if typ not in (0, 4):
                    continue
                cands = []
                for rr in as_list(el.get("RaviList")):
                    if isinstance(rr, dict) and rr.get("RaviId") is not None:
                        rid = int(rr["RaviId"])
                        cands.append(rid)
                        p = profiles[rid]
                        if rr.get("Hint"):
                            p.hints[rr["Hint"]] += 1
                        p.titles[title] += 1
                        p.hadiths.add(hid)
                        p.n_slots += 1.0 / max(1, len(as_list(el.get("RaviList"))))
                if len(cands) == 1:
                    profiles[cands[0]].n_slots_unambig += 1
                elif len(cands) > 1 and len(ambiguous_examples) < 20:
                    ambiguous_examples.append((hid, title, cands))
                slot_rows.append((hid, s_idx, pos, el.get("Order"), title, typ, term,
                                  cands[0] if len(cands) == 1 else None, json.dumps(cands)))
                chain.append(cands)
                books[book][2] += 1
                pos, term = pos + 1, None
            if len(chain) >= 2:
                n_chains += 1
                # direction check: is the Infallible at the end or the start?
                def is_masum(cs):
                    return any(",معصوم" in h for c in cs for h in profiles[c].hints)
                if is_masum(chain[-1]):
                    last_masum += 1
                if is_masum(chain[0]):
                    first_masum += 1
                for k in range(len(chain) - 1):
                    stu, tea = chain[k], chain[k + 1]
                    if not stu or not tea:
                        continue
                    if len(stu) == 1 and len(tea) == 1:
                        e = adj[(tea[0], stu[0])]
                        e[0] += 1
                        e[2].add(hid)
                    else:
                        w = 1.0 / (len(stu) * len(tea))
                        for t in tea:
                            for u in stu:
                                adj[(t, u)][1] += w
            if len(slot_rows) >= 50000:
                flush_slots()
        if n_sanad_docs % 20000 == 0:
            print(f"  {n_docs} docs, {n_sanad_docs} with sanad, {len(profiles)} ravi", file=sys.stderr)
    flush_slots()

    # --- write ------------------------------------------------------------------------
    insert_many(con, "hadith", ["hadith_id", "book", "group_id", "century", "n_sanads"], hadith_rows)
    insert_many(con, "noor_book", ["book", "n_hadith", "n_with_sanad", "n_slots"],
                [(b, v[0], v[1], v[2]) for b, v in books.items()])

    def first_text(items):
        return items[0]["text"] if items else None

    ravi_rows, cite_rows = [], []
    for rid, p in profiles.items():
        hint = p.hints.most_common(1)[0][0] if p.hints else ""
        name, mazhab, grade = split_hint(hint)
        if not name:
            name = first_text(p.fields.get("ishtihar")) or (p.titles.most_common(1)[0][0] if p.titles else "")
        f = p.fields
        extra = {k[6:]: v for k, v in f.items() if k.startswith("extra:")}
        ravi_rows.append((
            rid, name, norm_key(name), mazhab, grade,
            first_text(f.get("kunya")), json.dumps(f.get("laqab"), ensure_ascii=False) if f.get("laqab") else None,
            first_text(f.get("ishtihar")),
            json.dumps(f.get("tabaqa"), ensure_ascii=False) if f.get("tabaqa") else None,
            json.dumps(f.get("wafat"), ensure_ascii=False) if f.get("wafat") else None,
            json.dumps(f.get("wiladat"), ensure_ascii=False) if f.get("wiladat") else None,
            first_text(f.get("eval_result")), first_text(f.get("eval_summary")),
            json.dumps(f.get("jarh"), ensure_ascii=False) if f.get("jarh") else None,
            first_text(f.get("tawthiq")), first_text(f.get("mazhab_info")),
            json.dumps(extra, ensure_ascii=False) if extra else None,
            round(p.n_slots), p.n_slots_unambig, len(p.hadiths), sum(p.hints.values()),
        ))
        for (raw, vol, page, ctx, text), (c, b) in p.citations.items():
            cite_rows.append((rid, c[0], labels.get(norm_key(c[0])), vol, page, ctx, text,
                              b.get("ravi_title"), b.get("url")))
    insert_many(con, "ravi", ["ravi_id", "name", "name_key", "mazhab", "grade", "kunya", "laqab_json",
                              "ishtihar", "tabaqa_json", "wafat_json", "wiladat_json", "eval_result",
                              "eval_summary", "jarh_json", "tawthiq", "mazhab_info", "extra_json",
                              "n_slots", "n_slots_unambig", "n_hadith", "n_hints"], ravi_rows)
    insert_many(con, "citation", ["ravi_id", "book_raw", "book", "vol", "page", "context", "text",
                                  "ravi_title", "url"], cite_rows)
    insert_many(con, "adjacency", ["teacher", "student", "n_exact", "w_amb", "n_hadith"],
                [(t, s, v[0], round(v[1], 4), len(v[2])) for (t, s), v in adj.items()])
    con.commit()

    # --- report -----------------------------------------------------------------------
    print(f"docs {n_docs}, with sanadInfo {n_sanad_docs}")
    print(f"ravi {table_count(con, 'ravi')}, slots {table_count(con, 'slot')}, "
          f"citations {table_count(con, 'citation')} on "
          f"{con.execute('select count(distinct ravi_id) from citation').fetchone()[0]} ravi, "
          f"adjacency pairs {table_count(con, 'adjacency')}")
    print(f"direction: chains {n_chains}, Infallible last {last_masum} ({last_masum / max(1, n_chains):.1%}), "
          f"Infallible first {first_masum} ({first_masum / max(1, n_chains):.1%})")
    if last_masum < 0.95 * n_chains:
        print("WARNING: direction assumption (teacher = pos+1) not confirmed", file=sys.stderr)
    tot = con.execute("select sum(n_slots) from ravi").fetchone()[0]
    top = con.execute("select sum(n_slots) from (select n_slots from ravi order by n_slots desc limit 1000)").fetchone()[0]
    print(f"top-1000 ravi cover {top / tot:.1%} of slots")
    unknown = con.execute("select book_raw, count(*) from citation where book is null group by 1 order by 2 desc").fetchall()
    if unknown:
        print("citation labels without a book key:", [(r[0], r[1]) for r in unknown[:10]])
    print("ambiguous slots (sample):")
    for hid, title, cands in ambiguous_examples[:20]:
        names = [profiles[c].hints.most_common(1)[0][0].split(",")[0] if profiles[c].hints else "?" for c in cands]
        print(f"  h{hid} «{title}» -> {list(zip(cands, names))}")


if __name__ == "__main__":
    from .common import DB_PATH
    run(DB_PATH, limit=int(sys.argv[1]) if len(sys.argv) > 1 else None)
