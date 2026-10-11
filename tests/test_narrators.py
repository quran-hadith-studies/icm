import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from narrators.common import as_list, norm_key, ascii_digits
from narrators.arabic_numbers import parse_arabic_number, extract_death_year
from narrators.noor_extract import parse_citation, split_hint


def test_as_list_shapes():
    assert as_list(None) == []
    assert as_list("") == []
    assert as_list("x") == ["x"]
    assert as_list({"a": 1}) == [{"a": 1}]
    assert as_list([1, 2]) == [1, 2]


def test_citation_regex():
    assert parse_citation("رجال النجاشي1/16") == ("رجال النجاشي", 1, 16)
    assert parse_citation("اختیار معرفة الرجال2/345") == ("اختیار معرفة الرجال", 2, 345)
    assert parse_citation("الخلاصة للحلي٣/١٢") == ("الخلاصة للحلي", 3, 12)
    assert parse_citation("النجاشي") is None


def test_split_hint():
    assert split_hint("إبراهيم بن هاشم القمي,امامی,ثقة") == ("إبراهيم بن هاشم القمي", "امامی", "ثقة")
    assert split_hint("علي بن موسى الكميداني") == ("علي بن موسى الكميداني", None, None)


def test_norm_key_persian_equals_arabic():
    assert norm_key("علی بن ابراهیم") == norm_key("علي بن إبراهيم")
    assert ascii_digits("۱۴۱ق ٣") == "141ق 3"


def test_parse_arabic_number():
    assert parse_arabic_number("أربع و سبعين و مائتين") == 274
    assert parse_arabic_number("سنة ثمانين و مائتين") == 280
    assert parse_arabic_number("ثلاث مائة و سبع عشرة") == 317
    assert parse_arabic_number("احدى و ثلاثين و مائة") == 131
    assert parse_arabic_number("ثلاثمائة") == 300
    assert parse_arabic_number("260") == 260
    assert parse_arabic_number("خمس أو ست وأربعين ومئة") == 145
    assert parse_arabic_number("سنة ۲۶۰") == 260
    assert parse_arabic_number("لا شيء") is None


def test_extract_death_year():
    assert extract_death_year("كان ثقة، مات سنة ست وثلاثين ومائة بالكوفة.") == 136
    assert extract_death_year("سنة أربع و سبعين و مائتين", require_verb=False) == 274
    assert extract_death_year("سنة أربع و سبعين و مائتين") is None


def test_name_agree_and_heads():
    from narrators.link_shia import head_of, name_agree, Entry, weighted_lis
    assert head_of("أبو إسحاق إبراهيم بن هاشم") == ("kunya", "ابو اسحاق")
    assert head_of("الحسين بن سعيد الأهوازي") == ("ism", "حسين")
    row = {"entry_id": 1, "book": "najashi", "entry_no": 1, "vol": 0, "page_start": 1, "page_end": 1,
           "section": None, "imam": None, "heading": "أحمد بن محمد بن عيسى",
           "body": "أحمد بن محمد بن عيسى بن عبد الله الأشعري القمي، أبو جعفر، شيخ القميين، ثقة"}
    e = Entry(row, 0)
    assert name_agree("أحمد بن محمد بن عيسى الأشعري", e) >= 0.9
    assert name_agree("أحمد بن محمد بن عیسی", e) >= 0.9          # Persian letters
    assert name_agree("محمد بن أحمد بن عيسى", e) == 0.0           # father/son guard
    assert name_agree("أبو جعفر الأشعري", e) > 0.5                # kunya found in the body
    items = sorted([(1, 10, 1.0, "a"), (2, 20, 1.0, "b"), (2, 5, 1.0, "b"), (3, 30, 0.9, "c"), (3, 2, 1.0, "d")])
    chosen = {items[i] for i in weighted_lis(items)}
    assert (1, 10, 1.0, "a") in chosen and (2, 20, 1.0, "b") in chosen and (3, 30, 0.9, "c") in chosen
    assert (2, 5, 1.0, "b") not in chosen and (3, 2, 1.0, "d") not in chosen


def test_imams_in_text():
    from narrators.link_shia import imams_in_text
    assert "sadiq" in imams_in_text("روى عن أبي عبد الله عليه السلام")
    assert imams_in_text("من أصحاب أبي جعفر عليه السلام") >= {"baqir", "jawad"}
    assert "rida" in imams_in_text("لقى الرضا عليه السلام")


def test_wikishia_infobox():
    from narrators.wikishia_crawl import infobox_params, hijri_year, split_names
    wt = "{{جعبه اطلاعات راوی\n| عنوان =ابان بن تغلب\n| محل زندگی =کوفه\n| وفات =[[سال ۱۴۱ هجری قمری|سال ۱۴۱ق]]\n| روایت از =[[سلیمان بن مهران اعمش|سلیمان اعمش]] • سعید بن مسیب\n}}\nmore"
    p = infobox_params(wt, "جعبه اطلاعات راوی")
    assert p["محل زندگی"] == "کوفه" and hijri_year(p["وفات"]) == 141
    assert split_names(p["روایت از"]) == ["سلیمان اعمش", "سعید بن مسیب"]
    assert hijri_year("57 أو 54 ق.هـ (570 م)") is None
    assert hijri_year("148/765-6") == 148


def test_facts_extractor(tmp_path=None):
    import sqlite3, os, tempfile
    from narrators.common import open_db
    from narrators.places import build_city_tables, Places
    from narrators.facts import Extractor
    db = os.path.join(tempfile.mkdtemp(), "t.sqlite")
    con = open_db(db)
    build_city_tables(con)
    pl = Places(con)
    assert pl.from_nisba("الكوفي") == "الكوفة" and pl.from_nisba("كوفي") == "الكوفة" and pl.from_nisba("البجلي") is None
    assert pl.from_toponym("بغداد") == "بغداد" and pl.from_toponym("ببغداد") == "بغداد"
    ex = Extractor(con)
    ex.years_from_text(1, "كان ثقة، مات بالكوفة سنة ست وثلاثين ومائة.", "najashi", 9)
    ex.years_from_text(2, "توفي في أيام أبي عبد الله عليه السلام", "najashi", 9)
    ex.years_from_text(3, "سنة أربع و سبعين و مائتين", "noor", None, require_verb=False)
    ex.cities_from_text(4, "إبراهيم بن هاشم القمي", "أصله كوفي، انتقل إلى قم", "najashi", 9)
    kinds = {(f[0], f[1], f[2]) for f in ex.facts}
    assert (1, "death", "136") in kinds
    assert (2, "death_interval", "sadiq") in kinds
    assert (3, "death", "274") in kinds
    assert (4, "city", "قم") in kinds and (4, "city", "الكوفة") in kinds


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as e:  # noqa: BLE001
                fails += 1
                print("FAIL", name, repr(e))
    sys.exit(fails)
