"""Recover the sanad tree of a mind-map PDF (نمودار) as nodes + edges.

The diagram is right-to-left: the rightmost label is the root, connectors run
leftwards, and the leftmost label on each branch is the matn of one attestation.
Text comes from `pdftotext -bbox` (glyph boxes, clustered into labels) and the
connectors come from the page content stream (stroked beziers).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extract_labels import labels as cluster_labels, load as load_glyphs  # noqa: E402
import extract_paths  # noqa: E402

EDGE_COLOR = (1.0, 0.749019607, 0.066666666)   # the mind-map connector orange


def _is_edge_color(c, tol=0.02) -> bool:
    return bool(c) and all(abs(a - b) < tol for a, b in zip(c, EDGE_COLOR))


def extract(pdf: str, workdir: str, gap: float = 6.0, ytol: float = 3.0) -> Dict:
    os.makedirs(workdir, exist_ok=True)
    bbox = os.path.join(workdir, "bbox.xml")
    if not os.path.exists(bbox):
        subprocess.run(["pdftotext", "-bbox-layout", pdf, bbox], check=True)
    L = cluster_labels(load_glyphs(bbox), gap=gap, ytol=ytol)
    for i, l in enumerate(L):
        l["i"] = i
    segs, fills = extract_paths.run(pdf)
    conns = []
    for s in segs:
        if not _is_edge_color(s["color"]):
            continue
        p0, p1 = s["pts"][0], s["pts"][-1]
        right, left = (p0, p1) if p0[0] > p1[0] else (p1, p0)
        conns.append((right, left))
    edges, unmatched = [], []
    for right, left in conns:
        # the connector's right end abuts the parent's LEFT side, and its left
        # end abuts the child's RIGHT side (right-to-left layout)
        par = _attach(L, right, side="right_of_point")
        ch = _attach(L, left, side="left_of_point")
        if par is None or ch is None or par["i"] == ch["i"]:
            unmatched.append((right, left))
            continue
        edges.append((par["i"], ch["i"]))
    return dict(labels=L, edges=edges, n_connectors=len(conns), unmatched=len(unmatched))


def _attach(L, pt, side: str, ytol: float = 7.0, xtol: float = 60.0):
    """Nearest label lying on the requested side of the connector endpoint."""
    x, y = pt
    best, bd = None, 1e18
    for l in L:
        if not (l["y0"] - ytol <= y <= l["y1"] + ytol):
            continue
        if side == "right_of_point":
            d = l["x0"] - x           # label starts to the right of the endpoint
        else:
            d = x - l["x1"]           # label ends to the left of the endpoint
        if d < -2.0 or d > xtol:
            continue
        if d < bd:
            bd, best = d, l
    return best


def build_tree(res: Dict) -> Dict:
    """Orient the edge list into a rooted tree and report its shape."""
    L, edges = res["labels"], res["edges"]
    parent: Dict[int, int] = {}
    children: Dict[int, List[int]] = {}
    for p, c in edges:
        parent.setdefault(c, p)
        children.setdefault(p, []).append(c)
    roots = [l["i"] for l in L if l["i"] not in parent and
             (l["i"] in children or any(e[1] == l["i"] for e in edges))]
    return dict(parent=parent, children=children, roots=roots)


if __name__ == "__main__":
    pdf, out = sys.argv[1], sys.argv[2]
    res = extract(pdf, os.path.join(os.path.dirname(out), "_work"))
    tree = build_tree(res)
    json.dump(dict(labels=res["labels"], edges=res["edges"],
                   parent={str(k): v for k, v in tree["parent"].items()},
                   roots=tree["roots"]),
              open(out, "w"), ensure_ascii=False)
    print("labels", len(res["labels"]), "connectors", res["n_connectors"],
          "edges", len(res["edges"]), "unmatched", res["unmatched"],
          "roots", len(tree["roots"]))
