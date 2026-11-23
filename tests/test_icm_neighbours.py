"""The rules below the written-name model: cleaning of isnad wording, «عن أبيه» as the father of the known narrator, a dominant name
vouched for by a neighbour, the suggestion tier (icm/neighbours.py, identity._neighbour_links) and the consistency of name_major."""
import os, shutil, sqlite3, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from icm import config, identity, names, neighbours, sanad_parse
from icm.build import names as build_names
from test_icm_names import _el, _fake_sources, _get


def test_isnad_words_glued_to_a_name_are_cut_off_and_wording_alone_is_not_a_name():
    core = lambda n: sanad_parse.strip_nonname(n)["core"]
    assert core("قلت لابی عبد الله") == "ابی عبد الله"                       # a verb, and the preposition glued to the kunya
    assert core("نبا عمرو بن خالد الخزاعی") == "عمرو بن خالد الخزاعی"
    assert core("خ ونا موسی بن اسماعیل") == "موسی بن اسماعیل"                 # a siglum, a short «حدثنا»
    assert core("محمد بن علی انه") == "محمد بن علی"                          # the name ends before «انه»
    assert core("وبالاسناد الی ابی عبد الله") == "ابی عبد الله"
    assert core("عبد الله بن مسعود") == "عبد الله بن مسعود"                   # «الله» inside a name stays
    for junk in ("الله", "ما", "الفقیه والتهذیب", "الکافی عنه", "وعنه", "سالت"):
        assert sanad_parse.not_a_name(junk), junk
    assert not sanad_parse.not_a_name("عبد الله بن مسعود") and not sanad_parse.not_a_name("الزهری")


def test_a_place_with_two_names_is_split_at_the_conjunction_and_the_first_is_looked_up():
    assert identity.clean_parts("محمد بن عبد الله الحضرمی و زکریا بن یحیی الساجی") == ["محمد بن عبد الله الحضرمی", "زکریا بن یحیی الساجی"]
    assert identity.clean_parts("قلت لابی عبد الله") == ["ابی عبد الله"]
    assert identity.clean_parts("وعنه") == [""] or identity.clean_parts("وعنه") == []


def test_name_major_is_only_asked_for_on_request():
    t = {("namem", ("زهري",)): (11, 100, 101, 2)}
    assert names.decide(_get(t), "زهري", "a", "b")[0] is None
    r, _ = names.decide(_get(t), "زهري", "a", "b", major=True)
    assert r["rule"] == "name_major" and r["id"] == 11 and r["tot"] == 101


def test_a_dominant_name_counts_only_when_it_dominates_in_every_group_of_books():
    old = names.MAJOR, names.GROUP_MIN, names.GROUP_SHARE, build_names.FOLDS
    try:
        names.MAJOR, names.GROUP_MIN, names.GROUP_SHARE, build_names.FOLDS = (0.8, 4), 2, 0.8, 2
        nm = build_names.Names()
        zuhri, shaykh = nm.of("زهري"), nm.of("شيخ")
        data = []
        for g in (0, 1):                                    # two groups of books; «الزهري» is 7 in both, «الشيخ» is 5 in one and 6 in the other
            for _ in range(5):
                data.append((g, [((zuhri,), (7,))]))
                data.append((g, [((shaykh,), (5 if g == 0 else 6,))]))
        data.append((0, [((zuhri,), (8,))]))               # noise: another person once
        D = build_names.count(data)
        assert ("namem", zuhri, 0, 0) in D and ("namem", shaykh, 0, 0) not in D
        assert build_names.summary(D[("namem", zuhri, 0, 0)])[0] == 7
    finally:
        names.MAJOR, names.GROUP_MIN, names.GROUP_SHARE, build_names.FOLDS = old


