"""Sanad observation model (section 8).

A recorded chain C_i is a noisy observation of the latent path P_i(G, s).
Two likelihoods are provided:
  * `pair_hmm_log_likelihood` - a proper pair-HMM with MATCH / DELETE (omitted
    intermediary) / INSERT (added narrator) / SUBSTITUTE states, normalised per
    emission, used for the reported results;
  * `weighted_edit_log_likelihood` - the cheap pseudo-likelihood of section 8.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

NEG = -1e18


@dataclass
class SanadModel:
    """phi: omission / insertion / substitution / exact-match parameters."""
    p_match: float = 0.90        # recorded narrator equals the historical one
    p_sub: float = 0.04          # confused with another identity
    p_del: float = 0.04          # historical intermediary omitted from the record
    p_ins: float = 0.02          # extra narrator inserted in the record
    alphabet: int = 2000         # effective number of confusable identities
    mode: str = "pair_hmm"       # pair_hmm | edit
    lambda_del: float = 2.0
    lambda_ins: float = 3.0
    lambda_sub: float = 3.0
    weight: float = 1.0          # overall temperature on the sanad term

    def log_likelihood(self, recorded: Sequence[str], path: Sequence[str]) -> float:
        if self.mode == "edit":
            v = weighted_edit_log_likelihood(recorded, path, self)
        else:
            v = pair_hmm_log_likelihood(recorded, path, self)
        return self.weight * v


def pair_hmm_log_likelihood(recorded: Sequence[str], path: Sequence[str],
                            phi: SanadModel) -> float:
    """Forward algorithm for P(C_i | P_i, phi).

    Emissions: a MATCH emits the historical name with probability p_match and
    some other identity with probability p_sub / (alphabet - 1); a DELETE
    consumes a historical narrator without emitting; an INSERT emits a free
    identity with probability 1 / alphabet.
    """
    n, m = len(path), len(recorded)
    if n == 0:
        return NEG if m else 0.0
    tot = phi.p_match + phi.p_sub + phi.p_del
    lp_match = math.log(max(phi.p_match / tot, 1e-300))
    lp_sub = math.log(max(phi.p_sub / tot, 1e-300)) - math.log(max(phi.alphabet - 1, 1))
    lp_del = math.log(max(phi.p_del / tot, 1e-300))
    lp_ins = math.log(max(phi.p_ins, 1e-300)) - math.log(phi.alphabet)
    lp_noins = math.log(max(1.0 - phi.p_ins, 1e-300))

    # F[i][j] = log P(first j recorded names | first i historical narrators)
    F = np.full((n + 1, m + 1), NEG)
    F[0, 0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            best = NEG
            if i > 0 and j > 0:                      # consume path[i-1], emit rec[j-1]
                e = lp_match if path[i - 1] == recorded[j - 1] else lp_sub
                best = np.logaddexp(best, F[i - 1, j - 1] + lp_noins + e)
            if i > 0:                                # DELETE: omitted intermediary
                best = np.logaddexp(best, F[i - 1, j] + lp_del)
            if j > 0:                                # INSERT: spurious narrator
                best = np.logaddexp(best, F[i, j - 1] + lp_ins)
            F[i, j] = best
    return float(F[n, m])


def weighted_edit_log_likelihood(recorded: Sequence[str], path: Sequence[str],
                                 phi: SanadModel) -> float:
    """-lambda_d n_d - lambda_a n_a - lambda_s n_s (pseudo-likelihood)."""
    n, m = len(path), len(recorded)
    D = np.full((n + 1, m + 1), np.inf)
    D[0, 0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            best = np.inf
            if i > 0 and j > 0:
                c = 0.0 if path[i - 1] == recorded[j - 1] else phi.lambda_sub
                best = min(best, D[i - 1, j - 1] + c)
            if i > 0:
                best = min(best, D[i - 1, j] + phi.lambda_del)
            if j > 0:
                best = min(best, D[i, j - 1] + phi.lambda_ins)
            D[i, j] = best
    return float(-D[n, m])


def extract_true_path(graph, node: str) -> List[str]:
    return graph.path_to(node)


def sanad_log_likelihood(family, graph, phi: SanadModel,
                         cache: Optional[Dict[Tuple[int, Tuple[str, ...]], float]] = None
                         ) -> float:
    """log P(C | G, S=s) = sum_i log P(C_i | P_i(G,s))."""
    total = 0.0
    for i, a in enumerate(family.attestations):
        anchor = a.anchor
        if anchor not in graph.parent and anchor != graph.root:
            # the witness is not reachable from s under this graph
            return -math.inf
        path = tuple(graph.path_to(anchor))
        key = (i, path)
        if cache is not None and key in cache:
            ll = cache[key]
        else:
            ll = phi.log_likelihood(a.sanad, path)
            if cache is not None:
                cache[key] = ll
        total += a.weight * ll
    return total
