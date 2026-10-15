import os, sys, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ICM_OPEN"] = "0"                         # these tests check the user rules, which open mode switches off
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:                                  # the model code itself needs only numpy
    print("fastapi not installed: skipped"); sys.exit(0)
from icm import config, project, jobs

if not os.path.exists(config.V02_DB):
    print("corpus not present: skipped"); sys.exit(0)
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
