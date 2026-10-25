import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm import align
from icm.text import clauses, tokens


def _search(seed_text, witnesses):
    tk, sp = clauses(seed_text)
    cands = []
    for i, t in enumerate(witnesses):
        wt = " ".join(tokens(t))
        cands.append(dict(wid=f"t:{i}", window_text=wt, matn=t, title="", author="", death=None, tradition="",
                          work=f"w{i}", suggested="witness", copies=[]))
    return dict(seed=dict(wid="t:seed", matn=seed_text, title="", author="", death=None, tradition="", work="s", copies=[]),
                tokens=tk, clauses=[dict(i=i, span=list(s), text=" ".join(tk[s[0]:s[1]])) for i, s in enumerate(sp)],
                candidates=cands)


SEED = "ستة لعنتهم، المكذب بقدر الله، والزائد في كتاب الله، والتارك لسنتي، والمستحل من عترتي ما حرم الله"


def test_presence_and_number_units():
    w = [
        "ستة لعنتهم، المكذب بقدر الله، والزائد في كتاب الله، والتارك لسنتي، والمستحل من عترتي ما حرم الله",
        "سبعة لعنتهم، والزائد في كتاب الله، المكذب بقدر الله، والتارك لسنتي، والمستحل من عترتي ما حرم الله",
        "ستة لعنتهم، المكذب بقدر الله، والزائد في كتاب الله، والمستحل من عترتي ما حرم الله",
        "ستة لعنتهم، المكذب بقدر الله، والزائد في كتاب الله، والمستحل من عترتي ما حرم الله",
        "ستة لعنتهم، المكذب بقدر الله، والتارك لسنتي، والمستحل من عترتي ما حرم الله",
        "سبعة لعنتهم، المكذب بقدر الله، والتارك لسنتي، والمستحل من عترتي ما حرم الله",
    ]
    r = align.run(_search(SEED, w), [f"t:{i}" for i in range(len(w))])
    names = [u["name"] for u in r["units"]]
    num = [u for u in r["units"] if u["kind"] == "numeric"]
    assert num and set(num[0]["labels"]) == {"6", "7"}, names
    # the clause "والتارك لسنتي" is absent in two witnesses and present in four
    pres = [u for u in r["units"] if u["kind"] == "presence" and "التارک" in u["name"]]
    assert pres, names
    st = pres[0]["states"]
    assert sorted(st.values()).count(0) == 2
    # reordering does not change presence
    assert r["X"]["t:1"] is not None


def test_abridgement_marker_makes_absence_unknown():
    w = [
        SEED,
        SEED,
        "ستة لعنتهم، المكذب بقدر الله وساق الحديث",
        "ستة لعنتهم، المكذب بقدر الله وساق الحديث",
        "ستة لعنتهم المكذب بقدر الله والزائد في كتاب الله والتارك لسنتي",
        "ستة لعنتهم المكذب بقدر الله والزائد في كتاب الله والتارك لسنتي",
    ]
    r = align.run(_search(SEED, w), [f"t:{i}" for i in range(len(w))])
    for u in r["units"]:
        if u["kind"] == "presence" and "المستحل" in u["name"]:
            assert u["states"]["t:2"] == -1 and u["states"]["t:3"] == -1


def test_slot_keeps_edge_words_and_drops_formula():
    seed = "ستة لعنهم الله وكل نبي مجاب، المكذب بقدر الله، والتارك لسنتي"
    w = [
        seed,
        "سبعة لعنتهم لعنهم الله تعالى وكل نبي مجاب، والمكذب بقدر الله، والتارك لسنتي",   # an inserted word before "لعنهم"
        "سبعة لعنتهم لعنهم الله وكل نبي مجاب، والمكذب بقدر الله",
        "قال رسول الله صلى الله عليه وآله خمسة لعنتهم وكل نبي مجاب، المكذب بقدر الله",       # the formula's "الله"
        "قال رسول الله صلى الله عليه وآله خمسة لعنتهم وكل نبي مجاب، المكذب بقدر الله، والتارك لسنتي",
    ]
    r = align.run(_search(seed, w), [f"t:{i}" for i in range(len(w))])
    assert r["segments"]["0|t:1"]["tokens"][0] == "سبعه", r["segments"]["0|t:1"]
    assert r["segments"]["0|t:3"]["tokens"][0] == "خمسه", r["segments"]["0|t:3"]
    num = [u for u in r["units"] if u["kind"] == "numeric"]
    assert num, [u["name"] for u in r["units"]]
    lab = num[0]["labels"]
    st = {w: (lab[v] if v >= 0 else None) for w, v in num[0]["states"].items()}
    assert st == {"t:seed": "6", "t:0": "6", "t:1": "7", "t:2": "7", "t:3": "5", "t:4": "5"}, st
    # every unit points at its grid columns (None for a whole clause)
    assert all("cols" in u for u in r["units"])
    g = r["grids"][0]
    j = num[0]["cols"][0]
    assert {g["columns"][j].get(w) for w in ("t:1", "t:3")} == {"سبعه", "خمسه"}


