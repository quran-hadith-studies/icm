import os, sqlite3, sys, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ICM_OPEN"] = "0"                         # these tests check the user rules, which open mode switches off
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:                                  # the model code itself needs only numpy
    print("fastapi not installed: skipped"); sys.exit(0)
from icm import config, project, jobs

from icm.api import make_router

USERS = {"alice": dict(username="alice", is_admin=False), "bob": dict(username="bob", is_admin=False),
         "root": dict(username="root", is_admin=True)}
H = lambda u=None, x=True: ({"X-User": u} if u else {}) | ({"X-ICM": "1"} if x else {})


def app():
    d = tempfile.mkdtemp(prefix="icmapi_")
    config.CACHE_DIR = d
    config.JOBS_DB = os.path.join(d, "jobs.sqlite")
    a = FastAPI()
    a.include_router(make_router(lambda req: USERS.get(req.headers.get("x-user", ""))))
    return a, d


def _version_file(d, name="t1"):
    c = sqlite3.connect(os.path.join(d, f"{name}.sqlite"))
    c.executescript("""create table meta(k text primary key, v text);
        create table person(id integer primary key, canon text, name text, n_ext integer);
        create table ext(kind text, ext_id integer, person_id integer, rule text, score real, primary key(kind, ext_id));
        create table edge(a text, b text, rule text, score real, evidence_json text);
        create table conflict(a text, b text, rule text, score real, reason text)""")
    c.executemany("insert into meta values (?,?)", [("label_fa", "آزمایشی"), ("built", "2026-10-15"), ("wiki_state", "s1")])
    c.execute("insert into person values (1, 'noor:2607', 'x', 2)")
    c.executemany("insert into ext values (?,?,1,'noor_kg',0.9)", [("noor", 2607), ("kg", 3151)])
    c.execute("insert into edge values ('noor:2607', 'kg:3151', 'noor_kg', 0.9, '{}')")
    c.commit(); c.close()


def test_narrator_versions_endpoint_and_decision_validation():
    a, d = app()
    old = config.NARRATOR_VERSIONS_DIR, config.NARRATOR_VERSION_DEFAULT
    config.NARRATOR_VERSIONS_DIR, config.NARRATOR_VERSION_DEFAULT = os.path.join(d, "versions"), "t1"
    try:
        c = TestClient(a)
        assert [v["name"] for v in c.get("/icm/api/narrator-versions").json()] == ["raw"]      # no directory: only raw
        os.makedirs(config.NARRATOR_VERSIONS_DIR)
        _version_file(config.NARRATOR_VERSIONS_DIR)
        got = c.get("/icm/api/narrator-versions").json()
        assert [v["name"] for v in got] == ["raw", "t1"] and got[1]["built"] == "2026-10-15" and got[1]["n_groups"] == 1
        p = project.create("v02:1", "alice")
        pid = p["pid"]
        info = c.get(f"/icm/api/project/{pid}").json()
        assert info["narrator_version"] == "t1"                                                   # no decision: the default
        for bad in ({"version": "nope"}, {"version": "../t1"}, {"version": 3}, {}):
            assert c.post(f"/icm/api/project/{pid}/decide/narrators", json={"payload": bad}, headers=H("alice")).status_code == 400, bad
        assert c.post(f"/icm/api/project/{pid}/decide/narrators", json={"payload": {"version": "raw"}}, headers=H("bob")).status_code == 403
        assert c.post(f"/icm/api/project/{pid}/decide/narrators", json={"payload": {"version": "raw"}}, headers=H("alice")).status_code == 200
        assert project.decision(pid, "narrators")["name"] == "raw"
        assert c.get(f"/icm/api/project/{pid}").json()["narrator_version"] == "raw"
        assert c.get("/icm/api/narrator/noor:1", params={"version": "nope"}).status_code in (400, 404)
    finally:
        config.NARRATOR_VERSIONS_DIR, config.NARRATOR_VERSION_DEFAULT = old
        shutil.rmtree(d)


if not os.path.exists(config.V02_DB):
    print("corpus not present: skipped"); sys.exit(0)


