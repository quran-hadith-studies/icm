"""authors.json: who the author (collector) of every book is, in the narrator databases - only where that is certain.

    python3 -m icm build authors

The collector of a hadith is its book's author, written as the book's catalogue wrote him («الشيخ الصدوق», «البخاري»,
«مفید، محمد بن محمد»). Matching that name against the narrator databases alone picks wrong people (al-Kulayni became
al-Tusi), so a link is made only when *both* checks hold:

  name   (an author given by a single word needs OpenITI or muslimscholars to know him as well) every word of the author's name is in one of the person's names (titles like «الشيخ» are not words), or the
         person's name (at least two words) is inside the author's; either way the shared part has a rare word (a
         shared «محمد بن علي» proves nothing);
  death  the book's author death year (hijri) and the person's death year in that database differ by at most one;

and, per database, exactly one person passes both. Two persons passing (or none) means no link from that database; the
author is then listed in ``authors_review.tsv`` with the candidates, for a person to decide. The databases are Noor
(``narrators.sqlite``; death years from ``narrators.facts.resolve``, only exact years), the Hadith Knowledge Graph,
OpenITI's author list and muslimscholars.info; the last two have no ICM ids, they only say that the author is a known person.

``icm/data/author_overrides.json`` holds links made by hand, for the people the check cannot decide (no or wrong death year in
the database, a title in the name); each says what was checked, and it applies only to an author that is not already linked.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .. import config
from ..authors import AUTHORS_JSON, key_of, noor_deaths, toks

OVERRIDES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "author_overrides.json")
OUT = AUTHORS_JSON
REVIEW = os.path.join(config.DATA_DIR, "authors_review.tsv")
OPENITI = os.path.join(config.HERE, "external", "datasets", "openiti-metadata", "OpenITI_metadata_2025-1-9.tsv")
MUSLIMSCHOLARS = os.path.join(config.HERE, "external", "datasets", "zenodo-narrator-er", "extracted", "zenodo", "muslimscholars_scholars.csv")
RARE_DF = 250          # a single shared word must occur in fewer than this many persons' names


def ism_ok(p: "Person", q: "Person") -> bool:
    """Some name of p and some name of q begin with the same two words (and not with a kunya): same first name and father's."""
    return any(len(v) >= 2 and len(w) >= 2 and v[:2] == w[:2] and v[0] != "ابو" for v in q.variants for w in p.variants)


def subseq(a: tuple, b: tuple) -> bool:
    it = iter(b)
    return all(t in it for t in a)


class Person:
    """``deaths``: every death year the database has (Noor may hold several, from different books)."""
    __slots__ = ("id", "name", "variants", "deaths")

    def __init__(self, id, name, variants, deaths):
        self.id, self.name = id, name
        self.deaths = tuple(deaths) if isinstance(deaths, (list, tuple, set)) else (deaths,)
        self.variants = [v for v in {toks(x) for x in variants if x} if v]


def _noor() -> List[Person]:
    c = sqlite3.connect(f"file:{config.NARRATORS_DB}?mode=ro&immutable=1", uri=True)
    c.row_factory = sqlite3.Row
    deaths = defaultdict(set)
    for rid, ds in noor_deaths().items():
        deaths[rid] = ds
    out = []
    for r in c.execute("select ravi_id, name, kunya, ishtihar, laqab_json from ravi"):
        if r["ravi_id"] not in deaths:
            continue
        lq = []
        for x in json.loads(r["laqab_json"] or "[]"):
            lq.append(x.get("text") if isinstance(x, dict) else x)
        out.append(Person(f"noor:{r['ravi_id']}", r["name"], [r["name"], r["kunya"], r["ishtihar"]] + lq, deaths[r["ravi_id"]]))
    return out


def _kg() -> List[Person]:
    c = sqlite3.connect(f"file:{config.KG_DB}?mode=ro&immutable=1", uri=True)
    return [Person(f"kg:{i}", n, [n, nn], d) for i, n, nn, d in c.execute(
        "select id, name, nickname, death_year from rawis where death_year is not null and death_year > 0")]


def _openiti() -> List[Person]:
    csv.field_size_limit(10 ** 9)
    au: Dict[str, dict] = {}
    for r in csv.DictReader(open(OPENITI, encoding="utf-8"), delimiter="\t"):
        k = r["version_uri"].split(".")[0]
        if not k[:4].isdigit():
            continue
        a = au.setdefault(k, dict(names=set(), lat=r["author_lat_full_name"] or r["author_lat"]))
        a["names"].update(x.strip() for x in r["author_ar"].split("::") if x.strip())
    return [Person(f"openiti:{k}", sorted(a["names"])[0] if a["names"] else a["lat"], a["names"], int(k[:4])) for k, a in au.items()]


