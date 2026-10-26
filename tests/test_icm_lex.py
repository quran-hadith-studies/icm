"""Root-aware search: the language of the search (icm/query.py), the lexical index (icm/lex.py, built here from a few
hadiths), the finder, root matching in the scoring (icm/similarity.py + lex.search_key) and the queries of search._generate_lex."""
import os
import sqlite3
import sys
from contextlib import contextmanager

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm import config, corpus, lex, query, search
from icm.build import lex as build_lex
from icm.build.roots import root_key
from icm.similarity import align_clauses, match_key

DOCS = {                                    # v02 id -> (matn, sanad)
    1: ("أقيموا الصلاة وآتوا الزكاة", "حدثنا زرارة عن أبي جعفر"),
    2: ("يقيم الصلاة ويؤتي الزكاة", "حدثنا محمد عن أبي عبد الله"),
    3: ("الصلاة عماد الدين", ""),
    4: ("لا تقبل صلاة بغير طهور", "حدثنا زرارة"),
    5: ("من زكى نفسه فقد أفلح وأقام الصلاة", ""),
    6: ("الكذب يهدي إلى الفجور ويقيم الصلاة", "حدثنا زرارة"),
}
ROOTS = {"اقیموا": "ق-و-م", "یقیم": "ق-ي-م", "اقام": "ق-و-م", "الصلاه": "ص-ل-و", "صلاه": "ص-ل-و", "واتوا": "ا-ت-ي", "ویوتی": "ا-ت-ي",
         "الزکاه": "ز-ک-و", "زکی": "ز-ک-ی", "الکذب": "ک-ذ-ب", "الفجور": "ف-ج-ر", "طهور": "ط-ه-ر", "عماد": "ع-م-د", "الدین": "د-ی-ن"}


@contextmanager
def toy_index(tmp_path, stop_df=None):
    path = str(tmp_path / "lex.sqlite")
    con = sqlite3.connect(path)
    build_lex.create_schema(con)
    roots = {t: root_key(r) for t, r in ROOTS.items()}
    for t, k in roots.items():
        con.execute("insert into term_root values (?,?,?,?,?,?)", (t, k, "", "", "toy", 1))
    con.execute("insert into term_alt values ('زکی','ازک',1)")
    rows = [build_lex.prepare(i, "b1", m, s, roots) for i, (m, s) in DOCS.items()]
    build_lex.index_rows(con, "v02_lex", rows)
    build_lex.index_rows(con, "kg_lex", [build_lex.prepare(100, "b1", DOCS[2][0], "", roots)])
    con.executemany("insert into book_tok values (?,?,?)", [("b1", "v02", "kafi"), ("b1", "kg", "7")])
    build_lex.fill_stats(con)
    con.commit()
    con.close()
    old = config.LEX_DB, dict(lex.STOP_DF), config.KG_DB, config.BOOKS_META
    config.LEX_DB, config.KG_DB, config.BOOKS_META = path, "/nonexistent/kg.sqlite", "/nonexistent/books_meta.json"
    corpus._books_meta = None
    if stop_df:
        lex.STOP_DF.update(stop_df)
    _reset()
    try:
        yield
    finally:
        config.LEX_DB, config.KG_DB, config.BOOKS_META = old[0], old[2], old[3]
        corpus._books_meta = None
        lex.STOP_DF.update(old[1])
        _reset()


def _reset():
    corpus._local.cs = {}
    lex._root_cache.clear()
    lex._root_df_one.cache_clear()
    lex.same_root.cache_clear()
    lex._titles = None


def ids(r):
    return sorted(r["ids"])


def test_root_key_makes_weak_letters_one():
    assert root_key("ق-و-م") == root_key("ق-ي-م") == root_key("قوم") == root_key("ق.#.م") == "قوم"
    assert root_key("ص ل و") == "صلو" and root_key("ا") == ""


def test_parser_reads_the_language_and_says_what_is_wrong():
    q = query.parse('صلاه "اقیموا الصلاه" -کذب ریشه:زکو سند:زراره صلا* "صلاه زکاه"~5 =الصلاه صلاه|زکاه کتاب:کافی')
    kinds = [(i.kind, i.neg) for i in q.items]
    assert kinds == [("word", False), ("phrase", False), ("word", True), ("root", False), ("word", False), ("word", False), ("near", False),
                     ("word", False), ("alt", False), ("book", False)]
    assert q.items[4].field == "sanad" and q.items[5].prefix and q.items[7].exact and q.items[6].near == 5
    for bad in ("", "-کذب", '"abc', "x:y", '"a b"~99', '"a"~3'):
        with pytest.raises(query.QueryError):
            query.parse(bad)
    assert query.parse("صلاه | زکاه").items[0].kind == "alt"            # spaces around | do not matter


