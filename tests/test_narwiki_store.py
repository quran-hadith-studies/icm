"""narwiki: store transitions on a temporary database (no source databases needed), lookup, export/replay,
and the HTTP API when fastapi is installed.

    python3 -m pytest tests/test_narwiki_store.py -q        (or: python3 tests/test_narwiki_store.py)
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from narwiki import config, lookup, seed, store  # noqa: E402

RECS = [
    dict(kind="noor", ext_id="1", display_name="سفيان بن عيينة", n_narr=165,
         fields=[("name", "سفيان بن عيينة", "noor"), ("kunya", "أبو محمد", "noor"), ("mazhab", "غیر امامی", "noor")]),
    dict(kind="kg", ext_id="10", display_name="سفيان بن عيينة بن ميمون", n_narr=5000,
         fields=[("name", "سفيان بن عيينة بن ميمون", "kg"), ("alias", "سفيان بن عيينة الهلالي", "kg"),
                 ("death_lo", "198", "kg"), ("death_hi", "198", "kg")]),
    dict(kind="noor", ext_id="2", display_name="زرارة بن أعين", n_narr=4000, fields=[("name", "زرارة بن أعين", "noor")]),
    dict(kind="kg", ext_id="20", display_name="محمد بن مسلم بن شهاب الزهري", n_narr=2500,
         fields=[("name", "محمد بن مسلم بن شهاب الزهري", "kg"), ("death_lo", "124", "kg"), ("death_hi", "124", "kg")]),
]


def fresh(d, name="w.sqlite"):
    path = os.path.join(d, name)
    c = store.connect(path)
    with c:
        ids = [seed.insert_record(c, r) for r in RECS]
    return c, path, ids


def cur(c, pid, field):
    return sorted(f["value"] for f in store.get_person(c, pid)["fields"] if f["field"] == field)


def test_seed_search_edit_revert():
    d = tempfile.mkdtemp(prefix="narwiki_")
    try:
        c, path, (a, b, z, h) = fresh(d)
        assert store.by_ext(c, "noor", 1) == a and store.by_ext(c, "kg", "10") == b
        assert store.search(c, "عيينه")["total"] == 2                       # spelling-insensitive, prefix tokens
        assert store.search(c, "الزهري")["items"][0]["id"] == h
        assert store.search(c, "kg:10")["items"][0]["id"] == b
        assert store.search(c, "", kind="kg", dated="1")["total"] == 2
        assert store.search(c, "", kind="noor", dated="1")["total"] == 0
        assert all(r["review_status"] == "approved" for r in c.execute("select review_status from revision where kind='seed'"))
        # edit: add a death year, retire the madhhab
        fid = next(f["id"] for f in store.get_person(c, a)["fields"] if f["field"] == "mazhab")
        rev = store.edit_fields(c, a, "ali", display_name="سفيان بن عيينة الهلالي", add=[("death_lo", "198")], retire=[fid], note="t")
        assert cur(c, a, "death_lo") == ["198"] and cur(c, a, "mazhab") == []
        assert store.get_person(c, a)["display_name"] == "سفيان بن عيينة الهلالي"
        r = c.execute("select * from revision where id=?", (rev,)).fetchone()
        assert r["review_status"] == "unreviewed" and r["user"] == "ali"
        kinds = {(x["what"], x.get("field")) for x in store.rev_dict(c, r)["diff"]}
        assert ("display_name", None) in kinds and ("field", "death_lo") in kinds and ("field", "mazhab") in kinds
        try:
            store.edit_fields(c, a, "ali", add=[("death_lo", "abc")])
            assert False, "non-numeric year accepted"
        except store.WikiError:
            pass
        # a later edit of another value survives reverting the first one
        store.edit_fields(c, a, "bob", add=[("note", "later")])
        assert store.queue(c)["total"] == 2
        out = store.review(c, rev, "rejected", "rev", revert_it=True)
        assert out["revert"] and cur(c, a, "death_lo") == [] and cur(c, a, "mazhab") == ["غیر امامی"] and cur(c, a, "note") == ["later"]
        assert store.get_person(c, a)["display_name"] == "سفيان بن عيينة"
        assert c.execute("select review_status from revision where id=?", (rev,)).fetchone()[0] == "rejected"
        assert c.execute("select review_status from revision where id=?", (out["revert"],)).fetchone()[0] == "approved"
        try:
            store.revert(c, rev, "x")
            assert False, "reverted twice"
        except store.WikiError:
            pass
        try:
            store.revert(c, c.execute("select id from revision where kind='seed' limit 1").fetchone()[0], "x")
            assert False, "seed reverted"
        except store.WikiError:
            pass
    finally:
        shutil.rmtree(d)


def test_merge_split_suggestion_revert_and_lookup():
    d = tempfile.mkdtemp(prefix="narwiki_")
    old = config.DB
    try:
        c, path, (a, b, z, h) = fresh(d)
        assert store.add_suggestion(c, b, a, "link_kg", 1.9, {"confidence": "high"})
        assert not store.add_suggestion(c, a, b, "link_kg", 1.0, {})                  # unique per pair and kind
        c.commit()
        sid = store.suggestions(c)["items"][0]["id"]
        res = store.decide_suggestion(c, sid, "accepted", "ali", keep=a)
        assert res["person"] == a
        merge_rev = res["revision"]
        assert store.by_ext(c, "kg", 10) == a and store.resolve_id(c, b) == a
        assert c.execute("select status, merged_into from person where id=?", (b,)).fetchone()[:] == ("merged", a)
        assert cur(c, a, "death_lo") == ["198"]                                         # KG values moved with provenance
        assert {f["source"] for f in store.get_person(c, a)["fields"]} == {"noor", "kg"}
        assert store.suggestions(c, status="accepted")["total"] == 1
        assert store.get_person(c, b)["id"] == a                                        # old ids follow the merge
        # lookup (read-only, used by ICM)
        config.DB = path
        assert lookup.available()
        got = lookup.persons_for(["noor:1", "kg:10", "kg:999", "noor:2"])
        assert got["kg:10"] == {"person_id": a, "name": "سفيان بن عيينة", "also": ["noor:1"]}
        assert got["noor:1"]["also"] == ["kg:10"] and "kg:999" not in got and got["noor:2"]["also"] == []
        # merging again is refused; merge with a third person, then split it off
        try:
            store.merge(c, a, b, a, "x")
            assert False
        except store.WikiError:
            pass
        m2 = store.merge(c, a, h, a, "ali")
        try:
            store.revert(c, merge_rev, "x")                                             # a later revision touched a
            assert False
        except store.WikiError as e:
            assert "later" in str(e)
        new, srev = store.split(c, a, ["kg:20"], [], "الزهري", "ali")
        assert store.by_ext(c, "kg", 20) == new and cur(c, new, "death_lo") == ["124"] and "124" not in cur(c, a, "death_lo")
        assert any(x.startswith("wiki:") for x in store.get_person(c, new)["ext"])
        try:
            store.split(c, a, ["noor:1", "kg:10"], [], "x", "ali")                        # must keep one id
            assert False
        except store.WikiError:
            pass
        store.revert(c, srev, "ali")
        assert c.execute("select status from person where id=?", (new,)).fetchone()[0] == "deleted"
        assert store.by_ext(c, "kg", 20) == a
        store.revert(c, m2, "ali")
        assert store.by_ext(c, "kg", 20) == h and c.execute("select status from person where id=?", (h,)).fetchone()[0] == "active"
        store.revert(c, merge_rev, "ali")                                               # now possible
        assert store.by_ext(c, "kg", 10) == b and cur(c, a, "death_lo") == []
        assert store.suggestions(c)["items"][0]["status"] == "proposed"                  # the suggestion reopens
        store.decide_suggestion(c, sid, "dismissed", "ali")
        assert store.suggestions(c, status="dismissed")["total"] == 1
        assert lookup.persons_for(["kg:10"])["kg:10"]["person_id"] == b
        # add a person by hand, reject it
        p = store.add_person(c, "راوٍ جديد", [("death_lo", "300")], "ali")
        rv = c.execute("select id from revision where person_id=? and kind='add'", (p,)).fetchone()[0]
        store.review(c, rv, "rejected", "rev", revert_it=True)
        assert c.execute("select status from person where id=?", (p,)).fetchone()[0] == "deleted"
        assert store.search(c, "جديد")["total"] == 0
        h_rows = store.history(c, a)
        assert {r["kind"] for r in h_rows} >= {"seed", "merge", "split", "revert"}
    finally:
        config.DB = old
        shutil.rmtree(d)


def test_export_import_replay():
    d = tempfile.mkdtemp(prefix="narwiki_")
    try:
        c, path, (a, b, z, h) = fresh(d)
        store.add_suggestion(c, a, b, "name_chrono", 1.5, {})
        store.add_suggestion(c, z, h, "name_chrono", 0.9, {})
        c.commit()
        sid = c.execute("select id from merge_suggestion where a=? and b=?", (a, b)).fetchone()[0]
        sid2 = c.execute("select id from merge_suggestion where a=? and b=?", (z, h)).fetchone()[0]
        store.decide_suggestion(c, sid, "accepted", "ali", keep=a)
        store.decide_suggestion(c, sid2, "dismissed", "ali")
        store.edit_fields(c, a, "ali", add=[("note", "x")])
        p = store.add_person(c, "راوٍ جديد", [("kunya", "أبو علي")], "bob")
        new, _ = store.split(c, a, [], [store.get_person(c, a)["fields"][0]["id"]], "قسم", "ali")
        dump = json.loads(json.dumps(store.export_revisions(c)))
        assert [r["op"]["op"] for r in dump["revisions"]] == ["merge", "edit", "add", "split"]
        c2, _, _ = fresh(d, "w2.sqlite")
        store.add_suggestion(c2, a, b, "name_chrono", 1.5, {})
        store.add_suggestion(c2, z, h, "name_chrono", 0.9, {})
        c2.commit()
        out = store.import_revisions(c2, dump)
        assert out["replayed"] == 4 and not out["skipped"], out
        assert store.by_ext(c2, "kg", 10) == store.by_ext(c2, "noor", 1)
        assert c2.execute("select status from merge_suggestion where a=? and b=?", (z, h)).fetchone()[0] == "dismissed"
        assert c2.execute("select status from merge_suggestion where a=? and b=?", (a, b)).fetchone()[0] == "accepted"
        w = [x for x in store.get_person(c, p)["ext"]]
        assert store.from_anchor(c2, w[0]) is not None
        assert cur(c2, store.by_ext(c2, "noor", 1), "note") == ["x"]
    finally:
        shutil.rmtree(d)


def test_api():
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
    except ImportError:
        print("fastapi not installed: API test skipped")
        return
    from narwiki import api
    d = tempfile.mkdtemp(prefix="narwiki_")
    old, old_open, old_cache = config.DB, config.OPEN, config.ICM_CACHE_DIR
    try:
        config.DB = os.path.join(d, "missing.sqlite")
        app = FastAPI()
        app.include_router(api.make_router(lambda req: None))
        t = TestClient(app)
        assert t.get("/narrator/api/me").json()["user"] == "guest"                       # open mode
        assert t.get("/narrator/api/search?q=x").status_code == 503                      # never creates the DB
        assert not os.path.exists(config.DB)
        c, path, (a, b, z, h) = fresh(d)
        config.DB = path
        html = t.get("/narrator/p/1").text
        assert "__BASE__" not in html and "/narrator/static/narwiki.js?v=" in html
        assert t.get("/narrator/static/narwiki.js").status_code == 200 and t.get("/narrator/static/x.py").status_code == 404
        r = t.get("/narrator/api/search", params={"q": "عيينة"}).json()
        assert r["total"] == 2
        pj = t.get(f"/narrator/api/person/{a}").json()
        assert pj["person"]["ext"] == ["noor:1"] and "cards" in pj and "counts" in pj
        assert t.post(f"/narrator/api/person/{a}", json={"add": [["note", "n"]]}).status_code == 403   # no X-NARWIKI header
        H = {"X-NARWIKI": "1"}
        assert t.post(f"/narrator/api/person/{a}", json={"add": [["bogus", "n"]]}, headers=H).status_code == 400
        assert t.post(f"/narrator/api/person/{a}", json={"add": [["note", "n"]]}, headers=H).status_code == 200
        pv = t.get(f"/narrator/api/merge/preview?a={a}&b={b}").json()
        assert pv["a"]["id"] == a and any(f["field"] == "name" for f in pv["fields"])
        assert t.post("/narrator/api/merge", json={"a": a, "b": b, "keep": b}, headers=H).json()["person"] == b
        assert t.get(f"/narrator/api/by-ext/noor/1").json()["id"] == b
        q = t.get("/narrator/api/queue").json()
        assert q["total"] == 2 and q["items"][0]["kind"] == "edit"
        rv = q["items"][1]["id"]
        assert t.post(f"/narrator/api/revision/{rv}/review", json={"status": "rejected", "revert": True}, headers=H).status_code == 200
        assert t.get(f"/narrator/api/by-ext/noor/1").json()["id"] == a
        hist = t.get(f"/narrator/api/person/{a}/history").json()
        assert any(x["kind"] == "merge" for x in hist["revisions"])
        config.ICM_CACHE_DIR = d
        assert t.get(f"/narrator/api/person/{a}/projects").json() == []                  # no narrator_index.json yet
        json.dump({"noor:1": [{"pid": "abc123", "title": "t", "status": "finalized"}]}, open(os.path.join(d, "narrator_index.json"), "w"))
        assert t.get(f"/narrator/api/person/{a}/projects").json()[0]["pid"] == "abc123"
        ex = t.get(f"/narrator/api/person/{a}/export.html")
        assert ex.status_code == 200 and "window.NARWIKI_STATIC" in ex.text and "__BASE__" not in ex.text and "<script src" not in ex.text
        config.OPEN = False
        app2 = FastAPI()
        app2.include_router(api.make_router(lambda req: {"username": "u", "is_admin": False} if req.headers.get("x-user") else None))
        t2 = TestClient(app2)
        assert t2.post(f"/narrator/api/person/{a}", json={"add": [["note", "m"]]}, headers=H).status_code == 401
        assert t2.post(f"/narrator/api/person/{a}", json={"add": [["note", "m"]]}, headers={**H, "X-User": "u"}).status_code == 200
        assert t2.post(f"/narrator/api/revision/{rv}/review", json={"status": "approved"}, headers={**H, "X-User": "u"}).status_code == 403
    finally:
        config.DB, config.OPEN, config.ICM_CACHE_DIR = old, old_open, old_cache
        shutil.rmtree(d)


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_") and callable(f):
            f()
            print("ok", name)
