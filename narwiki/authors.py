"""Book authors that no narrator database holds, as persons of the narrator wiki (a new source: the book catalogue).

The catalogue (``books_meta.json``: every book's author and his death year) names ~1,100 authors. ``icm/authors.json`` links
those that Noor or the knowledge graph know (name and death year agree with one person). The rest wrote after the databases end
(Fayd Kashani, d. 1091) or are not in them; each of those becomes a person here, with the information the catalogue has:

    name     the spelling used by most books (``family، given`` turned into ``given family``)
    alias    the other spellings of the same author (spellings with the same death year and one name inside the other are one author)
    death    the hijri year of the books' author
    note     how many books, and the first titles
    ext      ``author:<key>`` for every spelling (``icm.authors.key_of``), ``openiti:<id>`` when OpenITI knows him

The source of every field is ``books`` (not ``noor`` / ``kg``: OpenITI gives names and years only, not enough to be the source).
``python3 -m narwiki authors`` shows the plan, ``--yes`` writes it (idempotent: a spelling that is already an ``author`` ext is skipped).
It also writes ``author_wiki.json`` next to ``authors.json``: {spelling key: {ids: [wiki:<token>]}}, which ICM reads as the link of an
author the databases do not know.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from typing import Dict, List

from . import config, store

SOURCE = "books"
USER = "books-catalogue"
SKIP = re.compile(r"\(\s*ع\s*\)|\(\s*عج\s*\)|الإمام|الامام|امام |المنسوب|مؤسس|منسوب|\(\s*ص\s*\)")
COMMON = {"محمد", "احمد", "علي", "حسن", "حسين", "عبد", "الله", "ابن", "ابو", "ابي", "بن", "يحيي", "يوسف", "ابراهيم", "جعفر", "موسي", "عمر", "عثمان", "سليمان", "داود", "اسماعيل", "ابا"}


def _title(t: str) -> str:
    return re.sub(r"\s*\(.*?\)\s*", " ", t or "").strip()


PAREN = re.compile(r"[(（]([^)）]*)[)）]")
NOTE = re.compile(r"^\s*(تحقيق|مترجم|ترجمه|ترجمة|شرح|جمع|اعداد|إعداد|ع|عج|ص)\b")


def display(author: str) -> str:
    """«family، given» is turned into «given family»; a note in brackets («مترجم: …», «تحقيق …») is left out."""
    a = PAREN.sub(lambda m: " " if NOTE.match(m.group(1)) or len(m.group(1).split()) < 2 else m.group(0), (author or "").strip())
    a = re.sub(r"\s+", " ", a).strip()
    if "،" in a and "(" not in a.split("،")[0]:
        a = " ".join(reversed([x.strip() for x in a.split("،")]))
    return re.sub(r"\s+", " ", a)


def _existing(clusters, window: int = 3):
    """{first key of a cluster: [person ids]} the narrator databases may already hold: every word of one spelling is in a person's
    name (two words at least; a lone word needs a death year within ``window`` of the book's), and the person has no death year of
    his own or one within ``window``. Such an author is not made again: he is for a person to link."""
    from icm.build import authors as ba
    from icm.authors import toks
    found = {}
    people = {"noor": ba._noor(True), "kg": ba._kg(True)}
    inv = {k: defaultdict(list) for k in people}
    for k, ps in people.items():
        for p in ps:
            for v in p.variants:
                for t in set(v):
                    inv[k][t].append((p, set(v)))
    df = {k: defaultdict(int) for k in people}
    for k in people:
        for t, l in inv[k].items():
            df[k][t] = len(l)
    for cl in clusters:
        hits = set()
        for key in cl["keys"]:
            for form in ba.forms(cl["spellings"][key]):
                ts = set(form)
                if not ts:
                    continue
                for db in people:
                    rare = min(ts, key=lambda t: df[db].get(t, 0) or 10 ** 9)
                    for p, vs in inv[db].get(rare, ()):
                        if ts <= vs and (len(ts) >= 2 or (p.deaths and min(abs(d - cl["death"]) for d in p.deaths) <= window)):
                            if not p.deaths or min(abs(d - cl["death"]) for d in p.deaths) <= window:
                                hits.add(p.id)
        if hits:
            found[cl["keys"][0]] = sorted(hits)[:5]
    return found


def plan(authors_path: str, books_path: str, with_databases: bool = True) -> dict:
    from icm.authors import key_of, toks
    table = json.load(open(authors_path, encoding="utf-8"))["authors"]
    books = json.load(open(books_path, encoding="utf-8"))
    titles: Dict[str, list] = defaultdict(list)
    for v in books.values():
        if v.get("author") and v.get("death"):
            titles[key_of(v["author"], v["death"])].append(_title(v.get("title")))
    skipped, cand = [], {}
    for key, v in table.items():
        if v["ids"] or v.get("by_name"):
            continue                                       # a person the databases hold (or a suggestion for one)
        if not (0 < (v["death"] or 0) < 3000) or SKIP.search(v["author"]):
            skipped.append(v)
            continue
        cand[key] = v
    # spellings of one author: same death year, one name inside the other (two words at least, one of them not a common first name)
    parent = {k: k for k in cand}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    by_death = defaultdict(list)
    for k, v in cand.items():
        by_death[v["death"]].append(k)
    for ks in by_death.values():
        sets = {k: set(toks(display(cand[k]["author"]))) for k in ks}
        for a in ks:
            for b in ks:
                if a < b:
                    sa, sb = sets[a], sets[b]
                    s, t = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
                    shared_known = set(cand[a]["known"]) & set(cand[b]["known"])
                    if shared_known or (s and s <= t and any(x not in COMMON and len(x) >= (4 if len(s) >= 2 else 5) for x in s)):
                        parent[find(a)] = find(b)
    groups: Dict[str, list] = defaultdict(list)
    for k in cand:
        groups[find(k)].append(k)
    persons = []
    for ks in groups.values():
        ks.sort(key=lambda k: (-cand[k]["n_books"], k))
        main = cand[ks[0]]
        seen = {" ".join(sorted(toks(main["author"])))}
        alias = []
        for k in ks[1:]:
            sig = " ".join(sorted(toks(cand[k]["author"])))
            if sig not in seen:
                seen.add(sig)
                alias.append(display(cand[k]["author"]))
        all_titles = [t for k in ks for t in titles.get(k, [])]
        uniq = list(dict.fromkeys(t for t in all_titles if t))
        n = sum(cand[k]["n_books"] for k in ks)
        note = f"گردآورنده‌ی {n} کتاب از کتابخانه‌ی ایبادالله (فهرست کتاب‌ها، نه نور یا گراف دانش): " + "؛ ".join(uniq[:8]) + (" …" if len(uniq) > 8 else "")
        known = sorted({x for k in ks for x in cand[k]["known"] if x.startswith("openiti:")})
        persons.append(dict(name=display(main["author"]), alias=alias, death=main["death"], n_books=n, note=note, keys=ks, openiti=known,
                            spellings={k: cand[k]["author"] for k in ks}))
    maybe = _existing(persons) if with_databases else {}
    kept = [p for p in persons if p["keys"][0] not in maybe]
    for p in persons:
        if p["keys"][0] in maybe:
            p["maybe"] = maybe[p["keys"][0]]
    kept.sort(key=lambda p: -p["n_books"])
    return dict(persons=kept, skipped=skipped, maybe_existing=[p for p in persons if p["keys"][0] in maybe])


def apply(c, persons: List[dict], user: str = USER) -> dict:
    """Write the persons; returns {spelling key: {"ids": ["wiki:<token>"], "by": "catalogue", "person_id": n}} (also for those already there)."""
    out, made = {}, 0
    for p in persons:
        have = [store.by_ext(c, "author", k) for k in p["keys"]]
        have = [h for h in have if h]
        if have:
            pid = have[0]
            tok = next((e for kind, e in store.ext_of(c, pid) if kind == "wiki"), None)
            for k in p["keys"]:
                out[k] = dict(ids=[f"wiki:{tok}"] if tok else [], by="catalogue", person_id=pid)
            continue
        with c:
            pid = store.create_person(c, p["name"][:300])
            tok = store._wiki_token()
            exts = [("wiki", tok)] + [("author", k) for k in p["keys"]] + [("openiti", o.split(":", 1)[1]) for o in p["openiti"]
                                                                               if store.by_ext(c, "openiti", o.split(":", 1)[1]) is None]
            for kind, e in exts:
                c.execute("insert into person_ext(kind, ext_id, person_id) values (?,?,?)", (kind, e, pid))
            rev = store._rev(c, pid, "add", None, None, None, user, "از فهرست کتاب‌ها: گردآورنده‌ای که نور و گراف دانش ندارند")
            fields = [("name", p["name"])] + [("alias", a) for a in p["alias"]] + [("death_lo", str(p["death"])), ("death_hi", str(p["death"])), ("note", p["note"])]
            for f, v in fields:
                store._add_field(c, pid, f, v, SOURCE, rev)
            op = dict(op="add", display_name=p["name"], fields=[[f, v] for f, v in fields], ext=[[k, e] for k, e in exts], source=SOURCE)
            c.execute("update revision set after_json=?, op_json=? where id=?",
                      (json.dumps(dict(persons=[store.snapshot(c, pid)]), ensure_ascii=False), json.dumps(op, ensure_ascii=False), rev))
            store.refresh_fts(c, pid)
        made += 1
        for k in p["keys"]:
            out[k] = dict(ids=[f"wiki:{tok}"], by="catalogue", person_id=pid)
    out["_made"] = made
    return out


def main(argv=None) -> int:
    import argparse
    from icm import config as icm_config
    ap = argparse.ArgumentParser(prog="narwiki authors")
    ap.add_argument("--yes", action="store_true", help="write the persons (default: only show the plan)")
    ap.add_argument("--authors", default=os.path.join(icm_config.DATA_DIR, "authors.json"))
    ap.add_argument("--books", default=icm_config.BOOKS_META)
    ap.add_argument("--out", default=None, help="author_wiki.json (default: next to --authors)")
    a = ap.parse_args(argv)
    pl = plan(a.authors, a.books)
    ps = pl["persons"]
    print(f"{len(pl['maybe_existing'])} authors left out: the databases may hold them (a person with the same words and no death year, or within 3 years)")
    print(f"{len(ps)} persons for {sum(len(p['keys']) for p in ps)} spellings, {sum(p['n_books'] for p in ps)} books; {len(pl['skipped'])} authors skipped (imams, institutions, no year)")
    for p in ps[:15]:
        print(f"  {p['n_books']:4d} ف {p['death']}  {p['name']}   (+{len(p['alias'])} spellings)")
    if not a.yes:
        print("preview only; --yes writes to", config.DB)
        return 0
    c = store.connect()
    got = apply(c, ps)
    made = got.pop("_made")
    out = a.out or os.path.join(os.path.dirname(a.authors), "author_wiki.json")
    json.dump(dict(authors=got), open(out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{made} persons made in {config.DB}; {len(got)} spellings -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