def test_the_word_search_finds_clitic_forms_and_roots_find_other_inflections(tmp_path):
    with toy_index(tmp_path):
        got = lambda s, corp="v02": ids(lex.search(corp, query.to_fts(query.parse(s), corp), 50))
        assert got("الصلاه") == [1, 2, 3, 4, 5, 6]                  # «صلاة» of doc 4 is the same word without الـ
        assert got("=الصلاه") == [1, 2, 3, 5, 6]                    # the exact form only
        assert got("صلا*") == [4]                                   # words that begin so (the others have الـ)
        assert got("اقیموا") == [1]                                 # not «یقیم»
        assert got("ریشه:قوم") == [1, 2, 5, 6]                      # every form of the root: اقیموا یقیم اقام
        assert got("ریشه:یقیم") == [1, 2, 5, 6]                     # a word may be given for its root
        assert got("ریشه:قوم ریشه:زکو") == [1, 2, 5]
        assert got("ریشه:قوم -ریشه:زکو") == [6]                      # not
        assert got("الصلاه -الکذب") == [1, 2, 3, 4, 5]
        assert got('"اقیموا الصلاه"') == [1]                         # exact phrase
        assert got('"الصلاه الزکاه"~3') == [1, 2]                     # within 3 words
        assert got("سند:زراره") == [1, 4, 6]
        assert got("سند:زراره متن:الصلاه") == [1, 4, 6]
        assert got("کتاب:kafi ریشه:قوم") == [1, 2, 5, 6]
        assert got("کتاب:ناموجود الصلاه") == []
        assert got("الصلاه|الکذب") == [1, 2, 3, 4, 5, 6]
        assert got("ریشه:یقیم", "kg") == [100]
        with pytest.raises(query.QueryError):
            query.to_fts(query.parse("ریشه:ناموجودواژه"), "v02")


def test_search_is_ranked_and_counts(tmp_path):
    with toy_index(tmp_path):
        e = query.to_fts(query.parse("ریشه:قوم"), "v02")
        r = lex.search("v02", e, 2, rank=True, scores=True)
        assert len(r["ids"]) == 2 and r["capped"] and r["ranked"] and r["scores"][0] <= r["scores"][1]
        assert lex.count("v02", e) == (4, True)
        assert lex.search("v02", e, 10, rank="auto")["ranked"] is False       # at most the limit: all of them, in index order
        assert lex.search("v02", e, 1, rank="auto")["ranked"] is True


def test_finder_orders_by_closeness_and_marks_the_words(tmp_path):
    from icm import finder
    with toy_index(tmp_path):
        # the three-letter words stand in the toy corpus's real records only through corpus.get_many: fake it
        recs = {f"v02:{i}": dict(wid=f"v02:{i}", corpus="v02", hid=i, book="b", work="w", title="t", author="a", death=None, tradition="shia",
                                 vol=None, page=None, no=None, matn=m, sanad=s, translation="") for i, (m, s) in DOCS.items()}
        recs["kg:100"] = dict(recs["v02:2"], wid="kg:100", corpus="kg", hid=100)           # the same text as v02:2
        old = corpus.get_many
        corpus.get_many = lambda w: {x: recs[x] for x in w if x in recs}
        try:
            r = finder.find("ریشه:قوم الصلاه", limit=10)
        finally:
            corpus.get_many = old
        assert r["mode"] == "lexical" and r["exact"] and r["total"] >= 4
        wids = [h["wid"] for h in r["hits"]]
        assert set(wids) >= {"v02:1", "v02:2", "v02:5", "v02:6"}
        top = r["hits"][0]
        assert top["marks"] and all(len(m) == 3 for m in top["marks"])       # [start, end, 0] ranges of the matn
        assert any(h["copies"] for h in r["hits"])                            # kg:100 is v02:2's text: one hit with a copy


