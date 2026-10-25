import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm import config
if not (os.path.exists(config.V02_DB) and os.path.exists(config.KG_DB)):
    print("corpus not present: skipped"); sys.exit(0)
from icm import evaluate


def test_sitta_recall_and_near_misses():
    r = evaluate.sitta()
    assert r["recall_preselected"] >= 0.95, r
    assert r["near_miss_ok"], r


if __name__ == "__main__":
    try:
        test_sitta_recall_and_near_misses(); print("PASS test_sitta_recall_and_near_misses")
    except AssertionError as e:
        print("FAIL", e); sys.exit(1)


def test_the_hadiths_of_the_list_are_equal_their_cuts_make_the_same_parts_whatever_the_order():
    from icm import search
    A, B, C = "v02:800316143", "v02:600055010", "v02:700126570"
    un = search.run(A, seeds=[B, C], retrieve=False)                                   # the button for the shared parts was not pressed: every hadith's cuts are parts of its own
    assert all(len(c["members"]) == 1 for c in un["clauses"]) and not un["join"]["built"] and un["join"]["fresh"]
    runs = [search.run(a, {"join": True}, seeds=[b, c], retrieve=False) for a, b, c in ((A, B, C), (C, A, B), (B, C, A))]
    assert runs[0]["join"]["built"] and not runs[0]["join"]["stale"]
    for r in runs[1:]:
        assert r["seed"]["wid"] == runs[0]["seed"]["wid"] and sorted(r["seeds"]) == sorted(runs[0]["seeds"])
        assert [c["text"] for c in r["clauses"]] == [c["text"] for c in runs[0]["clauses"]]
        assert [c["members"] for c in r["clauses"]] == [c["members"] for c in runs[0]["clauses"]]
        assert r["tokens"] == runs[0]["tokens"]
    # a part more than one hadith has is one part; every hadith's own words are its cut, in its own coordinates
    r = runs[0]
    assert any(len(c["members"]) == 3 for c in r["clauses"]) and set(r["cuts"]) == {A, B, C}
    for w, sp in r["cuts"].items():
        assert all(0 <= a < b <= r["cut_tokens"][w] for a, b in sp)
    # one hadith alone: its own cut, as before
    again = search.run(A, {"join": [[[A, 0, 1]]]}, seeds=[B, C], retrieve=False)       # groups that no longer fit the cuts are stale, not an error
    assert again["join"]["stale"]
    one = search.run(A, retrieve=False)
    assert one["seed"]["wid"] == A and one["seeds"] == [] and [c["members"][A] for c in one["clauses"]] == one["cuts"][A]
    # the user's cut of one hadith applies to it only
    n = r["cut_tokens"][B]
    cut = search.run(A, {"clauses": {B: [[0, n]]}}, seeds=[B, C], retrieve=False)
    assert cut["cuts"][B] == [[0, n]] and cut["cuts_by"][B] == "user" and cut["cuts_by"][A] == "auto" and cut["cuts"][A] == r["cuts"][A]
