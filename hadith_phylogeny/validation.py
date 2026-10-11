"""Simulation and sensitivity checks (section 15)."""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .candidates import UNKNOWN, build_candidate_space
from .data import Attestation, Family, Narrator
from .graph_model import Graph, GraphPrior
from .inference import calculate_source_posterior
from .sanad_model import SanadModel
from .text_model import NA, VariantModel, VariantSpec


def simulate_dataset(n_leaves: int = 6, depth: int = 3, K: int = 8,
                     branching: int = 2, seed: int = 0,
                     phi: Optional[SanadModel] = None,
                     rates: Optional[Dict] = None) -> Tuple[Family, str, Graph]:
    """Simulate a transmission tree, matns on it, and noisy sanads."""
    rng = np.random.default_rng(seed)
    phi = phi or SanadModel()
    # build a layered tree
    layers: List[List[str]] = [["n0_0"]]
    nid = 1
    for d in range(1, depth + 1):
        prev = layers[-1]
        cur = []
        for p in prev:
            for b in range(branching):
                cur.append(f"n{d}_{len(cur)}")
        layers.append(cur)
    parent: Dict[str, str] = {}
    for d in range(1, len(layers)):
        prev, cur = layers[d - 1], layers[d]
        for i, v in enumerate(cur):
            parent[v] = prev[i // branching]
    root = layers[0][0]
    narrators = {}
    for d, layer in enumerate(layers):
        for v in layer:
            narrators[v] = Narrator(nid=v, name=v, layer=d, locations=("X",))
    leaves = layers[-1][:n_leaves] if n_leaves <= len(layers[-1]) else layers[-1]

    specs = [VariantSpec(name=f"v{k}", n_states=2, cls="lexical") for k in range(K)]
    tmodel = VariantModel(specs)
    # evolve states down the tree
    state: Dict[str, np.ndarray] = {root: rng.integers(0, 2, size=K)}
    order = [v for d in range(1, len(layers)) for v in layers[d]]
    for v in order:
        p = parent[v]
        s = state[p].copy()
        for k in range(K):
            T = tmodel.transition(k, 1.0)
            s[k] = rng.choice(2, p=T[s[k]])
        state[v] = s

    G = Graph(root, dict(parent))
    atts = []
    for i, lf in enumerate(leaves):
        path = G.path_to(lf)
        rec = _corrupt(path, phi, rng, pool=list(narrators))
        atts.append(Attestation(aid=f"a{i}", sanad=tuple(rec), row=tuple(int(x) for x in state[lf]),
                                anchor=lf, collection=f"c{i}", independence_group=f"c{i}"))
    fam = Family("sim", narrators, atts, specs)
    fam.assign_independence_weights()
    G.anchors = {a.anchor: [i] for i, a in enumerate(fam.attestations)}
    return fam, root, G


def _corrupt(path: Sequence[str], phi: SanadModel, rng, pool: Sequence[str]) -> List[str]:
    out = []
    for v in path:
        u = rng.random()
        if u < phi.p_del:
            continue
        if u < phi.p_del + phi.p_sub:
            out.append(str(rng.choice(pool)))
        else:
            out.append(v)
        if rng.random() < phi.p_ins:
            out.append(str(rng.choice(pool)))
    if not out:
        out = list(path)
    return out


def source_recovery_test(n_rep: int = 10, **kw) -> Dict[str, float]:
    """Fraction of simulations where the true source is the MAP source."""
    hits, in95, probs = 0, 0, []
    for r in range(n_rep):
        fam, true_s, _ = simulate_dataset(seed=r, **kw)
        space = build_candidate_space(fam)
        if true_s not in space.sources:
            space.sources.append(true_s)
        post = calculate_source_posterior(fam, space, VariantModel(fam.variant_specs),
                                          SanadModel(), method="importance", n_samples=120,
                                          seed=r)
        hits += int(post.map_source() == true_s)
        in95 += int(true_s in post.credible_set(0.95))
        probs.append(post.posterior.get(true_s, 0.0))
    return dict(map_accuracy=hits / n_rep, coverage95=in95 / n_rep,
                mean_true_posterior=float(np.mean(probs)))


def permutation_test(family: Family, space, tmodel, phi, n_perm: int = 20,
                     seed: int = 0, **kw) -> Dict[str, float]:
    """Permute matns among sanads; the source signal should degrade."""
    rng = np.random.default_rng(seed)
    base = calculate_source_posterior(family, space, tmodel, phi, **kw)
    top = base.map_source()
    null = []
    rows = [a.row for a in family.attestations]
    for _ in range(n_perm):
        idx = rng.permutation(len(rows))
        for a, j in zip(family.attestations, idx):
            a.row = rows[j]
        p = calculate_source_posterior(family, space, tmodel, phi, **kw)
        null.append(p.posterior.get(top, 0.0))
    for a, r in zip(family.attestations, rows):
        a.row = r
    obs = base.posterior[top]
    return dict(source=top, observed=obs, null_mean=float(np.mean(null)),
                p_value=float((np.sum(np.array(null) >= obs) + 1) / (n_perm + 1)))


def prior_sensitivity(family: Family, space, tmodel, phi,
                      priors: Optional[Dict[str, Dict[str, float]]] = None,
                      **kw) -> Dict[str, Dict[str, float]]:
    """Posterior under equal / date-informed / unknown-favouring source priors."""
    S = list(space.sources)
    if priors is None:
        equal = {s: 1.0 / len(S) for s in S}
        unk = {s: (0.5 if s == UNKNOWN else 0.5 / max(len(S) - 1, 1)) for s in S}
        early = {}
        for s in S:
            n = family.narrators.get(s)
            lay = n.layer if n and n.layer is not None else 1
            early[s] = 1.0 / (1.0 + lay)
        tot = sum(early.values())
        early = {s: v / tot for s, v in early.items()}
        priors = {"equal": equal, "favour_unknown": unk, "date_informed": early}
    out = {}
    for name, pr in priors.items():
        post = calculate_source_posterior(family, space, tmodel, phi, source_prior=pr, **kw)
        out[name] = post.posterior
    return out


def leave_one_collection_out(family: Family, tmodel, phi, **kw) -> Dict[str, str]:
    """MAP source with each collection removed in turn."""
    out = {}
    groups = sorted({a.independence_group or a.collection for a in family.attestations})
    for g in groups:
        keep = [a for a in family.attestations
                if (a.independence_group or a.collection) != g]
        if len(keep) < 2:
            continue
        sub = Family(family.name, family.narrators, keep, family.variant_specs)
        sub.assign_independence_weights()
        space = build_candidate_space(sub)
        post = calculate_source_posterior(sub, space, tmodel, phi, **kw)
        out[g] = post.map_source()
    return out