def test_user_parts_slots_extras_and_drops():
    """The user's corrections (the ``parts`` decision) replace the automatic slots and extra parts."""
    w = [
        SEED,
        "ستة لعنتهم، المكذب بقدر الله، والزائد في كتاب الله، والتارك لسنتي، والمستأثر بالفيء لنفسه، والمستحل من عترتي ما حرم الله",
        "ستة لعنتهم، المكذب بقدر الله، والتارك لسنتي، والمستأثر بالفيء لنفسه، والمستحل من عترتي ما حرم الله",
        "ستة لعنتهم المكذب بقدر الله والزائد في كتاب الله وساق الحديث",
    ]
    S = _search(SEED, w)
    sel = [f"t:{i}" for i in range(len(w))]
    auto = align.run(S, sel)
    keys = [c["key"] for c in auto["clauses"]]
    assert all(k.startswith("s:") for k in keys[:len(S["clauses"])]) and len(set(keys)) == len(keys), keys
    extra = [c for c in auto["clauses"] if c["kind"] == "extra"]
    assert extra and "المستاثر" in extra[0]["text"], auto["clauses"]
    # drop the automatic extra part, add one cut from a witness, and set slots
    k_tarik = next(c["key"] for c in auto["clauses"] if "التارک" in c["text"])
    t1 = tokens(w[1])
    a = t1.index("والمستاثر")
    parts = dict(extras_dropped=[extra[0]["key"]], extras_added=[dict(wid="t:1", span=[a, a + 1])],
                 slots={k_tarik: {"t:1": "absent", "t:3": "absent"}})
    r = align.run(S, sel, parts)
    ex = [c for c in r["clauses"] if c["kind"] == "extra"]
    assert len(ex) == 1 and ex[0]["by"] == "user" and ex[0]["text"] == "والمستاثر", ex
    ci = ex[0]["i"]
    assert r["segments"][f"{ci}|t:1"]["user"] and r["segments"][f"{ci}|t:1"]["tokens"] == ["والمستاثر"]
    ti = next(c["i"] for c in r["clauses"] if c["key"] == k_tarik)
    assert r["segments"][f"{ti}|t:1"]["state"] == "absent" and r["segments"][f"{ti}|t:1"]["user"]
    pres = [u for u in r["units"] if u["unit_kind"] == "clause" and u["clause"] == ti]
    assert pres, [u["name"] for u in r["units"]]
    # an abridged witness the user says lacks the part is absent (0), not unknown
    assert pres[0]["states"]["t:3"] == 0 and pres[0]["states"]["t:1"] == 0, pres[0]["states"]
    # a slot given by hand: the tokens of the stored text, in window coordinates
    k_mukadh = next(c["key"] for c in r["clauses"] if "المکذب" in c["text"])
    t2 = tokens(w[2])
    b = t2.index("المکذب")
    r2 = align.run(S, sel, dict(slots={k_mukadh: {"t:2": [b, b + 2]}}))
    mi = next(c["i"] for c in r2["clauses"] if c["key"] == k_mukadh)
    sg = r2["segments"][f"{mi}|t:2"]
    assert sg["tokens"] == t2[b:b + 2] and sg["span"] == [b, b + 2] and sg["state"] == "present" and sg["user"], sg
    # corrections for parts or witnesses not in the alignment are reported, not applied
    r3 = align.run(S, sel, dict(slots={"s:نیست": {"t:1": "absent"}}))
    assert any(f["kind"] == "part_unused" for f in r3["flags"])


