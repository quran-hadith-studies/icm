"""Convert the extracted diagram tree into a hadith_phylogeny Family.

Internal nodes are narrators; a leaf carrying matn text is an attestation whose
sanad is its root-to-collector path. The six cursed categories of the hadith are
encoded as presence/absence variant units, and the count word (سبعة / ستة / ...)
as one categorical unit.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hadith_phylogeny.chronology import interpolate
from hadith_phylogeny.collation import encode_categorical, encode_clause_matrix, normalize_arabic
from hadith_phylogeny.data import Attestation, Family, Narrator
from hadith_phylogeny.text_model import NA

# the six (or seven) cursed categories
CLAUSES: List[Tuple[str, List[str]]] = [
    ("zaid_fi_kitab",      ["الزائد في كتاب", "الزايد في كتاب"]),
    ("mukaddhib_bi_qadar", ["المكذب بقدر"]),
    ("mutasallit_jabarut", ["المتسلط", "المسلط", "المتسلط بالجبروت"]),
    ("mustahill_maharim",  ["المستحل محارم", "مستحل محارم", "المستحل من محارم"]),
    ("tarik_sunnati",      ["التارك لسنتي", "التارك للسنة", "التارك لسنة"]),
    ("mustahill_itra",     ["المستحل من عترتي", "المستحل لحرم", "من عترتي ما حرم"]),
    ("mutakabbir",         ["المتكبر على عباد"]),
]
COUNTS = [("sab'a", ["سبعة"]), ("sitta", ["ستة"]), ("khamsa", ["خمسة"]),
          ("arba'a", ["اربعة"])]
TRUNCATION = ["وساق الحديث", "وساق الحدیث", "فذكر الحديث", "فذكر الحدیث",
              "ثم ذكر مثله", "بنحوه", "بمعناه", "الحديث", "نحوه"]

MATN_MARK = re.compile(r"لعنتهم|لعنتھم|العنهم|ألعنھم|لعنهم الله|مجاب")
# A date in the diagram is a parenthesised note such as (287ق), (مدینة؛ 58 ق),
# (حدود 300ق) or (مدینه؛ 98ق یا 106ق). ق = lunar Hijri year of death.
DATE = re.compile(r"(\d{1,4})\s*ق(?!رن)")
CENTURY = re.compile(r"قرن\s*\(?\s*(\d)")
# city names use the knowledge graph's spelling, so diagram and biography agree
CITIES = {"مدینة": "المدينة", "مدینه": "المدينة", "مدينة": "المدينة", "کوفه": "الكوفة",
          "كوفة": "الكوفة", "بصره": "البصرة", "بصرة": "البصرة", "بغداد": "بغداد",
          "مکه": "مكة", "مكة": "مكة", "شام": "الشام", "قم": "قم"}

# Visual-order extraction turns paired punctuation inside out: ")287ق(" and
# "»...«". Mirroring the pairs gives the logical (reading-order) text.
_MIRROR = str.maketrans({"(": ")", ")": "(", "«": "»", "»": "«", "[": "]", "]": "["})


def clean_text(t: str) -> str:
    """Display form: logical bracket order, Arabic heh instead of heh-doachashmee."""
    t = t.translate(_MIRROR).replace("ھ", "ه")
    t = re.sub(r"\(\s+", "(", t)
    t = re.sub(r"\s+\)", ")", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_meta(t: str) -> dict:
    """Death date (interval), whether it is approximate, and a city, from a label."""
    out = {"death": None, "approx": False, "city": None, "note": None}
    years = [int(y) for y in DATE.findall(t)]
    if years:
        out["death"] = (min(years), max(years))
        out["approx"] = ("حدود" in t) or len(set(years)) > 1
    else:
        c = CENTURY.search(t)
        if c:
            cent = int(c.group(1))
            half2 = "دوم" in t or "ثانی" in t
            lo = (cent - 1) * 100 + (50 if half2 else 0)
            out["death"] = (lo, lo + (50 if half2 else 100))
            out["approx"] = True
    for k, v in CITIES.items():
        if k in t:
            out["city"] = v
            break
    m = re.search(r"\(([^)]*)\)", clean_text(t))
    if m:
        out["note"] = m.group(1)
    return out


def is_matn(text: str) -> bool:
    n = normalize_arabic(text)
    return bool(MATN_MARK.search(text)) or ("مجاب" in n) or ("لعن" in n and len(n) > 40)


def is_abridgement(text: str) -> bool:
    n = normalize_arabic(text)
    return len(n) < 60 and any(normalize_arabic(m) in n for m in
                               ["فذكر الحديث", "بنحوه", "بمعناه", "ثم ذكر مثله", "مرسلا"])


KG_LINKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "outputs", "narrator_links.json")


def load_links(path: str = KG_LINKS) -> Optional[dict]:
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None


def build_family(graph_json: str, name: str = "sitta la'anahum Allah",
                 links: Optional[dict] = "auto") -> Family:
    if links == "auto":
        links = load_links()
    G = json.load(open(graph_json, encoding="utf-8"))
    linked = {}
    if links:
        for k, v in links["narrators"].items():
            if v.get("confidence") in ("high", "medium"):
                linked[k] = v["record"]
    nodes = G["nodes"]
    parent = {int(k): int(v) for k, v in G["parent"].items()}
    children: Dict[int, List[int]] = {}
    for c, p in parent.items():
        children.setdefault(p, []).append(c)

    roots = [n["id"] for n in nodes if n["id"] not in parent]
    root = max(roots, key=lambda i: len(children.get(i, [])))

    # A long matn can wrap onto a second line that has no connector of its own.
    # Such an orphan line continues the matn leaf directly above it.
    # The wrapped line may itself be split by a highlight into a short chain
    # (fragment -> fragment); its text is read along that chain.
    continuation = set()
    for o in [r for r in roots if r != root]:
        frag, cur = [], o
        while True:
            frag.append(cur)
            ks = children.get(cur, [])
            if len(ks) != 1:
                break
            cur = ks[0]
        if children.get(cur):
            continue                        # a real subtree, not a wrapped line
        on = nodes[o]
        fx0 = min(nodes[f]["x0"] for f in frag)
        fx1 = max(nodes[f]["x1"] for f in frag)
        leaves = [n for n in nodes if n["id"] in parent and not children.get(n["id"])]
        # (a) same row, immediately to the left of a matn (split off by a highlight)
        same = [n for n in leaves if abs(n["y0"] - on["y0"]) < 4 and n["x0"] >= fx1 - 2
                and n["x0"] - fx1 < 60]
        # (b) the next line down under a matn (a wrapped line)
        below = [n for n in leaves if n["y1"] <= on["y0"] + 2 and on["y0"] - n["y1"] < 30
                 and n["x0"] < fx1 and fx0 < n["x1"]]
        host = (min(same, key=lambda n: n["x0"] - fx1) if same
                else max(below, key=lambda n: n["y1"]) if below else None)
        if host is not None:
            host["text"] = host["text"] + " " + " ".join(nodes[f]["text"] for f in frag)
            continuation.update(frag)

    def path(i: int) -> List[int]:
        out = [i]
        while out[-1] in parent:
            out.append(parent[out[-1]])
        return list(reversed(out))

    def nid(i: int) -> str:
        return f"n{i}"

    def depth(i: int) -> int:
        # the diagram root is the hadith itself, not a narrator: the companions
        # are layer 0 and everything below them is layer >= 1
        return len(path(i)) - 2

    # Chronology. Depth in the diagram is a poor clock (a short chain only means
    # fewer recorded links), so each narrator's floruit is interpolated linearly
    # between the companion's death date and the collector's, over every chain
    # that contains it.
    # narrators: every node that is not a matn leaf
    matn_leaves = [n["id"] for n in nodes
                   if not children.get(n["id"]) and n["id"] in parent
                   and (is_matn(n["text"]) or is_abridgement(n["text"]))]
    matn_set = set(matn_leaves)
    meta = {n["id"]: parse_meta(n["text"]) for n in nodes}

    def kg_death(i) -> Optional[float]:
        r = linked.get(nid(i))
        return float(r["death"]) if r and r.get("death") else None

    def dated(i) -> Optional[float]:
        d = meta[i]["death"]
        if d:
            return (d[0] + d[1]) / 2.0
        return kg_death(i)                 # a linked biography's death year

    years = interpolate([path(lf)[1:-1] for lf in matn_leaves], dated)

    narrators: Dict[str, Narrator] = {}
    for n in nodes:
        i = n["id"]
        if i in matn_set or i == root:
            continue
        if (i not in parent and i != root) or i in continuation:
            continue                      # stray text fragment / wrapped matn line
        d = meta[i]["death"]
        ys = years.get(i)
        kd = kg_death(i)
        if d is not None:
            death = (float(d[0]), float(d[1]))
            y = (death[0] + death[1]) / 2.0
        elif kd is not None:
            death = (kd, kd)
            y = kd
        else:
            y = sum(ys) / len(ys) if ys else None
            death = (y - 5.0, y + 5.0) if y is not None else None
        active = (y - 45.0, y) if y is not None else None
        rec = linked.get(nid(i))
        locs = set()
        if meta[i]["city"]:
            locs.add(meta[i]["city"])
        if rec and rec.get("iqama"):
            locs |= {c.strip() for c in re.split(r"[،,]", rec["iqama"]) if c.strip()}
        nar = Narrator(nid=nid(i), name=clean_text(n["text"]), layer=None,
                       death=death, active=active, locations=tuple(sorted(locs)))
        nar.date_source = ("diagram" if d is not None else "hadith-kg" if kd is not None
                           else "interpolated" if ys else None)
        nar.date_approx = meta[i]["approx"]
        nar.raw_label = n["text"]
        narrators[nid(i)] = nar


    # attestations
    texts, chains, collections = [], [], []
    for lf in matn_leaves:
        p = path(lf)[1:-1]        # drop the hadith node and the matn node itself
        if len(p) < 2:
            continue
        txt = clean_text(nodes[lf]["text"])
        if is_abridgement(txt):           # abridged: text of the parent chain only
            txt = txt + " وساق الحديث"
        chains.append([nid(i) for i in p])
        texts.append(txt)
        coll = clean_text(next((nodes[i]["text"] for i in reversed(p)
                                if DATE.search(nodes[i]["text"])), nodes[p[-1]]["text"]))
        collections.append(coll)

    rows_cl, specs_cl = encode_clause_matrix(texts, CLAUSES, TRUNCATION)
    col_count, spec_count = encode_categorical(texts, "count_word", COUNTS)
    specs = specs_cl + [spec_count]
    atts = []
    for j, (chain, txt) in enumerate(zip(chains, texts)):
        row = tuple(rows_cl[j] + [col_count[j]])
        atts.append(Attestation(aid=f"a{j}", sanad=tuple(chain), row=row,
                                anchor=chain[-1], collection=collections[j],
                                independence_group=collections[j]))
    # attested teacher -> student pairs from independent isnads (external R_uv)
    if links:
        for u, v, c in links.get("kg_pairs", []):
            if u in narrators and v in narrators and c > 0:
                narrators[v].teachers = tuple(sorted(set(narrators[v].teachers) | {u}))
    fam = Family(name, narrators, atts, specs)
    fam.assign_independence_weights()
    fam.texts = texts                      # kept for reporting
    fam.companions = [nid(c) for c in children.get(root, [])]
    return fam


if __name__ == "__main__":
    fam = build_family(sys.argv[1])
    print("narrators", len(fam.narrators), "attestations", len(fam.attestations))
    X = fam.X
    print("X shape", X.shape)
    names = [s.name for s in fam.variant_specs]
    print("variants", names)
    import numpy as np
    for j, nm in enumerate(names):
        col = X[:, j]
        print(f"  {nm:22s} present={int((col==1).sum()):3d} absent={int((col==0).sum()):3d} NA={int((col==NA).sum()):3d}")
    print("\nsample attestations:")
    for a in fam.attestations[:4]:
        print(" ", a.aid, "| chain", len(a.sanad), "|", fam.narrators[a.sanad[1]].name,
              "->", fam.narrators[a.anchor].name, "| row", a.row, "| w", round(a.weight, 2))