def test_permissions_and_validation():
    a, d = app()
    try:
        c = TestClient(a)
        assert c.get("/icm/api/me").json()["user"] is None
        assert c.post("/icm/api/project", json={"seed": "v02:800316143"}).status_code == 401        # not logged in
        assert c.post("/icm/api/project", json={"seed": "v02:800316143"}, headers=H("alice", x=False)).status_code == 403   # CSRF header
        assert c.post("/icm/api/project", json={"seed": "v02:1"}, headers=H("alice")).status_code == 400   # unknown hadith
        r = c.post("/icm/api/project", json={"seed": "v02:800316143"}, headers=H("alice"))
        assert r.status_code == 200
        pid = r.json()["pid"]
        assert jobs.active(pid)["status"] == "queued"
        assert c.get(f"/icm/api/project/{pid}").json()["project"]["owner"] == "alice"                    # readable by anyone
        body = {"payload": {"selected": ["v02:800316143"]}}
        assert c.post(f"/icm/api/project/{pid}/decide/identities", json={"payload": {"choices": {"x": "evil"}}}, headers=H("bob")).status_code == 403
        assert c.post(f"/icm/api/project/{pid}/decide/identities", json={"payload": {"choices": {"x": "evil"}}}, headers=H("alice")).status_code == 400
        assert c.post(f"/icm/api/project/{pid}/decide/identities", json={"payload": {"choices": {"x": "noor:12"}}}, headers=H("root")).status_code == 200   # admin
        assert project.decision(pid, "identities")["choices"] == {"x": "noor:12"}
        dated = {"choices": {}, "dates": {"noor:12": [120, 125], "occ:v02:1#t|3": [200, 200]}}
        assert c.post(f"/icm/api/project/{pid}/decide/identities", json={"payload": dated}, headers=H("alice")).status_code == 200
        assert project.decision(pid, "identities")["dates"] == dated["dates"]
        for bad in ({"x": [1, 2]}, {"noor:12": [130, 120]}, {"noor:12": ["a", 2]}, {"noor:12": [1]}):
            assert c.post(f"/icm/api/project/{pid}/decide/identities", json={"payload": {"dates": bad}}, headers=H("alice")).status_code == 400, bad
        sug = {"merges": [["noor:4013", "kg:5739"]], "drops": ["v02:1#t|3", "v02:1#t|3"], "dismissed": ["title_match:shia:x"]}
        assert c.post(f"/icm/api/project/{pid}/decide/identities", json={"payload": sug}, headers=H("alice")).status_code == 200
        got = project.decision(pid, "identities")
        assert got["merges"] == sug["merges"] and got["drops"] == ["v02:1#t|3"] and got["dismissed"] == sug["dismissed"]
        for bad in ({"merges": [["noor:1", "noor:1"]]}, {"merges": [["noor:1"]]}, {"merges": [["x", "noor:1"]]},
                    {"drops": ["nobar"]}, {"drops": [3]}, {"dismissed": "x"}):
            assert c.post(f"/icm/api/project/{pid}/decide/identities", json={"payload": bad}, headers=H("alice")).status_code == 400, bad
        free = {"merges": [["name:فلان بن فلان", "noor:12"], ["rel:v02:1#t:2", "split:1"]], "overrides": {"v02:1#t|3": "split:1", "v02:1#t|4": "noor:7"},
                "splits": {"split:1": {"label": " زيد الآخر ", "from": "noor:12"}, "split:9": {"label": "unused"}}, "dates": {"split:1": [100, 110]}}
        assert c.post(f"/icm/api/project/{pid}/decide/identities", json={"payload": free}, headers=H("alice")).status_code == 200
        got = project.decision(pid, "identities")
        assert got["merges"] == free["merges"] and got["splits"] == {"split:1": {"label": "زيد الآخر", "from": "noor:12"}}   # unused split dropped
        for bad in ({"overrides": {"v02:1#t|3": "split:x"}}, {"overrides": {"nobar": "noor:1"}}, {"splits": {"split:1": {"label": 3}}},
                    {"splits": {"x": {"label": "a"}}}, {"merges": [["name:a|b", "noor:1"]]}, {"dates": {"split:a": [1, 2]}}):
            assert c.post(f"/icm/api/project/{pid}/decide/identities", json={"payload": bad}, headers=H("alice")).status_code == 400, bad
        assert c.post(f"/icm/api/project/{pid}/decide/phylogeny", json={"payload": {"confirmed": "nope"}}, headers=H("alice")).status_code == 409
    finally:
        shutil.rmtree(d)


