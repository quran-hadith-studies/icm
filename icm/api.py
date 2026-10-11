"""HTTP API and pages of the wizard, as a FastAPI router the host app mounts.

    app.include_router(make_router(get_user))

``get_user(request)`` returns ``{"username", "is_admin"}`` or None (the host's session). Everything is
readable by anyone; creating projects, deciding and re-running need a logged-in user, and changes to a
project need its owner or an admin. Writes must carry the header ``X-ICM: 1`` (a custom header cannot be
sent cross-site without a CORS pre-flight, which keeps cookie-authenticated writes safe from CSRF).
"""
from __future__ import annotations

import os
import re
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from hadith_phylogeny.report import finite

from . import config, corpus, jobs, narrator_info, project, viewer
from .text import norm

STATIC = os.path.join(os.path.dirname(__file__), "static")
ID_RE = re.compile(r"^(noor|kg):\d+$")
WID_RE = re.compile(r"^(v02|kg):\d+$")
STAGES = {"search", "align", "identify", "graph", "infer"}


def make_router(get_user: Callable[[Request], Optional[dict]], prefix: str = "/icm") -> APIRouter:
    r = APIRouter(prefix=prefix)

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
        html = open(os.path.join(STATIC, "icm.html"), encoding="utf-8").read().replace("__BASE__", prefix)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @r.get("", response_class=HTMLResponse)
    @r.get("/", response_class=HTMLResponse)
    @r.get("/p/{pid}", response_class=HTMLResponse)
    @r.get("/p/{pid}/r/{run}", response_class=HTMLResponse)
    def index_page(pid: str = "", run: str = ""):
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
        return dict(user=u["username"] if u else None, admin=bool(u and u.get("is_admin")), presets=list(config.PRESETS))

    @r.get("/api/index")
    def index():
        return project.read(os.path.join(config.CACHE_DIR, "index.json"), dict(projects=[]))

    @r.get("/api/hadith/{wid}/projects")
    def hadith_projects(wid: str):
        if not WID_RE.match(wid):
            raise HTTPException(400, "bad id")
        return project.projects_of(wid)

    @r.get("/api/hadith/{wid}")
    def hadith(wid: str):
        if not WID_RE.match(wid):
            raise HTTPException(400, "bad id")
        rec = corpus.get(wid)
        if rec is None:
            raise HTTPException(404, "not found")
        rec = {k: v for k, v in rec.items() if k != "rowid"}
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

    @r.get("/api/project/{pid}")
    def project_info(pid: str):
        p = _project(pid)
        vers = project.versions(pid)
        return dict(project=p, versions=vers, runs=project.runs(pid), job=jobs.active(pid),
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
        out = project.run_stage(pid, run, "infer")
        if out is None:
            raise HTTPException(404, "no analysis in this run")
        return HTMLResponse(viewer.render(out["viz"]))

    @r.get("/api/project/{pid}/runs/{run}/{stage}")
    def run_stage(pid: str, run: str, stage: str):
        _project(pid)
        if stage not in STAGES | {"viz", "results"}:
            raise HTTPException(404)
        src = "infer" if stage in ("viz", "results") else stage
        out = project.run_stage(pid, run, src)
        if out is None:
            raise HTTPException(404, "stage not available in this run")
        return finite(out["viz"] if stage == "viz" else out["results"] if stage == "results" else out)

    @r.get("/api/narrator/{nid}")
    def narrator(nid: str):
        return narrator_info.card(nid)

    # -- write -----------------------------------------------------------------------------------
    @r.post("/api/project")
    async def create(request: Request):
        u = need_user(request)
        body = await request.json()
        seed = str(body.get("seed", ""))
        if not WID_RE.match(seed) or corpus.get(seed) is None:
            raise HTTPException(400, "unknown seed hadith")
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
        n = project.add_decision(pid, step, payload, u["username"], str(body.get("note", ""))[:500])
        jobs.enqueue(pid)
        return dict(version=n)

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
            for d in (ch, ov):
                if not isinstance(d, dict) or not all(v in (None, "none") or (isinstance(v, str) and ID_RE.match(v)) for v in d.values()):
                    raise HTTPException(400, "choices must map to noor:<id>, kg:<id> or none")
            return dict(choices=ch, overrides=ov, merges=[])
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
            out = dict(dates=dates, chain_edits=edits, preset=preset)
            if pl.get("confirmed"):
                if pl["confirmed"] != p.get("graph_hash"):
                    raise HTTPException(409, "the graph changed; confirm the one on screen")
                out["confirmed"] = pl["confirmed"]
            return out
        raise HTTPException(400)

    return r
