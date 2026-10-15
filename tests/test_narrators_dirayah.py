"""narrators.dirayah: Dirayat al-Noor 3 export → dirayah.sqlite, record and person links, on a fake export.

    python3 -m pytest tests/test_narrators_dirayah.py -q
"""
import gzip
import json
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from narrators import dirayah  # noqa: E402

MATN = "سألت أبا عبد الله عليه السلام عن رجل نسي أن يزور البيت حتى أصبح"
OTHER = "قال رسول الله صلى الله عليه و آله الصلاة عمود الدين"
TAIL = ["", "فقال لا بأس", "فقال يعيد الطواف", "فقال عليه دم شاة"]          # four different hadiths on one page


def _jsonl(path, rows):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _ent(ids, is_ravi=1):
    v = [f"{i},1" for i in ids]
    return dict(CriterionRaviId=v[0] if len(v) == 1 else v, IsRavi=str(is_ravi), RaviListId="9")


def _fixture(d):
    exp = os.path.join(d, "export")
    os.makedirs(exp)
    # Dirayah persons: 10 and 11 as in Noor; Dirayah 30 is Noor's 20 (another id); 40 shares Noor 40's id, not the person
    names = {10: "حماد بن عيسى الجهني", 11: "حريز بن عبد الله", 30: "عبد الله بن جبلة الكناني", 40: "عمر بن جبلة الأحمسي",
             50: "زرارة بن أعين"}
    _jsonl(os.path.join(exp, "ravi.jsonl.gz"),
           [dict(_id=f"{i};CriterionRaviId", _source=dict(RaviTitle=n, SandCount="5"),
                 fields=dict(CriterionRaviId=[i], CriterionStatus=["عنوان معیار"], CreditResult=["ثقة"]),
                 nested={"CreditThamanieh": [{"CreditThamanieh.Root": ["ممدوح"], "CreditThamanieh.Child": ["ثقة"]}]})
            for i, n in names.items()] + [dict(_id="9;RaviListId", _source=dict(RaviTitle="حماد", SandCount="0"),
                                               fields=dict(RaviListId=[9], CriterionStatus=["عنوان اصلی"]))])
    _jsonl(os.path.join(exp, "dtarajem.jsonl.gz"), [dict(_id="t1", _source=dict(CriterionRaviId="10", RaviListId="9", BookId="1471",
                                                                              BookName="رجال الطوسي", Vol="1", StartPage="187"))])
    sanads = []
    for k in range(4):                                 # four isnads of four hadiths on page 5: chain 10, 11, 30, 40 (+ group)
        sanads.append(dict(_id=str(k), _source=dict(BookId="1346", Vol="4", StartPage="5",
                                                     Entities=[_ent([10]), _ent([0], is_ravi=0), _ent([11]), _ent([30]), _ent([40]),
                                                               _ent([50, 51])]),
                           fields=dict(SanadId=[100 + k], BookTitle=["تهذیب الأحکام"], SanadEvaluation=["صحیح"], IsMain=[1]),
                           _decoded=dict(SanadText="محمد بن الحسن عن حماد عن حريز عن عبد الله بن جبلة عن عمر عن عدة من أصحابنا", Hadith=f"{MATN} {TAIL[k]}")))
    sanads.append(dict(_id="x", _source=dict(BookId="1346", Vol="4", StartPage="9", Entities=[_ent([10])]),
                       fields=dict(SanadId=[200]), _decoded=dict(SanadText="x", Hadith=OTHER)))   # no Noor hadith on page 9
    _jsonl(os.path.join(exp, "sanad.jsonl.gz"), sanads)
    v02 = os.path.join(d, "v02.sqlite")
    c = sqlite3.connect(v02)
    c.execute("create table hadith(id, book, vol, page, matn, sanad)")
    for k in range(4):
        c.execute("insert into hadith values (?,?,?,?,?,?)", (800000000 + 1000 + k, "angel01346", "4", "5",
                                                              f"{MATN} {TAIL[k]}", "عنه عن حماد"))
    c.execute("insert into hadith values (?,?,?,?,?,?)", (800000000 + 2000, "angel01346", "4", "5", OTHER, "عنه"))
    c.commit()
    c.close()
    nd = os.path.join(d, "narrators.sqlite")
    c = sqlite3.connect(nd)
    c.execute("create table ravi(ravi_id integer primary key, name text, mazhab text, grade text, tabaqa_json text)")
    c.executemany("insert into ravi values (?,?,?,?,?)", [(10, "حماد بن عيسى الجهني", None, "ثقة", None),
                                                           (11, "حريز بن عبد الله السجستاني", None, None, None),
                                                           (20, "عبد الله بن جبلة الكناني", None, "ثقة", None), (40, "عبد الله بن جبلة", None, None, None),
                                                           (60, "زرارة بن أعين", None, None, None)])
    c.execute("create table citation(ravi_id, book_raw, vol, page)")
    c.execute("insert into citation values (10, 'رجال الطوسي', 1, 188)")       # Dirayah's entry for 10 is on page 187
    c.execute("create table slot(hadith_id, sanad_idx, pos, title, ravi_id)")
    chain = [(10, "حماد"), (11, "حريز"), (20, "عبد الله بن جبلة"), (60, "عمر"), (70, "عدة من أصحابنا")]
    for k in range(4):
        for pos, (rid, t) in enumerate(chain):        # Noor: its 20 stands where Dirayah has 30, its 60 where 40
            c.execute("insert into slot values (?,?,?,?,?)", (1000 + k, 0, pos, t, rid))
        c.execute("insert into slot values (?,?,?,?,?)", (1000 + k, 1, 0, "علي بن إبراهيم", 99))   # another isnad of the record
    c.commit()
    c.close()
    return exp, v02, nd