def _cut(*pieces):
    """tokens and spans of a hadith written as its parts."""
    tok, sp = [], []
    for s in pieces:
        t = tokens(s)
        sp.append((len(tok), len(tok) + len(t)))
        tok += t
    return tok, sp


def test_source_hadiths_are_cut_on_their_own_and_the_cuts_that_agree_are_one_part():
    from icm import family
    ca = {"A": _cut("ستة لعنتهم", "المكذب بقدر الله", "والزائد في كتاب الله", "والتارك لسنتي"),
          "B": _cut("ستة لعنتهم", "المكذب بقدر الله", "والمستأثر بالفيء لنفسه", "والتارك لسنتي")}
    fwd = family.build(ca)
    back = family.build({k: ca[k] for k in ("B", "A")})                        # the order of the list is no part of it
    assert [(p["text"], p["members"]) for p in fwd] == [(p["text"], p["members"]) for p in back]
    assert sorted(len(p["members"]) for p in fwd) == [1, 1, 2, 2, 2], [(p["text"], list(p["members"])) for p in fwd]
    tok, sp = family.reference(fwd)
    assert [" ".join(tok[x:y]) for x, y in sp] == [p["text"] for p in fwd]
    # one hadith alone: its parts are its cut, as they were
    one = family.build({"A": ca["A"]})
    assert [p["members"]["A"] for p in one] == [list(x) for x in ca["A"][1]]
    assert family.canonical({"A": {}, "B": {}}, fwd) == "A"                     # the same number of parts and words: the lower id names the family
    lab = family.labels(len(ca["B"][0]), fwd, "B")
    assert -1 not in lab and len(set(lab)) == 4


def test_a_part_one_hadith_has_in_its_own_cut_is_aligned_from_that_cut():
    """The words of each source hadith in a part are the ones its own cut has (the part's words are its representative's)."""
    own = "ستة لعنتهم، المكذب بقدر الله، والزائد في كتاب الله، والتارك لسنتي، والمستأثر بالفيء لنفسه، والمستحل من عترتي ما حرم الله"
    S = _search(SEED, [own])
    c = S["candidates"][0]
    c.update(signals=["seed"])
    tk = tokens(own)
    x = tk.index(next(t for t in tk if "مستاثر" in t))
    S["clauses"].append(dict(i=len(S["clauses"]), span=[len(S["tokens"]), len(S["tokens"]) + 3], text=" ".join(tk[x:x + 3]), members={"t:0": [x, x + 3]}))
    S["tokens"] = S["tokens"] + tk[x:x + 3]
    S["seed_tokens"] = tokens(SEED)
    for q in S["clauses"][:-1]:
        q["members"] = {"t:seed": q["span"]}
    r = align.run(S, ["t:0"])
    seg = r["segments"][f"{len(S['clauses']) - 1}|t:0"]
    assert seg["state"] == "present" and seg["tokens"] == tk[x:x + 3] and seg["span"] == [x, x + 3], seg
    assert r["segments"][f"{len(S['clauses']) - 1}|t:seed"]["state"] == "absent"


def test_bare_conjunction_is_part_of_the_next_word():
    from icm.similarity import align_pair, conj_groups, match_key
    assert conj_groups(["قال", "و", "و", "الصوم", "و"]) == [(0, 1), (1, 4), (4, 5)]
    assert align.join_conj(["قال", "و", "و", "الصوم"]) == ["قال", "والصوم"] and align.join_conj(["قال", "و"]) == ["قال"]
    assert match_key("ولا") == match_key("لا") == "لا" and match_key("ولد") == "ولد" and match_key("وكل".replace("ك", "ک")) == "کل"
    a = "لیس العباده کثره الصلاه و الصوم".split()
    # a «و» left at the end of the text matches nothing, and «و الصوم» is the same word as «الصوم» and as «والصوم»
    r = align_pair(a, "لیس العباده کثره الصلاه ولا الصوم و".split())
    assert r["match_a"] == [True] * 6 and r["span_b"] == (0, 6) and r["aligned_b"][-1] == "الصوم"
    r = align_pair(a, "لیس العباده کثره الصیام و الصلاه و".split())
    assert r["span_b"][1] == 7 - 1 and not any(r["match_a"][4:])           # the final «و» is not a word of the slot


