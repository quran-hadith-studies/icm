"""HTTP API and pages of the narrator wiki, as a FastAPI router the host app mounts.

    app.include_router(make_router(get_user, prefix="/narrator"))

``get_user(request)`` returns ``{"username", "is_admin"}`` or None (the host's session). Everything is
readable by anyone. While ``config.OPEN`` is set (``NARWIKI_OPEN``, default "1") every visitor counts as an
admin (anonymous ones as "guest"), like ICM today; otherwise writes need a login and reviews / reverts an
admin. Writes must carry the header ``X-NARWIKI: 1`` (a custom header cannot be sent cross-site without a CORS
pre-flight, which keeps cookie-authenticated writes safe from CSRF).

Every write applies at once and is recorded as an unreviewed revision (``store``); the queue page is the
review. Hadith occurrences are read live from narrators.sqlite (``slot``) and kg.sqlite (``sanad_rawis``);
hadith.ai's narrator mentions from hadithai.sqlite (``mention``, ``icm/build/hadithai.py``). hadith.ai has no
narrator identities, so its mentions never become persons or merges: a mention is shown under a Noor narrator
only as a *suggestion* (the same name with the same neighbours is that narrator in every linked Noor isnad);
the mentions Noor's own slots confirm are only counted, since they are the Noor occurrences already listed.
"""
from __future__ import annotations

import fcntl
import glob
import json
import os
import re
import sqlite3
import threading
import zlib
from contextlib import contextmanager
from typing import Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool

from . import config, store, version

STATIC = os.path.join(os.path.dirname(__file__), "static")
ASSETS = ("narwiki.js", "narwiki.css", "narwiki.html")
EXT_RE = re.compile(r"^[a-z_]{2,20}:[\w.-]{1,40}$")
_init_lock = threading.Lock()
_inited: set = set()
_proj_cache: Dict[str, tuple] = {}


@contextmanager
def _db(write: bool = False):
    """A connection per request. The web app never creates the database: until the seeded file is installed
    (deploy/sync-narwiki-db.sh) every call answers 503."""
    path = config.DB
    if not os.path.exists(path):
        raise HTTPException(503, "the narrator wiki database is not installed yet")
    with _init_lock:
        first = path not in _inited
        _inited.add(path)
    c = store.connect(path) if first else _open(path)
    try:
        yield c
    except store.WikiError as e:
        raise HTTPException(400, str(e))
    finally:
        c.close()


