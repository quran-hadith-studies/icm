"""Dirayat al-Noor 3 (CRCIS, درایة النور ۳) → ``external/dirayah.sqlite``, linked to Noor (laptop side):

    python3 -m narrators dirayah [--export DIR] [--out PATH]

Input: the JSONL export of the program's Elasticsearch data (``hadith-hub/dirayah3/export.py``; default
``<hadith-project>/dirayah3-data/export``), the v02 corpus (``ICM_V02_DB``: Jami' al-Ahadith hadiths, id 800M +
Noor HadithId, with book code, volume, page, matn and the isnad as written) and ``narrators.sqlite`` (Noor's
linked isnads, ``slot``).

Tables, kept as Dirayah gives them (nothing merged):

    ravi(id PK, title, status, credit_result, credit_summary, religion_result, religion_summary, class_summary,
         tawthiq_summary, n_sanad, subjects, credit_thamanieh, religion_thamanieh, books, categories)
        one row per Dirayah narrator («عنوان معیار», ``CriterionRaviId``); the list fields are JSON
    title(id PK, title, status, n_sanad)            the name forms («عنوان اصلی», ``RaviListId``)
    title_person(title_id, ravi_id, n)              which narrators a name form stands for in Dirayah's isnads
    tarjama(ravi_id, title_id, book_id, book, category_id, vol, start_page, start_pos, end_page, end_pos, section)
        where each narrator's biography is in the rijal books (the text is in the export's ``data`` pages)
    sanad(id PK, book_id, book, vol, start_page, start_pos, end_page, end_pos, chapter, is_main, types, evaluation,
          connection, masuman, torog_status, ekhtelal, subjects, sanad_text, criterion_sanad_text, torog_sanad_text,
          torog_criterion_sanad_text, hadith_text, noor_hadith_id, link_how, link_score)
        Dirayah's rebuilt isnads with their grading; ``noor_hadith_id`` is the Noor hadith (Jami' al-Ahadith
        HadithId = the hadith.ai v2 Noor id) the isnad belongs to, see "Linking records"
    slot(sanad_id, pos, ravi_ids, property, title_id, ravi_title_id, is_ravi, is_author, is_masum, is_torog,
         is_by_atf, intern_title_id, intern_word_type)
        every entity of an isnad in order; ``ravi_ids`` is a JSON list: one id, or the members of a group
        («عدة من أصحابنا») / the alternatives of «أحدهما», ``property`` the kind of reference (1 = the narrator)
    person_link(noor_id, dirayah_id, n_aligned, share_noor, share_dirayah, same_id, same_name, tier, evidence)
    meta(k, v)

Linking records. A Dirayah isnad's candidates are the v02 Jami' al-Ahadith hadiths of the same book (Dirayah's
``BookId`` is Noor's book series code), volume and page (±1: a hadith's page in v02 is where it starts). The
matn decides: the share of Dirayah's matn tokens found, in order, in the candidate's matn must be >= 0.8
(``link_score``) and 0.1 ahead of the next candidate; candidates within 0.1 of the best are told apart by the
share of their own matn found in Dirayah's, again by 0.1 (a short matn found inside several longer ones goes to
the one it equals). An isnad whose matn is shorter
than 3 tokens («مثله», Wasa'il's «رواه الشيخ بإسناده عن … مثله») is decided by the names of the candidates' linked
isnads (``link_how = 'isnad'``: the share of the names Noor wrote in a ``slot`` isnad of the record, as words, found
in Dirayah's isnad text; Noor keeps such routes as the record's further isnads), or, on a page without linked
isnads, by v02's written isnad (``'sanad'``). The deciding share is taken of Noor's side, since Dirayah's text also
names the collector the book leaves out. Otherwise unlinked. ``noor_sanad_idx`` is the Noor isnad of the record
that the same name test picks (at least half of its written names found; else None and the record gives no person
evidence); it is what the persons are aligned against. Ids never decide a record link.

Linking persons — certain only from shared records. In every linked record the Noor isnad (``noor_sanad_idx``)
and the Dirayah narrators of the isnad's written chain (without the route to the book, ``is_torog``, which Noor's
isnad does not hold) are aligned (``SequenceMatcher`` on the ids; equal runs pair one to one, and so do
equal-length substituted runs between them, except parallel narrators «X و Y», ``is_by_atf``, whose order is free);
each pair is one aligned position. A Noor id and a Dirayah id are
the same person (``tier = 'certain'``) when they are aligned at >= 3 positions and each is >= 95 % of the other's
aligned positions, and their names do not disagree (``name_conflict``: another first name or another father;
«عمر»/«عمرو» counts as a disagreement, a kunya found inside the other name does not). Weaker record evidence,
a name disagreement, and the same id with the same normalised name but no record
evidence either way, are ``'suggested'``. The id numbers alone decide nothing: the two databases share an id
space but not always the person (Noor 4305 عبد الله بن جبلة is Dirayah 3462; Dirayah 4305 is عمر بن جبلة),
and namesakes under one id exist (Noor 407 أحمد بن أبي داود is Dirayah 35429 in 13 of 14 positions).
"""
from __future__ import annotations

