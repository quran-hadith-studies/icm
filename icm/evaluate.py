"""Evaluation of the search stage.

  recall     the substantive witnesses of the hand-built diagram (tests/fixtures/sitta_witnesses.json)
             must be found AND pre-selected (``search.PRESELECTED``)
  near-miss  hadiths that look alike but are another hadith must not be pre-selected
  oracle     for random v02 consensus sets (exact-duplicate + alignment folds), the members of a set
             found from one member: recall of the other members (reported, not asserted: sets can
             contain related-but-different texts)
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
from typing import Dict, List

from . import config, corpus, neighbours as neighbours_mod, search

FIXTURE = os.path.join(config.HERE, "tests", "fixtures", "sitta_witnesses.json")
NEAR_MISSES = ["kg:272683", "kg:268518"]      # «لا يدخل الجنة مكذب بقدر», a one-clause fragment


def sitta(params: Dict = None) -> Dict:
    fx = json.load(open(FIXTURE, encoding="utf-8"))
    r = search.run(fx["seed"], params)
    by = {c["wid"]: c for c in r["candidates"]}
    for c in r["candidates"]:
        for w in c.get("copies", []):
            by.setdefault(w, c)
    exp = [f for f in fx["witnesses"] if f["expected"]]
    found = [f for f in exp if f["best_wid"] in by]
    pre = [f for f in found if search.preselected(by[f["best_wid"]])]
    miss = {w: (by[w]["suggested"] if w in by else "not found") for w in NEAR_MISSES}
    return dict(expected=len(exp), found=len(found), preselected=len(pre),
                recall_found=len(found) / max(len(exp), 1), recall_preselected=len(pre) / max(len(exp), 1),
                near_misses=miss, near_miss_ok=all(w not in by or not search.preselected(by[w]) for w in NEAR_MISSES),
                candidates=len(r["candidates"]), seconds=r["seconds"])


def consensus_oracle(n: int = 30, seed: int = 0, params: Dict = None) -> Dict:
    c = corpus.conn("consensus")
    sets = [r[0] for r in c.execute("select cid from cset where size between 3 and 30")]
    random.Random(seed).shuffle(sets)
    recalls: List[float] = []
    for cid in sets[:n]:
        members = [r[0] for r in c.execute("select hid from membership where cid=?", (cid,))]
        wid = f"v02:{members[0]}"
        if corpus.get(wid) is None:
            continue
        r = search.run(wid, dict(rounds=1, **(params or {})))
        got = {c_["wid"] for c_ in r["candidates"]}
        for c_ in r["candidates"]:
            got.update(c_.get("copies", []))
        got.add(r["seed"]["wid"])
        got.update(r["seed"].get("copies", []))
        rest = {f"v02:{m}" for m in members[1:]}
        recalls.append(len(rest & got) / max(len(rest), 1))
    return dict(sets=len(recalls), mean_recall=sum(recalls) / max(len(recalls), 1),
                share_full=sum(1 for x in recalls if x >= 0.999) / max(len(recalls), 1))


def chains(n: int = 3000, seed: int = 0) -> Dict:
    """How well the two automatic readings of an isnad recover Noor's own linked isnad (``noor_slots``, the
    reference): hadith.ai's narrator tags (``identity.hadithai_chain``) and the rule-based parser
    (``identity.text_chain``), on random Noor hadiths that have both a linked isnad and hadith.ai tags.

      recall      share of Noor's narrators found, in order, in a reading (name-token cover ``identity._cov``
                  >= 0.5 either way). Noor's linked chain can hold steps the text does not write (al-Tusi's path
                  to al-Kulayni), which lowers both readings alike
      precision   share of a reading's names that are narrators of Noor's chain, in order
      no_chain    share of hadiths for which a reading has no narrator at all
    Noor's collector slot, the words Noor folded into a connective (``hidden``) and both readings' collector
    (the work's author, which they add) are left out."""
    from . import hadithai, identity
    from narrators.common import name_tokens
    nc = identity._nconn()
    ids = [r[0] for r in nc.execute("select distinct hadith_id from slot where ravi_id is not null")]
    nc.close()
    random.Random(seed).shuffle(ids)
    acc = {k: dict(n=0, hit=0, gold=0, got=0, used=0, empty=0) for k in ("hadithai", "text")}
    same = lambda a, b: max(identity._cov(a, b), identity._cov(b, a)) >= 0.5
    used = 0
    for nid in ids:
        if used >= n:
            break
        if not hadithai.row(nid):
            continue
        rec = corpus.get(f"v02:{config.NOOR_OFFSET + nid}")
        if rec is None:
            continue
        ref = identity.noor_chains(rec)
        h = identity.hadithai_chain(rec)
        if not ref or not h:
            continue
        used += 1
        gold = [name_tokens(e["name"]) for e in ref[0]["elems"] if e.get("role") != "collector" and not e.get("hidden")]
        for k, ch in (("hadithai", h), ("text", identity.text_chain(rec))):
            got = [name_tokens(e["name"]) for e in (ch or {}).get("elems", []) if e.get("role") != "collector" and e["kind"] != "collector"]
            a = acc[k]
            a["n"] += 1
            a["empty"] += not got
            j = hit = 0
            for g in gold:                       # in-order matching: each reading name is used once
                for jj in range(j, len(got)):
                    if same(g, got[jj]):
                        hit += 1
                        j = jj + 1
                        break
            a["hit"] += hit
            a["gold"] += len(gold)
            a["got"] += len(got)
    out = {}
    for k, a in acc.items():
        r, p = a["hit"] / max(a["gold"], 1), a["hit"] / max(a["got"], 1)
        out[k] = dict(hadiths=a["n"], recall=round(r, 3), precision=round(p, 3), f1=round(2 * r * p / max(r + p, 1e-9), 3),
                      no_chain=round(a["empty"] / max(a["n"], 1), 3))
    return out


def mentions(folds: int = 5) -> Dict:
    """Precision of the context suggestions of ``hadithai.sqlite`` (``icm.build.hadithai.suggest``), leave-book-out:
    the certain mentions (``link = 'noor_slot'`` with a RaviId) of the books of one fold are hidden, the model is
    learnt from the other books, and each hidden mention's suggestion is compared with Noor's RaviId. Books are
    split by a hash of their title, so a book and the chains it shares with others never leak into its own test."""
    import zlib
    from .build.hadithai import SUPPORT, context_model, suggest
    c = sqlite3.connect(f"file:{config.HADITHAI_DB}?mode=ro&immutable=1", uri=True)
    title = dict(c.execute("select id, title from book"))
    rows = c.execute("select h.book, m.nkey, m.pk, m.nk, m.ravi_id from mention m join hadith h on h.hid=m.hid "
                     "where m.link='noor_slot' and m.ravi_id is not null").fetchall()
    c.close()
    fold = {b: zlib.crc32((t or "").encode()) % folds for b, t in title.items()}
    acc = {t: [0, 0] for t, _ in SUPPORT}
    n = none = 0
    for f in range(folds):
        model = context_model((k, p, nx, r) for b, k, p, nx, r in rows if fold[b] != f)
        for b, k, p, nx, r in rows:
            if fold[b] != f:
                continue
            n += 1
            s = suggest(model, k, p, nx)
            if s is None:
                none += 1
            else:
                acc[s[1]][0] += s[0] == r
                acc[s[1]][1] += 1
    out = dict(mentions=n, suggested=round(1 - none / max(n, 1), 3))
    for t, (ok, tot) in acc.items():
        out[t] = dict(n=tot, precision=round(ok / max(tot, 1), 4))
    ok = sum(a[0] for a in acc.values())
    out["all"] = dict(n=n - none, precision=round(ok / max(n - none, 1), 4))
    return out


def names_twins(n: int = 2000, seed: int = 0, books: str = "rezwan%") -> Dict:
    """The automatic links (and, as ``sugg:<rule>``, the suggestions) of ``identity.auto_links`` (family, written name in company, the
    neighbour rules; NOT twins) on real text chains, against the
    persons the same hadith has in a book that carries ids: Noor's linked isnad of a v02 consensus twin, or a KG hadith with the same
    first nine words of matn, aligned with the chain's written names in order. The gold is partial (a place with no twin gets none)
    and its alignment is itself a measurement, so the figures are lower bounds of the precision a place with an id would show.

    Per rule: places with an automatic link that the gold also names / of which it agrees. ``decided``: places with any link,
    over the places of the hadiths that have a gold."""
    import collections
    from difflib import SequenceMatcher
    from . import identity, names, narrator_versions
    from .text import norm
    ver = narrator_versions.get(narrator_versions.default_name())          # a Noor id and a KG id of one person agree
    rnd = random.Random(seed)
    v = sqlite3.connect(f"file:{config.V02_DB}?mode=ro", uri=True)
    ids = [r[0] for r in v.execute("select id from hadith where book like ? and sanad is not null and length(sanad) > 15", (books,))]
    kc = sqlite3.connect(f"file:{config.KG_SRC}?mode=ro", uri=True)
    key9 = lambda t: " ".join(norm(t or "").split()[:9]) if len((t or "").split()) >= 9 else None
    kidx = collections.defaultdict(list)
    for hid, nass, ms, me in kc.execute("select id, nass, matn_start, matn_end from hadiths where matn_start is not null"):
        k = key9(nass[ms:me])
        if k:
            kidx[k].append(hid)
    stat = collections.defaultdict(lambda: [0, 0])
    places = decided = gold_places = hadiths = 0
    wrong = collections.Counter()
    for hid in rnd.sample(ids, min(n * 3, len(ids))):
        if hadiths >= n:
            break
        rec = corpus.get(f"v02:{hid}")
        if rec is None:
            continue
        chains = identity.chains_for(rec)
        if not chains:
            continue
        trad = {rec["wid"]: rec.get("tradition") or "shia"}
        identity.attach_candidates(chains, trad)
        pl, ks = identity._auto_places(chains[0])
        gold: Dict[int, set] = collections.defaultdict(set)
        for i, s in identity._twins(rec, ks).items():
            gold[i] |= s
        for t in (kidx.get(key9(rec["matn"])) or [])[:30] if key9(rec["matn"]) else []:
            kw = [(names.written_key(nass[a:b]), rid) for rid, a, b, nass, ms in kc.execute(
                "select r.rawi_id, r.start, r.end, h.nass, h.matn_start from hadith_rawis r join hadiths h on h.id=r.hadith_id where r.hadith_id=? order by r.seq", (t,))
                  if ms is None or a < ms]
            blocks = [b for b in SequenceMatcher(None, ks, [x[0] for x in kw], autojunk=False).get_matching_blocks() if b.size]
            if sum(b.size for b in blocks) >= min(2, len(ks)):
                for b in blocks:
                    for d in range(b.size):
                        if kw[b.b + d][1]:
                            gold[b.a + d].add(f"kg:{kw[b.b + d][1]}")
        if not gold:
            continue
        hadiths += 1
        identity.auto_links(chains, trad, [rec], twins=False)
        for i, e in enumerate(pl):
            if e.get("exact") or e["kind"] not in ("name", "relative"):
                continue
            places += 1
            a = e.get("auto")
            sg = e.get("sugg") if not a else None
            if a:
                decided += 1
            g = gold.get(i)
            if not g:
                continue
            gold_places += 1
            if a:
                cn = ver.canon([a["id"]] + sorted(g))
                ok = cn.get(a["id"], a["id"]) in {cn.get(x, x) for x in g}
                r = stat[a["rule"] if a["src"] not in ("family",) else "family"]
                r[0] += 1
                r[1] += ok
                if not ok:
                    wrong[f"{e['name']}: auto {identity._label(a['id'])[:30]} | gold {', '.join(identity._label(x)[:30] for x in sorted(g))}"] += 1
            elif sg:                                            # the suggestion tier, kept apart: it is not used until approved
                cn = ver.canon([sg["id"]] + sorted(g))
                r = stat["sugg:" + sg["rule"]]
                r[0] += 1
                r[1] += cn.get(sg["id"], sg["id"]) in {cn.get(x, x) for x in g}
    return dict(hadiths=hadiths, places=places, decided=round(decided / max(places, 1), 3), with_gold=gold_places,
                rules={k: dict(places=v[0], agree=v[1], precision=round(v[1] / max(v[0], 1), 4)) for k, v in sorted(stat.items())},
                wrong=wrong.most_common(25))


def neighbours(db: str = "noor", fold: int = 0) -> Dict:
    """The rules of ``icm.neighbours`` and ``name_major`` measured leave-book-out on the linked isnads of ``db`` ("noor" | "kg"): the
    books of one fold are held out, the model and the teacher/student pairs come from the others, and a place counts as right when the
    rule's person is the database's. Returns {rule: {fired, right, precision, share}}; ``total`` = places with a person.
    (``father_of_prev`` is measured on Noor's «ابيه» slots only: the KG has no such slot.)"""
    import collections
    from . import names
    from .build import names as B
    nm = B.Names()
    data = B.load_noor(nm, lambda *a: None) if db == "noor" else B.load_kg(nm, lambda *a: None, [])
    D = B.count(data, skip=fold)
    get, cfg = B.getter(D, nm), names.cfg(db)
    A: Dict = collections.Counter()                                 # (student, teacher) -> hadiths, from the other folds
    for g, chs in data:
        if g % B.FOLDS == fold:
            continue
        seen = set()
        for ids, rids in chs:
            seen |= {(rids[i], rids[i + 1]) for i in range(len(rids) - 1) if rids[i] and rids[i + 1]}
        A.update(seen)
    H = collections.defaultdict(lambda: [0, 0])
    total = 0
    for g, chs in data:
        if g % B.FOLDS != fold:
            continue
        for ids, rids in chs:
            pid = [None] * len(ids)
            for i, rid in enumerate(rids):
                if rid:
                    k, p, n = nm.s[ids[i]], (nm.s[ids[i - 1]] if i else "^"), (nm.s[ids[i + 1]] if i + 1 < len(ids) else "$")
                    got, _ = names.decide(get, k, p, n, None, None, cfg)
                    pid[i] = got["id"] if got else None
            for i, rid in enumerate(rids):
                if not rid:
                    continue
                total += 1
                if pid[i]:
                    continue
                a, b = (pid[i - 1] if i else None), (pid[i + 1] if i + 1 < len(pid) else None)
                d = D.get(("name", ids[i], 0, 0))
                m = D.get(("namem", ids[i], 0, 0))
                if m:
                    top, sup = max(m.items(), key=lambda x: x[1])
                    pr = (A[(a, top)] if a else 0) + (A[(top, b)] if b else 0)
                    H["name_major"][0] += 1
                    H["name_major"][1] += top == rid
                    if pr >= 3 and sup / sum(m.values()) >= 0.99:
                        H["name_major_adj"][0] += 1
                        H["name_major_adj"][1] += top == rid
                if d and len(d) > 1 and (a or b):
                    tot = sum(d.values())
                    sc = sorted((((A[(a, c)] if a else 0) + (A[(c, b)] if b else 0) + 1) * d[c] / tot, c, (A[(a, c)] if a else 0) + (A[(c, b)] if b else 0)) for c in d)[::-1]
                    if sc[0][2] >= 2 and sc[0][0] >= neighbours_mod.ADJ_RATIO * sc[1][0]:
                        H["adj"][0] += 1
                        H["adj"][1] += sc[0][1] == rid
                    if a and b and sc[0][2] >= 3 and all(x[2] == 0 for x in sc[1:]):
                        H["adj_one"][0] += 1
                        H["adj_one"][1] += sc[0][1] == rid
    out = {r: dict(fired=n, right=k, precision=round(k / n, 4), share=round(n / total, 4)) for r, (n, k) in H.items() if n}
    out["total"] = total
    if db == "noor":
        out["father_of_prev"] = _father_of_prev(fold)
    return out


def _father_of_prev(fold: int) -> Dict:
    """«ابيه» slots of Noor's linked isnads whose previous narrator is linked: the person with the father's first name most often next
    to the narrator (pairs from the books outside the fold), at a threefold lead (the middle level) and at any lead (the suggestion)."""
    import collections
    import sqlite3
    import zlib
    from . import names
    c = sqlite3.connect(f"file:{config.NARRATORS_DB}?mode=ro&immutable=1", uri=True)
    nm = dict(c.execute("select ravi_id, name from ravi"))
    toks = {r: tuple(names.written_key(n).split()) for r, n in nm.items()}
    book = dict(c.execute("select hadith_id, book from hadith"))
    from .build import names as B
    grp = lambda h: zlib.crc32(str(book.get(h)).encode()) % B.GROUPS % B.FOLDS
    chains: Dict = collections.defaultdict(list)
    for h, sidx, title, rid, ty in c.execute("select hadith_id, sanad_idx, title, ravi_id, type from slot order by hadith_id, sanad_idx, pos"):
        if ty != 4:
            chains[(h, sidx)].append((names.written_key(title or ""), rid, h))
    nb: Dict = collections.defaultdict(collections.Counter)         # person -> {(group, other): hadiths next to him}
    nt: Dict = collections.defaultdict(collections.Counter)         # person -> {(group, teacher): hadiths}
    for (h, _), ch in chains.items():
        for (_, r1, _), (_, r2, _) in zip(ch, ch[1:]):
            if r1 and r2:
                nb[r1][(grp(h), r2)] += 1
                nb[r2][(grp(h), r1)] += 1
                nt[r1][(grp(h), r2)] += 1
    res = {"slots": 0, "link": [0, 0], "hint": [0, 0], "dlink": [0, 0], "dhint": [0, 0]}
    for (h, _), ch in chains.items():
        if grp(h) != fold:
            continue
        for i, (k, rid, _) in enumerate(ch):
            if k not in ("ابيه", "ابوه", "ابي") or not rid or i == 0 or not ch[i - 1][1] or ch[i - 1][0] in ("ابيه", "ابوه", "ابي", "جده"):
                continue
            p = ch[i - 1][1]
            t = toks.get(p, ())
            f = next((t[j + 1] for j in range(1, len(t) - 1) if t[j] in ("بن", "ابن")), None)
            if not f:
                continue
            res["slots"] += 1
            for src, kl, kh in ((nb, "link", "hint"), (nt, "dlink", "dhint")):       # next to him on either side / as his teacher only
                sc = collections.Counter()
                for (g2, x), n in src[p].items():
                    if g2 != fold and x != p and toks.get(x, ("",))[:1] == (f,):
                        sc[x] += n
                top = sc.most_common(2)
                if not top or top[0][1] < 2:
                    continue
                n2 = top[1][1] if len(top) > 1 else 0
                if n2 == 0 or top[0][1] >= neighbours_mod.REL_RATIO * n2:
                    res[kl][0] += 1
                    res[kl][1] += top[0][0] == rid
                res[kh][0] += 1
                res[kh][1] += top[0][0] == rid
    pr = lambda k: dict(fired=res[k][0], precision=round(res[k][1] / max(res[k][0], 1), 4))
    return dict(slots=res["slots"], link=pr("link"), any_lead=pr("hint"), teacher_only_link=pr("dlink"), teacher_only_any_lead=pr("dhint"))


def hard_pairs(n: int = 60, seed: int = 0, params: dict = None, lo: float = 0.05, hi: float = 0.45) -> Dict:
    """The hard half of the oracle: pairs of one consensus set whose wording differs (a share of ``lo``..``hi`` of the
    first's 8-grams is in the second), the second searched from the first. ``params`` go to ``search.run`` (``lex``, ``sem``...).
    Reports the share of pairs found, the share found and pre-selected, and the pairs missed (for reading)."""
    from .text import grams, norm
    c = corpus.conn("consensus")
    sets = [r[0] for r in c.execute("select cid from cset where size between 2 and 12")]
    random.Random(seed).shuffle(sets)
    pairs = []
    for cid in sets:
        if len(pairs) >= n:
            break
        ms = [f"v02:{r[0]}" for r in c.execute("select hid from membership where cid=?", (cid,))]
        recs = corpus.get_many(ms)
        g = {w: grams(" ".join(norm(r["matn"]).split()), 8) for w, r in recs.items() if len(norm(r["matn"]).split()) >= 8}
        best = None
        for a in g:
            for b in g:
                if a != b and g[a]:
                    sh = len(g[a] & g[b]) / len(g[a])
                    if lo <= sh <= hi and (best is None or sh < best[0]):
                        best = (sh, a, b)
        if best:
            pairs.append(best)
    found = pre = sem_only = 0
    missed = []
    for sh, a, b in pairs:
        r = search.run(a, dict(rounds=1, **(params or {})))
        by = {x["wid"]: x for x in r["candidates"]}
        for x in r["candidates"]:
            for w in x.get("copies", []):
                by.setdefault(w, x)
        if b in by:
            found += 1
            pre += bool(search.preselected(by[b]))
            sem_only += by[b]["signals"] == ["q:sem"]
        else:
            missed.append((a, b, round(sh, 2)))
    return dict(pairs=len(pairs), found=found, preselected=pre, found_only_by_meaning=sem_only, recall=round(found / max(len(pairs), 1), 3),
                recall_preselected=round(pre / max(len(pairs), 1), 3), missed=missed[:20])