def test_separate_waw_does_not_make_a_column_of_its_own():
    seed = "ليس العبادة كثرة الصلاة و الصوم، وإنما العبادة التفكر في أمر الله"
    w = ["ليس العبادة كثرة الصلاة و الصوم وإنما العبادة التفكر في أمر الله",
         "ليس العبادة كثرة الصلاة ولا الصوم وإنما العبادة التفكر في أمر الله",
         "ليس العبادة كثرة الصيام و الصلاة و إنما العبادة التفكر في أمر الله",
         "ليس العبادة كثرة الصيام و الصلاة و إنما العبادة التفكر في أمر الله"]
    S, sel = _search(seed, w), [f"t:{i}" for i in range(4)]
    r = align.run(S, sel)
    for g in r["grids"]:
        assert not [c for c in g["columns"] if set(c.values()) <= {"و", "ف"}], g["columns"]
    assert all(not sg["tokens"] or sg["tokens"][-1] not in ("و", "ف") for sg in r["segments"].values())
    # «ولا» is the witness's addition and its «الصوم» stands opposite the seed's «والصوم»
    g1 = next(g for g in r["grids"] if g["text"] == "و الصوم")
    assert g1["columns"] == [{"t:1": "ولا"}, {"t:1": "الصوم", "t:seed": "والصوم", "t:0": "الصوم"}]
    key = next(c["key"] for c in r["clauses"] if c["text"] == "و الصوم")
    r2 = align.run(S, sel, {"words": {key: {"t:1": {"ولا#1": "add", "الصوم#1": {"to": ["t:seed", "والصوم#1"]}}}}})
    assert next(g for g in r2["grids"] if g["text"] == "و الصوم")["columns"] == g1["columns"]
    assert not [f for f in r2["flags"] if f["kind"] == "word_unused"]


def test_an_added_word_stands_in_front_when_the_next_word_moves_into_its_column():
    # the witness has «x y» where the seed has «b»: x is the addition, y the variant of b
    cols = [{"s": "a", "w": "a"}, {"s": "b", "w": "x"}, {"w": "y"}, {"s": "c", "w": "c"}]
    flags = []
    out, cells, added = align._apply_word_edits(cols, {"w": {"x#1": "add", "y#1": {"to": ["s", "b#1"]}}}, flags, "k")
    assert out == [{"s": "a", "w": "a"}, {"w": "x"}, {"s": "b", "w": "y"}, {"s": "c", "w": "c"}] and added == [1] and cells == [[1, "w"], [2, "w"]] and not flags
    # without such a move the word still stands behind its column
    out, _, added = align._apply_word_edits(cols, {"w": {"x#1": "add"}}, [], "k")
    assert out == [{"s": "a", "w": "a"}, {"s": "b"}, {"w": "x"}, {"w": "y"}, {"s": "c", "w": "c"}] and added == [2]
    # an addition that already stands alone, behind the column its successor is due in, moves in front of that column
    out, cells, added = align._apply_word_edits([{"s": "a", "w": "a"}, {"s": "b"}, {"w": "x"}, {"w": "y"}],
                                                {"w": {"x#1": "add", "y#1": {"to": ["s", "b#1"]}}}, flags := [], "k")
    assert out == [{"s": "a", "w": "a"}, {"w": "x"}, {"s": "b", "w": "y"}] and added == [1] and not flags
    # each witness's added word stands in front of its own column
    cols = [{"s": "b", "u": "x"}, {"u": "y"}, {"s": "b2", "v": "x"}, {"v": "y"}]
    out, _, added = align._apply_word_edits(cols, {"u": {"x#1": "add", "y#1": {"to": ["s", "b#1"]}}, "v": {"x#1": "add", "y#1": {"to": ["s", "b2#1"]}}}, [], "k")
    assert out == [{"u": "x"}, {"s": "b", "u": "y"}, {"v": "x"}, {"s": "b2", "v": "y"}] and added == [0, 2]


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


