"""The middle level of certainty: written names in company (icm/names.py, icm/build/names.py) and the automatic links of
identity.auto_links with their approval."""
import os, shutil, sqlite3, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm import config, identity, names
from icm.build import names as build_names


def _get(table):
    """a ``get`` over {(tier, parts): (pid, support, total, people)}"""
    return lambda tier, parts: table.get((tier, tuple(parts)))


def test_a_context_decides_only_with_enough_hadiths_and_one_person():
    t = {("ctx3", ("زهري", "معمر", "سالم")): (11, 2, 2, 1)}
    r, seen = names.decide(_get(t), "زهري", "معمر", "سالم")
    assert r["id"] == 11 and r["rule"] == "ctx3" and seen
    t = {("ctx3", ("زهري", "معمر", "سالم")): (11, 1, 1, 1)}                      # one hadith is not enough
    assert names.decide(_get(t), "زهري", "معمر", "سالم")[0] is None
    t = {("prev", ("زهري", "معمر")): (11, 2, 2, 1)}                              # one neighbour needs three
    assert names.decide(_get(t), "زهري", "معمر", "x")[0] is None
    t = {("prev", ("زهري", "معمر")): (11, 3, 3, 1)}
    assert names.decide(_get(t), "زهري", "معمر", "x")[0]["rule"] == "prev"


def test_an_ambiguous_context_blocks_the_weaker_ones_unless_one_person_dominates():
    t = {("ctx3", ("سفيان", "a", "b")): (1, 5, 10, 2), ("name", ("سفيان",)): (1, 50, 50, 1)}
    r, seen = names.decide(_get(t), "سفيان", "a", "b")
    assert r is None and seen                                                     # seen with two people: the name alone must not decide
    t[("ctx3", ("سفيان", "a", "b"))] = (1, 29, 30, 2)                             # 97% of 30 hadiths
    r, _ = names.decide(_get(t), "سفيان", "a", "b")
    assert r["rule"] == "ctx3_major" and r["id"] == 1


def test_the_name_alone_a_relative_at_the_head_and_the_neighbour_person():
    t = {("name", ("ابن عباس",)): (7, 5, 5, 1)}
    assert names.decide(_get(t), "ابن عباس", "^", "$")[0]["rule"] == "name"
    assert names.decide(_get({("name", ("ابيه",)): (7, 9, 9, 1)}), "ابيه", "^", "x")[0] is None       # whose father depends on the author
    t = {("pprev", ("زهري", 5)): (11, 3, 3, 1)}
    assert names.decide(_get(t), "زهري", "a", "b", prev_pid=5)[0]["rule"] == "pprev"
    assert names.decide(_get(t), "زهري", "a", "b", prev_pid=6)[0] is None
    off = dict(need=names.NEED, off={"pprev"})                                    # a rule the evaluation switched off
    assert names.decide(_get(t), "زهري", "a", "b", prev_pid=5, cfg=off)[0] is None


def _fake_sources(d):
    """Noor: three hadiths in two books. 1 and 2 write «معمر / الزهري / سالم» (ids 10, 11, 12) after the collector's own
    slot (type 4) and before the Prophet; 1 has a second sanad with the same names (one hadith, not two); 3 is in the
    other book. KG: hadith 1 names the same three inline."""
    nd = os.path.join(d, "narrators.sqlite")
    c = sqlite3.connect(nd)
    c.execute("create table slot(hadith_id, sanad_idx, pos, order_no, title, type, term, ravi_id, cand_json)")
    c.execute("create table hadith(hadith_id integer primary key, book text, group_id, century, n_sanads)")
    for h, book in ((1, "A"), (2, "A"), (3, "B"), (4, "B")):
        c.execute("insert into hadith values (?,?,?,?,?)", (h, book, None, None, 1))
        for sidx in ((0, 1) if h == 1 else (0,)):
            rows = [("الكليني", 4, 5), ("معمر", 0, 10), ("الزهري", 0, 11), ("سالم", 0, 12), ("رسول الله", 0, 2512)]
            for pos, (t, ty, rid) in enumerate(rows):
                c.execute("insert into slot values (?,?,?,?,?,?,?,?,?)", (h, sidx, pos, pos, t, ty, "عن", rid, "[]"))
    c.commit(); c.close()
    kd = os.path.join(d, "hadith-kg.db")
    k = sqlite3.connect(kd)
    k.execute("create table hadiths(id integer primary key, book_id integer, nass text, matn_start integer, matn_end integer)")
    k.execute("create table hadith_rawis(hadith_id, seq, rawi_id, start, end)")
    nass = "حدثنا معمر عن الزهري عن سالم قال متن"
    k.execute("insert into hadiths values (1, 9, ?, ?, ?)", (nass, nass.index("قال"), len(nass)))
    for seq, w in enumerate(("معمر", "الزهري", "سالم")):
        a = nass.index(w)
        k.execute("insert into hadith_rawis values (1, ?, ?, ?, ?)", (seq, 10 + seq, a, a + len(w)))
    k.commit(); k.close()
    return nd, kd


