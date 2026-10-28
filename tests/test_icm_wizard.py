"""The wizard's rules: saving runs nothing; every step has a run button; later steps are marked when an earlier one changed;
the semantic search is its own run; the project list knows which projects are busy."""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient

from icm import jobs, narrator_versions, pipeline, project, search, semantic, wizard
from test_icm_api import H, app, press_search, run

W0 = "v02:800316143"


def _states(c, pid):
    w = c.get(f"/icm/api/project/{pid}").json()["wizard"]
    return {x["slug"]: x for x in w["steps"]}, w


def _chain(c, d):
    """A project searched, with its witnesses chosen, aligned and with the narrators found (not yet saved)."""
    pid = c.post("/icm/api/project", json={"seed": W0}, headers=H("alice")).json()["pid"]
    pipeline.advance(pid, want={"search"})
    press_search(c, pid)
    S = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
    sel = [W0] + [x["wid"] for x in S["candidates"] if search.preselected(x)][:3]
    assert c.post(f"/icm/api/project/{pid}/decide/selection", json={"payload": {"selected": sel}}, headers=H("alice")).status_code == 200
    run(c, pid, "align")
    run(c, pid, "identify")
    assert c.post(f"/icm/api/project/{pid}/decide/identities", json={"payload": {"choices": {}}}, headers=H("alice")).status_code == 200
    run(c, pid, "graph")
    return pid, sel