def _edit_fixture():
    seed = "من كذب علي متعمدا فليتبوأ مقعده من النار"
    w = [seed, seed,
         "من كذب علي عمدا فليتبوأ مقعده من النار",
         "من كذب علي عمدا فليتبوأ مقعده من النار",
         "من كذب علي متعمدا فليتبوأ بيتا مقعده من النار",
         "من كذب علي متعمدا فليتبوأ بيتا مقعده من النار"]
    S, sel = _search(seed, w), [f"t:{i}" for i in range(len(w))]
    return S, sel, align.run(S, sel)["clauses"][0]["key"]


def test_word_marked_as_added_stands_alone_and_its_unit_is_an_addition():
    S, sel, key = _edit_fixture()
    base = align.run(S, sel)
    assert [c.get("t:2") for c in base["grids"][0]["columns"]].count("عمدا") == 1
    assert all(not u.get("cls_user") for u in base["units"])
    # the automatic guess calls «بیتا» (in 2 of 7 witnesses) an omission: the user says it is an addition
    assert next(u for u in base["units"] if u["ukey"] == "بیتا")["cls"] == "omission"
    r = align.run(S, sel, {"words": {key: {"t:2": {"عمدا#1": "add"}, "t:3": {"عمدا#1": "add"}}}, "unit_cls": {key: {"بیتا": "addition"}}})
    cols = r["grids"][0]["columns"]
    assert {"t:2": "عمدا", "t:3": "عمدا"} in cols                                  # the two added words share one column of their own
    assert all(c.get("t:2") != "عمدا" or len(c) == 2 for c in cols)
    assert [list(x) for x in r["grids"][0]["user_cells"]] == [[cols.index({"t:2": "عمدا", "t:3": "عمدا"}), "t:2"], [cols.index({"t:2": "عمدا", "t:3": "عمدا"}), "t:3"]]
    by = {u["ukey"]: u for u in r["units"] if u["kind"] == "presence"}
    assert by["عمدا"]["cls"] == "addition" and by["عمدا"]["cls_user"] and by["عمدا"]["states"]["t:2"] == 1
    assert by["متعمدا"]["states"]["t:2"] == 0 and not by["متعمدا"]["cls_user"]      # what it replaced is now missing there
    assert by["بیتا"]["cls"] == "addition" and by["بیتا"]["cls_user"]
    assert r["parts_in"]["words"] and not [f for f in r["flags"] if f["kind"] == "word_unused"]


def test_word_edit_that_no_longer_fits_is_flagged_not_applied():
    S, sel, key = _edit_fixture()
    r = align.run(S, sel, {"words": {key: {"t:2": {"ghost#1": "add"}}, "s:no such part": {"t:2": {"عمدا#1": "add"}}}})
    assert sorted(f["kind"] for f in r["flags"]) == ["word_unused", "word_unused"]
    assert r["grids"][0]["columns"] == align.run(S, sel)["grids"][0]["columns"]


def test_a_word_can_be_moved_into_the_column_of_the_word_it_varies():
    cols = [{"s": "a", "w": "a"}, {"s": "b"}, {"w": "x"}, {"s": "c", "w": "c"}]
    flags = []
    out, cells, added = align._apply_word_edits(cols, {"w": {"x#1": {"to": ["s", "b#1"]}}}, flags, "k")
    assert out == [{"s": "a", "w": "a"}, {"s": "b", "w": "x"}, {"s": "c", "w": "c"}] and cells == [[1, "w"]] and added == [] and not flags
    assert cols[2] == {"w": "x"}                                                      # the input is not changed
    # moving past a neighbour would reorder the witness's words: refused
    flags = []
    out, cells, _ = align._apply_word_edits([{"s": "a", "w": "b"}, {"s": "b", "w": "a"}], {"w": {"a#1": {"to": ["s", "a#1"]}}}, flags, "k")
    assert out == [{"s": "a", "w": "b"}, {"s": "b", "w": "a"}] and cells == [] and [f["kind"] for f in flags] == ["word_unused"]
    # «متفاوت» for a word that is already in that column: nothing moves, but the user's confirmation is shown
    flags = []
    out, cells, _ = align._apply_word_edits([{"s": "a", "w": "b"}], {"w": {"b#1": {"to": ["s", "a#1"]}}}, flags, "k")
    assert out == [{"s": "a", "w": "b"}] and cells == [[0, "w"]] and not flags
    assert align.row_keys([{"w": "a"}, {}, {"w": "a"}], "w") == [(0, "a#1"), (2, "a#2")]


