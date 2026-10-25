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


def press_search(c, pid, mode=None, **more):
    """«جستجو» pressed (the corpora are asked only then), and the job run."""
    from icm import pipeline
    pl = dict(part_query=None, extra_ids=[], go=True) | ({"mode": mode} if mode else {}) | more
    assert c.post(f"/icm/api/project/{pid}/decide/search", json={"payload": pl}, headers=H("alice")).status_code == 200
    return pipeline.advance(pid)


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


def test_versions_endpoints():
    a, d = app()
    try:
        c = TestClient(a)
        pid = project.create("v02:1", "alice")["pid"]
        project.add_decision(pid, "selection", dict(selected=["v02:1", "v02:2"]), "alice")
        V = lambda: c.get(f"/icm/api/project/{pid}").json()["branches"]
        assert [b["id"] for b in V()["versions"]] == ["1"]
        assert c.post(f"/icm/api/project/{pid}/versions", json={}, headers=H("bob")).status_code == 403
        r = c.post(f"/icm/api/project/{pid}/versions", json={"base": "1", "name": "  آزمون  "}, headers=H("alice"))
        assert r.status_code == 200 and V()["active"] == r.json()["id"] == "2"
        assert [b["name"] for b in V()["versions"]] == ["نسخه‌ی ۱", "آزمون"]
        project.add_decision(pid, "selection", dict(selected=["v02:7"]), "alice")
        run = project.write_run(project.load(pid)["pid"], project.versions(pid), {"search": "x"})
        assert c.get(f"/icm/api/project/{pid}/decision/selection").json()["selected"] == ["v02:7"]
        assert c.post(f"/icm/api/project/{pid}/versions/2/delete", json={}, headers=H("alice")).status_code == 409     # the active one
        assert c.post(f"/icm/api/project/{pid}/versions/9/activate", json={}, headers=H("alice")).status_code == 404
        assert c.post(f"/icm/api/project/{pid}/versions/1/rename", json={"name": " "}, headers=H("alice")).status_code == 400
        assert c.post(f"/icm/api/project/{pid}/versions/1/rename", json={"name": "اصلی"}, headers=H("alice")).status_code == 200
        jobs.enqueue(pid)
        assert c.post(f"/icm/api/project/{pid}/versions/1/activate", json={}, headers=H("alice")).status_code == 409    # a job is queued
        with jobs._db() as db:
            db.execute("update jobs set status='done' where pid=?", (pid,))
        assert c.post(f"/icm/api/project/{pid}/versions/1/activate", json={}, headers=H("alice")).status_code == 200
        assert c.get(f"/icm/api/project/{pid}/decision/selection").json()["selected"] == ["v02:1", "v02:2"]
        assert c.get(f"/icm/api/project/{pid}/decision/selection", params={"run": run}).json()["selected"] == ["v02:7"]   # a read-only page of version 2
        assert c.post(f"/icm/api/project/{pid}/versions/2/delete", json={}, headers=H("alice")).status_code == 200
        assert [b["name"] for b in V()["versions"]] == ["اصلی"]
    finally:
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
        assert c.post(f"/icm/api/project/{pid}/decide/phylogeny", json={"payload": {"build": "nope"}}, headers=H("alice")).status_code == 409
    finally:
        shutil.rmtree(d)


