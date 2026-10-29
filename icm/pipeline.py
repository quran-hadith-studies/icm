"""Run the stages of a project: only what the user asked for, and only when they pressed its button.

    advance(pid, progress=None, want=None) -> project dict (status/step updated)
    plan(pid) -> the keys of every stage for the decisions now, computing nothing

Stages are cached by the hash of their inputs (``project.stage_put``); a repeated call recomputes nothing. The chain is
search -> alignment -> identities -> graph -> analysis. ``want`` is the set of stages this call may COMPUTE (a stage already
on disk is always read); ``None`` computes everything the decisions allow. Saving a decision never runs anything: the
run buttons (``api.run``) enqueue a job whose ``want`` is ``want_of(kind)``. The chain stops at a stage that is neither
cached nor wanted, and at the user's own gates: no selection; no identities decision; the tree ``build`` (the key of the
graph step 8 orders); ``confirmed`` (for exactly this graph). The ``parts`` decision (the user's segmentation) changes
search and alignment when it is made; its confirmations are read by the pages only.
"""
from __future__ import annotations

import os
import time
import traceback
from typing import Callable, Dict, Optional, Tuple

from . import STAGE_VERSIONS, align as align_mod, config, corpus, graph as graph_mod, identity, infer, jobs, narrator_versions, project, search as search_mod
from . import semantic, suggest as suggest_mod
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
    return h("search", seed, sp, [], *([keep] if keep else []), dict(n=ret["n"], seeds=sorted(ret.get("seeds") or [])), STAGE_VERSIONS["search"], config.DATA_VERSION,
             *([dict(lex=ret["lex"], **({"sem": ret["sem"]} if ret.get("sem") else {}), **{k: ret[k] for k in ("sem_min", "sem_k") if ret.get(k) is not None})] if ret.get("lex") else []))     # (only a search pressed with the root index has it: the keys of the others stay)


class _Stop(Exception):
    """The chain stops at ``stage``: not on disk, and not one this call may compute."""

    def __init__(self, stage: str):
        super().__init__(stage)
        self.stage = stage


def snapshot(pid: str) -> Tuple[Dict[str, int], Dict[str, Optional[dict]]]:
    """The active version's decisions as they are now, read once: ``(versions, {step: decision or None})``. A run is built from
    this and its manifest names exactly these versions, whatever the user saves while it runs."""
    vers = project.versions(pid)
    return vers, {s: (project.decision(pid, s, vers[s]) if vers[s] else None) for s in project.STEPS}


def _prior_of(R: dict) -> dict:
    prior = {}
    for c in R["candidates"]:
        sig = [g for g in c["signals"] if g not in ("user", "seed")]       # what the user added or named is read from the decision now
        for w in [c["wid"], *c.get("copies", [])]:
            prior[w] = dict(signals=sig, round=c.get("round"), via=c.get("via"), sem=(c.get("score") or {}).get("sem"), sem_hits=c.get("sem_hits"))
    return prior


def sem_at(p: dict, sdec: dict, pdec: dict) -> dict:
    """What the parts and hadiths were when the semantic search was pressed (kept in ``ret_sem.at``): while they are the same
    the semantic list is current."""
    seed = project.anchor(p, sdec)
    return dict(anchor=seed, seeds=sorted(set(sdec.get("seeds") or []) - {seed}), clauses=project.cuts_of(pdec), join=pdec.get("join"), part_query=sdec.get("part_query"))