# ---- editable units (parts.units) -----------------------------------------------------------------------------

def _sub_fixture():
    """Three witnesses with «مقعده», three with «بیته» in the same place, one that adds «حقا» (a private variant)."""
    seed = "من كذب علي متعمدا فليتبوأ مقعده من النار"
    w = [seed, seed, seed, *[seed.replace("مقعده", "بیته")] * 3, seed.replace("فليتبوأ", "فليتبوا حقا")]
    S, sel = _search(seed, w), [f"t:{i}" for i in range(len(w))]
    return S, sel, align.run(S, sel)


def _key_at(cols, wid, j):
    """The row key of the word a witness has in grid column j."""
    return next(k for jj, k in align.row_keys(cols, wid) if jj == j)


def _num_fixture():
    w = [SEED] * 2 + [SEED.replace("ستة", "سبعة")] * 2 + [SEED.replace("والتارك لسنتي، ", "")] * 2
    return _search(SEED, w), [f"t:{i}" for i in range(len(w))]


def test_every_unit_has_a_key_that_does_not_depend_on_column_numbers():
    S, sel, r = _sub_fixture()
    sub = next(u for u in r["units"] if u["kind"] == "lexical")
    assert sub["ukey"] == "s:" + _key_at(r["grids"][0]["columns"], "t:seed", 6) and sub["cols"] == [6, 6]
    priv = next(u for u in r["private_variants"] if u["kind"] == "presence")
    assert priv["why"] == "private" and priv["ukey"] == "حقا" and priv["labels"] == ["absent", "present"]
    S2, sel2 = _num_fixture()
    r2 = align.run(S2, sel2)
    assert sorted(u["ukey"] for u in r2["units"]) == ["n:سته#1", "والتارک لسنتی"]
    # a new witness moves nothing: the keys stay
    w = [S["seed"]["matn"]] + [c["matn"] for c in S["candidates"]]
    S3 = _search(S["seed"]["matn"], w[1:] + ["من كذب علي متعمدا فليتبوأ بيتا مقعده من النار"])
    r3 = align.run(S3, [f"t:{i}" for i in range(len(w))])
    assert sub["ukey"] in {u["ukey"] for u in r3["units"]}
    for res in (r, r2, r3):
        for part in {u["clause"] for u in res["units"] + res["private_variants"]}:
            ks = [u["ukey"] for u in res["units"] + res["private_variants"] if u["clause"] == part]
            assert len(ks) == len(set(ks))


def test_drop_keep_and_state_overrides_of_units():
    S, sel, base = _sub_fixture()
    key = base["clauses"][0]["key"]
    sub = next(u for u in base["units"] if u["kind"] == "lexical")["ukey"]
    # drop an automatic unit: it leaves the units and X, and is listed so it can be restored
    r = align.run(S, sel, {"units": {key: {sub: {"drop": True}}}})
    assert not r["units"] and r["n_units"] == 0 and all(row == [] for row in r["X"].values())
    assert [u["ukey"] for u in r["dropped_units"]] == [sub] and not r["flags"]
    # keep a unit that only one witness differs in
    r = align.run(S, sel, {"units": {key: {"حقا": {"keep": True}}}})
    kept = next(u for u in r["units"] if u["ukey"] == "حقا")
    assert kept["user_keep"] and all(u["why"] != "private" or u["ukey"] != "حقا" for u in r["private_variants"])
    assert r["X"]["t:6"][r["units"].index(kept)] == 1
    # a witness's state: unknown, an existing label, and a new label
    r = align.run(S, sel, {"units": {key: {sub: {"keep": True, "states": {"t:3": None, "t:4": "مقعده", "t:5": "حجرته"}}}}})
    u = next(u for u in r["units"] if u["ukey"] == sub)
    assert u["states"]["t:3"] == -1 and u["states"]["t:4"] == 0 and u["labels"][u["states"]["t:5"]] == "حجرته"
    assert sorted(u["states_user"]) == ["t:3", "t:4", "t:5"] and u["n_states"] == 3
    # a state a presence unit does not have, a witness and a unit that are not there: flagged, nothing applied
    r = align.run(S, sel, {"units": {key: {"حقا": {"states": {"t:0": "maybe"}}, "nothing": {"drop": True}, sub: {"states": {"x:1": None}}}}})
    assert sorted(f["kind"] for f in r["flags"]) == ["unit_unused"] * 3
    # numbers: a new number
    S2, sel2 = _num_fixture()
    k2 = align.run(S2, sel2)["clauses"][0]["key"]
    r = align.run(S2, sel2, {"units": {k2: {"n:سته#1": {"states": {"t:0": "8"}}}}})
    n = next(u for u in r["units"] if u["ukey"] == "n:سته#1")
    assert n["labels"] == ["6", "7", "8"] and n["states"]["t:0"] == 2
    # a label in the `units` decision beats unit_cls
    S, sel, base = _sub_fixture()
    r = align.run(S, sel, {"units": {key: {"حقا": {"keep": True, "cls": "omission"}}}, "unit_cls": {key: {"حقا": "addition"}}})
    assert next(u for u in r["units"] if u["ukey"] == "حقا")["cls"] == "omission"