def test_parts_decision_recuts_the_seed_and_corrects_slots():
    from icm import pipeline
    from icm.text import tokens
    a, d = app()
    try:
        c = TestClient(a)
        pid = c.post("/icm/api/project", json={"seed": "v02:800316143"}, headers=H("alice")).json()["pid"]
        assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": {}}, headers=H("alice")).status_code == 409   # nothing searched yet
        pipeline.advance(pid)
        S = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        assert S["clauses_by"] == "auto" and S["auto_clauses"] == [x["span"] for x in S["clauses"]]
        seed_w = c.get(f"/icm/api/project/{pid}/runs/current/words/v02:800316143").json()
        assert seed_w["tokens"] == S["tokens"] == tokens(S["seed"]["matn"]) and len(seed_w["offsets"]) == len(S["tokens"])
        sel = [x["wid"] for x in S["candidates"][:3]]
        c.post(f"/icm/api/project/{pid}/decide/selection", json={"payload": {"selected": sel}}, headers=H("alice"))
        c.post(f"/icm/api/project/{pid}/decide/search", json={"payload": {"part_query": [0], "extra_ids": []}}, headers=H("alice"))
        n = len(S["tokens"])
        for bad in ({"seed": [[0, n + 1]]}, {"seed": [[3, 2]]}, {"seed": [[0, 4], [3, 6]]}, {"seed": []},
                    {"slots": {"bad": {"v02:1": "absent"}}}, {"slots": {"s:x": {"v02:1": "maybe"}}},
                    {"extras_added": [{"wid": "v02:1", "span": [2, 2]}]}, {"confirmed": {"s:x": "v02:1"}}):
            assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": bad}, headers=H("alice")).status_code == 400, bad
        # the same parts as the automatic ones are stored as "automatic"
        auto = S["auto_clauses"]
        assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": {"seed": auto, "seed_confirmed": auto}}, headers=H("alice")).status_code == 200
        assert project.decision(pid, "parts")["seed"] is None and project.decision(pid, "search")["part_query"] == [0]
        # one part for the whole text: the search runs again with it, the selected witnesses stay
        assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": {"seed": [[0, n]], "seed_confirmed": [[0, n]]}}, headers=H("alice")).status_code == 200
        got = project.decision(pid, "parts")
        assert got["seed"] == [[0, n]] and got["keep_ids"] == sorted(set(sel) - {"v02:800316143"})
        assert project.decision(pid, "search")["part_query"] is None                    # the old ticks are reset
        pipeline.advance(pid)
        S2 = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        assert S2["clauses_by"] == "user" and [x["span"] for x in S2["clauses"]] == [[0, n]]
        known = {x["wid"] for x in S2["candidates"]} | {w for x in S2["candidates"] for w in x.get("copies", [])}
        assert set(sel) - {"v02:800316143"} <= known
        A = c.get(f"/icm/api/project/{pid}/runs/current/align").json()
        key = A["clauses"][0]["key"]
        w = next(x["wid"] for x in A["witnesses"] if x["wid"] != "v02:800316143")
        ww = c.get(f"/icm/api/project/{pid}/runs/current/words/{w}").json()
        cw = next(x for x in S2["candidates"] if x["wid"] == w)
        assert ww["window"] == cw["window"] and ww["tokens"][cw["window"][0]:cw["window"][1]] == cw["window_text"].split()[:len(ww["tokens"]) - cw["window"][0]]
        body = {"seed": [[0, n]], "seed_confirmed": [[0, n]], "slots": {key: {w: [0, 2]}}, "confirmed": {key: [w]}}
        assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": body}, headers=H("alice")).status_code == 200
        assert project.decision(pid, "parts")["keep_ids"] == got["keep_ids"]           # unchanged seed: no new search
        pipeline.advance(pid)
        A2 = c.get(f"/icm/api/project/{pid}/runs/current/align").json()
        sg = A2["segments"][f"0|{w}"]
        assert sg["user"] and sg["tokens"] == ww["tokens"][0:2], sg
        assert c.get(f"/icm/api/project/{pid}/runs/current/words/v02:2").status_code == 404
        # per-word corrections (step 3), unit labels, and name/term readings of a place (step 4) are validated and stored
        W0, ok_place = "v02:800316143", "v02:1#t|2"
        good = dict(body, words={key: {w: {"من#1": "add", "الله#2": {"to": [W0, "الله#1"]}}}}, unit_cls={key: {"بیتا": "addition"}},
                    renames={ok_place: {"name": " أبو محمد ", "term": "قرأت علی"}})
        assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": good}, headers=H("alice")).status_code == 200
        got = project.decision(pid, "parts")
        assert got["words"] == good["words"] and got["unit_cls"] == good["unit_cls"] and got["renames"] == {ok_place: {"name": "أبو محمد", "term": "قرأت علی"}}
        for bad in ({"words": {key: {w: {"nohash": "add"}}}}, {"words": {key: {w: {"من#1": "maybe"}}}}, {"words": {key: {w: {"من#1": {"to": ["bad", "الله#1"]}}}}},
                    {"words": {key: {w: {"من#1": {"to": [W0, "الله"]}}}}}, {"words": {"bad": {w: {"من#1": "add"}}}}, {"words": [1]},
                    {"unit_cls": {key: {"بیتا": "both"}}}, {"unit_cls": {"bad": {"بیتا": "addition"}}},
                    {"renames": {"nobar": {"name": "x", "term": ""}}}, {"renames": {ok_place: {"name": "  ", "term": ""}}}, {"renames": {ok_place: {"name": "x", "term": 3}}}):
            assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": dict(body, **bad)}, headers=H("alice")).status_code == 400, bad
        assert project.decision(pid, "parts")["words"] == good["words"]                     # a refused decision stores nothing
        pipeline.advance(pid)                                                               # the corrections reach the alignment and the narrators stage
        A3 = c.get(f"/icm/api/project/{pid}/runs/current/align").json()
        assert A3["parts_in"]["words"] == good["words"] and A3["parts_in"]["unit_cls"] == good["unit_cls"]
        assert c.get(f"/icm/api/project/{pid}/runs/current/identify").json()["renames_in"] == got["renames"]     # trimmed
        # edits of the variation units (step 3): exclude, keep, a witness's state, a unit of the user's own over words of the grid
        manual = {"kind": "lexical", "anchors": {W0: ["من#1", "من#1"]}, "text": " من "}
        units = {key: {"بیتا": {"drop": True}, "حقا": {"keep": True, "cls": "omission", "states": {w: None}}, "m:1": {"def": manual}}}
        assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": dict(body, units=units)}, headers=H("alice")).status_code == 200
        want = {key: {"بیتا": {"drop": True}, "حقا": {"keep": True, "cls": "omission", "states": {w: None}}, "m:1": {"def": dict(manual, text="من")}}}
        assert project.decision(pid, "parts")["units"] == want
        mdef = lambda **kw: {"m:1": {"def": dict(manual, **kw)}}
        for bad in ({"units": {key: {"m:1": {}}}}, {"units": {key: {"بیتا": {"def": manual}}}}, {"units": {key: {"بیتا": {"drop": True, "keep": True}}}},
                    {"units": {key: {"بیتا": {"cls": "both"}}}}, {"units": {key: {"بیتا": {"states": {w: 3}}}}}, {"units": {key: {"بیتا": {"states": {"bad": None}}}}},
                    {"units": {key: {"بیتا": {"drop": "yes"}}}}, {"units": {key: {"بیتا": {"color": 1}}}},
                    {"units": {key: mdef(anchors={W0: ["من", "من#1"]})}}, {"units": {key: mdef(anchors={})}}, {"units": {key: mdef(kind="both")}},
                    {"units": {key: mdef(text="  ")}}, {"units": {key: {"m:1": {"def": manual, "drop": True}}}}, {"units": {"bad": {"بیتا": {"drop": True}}}}, {"units": [1]}):
            assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": dict(body, **bad)}, headers=H("alice")).status_code == 400, bad
        assert project.decision(pid, "parts")["units"] == want
        pipeline.advance(pid)                                                               # reaches the alignment (a unit that does not fit is flagged)
        A4 = c.get(f"/icm/api/project/{pid}/runs/current/align").json()
        assert A4["parts_in"]["units"] == want and isinstance(A4["dropped_units"], list)
        assert all(u.get("ukey") is not None for u in A4["units"] + A4["private_variants"])
        # emptied corrections are not stored at all, so the stage keys of older projects stay as they were
        assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": dict(body, words={key: {w: {}}}, unit_cls={}, renames={}, units={key: {}})}, headers=H("alice")).status_code == 200
        assert not any(k in project.decision(pid, "parts") for k in ("words", "unit_cls", "renames", "units"))
    finally:
        shutil.rmtree(d)


