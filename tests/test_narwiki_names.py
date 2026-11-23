"""The wiki's side of written names: a person's forms, the corpus-wide patterns, the reviewers' decisions and their publication to ICM.

    ~/miniforge3/envs/dev2/bin/python3 -m pytest tests/test_narwiki_names.py -q
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from icm import config as icm_config, identity, names  # noqa: E402
from icm.build import names as build_names  # noqa: E402
from narwiki import api, config, store  # noqa: E402
from narwiki import names as nwn  # noqa: E402
from test_icm_names import _fake_sources, _family_chains  # noqa: E402
from test_narwiki_version import world  # noqa: E402

H = {"X-NARWIKI": "1"}


@pytest.fixture
def env():
    d = tempfile.mkdtemp(prefix="narnames_")
    c, path, p = world(d)
    c.close()
    keep = (config.DB, config.VERSIONS_DIR, config.narrators_db, config.kg_db, config.dirayah_db,
            icm_config.NARRATORS_DB, icm_config.KG_SRC, icm_config.NAMES_DB, icm_config.NAMES_WIKI_DB, icm_config.V02_DB, icm_config.BOOKS_META,
            build_names.MIN_FIRED, build_names.FOLDS, os.environ.get("NARWIKI_NAMES_PATTERNS"))
    config.DB, config.VERSIONS_DIR = path, os.path.join(d, "versions")
    config.narrators_db = config.kg_db = config.dirayah_db = lambda: os.path.join(d, "none.sqlite")
    icm_config.NARRATORS_DB, icm_config.KG_SRC = _fake_sources(d)
    icm_config.NAMES_DB, icm_config.NAMES_WIKI_DB = os.path.join(d, "names.sqlite"), os.path.join(d, "names_wiki.sqlite")
    os.environ["NARWIKI_NAMES_PATTERNS"] = os.path.join(d, "name_patterns.sqlite")
    build_names.MIN_FIRED, build_names.FOLDS = 1, 2
    build_names.run(icm_config.NAMES_DB, log=lambda *a: None)
    names._local.c = False
    names._META.clear()
    names._WIKI.clear()
    api._inited.clear()
    app = FastAPI()
    app.include_router(api.make_router(lambda req: dict(username="ali", is_admin=True)))
    yield TestClient(app), d, p
    (config.DB, config.VERSIONS_DIR, config.narrators_db, config.kg_db, config.dirayah_db,
     icm_config.NARRATORS_DB, icm_config.KG_SRC, icm_config.NAMES_DB, icm_config.NAMES_WIKI_DB, icm_config.V02_DB, icm_config.BOOKS_META,
     build_names.MIN_FIRED, build_names.FOLDS, old_env) = keep
    if old_env is None:
        os.environ.pop("NARWIKI_NAMES_PATTERNS", None)
    else:
        os.environ["NARWIKI_NAMES_PATTERNS"] = old_env
    names._local.c = False
    names._META.clear()
    names._WIKI.clear()
    shutil.rmtree(d)


def _v02(d, rows):
    """a v02 file with Rezwan sanads, and the books file saying they are Imami"""
    p = os.path.join(d, "hadith.sqlite")
    c = sqlite3.connect(p)
    c.execute("create table hadith(id integer, book text, sanad text)")
    c.executemany("insert into hadith values (?,?,?)", rows)
    c.commit()
    c.close()
    b = os.path.join(d, "books_meta.json")
    json.dump({"rezwan1": dict(tradition="shia")}, open(b, "w"))
    icm_config.V02_DB, icm_config.BOOKS_META = p, b


def test_a_persons_written_names_come_from_the_names_file(env):
    t, d, p = env
    r = nwn.person_forms(["noor:11", "kg:11", "wiki:abc"])
    assert r["available"] and [x["k"] for x in r["items"]] == ["الزهري"] and r["items"][0]["n"] >= 2 and r["items"][0]["db"] == ["noor"]
    with store.connect(config.DB) as c:
        pid = store.add_person(c, "الزهري", [], "ali", ext=[("noor", "11")])
    j = t.get(f"/narrator/api/person/{pid}/names").json()
    assert j["available"] and j["items"][0]["k"] == "الزهري"


def test_patterns_list_the_reading_of_every_name_and_the_unknown_ones(env):
    t, d, p = env
    _v02(d, [(100000000001 + i, "rezwan1", "حدثنا معمر عن الزهري عن سالم عن فلان بن علان الغريب") for i in range(3)])
    meta = nwn.build_patterns(os.path.join(d, "name_patterns.sqlite"), "rezwan%", log=lambda *a: None, min_places=1, min_unknown=1)
    assert meta["hadiths"] == 3
    j = t.get("/narrator/api/names?status=all").json()
    got = {(x["k"], x["id"]): x for x in j["items"]}
    assert ("الزهري", "noor:11") in got and got[("الزهري", "noor:11")]["places"] == 3 and got[("الزهري", "noor:11")]["share"] == 1.0
    u = t.get("/narrator/api/names/unknown").json()
    assert "فلان بن علان الغريب" in [x["k"] for x in u["items"]]


def test_a_decision_is_kept_until_published_and_icm_reads_what_is_published(env):
    t, d, p = env
    _v02(d, [(100000000001, "rezwan1", "حدثنا معمر عن الزهري عن سالم")])
    nwn.build_patterns(os.path.join(d, "name_patterns.sqlite"), "rezwan%", log=lambda *a: None, min_places=1, min_unknown=1)
    assert t.post("/narrator/api/names/decide", json=dict(k="الزهري", id="noor:11", decision="approve")).status_code == 403      # no header
    assert t.post("/narrator/api/names/decide", json=dict(k="الزهري", id="bad", decision="approve"), headers=H).status_code == 400
    assert t.post("/narrator/api/names/decide", json=dict(k="الزهري", id="noor:11", decision="approve"), headers=H).json()["ok"]
    assert t.post("/narrator/api/names/decide", json=dict(k="معمر", id="noor:10", decision="reject"), headers=H).json()["ok"]
    j = t.get("/narrator/api/names?status=approve").json()
    assert [(x["k"], x["decision"]) for x in j["items"]] == [("الزهري", "approve")]
    assert names.wiki() == dict(approved={}, rejected=set())                                                                       # nothing published yet
    k0 = names.key()
    r = t.post("/narrator/api/names/publish", headers=H).json()
    assert r["approved"] == 1 and r["rejected"] == 1
    assert names.wiki() == dict(approved={"الزهري": "noor:11"}, rejected={("معمر", "noor:10")})
    assert names.key() != k0                                                                                                        # every project's identify is stale
    assert t.post("/narrator/api/names/decide", json=dict(k="الزهري", id="noor:11", decision="clear"), headers=H).json()["ok"]
    assert [x["k"] for x in t.get("/narrator/api/names?status=approve").json()["items"]] == []


def test_a_name_no_database_knows_becomes_a_person_approved_for_it(env):
    t, d, p = env
    r = t.post("/narrator/api/names/create", json=dict(k="فلان بن علان الغريب", name="فلان الغريب"), headers=H).json()
    assert r["ok"] and r["id"].startswith("wiki:")
    with store.connect(config.DB, readonly=True) as c:
        assert store.name_decisions(c)[("فلان بن علان الغريب", r["id"])]["decision"] == "approve"
        assert c.execute("select display_name from person where id=?", (r["person"],)).fetchone()[0] == "فلان الغريب"


def test_what_the_wiki_approved_is_sure_in_icm_and_what_it_rejected_is_not_linked(env):
    t, d, p = env
    label, identity._label = identity._label, lambda i: i                   # (the fake narrator database has no names)
    nwn.publish({("معمر", "noor:2107"): dict(decision="approve", user="ali"), ("الزهري", "noor:11"): dict(decision="reject", user="ali")})
    names._WIKI.clear()
    ch = _family_chains()
    ch = [ch[1]]                                                    # only the text chain: nothing else settles its names
    try:
        identity.auto_links(ch, {"w:2": "shia"}, [dict(wid="w:2", corpus="x")], twins=False)
    finally:
        identity._label = label
    e = {x["i"]: x for x in ch[0]["elems"]}
    assert e[1]["exact"] == "noor:2107" and e[1]["by"] == "wiki" and e[1]["cands"][0]["exact"]          # approved: as sure as a database
    assert "auto" not in e[2]                                       # rejected: no automatic link for the name «الزهري» -> noor:11 (what the model reads)
