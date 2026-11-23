"""The wiki's side of written names (what ICM's step «راویان» does with ``icm/names.py``).

  person_forms     the names a person is written under in isnads, and in how many hadiths (``names.sqlite`` ``form``)
  build_patterns   over every text isnad of a book set (Rezwan by default), the people the names model reads names as, per
                   (written name, person): ``name_patterns.sqlite`` ``pattern``; and the frequent written names no database
                   knows: ``unknown``. A laptop job (``python3 -m narwiki names build``); the server only reads the file.
  list_patterns    a page of them with the reviewers' decisions (``store.name_decisions``) joined in
  publish          the decisions as ``names_wiki.sqlite`` (``approved`` name -> person: as sure as a database; ``rejected``:
                   the model's reading of the name is refused), which ICM reads (``icm.names.wiki``)

Nothing here changes a project by itself: a published decision reaches ICM at the next identify run of each project.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from . import config


def patterns_db() -> str:
    return os.environ.get("NARWIKI_NAMES_PATTERNS") or os.path.join(os.path.dirname(names_db()), "name_patterns.sqlite")


def names_db() -> str:
    from icm import config as icm_config
    return icm_config.NAMES_DB


def wiki_overlay() -> str:
    from icm import config as icm_config
    return icm_config.NAMES_WIKI_DB


def _ro(path: str) -> Optional[sqlite3.Connection]:
    if not os.path.exists(path):
        return None
    c = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def person_forms(ext_ids: List[str], limit: int = 40) -> dict:
    """The written names of a person's ids (``noor:N`` / ``kg:N``): [{k, n, db}] most often first, with the hadiths counted."""
    c = _ro(names_db())
    if c is None:
        return dict(available=False, items=[])
    try:
        out: Dict[str, dict] = {}
        for x in ext_ids:
            kind, _, i = x.partition(":")
            if kind not in ("noor", "kg") or not i.isdigit():
                continue
            try:
                rows = c.execute("select n.k, f.n from form f join name n on n.id=f.nid where f.db=? and f.pid=? order by f.n desc limit ?",
                                 (kind, int(i), limit)).fetchall()
            except sqlite3.OperationalError:
                return dict(available=False, items=[])
            for r in rows:
                if "عن" in r["k"].split() or len(r["k"].split()) > 5:               # a KG span that covers two names
                    continue
                o = out.setdefault(r["k"], dict(k=r["k"], n=0, db=[]))
                o["n"] += r["n"]
                if kind not in o["db"]:
                    o["db"].append(kind)
        items = sorted(out.values(), key=lambda o: -o["n"])[:limit]
        return dict(available=True, items=items)
    finally:
        c.close()


def looks_like_a_name(k: str) -> bool:
    """A written name that may be a person no database has: several words with a «بن» or a kunya/ibn opening, none of them isnad wording."""
    from icm import sanad_parse
    toks = k.split()
    words = sanad_parse.NON_NAME | sanad_parse.TRANSMIT | sanad_parse._PROCLITIC | sanad_parse.HONORIFIC | {"و", "ف", "ثم", "عن"}
    if not 2 <= len(toks) <= 6 or any(t in words for t in toks) or sanad_parse.not_a_name(k):
        return False
    return "بن" in toks or toks[0] in ("ابو", "ابن", "ام")


