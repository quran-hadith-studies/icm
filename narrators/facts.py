"""Stage 4: facts (death year, Imam period, city) for every linked narrator → ``fact``.

Every fact keeps its source, the entry it came from, the clause that states it and the
pattern that matched. Nothing is resolved here; ``resolve`` (used by export and eval)
combines the facts of one narrator by source priority.

Sources, in priority order: the Infallibles table (fixed), Noor's own وفات / طبقه
fields, then the linked rijāl entries (high/medium links only) in the order
najashi, tusi_rijal, ghadairi, khulasa, tusi_fihrist, khoei, tafrishi / ibn_dawud,
WikiShia; Sunni sets come last (added by ``link_sunni``).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from .arabic_numbers import parse_arabic_number
from .common import ascii_digits, data_file, insert_many, load_json, name_tokens, norm_key, normalize_arabic, open_db, reset_tables, table_count
from .link_shia import imam_groups, imams_in_text
from .common import bare
from .places import Places, build_city_tables

TABLES = ["fact"]
PRIORITY = {"fixed": -1, "noor": 0, "najashi": 1, "tusi_rijal": 2, "ghadairi": 3, "khulasa": 4, "tusi_fihrist": 5,
            "khoei": 6, "tafrishi": 7, "ibn_dawud": 7, "wikishia": 8, "kg": 9, "arsanad": 9, "hawramani": 9,
            "muslimscholars": 9}

_VERB = r"(?:مات|توفي|توفى|قبض|وفاته|هلك|استشهد|قتل|درگذشت|وفات)"
_YEAR = r"(?:سنه|عام|سال)"
_DEATH_POINT = re.compile(_VERB + r"(?:\s+\S+){0,4}?\s+(?:في\s+)?" + _YEAR + r"\s+(?P<y>[\w\s]{1,70})")
_DEATH_APPROX = re.compile(_VERB + r"(?:\s+\S+){0,3}?\s+(?:في\s+)?(?:حدود|نحو|قريب(?:ا)?\s*من)\s+" + _YEAR + r"\s+(?P<y>[\w\s]{1,70})")
_DEATH_BEFORE = re.compile(_VERB + r"(?:\s+\S+){0,3}?\s+قبل\s+" + _YEAR + r"\s+(?P<y>[\w\s]{1,70})")
_DEATH_AFTER = re.compile(_VERB + r"(?:\s+\S+){0,3}?\s+بعد\s+" + _YEAR + r"\s+(?P<y>[\w\s]{1,70})")
_PERIOD = re.compile(_VERB + r"(?:\s+\S+){0,3}?\s+(?:في\s+)?(?:ايام|زمن|زمان|عهد|حياه|خلافه|امامه|عصر)\s+(?P<who>.{3,60})")
_MEMBER = re.compile(r"(?:من\s+)?(?:اصحاب|رجال|حواري)\s+(?P<who>.{3,60})|(?:روى|يروي|سمع|لقي|ادرك|صحب)\s+(?:عن\s+)?(?P<who2>.{3,60})")
_CITY_VERB = re.compile(r"(?:سكن|نزل|من\s+اهل|اصله\s+من|انتقل\s+الى|قدم|كان\s+ب|ولد\s+ب|مولده\s+ب|توفي\s+ب|مات\s+ب|دفن\s+ب|يسكن|ساكن|نزيل|اقام\s+ب|استوطن)\s*(?P<c>\S+(?:\s+\S+)?)")
_CLAUSE_SPLIT = re.compile(r"[.،؛:!?؟]")


def _nt(s: str) -> str:
    t = normalize_arabic(ascii_digits(s or ""))
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def clauses(text: str) -> List[str]:
    return [c.strip() for c in _CLAUSE_SPLIT.split(text or "") if c and c.strip()]


class Extractor:
    def __init__(self, con):
        self.places = Places(con)
        d = load_json(data_file("imam_reigns.json"))["imams"]
        self.reign = {k: tuple(v["reign"]) for k, v in d.items()}
        self.facts: List[tuple] = []

    def add(self, rid: int, kind: str, value, lo, hi, source: str, entry_id, evidence: str, pattern: str):
        self.facts.append((rid, kind, None if value is None else str(value), lo, hi, source, entry_id,
                           (evidence or "")[:200], pattern, PRIORITY.get(source, 9)))

    def add_imam_group(self, rid: int, group: frozenset, source: str, entry_id, evidence: str, pattern: str,
                       kind: str = "imam", tail: int = 0) -> None:
        """One fact per mention; an ambiguous kunya becomes value «kazim|rida|hadi» whose
        interval spans every Imam it can mean (lo = the earliest accession)."""
        lo = min(self.reign[i][0] for i in group)
        hi = max(self.reign[i][1] for i in group) + tail
        self.add(rid, kind, "|".join(sorted(group)), lo, hi, source, entry_id, evidence, pattern)

    # -- dates ----------------------------------------------------------------------
    def years_from_text(self, rid: int, text: str, source: str, entry_id, require_verb: bool = True,
                        head_clauses: Optional[int] = None) -> bool:
        """Death statements. For the long dictionaries (Khoei, Khulāṣa, Tafrishī) only the
        opening clauses are read: further down they quote other people's dates
        («حميد المتوفى سنة 310»)."""
        found = False
        cls = clauses(text)
        if head_clauses:
            cls = cls[:head_clauses]
        for cl in cls:
            t = _nt(cl)
            for pat, kind, name in ((_DEATH_APPROX, "approx", "death_approx"), (_DEATH_BEFORE, "before", "death_before"),
                                    (_DEATH_AFTER, "after", "death_after"), (_DEATH_POINT, "point", "death_point")):
                m = pat.search(t)
                if not m:
                    continue
                y = parse_arabic_number(m.group("y"))
                if not y or not 1 <= y <= 1450:
                    continue
                if kind == "point":
                    self.add(rid, "death", y, y, y, source, entry_id, cl, name)
                elif kind == "approx":
                    self.add(rid, "death", y, y - 5, y + 5, source, entry_id, cl, name)
                elif kind == "before":
                    self.add(rid, "death_interval", None, max(1, y - 40), y, source, entry_id, cl, name)
                else:
                    self.add(rid, "death_interval", None, y, y + 40, source, entry_id, cl, name)
                found = True
                break
            m = _PERIOD.search(t)
            if m:
                for g in imam_groups(m.group("who")):
                    self.add_imam_group(rid, g, source, entry_id, cl, "death_period", kind="death_interval", tail=30)
                    found = True
        if not found and not require_verb:
            # Noor's وفات field: «274»، «سنة أربع و سبعين و مائتين»، «قبل 148،مات في حياة …»،
            # «بعد 203،عاش بعد الرضا» — a bare year, optionally qualified
            t = _nt(text)
            m = re.match(r"^(?P<q>قبل|بعد|حدود|نحو)?\s*(?:سنه|عام|سال)?\s*(?P<y>.+)$", t)
            y = parse_arabic_number(m.group("y")) if m else None
            if y and 1 <= y <= 1450:
                q = m.group("q")
                if q == "قبل":
                    self.add(rid, "death_interval", None, max(1, y - 40), y, source, entry_id, text, "bare_before")
                elif q == "بعد":
                    self.add(rid, "death_interval", None, y, y + 40, source, entry_id, text, "bare_after")
                elif q:
                    self.add(rid, "death", y, y - 5, y + 5, source, entry_id, text, "bare_approx")
                else:
                    self.add(rid, "death", y, y, y, source, entry_id, text, "bare_year")
                found = True
        return found

    def imams_from_text(self, rid: int, text: str, source: str, entry_id, head_clauses: int = 5) -> None:
        """Imams the narrator transmitted from, read from the opening clauses only: deeper
        in an entry (Khoei especially) the text quotes other people's chains."""
        seen: Set[frozenset] = set()
        for cl in clauses(text)[:head_clauses]:
            t = _nt(cl)
            for m in _MEMBER.finditer(t):
                who = m.group("who") or m.group("who2") or ""
                for g in imam_groups(who):
                    if g in seen:
                        continue
                    seen.add(g)
                    self.add_imam_group(rid, g, source, entry_id, cl, "member")

    # -- places ---------------------------------------------------------------------
    def cities_from_text(self, rid: int, heading: str, body: str, source: str, entry_id) -> None:
        """Nisbas are read from the heading and the entry's opening clause only (the name
        and its epithets, «X كوفي، ثقة»); deeper in the body «روى الكشي عن» or «ذكره
        الطوسي» would turn authors' nisbas into cities."""
        seen: Set[str] = set()
        first = clauses(body)[:1]
        opening = _nt(first[0]) if first else ""
        toks = name_tokens(heading) + [bare(t) for t in opening.split()[:15]]
        for t in toks:
            c = self.places.from_nisba(t)
            if c and c not in seen:
                seen.add(c)
                self.add(rid, "city", c, None, None, source, entry_id, (heading + " " + body)[:120], f"nisba:{t}")
        for cl in clauses(body):
            t = _nt(cl)
            for m in _CITY_VERB.finditer(t):
                words = m.group("c").split()
                for w in (" ".join(words[:2]), words[0]):
                    c = self.places.from_toponym(w)
                    if c:
                        if c not in seen:
                            seen.add(c)
                            self.add(rid, "city", c, None, None, source, entry_id, cl, "verb")
                        break


