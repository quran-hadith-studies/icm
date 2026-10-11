"""Chronology by interpolation along recorded chains (README section 3).

Depth in a diagram or an isnad is a poor clock: a short chain only means fewer
recorded links. Each narrator's floruit is therefore interpolated linearly
between dated narrators of the same chain, and averaged over every chain that
contains the narrator.
"""
from __future__ import annotations

from typing import Callable, Dict, Hashable, List, Optional, Sequence


def interpolate(chains: Sequence[Sequence[Hashable]],
                dated: Callable[[Hashable], Optional[float]],
                first_default: float = 60.0,
                step: float = 35.0,
                step_nodate: float = 40.0,
                min_len: int = 2) -> Dict[Hashable, List[float]]:
    """Return {node: [year estimate per chain containing it]}.

    chains   node ids ordered source -> collector.
    dated    node -> known year (death or interval midpoint) or None.
    first_default  year of the chain head when it is undated (a companion of the
                   first generation).
    step     years added per step beyond the last dated narrator.
    step_nodate  years per step in a chain with no date besides the head.
    """
    years: Dict[Hashable, List[float]] = {}
    for chain in chains:
        chain = list(chain)
        if len(chain) < min_len:
            continue
        anchors = [(j, dated(i)) for j, i in enumerate(chain) if dated(i) is not None]
        first = dated(chain[0])
        if first is None:
            first = first_default
        pts = [(0, first)] + [a for a in anchors if a[0] > 0]
        if len(pts) < 2:
            pts.append((len(chain) - 1, first + step_nodate * (len(chain) - 1)))
        for j, i in enumerate(chain):
            lo = max([p for p in pts if p[0] <= j], key=lambda p: p[0])
            hi = min([p for p in pts if p[0] >= j], key=lambda p: p[0], default=None)
            if hi is None or hi[0] == lo[0]:
                y = lo[1] + step * (j - lo[0])
            else:
                f = (j - lo[0]) / (hi[0] - lo[0])
                y = lo[1] + f * (hi[1] - lo[1])
            years.setdefault(i, []).append(y)
    return years
