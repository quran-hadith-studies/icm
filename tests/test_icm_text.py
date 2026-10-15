import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm import config
from icm.similarity import align_clause, align_clauses, classify
from icm.text import clauses, norm, tokens

SEED = ("سِتَّةٌ لَعَنَهُمُ اَللَّهُ وَ كُلُّ نَبِيٍّ مُجَابٍ اَلزَّائِدُ فِي كِتَابِ اَللَّهِ‌ وَ اَلْمُكَذِّبُ بِقَدَرِ اَللَّهِ "
        "وَ اَلتَّارِكُ لِسُنَّتِي وَ اَلْمُسْتَحِلُّ مِنْ عِتْرَتِي مَا حَرَّمَ اَللَّهُ")


def test_norm_matches_site_normaliser():
    lib = os.path.join(config.EBAD, "lib", "hadith_text.py")
    if not os.path.exists(lib):
        print("  (site module not present, skipped)")
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location("hadith_text", lib)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for s in [SEED, "<b>إِنَّمَا</b> الأَعْمَالُ بِالنِّيَّاتِ، كُلُّ مُسْكِرٍ", "يي ك ة ى ؤ ئ", ""]:
        assert norm(s) == m.normalize(s), s


def test_norm_unifies_sitta():
    assert norm("سِتَّةٌ") == "سته" == norm("ستة")


def test_clauses_punctuated_and_unpunctuated():
    tk, sp = clauses(SEED)
    parts = [" ".join(tk[a:b]) for a, b in sp]
    assert len(parts) >= 4, parts
    assert any(p.endswith("المکذب بقدر الله") for p in parts), parts
    assert all(b - a >= 2 for a, b in sp[:-1])
    tk2, sp2 = clauses("المكذب بقدر الله، والزائد في كتاب الله، والتارك لسنتي")
    assert len(sp2) == 3


def test_clauses_cover_without_overlap():
    tk, sp = clauses(SEED)
    for (a, b), (c, d) in zip(sp, sp[1:]):
        assert b <= c


def test_clause_order_is_free():
    a, spa = clauses("المكذب بقدر الله، والزائد في كتاب الله، والتارك لسنتي")
    b = tokens("التارك لسنتي والزائد في كتاب الله والمكذب بقدر الله")
    res = align_clauses(a, spa, b)
    assert all(p["state"] == "present" for p in res["parts"])
    assert res["reordered"]
    cls, sc = classify(res, sum(y - x for x, y in spa), len(b))
    assert cls == "witness", (cls, sc)


def test_clause_states():
    a, spa = clauses("المكذب بقدر الله، والزائد في كتاب الله، والتارك لسنتي")
    b = tokens("المكذب بقدر الله وفي الدار رجل كثير الكلام")
    res = align_clauses(a, spa, b)
    states = [p["state"] for p in res["parts"]]
    assert states[0] == "present" and states[1] == "absent"


def test_unrelated_text_is_different():
    a, spa = clauses("المكذب بقدر الله، والزائد في كتاب الله، والتارك لسنتي")
    b = tokens("إنما الأعمال بالنيات وإنما لكل امرئ ما نوى فمن كانت هجرته إلى الله ورسوله")
    res = align_clauses(a, spa, b)
    cls, _ = classify(res, sum(y - x for x, y in spa), len(b))
    assert cls == "different"



def test_marks_point_at_the_raw_words():
    from icm.text import marks, token_offsets
    raw = "سِتَّةٌ لَعَنَهُمُ اَللَّهُ ، وَ اَلْمُكَذِّبُ بِقَدَرِ اَللَّهِ وَ اَلْمُسْتَأْثِرُ بِفَيْ‌ءِ"
    assert len(token_offsets(raw)) == len(tokens(raw))
    toks, spans = clauses(raw)
    lab = [-1] * len(toks)
    for k, (x, y) in enumerate(spans):
        lab[x:y] = [k] * (y - x)
    m = marks(raw, lab)
    assert [c for _, _, c in m] == list(range(len(spans)))
    assert raw[m[0][0]:m[0][1]] == "سِتَّةٌ لَعَنَهُمُ اَللَّهُ" and raw[m[-1][1] - 1] == "ءِ"[-1]
    assert all(a < b for a, b, _ in m) and all(m[i][1] <= m[i + 1][0] for i in range(len(m) - 1))


def test_union_cuts_keeps_both_boundaries():
    from icm.text import union_cuts
    tk, rules = clauses(SEED)
    # a coarse semantic cut (one part for the opening, one for all the list) and a cut the rules lack
    first = tk.index("مجاب") + 1
    coarse = [(0, first), (first, len(tk))]
    got = [" ".join(tk[a:b]) for a, b in union_cuts(tk, rules, coarse)]
    assert got[0].endswith("مجاب") and got[1].startswith("الزاید"), got
    assert set(" ".join(tk[a:b]) for a, b in rules[1:]) <= set(got), (got, rules)        # every rule boundary kept
    assert all(len(p.split()) >= 2 for p in got) and "و" not in got


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception as e:
                fails += 1; print("FAIL", name, repr(e))
    sys.exit(1 if fails else 0)