def run(db_path: str) -> None:
    con = open_db(db_path)
    build_city_tables(con)
    reset_tables(con, TABLES)
    ex = Extractor(con)
    # (1) Infallibles
    for r in con.execute("select * from infallible"):
        if r["death"]:
            ex.add(r["ravi_id"], "death", r["death"], r["death"], r["death"], "fixed", None, r["canonical"], "fixed")
        for c in (r["city"] or "").split("،"):
            if c.strip():
                ex.add(r["ravi_id"], "city", c.strip(), None, None, "fixed", None, r["canonical"], "fixed")
    # (2) Noor's own fields
    for r in con.execute("select ravi_id, wafat_json, tabaqa_json, laqab_json, name from ravi"):
        rid = r["ravi_id"]
        for it in json.loads(r["wafat_json"] or "[]"):
            ex.years_from_text(rid, it.get("text", ""), "noor", None, require_verb=False)
        for it in json.loads(r["tabaqa_json"] or "[]"):
            for g in imam_groups(it.get("text", "")):
                ex.add_imam_group(rid, g, "noor", None, it.get("text", ""), "tabaqa")
        laqabs = " ".join(it.get("text", "") for it in json.loads(r["laqab_json"] or "[]"))
        if laqabs:
            ex.cities_from_text(rid, laqabs, "", "noor", None)
    # (3) linked rijāl entries
    q = """select l.ravi_id, l.confidence, e.entry_id, e.book, e.heading, e.body, e.imam
           from link l join entry e on e.entry_id = cast(l.target_id as integer)
           where l.target_kind = 'entry' and l.confidence in ('high', 'medium')"""
    for r in con.execute(q):
        rid, eid, book = r["ravi_id"], r["entry_id"], r["book"]
        ex.years_from_text(rid, r["body"], book, eid,
                           head_clauses=8 if book in ("khoei", "khulasa", "tafrishi", "ibn_dawud") else None)
        ex.imams_from_text(rid, r["body"], book, eid)
        if r["imam"] and r["imam"] in ex.reign:
            lo, hi = ex.reign[r["imam"]]
            ex.add(rid, "imam", r["imam"], lo, hi, book, eid, r["heading"], "section")
        ex.cities_from_text(rid, r["heading"], r["body"], book, eid)
    # (4) WikiShia
    q = """select l.ravi_id, w.* from link l join wikishia w
           on l.target_kind = 'wikishia' and w.page_id = cast(substr(l.target_id, 1, instr(l.target_id, ':') - 1) as integer)
           and w.lang = substr(l.target_id, instr(l.target_id, ':') + 1)
           where l.confidence in ('high', 'medium')"""
    for r in con.execute(q):
        rid = r["ravi_id"]
        key = f"wikishia:{r['lang']}:{r['page_id']}"
        if r["death_year"]:
            ex.add(rid, "death", r["death_year"], r["death_year"], r["death_year"], "wikishia", None, r["death_text"], key)
        for field in ("residence", "birthplace"):
            for part in re.split(r"[•،,;/]| و | and ", r[field] or ""):
                c = ex.places.from_toponym(part.strip())
                if c:
                    ex.add(rid, "city", c, None, None, "wikishia", None, r[field], f"{key}:{field}")
        for name in json.loads(r["imams_json"] or "[]"):
            for g in imam_groups(name):
                ex.add_imam_group(rid, g, "wikishia", None, name, key)
    insert_many(con, "fact", ["ravi_id", "kind", "value", "lo", "hi", "source", "entry_id", "evidence", "pattern", "priority"],
                ex.facts)
    con.commit()
    print(f"facts: {table_count(con, 'fact')} rows")
    for kind in ("death", "death_interval", "imam", "city"):
        n = con.execute("select count(distinct ravi_id) from fact where kind = ?", (kind,)).fetchone()[0]
        print(f"  {kind}: {n} narrators")
    tot = con.execute("select sum(n_slots) from ravi").fetchone()[0]
    dated = con.execute("""select sum(n_slots) from ravi where ravi_id in
                           (select ravi_id from fact where kind in ('death', 'death_interval', 'imam'))""").fetchone()[0]
    print(f"  slots with any date information: {(dated or 0) / tot:.1%}")


