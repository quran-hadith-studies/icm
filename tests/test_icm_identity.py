import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm import identity


def _el(i, name, cands, kind="name"):
    return dict(i=i, name=name, raw=name, term=None, kind=kind, exact=None, cands=cands)


def _chains():
    zayd_collector = [dict(id="kg:3151", label="زيد بن علي بن الحسين", score=1.66, cov=0.99, ctx=0, tier="low"),
                      dict(id="noor:2607", label="زيد بن علي الشهيد", score=1.0, cov=1.0, ctx=0, tier="low")]
    zayd_narrator = [dict(id="noor:2607", label="زيد بن علي الشهيد", score=1.43, cov=1.0, ctx=1, tier="high"),
                     dict(id="noor:2608", label="زيد بن علي العسكري", score=1.03, cov=1.0, ctx=0, tier="low")]
    return [dict(aid="w:1#t", wid="w:1", source="text", elems=[_el(0, "زید بن علی", zayd_collector, "collector")], speaker=None, quoted_work=None, flags=[]),
            dict(aid="w:2#t", wid="w:2", source="text", elems=[_el(0, "فلان", []), _el(1, "زید بن علی", zayd_narrator)], speaker=None, quoted_work=None, flags=[])]


def test_card_prefers_a_candidate_vouched_for_in_any_chain():
    ch = _chains()
    cards = identity.make_cards(ch, {"w:1": "shia", "w:2": "shia"})
    cd = cards["shia:زید بن علی"]
    assert cd["occurrences"] == [["w:1#t", 0], ["w:2#t", 1]]
    assert [c["id"] for c in cd["candidates"]][:2] == ["noor:2607", "kg:3151"]
    assert cd["default"] == "noor:2607" and cd["tier"] == "high"
    assert cd["candidates"][0]["ctx"] == 1


def test_override_for_one_place_and_none():
    ch = _chains()
    cards = identity.make_cards(ch, {"w:1": "shia", "w:2": "shia"})
    ident = dict(chains=ch, cards=cards)
    r = identity.resolve(ident, dict(choices={}, overrides={"w:1#t|0": "none", "w:2#t|1": "noor:2608"}))
    nid = {(c["aid"], e["i"]): (e["nid"], e["tier"]) for c in r["chains"] for e in c["elems"]}
    assert nid[("w:1#t", 0)] == ("name:زید بن علی", "name")
    assert nid[("w:2#t", 1)] == ("noor:2608", "chosen")
    r = identity.resolve(ident, dict(choices={}))
    assert {nid for c in r["chains"] for e in c["elems"] for nid in [e["nid"]] if "2607" in nid} == {"noor:2607"}


def test_relative_words():
    assert identity.relative_word("أبيه عليه السلام") == "ابیه"
    assert identity.relative_word("جده") == "جده" and identity.relative_word("أبي") == "ابی"
    assert identity.relative_word("أبي عبد الله") is None and identity.relative_word("زيد بن علي") is None


def test_each_relative_is_its_own_card():
    baqir = dict(id="noor:5485", label="محمد بن علي الباقر", score=5.0, cov=1.0, derived=True, ctx=0, tier="high")
    ch = [dict(aid="w:1#t", wid="w:1", source="text", elems=[_el(0, "جعفر بن محمد", []), _el(1, "ابیه", [baqir], "relative")], speaker=None, quoted_work=None, flags=[]),
          dict(aid="w:2#t", wid="w:2", source="text", elems=[_el(0, "زید بن علی", []), _el(1, "ابیه", [], "relative")], speaker=None, quoted_work=None, flags=[])]
    cards = identity.make_cards(ch, {"w:1": "shia", "w:2": "shia"})
    assert cards["shia:ابیه@w:1#t|1"]["default"] == "noor:5485" and cards["shia:ابیه@w:2#t|1"]["default"] is None
    r = identity.resolve(dict(chains=ch, cards=cards), dict(choices={}))
    nid = {c["aid"]: c["elems"][1]["nid"] for c in r["chains"]}
    assert nid["w:1#t"] == "noor:5485" and nid["w:2#t"] == "rel:w:2#t:1", nid


def test_relatives_from_noor_chains_and_the_imams():
    saved = identity._noor_rel, identity._noor
    identity._noor_rel = {(2607, "father"): {4013: 91}}
    identity._noor = dict(ravis={4013: dict(name="علي بن الحسين زين العابدين ع", n=9, mazhab=None, grade=None)}, post={})
    try:
        for exact in (False, True):
            zayd = _el(0, "زید بن علی", [dict(id="noor:2607", label="زيد بن علي الشهيد", score=1.0, cov=1.0)])
            if exact:
                zayd["exact"] = "noor:2607"; zayd["cands"][0]["exact"] = True
            ch = dict(aid="w:1#t", wid="w:1", source="text", speaker=None, quoted_work=None, flags=[],
                      elems=[zayd, _el(1, "ابیه", [], "relative"), _el(2, "جده", [], "relative")])
            identity._derive_relatives(ch)
            father, gf = ch["elems"][1]["cands"], ch["elems"][2]["cands"]
            assert father[0]["id"] == "noor:4013" and bool(father[0]["derived"]) == exact, father
            assert gf and gf[0]["id"] == identity._NOOR_OF_KEY["husayn"], gf      # «عن أبيه عن جده»: the father's father
    finally:
        identity._noor_rel, identity._noor = saved


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