def _search(pid: str, p: dict, D: dict, use, info: dict) -> dict:
    """The search stage of the current decisions. Corpora are asked only when the user pressed «جستجو» (``sdec.ret``, the
    parameters of that press); the semantic index only when they pressed «جستجوی معنایی» (``sdec.ret_sem``, on top of that
    press); the hadiths the user added, the further seeds, edited texts and a re-cut seed only score the candidates again.
    Before the first press there are no candidates, only the user's own hadiths. Projects made before the button (no
    ``manual_search`` in project.json) keep their keys and search as they did."""
    sdec, pdec = D["search"] or {}, D["parts"] or {}
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
        return use("search", k0, lambda: dict(search_mod.run(seed, sp, cur["extra"], cur["keep"], seeds=cur["seeds"], retrieve=False,
                                                              texts=cur["edits"]), searched=False, stale=False))
    rseed = ret.get("anchor") or p["seed"]                     # the first hadith when «جستجو» was pressed
    k_ret = _ret_key(rseed, ret)
    rsp = _sparams(ret.get("part_query"), ret.get("clauses"), ret.get("join"))
    if ret.get("lex"):
        rsp["lex"] = True                                      # roots in the queries and in the scoring (icm/lex.py)
    if ret.get("sem"):
        rsp["sem"] = ret["sem"]                                # (a press made before the semantic search had its own button: hadiths of the same meaning, its cut-off part of the key)
        if ret.get("sem_min") is not None:
            rsp["sem_min"] = ret["sem_min"]
        if ret.get("sem_k") is not None:
            rsp["sem_k_part"], rsp["sem_k_all"] = ret["sem_k"], 2 * ret["sem_k"]
    R = use("search", k_ret, lambda: dict(search_mod.run(rseed, rsp, ret.get("extra") or [], ret.get("keep") or [],
                                                           seeds=ret.get("seeds") or []), searched=True, stale=False))
    k_base = k_ret
    rs = sdec.get("ret_sem")                                   # step 4's press: the semantic index asked about the parts of that press
    if rs and rs.get("mode") == "parts":
        # two presses: the passages that resemble each part (nothing joins the candidates), then the hadiths of the passages the user ticked
        pmin = {int(c): v for c, v in (rs.get("part_min") or {}).items()}          # the least cosine of the parts that have their own
        rsp_sem = dict(rsp, sem=rs.get("model") or True, sem_min=rs["sem_min"], sem_k_part=rs["sem_k"], sem_k_all=2 * rs["sem_k"], sem_min_by=pmin)
        ix = semantic.meta()                                   # the index asked: a rebuilt one (other texts, other vectors) is not the saved answer of the old
        k_base = h("sempassages", k_ret, rs["sem_min"], rs["sem_k"], rs.get("model"), *([rs["unit"], rs.get("pca"), "tiers"] if rs.get("unit") else []),
                   *([sorted(pmin.items())] if pmin else []), rs.get("at"), STAGE_VERSIONS["search"], *([ix.get("built"), ix.get("n")] if ix.get("unit") == "parts" else []))

        def sem_cache(key, fn):                                # a part's passages are kept by what they were made from: a part asked again alone costs that part only
            got = project.stage_get(pid, "sempart", key)
            if got is None:
                got = fn()
                project.stage_put(pid, "sempart", key, got)
            return got

        def passages():
            out = search_mod.run(rseed, rsp_sem, ret.get("extra") or [], ret.get("keep") or [], seeds=ret.get("seeds") or [], prior=_prior_of(R),
                                 retrieve=False, sem_passages=True, sem_cache=sem_cache)
            return dict(out, searched=True, stale=False, retrieved=R.get("retrieved", True), queries=R["queries"] + out["queries"], rounds=R["rounds"],
                        flags=R["flags"] + out["flags"], sem_n=rs.get("n"))
        R = use("search", k_base, passages)
        picks = [[w, c] for w, c in rs.get("picks") or []]
        if picks:
            k_pick = h("sempicks", k_base, picks, STAGE_VERSIONS["search"])
            parts_found = R.get("sem_parts") or []

            def picked():
                out = search_mod.run(rseed, rsp_sem, ret.get("extra") or [], ret.get("keep") or [], seeds=ret.get("seeds") or [], prior=_prior_of(R),
                                     retrieve=False, sem_picks=search_mod.resolve_picks(parts_found, picks))
                return dict(out, searched=True, stale=False, retrieved=R.get("retrieved", True), queries=R["queries"], rounds=R["rounds"],
                            flags=R["flags"] + out["flags"], sem_n=rs.get("n"), sem_parts=parts_found, sem_pn=rs.get("pn"))
            R = use("search", k_pick, picked)
            k_base = k_pick
        info["sem_stale"] = rs.get("at") != sem_at(p, sdec, pdec)
    elif rs:
        k_base = h("semsearch", k_ret, rs["sem_min"], rs["sem_k"], rs.get("model"), rs.get("at"), STAGE_VERSIONS["search"])
        rsp_sem = dict(rsp, sem=rs.get("model") or True, sem_min=rs["sem_min"], sem_k_part=rs["sem_k"], sem_k_all=2 * rs["sem_k"])

        def semrun():
            out = search_mod.run(rseed, rsp_sem, ret.get("extra") or [], ret.get("keep") or [], seeds=ret.get("seeds") or [], prior=_prior_of(R),
                                 retrieve=False, semantic_only=True)
            return dict(out, searched=True, stale=False, retrieved=R.get("retrieved", True), queries=R["queries"] + out["queries"], rounds=R["rounds"],
                        flags=R["flags"] + out["flags"], sem_n=rs.get("n"))
        R = use("search", k_base, semrun)
        info["sem_stale"] = rs.get("at") != sem_at(p, sdec, pdec)
    if (seed, cur["clauses"], cur["join"], cur["part_query"], cur["extra"], cur["keep"], cur["seeds"]) == (rseed, ret.get("clauses"), ret.get("join"), ret.get("part_query"), sorted(ret.get("extra") or []),
                                                                                              sorted(ret.get("keep") or []), sorted(ret.get("seeds") or [])) and not cur["edits"] \
            and ("cuts" in R or not R.get("seeds")):             # (a search made before every hadith of the list was cut on its own, with several hadiths, is scored once more: the corpora are not asked again)
        return R
    prior = _prior_of(R)
    stale = (seed, cur["clauses"], cur["join"], cur["part_query"]) != (rseed, ret.get("clauses"), ret.get("join"), ret.get("part_query"))
    k_fin = h("rescore", k_base, sp, cur["extra"], cur["keep"], cur["seeds"], cur["edits"], STAGE_VERSIONS["search"])
    if ret.get("lex"):
        sp = dict(sp, lex=True, **({"sem": ret["sem"]} if ret.get("sem") else {}), **({"sem_min": ret["sem_min"]} if ret.get("sem_min") is not None else {}))        # (the press's rules score the re-cut too; k_ret has it already)
    if rs:
        sp = dict(sp, sem=rs.get("model") or True, sem_min=rs["sem_min"], sem_k_part=rs["sem_k"], sem_k_all=2 * rs["sem_k"])

    def rescore():
        out = search_mod.run(seed, sp, cur["extra"], cur["keep"], seeds=cur["seeds"], prior=prior, retrieve=False, texts=cur["edits"])
        # what the corpora were asked and found stays that of the press (its part numbers are the old parts' while ``stale``)
        return dict(out, searched=True, stale=stale, ret_n=ret.get("n"), sem_n=R.get("sem_n"), sem_pn=R.get("sem_pn"), **({"sem_parts": R["sem_parts"]} if R.get("sem_parts") is not None else {}), retrieved=R.get("retrieved", True), queries=R["queries"], rounds=R["rounds"],
                    flags=R["flags"] + out["flags"])
    return use("search", k_fin, rescore)


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
    """What «build the alignment» / «find the narrators» were asked for: the search and the witnesses it was asked on (kept in
    the ``go`` decision as a record of who pressed what and when; nothing reads it to decide whether to run)."""
    return h("go", k_search, selected)


