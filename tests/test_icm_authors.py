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
