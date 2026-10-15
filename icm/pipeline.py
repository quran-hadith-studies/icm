"""Run the stages of a project until a decision from the user is needed.

    advance(pid, progress=None) -> project dict (status/step updated)

Stages are cached by the hash of their inputs (``project.stage_put``); a repeated call recomputes nothing.
The wizard order is: search -> [selection] -> alignment + identities (candidates) -> [identities]
-> graph -> [phylogeny, confirmed for exactly this graph] -> analysis. The ``parts`` decision (the user's
segmentation: the seed's parts, extra parts, slots in witnesses) never blocks: it changes search and alignment
when it is made, and its confirmations are read by the pages only.
"""
from __future__ import annotations

import time
import traceback
from typing import Callable, Dict, Optional

from . import STAGE_VERSIONS, align as align_mod, config, corpus, graph as graph_mod, identity, infer, project, search as search_mod
from . import suggest as suggest_mod
from .project import h


def _get_or_run(pid: str, stage: str, key: str, fn, stage_hashes: Dict[str, str]):
    out = project.stage_get(pid, stage, key)
    stage_hashes[stage] = key
    if out is not None:
        return out
    t0 = time.time()
    project.log(pid, stage, "start")
    out = fn()
    out["stage_version"] = STAGE_VERSIONS.get(stage, 1)
    out["inputs_hash"] = key
    out["generated"] = project.now()
    project.stage_put(pid, stage, key, out)
    project.log(pid, stage, f"done in {time.time() - t0:.1f}s")
    return out


def _set(pid: str, **kw) -> dict:
    with project.lock(pid):
        p = project.load(pid)
        p.update(kw)
        project.save(p)
    project.refresh_indexes()
    return p


