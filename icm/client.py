"""Entry points for running the model away from the server (browser via Pyodide, or the helper).

All values cross the boundary as JSON strings. The protocol, per project:

    init(payload)        build the shared context; returns the candidate sources (names, in order)
    source(i)            annealed SMC for source i; returns log Z / ESS (small); keeps the particles here
    graphs(i)            the particles of source i (needed only for the best source)
    finish(light, best)  assemble results + viewer bundle from every source's light evidence and
                         the best source's particles

Sources are independent, so they can be spread over any number of workers. Every worker builds its
own context from the same payload and so obtains the same sources in the same order.
"""
from __future__ import annotations

import json
import time

from hadith_phylogeny.report import finite
from . import infer

_ctx = None
_kept = {}


def init(payload_json: str) -> str:
    global _ctx, _kept
    p = json.loads(payload_json)
    _ctx = infer.Ctx(p["graph"], p["texts"], p.get("preset"), int(p.get("seed", 0)))
    _kept = {}
    return json.dumps(dict(sources=[_ctx.names.get(s, s) for s in _ctx.sources], preset=_ctx.preset, params=_ctx.params))


def source(i: int) -> str:
    t0 = time.time()
    ev = _ctx.source_evidence(int(i))
    _kept[int(i)] = ev
    d = infer.evidence_to_json(ev, with_graphs=False)
    d["index"], d["seconds"] = int(i), round(time.time() - t0, 1)
    return json.dumps(finite(d))


def graphs(i: int) -> str:
    return json.dumps(finite(infer.evidence_to_json(_kept[int(i)], with_graphs=True)))


def finish(light_json: str, best_json: str, seconds: float, provenance_json: str) -> str:
    light = json.loads(light_json)
    best = infer.evidence_from_json(json.loads(best_json))
    ev = {}
    for d in light:
        ev[d["source"]] = best if d["source"] == best.source else infer.evidence_from_json(d)
    out = infer.assemble(_ctx, ev, round(float(seconds), 1), json.loads(provenance_json))
    return json.dumps(finite(out))
