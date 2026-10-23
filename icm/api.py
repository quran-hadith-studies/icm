"""HTTP API and pages of the wizard, as a FastAPI router the host app mounts.

    app.include_router(make_router(get_user))

``get_user(request)`` returns ``{"username", "is_admin"}`` or None (the host's session). Everything is
readable by anyone; creating projects, deciding and re-running need a logged-in user, and changes to a
project need its owner or an admin. Writes must carry the header ``X-ICM: 1`` (a custom header cannot be
sent cross-site without a CORS pre-flight, which keeps cookie-authenticated writes safe from CSRF).
While ``config.OPEN`` is set, every visitor counts as an admin (anonymous ones as "guest").
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

from . import config, corpus, export, identity, jobs, narrator_info, narrator_versions, project, serve, viewer
from . import graph as graph_mod
from .text import norm

STATIC = os.path.join(os.path.dirname(__file__), "static")
ASSETS = ("icm.js", "base.js", "base.css", "icm.css", "tree.js", "tree.css", "results.js", "results.css", "recon.js", "help_fa.js", "icm.html",
          "model_worker.js", "model.zip")
ID_RE = re.compile(r"^(noor|kg):\d+$")
SPLIT_RE = re.compile(r"^split:\d{1,6}$")
NODE_RE = re.compile(r"^(?:(?:noor|kg):\d+|split:\d{1,6}|name:[^|]{1,200}|(?:rel|anon):[^|]{1,200}:\d+)$")   # any node a merge may name
PLACE_RE = re.compile(r"^[^|]{1,200}\|\d+$")
WID_RE = re.compile(r"^(v02|kg):\d+$")
STAGES = serve.STAGES
EXPORTS = ("seed", "selection", "alignment", "identities", "phylogeny", "analysis", "all")
MAX_OPEN = 20          # unfinished projects per (non-admin) user


def make_router(get_user: Callable[[Request], Optional[dict]], prefix: str = "/icm") -> APIRouter:
    r = APIRouter(prefix=prefix)
    if config.OPEN:
        host_user = get_user

        def get_user(request: Request) -> dict:
            u = host_user(request)
            return dict(username=u["username"] if u else "guest", is_admin=True)

    # -- helpers ----------------------------------------------------------------------------------
    def need_user(request: Request) -> dict:
        u = get_user(request)
        if not u:
            raise HTTPException(401, "login required")
        if request.method != "GET" and request.headers.get("x-icm") != "1":
            raise HTTPException(403, "missing X-ICM header")
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
        return dict(user=u["username"] if u else None, admin=bool(u and u.get("is_admin")), presets=list(config.PRESETS))

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
                    last_job=(jobs.for_project(pid, 1) or [None])[0], log=(project.read(os.path.join(project.pdir(pid), "log.json"), []))[-30:],
                    decisions={s: [dict(version=n, **{k: d.get(k) for k in ("user", "created", "note")})
                                   for n in range(1, vers[s] + 1) for d in [project.decision(pid, s, n) or {}]] for s in project.STEPS})

    @r.get("/api/project/{pid}/decision/{step}")
    def decision(pid: str, step: str, v: Optional[int] = None):
        _project(pid)
        if step not in project.STEPS:
            raise HTTPException(404)
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
        if len(open_) >= MAX_OPEN and not u.get("is_admin"):
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
        payload = _validate(step, body.get("payload") or {}, pid, p)
        if step == "parts":
            prev = project.decision(pid, "parts") or {}
            if payload["seed"] != prev.get("seed"):
                # a re-cut seed searches again: the witnesses selected now stay candidates, and the
                # «در جستجو» ticks (numbers of the old parts) no longer apply
                sel = project.decision(pid, "selection") or {}
                payload["keep_ids"] = sorted(set(sel.get("selected", [])) - {p["seed"]})
                sdec = project.decision(pid, "search") or {}
                if sdec.get("part_query") is not None:
                    project.add_decision(pid, "search", dict(part_query=None, extra_ids=sdec.get("extra_ids", [])),
                                         u["username"], "the seed's parts changed")
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

    def _validate(step: str, pl: dict, pid: str, p: dict) -> dict:
        if step == "search":
            pq = pl.get("part_query")
            extra = pl.get("extra_ids", [])
            if pq is not None and not (isinstance(pq, list) and all(isinstance(i, int) for i in pq)):
                raise HTTPException(400, "part_query must be a list of clause numbers")
            if not (isinstance(extra, list) and all(isinstance(w, str) and WID_RE.match(w) for w in extra)):
                raise HTTPException(400, "extra_ids must be witness ids")
            return dict(part_query=pq, extra_ids=extra[:50])
        if step == "selection":
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
            if pl.get("confirmed"):
                if pl["confirmed"] != p.get("graph_hash"):
                    raise HTTPException(409, "the graph changed; confirm the one on screen")
                out["confirmed"] = pl["confirmed"]
            return out
        raise HTTPException(400)

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
        out = dict(seed=None if seed == auto else seed, seed_confirmed=conf,
                   extras_added=[dict(wid=a["wid"], span=a["span"]) for a in added], extras_dropped=sorted(set(dropped)),
                   slots={k: v for k, v in slots.items() if v}, confirmed={k: sorted(set(v)) for k, v in confirmed.items()})
        for key, val in (("words", {k: {w: m for w, m in v.items() if m} for k, v in words.items()}), ("unit_cls", ucls),
                         ("renames", {k: dict(name=v["name"].strip(), term=v.get("term", "").strip()) for k, v in renames.items()}),
                         ("chain", chain_out), ("units", units_out)):
            val = {k: v for k, v in val.items() if v}
            if val:                                         # only then, so older decisions and their stage keys stay as they were
                out[key] = val
        return out

    return r


def wiki_persons(ids) -> dict:
    """{id: {person_id, name, also}} from the narrator wiki (/narrator) when its database is present, else {}."""
    try:
        from narwiki import lookup
        return lookup.persons_for(list(ids)) if lookup.available() else {}
    except Exception:                                   # the wiki is optional: ICM works without it
        return {}
