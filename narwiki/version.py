"""Narrator database versions: a policy applied to the wiki gives a standalone, read-only snapshot file.

    p = version.load_policy("v1m")                       # narwiki/policies/v1m.json, or "raw" (built in)
    version.build(p, wiki_db, "…/v1m.sqlite")            # reads the wiki read-only, writes the snapshot
    version.evaluate(out, narrators_db, kg_db, dirayah_db)   # precision figures into meta.eval_json

The wiki is never written. A version groups the wiki's persons (``person_ext`` rows) into one-to-one matched
groups: the wiki's own grouping (seed, certain Noor-Dirayah joins, merges), then the policy's chosen
``merge_suggestion`` rows, dup edges first and cross-source edges after, each by score. An edge is accepted
only if the two groups share no source kind (a group holding a KG id refuses a second KG claim); refused
and below-threshold candidates of the enabled kinds go to ``conflict``. Dismissed suggestions never merge.

Snapshot tables: meta(k, v), person(id, canon, name, n_ext), ext(kind, ext_id, person_id, rule, score),
edge(a, b, rule, score, evidence_json), conflict(a, b, rule, score, reason). Ids in edge/conflict are "kind:id".
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sqlite3
import time
from collections import Counter, defaultdict
from typing import Callable, Dict, List, Optional, Set, Tuple

from . import store

HERE = os.path.dirname(os.path.abspath(__file__))
POLICY_DIR = os.path.join(HERE, "policies")
SOURCE_KINDS = ("noor", "kg", "dirayah")
RULES = ("seed", "certain_dirayah", "wiki_merge", "noor_kg", "dup", "dirayah_same_id")

TOP_KEYS = {"name", "label_fa", "description_fa", "wiki_merges", "noor_kg", "dup", "dirayah"}
SECTION_DEFAULTS = {
    "noor_kg": {"kinds": ["link_kg", "name_chrono"], "confidence": ["high"], "min_ctx": 1, "max_death_gap": 10,
                "require_dated_or_ctx2": True, "matching": "one_to_one"},
    "dup": {"kinds": ["noor_dup", "kg_dup"], "require_same_name_key": True, "max_death_gap": 5, "min_shared_if_undated": 2},
    "dirayah": {"suggested_same_id_and_name": True, "max_name_conflict": False},
}
# which two source kinds a suggestion kind joins, and the class that orders it
KIND_INFO = {"link_kg": (("noor", "kg"), "cross"), "name_chrono": (("noor", "kg"), "cross"),
             "link_dirayah": (("noor", "dirayah"), "cross"),
             "noor_dup": (("noor", "noor"), "dup"), "kg_dup": (("kg", "kg"), "dup")}

SCHEMA = """
create table meta(k text primary key, v text);
create table person(id integer primary key, canon text not null, name text, n_ext integer);
create table ext(kind text not null, ext_id text not null, person_id integer not null, rule text not null, score real,
  primary key (kind, ext_id));