def test_a_unit_defined_over_columns_follows_its_words():
    S, sel, base = _sub_fixture()
    key = base["clauses"][0]["key"]
    cols = base["grids"][0]["columns"]
    at = lambda w, *js: [_key_at(cols, w, j) for j in js]
    lexical = {"kind": "lexical", "text": "مقعده", "anchors": {"t:seed": at("t:seed", 6) * 2, "t:3": at("t:3", 6) * 2}}
    r = align.run(S, sel, {"units": {key: {"m:1": {"def": lexical}}}})
    m = next(u for u in r["units"] if u["ukey"] == "m:1")
    auto = next(u for u in r["units"] if u["kind"] == "lexical" and u["ukey"] != "m:1")
    assert m["by"] == "user" and m["cols"] == auto["cols"] == [6, 6] and m["states"] == auto["states"] and m["labels"] == auto["labels"]
    assert m["name"].startswith("c0u1:") and r["n_units"] == 2
    # a presence unit over the extra word: one witness has it, so it is forced in; the others have an empty place there
    pres = {"kind": "presence", "text": "حقا", "anchors": {"t:6": at("t:6", 5) * 2}}
    r = align.run(S, sel, {"units": {key: {"m:2": {"def": pres}}}})
    p = next(u for u in r["units"] if u["ukey"] == "m:2")
    assert p["kind"] == "presence" and p["states"]["t:6"] == 1 and p["states"]["t:0"] == 0 and p["cols"] == [5, 5]
    # the range is the columns the words are in now: after the user's correction moved a word, the unit follows it
    r = align.run(S, sel, {"units": {key: {"m:1": {"def": lexical, "states": {"t:0": None}}}}})
    assert next(u for u in r["units"] if u["ukey"] == "m:1")["states"]["t:0"] == -1
    # merge: one unit over two columns, the automatic one excluded
    wide = {"kind": "lexical", "text": "مقعده من", "anchors": {"t:seed": at("t:seed", 6, 7), "t:3": at("t:3", 6, 7)}}
    r = align.run(S, sel, {"units": {key: {"m:3": {"def": wide}, sub_key(base): {"drop": True}}}})
    w = next(u for u in r["units"] if u["ukey"] == "m:3")
    assert w["cols"] == [6, 7] and len(w["labels"]) == 2 and [u["ukey"] for u in r["dropped_units"]] == [sub_key(base)]
    # a unit of one's own can be switched off, and back on
    r = align.run(S, sel, {"units": {key: {"m:1": {"def": lexical, "drop": True}}}})
    assert [u["ukey"] for u in r["dropped_units"]] == ["m:1"] and "m:1" not in {u["ukey"] for u in r["units"]}
    # words that are not in the alignment: flagged, no unit
    ghost = {"kind": "presence", "text": "ghost", "anchors": {"t:seed": ["ghost#1", "ghost#1"]}}
    r = align.run(S, sel, {"units": {key: {"m:4": {"def": ghost}}}})
    assert not [u for u in r["units"] + r["private_variants"] if u["ukey"] == "m:4"]
    assert [(f["kind"], f["ref"]) for f in r["flags"]] == [("unit_unused", f"{key}|m:4")]
    # everybody agrees over the range: the unit has one state and does not reach the model
    same = {"kind": "lexical", "text": "من", "anchors": {"t:seed": at("t:seed", 0) * 2}}
    r = align.run(S, sel, {"units": {key: {"m:5": {"def": same}}}})
    assert not [u for u in r["units"] if u["ukey"] == "m:5"]
    assert next(u for u in r["private_variants"] if u["ukey"] == "m:5")["why"] == "constant"
    assert [f["kind"] for f in r["flags"]] == ["unit_constant"]


