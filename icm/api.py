"""HTTP API and pages of the wizard, as a FastAPI router the host app mounts.

    app.include_router(make_router(get_user))

``get_user(request)`` returns ``{"username", "is_admin"}`` or None (the host's session). Everything is
readable by anyone; creating projects, deciding and re-running need a logged-in user, and changes to a
project need its owner or an admin. Writes must carry the header ``X-ICM: 1`` (a custom header cannot be
sent cross-site without a CORS pre-flight, which keeps cookie-authenticated writes safe from CSRF).
While ``config.OPEN`` is set, every visitor counts as an admin (anonymous ones as "guest").
With ``config.ACCOUNTS`` the wizard ignores ``get_user`` and uses its own accounts (icm/users.py, cookie
``icm_sid``): /login, /register, /account, and /users (admins: approve, block, roles, quotas, activity).
"""
from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import secrets
import time
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from hadith_phylogeny.report import finite

from . import config, corpus, export, identity, jobs, narrator_info, narrator_versions, project, serve, users, viewer
from . import graph as graph_mod
from .text import norm

STATIC = os.path.join(os.path.dirname(__file__), "static")
ASSETS = ("icm.js", "users.js", "base.js", "base.css", "icm.css", "users.css", "tree.js", "tree.css", "results.js", "results.css", "recon.js", "help_fa.js", "icm.html",
          "model_worker.js", "model.zip")
ID_RE = re.compile(r"^(noor|kg):\d+$")
SPLIT_RE = re.compile(r"^split:\d{1,6}$")
NODE_RE = re.compile(r"^(?:(?:noor|kg):\d+|split:\d{1,6}|name:[^|]{1,200}|(?:rel|anon):[^|]{1,200}:\d+)$")   # any node a merge may name
PLACE_RE = re.compile(r"^[^|]{1,200}\|\d+$")
WID_RE = re.compile(r"^(v02|kg):\d+(~\d{1,2})?$")          # «<id>~k»: reference k inside a record (pointers.py)
STAGES = serve.STAGES
EXPORTS = ("seed", "selection", "alignment", "identities", "phylogeny", "analysis", "all")
MAX_OPEN = 20          # unfinished projects per (non-admin) user, unless the account has its own quota
COOKIE = "icm_sid"


