"""Hadiths that refer to another text instead of quoting it («مثله», «نحوه وزاد فيه …», «مثله إلا أنه قال …»): pointers.py."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm import config, pointers
from icm.text import tokens

S = "حدثنا محمد بن يحيى عن أحمد بن محمد عن أبي عبد الله عليه السلام"
lone = lambda matn, sanad=S: pointers.lone_ref(dict(sanad=sanad, matn=matn))


def test_templates_of_a_lone_reference():
    assert lone("مِثْلَهُ.")["tpl"] == "copy"
    r = lone("قال رسول الله صلى الله عليه وسلم نحوه")
    assert r["tpl"] == "similar" and r["approx"]
    r = lone("إذا أفلس الرجل فذكر مثله.")                       # its own opening words, then «he mentioned the like»
    assert r["tpl"] == "incipit" and r["incipit"] == "إذا أفلس الرجل"
    r = lone("فإذا فعلت ذلك حل له", S + " بهذا الإسناد مثله، وزاد فيه:")
    assert r["tpl"] == "add" and r["arg"] == "فإذا فعلت ذلك حل له"
    r = lone("مثله إلا أنه قال: يرد عليهم الدعاء جماعة")
    assert r["tpl"] == "change" and r["arg"] == "يرد عليهم الدعاء جماعة"
    assert lone("مثله ولم يذكر قوله: والصوم")["tpl"] == "omit"
    assert lone("نحوه إلى قوله: في أمر الله")["tpl"] == "upto"
    assert lone("فذكر معناه")["tpl"] == "similar"
    r = lone("مِثلُه [أيْ مثلَ حديثِ: إنَّ مِنَ الشِّعرِ حِكمةً]", "")
    assert r["tpl"] == "explicit" and r["arg"] == "إنَّ مِنَ الشِّعرِ حِكمةً"
    assert lone("مثله. قال أبو عيسى هذا حديث حسن")["tpl"] == "copy"      # a comment of the compiler, not text


def test_words_that_only_look_like_formulas():
    assert lone("العاقل يألف مثله") is None                       # «مثله» is a word of the text
    assert lone("معناه أنه إذا كنت خلف إمام فأنصت") is None        # «its meaning is»
    assert lone("لك أن ترمي من أول النهار إلى آخره") is None
    assert lone("ليس العبادة كثرة الصلاة والصوم") is None
    sp = pointers.split(dict(sanad=S, matn="قال حسدت عليا أن يكون له مثله، فصرت إليه فقلت: أنت ابن علي بن أبي طالب؟ قال: أنا ابن ابنه فقلت إني أبغضك"))
    assert sp["refs"] == []                                        # running text goes on after the word


def test_a_reference_inside_a_record_and_the_text_it_stands_for():
    """The user's example: Mujahid's isnad inside the record, «مثله» + the words that differ."""
    rec = dict(sanad="", matn="قال: حدثنا الأعمش، عن مجاهد، عن طاوس، عن ابن عباس قال: مر النبي صلى الله عليه وسلم بقبرين فقال: "
               "إنهما ليعذبان، وما يعذبان في كبير، أما أحدهما فكان لا يستتر من البول، وأما الآخر فكان يمشي بالنميمة. "
               "قال ابن المثنى: وحدثنا وكيع قال: حدثنا الأعمش قال: سمعت مجاهدا مثله يستتر من بوله.")
    sp = pointers.split(rec)
    assert sp["main"].endswith("يمشي بالنميمة") and len(sp["refs"]) == 1
    r = sp["refs"][0]
    assert r["k"] == 1 and r["of"] == "host" and r["tpl"] == "change" and r["arg"] == "يستتر من بوله"
    assert r["isnad"].startswith("قال ابن المثنى") and r["isnad"].endswith("سمعت مجاهدا")
    b = pointers.build(sp["main"], r["tpl"], r["arg"])
    assert "فكان لا يستتر من بوله" in b["text"] and "من البول" not in b["text"]
    assert b["text"][b["mark"][0]:b["mark"][1]] == "يستتر من بوله"


def test_build():
    t = "ليس العبادة كثرة الصلاة والصوم إنما العبادة التفكر في أمر الله"
    assert pointers.build(t, "copy")["text"] == t
    assert pointers.build(t, "add", "والورع عن محارم الله")["text"] == t + " والورع عن محارم الله"
    assert tokens(pointers.build(t, "omit", "والصوم")["text"]) == tokens("ليس العبادة كثرة الصلاة إنما العبادة التفكر في أمر الله")
    assert pointers.build(t, "upto", "العبادة التفكر")["text"].endswith("التفكر")
    assert pointers.build(t, "incipit", "", "ليس العبادة بكثرة")["text"].startswith("ليس العبادة بكثرة")
    assert pointers.build(t, "change", "كلام لا صلة له أبدا")["note"]          # not found: the text stays, with a note


def test_references_are_found_after_their_text_and_scored_on_it():
    if not os.path.exists(config.V02_DB):
        return
    from icm import corpus, identity, search
    if corpus.get("v02:415300710400") is None:
        return
    assert corpus.neighbours("v02:800105455", 2) == ["v02:800105456", "v02:800105457"]
    assert corpus.neighbours("v02:800105455", 2, after=False) == ["v02:800105454", "v02:800105453"]
    seed = "v02:415300710200"                                       # الشؤم في ثلاثة أشياء…, then «… الشؤم ( وذكر مثله»
    r = search.run(seed)
    c = next(c for c in r["candidates"] if c["wid"] == "v02:415300710400")
    assert c["ref"]["ref"] == seed and c["ref"]["tpl"] == "incipit" and c["suggested"] == "witness" and "ref" in c["signals"]
    # a reference inside a record is a record of its own: its isnad, read like any other
    inner = corpus.get("v02:440803~1")
    assert inner and inner["host"] == "v02:440803" and "الصدوق" in tokens(inner["sanad"])
    ch = identity.chains_for(inner)
    assert ch and ch[0]["aid"].startswith("v02:440803~1#") and any("الولید" in tokens(e["name"]) for e in ch[0]["elems"])
