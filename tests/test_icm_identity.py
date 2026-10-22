import os, shutil, sqlite3, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm import config, identity, narrator_versions


def _version(d, name, groups):
    """Write a version file (the schema ``narwiki version build`` writes) into ``d`` and open it.
    groups: [(canon id, [member ids])]; a member's kind is the part before the colon."""
    old, config.NARRATOR_VERSIONS_DIR = config.NARRATOR_VERSIONS_DIR, d
    c = sqlite3.connect(os.path.join(d, f"{name}.sqlite"))
    c.executescript("""create table meta(k text primary key, v text);
        create table person(id integer primary key, canon text, name text, n_ext integer);
        create table ext(kind text, ext_id integer, person_id integer, rule text, score real, primary key(kind, ext_id));
        create table edge(a text, b text, rule text, score real, evidence_json text);
        create table conflict(a text, b text, rule text, score real, reason text)""")
    c.executemany("insert into meta values (?,?)", [("name", name), ("label_fa", "آزمایشی"), ("built", "2026-10-15"),
                                                    ("wiki_state", "s1"), ("counts_json", "{}"), ("eval_json", "{}")])
    for k, (canon, members) in enumerate(groups, 1):
        c.execute("insert into person values (?,?,?,?)", (k, canon, canon, len(members)))
        for m in members:
            kind, _, i = m.partition(":")
            c.execute("insert into ext values (?,?,?,?,?)", (kind, int(i), k, "seed" if m == canon else "noor_kg", 1.0))
        if len(members) > 1:
            c.execute("insert into edge values (?,?,?,?,?)", (members[0], members[1], "noor_kg", 0.9, '{"ctx": 2}'))
    c.commit(); c.close()
    try:
        return narrator_versions.get(name)
    finally:
        config.NARRATOR_VERSIONS_DIR = old


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


def test_free_merges_and_splits():
    """Merges name any node (a name-only place too); a split moves places off an identity, even one the source fixed;
    a user merge naming a kg id still applies after the database maps that id to its Noor id."""
    ch = _chains()
    ch[1]["elems"][1]["exact"] = "noor:2607"                     # the source fixed this one
    cards = identity.make_cards(ch, {"w:1": "shia", "w:2": "shia"})
    ident = identity.with_node_ids(dict(chains=ch, cards=cards))
    assert ch[1]["elems"][0]["node_name"] == "name:فلان" and ch[0]["elems"][0]["node_name"] == "name:زید بن علی"
    r = identity.resolve(ident, dict(choices={}, merges=[["name:فلان", "noor:2608"]],
                                     overrides={"w:2#t|1": "split:1"}, splits={"split:1": dict(label="زيد آخر", **{"from": "noor:2607"})}))
    nid = {(c["aid"], e["i"]): (e["nid"], e["tier"]) for c in r["chains"] for e in c["elems"]}
    assert nid[("w:2#t", 0)][0] == "noor:2608"                   # the name-only place merged into a person
    assert nid[("w:2#t", 1)] == ("split:1", "chosen")            # split off the source's own link
    assert r["nodes"]["split:1"]["names"][0] == "زيد آخر" and r["nodes"]["split:1"]["label"] == "زيد آخر"
    assert identity._union([["name:x", "split:2"], ["split:2", "rel:a:1"]]) == {"name:x": "split:2", "rel:a:1": "split:2"}
    d = tempfile.mkdtemp(prefix="icmver_")
    try:                                                         # kg:3151 is noor:2607 for the version; the user merges it with 2608
        V = _version(d, "t1", [("noor:2607", ["noor:2607", "kg:3151"])])
        r = identity.resolve(dict(chains=_chains(), cards=cards), dict(choices={}, merges=[["kg:3151", "noor:2608"]],
                                                                       overrides={"w:1#t|0": "kg:3151"}), V)
        assert r["chains"][0]["elems"][0]["nid"] == "noor:2607" and r["alias"]["noor:2608"] == "noor:2607"
    finally:
        shutil.rmtree(d)


