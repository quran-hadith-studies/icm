"""Evaluation of the search stage.

  recall     the substantive witnesses of the hand-built diagram (tests/fixtures/sitta_witnesses.json)
             must be found AND pre-selected (``search.PRESELECTED``)
  near-miss  hadiths that look alike but are another hadith must not be pre-selected
  oracle     for random v02 consensus sets (exact-duplicate + alignment folds), the members of a set
             found from one member: recall of the other members (reported, not asserted: sets can
             contain related-but-different texts)
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
from typing import Dict, List

from . import config, corpus, search

FIXTURE = os.path.join(config.HERE, "tests", "fixtures", "sitta_witnesses.json")
NEAR_MISSES = ["kg:272683", "kg:268518"]      # «لا يدخل الجنة مكذب بقدر», a one-clause fragment


def sitta() -> Dict:
    fx = json.load(open(FIXTURE, encoding="utf-8"))
    r = search.run(fx["seed"])
    by = {c["wid"]: c for c in r["candidates"]}
    for c in r["candidates"]:
        for w in c.get("copies", []):
            by.setdefault(w, c)
    exp = [f for f in fx["witnesses"] if f["expected"]]
    found = [f for f in exp if f["best_wid"] in by]
    pre = [f for f in found if by[f["best_wid"]]["suggested"] in search.PRESELECTED]
    miss = {w: (by[w]["suggested"] if w in by else "not found") for w in NEAR_MISSES}
    return dict(expected=len(exp), found=len(found), preselected=len(pre),
                recall_found=len(found) / max(len(exp), 1), recall_preselected=len(pre) / max(len(exp), 1),
                near_misses=miss, near_miss_ok=all(v not in search.PRESELECTED for v in miss.values()),
                candidates=len(r["candidates"]), seconds=r["seconds"])


def consensus_oracle(n: int = 30, seed: int = 0) -> Dict:
    c = corpus.conn("consensus")
    sets = [r[0] for r in c.execute("select cid from cset where size between 3 and 30")]
    random.Random(seed).shuffle(sets)
    recalls: List[float] = []
    for cid in sets[:n]:
        members = [r[0] for r in c.execute("select hid from membership where cid=?", (cid,))]
        wid = f"v02:{members[0]}"
        if corpus.get(wid) is None:
            continue
        r = search.run(wid, dict(rounds=1))
        got = {c_["wid"] for c_ in r["candidates"]}
        for c_ in r["candidates"]:
            got.update(c_.get("copies", []))
        got.add(r["seed"]["wid"])
        got.update(r["seed"].get("copies", []))
        rest = {f"v02:{m}" for m in members[1:]}
        recalls.append(len(rest & got) / max(len(rest), 1))
    return dict(sets=len(recalls), mean_recall=sum(recalls) / max(len(recalls), 1),
                share_full=sum(1 for x in recalls if x >= 0.999) / max(len(recalls), 1))
