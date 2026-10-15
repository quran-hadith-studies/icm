"""The writable wiki database: persons, their external ids, fields with provenance, revisions, suggestions.

    c = store.connect()                      # NARWIKI_DB, WAL, timeout 30 s, tables created if missing
    store.get_person(c, 12)                  # person + ext ids + fields (follows merged_into)
    store.edit_fields(c, 12, "ali", add=[("death_lo", "260")], retire=[345])
    store.merge(c, 12, 9001, keep=12, user="ali")
    store.revert(c, rev_id, "ali")

Every write function applies its change at once, inside one transaction, and records a ``revision`` with
``review_status='unreviewed'`` (seed revisions are ``approved``). A revision keeps the affected persons
before and after (``before_json`` / ``after_json``: ``{"persons": [snapshot, ...]}``) for the history page,
and the operation itself (``op_json``) with persons named by an external id ("anchor") so the revisions made
after seeding can be exported and replayed onto a re-seeded database (``export_revisions`` /
``import_revisions``).

Persons are never deleted: a merge marks the absorbed person ``merged`` (``merged_into`` = the kept one,
its ids and fields move over); reverting an ``add`` marks it ``deleted``.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import time
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import config

FIELDS = ["name", "alias", "kunya", "laqab", "nisba", "mazhab", "grade", "rank", "tabaqa",
          "death_lo", "death_hi", "birth", "places", "note"]
NAME_FIELDS = ("name", "alias", "kunya", "laqab", "nisba")
SOURCE_ORDER = {"user": 0, "noor": 1, "kg": 2, "dirayah": 3}          # which source's value is shown first

SCHEMA = """
create table if not exists person(
  id           integer primary key,
  display_name text not null,
  name_key     text,
  status       text not null default 'active',     -- active | merged | deleted
  merged_into  integer,
  n_narr       integer default 0,                  -- narrations in the source databases (list order only)
  created      text,
  updated      text);
create index if not exists person_name_key on person(name_key);
create index if not exists person_status on person(status, n_narr);
create index if not exists person_merged on person(merged_into);
create table if not exists person_ext(
  kind      text not null,                         -- noor | kg | wiki | (any later database)
  ext_id    text not null,
  person_id integer not null,
  primary key (kind, ext_id));
create index if not exists person_ext_person on person_ext(person_id);
create table if not exists person_field(
  id          integer primary key,
  person_id   integer not null,
  field       text not null,
  value       text,
  source      text not null,                       -- noor | noor/<fact source> | kg | user
  ext_kind    text,
  ext_id      text,
  revision_id integer,
  status      text not null default 'current');    -- current | retired
create index if not exists person_field_person on person_field(person_id, status);
create index if not exists person_field_field on person_field(field, status);
create table if not exists revision(
  id             integer primary key,
  person_id      integer,
  kind           text not null,                    -- seed | add | edit | merge | split | revert
  before_json    text,
  after_json     text,
  op_json        text,                             -- the operation, persons named by external id (replay)
  user           text,
  ts             text,
  note           text,
  review_status  text not null default 'unreviewed',   -- unreviewed | approved | rejected
  reviewed_by    text,
  reviewed_ts    text,
  related_person integer,
  reverts        integer,                          -- for kind=revert: the revision undone
  reverted_by    integer);
create index if not exists revision_person on revision(person_id);
create index if not exists revision_related on revision(related_person);
create index if not exists revision_review on revision(review_status, id);
create table if not exists merge_suggestion(
  id            integer primary key,
  a             integer not null,
  b             integer not null,
  kind          text not null,                     -- link_kg | name_chrono | noor_dup | kg_dup
  score         real,
  evidence_json text,
  status        text not null default 'proposed',  -- proposed | accepted | dismissed
  decided_by    text,
  decided_ts    text,
  unique (a, b, kind));