# ---------------------------------------------------------------------------
# the corpus-wide reading: patterns and unknown names
# ---------------------------------------------------------------------------
def build_patterns(dst: Optional[str] = None, books: str = "rezwan%", limit: Optional[int] = None, log=print,
                   min_places: int = 3, min_unknown: int = 20) -> dict:
    from icm import config as ic, names as nm, sanad_parse
    from icm.build.hadithai import is_relative
    dst = dst or patterns_db()
    v = sqlite3.connect(f"file:{ic.V02_DB}?mode=ro", uri=True)
    bm = json.load(open(ic.BOOKS_META, encoding="utf-8"))
    gs = {db: (nm._getter(db), nm.cfg(db)) for db in ("noor", "kg")}
    approved = nm.wiki()["approved"]
    pat: Dict[Tuple[str, str], list] = {}
    name_ids: Dict[str, Counter] = defaultdict(Counter)
    total: Counter = Counter()
    unk: Dict[str, list] = {}
    memo: Dict[tuple, tuple] = {}
    t0 = time.time()
    q = "select id, book, sanad from hadith where book like ? and sanad is not null and length(sanad) > 15"
    n_h = n_p = 0
    for hid, book, sanad in v.execute(q, (books,)):
        n_h += 1
        if limit and n_h > limit:
            break
        trad = (bm.get(book) or {}).get("tradition") or "shia"
        dbs = ("noor", "kg") if trad == "shia" else ("kg", "noor")
        els = [e for e in sanad_parse.parse(sanad)["elems"] if e["kind"] == "name" and not sanad_parse.not_a_name(e["name"])]
        ks = []
        for e in els:
            raw = e.get("raw") or e["name"]
            ks.append(nm.written_key(sanad_parse.strip_nonname(raw)["core"] or raw))
        for i, k in enumerate(ks):
            if not k:
                continue
            p, n = (ks[i - 1] if i else "^"), (ks[i + 1] if i + 1 < len(ks) else "$")
            n_p += 1
            total[k] += 1
            got = memo.get((trad, k, p, n))
            if got is None:
                got, seen_any = None, False
                for db in dbs:
                    g, cf = gs[db]
                    r, seen = nm.decide(g, k, p, n, None, None, cf)
                    seen_any = seen_any or seen
                    if r:
                        got = (f"{db}:{r['id']}", r["rule"])
                        break
                    if seen:
                        break
                got = got or (None, seen_any)
                if len(memo) < 2_000_000:
                    memo[(trad, k, p, n)] = got
            pid, rule = got
            if pid:
                a = pat.setdefault((k, pid), [0, set(), Counter(), Counter(), []])
                a[0] += 1
                if len(a[1]) < 200:
                    a[1].add(book)
                a[2][rule] += 1
                a[3][(p, n)] += 1
                if len(a[4]) < 3:
                    a[4].append(hid)
                name_ids[k][pid] += 1
            elif rule is False and not is_relative(k) and k not in approved:       # no database knows this name in any company
                u = unk.setdefault(k, [0, set(), []])
                u[0] += 1
                if len(u[1]) < 200:
                    u[1].add(book)
                if len(u[2]) < 3:
                    u[2].append(hid)
        if n_h % 20000 == 0:
            log(f"  {n_h} hadiths, {n_p} names ({time.time() - t0:.0f}s)")
    tmp = dst + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    c = sqlite3.connect(tmp)
    c.executescript("""
        create table pattern(k text, id text, places integer, books integer, share real, n_ids integer, rules_json text, ctx_json text,
                             examples_json text, primary key (k, id)) without rowid;
        create index pattern_places on pattern(places desc);
        create table unknown(k text primary key, places integer, books integer, examples_json text) without rowid;
        create table meta(k text primary key, v text);
    """)
    rows = []
    for (k, pid), (places, bs, rules, ctx, ex) in pat.items():
        if places < min_places:
            continue
        rows.append((k, pid, places, len(bs), places / max(sum(name_ids[k].values()), 1), len(name_ids[k]), json.dumps(dict(rules), ensure_ascii=False),
                     json.dumps([[a, b, n] for (a, b), n in ctx.most_common(4)], ensure_ascii=False), json.dumps([f"v02:{x}" for x in ex])))
    c.executemany("insert into pattern values (?,?,?,?,?,?,?,?,?)", rows)
    urows = [(k, u[0], len(u[1]), json.dumps([f"v02:{x}" for x in u[2]])) for k, u in unk.items()
             if u[0] >= min_unknown and looks_like_a_name(k) and not sanad_parse.is_anon(k)]
    c.executemany("insert into unknown values (?,?,?,?)", urows)
    meta = dict(built=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), books=books, hadiths=n_h, names=n_p, patterns=len(rows), unknown=len(urows),
                names_built=str((nm.meta() or {}).get("built")))
    c.executemany("insert into meta values (?,?)", [(k, json.dumps(x, ensure_ascii=False)) for k, x in meta.items()])
    c.commit()
    c.close()
    os.replace(tmp, dst)
    log(f"-> {dst} {meta} ({time.time() - t0:.0f}s)")
    return meta


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------
def list_patterns(decisions: dict, status: str = "proposed", q: str = "", offset: int = 0, limit: int = 30, only_clear: bool = False) -> dict:
    """``decisions``: {(k, id): {decision, user, ts, note}} (``store.name_decisions``). ``status``: proposed | approve | reject | all."""
    c = _ro(patterns_db())
    if c is None:
        return dict(available=False, total=0, items=[], counts={})
    try:
        c.execute("create temp table dec(k text, id text, decision text, user text, ts text, note text)")
        c.executemany("insert into dec values (?,?,?,?,?,?)", [(k, i, d["decision"], d.get("user"), d.get("ts"), d.get("note")) for (k, i), d in decisions.items()])
        where, args = [], []
        if status == "proposed":
            where.append("d.decision is null")
        elif status in ("approve", "reject"):
            where.append("d.decision = ?")
            args.append(status)
        if only_clear:
            where.append("p.share >= 0.95 and p.n_ids = 1")
        if q.strip():
            where.append("p.k like ?")
            args.append("%" + q.strip().replace("%", "") + "%")
        w = (" where " + " and ".join(where)) if where else ""
        base = "from pattern p left join dec d on d.k = p.k and d.id = p.id" + w
        total = c.execute("select count(*) " + base, args).fetchone()[0]
        rows = c.execute("select p.*, d.decision, d.user, d.ts, d.note " + base + " order by p.places desc limit ? offset ?", args + [limit, offset]).fetchall()
        counts = {r[0] or "proposed": r[1] for r in c.execute("select d.decision, count(*) from pattern p left join dec d on d.k = p.k and d.id = p.id group by 1")}
        items = [dict(k=r["k"], id=r["id"], places=r["places"], books=r["books"], share=round(r["share"], 3), n_ids=r["n_ids"], rules=json.loads(r["rules_json"]),
                      ctx=json.loads(r["ctx_json"]), examples=json.loads(r["examples_json"]),
                      decision=r["decision"], by=r["user"], ts=r["ts"], note=r["note"]) for r in rows]
        meta = {r[0]: json.loads(r[1]) for r in c.execute("select k, v from meta")}
        return dict(available=True, total=total, items=items, counts=counts, meta=meta)
    finally:
        c.close()


