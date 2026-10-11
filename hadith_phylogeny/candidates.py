"""Candidate edge space G* and candidate source set S (sections 3-4)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .data import Family, Narrator

UNKNOWN = "UNKNOWN_SOURCE"


def unknown_link(v: str) -> str:
    """The unnamed intermediary above v alone: choosing UNKNOWN as v's parent never makes v
    share an unnamed teacher (or that teacher's text) with any other narrator."""
    return f"{UNKNOWN}#{v}"


def is_unknown_link(n: str) -> bool:
    return n.startswith(UNKNOWN + "#")


@dataclass
class CandidateSpace:
    nodes: List[str]
    parents: Dict[str, List[str]]          # P_v
    sources: List[str]
    observed_edges: Set[Tuple[str, str]]   # edges seen in some recorded sanad
    anchors: Dict[str, List[int]]          # node -> indices of X rows observed there
    order: Dict[str, float]                # a time key used for acyclicity

    def edge_count(self) -> int:
        return sum(len(p) for p in self.parents.values())


def _time_key(n: Optional[Narrator], fallback: float) -> float:
    if n is None:
        return fallback
    if n.layer is not None:
        return float(n.layer)
    act = n.activity() if hasattr(n, "activity") else None
    if act:
        return float(act[1])
    return fallback


def compatible(u: Narrator, v: Narrator, min_overlap: float = 0.0) -> bool:
    """Can u plausibly transmit to v? (chronology, then geography)."""
    if u.nid == v.nid:
        return False
    if u.layer is not None and v.layer is not None:
        if u.layer >= v.layer:
            return False
    au, av = u.activity(), v.activity()
    if au and av:
        if au[0] > av[1]:            # u strictly later than v
            return False
        overlap = min(au[1], av[1]) - max(au[0], av[0])
        if overlap < min_overlap:
            return False
    if u.locations and v.locations and not set(u.locations) & set(v.locations):
        # different cities are allowed only with explicit teacher/meeting evidence
        if v.nid not in u.meetings and u.nid not in v.teachers:
            return False
    return True


def build_candidate_space(family: Family,
                          sources: Optional[Sequence[str]] = None,
                          allow_unknown: bool = True,
                          extra_parents: int = 2,
                          min_overlap: float = 0.0) -> CandidateSpace:
    """Build P_v for every node, from recorded sanads plus plausible alternatives.

    Recorded edges are always candidates. Additional candidates per node are the
    recorded grandparent (an omitted intermediary) and parents attested for the
    same narrator in other chains, filtered by `compatible`.
    """
    nodes = list(dict.fromkeys(family.nodes()))
    meta = family.narrators
    observed: Set[Tuple[str, str]] = set()
    recorded_parents: Dict[str, Set[str]] = {n: set() for n in nodes}
    grand: Dict[str, Set[str]] = {n: set() for n in nodes}
    anchors: Dict[str, List[int]] = {n: [] for n in nodes}

    for i, a in enumerate(family.attestations):
        chain = list(a.sanad)
        for j in range(1, len(chain)):
            observed.add((chain[j - 1], chain[j]))
            recorded_parents[chain[j]].add(chain[j - 1])
            if j >= 2:
                grand[chain[j]].add(chain[j - 2])
        anchors.setdefault(a.anchor, []).append(i)

    def narr(n: str) -> Narrator:
        return meta.get(n) or Narrator(nid=n, name=n)

    parents: Dict[str, List[str]] = {}
    for v in nodes:
        cand = set(recorded_parents[v])
        if extra_parents > 0:
            # the recorded grand-teachers: choosing one means the record dropped
            # an intermediary
            pool = set(grand[v])
            for p in list(recorded_parents[v]):
                pool |= recorded_parents.get(p, set())
            pool -= cand | {v}
            ok = [u for u in sorted(pool) if compatible(narr(u), narr(v), min_overlap)]
            cand |= set(ok[:extra_parents])
        if allow_unknown:
            cand.add(UNKNOWN)
        parents[v] = sorted(cand)

    if allow_unknown:
        nodes = nodes + [UNKNOWN]
        parents[UNKNOWN] = []
        anchors.setdefault(UNKNOWN, [])

    order = {}
    for idx, n in enumerate(nodes):
        order[n] = _time_key(meta.get(n), float(idx))
    order[UNKNOWN] = -1e9

    if sources is None:
        sources = define_source_candidates(family, observed, allow_unknown)
    return CandidateSpace(nodes, parents, list(sources), observed, anchors, order)


def define_source_candidates(family: Family, observed: Set[Tuple[str, str]],
                             allow_unknown: bool = True,
                             min_branching: int = 2) -> List[str]:
    """Traditional claimant, common links, partial common links, UNKNOWN."""
    out: List[str] = []
    # the narrator each sanad starts from (traditional claim)
    for a in family.attestations:
        if a.sanad and a.sanad[0] not in out:
            out.append(a.sanad[0])
    # common links: nodes with >= min_branching distinct recorded students
    children: Dict[str, Set[str]] = {}
    for u, v in observed:
        children.setdefault(u, set()).add(v)
    for u, cs in sorted(children.items(), key=lambda kv: -len(kv[1])):
        if len(cs) >= min_branching and u not in out:
            out.append(u)
    if allow_unknown:
        out.append(UNKNOWN)
    return out