def test_search_and_hadith():
    a, d = app()
    try:
        c = TestClient(a)
        r = c.get("/icm/api/search", params={"q": "ستة لعنهم الله", "limit": 100}).json()
        assert r and any(x["wid"] == "v02:800316143" for x in r)
        assert c.get("/icm/api/hadith/v02:800316143").json()["title"] == "الخصال"
        assert c.get("/icm/api/hadith/v02:abc").status_code == 400
        assert c.get("/icm/api/narrator/noor:2107").json()["infallible"] is True
        n = c.get("/icm/api/narrators", params={"q": "زيد بن علي"}).json()
        assert {"noor:2607", "kg:3151"} <= {x["id"] for x in n} and {x["db"] for x in n} == {"noor", "kg"}
        assert c.get("/icm/api/narrators", params={"q": "ز"}).json() == []
        dd = c.get("/icm/api/narrators/deaths", params={"ids": "kg:3151,noor:2607,bad,kg:999999999"}).json()
        assert dd["kg:3151"]["death"] == [122, 122] and "noor:2607" in dd and "bad" not in dd and "kg:999999999" not in dd
        assert c.get("/icm/api/project/..%2Fetc").status_code in (400, 404)
    finally:
        shutil.rmtree(d)


def test_pages():
    a, d = app()
    try:
        c = TestClient(a)
        assert c.get("/icm").status_code == 200 and c.get("/icm/p/abc").status_code == 200
        assert c.get("/icm/p/abc/selection").status_code == 200 and c.get("/icm/p/abc/r/0000/alignment").status_code == 200
        assert c.get("/icm/static/nope.js").status_code == 404
    finally:
        shutil.rmtree(d)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception as e:
                import traceback; traceback.print_exc()
                fails += 1; print("FAIL", name, repr(e))
    sys.exit(1 if fails else 0)


