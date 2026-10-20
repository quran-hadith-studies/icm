import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm.sanad_parse import is_anon, not_a_name, parse, strip_nonname


def names(t):
    return [e["name"] for e in parse(t)["elems"]]


def kinds(t):
    return [e["kind"] for e in parse(t)["elems"]]


def test_noor_style_qala_haddathana():
    t = "41- حَدَّثَنَا حَمْزَةُ بْنُ مُحَمَّدٍ اَلْعَلَوِيُّ‌ رَضِيَ اَللَّهُ عَنْهُ قَالَ حَدَّثَنَا أَحْمَدُ بْنُ مُحَمَّدٍ عَنْ جَعْفَرِ بْنِ مُحَمَّدٍ عَنْ أَبِيهِ عَنْ عَلِيِّ بْنِ اَلْحُسَيْنِ عَلَيْهِمَا السَّلاَمُ‌ قَالَ قَالَ رَسُولُ اَللَّهِ صَلَّى اَللَّهُ عَلَيْهِ وَ آلِهِ"
    p = parse(t)
    assert p["number"] == "41"
    assert [e["kind"] for e in p["elems"]] == ["name", "name", "name", "relative", "name"]
    assert p["elems"][0]["name"].startswith("حمزه بن محمد")
    assert p["elems"][4]["honorific"] is True        # «عليهما السلام»
    assert p["speaker"]["kind"] == "prophet"


def test_blessing_is_not_imam_honorific():
    p = parse("حدثنا محمد بن موسى بن المتوكل رضي الله عنه قال حدثنا محمد بن يحيى")
    assert p["elems"][0]["honorific"] is False and p["elems"][0]["blessing"] is True


def test_bihar_sigla():
    p = parse("4 - ل، [الخصال] ، حَمْزَةُ اَلْعَلَوِيُّ‌ عَنْ أَحْمَدَ اَلْهَمْدَانِيِّ‌ عَنْ يَحْيَى")
    assert p["quoted_work"] == "الخصال" and p["number"] == "4"
    assert len(p["elems"]) == 3


def test_sunni_tha_and_anna():
    t = "أخبرني أبو بكر محمد بن عبد الله الشافعي ثنا إسحاق بن الحسن ثنا أبو حذيفة ثنا سفيان عن الأعمش عن المنهال بن عمرو"
    assert names(t)[0].startswith("ابو بکر") and len(names(t)) == 6


def test_glued_companion():
    t = "ثنا سفيان عن المنهال بن عمرو عن سعيد بن جبير ابن عباس رضي الله عنهما"
    n = names(t)
    assert n[-1] == "ابن عباس" and n[-2].startswith("سعید بن جبیر")


def test_anonymous_and_prev():
    p = parse("عنه عن أبي القاسم عمن ذكره عن عبد المؤمن عن أبي عبد الله عليه السلام")
    assert [e["kind"] for e in p["elems"]] == ["prev", "name", "anon", "name", "name"]


def test_anon_phrases():
    for s in ("عدة من أصحابنا", "رجل من أصحابنا", "بعض أصحابنا", "من ذكره", "رجل"):
        assert is_anon(s), s
    assert not is_anon("محمد بن يحيى")


def test_gap_marker_and_relative():
    p = parse("بإسناده عن أبيه عن جده")
    assert [e["kind"] for e in p["elems"]] == ["gap", "relative", "relative"]


def test_anaphoric_prophet():
    p = parse("2 - و عنه صلّى اللّه عليه و آله :")
    assert p["speaker"]["kind"] == "prophet" and p["elems"] == []


def test_co_narrators():
    p = parse("حدثنا محمد بن يحيى و أحمد بن إدريس عن سهل")
    assert p["elems"][0]["alts"] and len(p["elems"][0]["alts"]) == 2


def test_empty():
    p = parse("")
    assert p["elems"] == [] and p["speaker"] is None



def test_strip_nonname():
    assert strip_nonname("عایشه ان رسول الله") == dict(core="عایشه", stripped=["ان", "رسول", "الله"], reasons=["prophet"])
    assert strip_nonname("سفیان الثوری باسناده")["core"] == "سفیان الثوری"
    assert strip_nonname("علی بن الحسین یحدث")["core"] == "علی بن الحسین"
    assert strip_nonname("علی بن الحسین انه")["core"] == "علی بن الحسین"
    assert strip_nonname("واخبرنا")["core"] == ""
    assert strip_nonname("محمد بن یحیی")["stripped"] == []


def test_not_a_name():
    for t in ("لما ثقل", "وقال رسول الله", "اقول", "الحافظ", "فقد", "کلهم", "به ایضا", "قلت ل"):
        assert not_a_name(t), t
    for t in ("محمد بن یحیی", "ابن عباس", "ابیه", "سلمان", "عایشه ان رسول الله"):
        assert not not_a_name(t), t


def test_parse_keeps_glued_words_as_one_name():
    # suggestions (step 4) work on these names; parse() must not change, or saved choices lose their places
    for t, n in (("عن عائشة ان رسول الله قال", "عایشه ان رسول الله"), ("عن سفيان الثوري باسناده", None),
                 ("حدثنا اقول واخبرنا الحافظ ابو بكر", "اقول واخبرنا الحافظ ابو بکر")):
        p = parse(t)
        if n:
            assert [e["name"] for e in p["elems"]] == [n], p["elems"]

if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception as e:
                fails += 1; print("FAIL", name, repr(e))
    sys.exit(1 if fails else 0)


def test_reading_to_and_hearing_from_is_a_transmission_phrase_not_part_of_the_name():
    # «قرأتُ علی أبو محمد»: the narrator is «أبو محمد»; «قرأتُ علی» (I read to him) is the wording of the transmission
    for t, term, name in (("قرأتُ علی أبو محمد", "قرات علی", "ابو محمد"), ("سمعت من أبي محمد", "سمعت من", "ابی محمد"),
                          ("كتب إلي أبو جعفر", "کتب الی", "ابو جعفر"), ("حدثنا قرئ على محمد بن يحيى", "قری علی", "محمد بن یحیی")):
        p = parse(t)
        assert [(e["term"], e["name"]) for e in p["elems"]] == [(term, name)], p["elems"]
    p = parse("قرأت على أبي عبد الله عليه السلام")                       # the Imam is found once «على» no longer leads the name
    assert [e["name"] for e in p["elems"]] == ["ابی عبد الله"] and p["speaker"]["kind"] == "imam"


def test_a_pronoun_after_the_phrase_is_no_narrator_and_ali_the_narrator_stays():
    assert names("أخبرنا أحمد قراءة عليه") == ["احمد"]                  # «قراءة عليه» used to give a second narrator «عليه»
    assert names("قال قرأت على") == []                                    # Noor slot terms: no phantom narrator «علی»
    assert names("قرأت على علي بن موسى") == ["علی بن موسی"]               # «على» then the name «علي بن موسى»
    assert names("قرأت علي بن موسى") == ["علی بن موسی"]                   # «علي بن» is a name, not a preposition