def sub_key(r):
    return next(u for u in r["units"] if u["kind"] == "lexical")["ukey"]


def test_units_the_user_keeps_come_on_top_of_the_cap(monkeypatch):
    S2, sel2 = _num_fixture()
    r = align.run(S2, sel2)
    monkeypatch.setattr(align, "MAX_UNITS", 1)
    capped = align.run(S2, sel2)
    assert capped["n_units"] == 1 and [f["kind"] for f in capped["flags"]] == ["units_capped"]
    out = next(u for u in capped["private_variants"] if u["why"] == "capped")
    kept = align.run(S2, sel2, {"units": {r["clauses"][out["clause"]]["key"]: {out["ukey"]: {"keep": True}}}})
    assert kept["n_units"] == 2 and {u["ukey"] for u in kept["units"]} == {u["ukey"] for u in r["units"]}


def test_spelling_only_columns_are_units_set_aside_that_can_be_kept():
    seed = "ليس العبادة كثرة الصلاة والصوم إنما العبادة التفكر في أمر الله"
    w = [seed] * 4 + [seed.replace("ليس", "ليست")] * 3
    S, sel = _search(seed, w), [f"t:{i}" for i in range(len(w))]
    r = align.run(S, sel)
    o = [u for u in r["private_variants"] if u["why"] == "orthographic"]
    assert len(o) == 1 and o[0]["ortho"] and o[0]["ukey"].startswith("o:") and set(o[0]["labels"]) == {"لیس", "لیست"}
    assert not [u for u in r["units"] if u.get("ortho")]                      # not in the model unless kept
    key = r["clauses"][o[0]["clause"]]["key"]
    k = align.run(S, sel, {"units": {key: {o[0]["ukey"]: {"keep": True}}}})
    u = next(u for u in k["units"] if u["ukey"] == o[0]["ukey"])
    assert u["user_keep"] and u["cls"] == "orthography" and sorted(u["states"].values()).count(1) == 3
    from hadith_phylogeny.text_model import VariantSpec, VariantModel
    VariantModel([VariantSpec(**{f: x[f] for f in ("name", "n_states", "cls", "kind", "labels")}) for x in k["units"]])


def test_a_pointer_has_its_texts_readings_or_none():
    """«مثله» reads as the text it points at; «نحوه» is close to it, its words unknown: NA everywhere, no grid row."""
    w = [SEED, SEED, SEED.replace("ستة", "سبعة"), SEED.replace("ستة", "سبعة"), SEED.replace("ستة", "سبعة")]
    S = _search(SEED, w)
    for i, kind in ((3, "same"), (4, "similar")):
        S["candidates"][i]["ref"] = dict(ref="t:2", tpl="copy", approx=kind == "similar", marker="مثله" if kind == "same" else "نحوه")
    r = align.run(S, [f"t:{i}" for i in range(len(w))])
    num = next(u for u in r["units"] if u["kind"] == "numeric")
    assert num["states"]["t:3"] == num["states"]["t:2"] != num["states"]["t:0"]
    assert all(u["states"].get("t:4", align.NA) == align.NA for u in r["units"])
    assert all("t:4" not in g["witnesses"] for g in r["grids"])
    assert next(x for x in r["witnesses"] if x["wid"] == "t:4")["ref"]["approx"]
    # the text the user writes out in step 3 counts, «نحوه» included
    r2 = align.run(S, [f"t:{i}" for i in range(len(w))], {"refs": {"t:4": {"text": SEED, "approx": False}}})
    num2 = next(u for u in r2["units"] if u["kind"] == "numeric")
    assert num2["states"]["t:4"] == num2["states"]["t:0"] and any("t:4" in g["witnesses"] for g in r2["grids"])