WANT = {"run:rescore": {"search"}, "run:search": {"search"}, "run:semantic": {"search"}, "run:sempick": {"search"}, "run:align": {"align"}, "run:identify": {"identify"},
        "run:graph": {"graph"}, "run:infer": {"infer"}, "finalize": set()}


def want_of(kind: str) -> Optional[set]:
    """The stages a job of this kind may compute (None: all). Jobs queued before the run buttons (``advance``) compute whatever
    the decisions allow; ``go:<stage>`` jobs compute that stage."""
    if kind in WANT:
        return set(WANT[kind])
    if kind.startswith("go:"):
        return {kind[3:]}
    return None


def cancel(pid: str, kinds: list, user: str) -> None:
    """The owner cancelled the project's jobs: the project is no longer «running»."""
    if (project.load(pid) or {}).get("status") == "running":
        _set(pid, status="draft", error=None)


def _walk(pid: str, p: dict, D: dict, use, info: dict, say, dry: bool = False) -> dict:
    """Walk the chain with ``use(stage, key, fn)`` (read the stage, compute it, or raise ``_Stop``). Fills ``info`` with what
    later code needs (``k_graph``, ``selected``, ...); returns ``dict(status, gate)``: the gate is what the chain waits for
    (``selection``, ``identities``, ``build``, ``confirm``, ``awaiting``) or None when it ran through."""
    S = _search(pid, p, D, use, info)
    k_search = info["k_search"] = _last_key(use, "search")
    seldec = D["selection"]
    if not seldec or not seldec.get("selected"):
        return dict(status="draft", gate="selection")
    selected = info["selected"] = _selected(S, seldec)
    say("align", 0.2, "aligning")
    pdec = D["parts"] or {}
    parts_in = {k: pdec[k] for k in ("extras_dropped", "extras_added", "slots", "words", "unit_cls", "units", "refs") if pdec.get(k)}
    k_align = h("align", k_search, selected, STAGE_VERSIONS["align"], *([parts_in] if parts_in else []))
    A = use("align", k_align, lambda: align_mod.run(S, selected, parts_in), need=False)
    say("identify", 0.4, "finding the narrators")
    V = project.narrator_version(pid, D)
    ren = pdec.get("renames") or None                         # step 7: where a name begins and ends (only then, so keys stay)
    ced = pdec.get("chain") or None                           # step 7: places added, removed or re-bounded in the text
    iddec = D["identities"]
    idrops = sorted(set((iddec or {}).get("drops") or [])) or None   # step 7: places removed on the main page leave the chain too
    k_ident = h("identify", k_search, selected, STAGE_VERSIONS["identify"], config.DATA_VERSION, V.key, *([ren] if ren else []),
                *([ced] if ced else []), *([{"drops": idrops}] if idrops else []))
    I = use("identify", k_ident, lambda: _identify(S, A, V, ren, ced, idrops), need=False)
    if not iddec:
        return dict(status="draft", gate="identities")
    # --- graph -----------------------------------------------------------------------------------
    phdec = D["phylogeny"] or {}
    say("graph", 0.6, "building the graph")
    ph_in = dict(dates=phdec.get("dates", {}), chain_edits=phdec.get("chain_edits", {}))
    iddec_in = dict(choices=iddec.get("choices", {}), overrides=iddec.get("overrides", {}), merges=iddec.get("merges", []))
    if iddec.get("dates"):                                    # only then, so older graphs keep their keys
        iddec_in["dates"] = iddec["dates"]
    if iddec.get("drops"):
        iddec_in["drops"] = iddec["drops"]
    if iddec.get("splits"):                                   # likewise: people the user split off an identity
        iddec_in["splits"] = iddec["splits"]
    k_graph = info["k_graph"] = h("graph", k_align, k_ident, iddec_in, ph_in, STAGE_VERSIONS["graph"])
    info["keys"]["graph"] = k_graph
    gone = phdec.get("deleted") == k_graph                    # step 8 «حذف شجره»: the cached tree stays on disk but is not shown until it is ordered again
    if (project.stage_get(pid, "graph", k_graph) is None or gone) and phdec.get("build") != k_graph:
        return dict(status="draft", gate="build")             # step 8 orders the tree by hand
    G = use("graph", k_graph, lambda: _graph(A, I, iddec_in, ph_in, S, V), need=phdec.get("confirmed") == k_graph)
    if phdec.get("confirmed") != k_graph:
        return dict(status="draft", gate="confirm")
    # --- analysis --------------------------------------------------------------------------------
    preset = info["preset"] = phdec.get("preset") or config.preset_for(len(G["narrators"]))
    k_infer = h("infer", k_graph, preset, STAGE_VERSIONS["infer"])
    compute = info["compute"] = phdec.get("compute") or "server"
    if compute != "server" and project.stage_get(pid, "infer", k_infer) is None:
        # the user (browser) or the helper computes; they upload the result under this key
        info["keys"]["infer"] = k_infer
        if not dry:
            _set(pid, awaiting=dict(key=k_infer, graph_hash=k_graph, preset=preset, compute=compute, since=project.now(), claimed=None))
        return dict(status="awaiting", gate="awaiting")
    say("infer", 0.7, "running the model")

    def run_infer():
        texts = {S["seed"]["wid"]: S["seed"]["matn"]}
        texts.update({c["wid"]: c["matn"] for c in S["candidates"]})
        out = infer.run(G, texts, preset=preset,
                        progress=lambda i, n, s: say("infer", 0.7 + 0.3 * i / max(n, 1), f"{i + 1}/{n} {s}"))
        return dict(results=out["results"], viz=out["bundle"])

    use("infer", k_infer, run_infer)
    return dict(status="finalized", gate=None)


