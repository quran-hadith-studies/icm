"""Step 6: the model. Candidate space, annealed SMC per candidate source, posterior summaries.

    out = run(graph, texts, preset="standard", progress=cb)
    out["results"]   source posterior, Bayes factors, sensitivity to the sanad term
    out["bundle"]    the viewer bundle (hadith_phylogeny.report.build_bundle)
"""
from __future__ import annotations

import math
import time
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np

from hadith_phylogeny.candidates import UNKNOWN, build_candidate_space
from hadith_phylogeny.data import Family, family_from_dict
from hadith_phylogeny.graph_model import GraphPrior
from hadith_phylogeny.inference import (Evidence, SourcePosterior, ancestral_state_posterior, calculate_source_posterior,
                                        logsumexp, posterior_summary, smc_source_evidence)
from hadith_phylogeny.report import build_bundle
from hadith_phylogeny.sanad_model import SanadModel
from hadith_phylogeny.text_model import VariantModel
from . import config
from .paths import max_paths

MAX_HEADS = 12
MAX_COMMON = 6


def preset_params(preset: str) -> Dict[str, int]:
    n, t, m = config.PRESETS[preset]
    return dict(n_particles=n, n_steps=t, n_mcmc=m)


def make_family(graph: dict, texts: Dict[str, str]) -> Family:
    fam = family_from_dict({k: graph[k] for k in ("name", "narrators", "attestations", "variants")})
    wid_of = {a["id"]: a["wid"] for a in graph["attestations"]}
    fam.texts = [texts.get(wid_of[a.aid], "") for a in fam.attestations]
    heads: Dict[str, int] = {}
    for a in fam.attestations:
        heads[a.sanad[0]] = heads.get(a.sanad[0], 0) + 1
    fam.companions = [h for h, _ in sorted(heads.items(), key=lambda kv: (-kv[1], kv[0]))]
    return fam


UNKNOWN_LABEL = "مبدأ بی‌نام (بالاتر از همه‌ی راویان ثبت‌شده)"


def unique_names(fam: Family) -> Dict[str, str]:
    """{nid: label}; the results are keyed by label, so two narrators who share a name (each
    chain's own «موضع إرسال») are told apart by the book of their first chain."""
    names = {n: fam.narrators[n].name for n in fam.narrators}
    book = {}
    for a in fam.attestations:
        for n in a.sanad:
            book.setdefault(n, a.collection)
    count: Dict[str, int] = {}
    for x in names.values():
        count[x] = count.get(x, 0) + 1
    seen: set = set()
    for n in sorted(names):
        if count[names[n]] > 1:
            x = f"{names[n]} ({book.get(n) or n})"
            k = 2
            while x in seen:
                x, k = f"{names[n]} ({book.get(n) or n} {k})", k + 1
            names[n] = x
        seen.add(names[n])
    names[UNKNOWN] = UNKNOWN_LABEL
    return names


def source_set(fam: Family, space, max_heads: int = MAX_HEADS, n_common: int = MAX_COMMON) -> List[str]:
    students: Dict[str, set] = {}
    for u, v in space.observed_edges:
        students.setdefault(u, set()).add(v)
    heads = list(fam.companions)[:max_heads]
    commons = [u for u, c in sorted(students.items(), key=lambda kv: -len(kv[1]))
               if len(c) >= 2 and u not in heads][:n_common]
    return heads + commons + [UNKNOWN]


class Ctx:
    """Everything derived from the confirmed graph that the per-source runs share.

    Deterministic given (graph, texts): a browser worker, the helper and the server each build
    their own copy and obtain the same candidate sources in the same order."""

    def __init__(self, graph: dict, texts: Dict[str, str], preset: Optional[str] = None, seed: int = 0,
                 sanad_weight: float = 1.0):
        self.graph, self.texts, self.seed = graph, texts, seed
        self.fam = make_family(graph, texts)
        self.space = build_candidate_space(self.fam)
        self.space.sources = source_set(self.fam, self.space)
        self.flags: List[dict] = []
        if len(self.fam.companions) > MAX_HEADS:
            self.flags.append(dict(kind="heads_capped", ref=None,
                                   message=f"{len(self.fam.companions)} chain heads; the {MAX_HEADS} with most witnesses are candidate sources"))
        self.preset = preset or config.preset_for(len(self.fam.narrators))
        self.params = preset_params(self.preset)
        self.tmodel = VariantModel(self.fam.variant_specs)
        self.phi = SanadModel(weight=sanad_weight)
        self.prior = GraphPrior(self.fam, self.space)
        self.names = unique_names(self.fam)

    @property
    def sources(self) -> List[str]:
        return self.space.sources

    def source_evidence(self, i: int) -> Evidence:
        """Annealed SMC for candidate source number ``i`` (independent of the others)."""
        return smc_source_evidence(self.fam, self.space, self.sources[i], self.tmodel, self.phi, self.prior,
                                   seed=self.seed, **self.params)


def evidence_to_json(ev: Evidence, with_graphs: bool = True) -> dict:
    d = dict(source=ev.source, log_Z=ev.log_Z, method=ev.method, n_graphs=ev.n_graphs, ess=ev.ess)
    if with_graphs:
        d["graphs"] = [dict(root=g.root, parent=g.parent, anchors=g.anchors) for g in ev.graphs]
        d["log_w"] = list(ev.log_w)
    return d


