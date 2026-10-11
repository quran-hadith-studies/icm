"""Detect mind-map connectors by rasterising the page and finding orange strokes.

The vector content stream does not expose every connector, so connectors are
recovered from pixels: orange components are the links, and each component's
left/right extreme pixels are its endpoints (in PDF points).
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Dict, List, Tuple

import numpy as np
from PIL import Image

ORANGE = np.array([255, 191, 17])     # 1.0, 0.749, 0.0667


def render(pdf: str, workdir: str, dpi: int = 150) -> Tuple[np.ndarray, float]:
    os.makedirs(workdir, exist_ok=True)
    prefix = os.path.join(workdir, f"page{dpi}")
    png = prefix + "-1.png"
    if not os.path.exists(png):
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", pdf, prefix], check=True)
    img = np.asarray(Image.open(png).convert("RGB")).astype(np.int16)
    return img, dpi / 72.0


def orange_mask(img: np.ndarray, tol: int = 60) -> np.ndarray:
    d = np.abs(img - ORANGE[None, None, :]).sum(axis=2)
    return d < tol


def components(mask: np.ndarray) -> List[np.ndarray]:
    """Run-length union-find labelling of the mask (8-connected)."""
    H, W = mask.shape
    parent: List[int] = []

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    runs_prev: List[Tuple[int, int, int]] = []
    all_runs: List[Tuple[int, int, int, int]] = []   # y, x0, x1, id
    for y in range(H):
        row = mask[y]
        if not row.any():
            runs_prev = []
            continue
        idx = np.flatnonzero(np.diff(np.concatenate(([0], row.view(np.int8), [0]))))
        runs_cur = []
        for x0, x1 in zip(idx[0::2], idx[1::2]):
            rid = len(parent)
            parent.append(rid)
            runs_cur.append((x0, x1, rid))
            all_runs.append((y, x0, x1, rid))
            for px0, px1, pid in runs_prev:
                if px0 <= x1 and x0 <= px1:
                    union(rid, pid)
        runs_prev = runs_cur
    groups: Dict[int, List[Tuple[int, int, int]]] = {}
    for y, x0, x1, rid in all_runs:
        groups.setdefault(find(rid), []).append((y, x0, x1))
    return list(groups.values())


def connectors(pdf: str, workdir: str, dpi: int = 150,
               min_len_pt: float = 4.0) -> List[Dict]:
    img, scale = render(pdf, workdir, dpi)
    comps = components(orange_mask(img))
    out = []
    for runs in comps:
        xs0 = min(r[1] for r in runs)
        xs1 = max(r[2] for r in runs)
        ys0 = min(r[0] for r in runs)
        ys1 = max(r[0] for r in runs)
        if (xs1 - xs0) / scale < min_len_pt and (ys1 - ys0) / scale < min_len_pt:
            continue
        # endpoint y = mean row y at the extreme x
        left_y = np.mean([r[0] for r in runs if r[1] <= xs0 + 1])
        right_y = np.mean([r[0] for r in runs if r[2] >= xs1 - 1])
        out.append(dict(
            left=(xs0 / scale, left_y / scale),
            right=(xs1 / scale, right_y / scale),
            width=(xs1 - xs0) / scale, height=(ys1 - ys0) / scale,
            n_px=sum(r[2] - r[1] for r in runs)))
    return out


if __name__ == "__main__":
    pdf = sys.argv[1]
    work = sys.argv[2] if len(sys.argv) > 2 else "../outputs/_work"
    cs = connectors(pdf, work)
    print("connectors", len(cs))
    straight = [c for c in cs if c["height"] < 3]
    print("straight", len(straight), "curved", len(cs) - len(straight))
    import json
    json.dump(cs, open(os.path.join(work, "connectors.json"), "w"))