def make_router(get_user: Callable[[Request], Optional[dict]], prefix: str = "/icm") -> APIRouter:
    r = APIRouter(prefix=prefix)
    if config.OPEN:
        host_user = get_user

        def get_user(request: Request) -> dict:
            u = host_user(request)
            return dict(username=u["username"] if u else "guest", is_admin=True)
    if config.ACCOUNTS:
        def get_user(request: Request) -> Optional[dict]:
            return users.user_for(request.cookies.get(COOKIE))

    # -- helpers ----------------------------------------------------------------------------------
    def need_user(request: Request) -> dict:
        u = get_user(request)
        if not u:
            raise HTTPException(401, "login required")
        if request.method != "GET" and request.headers.get("x-icm") != "1":
            raise HTTPException(403, "missing X-ICM header")
        if u.get("role") == "viewer" and request.method != "GET":
            raise HTTPException(403, "your account may only read")
        return u

    def own_project(request: Request, pid: str) -> tuple:
        u = need_user(request)
        p = _project(pid)
        if p["owner"] != u["username"] and not u.get("is_admin"):
            raise HTTPException(403, "only the owner or an admin may change this project")
        return u, p

    def helper_ok(request: Request) -> bool:
        tok = os.environ.get("EBAD_ICM_HELPER_TOKEN") or os.environ.get("ICM_HELPER_TOKEN") or ""
        got = request.headers.get("x-icm-helper", "")
        return bool(tok) and len(tok) >= 20 and hmac.compare_digest(tok.encode(), got.encode())

    def job_ok(request: Request, p: dict) -> bool:
        """The desktop app's one-job token (issued by /desktop-token, valid while the project waits for this key)."""
        aw = p.get("awaiting") or {}
        want, got = aw.get("desktop_hash") or "", request.headers.get("x-icm-job", "")
        return (p.get("status") == "awaiting" and len(want) == 64 and len(got) >= 20
                and hmac.compare_digest(want.encode(), hashlib.sha256(got.encode()).hexdigest().encode()))

    def _compute_input(pid: str, p: dict) -> dict:
        aw = p.get("awaiting")
        if p.get("status") != "awaiting" or not aw:
            raise HTTPException(409, "this project is not waiting for a computation")
        G = project.stage_get(pid, "graph", aw["graph_hash"])
        run = p.get("current_run")
        S = project.run_stage(pid, run, "search") if run else None
        if G is None or S is None:
            raise HTTPException(500, "stage files are missing")
        texts = {S["seed"]["wid"]: S["seed"]["matn"]}
        texts.update({c["wid"]: c["matn"] for c in S["candidates"]})
        used = {a["wid"] for a in G["attestations"]}
        from .build import model_zip
        return dict(pid=pid, key=aw["key"], graph_hash=aw["graph_hash"], preset=aw["preset"], seed=0, model=model_zip.digest(),
                    graph=finite(G), texts={w: t for w, t in texts.items() if w in used})

    def _accept_result(pid: str, p: dict, body: dict, provenance: dict) -> dict:
        aw = p.get("awaiting")
        if p.get("status") != "awaiting" or not aw or body.get("key") != aw["key"]:
            raise HTTPException(409, "the project no longer waits for this computation")
        res, viz = body.get("results"), body.get("viz")
        if not isinstance(res, dict) or not isinstance(viz, dict):
            raise HTTPException(400, "results and viz are required")
        G = project.stage_get(pid, "graph", aw["graph_hash"])
        post = res.get("posterior")
        ok = (isinstance(post, dict) and post and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in post.values())
              and abs(sum(post.values()) - 1) < 1e-3 and res.get("preset") == aw["preset"] and res.get("seed") == 0
              and res.get("n_narrators") == len(G["narrators"]) and res.get("map_source") in post
              and isinstance(res.get("log_Z"), dict) and set(res["log_Z"]) == set(post)
              and all(v is None or (isinstance(v, (int, float)) and math.isfinite(v)) for v in res["log_Z"].values())
              and "narrators" in viz and "edges" in viz and "source_posterior" in viz)
        if not ok:
            raise HTTPException(400, "the result is not consistent with this project's graph")
        res["provenance"] = provenance
        out = dict(results=res, viz=viz, stage_version=1, inputs_hash=aw["key"], generated=project.now())
        project.stage_put(pid, "infer", aw["key"], finite(out))
        jobs.enqueue(pid)                                   # the worker finalises (cheap: every stage is cached)
        return dict(ok=True)

    def _project(pid: str) -> dict:
        try:
            p = project.load(pid)
        except ValueError:
            p = None
        if not p:
            raise HTTPException(404, "no such project")
        return p

    # -- pages -----------------------------------------------------------------------------------
    def page() -> HTMLResponse:
        # Cloudflare caches /static for hours whatever our Cache-Control says: version the URLs instead
        v = "%x" % max(int(os.path.getmtime(os.path.join(STATIC, f))) for f in ASSETS)
        html = open(os.path.join(STATIC, "icm.html"), encoding="utf-8").read().replace("__BASE__", prefix).replace("__V__", v)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @r.get("", response_class=HTMLResponse)
    @r.get("/", response_class=HTMLResponse)
    @r.get("/new", response_class=HTMLResponse)
    @r.get("/p/{pid}", response_class=HTMLResponse)
    @r.get("/p/{pid}/{step}", response_class=HTMLResponse)
    @r.get("/p/{pid}/r/{run}", response_class=HTMLResponse)
    @r.get("/p/{pid}/r/{run}/{step}", response_class=HTMLResponse)
    @r.get("/login", response_class=HTMLResponse)
    @r.get("/register", response_class=HTMLResponse)
    @r.get("/account", response_class=HTMLResponse)
    @r.get("/users", response_class=HTMLResponse)
    def index_page(pid: str = "", run: str = "", step: str = ""):
        return page()

    @r.get("/static/{name}")
    def static(name: str):
        if name not in os.listdir(STATIC):
            raise HTTPException(404)
        return FileResponse(os.path.join(STATIC, name), headers={"Cache-Control": "no-cache"})

    @r.get("/static/fonts/{name}")
    def static_font(name: str):
        if name not in os.listdir(os.path.join(STATIC, "fonts")):
            raise HTTPException(404)
        return FileResponse(os.path.join(STATIC, "fonts", name), headers={"Cache-Control": "public, max-age=2592000"})

    # -- read ------------------------------------------------------------------------------------
    @r.get("/api/me")
    def me(request: Request):
        u = get_user(request)
        out = dict(user=u["username"] if u else None, admin=bool(u and u.get("is_admin")), presets=list(config.PRESETS))
        if config.ACCOUNTS:
            out.update(accounts=True, bot=users.bot_username(), name=u and u["name"], role=u and u["role"],
                       bale=u and u["bale"], quota=u and (u["quota"] if u["quota"] is not None else MAX_OPEN))
            if u and u["is_admin"]:
                out["pending"] = users.count_pending()
        return out

    @r.get("/api/index")
    def index():
        idx = project.read(os.path.join(config.CACHE_DIR, "index.json"), dict(projects=[]))
        # projects made before titles were readable carry their seed id as the title: name them from the hadith
        return dict(idx, projects=[dict(p, title=project.hadith_label(p["seed"])) if p.get("title") == p.get("seed") else p for p in idx.get("projects", [])])

    @r.get("/api/hadith/{wid}/projects")
    def hadith_projects(wid: str):
        if not WID_RE.match(wid):
            raise HTTPException(400, "bad id")
        return project.projects_of(wid)

    @r.get("/api/hadith/{wid}/tokens")
    def hadith_tokens(wid: str, pid: Optional[str] = None):
        """The whole text as words, with the places of a project's chains in it, for the step-4 chain editor."""
        if not WID_RE.match(wid):
            raise HTTPException(400, "bad id")
        if pid:
            _project(pid)
        out = serve.hadith_tokens(wid, pid)
        if out is None:
            raise HTTPException(404, "not found")
        return out

    @r.get("/api/hadith/{wid}/near")
    def hadith_near(wid: str, n: int = 4):
        """The hadiths around one in its book, with their own words: step 3 offers them as the text a reference points at."""
        from . import pointers
        if not WID_RE.match(wid) or "~" in wid:
            raise HTTPException(400, "bad id")
        n = max(1, min(n, 8))
        before, after = list(reversed(corpus.neighbours(wid, n, after=False))), corpus.neighbours(wid, n)
        recs = corpus.get_many(before + after)
        return [dict(wid=w, before=w in before, text=pointers.own_text(recs[w])[:600], ref=pointers.lone_ref(recs[w]) is not None)
                for w in before + after if w in recs]

    @r.post("/api/refs/build")
    async def refs_build(request: Request):
        """The text a template builds: {ref, tpl, arg, incipit} -> {text, note, mark, source} (source: the text referred to)."""
        from . import pointers
        b = await request.json()
        ref, tpl = b.get("ref"), b.get("tpl")
        if not (isinstance(ref, str) and WID_RE.match(ref) and tpl in pointers.TEMPLATES
                and all(isinstance(b.get(f, ""), str) and len(b.get(f, "")) <= 6000 for f in ("arg", "incipit", "at"))):
            raise HTTPException(400, "build needs {ref: <wid>, tpl, arg, incipit, at}")
        rec = corpus.get(ref)
        if rec is None:
            raise HTTPException(404, "not found")
        src = pointers.own_text(rec)
        return dict(pointers.build(src, tpl, b.get("arg", ""), b.get("incipit", ""), b.get("at", "")), source=src)

    @r.get("/api/hadith/{wid}")
    def hadith(wid: str):
        if not WID_RE.match(wid):
            raise HTTPException(400, "bad id")
        rec = serve.hadith(wid)
        if rec is None:
            raise HTTPException(404, "not found")
        rec["projects"] = project.projects_of(wid)
        return rec

    @r.get("/api/search")
    def find(q: str, limit: int = 20):
        q = (q or "").strip()
        m = re.search(r"(?:v02:|/h/i/(?:angel/)?|^)(\d{3,12})$", q)
        wids = []
        if m and re.fullmatch(r"(v02:|kg:)?\d+|.*/h/i/.*", q):
            n = int(m.group(1))
            wids = [f"v02:{n}", f"v02:{config.NOOR_OFFSET + n}"] if n < config.NOOR_OFFSET else [f"v02:{n}"]
            if q.startswith("kg:"):
                wids = [f"kg:{n}"]
        else:
            expr = corpus.fts_query([q])
            if expr:
                wids = [f"v02:{i}" for i in corpus.fts_v02(expr, 300)] + [f"kg:{i}" for i in corpus.fts_kg(expr, 100)]
        recs = corpus.get_many(wids[:400])
        qn = len(norm(q).split())
        out = []
        for w in wids:
            rec = recs.get(w)
            if rec:
                n = max(len(norm(rec["matn"]).split()), 1)
                out.append(dict(wid=w, title=rec["title"], author=rec["author"], tradition=rec["tradition"], vol=rec["vol"],
                                page=rec["page"], matn=rec["matn"][:400], projects=project.projects_of(w),
                                _r=min(qn / n, 1.0) if not m else 1.0))
        out.sort(key=lambda d: -d["_r"])            # the matns the query covers most come first
        for d in out:
            d.pop("_r")
        return out[:limit]

    @r.get("/api/narrator-versions")
    def narrator_version_list():
        """The narrator database versions a project may use (step 2): ``raw`` and every built version, with their precision figures."""
        return narrator_versions.available()

    def _version(name: Optional[str]) -> narrator_versions.Version:
        try:
            return narrator_versions.get(name)
        except KeyError:
            raise HTTPException(400, "unknown narrator database version")

    @r.get("/api/project/{pid}")
    def project_info(pid: str):
        p = _project(pid)
        vers = project.versions(pid)
        p = dict(p, label=(project.hadith_label(p["seed"]) if p.get("title") == p.get("seed") else p.get("title")))
        return dict(project=p, narrator_version=(project.decision(pid, "narrators") or {}).get("name") or narrator_versions.default_name(), versions=vers, runs=project.runs(pid), job=jobs.active(pid),
                    branches=project.branches(pid),
                    last_job=(jobs.for_project(pid, 1) or [None])[0], log=(project.read(os.path.join(project.pdir(pid), "log.json"), []))[-30:],
                    decisions={s: project.history(pid, s) for s in project.STEPS})

    @r.get("/api/project/{pid}/decision/{step}")
    def decision(pid: str, step: str, v: Optional[int] = None, run: Optional[str] = None):
        """The active version's decision; ``run``: the one that run was computed with (a read-only page of another version)."""
        _project(pid)
        if step not in project.STEPS:
            raise HTTPException(404)
        if v is None and run and run != "current":
            m = next((x for x in project.runs(pid) if x["run"] == run), None)
            if m is None:
                raise HTTPException(404, "no such run")
            v = int((m.get("versions") or {}).get(step) or 0)
            if not v:
                return {}
        return project.decision(pid, step, v) or {}

    @r.get("/api/project/{pid}/runs/{run}/viewer", response_class=HTMLResponse)
    def run_viewer(pid: str, run: str):
        _project(pid)
        v = serve.stage(pid, run, "viz")
        if v is None:
            raise HTTPException(404, "no analysis in this run")
        ctx = serve.page_context(pid, run)
        ctx["R"] = serve.stage(pid, run, "results")
        return HTMLResponse(viewer.render(dict(v, page_context=ctx)))

    @r.get("/api/project/{pid}/runs/{run}/export/{what}.html", response_class=HTMLResponse)
    def run_export(pid: str, run: str, what: str):
        """One self-contained HTML file: a step of the wizard (or all of them) with its data, texts and help."""
        p = _project(pid)
        if what not in EXPORTS:
            raise HTTPException(404)
        rid = p.get("current_run") if run == "current" else run
        if not rid or project.run_stage(pid, rid, "search") is None:
            raise HTTPException(404, "no such run")
        html = export.render(pid, rid, what)
        fn = f"icm-{pid}-{what}.html"
        return HTMLResponse(html, headers={"Content-Disposition": f'attachment; filename="{fn}"', "Cache-Control": "no-store"})

    @r.get("/api/project/{pid}/runs/{run}/{stage}")
    def run_stage(pid: str, run: str, stage: str):
        _project(pid)
        if stage not in STAGES | {"viz", "results"}:
            raise HTTPException(404)
        out = serve.stage(pid, run, stage)
        if out is None:
            raise HTTPException(404, "stage not available in this run")
        return finite(out)

    @r.get("/api/project/{pid}/runs/{run}/words/{wid}")
    def run_words(pid: str, run: str, wid: str):
        """The tokens a part's slot counts in (``align.text_tokens``), with their character ranges in ``matn`` and the
        witness's aligned window, for the segmentation editors of steps 2 and 3."""
        _project(pid)
        out = serve.words(pid, run, wid)
        if out is None:
            raise HTTPException(404, "no such text in this run")
        return out

    @r.get("/api/narrator/{nid}")
    def narrator(nid: str, version: Optional[str] = None):
        out = narrator_info.card(nid)
        V = _version(version)
        mem = V.members(nid) if ID_RE.match(nid) else []
        if mem:                                          # the ids the version treats as this person, with the rule that joined each
            out["merged"] = dict(version=V.name, members=mem, edges=V.edges(m["id"] for m in mem))
        w = wiki_persons([nid]).get(nid)
        if w:
            out["wiki"] = w                              # the person's page in the narrator wiki
        return out

    @r.get("/api/narrators/deaths")
    def narrator_deaths(ids: str = "", version: Optional[str] = None):
        """Death dates the databases give, for ``ids`` = comma-separated noor:/kg: ids (the step-4 page)."""
        nids = [i for i in ids.split(",") if ID_RE.match(i)][:3000]
        return graph_mod.known_deaths(nids, _version(version))

    @r.get("/api/narrators")
    def narrators(q: str, death: Optional[int] = None, version: Optional[str] = None):
        """Narrators of both databases whose name matches ``q`` (the step-4 search). With a narrator database
        ``version``, ids it treats as one person are one row (the best match, under the group's id, ``also`` the others)."""
        V = _version(version)
        q = (q or "").strip()[:80]
        if len(norm(q)) < 2:
            return []
        out = [dict(c, db="noor") for c in identity.noor_candidates(q, k=15)]
        out += [dict(c, db="kg") for c in identity.kg_candidates(q, death, k=15)]
        out.sort(key=lambda d: -d["score"])
        if not V.raw:
            db = {c["id"]: c["db"] for c in out}
            out = identity.collapse(out, V)
            for c in out:
                c["db"] = db.get(c["id"], c["db"])
        wiki = wiki_persons([c["id"] for c in out])
        if wiki:                    # one row per person of the narrator wiki; the other ids of that person go with it
            seen, keep = set(), []
            for c in out:
                w = wiki.get(c["id"])
                if w:
                    if w["person_id"] in seen:
                        continue
                    seen.add(w["person_id"])
                    c["wiki"] = w
                keep.append(c)
            out = keep
        return out[:25]

    # -- write -----------------------------------------------------------------------------------
    @r.post("/api/project")
    async def create(request: Request):
        u = need_user(request)
        body = await request.json()
        seed = str(body.get("seed", ""))
        if not WID_RE.match(seed) or corpus.get(seed) is None:
            raise HTTPException(400, "unknown seed hadith")
        open_ = [x for x in project.list_projects() if x["owner"] == u["username"] and x["status"] in ("draft", "running")]
        if len(open_) >= (MAX_OPEN if u.get("quota") is None else u["quota"]) and not u.get("is_admin"):
            raise HTTPException(429, f"you have {len(open_)} unfinished projects; finish or continue one first")
        p = project.create(seed, u["username"], body.get("title"))
        jobs.enqueue(p["pid"])
        return dict(pid=p["pid"])

    @r.post("/api/project/{pid}/decide/{step}")
    async def decide(request: Request, pid: str, step: str):
        u, p = own_project(request, pid)
        if step not in project.STEPS:
            raise HTTPException(404)
        body = await request.json()
        prev_search = project.decision(pid, "search") or {} if step == "search" else {}
        payload = _validate(step, body.get("payload") or {}, pid, p)
        if step == "search":
            # hadiths added now join a selection already made (they would otherwise be listed unticked), and a rebuilt search clears it
            sel = project.decision(pid, "selection") or {}
            new = (set(payload["extra_ids"]) | set(payload["seeds"])) - (set(prev_search.get("extra_ids") or []) | set(prev_search.get("seeds") or []))
            if payload.pop("_reset", False) and sel.get("selected"):
                project.add_decision(pid, "selection", dict(selected=[], reset=True), u["username"], "search rebuilt")
            elif new and sel.get("selected"):
                project.add_decision(pid, "selection", dict(selected=sorted(set(sel["selected"]) | new)), u["username"], "hadiths added")
        if step == "parts":
            prev = project.decision(pid, "parts") or {}
            if payload["seed"] != prev.get("seed"):
                # a re-cut seed searches again: the witnesses selected now stay candidates, and the
                # «در جستجو» ticks (numbers of the old parts) no longer apply
                sel = project.decision(pid, "selection") or {}
                payload["keep_ids"] = sorted(set(sel.get("selected", [])) - {p["seed"]})
                sdec = project.decision(pid, "search") or {}
                if sdec.get("part_query") is not None:
                    project.add_decision(pid, "search", dict(sdec, part_query=None), u["username"], "the seed's parts changed")
            else:
                payload["keep_ids"] = prev.get("keep_ids", [])
        n = project.add_decision(pid, step, payload, u["username"], str(body.get("note", ""))[:500])
        if step == "phylogeny" and payload.get("confirmed"):
            with project.lock(pid):                     # the next tab at once; the run that follows sets its own step
                q = project.load(pid)
                q["step"] = "analysis"
                project.save(q)
        jobs.enqueue(pid)
        return dict(version=n)

    @r.get("/api/project/{pid}/compute-input")
    def compute_input(request: Request, pid: str):
        p = _project(pid)
        if not helper_ok(request) and not job_ok(request, p):
            u = get_user(request)
            if not u or (p["owner"] != u["username"] and not u.get("is_admin")):
                raise HTTPException(401, "owner, admin, helper or job token required")
        return _compute_input(pid, p)

    @r.post("/api/project/{pid}/desktop-token")
    def desktop_token(request: Request, pid: str):
        """The page asks to hand this project's computation to the desktop app: a fresh token the app presents
        for this one job (it fetches the input and uploads the result without the user's login)."""
        u, p = own_project(request, pid)
        if p.get("status") != "awaiting" or not p.get("awaiting"):
            raise HTTPException(409, "this project is not waiting for a computation")
        tok = secrets.token_urlsafe(24)
        with project.lock(pid):                             # the project file is public (icm-cache): keep only the hash
            q = project.load(pid)
            q["awaiting"]["desktop_hash"] = hashlib.sha256(tok.encode()).hexdigest()
            project.save(q)
        return dict(pid=pid, token=tok, key=q["awaiting"]["key"])

    @r.post("/api/project/{pid}/compute-result")
    async def compute_result(request: Request, pid: str):
        p = _project(pid)
        body = await request.json()
        if helper_ok(request):
            prov = dict(kind="helper", **{k: body.get("provenance", {}).get(k) for k in ("workers", "seconds", "platform")})
        elif job_ok(request, p):
            prov = dict(kind="desktop", **{k: body.get("provenance", {}).get(k) for k in ("workers", "seconds", "platform", "app")})
        else:
            u, p = own_project(request, pid)
            prov = dict(kind="browser", user=u["username"], **{k: body.get("provenance", {}).get(k) for k in ("workers", "seconds", "agent")})
        for k, v in list(prov.items()):
            if isinstance(v, str):
                prov[k] = v[:120]
        return _accept_result(pid, p, body, prov)

    @r.post("/api/helper/claim")
    def helper_claim(request: Request):
        if not helper_ok(request):
            raise HTTPException(401, "helper token required")
        now = project.now()
        for p in sorted(project.list_projects(), key=lambda x: x["updated"]):
            aw = p.get("awaiting")
            if p.get("status") == "awaiting" and aw and aw.get("compute") == "helper":
                if aw.get("claimed") and aw["claimed"] > time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3 * 3600)):
                    continue                                # claimed within the last three hours
                with project.lock(p["pid"]):
                    q = project.load(p["pid"])
                    q["awaiting"]["claimed"] = now
                    project.save(q)
                return _compute_input(p["pid"], q)
        return JSONResponse(None)

    @r.post("/api/project/{pid}/rerun")
    async def rerun(request: Request, pid: str):
        own_project(request, pid)
        jobs.enqueue(pid)
        return dict(ok=True)

    # -- versions: named sets of decisions; the page edits the active one (project.py «versions») --
    def _bname(body: dict, default: str = "") -> str:
        name = re.sub(r"\s+", " ", str(body.get("name") or default)).strip()[:60]
        if not name:
            raise HTTPException(400, "a version needs a name")
        return name

    def _switch(pid: str, bid: str) -> None:
        if jobs.active(pid):
            raise HTTPException(409, "a computation is running for this project; switch versions when it is over")
        project.activate(pid, bid)
        project.refresh_indexes()

    def _bid(pid: str, bid: str) -> str:
        if not any(b["id"] == bid for b in project.branches(pid)["versions"]):
            raise HTTPException(404, "no such version")
        return bid

    @r.post("/api/project/{pid}/versions")
    async def version_add(request: Request, pid: str):
        """A new version copied from ``base`` (default: the active one), made active unless ``activate`` is false."""
        u, p = own_project(request, pid)
        body = await request.json()
        B = project.branches(pid)
        base = _bid(pid, str(body.get("base") or B["active"]))
        n = 1 + max(int(b["id"]) for b in B["versions"])
        bid = project.add_branch(pid, base, _bname(body, "نسخه‌ی " + str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))), u["username"])
        if body.get("activate", True):
            _switch(pid, bid)
        return dict(id=bid)

    @r.post("/api/project/{pid}/versions/{bid}/activate")
    async def version_activate(request: Request, pid: str, bid: str):
        own_project(request, pid)
        _switch(pid, _bid(pid, bid))
        return dict(ok=True)

    @r.post("/api/project/{pid}/versions/{bid}/rename")
    async def version_rename(request: Request, pid: str, bid: str):
        own_project(request, pid)
        project.rename_branch(pid, _bid(pid, bid), _bname(await request.json()))
        return dict(ok=True)

    @r.post("/api/project/{pid}/versions/{bid}/delete")
    async def version_delete(request: Request, pid: str, bid: str):
        own_project(request, pid)
        try:
            project.delete_branch(pid, _bid(pid, bid))
        except ValueError as e:
            raise HTTPException(409, str(e))
        return dict(ok=True)

    if config.ACCOUNTS:
        _accounts(r, prefix, get_user, need_user, helper_ok)

    def _validate(step: str, pl: dict, pid: str, p: dict) -> dict:
        if step == "search":
            return _validate_search(pl, pid, p)
        if step == "selection":
            if pl.get("reset"):                          # a rebuilt search: the ticks belong to candidates that are gone
                return dict(selected=[], reset=True)
            sel = pl.get("selected", [])
            S = project.run_stage(pid, "current", "search")
            known = {c["wid"] for c in (S or {}).get("candidates", [])} | {p["seed"]}
            for c in (S or {}).get("candidates", []):
                known.update(c.get("copies", []))
            if not (isinstance(sel, list) and sel and all(w in known for w in sel)):
                raise HTTPException(400, "selection must be a non-empty list of candidates of this project")
            return dict(selected=sorted(set(sel) | {p["seed"]}))
        if step == "identities":
            ch = pl.get("choices", {})
            ov = pl.get("overrides", {})
            if not isinstance(ch, dict) or not all(v in (None, "none") or (isinstance(v, str) and ID_RE.match(v)) for v in ch.values()):
                raise HTTPException(400, "choices must map to noor:<id>, kg:<id> or none")
            if not (isinstance(ov, dict) and len(ov) <= 3000 and all(isinstance(k, str) and PLACE_RE.match(k) for k in ov) and all(
                    v in (None, "none") or (isinstance(v, str) and (ID_RE.match(v) or SPLIT_RE.match(v))) for v in ov.values())):
                raise HTTPException(400, "overrides must map places to noor:<id>, kg:<id>, split:<n> or none")
            splits = pl.get("splits", {}) or {}
            if not (isinstance(splits, dict) and len(splits) <= 200 and all(
                    isinstance(k, str) and SPLIT_RE.match(k) and isinstance(v, dict) and isinstance(v.get("label", ""), str)
                    and len(v.get("label", "")) <= 120 and (v.get("from") is None or (isinstance(v["from"], str) and NODE_RE.match(v["from"])))
                    for k, v in splits.items())):
                raise HTTPException(400, "splits must map split:<n> to {label, from}")
            used = {v for v in ov.values() if isinstance(v, str) and SPLIT_RE.match(v)}
            splits = {k: dict(label=v.get("label", "").strip(), **({"from": v["from"]} if v.get("from") else {}))
                      for k, v in splits.items() if k in used}            # a split nobody is moved to is gone
            dates = pl.get("dates", {})
            if not (isinstance(dates, dict) and len(dates) <= 2000 and all(
                    isinstance(k, str) and len(k) <= 300 and (ID_RE.match(k) or SPLIT_RE.match(k) or k.startswith("occ:")) and isinstance(v, list) and len(v) == 2
                    and all(isinstance(x, (int, float)) and not isinstance(x, bool) and -100 <= x <= 1500 for x in v) and v[0] <= v[1]
                    for k, v in dates.items())):
                raise HTTPException(400, "dates must map noor:<id>, kg:<id> or occ:<place> to [lo, hi] hijri years")
            merges, drops, dismissed = pl.get("merges", []), pl.get("drops", []), pl.get("dismissed", [])
            if not (isinstance(merges, list) and len(merges) <= 500 and all(
                    isinstance(m, list) and len(m) == 2 and all(isinstance(x, str) and NODE_RE.match(x) for x in m) and m[0] != m[1]
                    for m in merges)):
                raise HTTPException(400, "merges must be pairs of distinct node ids (noor:, kg:, split:, name:, rel:, anon:)")
            if not (isinstance(drops, list) and len(drops) <= 2000 and all(isinstance(x, str) and PLACE_RE.match(x) for x in drops)):
                raise HTTPException(400, "drops must be places <aid>|<i>")
            if not (isinstance(dismissed, list) and len(dismissed) <= 3000 and all(isinstance(x, str) and len(x) <= 300 for x in dismissed)):
                raise HTTPException(400, "dismissed must be suggestion ids")
            out = dict(choices=ch, overrides=ov, merges=merges, drops=sorted(set(drops)), dismissed=sorted(set(dismissed)), dates=dates)
            if splits:
                out["splits"] = splits
            return out
        if step == "parts":
            return _validate_parts(pl, pid)
        if step == "narrators":
            name = pl.get("version")
            if not (isinstance(name, str) and any(v["name"] == name for v in narrator_versions.available())):
                raise HTTPException(400, "unknown narrator database version")
            return dict(name=name)                       # not "version": add_decision stamps that key with the decision number
        if step == "phylogeny":
            dates = pl.get("dates", {})
            edits = pl.get("chain_edits", {})
            if not (isinstance(dates, dict) and all(isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v) for v in dates.values())):
                raise HTTPException(400, "dates must map to [lo, hi]")
            if not (isinstance(edits, dict) and all(isinstance(v, list) and all(isinstance(x, str) for x in v) for v in edits.values())):
                raise HTTPException(400, "chain_edits must map to lists of node ids")
            preset = pl.get("preset")
            if preset is not None and preset not in config.PRESETS:
                raise HTTPException(400, "unknown preset")
            compute = pl.get("compute", "browser")
            if compute not in ("browser", "helper", "server"):
                raise HTTPException(400, "unknown compute mode")
            out = dict(dates=dates, chain_edits=edits, preset=preset, compute=compute)
            if pl.get("build"):                          # step 5 orders the tree of exactly this graph key
                if pl["build"] != p.get("graph_hash"):
                    raise HTTPException(409, "the graph changed; build the one on screen")
                out["build"] = pl["build"]
            if pl.get("deleted"):                        # step 5 drops the tree on screen (only while it is not confirmed)
                prev = project.decision(pid, "phylogeny") or {}
                if pl["deleted"] != p.get("graph_hash"):
                    raise HTTPException(409, "the graph changed; delete the one on screen")
                if prev.get("confirmed") == pl["deleted"] or p.get("step") not in ("phylogeny",):
                    raise HTTPException(409, "the tree is already confirmed; it cannot be deleted")
                out["deleted"] = pl["deleted"]
            if pl.get("confirmed"):
                if pl["confirmed"] != p.get("graph_hash"):
                    raise HTTPException(409, "the graph changed; confirm the one on screen")
                out["confirmed"] = pl["confirmed"]
            return out
        raise HTTPException(400)

    def _validate_search(pl: dict, pid: str, p: dict) -> dict:
        """The search decision is the full state: ``part_query`` (parts asked in the corpora), ``extra_ids`` (hadiths the user added),
        ``seeds`` (further seed hadiths), ``edits`` (wid -> the user's text) and ``ret``, the parameters of the last press of
        «جستجو» (the server writes it: ``go`` is the press, ``mode`` what to do with the earlier candidates)."""
        prev = project.decision(pid, "search") or {}
        pq = pl.get("part_query")
        if pq is not None and not (isinstance(pq, list) and all(isinstance(i, int) and not isinstance(i, bool) for i in pq)):
            raise HTTPException(400, "part_query must be a list of clause numbers")
        wl = lambda x, n: isinstance(x, list) and len(x) <= n and all(isinstance(w, str) and WID_RE.match(w) and "~" not in w for w in x)
        extra, seeds = pl.get("extra_ids", []), pl.get("seeds", [])
        if not (wl(extra, 200) and wl(seeds, 20)):
            raise HTTPException(400, "extra_ids and seeds must be hadith ids")
        extra = sorted(set(extra) - set(seeds) - {p["seed"]})
        seeds = sorted(set(seeds) - {p["seed"]})
        for w in set(extra) | set(seeds):
            if w not in (prev.get("extra_ids") or []) and w not in (prev.get("seeds") or []) and corpus.get(w) is None:
                raise HTTPException(400, f"unknown hadith {w}")
        edits = pl.get("edits", {}) or {}
        if not (isinstance(edits, dict) and len(edits) <= 200 and all(
                isinstance(w, str) and WID_RE.match(w) and "~" not in w and w != p["seed"] and isinstance(t, str) and 0 < len(t.strip()) <= 20000
                for w, t in edits.items())):
            raise HTTPException(400, "edits must map hadith ids to their text (not the seed's)")
        out = dict(part_query=pq, extra_ids=extra, seeds=seeds)
        if edits:
            out["edits"] = {w: t.strip() for w, t in edits.items()}
        S = project.run_stage(pid, "current", "search")
        pdec = project.decision(pid, "parts") or {}
        ret = prev.get("ret")
        if ret is None and not p.get("manual_search"):
            # a project made before the button: from now on the corpora are asked again only when «جستجو» is pressed; what the
            # search on screen was made from is what the page showed (the same key, so nothing is computed again)
            cl = pdec.get("seed") or None
            ret = dict(legacy=True, n=0, clauses=cl, part_query=prev.get("part_query"), extra=sorted(set(prev.get("extra_ids") or [])),
                       keep=sorted(set(pdec.get("keep_ids") or [])) if cl else [], seeds=[])
        if pl.get("go"):
            mode = pl.get("mode") or "rebuild"
            if mode not in ("rebuild", "selected", "all"):
                raise HTTPException(400, "mode must be rebuild, selected or all")
            keep: list = []
            if S is not None and S.get("searched") is not False:
                if mode == "all":
                    keep = [w for c in S["candidates"] for w in [c["wid"], *c.get("copies", [])]]
                elif mode == "selected":
                    keep = list((project.decision(pid, "selection") or {}).get("selected") or [])
            cl = pdec.get("seed") or None
            ret = dict(legacy=False, n=(ret or {}).get("n", 0) + 1, clauses=cl, part_query=pq, extra=[],
                       keep=sorted(set(keep) - {p["seed"]})[:5000], seeds=seeds)
            if mode == "rebuild":
                out["_reset"] = True
        if ret is not None:
            out["ret"] = ret
        return out

    def _validate_parts(pl: dict, pid: str) -> dict:
        S = project.run_stage(pid, "current", "search")
        if S is None:
            raise HTTPException(409, "the search has not run yet")
        n = len(S["tokens"])
        auto = S.get("auto_clauses") or [c["span"] for c in S["clauses"]]

        def spans(x, top):
            ok = isinstance(x, list) and 0 < len(x) <= 300 and all(
                isinstance(v, list) and len(v) == 2 and all(isinstance(t, int) and not isinstance(t, bool) for t in v) and 0 <= v[0] < v[1] <= top
                for v in x)
            return ok and all(a[1] <= b[0] for a, b in zip(x, x[1:]))

        seed, conf = pl.get("seed"), pl.get("seed_confirmed")
        for x in (seed, conf):
            if x is not None and not spans(x, n):
                raise HTTPException(400, f"the seed's parts must be ordered, non-overlapping [start, end) token spans within {n} tokens")
        added, dropped = pl.get("extras_added", []) or [], pl.get("extras_dropped", []) or []
        if not (isinstance(added, list) and len(added) <= 50 and all(
                isinstance(a, dict) and isinstance(a.get("wid"), str) and WID_RE.match(a["wid"]) and spans([a.get("span")], 5000) for a in added)):
            raise HTTPException(400, "extras_added must be {wid, span: [start, end)}")
        key_ok = lambda k: isinstance(k, str) and 2 < len(k) <= 600 and k[:2] in ("s:", "x:")
        if not (isinstance(dropped, list) and len(dropped) <= 300 and all(key_ok(k) for k in dropped)):
            raise HTTPException(400, "extras_dropped must be part keys")
        slots, confirmed = pl.get("slots", {}) or {}, pl.get("confirmed", {}) or {}
        if not (isinstance(slots, dict) and len(slots) <= 300 and sum(len(v) for v in slots.values() if isinstance(v, dict)) <= 6000 and all(
                key_ok(k) and isinstance(v, dict) and all(isinstance(w, str) and WID_RE.match(w) and (x in ("absent", "unknown") or spans([x], 5000))
                                                          for w, x in v.items()) for k, v in slots.items())):
            raise HTTPException(400, "slots must map a part key to {wid: [start, end) | absent | unknown}")
        if not (isinstance(confirmed, dict) and len(confirmed) <= 300 and all(
                key_ok(k) and isinstance(v, list) and len(v) <= 2000 and all(isinstance(w, str) and WID_RE.match(w) for w in v) for k, v in confirmed.items())):
            raise HTTPException(400, "confirmed must map a part key to the witnesses it was checked in")
        tok_ok = lambda t: isinstance(t, str) and 2 < len(t) <= 200 and "#" in t and t.rsplit("#", 1)[1].isdigit()   # "<word>#<nth time in the row>"
        words, ucls = pl.get("words", {}) or {}, pl.get("unit_cls", {}) or {}
        edit_ok = lambda x: x == "add" or (isinstance(x, dict) and set(x) == {"to"} and isinstance(x["to"], list) and len(x["to"]) == 2
                                           and isinstance(x["to"][0], str) and WID_RE.match(x["to"][0]) and tok_ok(x["to"][1]))
        if not (isinstance(words, dict) and len(words) <= 300 and all(
                key_ok(k) and isinstance(v, dict) and len(v) <= 500 and all(
                    isinstance(w, str) and WID_RE.match(w) and isinstance(m, dict) and len(m) <= 200
                    and all(tok_ok(t) and edit_ok(x) for t, x in m.items()) for w, m in v.items()) for k, v in words.items())
                and sum(len(m) for v in words.values() for m in v.values()) <= 6000):
            raise HTTPException(400, "words must map a part key to {wid: {<word>#<n>: add | {to: [wid, <word>#<n>]}}}")
        if not (isinstance(ucls, dict) and len(ucls) <= 300 and all(
                key_ok(k) and isinstance(v, dict) and len(v) <= 200 and all(
                    isinstance(t, str) and 0 < len(t) <= 300 and x in ("addition", "omission") for t, x in v.items()) for k, v in ucls.items())):
            raise HTTPException(400, "unit_cls must map a part key to {<words of the unit>: addition | omission}")
        units_in, units_out = pl.get("units", {}) or {}, {}
        bad_units = HTTPException(400, "units must map a part key to {<unit key>: {drop, keep, cls, states: {wid: label | null}, def: {kind, anchors: {wid: [<word>#<n>, <word>#<n>]}, text}}}")
        if not (isinstance(units_in, dict) and len(units_in) <= 300):
            raise bad_units
        for k, m in units_in.items():
            if not (key_ok(k) and isinstance(m, dict) and len(m) <= 200):
                raise bad_units
            one = {}
            for uk, d in m.items():
                if not (isinstance(uk, str) and 0 < len(uk) <= 400 and isinstance(d, dict) and set(d) <= {"drop", "keep", "cls", "states", "def"}):
                    raise bad_units
                manual = re.fullmatch(r"m:\d{1,6}", uk) is not None
                if manual != ("def" in d) or (d.get("drop") and d.get("keep")) or any(f in d and not isinstance(d[f], bool) for f in ("drop", "keep")):
                    raise bad_units
                if "cls" in d and d["cls"] not in ("addition", "omission"):
                    raise bad_units
                st = d.get("states") or {}
                if not (isinstance(st, dict) and len(st) <= 2000 and all(
                        isinstance(w, str) and WID_RE.match(w) and (v is None or (isinstance(v, str) and 0 < len(v) <= 200)) for w, v in st.items())):
                    raise bad_units
                e = {f: True for f in ("drop", "keep") if d.get(f)}
                if d.get("cls"):
                    e["cls"] = d["cls"]
                if st:
                    e["states"] = st
                if manual:
                    df = d["def"]
                    an = df.get("anchors") if isinstance(df, dict) else None
                    if not (isinstance(df, dict) and set(df) <= {"kind", "anchors", "text"} and df.get("kind") in ("presence", "lexical")
                            and isinstance(df.get("text"), str) and 0 < len(df["text"].strip()) <= 300
                            and isinstance(an, dict) and 0 < len(an) <= 50 and all(
                                isinstance(w, str) and WID_RE.match(w) and isinstance(v, list) and len(v) == 2 and all(tok_ok(t) for t in v)
                                for w, v in an.items())):
                        raise bad_units
                    e["def"] = dict(kind=df["kind"], anchors={w: list(v) for w, v in an.items()}, text=df["text"].strip())
                if e:
                    one[uk] = e
            if one:
                units_out[k] = one
        if sum(len(m) for m in units_out.values()) > 3000:
            raise bad_units
        renames = pl.get("renames", {}) or {}
        if not (isinstance(renames, dict) and len(renames) <= 2000 and all(
                isinstance(k, str) and PLACE_RE.match(k) and isinstance(v, dict) and isinstance(v.get("name"), str) and 0 < len(v["name"].strip()) <= 200
                and isinstance(v.get("term", ""), str) and len(v.get("term", "")) <= 200 for k, v in renames.items())):
            raise HTTPException(400, "renames must map a place <aid>|<i> to {name, term}")
        chain, chain_out = pl.get("chain", {}) or {}, {}
        bad_chain = HTTPException(400, "chain must map a chain id to {span: {<i>: {t, a, b}}, add: [{t, a, b}], drop: [<i>], term: {<i>: [t, e]}} within the text's tokens")
        if not (isinstance(chain, dict) and len(chain) <= 2000):
            raise bad_chain
        int_ok = lambda x: isinstance(x, int) and not isinstance(x, bool) and x >= 0
        for aid, ed in chain.items():
            if not (isinstance(aid, str) and 0 < len(aid) <= 200 and "|" not in aid and WID_RE.match(aid.split("#")[0])
                    and isinstance(ed, dict) and set(ed) <= {"span", "add", "drop", "term"}):
                raise bad_chain
            rec = corpus.get(aid.split("#")[0])
            if rec is None:
                raise bad_chain
            top = len(identity.hadith_words(rec)[0])
            sp, add, drop, term = ed.get("span") or {}, ed.get("add") or [], ed.get("drop") or [], ed.get("term") or {}
            if not (isinstance(sp, dict) and len(sp) <= 200 and all(isinstance(k, str) and k.isdigit() and identity._span_ok(v, top) for k, v in sp.items())
                    and isinstance(add, list) and len(add) <= 50 and all(identity._span_ok(v, top) for v in add)
                    and isinstance(drop, list) and len(drop) <= 250 and all(int_ok(i) for i in drop)
                    and isinstance(term, dict) and len(term) <= 250 and all(isinstance(k, str) and k.isdigit() and isinstance(v, list) and len(v) == 2
                                                                             and all(int_ok(x) for x in v) and v[0] <= v[1] <= top for k, v in term.items())):
                raise bad_chain
            kept = sorted([v for k, v in sp.items() if int(k) not in drop] + [v for k, v in enumerate(add) if identity.ADDED_BASE + k not in drop],
                          key=lambda v: (v["a"], v["b"]))
            if any(x["b"] > y["a"] for x, y in zip(kept, kept[1:])):          # two places on one word
                raise HTTPException(400, "chain: two places of a chain cannot share a word")
            one = {}
            if sp:
                one["span"] = {k: dict(t=v["t"], a=v["a"], b=v["b"]) for k, v in sp.items()}
            if add:
                one["add"] = [dict(t=v["t"], a=v["a"], b=v["b"]) for v in add]
            if drop:
                one["drop"] = sorted(set(drop))
            if term:
                one["term"] = {k: [v[0], v[1]] for k, v in term.items()}
            if one:
                chain_out[aid] = one
        from . import pointers
        refs, refs_out = pl.get("refs", {}) or {}, {}
        bad_refs = HTTPException(400, "refs must map a witness to {ref, tpl, arg, incipit, at, text, approx, ok}")
        if not (isinstance(refs, dict) and len(refs) <= 1000):
            raise bad_refs
        for w, d in refs.items():                       # step 3: the text a reference («مثله / نحوه …») stands for
            if not (isinstance(w, str) and WID_RE.match(w) and isinstance(d, dict) and set(d) <= {"ref", "tpl", "arg", "incipit", "at", "text", "approx", "ok"}
                    and (d.get("ref") is None or (isinstance(d["ref"], str) and WID_RE.match(d["ref"])))
                    and d.get("tpl", "copy") in pointers.TEMPLATES
                    and all(isinstance(d.get(f, ""), str) and len(d.get(f, "")) <= 6000 for f in ("arg", "incipit", "at", "text"))
                    and all(isinstance(d.get(f, False), bool) for f in ("approx", "ok"))):
                raise bad_refs
            e = {f: d[f] for f in ("ref", "tpl", "arg", "incipit", "at", "text", "approx", "ok") if f in d}
            if e.get("text", "").strip():
                e["text"] = e["text"].strip()
                refs_out[w] = e
        out = dict(seed=None if seed == auto else seed, seed_confirmed=conf,
                   extras_added=[dict(wid=a["wid"], span=a["span"]) for a in added], extras_dropped=sorted(set(dropped)),
                   slots={k: v for k, v in slots.items() if v}, confirmed={k: sorted(set(v)) for k, v in confirmed.items()})
        for key, val in (("words", {k: {w: m for w, m in v.items() if m} for k, v in words.items()}), ("unit_cls", ucls),
                         ("renames", {k: dict(name=v["name"].strip(), term=v.get("term", "").strip()) for k, v in renames.items()}),
                         ("chain", chain_out), ("units", units_out), ("refs", refs_out)):
            val = {k: v for k, v in val.items() if v}
            if val:                                         # only then, so older decisions and their stage keys stay as they were
                out[key] = val
        return out

    return r


