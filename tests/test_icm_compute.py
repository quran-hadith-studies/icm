"""Compute-elsewhere endpoints: input, result validation, helper token. Needs fastapi, not the corpus."""
import json, os, shutil, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:
    print("fastapi not installed: skipped"); sys.exit(0)
from icm import client, config, jobs, project
from icm.api import make_router

TOKEN = "t" * 32
os.environ["ICM_HELPER_TOKEN"] = TOKEN
FX = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "mini_graph.json"), encoding="utf-8"))
USERS = {"alice": dict(username="alice", is_admin=False), "bob": dict(username="bob", is_admin=False)}
H = lambda u=None, tok=False: {"X-ICM": "1", **({"X-User": u} if u else {}), **({"X-ICM-Helper": TOKEN} if tok else {})}


def setup():
    d = tempfile.mkdtemp(prefix="icmcomp_")
    config.CACHE_DIR = d
    config.JOBS_DB = os.path.join(d, "jobs.sqlite")
    app = FastAPI()
    app.include_router(make_router(lambda r: USERS.get(r.headers.get("x-user", ""))))
    p = project.create("v02:1", "alice")
    pid = p["pid"]
    g = FX["graph"]
    seed = next(iter(FX["texts"]))
    project.stage_put(pid, "graph", "g1", g)
    project.stage_put(pid, "search", "s1", dict(seed=dict(wid=seed, matn="x"), candidates=[dict(wid=w, matn=t) for w, t in FX["texts"].items()]))
    rid = project.write_run(pid, project.versions(pid), dict(search="s1", graph="g1"))
    p.update(status="awaiting", step="analysis", current_run=rid, graph_hash="g1",
             awaiting=dict(key="k1", graph_hash="g1", preset="tiny", compute="helper", claimed=None))
    project.save(p)
    return TestClient(app), pid, d


def real_result():
    info = json.loads(client.init(json.dumps(dict(graph=FX["graph"], texts=FX["texts"], preset="tiny", seed=0))))
    light = [json.loads(client.source(i)) for i in range(len(info["sources"]))]
    best = max(range(len(light)), key=lambda i: -1e300 if light[i]["log_Z"] is None else light[i]["log_Z"])
    out = json.loads(client.finish(json.dumps(light), client.graphs(best), 1.0, json.dumps(dict(kind="test"))))
    return dict(key="k1", results=out["results"], viz=out["bundle"], provenance=dict(workers=2, seconds=1))


def test_input_access_and_result_flow():
    c, pid, d = setup()
    try:
        assert c.get(f"/icm/api/project/{pid}/compute-input").status_code == 401
        assert c.get(f"/icm/api/project/{pid}/compute-input", headers=H("bob")).status_code == 401      # not the owner
        r = c.get(f"/icm/api/project/{pid}/compute-input", headers=H("alice"))
        assert r.status_code == 200 and r.json()["preset"] == "tiny" and r.json()["graph"]["attestations"]
        assert c.get(f"/icm/api/project/{pid}/compute-input", headers=H(tok=True)).status_code == 200  # helper token
        res = real_result()
        bad = json.loads(json.dumps(res)); bad["results"]["posterior"] = {k: 0.9 for k in bad["results"]["posterior"]}
        assert c.post(f"/icm/api/project/{pid}/compute-result", json=bad, headers=H("alice")).status_code == 400   # not normalised
        assert c.post(f"/icm/api/project/{pid}/compute-result", json=res, headers=H("bob")).status_code == 403
        wrong = dict(res, key="other")
        assert c.post(f"/icm/api/project/{pid}/compute-result", json=wrong, headers=H("alice")).status_code == 409
        ok = c.post(f"/icm/api/project/{pid}/compute-result", json=res, headers=H("alice"))
        assert ok.status_code == 200, ok.text
        stored = project.stage_get(pid, "infer", "k1")
        assert stored["results"]["provenance"]["kind"] == "browser" and stored["results"]["provenance"]["user"] == "alice"
        assert jobs.active(pid)["status"] == "queued"                # the worker finalises
    finally:
        shutil.rmtree(d)


def test_helper_claim_and_token():
    c, pid, d = setup()
    try:
        assert c.post("/icm/api/helper/claim", headers=H("alice")).status_code == 401          # a user is not a helper
        r = c.post("/icm/api/helper/claim", headers=H(tok=True))
        assert r.status_code == 200 and r.json()["pid"] == pid
        assert c.post("/icm/api/helper/claim", headers=H(tok=True)).json() is None              # claimed: not handed out twice
        res = real_result()
        ok = c.post(f"/icm/api/project/{pid}/compute-result", json=res, headers=H(tok=True))
        assert ok.status_code == 200
        assert project.stage_get(pid, "infer", "k1")["results"]["provenance"]["kind"] == "helper"
    finally:
        shutil.rmtree(d)


def test_short_token_is_refused():
    c, pid, d = setup()
    try:
        os.environ["ICM_HELPER_TOKEN"] = "short"
        assert c.post("/icm/api/helper/claim", headers={"X-ICM-Helper": "short", "X-ICM": "1"}).status_code == 401
    finally:
        os.environ["ICM_HELPER_TOKEN"] = TOKEN
        shutil.rmtree(d)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception as e:
                import traceback; traceback.print_exc(); fails += 1; print("FAIL", name, repr(e))
    sys.exit(1 if fails else 0)
