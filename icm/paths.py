"""The maximum path (طریق بیشینه) of every witness: post-processing of the model's output, no model change.

Kept apart from ``infer`` so the web process can add it to bundles of older runs without loading the model."""
from __future__ import annotations

import heapq
import math
from typing import Dict, List, Optional, Sequence

UNKNOWN = "UNKNOWN_SOURCE"          # hadith_phylogeny.candidates.UNKNOWN


def _unk_link(v: str) -> str:
    return f"{UNKNOWN}#{v}"


def max_paths(bundle: dict, graphs: Optional[Sequence] = None, log_w: Optional[Sequence[float]] = None) -> dict:
    """The maximum path (طریق بیشینه) of every witness: the chain from the most probable source down to the
    witness's collector that the posterior favours, next to the chain the witness records. Post-processing of the
    model's output only.

    With the particles of the best source (inside ``assemble``) the path of a witness is the modal path over the
    weighted particles and ``mass`` its posterior probability. Without them (runs computed before this existed) it is
    the path maximising the product of the per-student parent posteriors (``parent_posterior``), a marginal estimate
    with no mass. ``map_path`` is the witness's path in the single most probable tree (``map_graph``) and ``dp_path``
    the marginal path in both cases. A path runs source -> collector; ``UNKNOWN_SOURCE#v`` is the unnamed teacher
    of v alone (the model's per-student unknown link)."""
    src = bundle.get("map_source")
    gp = bundle.get("graph_posterior") or {}
    pp: Dict[str, Dict[str, float]] = dict(gp.get("parent_posterior") or {})
    for v, n in (bundle.get("narrators") or {}).items():       # the bundle keeps it per narrator
        if n.get("parent_posterior") and v not in pp:
            pp[v] = n["parent_posterior"]
    ep: Dict[str, float] = gp.get("edge_prob") or {}
    mapg: Dict[str, str] = gp.get("map_graph") or {}
    atts = bundle.get("attestations") or []
    out = dict(method="marginal", source=src, n_particles=None, paths={})
    if not src or not atts:
        return out

    # marginal: Dijkstra on -log p from the source over every teacher the posterior gives each student
    succ: Dict[str, List[tuple]] = {}
    for v, d in pp.items():
        for u, p in d.items():
            if p <= 0:
                continue
            if u == UNKNOWN and src != UNKNOWN:
                succ.setdefault(src, []).append((v, p, _unk_link(v)))
            else:
                succ.setdefault(u, []).append((v, p, None))
    dist: Dict[str, float] = {src: 0.0}
    prev: Dict[str, tuple] = {}
    heap = [(0.0, src)]
    while heap:
        dd, u = heapq.heappop(heap)
        if dd > dist.get(u, math.inf):
            continue
        for v, p, via in succ.get(u, ()):
            nd = dd - math.log(p)
            if nd < dist.get(v, math.inf) - 1e-12:
                dist[v] = nd
                prev[v] = (u, via)
                heapq.heappush(heap, (nd, v))

    def dp_path(anchor: str) -> Optional[List[str]]:
        if anchor not in dist:
            return None
        path, v, guard = [anchor], anchor, 0
        while v != src and guard < 1000:
            u, via = prev[v]
            if via:
                path.append(via)
            path.append(u)
            v, guard = u, guard + 1
        return list(reversed(path))

    def map_path(anchor: str) -> Optional[List[str]]:
        path, v, seen = [anchor], anchor, {anchor}
        while v != src:
            u = mapg.get(v)
            if u is None or u in seen:
                return None
            if u == UNKNOWN and src != UNKNOWN:
                path += [_unk_link(v), src]
                break
            path.append(u)
            seen.add(u)
            v = u
        return list(reversed(path))

    def edge_p(u: str, v: str) -> Optional[float]:
        if u.startswith(UNKNOWN + "#"):
            return ep.get(f"{UNKNOWN}>{v}")
        if v.startswith(UNKNOWN + "#"):
            return 1.0
        return ep.get(f"{u}>{v}")

    # particles: the modal path of every witness and its weighted share
    modal: Dict[str, tuple] = {}
    if graphs:
        import numpy as np
        lw = np.asarray(log_w if log_w is not None and len(log_w) == len(graphs) else [0.0] * len(graphs), dtype=float)
        lw = np.where(np.isfinite(lw), lw, -np.inf)
        if np.isfinite(lw).any():
            e = np.exp(lw - np.max(lw[np.isfinite(lw)]))
            wt = e / e.sum()
        else:
            wt = np.full(len(graphs), 1.0 / len(graphs))
        acc: Dict[str, Dict[tuple, float]] = {}
        for G, w in zip(graphs, wt):
            if w <= 0:
                continue
            for a in atts:
                try:
                    pth = tuple(G.path_to(a["anchor"]))
                except (KeyError, RecursionError):
                    continue
                d = acc.setdefault(a["id"], {})
                d[pth] = d.get(pth, 0.0) + float(w)
        for aid, d in acc.items():
            pth, m = max(d.items(), key=lambda kv: kv[1])
            modal[aid] = (list(pth), m, len(d))
        out.update(method="particles", n_particles=len(graphs))

    for a in atts:
        rec, anchor = list(a["sanad"]), a["anchor"]
        dp = dp_path(anchor)
        mp = map_path(anchor)
        if a["id"] in modal:
            path, mass, n_alt = modal[a["id"]]
        else:
            path, mass, n_alt = dp, None, None
        if not path:
            out["paths"][a["id"]] = dict(wid=a.get("wid") or str(a["id"]).split("#")[0], anchor=anchor, recorded=rec,
                                         path=None, mass=None, dp_path=dp, map_path=mp)
            continue
        named = [n for n in path if not n.startswith(UNKNOWN)]
        out["paths"][a["id"]] = dict(
            wid=a.get("wid") or str(a["id"]).split("#")[0], anchor=anchor, recorded=rec, path=path,
            mass=None if mass is None else round(mass, 4), n_alternatives=n_alt,
            dp_path=dp, dp_score=round(math.exp(-dist[anchor]), 4) if anchor in dist else None, map_path=mp,
            edge_probs=[None if p is None else round(p, 4) for p in (edge_p(u, v) for u, v in zip(path, path[1:]))],
            same_as_recorded=named[-len(rec):] == rec and all(n in rec or n == src for n in named),
            diff=dict(only_recorded=[n for n in rec if n not in path],
                      only_path=[n for n in path if n not in rec and n != src],
                      source_above=src not in rec))
    return out