def test_version_file_maps_members_and_tolerates_a_missing_dir():
    d = tempfile.mkdtemp(prefix="icmver_")
    try:
        V = _version(d, "t1", [("noor:2607", ["noor:2607", "kg:3151"]), ("kg:9", ["kg:9", "dirayah:4"])])
        assert V.name == "t1" and V.key == ["t1", "s1"] and V.meta["label_fa"] == "آزمایشی"
        assert V.canon(["kg:3151", "noor:2607", "kg:9", "noor:1"]) == {"kg:3151": "noor:2607", "noor:2607": "noor:2607"}   # kg:9 + dirayah is no two-id group
        assert [m["id"] for m in V.members("kg:3151")] == ["noor:2607", "kg:3151"] and V.members("noor:1") == []
        assert V.edges(["kg:3151"])[0]["evidence"] == {"ctx": 2}
        old, config.NARRATOR_VERSIONS_DIR = config.NARRATOR_VERSIONS_DIR, d
        try:
            assert [v["name"] for v in narrator_versions.available()] == ["raw", "t1"]
            config.NARRATOR_VERSION_DEFAULT = "t1"
            assert narrator_versions.default_name() == "t1"
            config.NARRATOR_VERSION_DEFAULT = "nope"
            assert narrator_versions.default_name() == "raw"
        finally:
            config.NARRATOR_VERSIONS_DIR, config.NARRATOR_VERSION_DEFAULT = old, "v1m"
        config.NARRATOR_VERSIONS_DIR = os.path.join(d, "absent")
        assert [v["name"] for v in narrator_versions.available()] == ["raw"]
        for bad in ("nope", "../x"):
            try:
                narrator_versions.get(bad); assert False
            except KeyError:
                pass
        raw = narrator_versions.get("raw")
        assert raw.key == ["raw", ""] and raw.canon(["kg:3151"]) == {} and raw.members("noor:2607") == []
    finally:
        config.NARRATOR_VERSIONS_DIR = old
        shutil.rmtree(d)


def _collapsed(V):
    ch = _chains()
    identity._collapse_chains(ch, V)
    return ch


def test_candidates_collapse_to_the_canon_id_with_the_others_in_also():
    d = tempfile.mkdtemp(prefix="icmver_")
    try:
        V = _version(d, "t1", [("noor:2607", ["noor:2607", "kg:3151"])])
        ch = _collapsed(V)
        c = ch[0]["elems"][0]["cands"]
        assert [x["id"] for x in c] == ["noor:2607"] and c[0]["also"] == ["kg:3151"]
        assert c[0]["score"] == 1.66                                   # the best of the two, under the group's id
        cards = identity.make_cards(ch, {"w:1": "shia", "w:2": "shia"})
        assert [x["id"] for x in cards["shia:زید بن علی"]["candidates"]] == ["noor:2607", "noor:2608"]
        raw = _chains()
        identity._collapse_chains(raw, narrator_versions.RAW)
        assert [x["id"] for x in raw[0]["elems"][0]["cands"]] == ["kg:3151", "noor:2607"] and "also" not in raw[0]["elems"][0]["cands"][0]
        e = dict(i=0, name="x", raw="x", term=None, kind="name", exact="kg:3151", cands=[dict(id="kg:3151", score=9.0, label="x", exact=True)])
        identity._collapse_chains([dict(elems=[e])], V)
        assert e["exact"] == "noor:2607" and e["exact_src"] == "kg:3151" and e["cands"][0]["id"] == "noor:2607"
    finally:
        shutil.rmtree(d)


