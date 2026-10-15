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
    pre = [f for f in found if search.preselected(by[f["best_wid"]])]
    miss = {w: (by[w]["suggested"] if w in by else "not found") for w in NEAR_MISSES}
    return dict(expected=len(exp), found=len(found), preselected=len(pre),
                recall_found=len(found) / max(len(exp), 1), recall_preselected=len(pre) / max(len(exp), 1),
                near_misses=miss, near_miss_ok=all(w not in by or not search.preselected(by[w]) for w in NEAR_MISSES),
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


def chains(n: int = 3000, seed: int = 0) -> Dict:
    """How well the two automatic readings of an isnad recover Noor's own linked isnad (``noor_slots``, the
    reference): hadith.ai's narrator tags (``identity.hadithai_chain``) and the rule-based parser
    (``identity.text_chain``), on random Noor hadiths that have both a linked isnad and hadith.ai tags.

      recall      share of Noor's narrators found, in order, in a reading (name-token cover ``identity._cov``
                  >= 0.5 either way). Noor's linked chain can hold steps the text does not write (al-Tusi's path
                  to al-Kulayni), which lowers both readings alike
      precision   share of a reading's names that are narrators of Noor's chain, in order
      no_chain    share of hadiths for which a reading has no narrator at all
    Noor's collector slot, the words Noor folded into a connective (``hidden``) and both readings' collector
    (the work's author, which they add) are left out."""
    from . import hadithai, identity
    from narrators.common import name_tokens
    nc = identity._nconn()
    ids = [r[0] for r in nc.execute("select distinct hadith_id from slot where ravi_id is not null")]
    nc.close()
    random.Random(seed).shuffle(ids)
    acc = {k: dict(n=0, hit=0, gold=0, got=0, used=0, empty=0) for k in ("hadithai", "text")}
    same = lambda a, b: max(identity._cov(a, b), identity._cov(b, a)) >= 0.5
    used = 0
    for nid in ids:
        if used >= n:
            break
        if not hadithai.row(nid):
            continue
        rec = corpus.get(f"v02:{config.NOOR_OFFSET + nid}")
        if rec is None:
            continue
        ref = identity.noor_chains(rec)
        h = identity.hadithai_chain(rec)
        if not ref or not h:
            continue
        used += 1
        gold = [name_tokens(e["name"]) for e in ref[0]["elems"] if e.get("role") != "collector" and not e.get("hidden")]
        for k, ch in (("hadithai", h), ("text", identity.text_chain(rec))):
            got = [name_tokens(e["name"]) for e in (ch or {}).get("elems", []) if e.get("role") != "collector" and e["kind"] != "collector"]
            a = acc[k]
            a["n"] += 1
            a["empty"] += not got
            j = hit = 0
            for g in gold:                       # in-order matching: each reading name is used once
                for jj in range(j, len(got)):
                    if same(g, got[jj]):
                        hit += 1
                        j = jj + 1
                        break
            a["hit"] += hit
            a["gold"] += len(gold)
            a["got"] += len(got)
    out = {}
    for k, a in acc.items():
        r, p = a["hit"] / max(a["gold"], 1), a["hit"] / max(a["got"], 1)
        out[k] = dict(hadiths=a["n"], recall=round(r, 3), precision=round(p, 3), f1=round(2 * r * p / max(r + p, 1e-9), 3),
                      no_chain=round(a["empty"] / max(a["n"], 1), 3))
    return out


def mentions(folds: int = 5) -> Dict:
    """Precision of the context suggestions of ``hadithai.sqlite`` (``icm.build.hadithai.suggest``), leave-book-out:
    the certain mentions (``link = 'noor_slot'`` with a RaviId) of the books of one fold are hidden, the model is
    learnt from the other books, and each hidden mention's suggestion is compared with Noor's RaviId. Books are
    split by a hash of their title, so a book and the chains it shares with others never leak into its own test."""
    import zlib
    from .build.hadithai import SUPPORT, context_model, suggest
    c = sqlite3.connect(f"file:{config.HADITHAI_DB}?mode=ro&immutable=1", uri=True)
    title = dict(c.execute("select id, title from book"))
    rows = c.execute("select h.book, m.nkey, m.pk, m.nk, m.ravi_id from mention m join hadith h on h.hid=m.hid "
                     "where m.link='noor_slot' and m.ravi_id is not null").fetchall()
    c.close()
    fold = {b: zlib.crc32((t or "").encode()) % folds for b, t in title.items()}
    acc = {t: [0, 0] for t, _ in SUPPORT}
    n = none = 0
    for f in range(folds):
        model = context_model((k, p, nx, r) for b, k, p, nx, r in rows if fold[b] != f)
        for b, k, p, nx, r in rows:
            if fold[b] != f:
                continue
            n += 1
            s = suggest(model, k, p, nx)
            if s is None:
                none += 1
            else:
                acc[s[1]][0] += s[0] == r
                acc[s[1]][1] += 1
    out = dict(mentions=n, suggested=round(1 - none / max(n, 1), 3))
    for t, (ok, tot) in acc.items():
        out[t] = dict(n=tot, precision=round(ok / max(tot, 1), 4))
    ok = sum(a[0] for a in acc.values())
    out["all"] = dict(n=n - none, precision=round(ok / max(n - none, 1), 4))
    return out
