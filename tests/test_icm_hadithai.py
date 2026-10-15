import json, os, sys, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm import config


TEXT = ("حَدَّثَنِي أَبُو اَلْقَاسِمِ جَعْفَرُ بْنُ مُحَمَّدٍ قَالَ حَدَّثَنِي أَبِي عَنْ سَعْدِ بْنِ عَبْدِ اَللَّهِ عَنْ أَبِي جَعْفَرٍ "
        "اَلْبَاقِرِ عَلَيْهِ السَّلاَمُ قَالَ: قُلْتُ لَهُ أَيُّ بِقَاعِ اَللَّهِ أَفْضَلُ فَقَالَ اَلْكُوفَةُ يَا أَبَا بَكْرٍ "
        "هِيَ اَلزَّكِيَّةُ اَلطَّاهِرَةُ فِيهَا قُبُورُ اَلنَّبِيِّينَ")


def _span(sub, after=0):
    a = TEXT.index(sub, after)
    return a, a + len(sub)


def _hub(d):
    """A hub book in the shape convert_hadithai_v2.py writes, with one Noor hadith (id 316143)."""
    cut = TEXT.index("قُلْتُ")
    isn = TEXT[:cut]
    s1, s2, s3 = _span("قُلْتُ لَهُ أَيُّ بِقَاعِ اَللَّهِ أَفْضَلُ"), _span("فَقَالَ اَلْكُوفَةُ يَا أَبَا بَكْرٍ"), _span("هِيَ اَلزَّكِيَّةُ")
    s3 = (s3[0], len(TEXT))
    rec = dict(id=316143, source="noor", text=TEXT, clean_sanad=isn, matn_body=TEXT[cut:], isnad_end=cut,
               segments_v2=[dict(start=0, end=cut, idx=0, types=["Isnad"], owner="…"),
                            dict(start=s1[0], end=s1[1], idx=1, types=["Sadr"], owner="أبو بكر الحضرمي", seg_qael="O"),
                            dict(start=s2[0], end=s2[1], idx=2, types=[], owner="امام باقر (علیه السلام)", owner_canonical="امام باقر (ع)", seg_qael="I"),
                            dict(start=s3[0], end=s3[1], idx=3, types=[], owner_canonical="امام باقر (ع)", seg_qael="I")],
               tag_spans=dict(Narrator=[list(_span("أَبُو اَلْقَاسِمِ جَعْفَرُ بْنُ مُحَمَّدٍ")), [TEXT.index("أَبِي عَنْ"), TEXT.index("أَبِي عَنْ") + len("أَبِي")],
                                        list(_span("سَعْدِ بْنِ عَبْدِ اَللَّهِ"))],
                              Exporter=[list(_span("أَبِي جَعْفَرٍ اَلْبَاقِرِ عَلَيْهِ السَّلاَمُ"))]),
               speaker="امام باقر (ع)")
    b = os.path.join(d, "hadithai-x")
    os.makedirs(b)
    json.dump(dict(slug="hadithai-x", source=dict(site="api.hadith.ai", via="api.hadith.ai")), open(os.path.join(b, "meta.json"), "w"))
    open(os.path.join(b, "hadiths.jsonl"), "w").write(json.dumps(rec, ensure_ascii=False) + "\n")
    other = os.path.join(d, "rezwan1")                     # a book not from hadith.ai: ignored
    os.makedirs(other)
    json.dump(dict(slug="rezwan1", source=dict(site="ahlolbait.com")), open(os.path.join(other, "meta.json"), "w"))
    open(os.path.join(other, "hadiths.jsonl"), "w").write(json.dumps(dict(id=5, text="x"), ensure_ascii=False) + "\n")


