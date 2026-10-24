"""Hadiths that only point at the text before them («… عن أبي عبد الله عليه السلام مثله», «فذكر نحوه»)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm import config
from icm.search import pointer_of


def test_pointer_markers():
    s = "حدثنا محمد بن يحيى عن أحمد بن محمد عن أبي عبد الله عليه السلام"
    assert pointer_of(dict(sanad=s, matn="مِثْلَهُ.")) == dict(kind="same", marker="مثله", own="مِثْلَهُ.")
    assert pointer_of(dict(sanad=s, matn="قال رسول الله صلى الله عليه وسلم نحوه"))["kind"] == "similar"
    assert pointer_of(dict(sanad=s, matn="إذا أفلس الرجل فذكر مثله."))["kind"] == "same"          # an incipit, then «he mentioned the like»
    assert pointer_of(dict(sanad=s + " بهذا الإسناد مثله، وزاد فيه:", matn="فإذا فعلت ذلك حل له"))["kind"] == "same"
    assert pointer_of(dict(sanad=s, matn="فذكر معناه"))["kind"] == "similar"
    assert pointer_of(dict(sanad=s, matn="العاقل يألف مثله")) is None                                 # «مثله» is a word of the text
    assert pointer_of(dict(sanad=s, matn="معناه أنه إذا كنت خلف إمام فأنصت")) is None                  # «its meaning is»
    assert pointer_of(dict(sanad=s, matn="ليس العبادة كثرة الصلاة والصوم")) is None


def test_a_pointer_is_found_after_its_text_and_scored_on_it():
    if not os.path.exists(config.V02_DB):
        return
    from icm import corpus, search
    assert corpus.neighbours("v02:800105455", 2) == ["v02:800105456", "v02:800105457"]
    assert corpus.neighbours("v02:800105455", 2, after=False) == ["v02:800105454", "v02:800105453"]
    seed = "v02:415300710200"                           # الشؤم في ثلاثة أشياء…, then «… الشؤم ( وذكر مثله»
    if corpus.get("v02:415300710400") is None:
        return
    assert search.referent_of("v02:415300710400") == seed
    r = search.run(seed)
    c = next(c for c in r["candidates"] if c["wid"] == "v02:415300710400")
    assert c["ref"]["wid"] == seed and c["ref"]["kind"] == "same" and c["suggested"] == "witness"
    assert "ref" in c["signals"] and c["matn"].startswith(corpus.get(seed)["matn"][:20])
