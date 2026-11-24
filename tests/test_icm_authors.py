"""Book authors: a link to a narrator-database person only when name and death year agree with exactly one person."""
import os, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm import authors, identity
from icm.build import authors as build


def _src(*people, relaxed=False):
    """people: (id, [names], death); the word counts are those of these names only (every word is rare unless repeated 250 times)."""
    persons = [build.Person(i, ns[0], ns, d) for i, ns, d in people]
    df = Counter()
    for p in persons:
        for t in set().union(*map(set, p.variants)):
            df[t] += 1
    for t in ("محمد", "علي", "احمد", "حسن"):
        df[t] = 5000                                  # common given names
    return build.Source("t", persons, df, relaxed=relaxed)


def _ids(src, author, death):
    return [p.id for p in src.passing(authors.toks(author), death)]


def test_key_does_not_depend_on_the_order_of_a_catalogues_name():
    assert authors.key_of("مفید، محمد بن محمد", 413) == authors.key_of("محمد بن محمد مفید", 413)
    assert authors.key_of("الشيخ المفيد", 413) != authors.key_of("الشيخ المفيد", 414)


def test_a_laqab_and_the_death_year_find_the_person():
    s = _src(("kg:1", ["محمد بن إسماعيل بن إبراهيم", "البخاري"], 256))
    assert _ids(s, "البخاري", 256) == ["kg:1"]
    assert _ids(s, "البخاري", 257) == ["kg:1"]          # a year off is still the same death
    assert _ids(s, "البخاري", 260) == []                 # the name alone is not enough


def test_two_persons_who_fit_are_not_decided():
    s = _src(("kg:1", ["محمد بن إسماعيل", "البخاري"], 256), ("kg:2", ["زاهر بن خالد", "البخاري"], 256))
    assert sorted(_ids(s, "البخاري", 256)) == ["kg:1", "kg:2"]


def test_common_given_names_prove_nothing():
    s = _src(("kg:1", ["محمد بن علي"], 449))
    assert _ids(s, "کراجکی، محمد بن علی", 449) == []


def test_a_shortened_name_starts_with_the_same_first_name():
    s = _src(("noor:1", ["محمد بن علي بن جعفر العريضي"], 220))
    assert _ids(s, "عریضی، علی بن جعفر", 220) == []                # that is his grandfather
    assert _ids(s, "محمد بن علي العريضي", 220) == ["noor:1"]


def test_the_fathers_name_inside_the_authors_is_not_the_person():
    s = _src(("kg:1", ["أحمد بن محمد بن حنبل"], 241))
    assert _ids(s, "عبد الله بن أحمد بن حنبل", 241) == []
    assert _ids(s, "الإمام أحمد بن حنبل", 241) == ["kg:1"]


def test_a_nickname_at_the_end_of_the_authors_name_counts():
    s = _src(("kg:1", ["أحمد بن علي بن محمد", "ابن حجر العسقلاني"], 852))
    assert _ids(s, "أحمد بن علي بن حجر العسقلاني", 852) == ["kg:1"]


def test_titles_are_not_words():
    s = _src(("noor:1", ["محمد بن محمد بن النعمان المفيد"], 413))
    assert _ids(s, "الشيخ المفيد", 413) == ["noor:1"]


def test_the_collector_is_exact_when_the_table_is_sure_and_keeps_its_candidates_otherwise(monkeypatch):
    monkeypatch.setattr(authors, "_table", {authors.key_of("الشيخ الكليني", 329): dict(ids=["noor:5717"], by="name+death")})
    e = identity._elem(0, "الشيخ الكليني", None, kind="collector", role="collector", death=329)
    identity._collector(e, "shia")
    assert e["exact"] == "noor:5717" and e["cands"][0]["id"] == "noor:5717" and e["cands"][0]["exact"]
    other = identity._elem(0, "الشيخ الكليني", None, kind="collector", role="collector", death=330)     # not the table's year
    identity._collector(other, "shia")
    assert not other["exact"]


def test_a_candidate_whose_death_is_years_from_the_books_is_dropped(monkeypatch):
    monkeypatch.setattr(authors, "_noor", {1: {525.0}, 2: {460.0}, 3: set()})
    assert authors.contradicts("noor:1", 460) and not authors.contradicts("noor:2", 460)
    assert not authors.contradicts("noor:3", 460) and not authors.contradicts("noor:9", 460)      # no death recorded: nothing to contradict
    assert not authors.contradicts("kg:7", 460)


def _ids_forms(src, author, death):
    return [p.id for p in src.passing_any(build.forms(author), death)]


def test_a_name_in_brackets_is_another_name_of_the_author_and_a_note_is_not():
    s = _src(("kg:1", ["يوسف بن عبد الله بن محمد بن عبد البر بن عاصم", "ابن عبد البر القرطبي"], 463),
             ("kg:2", ["صالح بن أحمد"], 463))
    assert _ids_forms(s, "يوسف بن عبد الله النمري القرطبي ( ابن عبد البر )", 463) == ["kg:1"]
    assert build.forms("محمد بن إسحاق ( تحقيق صالح )") == [authors.toks("محمد بن إسحاق")]
    assert build.forms("علي (ع)") == [authors.toks("علي")]


def test_abd_allah_is_one_name_written_as_one_word_or_two():
    s = _src(("kg:1", ["يوسف بن عبد الله بن محمد بن عبد البر بن عاصم", "ابن عبد البر القرطبي"], 463))
    assert _ids_forms(s, "يوسف بن عبدالله بن عبدالبر", 463) == ["kg:1"]


