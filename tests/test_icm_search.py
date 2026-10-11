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