def test_the_tree_is_built_only_when_step_5_orders_it():
    from icm import pipeline, search
    a, d = app()
    try:
        c = TestClient(a)
        pid = c.post("/icm/api/project", json={"seed": "v02:800316143"}, headers=H("alice")).json()["pid"]
        p = pipeline.advance(pid)
        S = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        sel = [S["seed"]["wid"]] + [x["wid"] for x in S["candidates"] if search.preselected(x)]
        assert c.post(f"/icm/api/project/{pid}/decide/selection", json={"payload": {"selected": sel}}, headers=H("alice")).status_code == 200
        p = pipeline.advance(pid)
        assert p["step"] == "identities"
        assert c.post(f"/icm/api/project/{pid}/decide/identities", json={"payload": {"choices": {}}}, headers=H("alice")).status_code == 200
        p = pipeline.advance(pid)                                                       # saved: step 5, but no tree yet
        assert p["step"] == "phylogeny" and p["status"] == "draft" and p["graph_hash"]
        assert project.run_stage(pid, "current", "graph") is None
        assert c.get(f"/icm/api/project/{pid}/runs/current/graph").status_code == 404
        assert c.post(f"/icm/api/project/{pid}/decide/phylogeny", json={"payload": {"build": p["graph_hash"]}}, headers=H("bob")).status_code == 403
        assert c.post(f"/icm/api/project/{pid}/decide/phylogeny", json={"payload": {"build": p["graph_hash"]}}, headers=H("alice")).status_code == 200
        q = pipeline.advance(pid)                                                       # ordered: the tree exists, the analysis waits for the confirmation
        assert q["step"] == "phylogeny" and q["graph_hash"] == p["graph_hash"]
        assert project.run_stage(pid, "current", "graph") is not None
        gh = p["graph_hash"]                                                            # «حذف شجره»: only the owner, only the graph on screen
        assert c.post(f"/icm/api/project/{pid}/decide/phylogeny", json={"payload": {"deleted": gh}}, headers=H("bob")).status_code == 403
        assert c.post(f"/icm/api/project/{pid}/decide/phylogeny", json={"payload": {"deleted": "nope"}}, headers=H("alice")).status_code == 409
        assert c.post(f"/icm/api/project/{pid}/decide/phylogeny", json={"payload": {"deleted": gh}}, headers=H("alice")).status_code == 200
        q = pipeline.advance(pid)
        assert q["step"] == "phylogeny" and q["graph_hash"] == gh and project.run_stage(pid, "current", "graph") is None
        assert c.post(f"/icm/api/project/{pid}/decide/phylogeny", json={"payload": {"build": gh}}, headers=H("alice")).status_code == 200
        pipeline.advance(pid)                                                           # ordered again: the cached tree is back
        assert project.run_stage(pid, "current", "graph") is not None
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
        assert c.get(f"/icm/api/project/{pid}/runs/current/search").json()["searched"] is False and not c.get(f"/icm/api/project/{pid}/runs/current/search").json()["queries"]
        press_search(c, pid)                                                              # the corpora are asked only when «جستجو» is pressed
        S = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        assert S["searched"] and S["clauses_by"] == "auto" and S["auto_clauses"] == [x["span"] for x in S["clauses"]]
        seed_w = c.get(f"/icm/api/project/{pid}/runs/current/words/v02:800316143").json()
        assert seed_w["tokens"] == S["tokens"] == tokens(S["seed"]["matn"]) and len(seed_w["offsets"]) == len(S["tokens"])
        sel = [x["wid"] for x in S["candidates"][:3]]
        c.post(f"/icm/api/project/{pid}/decide/selection", json={"payload": {"selected": sel}}, headers=H("alice"))
        c.post(f"/icm/api/project/{pid}/decide/search", json={"payload": {"part_query": [0], "extra_ids": []}}, headers=H("alice"))      # ticks only; no new search
        n = len(S["tokens"])
        for bad in ({"seed": [[0, n + 1]]}, {"seed": [[3, 2]]}, {"seed": [[0, 4], [3, 6]]}, {"seed": []},
                    {"slots": {"bad": {"v02:1": "absent"}}}, {"slots": {"s:x": {"v02:1": "maybe"}}},
                    {"extras_added": [{"wid": "v02:1", "span": [2, 2]}]}, {"confirmed": {"s:x": "v02:1"}}):
            assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": bad}, headers=H("alice")).status_code == 400, bad
        # the same parts as the automatic ones are stored as "automatic"
        auto = S["auto_clauses"]
        assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": {"seed": auto, "seed_confirmed": auto}}, headers=H("alice")).status_code == 200
        assert project.decision(pid, "parts")["seed"] is None and project.decision(pid, "search")["part_query"] == [0]
        # one part for the whole text: the candidates are scored on it again (no new search: that is the button), the selected witnesses stay
        assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": {"seed": [[0, n]], "seed_confirmed": [[0, n]]}}, headers=H("alice")).status_code == 200
        got = project.decision(pid, "parts")
        assert got["seed"] == [[0, n]] and got["keep_ids"] == sorted(set(sel) - {"v02:800316143"})
        assert project.decision(pid, "search")["part_query"] is None                    # the old ticks are reset
        pipeline.advance(pid)
        S2 = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        assert S2["clauses_by"] == "user" and [x["span"] for x in S2["clauses"]] == [[0, n]]
        assert S2["searched"] and S2["stale"] and S2["queries"] == S["queries"]                 # the corpora were not asked again; the candidates are scored on the new parts
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
                    {"units": {key: mdef(text="  ")}}, {"units": {"bad": {"بیتا": {"drop": True}}}}, {"units": [1]}):
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
        assert c.get("/icm/new").status_code == 200
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