def _open(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(path, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _src(kind: str) -> sqlite3.Connection:
    path = config.narrators_db() if kind == "noor" else config.hadithai_db() if kind == "hadithai" else config.kg_db()
    c = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------------------
# person page data
# ---------------------------------------------------------------------------
def _card(nid: str) -> dict:
    try:
        from icm import narrator_info
        return narrator_info.card(nid)
    except Exception as e:                            # noqa: BLE001 - a missing source must not break the page
        return dict(id=nid, kind=nid.split(":")[0], error=str(e)[:200])


def icm_projects(ext: List[str]) -> List[dict]:
    """ICM projects using any of these ids (``<ICM_CACHE_DIR>/narrator_index.json``; absent -> [])."""
    p = os.path.join(config.ICM_CACHE_DIR, "narrator_index.json")
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return []
    hit = _proj_cache.get(p)
    if not hit or hit[0] != mt:
        try:
            data = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _proj_cache[p] = hit = (mt, data if isinstance(data, dict) else {})
    out, seen = [], set()
    for x in ext:
        for e in hit[1].get(x, []) or []:
            if isinstance(e, dict) and e.get("pid") and e["pid"] not in seen:
                seen.add(e["pid"])
                out.append(dict(pid=str(e["pid"]), title=e.get("title"), status=e.get("status"), via=x))
    return out


def hadith_counts(ext: List[str]) -> dict:
    noor = [int(x.split(":")[1]) for x in ext if x.startswith("noor:") and x.split(":")[1].isdigit()]
    kg = [int(x.split(":")[1]) for x in ext if x.startswith("kg:") and x.split(":")[1].isdigit()]
    out = dict(noor=0, kg=0, hadithai=0, hadithai_confirmed=0)
    try:
        if noor:
            c = _src("noor")
            out["noor"] = c.execute("select count(*) from slot where ravi_id in (%s)" % ",".join("?" * len(noor)), noor).fetchone()[0]
            c.close()
            if os.path.exists(config.hadithai_db()):
                c = _src("hadithai")
                q = ",".join("?" * len(noor))
                out["hadithai"] = c.execute(f"select count(*) from mention where sugg_ravi in ({q})", noor).fetchone()[0]
                out["hadithai_confirmed"] = c.execute(f"select count(*) from mention where ravi_id in ({q})", noor).fetchone()[0]
                c.close()
        if kg:
            c = _src("kg")
            out["kg"] = c.execute("select count(*) from sanad_rawis where rawi_id in (%s)" % ",".join("?" * len(kg)), kg).fetchone()[0]
            c.close()
    except sqlite3.Error:
        pass
    return out


def hai_stats() -> dict:
    """hadithai.sqlite's mention counts (``meta``), or {} without the file."""
    if not os.path.exists(config.hadithai_db()):
        return {}
    c = _src("hadithai")
    try:
        return {k: int(v) for k, v in c.execute("select k, v from meta where k like 'mentions%' or k='speakers'")}
    except (sqlite3.Error, ValueError):
        return {}
    finally:
        c.close()


# ---------------------------------------------------------------------------
# narrator-database versions: manual decisions per version
# ---------------------------------------------------------------------------
def _vdir() -> str:
    return config.VERSIONS_DIR


def _vview(name: str) -> Optional["version.View"]:
    """The built file of ``name`` loaded read-only; None for ``raw`` or a version not built yet."""
    if name == "raw":
        return None
    path = version.version_path(_vdir(), name)
    if not os.path.exists(path):
        return None
    try:
        return version.View(path)
    except sqlite3.Error:
        return None


def _vcheck(name: str, editable: bool = False) -> None:
    if not version.NAME_RE.fullmatch(name):
        raise HTTPException(404, "unknown version")
    known = name == "raw" or version.policy_exists(name) or os.path.exists(version.version_path(_vdir(), name))
    if not known:
        raise HTTPException(404, "unknown version")
    if editable:
        if name == "raw":
            raise HTTPException(400, "the raw version merges nothing and has no decisions")
        if not version.policy_exists(name):
            raise HTTPException(400, "this version has no policy file here, so it cannot be rebuilt")


def versions_list(c) -> List[dict]:
    built = {m["name"]: m for m in version.list_versions(_vdir())}
    pols = {os.path.basename(f)[:-5] for f in glob.glob(os.path.join(version.POLICY_DIR, "*.json"))} - {"raw"}
    out = [dict(name="raw", label_fa=version.load_policy("raw")["label_fa"], description_fa=version.load_policy("raw")["description_fa"],
                built=None, wiki_state="", n_groups=None, counts=None, editable=False, n_decisions=0)]
    for n in sorted(set(built) | pols):
        m = built.get(n, {})
        pol = {}
        try:
            pol = version.load_policy(n) if n in pols else json.loads(m.get("policy_json") or "{}")
        except (ValueError, OSError):
            pass
        try:
            counts = json.loads(m["counts_json"]) if m.get("counts_json") else None
        except ValueError:
            counts = None
        out.append(dict(name=n, label_fa=m.get("label_fa") or pol.get("label_fa"), description_fa=m.get("description_fa") or pol.get("description_fa"),
                        built=m.get("built"), wiki_state=m.get("wiki_state"), n_groups=int(m["n_groups"]) if m.get("n_groups") else None,
                        counts=counts, editable=n in pols and n != "raw", n_decisions=len(store.version_decisions(c, n))))
    return out


def group_payload(c, name: str, view, ext: str) -> dict:
    """Members of the id's group in the version (from the built file) and the id's current manual decisions."""
    decs = store.version_decisions(c, name)
    mine = [d for d in decs if ext in (d["a"], d["b"])]
    if view is None:
        k, _, e = ext.partition(":")
        if store.by_ext(c, k, e) is None:
            raise HTTPException(404, "unknown id")
        return dict(id=ext, version=name, person_id=None, canon=ext, name=None, members=[dict(id=ext, kind=k, rule="seed", score=None)],
                    edges=[], decisions=mine)
    g = view.group(ext)
    if g is None:
        raise HTTPException(404, "id not in this version")
    ids = {m["id"] for m in g["members"]}
    return dict(id=ext, version=name, **g, decisions=mine, group_decisions=[d for d in decs if d["a"] in ids or d["b"] in ids])


def version_suggestions(c, name: str, kind: str, status: str, limit: int, offset: int) -> dict:
    view = _vview(name)
    decs = {(d["a"], d["b"]): d for d in store.version_decisions(c, name)}
    where, args = "", []
    if kind:
        where, args = "where kind=?", [kind]
    rows = c.execute(f"select * from merge_suggestion {where} order by score desc, id", args).fetchall()
    counts = {"proposed": 0, "merged": 0, "rejected": 0}
    keep = []
    for r in rows:
        ev = json.loads(r["evidence_json"]) if r["evidence_json"] else {}
        ea, eb = version.suggestion_ids(r["kind"], ev)
        if not ea:
            ra = store.resolve_id(c, r["a"])
            ea = next((f"{k}:{e}" for k, e in store.ext_of(c, ra) if k in version.SOURCE_KINDS), None) if ra else None
        if not eb:
            rb = store.resolve_id(c, r["b"])
            eb = next((f"{k}:{e}" for k, e in store.ext_of(c, rb) if k in version.SOURCE_KINDS and f"{k}:{e}" != ea), None) if rb else None
        d = decs.get(store.pair_key(ea, eb)) if ea and eb else None
        if r["status"] == "dismissed" and d is None:
            continue                                              # a dismissed suggestion is not offered again
        if ea and eb and view is not None:
            ps = view.pair_state(ea, eb, d)
        else:
            ps = dict(state="rejected" if d and d["decision"] == "reject" else "proposed", rule=None, conflict=None)
        counts[ps["state"]] += 1
        if status in ("all", ps["state"]):
            keep.append((r, ea, eb, d, ps))
    page = keep[offset:offset + limit]
    items = []
    for r, ea, eb, d, ps in page:
        x = store._sug_dict(c, r)
        x.update(a_ext=ea, b_ext=eb, version_state=ps["state"], version_rule=ps["rule"], conflict_reason=ps["conflict"],
                 decision=d)
        items.append(x)
    return dict(version=name, total=len(keep), items=items, counts=counts)


def person_full(c, pid: int) -> dict:
    p = store.get_person(c, pid, include_retired=True)
    if p is None:
        raise HTTPException(404, "no such person")
    fields = [f for f in p["fields"] if f["status"] == "current"]
    retired = [f for f in p["fields"] if f["status"] != "current"]
    cards = [_card(x) for x in p["ext"] if x.split(":")[0] in ("noor", "kg")]
    neigh: Dict[str, Dict] = {"teachers": {}, "students": {}}
    for cd in cards:
        for role in ("teachers", "students"):
            for t in cd.get(role) or []:
                k, _, e = t["id"].partition(":")
                wp = store.by_ext(c, k, e)
                key = f"p{wp}" if wp else t["id"]
                d = neigh[role].setdefault(key, dict(person_id=wp, name=None, ids=[], n=0))
                if wp and d["name"] is None:
                    d["name"] = c.execute("select display_name from person where id=?", (wp,)).fetchone()[0]
                d["name"] = d["name"] or t.get("name")
                d["ids"].append(t["id"])
                d["n"] += t.get("n") or 0
    p["fields"] = fields
    p["retired"] = retired
    return dict(person=p, cards=cards,
                teachers=sorted(neigh["teachers"].values(), key=lambda d: -d["n"]),
                students=sorted(neigh["students"].values(), key=lambda d: -d["n"]),
                suggestions=store.suggestions(c, status="", person=p["id"], limit=100, order="score")["items"],
                projects=icm_projects(p["ext"]), counts=hadith_counts(p["ext"]),
                n_revisions=c.execute("select count(*) from revision where person_id=? or related_person=?", (p["id"], p["id"])).fetchone()[0])


def hai_mentions(ids: List[int], offset: int, limit: int) -> dict:
    """hadith.ai mentions suggested for these Noor narrators, each with its record's whole tagged chain."""
    if not ids or not os.path.exists(config.hadithai_db()):
        return dict(total=0, items=[])
    q = ",".join("?" * len(ids))
    c = _src("hadithai")
    n = _src("noor")
    names: Dict[int, Optional[str]] = {}

    def name(rid):
        if rid not in names:
            r = n.execute("select name from ravi where ravi_id=?", (rid,)).fetchone()
            names[rid] = r[0] if r else None
        return names[rid]
    try:
        total = c.execute(f"select count(*) from mention where sugg_ravi in ({q})", ids).fetchone()[0]
        rows = c.execute(f"select m.hid, m.i, m.sugg_ravi, m.sugg, m.sugg_n, b.title from mention m join hadith h on h.hid=m.hid "
                         f"left join book b on b.id=h.book where m.sugg_ravi in ({q}) order by m.hid, m.i limit ? offset ?",
                         (*ids, limit, offset)).fetchall()
        items = []
        for r in rows:
            chain = c.execute("select i, name, term, ravi_id, sugg_ravi from mention where hid=? order by i", (r["hid"],)).fetchall()
            items.append(dict(src="hadithai", hadith_id=r["hid"], book=r["title"], url=f"/h/i/{config.NOOR_OFFSET + r['hid']}",
                              self=f"noor:{r['sugg_ravi']}", sugg=r["sugg"], sugg_n=r["sugg_n"],
                              chain=[dict(term=x["term"], title=x["name"], me=x["i"] == r["i"],
                                          id=f"noor:{x['ravi_id'] or x['sugg_ravi']}" if (x["ravi_id"] or x["sugg_ravi"]) else None,
                                          sugg=bool(x["sugg_ravi"] and not x["ravi_id"]),
                                          label=name(x["ravi_id"] or x["sugg_ravi"]) if (x["ravi_id"] or x["sugg_ravi"]) else None)
                                     for x in chain]))
        return dict(total=total, items=items)
    except sqlite3.Error as e:
        return dict(total=0, items=[], error=str(e)[:200])
    finally:
        c.close()
        n.close()


def hadiths(ext: List[str], src: str, offset: int, limit: int) -> dict:
    if src == "hadithai":
        return hai_mentions([int(x.split(":")[1]) for x in ext if x.startswith("noor:") and x.split(":")[1].isdigit()], offset, limit)
    ids = [int(x.split(":")[1]) for x in ext if x.startswith(src + ":") and x.split(":")[1].isdigit()]
    if not ids:
        return dict(total=0, items=[])
    q = ",".join("?" * len(ids))
    c = _src(src)
    try:
        if src == "noor":
            total = c.execute(f"select count(*) from slot where ravi_id in ({q})", ids).fetchone()[0]
            rows = c.execute(f"select hadith_id, sanad_idx, pos, ravi_id from slot where ravi_id in ({q}) order by hadith_id, sanad_idx, pos "
                             f"limit ? offset ?", (*ids, limit, offset)).fetchall()
            items = []
            for r in rows:
                chain = c.execute("select pos, term, title, ravi_id from slot where hadith_id=? and sanad_idx=? order by pos",
                                  (r["hadith_id"], r["sanad_idx"])).fetchall()
                h = c.execute("select book from hadith where hadith_id=?", (r["hadith_id"],)).fetchone()
                items.append(dict(src="noor", hadith_id=r["hadith_id"], sanad_idx=r["sanad_idx"], book=h["book"] if h else None,
                                  url=f"/h/i/{config.NOOR_OFFSET + r['hadith_id']}", self=f"noor:{r['ravi_id']}",
                                  chain=[dict(term=x["term"], title=x["title"], id=f"noor:{x['ravi_id']}" if x["ravi_id"] else None,
                                              me=x["pos"] == r["pos"]) for x in chain]))
        else:
            total = c.execute(f"select count(*) from sanad_rawis where rawi_id in ({q})", ids).fetchone()[0]
            rows = c.execute(f"select sanad_id, pos, rawi_id from sanad_rawis where rawi_id in ({q}) order by sanad_id limit ? offset ?",
                             (*ids, limit, offset)).fetchall()
            books = {}
            items = []
            for r in rows:
                s = c.execute("select hadith_id, hukm from sanads where id=?", (r["sanad_id"],)).fetchone()
                h = c.execute("select id, book_id, no_inbook, page_no, sanad_z from kg_hadith where id=?", (s["hadith_id"],)).fetchone() if s else None
                if h and h["book_id"] not in books:
                    b = c.execute("select name from books where id=?", (h["book_id"],)).fetchone()
                    books[h["book_id"]] = b[0] if b else None
                chain = c.execute("select s.pos, s.rawi_id, r.name from sanad_rawis s left join rawis r on r.id=s.rawi_id "
                                  "where s.sanad_id=? order by s.pos", (r["sanad_id"],)).fetchall()
                try:
                    sanad = zlib.decompress(h["sanad_z"]).decode() if h and h["sanad_z"] else ""
                except zlib.error:
                    sanad = ""
                items.append(dict(src="kg", hadith_id=h["id"] if h else None, sanad_id=r["sanad_id"], book=books.get(h["book_id"]) if h else None,
                                  no=h["no_inbook"] if h else None, page=h["page_no"] if h else None, hukm=s["hukm"] if s else None,
                                  sanad=sanad[:1500], self=f"kg:{r['rawi_id']}",
                                  chain=[dict(title=x["name"], id=f"kg:{x['rawi_id']}", me=x["pos"] == r["pos"]) for x in chain]))
        return dict(total=total, items=items)
    except sqlite3.Error as e:
        return dict(total=0, items=[], error=str(e)[:200])
    finally:
        c.close()


def merge_preview(c, a: int, b: int) -> dict:
    pa, pb = store.get_person(c, a), store.get_person(c, b)
    if pa is None or pb is None:
        raise HTTPException(404, "no such person")
    if pa["status"] != "active" or pb["status"] != "active":
        raise HTTPException(400, "only active persons can be merged")
    va: Dict[str, set] = {}
    vb: Dict[str, set] = {}
    for f in pa["fields"]:
        va.setdefault(f["field"], set()).add(f["value"])
    for f in pb["fields"]:
        vb.setdefault(f["field"], set()).add(f["value"])
    rows = []
    for fld in store.FIELDS:
        x, y = sorted(va.get(fld, ())), sorted(vb.get(fld, ()))
        if x or y:
            rows.append(dict(field=fld, a=x, b=y, same=set(x) == set(y), conflict=bool(x and y and not set(x) & set(y))))
    sug = store.suggestions(c, status="", person=pa["id"], limit=200)["items"]
    sug = [s for s in sug if pb["id"] in (s["a_now"], s["b_now"])]
    da = lambda p: next((int(f["value"]) for f in p["fields"] if f["field"] in ("death_lo", "death_hi") and re.fullmatch(r"-?\d+", f["value"] or "")), None)
    warn = []
    if da(pa) is not None and da(pb) is not None and abs(da(pa) - da(pb)) > 30:
        warn.append(f"death dates differ by {abs(da(pa) - da(pb))} years")
    both = {x.split(":")[0] for x in pa["ext"]} & {x.split(":")[0] for x in pb["ext"]} - {"wiki"}
    if both:
        warn.append("both have ids in the same database: " + ", ".join(sorted(both)))
    return dict(a=store.summary(c, pa["id"]), b=store.summary(c, pb["id"]), a_ext=pa["ext"], b_ext=pb["ext"], fields=rows,
                suggestions=sug, warnings=warn)


def export_html(data: dict, base: str) -> str:
    css = open(os.path.join(STATIC, "narwiki.css"), encoding="utf-8").read()
    js = open(os.path.join(STATIC, "narwiki.js"), encoding="utf-8").read()
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")
    name = data["person"]["person"]["display_name"]
    esc = lambda s: (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return (f'<!doctype html>\n<html lang="fa-IR" dir="rtl"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(name)} — ویکی راویان</title>'
            f"<style>{css}</style></head>\n<body data-base=\"{esc(base)}\" data-static=\"1\">"
            f'<header class="top"><div class="wrap"><a class="brand" href="{esc(base)}/">ویکی راویان</a>'
            f'<span class="sub">نسخه‌ی ذخیره‌شده — {esc(data.get("exported", ""))}</span><span class="grow"></span><span id="who" class="nav"></span></div></header>'
            f'<main class="wrap" id="app"></main><div id="modal" class="modal" hidden><div class="modal-box"><button class="x" id="modal-x">×</button>'
            f'<div id="modal-body"></div></div></div>\n<script>window.NARWIKI_STATIC = {payload};</script>\n<script>{js.replace("</script", "<\\/script")}</script>'
            f"</body></html>")


# ---------------------------------------------------------------------------
# router
# ---------------------------------------------------------------------------
version_mod = version


def make_router(get_user: Callable[[Request], Optional[dict]], prefix: str = "/narrator") -> APIRouter:
    r = APIRouter(prefix=prefix)
    if config.OPEN:
        host_user = get_user

        def get_user(request: Request) -> dict:
            u = host_user(request)
            return dict(username=u["username"] if u else "guest", is_admin=True)

    def need_user(request: Request, admin: bool = False) -> dict:
        u = get_user(request)
        if not u:
            raise HTTPException(401, "login required")
        if request.method != "GET" and request.headers.get("x-narwiki") != "1":
            raise HTTPException(403, "missing X-NARWIKI header")
        if admin and not u.get("is_admin"):
            raise HTTPException(403, "only an admin may do this")
        return u

    async def body(request: Request) -> dict:
        try:
            b = await request.json()
        except ValueError:
            raise HTTPException(400, "JSON body required")
        if not isinstance(b, dict):
            raise HTTPException(400, "JSON object required")
        return b

    # -- pages -----------------------------------------------------------------------------------
    def page() -> HTMLResponse:
        # Cloudflare caches /static for hours whatever our Cache-Control says: version the URLs instead
        v = "%x" % max(int(os.path.getmtime(os.path.join(STATIC, f))) for f in ASSETS)
        html = open(os.path.join(STATIC, "narwiki.html"), encoding="utf-8").read().replace("__BASE__", prefix).replace("__V__", v)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @r.get("", response_class=HTMLResponse)
    @r.get("/", response_class=HTMLResponse)
    @r.get("/p/{pid}", response_class=HTMLResponse)
    @r.get("/p/{pid}/{view}", response_class=HTMLResponse)
    @r.get("/merge", response_class=HTMLResponse)
    @r.get("/queue", response_class=HTMLResponse)
    @r.get("/suggestions", response_class=HTMLResponse)
    @r.get("/new", response_class=HTMLResponse)
    def index_page(pid: str = "", view: str = ""):
        return page()

    @r.get("/static/{name}")
    def static(name: str):
        if name not in os.listdir(STATIC):
            raise HTTPException(404)
        return FileResponse(os.path.join(STATIC, name), headers={"Cache-Control": "no-cache"})

    # -- read ------------------------------------------------------------------------------------
    @r.get("/api/me")
    def me(request: Request):
        u = get_user(request)
        return dict(user=u["username"] if u else None, admin=bool(u and u.get("is_admin")), open=config.OPEN,
                    installed=os.path.exists(config.DB))

    @r.get("/api/stats")
    def stats():
        with _db() as c:
            s = store.stats(c)
            return {k: s[k] for k in ("persons", "ext", "persons_by_sources", "suggestions", "meta")} | dict(
                unreviewed=c.execute("select count(*) from revision where review_status='unreviewed'").fetchone()[0],
                hadithai=hai_stats())

    @r.get("/api/search")
    def search(q: str = "", kind: str = "", dated: str = "", sugg: str = "", limit: int = 50, offset: int = 0):
        with _db() as c:
            return store.search(c, q[:200], kind, dated, sugg, max(1, min(limit, 200)), max(0, offset))

    @r.get("/api/person/{pid}")
    def person(pid: int, version: str = ""):
        with _db() as c:
            out = person_full(c, pid)
            if version:
                _vcheck(version)
                view = _vview(version)
                seen, groups = set(), []
                for x in out["person"]["ext"]:
                    if x.split(":")[0] not in version_mod.SOURCE_KINDS:
                        continue
                    g = group_payload(c, version, view, x)
                    key = g["person_id"] if g["person_id"] is not None else x
                    if key not in seen:
                        seen.add(key)
                        groups.append(g)
                out["version_group"] = groups
            return out

    @r.get("/api/person/{pid}/hadiths")
    def person_hadiths(pid: int, src: str = "noor", offset: int = 0, limit: int = 30):
        if src not in ("noor", "kg", "hadithai"):
            raise HTTPException(400, "src must be noor, kg or hadithai")
        with _db() as c:
            p = store.get_person(c, pid)
            if p is None:
                raise HTTPException(404, "no such person")
        return hadiths(p["ext"], src, max(0, offset), max(1, min(limit, 100)))

    @r.get("/api/person/{pid}/history")
    def person_history(pid: int):
        with _db() as c:
            rid = store.resolve_id(c, pid)
            if rid is None:
                raise HTTPException(404, "no such person")
            return dict(person=store.summary(c, rid), revisions=store.history(c, rid))

    @r.get("/api/person/{pid}/projects")
    def person_projects(pid: int):
        with _db() as c:
            p = store.get_person(c, pid)
            if p is None:
                raise HTTPException(404, "no such person")
        return icm_projects(p["ext"])

    @r.get("/api/person/{pid}/export.html")
    def person_export(request: Request, pid: int):
        with _db() as c:
            data = dict(person=person_full(c, pid), exported=store.now())
            rid = data["person"]["person"]["id"]
            data["history"] = dict(person=store.summary(c, rid), revisions=store.history(c, rid))
            ext = data["person"]["person"]["ext"]
        data["hadiths"] = {s: hadiths(ext, s, 0, 100) for s in ("noor", "kg", "hadithai")}
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        host = request.headers.get("host") or request.url.netloc
        html = export_html(data, f"{proto}://{host}{prefix}")
        return HTMLResponse(html, headers={"Content-Disposition": f'attachment; filename="narrator-{rid}.html"', "Cache-Control": "no-cache"})

    @r.get("/api/by-ext/{kind}/{ext_id}")
    def by_ext(kind: str, ext_id: str):
        if not EXT_RE.match(f"{kind}:{ext_id}"):
            raise HTTPException(400, "bad id")
        with _db() as c:
            pid = store.by_ext(c, kind, ext_id)
            if pid is None:
                raise HTTPException(404, "no person carries this id yet")
            return store.summary(c, pid)

    @r.get("/api/merge/preview")
    def preview(a: int, b: int):
        with _db() as c:
            return merge_preview(c, a, b)

    @r.get("/api/queue")
    def review_queue(status: str = "unreviewed", kind: str = "", limit: int = 30, offset: int = 0):
        if status not in ("", "unreviewed", "approved", "rejected"):
            raise HTTPException(400, "bad status")
        with _db() as c:
            return store.queue(c, status, kind, max(1, min(limit, 100)), max(0, offset))

    @r.get("/api/revision/{rev}")
    def revision(rev: int):
        with _db() as c:
            row = c.execute("select * from revision where id=?", (rev,)).fetchone()
            if row is None:
                raise HTTPException(404)
            return store.rev_dict(c, row)

    @r.get("/api/suggestions")
    def suggestions(kind: str = "", status: str = "proposed", person: Optional[int] = None, limit: int = 30, offset: int = 0):
        if status not in ("", "proposed", "accepted", "dismissed"):
            raise HTTPException(400, "bad status")
        with _db() as c:
            return store.suggestions(c, kind, status, person, max(1, min(limit, 100)), max(0, offset))

    @r.get("/api/versions")
    def versions():
        with _db() as c:
            return dict(items=versions_list(c))

    @r.get("/api/version/{name}/suggestions")
    def vsuggestions(name: str, kind: str = "", status: str = "proposed", limit: int = 30, offset: int = 0):
        if status not in ("proposed", "merged", "rejected", "all"):
            raise HTTPException(400, "bad status")
        _vcheck(name)
        with _db() as c:
            return version_suggestions(c, name, kind, status, max(1, min(limit, 100)), max(0, offset))

    @r.get("/api/version/{name}/group")
    def vgroup(name: str, id: str):
        _vcheck(name)
        if not EXT_RE.match(id):
            raise HTTPException(400, "id must look like kind:id")
        with _db() as c:
            return group_payload(c, name, _vview(name), id)

    # -- write -----------------------------------------------------------------------------------
    def _fields(v) -> list:
        if not isinstance(v, list) or len(v) > 100 or not all(
                isinstance(x, list) and len(x) == 2 and x[0] in store.FIELDS and isinstance(x[1], (str, int)) for x in v):
            raise HTTPException(400, "fields must be [[field, value], ...] with known field names")
        return [(x[0], str(x[1])[:2000]) for x in v]

    def _ints(v, what: str) -> list:
        if not isinstance(v, list) or len(v) > 500 or not all(isinstance(x, int) and not isinstance(x, bool) for x in v):
            raise HTTPException(400, f"{what} must be a list of numbers")
        return v

    @r.post("/api/person")
    async def add(request: Request):
        u = need_user(request)
        b = await body(request)
        with _db(True) as c:
            pid = store.add_person(c, str(b.get("display_name", "")), _fields(b.get("fields", [])), u["username"], str(b.get("note", ""))[:1000])
            return dict(person=pid)

    @r.post("/api/person/{pid}")
    async def edit(request: Request, pid: int):
        u = need_user(request)
        b = await body(request)
        dn = b.get("display_name")
        if dn is not None and not isinstance(dn, str):
            raise HTTPException(400, "display_name must be text")
        with _db(True) as c:
            rev = store.edit_fields(c, pid, u["username"], dn, _fields(b.get("add", [])), _ints(b.get("retire", []), "retire"),
                                    str(b.get("note", ""))[:1000])
            return dict(revision=rev, person=store.resolve_id(c, pid))

    @r.post("/api/merge")
    async def merge(request: Request):
        u = need_user(request)
        b = await body(request)
        try:
            a, bb = int(b["a"]), int(b["b"])
            keep = int(b.get("keep") or a)
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "a, b (and keep) must be person numbers")
        with _db(True) as c:
            rev = store.merge(c, a, bb, keep, u["username"], str(b.get("note", ""))[:1000])
            return dict(revision=rev, person=store.resolve_id(c, keep))

    @r.post("/api/person/{pid}/split")
    async def split(request: Request, pid: int):
        u = need_user(request)
        b = await body(request)
        ext = b.get("ext", [])
        if not isinstance(ext, list) or not all(isinstance(x, str) and EXT_RE.match(x) for x in ext):
            raise HTTPException(400, "ext must be a list of kind:id")
        with _db(True) as c:
            new, rev = store.split(c, pid, ext, _ints(b.get("fields", []), "fields"), str(b.get("label", "")), u["username"],
                                   str(b.get("note", ""))[:1000])
            return dict(person=new, revision=rev)

    @r.post("/api/revision/{rev}/revert")
    async def revert(request: Request, rev: int):
        u = need_user(request, admin=True)
        b = await body(request)
        with _db(True) as c:
            return dict(revision=store.revert(c, rev, u["username"], str(b.get("note", ""))[:1000]))

    @r.post("/api/revision/{rev}/review")
    async def review(request: Request, rev: int):
        u = need_user(request, admin=True)
        b = await body(request)
        with _db(True) as c:
            return store.review(c, rev, str(b.get("status", "")), u["username"], bool(b.get("revert")), str(b.get("note", ""))[:1000])

    @r.post("/api/suggestion/{sid}")
    async def decide(request: Request, sid: int):
        u = need_user(request)
        b = await body(request)
        keep = b.get("keep")
        if keep is not None and not isinstance(keep, int):
            raise HTTPException(400, "keep must be a person number")
        with _db(True) as c:
            return store.decide_suggestion(c, sid, str(b.get("decision", "")), u["username"], keep, str(b.get("note", ""))[:1000])

    @r.post("/api/version/{name}/decide")
    async def vdecide(request: Request, name: str):
        u = need_user(request)
        b = await body(request)
        _vcheck(name, editable=True)
        a, bb, decision = b.get("a"), b.get("b"), b.get("decision")
        if not (isinstance(a, str) and isinstance(bb, str) and EXT_RE.match(a) and EXT_RE.match(bb)):
            raise HTTPException(400, "a and b must be external ids like noor:12")
        if decision not in ("accept", "reject", "clear"):
            raise HTTPException(400, "decision must be accept, reject or clear")
        note, kind = str(b.get("note", ""))[:1000], b.get("kind")

        def work():
            os.makedirs(_vdir(), exist_ok=True)
            with open(os.path.join(_vdir(), ".lock"), "a+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)                  # one decision + rebuild at a time
                try:
                    with _db(True) as c:
                        key = store.pair_key(a, bb)
                        before_view = _vview(name)
                        d0 = store.version_decision_for(c, name, *key)
                        before = before_view.pair_state(*key, d0) if before_view else dict(state="proposed", rule=None, conflict=None)
                        res = store.set_version_decision(c, name, a, bb, decision, u["username"], note,
                                                         kind if isinstance(kind, str) else None)
                        try:
                            version.rebuild(name, config.DB, _vdir())
                        except Exception as e:
                            store.undo_version_decision(c, res)
                            raise HTTPException(500, f"rebuild failed, decision rolled back: {e}")
                        view = _vview(name)
                        d1 = store.version_decision_for(c, name, *key)
                        after = view.pair_state(*key, d1)
                        g = view.group(key[0])
                        warn = None
                        if after["conflict"] == "manual_reject_ignored":
                            warn = "these two ids are one person in the wiki itself; the wiki's identity wins, the reject was not applied"
                        elif after["conflict"] == "manual_reject_transitive":
                            warn = "the two ids stay together through other merges in this version; only the direct edge was removed"
                        return dict(ok=True, version=name, pair=list(key), decision=decision, state_before=before["state"],
                                    state_after=after["state"], conflict=after["conflict"], warning=warn,
                                    group=g["members"] if g else [], built=view.meta.get("built"))
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
        return await run_in_threadpool(work)

    @r.post("/api/by-ext/{kind}/{ext_id}")
    async def ensure(request: Request, kind: str, ext_id: str):
        """Create the person of a source id that has none yet (a KG stub); recorded as a seed revision."""
        u = need_user(request)
        if kind not in ("noor", "kg") or not ext_id.isdigit():
            raise HTTPException(400, "only noor:<n> and kg:<n> can be created from their database")
        with _db(True) as c:
            return store.summary(c, store.ensure_ext(c, kind, ext_id, u["username"]))

    return r
