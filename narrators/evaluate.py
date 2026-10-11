"""Evaluation protocol of the plan (`python3 -m narrators eval`).

Prints, with the targets from the plan:
  (b)  share of cited narrators linked high/medium per same-edition book;
  (c)  agreement between (b) and (c) links of one narrator (first two heading tokens);
  (d)  hold-out: the name linker run on the (b)-linked narrators without their citations,
       compared with the (b) answer;
  Khoei coverage of the top-1000 narrators;
  WikiShia / Thaqalayn cross-checks;
  death-year parser on Noor's وفات field;
  chronology sanity: resolved death ≥ reign start of the latest Imam the narrator met;
  city geocoding of the most frequent cities;
  Sunni links: KG death year vs Shia-source death year.
Review samples are written to external/eval_samples.json for manual checking.
"""
from __future__ import annotations

import json
import os
import random
from collections import Counter, defaultdict

from .common import EXTERNAL, data_file, load_json, name_tokens, norm_name, open_db, write_json
from .facts import resolve


def _first2(h: str) -> str:
    t = [x for x in norm_name(h or "").split() if x not in ("بن", "ابن")]
    return " ".join(t[:2])


def _same_person(h1: str, h2: str) -> bool:
    """Two headings name the same person when they share the ism and every further
    token of the shorter one is in the longer one («بريد» ~ «بريد بن معاوية»,
    «عبد الرحمان بن الحجاج» ~ «عبد الرحمن بن الحجاج»); a differing patronymic is a
    different person («معلى بن خنيس» vs «معلى بن محمد»)."""
    a = [x for x in name_tokens(h1) if x]
    b = [x for x in name_tokens(h2) if x]
    if not a or not b or a[0] != b[0]:
        return False
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return all(t in long_ for t in short[:3])