def test_resolve_merges_kg_into_noor_and_nodes_carry_ext():
    d = tempfile.mkdtemp(prefix="icmver_")
    try:
        V = _version(d, "t1", [("noor:2607", ["noor:2607", "kg:3151"])])
        ch = _chains()
        cards = identity.make_cards(ch, {"w:1": "shia", "w:2": "shia"})
        dec = dict(choices={}, overrides={"w:1#t|0": "kg:3151"}, dates={"kg:3151": [100, 110]})
        r = identity.resolve(dict(chains=ch, cards=cards), dec, V)
        assert r["chains"][0]["elems"][0]["nid"] == "noor:2607" and r["alias"]["kg:3151"] == "noor:2607"
        assert r["nodes"]["noor:2607"]["ext"] == ["noor:2607", "kg:3151"] and r["nodes"]["noor:2607"]["n"] == 2
        ch0 = _chains()
        r0 = identity.resolve(dict(chains=ch0, cards=identity.make_cards(ch0, {"w:1": "shia", "w:2": "shia"})), dec)      # raw: apart
        assert r0["chains"][0]["elems"][0]["nid"] == "kg:3151" and r0["nodes"]["kg:3151"]["ext"] == ["kg:3151"]
        assert r0["nodes"]["noor:2607"]["ext"] == ["noor:2607"] and r0["same_person"] == {}
    finally:
        shutil.rmtree(d)


def test_graph_gathers_facts_over_every_id_of_a_merged_node():
    from icm import corpus, graph
    if not (os.path.exists(config.NARRATORS_DB) and os.path.exists(config.KG_DB)):
        return
    d = tempfile.mkdtemp(prefix="icmver_")
    try:
        V = _version(d, "t1", [("noor:2607", ["noor:2607", "kg:3151"])])
        ch = _chains()
        cards = identity.make_cards(ch, {"w:1": "shia", "w:2": "shia"})
        r = identity.resolve(dict(chains=ch, cards=cards), dict(choices={}, overrides={"w:1#t|0": "kg:3151"}), V)
        A = dict(witnesses=[dict(wid="w:1"), dict(wid="w:2")], X={"w:1": [0], "w:2": [1]}, units=[], seed="w:1")
        n = graph.run(A, r)["nodes"]["noor:2607"]
        assert n["ids"] == {"noor": 2607, "kg": 3151} and n["date_source"] == "noor" and n["rank"]      # Noor's death first; the KG adds its rank
        assert graph.known_deaths(["kg:3151"], V)["kg:3151"]["source"] == "noor"
        assert graph.known_deaths(["kg:3151"])["kg:3151"]["source"] == "hadith-kg"
    finally:
        shutil.rmtree(d)


class _FakeDbs:
    """Stand-ins for the narrator database link table and the KG, so suggestions run without the corpora."""
    def __enter__(self):
        from icm import suggest
        self.suggest, self.saved = suggest, (identity._nconn, identity.noor_index, identity.corpus.conn, suggest.known_deaths)

        def nconn():
            c = sqlite3.connect(":memory:")
            c.execute("create table link(ravi_id int, target_kind text, target_id int, confidence text, score real)")
            c.execute("insert into link values (2607, 'kg', 3151, 'high', 0.9)")
            return c

        def kgconn(name):
            c = sqlite3.connect(":memory:")
            c.execute("create table rawis(id int, name text)")
            c.execute("insert into rawis values (3151, 'زيد')")
            return c
        identity._nconn = nconn
        identity.noor_index = lambda: dict(ravis={2607: dict(name="زيد بن علي الشهيد", n=1, toks=[], alts=[])})
        identity.corpus.conn = kgconn
        suggest.known_deaths = lambda ids, version=None: {}
        return self

    def __exit__(self, *a):
        identity._nconn, identity.noor_index, identity.corpus.conn, self.suggest.known_deaths = self.saved


def test_raw_offers_the_pair_as_a_suggestion_and_a_version_that_merges_it_does_not():
    from icm import suggest
    d = tempfile.mkdtemp(prefix="icmver_")
    try:
        V = _version(d, "t1", [("noor:2607", ["noor:2607", "kg:3151"])])
        with _FakeDbs():
            ch = _chains()
            I = dict(chains=ch, cards=identity.make_cards(ch, {"w:1": "shia", "w:2": "shia"}), flags=[])
            got = [s for s in suggest.suggest(I) if s["kind"] == "same_person"]
            assert [s["proposal"]["pair"] for s in got] == [["noor:2607", "kg:3151"]]
            assert "زیاد" in got[0]["evidence"] and got[0]["confidence"] == "high"      # the link's confidence is named
            ch = _collapsed(V)
            I = dict(chains=ch, cards=identity.make_cards(ch, {"w:1": "shia", "w:2": "shia"}), flags=[])
            assert [s for s in suggest.suggest(I, V) if s["kind"] == "same_person"] == []
            # the version holds the pair, but a stage computed under it is asked with an older raw card: still skipped
            ch = _chains()
            I = dict(chains=ch, cards=identity.make_cards(ch, {"w:1": "shia", "w:2": "shia"}), flags=[])
            assert [s for s in suggest.suggest(I, V) if s["kind"] == "same_person"] == []
    finally:
        shutil.rmtree(d)