create index ext_person on ext(person_id);
create index person_canon on person(canon);
create table edge(a text, b text, rule text, score real, evidence_json text);
create table conflict(a text, b text, rule text, score real, reason text);
"""


def load_policy(name_or_path: str) -> dict:
    """A policy by built-in/file name or path; unknown keys are refused so a policy stays explicit."""
    if name_or_path == "raw" and not os.path.exists(name_or_path):
        pol = {"name": "raw", "label_fa": "خام (بدون ادغام)",
               "description_fa": "شناسه‌های هر پایگاه (نور، گراف دانش، درایة النور) جدا نگه داشته می‌شوند و هیچ ادغامی اعمال نمی‌شود.",
               "wiki_merges": "none"}
    else:
        path = name_or_path if os.path.exists(name_or_path) else os.path.join(POLICY_DIR, name_or_path + ".json")
        with open(path, encoding="utf-8") as f:
            pol = json.load(f)
    bad = set(pol) - TOP_KEYS
    if bad:
        raise ValueError(f"unknown policy keys {sorted(bad)}")
    if "name" not in pol:
        raise ValueError("policy needs a name")
    if pol.setdefault("wiki_merges", "none") not in ("all", "approved", "none"):
        raise ValueError("wiki_merges must be all | approved | none")
    for sec, defaults in SECTION_DEFAULTS.items():
        if sec in pol:
            bad = set(pol[sec]) - set(defaults)
            if bad:
                raise ValueError(f"unknown keys {sorted(bad)} in policy section {sec}")
            for k, v in defaults.items():
                pol[sec].setdefault(k, v)
    if "noor_kg" in pol and pol["noor_kg"]["matching"] != "one_to_one":
        raise ValueError("noor_kg.matching must be one_to_one")
    return pol


def _enabled_kinds(policy: dict) -> Set[str]:
    out: Set[str] = set()
    if "noor_kg" in policy:
        out |= set(policy["noor_kg"]["kinds"])
    if "dup" in policy:
        out |= set(policy["dup"]["kinds"])
    if "dirayah" in policy:
        out.add("link_dirayah")
    return out


def _passes(policy: dict, kind: str, ev: dict) -> bool:
    """Whether a suggestion's stored evidence meets the policy's thresholds for its kind."""
    if kind in ("link_kg", "name_chrono"):
        p = policy["noor_kg"]
        gap = ev.get("death_gap")
        ctx = ev.get("ctx") or 0
        return (ev.get("confidence") in p["confidence"] and ctx >= p["min_ctx"]
                and (gap is None or gap <= p["max_death_gap"])
                and (not p["require_dated_or_ctx2"] or gap is not None or ctx >= 2))
    if kind in ("noor_dup", "kg_dup"):
        p = policy["dup"]
        if p["require_same_name_key"] and not ev.get("same_name_key"):
            return False
        gap = ev.get("death_gap")
        if gap is not None:
            return gap <= p["max_death_gap"]
        return (ev.get("shared_neighbours") or 0) >= p["min_shared_if_undated"]
    p = policy["dirayah"]
    if p["suggested_same_id_and_name"] and not (ev.get("same_id") and ev.get("same_name")):
        return False
    return p["max_name_conflict"] or not ev.get("name_conflict")


def _rule_of(kind: str) -> str:
    return {"link_kg": "noor_kg", "name_chrono": "noor_kg", "noor_dup": "dup", "kg_dup": "dup",
            "link_dirayah": "dirayah_same_id"}[kind]


def _wiki_state(c: sqlite3.Connection) -> str:
    q = lambda s: c.execute(s).fetchone()[0]
    raw = json.dumps([q("select max(id) from revision"), q("select count(*) from person_ext"),
                      q("select count(*) from merge_suggestion where status!='proposed'"),
                      q("select max(id) from merge_suggestion")])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _base_state(c: sqlite3.Connection, mode: str) -> Tuple[Dict[Tuple[str, str], int], Dict[Tuple[str, str], str]]:
    """ext key -> holding wiki person, and ext key -> rule, with unwanted wiki merges undone in memory."""
    holder = {(r["kind"], str(r["ext_id"])): r["person_id"] for r in c.execute("select kind, ext_id, person_id from person_ext")}
    rule: Dict[Tuple[str, str], str] = {}
    revs = c.execute("select id, before_json, review_status from revision where kind='merge' and reverted_by is null "
                     "order by id desc").fetchall()
    for r in revs:
        snaps = json.loads(r["before_json"] or "{}").get("persons") or []
        if len(snaps) < 2:
            continue
        undo = mode == "none" or (mode == "approved" and r["review_status"] != "approved")
        for i, s in enumerate(snaps[:2]):
            pid = s["person"]["id"]
            for kind, e in s["ext"]:
                key = (kind, str(e))
                if key not in holder:
                    continue
                if undo:
                    holder[key] = pid
                elif i == 1:
                    rule[key] = "wiki_merge"
    return holder, rule


class _Groups:
    """Union-find over wiki person ids that also tracks the source kinds each group holds."""

    def __init__(self, kinds: Dict[int, Set[str]]):
        self.parent = {p: p for p in kinds}
        self.kinds = {p: set(k) for p, k in kinds.items()}
        self.members = {p: [p] for p in kinds}

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> int:
        a, b = self.find(a), self.find(b)
        lo, hi = min(a, b), max(a, b)
        self.parent[hi] = lo
        self.kinds[lo] |= self.kinds.pop(hi)
        self.members[lo] += self.members.pop(hi)
        return lo


def _first_ext(by_person: Dict[int, List[Tuple[str, str]]], pid: int, kind: str) -> Optional[str]:
    ids = sorted((e for k, e in by_person.get(pid, []) if k == kind), key=lambda e: (len(e), e))
    return f"{kind}:{ids[0]}" if ids else None


def build(policy: dict, wiki_db: str, out_path: str, log: Callable[[str], None] = print) -> dict:
    """Apply ``policy`` to the wiki and write the snapshot at ``out_path``; returns its meta."""
    c = store.connect(wiki_db, readonly=True)
    state = _wiki_state(c)
    holder, rule = _base_state(c, policy["wiki_merges"])
    persons = {r["id"]: r for r in c.execute("select id, display_name, merged_into from person where status in ('active','merged')")}
    by_person: Dict[int, List[Tuple[str, str]]] = defaultdict(list)
    for (k, e), pid in holder.items():
        by_person[pid].append((k, e))
    for pid, exts in by_person.items():
        if any(k == "noor" for k, _ in exts):
            for k, e in exts:
                if k == "dirayah" and (k, e) not in rule:
                    rule[(k, e)] = "certain_dirayah"
    g = _Groups({p: {k for k, _ in exts if k in SOURCE_KINDS} for p, exts in by_person.items()})
    log(f"wiki: {len(holder)} ids in {len(by_person)} persons (wiki_merges={policy['wiki_merges']})")

    def resolve(pid: int) -> Optional[int]:
        for _ in range(50):
            if pid in g.parent:
                return pid
            p = persons.get(pid)
            if p is None or p["merged_into"] is None:
                return None
            pid = p["merged_into"]
        return None

    enabled = _enabled_kinds(policy)
    cands: List[dict] = []
    edges: List[dict] = []
    conflicts: List[dict] = []
    if enabled:
        q = "select id, a, b, kind, score, evidence_json from merge_suggestion where status in ('proposed','accepted') and kind in (%s)"
        for r in c.execute(q % ",".join("?" * len(enabled)), sorted(enabled)):
            pa, pb = resolve(r["a"]), resolve(r["b"])
            if pa is None or pb is None:
                continue
            ev = json.loads(r["evidence_json"] or "{}")
            kind = r["kind"]
            (ka, kb), cls = KIND_INFO[kind]
            if kind in ("link_kg", "name_chrono"):
                ea, eb = ev.get("noor", {}).get("id"), ev.get("kg", {}).get("id")
            elif kind == "link_dirayah":
                ea, eb = ev.get("noor"), ev.get("dirayah")
            else:
                ea, eb = ev.get("a", {}).get("id"), ev.get("b", {}).get("id")
            if pa > pb and cls == "dup":
                pa, pb, ea, eb = pb, pa, eb, ea
            ea = ea or _first_ext(by_person, pa, ka)
            eb = eb or _first_ext(by_person, pb, kb)
            if not ea or not eb:
                continue
            cand = dict(pa=pa, pb=pb, a=ea, b=eb, kind=kind, cls=cls, rule=_rule_of(kind), score=r["score"] or 0.0,
                        ev=ev, kinds=(ka, kb))
            if _passes(policy, kind, ev):
                cands.append(cand)
            else:
                conflicts.append(dict(a=ea, b=eb, rule=cand["rule"], score=cand["score"], reason="threshold"))
    cands.sort(key=lambda x: (x["cls"] != "dup", -x["score"], x["pa"], x["pb"]))

    joined: Dict[int, Tuple[str, float]] = {}                      # group-side person -> (rule, score) that brought it in
    for cd in cands:
        ra, rb = g.find(cd["pa"]), g.find(cd["pb"])
        if ra == rb:
            continue
        shared = g.kinds[ra] & g.kinds[rb]
        if cd["cls"] == "dup":
            shared -= {cd["kinds"][0]}
        if shared:
            reason = "one_to_one" if shared & set(cd["kinds"]) else "kind_clash"
            conflicts.append(dict(a=cd["a"], b=cd["b"], rule=cd["rule"], score=cd["score"], reason=reason))
            continue
        for p in g.members[rb]:                                 # the b side (second kind / second dup id) is what the edge brings in
            joined.setdefault(p, (cd["rule"], cd["score"]))
        g.union(ra, rb)
        edges.append(dict(a=cd["a"], b=cd["b"], rule=cd["rule"], score=cd["score"], ev=cd["ev"]))
    ext_rows: List[tuple] = []
    person_rows: List[tuple] = []
    for pids in g.members.values():
        exts = []
        for p in pids:
            for k, e in by_person[p]:
                r, s = rule.get((k, e), "seed"), None
                if r in ("seed", "certain_dirayah") and p in joined and len(pids) > 1:
                    r, s = joined[p]
                exts.append((k, e, p, r, s))
        for kind in SOURCE_KINDS:
            ids = [x for x in exts if x[0] == kind]
            if ids:
                top = min(ids, key=lambda x: (int(x[1]) if str(x[1]).isdigit() else 10 ** 12, x[1]))
                canon, rep = f"{kind}:{top[1]}", top[2]
                break
        else:
            top = min(exts, key=lambda x: (x[0], x[1]))
            canon, rep = f"{top[0]}:{top[1]}", top[2]
        person_rows.append((rep, canon, persons[rep]["display_name"] if rep in persons else None, len(exts)))
        ext_rows += [(k, e, rep, r, s) for k, e, p, r, s in exts]

    counts = Counter(r[3] for r in ext_rows)
    meta = dict(name=policy["name"], label_fa=policy.get("label_fa", ""), description_fa=policy.get("description_fa", ""),
                policy_json=json.dumps(policy, ensure_ascii=False), built=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                wiki_db=os.path.abspath(wiki_db), wiki_state=state, n_groups=str(len(person_rows)),
                n_ext=str(len(ext_rows)), n_edges=str(len(edges)), n_conflicts=str(len(conflicts)),
                counts_json=json.dumps({r: counts.get(r, 0) for r in RULES}))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp = out_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    o = sqlite3.connect(tmp)
    o.execute("pragma journal_mode=delete")
    o.executescript(SCHEMA)
    with o:
        o.executemany("insert into meta values (?,?)", meta.items())
        o.executemany("insert into person values (?,?,?,?)", person_rows)
        o.executemany("insert into ext values (?,?,?,?,?)", ext_rows)
        o.executemany("insert into edge values (?,?,?,?,?)", [(e["a"], e["b"], e["rule"], e["score"],
                                                               json.dumps(e["ev"], ensure_ascii=False)) for e in edges])
        o.executemany("insert into conflict values (?,?,?,?,?)", [(x["a"], x["b"], x["rule"], x["score"], x["reason"])
                                                                  for x in conflicts])
    o.execute("vacuum")
    o.close()
    os.replace(tmp, out_path)
    log(f"{policy['name']}: {len(person_rows)} groups, {len(edges)} edges, {len(conflicts)} conflicts -> {out_path}")
    return meta


def read_meta(path: str) -> dict:
    """meta table of a version file as a dict (JSON-valued keys stay strings)."""
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return dict(c.execute("select k, v from meta"))
    finally:
        c.close()


def list_versions(out_dir: str) -> List[dict]:
    out = []
    for p in sorted(glob.glob(os.path.join(out_dir, "*.sqlite"))):
        try:
            m = read_meta(p)
        except sqlite3.Error:
            continue
        if "policy_json" not in m:                      # some other sqlite file (e.g. a wiki) in the directory
            continue
        m["file"] = p
        out.append(m)
    return out


def _chunks(xs: List[int], n: int = 500):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def _death_agreement(edges: List[sqlite3.Row], narrators_db: str, kg_db: str) -> dict:
    from narrators import facts
    pairs = []
    for r in edges:
        a, b = r["a"], r["b"]
        n, k = (a, b) if a.startswith("noor:") else (b, a)
        if n.startswith("noor:") and k.startswith("kg:"):
            pairs.append((int(n[5:]), int(k[3:])))
    nc = sqlite3.connect(f"file:{narrators_db}?mode=ro", uri=True)
    nc.row_factory = sqlite3.Row
    kc = sqlite3.connect(f"file:{kg_db}?mode=ro", uri=True)
    noor_death: Dict[int, float] = {}
    ids = sorted({n for n, _ in pairs})
    for ch in _chunks(ids):
        for rid, f in facts.resolve(nc, ch).items():
            if f.get("death") and f.get("death_source") != "imam":
                lo, hi = f["death"]
                noor_death[rid] = (lo + hi) / 2
    kg_death: Dict[int, int] = {}
    kids = sorted({k for _, k in pairs})
    for ch in _chunks(kids):
        kg_death.update(kc.execute("select id, death_year from rawis where death_year is not null and id in (%s)"
                                   % ",".join("?" * len(ch)), ch).fetchall())
    gaps = [abs(kg_death[k] - noor_death[n]) for n, k in pairs if n in noor_death and k in kg_death]
    out = dict(n_pairs=len(pairs), n_checked=len(gaps))
    for tol in (3, 10):
        ok = sum(1 for x in gaps if x <= tol)
        out[f"n_agree_{tol}"] = ok
        out[f"rate_{tol}"] = round(ok / len(gaps), 4) if gaps else None
    out["n_agree"], out["rate"] = out["n_agree_3"], out["rate_3"]
    return out


def evaluate(out_path: str, narrators_db: Optional[str], kg_db: Optional[str], dirayah_db: Optional[str]) -> dict:
    """Precision figures of a built version, stored in meta.eval_json; missing source databases are skipped."""
    o = sqlite3.connect(out_path)
    o.row_factory = sqlite3.Row
    meta = dict(o.execute("select k, v from meta"))
    ev: dict = dict(rules=json.loads(meta.get("counts_json", "{}")),
                    edges=dict(o.execute("select rule, count(*) from edge group by rule").fetchall()),
                    conflicts=dict(o.execute("select reason, count(*) from conflict group by reason").fetchall()),
                    conflicts_by_rule={f"{r}/{k}": n for r, k, n in
                                       o.execute("select rule, reason, count(*) from conflict group by 1, 2")})
    if narrators_db and kg_db and os.path.exists(narrators_db) and os.path.exists(kg_db):
        rows = o.execute("select a, b from edge where rule='noor_kg'").fetchall()
        ev["noor_kg_death"] = _death_agreement(rows, narrators_db, kg_db)
    else:
        ev["noor_kg_death"] = None
        ev["skipped"] = "narrators or kg database not found"
    ev["dirayah_verification"] = None
    if dirayah_db and os.path.exists(dirayah_db):
        d = sqlite3.connect(f"file:{dirayah_db}?mode=ro", uri=True)
        try:
            row = d.execute("select v from meta where k='verification'").fetchone()
            ev["dirayah_verification"] = json.loads(row[0]) if row else None
        except sqlite3.Error:
            pass
        finally:
            d.close()
    with o:
        o.execute("insert or replace into meta values ('eval_json', ?)", (json.dumps(ev, ensure_ascii=False),))
    o.close()
    return ev