# -- accounts (config.ACCOUNTS) ---------------------------------------------------------------------
_fails: dict = {}          # (ip or username) -> recent failed logins, for a crude brake on password guessing


def _accounts(r: APIRouter, prefix: str, get_user: Callable, need_user: Callable, helper_ok: Callable) -> None:
    def body_of(request: Request, data) -> dict:
        if request.headers.get("x-icm") != "1":
            raise HTTPException(403, "missing X-ICM header")
        return data if isinstance(data, dict) else {}

    def run(f, *a):
        try:
            return f(*a)
        except users.UserError as e:
            raise HTTPException(e.status, str(e))

    def braked(*keys) -> bool:
        now = time.time()
        return any(len([t for t in _fails.get(k, []) if now - t < 900]) >= 10 for k in keys)

    def failed(*keys) -> None:
        now = time.time()
        for k in keys:
            _fails[k] = [t for t in _fails.get(k, []) if now - t < 900][-20:] + [now]

    def need_admin(request: Request) -> dict:
        u = need_user(request)
        if not u.get("is_admin"):
            raise HTTPException(403, "admins only")
        return u

    @r.post("/api/auth/register")
    async def register(request: Request):
        b = body_of(request, await request.json())
        return run(users.register, b.get("username"), b.get("name"), b.get("password"))

    @r.get("/api/auth/link-status")
    def link_status(code: str = ""):
        return run(users.link_status, code)

    @r.post("/api/auth/login")
    async def login(request: Request):
        b = body_of(request, await request.json())
        ip = "ip:" + (request.client.host if request.client else "?")
        name = "u:" + str(b.get("username", "")).strip().lower()
        if braked(ip, name):
            raise HTTPException(429, "تلاش‌های نادرست زیاد بود؛ ربع ساعت دیگر دوباره امتحان کنید.")
        try:
            out = users.login(b.get("username"), b.get("password"))
        except users.UserError as e:
            if e.status == 401:
                failed(ip, name)
            raise HTTPException(e.status, str(e))
        if "need_bale" in out:
            return out
        resp = JSONResponse(dict(user=out["user"]))
        resp.set_cookie(COOKIE, out["sid"], max_age=users.SESSION_TTL, path=prefix or "/", httponly=True, samesite="lax",
                        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
        return resp

    @r.post("/api/auth/logout")
    def logout(request: Request):
        users.logout(request.cookies.get(COOKIE, ""))
        resp = JSONResponse(dict(ok=True))
        resp.delete_cookie(COOKIE, path=prefix or "/")
        return resp

    @r.post("/api/auth/password")
    async def password(request: Request):
        b = body_of(request, await request.json())
        u = get_user(request)                               # not need_user: a viewer may change their password too
        if not u:
            raise HTTPException(401, "login required")
        run(users.change_password, u["username"], b.get("old"), b.get("new"))
        return dict(ok=True)

    @r.post("/api/auth/forgot")
    async def forgot(request: Request):
        b = body_of(request, await request.json())
        run(users.send_reset, b.get("username"))
        return dict(ok=True)

    @r.post("/api/auth/reset")
    async def reset(request: Request):
        b = body_of(request, await request.json())
        run(users.reset_password, b.get("username"), b.get("code"), b.get("password"))
        return dict(ok=True)

    @r.post("/api/auth/bale")
    async def bale(request: Request):
        """The Bale bot (ebadollah's bale_bot.py) passes on «/start icm-…»; authorised by the helper token."""
        if not helper_ok(request):
            raise HTTPException(401, "helper token required")
        b = await request.json()
        return run(users.confirm_bale, str(b.get("code", "")), b.get("chat_id"), b.get("handle"))

    @r.get("/api/users")
    def list_users(request: Request):
        need_admin(request)
        return dict(users=users.list_users(project.list_projects()), default_quota=MAX_OPEN, roles=list(users.ROLES))

    @r.post("/api/users/{username}")
    async def user_action(request: Request, username: str):
        u = need_admin(request)
        b = body_of(request, await request.json())
        return run(users.admin_action, u["username"], username, str(b.get("action", "")), b.get("value"))


def wiki_persons(ids) -> dict:
    """{id: {person_id, name, also}} from the narrator wiki (/narrator) when its database is present, else {}."""
    try:
        from narwiki import lookup
        return lookup.persons_for(list(ids)) if lookup.available() else {}
    except Exception:                                   # the wiki is optional: ICM works without it
        return {}