def _last_key(use, stage: str) -> str:
    return use.keys[stage]


def _make_use(pid: str, want: Optional[set], hashes: Dict[str, str], keys: Dict[str, str], compute: bool = True):
    def use(stage: str, key: str, fn, need: bool = True):
        keys[stage] = key
        if not compute and not need:                           # planning: only whether the file is there (its content feeds no key)
            if not os.path.exists(project.stage_path(pid, stage, key)):
                raise _Stop(stage)
            hashes[stage] = key
            return None
        out = project.stage_get(pid, stage, key)
        if out is None:
            if not compute or not (want is None or stage in want):
                raise _Stop(stage)
            out = _get_or_run(pid, stage, key, fn, hashes)
        hashes[stage] = key
        return out
    use.keys = keys
    return use


def plan(pid: str, D: Optional[dict] = None) -> dict:
    """The keys of every stage for the decisions now, computing nothing: ``keys`` (stage -> key; a stage after the first one
    that is not on disk has none), ``cached`` (the stages whose file exists), ``gate`` (what the chain waits for), plus
    ``k_graph``, ``selected``, ``preset``, ``compute``, ``sem_stale``. The single source of the keys: ``advance``, the run
    buttons and the wizard's stale marks all use it."""
    if D is None:
        D = snapshot(pid)[1]
    p = project.load(pid)
    keys, hashes, info = {}, {}, {}
    info["keys"] = keys
    use = _make_use(pid, None, hashes, keys, compute=False)
    try:
        res = _walk(pid, p, D, use, info, lambda *a: None, dry=True)
    except _Stop as e:
        res = dict(status="draft", gate="stop:" + e.stage)
    return dict(keys=keys, cached=sorted(hashes), gate=res["gate"], **{k: v for k, v in info.items() if k != "keys"})


def advance(pid: str, progress: Optional[Callable[[str, float, str], None]] = None, want: Optional[set] = None) -> dict:
    def say(stage, frac, msg=""):
        if progress:
            progress(stage, frac, msg)

    p = project.load(pid)
    vers, D = snapshot(pid)
    hashes: Dict[str, str] = {}
    keys: Dict[str, str] = {}
    info: dict = {"keys": keys}
    try:
        _set(pid, status="running")
        say("search", 0.0, "searching the corpora")
        use = _make_use(pid, want, hashes, keys)
        try:
            res = _walk(pid, p, D, use, info, say)
        except _Stop:                                         # the next stage was not asked for: this run ends here
            res = dict(status="draft", gate="stop")
        return _finish(pid, vers, hashes, res["status"], info.get("k_graph"))
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


def _finish(pid: str, vers, hashes, status: str, graph_hash: Optional[str] = None) -> dict:
    rid = project.write_run(pid, vers, hashes)                  # the decisions this run was built from (not those saved while it ran)
    kw = dict(awaiting=None) if status != "awaiting" else {}
    p = _set(pid, status=status, current_run=rid, error=None, graph_hash=graph_hash, **kw)
    from . import wizard
    return wizard.touch(pid) or p
