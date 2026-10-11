"""Probabilistic inference of the earliest recoverable source of a hadith family.

Implements the model of P(S=s | X, C, M): a latent root (source) variable over
a space of transmission arborescences, with
  * matn variation as a finite-state Markov process on the tree (ancestral
    states marginalised by Felsenstein pruning), and
  * recorded sanads as noisy observations of latent root-to-witness paths
    (pair-HMM or weighted-edit likelihood).

Per-source marginal likelihoods Z_s are obtained by exact enumeration,
importance sampling, or annealed SMC over graphs.
"""

from .data import Attestation, Family, Narrator, load_family, load_metadata
from .candidates import CandidateSpace, build_candidate_space
from .text_model import VariantModel, VariantSpec, pruning_log_likelihood, sample_ancestral_states
from .sanad_model import SanadModel, sanad_log_likelihood
from .graph_model import Graph, GraphPrior, enumerate_graphs, sample_graph_prior
from .inference import (
    SourcePosterior,
    calculate_source_posterior,
    exact_source_evidence,
    importance_source_evidence,
    smc_source_evidence,
)

__all__ = [
    "Attestation", "Family", "Narrator", "load_family", "load_metadata",
    "CandidateSpace", "build_candidate_space",
    "VariantModel", "VariantSpec", "pruning_log_likelihood", "sample_ancestral_states",
    "SanadModel", "sanad_log_likelihood",
    "Graph", "GraphPrior", "enumerate_graphs", "sample_graph_prior",
    "SourcePosterior", "calculate_source_posterior",
    "exact_source_evidence", "importance_source_evidence", "smc_source_evidence",
]
