"""Build the wiki database from the two narrator databases (laptop side):

    python3 -m narwiki seed [--db PATH] [--force]

* one person per Noor narrator (``narrators.sqlite`` ``ravi``, 8,120) with the fields Noor gives (name,
  اشتهار, کنیه, لقب, مذهب, درجه, طبقه, ولادت) and the resolved death date and cities of ``fact``
  (``narrators.facts.resolve``; the Imam-period estimate is *not* stored as a death date);
* one person per Hadith Knowledge Graph narrator (``kg.sqlite`` ``rawis`` with ``is_stub = 0``) with name,
  nickname, rank (Ibn Hajar's grade), tabaqa, birth and death years, residence, nisba, profession and flags;
* the Noor→KG links of ``narrators/link_sunni.py`` (``link`` rows, target_kind kg, confidence high or
  medium) become ``merge_suggestion(kind='link_kg', status='proposed')`` — **not** merged;
* every person gets one ``seed`` revision, already approved; every field row names its source.

KG stubs (``is_stub = 1``) are created on demand by ``store.ensure_ext``.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from typing import Dict, Iterator, List, Optional

from . import config, store

KG_FLAGS = (("is_bukhari", "از رجال صحیح بخاری"), ("is_muslim", "از رجال صحیح مسلم"),
            ("has_tadlis", "متهم به تدلیس"), ("has_ikhtilat", "دچار اختلاط"))


def _ro(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _texts(js: Optional[str]) -> List[str]:
    if not js:
        return []
    try:
        v = json.loads(js)
    except ValueError:
        return [js]
    out = []
    for x in v if isinstance(v, list) else [v]:
        t = x.get("text") if isinstance(x, dict) else x
        if isinstance(t, str) and t.strip() and t.strip() not in out:
            out.append(t.strip())
    return out


def _split(s: Optional[str]) -> List[str]:
    return [x.strip() for x in re.split(r"[،,]", s or "") if x.strip()]


def _noor_rec(r: sqlite3.Row, res: Optional[dict]) -> dict:
    rid = r["ravi_id"]
    F = []
    add = lambda f, v, src="noor": F.append((f, str(v).strip(), src)) if v is not None and str(v).strip() else None
    add("name", r["name"])
    if r["ishtihar"] and r["ishtihar"].strip() != (r["name"] or "").strip():
        add("alias", r["ishtihar"])
    add("kunya", r["kunya"])
    for t in _texts(r["laqab_json"]):
        add("laqab", t)
    add("mazhab", r["mazhab"])
    add("grade", r["grade"])
    for t in _texts(r["tabaqa_json"]):
        add("tabaqa", t)
    for t in _texts(r["wiladat_json"]):
        add("birth", t)
    for t in _texts(r["wafat_json"]):
        add("note", "وفات (نور): " + t)
    if res:
        if res.get("death") and res.get("death_source") != "imam":
            src = "noor/" + str(res.get("death_source") or "facts").split(":")[0]
            lo, hi = res["death"]
            add("death_lo", int(round(lo)), src)
            add("death_hi", int(round(hi)), src)
        for city in res.get("cities") or []:
            add("places", city, "noor/facts")
    return dict(kind="noor", ext_id=str(rid), display_name=(r["name"] or f"راوی نور {rid}").strip(), n_narr=r["n_hadith"] or 0, fields=F)


def _kg_rec(r: sqlite3.Row) -> dict:
    F = []
    add = lambda f, v: F.append((f, str(v).strip(), "kg")) if v is not None and str(v).strip() else None
    add("name", r["name"])
    if r["nickname"] and r["nickname"].strip() != (r["name"] or "").strip():
        add("alias", r["nickname"])
    add("rank", r["rank"])
    if r["tabaka"] is not None:
        add("tabaqa", f"الطبقة {r['tabaka']}")
    if r["birth_year"]:
        add("birth", r["birth_year"])
    if r["death_year"]:
        add("death_lo", r["death_year"])
        add("death_hi", r["death_year"])
    for x in _split(r["iqama"]):
        add("places", x)
    for x in _split(r["nasab"]):
        add("nisba", x)
    if r["profession"]:
        add("note", "حرفه: " + r["profession"])
    for col, label in KG_FLAGS:
        if r[col]:
            add("note", label)
    if r["is_stub"]:
        add("note", "رکورد ناقص در گراف دانش (stub)")
    return dict(kind="kg", ext_id=str(r["id"]), display_name=(r["name"] or r["nickname"] or f"راوی {r['id']}").strip(),
                n_narr=r["riwaya_qty"] or 0, fields=F)


def noor_record(rid: int) -> Optional[dict]:
    from narrators.facts import resolve
    c = _ro(config.narrators_db())
    try:
        r = c.execute("select * from ravi where ravi_id=?", (rid,)).fetchone()
        return _noor_rec(r, resolve(c, [rid]).get(rid)) if r else None
    finally:
        c.close()


def kg_record(kid: int) -> Optional[dict]:
    c = _ro(config.kg_db())
    try:
        r = c.execute("select * from rawis where id=?", (kid,)).fetchone()
        return _kg_rec(r) if r else None
    finally:
        c.close()


def insert_record(c: sqlite3.Connection, rec: dict, user: str = "seed", op: Optional[dict] = None, fts: bool = True) -> int:
    """One person from a source record, with its seed revision (approved). Caller holds the transaction."""
    pid = store.create_person(c, rec["display_name"], rec["n_narr"])
    c.execute("insert into person_ext(kind, ext_id, person_id) values (?,?,?)", (rec["kind"], rec["ext_id"], pid))
    rev = store._rev(c, pid, "seed", None, dict(source=f"{rec['kind']}:{rec['ext_id']}", n_fields=len(rec["fields"])), op, user,
                     f"from {rec['kind']}:{rec['ext_id']}", review="approved")
    c.executemany("insert into person_field(person_id, field, value, source, ext_kind, ext_id, revision_id) values (?,?,?,?,?,?,?)",
                  [(pid, f, v, s, rec["kind"], rec["ext_id"], rev) for f, v, s in rec["fields"]])
    if fts:
        names = [rec["display_name"]] + [v for f, v, s in rec["fields"] if f in store.NAME_FIELDS]
        c.execute("insert into person_fts(rowid, names) values (?,?)", (pid, store.name_terms(names)))
    return pid


def run(path: Optional[str] = None, force: bool = False, log=print) -> dict:
    from narrators.facts import resolve
    path = path or config.DB
    if os.path.exists(path):
        if not force:
            raise SystemExit(f"{path} exists; --force rebuilds it from scratch (every edit there is lost; "
                             f"export-revisions first)")
        for suf in ("", "-wal", "-shm"):
            if os.path.exists(path + suf):
                os.remove(path + suf)
    t0 = time.time()
    c = store.connect(path)
    c.execute("pragma synchronous=off")
    nc = _ro(config.narrators_db())
    res = resolve(nc)
    n_noor = n_kg = 0
    with c:
        for r in nc.execute("select * from ravi order by ravi_id"):
            insert_record(c, _noor_rec(r, res.get(r["ravi_id"])))
            n_noor += 1
    log(f"noor: {n_noor} persons ({time.time() - t0:.0f}s)")
    kc = _ro(config.kg_db())
    with c:
        for r in kc.execute("select * from rawis where is_stub=0 order by id"):
            insert_record(c, _kg_rec(r))
            n_kg += 1
    log(f"kg: {n_kg} persons ({time.time() - t0:.0f}s)")
    kg_names = {}
    n_link = 0
    rows = nc.execute("select l.ravi_id, l.target_id, l.confidence, l.score, l.name_score, l.ctx, l.method, l.detail_json, r.name "
                      "from link l join ravi r on r.ravi_id=l.ravi_id where l.target_kind='kg' and l.confidence in ('high','medium') "
                      "order by l.ravi_id").fetchall()
    noor_names = {r[0]: r[1] for r in nc.execute("select ravi_id, name from ravi")}
    for r in rows:
        a = store.by_ext(c, "noor", r["ravi_id"])
        b = store.ensure_ext(c, "kg", r["target_id"], user="seed")
        if a is None or b is None:
            continue
        det = json.loads(r["detail_json"] or "{}")
        ctx = []
        for x in det.get("context", []):
            kid = x.get("kg_id")
            if kid not in kg_names:
                k = kc.execute("select name from rawis where id=?", (kid,)).fetchone()
                kg_names[kid] = k[0] if k else None
            ctx.append(dict(x, ravi_name=noor_names.get(x.get("ravi")), kg_name=kg_names.get(kid)))
        ev = dict(method="narrators/link_sunni.py link_kg (narrators.sqlite link table)", confidence=r["confidence"],
                  score=r["score"], name_score=r["name_score"], ctx=r["ctx"], chrono=det.get("chrono"), context=ctx,
                  noor=dict(id=f"noor:{r['ravi_id']}", name=r["name"]), kg=dict(det.get("record") or {}, id=f"kg:{r['target_id']}"),
                  alternatives=det.get("alternatives", []))
        with c:
            n_link += store.add_suggestion(c, a, b, "link_kg", r["score"] or 0, ev)
    log(f"link_kg suggestions: {n_link}")
    meta = dict(seeded=store.now(), narrators_db=config.narrators_db(), kg_db=config.kg_db(),
                n_noor=n_noor, n_kg=n_kg, n_link_kg=n_link, seconds=round(time.time() - t0, 1))
    with c:
        c.executemany("insert or replace into meta(k, v) values (?,?)", [(k, str(v)) for k, v in meta.items()])
    c.execute("pragma synchronous=normal")
    c.execute("pragma optimize")
    c.close()
    nc.close()
    kc.close()
    return meta
