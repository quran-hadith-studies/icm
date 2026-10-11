"""City canonicalisation and geocoding (al-Ṯurayyā gazetteer).

``data/city_alias.tsv`` maps every spelling met in the texts to one canonical Arabic
name (and optionally a Cornu/al-Ṯurayyā URI); ``data/nisba_city.tsv`` maps geographic
nisbas (كوفي، قمي، …) to the same canonical names. ``build_city_tables`` fills ``city``
and ``city_alias``; ``Places`` answers lookups.
"""
from __future__ import annotations

import json
import os
from typing import Dict, Optional, Tuple

from .common import DATASETS, data_file, insert_many, norm_key, open_db, reset_tables

GEOJSON = os.path.join(DATASETS, "althurayya", "places.geojson")
TABLES = ["city", "city_alias"]


def _read_tsv(path: str):
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        yield [p.strip() for p in parts]


def thurayya_index() -> Dict[str, Tuple[str, float, float]]:
    """norm_key(toponym) → (uri, lat, lon); settlements win over regions of the same name."""
    out: Dict[str, Tuple[str, float, float]] = {}
    if not os.path.exists(GEOJSON):
        return out
    d = json.load(open(GEOJSON, encoding="utf-8"))
    rank = {"metropoles": 0, "capitals": 1, "towns": 2, "villages": 3, "quarters": 4, "regions": 5}
    best: Dict[str, Tuple[int, str, float, float]] = {}
    by_uri: Dict[str, Tuple[float, float]] = {}
    for f in d["features"]:
        p = f["properties"]["cornuData"]
        lon, lat = f["geometry"]["coordinates"]
        by_uri[p["cornu_URI"]] = (lat, lon)
        k = norm_key(p["toponym_arabic"])
        r = rank.get(p.get("top_type_hom"), 9)
        if k not in best or r < best[k][0]:
            best[k] = (r, p["cornu_URI"], lat, lon)
    out = {k: (v[1], v[2], v[3]) for k, v in best.items()}
    out["__by_uri__"] = by_uri  # type: ignore[assignment]
    return out


def build_city_tables(con) -> None:
    reset_tables(con, TABLES)
    idx = thurayya_index()
    by_uri = idx.pop("__by_uri__", {}) if "__by_uri__" in idx else {}
    cities: Dict[str, Tuple[Optional[str], Optional[float], Optional[float]]] = {}
    aliases = []
    for row in _read_tsv(data_file("city_alias.tsv")):
        if len(row) < 2 or not row[1]:
            continue
        alias, canonical = row[0], row[1]
        uri = row[2] if len(row) > 2 and row[2] else None
        aliases.append((norm_key(alias), canonical, "toponym"))
        if canonical not in cities or (uri and not cities[canonical][0]):
            if uri and uri in by_uri:
                cities[canonical] = (uri, by_uri[uri][0], by_uri[uri][1])
            else:
                hit = idx.get(norm_key(canonical))
                cities[canonical] = (hit[0], hit[1], hit[2]) if hit else (None, None, None)
        aliases.append((norm_key(canonical), canonical, "toponym"))
    for row in _read_tsv(data_file("nisba_city.tsv")):
        if len(row) < 2 or not row[1]:
            continue
        aliases.append((norm_key(row[0]), row[1], "nisba"))
        cities.setdefault(row[1], (None, None, None))
    insert_many(con, "city", ["canonical", "thurayya_id", "lat", "lon"],
                [(c, v[0], v[1], v[2]) for c, v in cities.items()])
    insert_many(con, "city_alias", ["alias_key", "canonical", "kind"], {a[0]: a for a in aliases}.values())
    con.commit()
    n_geo = sum(1 for v in cities.values() if v[0])
    print(f"places: {len(cities)} canonical cities ({n_geo} geocoded), {len(aliases)} aliases")


class Places:
    def __init__(self, con):
        self.toponym: Dict[str, str] = {}
        self.nisba: Dict[str, str] = {}
        for r in con.execute("select alias_key, canonical, kind from city_alias"):
            (self.nisba if r[2] == "nisba" else self.toponym)[r[0]] = r[1]
        self.geo = {r[0]: (r[1], r[2], r[3]) for r in con.execute("select canonical, thurayya_id, lat, lon from city")}

    def from_toponym(self, word: str) -> Optional[str]:
        k = norm_key(word)
        if not k:
            return None
        for cand in (k, k[2:] if k.startswith("ال") else "ال" + k, k[1:] if k.startswith("ب") else k):
            if cand in self.toponym:
                return self.toponym[cand]
        return None

    def from_nisba(self, token: str) -> Optional[str]:
        k = norm_key(token)
        k = k[2:] if k.startswith("ال") and len(k) > 4 else k
        return self.nisba.get(k)