# ----------------------------------------------------------------------------- resolution

def resolve(con, ravi_ids: Optional[List[int]] = None) -> Dict[int, dict]:
    """Per narrator: death (lo, hi) or None, death_source, conflict flag, cities, imams."""
    where = ""
    params: tuple = ()
    if ravi_ids:
        where = " where ravi_id in (%s)" % ",".join("?" * len(ravi_ids))
        params = tuple(ravi_ids)
    rows = con.execute("select * from fact" + where + " order by ravi_id, priority", params).fetchall()
    by: Dict[int, List] = defaultdict(list)
    for r in rows:
        by[r["ravi_id"]].append(r)
    out = {}
    for rid, fs in by.items():
        points = [f for f in fs if f["kind"] == "death" and f["lo"] is not None]
        intervals = [f for f in fs if f["kind"] == "death_interval"]
        # unambiguous Imams first; an ambiguous «kazim|rida|hadi» is kept only when no
        # single-Imam fact already covers one of its members
        singles = {f["value"] for f in fs if f["kind"] == "imam" and f["value"] and "|" not in f["value"]}
        groups = {f["value"] for f in fs if f["kind"] == "imam" and f["value"] and "|" in f["value"]
                  and not (set(f["value"].split("|")) & singles)}
        imams = sorted(singles | groups)
        cities = []
        for f in fs:
            if f["kind"] == "city" and f["value"] and f["value"] not in cities:
                cities.append(f["value"])
        death = src = None
        conflict = False
        if points:
            best = points[0]
            vals = sorted({(f["lo"], f["hi"]) for f in points})
            lo = min(v[0] for v in vals)
            hi = max(v[1] for v in vals)
            if hi - lo <= 3:
                death, src = (lo, hi), best["source"]
            else:
                death, src, conflict = (best["lo"], best["hi"]), best["source"], True
        elif intervals:
            best = intervals[0]
            death, src = (best["lo"], best["hi"]), best["source"] + ":" + (best["pattern"] or "")
        elif imams:
            reigns = [(f["lo"], f["hi"]) for f in fs if f["kind"] == "imam"]
            lo = min(r[0] for r in reigns)
            hi = max(r[1] for r in reigns) + 30
            death, src = (lo, hi), "imam"
        out[rid] = dict(death=death, death_source=src, death_conflict=conflict, cities=cities, imams=imams)
    return out
