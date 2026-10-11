"""Stage 3 (Shia): link Noor RaviIds to biographies → ``link`` (+ ``infallible``, ``book_coverage``).

(a) Infallibles: fixed table ``data/imam_reigns.json`` (by RaviId, never by name).
(b) Cited, same-edition books (najashi, tusi_rijal; ghadairi ±2): Noor's page citation
    selects the entries on that page (±1), the name decides.
(c) Alphabetical books in a different edition (khulasa, tusi_fihrist): name candidates,
    then a weighted longest-increasing-subsequence over (Noor page, entry order) so the
    chosen entries keep the book's order; (b) links of the same narrator add name tokens.
(d) Uncited narrators (and Khoei / WikiShia for everybody): name + tabaqa + nisba +
    chain context, with the README's confidence tiers. Ambiguity stays as several ``low``
    rows; nothing is guessed.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Set, Tuple

from .common import (STOP, bare, bigrams_ibn, data_file, insert_many, load_json, name_tokens, norm_key,
                     norm_name, norm_text, open_db, table_count)
from .rijal_parse import imam_of_section

KUNYA_HEADS = ("ابو", "ام", "ابن")
BODY_TOKENS = 60


# ----------------------------------------------------------------------------- names

def head_of(name: str) -> Tuple[str, str]:
    """('ism', 'ابراهيم') or ('kunya', 'ابو اسحاق') for the first element of a name."""
    toks = norm_name(name).split()
    toks = [t for t in toks if t]
    if not toks:
        return ("ism", "")
    if toks[0] in KUNYA_HEADS and len(toks) > 1:
        return ("kunya", f"{toks[0]} {bare(toks[1])}")
    return ("ism", bare(toks[0]))


class Entry:
    __slots__ = ("id", "book", "entry_no", "vol", "page_start", "page_end", "section", "imam",
                 "heading", "head", "htoks", "btoks", "text", "text_head", "order")

    def __init__(self, row, order: int):
        self.id = row["entry_id"]
        self.book = row["book"]
        self.entry_no = row["entry_no"]
        self.vol = row["vol"]
        self.page_start = row["page_start"]
        self.page_end = row["page_end"]
        self.section = row["section"]
        self.imam = row["imam"]
        self.heading = row["heading"] or ""
        self.head = head_of(self.heading)
        self.htoks = set(name_tokens(self.heading))
        body = norm_text(row["body"] or "")
        self.text = body
        # the biography proper: the opening clauses, before quoted isnāds and discussion
        # (Khoei's entries go on to list every chain the man occurs in)
        head_raw = " ".join(re.split(r"[.،؛:]", row["body"] or "")[:5])
        self.text_head = norm_text(head_raw)
        self.btoks = set(bare(t) for t in body.split()[:BODY_TOKENS] if t not in STOP and len(t) > 1)
        self.order = order


def load_entries(con, books: List[str]) -> Tuple[Dict[int, Entry], Dict[Tuple[str, str], List[int]]]:
    entries, index = {}, defaultdict(list)
    for b in books:
        rows = con.execute("select * from entry where book = ? order by line_no", (b,)).fetchall()
        for i, r in enumerate(rows):
            e = Entry(r, i)
            entries[e.id] = e
            index[(b, e.head[1])].append(e.id)
            if e.head[0] == "kunya":                   # also reachable through the ism after the kunya
                toks = norm_name(e.heading).split()
                if len(toks) > 2:
                    index[(b, bare(toks[2]))].append(e.id)
    return entries, index


def name_agree(variant: str, e: Entry) -> float:
    """Share of the Noor name's tokens found in the entry (heading ∪ first body tokens),
    with the father/son guard and the «ابن X» bigram check. 0 = rejected."""
    nt = [t for t in name_tokens(variant) if t]
    if not nt:
        return 0.0
    vh = head_of(variant)
    pool = e.htoks | e.btoks
    if vh[0] == "ism" and e.head[0] == "ism":
        if vh[1] != e.head[1]:
            return 0.0
    elif vh[0] == "kunya":
        if vh[1] not in norm_name(e.heading) and vh[1] not in e.text:
            return 0.0
    elif e.head[0] == "kunya":                         # entry listed under a kunya, Noor gives the ism
        if vh[1] not in pool:
            return 0.0
    hit = sum(1 for t in nt if t in pool)
    cov = hit / len(nt)
    # patronymic guard: «جعفر بن الحسين» is not «جعفر بن الحسن» however many later tokens
    # agree; the father's name must be in the heading itself (not somewhere in the body)
    vt = norm_name(variant).split()
    if vh[0] == "ism" and e.head[0] == "ism" and len(vt) >= 3 and vt[1] in ("بن", "ابن"):
        father = bare(vt[2])
        et = norm_name(e.heading).split()
        e_father = bare(et[2]) if len(et) >= 3 and et[1] in ("بن", "ابن") else None
        if e_father is not None and e_father != father:
            return 0.0
        if e_father is None and father not in e.htoks:
            cov *= 0.5
    ibn = bigrams_ibn(variant)
    if ibn and not all(b in e.text or b in norm_name(e.heading) for b in ibn):
        cov *= 0.6
    # tokens found in the heading count a little more than tokens found in the body
    cov += 0.05 * sum(1 for t in nt if t in e.htoks) / len(nt)
    return min(cov, 1.0)


# ----------------------------------------------------------------------------- Noor side

class Ravi:
    __slots__ = ("id", "name", "variants", "imams", "nisbas", "n_slots", "grade", "mazhab")

    def __init__(self, row, variants: List[str], imams: Set[str], nisbas: Set[str]):
        self.id = row["ravi_id"]
        self.name = row["name"] or ""
        self.variants = [v for v in dict.fromkeys([self.name] + variants) if v]
        self.imams = imams
        self.nisbas = nisbas
        self.n_slots = row["n_slots"]
        self.grade = row["grade"]
        self.mazhab = row["mazhab"]


def _imam_table():
    d = load_json(data_file("imam_reigns.json"))["imams"]
    noor = [(k, [norm_key(a) for a in v.get("noor_aliases", [])]) for k, v in d.items()]
    return d, noor


# Imam mentions inside a rijāl body («روى عن أبي عبد الله», «من أصحاب الرضا»); ambiguous
# kunyas map to every Imam they can mean.
BODY_IMAM_ALIASES = [
    ("sadiq", ["ابو عبد الله", "الصادق", "جعفر بن محمد"]),
    ("baqir", ["الباقر", "محمد بن علي بن الحسين"]),
    ("kazim", ["الكاظم", "موسى بن جعفر", "ابو ابراهيم", "العبد الصالح"]),
    ("rida", ["الرضا", "علي بن موسى"]),
    ("jawad", ["الجواد", "ابو جعفر الثاني", "محمد بن علي الرضا"]),
    ("hadi", ["الهادي", "ابو الحسن الثالث", "علي بن محمد"]),
    ("askari", ["العسكري", "الحسن بن علي بن محمد"]),
    ("sajjad", ["علي بن الحسين", "زين العابدين", "السجاد"]),
    ("husayn", ["الحسين بن علي"]),
    ("hasan", ["الحسن بن علي"]),
    ("ali", ["امير المومنين", "علي بن ابي طالب"]),
    ("prophet", ["النبي", "رسول الله"]),
]
_AMBIG = {"ابو جعفر": {"baqir", "jawad"}, "ابو الحسن": {"kazim", "rida", "hadi"}, "ابو محمد": {"hasan", "sajjad", "askari"}}


def imam_groups(text: str) -> List[frozenset]:
    """Imam mentions in a text, one frozenset per mention: a clear name gives a singleton,
    an ambiguous kunya («أبو الحسن») the set of Imams it can mean."""
    t = norm_key(text)
    out: List[frozenset] = []
    for key, aliases in BODY_IMAM_ALIASES:
        if any(a in t for a in aliases):
            out.append(frozenset([key]))
    for k, keys in _AMBIG.items():
        if k in t:
            # «أبو جعفر الثاني» / «أبو الحسن الثالث» are already resolved by the aliases above
            if any(k in a for a in sum((al for _, al in BODY_IMAM_ALIASES), []) if a in t):
                continue
            out.append(frozenset(keys))
    return out


def imams_in_text(text: str) -> Set[str]:
    out: Set[str] = set()
    for g in imam_groups(text):
        out |= g
    return out


def load_ravis(con) -> Dict[int, Ravi]:
    _, noor_aliases = _imam_table()
    titles = defaultdict(set)
    for r in con.execute("select ravi_id, ravi_title from citation where ravi_title is not null"):
        titles[r[0]].add(r[1])
    out = {}
    for row in con.execute("select * from ravi"):
        imams = set()
        for it in json.loads(row["tabaqa_json"] or "[]"):
            k = norm_key(it.get("text", ""))
            for key, als in noor_aliases:
                if any(a and a in k for a in als):
                    imams.add(key)
        nisbas = set()
        for it in json.loads(row["laqab_json"] or "[]"):
            for t in name_tokens(it.get("text", "")):
                if t.endswith("ي") and len(t) >= 4:
                    nisbas.add(t)
        out[row["ravi_id"]] = Ravi(row, sorted(titles.get(row["ravi_id"], ())), imams, nisbas)
    return out


# ----------------------------------------------------------------------------- (a)

def link_infallibles(con) -> None:
    imams, _ = _imam_table()
    con.execute("delete from infallible")
    con.execute("delete from link where target_kind = 'infallible'")
    rows, links = [], []
    for key, v in imams.items():
        if v.get("ravi_id") is None:
            continue
        rows.append((v["ravi_id"], v["canonical"], v.get("idx"), v.get("death"), v["reign"][0], v["reign"][1],
                     "،".join(v.get("cities", []))))
        links.append((v["ravi_id"], "infallible", key, "fixed", "high", 1.0, 1.0, 0,
                      json.dumps({"canonical": v["canonical"]}, ensure_ascii=False)))
    insert_many(con, "infallible", ["ravi_id", "canonical", "idx", "death", "reign_lo", "reign_hi", "city"], rows)
    insert_many(con, "link", ["ravi_id", "target_kind", "target_id", "method", "confidence", "score",
                              "name_score", "ctx", "detail_json"], links)
    tot = con.execute("select sum(n_slots) from ravi").fetchone()[0]
    inf = con.execute("select sum(n_slots) from ravi where ravi_id in (select ravi_id from infallible)").fetchone()[0]
    print(f"(a) infallibles: {len(rows)} ids, {inf / tot:.1%} of slots")


# ----------------------------------------------------------------------------- (b)

def _best_variant(r: Ravi, e: Entry, extra: List[str] = ()) -> Tuple[float, str]:
    best = (0.0, "")
    for v in list(r.variants) + list(extra):
        c = name_agree(v, e)
        if c > best[0]:
            best = (c, v)
    return best


def link_cited(con, ravis: Dict[int, Ravi], books: Dict[str, dict]) -> None:
    same = [k for k, b in books.items() if b.get("same_edition")]
    entries, _ = load_entries(con, same)
    by_page: Dict[Tuple[str, int], List[int]] = defaultdict(list)
    for e in entries.values():
        if e.page_start is None:
            continue
        for p in range(e.page_start, (e.page_end or e.page_start) + 1):
            by_page[(e.book, p)].append(e.id)
    con.execute("delete from link where method like 'cited%'")
    links, stats = {}, Counter()
    cites = con.execute("""select ravi_id, book, vol, page, ravi_title from citation
                           where book in (%s) group by ravi_id, book, page""" % ",".join("?" * len(same)), same).fetchall()
    for c in cites:
        r = ravis.get(c["ravi_id"])
        if not r:
            continue
        book, p = c["book"], c["page"]
        tol = 2 if books[book].get("same_edition") == "approx" else 1
        best = None
        for d in range(0, tol + 1):
            cands = set(by_page.get((book, p), [])) if d == 0 else set(by_page.get((book, p - d), [])) | set(by_page.get((book, p + d), []))
            for eid in cands:
                cov, var = _best_variant(r, entries[eid], [c["ravi_title"]] if c["ravi_title"] else [])
                if cov <= 0:
                    continue
                if d == 0:
                    conf = "high" if cov >= 0.6 else "medium" if cov >= 0.4 else "low" if cov >= 0.3 else None
                elif d == 1:
                    conf = "high" if cov >= 0.85 else "medium" if cov >= 0.6 else "low" if cov >= 0.3 else None
                else:
                    conf = "medium" if cov >= 0.85 else "low" if cov >= 0.3 else None
                if conf is None:
                    continue
                rank = ({"high": 3, "medium": 2, "low": 1}[conf], cov, -d)
                if best is None or rank > best[0]:
                    best = (rank, eid, cov, d, conf, var)
            if best and best[0][0] == 3:
                break
        key = (r.id, book)
        if best is None:
            stats[(book, "none")] += 1
            continue
        rank, eid, cov, d, conf, var = best
        prev = links.get(key)
        if prev and prev["eid"] != eid:
            if rank > prev["rank"]:
                prev.update(eid=eid, cov=cov, d=d, conf=conf, var=var, rank=rank, conflict=True)
            else:
                prev["conflict"] = True
        elif not prev:
            links[key] = dict(eid=eid, cov=cov, d=d, conf=conf, var=var, rank=rank, conflict=False, page=p)
    rows = []
    for (rid, book), l in links.items():
        stats[(book, l["conf"])] += 1
        rows.append((rid, "entry", str(l["eid"]), "cited" if l["d"] == 0 else f"cited_pm{l['d']}", l["conf"],
                     round(l["cov"], 3), round(l["cov"], 3), 0,
                     json.dumps({"book": book, "noor_page": l["page"], "page_delta": l["d"], "variant": l["var"],
                                 "conflict": l["conflict"]}, ensure_ascii=False)))
    insert_many(con, "link", ["ravi_id", "target_kind", "target_id", "method", "confidence", "score",
                              "name_score", "ctx", "detail_json"], rows)
    for book in same:
        n = {k: v for (b, k), v in stats.items() if b == book}
        tot = sum(n.values())
        hm = n.get("high", 0) + n.get("medium", 0)
        print(f"(b) {book}: {tot} cited narrators → high {n.get('high', 0)}, medium {n.get('medium', 0)}, "
              f"low {n.get('low', 0)}, none {n.get('none', 0)}  (high+medium {hm / max(1, tot):.0%})")


# ----------------------------------------------------------------------------- (c)

def _linked_headings(con) -> Dict[int, List[str]]:
    out = defaultdict(list)
    for r in con.execute("""select l.ravi_id, e.heading from link l join entry e on e.entry_id = cast(l.target_id as integer)
                            where l.target_kind = 'entry' and l.confidence in ('high', 'medium')"""):
        out[r[0]].append(r[1])
    return out


def weighted_lis(items: List[Tuple[int, int, float, int]]) -> Set[int]:
    """items: (rank, order, weight, owner) sorted by (rank, order). Returns the indices of
    the maximum-weight chain with non-decreasing rank, strictly increasing order and at
    most one item per owner (a narrator can sit at one place in the book only)."""
    n = len(items)
    best = [0.0] * n
    prev = [-1] * n
    for i in range(n):
        best[i] = items[i][2]
        for j in range(i):
            if (items[j][1] < items[i][1] and items[j][0] <= items[i][0] and items[j][3] != items[i][3]
                    and best[j] + items[i][2] > best[i]):
                best[i] = best[j] + items[i][2]
                prev[i] = j
    if not n:
        return set()
    i = max(range(n), key=lambda k: best[k])
    chosen = set()
    while i != -1:
        chosen.add(i)
        i = prev[i]
    return chosen


def link_alphabetical(con, ravis: Dict[int, Ravi], books: Dict[str, dict]) -> None:
    alpha = [k for k, b in books.items() if b.get("alphabetical") and not b.get("same_edition")
             and b.get("entry_regex") and b.get("noor_labels")]
    con.execute("delete from link where method = 'alpha'")
    extra = _linked_headings(con)
    for book in alpha:
        entries, index = load_entries(con, [book])
        # every cited page of a narrator is offered; the chain picks the one that sits where
        # the book's order says it should (the others are mentions inside other entries)
        cites = con.execute("select distinct ravi_id, vol, page from citation where book = ?", (book,)).fetchall()
        S = sorted(((c["vol"] or 0, c["page"], c["ravi_id"]) for c in cites if c["ravi_id"] in ravis))
        items, meta = [], []
        for rank, (vol, page, rid) in enumerate(S):
            r = ravis[rid]
            cand_ids = set()
            for v in r.variants + extra.get(rid, []):
                h = head_of(v)
                cand_ids |= set(index.get((book, h[1]), []))
                if h[0] == "kunya":
                    toks = norm_name(v).split()
                    if len(toks) > 2:
                        cand_ids |= set(index.get((book, bare(toks[2])), []))
            for eid in cand_ids:
                cov, var = _best_variant(r, entries[eid], extra.get(rid, []))
                if cov >= 0.5:
                    items.append((page, entries[eid].order, cov, rid))
                    meta.append((rid, eid, cov, var, page))
        order = sorted(range(len(items)), key=lambda i: (items[i][0], items[i][1]))
        items = [items[i] for i in order]
        meta = [meta[i] for i in order]
        chosen = weighted_lis(items)
        per_ravi = defaultdict(list)
        for i, (rid, eid, cov, var, page) in enumerate(meta):
            per_ravi[rid].append((i in chosen, cov, eid, var, page, items[i][1]))
        # interpolated entry position for off-chain checks
        chain_pts = sorted((items[i][0], items[i][1]) for i in chosen)
        rows, stats = [], Counter()
        for rid in {s[2] for s in S}:
            cands = per_ravi.get(rid, [])
            if not cands:
                stats["none"] += 1
                continue
            on = [c for c in cands if c[0]]
            if on:
                on.sort(key=lambda c: -c[1])
                _, cov, eid, var, page, _ = on[0]
                conf = "high" if cov >= 0.75 else "medium"
            else:
                cands.sort(key=lambda c: -c[1])
                _, cov, eid, var, page, pos = cands[0]
                lo = max([p for p in chain_pts if p[0] <= page], default=None)
                hi = min([p for p in chain_pts if p[0] >= page], default=None)
                near = lo is not None and hi is not None and lo[1] - 15 <= pos <= hi[1] + 15
                conf = "medium" if (len(cands) == 1 and cov >= 0.85 and near) else "low"
            stats[conf] += 1
            rows.append((rid, "entry", str(eid), "alpha", conf, round(cov, 3), round(cov, 3), 0,
                         json.dumps({"book": book, "noor_page": page, "variant": var, "on_chain": bool(on),
                                     "n_candidates": len(cands)}, ensure_ascii=False)))
        insert_many(con, "link", ["ravi_id", "target_kind", "target_id", "method", "confidence", "score",
                                  "name_score", "ctx", "detail_json"], rows)
        tot = sum(stats.values())
        print(f"(c) {book}: {tot} cited narrators → high {stats['high']}, medium {stats['medium']}, "
              f"low {stats['low']}, none {stats['none']}; chain length {len(chosen)}")


# ----------------------------------------------------------------------------- (d)

def link_by_name(con, ravis: Dict[int, Ravi], books: Dict[str, dict], targets: List[str],
                 min_slots: int = 1, include_linked: bool = False) -> None:
    """Name + tabaqa + nisba + chain-context linking against ``targets`` (book keys and/or
    'wikishia'). A narrator that already holds a (b)/(c) link in a target book is not
    linked again in that book (a second entry there would be a homonym), unless
    ``include_linked``."""
    present = [t for t in targets if t != "wikishia" and table_count_book(con, t)]
    entries, index = load_entries(con, present)
    wiki = {}
    if "wikishia" in targets:
        for r in con.execute("select * from wikishia"):
            # the body carries only the person's own epithets and places; teachers and
            # students are kept apart (``text``) for chain context, otherwise every name
            # token in those lists would count toward the name agreement
            fake = {"entry_id": -r["page_id"] * 10 - {"fa": 1, "ar": 2, "en": 3}[r["lang"]], "book": "wikishia",
                    "entry_no": r["page_id"], "vol": None, "page_start": None, "page_end": None,
                    "section": r["lang"], "imam": None, "heading": r["name_full"] or r["title"],
                    "body": " ".join([r["title"] or "", r["kunya"] or "", r["laqab"] or "", r["residence"] or "",
                                      r["birthplace"] or "", " ".join(json.loads(r["imams_json"] or "[]"))])}
            if r["lang"] == "en":                    # Latin names never match Arabic tokens
                continue
            e = Entry(fake, len(wiki))
            e.text = norm_text(" ".join(json.loads(r["teachers_json"] or "[]") + json.loads(r["students_json"] or "[]")))
            e.text_head = norm_text(fake["body"])
            wiki[e.id] = e
            index[("wikishia", e.head[1])].append(e.id)
        entries.update(wiki)
    infall = {r[0] for r in con.execute("select ravi_id from infallible")}
    existing = defaultdict(set)
    existing_pairs = set()
    for r in con.execute("""select l.ravi_id, e.book, e.entry_id from link l join entry e on e.entry_id = cast(l.target_id as integer)
                            where l.target_kind = 'entry' and l.method != 'name'"""):
        existing_pairs.add((r[0], r[2]))
        if r[1]:
            existing[r[0]].add(r[1])
    linked_heads: Dict[int, Set[str]] = defaultdict(set)      # ravi → name bigrams of its linked headings
    for rid, heads in _linked_headings(con).items():
        for h in heads:
            toks = norm_name(h).split()
            if len(toks) >= 2:
                linked_heads[rid].add(" ".join(toks[:2]))
            elif toks:
                linked_heads[rid].add(toks[0])
    neigh = defaultdict(set)
    for r in con.execute("select teacher, student from adjacency where n_exact >= 2"):
        if r[0] not in infall and r[1] not in infall:
            neigh[r[0]].add(r[1])
            neigh[r[1]].add(r[0])
    con.execute("delete from link where method = 'name' and target_kind in ('entry', 'wikishia')")
    rows, stats, n_none = [], Counter(), 0
    todo = sorted((r for r in ravis.values() if r.id not in infall and r.n_slots >= min_slots),
                  key=lambda r: -r.n_slots)
    for r in todo:
        cand_ids = set()
        for v in r.variants:
            h = head_of(v)
            for t in targets:
                cand_ids |= set(index.get((t, h[1]), []))
                if h[0] == "kunya":
                    toks = norm_name(v).split()
                    if len(toks) > 2:
                        cand_ids |= set(index.get((t, bare(toks[2])), []))
        scored = []
        for eid in cand_ids:
            e = entries[eid]
            if (r.id, eid) in existing_pairs:            # already linked by (b)/(c): never overwrite
                continue
            if not include_linked and e.book in existing.get(r.id, ()):
                continue
            cov, var = _best_variant(r, e)
            if cov < 0.5:
                continue
            e_imams = {e.imam} if e.imam else imams_in_text(e.text_head)
            tabaqa = bool(r.imams & e_imams)
            nisba = bool(r.nisbas & (e.htoks | e.btoks))
            ctx = 0
            for m in neigh.get(r.id, ()):
                if any(h in e.text for h in linked_heads.get(m, ())):
                    ctx += 1
            extra_tok = max(0, len(e.htoks) - len(set(name_tokens(var))))
            score = cov + 0.15 * tabaqa + 0.15 * nisba + 0.35 * min(ctx, 3) - 0.01 * extra_tok
            scored.append((score, cov, ctx, tabaqa, nisba, eid, var))
        if not scored:
            n_none += 1
            continue
        scored.sort(key=lambda s: -s[0])
        by_book = defaultdict(list)
        for s in scored:
            by_book[entries[s[5]].book].append(s)
        for book, ss in by_book.items():
            score, cov, ctx, tabaqa, nisba, eid, var = ss[0]
            unique = len(ss) == 1 or (len(ss) > 1 and ss[0][0] - ss[1][0] >= 0.25)
            if (cov >= 0.88 and ctx >= 1) or (cov >= 0.95 and unique and tabaqa):
                conf = "high"
            elif cov >= 0.72 and (ctx >= 1 or (tabaqa and nisba)):
                conf = "medium"
            elif cov >= 0.9 and unique and (tabaqa or nisba):
                conf = "medium"
            else:
                conf = "low"
            stats[(book, conf)] += 1
            emit = ss[:1] if conf != "low" else ss[:3]
            for s2 in emit:
                score, cov, ctx, tabaqa, nisba, eid, var = s2
                e = entries[eid]
                kind = "wikishia" if e.book == "wikishia" else "entry"
                tid = str(e.entry_no) + ":" + e.section if kind == "wikishia" else str(eid)
                rows.append((r.id, kind, tid, "name", conf, round(score, 3), round(cov, 3), ctx,
                             json.dumps({"book": book, "variant": var, "tabaqa": tabaqa, "nisba": nisba,
                                         "n_candidates": len(ss), "heading": e.heading}, ensure_ascii=False)))
    insert_many(con, "link", ["ravi_id", "target_kind", "target_id", "method", "confidence", "score",
                              "name_score", "ctx", "detail_json"], rows, ignore=True)
    print(f"(d) name linking: {len(todo)} narrators, no candidate {n_none}")
    for book in targets:
        n = {k: v for (b, k), v in stats.items() if b == book}
        if n:
            print(f"    {book}: high {n.get('high', 0)}, medium {n.get('medium', 0)}, low {n.get('low', 0)}")


def table_count_book(con, book: str) -> int:
    return con.execute("select count(*) from entry where book = ?", (book,)).fetchone()[0]


# ----------------------------------------------------------------------------- (t) Thaqalayn bridge

def link_thaqalayn(con, ravis: Dict[int, Ravi]) -> None:
    """Noor RaviId ↔ ThaqalaynData id. Pass 1: a normalised name that occurs exactly once on
    each side. Pass 2: name candidates (same ism, coverage ≥ 0.8) ranked by how many of the
    narrator's Noor neighbours (n_exact ≥ 2, already bridged) are adjacent to the candidate in
    Thaqalayn's chains; accepted when the best candidate has ≥ 2 such neighbours and the
    runner-up fewer than half as many."""
    tq = {r["tq_id"]: r["name_ar"] or "" for r in con.execute("select tq_id, name_ar from thaqalayn_narrator")}
    if not tq:
        return
    con.execute("delete from link where target_kind = 'thaqalayn'")
    tq_key = defaultdict(list)
    tq_ism = defaultdict(list)
    for tid, nm in tq.items():
        k = norm_key(nm)
        if k:
            tq_key[k].append(tid)
            tq_ism[head_of(nm)[1]].append(tid)
    noor_key = defaultdict(list)
    for r in ravis.values():
        noor_key[norm_key(r.name)].append(r.id)
    bridge: Dict[int, Tuple[int, str, int]] = {}
    for k, ids in noor_key.items():
        if len(ids) == 1 and len(tq_key.get(k, [])) == 1 and not re.search(r"\bابيه\b|\bابي\b$", k):
            bridge[ids[0]] = (tq_key[k][0], "exact_name", 0)
    tq_adj = set((r[0], r[1]) for r in con.execute("select teacher, student from thaqalayn_adjacency"))
    neigh = defaultdict(set)
    for r in con.execute("select teacher, student from adjacency where n_exact >= 2"):
        neigh[r[0]].add(("student", r[1]))
        neigh[r[1]].add(("teacher", r[0]))
    for _ in range(2):                                       # two rounds let new bridges help
        for r in ravis.values():
            if r.id in bridge:
                continue
            nt = name_tokens(r.name)
            if not nt:
                continue
            cands = tq_ism.get(head_of(r.name)[1], [])
            scored = []
            for tid in cands:
                tt = set(name_tokens(tq[tid]))
                cov = sum(1 for t in nt if t in tt) / len(nt)
                if cov < 0.8:
                    continue
                ctx = 0
                for role, m in neigh.get(r.id, ()):
                    b = bridge.get(m)
                    if not b:
                        continue
                    pair = (b[0], tid) if role == "teacher" else (tid, b[0])
                    ctx += pair in tq_adj
                scored.append((ctx, cov, tid))
            scored.sort(reverse=True)
            if scored and scored[0][0] >= 2 and (len(scored) == 1 or scored[1][0] * 2 < scored[0][0]):
                bridge[r.id] = (scored[0][2], "name_ctx", scored[0][0])
    rows = [(rid, "thaqalayn", str(tid), method, "high" if method == "exact_name" or ctx >= 3 else "medium",
             1.0, 1.0, ctx, json.dumps({"tq_name": tq[tid]}, ensure_ascii=False)) for rid, (tid, method, ctx) in bridge.items()]
    insert_many(con, "link", ["ravi_id", "target_kind", "target_id", "method", "confidence", "score",
                              "name_score", "ctx", "detail_json"], rows)
    print(f"(t) thaqalayn bridge: {len(rows)} narrators ({sum(1 for r in rows if r[3] == 'exact_name')} by unique name)")


# ----------------------------------------------------------------------------- driver

def run(db_path: str, steps: str = "abcdt", min_slots: int = 1) -> None:
    con = open_db(db_path)
    books = load_json(data_file("books.json"))["books"]
    ravis = load_ravis(con)
    if "a" in steps:
        link_infallibles(con)
    if "b" in steps:
        link_cited(con, ravis, books)
    if "c" in steps:
        link_alphabetical(con, ravis, books)
    if "d" in steps:
        targets = [k for k in ("khoei", "najashi", "tusi_rijal", "khulasa", "tusi_fihrist", "ghadairi") if table_count_book(con, k)]
        if table_count(con, "wikishia"):
            targets.append("wikishia")
        link_by_name(con, ravis, books, targets, min_slots=min_slots)
    if "t" in steps:
        link_thaqalayn(con, ravis)
    con.commit()
    cov = con.execute("""select count(distinct ravi_id), sum(n) from (select l.ravi_id, r.n_slots n from link l
                         join ravi r using(ravi_id) where l.confidence in ('high','medium') group by l.ravi_id)""").fetchone()
    tot = con.execute("select sum(n_slots) from ravi").fetchone()[0]
    print(f"linked high/medium: {cov[0]} narrators covering {(cov[1] or 0) / tot:.1%} of slots")
