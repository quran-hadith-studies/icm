"""Matn evolution: finite-state Markov process on the transmission tree.

Ancestral states A are never reconstructed before the likelihood: they are
summed out by Felsenstein pruning (section 7). Ancestral sampling (section 11)
is a separate backward pass run only after graph inference.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

NA = -1


@dataclass
class VariantSpec:
    """One column k of X."""
    name: str
    n_states: int = 2
    cls: str = "lexical"          # rate class r
    kind: str = "presence"        # presence | lexical | entity | numeric | narrative
    labels: Tuple[str, ...] = ()


@dataclass
class RateClass:
    """Rate parameters for one variant class r."""
    gain: float = 0.05    # lambda^+ (addition), binary only
    loss: float = 0.05    # lambda^- (omission), binary only
    mu: float = 0.05      # per-unit-branch substitution probability, multistate


DEFAULT_RATES: Dict[str, RateClass] = {
    "orthography": RateClass(0.15, 0.15, 0.15),
    "lexical":     RateClass(0.08, 0.08, 0.08),
    "omission":    RateClass(0.02, 0.12, 0.08),
    "addition":    RateClass(0.12, 0.02, 0.08),
    "entity":      RateClass(0.04, 0.04, 0.04),
    "numeric":     RateClass(0.03, 0.03, 0.03),
    "narrative":   RateClass(0.10, 0.03, 0.08),
}


class VariantModel:
    """Transition matrices T_r(t) = exp(Q_r t) per variant class."""

    def __init__(self, specs: Sequence[VariantSpec],
                 rates: Optional[Dict[str, RateClass]] = None,
                 root_prior: str = "uniform"):
        self.specs = list(specs)
        self.rates = dict(DEFAULT_RATES)
        if rates:
            self.rates.update(rates)
        self.root_prior = root_prior
        self._cache: Dict[Tuple[int, float], np.ndarray] = {}

    # ---- section 6 -------------------------------------------------------
    def transition(self, k: int, t: float) -> np.ndarray:
        key = (k, round(float(t), 6))
        if key in self._cache:
            return self._cache[key]
        spec = self.specs[k]
        r = self.rates.get(spec.cls, DEFAULT_RATES["lexical"])
        if spec.n_states == 2:
            T = _binary_expm(r.gain, r.loss, t)
        else:
            m = spec.n_states
            # equal-rate (Jukes-Cantor style) scaled so that at t = 1 the total
            # substitution probability equals mu
            keep = max(1e-12, 1.0 - r.mu * m / (m - 1.0))
            decay = keep ** t
            p_same = 1.0 / m + (1.0 - 1.0 / m) * decay
            p_diff = (1.0 - p_same) / (m - 1.0)
            T = np.full((m, m), p_diff)
            np.fill_diagonal(T, p_same)
        self._cache[key] = T
        return T

    def root_distribution(self, k: int) -> np.ndarray:
        spec = self.specs[k]
        if self.root_prior == "stationary" and spec.n_states == 2:
            r = self.rates.get(spec.cls, DEFAULT_RATES["lexical"])
            tot = r.gain + r.loss
            return np.array([r.loss / tot, r.gain / tot])
        return np.full(spec.n_states, 1.0 / spec.n_states)


def _binary_expm(lam_plus: float, lam_minus: float, t: float) -> np.ndarray:
    """exp(Q t) for Q = [[-l+, l+], [l-, -l-]] in closed form."""
    s = lam_plus + lam_minus
    if s <= 0:
        return np.eye(2)
    e = np.exp(-s * t)
    pi0, pi1 = lam_minus / s, lam_plus / s
    return np.array([[pi0 + pi1 * e, pi1 * (1 - e)],
                     [pi0 * (1 - e), pi1 + pi0 * e]])


# ---- section 7 -----------------------------------------------------------
def pruning_log_likelihood(X: np.ndarray, tree, model: VariantModel,
                           leaf_weights: Optional[np.ndarray] = None,
                           branch_lengths: Optional[Dict[Tuple[str, str], float]] = None,
                           leaf_branch: float = 1.0) -> float:
    """log P(X | G, S=s), summing over all ancestral matn states A.

    `tree` must expose .root, .children(v) and .leaves_at(v) -> row indices of X
    observed at node v.
    """
    total = 0.0
    for k in range(X.shape[1]):
        total += _prune_column(X[:, k], k, tree, model, leaf_weights, branch_lengths,
                               leaf_branch)
    return total


def _leaf_vector(x: int, m: int) -> np.ndarray:
    if x == NA or x < 0 or x >= m:
        return np.ones(m)          # missing: uninformative, never "absent"
    v = np.zeros(m)
    v[x] = 1.0
    return v


def _prune_column(col: np.ndarray, k: int, tree, model: VariantModel,
                  leaf_weights, branch_lengths, leaf_branch: float = 1.0) -> float:
    spec = model.specs[k]
    m = spec.n_states
    order = tree.postorder()
    L: Dict[str, np.ndarray] = {}
    scale_log = 0.0
    for v in order:
        vec = np.ones(m)
        for i in tree.leaves_at(v):
            # a surviving copy hangs off v by its own (written transmission) branch,
            # so two different copies at one narrator are not a contradiction
            lv = model.transition(k, leaf_branch) @ _leaf_vector(int(col[i]), m)
            w = 1.0 if leaf_weights is None else float(leaf_weights[i])
            vec = vec * (lv ** w if w != 1.0 else lv)
        for c in tree.children(v):
            t = 1.0
            if branch_lengths is not None:
                t = branch_lengths.get((v, c), 1.0)
            T = model.transition(k, t)
            vec = vec * (T @ L[c])
        s = vec.sum()
        if s <= 0:
            return -np.inf
        if s < 1e-200 or s > 1e200:
            vec = vec / s
            scale_log += np.log(s)
        L[v] = vec
    pi = model.root_distribution(k)
    val = float(pi @ L[tree.root])
    if val <= 0:
        return -np.inf
    return float(np.log(val) + scale_log)


# ---- section 11 ----------------------------------------------------------
def sample_ancestral_states(X: np.ndarray, tree, model: VariantModel,
                            rng: np.random.Generator,
                            branch_lengths=None,
                            leaf_branch: float = 1.0) -> Dict[str, np.ndarray]:
    """Backward (pre-order) sampling of A given X and one graph."""
    K = X.shape[1]
    nodes = tree.postorder()
    out = {v: np.zeros(K, dtype=int) for v in nodes}
    for k in range(K):
        m = model.specs[k].n_states
        L: Dict[str, np.ndarray] = {}
        for v in nodes:
            vec = np.ones(m)
            for i in tree.leaves_at(v):
                vec = vec * (model.transition(k, leaf_branch) @ _leaf_vector(int(X[i, k]), m))
            for c in tree.children(v):
                t = 1.0 if branch_lengths is None else branch_lengths.get((v, c), 1.0)
                vec = vec * (model.transition(k, t) @ L[c])
            s = vec.sum()
            L[v] = vec / s if s > 0 else vec
        pi = model.root_distribution(k)
        p = pi * L[tree.root]
        p = p / p.sum()
        out[tree.root][k] = rng.choice(m, p=p)
        stack = [tree.root]
        while stack:
            v = stack.pop()
            a = out[v][k]
            for c in tree.children(v):
                t = 1.0 if branch_lengths is None else branch_lengths.get((v, c), 1.0)
                p = model.transition(k, t)[a] * L[c]
                p = p / p.sum()
                out[c][k] = rng.choice(m, p=p)
                stack.append(c)
    return out
