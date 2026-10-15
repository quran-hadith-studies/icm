"""Add Dirayat al-Noor 3 to the wiki database (laptop side, after ``seed``):

    python3 -m narwiki dirayah [--db PATH]

Source: ``external/dirayah.sqlite`` (``python3 -m narrators dirayah``). Dirayah narrators become the external id
kind ``dirayah`` and are placed by the rule the wiki keeps for every source — joined only when certain:

* a Dirayah narrator with a ``certain`` link to a Noor narrator (``person_link.tier``: the two stand at the same
  place of the same isnads, >= 3 times, >= 95 % of each side, names not disagreeing; verified against CRCIS's
  evaluation and the rijal page citations with no contradiction, see ``narrators/dirayah.py``) gets its id and
  values on that Noor narrator's person — the ids may differ (``noor:4305`` + ``dirayah:3462``);
* every other Dirayah narrator is a person of its own;
* the ``suggested`` links become ``merge_suggestion(kind='link_dirayah')`` with their evidence — not merged.

Values carry ``source = 'dirayah'``: the title (``name``, or ``alias`` when it differs from the person's name),
school (``mazhab``), grade (``grade``), class — the Imams the narrator is a companion of (``tabaqa``), Dirayah's
summaries and the eight rijal books' evaluations (``note``), and where the narrator's biography is in the rijal
books (``note``). Every change is one approved ``seed`` revision naming the Dirayah id, like the Noor and KG
seed. Re-running adds only the Dirayah ids the database does not hold yet.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import defaultdict
from typing import Dict, List, Optional

from . import config, seed, store


def _ro(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _loads(v) -> list:
    try:
        x = json.loads(v or "[]")
    except ValueError:
        return []
    return x if isinstance(x, list) else [x]


def _fields(r: Optional[sqlite3.Row], tarjama: List[sqlite3.Row], person_name: Optional[str]) -> List[tuple]:
    F: List[tuple] = []
    add = lambda f, v: F.append((f, str(v).strip(), "dirayah")) if v is not None and str(v).strip() else None
    if r is None:
        return F
    title = (r["title"] or "").strip()
    if title:
        add("alias" if person_name and title != person_name.strip() else "name", title)
    add("mazhab", r["religion_result"])
    add("grade", r["credit_result"])
    add("tabaqa", r["class_summary"])
    summ = "، ".join(x for x in (r["religion_summary"], r["credit_summary"], r["tawthiq_summary"]) if x)
    if summ:
        add("note", "جمع‌بندی ارزیابی (درایة النور): " + summ)
    for e in _loads(r["credit_thamanieh"]) + _loads(r["religion_thamanieh"]):
        root, child = e.get("Root"), e.get("Child")
        child = "، ".join(child) if isinstance(child, list) else child
        parts = [x for x in (root, child if child != root else None) if x]
        if parts:
            add("note", "ارزیابی کتب ثمانیه (درایة النور): " + " — ".join(parts))
    for t in tarjama:
        page = f"{t['vol']}/{t['start_page']}" if t["vol"] else str(t["start_page"])
        add("note", f"شرح حال: {t['book']} {page}")
    return F


def _record(r: Optional[sqlite3.Row], did: int, tarjama) -> dict:
    name = (r["title"] if r is not None else None) or f"راوی درایة النور {did}"
    return dict(kind="dirayah", ext_id=str(did), display_name=name.strip(), n_narr=(r["n_sanad"] or 0) if r is not None else 0,
                fields=_fields(r, tarjama, None))


def _attach(c: sqlite3.Connection, pid: int, did: int, r, tarjama, link: dict) -> None:
    """Put ``dirayah:did`` and its values on person ``pid`` (a certain link), as one approved seed revision."""
    pname = c.execute("select display_name from person where id=?", (pid,)).fetchone()[0]
    F = _fields(r, tarjama, pname)
    c.execute("insert into person_ext(kind, ext_id, person_id) values ('dirayah',?,?)", (str(did), pid))
    rev = store._rev(c, pid, "seed", None, dict(source=f"dirayah:{did}", n_fields=len(F), joined_to=f"noor:{link['noor_id']}",
                                                n_aligned=link["n_aligned"], share_noor=link["share_noor"], share_dirayah=link["share_dirayah"]),
                     None, "seed", f"from dirayah:{did}, certain link to noor:{link['noor_id']} ({link['n_aligned']} shared isnad places)",
                     review="approved")
    c.executemany("insert into person_field(person_id, field, value, source, ext_kind, ext_id, revision_id) values (?,?,?,?,?,?,?)",
                  [(pid, f, v, s, "dirayah", str(did), rev) for f, v, s in F])
    store.refresh_fts(c, pid)


def run(path: Optional[str] = None, src: Optional[str] = None, log=print) -> dict:
    path = path or config.DB
    src = src or config.dirayah_db()
    if not os.path.exists(path):
        raise SystemExit(f"{path} does not exist: run `python3 -m narwiki seed` first")
    t0 = time.time()
    c = store.connect(path)
    d = _ro(src)
    ravi = {r["id"]: r for r in d.execute("select * from ravi")}
    tar: Dict[int, list] = defaultdict(list)
    for t in d.execute("select ravi_id, book, vol, start_page from tarjama where start_page is not null order by ravi_id, book, vol, start_page"):
        tar[t["ravi_id"]].append(t)
    links = [dict(x) for x in d.execute("select * from person_link")]
    certain = {x["dirayah_id"]: x for x in links if x["tier"] == "certain"}
    n = dict(joined=0, own=0, suggestions=0, skipped_present=0)
    with c:
        for did in sorted(set(ravi) | set(certain)):
            if store.by_ext(c, "dirayah", did) is not None:
                n["skipped_present"] += 1
                continue
            r = ravi.get(did)
            link = certain.get(did)
            if link:
                pid = store.ensure_ext(c, "noor", link["noor_id"], user="seed")
                _attach(c, pid, did, r, tar.get(did, []), link)
                n["joined"] += 1
            else:
                seed.insert_record(c, _record(r, did, tar.get(did, [])))
                n["own"] += 1
    log(f"dirayah: {n['joined']} joined to Noor persons, {n['own']} persons of their own ({time.time() - t0:.0f}s)")
    with c:
        for x in links:
            if x["tier"] != "suggested":
                continue
            a = store.by_ext(c, "noor", x["noor_id"])
            b = store.by_ext(c, "dirayah", x["dirayah_id"])
            if b is None:                                # a Dirayah id with no narrator record and no certain link
                rec = _record(ravi.get(x["dirayah_id"]), x["dirayah_id"], tar.get(x["dirayah_id"], []))
                b = seed.insert_record(c, rec)
            if a is None or a == b:
                continue
            ev = json.loads(x["evidence"] or "{}")
            ev.update(method="narrators/dirayah.py person_link (shared isnad places)", noor=f"noor:{x['noor_id']}",
                      dirayah=f"dirayah:{x['dirayah_id']}", n_aligned=x["n_aligned"], share_noor=x["share_noor"],
                      share_dirayah=x["share_dirayah"], same_id=bool(x["same_id"]), same_name=bool(x["same_name"]))
            n["suggestions"] += store.add_suggestion(c, a, b, "link_dirayah", x["share_noor"] or 0, ev)
    log(f"link_dirayah suggestions: {n['suggestions']}")
    meta = dict(dirayah=store.now(), dirayah_db=src, **{f"dirayah_{k}": v for k, v in n.items()})
    with c:
        c.executemany("insert or replace into meta(k, v) values (?,?)", [(k, str(v)) for k, v in meta.items()])
    c.close()
    d.close()
    return meta