def test_parts_and_chain_from_hadithai():
    d = tempfile.mkdtemp(prefix="icmhai_")
    old = config.HUB_BOOKS, config.HADITHAI_DB
    try:
        _hub(d)
        config.HUB_BOOKS, config.HADITHAI_DB = d, os.path.join(d, "hadithai.sqlite")
        from icm.build import hadithai as build
        build.run(config.HADITHAI_DB)
        from icm import hadithai, identity
        from icm.text import clauses, tokens
        hadithai._local.__dict__.clear()
        assert hadithai.available()
        assert hadithai.row(5) is None                       # only api.hadith.ai books are taken
        # the Jami' al-Ahadith edition of the same Noor hadith (another spelling, an extra word)
        matn = "قُلْتُ لَهُ أَيُّ بِقَاعِ اللَّهِ أَفْضَلُ؟ فَقَالَ الْكُوفَةُ يَا أَبَا بَكْرٍ هِيَ الزَّكِيَّةُ الطَّاهِرَةُ فِيهَا قُبُورُ النَّبِيِّينَ"
        tk, _ = clauses(matn)
        for hid in (316143, config.NOOR_OFFSET + 316143):
            got = hadithai.parts(hid, tk)
            assert got, hid
            spans, owners = got
            assert [" ".join(tk[a:b]) for a, b in spans] == [
                "قلت له ای بقاع الله افضل", "فقال الکوفه یا ابا بکر", "هی الزکیه الطاهره فیها قبور النبیین"], spans
            assert owners == ["أبو بكر الحضرمي", "امام باقر (ع)", "امام باقر (ع)"]
        assert hadithai.parts(316143, tokens("نص آخر لا علاقة له بهذا الحديث أبدا ولا يشبهه في شيء")) is None   # disagreeing texts: the rules decide
        elems, speaker = hadithai.chain_elems(316143)
        assert [e["kind"] for e in elems] == ["name", "relative", "name", "name"], elems
        assert elems[1]["name"] == "ابی" and elems[3]["honorific"] and speaker["kind"] == "imam" and speaker["elem"] == 3
        rec = dict(wid="v02:800316143", corpus="v02", hid=config.NOOR_OFFSET + 316143, author="ابن بابویه، محمد بن علی", death=381, sanad="")
        ch = identity.hadithai_chain(rec)
        assert ch["source"] == "hadithai" and ch["elems"][0]["role"] == "collector" and ch["elems"][0]["name"] == "محمد بن علی ابن بابویه"
        assert ch["speaker"]["elem"] == 4 and len(ch["elems"]) == 5
    finally:
        config.HUB_BOOKS, config.HADITHAI_DB = old
        shutil.rmtree(d)


CHAIN = ["عَلِيُّ بْنُ إِبْرَاهِيمَ", "أَبِيهِ", "اِبْنِ أَبِي عُمَيْرٍ", "حَمَّادٍ", "اَلْحَلَبِيِّ"]


def _mention_hub(d):
    """Noor records 1-3 carry Noor's linked isnad (fake narrators.sqlite); 4 has none (a Bihar quotation of the
    same chain); 5 opens with a relative word; 6 is from sunnah.com."""
    import sqlite3
    b = os.path.join(d, "books", "hadithai-k")
    os.makedirs(b)
    json.dump(dict(slug="hadithai-k", book_name="الكافي", source=dict(via="api.hadith.ai")), open(os.path.join(b, "meta.json"), "w"))
    recs = [dict(id=h, source="noor", text="x", isnad_owner=CHAIN) for h in (1, 2, 3, 4)]
    recs.append(dict(id=5, source="noor", text="x", isnad_owner=["أَبِيهِ", "اِبْنِ أَبِي عُمَيْرٍ", "حَمَّادٍ"]))
    recs.append(dict(id=700000001, source="sunnah", text="x", isnad_owner=CHAIN))
    with open(os.path.join(b, "hadiths.jsonl"), "w") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    nd = os.path.join(d, "narrators.sqlite")
    c = sqlite3.connect(nd)
    c.execute("create table slot(hadith_id, sanad_idx, pos, order_no, title, type, term, ravi_id, cand_json)")
    c.execute("create table ravi(ravi_id integer primary key, name text)")
    ravis = {"الكليني": 10, "علي بن إبراهيم": 11, "أبيه": 12, "ابن أبي عمير": 13, "حماد": 14, "الحلبي": 15}
    c.executemany("insert into ravi values (?,?)", [(v, k) for k, v in ravis.items()])
    for h in (1, 2, 3):                      # Noor's slots name the collector too, which the text does not write
        for pos, t in enumerate(["الكليني", "علي بن إبراهيم", "أبيه", "ابن أبي عمير", "حماد", "الحلبي"]):
            c.execute("insert into slot values (?,?,?,?,?,?,?,?,?)", (h, 0, pos, pos, t, 0, "عن", None if (h, t) == (3, "الحلبي") else ravis[t], "[]"))
    c.commit()
    c.close()
    return os.path.join(d, "books"), nd


