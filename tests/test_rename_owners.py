import json, os, sys, tempfile
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "scripts"))
from icm import config, project
import rename_owners_to_central as tool


def world(monkeypatch):
    d = tempfile.mkdtemp(prefix="icmrename_")
    monkeypatch.setattr(config, "CACHE_DIR", d)
    monkeypatch.setattr(config, "JOBS_DB", os.path.join(d, "jobs.sqlite"))
    a = project.create("v02:1", "ali")
    g = project.create("v02:2", "guest")
    project.add_decision(a["pid"], "search", {"x": 1}, "ali")
    project.add_decision(g["pid"], "search", {"x": 1}, "guest")
    m = os.path.join(d, "m.json")
    with open(m, "w") as f:
        json.dump({"workspace:1": {"username": "ali.j"}, "legacy:ali": {"username": "ali.j"}}, f)
    return a["pid"], g["pid"], m


def test_dry_run_changes_nothing_and_apply_renames_owners_versions_and_decisions(monkeypatch, capsys):
    a, g, m = world(monkeypatch)
    assert tool.main(["--mapping", m, "--guest-to", "root"]) == 0
    assert project.load(a)["owner"] == "ali" and "dry run" in capsys.readouterr().out
    assert tool.main(["--mapping", m, "--guest-to", "root", "--apply"]) == 0
    pa, pg = project.load(a), project.load(g)
    assert pa["owner"] == "ali.j" and all(b["user"] == "ali.j" for b in pa["branches"])
    assert pg["owner"] == "root" and pg["migrated_from"] == "guest"
    dec = project.read(os.path.join(project.pdir(a), "decisions", "search", "0001.json"), None)
    assert dec["user"] == "ali.j"
    assert project.read(os.path.join(project.pdir(g), "decisions", "search", "0001.json"), None)["user"] == "root"


def test_without_guest_to_guest_work_stays(monkeypatch):
    a, g, m = world(monkeypatch)
    tool.main(["--mapping", m, "--apply"])
    assert project.load(g)["owner"] == "guest" and project.load(a)["owner"] == "ali.j"