def test_words_of_one_root_are_one_word_when_texts_are_compared(tmp_path):
    seed = "اقیموا الصلاه واتوا الزکاه".split()
    cand = "یقیم الصلاه ویوتی الزکاه".split()
    plain = align_clauses(seed, [(0, 4)], cand)
    with toy_index(tmp_path):
        rooted = align_clauses(seed, [(0, 4)], cand, lex.search_key)
        assert rooted["m"] > plain["m"] and rooted.get("r", 0) >= 2 and plain.get("r", 0) == 0
        assert rooted["parts"][0]["state"] == "present"
        assert lex.same_root("اقیموا", "یقیم") and not lex.same_root("اقیموا", "الصلاه")
        assert lex.loose_key("یقیم") == lex.loose_key("اقیموا") and lex.loose_key("الصلاه") == lex.loose_key("صلاه")
        # a root in too many documents is not a distinctive one: its words keep their own keys, unless the texts are aligned (loose)
        lex.STOP_DF["v02"] = 1
        lex._root_df_one.cache_clear()
        lex.same_root.cache_clear()
        assert lex.search_key("اقیموا") == match_key("اقیموا") and not lex.same_root("اقیموا", "یقیم") and lex.same_root("اقیموا", "یقیم", True)


def test_queries_of_a_part_ask_words_and_roots_and_the_search_finds_the_other_inflection(tmp_path):
    with toy_index(tmp_path):
        found, queries = {}, []
        def add(w, sig):
            found.setdefault(w, set()).add(sig)
        part = "اقیموا الصلاه واتوا الزکاه".split()
        p = dict(search.DEFAULTS, lex=True, corpora=["v02", "kg"])
        search._generate_lex("v02:1", part, [(0, 0, len(part))], p, 1, add, queries)
        kinds = {(q["kind"], q["corpus"]) for q in queries}
        assert {("root", "v02"), ("rnear", "v02"), ("rphrase", "v02")} <= kinds
        assert found["v02:2"] & {"q:root", "q:rnear", "q:rphrase"}                 # «یقیم الصلاة ویؤتی الزکاة» by roots alone
        assert "kg:100" in found
        assert all(not q["capped"] for q in queries)


def _client():
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from icm.api import make_router
    a = FastAPI()
    a.include_router(make_router(lambda req: None))
    return TestClient(a)


def _fake_records(monkeypatch):
    recs = {f"v02:{i}": dict(wid=f"v02:{i}", corpus="v02", hid=i, book="b", work="w", title="t", author="a", death=None, tradition="shia",
                             vol=None, page=None, no=None, matn=m, sanad=s, translation="") for i, (m, s) in DOCS.items()}
    monkeypatch.setattr(corpus, "get_many", lambda w: {x: recs[x] for x in w if x in recs})
    monkeypatch.setattr(corpus, "get", lambda w: recs.get(w))


def test_the_api_finds_with_the_language_and_says_what_is_wrong(tmp_path, monkeypatch):
    c = _client()
    with toy_index(tmp_path):
        _fake_records(monkeypatch)
        info = c.get("/icm/api/search-info").json()
        assert info["lex"] is True and info["semantic"] is False
        r = c.get("/icm/api/find", params={"q": "ریشه:قوم -ریشه:زکو", "limit": 5})
        assert r.status_code == 200 and [h["wid"] for h in r.json()["hits"]] == ["v02:6"] and r.json()["total"] >= 1
        bad = c.get("/icm/api/find", params={"q": '"اقیموا'})
        assert bad.status_code == 400 and "گیومه" in bad.json()["detail"]
        assert c.get("/icm/api/find", params={"q": "x", "mode": "nonsense"}).status_code == 400
        sem = c.get("/icm/api/find", params={"q": "اقیموا الصلاه", "mode": "semantic"}).json()
        assert sem["unavailable"] is True and sem["hits"] == []                    # no semantic index here: said, not raised
        # the older endpoint (the new-project box) answers a list, and a query it cannot read is searched as words
        from icm import finder
        seen = []
        monkeypatch.setattr(finder, "_legacy", lambda q, limit: seen.append(q) or dict(hits=[]))
        old = c.get("/icm/api/search", params={"q": '"اقیموا الصلاه', "limit": 5})
        assert old.status_code == 200 and old.json() == [] and seen == ['"اقیموا الصلاه']
        assert [h["wid"] for h in c.get("/icm/api/search", params={"q": "ریشه:قوم", "limit": 10}).json()][:1] != []


def test_a_search_pressed_with_the_index_has_its_own_key_and_the_others_keep_theirs(tmp_path, monkeypatch):
    from icm import pipeline
    old = dict(n=1, seeds=[], part_query=None, clauses=None, join=None, keep=[], legacy=False)
    k0 = pipeline._ret_key("v02:1", old)
    assert pipeline._ret_key("v02:1", dict(old, lex=1)) != k0 and pipeline._ret_key("v02:1", dict(old, lex=1, sem="m")) != pipeline._ret_key("v02:1", dict(old, lex=1))
    assert pipeline._ret_key("v02:1", dict(old)) == k0                                       # (no lex: the key is what it always was)