def test_wiki_same_person_skips_pairs_the_version_merged():
    from icm import suggest
    d = tempfile.mkdtemp(prefix="icmver_")
    try:
        V = _version(d, "t1", [("noor:2607", ["noor:2607", "kg:3151"])])
        ch = _chains()
        I = dict(chains=ch, cards=identity.make_cards(ch, {"w:1": "shia", "w:2": "shia"}), flags=[])
        persons = lambda ids: {i: dict(person_id=5, name="زيد", also=[]) for i in ids if i in ("noor:2607", "kg:3151")}
        with _FakeDbs():
            assert [s["proposal"]["pair"] for s in suggest.wiki_same_person(I, persons)] == [["noor:2607", "kg:3151"]]
            assert suggest.wiki_same_person(I, persons, V) == []
    finally:
        shutil.rmtree(d)


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


def test_renames_set_where_a_name_begins_and_ends_in_one_place():
    ch = _chains()
    coll = ch[0]["elems"][0]
    a, b = ch[1]["elems"]                                      # «فلان», «زید بن علی»
    a.update(name="علی ابو محمد", raw="علی ابو محمد", term="قرات", hai={"link": "x"}, alts=["x"])
    b["exact"] = "noor:2607"
    n = identity.apply_renames(ch, {"w:2#t|0": dict(name="أبو  محمد", term="قرأت علی"),
                                    "w:2#t|1": dict(name="x", term="y"),                      # the source fixed this one
                                    "w:1#t|0": dict(name="زید", term=""),
                                    "nobody|0": dict(name="z", term="")})                     # no such place
    assert n == 2
    assert (a["name"], a["raw"], a["term"], a["renamed"], a["i"]) == ("ابو محمد", "ابو محمد", "قرات علی", True, 0)   # the place keeps its index
    assert "hai" not in a and "alts" not in a
    assert b["name"] == "زید بن علی" and not b.get("renamed")
    assert (coll["kind"], coll["name"], coll["term"]) == ("collector", "زید", None)
    assert identity.apply_renames(ch, None) == 0


# ---------------------------------------------------------------------------
# the chain in its text: anchoring, and the user's edits (parts.chain)
# ---------------------------------------------------------------------------
_REC = dict(wid="v02:9", corpus="v02", hid=9, author="مالک", death=179,
            sanad="حَدَّثَنَا يَحْيَى بْنُ سَعِيدٍ عَنْ أَبِيهِ عَنِ ابْنِ عَبَّاسٍ رَضِيَ اللهُ عَنْهُ",
            matn="عَنْ سَعِيدِ بْنِ الْمُسَيَّبِ قَالَ: إِنَّمَا الْأَعْمَالُ بِالنِّيَّاتِ")


def test_hadith_words_keeps_the_raw_spelling_sanad_first():
    toks, words, n = identity.hadith_words(_REC)
    assert n == 12 and toks[:3] == ["حدثنا", "یحیی", "بن"] and words[1] == "يَحْيَى" and len(toks) == len(words)
    assert words[16] == "قَالَ" and words[-1] == "بِالنِّيَّاتِ"                                      # edge punctuation is not part of a word; harakat are
    assert toks[n:n + 4] == ["عن", "سعید", "بن", "المسیب"]
    assert identity.hadith_words(dict(_REC, sanad=None))[2] == 0