def _narrators(d):
    """persons 1 (محمد بن يعقوب بن اسحاق), 2 (يعقوب بن اسحاق: his father), 3 (يعقوب بن داود) and who is next to whom"""
    p = os.path.join(d, "narrators.sqlite")
    c = sqlite3.connect(p)
    c.execute("create table ravi(ravi_id integer primary key, name text)")
    c.execute("create table adjacency(teacher integer, student integer, n_exact integer, w_amb real, n_hadith integer)")
    c.executemany("insert into ravi values (?,?)", [(1, "محمد بن يعقوب بن اسحاق"), (2, "يعقوب بن اسحاق"), (3, "يعقوب بن داود"), (4, "سالم")])
    c.commit()
    c.close()
    return p


def _with_narrators(rows):
    d = tempfile.mkdtemp(prefix="icmneigh_")
    p = _narrators(d)
    c = sqlite3.connect(p)
    c.executemany("insert into adjacency values (?,?,?,?,?)", [(t, s, n, 0.0, n) for t, s, n in rows])
    c.commit()
    c.close()
    old = config.NARRATORS_DB
    config.NARRATORS_DB = p
    neighbours._around.cache_clear()
    neighbours._tokens.cache_clear()
    neighbours._local.n = None
    return d, old


def _restore(d, old):
    config.NARRATORS_DB = old
    neighbours._around.cache_clear()
    neighbours._tokens.cache_clear()
    neighbours._local.n = None
    shutil.rmtree(d, ignore_errors=True)


def test_the_father_is_the_person_with_the_fathers_name_who_is_most_often_next_to_the_narrator():
    d, old = _with_narrators([(2, 1, 10), (3, 1, 2), (4, 1, 50)])           # (4 is next to him too, but his name is not the father's)
    try:
        assert neighbours.father_token("noor:1") == "يعقوب"
        link, hint = neighbours.father_rule("noor:1")
        assert link["id"] == "noor:2" and link["rule"] == "father_of_prev" and link["n"] == 10 and hint is None       # 10 >= 3 × 2
    finally:
        _restore(d, old)
    d, old = _with_narrators([(2, 1, 10), (3, 1, 6)])                         # a lead, but not a clear one: only a suggestion
    try:
        link, hint = neighbours.father_rule("noor:1")
        assert link is None and hint["id"] == "noor:2" and hint["prec"] < neighbours.REL_PREC
    finally:
        _restore(d, old)
    d, old = _with_narrators([(2, 1, 1)])                                     # one pair is no evidence
    try:
        assert neighbours.father_rule("noor:1") == (None, None)
    finally:
        _restore(d, old)


def test_the_ranking_of_an_ambiguous_name_needs_a_big_lead_or_both_neighbours():
    old = neighbours.around, names.persons_written
    try:
        names.persons_written = lambda db, k, limit=6: [(1, 10), (2, 10)]
        neighbours.around = lambda pid: ({"noor:1": 60}, {"noor:1": 0})      # the person before has taught noor:1 in 60 hadiths
        h = neighbours.hint("noor", "k", ["noor:1", "noor:2"], "noor:9", None)
        assert h["id"] == "noor:1" and h["rule"] == "adj" and h["prec"] == neighbours.ADJ_PREC["noor"]
        neighbours.around = lambda pid: ({"noor:1": 4}, {"noor:1": 4})       # a smaller lead, but both neighbours say the same and nobody else does
        h = neighbours.hint("noor", "k", ["noor:1", "noor:2"], "noor:9", "noor:8")
        assert h["rule"] == "adj_one" and h["id"] == "noor:1"
        assert neighbours.hint("noor", "k", ["noor:1", "noor:2"], None, None) is None            # no neighbour, no ranking
        neighbours.around = lambda pid: ({"noor:1": 4, "noor:2": 3}, {})
        assert neighbours.hint("noor", "k", ["noor:1", "noor:2"], "noor:9", None) is None
    finally:
        neighbours.around, names.persons_written = old


def _chain_with_father():
    return dict(aid="w:1#t", wid="w:1", source="text", speaker=None, quoted_work=None, flags=[],
                elems=[_el(0, "الكليني", None, "collector", "collector"), _el(1, "محمد بن يعقوب", "noor:1"),
                       dict(_el(2, "ابیه", None, kind="relative"), name="ابیه")])


