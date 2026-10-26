"""Run the stages of a project until a decision from the user is needed.

    advance(pid, progress=None) -> project dict (status/step updated)

Stages are cached by the hash of their inputs (``project.stage_put``); a repeated call recomputes nothing.
The wizard order is: search -> [selection] -> alignment + identities (candidates) -> [identities]
-> [phylogeny: ``build`` = the key of the graph step 5 orders] -> graph -> [phylogeny, confirmed for exactly this graph]
-> analysis. The ``parts`` decision (the user's segmentation: the seed's parts, extra parts, slots in witnesses) never blocks: it changes search and alignment
when it is made, and its confirmations are read by the pages only.
"""
from __future__ import annotations

import time
import traceback
from typing import Callable, Dict, Optional

from . import STAGE_VERSIONS, align as align_mod, config, corpus, graph as graph_mod, identity, infer, jobs, narrator_versions, project, search as search_mod
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


def _sparams(part_query, clauses, join=None) -> dict:
    out = dict(part_query=part_query)
    if clauses:                                               # only then, so automatic searches keep their keys
        out["clauses"] = clauses
    if join is not None:                                      # the user joined the hadiths' cuts (a list of groups, perhaps none)
        out["join"] = join
    return out


def _ret_key(seed: str, ret: dict) -> str:
    sp, keep = _sparams(ret.get("part_query"), ret.get("clauses"), ret.get("join")), sorted(set(ret.get("keep") or []))
    if ret.get("legacy"):                                     # the key every search had before «جستجو» became a button
        return h("search", seed, sp, sorted(ret.get("extra") or []), *([keep] if keep else []), STAGE_VERSIONS["search"], config.DATA_VERSION)
    return h("search", seed, sp, [], *([keep] if keep else []), dict(n=ret["n"], seeds=sorted(ret.get("seeds") or [])), STAGE_VERSIONS["search"], config.DATA_VERSION)


def _search(pid: str, p: dict, sdec: dict, pdec: dict, seldec: Optional[dict], hashes: Dict[str, str]) -> dict:
    """The search stage of the current decisions. Corpora are asked only when the user pressed «جستجو» (``sdec.ret``, the
    parameters of that press); the hadiths the user added, the further seeds, edited texts and a re-cut seed only score the
    candidates of that search again. Before the first press there are no candidates, only the user's own hadiths. Projects
    made before the button (no ``manual_search`` in project.json) keep their keys and search as they did."""
    seed = project.anchor(p, sdec)                             # the first hadith of step 1's list
    cl = project.cuts_of(pdec)
    cur = dict(part_query=sdec.get("part_query"), clauses=cl, join=pdec.get("join"), keep=sorted(set(pdec.get("keep_ids") or [])) if cl else [],
               extra=sorted(set(sdec.get("extra_ids") or [])), seeds=sorted(set(sdec.get("seeds") or []) - {seed}), edits=sdec.get("edits") or {})
    ret = sdec.get("ret")
    if ret is None:
        legacy = dict(legacy=True, n=0, clauses=cl, join=cur["join"], part_query=cur["part_query"], extra=cur["extra"], keep=cur["keep"], seeds=[])
        if not p.get("manual_search"):
            ret = legacy
    sp = _sparams(cur["part_query"], cl, cur["join"])
    if ret is None:                                            # not searched yet: the seed's parts and the user's own hadiths
        k0 = h("search0", seed, sp, cur["extra"], cur["keep"], cur["seeds"], cur["edits"], STAGE_VERSIONS["search"], config.DATA_VERSION, *(["family"] if cur["seeds"] else []))
        return _get_or_run(pid, "search", k0, lambda: dict(search_mod.run(seed, sp, cur["extra"], cur["keep"], seeds=cur["seeds"], retrieve=False,
                                                                           texts=cur["edits"]), searched=False, stale=False), hashes)
    rseed = ret.get("anchor") or p["seed"]                     # the first hadith when «جستجو» was pressed
    k_ret = _ret_key(rseed, ret)
    rsp = _sparams(ret.get("part_query"), ret.get("clauses"), ret.get("join"))
    R = _get_or_run(pid, "search", k_ret, lambda: dict(search_mod.run(rseed, rsp, ret.get("extra") or [], ret.get("keep") or [],
                                                                      seeds=ret.get("seeds") or []), searched=True, stale=False), hashes)
    if (seed, cur["clauses"], cur["join"], cur["part_query"], cur["extra"], cur["keep"], cur["seeds"]) == (rseed, ret.get("clauses"), ret.get("join"), ret.get("part_query"), sorted(ret.get("extra") or []),
                                                                                              sorted(ret.get("keep") or []), sorted(ret.get("seeds") or [])) and not cur["edits"] \
            and ("cuts" in R or not R.get("seeds")):             # (a search made before every hadith of the list was cut on its own, with several hadiths, is scored once more: the corpora are not asked again)
        return R
    prior = {}
    for c in R["candidates"]:
        sig = [g for g in c["signals"] if g not in ("user", "seed")]       # what the user added or named is read from the decision now
        for w in [c["wid"], *c.get("copies", [])]:
            prior[w] = dict(signals=sig, round=c.get("round"), via=c.get("via"))
    stale = (seed, cur["clauses"], cur["join"], cur["part_query"]) != (rseed, ret.get("clauses"), ret.get("join"), ret.get("part_query"))
    k_fin = h("rescore", k_ret, sp, cur["extra"], cur["keep"], cur["seeds"], cur["edits"], STAGE_VERSIONS["search"])
    def rescore():
        out = search_mod.run(seed, sp, cur["extra"], cur["keep"], seeds=cur["seeds"], prior=prior, retrieve=False, texts=cur["edits"])
        # what the corpora were asked and found stays that of the press (its part numbers are the old parts' while ``stale``)
        return dict(out, searched=True, stale=stale, ret_n=ret.get("n"), retrieved=R.get("retrieved", True), queries=R["queries"], rounds=R["rounds"], flags=R["flags"] + out["flags"])
    return _get_or_run(pid, "search", k_fin, rescore, hashes)


