"""term_root.tsv: the root of every Arabic word form of the corpus (the forms of ``termdf``), for the root index of
``icm.build.lex`` and for root matching in search and alignment.

Sources, strongest first
  quran  the Quranic Arabic Corpus morphology (ebadollah/web/quran/data/quranic-corpus-morphology-0.4.txt): hand-checked
         roots of every word of the Qur'an; the root of a form is the one most of its occurrences have
  itqan  ``word_defs_v2.json`` of the itqan data set (33,758 hadith word forms, CAMeL roots patched with Wensinck)
  camel  CAMeL Tools' analyser (calima-msa-r13): the form's most frequent analysis (MLE disambiguator) is its primary
         root, the other roots of its analyses are alternatives (``term_alt``, used to widen root queries)
A form no source knows (names, misspellings, Persian) has no root: the root index puts a filler there.

A *root key* spells a root in the letters of ``icm.text.norm`` with every weak letter (و ي ا ء and what CAMeL writes
as ``#``) as «و»: ص-ل-و, ق-و-م and ق-ي-م are «صلو», «قوم», «قوم». The index token is «r» + key (a Latin letter, so
it can never be a word). The same function (``root_key``) turns what the user types into a key.

Needs camel-tools (not a dependency of the wizard: run it in an environment that has it, e.g. conda env dev3; the
disambiguator data: ``camel_data -i morphology-db-msa-r13 -i disambig-mle-calima-msa-r13``):

    PYTHONPATH=. python -m icm.build.roots [--out external/icm/term_root.tsv] [--limit N] [--procs 8]
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from .. import config
from ..text import norm

from ..lex import ARABIC_FORM, is_arabic_form, root_key      # noqa: F401  (the key of a root and what is an Arabic form: icm/lex.py)

# ---------------------------------------------------------------------------
# Buckwalter (the Quranic corpus)
# ---------------------------------------------------------------------------
_BW = {"'": "ء", "|": "آ", ">": "أ", "&": "ؤ", "<": "إ", "}": "ئ", "A": "ا", "b": "ب", "p": "ة", "t": "ت", "v": "ث",
       "j": "ج", "H": "ح", "x": "خ", "d": "د", "*": "ذ", "r": "ر", "z": "ز", "s": "س", "$": "ش", "S": "ص", "D": "ض",
       "T": "ط", "Z": "ظ", "E": "ع", "g": "غ", "f": "ف", "q": "ق", "k": "ك", "l": "ل", "m": "م", "n": "ن", "h": "ه",
       "w": "و", "Y": "ى", "y": "ي", "{": "ٱ"}


def bw2ar(s: str) -> str:
    return "".join(_BW.get(c, "") for c in s)


def quran_gold(path: Optional[str] = None) -> Dict[str, str]:
    """{form (norm): root key}: the form's commonest root among its occurrences in the Qur'an."""
    path = path or os.path.join(config.EBAD, "web", "quran", "data", "quranic-corpus-morphology-0.4.txt")
    if not os.path.exists(path):
        return {}
    words: Dict[str, List[str]] = defaultdict(list)          # (s:a:w) -> segments' forms
    roots: Dict[str, str] = {}
    for ln in open(path, encoding="utf-8"):
        if not ln.startswith("("):
            continue
        loc, form, _pos, feats = ln.rstrip("\n").split("\t")
        key = loc.strip("()").rsplit(":", 1)[0]
        words[key].append(bw2ar(form))
        m = re.search(r"\|ROOT:([^|]+)", feats)
        if m and "STEM" in feats:
            roots[key] = root_key(bw2ar(m.group(1)))
    votes: Dict[str, Counter] = defaultdict(Counter)
    for key, segs in words.items():
        if roots.get(key):
            votes[norm("".join(segs))][roots[key]] += 1
    return {f: c.most_common(1)[0][0] for f, c in votes.items() if f}


def itqan(path: Optional[str] = None) -> Dict[str, str]:
    path = path or os.path.join(config.HERE, "external", "datasets", "itqan", "repo", "app", "data", "word_defs_v2.json")
    if not os.path.exists(path):
        return {}
    out = {}
    for form, d in json.load(open(path, encoding="utf-8")).items():
        k = root_key(d.get("rc") or d.get("r") or "")
        if k:
            out[norm(form)] = k
    return out


# ---------------------------------------------------------------------------
# CAMeL
# ---------------------------------------------------------------------------
def variants(t: str) -> List[str]:
    """The spellings a form of ``norm`` can come from (norm unifies the alefs, hamza carriers, ta marbuta, alef maqsura):
    first the likely ones, then those with one hamza carrier restored in the middle."""
    firsts = [t] if not t.startswith("ا") else [t, "أ" + t[1:], "إ" + t[1:], "آ" + t[1:]]
    lasts = lambda s: [s] if not s.endswith("ه") else [s, s[:-1] + "ة"]
    yi = lambda s: [s] if "ی" not in s else [s.replace("ی", "ي"), (s[:-1] + "ى") if s.endswith("ی") else s]
    out: List[str] = []
    for a in firsts:
        for b in lasts(a):
            for c in yi(b):
                c = c.replace("ک", "ك")
                if c not in out:
                    out.append(c)
    return out


def variants2(t: str) -> List[str]:
    """One more hamza carrier somewhere inside (used only when ``variants`` found no analysis)."""
    t = t.replace("ک", "ك").replace("ی", "ي")
    out = []
    for i, c in enumerate(t):
        for r in {"ا": "أإ", "ي": "ئ", "و": "ؤ"}.get(c, ""):
            out.append(t[:i] + r + t[i + 1:])
    return out[:12]


# words with no root worth matching on: function words (searched as words) and proper nouns (a name's letters are not
# a root: «محمد» is not «حمد»)
NOROOT_POS = {"prep", "conj", "conj_sub", "interj", "pron", "pron_dem", "pron_rel", "pron_interrog", "pron_exclam", "adv_rel",
              "adv_interrog", "verb_pseudo", "noun_prop", "part", "part_det", "part_focus", "part_fut", "part_interrog",
              "part_neg", "part_restrict", "part_verb", "part_voc", "abbrev"}
_AN = _MLE = None


def _init():
    global _AN, _MLE
    from camel_tools.disambig.mle import MLEDisambiguator
    _MLE = MLEDisambiguator.pretrained("calima-msa-r13")
    _AN = _MLE._analyzer


def _ok_root(r: str) -> str:
    return root_key(r) if r and re.fullmatch(r"[ء-ي#.]+", r) else ""


def analyse(term: str) -> Tuple[str, str, str, str, List[str]]:
    """(term, primary root key, lemma, pos, alternative root keys); empty primary = no analysis."""
    for vs in (variants(term), variants2(term)):
        alts: List[str] = []
        prim: Optional[Tuple[str, str, str]] = None
        for v in vs:
            try:
                top = _MLE.disambiguate([v])[0].analyses
            except Exception:
                top = []
            if top and prim is None:
                a = top[0].analysis
                if _ok_root(a.get("root", "")):
                    prim = (_ok_root(a["root"]), a.get("lex", ""), a.get("pos", ""))
            if prim is not None:
                for a in _AN.analyze(v):
                    k = _ok_root(a.get("root", ""))
                    if k and k != prim[0] and k not in alts:
                        alts.append(k)
                break
        if prim is not None:
            return (term, prim[0], prim[1], prim[2], alts[:4])
    return (term, "", "", "", [])


def terms(limit: Optional[int] = None) -> List[Tuple[str, int]]:
    """The Arabic forms of the index (termdf), commonest first, with their document frequency."""
    con = sqlite3.connect(f"file:{config.TERMDF_DB}?mode=ro", uri=True)
    rows = [(t, a + b) for t, a, b in con.execute("select term, v02, kg from termdf order by v02+kg desc") if is_arabic_form(t)]
    return rows[:limit] if limit else rows


def run(out: Optional[str] = None, limit: Optional[int] = None, procs: int = 8) -> None:
    out = out or os.path.join(config.DATA_DIR, "term_root.tsv")
    t0 = time.time()
    ts = terms(limit)
    print(f"{len(ts)} Arabic forms ({time.time() - t0:.0f}s)", flush=True)
    gold, itq = quran_gold(), itqan()
    print(f"quran gold {len(gold)} forms, itqan {len(itq)} forms", flush=True)
    names = [t for t, _ in ts]
    res: Dict[str, tuple] = {}
    with mp.Pool(procs, initializer=_init) as pool:
        for i, r in enumerate(pool.imap_unordered(analyse, names, chunksize=500), 1):
            res[r[0]] = r
            if i % 50000 == 0:
                print(f"  camel {i}/{len(names)} {time.time() - t0:.0f}s", flush=True)
    rows, n_src = [], Counter()
    agree = Counter()
    for t, df in ts:
        _t, croot, lem, pos, alts = res[t]
        src, root = "", ""
        if pos in NOROOT_POS:
            src = "norootpos"
        elif t in gold:
            src, root = "quran", gold[t]
        elif t in itq:
            src, root = "itqan", itq[t]
        elif croot:
            src, root = "camel", croot
        for name, other in (("quran", gold.get(t)), ("itqan", itq.get(t))):
            if other and croot:
                agree[name, other == croot] += 1
        n_src[src or "none"] += 1
        alt = [] if src == "norootpos" else [a for a in ([croot] if croot and croot != root else []) + alts if a and a != root]
        rows.append((t, root, lem, pos, src, ",".join(dict.fromkeys(alt)), df))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write("\t".join(map(str, r)) + "\n")
    cov = {"forms": dict(n_src)}
    for name in ("quran", "itqan"):
        a, b = agree[name, True], agree[name, False]
        cov[f"camel_vs_{name}"] = dict(agree=a, differ=b, rate=round(a / max(a + b, 1), 3))
    # token-weighted coverage (document frequency stands for frequency)
    tot = sum(r[6] for r in rows) or 1
    cov["df_weighted_rooted"] = round(sum(r[6] for r in rows if r[1]) / tot, 3)
    json.dump(cov, open(out + ".meta.json", "w"), indent=1)
    print(json.dumps(cov, ensure_ascii=False, indent=1))
    print(f"-> {out} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--procs", type=int, default=8)
    a = ap.parse_args()
    run(a.out, a.limit, a.procs)