def _muslimscholars() -> List[Person]:
    csv.field_size_limit(10 ** 9)
    out = []
    for r in csv.DictReader(open(MUSLIMSCHOLARS, encoding="utf-8")):
        if r["death_year_h"]:
            nm = re.sub(r"\(.*$", "", r["name_ar"]).strip()
            out.append(Person(f"ms:{r['id']}", nm or r["name_en"], [nm], float(r["death_year_h"])))
    return out


class Source:
    """``relaxed``: a small list of authors (a few per death year), where a lone common word plus the death year is already unique."""

    def __init__(self, name: str, persons: List[Person], df: Counter, relaxed: bool = False):
        self.name, self.df, self.relaxed = name, df, relaxed
        self.by_year: Dict[int, Dict[str, Person]] = defaultdict(dict)
        for p in persons:
            for d in p.deaths:
                self.by_year[int(round(d))][p.id] = p

    def _same_lineage(self, a: tuple, p: tuple) -> bool:
        """Two full names that begin with the same name, father, grandfather and great-grandfather (after a kunya): one may stop
        earlier, add a nisba or leave out an ancestor further down."""
        a, p = (t[2:] if t[0] == "ابو" and len(t) > 4 else t for t in (a, p))
        k = 0
        while k < min(len(a), len(p)) and a[k] == p[k]:
            k += 1
        return k >= 4 or (k == 3 and any(self.df[t] < RARE_DF for t in a[:3]))

    def passing(self, author: tuple, death: int) -> List[Person]:
        return self.passing_any([author], death)

    def passing_any(self, names: Iterable[tuple], death: int) -> List[Person]:
        out: Dict[str, Person] = {}
        for y in (death - 1, death, death + 1):
            for p in self.by_year.get(y, {}).values():
                if min(abs(d - death) for d in p.deaths) > 1:
                    continue
                if any(self._agree(a, v) for a in names for v in p.variants):
                    out[p.id] = p
        return list(out.values())

    def _agree(self, a: tuple, p: tuple) -> bool:
        if self._same_lineage(a, p):
            return True
        rare = (lambda x: True) if self.relaxed else (lambda x: any(self.df[t] < RARE_DF for t in set(x)))
        if subseq(a, p):               # the author is given by a laqab or a shortened name: it is in the person's name, in order
            return rare(a) and (len(a) == 1 or a[0] == p[0])       # a shortened name starts with the same first name («علي بن جعفر» is not «محمد بن علي بن جعفر»)
        tail = len(p) >= 2 and a[-len(p):] == p and self.df[p[0]] < RARE_DF      # «ابن حجر العسقلاني» ends the author's name; «أحمد بن حنبل» (his father's) must not count
        if tail and rare(p):
            return True
        if len(p) >= 2 and any(a[k:k + len(p)] == p for k in (0, 2) if k == 0 or a[0] == "ابو"):
            # the person's name is the shorter and is the start of the author's (after a kunya): not his father's name inside it («عبد الله بن أحمد بن حنبل»),
            # and it must still say more than «محمد بن علي»
            return rare(p) and (len(p) >= 3 or self.relaxed or all(self.df[t] < RARE_DF for t in p))
        return False


def _authors() -> Dict[str, dict]:
    """{key: {author, death, n_books}} from the v02 books and the KG's books."""
    out: Dict[str, dict] = {}

    def add(a, d, tradition):
        if not a or not d or not toks(a):
            return
        e = out.setdefault(key_of(a, d), dict(author=a, death=int(d), n_books=0, tradition=Counter()))
        e["n_books"] += 1
        e["tradition"][tradition or "?"] += 1

    for v in json.load(open(config.BOOKS_META, encoding="utf-8")).values():
        add(v.get("author"), v.get("death"), v.get("tradition"))
    c = sqlite3.connect(f"file:{config.KG_DB}?mode=ro&immutable=1", uri=True)
    for a, d in c.execute("select author_name, author_death_year from books"):
        add(a, d, "sunni")
    return out