create index if not exists ms_a on merge_suggestion(a, status);
create index if not exists ms_b on merge_suggestion(b, status);
create index if not exists ms_status on merge_suggestion(status, kind, score);
create virtual table if not exists person_fts using fts5(names, tokenize='unicode61 remove_diacritics 2');
create table if not exists meta(k text primary key, v text);
"""


class WikiError(ValueError):
    """A request the wiki refuses (unknown person, revert blocked, ...); the API turns it into HTTP 4xx."""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def connect(path: Optional[str] = None, readonly: bool = False) -> sqlite3.Connection:
    path = path or config.DB
    if readonly:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        c = sqlite3.connect(path, timeout=30, check_same_thread=False)
        c.execute("pragma journal_mode=wal")
        c.execute("pragma synchronous=normal")
        c.executescript(SCHEMA)
    c.row_factory = sqlite3.Row
    c.execute("pragma foreign_keys=off")
    return c


# ---------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------
def _common():
    from narrators import common                      # lazy: pulls hadith_phylogeny.collation
    return common


def name_key(s: str) -> str:
    return _common().norm_key(s or "")


def name_terms(names: Iterable[str]) -> str:
    """FTS text of a person: normalised tokens of every name form, with and without the article."""
    cm = _common()
    out: List[str] = []
    seen = set()
    for n in names:
        for t in cm.norm_name(n or "").split():
            for x in (t, cm.bare(t)):
                if x not in seen:
                    seen.add(x)
                    out.append(x)
    return " ".join(out)


def fts_query(q: str) -> str:
    cm = _common()
    toks = [cm.bare(t) for t in cm.norm_name(q or "").split() if t not in cm.STOP]
    toks = [re.sub(r'["*]', "", t) for t in toks if t]
    return " ".join(f'"{t}"*' for t in toks[:8] if t)


def refresh_fts(c: sqlite3.Connection, pid: int) -> None:
    c.execute("delete from person_fts where rowid=?", (pid,))
    p = c.execute("select display_name, status from person where id=?", (pid,)).fetchone()
    if p is None or p["status"] != "active":
        return
    names = [p["display_name"]] + [r[0] for r in c.execute(
        "select value from person_field where person_id=? and status='current' and field in (%s)" % ",".join("?" * len(NAME_FIELDS)),
        (pid, *NAME_FIELDS))]
    c.execute("insert into person_fts(rowid, names) values (?,?)", (pid, name_terms(names)))


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------
def resolve_id(c: sqlite3.Connection, pid: int) -> Optional[int]:
    """Follow merged_into to the person that now carries this id; None if unknown."""
    seen = set()
    while pid not in seen:
        seen.add(pid)
        r = c.execute("select status, merged_into from person where id=?", (pid,)).fetchone()
        if r is None:
            return None
        if r["status"] == "merged" and r["merged_into"]:
            pid = r["merged_into"]
        else:
            return pid
    return pid


def ext_of(c: sqlite3.Connection, pid: int) -> List[Tuple[str, str]]:
    return [(r[0], r[1]) for r in c.execute("select kind, ext_id from person_ext where person_id=? order by kind, cast(ext_id as integer), ext_id", (pid,))]


def by_ext(c: sqlite3.Connection, kind: str, ext_id) -> Optional[int]:
    r = c.execute("select person_id from person_ext where kind=? and ext_id=?", (kind, str(ext_id))).fetchone()
    return resolve_id(c, r[0]) if r else None


def fields_of(c: sqlite3.Connection, pid: int, include_retired: bool = False) -> List[dict]:
    q = ("select f.*, r.user as rev_user, r.ts as rev_ts, r.kind as rev_kind, r.review_status as rev_review from person_field f "
         "left join revision r on r.id=f.revision_id where f.person_id=?" + ("" if include_retired else " and f.status='current'"))
    rows = [dict(r) for r in c.execute(q, (pid,))]
    order = {f: i for i, f in enumerate(FIELDS)}
    rows.sort(key=lambda r: (order.get(r["field"], 99), SOURCE_ORDER.get(r["source"].split("/")[0], 5), r["id"]))
    return rows


def get_person(c: sqlite3.Connection, pid: int, follow: bool = True, include_retired: bool = False) -> Optional[dict]:
    rid = resolve_id(c, pid) if follow else pid
    if rid is None:
        return None
    p = c.execute("select * from person where id=?", (rid,)).fetchone()
    if p is None:
        return None
    out = dict(p)
    out["requested"] = pid
    out["ext"] = [f"{k}:{e}" for k, e in ext_of(c, rid)]
    out["fields"] = fields_of(c, rid, include_retired)
    out["merged_from"] = [r[0] for r in c.execute("select id from person where merged_into=? and status='merged'", (rid,))]
    return out


def best(fields: List[dict], name: str) -> Optional[str]:
    for f in fields:                                   # fields_of sorts by source preference
        if f["field"] == name and f["status"] == "current":
            return f["value"]
    return None


def snapshot(c: sqlite3.Connection, pid: int) -> dict:
    p = c.execute("select * from person where id=?", (pid,)).fetchone()
    if p is None:
        raise WikiError(f"no person {pid}")
    return dict(person={k: p[k] for k in p.keys()}, ext=[[k, e] for k, e in ext_of(c, pid)],
                fields=[{k: r[k] for k in ("id", "person_id", "field", "value", "source", "ext_kind", "ext_id", "revision_id", "status")}
                        for r in c.execute("select * from person_field where person_id=? order by id", (pid,))])


def anchor(c: sqlite3.Connection, pid: int) -> Optional[str]:
    """A stable name of a person across re-seeds: its wiki id, else its first Noor id, else first KG id..."""
    ex = ext_of(c, pid)
    for kind in ("wiki", "noor", "kg"):
        for k, e in ex:
            if k == kind:
                return f"{k}:{e}"
    return f"{ex[0][0]}:{ex[0][1]}" if ex else None


def from_anchor(c: sqlite3.Connection, a: Optional[str]) -> Optional[int]:
    if not a:
        return None
    k, _, e = a.partition(":")
    return by_ext(c, k, e)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
def summary(c: sqlite3.Connection, pid: int) -> dict:
    p = c.execute("select * from person where id=?", (pid,)).fetchone()
    fs = fields_of(c, pid)
    names = []
    for f in fs:
        if f["field"] in ("name", "alias") and f["value"] and f["value"] != p["display_name"] and f["value"] not in names:
            names.append(f["value"])
    n_sug = c.execute("select count(*) from merge_suggestion where status='proposed' and (a=? or b=?)", (pid, pid)).fetchone()[0]
    return dict(id=pid, display_name=p["display_name"], status=p["status"], ext=[f"{k}:{e}" for k, e in ext_of(c, pid)],
                death_lo=best(fs, "death_lo"), death_hi=best(fs, "death_hi"), mazhab=best(fs, "mazhab"), grade=best(fs, "grade") or best(fs, "rank"),
                aliases=names[:3], n_narr=p["n_narr"], n_suggestions=n_sug)


def search(c: sqlite3.Connection, q: str = "", kind: str = "", dated: str = "", sugg: str = "",
           limit: int = 50, offset: int = 0) -> dict:
    """Active persons matching ``q`` (names, ``noor:12`` / ``kg:5`` / ``wiki:…`` ids or a person number),
    filtered by which databases they are in (``kind``: noor | kg | dirayah | both | noor_only | kg_only | wiki), whether
    a death date is known (``dated``: 1 | 0) and whether a merge suggestion waits (``sugg``: 1)."""
    q = (q or "").strip()
    where, args, join, order = ["p.status='active'"], [], "", "p.n_narr desc, p.id"
    order_args: list = []
    m = re.fullmatch(r"(noor|kg|wiki|[a-z_]+):(\S+)", q)
    if m:
        pid = by_ext(c, m.group(1), m.group(2))
        where.append("p.id=?"); args.append(pid or -1)
    elif q.isdigit():
        pid = resolve_id(c, int(q))
        where.append("p.id=?"); args.append(pid or -1)
    elif q:
        expr = fts_query(q)
        if not expr:
            return dict(total=0, items=[])
        join = "join (select rowid as pid from person_fts where person_fts match ? limit 20000) f on f.pid=p.id"
        args.insert(0, expr)
        order = "(coalesce(p.n_narr, 0) + 1) * (1 + 2 * (p.name_key = ?)) desc, p.id"     # the most attested, exact names x3
        order_args = [name_key(q)]
    has = lambda k: f"exists (select 1 from person_ext x where x.person_id=p.id and x.kind='{k}')"
    kind_sql = {"noor": has("noor"), "kg": has("kg"), "both": f"{has('noor')} and {has('kg')}",
                "noor_only": f"{has('noor')} and not {has('kg')}", "kg_only": f"{has('kg')} and not {has('noor')}",
                "dirayah": has("dirayah"), "wiki": f"not {has('noor')} and not {has('kg')} and not {has('dirayah')}"}
    if kind in kind_sql:
        where.append(kind_sql[kind])
    dsql = "exists (select 1 from person_field d where d.person_id=p.id and d.field in ('death_lo','death_hi') and d.status='current')"
    if dated == "1":
        where.append(dsql)
    elif dated == "0":
        where.append("not " + dsql)
    if sugg == "1":
        where.append("exists (select 1 from merge_suggestion s where s.status='proposed' and (s.a=p.id or s.b=p.id))")
    base = f"from person p {join} where {' and '.join(where)}"
    total = c.execute(f"select count(*) {base}", args).fetchone()[0]
    ids = [r[0] for r in c.execute(f"select p.id {base} order by {order} limit ? offset ?", (*args, *order_args, int(limit), int(offset)))]
    return dict(total=total, items=[summary(c, i) for i in ids])


# ---------------------------------------------------------------------------
# revisions
# ---------------------------------------------------------------------------
def _rev(c, pid, kind, before, after, op, user, note="", related=None, review="unreviewed", reverts=None) -> int:
    cur = c.execute("insert into revision(person_id, kind, before_json, after_json, op_json, user, ts, note, review_status, related_person, reverts) "
                    "values (?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, kind, json.dumps(before, ensure_ascii=False) if before is not None else None,
                     json.dumps(after, ensure_ascii=False) if after is not None else None,
                     json.dumps(op, ensure_ascii=False) if op is not None else None, user, now(), (note or "")[:1000], review, related, reverts))
    return cur.lastrowid


def _touch(c, pid):
    c.execute("update person set updated=? where id=?", (now(), pid))


def _add_field(c, pid, field, value, source, rev, ext_kind=None, ext_id=None) -> int:
    if field not in FIELDS:
        raise WikiError(f"unknown field {field}")
    value = str(value).strip()[:2000]
    if field in ("death_lo", "death_hi") and not re.fullmatch(r"-?\d{1,4}", value):
        raise WikiError("death_lo / death_hi must be hijri years (numbers)")
    return c.execute("insert into person_field(person_id, field, value, source, ext_kind, ext_id, revision_id) values (?,?,?,?,?,?,?)",
                     (pid, field, value, source, ext_kind, ext_id, rev)).lastrowid


def _wiki_token() -> str:
    return secrets.token_hex(6)


def _active(c, pid) -> int:
    rid = resolve_id(c, int(pid))
    if rid is None:
        raise WikiError(f"no person {pid}")
    st = c.execute("select status from person where id=?", (rid,)).fetchone()[0]
    if st != "active":
        raise WikiError(f"person {rid} is {st}")
    return rid


def create_person(c, display_name: str, n_narr: int = 0, pid: Optional[int] = None) -> int:
    t = now()
    cur = c.execute("insert into person(id, display_name, name_key, status, n_narr, created, updated) values (?,?,?,?,?,?,?)",
                    (pid, display_name, name_key(display_name), "active", n_narr, t, t))
    return cur.lastrowid


def add_person(c, display_name: str, fields: Sequence[Tuple[str, str]], user: str, note: str = "",
               ext: Sequence[Tuple[str, str]] = (), wiki: Optional[str] = None) -> int:
    """A new person made by hand: gets a ``wiki:<token>`` id (its anchor) plus any given external ids."""
    display_name = (display_name or "").strip()[:300]
    if not display_name:
        raise WikiError("a name is required")
    with c:
        pid = create_person(c, display_name)
        wiki = wiki or _wiki_token()
        exts = [("wiki", wiki)] + [(k, str(e)) for k, e in ext]
        for k, e in exts:
            if by_ext(c, k, e) is not None:
                raise WikiError(f"{k}:{e} already belongs to person {by_ext(c, k, e)}")
            c.execute("insert into person_ext(kind, ext_id, person_id) values (?,?,?)", (k, e, pid))
        rev = _rev(c, pid, "add", None, None, None, user, note)
        for f, v in fields:
            if str(v).strip():
                _add_field(c, pid, f, v, "user", rev)
        after = dict(persons=[snapshot(c, pid)])
        op = dict(op="add", display_name=display_name, fields=[[f, v] for f, v in fields], ext=[[k, e] for k, e in exts])
        c.execute("update revision set after_json=?, op_json=? where id=?", (json.dumps(after, ensure_ascii=False), json.dumps(op, ensure_ascii=False), rev))
        refresh_fts(c, pid)
    return pid


def edit_fields(c, pid: int, user: str, display_name: Optional[str] = None, add: Sequence[Tuple[str, str]] = (),
                retire: Sequence[int] = (), note: str = "") -> int:
    """Change a person: new display name, new values (source ``user``), and values to retire (any source:
    a retired value stays in the history and can be restored by reverting). Returns the revision id."""
    pid = _active(c, pid)
    with c:
        before = dict(persons=[snapshot(c, pid)])
        rev = _rev(c, pid, "edit", before, None, None, user, note)
        retired, retired_desc = [], []
        for fid in retire:
            r = c.execute("select * from person_field where id=? and person_id=? and status='current'", (int(fid), pid)).fetchone()
            if r is None:
                raise WikiError(f"field {fid} is not a current value of person {pid}")
            c.execute("update person_field set status='retired' where id=?", (r["id"],))
            retired.append(r["id"])
            retired_desc.append([r["field"], r["value"], r["source"]])
        added = [_add_field(c, pid, f, v, "user", rev) for f, v in add if str(v).strip()]
        old_name = c.execute("select display_name from person where id=?", (pid,)).fetchone()[0]
        dn = None
        if display_name is not None and display_name.strip() and display_name.strip() != old_name:
            dn = display_name.strip()[:300]
            c.execute("update person set display_name=?, name_key=? where id=?", (dn, name_key(dn), pid))
        if not (retired or added or dn):
            raise WikiError("nothing to change")
        _touch(c, pid)
        after = dict(persons=[snapshot(c, pid)], added=added, retired=retired, display_name=[old_name, dn] if dn else None)
        op = dict(op="edit", person=anchor(c, pid), display_name=dn,
                  add=[[f, str(v).strip()] for f, v in add if str(v).strip()], retire=retired_desc)
        c.execute("update revision set after_json=?, op_json=? where id=?", (json.dumps(after, ensure_ascii=False), json.dumps(op, ensure_ascii=False), rev))
        refresh_fts(c, pid)
    return rev


def merge(c, a: int, b: int, keep: Optional[int], user: str, note: str = "", suggestion: Optional[int] = None) -> int:
    """Merge persons a and b into ``keep`` (a or b): the other is marked merged; its external ids and every
    field row (with their provenance) move to ``keep``. Returns the revision id."""
    a, b = _active(c, a), _active(c, b)
    if a == b:
        raise WikiError("these are already the same person")
    keep = a if keep is None else _active(c, keep)
    if keep not in (a, b):
        raise WikiError("keep must be one of the two persons")
    gone = b if keep == a else a
    with c:
        before = dict(persons=[snapshot(c, keep), snapshot(c, gone)])
        op = dict(op="merge", keep=anchor(c, keep), gone=anchor(c, gone))
        if suggestion:
            s = c.execute("select kind from merge_suggestion where id=?", (suggestion,)).fetchone()
            op["suggestion"] = dict(id=suggestion, kind=s["kind"] if s else None)
        rev = _rev(c, keep, "merge", before, None, op, user, note, related=gone)
        c.execute("update person_ext set person_id=? where person_id=?", (keep, gone))
        c.execute("update person_field set person_id=? where person_id=?", (keep, gone))
        n = c.execute("select n_narr from person where id=?", (gone,)).fetchone()[0] or 0
        c.execute("update person set status='merged', merged_into=?, updated=? where id=?", (keep, now(), gone))
        c.execute("update person set n_narr=coalesce(n_narr,0)+?, updated=? where id=?", (n, now(), keep))
        c.execute("update merge_suggestion set status='accepted', decided_by=?, decided_ts=? where status='proposed' and "
                  "((a=? and b=?) or (a=? and b=?))", (user, now(), keep, gone, gone, keep))
        after = dict(persons=[snapshot(c, keep), snapshot(c, gone)])
        c.execute("update revision set after_json=? where id=?", (json.dumps(after, ensure_ascii=False), rev))
        refresh_fts(c, keep)
        refresh_fts(c, gone)
    return rev


def split(c, pid: int, ext_ids: Sequence[str], field_ids: Sequence[int], label: str, user: str, note: str = "",
          wiki: Optional[str] = None) -> Tuple[int, int]:
    """Move some external ids (``"kind:id"``) and field rows of a person to a new person named ``label``.
    Returns (new person id, revision id)."""
    pid = _active(c, pid)
    label = (label or "").strip()[:300]
    if not label:
        raise WikiError("the new person needs a name")
    have = {f"{k}:{e}" for k, e in ext_of(c, pid)}
    ext_ids = [x for x in dict.fromkeys(ext_ids)]
    for x in ext_ids:
        if x not in have:
            raise WikiError(f"{x} is not an id of person {pid}")
        if x.startswith("wiki:"):
            raise WikiError("the wiki id stays with its person")
    if not ext_ids and not field_ids:
        raise WikiError("choose at least one id or one value to move")
    if have and len(have - set(ext_ids)) == 0:
        raise WikiError("the original person must keep at least one id")
    with c:
        before = dict(persons=[snapshot(c, pid)])
        new = create_person(c, label)
        wiki = wiki or _wiki_token()
        c.execute("insert into person_ext(kind, ext_id, person_id) values ('wiki',?,?)", (wiki, new))
        rev = _rev(c, pid, "split", before, None, None, user, note, related=new)
        fdesc = []
        for x in ext_ids:
            k, _, e = x.partition(":")
            c.execute("update person_ext set person_id=? where kind=? and ext_id=?", (new, k, e))
        for fid in field_ids:
            r = c.execute("select * from person_field where id=? and person_id=?", (int(fid), pid)).fetchone()
            if r is None:
                raise WikiError(f"field {fid} is not a value of person {pid}")
            c.execute("update person_field set person_id=? where id=?", (new, r["id"]))
            fdesc.append([r["field"], r["value"], r["source"]])
        for x in ext_ids:                              # values that came from a moved id follow it
            k, _, e = x.partition(":")
            for r in c.execute("select id, field, value, source from person_field where person_id=? and ext_kind=? and ext_id=?", (pid, k, e)).fetchall():
                c.execute("update person_field set person_id=? where id=?", (new, r["id"]))
                fdesc.append([r["field"], r["value"], r["source"]])
        _touch(c, pid)
        after = dict(persons=[snapshot(c, pid), snapshot(c, new)])
        op = dict(op="split", person=anchor(c, pid), ext=list(ext_ids), fields=fdesc, label=label, wiki=wiki)
        c.execute("update revision set after_json=?, op_json=? where id=?", (json.dumps(after, ensure_ascii=False), json.dumps(op, ensure_ascii=False), rev))
        refresh_fts(c, pid)
        refresh_fts(c, new)
    return new, rev


def _restore(c, snaps: List[dict], involved: Iterable[int]) -> None:
    involved = set(involved)
    snap_ids = {s["person"]["id"] for s in snaps}
    for pid in involved:
        c.execute("delete from person_ext where person_id=?", (pid,))
        c.execute("delete from person_field where person_id=?", (pid,))
    for s in snaps:
        p = s["person"]
        c.execute("update person set display_name=?, name_key=?, status=?, merged_into=?, n_narr=?, updated=? where id=?",
                  (p["display_name"], p["name_key"], p["status"], p["merged_into"], p.get("n_narr") or 0, now(), p["id"]))
        for k, e in s["ext"]:
            c.execute("insert or replace into person_ext(kind, ext_id, person_id) values (?,?,?)", (k, e, p["id"]))
        for f in s["fields"]:
            c.execute("insert or replace into person_field(id, person_id, field, value, source, ext_kind, ext_id, revision_id, status) "
                      "values (?,?,?,?,?,?,?,?,?)", (f["id"], p["id"], f["field"], f["value"], f["source"], f["ext_kind"], f["ext_id"],
                                                     f["revision_id"], f["status"]))
    for pid in involved - snap_ids:
        c.execute("update person set status='deleted', updated=? where id=?", (now(), pid))
    for pid in involved:
        refresh_fts(c, pid)


def revert(c, rev_id: int, user: str, note: str = "", review: str = "unreviewed") -> int:
    """Undo a revision; the undo is itself a revision (kind ``revert``). Edits are undone value by value
    (later edits of other values survive). Merges and splits restore the persons as they were, and are refused
    when a later revision touched one of those persons (revert that one first)."""
    r = c.execute("select * from revision where id=?", (rev_id,)).fetchone()
    if r is None:
        raise WikiError("no such revision")
    if r["kind"] == "seed":
        raise WikiError("seed revisions (the source databases) cannot be reverted; retire the value instead")
    if r["reverted_by"]:
        raise WikiError(f"already reverted by revision {r['reverted_by']}")
    after = json.loads(r["after_json"] or "{}")
    before = json.loads(r["before_json"] or "{}")
    with c:
        if r["kind"] in ("edit", "revert") and "added" in after:
            pid = resolve_id(c, r["person_id"]) or r["person_id"]
            cur_snap = dict(persons=[snapshot(c, pid)])
            added, retired = [], []
            for fid in after.get("added", []):
                if c.execute("update person_field set status='retired' where id=? and status='current'", (fid,)).rowcount:
                    retired.append(fid)
            for fid in after.get("retired", []):
                if c.execute("update person_field set status='current' where id=? and status='retired'", (fid,)).rowcount:
                    added.append(fid)
            dn = None
            if after.get("display_name"):
                old, new = after["display_name"]
                if c.execute("select display_name from person where id=?", (pid,)).fetchone()[0] == new:
                    c.execute("update person set display_name=?, name_key=? where id=?", (old, name_key(old), pid))
                    dn = [new, old]
            if not (added or retired or dn):
                raise WikiError("nothing left to undo: later changes already replaced these values")
            _touch(c, pid)
            snap_after = dict(persons=[snapshot(c, pid)], added=added, retired=retired, display_name=dn)
            new_rev = _rev(c, pid, "revert", cur_snap, snap_after, dict(op="revert", revision=rev_id),
                           user, note, review=review, reverts=rev_id)
            refresh_fts(c, pid)
        elif r["kind"] == "add":
            pid = r["person_id"]
            st = c.execute("select status from person where id=?", (pid,)).fetchone()[0]
            if st != "active":
                raise WikiError(f"person {pid} is {st}; revert the later merge first")
            later = c.execute("select count(*) from revision where id>? and (person_id=? or related_person=?) and reverted_by is null and kind!='revert'",
                              (rev_id, pid, pid)).fetchone()[0]
            if later:
                raise WikiError("later revisions changed this person; revert them first")
            b = dict(persons=[snapshot(c, pid)])
            c.execute("update person set status='deleted', updated=? where id=?", (now(), pid))
            new_rev = _rev(c, pid, "revert", b, dict(persons=[snapshot(c, pid)]), dict(op="revert", revision=rev_id), user, note,
                           review=review, reverts=rev_id)
            refresh_fts(c, pid)
        elif r["kind"] in ("merge", "split"):
            snaps = before["persons"]
            involved = {s["person"]["id"] for s in snaps} | {s["person"]["id"] for s in after.get("persons", [])}
            q = ",".join("?" * len(involved))
            later = c.execute(f"select id, kind from revision where id>? and reverted_by is null and kind!='revert' and "
                              f"(person_id in ({q}) or related_person in ({q}))", (rev_id, *involved, *involved)).fetchall()
            if later:
                raise WikiError("later revisions changed these persons (%s); revert them first" % ", ".join(f"#{x['id']} {x['kind']}" for x in later[:5]))
            cur = dict(persons=[snapshot(c, p) for p in sorted(involved)])
            _restore(c, snaps, involved)
            op = json.loads(r["op_json"] or "{}")
            sid = (op.get("suggestion") or {}).get("id")
            if sid:
                c.execute("update merge_suggestion set status='proposed', decided_by=null, decided_ts=null where id=?", (sid,))
            new_rev = _rev(c, r["person_id"], "revert", cur, dict(persons=[snapshot(c, p) for p in sorted(involved)]),
                           dict(op="revert", revision=rev_id), user, note, related=r["related_person"], review=review, reverts=rev_id)
        else:
            raise WikiError(f"cannot revert a {r['kind']} revision")
        c.execute("update revision set reverted_by=? where id=?", (new_rev, rev_id))
    return new_rev


def review(c, rev_id: int, status: str, user: str, revert_it: bool = False, note: str = "") -> dict:
    """approve / reject a revision; rejecting with ``revert_it`` also undoes it (the undo is approved)."""
    if status not in ("approved", "rejected", "unreviewed"):
        raise WikiError("status must be approved, rejected or unreviewed")
    r = c.execute("select * from revision where id=?", (rev_id,)).fetchone()
    if r is None:
        raise WikiError("no such revision")
    out = dict(revision=rev_id, status=status)
    if status == "rejected" and revert_it and not r["reverted_by"] and r["kind"] != "seed":
        out["revert"] = revert(c, rev_id, user, note or f"rejected #{rev_id}", review="approved")
    with c:
        c.execute("update revision set review_status=?, reviewed_by=?, reviewed_ts=? where id=?",
                  (status, user if status != "unreviewed" else None, now() if status != "unreviewed" else None, rev_id))
    return out


def rev_dict(c, r: sqlite3.Row, full: bool = True) -> dict:
    d = {k: r[k] for k in r.keys() if k not in ("before_json", "after_json", "op_json")}
    d["op"] = json.loads(r["op_json"]) if r["op_json"] else None
    if full:
        d["before"] = json.loads(r["before_json"]) if r["before_json"] else None
        d["after"] = json.loads(r["after_json"]) if r["after_json"] else None
        d["diff"] = diff(d["before"], d["after"])
        if r["kind"] == "seed":
            d["seed_fields"] = [dict(field=f["field"], value=f["value"], source=f["source"], status=f["status"])
                                for f in c.execute("select * from person_field where revision_id=? order by id", (r["id"],))]
    for k in ("person_id", "related_person"):
        if d.get(k):
            p = c.execute("select display_name, status from person where id=?", (d[k],)).fetchone()
            d[k + "_name"] = p["display_name"] if p else None
    return d


def diff(before: Optional[dict], after: Optional[dict]) -> List[dict]:
    """Rows {person, what, before, after} between two {"persons": [snapshot]} for the history page."""
    out = []
    bp = {s["person"]["id"]: s for s in (before or {}).get("persons", []) if s}
    ap = {s["person"]["id"]: s for s in (after or {}).get("persons", []) if s}
    for pid in sorted(set(bp) | set(ap)):
        b, a = bp.get(pid), ap.get(pid)
        for k in ("display_name", "status", "merged_into"):
            vb = b["person"].get(k) if b else None
            va = a["person"].get(k) if a else None
            if vb != va:
                out.append(dict(person=pid, what=k, before=vb, after=va))
        eb = {f"{k}:{e}" for k, e in (b or {}).get("ext", [])}
        ea = {f"{k}:{e}" for k, e in (a or {}).get("ext", [])}
        for x in sorted(eb - ea):
            out.append(dict(person=pid, what="ext", before=x, after=None))
        for x in sorted(ea - eb):
            out.append(dict(person=pid, what="ext", before=None, after=x))
        fb = {f["id"]: f for f in (b or {}).get("fields", []) if f["status"] == "current"}
        fa = {f["id"]: f for f in (a or {}).get("fields", []) if f["status"] == "current"}
        for fid in sorted(set(fb) - set(fa)):
            f = fb[fid]
            out.append(dict(person=pid, what="field", field=f["field"], source=f["source"], before=f["value"], after=None))
        for fid in sorted(set(fa) - set(fb)):
            f = fa[fid]
            out.append(dict(person=pid, what="field", field=f["field"], source=f["source"], before=None, after=f["value"]))
    return out


def history(c, pid: int) -> List[dict]:
    ids = {pid} | {r[0] for r in c.execute("select id from person where merged_into=?", (pid,))}
    q = ",".join("?" * len(ids))
    rows = c.execute(f"select * from revision where person_id in ({q}) or related_person in ({q}) order by id desc limit 300",
                     (*ids, *ids)).fetchall()
    return [rev_dict(c, r) for r in rows]


def queue(c, status: str = "unreviewed", kind: str = "", limit: int = 50, offset: int = 0) -> dict:
    where, args = ["kind!='seed'"], []
    if status:
        where.append("review_status=?"); args.append(status)
    if kind:
        where.append("kind=?"); args.append(kind)
    w = " and ".join(where)
    total = c.execute(f"select count(*) from revision where {w}", args).fetchone()[0]
    rows = c.execute(f"select * from revision where {w} order by id {'asc' if status == 'unreviewed' else 'desc'} limit ? offset ?",
                     (*args, int(limit), int(offset))).fetchall()
    return dict(total=total, items=[rev_dict(c, r) for r in rows])


# ---------------------------------------------------------------------------
# suggestions
# ---------------------------------------------------------------------------
def add_suggestion(c, a: int, b: int, kind: str, score: float, evidence: dict) -> bool:
    if a == b:
        return False
    a, b = min(a, b), max(a, b)
    cur = c.execute("insert or ignore into merge_suggestion(a, b, kind, score, evidence_json) values (?,?,?,?,?)",
                    (a, b, kind, round(float(score), 3), json.dumps(evidence, ensure_ascii=False)))
    return cur.rowcount > 0


def _sug_dict(c, r) -> dict:
    d = {k: r[k] for k in r.keys() if k != "evidence_json"}
    d["evidence"] = json.loads(r["evidence_json"]) if r["evidence_json"] else {}
    ra, rb = resolve_id(c, r["a"]), resolve_id(c, r["b"])
    d["a_now"], d["b_now"] = ra, rb
    d["same_now"] = ra is not None and ra == rb
    d["a_person"] = summary(c, ra) if ra else None
    d["b_person"] = summary(c, rb) if rb else None
    return d


def suggestions(c, kind: str = "", status: str = "proposed", person: Optional[int] = None,
                limit: int = 50, offset: int = 0, order: str = "score") -> dict:
    where, args = [], []
    if kind:
        where.append("kind=?"); args.append(kind)
    if status:
        where.append("status=?"); args.append(status)
    if person is not None:
        ids = {person} | {r[0] for r in c.execute("select id from person where merged_into=?", (person,))}
        q = ",".join("?" * len(ids))
        where.append(f"(a in ({q}) or b in ({q}))"); args += [*ids, *ids]
    w = ("where " + " and ".join(where)) if where else ""
    total = c.execute(f"select count(*) from merge_suggestion {w}", args).fetchone()[0]
    ob = "score desc, id" if order == "score" else "id desc"
    rows = c.execute(f"select * from merge_suggestion {w} order by {ob} limit ? offset ?", (*args, int(limit), int(offset))).fetchall()
    counts = {f"{r[0]}|{r[1]}": r[2] for r in c.execute("select kind, status, count(*) from merge_suggestion group by kind, status")}
    return dict(total=total, items=[_sug_dict(c, r) for r in rows], counts=counts)


def decide_suggestion(c, sid: int, decision: str, user: str, keep: Optional[int] = None, note: str = "") -> dict:
    """``accepted`` merges the two persons (``keep`` = which one stays; default the first), ``dismissed``
    just closes it, ``proposed`` reopens a dismissed one."""
    r = c.execute("select * from merge_suggestion where id=?", (sid,)).fetchone()
    if r is None:
        raise WikiError("no such suggestion")
    if decision == "accepted":
        if r["status"] == "accepted":
            raise WikiError("already accepted")
        a, b = resolve_id(c, r["a"]), resolve_id(c, r["b"])
        if a == b:
            with c:
                c.execute("update merge_suggestion set status='accepted', decided_by=?, decided_ts=? where id=?", (user, now(), sid))
            return dict(ok=True, person=a, note="already the same person")
        keep = resolve_id(c, int(keep)) if keep else a
        rev = merge(c, a, b, keep, user, note or f"suggestion #{sid} ({r['kind']})", suggestion=sid)
        with c:
            c.execute("update merge_suggestion set status='accepted', decided_by=?, decided_ts=? where id=?", (user, now(), sid))
        return dict(ok=True, person=keep, revision=rev)
    if decision in ("dismissed", "proposed"):
        if r["status"] == "accepted":
            raise WikiError("an accepted suggestion is undone by reverting its merge")
        with c:
            c.execute("update merge_suggestion set status=?, decided_by=?, decided_ts=? where id=?",
                      (decision, user if decision == "dismissed" else None, now() if decision == "dismissed" else None, sid))
        return dict(ok=True)
    raise WikiError("decision must be accepted, dismissed or proposed")


# ---------------------------------------------------------------------------
# on-demand persons for source ids (KG stubs, ids added to a source later)
# ---------------------------------------------------------------------------
def ensure_ext(c, kind: str, ext_id, user: str = "system") -> int:
    """The person carrying ``kind:ext_id``; created from the source database if there is none yet (KG stubs
    are not seeded). The creation is recorded as an approved ``seed`` revision."""
    pid = by_ext(c, kind, ext_id)
    if pid is not None:
        return pid
    from . import seed as seed_mod
    if kind == "kg":
        rec = seed_mod.kg_record(int(ext_id))
    elif kind == "noor":
        rec = seed_mod.noor_record(int(ext_id))
    else:
        raise WikiError(f"cannot create a person for {kind}:{ext_id}")
    if rec is None:
        raise WikiError(f"{kind}:{ext_id} does not exist in its database")
    with c:
        pid = seed_mod.insert_record(c, rec, user=user, op=dict(op="ensure", ext=f"{kind}:{ext_id}"))
        refresh_fts(c, pid)
    return pid


# ---------------------------------------------------------------------------
# export / replay of the revisions made after seeding
# ---------------------------------------------------------------------------
def export_revisions(c) -> dict:
    revs = []
    for r in c.execute("select * from revision where op_json is not null order by id"):
        revs.append(dict(id=r["id"], kind=r["kind"], op=json.loads(r["op_json"]), user=r["user"], ts=r["ts"], note=r["note"],
                         review_status=r["review_status"], reviewed_by=r["reviewed_by"], reviewed_ts=r["reviewed_ts"]))
    sugs = []
    for r in c.execute("select * from merge_suggestion where status!='proposed' order by id"):
        sugs.append(dict(kind=r["kind"], a=anchor(c, r["a"]), b=anchor(c, r["b"]), a_orig=_orig_anchor(c, r["a"]),
                         b_orig=_orig_anchor(c, r["b"]), status=r["status"], decided_by=r["decided_by"], decided_ts=r["decided_ts"]))
    meta = {r[0]: r[1] for r in c.execute("select k, v from meta")}
    return dict(version=1, exported=now(), seed=meta, revisions=revs, suggestions=sugs)


def _orig_anchor(c, pid: int) -> Optional[str]:
    """The external id a suggestion side was made for (the person's own first id, even after a merge)."""
    r = c.execute("select kind, after_json, op_json from revision where (person_id=? and kind in ('seed','add')) or "
                  "(related_person=? and kind='split') order by id limit 1", (pid, pid)).fetchone()
    try:
        if r and r["kind"] == "seed":
            return json.loads(r["after_json"])["source"]
        if r and r["kind"] == "add":
            return next(f"wiki:{e}" for k, e in json.loads(r["op_json"])["ext"] if k == "wiki")
        if r and r["kind"] == "split":
            return "wiki:" + json.loads(r["op_json"])["wiki"]
    except (KeyError, TypeError, ValueError, StopIteration):
        pass
    return anchor(c, pid)


def import_revisions(c, data: dict, user_suffix: str = "") -> dict:
    """Replay an ``export_revisions`` dump onto this (freshly seeded) database. Operations whose persons
    cannot be found are skipped and listed."""
    idmap: Dict[int, int] = {}
    done, skipped = 0, []
    for r in data.get("revisions", []):
        op = r["op"] or {}
        u = (r.get("user") or "import") + user_suffix
        try:
            k = op.get("op")
            if k == "ensure":
                kind, _, e = op["ext"].partition(":")
                pid = ensure_ext(c, kind, e, u)
                new = c.execute("select max(id) from revision where person_id=?", (pid,)).fetchone()[0]
            elif k == "add":
                exts = [(x, y) for x, y in op.get("ext", []) if x != "wiki"]
                wiki = next((y for x, y in op.get("ext", []) if x == "wiki"), None)
                pid = add_person(c, op["display_name"], op.get("fields", []), u, r.get("note") or "", ext=exts, wiki=wiki)
                new = c.execute("select max(id) from revision where person_id=? and kind='add'", (pid,)).fetchone()[0]
            elif k == "edit":
                pid = from_anchor(c, op["person"])
                if pid is None:
                    raise WikiError("person not found")
                ids = []
                for f, v, s in op.get("retire", []):
                    row = c.execute("select id from person_field where person_id=? and field=? and value=? and source=? and status='current'",
                                    (pid, f, v, s)).fetchone()
                    if row:
                        ids.append(row[0])
                new = edit_fields(c, pid, u, op.get("display_name"), op.get("add", []), ids, r.get("note") or "")
            elif k == "merge":
                a, b = from_anchor(c, op["keep"]), from_anchor(c, op["gone"])
                if a is None or b is None:
                    raise WikiError("person not found")
                sid = None
                if op.get("suggestion"):
                    row = c.execute("select id from merge_suggestion where kind=? and ((a=? and b=?) or (a=? and b=?))",
                                    (op["suggestion"].get("kind"), a, b, b, a)).fetchone()
                    sid = row[0] if row else None
                new = merge(c, a, b, a, u, r.get("note") or "", suggestion=sid)
                if sid:
                    with c:
                        c.execute("update merge_suggestion set status='accepted', decided_by=?, decided_ts=? where id=?", (u, r.get("ts"), sid))
            elif k == "split":
                pid = from_anchor(c, op["person"])
                if pid is None:
                    raise WikiError("person not found")
                fids = []
                for f, v, s in op.get("fields", []):
                    row = c.execute("select id from person_field where person_id=? and field=? and value=? and source=?", (pid, f, v, s)).fetchone()
                    if row and row[0] not in fids:
                        fids.append(row[0])
                ext_ok = [x for x in op.get("ext", []) if from_anchor(c, x) == pid]
                _, new = split(c, pid, ext_ok, fids, op["label"], u, r.get("note") or "", wiki=op.get("wiki"))
            elif k == "revert":
                orig = idmap.get(op["revision"])
                if orig is None:
                    raise WikiError("reverted revision was not replayed")
                new = revert(c, orig, u, r.get("note") or "")
            else:
                raise WikiError(f"unknown op {k}")
            idmap[r["id"]] = new
            with c:
                c.execute("update revision set review_status=?, reviewed_by=?, reviewed_ts=?, ts=? where id=?",
                          (r.get("review_status") or "unreviewed", r.get("reviewed_by"), r.get("reviewed_ts"), r.get("ts") or now(), new))
            done += 1
        except (WikiError, KeyError, TypeError, sqlite3.Error) as e:
            skipped.append(dict(id=r.get("id"), op=op.get("op"), error=str(e)))
    nsug = 0
    for s in data.get("suggestions", []):
        a, b = from_anchor(c, s.get("a_orig") or s["a"]), from_anchor(c, s.get("b_orig") or s["b"])
        if a is None or b is None or s["status"] == "accepted":
            continue
        with c:
            nsug += c.execute("update merge_suggestion set status=?, decided_by=?, decided_ts=? where kind=? and status='proposed' and "
                              "((a=? and b=?) or (a=? and b=?))", (s["status"], s.get("decided_by"), s.get("decided_ts"), s["kind"], a, b, b, a)).rowcount
    return dict(replayed=done, skipped=skipped, suggestions=nsug)


def stats(c) -> dict:
    q = lambda s, *a: c.execute(s, a).fetchall()
    return dict(
        persons={r[0]: r[1] for r in q("select status, count(*) from person group by status")},
        ext={r[0]: r[1] for r in q("select kind, count(*) from person_ext group by kind")},
        persons_by_sources={r[0]: r[1] for r in q(
            "select k, count(*) from (select group_concat(kind, '+') as k from (select distinct x.person_id, x.kind from person_ext x "
            "join person p on p.id=x.person_id and p.status='active' where x.kind in ('noor','kg','dirayah') order by x.person_id, x.kind) group by person_id) group by k")},
        fields=q("select count(*) from person_field")[0][0],
        revisions={f"{r[0]}|{r[1]}": r[2] for r in q("select kind, review_status, count(*) from revision group by kind, review_status")},
        suggestions={f"{r[0]}|{r[1]}": r[2] for r in q("select kind, status, count(*) from merge_suggestion group by kind, status")},
        meta={r[0]: r[1] for r in q("select k, v from meta")})