def test_build_links_records_and_persons():
    d = tempfile.mkdtemp(prefix="dirayah_")
    try:
        exp, v02, nd = _fixture(d)
        out = os.path.join(d, "dirayah.sqlite")
        dirayah.run(nd, export=exp, out=out, v02=v02, log=lambda *a: None)
        c = sqlite3.connect(out)
        assert c.execute("select title, credit_result, credit_thamanieh from ravi where id=10").fetchone() == (
            "حماد بن عيسى الجهني", "ثقة", json.dumps([{"Root": "ممدوح", "Child": "ثقة"}], ensure_ascii=False))
        assert c.execute("select title from title where id=9").fetchone() == ("حماد",)
        assert c.execute("select book, start_page from tarjama where ravi_id=10").fetchone() == ("رجال الطوسي", 187)
        links = dict(c.execute("select id, noor_hadith_id from sanad"))
        assert links == {100: 1000, 101: 1001, 102: 1002, 103: 1003, 200: None}      # by matn, page by page
        assert {x for (x,) in c.execute("select noor_sanad_idx from sanad where noor_hadith_id is not null")} == {0}   # by names
        assert json.loads(c.execute("select ravi_ids from slot where sanad_id=100 and pos=5").fetchone()[0]) == [50, 51]   # a group
        tiers = {(a, b): t for a, b, t in c.execute("select noor_id, dirayah_id, tier from person_link")}
        assert tiers[(10, 10)] == "certain" and tiers[(11, 11)] == "certain"
        assert tiers[(20, 30)] == "certain"                    # another id, the same person: the records decide
        assert (40, 40) not in tiers                           # the same id, another person: never linked by id
        assert tiers[(60, 40)] == "suggested"                  # always at the same place, but the names disagree
        assert not any(a == 70 for a, b in tiers)             # Noor's 70 stands opposite a group, not a person
        ver = json.loads(c.execute("select v from meta where k='verification'").fetchone()[0])
        assert ver["certain_same_id"]["grade_ok"] == 1 and ver["certain_same_id"]["pages_ok"] == 1
        assert ver["certain_other_id"] == dict(pairs=1, checked=1, contradicted=0, grade_ok=1)
        c.close()
    finally:
        shutil.rmtree(d)


def test_wiki_joins_only_certain_links():
    from narwiki import dirayah as wd, seed, store
    d = tempfile.mkdtemp(prefix="dirayah_wiki_")
    try:
        exp, v02, nd = _fixture(d)
        out = os.path.join(d, "dirayah.sqlite")
        dirayah.run(nd, export=exp, out=out, v02=v02, log=lambda *a: None)
        wiki = os.path.join(d, "wiki.sqlite")
        c = store.connect(wiki)
        with c:
            for rid, name in ((10, "حماد بن عيسى الجهني"), (11, "حريز بن عبد الله السجستاني"), (20, "عبد الله بن جبلة الكناني"),
                              (60, "زرارة بن أعين")):
                seed.insert_record(c, dict(kind="noor", ext_id=str(rid), display_name=name, n_narr=1, fields=[("name", name, "noor")]))
        c.close()
        meta = wd.run(wiki, src=out, log=lambda *a: None)
        c = store.connect(wiki, readonly=True)
        own = lambda k, e: store.by_ext(c, k, e)
        assert own("dirayah", 30) == own("noor", 20)              # certain, another id: one person
        assert own("dirayah", 10) == own("noor", 10) and own("dirayah", 11) == own("noor", 11)
        assert own("dirayah", 40) != own("noor", 60)               # same places, names disagree: a suggestion
        s = c.execute("select kind, status from merge_suggestion").fetchall()
        assert [tuple(x) for x in s] == [("link_dirayah", "proposed")]
        assert own("dirayah", 50) is not None and own("dirayah", 50) not in {own("noor", r) for r in (10, 11, 20, 60)}
        f = {(r["field"], r["value"]) for r in c.execute("select field, value from person_field where person_id=? and source='dirayah'",
                                                         (own("noor", 10),))}
        assert ("grade", "ثقة") in f and ("note", "شرح حال: رجال الطوسي 1/187") in f
        assert meta["dirayah_joined"] == 3
        c.close()
        assert wd.run(wiki, src=out, log=lambda *a: None)["dirayah_joined"] == 0   # re-running adds nothing twice
    finally:
        shutil.rmtree(d)


def test_name_conflict_and_ids():
    assert dirayah.name_conflict("إبراهيم بن إسحاق الخارفي", "إبراهيم بن زياد الخارفي")
    assert dirayah.name_conflict("عمر بن نهيك", "عمرو بن نهيك")
    assert not dirayah.name_conflict("یحیی بن العلاء الرازي", "يحيى بن أبي العلاء الرازي")
    assert not dirayah.name_conflict("أبو مطر الجهني", "عمرو بن عبد الله أبو مطر البصري")
    assert not dirayah.name_conflict("غير-يونس بن عبد الرحمن", "غير يونس بن عبد الرحمن")
    assert dirayah.ravi_ids("5102,1") == [5102] and dirayah.ravi_ids(["2107,17", "5485,17"]) == [2107, 5485]


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_") and callable(f):
            f()
            print("ok", name)