def test_the_first_hadith_of_step_1_can_be_taken_away_and_the_next_takes_its_place():
    from icm import pipeline
    a, d = app()
    try:
        c = TestClient(a)
        W0, W1 = "v02:800316143", "v02:800316145"
        pid = c.post("/icm/api/project", json={"seed": W0}, headers=H("alice")).json()["pid"]
        pipeline.advance(pid)
        dec = lambda step, pl: c.post(f"/icm/api/project/{pid}/decide/{step}", json={"payload": pl}, headers=H("alice"))
        Sget = lambda: c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        press_search(c, pid, seeds=[W1])
        S1 = Sget()
        assert S1["seed"]["wid"] == W0 and S1["seeds"] == [W1]
        n = len(S1["tokens"])
        assert dec("parts", {"seed": [[0, 3], [3, n]], "seed_confirmed": [[0, 3], [3, n]]}).status_code == 200
        pipeline.advance(pid)
        # nothing else seeds the project: the first hadith goes, the second is the reference, the cut that was on the first starts again
        assert dec("search", {"anchor": W1, "seeds": [], "extra_ids": []}).status_code == 200
        assert project.decision(pid, "search")["anchor"] == W1 and project.decision(pid, "search")["seeds"] == []
        pp = project.decision(pid, "parts")
        assert pp["seed"] is None and pp["seed_confirmed"] is None and not pp["slots"]
        pipeline.advance(pid)
        S2 = Sget()
        assert S2["seed"]["wid"] == W1 and S2["seeds"] == [] and S2["stale"] and W1 not in {x["wid"] for x in S2["candidates"]}
        assert W0 not in {x["wid"] for x in S2["candidates"]}                               # taken away, not left as a candidate
        # the next decisions keep the new first hadith, and it cannot be edited or be its own further seed
        assert dec("search", {"extra_ids": []}).status_code == 200 and project.decision(pid, "search")["anchor"] == W1
        assert dec("search", {"edits": {W1: "x"}}).status_code == 400 and dec("search", {"seeds": [W1]}).status_code == 200
        assert project.decision(pid, "search")["seeds"] == [] and project.decision(pid, "search")["anchor"] == W1
        sel = c.post(f"/icm/api/project/{pid}/decide/selection", json={"payload": {"selected": [W1]}}, headers=H("alice"))
        assert sel.status_code == 200 and project.decision(pid, "selection")["selected"] == [W1]
        # the old first hadith can be added again as an ordinary one
        assert dec("search", {"seeds": [W0]}).status_code == 200 and project.decision(pid, "search")["seeds"] == [W0]
        pipeline.advance(pid)
        S3 = Sget()
        assert S3["seed"]["wid"] == W1 and S3["seeds"] == [W0] and "seed" in next(x for x in S3["candidates"] if x["wid"] == W0)["signals"]
        # a new press searches from the new first hadith
        press_search(c, pid, seeds=[W0])
        S4 = Sget()
        assert S4["seed"]["wid"] == W1 and not S4["stale"] and project.decision(pid, "search")["ret"]["anchor"] == W1
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_search_is_a_button_hadiths_can_be_added_edited_and_seeded_and_a_second_search_merges():
    from icm import pipeline
    a, d = app()
    try:
        c = TestClient(a)
        W0 = "v02:800316143"
        pid = c.post("/icm/api/project", json={"seed": W0}, headers=H("alice")).json()["pid"]
        pipeline.advance(pid)
        S0 = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        assert S0["searched"] is False and S0["queries"] == [] and S0["retrieved"] is False and len(S0["clauses"]) >= 1
        dec = lambda pl: c.post(f"/icm/api/project/{pid}/decide/search", json={"payload": pl}, headers=H("alice"))
        near = "v02:800316144"
        assert dec({"extra_ids": ["v02:1"]}).status_code == 400 and dec({"extra_ids": ["nope"]}).status_code == 400    # unknown / malformed ids
        assert dec({"seeds": [W0]}).status_code == 200 and project.decision(pid, "search")["seeds"] == []              # the seed is not its own further seed
        assert dec({"edits": {W0: "x"}}).status_code == 400 and dec({"edits": {near: "  "}}).status_code == 400
        # before any search: only the user's own hadiths are scored
        assert dec({"extra_ids": [near]}).status_code == 200
        pipeline.advance(pid)
        S1 = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        assert S1["searched"] is False and [x["wid"] for x in S1["candidates"] if "user" in x["signals"]] == [near]
        # the first press asks the corpora; what was added stays, the further seed is always a candidate and always selected
        press_search(c, pid, extra_ids=[near], seeds=["v02:800316145"])
        S2 = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        by = {x["wid"]: x for x in S2["candidates"]}
        assert S2["searched"] and S2["retrieved"] and S2["queries"] and not S2["stale"] and S2["seeds"] == ["v02:800316145"]
        assert "user" in by[near]["signals"] and "seed" in by["v02:800316145"]["signals"] and len(S2["candidates"]) > 3
        pick = [x["wid"] for x in S2["candidates"] if x["wid"] not in (near, "v02:800316145") and x["suggested"] in ("witness", "variant", "extended", "abridged")][:2] or \
               [x["wid"] for x in S2["candidates"] if x["wid"] not in (near, "v02:800316145")][:2]
        sel = lambda ws: c.post(f"/icm/api/project/{pid}/decide/selection", json={"payload": {"selected": ws}}, headers=H("alice"))
        assert sel(pick + [near]).status_code == 200
        pipeline.advance(pid)
        A = c.get(f"/icm/api/project/{pid}/runs/current/align").json()
        assert {"v02:800316145", near, *pick} <= {w["wid"] for w in A["witnesses"]}           # the further seed was not asked for in the selection
        # an edited text is scored as edited; its row shows the new score
        old = by[near]["score"]["cov_seed"]
        ed = {near: " ".join(S2["tokens"])}
        assert dec({"extra_ids": [near], "seeds": ["v02:800316145"], "edits": ed}).status_code == 200
        pipeline.advance(pid)
        S3 = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        row = next(x for x in S3["candidates"] if x["wid"] == near)
        assert row["edited"] and row["score"]["cov_seed"] > max(old, 0.95) and S3["queries"] == S2["queries"] and not S3["stale"]
        # removing the added hadith: it is a plain candidate again or gone, never still «user»
        assert dec({"extra_ids": [], "seeds": ["v02:800316145"]}).status_code == 200
        pipeline.advance(pid)
        S4 = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        assert not any("user" in x["signals"] for x in S4["candidates"]) and "edited" not in next((x for x in S4["candidates"] if x["wid"] == near), {})
        # a second press: «selected» keeps the ticked ones with the new findings, «rebuild» clears the ticks
        n_all = len(S4["candidates"])
        assert sel(pick).status_code == 200
        press_search(c, pid, "selected", seeds=["v02:800316145"])
        S5 = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        assert set(pick) <= {x["wid"] for x in S5["candidates"]} and project.decision(pid, "selection")["selected"] == sorted(set(pick) | {W0})
        press_search(c, pid, "rebuild", seeds=["v02:800316145"])
        assert project.decision(pid, "selection")["selected"] == [] and project.decision(pid, "selection")["reset"] is True
        P = c.get(f"/icm/api/project/{pid}").json()["project"]
        assert P["status"] == "draft" and P["step"] == "selection"                              # the ticks are gone: step 2 waits again
        assert c.post(f"/icm/api/project/{pid}/decide/search", json={"payload": {"go": True, "mode": "nope"}}, headers=H("alice")).status_code == 400
    finally:
        shutil.rmtree(d)


