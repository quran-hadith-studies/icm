"""Book authors that no narrator database holds become persons of the narrator wiki (source «books»); ICM links the collector to them."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm import authors
from icm.authors import key_of
from narwiki import authors as nw_authors, lookup, store


def _files(tmp_path):
    rec = lambda a, d, n, ids=(), known=(): dict(author=a, death=d, n_books=n, ids=list(ids), known=list(known), ambiguous={}, by="name+death" if ids else None, by_name=[])
    table = {key_of("الفيض الكاشاني", 1091): rec("الفيض الكاشاني", 1091, 34),
             key_of("فیض کاشانی، محمد بن شاه مرتضی", 1091): rec("فیض کاشانی، محمد بن شاه مرتضی", 1091, 9),
             key_of("ابن عبد البر", 463): rec("ابن عبد البر", 463, 47, ids=["kg:34368"]),
             key_of("خطب الإمام علي (ع)", 40): rec("خطب الإمام علي (ع)", 40, 3)}
    (tmp_path / "authors.json").write_text(json.dumps(dict(authors=table), ensure_ascii=False))
    books = {"b1": dict(title="الوافي (دار)", author="الفيض الكاشاني", death=1091), "b2": dict(title="علم اليقين", author="فیض کاشانی، محمد بن شاه مرتضی", death=1091)}
    (tmp_path / "books.json").write_text(json.dumps(books, ensure_ascii=False))
    return str(tmp_path / "authors.json"), str(tmp_path / "books.json")


def test_the_plan_makes_one_person_of_an_authors_spellings_and_leaves_out_linked_authors_and_imams(tmp_path):
    a, b = _files(tmp_path)
    pl = nw_authors.plan(a, b, with_databases=False)
    assert len(pl["persons"]) == 1 and len(pl["skipped"]) == 1
    p = pl["persons"][0]
    assert p["death"] == 1091 and p["n_books"] == 43 and p["name"] == "الفيض الكاشاني" and p["alias"] == ["محمد بن شاه مرتضی فیض کاشانی"]
    assert "الوافي" in p["note"] and "علم اليقين" in p["note"] and len(p["keys"]) == 2


def test_the_persons_are_written_once_with_the_source_books_and_ICM_links_them(tmp_path, monkeypatch):
    a, b = _files(tmp_path)
    db = str(tmp_path / "wiki.sqlite")
    monkeypatch.setattr("narwiki.config.DB", db)
    c = store.connect(db)
    ps = nw_authors.plan(a, b, with_databases=False)["persons"]
    got = nw_authors.apply(c, ps)
    assert got.pop("_made") == 1 and len(got) == 2
    tok = got[ps[0]["keys"][0]]["ids"][0]
    pid = store.by_ext(c, "wiki", tok[5:])
    assert {f["field"]: f["source"] for f in store.fields_of(c, pid)}["death_lo"] == "books"
    assert store.by_ext(c, "author", ps[0]["keys"][1]) == pid
    again = nw_authors.apply(c, ps)                                    # idempotent
    assert again.pop("_made") == 0 and again[ps[0]["keys"][0]]["ids"] == [tok]
    assert lookup.wiki_people([tok])[tok]["death"] == [1091, 1091]
    # the table ICM reads: the author the databases do not know is linked to his wiki person
    (tmp_path / "author_wiki.json").write_text(json.dumps(dict(authors=got)))
    monkeypatch.setattr(authors, "AUTHORS_JSON", a)
    monkeypatch.setattr(authors, "AUTHOR_WIKI_JSON", str(tmp_path / "author_wiki.json"))
    monkeypatch.setattr(authors, "_table", None)
    assert authors.lookup("الفيض الكاشاني", 1091)["ids"] == [tok]
    assert authors.lookup("ابن عبد البر", 463)["ids"] == ["kg:34368"] and authors.lookup("خطب الإمام علي (ع)", 40) is None