def test_the_authors_table_has_a_key_that_changes_with_the_file(tmp_path, monkeypatch):
    f = tmp_path / "authors.json"
    monkeypatch.setattr(authors, "AUTHORS_JSON", str(f))
    assert authors.key() == ""
    f.write_text('{"authors": {}}')
    a = authors.key()
    f.write_text('{"authors": {"x": {}}}')
    assert a and authors.key() not in ("", a)


def test_a_person_without_a_death_year_whose_name_begins_with_the_authors_is_a_name_link():
    persons = [build.Person("noor:1", "يحيى بن الحسين بن هارون أبو طالب الحسني", ["يحيى بن الحسين بن هارون أبو طالب الحسني"], ()),
               build.Person("noor:2", "محمد بن أحمد ابن عبد الوهاب أبو بكر", ["محمد بن أحمد ابن عبد الوهاب أبو بكر"], ()),
               build.Person("noor:3", "أحمد بن الحسين بن علي بن موسى البيهقي", ["أحمد بن الحسين بن علي بن موسى البيهقي"], (458,)),
               build.Person("noor:4", "أحمد بن الحسين بن علي بن موسى البيهقي", ["أحمد بن الحسين بن علي بن موسى البيهقي"], (300,))]
    df = Counter()
    for p in persons:
        for t in set().union(*map(set, p.variants)):
            df[t] += 1
    src = build.Source("noor", persons, df)
    src.index_everyone(persons)

    def ids(author, death):
        return [p.id for p in src.by_name(build.forms(author), death)]
    assert ids("ابن هارون، یحیی بن حسین", 424) == ["noor:1"]
    assert ids("محمد بن عبد الوهاب", 1206) == []                    # his father's name is the grandfather's of noor:2
    assert ids("أحمد بن الحسين البيهقي", 458) == ["noor:3"]         # the one whose death year is years from the book's is left out


def test_name_link_reads_the_table(monkeypatch):
    monkeypatch.setattr(authors, "_table", {authors.key_of("ابن هارون، یحیی بن حسین", 424): dict(ids=[], by_name=["noor:20620"]),
                                            authors.key_of("فلان بن فلان", 400): dict(ids=[], by_name=["noor:1", "noor:2"])})
    assert authors.name_link("ابن هارون، یحیی بن حسین", 424) == "noor:20620"
    assert authors.name_link("فلان بن فلان", 400) is None and authors.lookup("ابن هارون، یحیی بن حسین", 424) is None


def test_twins_are_read_from_the_file_and_its_stamp_is_a_key(tmp_path, monkeypatch):
    import sqlite3
    from icm import config, twins
    f = tmp_path / "twins.sqlite"
    monkeypatch.setattr(config, "TWINS_DB", str(f))
    assert twins.key() == "" and twins.of("v02", 5) == []
    c = sqlite3.connect(f)
    c.executescript("create table twin(corp text, hid integer, db text, tid integer, primary key (corp, hid, db, tid)) without rowid;"
                    "create table meta(k text primary key, v text); insert into twin values ('v02', 5, 'noor', 77), ('v02', 5, 'kg', 9); insert into meta values ('built', 'T1');")
    c.commit()
    c.close()
    monkeypatch.setattr(twins, "_local", type(twins._local)())
    assert sorted(twins.of("v02", 5)) == [("kg", 9), ("noor", 77)] and twins.key() == "T1" and twins.of("kg", 5) == []


def test_a_name_that_begins_the_persons_with_the_same_death_year_links_when_only_one_fits_though_every_word_is_common():
    persons = [build.Person("kg:1", "أحمد بن عيسى بن زيد بن علي", ["أحمد بن عيسى بن زيد بن علي", "أحمد بن عيسى الهاشمي"], (247,)),
               build.Person("kg:2", "أحمد بن عيسى بن حسان", ["أحمد بن عيسى بن حسان"], (243,)),
               build.Person("kg:3", "أحمد بن محمد", ["أحمد بن محمد"], (247,))]
    df = Counter({t: 5000 for p in persons for v in p.variants for t in v})
    src = build.Source("kg", persons, df)
    assert [p.id for p in src.prefix_death(build.forms("احمد بن عیسی"), 247)] == ["kg:1"]          # not the one who died in 243
    assert [p.id for p in src.passing_any(build.forms("احمد بن عیسی"), 247)] == []                 # the rare-word rule cannot
    assert src.prefix_death(build.forms("عيسى"), 247) == []                                          # one word is never a prefix


def test_every_word_of_the_authors_name_in_one_persons_is_a_weaker_suggestion(monkeypatch):
    persons = [build.Person("noor:1", "أحمد بن محمد بن علي ابن قتيبة الدينوري", ["أحمد بن محمد بن علي ابن قتيبة الدينوري"], ()),
               build.Person("noor:2", "عبد الله بن مسلم بن قتيبة الدينوري", ["عبد الله بن مسلم بن قتيبة الدينوري"], (276,)),
               build.Person("noor:3", "عبد الله بن مسلم بن قتيبة", ["عبد الله بن مسلم بن قتيبة"], (330,))]
    df = Counter({t: 3 for p in persons for v in p.variants for t in v})
    src = build.Source("noor", persons, df)
    src.index_everyone(persons)
    assert [p.id for p in src.contained(build.forms("عبد الله بن مسلم الدينوري"), 276)] == ["noor:2"]      # the one who died in 330 is left out
    monkeypatch.setattr(authors, "_table", {authors.key_of("ابن قتيبة الدينوري", 276): dict(ids=[], by_words=["noor:2"])})
    assert authors.words_link("ابن قتيبة الدينوري", 276) == "noor:2" and authors.name_link("ابن قتيبة الدينوري", 276) is None
