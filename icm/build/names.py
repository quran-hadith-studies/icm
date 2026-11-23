"""names.sqlite: which person a written name is, in which company (see ``icm/names.py`` for the rules).

Sources (both give a *written* name and a person id for it):
    noor   ``narrators.sqlite`` slot rows (title as written in the isnad, RaviId), every sanad of every hadith, without the
           collector's own slot (type 4, not written in the isnad) and the Prophet (the speaker, not a chain element)
    kg     the Hadith Knowledge Graph's inline narrator mentions (``hadith_rawis`` of ``external/hadith-kg.db``, character
           offsets into ``nass``), those before the matn

Support is counted per hadith, not per sanad. Before anything is written, every rule is measured leave-book-out
(``FOLDS`` folds of books): the contexts of the held-out books are looked up in a model counted from the others, with
``icm.names.decide`` itself, so the stored precision is the one of the shipped procedure. A rule is off in the file
when it is not ``MIN_PREC`` right at any support; otherwise the smallest support that is becomes its ``need``.

    ctx(h, pid, sup, tot, np)    h = hash of (db, tier, parts); the person most often meant, his hadiths, all hadiths, people
    kgm(hid, seq, rawi_id, nid)  the written names of a KG hadith's inline mentions, in text order;  name(id, k)
    form(db, pid, nid, n)        a person's written names and in how many hadiths (at least 2): «names in isnads» on his wiki page
    meta(k, v)                   built, counts, eval (json)

    python3 -m icm build names
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import zlib
from typing import Dict, Iterator, List, Tuple

from .. import config, names

FOLDS = 3
GROUPS = 24                      # books are hashed into this many groups; a fold is a union of groups (GROUPS is a multiple of FOLDS)
MIN_FIRED = 100                  # a rule is judged only when it fired at least this often (tests lower it)
CAP = 20                         # supports above this share one bucket in the evaluation
_PAD = 0


class Names:
    """Written names as small integers ('^' and '$' are 0 and 1)."""

    def __init__(self):
        self.id: Dict[str, int] = {}
        self.s: List[str] = []
        self.of("^")
        self.of("$")

    def of(self, k: str) -> int:
        i = self.id.get(k)
        if i is None:
            i = self.id[k] = len(self.s)
            self.s.append(k)
        return i


def _fold(book) -> int:
    """The group of books a hadith's book is in; its fold is ``group % FOLDS``."""
    return zlib.crc32(str(book).encode()) % GROUPS


def load_noor(nm: Names, log) -> list:
    """[(fold, [(name ids, person ids) per sanad])] per hadith."""
    prophet = json.load(open(os.path.join(config.HERE, "narrators", "data", "imam_reigns.json"), encoding="utf-8"))["imams"]["prophet"]["ravi_id"]
    c = sqlite3.connect(f"file:{config.NARRATORS_DB}?mode=ro&immutable=1", uri=True)
    book = dict(c.execute("select hadith_id, book from hadith"))
    out, cur_h, chains, ids, rids, cur_s = [], None, [], [], [], None

    def end_chain():
        if ids:
            chains.append((tuple(ids), tuple(rids)))
        ids.clear()
        rids.clear()

    def end_hadith():
        end_chain()
        if chains:
            out.append((_fold(book.get(cur_h)), list(chains)))
        chains.clear()
    for hid, sidx, title, rid, typ in c.execute("select hadith_id, sanad_idx, title, ravi_id, type from slot order by hadith_id, sanad_idx, pos"):
        if hid != cur_h:
            if cur_h is not None:
                end_hadith()
            cur_h, cur_s = hid, None
        if sidx != cur_s:
            end_chain()
            cur_s = sidx
        if typ == 4 or rid == prophet:
            continue
        k = names.written_key(title or "")
        if not k:
            continue
        ids.append(nm.of(k))
        rids.append(rid)
    if cur_h is not None:
        end_hadith()
    c.close()
    log(f"  noor: {len(out)} hadiths, {sum(len(i) for _, ch in out for i, _ in ch)} written names")
    return out


def load_kg(nm: Names, log, kgm: list) -> list:
    c = sqlite3.connect(f"file:{config.KG_SRC}?mode=ro", uri=True)
    out, cur_h, ids, rids, fold, seq = [], None, [], [], 0, 0

    def end():
        if ids:
            out.append((fold, [(tuple(ids), tuple(rids))]))
        ids.clear()
        rids.clear()
    q = ("select h.id, h.book_id, h.nass, h.matn_start, r.rawi_id, r.start, r.end from hadiths h "
         "join hadith_rawis r on r.hadith_id=h.id order by h.id, r.seq")
    for hid, book, nass, ms, rid, a, b in c.execute(q):
        if hid != cur_h:
            end()
            cur_h, fold, seq = hid, _fold(book), 0
        if ms is not None and a >= ms:
            continue
        k = names.written_key(nass[a:b])
        if not k:
            continue
        i = nm.of(k)
        ids.append(i)
        rids.append(rid)
        if rid:
            kgm.append((hid, seq, rid, i))
        seq += 1
    end()
    c.close()
    log(f"  kg: {len(out)} hadiths, {sum(len(i) for _, ch in out for i, _ in ch)} written names")
    return out


