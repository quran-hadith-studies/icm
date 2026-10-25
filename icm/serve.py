"""Stage files as the pages read them (the API, the standalone results page and the HTML exports).

Stage files stay exactly as computed. What newer pages need from older runs is added here when a stage is
served, never written back:

* search: the clause marks of every text (``search.with_marks``), and the automatic parts for runs from before
  the user could cut the seed;
* align: every part's ``key`` (``align.part_keys``) for runs from before the user could correct the parts;
* identify: step-4 suggestions for runs from before them, and ``node_name`` on every place (merge/split);
* viz: the maximum paths (``paths.max_paths``, the marginal estimate) for runs from before them.
"""
from __future__ import annotations

import copy
import os
from typing import Dict, Optional

from . import align as align_mod, corpus, identity, narrator_versions, paths, project
from . import sanad_parse
from . import search as search_mod
from . import suggest as suggest_mod
from .text import strip_tags, token_offsets, tokens

STAGES = {"search", "align", "identify", "graph", "infer"}
_CACHE: Dict[tuple, dict] = {}


def _cached(key: tuple, make):
    if key not in _CACHE:
        if len(_CACHE) > 40:
            _CACHE.clear()
        _CACHE[key] = make()
    return _CACHE[key]


def _parts_decision(pid: str, run: str) -> dict:
    """The parts decision a run is read with: the active one for the current run, the run's own for an older one."""
    p = project.load(pid) or {}
    if run != "current" and run != p.get("current_run"):
        m = project.read(os.path.join(project.pdir(pid), "runs", run, "manifest.json")) or {}
        v = int((m.get("versions") or {}).get("parts") or 0)
        return (project.decision(pid, "parts", v) if v else None) or {}
    return project.decision(pid, "parts") or {}


def stage(pid: str, run: str, name: str) -> Optional[dict]:
    """``name`` is a stage or ``viz`` / ``results`` (the two halves of ``infer``)."""
    src = "infer" if name in ("viz", "results") else name
    out = project.run_stage(pid, run, src)
    if out is None:
        return None
    key = (pid, name, out.get("inputs_hash") or run)
    if name == "search":
        if "auto_clauses" not in out:                          # every earlier search cut the seed automatically
            out = dict(out, auto_clauses=[c["span"] for c in out["clauses"]], clauses_by="auto")
        return search_mod.with_marks(out, _parts_decision(pid, run))
    if name == "align" and out["clauses"] and "key" not in out["clauses"][0]:
        for c, k in zip(out["clauses"], align_mod.part_keys(out["clauses"])):
            c["key"] = k
        return out
    if name == "identify":
        def make():
            c = copy.deepcopy(out)
            if "suggestions" not in c:                         # runs from before step-4 suggestions
                c["suggestions"] = suggest_mod.suggest(c, version_of(c))
            return identity.with_node_ids(c)
        c = _cached(key, make)
        extra = wiki_suggestions(c, version_of(c))
        return dict(c, suggestions=c["suggestions"] + extra) if extra else c
    if name == "viz":
        v = out["viz"]
        if "max_paths" in v:
            return v
        return _cached(key, lambda: dict(v, max_paths=paths.max_paths(v)))
    if name == "results":
        return out["results"]
    return out


def words(pid: str, run: str, wid: str) -> Optional[dict]:
    """A text of the run as the segmentation editors need it: ``tokens`` (the ones slots count in), the character
    range of each in ``matn``, and ``window`` (the tokens the aligner was given) for a candidate."""
    S = project.run_stage(pid, run, "search")
    if S is None:
        return None
    if wid == S["seed"]["wid"]:
        x, tk, win = S["seed"], S["tokens"], None
    else:
        x = next((c for c in S["candidates"] if c["wid"] == wid or wid in c.get("copies", [])), None)
        if x is None:
            return None
        tk, win = None, x.get("window")
    matn = strip_tags(x["matn"])
    tk = tk if tk is not None else tokens(matn)
    offs = token_offsets(matn)
    return dict(wid=x["wid"], matn=matn, tokens=tk, offsets=[list(o) for o in offs[:len(tk)]],
                window=win or [0, len(tk)], truncated=len(x["matn"]) < (x.get("matn_len") or len(x["matn"])))


def hadith(wid: str) -> Optional[dict]:
    """A full hadith record for display: sanad and matn without markup."""
    rec = corpus.get(wid)
    if rec is None:
        return None
    rec = {k: v for k, v in rec.items() if k != "rowid"}
    for k in ("matn", "sanad", "translation"):
        if rec.get(k):
            rec[k] = strip_tags(rec[k])
    return rec