def test_a_project_made_before_the_button_keeps_searching_by_itself_and_its_keys():
    from icm import pipeline
    a, d = app()
    try:
        c = TestClient(a)
        W0 = "v02:800316143"
        pid = c.post("/icm/api/project", json={"seed": W0}, headers=H("alice")).json()["pid"]
        p = project.load(pid); p.pop("manual_search"); project.save(p)
        pipeline.advance(pid)
        S = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        assert S.get("searched") is not False and S["queries"] and S["candidates"]
        key = project.load(pid)["current_run"]
        # the first change takes the project into the button's way without computing the search again
        assert c.post(f"/icm/api/project/{pid}/decide/search", json={"payload": {"part_query": None, "extra_ids": []}}, headers=H("alice")).status_code == 200
        assert project.decision(pid, "search")["ret"]["legacy"] is True
        pipeline.advance(pid)
        assert project.load(pid)["current_run"].split("-")[1] != "" and c.get(f"/icm/api/project/{pid}/runs/current/search").json()["generated"] == S["generated"]
    finally:
        shutil.rmtree(d)


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
        press_search(c, pid)
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
        assert next(x for x in T["chains"] if x["aid"] == aid)["auto"] == spans                     # unedited: the automatic reading is the reading
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
        # no two places on one word: a span over an added place, two added places on one word; a dropped place frees its words
        over = {"t": s1["t"], "a": s1["a"], "b": s1["b"]}
        for bad in ({aid: {"add": [over], "span": {str(e1["i"]): over}}}, {aid: {"add": [over, dict(over, b=over["a"] + 1)]}}):
            assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": {"chain": bad}}, headers=H("alice")).status_code == 400, bad
        assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": {"chain": {aid: {"add": [over], "span": {str(e1["i"]): over}, "drop": [1000]}}}}, headers=H("alice")).status_code == 200
        for bad in ({aid: {"term": {"1": [3, 2]}}}, {aid: {"term": {"x": [0, 1]}}}, {aid: {"term": {"1": [0]}}}):     # a transmission phrase is [t, e), t <= e
            assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": {"chain": bad}}, headers=H("alice")).status_code == 400, bad
        assert c.post(f"/icm/api/project/{pid}/decide/parts", json={"payload": {"chain": {aid: {"term": {"1": [0, 0]}}}}}, headers=H("alice")).status_code == 200
        assert project.decision(pid, "parts")["chain"] == {aid: {"term": {"1": [0, 0]}}}
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
        # its transmission phrase: every word between it and the name before it, not reaching back over the start of the matn
        assert new["added"] and new["span"] == {"t": T["n_sanad"], "a": n - 2, "b": n} and new["raw"] == " ".join(T["words"][n - 2:n])
        moved = next(e for e in ch2["elems"] if e["i"] == e1["i"])
        assert moved["renamed"] and moved["span"]["b"] == s1["b"] + 1 and moved["name"] != "x"     # the span wins over the rename of the same place
        T2 = c.get(f"/icm/api/hadith/{W0}/tokens", params={"pid": pid}, headers=H("alice")).json()
        assert next(x for x in T2["chains"] if x["aid"] == aid)["spans"]["1000"] == new["span"]
        auto2 = next(x for x in T2["chains"] if x["aid"] == aid)["auto"]
        assert auto2[str(e1["i"])] == s1 and auto2["1000"] is None                                 # the moved place's automatic reading; an added one has none
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