def contexts(ids, rids, i) -> list:
    """The keys (tier, a, b, c) a name of a chain is counted under."""
    k = ids[i]
    p = ids[i - 1] if i else 0
    n = ids[i + 1] if i + 1 < len(ids) else 1
    out = [("ctx3", k, p, n), ("prev", k, p, _PAD), ("next", k, n, _PAD), ("name", k, _PAD, _PAD)]
    if i and rids[i - 1]:
        out.append(("pprev", k, rids[i - 1], _PAD))
    if i + 1 < len(ids) and rids[i + 1]:
        out.append(("pnext", k, rids[i + 1], _PAD))
    return out


def count(data: list, skip=None) -> dict:
    """key -> {person: hadiths}; the held-out fold ``skip`` is left out. ``("namem", name)`` = the names one person dominates in
    every group of books (see ``majors``)."""
    D: dict = {}
    for fold, chains in data:
        if skip is not None and fold % FOLDS == skip:
            continue
        seen = set()
        for ids, rids in chains:
            for i, rid in enumerate(rids):
                if rid:
                    for key in contexts(ids, rids, i):
                        seen.add((key, rid))
        for key, rid in seen:
            d = D.get(key)
            if d is None:
                D[key] = {rid: 1}
            else:
                d[rid] = d.get(rid, 0) + 1
    majors(data, D, skip)
    return D


def majors(data: list, D: dict, skip=None) -> None:
    """Add ``("namem", k, 0, 0)`` for the names one person has (nearly) all of the hadiths of, although some other person has a few
    (noise in the databases, or a name two people share rarely): the person must also dominate in every group of books that has the
    name often enough. «الشيخ» is Mufid in one author's books and Tusi in another's: not consistent, so not decided by the name alone."""
    cand = {}
    for key, d in D.items():
        if key[0] == "name" and len(d) > 1:
            pid, sup, tot, npl = summary(d)
            if sup / tot >= names.MAJOR[0] and tot >= names.MAJOR[1]:
                cand[key[1]] = pid
    if not cand:
        return
    G: dict = {}
    for fold, chains in data:
        if skip is not None and fold % FOLDS == skip:
            continue
        seen = set()
        for ids, rids in chains:
            for i, rid in enumerate(rids):
                if rid and ids[i] in cand:
                    seen.add((ids[i], rid))
        for k, rid in seen:
            g = G.setdefault(k, {}).setdefault(fold, [0, 0])
            g[0] += 1
            g[1] += rid == cand[k]
    for k, pid in cand.items():
        grp = [v for v in G.get(k, {}).values() if v[0] >= names.GROUP_MIN]
        if len(grp) >= 2 and all(v[1] / v[0] >= names.GROUP_SHARE for v in grp):
            D[("namem", k, _PAD, _PAD)] = D[("name", k, _PAD, _PAD)]


def summary(d: dict) -> Tuple[int, int, int, int]:
    pid, sup = max(d.items(), key=lambda x: x[1])
    return pid, sup, sum(d.values()), len(d)


def getter(D: dict, nm: Names):
    """``get(tier, parts)`` of ``names.decide`` over an in-memory model."""
    ids = nm.id

    def get(tier, parts):
        if tier in ("pprev", "pnext"):
            a = ids.get(parts[0])
            key = (tier, a, parts[1], _PAD)
        else:
            a = tuple(ids.get(x) for x in parts)
            if None in a:
                return None
            key = (tier,) + a + (_PAD,) * (3 - len(a))
        if key[1] is None:
            return None
        d = D.get(key)
        return summary(d) if d else None
    return get


def evaluate(data: list, nm: Names, log, db: str) -> dict:
    """Leave-book-out: per rule, support -> [fired, right]; ``unfired`` = the names no rule decided."""
    hist: Dict[str, Dict[int, list]] = {}
    total = unfired = 0
    for f in range(FOLDS):
        t0 = time.time()
        D = count(data, skip=f)
        get = getter(D, nm)
        for fold, chains in data:
            if fold % FOLDS != f:
                continue
            for ids, rids in chains:
                for i, rid in enumerate(rids):
                    if not rid:
                        continue
                    total += 1
                    k = nm.s[ids[i]]
                    p = nm.s[ids[i - 1]] if i else "^"
                    n = nm.s[ids[i + 1]] if i + 1 < len(ids) else "$"
                    got, _ = names.decide(get, k, p, n, rids[i - 1] if i else None, rids[i + 1] if i + 1 < len(ids) else None, major=True)
                    if not got:
                        unfired += 1
                        continue
                    h = hist.setdefault(got["rule"], {}).setdefault(min(got["n"], CAP), [0, 0])
                    h[0] += 1
                    h[1] += got["id"] == rid
        log(f"  {db} fold {f}: {time.time() - t0:.0f}s")
        del D
    return dict(total=total, unfired=unfired, hist=hist)