def test_the_builder_counts_per_hadith_drops_the_collector_and_the_prophet_and_the_file_answers():
    d = tempfile.mkdtemp(prefix="icmnames_")
    old = config.NARRATORS_DB, config.KG_SRC, config.NAMES_DB, build_names.MIN_FIRED, build_names.FOLDS
    try:
        config.NARRATORS_DB, config.KG_SRC = _fake_sources(d)
        config.NAMES_DB = os.path.join(d, "names.sqlite")
        build_names.MIN_FIRED, build_names.FOLDS = 1, 2
        build_names.run(config.NAMES_DB, log=lambda *a: None)
        names._local.c = False                                                     # reopen
        names._META.clear()
        assert names.available() and names.key()
        r, seen = names.lookup("noor", "الزهري", "معمر", "سالم")
        assert r and r["id"] == 11 and r["rule"] == "ctx3" and r["n"] == 4 and r["tot"] == 4       # 4 hadiths, the second sanad of 1 counted once
        r, _ = names.lookup("noor", "معمر", "^", "الزهري")                                           # the collector's slot is not a neighbour
        assert r and r["id"] == 10
        r, _ = names.lookup("noor", "سالم", "الزهري", "$")                                           # the Prophet is not a neighbour
        assert r and r["id"] == 12
        assert names.kg_written(1) == {10: ["معمر"], 11: ["الزهري"], 12: ["سالم"]}
        assert "noor" in names.meta()["eval"] and names.meta()["counts"]["kg"] == 3
    finally:
        config.NARRATORS_DB, config.KG_SRC, config.NAMES_DB, build_names.MIN_FIRED, build_names.FOLDS = old
        names._local.c = False
        names._META.clear()
        shutil.rmtree(d, ignore_errors=True)


def _el(i, name, exact=None, kind="name", role=None):
    e = dict(i=i, name=name, raw=name, term="عن", kind=kind, exact=exact, imam_hint=False, cands=[])
    if exact:
        e["cands"] = [dict(id=exact, label=name, score=9.0, cov=1.0, exact=True, tier="exact")]
    if role:
        e["role"] = role
    return e


def _family_chains():
    """A has its narrators settled by the source; B (a text) writes the same names."""
    A = dict(aid="w:1#0", wid="w:1", source="noor_slots", speaker=None, quoted_work=None, flags=[],
             elems=[_el(0, "الكليني", "noor:5485", "collector", "collector"), _el(1, "معمر", "noor:2107"), _el(2, "الزهري", "noor:5485"), _el(3, "سالم", "noor:2107")])
    B = dict(aid="w:2#t", wid="w:2", source="text", speaker=None, quoted_work=None, flags=[],
             elems=[_el(0, "الطبراني", None, "collector", "collector"), _el(1, "معمر"), _el(2, "الزهري"), _el(3, "رجل من اهل المدينة", None, "anon"), _el(4, "غيره")])
    return [A, B]


