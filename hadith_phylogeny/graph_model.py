"""Transmission graphs: prior, enumeration, prior sampling, MCMC moves (section 5, 10)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import numpy as np

from .candidates import UNKNOWN, CandidateSpace
from .data import Family, Narrator

# section 5: explicit edge-score rules for the first version
SCORE_SAME_LOCATION = 1.0
SCORE_TEACHER_STUDENT = 2.0
SCORE_MEETING = 2.0
SCORE_RECORDED = 3.0        # "strong external evidence": the edge is in a sanad
SCORE_UNKNOWN_PARENT = -4.0  # inserting an unknown intermediary is costly


@dataclass
class Graph:
    """A rooted arborescence: parent map plus derived child lists."""
    root: str
    parent: Dict[str, str]
    _children: Dict[str, List[str]] = field(default_factory=dict, repr=False)
    anchors: Dict[str, List[int]] = field(default_factory=dict, repr=False)
    _order: Optional[List[str]] = field(default=None, repr=False)

    def __post_init__(self):
        ch: Dict[str, List[str]] = {}
        for v, p in self.parent.items():
            ch.setdefault(p, []).append(v)
        self._children = ch

    def children(self, v: str) -> List[str]:
        return self._children.get(v, [])

    def nodes(self) -> List[str]:
        return [self.root] + list(self.parent)

    def leaves_at(self, v: str) -> List[int]:
        return self.anchors.get(v, [])

    def postorder(self) -> List[str]:
        if self._order is not None:
            return self._order
        out: List[str] = []
        stack = [(self.root, False)]
        while stack:
            v, done = stack.pop()
            if done:
                out.append(v)
                continue
            stack.append((v, True))
            for c in self.children(v):
                stack.append((c, False))
        self._order = out
        return out

    def path_to(self, v: str) -> List[str]:
        """Root-to-node path P_i(G, s)."""
        out = [v]
        while out[-1] != self.root:
            out.append(self.parent[out[-1]])
        return list(reversed(out))


class GraphPrior:
    """P(G | S=s, M) = prod_v rho_{pa(v),v} over a valid arborescence."""

    def __init__(self, family: Family, space: CandidateSpace,
                 beta: Optional[Dict[str, float]] = None):
        self.family = family
        self.space = space
        self.beta = beta or {}
        self.log_rho: Dict[str, Dict[str, float]] = {}
        for v, ps in space.parents.items():
            if not ps:
                continue
            etas = {u: self.eta(u, v) for u in ps}
            mx = max(etas.values())
            z = math.log(sum(math.exp(e - mx) for e in etas.values())) + mx
            self.log_rho[v] = {u: e - z for u, e in etas.items()}
        # the unnamed intermediary has a deterministic parent (the root)
        self.log_rho.setdefault(UNKNOWN, {})

    def narr(self, n: str) -> Narrator:
        return self.family.narrators.get(n) or Narrator(nid=n, name=n)

    def eta(self, u: str, v: str) -> float:
        """eta_uv = b0 + b1 T_uv + b2 L_uv + b3 R_uv."""
        if u == UNKNOWN:
            return SCORE_UNKNOWN_PARENT
        nu, nv = self.narr(u), self.narr(v)
        s = 0.0
        if (u, v) in self.space.observed_edges:
            s += self.beta.get("recorded", SCORE_RECORDED)
        if nu.locations and nv.locations and set(nu.locations) & set(nv.locations):
            s += self.beta.get("location", SCORE_SAME_LOCATION)
        if u in nv.teachers:
            s += self.beta.get("teacher", SCORE_TEACHER_STUDENT)
        if v in nu.meetings or u in nv.meetings:
            s += self.beta.get("meeting", SCORE_MEETING)
        return s

    # ---- P(G | s, M) -----------------------------------------------------
    def log_prior(self, graph: Graph) -> float:
        tot = 0.0
        for v, p in graph.parent.items():
            if v == UNKNOWN:
                continue                 # deterministic: pa(UNKNOWN) = root
            lr = self.log_rho.get(v, {})
            if p not in lr:
                return -math.inf
            tot += lr[p]
        return tot

    def active_nodes(self, s: str) -> List[str]:
        """Nodes allowed to appear in a tree rooted at s.

        No node that is strictly earlier than s can descend from s. The optional
        UNKNOWN node (assumption 7) is always available as a single unnamed
        intermediary hanging directly below the root.
        """
        order = self.space.order
        out = [s]
        for n in self.space.nodes:
            if n == s or n == UNKNOWN:
                continue
            if order.get(n, 0.0) > order.get(s, 0.0):
                out.append(n)
        if s != UNKNOWN and UNKNOWN in self.space.nodes:
            out.append(UNKNOWN)
        return out

    def reachable_parent_sets(self, s: str) -> Dict[str, List[str]]:
        act = set(self.active_nodes(s))
        out: Dict[str, List[str]] = {}
        for v in act:
            if v == s:
                continue
            if v == UNKNOWN:
                out[v] = [s]          # the unnamed intermediary sits below s
                continue
            ps = [u for u in self.space.parents.get(v, []) if u in act and u != v]
            out[v] = ps
        return out


def _connected(parent: Dict[str, str], root: str) -> bool:
    seen = {root}
    changed = True
    nodes = list(parent)
    while changed:
        changed = False
        for v in nodes:
            if v not in seen and parent[v] in seen:
                seen.add(v)
                changed = True
    return len(seen) == len(nodes) + 1


def enumerate_graphs(prior: GraphPrior, s: str, anchors: Dict[str, List[int]],
                     limit: int = 200000) -> Iterator[Graph]:
    """Exact enumeration of valid arborescences rooted at s (section 10)."""
    psets = prior.reachable_parent_sets(s)
    vs = sorted(psets)
    if any(not psets[v] for v in vs):
        return
    total = 1
    for v in vs:
        total *= len(psets[v])
        if total > limit:
            raise ValueError(f"graph space too large for enumeration: >{limit}")
    count = 0
    assign: Dict[str, str] = {}

    def rec(i: int):
        nonlocal count
        if i == len(vs):
            if _connected(assign, s):
                count += 1
                yield Graph(s, dict(assign), anchors=anchors)
            return
        v = vs[i]
        for u in psets[v]:
            assign[v] = u
            yield from rec(i + 1)
        assign.pop(v, None)

    yield from rec(0)


def sample_graph_prior(prior: GraphPrior, s: str, anchors: Dict[str, List[int]],
                       rng: np.random.Generator, max_tries: int = 200
                       ) -> Tuple[Optional[Graph], float]:
    """Draw G ~ q(G|s) = P(G|s,M) restricted to valid trees.

    Returns (graph, log q). Rejection on cycles; cycles are rare because edges
    point forward in time, but the UNKNOWN parent can create disconnection.
    """
    psets = prior.reachable_parent_sets(s)
    order = prior.space.order
    for _ in range(max_tries):
        assign: Dict[str, str] = {}
        logq = 0.0
        ok = True
        for v, ps in psets.items():
            if not ps:
                ok = False
                break
            lp = np.array([prior.log_rho.get(v, {}).get(u, 0.0) for u in ps])
            lp = lp - lp.max()
            p = np.exp(lp)
            p /= p.sum()
            j = int(rng.choice(len(ps), p=p))
            assign[v] = ps[j]
            logq += float(np.log(p[j]))
        if not ok:
            continue
        if _acyclic(assign, s, order) and _connected(assign, s):
            return Graph(s, assign, anchors=anchors), logq
    return None, -math.inf


def _acyclic(parent: Dict[str, str], root: str, order: Dict[str, float]) -> bool:
    seen = set()
    for v in parent:
        path = set()
        cur = v
        while cur != root:
            if cur in path:
                return False
            path.add(cur)
            if cur in seen:
                break
            cur = parent.get(cur)
            if cur is None:
                return False
        seen |= path
    return True


def propose_parent_change(graph: Graph, prior: GraphPrior, rng: np.random.Generator
                          ) -> Tuple[Optional[Graph], float]:
    """Single parent reassignment move; returns (proposal, log q-ratio)."""
    vs = [v for v in graph.parent if len(prior.space.parents.get(v, [])) > 1]
    if not vs:
        return None, 0.0
    v = vs[int(rng.integers(len(vs)))]
    psets = prior.reachable_parent_sets(graph.root)
    opts = [u for u in psets.get(v, []) if u != graph.parent[v]]
    if not opts:
        return None, 0.0
    u = opts[int(rng.integers(len(opts)))]
    newp = dict(graph.parent)
    newp[v] = u
    if not _acyclic(newp, graph.root, prior.space.order) or not _connected(newp, graph.root):
        return None, 0.0
    return Graph(graph.root, newp, anchors=graph.anchors), 0.0


def graph_from_sanads(family: Family, s: str, anchors: Dict[str, List[int]]) -> Graph:
    """The recorded tree itself, rooted at s (used as a reference graph)."""
    parent: Dict[str, str] = {}
    for a in family.attestations:
        chain = list(a.sanad)
        if s in chain:
            chain = chain[chain.index(s):]
        for u, v in zip(chain, chain[1:]):
            parent.setdefault(v, u)
    parent.pop(s, None)
    return Graph(s, parent, anchors=anchors)