def list_unknown(created: dict, q: str = "", offset: int = 0, limit: int = 30) -> dict:
    """The frequent written names no database knows. ``created``: {name: person id} the reviewers already made persons for."""
    c = _ro(patterns_db())
    if c is None:
        return dict(available=False, total=0, items=[])
    try:
        args, w = [], ""
        if q.strip():
            w, args = " where k like ?", ["%" + q.strip().replace("%", "") + "%"]
        rows = [r for r in c.execute("select * from unknown" + w + " order by places desc", args) if r["k"] not in created]
        return dict(available=True, total=len(rows), items=[dict(k=r["k"], places=r["places"], books=r["books"], examples=json.loads(r["examples_json"]))
                                                            for r in rows[offset:offset + limit]])
    finally:
        c.close()


def publish(decisions: dict, dst: Optional[str] = None) -> dict:
    """Write the approved / rejected readings of written names where ICM reads them; atomically."""
    dst = dst or wiki_overlay()
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    tmp = dst + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    c = sqlite3.connect(tmp)
    c.executescript("create table decided(k text, id text, decision text, user text, ts text, note text, primary key (k, id)) without rowid;"
                    "create table meta(k text primary key, v text);")
    rows = [(k, i, d["decision"], d.get("user"), d.get("ts"), d.get("note")) for (k, i), d in decisions.items() if d["decision"] in ("approve", "reject")]
    c.executemany("insert into decided values (?,?,?,?,?,?)", rows)
    c.execute("insert into meta values ('published', ?)", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),))
    c.commit()
    c.close()
    os.replace(tmp, dst)
    return dict(approved=sum(1 for r in rows if r[2] == "approve"), rejected=sum(1 for r in rows if r[2] == "reject"), file=dst)