def test_the_father_of_a_known_narrator_is_an_automatic_link_and_a_weak_lead_a_suggestion():
    d, old = _with_narrators([(2, 1, 10), (3, 1, 2)])
    label, ni = identity._label, identity.noor_index
    identity._label = lambda i: i
    identity.noor_index = lambda: dict(ravis={}, post={}, titles={})
    try:
        ch = [_chain_with_father()]
        identity.auto_links(ch, {"w:1": "shia"}, [dict(wid="w:1", corpus="x")], twins=False)
        e = ch[0]["elems"][2]
        assert e["auto"]["id"] == "noor:2" and e["auto"]["rule"] == "father_of_prev" and e["auto"]["src"] == "father"
        assert e["cands"][0]["id"] == "noor:2" and e["cands"][0]["tier"] == "auto" and "sugg" not in e
        _restore(d, old)
        d, old = _with_narrators([(2, 1, 10), (3, 1, 6)])
        ch = [_chain_with_father()]
        identity.auto_links(ch, {"w:1": "shia"}, [dict(wid="w:1", corpus="x")], twins=False)
        e = ch[0]["elems"][2]
        assert "auto" not in e and e["sugg"]["id"] == "noor:2" and e["cands"][0]["tier"] == "sugg"
    finally:
        identity._label, identity.noor_index = label, ni
        _restore(d, old)


def test_a_suggestion_is_used_nowhere_until_the_user_approves_it():
    ch = [_chain_with_father()]
    e = ch[0]["elems"][2]
    e["sugg"] = dict(id="noor:2", rule="father_of_prev", n=10, tot=16, src="father", prec=0.96)
    e["cands"] = [dict(id="noor:2", label="يعقوب", score=1.0, cov=0.0, tier="sugg")]
    trad = {"w:1": "shia"}
    cards = identity.make_cards(ch, trad)
    assert cards[e["card"]]["suggs"] == {"noor:2": [["w:1#t", 2]]} and cards[e["card"]]["default"] is None
    ident = dict(chains=ch, cards=cards)
    place = "w:1#t|2"
    def tier(r):
        x = next(x for c in r["chains"] for x in c["elems"] if x["i"] == 2)
        return x["tier"], x["nid"]
    assert tier(identity.resolve(ident, {})) == ("name", "rel:w:1#t:2")                          # not used
    assert tier(identity.resolve(ident, dict(approved={place: "noor:2"}))) == ("chosen", "noor:2")
    assert identity.approved_pins(ident, {place: "noor:2"}) == {place: "noor:2"}               # the tree changes: it is part of the graph's key
    assert identity.approved_pins(ident, {}) == {}


def test_a_sentence_no_name_resembles_is_a_fragment():
    old = names.vocab
    names.vocab = lambda: {"معمر": 50, "الغريب": 9, "بن": 900, "سالم": 40}
    try:
        assert not names.fragment("معمر")
        assert not names.fragment("معمر الغريب")
        assert not names.fragment("معمر بن سالم الجديدي")                                         # one word we have not seen is a name we have not seen
        assert names.fragment("وكان المسلمون تضيق صدورهم مما يوسوس")
        names.vocab = lambda: {}
        assert not names.fragment("وكان المسلمون تضيق صدورهم")                                    # without the names file nothing is decided
    finally:
        names.vocab = old


def test_the_father_of_an_imam_comes_from_the_table_of_imams_not_from_the_pairs():
    d, old = _with_narrators([(4, 2107, 50)])                                # (a «محمد» who sits next to the Sadiq in many isnads is not his father)
    label, ni = identity._label, identity.noor_index
    identity._label = lambda i: i
    identity.noor_index = lambda: dict(ravis={}, post={}, titles={})
    try:
        ch = [_chain_with_father()]
        ch[0]["elems"][1]["exact"] = "noor:2107"                             # جعفر بن محمد الصادق
        identity.auto_links(ch, {"w:1": "shia"}, [dict(wid="w:1", corpus="x")], twins=False)
        e = ch[0]["elems"][2]
        assert e["auto"]["id"] == "noor:5485" and e["auto"]["src"] == "imams"      # the Baqir
    finally:
        identity._label, identity.noor_index = label, ni
        _restore(d, old)