def _set(pid: str, **kw) -> dict:
    with project.lock(pid):
        p = project.load(pid)
        p.update(kw)
        project.save(p)
    project.refresh_indexes()
    return p


def _selected(S: dict, seldec: dict) -> list:
    known = {c["wid"] for c in S["candidates"]} | {w for c in S["candidates"] for w in c.get("copies", [])}
    return sorted((set(seldec.get("selected", [])) & known) | (set(S.get("seeds") or []) & known))    # a re-search may have taken a ticked one away; further seeds are always in


def go_key(k_search: str, selected: list) -> str:
    """What «build the alignment» / «find the narrators» were asked for: the search and the witnesses it was asked on. The user's
    later edits of the alignment itself (words, units, parts) do not change it; another selection or search does."""
    return h("go", k_search, selected)


def request(pid: str, stage: str, user: str) -> Optional[str]:
    """The user asks for a step to be computed (``align`` or ``identify``) on the search and selection on screen; ``identify``
    needs the alignment asked for first. Returns the key, or None when there is nothing to ask for yet."""
    hs, seldec = project.run_hashes(pid).get("search"), project.decision(pid, "selection") or {}
    S = project.run_stage(pid, "current", "search")
    if not hs or S is None or not seldec.get("selected"):
        return None
    key = go_key(hs, _selected(S, seldec))
    go = project.decision(pid, "go") or {}
    if stage == "identify" and go.get("align") != key:
        return None
    project.add_decision(pid, "go", dict({k: go[k] for k in ("align", "identify") if go.get(k)}, **{stage: key}), user, f"{stage} asked for")
    return key


