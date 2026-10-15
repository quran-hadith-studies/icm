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