def test_a_place_removed_on_the_main_page_leaves_the_chain():
    from icm import pipeline
    a, d = app()
    try:
        c = TestClient(a)
        W0 = "v02:800316143"
        pid = c.post("/icm/api/project", json={"seed": W0}, headers=H("alice")).json()["pid"]
        press_search(c, pid)
        S = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        c.post(f"/icm/api/project/{pid}/decide/selection", json={"payload": {"selected": [x["wid"] for x in S["candidates"][:2]]}}, headers=H("alice"))
        pipeline.advance(pid)
        I = c.get(f"/icm/api/project/{pid}/runs/current/identify").json()
        ch = next(x for x in I["chains"] if x["wid"] == W0)
        e = next(x for x in ch["elems"] if x.get("role") != "collector")
        occ, others = f"{ch['aid']}|{e['i']}", [x["i"] for x in ch["elems"] if x["i"] != e["i"]]
        assert "drops_in" in I and I["drops_in"] == []
        assert c.post(f"/icm/api/project/{pid}/decide/identities", json={"payload": {"drops": [occ]}}, headers=H("alice")).status_code == 200
        pipeline.advance(pid)
        I2 = c.get(f"/icm/api/project/{pid}/runs/current/identify").json()
        ch2 = next(x for x in I2["chains"] if x["aid"] == ch["aid"])
        assert [x["i"] for x in ch2["elems"]] == others                    # gone; the others keep their ids and their order
        assert I2["drops_in"] == [occ] and I2["chain_in"] == {}           # parts.chain untouched; the editor's pending check stays quiet
        assert all(o != [ch["aid"], e["i"]] for cd in I2["cards"].values() for o in cd["occurrences"])
        # restored on the main page and confirmed: back in the chain
        assert c.post(f"/icm/api/project/{pid}/decide/identities", json={"payload": {"drops": []}}, headers=H("alice")).status_code == 200
        pipeline.advance(pid)
        I3 = c.get(f"/icm/api/project/{pid}/runs/current/identify").json()
        assert [x["i"] for x in next(x for x in I3["chains"] if x["aid"] == ch["aid"])["elems"]] == [x["i"] for x in ch["elems"]]
    finally:
        shutil.rmtree(d)
