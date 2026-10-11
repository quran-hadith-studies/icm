"""Viewer bundle: everything the HTML viewer needs, as one JSON-able dict.

Contains the recorded tree, every narrator with its dates and posterior parent
distribution, every attestation with its matn and encoded row, posterior edge
probabilities and ancestral matn states under the best-supported source, the
MAP graph, the source posteriors, and any extra diagnostics supplied by the
caller. No file paths here: callers pass data in and write the result out.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Sequence

import numpy as np


def finite(obj):
    """JSON has no -Infinity: non-finite numbers become null (read as -inf)."""
    if isinstance(obj, dict):
        return {k: finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [finite(v) for v in obj]
    if isinstance(obj, float) and not np.isfinite(obj):
        return None
    if isinstance(obj, (np.floating, np.integer)):
        v = float(obj)
        return v if np.isfinite(v) else None
    return obj


def build_bundle(fam, space, prior, post, summ, best, links: Optional[dict] = None,
                 texts: Optional[Sequence[str]] = None, title: str = "",
                 heading: str = "", seconds: float = 0.0,
                 extras: Optional[Dict[str, Any]] = None) -> dict:
    """Assemble the viewer bundle. `texts` aligns with fam.attestations (defaults to
    fam.texts); `extras` keys (branch_results, matn_only, global_results,
    recovery_test, ...) are merged in as they are."""
    links = links or {}
    texts = texts if texts is not None else fam.texts
    secs = seconds
    # the recorded tree from the diagram (the structure the viewer draws)
    recorded_parent = {}
    for a in fam.attestations:
        for u, v in zip(a.sanad, a.sanad[1:]):
            recorded_parent.setdefault(v, u)

    kg_n = links.get("narrators", {})
    same = links.get("same_person", {})
    narrators = {}
    for nid, n in fam.narrators.items():
        act = n.activity()
        kg = kg_n.get(nid, {})
        narrators[nid] = dict(
            name=n.name,
            year=round(act[1]) if act else None,
            death=list(n.death) if n.death else None,
            date_source=getattr(n, "date_source", None),
            date_approx=getattr(n, "date_approx", False),
            locations=list(n.locations),
            parent=recorded_parent.get(nid),
            parent_posterior=summ.get("parent_posterior", {}).get(nid, {}),
            ancestral=summ.get("ancestral_states", {}).get(nid),
            map_parent=summ.get("map_graph", {}).get(nid),
            kg=dict(confidence=kg.get("confidence", "none"),
                    record=kg.get("record"), name_score=kg.get("name_score"),
                    context_links=kg.get("context_links"),
                    alternatives=kg.get("alternatives", [])) if kg else None,
            same_person=same.get(nid, []),
        )

    # every recorded edge: support, prior, external evidence, posterior
    support = {}
    for a in fam.attestations:
        for u, v in zip(a.sanad, a.sanad[1:]):
            support.setdefault(f"{u}>{v}", []).append(a.aid)
    edges = {}
    for v, u in recorded_parent.items():
        key = f"{u}>{v}"
        lr = prior.log_rho.get(v, {})
        cand = sorted(((w, float(np.exp(x))) for w, x in lr.items()), key=lambda t: -t[1])
        edges[key] = dict(
            teacher=u, student=v,
            posterior=summ.get("edge_prob", {}).get(key),
            in_map=summ.get("map_graph", {}).get(v) == u,
            prior=float(np.exp(lr[u])) if u in lr else None,
            score=prior.eta(u, v),
            score_parts=dict(
                recorded=(u, v) in space.observed_edges,
                same_city=bool(set(fam.narrators[u].locations) & set(fam.narrators[v].locations)),
                kg_teacher=u in fam.narrators[v].teachers),
            kg_chains=links.get("edge_kg_chains", {}).get(key),
            witnesses=support.get(key, []),
            candidates=[dict(node=w, prior=q) for w, q in cand],
            gap_years=(round(fam.narrators[v].activity()[1] - fam.narrators[u].activity()[1])
                       if fam.narrators[u].activity() and fam.narrators[v].activity() else None),
        )

    attestations = []
    for a, text in zip(fam.attestations, texts):
        attestations.append(dict(
            id=a.aid, sanad=list(a.sanad), anchor=a.anchor, text=text,
            row=list(a.row), collection=a.collection, weight=round(a.weight, 3),
            companion=a.sanad[0]))

    bundle = dict(
        family=fam.name,
        title=title,
        heading=heading,
        generated=time.strftime("%Y-%m-%d"),
        seconds=secs,
        variants=[dict(name=s.name, n_states=s.n_states, cls=s.cls,
                       labels=list(s.labels)) for s in fam.variant_specs],
        narrators=narrators,
        attestations=attestations,
        recorded_parent=recorded_parent,
        edges=edges,
        kg_source=links.get("source"),
        kg_stats=dict(
            linked=sum(1 for v in kg_n.values() if v.get("confidence") in ("high", "medium")),
            high=sum(1 for v in kg_n.values() if v.get("confidence") == "high"),
            total=len(fam.narrators),
            edges_linkable=sum(1 for v in links.get("edge_kg_chains", {}).values() if v is not None),
            edges_attested=sum(1 for v in links.get("edge_kg_chains", {}).values() if v)),
        companions=list(fam.companions),
        source_posterior={s: dict(posterior=post.posterior[s], log_Z=post.log_Z[s],
                                  ess=post.evidences[s].ess,
                                  name=fam.narrators[s].name if s in fam.narrators
                                  else "UNKNOWN — unnamed source above all companions")
                          for s in post.sources},
        map_source=best,
        credible_set_95=post.credible_set(0.95),
        graph_posterior=dict(
            edge_prob=summ.get("edge_prob", {}),
            map_graph=summ.get("map_graph", {}),
            map_log_score=summ.get("map_log_score"),
            map_mass=summ.get("map_mass"),
            n_particles=summ.get("n_particles"),
            n_distinct=summ.get("n_distinct"),
        ),
    )
    bundle.update(extras or {})
    return bundle