def settle(ev: dict) -> dict:
    """From the histograms: precision per rule, the support each tier needs to reach MIN_PREC, the rules that never do."""
    rules, need, off = {}, {}, []
    for rule, h in sorted(ev["hist"].items()):
        fired = sum(v[0] for v in h.values())
        right = sum(v[1] for v in h.values())
        rules[rule] = dict(fired=fired, right=right, precision=round(right / max(fired, 1), 4))
        base = names.NEED.get(rule)
        if base is None:                                  # a *_major rule: all or nothing
            if right / max(fired, 1) < names.MIN_PREC:
                off.append(rule)
            continue
        ok = None
        for s in range(base, CAP + 1):
            f = sum(v[0] for k, v in h.items() if k >= s)
            r = sum(v[1] for k, v in h.items() if k >= s)
            if f >= MIN_FIRED and r / f >= names.MIN_PREC:
                ok = s
                break
        if ok is None:
            off.append(rule)
        else:
            need[rule] = ok
            rules[rule]["need"] = ok
            f = sum(v[0] for k, v in h.items() if k >= ok)
            rules[rule]["fired_at_need"] = f
            rules[rule]["precision_at_need"] = round(sum(v[1] for k, v in h.items() if k >= ok) / f, 4)
    return dict(total=ev["total"], decided=round(1 - ev["unfired"] / max(ev["total"], 1), 4), rules=rules, need=need, off=off)


def write(dst: str, D_by_db: dict, nms: Names, kgm: list, meta: dict, log) -> None:
    tmp = dst + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    c = sqlite3.connect(tmp)
    c.executescript("""
        create table ctx(h integer primary key, pid integer, sup integer, tot integer, np integer) without rowid;
        create table kgm(hid integer, seq integer, rawi_id integer, nid integer, primary key (hid, seq)) without rowid;
        create table name(id integer primary key, k text);
        create table form(db text, pid integer, nid integer, n integer, primary key (db, pid, nid)) without rowid;
        create index form_nid on form(nid, db);
        create table meta(k text primary key, v text);
    """)
    used = {r[3] for r in kgm}
    for db, D in D_by_db.items():
        frows = [(db, pid, key[1], n) for key, d in D.items() if key[0] == "name" for pid, n in d.items() if n >= 2]
        c.executemany("insert or replace into form values (?,?,?,?)", frows)         # the forms a person's name is written in (the wiki shows them)
        used |= {r[2] for r in frows}
        log(f"  {db}: {len(frows)} forms of persons")
        rows = []
        n = 0
        for key, d in D.items():
            tier, a, b, cc = key
            pid, sup, tot, npl = summary(d)
            if tier in ("pprev", "pnext"):
                if npl != 1 or sup < names.NEED[tier]:
                    continue
                parts = (nms.s[a], b)
            else:
                if tot < 2:
                    continue
                parts = {"ctx3": (nms.s[a], nms.s[b], nms.s[cc]), "prev": (nms.s[a], nms.s[b]), "next": (nms.s[a], nms.s[b]), "name": (nms.s[a],), "namem": (nms.s[a],)}[tier]
            rows.append((names.hkey(db, tier, *parts), pid, sup, tot, npl))
            if len(rows) >= 200000:
                c.executemany("insert or replace into ctx values (?,?,?,?,?)", rows)
                n += len(rows)
                rows = []
        c.executemany("insert or replace into ctx values (?,?,?,?,?)", rows)
        n += len(rows)
        log(f"  {db}: {n} contexts written")
    c.executemany("insert into kgm values (?,?,?,?)", kgm)
    c.executemany("insert into name values (?,?)", [(i, nms.s[i]) for i in sorted(used)])
    c.execute("create index name_k on name(k)")
    c.executemany("insert into meta values (?,?)", [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()])
    c.commit()
    c.execute("vacuum")
    c.close()
    os.replace(tmp, dst)


def run(dst: str = None, log=print) -> None:
    dst = dst or config.NAMES_DB
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    t0 = time.time()
    nms = Names()
    kgm: list = []
    data = {"noor": load_noor(nms, log), "kg": load_kg(nms, log, kgm)}
    log(f"loaded {len(nms.s)} distinct written names ({time.time() - t0:.0f}s)")
    ev, D_all = {}, {}
    for db, d in data.items():
        ev[db] = settle(evaluate(d, nms, log, db))
        log(f"{db}: {json.dumps({k: v for k, v in ev[db].items() if k != 'rules'}, ensure_ascii=False)}")
        for r, v in ev[db]["rules"].items():
            log(f"    {r:12s} fired {v['fired']:8d} precision {100 * v['precision']:6.2f}%  need {v.get('need', '-')}")
        D_all[db] = count(d)
    meta = dict(built=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), eval=ev,
                counts={db: sum(len(ids) for _, ch in d for ids, _ in ch) for db, d in data.items()})
    write(dst, D_all, nms, kgm, meta, log)
    log(f"-> {dst} {os.path.getsize(dst) >> 20} MB ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    run()
