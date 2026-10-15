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


# ---- manual decisions per version -------------------------------------------------------------
def same(o, a, b):
    g = lambda x: o.execute("select person_id from ext where kind=? and ext_id=?", tuple(x.split(":"))).fetchone()[0]
    return g(a) == g(b)


def test_manual_accept_forces_pair_rules_reject():
    d = tempfile.mkdtemp(prefix="narver_")
    try:
        c, path, p = world(d)
        out, o = built(d, V1, path, "a.sqlite")
        st0 = version.read_meta(out)["wiki_state"]
        assert not same(o, "noor:2", "kg:20")                               # medium pair: excluded by the policy
        store.set_version_decision(c, "t", "kg:20", "noor:2", "accept", "ali", "sure")
        out, o = built(d, V1, path, "b.sqlite")
        assert same(o, "noor:2", "kg:20")
        e = o.execute("select rule, score, evidence_json from edge where a='kg:20' or b='kg:20'").fetchone()
        assert e[0] == "manual" and e[1] == 1.0 and json.loads(e[2])["forced"] is True and "kind_clash" not in json.loads(e[2])
        assert o.execute("select rule from ext where kind='noor' and ext_id='2'").fetchone()[0] == "manual"      # the b side is brought in
        m = version.read_meta(out)
        cj = json.loads(m["counts_json"])
        assert cj["manual"] == 1 and cj["manual_accept"] == 1 and cj["manual_reject"] == 0
        assert m["wiki_state"] != st0
        # clear restores the rule-only result and the original state
        store.set_version_decision(c, "t", "noor:2", "kg:20", "clear", "ali")
        out, o = built(d, V1, path, "c.sqlite")
        assert not same(o, "noor:2", "kg:20") and version.read_meta(out)["wiki_state"] != m["wiki_state"]
        assert store.version_decision_for(c, "t", "noor:2", "kg:20") is None
        assert len(store.version_decisions(c, "t", status="retired")) == 1
    finally:
        shutil.rmtree(d)


def test_manual_reject_removes_rule_edge_and_state_changes():
    d = tempfile.mkdtemp(prefix="narver_")
    try:
        c, path, p = world(d)
        s0 = version.read_meta(built(d, V1, path, "a.sqlite")[0])["wiki_state"]
        store.set_version_decision(c, "t", "noor:1", "kg:10", "reject", "ali")
        out, o = built(d, V1, path, "b.sqlite")
        assert not same(o, "noor:1", "kg:10")
        assert o.execute("select count(*) from edge where a='noor:1' and b='kg:10'").fetchone()[0] == 0
        assert same(o, "noor:1", "kg:11")                                   # the runner-up now wins noor:1
        assert o.execute("select reason from conflict where a='noor:1' and b='kg:10'").fetchone()[0] == "manual_reject"
        s1 = version.read_meta(out)["wiki_state"]
        assert s1 != s0 and json.loads(version.read_meta(out)["counts_json"])["manual_reject"] == 1
        store.set_version_decision(c, "t", "noor:1", "kg:10", "accept", "ali")           # reject -> accept replaces it
        out, o = built(d, V1, path, "c.sqlite")
        assert same(o, "noor:1", "kg:10") and version.read_meta(out)["wiki_state"] not in (s0, s1)
        assert len(store.version_decisions(c, "t")) == 1
        # decisions of another version do not leak
        store.set_version_decision(c, "other", "noor:2", "kg:20", "accept", "ali")
        _, o = built(d, V1, path, "d.sqlite")
        assert not same(o, "noor:2", "kg:20")
        try:
            store.set_version_decision(c, "t", "noor:2", "kg:999", "accept", "ali")
            assert False
        except store.WikiError:
            pass
    finally:
        shutil.rmtree(d)


def test_manual_accept_beats_clashing_rule_edge_and_flags_kind_clash():
    d = tempfile.mkdtemp(prefix="narver_")
    try:
        c, path, p = world(d)
        store.set_version_decision(c, "t", "noor:1", "kg:11", "accept", "ali")           # rule edge noor:1-kg:10 (score 2.2) loses
        _, o = built(d, V1, path)
        assert same(o, "noor:1", "kg:11") and not same(o, "noor:1", "kg:10")
        assert o.execute("select reason, rule from conflict where a='noor:1' and b='kg:10'").fetchone() == ("one_to_one", "noor_kg")
        # accepting across groups that both hold a KG id still merges, but flags it
        store.set_version_decision(c, "t", "noor:1", "kg:11", "clear", "ali")
        store.set_version_decision(c, "t", "noor:1", "kg:10", "accept", "ali")
        store.set_version_decision(c, "t", "noor:1", "kg:20", "accept", "ali")
        _, o = built(d, V1, path, "k.sqlite")
        assert same(o, "noor:1", "kg:20") and same(o, "noor:1", "kg:10")                 # group {noor:1, kg:10} + {kg:20}
        ev = json.loads(o.execute("select evidence_json from edge where rule='manual' and (a='kg:20' or b='kg:20')").fetchone()[0])
        assert ev["forced"] is True and ev["kind_clash"] == ["kg"]
    finally:
        shutil.rmtree(d)


def test_manual_reject_of_wiki_joined_pair_is_ignored_and_transitive_recorded():
    d = tempfile.mkdtemp(prefix="narver_")
    try:
        c, path, p = world(d)
        store.merge(c, p["z"], p["h"], keep=p["z"], user="ali")
        store.set_version_decision(c, "t", "noor:2", "kg:20", "reject", "ali")
        _, o = built(d, V1, path)
        assert same(o, "noor:2", "kg:20")
        assert o.execute("select count(*) from conflict where reason='manual_reject_ignored' and a='kg:20'").fetchone()[0] == 1
        # noor:3~noor:4 (dup edge) and noor:3~kg:11 forced: rejecting kg:11/noor:4 cannot break the chain
        store.set_version_decision(c, "t", "noor:3", "kg:11", "accept", "ali")
        store.set_version_decision(c, "t", "kg:11", "noor:4", "reject", "ali")
        _, o = built(d, V1, path, "t.sqlite")
        assert same(o, "kg:11", "noor:4")
        assert o.execute("select count(*) from conflict where reason='manual_reject_transitive'").fetchone()[0] == 1
    finally:
        shutil.rmtree(d)


def test_rebuild_without_source_databases_and_old_wiki_without_table():
    d = tempfile.mkdtemp(prefix="narver_")
    try:
        c, path, p = world(d)
        out_dir = os.path.join(d, "versions")
        os.environ["NARWIKI_DIRAYAH_DB"] = os.path.join(d, "none.sqlite")
        from narwiki import config
        old = config.narrators_db, config.kg_db
        config.narrators_db = config.kg_db = lambda: os.path.join(d, "none.sqlite")
        try:
            store.set_version_decision(c, "v1m", "noor:2", "kg:20", "accept", "ali")
            m = version.rebuild("v1m", path, out_dir)
        finally:
            config.narrators_db, config.kg_db = old
            os.environ.pop("NARWIKI_DIRAYAH_DB")
        f = os.path.join(out_dir, "v1m.sqlite")
        assert os.path.exists(f) and not os.path.exists(f + ".tmp")
        assert same(sqlite3.connect(f), "noor:2", "kg:20") and "eval_json" in version.read_meta(f)
        assert json.loads(m["counts_json"])["manual_accept"] == 1
        # a wiki database from before the table existed: no decisions, no failure
        c.execute("drop table version_decision"); c.commit()
        version.rebuild("v1m", path, out_dir)
        assert not same(sqlite3.connect(f), "noor:2", "kg:20")
    finally:
        shutil.rmtree(d)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