def advance(pid: str, progress: Optional[Callable[[str, float, str], None]] = None) -> dict:
    def say(stage, frac, msg=""):
        if progress:
            progress(stage, frac, msg)

    p = project.load(pid)
    vers = project.versions(pid)
    hashes: Dict[str, str] = {}
    try:
        _set(pid, status="running")
        # --- search ----------------------------------------------------------------------------
        sdec = project.decision(pid, "search") or {}
        pdec = project.decision(pid, "parts") or {}               # the user's segmentation (steps 2 and 3)
        sparams = dict(part_query=sdec.get("part_query"))
        extra = list(sdec.get("extra_ids", []))
        keep: list = []
        if pdec.get("seed"):                                      # only then, so automatic searches keep their keys
            sparams["clauses"] = pdec["seed"]
            keep = sorted(set(pdec.get("keep_ids") or []))
        k_search = h("search", p["seed"], sparams, sorted(extra), *([keep] if keep else []), STAGE_VERSIONS["search"], config.DATA_VERSION)
        say("search", 0.0, "searching the corpora")
        S = _get_or_run(pid, "search", k_search, lambda: search_mod.run(p["seed"], sparams, extra, keep), hashes)
        seldec = project.decision(pid, "selection")
        if not seldec:
            return _finish(pid, vers, hashes, "draft", "selection")
        selected = sorted(set(seldec.get("selected", [])) - {p["seed"]})
        # --- alignment + identity candidates --------------------------------------------------------
        say("align", 0.2, "aligning")
        parts_in = {k: pdec[k] for k in ("extras_dropped", "extras_added", "slots") if pdec.get(k)}
        k_align = h("align", k_search, selected, STAGE_VERSIONS["align"], *([parts_in] if parts_in else []))
        A = _get_or_run(pid, "align", k_align, lambda: align_mod.run(S, selected, parts_in), hashes)
        say("identify", 0.4, "finding the narrators")
        k_ident = h("identify", k_search, selected, STAGE_VERSIONS["identify"], config.DATA_VERSION)
        I = _get_or_run(pid, "identify", k_ident, lambda: _identify(S, A), hashes)
        iddec = project.decision(pid, "identities")
        if not iddec:
            return _finish(pid, vers, hashes, "draft", "identities")
        # --- graph -------------------------------------------------------------------------------
        phdec = project.decision(pid, "phylogeny") or {}
        say("graph", 0.6, "building the graph")
        ph_in = dict(dates=phdec.get("dates", {}), chain_edits=phdec.get("chain_edits", {}))
        iddec_in = dict(choices=iddec.get("choices", {}), overrides=iddec.get("overrides", {}), merges=iddec.get("merges", []))
        if iddec.get("dates"):                                    # only then, so older graphs keep their keys
            iddec_in["dates"] = iddec["dates"]
        if iddec.get("drops"):
            iddec_in["drops"] = iddec["drops"]
        if iddec.get("splits"):                                   # likewise: people the user split off an identity
            iddec_in["splits"] = iddec["splits"]
        k_graph = h("graph", k_align, k_ident, iddec_in, ph_in, STAGE_VERSIONS["graph"])
        G = _get_or_run(pid, "graph", k_graph, lambda: _graph(A, I, iddec_in, ph_in, S), hashes)
        if phdec.get("confirmed") != k_graph:
            return _finish(pid, vers, hashes, "draft", "phylogeny", graph_hash=k_graph)
        # --- analysis ---------------------------------------------------------------------------
        preset = phdec.get("preset") or config.preset_for(len(G["narrators"]))
        k_infer = h("infer", k_graph, preset, STAGE_VERSIONS["infer"])
        compute = phdec.get("compute") or "server"
        if compute != "server" and project.stage_get(pid, "infer", k_infer) is None:
            # the user (browser) or the helper computes; they upload the result under this key
            _set(pid, awaiting=dict(key=k_infer, graph_hash=k_graph, preset=preset, compute=compute, since=project.now(), claimed=None))
            return _finish(pid, vers, hashes, "awaiting", "analysis", graph_hash=k_graph)
        say("infer", 0.7, "running the model")

        def run_infer():
            texts = {S["seed"]["wid"]: S["seed"]["matn"]}
            texts.update({c["wid"]: c["matn"] for c in S["candidates"]})
            out = infer.run(G, texts, preset=preset,
                            progress=lambda i, n, s: say("infer", 0.7 + 0.3 * i / max(n, 1), f"{i + 1}/{n} {s}"))
            return dict(results=out["results"], viz=out["bundle"])

        R = _get_or_run(pid, "infer", k_infer, run_infer, hashes)
        return _finish(pid, vers, hashes, "finalized", "done", graph_hash=k_graph)
    except Exception as e:                                        # keep the project readable, report the failure
        project.log(pid, "error", "".join(traceback.format_exception_only(type(e), e)).strip(), "error")
        _set(pid, status="failed", error=str(e))
        raise


def _identify(S: dict, A: dict) -> dict:
    wids = [S["seed"]["wid"]] + [w["wid"] for w in A["witnesses"] if w["wid"] != S["seed"]["wid"]]
    recs = corpus.get_many(wids)
    I = identity.build([recs[w] for w in wids if w in recs])
    I["suggestions"] = suggest_mod.suggest(I)
    return I


def _graph(A: dict, I: dict, iddec: dict, ph: dict, S: dict) -> dict:
    res = identity.resolve(I, iddec)
    if iddec.get("dates"):                     # step 4 dates a person (noor:/kg:) or the name at one place (occ:<aid>|<i>)
        occ = {f"{c['aid']}|{e['i']}": e["nid"] for c in res["chains"] for e in c["elems"]}
        dates = dict(ph.get("dates", {}))
        for k, v in iddec["dates"].items():
            nid = occ.get(k[4:]) if k.startswith("occ:") else res["alias"].get(k, k)
            if nid:
                dates[nid] = v
        ph = dict(ph, dates=dates)
    g = graph_mod.run(A, res, ph)
    g["undecided"] = res["undecided"]
    g["title"] = S["seed"].get("title") or "ICM"
    g["heading"] = f"{S['seed'].get('title') or ''} — {S['seed']['wid']}"
    return g


def _finish(pid: str, vers, hashes, status: str, step: str, graph_hash: Optional[str] = None) -> dict:
    rid = project.write_run(pid, project.versions(pid), hashes)
    kw = dict(awaiting=None) if status != "awaiting" else {}
    return _set(pid, status=status, step=step, current_run=rid, error=None, graph_hash=graph_hash, **kw)