def evidence_from_json(d: dict) -> Evidence:
    from hadith_phylogeny.graph_model import Graph
    inf = lambda x: float("-inf") if x is None else x
    graphs = [Graph(g["root"], g["parent"], anchors=g["anchors"]) for g in d.get("graphs", [])]
    return Evidence(d["source"], inf(d["log_Z"]), d["method"], d["n_graphs"], float("nan") if d["ess"] is None else d["ess"],
                    graphs, [inf(x) for x in d.get("log_w", [])])


def assemble(ctx: Ctx, ev: Dict[str, Evidence], seconds: float, provenance: Optional[dict] = None) -> dict:
    """Source posterior, summaries under the best source, results and the viewer bundle."""
    fam, space, names = ctx.fam, ctx.space, ctx.names
    lp = {s: math.log(1.0 / len(space.sources)) for s in space.sources}
    scores = {s: lp[s] + ev[s].log_Z for s in space.sources}
    tot = logsumexp(list(scores.values()))
    post = SourcePosterior(list(space.sources), {s: ev[s].log_Z for s in space.sources}, lp,
                           {s: (math.exp(v - tot) if tot > -np.inf else 0.0) for s, v in scores.items()}, ev)
    best = post.map_source()
    summ = posterior_summary(fam, ev[best], ctx.tmodel, ctx.phi, ctx.prior, seed=ctx.seed)
    rest = [s for s in post.sources if s != best]
    results = dict(
        family=fam.name, preset=ctx.preset, **ctx.params, seconds=seconds, seed=ctx.seed,
        n_narrators=len(fam.narrators), n_attestations=len(fam.attestations),
        variants=[s.name for s in fam.variant_specs],
        posterior={names[s]: post.posterior[s] for s in post.sources},
        log_Z={names[s]: post.log_Z[s] for s in post.sources},
        ess={names[s]: post.evidences[s].ess for s in post.sources},
        map_source=names[best], map_source_id=best,
        credible_set_95=[names[s] for s in post.credible_set(0.95)], flags=ctx.flags)
    if provenance:
        results["provenance"] = provenance
    if rest:
        runner = max(rest, key=lambda s: post.log_Z[s])
        results["bayes_factor_top_vs_next"] = dict(pair=[names[best], names[runner]],
                                                   log_BF=post.log_Z[best] - post.log_Z[runner])
    anc = ancestral_state_posterior(fam, ev[best], ctx.tmodel, best, seed=ctx.seed)
    results["ancestral_matn_at_map_source"] = {fam.variant_specs[k].name: [round(float(x), 3) for x in v] for k, v in anc.items()}
    bundle = build_bundle(fam, space, ctx.prior, post, summ, best, links=_links_from_nodes(ctx.graph.get("nodes", {})),
                          texts=fam.texts, seconds=seconds, title=ctx.graph.get("title", "ICM"),
                          heading=ctx.graph.get("heading", fam.name), extras=dict(global_results=results))
    for nid, b in bundle["narrators"].items():           # what step 5 colours by: how the name was resolved, where the date is from
        g = ctx.graph.get("nodes", {}).get(nid, {})
        b["tiers"], b["date_source"] = g.get("tiers", []), g.get("date_source")
    bundle["max_paths"] = max_paths(bundle, ev[best].graphs, ev[best].log_w)
    return dict(results=results, bundle=bundle)


def run(graph: dict, texts: Dict[str, str], preset: Optional[str] = None, seed: int = 0,
        progress: Optional[Callable[[int, int, str], None]] = None, sanad_weight: float = 1.0) -> dict:
    """The whole analysis in this process (the server's slow path)."""
    ctx = Ctx(graph, texts, preset, seed, sanad_weight)
    t0 = time.time()
    ev = {}
    for i, s in enumerate(ctx.sources):
        if progress:
            progress(i, len(ctx.sources), ctx.names.get(s, s))
        ev[s] = ctx.source_evidence(i)
    return assemble(ctx, ev, round(time.time() - t0, 1), dict(kind="server"))


def matn_only(graph: dict, texts: Dict[str, str], weights: Sequence[float] = (0.0, 0.1), preset: str = "quick",
              seed: int = 0) -> dict:
    """The sanad term switched off (or down-weighted): how much of the source signal is the matn's?"""
    fam = make_family(graph, texts)
    space = build_candidate_space(fam)
    space.sources = source_set(fam, space)
    names = unique_names(fam)
    prior = GraphPrior(fam, space)
    out = {}
    for w in weights:
        post = calculate_source_posterior(fam, space, VariantModel(fam.variant_specs), SanadModel(weight=w), prior,
                                          method="smc", seed=seed, **preset_params(preset))
        out[f"sanad_weight_{w}"] = dict(
            map_source=names[post.map_source()],
            top={names[s]: round(post.posterior[s], 4) for s in sorted(post.sources, key=lambda z: -post.posterior[z])[:4]},
            log_Z={names[s]: round(post.log_Z[s], 2) for s in post.sources})
    return out


def _links_from_nodes(nodes: Dict[str, dict]) -> dict:
    """The viewer's biography panel reads {narrators: {nid: {confidence, record}}}."""
    narr = {}
    for nid, n in nodes.items():
        tiers = n.get("tiers", [])
        conf = "high" if set(tiers) & {"exact", "chosen", "derived"} else "medium" if "default" in tiers else "none"
        d = n.get("death")
        narr[nid] = dict(confidence=conf, name_score=None, context_links=None,
                         record=dict(id=nid, name=n.get("name"), death=(d[0] + d[1]) / 2 if d else None,
                                     iqama="،".join(n.get("cities", [])), tabaqa=n.get("imams", []),
                                     source=n.get("date_source"), heading=None),
                         alternatives=[])
    return dict(source="ICM narrator identities", narrators=narr, edge_kg_chains={}, kg_pairs=[], same_person={})
