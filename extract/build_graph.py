"""Turn the diagram into a sanad tree: labels -> nodes, orange components -> edges."""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import raster_edges
from extract_labels import labels as cluster_labels, load as load_glyphs
from row_parse import rows_of

ATTACH_TOL = 45.0     # pt: how far a connector end may sit from the text it joins
BAND_PAD = 6.0


def label_rows(workdir: str, pdf: str):
    bbox = os.path.join(workdir, "bbox.xml")
    if not os.path.exists(bbox):
        import subprocess
        os.makedirs(workdir, exist_ok=True)
        subprocess.run(["pdftotext", "-bbox-layout", pdf, bbox], check=True)
    L = cluster_labels(load_glyphs(bbox))
    return L, rows_of(L)


def component_bands(runs, scale, rows):
    """Per text row touched by a component: (min x, max x) in points."""
    out: Dict[int, List[float]] = {}
    for y, x0, x1 in runs:
        yp = y / scale
        for r in rows:
            if r["y0"] - BAND_PAD <= yp <= r["y1"] + BAND_PAD:
                a, b = x0 / scale, x1 / scale
                cur = out.get(r["id"])
                out[r["id"]] = [min(cur[0], a), max(cur[1], b)] if cur else [a, b]
    return out


def nearest_label(row, x: float, side: str, tol: float = ATTACH_TOL):
    best, bd = None, 1e18
    for l in row["labels"]:
        d = (l["x0"] - x) if side == "right" else (x - l["x1"])
        if -3.0 <= d < bd:
            bd, best = d, l
    return (best, bd) if best is not None and bd <= tol else (None, bd)


def nearest_label_any(rows, x: float, y: float, side: str,
                      tol: float = ATTACH_TOL, ytol: float = 20.0):
    """Label joined by a connector end at (x, y).

    The end may overlap the text (a fan apex is drawn over the root label), so a
    negative gap up to the label's own width is allowed and scored as zero.
    """
    best, bs, brow, bd = None, 1e18, None, None
    for r in rows:
        for l in r["labels"]:
            yc = (l["y0"] + l["y1"]) / 2
            dy = abs(yc - y)
            if dy > ytol:
                continue
            d = (l["x0"] - x) if side == "right" else (x - l["x1"])
            w = l["x1"] - l["x0"]
            if d < -(w + 5.0) or d > tol:
                continue
            score = max(d, 0.0) + 0.5 * dy
            if score < bs:
                bs, best, brow, bd = score, l, r["id"], d
    return (best, bd, brow) if best is not None else (None, 1e18, None)


def build(pdf: str, workdir: str, dpi: int = 150):
    L, rows = label_rows(workdir, pdf)
    img, scale = raster_edges.render(pdf, workdir, dpi)
    comps = raster_edges.components(raster_edges.orange_mask(img))

    # merge label fragments that no connector separates -> nodes
    links: List[Tuple[dict, dict, dict]] = []   # (parent label, child label, info)
    for runs in comps:
        if sum(r[2] - r[1] for r in runs) < 20:
            continue
        bands = component_bands(runs, scale, rows)
        if not bands:
            continue
        # the parent side is the component's global right end: a fan apex can sit
        # between two text rows, so search labels by y rather than by band
        mx = max(r[2] for r in runs)
        apex_y = float(np.mean([r[0] for r in runs if r[2] >= mx - 1])) / scale
        plabel, pd, prow_id = nearest_label_any(rows, mx / scale, apex_y, "right",
                                                ytol=40.0)
        if plabel is None:
            continue
        px = mx / scale
        for rid, (minx, maxx) in bands.items():
            if rid == prow_id and minx >= px - 2:
                continue
            crow = rows[rid]
            clabel, cd, _ = nearest_label_any([crow], minx, (crow["y0"] + crow["y1"]) / 2,
                                              "left")
            if clabel is None or clabel is plabel:
                continue
            links.append((plabel, clabel, dict(prow=prow_id, crow=rid,
                                               px=px, cx=minx, pd=pd, cd=cd)))
    return L, rows, links


def merge_nodes(L, rows, links):
    """Label fragments not separated by a connector belong to one node."""
    attach_x: Dict[int, List[float]] = {}
    for p, c, info in links:
        attach_x.setdefault(info["prow"], []).append(info["px"])
        attach_x.setdefault(info["crow"], []).append(info["cx"])
    node_of: Dict[int, int] = {}
    nodes: List[dict] = []
    for r in rows:
        cuts = sorted(attach_x.get(r["id"], []), reverse=True)
        groups: Dict[int, List[dict]] = {}
        for l in r["labels"]:
            xc = (l["x0"] + l["x1"]) / 2
            gid = sum(1 for x in cuts if x > xc)
            groups.setdefault(gid, []).append(l)
        for gid, ls in sorted(groups.items()):
            ls.sort(key=lambda l: -l["x1"])
            nid = len(nodes)
            nodes.append(dict(id=nid, row=r["id"], text=" ".join(l["text"] for l in ls),
                              x0=min(l["x0"] for l in ls), x1=max(l["x1"] for l in ls),
                              y0=min(l["y0"] for l in ls), y1=max(l["y1"] for l in ls),
                              n_frag=len(ls)))
            for l in ls:
                node_of[id(l)] = nid
    edges = []
    for p, c, info in links:
        a, b = node_of.get(id(p)), node_of.get(id(c))
        if a is None or b is None or a == b:
            continue
        edges.append((a, b, info))
    return nodes, edges


if __name__ == "__main__":
    pdf = sys.argv[1]
    work = sys.argv[2]
    out = sys.argv[3]
    L, rows, links = build(pdf, work)
    nodes, edges = merge_nodes(L, rows, links)
    parent: Dict[int, int] = {}
    multi = []
    for a, b, info in edges:
        if b in parent and parent[b] != a:
            multi.append((b, parent[b], a))
        else:
            parent[b] = a
    roots = [n["id"] for n in nodes if n["id"] not in parent]
    print("nodes", len(nodes), "links", len(links), "edges", len(edges),
          "multi-parent", len(multi), "roots", len(roots))
    for rid in roots[:12]:
        print("   root:", nodes[rid]["text"][:70])
    json.dump(dict(nodes=nodes,
                   edges=[(int(a), int(b)) for a, b, _ in edges],
                   parent={str(k): int(v) for k, v in parent.items()}),
              open(out, "w"), ensure_ascii=False)