def run(db_path: str) -> None:
    con = open_db(db_path)
    random.seed(11)
    samples = {}
    report = []

    def say(s):
        report.append(s)
        print(s)

    # ---------------------------------------------------------------- (b)
    for book in ("najashi", "tusi_rijal", "ghadairi"):
        tot = con.execute("select count(distinct ravi_id) from citation where book = ?", (book,)).fetchone()[0]
        hm = con.execute("""select count(distinct l.ravi_id) from link l join entry e on e.entry_id = cast(l.target_id as integer)
                            where l.target_kind='entry' and l.method like 'cited%' and e.book = ? and l.confidence in ('high','medium')""",
                         (book,)).fetchone()[0]
        say(f"(b) {book}: {hm}/{tot} cited narrators linked high/medium = {hm / max(1, tot):.0%}  (target ≥ 85%, ghadairi ≥ 65%)")
        rows = con.execute("""select r.name, e.heading, e.page_start, l.detail_json from link l join ravi r using(ravi_id)
                              join entry e on e.entry_id = cast(l.target_id as integer)
                              where l.method like 'cited%' and e.book = ? and l.confidence = 'high'""", (book,)).fetchall()
        samples[f"b_{book}_high"] = [dict(noor=r[0], entry=r[1], page=r[2], detail=json.loads(r[3])) for r in random.sample(rows, min(50, len(rows)))]

    # ---------------------------------------------------------------- (c)
    for book in ("khulasa", "tusi_fihrist", "ibn_dawud"):
        pairs = con.execute("""select l1.ravi_id, e1.heading, e2.heading, l1.confidence from link l1
                               join entry e1 on e1.entry_id = cast(l1.target_id as integer) and e1.book = ?
                               join link l2 on l2.ravi_id = l1.ravi_id and l2.method like 'cited%' and l2.confidence in ('high','medium')
                               join entry e2 on e2.entry_id = cast(l2.target_id as integer)
                               where l1.method = 'alpha' and l1.confidence in ('high','medium')""", (book,)).fetchall()
        agree = sum(1 for p in pairs if _same_person(p[1], p[2]))
        samples[f"c_{book}_disagreements"] = [dict(khulasa_like=p[1], cited=p[2], confidence=p[3]) for p in pairs
                                              if not _same_person(p[1], p[2])][:60]
        tot = con.execute("select count(distinct ravi_id) from citation where book = ?", (book,)).fetchone()[0]
        hm = con.execute("""select count(distinct l.ravi_id) from link l join entry e on e.entry_id = cast(l.target_id as integer)
                            where l.method='alpha' and e.book = ? and l.confidence in ('high','medium')""", (book,)).fetchone()[0]
        say(f"(c) {book}: {hm}/{tot} linked high/medium = {hm / max(1, tot):.0%} (target ≥ 70%); "
            f"agreement with a (b) link on the same narrator {agree}/{len(pairs)} = {agree / max(1, len(pairs)):.0%} (target ≥ 95%)")

    # ---------------------------------------------------------------- (d) hold-out
    from .link_shia import load_entries, load_ravis, link_by_name
    answer = {}
    for r in con.execute("""select l.ravi_id, e.book, e.entry_id from link l join entry e on e.entry_id = cast(l.target_id as integer)
                            where l.method like 'cited%' and l.confidence = 'high' and e.book in ('najashi','tusi_rijal')"""):
        answer[(r[0], r[1])] = r[2]
    books = load_json(data_file("books.json"))["books"]
    ravis = load_ravis(con)
    # hide: run the name linker in a scratch copy of the link table
    con.execute("create temp table link_backup as select * from link")
    con.execute("delete from link where method = 'name'")
    con.execute("delete from link where method like 'cited%'")      # the answer must not feed ctx
    link_by_name(con, ravis, books, ["najashi", "tusi_rijal"], min_slots=1)
    got = defaultdict(list)
    for r in con.execute("""select l.ravi_id, e.book, e.entry_id, l.confidence from link l join entry e on e.entry_id = cast(l.target_id as integer)
                            where l.method = 'name' and e.book in ('najashi','tusi_rijal')"""):
        got[(r[0], r[1])].append((r[3], r[2]))
    con.execute("delete from link")
    con.execute("insert into link select * from link_backup")
    con.execute("drop table link_backup")
    con.commit()
    heads = {r[0]: r[1] for r in con.execute("select entry_id, heading from entry where book in ('najashi','tusi_rijal')")}
    names = {r[0]: r[1] for r in con.execute("select ravi_id, name from ravi")}
    samples["d_holdout_false_positives"] = []
    for tier in (("high",), ("high", "medium")):
        tp = fp = 0
        for key, eid in answer.items():
            g = [x for x in got.get(key, []) if x[0] in tier]
            if not g:
                continue
            # Rijāl al-Ṭūsī lists one man under every Imam he met: an entry with the same
            # heading as the answer is the same person
            if any(x[1] == eid or heads.get(x[1]) == heads.get(eid) for x in g[:1]):
                tp += 1
            else:
                fp += 1
                if len(tier) == 1:
                    samples["d_holdout_false_positives"].append(dict(noor=names.get(key[0]), book=key[1],
                                                                     chosen=heads.get(g[0][1]), answer=heads.get(eid)))
        prec = tp / max(1, tp + fp)
        rec = tp / max(1, len(answer))
        say(f"(d) hold-out {'+'.join(tier)}: precision {prec:.0%} (target ≥ {'90' if len(tier) == 1 else '80'}%), "
            f"recall {rec:.0%} (target ≥ 50% for high) on {len(answer)} answers")

    # ---------------------------------------------------------------- Khoei
    n_khoei = con.execute("select count(*) from entry where book='khoei'").fetchone()[0]
    if n_khoei:
        top = [r[0] for r in con.execute("""select ravi_id from ravi where ravi_id not in (select ravi_id from infallible)
                                            order by n_slots desc limit 1000""")]
        linked = con.execute("""select count(distinct l.ravi_id) from link l join entry e on e.entry_id = cast(l.target_id as integer)
                                where e.book='khoei' and l.confidence in ('high','medium') and l.ravi_id in (%s)""" % ",".join("?" * len(top)), top).fetchone()[0]
        say(f"Khoei: {linked}/1000 top narrators linked high/medium = {linked / 10:.0f}% (target ≥ 90%); {n_khoei} entries parsed")
        rows = con.execute("""select r.name, e.heading, l.confidence, l.detail_json from link l join ravi r using(ravi_id)
                              join entry e on e.entry_id = cast(l.target_id as integer) where e.book='khoei' and l.confidence='high'""").fetchall()
        samples["khoei_high"] = [dict(noor=r[0], entry=r[1], detail=json.loads(r[3])) for r in random.sample(rows, min(50, len(rows)))]
    else:
        say("Khoei: not parsed yet")

    # ---------------------------------------------------------------- WikiShia
    rows = con.execute("""select f1.ravi_id, f1.lo wiki, f2.lo prim from fact f1 join fact f2 on f1.ravi_id = f2.ravi_id
                          where f1.kind='death' and f1.source='wikishia' and f2.kind='death' and f2.source in ('najashi','tusi_rijal','khulasa','tusi_fihrist','noor','khoei')""").fetchall()
    if rows:
        ok = sum(1 for r in rows if abs(r[1] - r[2]) <= 3)
        say(f"WikiShia: death year agrees (±3) with a primary source in {ok}/{len(rows)} = {ok / len(rows):.0%} (target ≥ 90%)")
    else:
        say("WikiShia: no overlapping death years yet")

    # ---------------------------------------------------------------- Thaqalayn
    n_tq = con.execute("select count(*) from thaqalayn_adjacency").fetchone()[0]
    if n_tq:
        bridge = {r[0]: int(r[1]) for r in con.execute("select ravi_id, target_id from link where target_kind='thaqalayn' and confidence in ('high','medium')")}
        tq = {(r[0], r[1]) for r in con.execute("select teacher, student from thaqalayn_adjacency")}
        tot = hit = 0
        for r in con.execute("select teacher, student from adjacency where n_exact >= 3"):
            if r[0] in bridge and r[1] in bridge:
                tot += 1
                hit += (bridge[r[0]], bridge[r[1]]) in tq
        say(f"Thaqalayn: {hit}/{tot} Noor pairs (n_exact ≥ 3, both ids bridged) present = {hit / max(1, tot):.0%} (target ≥ 90%)")

    # ---------------------------------------------------------------- death parser on Noor
    n_wafat = con.execute("select count(*) from ravi where wafat_json is not null").fetchone()[0]
    parsed = con.execute("select count(distinct ravi_id) from fact where source='noor' and kind in ('death','death_interval')").fetchone()[0]
    say(f"death parser: {parsed}/{n_wafat} Noor وفات fields parsed = {parsed / max(1, n_wafat):.0%} (target ≥ 95%)")
    un = con.execute("""select name, wafat_json from ravi where wafat_json is not null and ravi_id not in
                        (select ravi_id from fact where source='noor' and kind in ('death','death_interval'))""").fetchall()
    samples["noor_wafat_unparsed"] = [dict(name=r[0], wafat=json.loads(r[1])) for r in un]

    # ---------------------------------------------------------------- chronology sanity
    res = resolve(con)
    # a narrator who met Imam X died after X's accession; an ambiguous mention
    # («أبو الحسن» = Kāẓim | Riḍā | Hādī) only requires the earliest of them
    # «من لم يرو عن واحد من الأئمة» is a Tusi section, not an Imam: no lower bound from it
    acc = defaultdict(float)
    for r in con.execute("""select ravi_id, max(lo) from fact where kind = 'imam' and pattern in ('tabaqa', 'section')
                            and value != 'lam_yarwi' group by ravi_id"""):
        acc[r[0]] = r[1]
    viol, tot = [], 0
    for rid, x in res.items():
        if not x["death"] or x["death_source"] in ("imam",) or rid not in acc:
            continue
        if x["death_source"] in ("noor", "najashi", "tusi_rijal", "khulasa", "tusi_fihrist", "khoei", "wikishia", "fixed"):
            tot += 1
            if x["death"][1] < acc[rid]:
                viol.append((rid, x["death"], x["imams"], x["death_source"]))
    say(f"chronology: {len(viol)}/{tot} dated narrators die before their latest Imam's accession = {len(viol) / max(1, tot):.1%} (target < 3%)")
    samples["chronology_violations"] = [dict(ravi=v[0], name=names.get(v[0]), death=v[1], imams=v[2], source=v[3]) for v in viol]

    # ---------------------------------------------------------------- cities
    top = con.execute("select value, count(*) from fact where kind='city' group by 1 order by 2 desc limit 30").fetchall()
    geo = {r[0]: r[1] for r in con.execute("select canonical, thurayya_id from city")}
    missing = [c for c, _ in top if not geo.get(c)]
    say(f"cities: top-30 cities geocoded except {missing} (target: all; regions like الشام/خراسان have no point)")
    rows = con.execute("select r.name, f.value, f.source, f.pattern, f.evidence from fact f join ravi r using(ravi_id) where f.kind='city' and f.source != 'fixed'").fetchall()
    samples["city_facts"] = [dict(name=r[0], city=r[1], source=r[2], pattern=r[3], evidence=r[4]) for r in random.sample(rows, min(50, len(rows)))]

    # ---------------------------------------------------------------- Sunni
    rows = con.execute("""select l.ravi_id, json_extract(l.detail_json, '$.record.death') kgd from link l
                          where l.target_kind='kg' and l.confidence='high'""").fetchall()
    if rows:
        agree = tot = 0
        for rid, kgd in rows:
            x = res.get(rid)
            if not x or not x["death"] or not kgd or x["death_source"] in ("imam", "kg"):
                continue
            tot += 1
            agree += abs(float(kgd) - (x["death"][0] + x["death"][1]) / 2) <= 3
        say(f"(e) hadith-kg high links: death year agrees (±3) with the Shia sources in {agree}/{tot} = {agree / max(1, tot):.0%} (target ≥ 90%); {len(rows)} high links")
    else:
        say("(e) Sunni links: none yet")

    # ---------------------------------------------------------------- coverage summary
    tot_slots = con.execute("select sum(n_slots) from ravi").fetchone()[0]
    dated = sum(names and 0 for _ in ())  # placeholder to keep names referenced
    n_dated = 0
    w_dated = 0
    w_city = 0
    for r in con.execute("select ravi_id, n_slots from ravi"):
        x = res.get(r[0])
        if x and x["death"]:
            n_dated += 1
            w_dated += r[1]
        if x and x["cities"]:
            w_city += r[1]
    say(f"coverage: {n_dated} narrators with a death year or interval ({w_dated / tot_slots:.1%} of slots); "
        f"cities for {w_city / tot_slots:.1%} of slots")
    write_json(os.path.join(EXTERNAL, "eval_samples.json"), samples)
    write_json(os.path.join(EXTERNAL, "eval_report.json"), {"lines": report})
    print(f"review samples → {os.path.join(EXTERNAL, 'eval_samples.json')}")