def _overrides() -> Dict[str, dict]:
    """Links made by hand, for authors whose person has no (or a wrong) death year in the database. Each says in ``note``
    what was checked; the id must exist."""
    if not os.path.exists(OVERRIDES):
        return {}
    nc = sqlite3.connect(f"file:{config.NARRATORS_DB}?mode=ro&immutable=1", uri=True)
    kc = sqlite3.connect(f"file:{config.KG_DB}?mode=ro&immutable=1", uri=True)
    out = {}
    for k, v in json.load(open(OVERRIDES, encoding="utf-8")).items():
        if k.startswith("_"):
            continue
        ids = [f"{kind}:{i}" for kind, i in v.items() if kind in ("noor", "kg")]
        gone = [i for i in ids if not (nc.execute("select 1 from ravi where ravi_id=?", (i[5:],)).fetchone() if i.startswith("noor:")
                                       else kc.execute("select 1 from rawis where id=?", (i[3:],)).fetchone())]
        if gone or not v.get("note"):
            print(f"override {k}: {'no such id ' + str(gone) if gone else 'no note'} - ignored", file=sys.stderr)
            continue
        if k.startswith("openiti:"):
            out[k] = dict(ids=ids, note=v["note"])
        else:
            author, _, death = k.rpartition("|")
            out[key_of(author, death)] = dict(ids=ids, note=v["note"])
    return out


def run(dst: str = OUT) -> None:
    persons = {"noor": _noor(), "kg": _kg(), "openiti": _openiti(), "ms": _muslimscholars()}
    df: Counter = Counter()
    for k in ("noor", "kg"):
        for p in persons[k]:
            for t in set().union(*map(set, p.variants)) if p.variants else ():
                df[t] += 1
    srcs = {k: Source(k, v, df, relaxed=k in ("openiti", "ms")) for k, v in persons.items()}
    hand = _overrides()
    authors = _authors()
    res, review = {}, []
    for key, a in sorted(authors.items(), key=lambda kv: -kv[1]["n_books"]):
        tk = toks(a["author"])
        rec = dict(author=a["author"], death=a["death"], n_books=a["n_books"], ids=[], known=[], ambiguous={}, by=None)
        known = []                 # the author as OpenITI / muslimscholars have him, when exactly one of their persons fits
        for name in ("openiti", "ms"):
            ps = srcs[name].passing(tk, a["death"])
            if len(ps) == 1:
                known.append(ps[0])
        rec["known"] = [q.id for q in known]
        for name in ("noor", "kg"):
            ps = srcs[name].passing(tk, a["death"])
            if len(set(tk)) == 1 and not known:
                ps = []                  # a lone nickname («الخلال») and a year are not enough unless OpenITI / muslimscholars know the author too
            by = "name+death"
            if len(ps) != 1 and known:
                # a second way to the same person: the fuller names OpenITI / muslimscholars give him. A lone nickname («ماجه») is
                # accepted only when the first names agree (otherwise the person of the same nickname and year may be someone else)
                ps2 = {p.id: p for q in known for p in srcs[name].passing_any([v for v in q.variants if len(v) >= 2], a["death"])}
                ps2.update({p.id: p for q in known for p in srcs[name].passing_any(q.variants, a["death"]) if ism_ok(p, q)})
                if ps:
                    ps2 = {i: p for i, p in ps2.items() if i in {q.id for q in ps}}      # the catalogue's name and the fuller one both fit
                ps, by = list(ps2.values()), "name+death, confirmed by " + known[0].id.split(":")[0]
            if len(ps) == 1:
                rec["ids"].append(ps[0].id)
                rec["by"] = by
            elif len(ps) > 1:
                rec["ambiguous"][name] = [f"{p.id} {p.name}" for p in ps[:6]]
        h = hand.get(key) or next((hand[q] for q in rec["known"] if q in hand), None)
        if h and not rec["ids"]:
            rec["ids"], rec["by"] = h["ids"], "hand: " + h["note"]
        res[key] = rec
        if not rec["ids"]:
            review.append(rec)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    json.dump(dict(authors=res), open(dst, "w", encoding="utf-8"), ensure_ascii=False)
    with open(REVIEW, "w", encoding="utf-8") as f:
        f.write("books\tdeath\tauthor\tknown_elsewhere\tambiguous\n")
        for r in review:
            f.write(f"{r['n_books']}\t{r['death']}\t{r['author']}\t{' '.join(r['known'])}\t{json.dumps(r['ambiguous'], ensure_ascii=False)}\n")
    nb = sum(r["n_books"] for r in res.values())
    got = [r for r in res.values() if r["ids"]]
    print(f"{len(res)} authors ({nb} books): {len(got)} linked ({sum(r['n_books'] for r in got)} books), "
          f"{sum(1 for r in res.values() if not r['ids'] and r['known'])} known only in OpenITI / muslimscholars, "
          f"{sum(1 for r in res.values() if r['ambiguous'] and not r['ids'])} ambiguous -> {dst}, {REVIEW}")


if __name__ == "__main__":
    run()