import gzip
import json
import os
import re
import sqlite3
import time
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Dict, Iterator, List, Optional, Tuple

from .common import DB_PATH, EXTERNAL, PROJECT, as_list, bare, norm_name

EXPORT = os.environ.get("DIRAYAH_EXPORT", os.path.join(PROJECT, "dirayah3-data", "export"))
OUT = os.path.join(EXTERNAL, "dirayah.sqlite")
V02_DB = os.environ.get("ICM_V02_DB", os.path.join(os.path.dirname(PROJECT), "ebadollah-data", "data", "v02", "hadith.sqlite"))
NOOR_OFFSET = 800_000_000

MIN_MATN = 3              # tokens of matn below which the written isnad decides
MIN_SHARE = 0.8           # share of the deciding text found in the candidate
MIN_MARGIN = 0.1          # ahead of the next candidate
MIN_ISNAD = 0.5           # share of a Noor isnad's written names found in Dirayah's text for it to be the one aligned
CERTAIN_N = 3             # aligned positions for a certain person link
CERTAIN_SHARE = 0.95      # share of each side's aligned positions

SCHEMA = """
create table ravi(id integer primary key, title text, status text, credit_result text, credit_summary text,
                  religion_result text, religion_summary text, class_summary text, tawthiq_summary text, n_sanad integer,
                  subjects text, credit_thamanieh text, religion_thamanieh text, books text, categories text);
create table title(id integer primary key, title text, status text, n_sanad integer);
create table title_person(title_id integer, ravi_id integer, n integer, primary key (title_id, ravi_id));
create table tarjama(ravi_id integer, title_id integer, book_id integer, book text, category_id integer, vol integer,
                     start_page integer, start_pos integer, end_page integer, end_pos integer, section integer);
create table sanad(id integer primary key, book_id integer, book text, vol integer, start_page integer, start_pos integer,
                   end_page integer, end_pos integer, chapter text, is_main integer, types text, evaluation text,
                   connection text, masuman text, torog_status text, ekhtelal text, subjects text, sanad_text text,
                   criterion_sanad_text text, torog_sanad_text text, torog_criterion_sanad_text text, hadith_text text,
                   noor_hadith_id integer, noor_sanad_idx integer, link_how text, link_score real);
create table slot(sanad_id integer, pos integer, ravi_ids text, property integer, title_id integer, ravi_title_id integer,
                  is_ravi integer, is_author integer, is_masum integer, is_torog integer, is_by_atf integer,
                  intern_title_id integer, intern_word_type integer, primary key (sanad_id, pos)) without rowid;
create table person_link(noor_id integer, dirayah_id integer, n_aligned integer, share_noor real, share_dirayah real,
                         same_id integer, same_name integer, tier text, evidence text, primary key (noor_id, dirayah_id));
create table meta(k text primary key, v text);
"""


