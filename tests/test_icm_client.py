import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm import client, infer
from icm.build import model_zip

FX = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "mini_graph.json"), encoding="utf-8"))


def test_committed_zip_matches_sources():
    assert model_zip.zip_digest() == model_zip.digest(), "run: python3 -m icm build model"


def test_split_run_equals_whole_run():
    """Per-source evidence computed independently and assembled = the one-process run (same process, same seed)."""
    whole = infer.run(FX["graph"], FX["texts"], preset="tiny")["results"]
    info = json.loads(client.init(json.dumps(dict(graph=FX["graph"], texts=FX["texts"], preset="tiny", seed=0))))
    light = [json.loads(client.source(i)) for i in range(len(info["sources"]))]
    best = max(range(len(light)), key=lambda i: -1e300 if light[i]["log_Z"] is None else light[i]["log_Z"])
    split = json.loads(client.finish(json.dumps(light), client.graphs(best), 1.0, json.dumps(dict(kind="t"))))["results"]
    assert split["map_source"] == whole["map_source"]
    for k, v in whole["log_Z"].items():
        a, b = v, split["log_Z"][k]
        assert (a is None or a == float("-inf")) and b is None or abs(a - b) < 1e-9, (k, a, b)
    assert abs(sum(split["posterior"].values()) - 1) < 1e-9


def test_max_paths():
    """Every witness gets a path from the best source to its collector: modal over the particles inside a run,
    marginal (no particles) for a bundle alone; both modes give the same keys."""
    b = infer.run(FX["graph"], FX["texts"], preset="tiny")["bundle"]
    mp = b["max_paths"]
    assert mp["method"] == "particles" and mp["n_particles"] and mp["source"] == b["map_source"]
    assert set(mp["paths"]) == {a["id"] for a in b["attestations"]}
    for a in b["attestations"]:
        x = mp["paths"][a["id"]]
        assert x["path"][0] == b["map_source"] and x["path"][-1] == a["anchor"], x
        assert 0 < x["mass"] <= 1 + 1e-9 and len(x["edge_probs"]) == len(x["path"]) - 1
    lazy = infer.max_paths(b)
    assert lazy["method"] == "marginal" and set(lazy["paths"]) == set(mp["paths"])
    for aid, x in lazy["paths"].items():
        assert x["mass"] is None and (x["path"] is None or x["path"][-1] == mp["paths"][aid]["anchor"])


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception as e:
                import traceback; traceback.print_exc(); fails += 1; print("FAIL", name, repr(e))
    sys.exit(1 if fails else 0)