def test_family_transfer_gives_a_text_place_the_person_of_the_same_name_and_neighbours():
    if not os.path.exists(config.NARRATORS_DB):
        return
    ch = _family_chains()
    trad = {"w:1": "shia", "w:2": "shia"}
    n = identity.auto_links(ch, trad, [dict(wid="w:1", corpus="x"), dict(wid="w:2", corpus="x")], twins=False)
    b = {e["i"]: e for e in ch[1]["elems"]}
    assert b[1]["auto"]["id"] == "noor:2107" and b[1]["auto"]["src"] == "family"          # «معمر» at the head, before «الزهري»
    assert "auto" not in b[3] and "auto" not in b[0]                                       # an anonymous place and the collector stay
    assert b[1]["cands"][0]["id"] == "noor:2107" and b[1]["cands"][0]["tier"] == "auto"    # put first, with its own tier
    assert n >= 1
    assert all("auto" not in e for e in ch[0]["elems"])                                    # settled places need nothing


def test_resolve_uses_an_automatic_link_until_the_user_chooses_and_an_approval_pins_it():
    if not os.path.exists(config.NARRATORS_DB):
        return
    ch = _family_chains()
    trad = {"w:1": "shia", "w:2": "shia"}
    identity.auto_links(ch, trad, [dict(wid="w:1", corpus="x"), dict(wid="w:2", corpus="x")], twins=False)
    cards = identity.make_cards(ch, trad)
    ident = dict(chains=ch, cards=cards)
    tier = lambda r, aid, i: next((e["tier"], e["nid"], e.get("auto")) for c in r["chains"] if c["aid"] == aid for e in c["elems"] if e["i"] == i)
    r = identity.resolve(ident, dict(choices={}))
    assert tier(r, "w:2#t", 1) == ("default", "noor:2107", "family") or tier(r, "w:2#t", 1)[:2] == ("default", "noor:2107")
    assert "w:2#t|1" not in r["undecided"] and r["undecided"] == sorted(r["undecided"])
    cd = cards[ch[1]["elems"][1]["card"]]
    assert cd["autos"] == {"noor:2107": [["w:2#t", 1]]}
    r = identity.resolve(ident, dict(choices={cd["key"]: "noor:5485"}))                    # the user's choice wins over the link
    assert tier(r, "w:2#t", 1)[:2] == ("chosen", "noor:5485")
    r = identity.resolve(ident, dict(choices={}, approved={"w:2#t|1": "noor:2107"}))
    assert tier(r, "w:2#t", 1)[:2] == ("chosen", "noor:2107")                              # approved: the user's own
    r = identity.resolve(ident, dict(choices={}, overrides={"w:2#t|1": "noor:5485"}, approved={"w:2#t|1": "noor:2107"}))
    assert tier(r, "w:2#t", 1)[:2] == ("chosen", "noor:5485")                              # a choice for the place beats an approval


def test_only_an_approval_that_names_another_person_than_the_link_reaches_the_graph():
    ident = dict(chains=[dict(aid="a#0", elems=[dict(i=1, auto=dict(id="noor:1")), dict(i=2, auto=dict(id="noor:2")), dict(i=3)])])
    pins = identity.approved_pins(ident, {"a#0|1": "noor:1", "a#0|2": "noor:9", "a#0|3": "noor:3", "zz#0|1": "noor:1"})
    assert pins == {"a#0|2": "noor:9"}                                                      # the first confirms the link; the others have no link
    assert identity.approved_pins(ident, None) == {}


def test_a_default_the_names_contradict_is_taken_back():
    cards = {"shia:ابن عمر": dict(key="shia:ابن عمر", name="ابن عمر", kind="name", default="noor:1", candidates=[dict(id="noor:1")],
                                  autos={"noor:2": [["a", 1]]}, occurrences=[["a", 1]])}
    assert identity._veto_defaults(cards, {}) == 1 and cards["shia:ابن عمر"]["default"] is None
    cards = {"shia:ابن عمر": dict(key="shia:ابن عمر", name="ابن عمر", kind="name", default="kg:1", candidates=[dict(id="kg:1", also=["noor:2"])],
                                  autos={"noor:2": [["a", 1]]}, occurrences=[["a", 1]])}
    assert identity._veto_defaults(cards, {}) == 0                                          # the same person under another database's id


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
    print("ok")
