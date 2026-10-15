"""narwiki API routes for per-version manual decisions (needs fastapi + httpx; skipped otherwise).

    ~/miniforge3/envs/dev2/bin/python3 -m pytest tests/test_narwiki_api.py -q
"""
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from narwiki import api, config, store, version  # noqa: E402
from test_narwiki_version import world  # noqa: E402

H = {"X-NARWIKI": "1"}


@pytest.fixture
def env():
    d = tempfile.mkdtemp(prefix="narapi_")
    c, path, p = world(d)
    c.close()
    old = (config.DB, config.VERSIONS_DIR, config.narrators_db, config.kg_db, config.dirayah_db)
    config.DB, config.VERSIONS_DIR = path, os.path.join(d, "versions")
    config.narrators_db = config.kg_db = config.dirayah_db = lambda: os.path.join(d, "none.sqlite")
    api._inited.clear()
    app = FastAPI()
    app.include_router(api.make_router(lambda req: dict(username="ali", is_admin=True)))
    yield TestClient(app), d, p
    config.DB, config.VERSIONS_DIR, config.narrators_db, config.kg_db, config.dirayah_db = old
    shutil.rmtree(d)


def test_decide_raw_unknown_and_header(env):
    t, d, p = env
    body = dict(a="noor:2", b="kg:20", decision="accept")
    assert t.post("/narrator/api/version/raw/decide", json=body, headers=H).status_code == 400
    assert t.post("/narrator/api/version/nope/decide", json=body, headers=H).status_code == 404
    assert t.post("/narrator/api/version/v1m/decide", json=body).status_code == 403
    assert t.post("/narrator/api/version/v1m/decide", json=dict(body, decision="maybe"), headers=H).status_code == 400
    assert t.post("/narrator/api/version/v1m/decide", json=dict(body, b="kg:999"), headers=H).status_code == 400


def test_decide_v1m_group_suggestions_person(env):
    t, d, p = env
    r = t.get("/narrator/api/versions").json()["items"]
    assert r[0]["name"] == "raw" and r[0]["editable"] is False
    assert [x for x in r if x["name"] == "v1m"][0]["editable"] is True
    r = t.post("/narrator/api/version/v1m/decide", json=dict(a="kg:20", b="noor:2", decision="accept", note="x"), headers=H)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] and j["state_before"] == "proposed" and j["state_after"] == "merged"
    assert sorted(m["id"] for m in j["group"]) == ["dirayah:2", "kg:20", "noor:2"]
    assert os.path.exists(os.path.join(config.VERSIONS_DIR, ".lock")) and os.path.exists(os.path.join(config.VERSIONS_DIR, "v1m.sqlite"))
    g = t.get("/narrator/api/version/v1m/group", params=dict(id="noor:2")).json()
    assert sorted(m["id"] for m in g["members"]) == ["dirayah:2", "kg:20", "noor:2"] and g["decisions"][0]["decision"] == "accept"
    assert g["edges"][0]["rule"] == "manual"
    # suggestions: noor:1/kg:10 (link_kg) is merged by the rules, the rest is proposed
    sm = t.get("/narrator/api/version/v1m/suggestions", params=dict(status="merged")).json()
    assert [x["version_state"] for x in sm["items"]] and any(x["a_ext"] == "noor:1" and x["b_ext"] == "kg:10" for x in sm["items"])
    sp = t.get("/narrator/api/version/v1m/suggestions").json()
    assert sp["total"] == len(sp["items"]) and all(x["version_state"] == "proposed" for x in sp["items"])
    # reject a proposed pair
    pair = sp["items"][0]
    r = t.post("/narrator/api/version/v1m/decide", json=dict(a=pair["a_ext"], b=pair["b_ext"], decision="reject"), headers=H).json()
    assert r["state_after"] == "rejected"
    assert t.get("/narrator/api/version/v1m/suggestions", params=dict(status="rejected")).json()["total"] == 1
    # person payload with ?version=
    pid = store.by_ext(store.connect(config.DB, readonly=True), "noor", "2")
    pj = t.get(f"/narrator/api/person/{pid}", params=dict(version="v1m")).json()
    assert len(pj["version_group"]) == 1 and "kg:20" in [m["id"] for m in pj["version_group"][0]["members"]]
    assert "version_group" not in t.get(f"/narrator/api/person/{pid}").json()
    # clear
    r = t.post("/narrator/api/version/v1m/decide", json=dict(a="noor:2", b="kg:20", decision="clear"), headers=H).json()
    assert r["state_before"] == "merged" and r["state_after"] == "proposed"


def test_rebuild_failure_rolls_back(env, monkeypatch):
    t, d, p = env
    assert t.post("/narrator/api/version/v1m/decide", json=dict(a="noor:2", b="kg:20", decision="accept"), headers=H).status_code == 200
    f = os.path.join(config.VERSIONS_DIR, "v1m.sqlite")
    before = open(f, "rb").read()

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(version, "rebuild", boom)
    r = t.post("/narrator/api/version/v1m/decide", json=dict(a="noor:2", b="kg:20", decision="reject"), headers=H)
    assert r.status_code == 500 and "boom" in r.text
    assert open(f, "rb").read() == before
    c = store.connect(config.DB, readonly=True)
    assert store.version_decision_for(c, "v1m", "noor:2", "kg:20")["decision"] == "accept"