def test_anchor_elems_finds_each_place_after_the_one_before_without_honorifics():
    toks, _, _ = identity.hadith_words(_REC)
    ch = identity.text_chain(_REC)
    names = [(e["i"], e["kind"], e["name"]) for e in ch["elems"]]
    assert names[0] == (0, "collector", "مالک") and names[1][2] == "یحیی بن سعید" and names[2][1] == "relative"
    sp = identity.anchor_elems(toks, ch["elems"])
    assert sp[0] is None                                                   # the collector is the book's author, not in the text
    assert sp[1] == dict(t=0, a=1, b=4)                                    # «حدثنا» + «یحیی بن سعید»
    assert sp[2] == dict(t=4, a=5, b=6)                                    # «عن» + «ابیه»
    assert sp[3] == dict(t=6, a=7, b=9)                                    # «عن» + «ابن عباس» (the blessing after it is not the name)
    # ابن≡بن, ابی≡ابو; a name not in the text; a place without a transmission word
    el = [dict(i=0, name="x", raw="x", term=None, kind="collector", role="collector", exact=None, cands=[]),
          dict(i=1, name="بن عباس", raw="بن عباس", term="عن", kind="name", exact=None, cands=[]),
          dict(i=2, name="فلان", raw="فلان", term="عن", kind="name", exact=None, cands=[]),
          dict(i=3, name="سعید بن المسیب", raw="سعید بن المسیب عليه السلام", term=None, kind="name", exact=None, cands=[])]
    sp = identity.anchor_elems(toks, el)
    assert sp == {0: None, 1: dict(t=6, a=7, b=9), 2: None, 3: dict(t=12, a=13, b=16)}      # no phrase given: «عن» in front of it is found
    assert identity.anchor_elems(["ابو", "محمد"], [dict(i=5, name="ابی محمد", raw="ابی محمد", term=None, kind="name")]) == {5: dict(t=0, a=0, b=2)}


def test_chain_edits_add_from_the_matn_drop_and_move_keep_place_ids():
    toks, words, _ = identity.hadith_words(_REC)
    ch = identity.text_chain(_REC)
    ch["elems"][1]["exact"] = "noor:5"
    texts = {"v02:9": (toks, words)}
    edits = {"v02:9#t": dict(add=[dict(t=12, a=13, b=16)], drop=[2], span={"1": dict(t=1, a=1, b=4)}),
             "other#t": dict(drop=[1])}
    n = identity.apply_chain_edits([ch], texts, edits)
    assert n == 3
    got = [(e["i"], e["raw"], e["term"]) for e in ch["elems"]]
    assert got == [(0, "مالک", None), (1, "يَحْيَى بْنُ سَعِيدٍ", None), (3, "ابن عباس", "عن"), (1000, "سَعِيدِ بْنِ الْمُسَيَّبِ", "عن")]
    a, b, c = ch["elems"][1], ch["elems"][2], ch["elems"][3]
    assert a["renamed"] and a["exact"] is None and a["exact_was"] == "noor:5" and a["span"] == dict(t=1, a=1, b=4)   # the words changed: the link is gone
    assert b["span"] == dict(t=6, a=7, b=9) and not b.get("renamed")
    assert c["added"] and c["name"] == "سعید بن المسیب" and c["kind"] == "name" and c["span"] == dict(t=12, a=13, b=16) and not c["imam_hint"]
    assert ch["speaker"] is None and "no_sanad" not in ch["flags"]
    # bad spans are ignored, the collector can be neither dropped nor moved, an added place can be dropped by its id
    ch2 = identity.text_chain(_REC)
    identity.apply_chain_edits([ch2], texts, {"v02:9#t": dict(add=[dict(t=0, a=3, b=2), dict(t=12, a=13, b=16), dict(t=13, a=13, b=99)],
                                                              drop=[0, 1001], span={"0": dict(t=0, a=1, b=2), "9": dict(t=0, a=1, b=2)})})
    assert [e["i"] for e in ch2["elems"]] == [0, 1, 2, 3] and ch2["elems"][0]["kind"] == "collector" and not ch2["elems"][0].get("renamed")
    assert identity.apply_chain_edits([ch2], texts, None) == 0


