"""Core data containers and loaders."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass
class Narrator:
    """A narrator node with the metadata M of section 2."""
    nid: str
    name: str = ""
    birth: Optional[Tuple[float, float]] = None   # interval (lo, hi), Hijri
    death: Optional[Tuple[float, float]] = None
    active: Optional[Tuple[float, float]] = None  # derived activity interval
    locations: Tuple[str, ...] = ()
    teachers: Tuple[str, ...] = ()
    meetings: Tuple[str, ...] = ()
    layer: Optional[int] = None  # tabaqa / generation index, used when dates are absent

    def activity(self) -> Optional[Tuple[float, float]]:
        if self.active is not None:
            return self.active
        if self.death is not None:
            lo = self.birth[1] if self.birth else self.death[0] - 60.0
            return (lo, self.death[1])
        return None


@dataclass
class Attestation:
    """One surviving report: a sanad C_i, a matn row X_i, and provenance."""
    aid: str
    sanad: Tuple[str, ...]            # source -> collector order
    row: Tuple[int, ...] = ()         # encoded variants; -1 = NA
    anchor: Optional[str] = None      # narrator node the text is observed at
    collection: str = ""
    independence_group: str = ""      # copies of one collection share a group
    weight: float = 1.0               # power-likelihood weight (assumption 12)

    def __post_init__(self):
        if self.anchor is None and self.sanad:
            self.anchor = self.sanad[-1]


@dataclass
class Family:
    """One coherent hadith family: D = {X, C, M}."""
    name: str
    narrators: Dict[str, Narrator]
    attestations: List[Attestation]
    variant_specs: List["VariantSpec"] = field(default_factory=list)

    @property
    def X(self):
        import numpy as np
        return np.array([a.row for a in self.attestations], dtype=int)

    def nodes(self) -> List[str]:
        seen = list(self.narrators)
        for a in self.attestations:
            for n in a.sanad:
                if n not in self.narrators:
                    seen.append(n)
        return seen

    def assign_independence_weights(self) -> None:
        """Assumption 12: copies from one collection are not fully independent."""
        groups: Dict[str, List[Attestation]] = {}
        for a in self.attestations:
            groups.setdefault(a.independence_group or a.aid, []).append(a)
        for g in groups.values():
            for a in g:
                a.weight = 1.0 / len(g)


def load_metadata(path: str) -> Dict[str, Narrator]:
    raw = json.load(open(path, encoding="utf-8"))
    out = {}
    for nid, d in raw.items():
        out[nid] = Narrator(
            nid=nid, name=d.get("name", nid),
            birth=tuple(d["birth"]) if d.get("birth") else None,
            death=tuple(d["death"]) if d.get("death") else None,
            active=tuple(d["active"]) if d.get("active") else None,
            locations=tuple(d.get("locations", ())),
            teachers=tuple(d.get("teachers", ())),
            meetings=tuple(d.get("meetings", ())),
            layer=d.get("layer"),
        )
    return out


def load_family(path: str) -> Family:
    """Load a family from a JSON document {name, narrators, attestations, variants}."""
    from .text_model import VariantSpec
    raw = json.load(open(path, encoding="utf-8"))
    narrators = {}
    for nid, d in raw.get("narrators", {}).items():
        narrators[nid] = Narrator(
            nid=nid, name=d.get("name", nid),
            birth=tuple(d["birth"]) if d.get("birth") else None,
            death=tuple(d["death"]) if d.get("death") else None,
            active=tuple(d["active"]) if d.get("active") else None,
            locations=tuple(d.get("locations", ())),
            teachers=tuple(d.get("teachers", ())),
            meetings=tuple(d.get("meetings", ())),
            layer=d.get("layer"),
        )
    atts = [
        Attestation(
            aid=a["id"], sanad=tuple(a["sanad"]), row=tuple(a.get("row", ())),
            anchor=a.get("anchor"), collection=a.get("collection", ""),
            independence_group=a.get("independence_group", ""),
        )
        for a in raw["attestations"]
    ]
    specs = [VariantSpec(**s) for s in raw.get("variants", [])]
    fam = Family(raw.get("name", "family"), narrators, atts, specs)
    fam.assign_independence_weights()
    return fam