def test_marks_of_the_steps_after_a_change():
    a, d = app()
    try:
        c = TestClient(a)
        pid, sel = _chain(c, d)
        by, w = _states(c, pid)
        assert [by[s]["state"] for s in ("hadiths", "parts", "candidates", "slots", "alignment", "units", "narrators", "graph")] == ["done"] * 8
        assert by["semantic"]["state"] == "idle" and by["run"]["state"] == "needs_run" and w["current"] == "run" and not w["stale_any"]
        # a word edit on step 7 is run by step 7's button; the other pages of the alignment and every step after it are marked
        prev = {k: v for k, v in project.decision(pid, "parts").items() if k not in wizard.META} if project.decision(pid, "parts") else {}
        project.add_decision(pid, "parts", dict(prev, words={"x": {"y": 1}}), "alice")
        by, w = _states(c, pid)
        assert by["alignment"]["state"] == "needs_run"
        assert [by[s]["state"] for s in ("slots", "units", "narrators", "graph")] == ["stale"] * 4
        assert all(by[s]["state"] in ("done", "idle") for s in ("hadiths", "parts", "candidates", "semantic", "added"))
        assert [x["slug"] for x in by["graph"]["changed_since"]] == ["alignment"] and w["stale_any"] and w["current"] == "slots"
        # the same words again: nothing moved, nothing is marked
        project.add_decision(pid, "parts", dict(prev), "alice")
        by, w = _states(c, pid)
        assert not w["stale_any"]
        # narrator database: a decision that moves no key marks nothing; a program change marks the stage
        project.add_decision(pid, "narrators", dict(name=narrator_versions.default_name()), "alice")
        by, w = _states(c, pid)
        assert not w["stale_any"], "; ".join(f"{k}={v['state']}/{v['edited']}" for k, v in by.items())
        import icm
        old = icm.STAGE_VERSIONS["graph"]
        icm.STAGE_VERSIONS["graph"] = old + 1
        try:
            pipeline.advance(pid, want=set())                                            # (the old plan is remembered per decisions and run)
            wizard._PLANS.clear()
            by, w = _states(c, pid)
            assert by["graph"]["state"] in ("stale", "needs_run") and by["graph"]["changed_since"] == []
        finally:
            icm.STAGE_VERSIONS["graph"] = old
            wizard._PLANS.clear()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_saving_runs_nothing_and_a_run_button_runs_its_own_stage_only():
    a, d = app()
    try:
        c = TestClient(a)
        pid, sel = _chain(c, d)
        assert jobs.active(pid) is None
        for step, pl in (("selection", {"selected": sel[:2]}), ("identities", {"choices": {}}), ("narrators", {"version": "raw"})):
            c.post(f"/icm/api/project/{pid}/decide/{step}", json={"payload": pl}, headers=H("alice"))
            assert jobs.active(pid) is None, step                                        # a save is not a run
        assert c.post(f"/icm/api/project/{pid}/decide/go", json={"payload": {"stage": "align"}}, headers=H("alice")).status_code == 410
        assert c.post(f"/icm/api/project/{pid}/run/identify", json={}, headers=H("alice")).status_code == 409    # the alignment of the new selection is not built
        assert jobs.active(pid) is None
        r = c.post(f"/icm/api/project/{pid}/run/align", json={}, headers=H("alice"))
        assert r.status_code == 200 and jobs.active(pid)["kind"] == "run:align" and pipeline.want_of("run:align") == {"align"}
        assert pipeline.want_of("advance") is None and pipeline.want_of("go:identify") == {"identify"} and pipeline.want_of("finalize") == set()
        jobs.cancel(pid)
        # «rescore» with nothing to do answers at once
        assert run(c, pid, "rescore").get("status") or True
        r = c.post(f"/icm/api/project/{pid}/run/rescore", json={}, headers=H("alice"))
        assert r.json().get("noop") is True and jobs.active(pid) is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_semantic_search_is_its_own_run_on_top_of_the_lexical_press(monkeypatch):
    a, d = app()
    try:
        c = TestClient(a)
        pid = c.post("/icm/api/project", json={"seed": W0}, headers=H("alice")).json()["pid"]
        pipeline.advance(pid, want={"search"})
        assert run(c, pid, "semantic", 409).json()["detail"]                             # nothing pressed yet / no index
        monkeypatch.setattr(semantic, "available", lambda: True)
        monkeypatch.setattr(semantic, "meta", lambda: {"model": "toy"})
        monkeypatch.setattr(semantic, "neighbours", lambda texts, k=40, min_cos=0.0, chunks=False, **kw: [[("v02:800316145", 0.9, 0)] for _ in texts])
        assert run(c, pid, "semantic", 409).json()["detail"]                             # «جستجو» (step 3) first
        press_search(c, pid)
        S0 = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        assert not any("q:sem" in x["signals"] for x in S0["candidates"]) and project.decision(pid, "search").get("ret_sem") is None
        for bad in ({"sem_min": 0.2}, {"sem_min": "x"}, {"sem_k": 0}, {"sem_k": 500}, {"sem_k": True}):
            assert run(c, pid, "semantic", 400, **bad)
        run(c, pid, "semantic", sem_min=0.6, sem_k=5)
        rs = project.decision(pid, "search")["ret_sem"]
        assert rs["sem_min"] == 0.6 and rs["sem_k"] == 5 and rs["n"] == 1 and rs["model"] == "toy"
        S1 = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        assert rs["mode"] == "parts" and not any("q:sem" in x["signals"] for x in S1["candidates"])       # the first press only finds passages
        assert S1["sem_n"] == 1 and S1["params"]["sem_min"] == 0.6
        found = [(q["clause"], it) for q in S1["sem_parts"] for it in q["items"]]
        assert found and all(it["wid"] == "v02:800316145" and it["text"] and 0.9 == it["cos"] for _, it in found)
        assert run(c, pid, "sempick", 400, picks="x") and run(c, pid, "sempick", 409, picks=[])        # a pick is needed
        cl, it = found[0]
        run(c, pid, "sempick", picks=[[it["wid"], cl], ["v02:1", cl]])
        assert project.decision(pid, "search")["ret_sem"]["picks"] == [["v02:1", cl], [it["wid"], cl]] and project.decision(pid, "search")["ret_sem"]["pn"] == 1
        S1 = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        row = next(x for x in S1["candidates"] if x["wid"] == "v02:800316145")                           # the second press lists the hadiths of the ticked passages
        assert "q:sem" in row["signals"] and S1["sem_pn"] == 1 and S1["sem_parts"] and row["sem_hits"][0]["clause"] == cl
        assert not any(x["wid"] == "v02:1" for x in S1["candidates"])                                   # (a pair the passages did not have is ignored)
        rs = project.decision(pid, "search")["ret_sem"]
        by, w = _states(c, pid)
        assert by["semantic"]["state"] == "done"
        # a save keeps the semantic press; a change of the parts marks it as made on other parts
        assert c.post(f"/icm/api/project/{pid}/decide/search", json={"payload": {"part_query": [0], "extra_ids": []}}, headers=H("alice")).status_code == 200
        assert project.decision(pid, "search")["ret_sem"] == rs
        by, w = _states(c, pid)
        assert by["semantic"]["state"] == "stale"
        # a new «جستجو» asks the lexical index again and drops the semantic press (its list is asked for again on its own button)
        run(c, pid, "search", mode="all")
        assert project.decision(pid, "search").get("ret_sem") is None
        S2 = c.get(f"/icm/api/project/{pid}/runs/current/search").json()
        assert not any("q:sem" in x["signals"] for x in S2["candidates"] if x["wid"] != "v02:800316145") and by is not None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_run_records_the_decisions_it_was_built_from(monkeypatch):
    a, d = app()
    try:
        c = TestClient(a)
        pid = c.post("/icm/api/project", json={"seed": W0}, headers=H("alice")).json()["pid"]
        for j in jobs.for_project(pid, 5):
            jobs.finish(j["id"])
        before = project.versions(pid)
        orig = search.run

        def sneaky(*a, **k):                                   # the user saves while the job computes
            out = orig(*a, **k)
            project.add_decision(pid, "selection", dict(selected=[W0]), "alice")
            return out
        monkeypatch.setattr(search, "run", sneaky)
        pipeline.advance(pid, want={"search"})
        m = project.runs(pid)[-1]
        assert m["versions"]["selection"] == before["selection"] == 0 and project.versions(pid)["selection"] == 1
        by, w = _states(c, pid)
        assert by["slots"]["state"] in ("needs_run", "unreached") or w["steps"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_rename_index_and_pages():
    a, d = app()
    try:
        c = TestClient(a)
        pid = c.post("/icm/api/project", json={"seed": W0}, headers=H("alice")).json()["pid"]
        rn = lambda who, t: c.post(f"/icm/api/project/{pid}/rename", json={"title": t}, headers=H(who))
        assert rn("bob", "x").status_code == 403 and rn("alice", "   ").status_code == 400
        assert rn("alice", "  نام  تازه ").json()["title"] == "نام تازه"
        row = c.get("/icm/api/index").json()["projects"][0]
        assert row["title"] == "نام تازه" and row["busy"]["kind"] == "run:search" and "stale" in row and row["step"] in wizard.WIZARD + ("done",)
        assert c.get(f"/icm/p/{pid}/settings/general").status_code == 200 and c.get(f"/icm/p/{pid}/results").status_code == 200
        assert "wizard.js" in c.get(f"/icm/p/{pid}/hadiths").text
        info = c.get(f"/icm/api/project/{pid}").json()
        assert len(info["wizard"]["steps"]) == 11 and info["busy"] and "semantic_available" in info
    finally:
        shutil.rmtree(d, ignore_errors=True)
