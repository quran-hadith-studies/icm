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
