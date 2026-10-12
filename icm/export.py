"""One self-contained HTML file per step of a project (or the whole project): the wizard's own page with the
step's data, the full texts of its hadiths, the narrators' cards and the explanations inlined. It opens offline
(``file://``) and read-only: ``icm.js`` reads ``window.ICM_STATIC`` instead of calling the API.

    render(pid, run, what) -> html          what: seed | selection | alignment | identities | phylogeny | analysis | all
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List

from hadith_phylogeny.report import finite

from . import graph as graph_mod
from . import narrator_info, project, serve

STATIC = os.path.join(os.path.dirname(__file__), "static")
SITE = "https://ebadollah.ir"
VERSION = 1
STEPS = ["seed", "selection", "alignment", "identities", "phylogeny", "analysis"]
NEEDS = dict(seed=["search"], selection=["search"], alignment=["align", "search"], identities=["identify", "align", "search"],
             phylogeny=["graph"], analysis=["results", "viz", "graph", "align", "search"])
MAX_CARDS = 800
MAX_TEXTS = 600


def _asset(name: str) -> str:
    return open(os.path.join(STATIC, name), encoding="utf-8").read()


def _ids_of_identities(I: dict, dec: dict, G: dict = None) -> List[str]:
    """The people step 4 shows: fixed by the source, chosen, defaults and the top candidates."""
    ids: List[str] = []
    for ch in I.get("chains", []):
        for e in ch["elems"]:
            if e.get("exact"):
                ids.append(e["exact"])
            ids += [c["id"] for c in e.get("cands", [])[:3]]
    for cd in I.get("cards", {}).values():
        if cd.get("default"):
            ids.append(cd["default"])
        ids += [c["id"] for c in cd.get("candidates", [])[:3]]
    ids += [v for v in list((dec.get("choices") or {}).values()) + list((dec.get("overrides") or {}).values()) if isinstance(v, str)]
    ids += [x for m in dec.get("merges", []) for x in m]
    if G:
        ids += list(G.get("nodes", {}))
    seen, out = set(), []
    for i in ids:
        if i not in seen and (i.startswith("noor:") or i.startswith("kg:")):
            seen.add(i)
            out.append(i)
    return out


def collect(pid: str, run: str, what: str) -> dict:
    steps = STEPS if what == "all" else [what]
    want = sorted({s for st in steps for s in NEEDS[st]})
    stages = {s: serve.stage(pid, run, s) for s in want}
    stages = {k: v for k, v in stages.items() if v is not None}
    steps = [st for st in steps if all(s in stages for s in NEEDS[st][:1])]
    p = project.load(pid)
    vers = project.versions(pid)
    proj = dict(project=p, versions=vers, runs=[r for r in project.runs(pid) if r["run"] == run], job=None, last_job=None,
                log=(project.read(os.path.join(project.pdir(pid), "log.json"), []))[-30:],
                decisions={s: [dict(version=n, **{k: d.get(k) for k in ("user", "created", "note")})
                               for n in range(1, vers[s] + 1) for d in [project.decision(pid, s, n) or {}]] for s in project.STEPS})
    decisions = {s: project.decision(pid, s) or {} for s in project.STEPS}
    out: Dict = dict(v=VERSION, what=what, steps=steps, site=SITE, generated=time.strftime("%Y-%m-%d %H:%M"),
                     route=dict(pid=pid, run=run, step=steps[0] if steps else "seed"), project=proj, stages=stages,
                     decisions=decisions, deaths={}, hadith={}, narrators={})
    S, A, I, G = stages.get("search"), stages.get("align"), stages.get("identify"), stages.get("graph")
    # full texts: the witnesses (and, on step 2, the candidates whose text the search stage cut)
    wids: List[str] = []
    if A:
        wids += [w["wid"] for w in A["witnesses"]]
    if I:
        wids += [ch["wid"] for ch in I["chains"]]
    if G:
        wids += [a["wid"] for a in G["attestations"]]
    if S:
        wids.append(S["seed"]["wid"])
        if "selection" in steps:
            wids += [c["wid"] for c in S["candidates"] if c.get("matn_len", 0) > len(c.get("matn", ""))]
    wids = list(dict.fromkeys(wids))[:MAX_TEXTS]
    out["hadith"] = serve.hadiths(wids)
    # narrators: cards and the dates the databases give
    ids: List[str] = []
    if I:
        ids = _ids_of_identities(I, decisions.get("identities") or {}, G)
    elif G:
        ids = [n for n in G.get("nodes", {}) if n.startswith(("noor:", "kg:"))]
    ids = ids[:MAX_CARDS]
    if ids:
        out["deaths"] = graph_mod.known_deaths(ids)
        out["narrators"] = {i: narrator_info.card(i, entry_chars=600) for i in ids}
    return finite(out)


def render(pid: str, run: str, what: str) -> str:
    data = json.dumps(collect(pid, run, what), ensure_ascii=False).replace("</", "<\\/")
    page = _asset("icm.html").replace("__BASE__", "").replace("?v=__V__", "")
    for css in ("icm.css", "tree.css", "results.css"):
        page = page.replace(f'<link rel="stylesheet" href="/static/{css}">', f"<style>\n{_asset(css)}\n</style>")
    scripts = "".join(f'<script src="/static/{js}"></script>\n' for js in ("help_fa.js", "tree.js", "recon.js", "results.js", "icm.js"))
    inline = (f'<script id="icm-static" type="application/json">{data}</script>\n'
              "<script>window.ICM_STATIC = JSON.parse(document.getElementById('icm-static').textContent);</script>\n"
              + "".join(f"<script>\n{_asset(js)}\n</script>\n" for js in ("help_fa.js", "tree.js", "recon.js", "results.js", "icm.js")))
    if scripts not in page:
        raise RuntimeError("icm.html no longer lists the scripts export.py expects")
    page = page.replace(scripts, inline)
    page = page.replace("url('/q-static/", f"url('{SITE}/q-static/")             # the site's Lotus font (optional offline)
    page = page.replace('href="/h"', f'href="{SITE}/h"').replace('<a class="brand" href="">', f'<a class="brand" href="{SITE}/icm">')
    return page
