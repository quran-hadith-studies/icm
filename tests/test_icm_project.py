import os, sys, tempfile, shutil, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm import config, jobs, project


def _tmp():
    d = tempfile.mkdtemp(prefix="icmtest_")
    config.CACHE_DIR = d
    config.JOBS_DB = os.path.join(d, "jobs.sqlite")
    return d


def test_decisions_are_append_only():
    d = _tmp()
    try:
        p = project.create("v02:1", "u")
        assert project.versions(p["pid"]) == dict(search=0, selection=0, identities=0, phylogeny=0, parts=0, narrators=0)
        n1 = project.add_decision(p["pid"], "selection", dict(selected=["v02:1", "v02:2"]), "u")
        n2 = project.add_decision(p["pid"], "selection", dict(selected=["v02:1"]), "u", "removed one")
        assert (n1, n2) == (1, 2)
        assert project.decision(p["pid"], "selection", 1)["selected"] == ["v02:1", "v02:2"]     # old version intact
        assert project.decision(p["pid"], "selection")["selected"] == ["v02:1"]
        assert project.decision(p["pid"], "selection")["note"] == "removed one"
    finally:
        shutil.rmtree(d)


def test_run_id_keeps_its_old_shape_until_a_newer_step_is_decided():
    hs = {"search": "a"}
    v = dict(search=1, selection=2, identities=0, phylogeny=0, parts=0, narrators=0)
    old = project.run_id(v, hs)
    assert old.startswith("0001.0002.0000.0000-")                 # four numbers: neither parts nor narrators decided
    assert project.run_id(dict(v, parts=3), hs).startswith("0001.0002.0000.0000.0003-")
    assert project.run_id(dict(v, narrators=1), hs).startswith("0001.0002.0000.0000.0000.0001-")
    assert project.run_id(dict(v, parts=3, narrators=1), hs).startswith("0001.0002.0000.0000.0003.0001-")
    assert project.run_id(dict(v, parts=3), hs).split("-")[1] == old.split("-")[1]      # only the numbers differ


def test_stage_files_are_content_addressed_and_never_overwritten():
    d = _tmp()
    try:
        p = project.create("v02:1", "u")
        project.stage_put(p["pid"], "search", "abc", dict(x=1))
        project.stage_put(p["pid"], "search", "abc", dict(x=2))          # ignored
        assert project.stage_get(p["pid"], "search", "abc") == dict(x=1)
    finally:
        shutil.rmtree(d)


def test_runs_share_unchanged_stages():
    d = _tmp()
    try:
        p = project.create("v02:1", "u")
        v1 = dict(search=0, selection=1, identities=0, phylogeny=0)
        v2 = dict(search=0, selection=1, identities=1, phylogeny=0)
        r1 = project.write_run(p["pid"], v1, dict(search="s1", align="a1"))
        r2 = project.write_run(p["pid"], v2, dict(search="s1", align="a1", graph="g1"))
        assert r1 != r2 and len(project.runs(p["pid"])) == 2
        assert project.runs(p["pid"])[0]["stages"]["search"] == project.runs(p["pid"])[1]["stages"]["search"] == "s1"
    finally:
        shutil.rmtree(d)


def test_hadith_index_lists_projects_of_selected_witnesses():
    d = _tmp()
    try:
        p = project.create("v02:1", "u")
        project.add_decision(p["pid"], "selection", dict(selected=["v02:1", "v02:77"]), "u")
        project.refresh_indexes()
        assert project.projects_of("v02:77")[0]["pid"] == p["pid"]
        assert project.projects_of("v02:99") == []
    finally:
        shutil.rmtree(d)


def test_bad_project_id_rejected():
    d = _tmp()
    try:
        try:
            project.pdir("../etc")
            assert False
        except ValueError:
            pass
    finally:
        shutil.rmtree(d)


def test_job_queue():
    d = _tmp()
    try:
        a = jobs.enqueue("p1")
        assert jobs.enqueue("p1") == a                         # one queued job per project
        b = jobs.enqueue("p2")
        j = jobs.claim()
        assert j["id"] == a and jobs.active("p1")["status"] == "running"
        jobs.progress(a, "search", 0.5, "x")
        assert jobs.active("p1")["progress"]["frac"] == 0.5
        jobs.finish(a)
        assert jobs.active("p1") is None
        assert jobs.claim()["id"] == b and jobs.claim() is None
    finally:
        shutil.rmtree(d)


def test_stale_jobs_requeued():
    d = _tmp()
    try:
        a = jobs.enqueue("p1")
        jobs.claim()
        c = jobs.conn()
        with c:
            c.execute("update jobs set heartbeat=heartbeat-5000")
        assert jobs.requeue_stale() == 1 and jobs.claim()["id"] == a
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