def cancel(pid: str, kinds: list, user: str) -> None:
    """The owner cancelled the project's jobs: what a cancelled «build the alignment» / «find the narrators» asked for is
    asked for no more (else the next job would start it again), and the project is no longer «running»."""
    stages = [k[3:] for k in kinds if k.startswith("go:")]
    go = project.decision(pid, "go") or {}
    if any(go.get(st) for st in stages):
        drop = set(stages) | ({"identify"} if "align" in stages else set())
        project.add_decision(pid, "go", {k: go[k] for k in ("align", "identify") if go.get(k) and k not in drop}, user, "cancelled")
    if (project.load(pid) or {}).get("status") == "running":
        _set(pid, status="draft", error=None)


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
        say("search", 0.0, "searching the corpora")
        S = _search(pid, p, sdec, pdec, project.decision(pid, "selection"), hashes)
        k_search = hashes["search"]
        seldec = project.decision(pid, "selection")
        if not seldec or not seldec.get("selected"):
            return _finish(pid, vers, hashes, "draft", "selection")
        selected = _selected(S, seldec)
        # --- alignment: only when the user asked for it (step 3's button) on this search and selection -------------
        key = go_key(k_search, selected)
        if not p.get("manual_steps"):
            # a project from before the steps were buttons: what it had already computed counts as asked for
            project.add_decision(pid, "go", dict(align=key, identify=key), "system", "steps made before they were buttons")
            _set(pid, manual_steps=True)
            p = project.load(pid)
        go = project.decision(pid, "go") or {}
        if go.get("align") != key:
            return _finish(pid, vers, hashes, "draft", "alignment")
        say("align", 0.2, "aligning")
        parts_in = {k: pdec[k] for k in ("extras_dropped", "extras_added", "slots", "words", "unit_cls", "units", "refs") if pdec.get(k)}
        k_align = h("align", k_search, selected, STAGE_VERSIONS["align"], *([parts_in] if parts_in else []))
        A = _get_or_run(pid, "align", k_align, lambda: align_mod.run(S, selected, parts_in), hashes)
        if go.get("identify") != key:                             # likewise step 4's button
            return _finish(pid, vers, hashes, "draft", "identities")
        say("identify", 0.4, "finding the narrators")
        V = project.narrator_version(pid)
        ren = pdec.get("renames") or None                         # step 4: where a name begins and ends (only then, so keys stay)
        ced = pdec.get("chain") or None                           # step 4: places added, removed or re-bounded in the text
        iddec = project.decision(pid, "identities")
        idrops = sorted(set((iddec or {}).get("drops") or [])) or None   # step 4: places removed on the main page leave the chain too
        k_ident = h("identify", k_search, selected, STAGE_VERSIONS["identify"], config.DATA_VERSION, V.key, *([ren] if ren else []),
                    *([ced] if ced else []), *([{"drops": idrops}] if idrops else []))
        I = _get_or_run(pid, "identify", k_ident, lambda: _identify(S, A, V, ren, ced, idrops), hashes)
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
        gone = phdec.get("deleted") == k_graph                    # step 5 «حذف شجره»: the cached tree stays on disk but is not shown until it is ordered again
        if (project.stage_get(pid, "graph", k_graph) is None or gone) and phdec.get("build") != k_graph:
            return _finish(pid, vers, hashes, "draft", "phylogeny", graph_hash=k_graph)    # step 5 orders the tree by hand
        G = _get_or_run(pid, "graph", k_graph, lambda: _graph(A, I, iddec_in, ph_in, S, V), hashes)
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
    except jobs.Cancelled:                                        # the owner stopped it: not a failure; cancel() has set the project right
        raise
    except Exception as e:                                        # keep the project readable, report the failure
        project.log(pid, "error", "".join(traceback.format_exception_only(type(e), e)).strip(), "error")
        _set(pid, status="failed", error=str(e))
        raise


def _identify(S: dict, A: dict, V: narrator_versions.Version, renames: Optional[dict] = None, chain_edits: Optional[dict] = None,
              drops: Optional[list] = None) -> dict:
    wids = [S["seed"]["wid"]] + [w["wid"] for w in A["witnesses"] if w["wid"] != S["seed"]["wid"]]
    recs = corpus.get_many(wids)
    I = identity.build([recs[w] for w in wids if w in recs], V, renames, identity.merge_drops(chain_edits, drops))
    I["renames_in"] = renames or {}
    I["chain_in"] = chain_edits or {}
    I["drops_in"] = drops or []
    I["suggestions"] = suggest_mod.suggest(I, V)
    I["narrator_version"] = V.info()
    return I


def _graph(A: dict, I: dict, iddec: dict, ph: dict, S: dict, V: narrator_versions.Version) -> dict:
    res = identity.resolve(I, iddec, V)
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