def hadith_tokens(wid: str, pid: Optional[str] = None) -> Optional[dict]:
    """The whole text of a hadith as the chain editor needs it: ``tokens`` (the sanad's first, ``n_sanad`` of them),
    the raw spelling of each (``words``), which are transmission words (``tflag``) and which begin a blessing (``hflag``, «رضي الله عنه»), and, for a project, where every
    place of its chains stands in them (``chains``: aid -> ``spans`` {i: {t, a, b} | None}, the user's spans where they set
    them, and ``auto``, the automatic reading of each place, which «back to automatic» would give; None for an added place)."""
    rec = corpus.get(wid)
    if rec is None:
        return None
    toks, words, n_sanad = identity.hadith_words(rec)
    tflag = [1 if sanad_parse.is_transmit(toks[j - 1] if j else None, toks[j]) else 0 for j in range(len(toks))]
    hflag = [1 if t in sanad_parse.HONORIFIC else 0 for t in toks]
    chains = []
    I = project.run_stage(pid, "current", "identify") if pid else None
    for ch in (I or {}).get("chains", []):
        if ch["wid"] != wid:
            continue
        spans = identity.anchor_elems(toks, ch["elems"])
        auto = {e["i"]: e.get("span_auto", spans.get(e["i"])) if "span" in e else spans.get(e["i"]) for e in ch["elems"]}
        for e in ch["elems"]:
            if "span" in e:                                    # an edited chain: the stage already holds the user's spans
                spans[e["i"]] = e["span"]
        chains.append(dict(aid=ch["aid"], spans={str(i): s for i, s in spans.items()}, auto={str(i): s for i, s in auto.items()}))
    return dict(wid=wid, n_sanad=n_sanad, tokens=toks, words=words, tflag=tflag, hflag=hflag, chains=chains)


def hadiths(wids) -> Dict[str, dict]:
    out = {}
    for w, rec in corpus.get_many(sorted(set(wids))).items():
        rec = {k: v for k, v in rec.items() if k != "rowid"}
        for k in ("matn", "sanad", "translation"):
            if rec.get(k):
                rec[k] = strip_tags(rec[k])
        out[w] = rec
    return out


def page_context(pid: str, run: str, full_texts: bool = True) -> dict:
    """What step 6 shows besides the bundle, for the standalone page of a run: clause marks of the texts, the
    variant units, the alignment grids (the reconstructed text), how each name was resolved and the full texts."""
    S, A, G = (stage(pid, run, x) for x in ("search", "align", "graph"))
    ctx: dict = {}
    if S:
        ctx["texts"] = {w: dict(matn=x["matn"], marks=x.get("marks", []), ref=x.get("ref")) for x in [S["seed"], *S["candidates"]] for w in [x["wid"], *x.get("copies", [])]}
        ctx["clauses"] = [c["text"] for c in S["clauses"]]
    if A:
        ctx["units"] = [{k: u.get(k) for k in ("name", "kind", "unit_kind", "clause", "labels", "states", "cols", "ukey", "by")} for u in A["units"]]
        ctx["align"] = align_view(A)
    if G:
        ctx["nodes"] = {n: dict(tiers=i.get("tiers", []), date_source=i.get("date_source")) for n, i in G["nodes"].items()}
        if full_texts:
            recs = hadiths({a["wid"] for a in G["attestations"]})
            ctx["full"] = {w: {k: r.get(k) for k in ("wid", "title", "author", "death", "sanad", "matn")} for w, r in recs.items()}
    return ctx


def align_view(A: dict) -> dict:
    """The parts of the align stage the reconstructed text needs (results.js / recon.js)."""
    return dict(seed=A["seed"], clauses=[{k: c.get(k) for k in ("i", "kind", "text", "support")} for c in A["clauses"]],
                grids=A["grids"], witnesses=[w["wid"] for w in A["witnesses"]],
                segments={k: {"state": v.get("state"), "span": v.get("span"), "tokens": v.get("tokens")} for k, v in A["segments"].items()})


def version_of(I: dict) -> narrator_versions.Version:
    """The narrator database version an identify stage was computed with (raw for runs from before versions, or
    when its file is gone)."""
    try:
        return narrator_versions.get((I.get("narrator_version") or {}).get("name"))
    except KeyError:
        return narrator_versions.RAW


def wiki_suggestions(I: dict, version=None) -> list:
    """Merge suggestions from the narrator wiki, when its database is present."""
    try:
        from narwiki import lookup
        if not lookup.available():
            return []
        return suggest_mod.wiki_same_person(I, lookup.persons_for, version)
    except Exception:                                  # the wiki is optional
        return []
