"""narwiki.version: policies applied to a temporary wiki (no source databases needed).

    python3 -m pytest tests/test_narwiki_version.py -q
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from narwiki import seed, store, version  # noqa: E402
from test_narwiki_store import RECS, fresh  # noqa: E402

EXTRA = [
    dict(kind="kg", ext_id="11", display_name="سفيان الثوري", n_narr=10, fields=[("name", "سفيان الثوري", "kg")]),
    dict(kind="noor", ext_id="3", display_name="زرارة", n_narr=1, fields=[("name", "زرارة", "noor")]),
    dict(kind="noor", ext_id="4", display_name="زراره", n_narr=1, fields=[("name", "زراره", "noor")]),
    dict(kind="dirayah", ext_id="2", display_name="زرارة بن أعين", n_narr=1, fields=[("name", "زرارة بن أعين", "dirayah")]),
]
V1 = {"name": "t", "wiki_merges": "all",
      "noor_kg": {"kinds": ["link_kg", "name_chrono"], "confidence": ["high"], "min_ctx": 1, "max_death_gap": 10,
                  "require_dated_or_ctx2": True, "matching": "one_to_one"},
      "dup": {"kinds": ["noor_dup", "kg_dup"], "require_same_name_key": True, "max_death_gap": 5, "min_shared_if_undated": 2},
      "dirayah": {"suggested_same_id_and_name": True, "max_name_conflict": False}}


def world(d):
    """RECS persons: a noor:1, b kg:10, z noor:2, h kg:20; plus kg:11, noor:3, noor:4, dirayah:2."""
    c, path, (a, b, z, h) = fresh(d)
    with c:
        k11, n3, n4, d2 = [seed.insert_record(c, r) for r in EXTRA]
    with c:
        store.add_suggestion(c, a, b, "link_kg", 2.2, dict(confidence="high", ctx=4, death_gap=None))
        store.add_suggestion(c, a, k11, "name_chrono", 1.5, dict(confidence="high", ctx=2, death_gap=None))   # loses: noor:1 taken
        store.add_suggestion(c, z, h, "name_chrono", 1.1, dict(confidence="medium", ctx=0, death_gap=2))     # medium: excluded
        store.add_suggestion(c, n3, n4, "noor_dup", 0.9, dict(same_name_key=True, death_gap=2, shared_neighbours=0))
        store.add_suggestion(c, z, d2, "link_dirayah", 1.0, dict(same_id=True, same_name=True, name_conflict=False))
    return c, path, dict(a=a, b=b, z=z, h=h, k11=k11, n3=n3, n4=n4, d2=d2)


def built(d, policy, path, name="o.sqlite"):
    out = os.path.join(d, name)
    version.build(policy, path, out, log=lambda m: None)
    return out, sqlite3.connect(out)


def test_v1m_matching_and_conflicts():
    d = tempfile.mkdtemp(prefix="narver_")
    try:
        c, path, p = world(d)
        out, o = built(d, V1, path)
        grp = lambda kind, e: o.execute("select person_id from ext where kind=? and ext_id=?", (kind, e)).fetchone()[0]
        assert grp("noor", "1") == grp("kg", "10")                          # high pair merged
        assert grp("kg", "11") != grp("noor", "1")                          # second claim on noor:1 refused
        assert o.execute("select count(*) from conflict where reason='one_to_one'").fetchone()[0] == 1
        assert grp("noor", "2") != grp("kg", "20")                          # medium excluded
        assert grp("noor", "3") == grp("noor", "4")                         # dup, death_gap 2
        assert grp("noor", "2") == grp("dirayah", "2")
        assert dict(o.execute("select ext_id, rule from ext where kind='kg' and ext_id in ('10','20')")) == {"10": "noor_kg", "20": "seed"}
        assert o.execute("select rule from ext where kind='noor' and ext_id='4'").fetchone()[0] == "dup"
        assert o.execute("select rule from ext where kind='dirayah'").fetchone()[0] == "dirayah_same_id"
        e = o.execute("select a, b, rule from edge where rule='noor_kg'").fetchone()
        assert (e[0], e[1]) == ("noor:1", "kg:10")
        m = version.read_meta(out)
        assert json.loads(m["counts_json"])["noor_kg"] == 1 and m["name"] == "t" and len(m["wiki_state"]) == 16
        assert o.execute("pragma journal_mode").fetchone()[0] == "delete" and not os.path.exists(out + "-wal")
    finally:
        shutil.rmtree(d)


def test_threshold_conflicts_only_for_enabled_kinds():
    d = tempfile.mkdtemp(prefix="narver_")
    try:
        c, path, p = world(d)
        pol = dict(V1)
        del pol["dup"], pol["dirayah"]
        out, o = built(d, pol, path)
        rows = o.execute("select a, b, reason from conflict").fetchall()
        assert ("noor:2", "kg:20", "threshold") in rows
        assert o.execute("select count(*) from conflict where rule in ('dup','dirayah_same_id')").fetchone()[0] == 0
        assert o.execute("select count(*) from edge where rule!='noor_kg'").fetchone()[0] == 0
    finally:
        shutil.rmtree(d)


def test_canon_rules():
    d = tempfile.mkdtemp(prefix="narver_")
    try:
        c, path, p = world(d)
        out, o = built(d, V1, path)
        canon = dict(o.execute("select id, canon from person"))
        assert canon[p["a"]] == "noor:1"                                    # noor wins over kg:10
        assert canon[p["n3"]] == "noor:3"                                   # min numeric noor
        assert canon[p["k11"]] == "kg:11"
        names = dict(o.execute("select id, name from person"))
        assert names[p["a"]] == "سفيان بن عيينة"
        assert o.execute("select n_ext from person where id=?", (p["a"],)).fetchone()[0] == 2
    finally:
        shutil.rmtree(d)


def test_raw_is_all_single():
    d = tempfile.mkdtemp(prefix="narver_")
    try:
        c, path, p = world(d)
        out, o = built(d, version.load_policy("raw"), path)
        assert o.execute("select count(*) from edge").fetchone()[0] == 0
        assert o.execute("select count(*) from person").fetchone()[0] == o.execute("select count(*) from ext").fetchone()[0]
        assert o.execute("select count(*) from ext where rule!='seed'").fetchone()[0] == 0
        assert o.execute("select count(*) from conflict").fetchone()[0] == 0
    finally:
        shutil.rmtree(d)


def test_wiki_merges_modes():
    d = tempfile.mkdtemp(prefix="narver_")
    try:
        c, path, p = world(d)
        store.merge(c, p["z"], p["h"], keep=p["z"], user="ali")             # unreviewed merge noor:2 + kg:20
        same = lambda o: o.execute("select (select person_id from ext where kind='noor' and ext_id='2') = "
                                   "(select person_id from ext where kind='kg' and ext_id='20')").fetchone()[0]
        for mode, merged, rule in (("all", 1, "wiki_merge"), ("approved", 0, None), ("none", 0, None)):
            _, o = built(d, dict(V1, wiki_merges=mode), path, f"{mode}.sqlite")
            assert same(o) == merged, mode
            if rule:
                assert o.execute("select rule from ext where kind='kg' and ext_id='20'").fetchone()[0] == rule
        rev = c.execute("select max(id) from revision where kind='merge'").fetchone()[0]
        store.review(c, rev, "approved", "rev")
        _, o = built(d, dict(V1, wiki_merges="approved"), path, "ap2.sqlite")
        assert same(o) == 1
    finally:
        shutil.rmtree(d)


def test_policy_validation_and_listing():
    for bad in ({"name": "x", "oops": 1}, {"name": "x", "dup": {"nope": 1}}, {"name": "x", "wiki_merges": "some"}):
        d = tempfile.mkdtemp(prefix="narver_")
        try:
            f = os.path.join(d, "p.json")
            json.dump(bad, open(f, "w"))
            try:
                version.load_policy(f)
                assert False, bad
            except ValueError:
                pass
        finally:
            shutil.rmtree(d)
    assert version.load_policy("raw")["wiki_merges"] == "none"
    v1m = version.load_policy("v1m")
    assert v1m["noor_kg"]["confidence"] == ["high"] and "description_fa" in v1m


def test_evaluate_without_sources_and_list():
    d = tempfile.mkdtemp(prefix="narver_")
    try:
        c, path, p = world(d)
        out, o = built(d, V1, path, "t.sqlite")
        ev = version.evaluate(out, os.path.join(d, "none.sqlite"), os.path.join(d, "none2.sqlite"), None)
        assert ev["rules"]["noor_kg"] == 1 and ev["conflicts"]["one_to_one"] == 1 and ev["noor_kg_death"] is None
        assert ev["edges"]["noor_kg"] == 1
        m = version.read_meta(out)
        assert json.loads(m["eval_json"]) == ev
        got = version.list_versions(d)
        assert [x["name"] for x in got] == ["t"] and got[0]["file"] == out
    finally:
        shutil.rmtree(d)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
