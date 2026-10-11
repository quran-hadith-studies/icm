"""Row-based parsing of the mind-map: labels and connectors alternate along a row.

A row is a horizontal band of labels. Connector endpoints that fall in a row cut
it into node groups; all label fragments between two consecutive cuts belong to
one node. An edge joins the group immediately right of a connector's right end
(the earlier narrator) to the group immediately left of its left end.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract_paths
import pdf_tree
from extract_labels import labels as cluster_labels, load as load_glyphs

ROW_TOL = 7.0
CUT_TOL = 3.0


def rows_of(labels: List[dict], tol: float = ROW_TOL) -> List[dict]:
    items = sorted(labels, key=lambda l: (l["y0"] + l["y1"]) / 2)
    rows: List[dict] = []
    for l in items:
        yc = (l["y0"] + l["y1"]) / 2
        if rows and abs(yc - rows[-1]["yc"]) <= tol:
            r = rows[-1]
            r["labels"].append(l)
            r["yc"] = sum((q["y0"] + q["y1"]) / 2 for q in r["labels"]) / len(r["labels"])
        else:
            rows.append(dict(yc=yc, labels=[l]))
    for i, r in enumerate(rows):
        r["id"] = i
        r["labels"].sort(key=lambda l: -l["x1"])
        r["y0"] = min(l["y0"] for l in r["labels"])
        r["y1"] = max(l["y1"] for l in r["labels"])
    return rows


def assign_row(rows: List[dict], y: float, tol: float = 14.0):
    best, bd = None, 1e18
    for r in rows:
        d = 0.0 if r["y0"] - tol <= y <= r["y1"] + tol else min(abs(y - r["y0"]), abs(y - r["y1"]))
        if d < bd:
            bd, best = d, r
    return best if bd <= tol else None


def parse(pdf: str, workdir: str) -> Dict:
    bbox = os.path.join(workdir, "bbox.xml")
    if not os.path.exists(bbox):
        os.makedirs(workdir, exist_ok=True)
        import subprocess
        subprocess.run(["pdftotext", "-bbox-layout", pdf, bbox], check=True)
    labels = cluster_labels(load_glyphs(bbox))
    rows = rows_of(labels)
    segs, fills = extract_paths.run(pdf)
    conns = []
    for s in segs:
        if not pdf_tree._is_edge_color(s["color"]):
            continue
        p0, p1 = s["pts"][0], s["pts"][-1]
        right, left = (p0, p1) if p0[0] > p1[0] else (p1, p0)
        conns.append(dict(right=right, left=left))

    # cuts per row
    for r in rows:
        r["cuts"] = []
    for c in conns:
        for end in ("right", "left"):
            r = assign_row(rows, c[end][1])
            c[end + "_row"] = r["id"] if r else None
            if r:
                r["cuts"].append(c[end][0])
    for r in rows:
        cuts = sorted(set(round(x, 1) for x in r["cuts"]), reverse=True)
        merged = []
        for x in cuts:
            if merged and abs(merged[-1] - x) <= CUT_TOL:
                continue
            merged.append(x)
        r["cuts"] = merged            # descending x

    # group labels between cuts -> nodes
    nodes: List[dict] = []
    for r in rows:
        groups: Dict[int, List[dict]] = {}
        for l in r["labels"]:
            xc = (l["x0"] + l["x1"]) / 2
            gid = sum(1 for x in r["cuts"] if x > xc)
            groups.setdefault(gid, []).append(l)
        r["groups"] = {}
        for gid, ls in groups.items():
            ls.sort(key=lambda l: -l["x1"])
            nid = len(nodes)
            nodes.append(dict(id=nid, row=r["id"], gid=gid,
                              text=" ".join(l["text"] for l in ls),
                              x0=min(l["x0"] for l in ls), x1=max(l["x1"] for l in ls),
                              y0=min(l["y0"] for l in ls), y1=max(l["y1"] for l in ls)))
            r["groups"][gid] = nid

    def group_at(row_id, x, side):
        r = rows[row_id]
        n_right = sum(1 for cx in r["cuts"] if cx > x + CUT_TOL)
        gid = n_right if side == "right" else n_right + 1
        return r["groups"].get(gid)

    edges = []
    unmatched = 0
    for c in conns:
        if c["right_row"] is None or c["left_row"] is None:
            unmatched += 1
            continue
        p = group_at(c["right_row"], c["right"][0], "right")
        ch = group_at(c["left_row"], c["left"][0], "left")
        if p is None or ch is None or p == ch:
            unmatched += 1
            continue
        edges.append((p, ch))
    return dict(nodes=nodes, edges=edges, rows=[{k: v for k, v in r.items() if k != "labels"}
                                                for r in rows],
                fills=fills, unmatched=unmatched, n_conn=len(conns))


if __name__ == "__main__":
    pdf, out = sys.argv[1], sys.argv[2]
    res = parse(pdf, os.path.join(os.path.dirname(out), "_work"))
    json.dump(res, open(out, "w"), ensure_ascii=False)
    par = {}
    dup = 0
    for p, c in res["edges"]:
        if c in par:
            dup += 1
        par.setdefault(c, p)
    roots = sorted({p for p, _ in res["edges"]} - set(par))
    print("nodes", len(res["nodes"]), "conns", res["n_conn"], "edges", len(res["edges"]),
          "unmatched", res["unmatched"], "multi-parent", dup, "roots", roots)
    for r in roots[:10]:
        print("  root:", res["nodes"][r]["text"][:60])