def test_chain_edits_add_a_narrator_from_the_text_and_keep_place_ids():
    from icm import pipeline
    a, d = app()
    try:
        c = TestClient(a)
        W0 = "v02:800316143"
        pid = c.post("/icm/api/project", json={"seed": W0}, headers=H("alice")).json()["pid"]
        pipeline.advance(pid)
        T = c.get(f"/icm/api/hadith/{W0}/tokens", params={"pid": pid}, headers=H("alice")).json()
        assert len(T["tokens"]) == len(T["words"]) == len(T["tflag"]) and 0 <= T["n_sanad"] <= len(T["tokens"]) and T["chains"] == []   # no narrators yet
        assert T["words"][0] == "41" and not any(w.endswith(("،", ":", ".")) for w in T["words"])
        S = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        c.post(f"/icm/api/project/{pid}/decide/selection", json={"payload": {"selected": [x["wid"] for x in S["candidates"][:2]]}}, headers=H("alice"))
        pipeline.advance(pid)
        T = c.get(f"/icm/api/hadith/{W0}/tokens", params={"pid": pid}, headers=H("alice")).json()
        assert T["chains"]
        assert c.get("/icm/api/hadith/bad/tokens").status_code == 400 and c.get("/icm/api/hadith/v02:1/tokens").status_code == 404
        I = c.get(f"/icm/api/project/{pid}/runs/current/identify").json()
        ch = next(x for x in I["chains"] if x["wid"] == W0)
        aid, n = ch["aid"], len(T["tokens"])
        spans = next(x for x in T["chains"] if x["aid"] == aid)["spans"]
        assert set(spans) == {str(e["i"]) for e in ch["elems"]} and spans["0"] is None            # the collector is never in the text
        found = [e for e in ch["elems"] if spans[str(e["i"])]]
        assert found, "the rule-based chain of the seed should be found in its own sanad"
        e1 = found[0]
        s1 = spans[str(e1["i"])]
        for bad in ({"chain": [1]}, {"chain": {"x|1": {}}}, {"chain": {aid: {"drop": "1"}}}, {"chain": {aid: {"add": [{"t": 0, "a": 2, "b": 1}]}}},
                    {"chain": {aid: {"add": [{"t": 0, "a": 0, "b": n + 1}]}}}, {"chain": {aid: {"span": {"x": {"t": 0, "a": 0, "b": 1}}}}},
                    {"chain": {aid: {"span": {"1": {"t": 0, "a": 0}}}}}, {"chain": {aid: {"extra": 1}}}, {"chain": {"v02:1#t": {"drop": [1]}}}):
            assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": bad}, headers=H("alice")).status_code == 400, bad
        # add the last two words of the text as a narrator, move the first found name by one word, drop nothing
        good = {"chain": {aid: {"add": [{"t": n - 2, "a": n - 2, "b": n}], "span": {str(e1["i"]): {"t": s1["t"], "a": s1["a"], "b": s1["b"] + 1}}, "drop": [], "foo": None}},
                "renames": {f"{aid}|{e1['i']}": {"name": "x", "term": ""}}}
        assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": {"chain": {aid: {"add": [], "span": {}, "drop": []}}}}, headers=H("alice")).status_code == 200
        assert "chain" not in project.decision(pid, "parts")                                      # emptied: not stored
        r = c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": good}, headers=H("alice"))
        assert r.status_code == 400                                                                 # "foo" is not a key
        del good["chain"][aid]["foo"]
        assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": good}, headers=H("alice")).status_code == 200
        got = project.decision(pid, "parts")["chain"]
        assert got == {aid: {"add": good["chain"][aid]["add"], "span": good["chain"][aid]["span"]}}
        pipeline.advance(pid)
        I2 = c.get(f"/icm/api/project/{pid}/runs/current/identify").json()
        assert I2["chain_in"] == got
        ch2 = next(x for x in I2["chains"] if x["aid"] == aid)
        ids = [e["i"] for e in ch2["elems"]]
        assert ids[-1] == 1000 and sorted(ids[:-1]) == sorted(e["i"] for e in ch["elems"])        # ids kept; the added place is last (end of the matn)
        new = ch2["elems"][-1]
        assert new["added"] and new["span"] == {"t": n - 2, "a": n - 2, "b": n} and new["raw"] == " ".join(T["words"][n - 2:n])
        moved = next(e for e in ch2["elems"] if e["i"] == e1["i"])
        assert moved["renamed"] and moved["span"]["b"] == s1["b"] + 1 and moved["name"] != "x"     # the span wins over the rename of the same place
        T2 = c.get(f"/icm/api/hadith/{W0}/tokens", params={"pid": pid}, headers=H("alice")).json()
        assert next(x for x in T2["chains"] if x["aid"] == aid)["spans"]["1000"] == new["span"]
        # the added place takes an override like any other, and can be dropped by its id
        assert c.post(f"/icm/api/project/{pid}/decide/identities", json={"payload": {"choices": {}, "overrides": {f"{aid}|1000": "none"}}}, headers=H("alice")).status_code == 200
        good["chain"][aid]["drop"] = [1000, e1["i"]]
        assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": good}, headers=H("alice")).status_code == 200
        pipeline.advance(pid)
        I3 = c.get(f"/icm/api/project/{pid}/runs/current/identify").json()
        ids3 = [e["i"] for e in next(x for x in I3["chains"] if x["aid"] == aid)["elems"]]
        assert 1000 not in ids3 and e1["i"] not in ids3 and len(ids3) == len(ids) - 2
    finally:
        shutil.rmtree(d)
