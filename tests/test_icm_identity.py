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


def test_resolve_drops_and_merges():
    from icm import graph
    ch = _chains()
    cards = identity.make_cards(ch, {"w:1": "shia", "w:2": "shia"})
    ident = dict(chains=ch, cards=cards)
    r = identity.resolve(ident, dict(choices={}, drops=["w:2#t|0"], merges=[["kg:3151", "noor:2607"]],
                                     overrides={"w:1#t|0": "kg:3151"}))
    e0 = r["chains"][1]["elems"][0]
    assert e0["dropped"] and e0["nid"] is None and r["dropped"] == ["w:2#t|0"]
    assert r["chains"][0]["elems"][0]["nid"] == "noor:2607"         # the merged KG id goes by its Noor id
    assert r["alias"]["kg:3151"] == "noor:2607"
    A = dict(witnesses=[dict(wid="w:1"), dict(wid="w:2")], X={"w:1": [0], "w:2": [1]}, units=[], seed="w:1")
    G = graph.run(A, r) if os.path.exists(identity.config.NARRATORS_DB) else None
    if G:
        assert all("name:فلان" not in a["sanad"] for a in G["attestations"]) and G["dropped"] == ["w:2#t|0"]


def _sug_ident(elems, trad="shia", source="text"):
    ch = [dict(aid="w:1#t", wid="w:1", source=source, tradition=trad, elems=elems, speaker=None, quoted_work=None, flags=[])]
    return dict(chains=ch, cards=identity.make_cards(ch, {"w:1": trad}), flags=[])


def test_suggest_drops_isnad_words_and_strips_glued_ones():
    from icm import suggest
    saved_m, saved_t = identity.match, identity.noor_title_hits
    aisha = dict(id="kg:4049", label="عائشة بنت أبي بكر", score=1.0, cov=1.0)
    identity.match = lambda name, *a, **k: [dict(aisha)] if name == "عایشه" else []
    identity.noor_title_hits = lambda name: []
    try:
        I = _sug_ident([_el(0, "الحاکم", [], "collector") | dict(role="collector"), _el(1, "اقول", []),
                        _el(2, "عایشه ان رسول الله", [])], trad="sunni")
        got = {s["kind"]: s for s in suggest.suggest(I)}
        assert got["not_a_name"]["proposal"] == {"op": "drop"} and got["not_a_name"]["occ"] == "w:1#t|1"
        st = got["strip_words"]
        assert st["proposal"]["id"] == "kg:4049" and st["card"] == "sunni:عایشه ان رسول الله"
        assert any(c["id"] == "kg:4049" and c["via"] == "strip" for c in I["cards"][st["card"]]["candidates"])
        assert I["cards"][st["card"]]["default"] is None                  # a suggestion never becomes the default
        assert st["id"] == "strip_words:sunni:عایشه ان رسول الله"
    finally:
        identity.match, identity.noor_title_hits = saved_m, saved_t


def test_suggest_relative_depends_on_its_base():
    from icm import suggest
    saved = identity._noor_rel, identity._noor
    identity._noor_rel = {(2607, "father"): {4013: 6, 9: 2}}
    identity._noor = dict(ravis={4013: dict(name="علي بن الحسين زين العابدين ع", n=9, mazhab=None, grade=None, toks=[], alts=[]),
                                 9: dict(name="فلان", n=1, mazhab=None, grade=None, toks=[], alts=[])}, post={})
    saved_t = identity.noor_title_hits
    identity.noor_title_hits = lambda name: []
    try:
        zayd = _el(0, "زید بن علی", [dict(id="noor:2607", label="زيد بن علي الشهيد", score=1.0, cov=0.7)])
        I = _sug_ident([zayd, _el(1, "ابیه", [], "relative")])
        rel = [s for s in suggest.suggest(I) if s["kind"] == "relative"]
        assert rel[0]["proposal"]["id"] == "noor:4013" and rel[0]["depends_on"] == {"occ": "w:1#t|0", "id": "noor:2607"}
        assert rel[0]["confidence"] == "low"                              # 6 of 8 chains: below the 0.8 share
    finally:
        identity._noor_rel, identity._noor = saved
        identity.noor_title_hits = saved_t


def test_title_index_finds_ibn_abbas():
    if not os.path.exists(identity.config.NARRATORS_DB):
        return
    rid, n, share = identity.noor_title_hits("ابن عباس")[0]
    assert rid == 3425 and n > 500 and share > 0.9


def test_kg_gap_markers_are_unnamed_per_chain():
    from icm import config, corpus
    if not os.path.exists(config.KG_DB):
        return
    rec = corpus.get("kg:653719")                     # الرافعي: «… هبة الله … موضع تعليق» at the collector's end
    ch = identity.kg_chains(rec)[0]
    assert not any(e["exact"] in ("kg:30000", "kg:30001", "kg:30002") for e in ch["elems"])
    assert ch["elems"][0]["kind"] == "collector" and ch["elems"][1]["kind"] == "anon"
    assert [e["i"] for e in ch["elems"]] == list(range(len(ch["elems"])))


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