def test_chain_edits_order_follows_the_text_and_unfound_places_stay_behind_their_predecessor():
    toks, words, _ = identity.hadith_words(_REC)
    el = lambda i, name, **kw: dict(i=i, name=name, raw=name, term=None, kind="name", exact=None, cands=[], **kw)
    ch = dict(aid="v02:9#s", wid="v02:9", source="noor_slots", speaker=dict(kind="imam", elem=2), quoted_work=None, flags=[],
              elems=[el(0, "مالک", role="collector"), el(1, "یحیی بن سعید"), el(2, "کسی که در متن نیست"), el(3, "ابن عباس"), el(4, "نام ناپیدای دیگر")])
    identity.apply_chain_edits([ch], {"v02:9": (toks, words)}, {"v02:9#s": dict(add=[dict(t=4, a=5, b=6), dict(t=12, a=13, b=16)])})
    # «ابیه» (added, at 5) comes between «یحیی بن سعید» (1) and «ابن عباس» (7); the unfound places stick to the place before them
    assert [e["i"] for e in ch["elems"]] == [0, 1, 2, 1000, 3, 4, 1001]
    assert ch["speaker"]["elem"] is None
    # a one-place chain with no speaker is flagged
    ch3 = dict(aid="v02:9#s", wid="v02:9", source="noor_slots", speaker=None, quoted_work=None, flags=[], elems=[el(0, "مالک", role="collector"), el(1, "یحیی بن سعید")])
    identity.apply_chain_edits([ch3], {"v02:9": (toks, words)}, {"v02:9#s": dict(drop=[1])})
    assert [e["i"] for e in ch3["elems"]] == [0] and ch3["flags"] == ["no_sanad"]


def test_chain_edits_no_two_places_share_a_word_and_each_keeps_its_automatic_reading():
    toks, words, _ = identity.hadith_words(_REC)
    texts = {"v02:9": (toks, words)}
    # a place added over the automatic words of «ابن عباس» (place 3, [7, 9)), place 3 itself untouched: the user's span wins
    ch = identity.text_chain(_REC)
    identity.apply_chain_edits([ch], texts, {"v02:9#t": dict(add=[dict(t=6, a=7, b=9)])})
    by = {e["i"]: e for e in ch["elems"]}
    assert by[3]["span"] is None and by[3]["span_auto"] == dict(t=6, a=7, b=9) and ch["flags"].count("chain_overlap") == 1
    assert by[1000]["span"] == dict(t=6, a=7, b=9) and by[1000]["span_auto"] is None
    assert by[1]["span"] == by[1]["span_auto"] == dict(t=0, a=1, b=4)
    assert [e["i"] for e in ch["elems"]] == [0, 1, 2, 3, 1000]           # the unplaced one stays behind the place before it
    assert "removed" not in ch                                              # a removed place is gone, not kept for restoring
    # an edited place keeps its automatic reading (what «back to automatic» gives); no overlap, no flag
    ch2 = identity.text_chain(_REC)
    identity.apply_chain_edits([ch2], texts, {"v02:9#t": dict(span={"1": dict(t=1, a=1, b=4)}, drop=[2])})
    e1 = next(e for e in ch2["elems"] if e["i"] == 1)
    assert e1["span"] == dict(t=1, a=1, b=4) and e1["span_auto"] == dict(t=0, a=1, b=4)
    assert "chain_overlap" not in ch2["flags"] and "removed" not in ch2 and 2 not in [e["i"] for e in ch2["elems"]]


def test_is_transmit_marks_phrases_and_their_prepositions():
    from icm import sanad_parse
    assert sanad_parse.is_transmit(None, "حدثنا") and sanad_parse.is_transmit("حدثنا", "عن") and sanad_parse.is_transmit(None, "وعن")
    assert sanad_parse.is_transmit("قرات", "علی") and not sanad_parse.is_transmit("یحیی", "علی") and not sanad_parse.is_transmit(None, "سعید")