def test_mentions_links_and_suggestions():
    import sqlite3
    d = tempfile.mkdtemp(prefix="icmhai_m_")
    old = config.HUB_BOOKS, config.HADITHAI_DB, config.NARRATORS_DB
    try:
        config.HUB_BOOKS, config.NARRATORS_DB = _mention_hub(d)
        config.HADITHAI_DB = os.path.join(d, "hadithai.sqlite")
        from icm.build import hadithai as build
        build.run(config.HADITHAI_DB, log=lambda *a: None)
        c = sqlite3.connect(config.HADITHAI_DB)
        rows = {(h, i): (rid, link, pos, s, sr, n) for h, i, rid, link, pos, s, sr, n in
                c.execute("select hid, i, ravi_id, link, slot_pos, sugg, sugg_ravi, sugg_n from mention")}
        # certain: same record, same name, same order (Noor's extra collector slot shifts positions by one)
        assert [rows[(1, i)][:3] for i in range(5)] == [(11, "noor_slot", 1), (12, "noor_slot", 2), (13, "noor_slot", 3),
                                                         (14, "noor_slot", 4), (15, "noor_slot", 5)]
        assert rows[(3, 4)][:2] == (None, "noor_slot")                    # Noor left that slot unresolved: linked, no person
        # record 4 has no Noor isnad: suggestions only, never links; the last name has 2 certain mentions with a RaviId
        assert all(rows[(4, i)][1] is None for i in range(5))
        assert [rows[(4, i)][3:] for i in range(5)] == [("ctx3", 11, 3), ("ctx3", 12, 3), ("ctx3", 13, 3), ("ctx3", 14, 3), ("ctx3", 15, 2)]
        assert rows[(5, 0)][3] is None                                    # «أبيه» opening a chain: whose father is unknown
        assert rows[(5, 1)][3:] == ("ctx3", 13, 3)                        # ... while the name after it keeps its context
        assert all(rows[(700000001, i)][1] is None and rows[(700000001, i)][3] is None for i in range(5))   # Sunni: kept, untouched
        assert c.execute("select count(*) from speaker").fetchone()[0] == 0
        c.close()
        m = build.context_model([("a", "^", "b", 1), ("a", "^", "b", 2), ("a", "^", "c", 1)] * 3)
        assert build.suggest(m, "a", "^", "b") is None                    # two people in that context: no weaker guess either
        assert build.suggest(m, "a", "^", "c") == (1, "ctx3", 3)
        from icm import evaluate, hadithai
        hadithai._local.__dict__.clear()
        ev = evaluate.mentions(folds=2)
        assert ev["mentions"] == 14 and 0 <= ev["all"]["precision"] <= 1
        el, _ = hadithai.chain_elems(4)
        assert [e["hai"]["sugg_ravi"] for e in el] == [11, 12, 13, 14, 15]
        try:
            from narwiki import api
        except ImportError:                                               # fastapi not installed
            return
        got = api.hai_mentions([13], 0, 10)
        assert got["total"] == 2 and [x["hadith_id"] for x in got["items"]] == [4, 5] and got["items"][0]["book"] == "الكافي"
        ch = got["items"][0]["chain"]
        assert [x["me"] for x in ch] == [False, False, True, False, False] and all(x["sugg"] for x in ch) and ch[2]["label"] == "ابن أبي عمير"
        cnt = api.hadith_counts(["noor:13"])
        assert cnt["hadithai"] == 2 and cnt["hadithai_confirmed"] == 3
    finally:
        config.HUB_BOOKS, config.HADITHAI_DB, config.NARRATORS_DB = old
        from icm import hadithai
        hadithai._local.__dict__.clear()
        shutil.rmtree(d)


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
