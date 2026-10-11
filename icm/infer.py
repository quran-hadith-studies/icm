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
from hadith_phylogeny.inference import (SourcePosterior, ancestral_state_posterior, calculate_source_posterior,
                                        logsumexp, posterior_summary, smc_source_evidence)
from hadith_phylogeny.report import build_bundle
from hadith_phylogeny.sanad_model import SanadModel
from hadith_phylogeny.text_model import VariantModel
from . import config

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


def source_set(fam: Family, space, max_heads: int = MAX_HEADS, n_common: int = MAX_COMMON) -> List[str]:
    students: Dict[str, set] = {}
    for u, v in space.observed_edges:
        students.setdefault(u, set()).add(v)
    heads = list(fam.companions)[:max_heads]
    commons = [u for u, c in sorted(students.items(), key=lambda kv: -len(kv[1]))
               if len(c) >= 2 and u not in heads][:n_common]
    return heads + commons + [UNKNOWN]


def run(graph: dict, texts: Dict[str, str], preset: Optional[str] = None, seed: int = 0,
        progress: Optional[Callable[[int, int, str], None]] = None, sanad_weight: float = 1.0) -> dict:
    fam = make_family(graph, texts)
    space = build_candidate_space(fam)
    space.sources = source_set(fam, space)
    flags: List[dict] = []
    if len(fam.companions) > MAX_HEADS:
        flags.append(dict(kind="heads_capped", ref=None,
                          message=f"{len(fam.companions)} chain heads; the {MAX_HEADS} with most witnesses are candidate sources"))
    preset = preset or config.preset_for(len(fam.narrators))
    params = preset_params(preset)
    tmodel = VariantModel(fam.variant_specs)
    phi = SanadModel(weight=sanad_weight)
    prior = GraphPrior(fam, space)
    names = {n: fam.narrators[n].name for n in fam.narrators}
    names[UNKNOWN] = "UNKNOWN (source above all recorded narrators)"

    t0 = time.time()
    ev = {}
    for i, s in enumerate(space.sources):
        if progress:
            progress(i, len(space.sources), names.get(s, s))
        ev[s] = smc_source_evidence(fam, space, s, tmodel, phi, prior, seed=seed, **params)
    src_prior = {s: 1.0 / len(space.sources) for s in space.sources}
    lp = {s: math.log(src_prior[s]) for s in space.sources}
    scores = {s: lp[s] + ev[s].log_Z for s in space.sources}
    tot = logsumexp(list(scores.values()))
    post = SourcePosterior(list(space.sources), {s: ev[s].log_Z for s in space.sources}, lp,
                           {s: (math.exp(v - tot) if tot > -np.inf else 0.0) for s, v in scores.items()}, ev)
    best = post.map_source()
    summ = posterior_summary(fam, ev[best], tmodel, phi, prior, seed=seed)
    secs = round(time.time() - t0, 1)

    rest = [s for s in post.sources if s != best]
    results = dict(
        family=fam.name, preset=preset, **params, seconds=secs, seed=seed,
        n_narrators=len(fam.narrators), n_attestations=len(fam.attestations),
        variants=[s.name for s in fam.variant_specs],
        posterior={names[s]: post.posterior[s] for s in post.sources},
        log_Z={names[s]: post.log_Z[s] for s in post.sources},
        ess={names[s]: post.evidences[s].ess for s in post.sources},
        map_source=names[best], map_source_id=best,
        credible_set_95=[names[s] for s in post.credible_set(0.95)],
        flags=flags)
    if rest:
        runner = max(rest, key=lambda s: post.log_Z[s])
        results["bayes_factor_top_vs_next"] = dict(pair=[names[best], names[runner]],
                                                   log_BF=post.log_Z[best] - post.log_Z[runner])
    anc = ancestral_state_posterior(fam, ev[best], tmodel, best, seed=seed)
    results["ancestral_matn_at_map_source"] = {fam.variant_specs[k].name: [round(float(x), 3) for x in v] for k, v in anc.items()}

    links = _links_from_nodes(graph.get("nodes", {}))
    bundle = build_bundle(fam, space, prior, post, summ, best, links=links, texts=fam.texts, seconds=secs,
                          title=graph.get("title", "ICM"), heading=graph.get("heading", fam.name),
                          extras=dict(global_results=results))
    return dict(results=results, bundle=bundle)


def matn_only(graph: dict, texts: Dict[str, str], weights: Sequence[float] = (0.0, 0.1), preset: str = "quick",
              seed: int = 0) -> dict:
    """The sanad term switched off (or down-weighted): how much of the source signal is the matn's?"""
    fam = make_family(graph, texts)
    space = build_candidate_space(fam)
    space.sources = source_set(fam, space)
    names = {n: fam.narrators[n].name for n in fam.narrators}
    names[UNKNOWN] = "UNKNOWN"
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
