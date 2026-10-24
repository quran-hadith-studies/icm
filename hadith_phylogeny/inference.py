"""Evidence Z_s and the source posterior (sections 9-12)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .candidates import UNKNOWN, CandidateSpace, is_unknown_link
from .data import Family
from .graph_model import (Graph, GraphPrior, choices, enumerate_graphs,
                          propose_parent_change, sample_graph_prior)
from .sanad_model import SanadModel, sanad_log_likelihood
from .text_model import VariantModel, pruning_log_likelihood, sample_ancestral_states


def logsumexp(a: Sequence[float]) -> float:
    a = np.asarray([x for x in a if x > -np.inf], dtype=float)
    if a.size == 0:
        return -np.inf
    m = a.max()
    return float(m + np.log(np.exp(a - m).sum()))


@dataclass
class Evidence:
    source: str
    log_Z: float
    method: str
    n_graphs: int = 0
    ess: float = float("nan")
    graphs: List[Graph] = field(default_factory=list, repr=False)
    log_w: List[float] = field(default_factory=list, repr=False)
    trace: Dict = field(default_factory=dict, repr=False)   # SMC: how the run went, step by step


@dataclass
class SourcePosterior:
    sources: List[str]
    log_Z: Dict[str, float]
    log_prior: Dict[str, float]
    posterior: Dict[str, float]
    evidences: Dict[str, Evidence] = field(default_factory=dict, repr=False)

    def map_source(self) -> str:
        return max(self.posterior, key=self.posterior.get)

    def credible_set(self, mass: float = 0.95) -> List[str]:
        out, tot = [], 0.0
        for s, p in sorted(self.posterior.items(), key=lambda kv: -kv[1]):
            out.append(s)
            tot += p
            if tot >= mass:
                break
        return out

    def bayes_factor(self, s1: str, s2: str) -> float:
        return math.exp(self.log_Z[s1] - self.log_Z[s2])

    def table(self) -> str:
        rows = ["| Source | log Z | Posterior |", "|---|---:|---:|"]
        for s, p in sorted(self.posterior.items(), key=lambda kv: -kv[1]):
            rows.append(f"| {s} | {self.log_Z[s]:.2f} | {p:.4f} |")
        return "\n".join(rows)


def _log_joint(family: Family, graph: Graph, tmodel: VariantModel,
               phi: SanadModel, prior: GraphPrior, X: np.ndarray,
               weights: np.ndarray, sanad_cache=None
               ) -> Tuple[float, float, float]:
    lt = pruning_log_likelihood(X, graph, tmodel, leaf_weights=weights)
    ls = sanad_log_likelihood(family, graph, phi, cache=sanad_cache)
    lg = prior.log_prior(graph)
    return lt, ls, lg


# ---- section 10: exact enumeration ---------------------------------------
def exact_source_evidence(family: Family, space: CandidateSpace, s: str,
                          tmodel: VariantModel, phi: SanadModel,
                          prior: Optional[GraphPrior] = None,
                          limit: int = 200000, keep_graphs: bool = False) -> Evidence:
    prior = prior or GraphPrior(family, space)
    X = family.X
    w = np.array([a.weight for a in family.attestations])
    terms, graphs, cache, priors = [], [], {}, []
    n = 0
    for G in enumerate_graphs(prior, s, space.anchors, limit=limit):
        lt, ls, lg = _log_joint(family, G, tmodel, phi, prior, X, w, cache)
        priors.append(lg)
        v = lt + ls + lg
        if v > -np.inf:
            terms.append(v)
            if keep_graphs:
                graphs.append(G)
        n += 1
    # P(G|s,M) is the product prior renormalised over the valid trees of s, the
    # same distribution the samplers draw from
    log_K = logsumexp(priors)
    return Evidence(s, logsumexp(terms) - log_K, "exact", n, float("nan"), graphs,
                    [t - log_K for t in terms])


# ---- section 10: importance sampling -------------------------------------
def importance_source_evidence(family: Family, space: CandidateSpace, s: str,
                               tmodel: VariantModel, phi: SanadModel,
                               prior: Optional[GraphPrior] = None,
                               n_samples: int = 500, seed: int = 0,
                               keep_graphs: bool = True) -> Evidence:
    """q(G|s) = P(G|s,M) so that w_j = P(X|G_j,s) P(C|G_j,s)."""
    prior = prior or GraphPrior(family, space)
    rng = np.random.default_rng(seed)
    X = family.X
    w = np.array([a.weight for a in family.attestations])
    logw, graphs, cache = [], [], {}
    for _ in range(n_samples):
        G, logq = sample_graph_prior(prior, s, space.anchors, rng)
        if G is None:
            continue
        lt, ls, lg = _log_joint(family, G, tmodel, phi, prior, X, w, cache)
        # proposal == prior: the prior term cancels exactly
        val = lt + ls + (lg - logq)
        logw.append(val)
        if keep_graphs:
            graphs.append(G)
    if not logw:
        return Evidence(s, -np.inf, "importance", 0, 0.0)
    lZ = logsumexp(logw) - math.log(len(logw))
    return Evidence(s, lZ, "importance", len(logw), effective_sample_size(logw), graphs, logw)


def effective_sample_size(logw: Sequence[float]) -> float:
    a = np.asarray([x for x in logw if np.isfinite(x)], dtype=float)
    if a.size == 0:
        return 0.0
    a = a - a.max()
    w = np.exp(a)
    if w.sum() <= 0:
        return 0.0
    wt = w / w.sum()
    return float(1.0 / np.sum(wt ** 2))


# ---- section 10: annealed SMC --------------------------------------------
def smc_source_evidence(family: Family, space: CandidateSpace, s: str,
                        tmodel: VariantModel, phi: SanadModel,
                        prior: Optional[GraphPrior] = None,
                        n_particles: int = 200, n_steps: int = 10,
                        n_mcmc: int = 3, seed: int = 0,
                        ess_threshold: float = 0.5) -> Evidence:
    """p_gamma(G) ∝ P(G|s,M) [P(X|G,s) P(C|G,s)]^gamma, gamma: 0 -> 1."""
    prior = prior or GraphPrior(family, space)
    rng = np.random.default_rng(seed)
    X = family.X
    wts = np.array([a.weight for a in family.attestations])
    cache: Dict = {}

    def loglik(G: Graph) -> float:
        lt = pruning_log_likelihood(X, G, tmodel, leaf_weights=wts)
        ls = sanad_log_likelihood(family, G, phi, cache=cache)
        return lt + ls

    particles, ll = [], []
    n_tried = 0
    for _ in range(n_particles):
        G, _q = sample_graph_prior(prior, s, space.anchors, rng)
        n_tried += 1
        if G is None:
            continue
        v = loglik(G)
        if not np.isfinite(v):
            continue          # the witnesses are unreachable from s under this graph
        particles.append(G)
        ll.append(v)
    if not particles:
        return Evidence(s, -np.inf, "smc", 0, 0.0, trace=dict(tried=n_tried, survived=0, steps=[]))
    ll = np.array(ll)
    logw = np.zeros(len(particles))
    # the discarded draws carry zero likelihood: Z is scaled by the share that survived
    log_Z = float(np.log(len(particles) / n_tried))
    # per temperature: [gamma, log Z so far, ESS / N before resampling, resampled, MCMC acceptance, distinct trees]
    steps: List[list] = []
    gammas = np.linspace(0, 1, n_steps + 1)
    for t in range(1, len(gammas)):
        d = gammas[t] - gammas[t - 1]
        inc = d * ll
        log_Z += logsumexp(logw + inc) - logsumexp(logw)
        logw = logw + inc
        ess = effective_sample_size(logw)
        resampled = ess < ess_threshold * len(particles)
        if resampled:
            p = np.exp(logw - logw.max())
            p /= p.sum()
            idx = rng.choice(len(particles), size=len(particles), p=p)
            particles = [particles[i] for i in idx]
            ll = ll[idx]
            logw = np.zeros(len(particles))
        g = gammas[t]
        lprior = np.array([prior.log_prior(G) for G in particles])
        n_prop = n_acc = 0
        for i, G in enumerate(particles):          # parent-reassignment MCMC
            for _ in range(n_mcmc):
                n_prop += 1
                P, _r = propose_parent_change(G, prior, rng)
                if P is None:
                    continue
                lp_new = prior.log_prior(P)
                if lp_new == -np.inf:
                    continue
                ll_new = loglik(P)
                if not np.isfinite(ll_new):
                    continue
                a = (lp_new + g * ll_new) - (lprior[i] + g * ll[i])
                if math.log(rng.random() + 1e-300) < a:
                    G, ll[i], lprior[i] = P, ll_new, lp_new
                    n_acc += 1
            particles[i] = G
        distinct = len({tuple(sorted(G.parent.items())) for G in particles})
        steps.append([round(float(g), 4), round(float(log_Z), 3), round(ess / len(particles), 3), bool(resampled),
                      round(n_acc / n_prop, 3) if n_prop else None, distinct])
    return Evidence(s, float(log_Z), "smc", len(particles),
                    effective_sample_size(logw), particles, list(logw),
                    trace=dict(tried=n_tried, survived=len(particles), steps=steps))


# ---- section 9 -----------------------------------------------------------
def calculate_source_posterior(family: Family, space: CandidateSpace,
                               tmodel: VariantModel, phi: SanadModel,
                               prior: Optional[GraphPrior] = None,
                               method: str = "auto",
                               source_prior: Optional[Dict[str, float]] = None,
                               enumeration_limit: int = 20000,
                               **kwargs) -> SourcePosterior:
    prior = prior or GraphPrior(family, space)
    ev: Dict[str, Evidence] = {}
    for s in space.sources:
        m = method
        if m == "auto":
            psets = prior.reachable_parent_sets(s)
            size = 1.0
            for v, ps in psets.items():
                size *= max(len(ps), 1)
                if size > enumeration_limit:
                    break
            m = "exact" if size <= enumeration_limit else "importance"
        if m == "exact":
            ev[s] = exact_source_evidence(family, space, s, tmodel, phi, prior,
                                          limit=max(enumeration_limit, 1), **kwargs)
        elif m == "smc":
            ev[s] = smc_source_evidence(family, space, s, tmodel, phi, prior, **kwargs)
        else:
            ev[s] = importance_source_evidence(family, space, s, tmodel, phi, prior, **kwargs)

    if source_prior is None:
        source_prior = {s: 1.0 / len(space.sources) for s in space.sources}
    lp = {s: math.log(max(source_prior.get(s, 1e-300), 1e-300)) for s in space.sources}
    scores = {s: lp[s] + ev[s].log_Z for s in space.sources}
    tot = logsumexp(list(scores.values()))
    post = {s: (math.exp(v - tot) if tot > -np.inf else 0.0) for s, v in scores.items()}
    return SourcePosterior(list(space.sources), {s: ev[s].log_Z for s in space.sources},
                           lp, post, ev)


# ---- section 11 ----------------------------------------------------------
def ancestral_state_posterior(family: Family, evidence: Evidence,
                              tmodel: VariantModel, node: str,
                              seed: int = 0) -> Dict[int, np.ndarray]:
    """P(A_{v,k}=a | D) averaged over weighted posterior graphs."""
    rng = np.random.default_rng(seed)
    X = family.X
    if not evidence.graphs:
        return {}
    lw = np.asarray(evidence.log_w, dtype=float)
    lw = lw - lw.max()
    w = np.exp(lw)
    w /= w.sum()
    out: Dict[int, np.ndarray] = {k: np.zeros(tmodel.specs[k].n_states)
                                 for k in range(X.shape[1])}
    for G, wj in zip(evidence.graphs, w):
        if wj <= 0 or (node != G.root and node not in G.parent):
            continue
        A = sample_ancestral_states(X, G, tmodel, rng)
        for k in range(X.shape[1]):
            out[k][A[node][k]] += wj
    for k in out:
        tot = out[k].sum()
        if tot > 0:
            out[k] /= tot
    return out


# ---- posterior summaries over sampled graphs -----------------------------
def posterior_summary(family: Family, evidence: Evidence, tmodel: VariantModel,
                      phi: SanadModel, prior: GraphPrior, seed: int = 0,
                      n_ancestral: int = 1) -> Dict:
    """Edge posteriors, the MAP graph, and ancestral states for every node.

    The particles of an SMC run (or the weighted draws of an importance run) are
    the posterior sample; weights are the normalised log_w.
    """
    rng = np.random.default_rng(seed)
    X = family.X
    w = np.array([a.weight for a in family.attestations])
    graphs = evidence.graphs
    if not graphs:
        return {}
    lw = np.asarray(evidence.log_w, dtype=float)
    lw = np.where(np.isfinite(lw), lw, -np.inf)
    if not np.isfinite(lw).any():
        wt = np.full(len(graphs), 1.0 / len(graphs))
    else:
        a = lw - np.nanmax(lw[np.isfinite(lw)])
        e = np.exp(np.where(np.isfinite(a), a, -np.inf))
        wt = e / e.sum()

    edge_prob: Dict[Tuple[str, str], float] = {}
    node_parent: Dict[str, Dict[str, float]] = {}
    anc: Dict[str, np.ndarray] = {}
    best, best_score, best_idx = None, -np.inf, -1
    for j, (G, pj) in enumerate(zip(graphs, wt)):
        if pj <= 0:
            continue
        # an unnamed link belongs to its one student: reported as v's parent UNKNOWN
        for v, p in choices(G.parent).items():
            edge_prob[(p, v)] = edge_prob.get((p, v), 0.0) + pj
            node_parent.setdefault(v, {})
            node_parent[v][p] = node_parent[v].get(p, 0.0) + pj
        score = (prior.log_prior(G)
                 + pruning_log_likelihood(X, G, tmodel, leaf_weights=w)
                 + sanad_log_likelihood(family, G, phi))
        if score > best_score:
            best, best_score, best_idx = G, score, j
        for _ in range(n_ancestral):
            A = sample_ancestral_states(X, G, tmodel, rng)
            for node, vals in A.items():
                if is_unknown_link(node):
                    continue
                if node not in anc:
                    anc[node] = np.zeros((X.shape[1], max(s.n_states for s in tmodel.specs)))
                for k, a_ in enumerate(vals):
                    anc[node][k, int(a_)] += pj / n_ancestral

    states = {}
    for node, mat in anc.items():
        states[node] = []
        for k, spec in enumerate(tmodel.specs):
            row = mat[k, :spec.n_states]
            tot = row.sum()
            states[node].append([float(x / tot) if tot > 0 else 0.0 for x in row])

    # how much posterior mass sits on the single best (MAP) graph
    map_mass = float(sum(pj for G, pj in zip(graphs, wt)
                         if G.parent == (best.parent if best else None)))
    return dict(
        edge_prob={f"{u}>{v}": float(p) for (u, v), p in edge_prob.items()},
        parent_posterior={v: {u: float(p) for u, p in d.items()}
                          for v, d in node_parent.items()},
        ancestral_states=states,
        map_graph=choices(best.parent) if best else {},
        map_log_score=float(best_score),
        map_mass=map_mass,
        n_particles=len(graphs),
        n_distinct=len({tuple(sorted(G.parent.items())) for G in graphs}),
    )