def _rows(name: str, export: str) -> Iterator[dict]:
    with gzip.open(os.path.join(export, f"{name}.jsonl.gz"), "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _one(fields: dict, k: str):
    v = fields.get(k)
    return v[0] if isinstance(v, list) and v else v


def _int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _nested(rec: dict, path: str) -> List[dict]:
    """A nested path's objects as {child field: value or list}, from the export's ``nested`` doc values."""
    out = []
    for o in (rec.get("nested") or {}).get(path, []):
        d = {}
        for k, v in o.items():
            k = k.split(".", 1)[1]
            d[k] = v[0] if isinstance(v, list) and len(v) == 1 else v
        out.append(d)
    return out


_DIGITS = re.compile(r"[\d٠-٩۰-۹]+")


def tokens(s: str) -> List[str]:
    return [t for t in norm_name(_DIGITS.sub(" ", s or "")).split() if len(t) > 1]


def ravi_ids(v) -> List[int]:
    """``CriterionRaviId`` of an isnad entity: "5102,1" or a list of such (a group or alternatives)."""
    return sorted({int(str(x).split(",")[0]) for x in as_list(v) if str(x).split(",")[0].isdigit()})


def _prop(v) -> Optional[int]:
    xs = [str(x).split(",") for x in as_list(v)]
    ps = {int(x[1]) for x in xs if len(x) > 1 and x[1].isdigit()}
    return ps.pop() if len(ps) == 1 else None


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------
def load_ravi(c: sqlite3.Connection, export: str) -> None:
    persons, titles = [], []
    for r in _rows("ravi", export):
        f, s = r.get("fields") or {}, r.get("_source") or {}
        if r["_id"].endswith(";CriterionRaviId"):
            persons.append((_one(f, "CriterionRaviId"), s.get("RaviTitle"), _one(f, "CriterionStatus"), _one(f, "CreditResult"),
                            _one(f, "CreditSummery"), _one(f, "ReligionResult"), _one(f, "ReligionSummery"), _one(f, "ClassSummery"),
                            _one(f, "TawthighSummery"), _int(s.get("SandCount")), json.dumps(f.get("Subject") or [], ensure_ascii=False),
                            json.dumps(_nested(r, "CreditThamanieh"), ensure_ascii=False),
                            json.dumps(_nested(r, "ReligionThamanieh"), ensure_ascii=False),
                            json.dumps(_nested(r, "CategoryBook"), ensure_ascii=False),
                            json.dumps(_nested(r, "Category"), ensure_ascii=False)))
        else:
            titles.append((_int(_one(f, "RaviListId") or r["_id"].split(";")[0]), s.get("RaviTitle"), _one(f, "CriterionStatus"),
                           _int(s.get("SandCount"))))
    c.executemany("insert into ravi values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", persons)
    c.executemany("insert or replace into title values (?,?,?,?)", titles)


def load_tarjama(c: sqlite3.Connection, export: str) -> None:
    rows = []
    for r in _rows("dtarajem", export):
        s = r["_source"]
        rows.append((_int(s.get("CriterionRaviId")), _int(s.get("RaviListId")), _int(s.get("BookId")), s.get("BookName"),
                     _int(s.get("CategoryId")), _int(s.get("Vol")), _int(s.get("StartPage")), _int(s.get("StartIndexPos")),
                     _int(s.get("EndPage")), _int(s.get("EndIndexPos")), _int(s.get("Section"))))
    c.executemany("insert into tarjama values (?,?,?,?,?,?,?,?,?,?,?)", rows)


def load_sanad(c: sqlite3.Connection, export: str) -> None:
    tp: Counter = Counter()
    batch_s, batch_e = [], []
    for r in _rows("sanad", export):
        s, f, d = r["_source"], r.get("fields") or {}, r.get("_decoded") or {}
        sid = _one(f, "SanadId")
        batch_s.append((sid, _int(s.get("BookId")), _one(f, "BookTitle"), _int(s.get("Vol")), _int(s.get("StartPage")),
                        _int(s.get("StartPagePos")), _int(s.get("EndPage")), _int(s.get("EndPagePos")), _one(f, "BookChapter"),
                        _one(f, "IsMain"), json.dumps(f.get("SanadType") or [], ensure_ascii=False), _one(f, "SanadEvaluation"),
                        _one(f, "SanadConnection"), _one(f, "Masuman"), _one(f, "TorogStatus"), _one(f, "Ekhtelal"),
                        json.dumps(f.get("Subjects") or [], ensure_ascii=False), d.get("SanadText"), d.get("CriterionSanadText"),
                        d.get("TorogSanadText"), d.get("TorogCriterionSanadText"), d.get("Hadith"), None, None, None, None))
        for k, e in enumerate(as_list(s.get("Entities"))):
            ids = ravi_ids(e.get("CriterionRaviId"))
            tid = _int(e.get("RaviListId"))
            if tid and len(ids) == 1:
                tp[(tid, ids[0])] += 1
            batch_e.append((sid, k, json.dumps(ids) if ids else None, _prop(e.get("CriterionRaviId")), tid, _int(e.get("RaviTitleId")),
                            _int(e.get("IsRavi")), _int(e.get("IsAuthor")), _int(e.get("IsMasum")), _int(e.get("IsTorog")),
                            _int(e.get("IsByAtf")), _int(e.get("InternTitleId")), _int(e.get("InternWordTypeId"))))
        if len(batch_e) > 200_000:
            c.executemany("insert into sanad values (%s)" % ",".join("?" * 26), batch_s)
            c.executemany("insert into slot values (%s)" % ",".join("?" * 13), batch_e)
            batch_s, batch_e = [], []
    c.executemany("insert into sanad values (%s)" % ",".join("?" * 26), batch_s)
    c.executemany("insert into slot values (%s)" % ",".join("?" * 13), batch_e)
    c.executemany("insert into title_person values (?,?,?)", [(t, p, n) for (t, p), n in tp.items()])


# ---------------------------------------------------------------------------
# linking records
# ---------------------------------------------------------------------------
def _share(a: List[str], b: List[str]) -> float:
    """Share of ``a``'s tokens found, in order, in ``b``."""
    if not a:
        return 0.0
    return sum(m.size for m in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()) / len(a)


def noor_isnads(narrators_db: str) -> Dict[int, Dict[int, Tuple[List[str], List[Optional[int]]]]]:
    """Noor's linked isnads: hadith_id -> {sanad_idx: (tokens of the names as written, RaviIds)}."""
    nc = sqlite3.connect(f"file:{narrators_db}?mode=ro&immutable=1", uri=True)
    out: Dict[int, Dict[int, Tuple[List[str], List[Optional[int]]]]] = defaultdict(dict)
    for hid, sidx, title, rid in nc.execute("select hadith_id, sanad_idx, title, ravi_id from slot order by hadith_id, sanad_idx, pos"):
        t, ids = out[hid].setdefault(sidx, ([], []))
        t.extend(tokens(title))
        ids.append(rid)
    nc.close()
    return out


def _decide(sc: List[Tuple[float, float, int]]) -> Tuple[str, Optional[Tuple[float, float, int]]]:
    """``sc``: (deciding share, other side's share, candidate). The best by the deciding share, >= MIN_SHARE; candidates
    within MIN_MARGIN of it are told apart by the other share, again by MIN_MARGIN."""
    if not sc:
        return "low", None
    sc = sorted(sc, reverse=True)
    if sc[0][0] < MIN_SHARE:
        return "low", None
    top = sorted((x for x in sc if x[0] >= sc[0][0] - MIN_MARGIN), key=lambda x: -x[1])
    if len(top) > 1 and top[0][1] - top[1][1] < MIN_MARGIN:
        return "ambiguous", None
    return ("linked", top[0]) if top[0][0] >= MIN_SHARE else ("low", None)


def _shares(a: List[str], b: List[str]) -> Tuple[float, float]:
    m = sum(x.size for x in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()) if a and b else 0
    return m / max(len(a), 1), m / max(len(b), 1)


def link_records(c: sqlite3.Connection, v02: str, narrators_db: str, log=print) -> Counter:
    noor = noor_isnads(narrators_db)
    vc = sqlite3.connect(f"file:{v02}?mode=ro&immutable=1", uri=True)
    st: Counter = Counter()
    upd = []
    cache: Dict[Tuple[str, int], Dict[int, list]] = {}
    rows = c.execute("select id, book_id, vol, start_page, sanad_text, hadith_text from sanad order by book_id, vol").fetchall()
    for sid, book_id, vol, page, stext, matn in rows:
        key = (f"angel{book_id:05d}" if book_id is not None else "", vol or 0)
        if key not in cache:
            if len(cache) > 8:
                cache.clear()
            by: Dict[int, list] = defaultdict(list)
            for hid, p, m, sn in vc.execute("select id, page, matn, sanad from hadith where book=? and vol=?", (key[0], str(key[1]))):
                try:
                    by[int(p)].append((int(hid) - NOOR_OFFSET, tokens(m), tokens(sn)))
                except (TypeError, ValueError):
                    continue
            cache[key] = by
        cands = [] if page is None else [x for p in (page - 1, page, page + 1) for x in cache[key].get(p, [])]
        if not cands:
            st["no_page"] += 1
            continue
        dm, ds = tokens(matn), tokens(stext)

        def best_isnad(hid):
            """Noor's isnad of the record whose written names are found best in Dirayah's isnad text."""
            got = [(_shares(t, ds), k) for k, (t, _) in noor.get(hid, {}).items() if t]
            return max(got) if got else None

        def isnad_of(hid):
            b = best_isnad(hid)
            return b[1] if b and b[0][0] >= MIN_ISNAD else None
        if len(dm) >= MIN_MATN:
            how = "matn"
            sc = [_shares(dm, nm) + (hid,) for hid, nm, ns in cands]
        elif any(hid in noor for hid, _, _ in cands):
            how = "isnad"                              # a short matn («مثله»): the names of the record's isnads decide
            sc = []
            for hid, _, _ in cands:
                b = best_isnad(hid)
                if b:
                    sc.append(b[0] + (hid,))
        else:
            how = "sanad"                              # no linked isnad on the page: the written isnad of v02 decides
            sc = [_shares(ns, ds) + (hid,) for hid, nm, ns in cands]
        res, top = _decide(sc)
        st[f"{res}_{how}"] += 1
        if top:
            upd.append((top[2], isnad_of(top[2]), how, round(top[0], 3), sid))
    c.executemany("update sanad set noor_hadith_id=?, noor_sanad_idx=?, link_how=?, link_score=? where id=?", upd)
    vc.close()
    log(f"  records: {dict(st)} of {len(rows)} isnads")
    return st


# ---------------------------------------------------------------------------
# linking persons
# ---------------------------------------------------------------------------
def aligned_pairs(c: sqlite3.Connection, narrators_db: str) -> Counter:
    """(noor_id, dirayah_id) -> number of aligned positions: each linked Dirayah isnad against the Noor isnad of
    its record chosen by the written names (``noor_sanad_idx``)."""
    noor = noor_isnads(narrators_db)
    dir_: Dict[int, list] = defaultdict(list)
    for sid, ids, is_ravi, torog, atf in c.execute("select sanad_id, ravi_ids, is_ravi, is_torog, is_by_atf from slot order by sanad_id, pos"):
        # the written chain only: the route to the book (is_torog, from the author's mashikha) is not in Noor's isnad
        if is_ravi == 1 and ids and torog != 1:
            v = json.loads(ids)
            dir_[sid].append((v[0] if len(v) == 1 else ("group",) + tuple(v), atf == 1))
    pairs: Counter = Counter()
    for sid, hid, sidx in c.execute("select id, noor_hadith_id, noor_sanad_idx from sanad where noor_sanad_idx is not null"):
        a, d = noor[hid][sidx][1], dir_.get(sid) or []
        if not a or not d:
            continue
        b = [x for x, _ in d]
        # parallel narrators («X و Y»): an atf entity and the one it is joined to; their order is free
        par = [d[k][1] or (k + 1 < len(d) and d[k + 1][1]) for k in range(len(d))]
        for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
                for k in range(i2 - i1):
                    x, y = a[i1 + k], b[j1 + k]
                    if x is None or isinstance(y, tuple) or (tag == "replace" and par[j1 + k]):
                        continue
                    pairs[(x, y)] += 1
    return pairs


def _name_parts(name: str) -> Tuple[List[str], Optional[str], Optional[str]]:
    """(tokens, first name, father's name) of «X بن [أبي] Y ...»; «أبي العلاء» and «العلاء» are one father."""
    t = [bare(x) for x in norm_name((name or "").replace("-", " ")).replace("ابن ", "بن ").split()]
    first = t[0] if t else None
    father = None
    if "بن" in t:
        k = t.index("بن") + 1
        if k < len(t) and t[k] == "ابو":
            k += 1
        father = t[k] if k < len(t) else None
    return t, first, father


def name_conflict(a: str, b: str) -> bool:
    """The two databases name the person differently: another first name or another father. A kunya form
    («أبو مطر الجهني») found inside the other name is no conflict."""
    ta, fa, pa = _name_parts(a)
    tb, fb, pb = _name_parts(b)
    if not ta or not tb:
        return False
    for x, y in ((ta, tb), (tb, ta)):
        if x[0] == "ابو" and len(x) > 1 and any(y[i] == "ابو" and y[i + 1] == x[1] for i in range(len(y) - 1)):
            return False
    return fa != fb or bool(pa and pb and pa != pb)


def link_persons(c: sqlite3.Connection, narrators_db: str, log=print) -> Counter:
    pairs = aligned_pairs(c, narrators_db)
    nc = sqlite3.connect(f"file:{narrators_db}?mode=ro&immutable=1", uri=True)
    nname = dict(nc.execute("select ravi_id, name from ravi"))
    nc.close()
    dname = dict(c.execute("select id, title from ravi"))
    tot_n: Counter = Counter()
    tot_d: Counter = Counter()
    for (a, b), v in pairs.items():
        tot_n[a] += v
        tot_d[b] += v
    by_n: Dict[int, Counter] = defaultdict(Counter)
    for (a, b), v in pairs.items():
        by_n[a][b] = v
    rows, st = [], Counter()
    for (a, b), v in pairs.items():
        sn, sd = v / tot_n[a], v / tot_d[b]
        same_name = int(a in nname and b in dname and norm_name(nname[a] or "") == norm_name(dname[b] or ""))
        conflict = not same_name and name_conflict(nname.get(a), dname.get(b))
        tier = "certain" if v >= CERTAIN_N and sn >= CERTAIN_SHARE and sd >= CERTAIN_SHARE and not conflict else "suggested"
        if tier == "suggested" and by_n[a].most_common(1)[0][0] != b and v < 3:
            continue                                   # a stray position next to the person's usual id: not worth a row
        ev = dict(noor_name=nname.get(a), dirayah_name=dname.get(b), noor_positions=tot_n[a], dirayah_positions=tot_d[b], name_conflict=conflict,
                  noor_other=[[x, n] for x, n in by_n[a].most_common(4) if x != b])
        rows.append((a, b, v, round(sn, 4), round(sd, 4), int(a == b), same_name, tier, json.dumps(ev, ensure_ascii=False)))
        st[tier] += 1
    # same id and same name, with no record evidence on either side: a suggestion, never more
    for a, nm in nname.items():
        if a in dname and a not in tot_n and a not in tot_d and norm_name(nm or "") == norm_name(dname[a] or ""):
            rows.append((a, a, 0, None, None, 1, 1, "suggested",
                         json.dumps(dict(noor_name=nm, dirayah_name=dname[a], note="same id and name, no shared record"), ensure_ascii=False)))
            st["suggested_same_id_name"] += 1
    c.executemany("insert or replace into person_link values (?,?,?,?,?,?,?,?,?)", rows)
    log(f"  persons: {dict(st)}")
    return st


# ---------------------------------------------------------------------------
# independent verification
# ---------------------------------------------------------------------------
def _first(s: Optional[str]) -> str:
    return (norm_name(s or "").split() or [""])[0]


def _book(s: Optional[str]) -> str:
    return norm_name((s or "").replace("ي", "ی")).replace(" ", "")


def _page_key(book: str, vol: Optional[int], page: int) -> tuple:
    # Noor cites اختيار معرفة الرجال by its part («رجال الكشي6/831»), Dirayah by the volume of the edition: page only
    return (book, 0 if book.startswith("اختيارمعرفه") else (vol or 1), page)


def verify(c: sqlite3.Connection, narrators_db: str) -> dict:
    """Check the person links against what the two databases say about a person, which the links never used.

    Jami' al-Ahadith's narrator records (``narrators.sqlite`` ``ravi``, from Noor's SanadInfo) carry CRCIS's own
    evaluation, the one Dirayah computes: school (``mazhab`` / ``religion_result``), grade (first word: Noor
    writes «ضعيف» where Dirayah writes «ضعيف مصرح»), and the Imams of the narrator's class (``tabaqa_json`` /
    ``class_summary``). Noor also cites the rijal pages that grade a narrator (``citation``); Dirayah locates his
    entries (``tarjama``, ±1 page). For each tier: how many pairs can be compared, how many agree, how many
    contradict; the same for random pairs (the baseline) and, for links between different ids, for the
    same-numbered Dirayah id (the control)."""
    nc = sqlite3.connect(f"file:{narrators_db}?mode=ro&immutable=1", uri=True)
    N = {}
    for rid, mz, gr, tab in nc.execute("select ravi_id, mazhab, grade, tabaqa_json from ravi"):
        N[rid] = (norm_name(mz or ""), _first(gr), {norm_name(x["text"]) for x in json.loads(tab or "[]") if x.get("text")})
    cit: Dict[int, set] = defaultdict(set)
    for rid, b, v, p in nc.execute("select ravi_id, book_raw, vol, page from citation where page is not null"):
        cit[rid].add(_page_key(_book(b), v, p))
    nc.close()
    D = {i: (norm_name(rr or ""), _first(cr), {norm_name(cs)} if cs else set())
         for i, rr, cr, cs in c.execute("select id, religion_result, credit_result, class_summary from ravi")}
    tar: Dict[int, set] = defaultdict(set)
    books = set()
    for rid, b, v, sp, ep in c.execute("select ravi_id, book, vol, start_page, end_page from tarjama where start_page is not null"):
        books.add(_book(b))
        for p in range(sp - 1, (ep or sp) + 2):
            tar[rid].add(_page_key(_book(b), v, p))

    def check(pairs):
        out = Counter()
        for x, y in pairs:
            ok = bad = 0
            if x in N and y in D:
                a, b = N[x], D[y]
                for k, (u, w) in enumerate(zip(a, b)):
                    if u and w:
                        same = bool(u & w) if k == 2 else u == w
                        out[("school", "grade", "class")[k] + ("_ok" if same else "_bad")] += 1
                        ok += same
                        bad += not same
            cs = {t for t in cit.get(x, ()) if t[0] in books}
            if cs and y in tar:
                out["pages_ok" if cs & tar[y] else "pages_none"] += 1
                ok += bool(cs & tar[y])
            out["pairs"] += 1
            out["checked"] += bool(ok or bad)
            out["contradicted"] += bad > 0
        return dict(out)
    rows = c.execute("select noor_id, dirayah_id, tier, same_id from person_link").fetchall()
    res = {}
    for name, sel in (("certain_same_id", lambda t, s: t == "certain" and s), ("certain_other_id", lambda t, s: t == "certain" and not s),
                      ("suggested", lambda t, s: t == "suggested")):
        res[name] = check([(a, b) for a, b, t, s in rows if sel(t, s)])
    res["control_same_number"] = check([(a, a) for a, b, t, s in rows if t == "certain" and not s])
    import random
    rnd = random.Random(0)
    ds = sorted(D)
    res["random"] = check([(a, rnd.choice(ds)) for a in sorted(N)[:5000]])
    return res


def run(db_path: str = DB_PATH, export: str = EXPORT, out: str = OUT, v02: str = V02_DB, log=print) -> dict:
    t0 = time.time()
    tmp = out + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    c = sqlite3.connect(tmp)
    c.executescript(SCHEMA)
    load_ravi(c, export)
    load_tarjama(c, export)
    load_sanad(c, export)
    c.commit()
    log(f"  loaded ({time.time() - t0:.0f}s)")
    rec = link_records(c, v02, db_path, log)
    c.commit()
    per = link_persons(c, db_path, log)
    c.executescript("""
        create index sanad_noor on sanad(noor_hadith_id);
        create index slot_title on slot(title_id);
        create index tarjama_ravi on tarjama(ravi_id);
        create index person_link_dirayah on person_link(dirayah_id);
    """)
    ver = verify(c, db_path)
    log(f"  verification: {json.dumps(ver)}")
    manifest = {}
    mp = os.path.join(export, "manifest.json")
    if os.path.exists(mp):
        manifest = json.load(open(mp, encoding="utf-8")).get("source", {})
    meta = dict(built=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), source=json.dumps(manifest, ensure_ascii=False),
                records=json.dumps(rec), persons=json.dumps(per), verification=json.dumps(ver),
                **{f"n_{t}": c.execute(f"select count(*) from {t}").fetchone()[0] for t in ("ravi", "title", "tarjama", "sanad", "slot")})
    c.executemany("insert into meta values (?,?)", [(k, str(v)) for k, v in meta.items()])
    c.commit()
    c.execute("vacuum")
    c.close()
    os.replace(tmp, out)
    log(f"-> {out} ({time.time() - t0:.0f}s)")
    return meta
