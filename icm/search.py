"""Step 2: find the hadiths that share parts of the seed, with similarity per part.

    result = run("v02:800316143", params)            # -> search.json content

Candidate generation (recall) is deliberately broad and has several independent signals;
the decision is the user's. Every candidate keeps its signals, its scores and a *suggested*
class; nothing is filtered by a hidden threshold except candidates with no resemblance at
all (counted in ``n_dropped``).

Signals
  rel:*      existing groupings (Noor group/cluster/related, v02 consensus set, kg group)
  q:pair     two most distinctive terms of a clause, ANDed (document frequency from termdf)
  q:phrase   a distinctive 3-word window of a clause as an FTS phrase
Scoring is order-free per clause (``similarity.align_clauses``): witnesses reorder clauses.
"""
from __future__ import annotations

import hashlib
import math
import time
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import corpus, hadithai, pointers, relations
from .similarity import align_clauses, classify, match_key
from .text import clauses, grams, marks, norm, squeeze, strip_tags, tokens, union_cuts

PRESELECTED = ("witness", "extended", "abridged", "variant")


def is_reference(rec: dict) -> Optional[str]:
    """A row that quotes a hadith without transmitting it: a Dorar (al-Durar al-Saniyya) entry with no isnad is a later
    scholar's citation or grading of a hadith of another collection («ضعيف الترمذي» quoting al-Tirmidhi), not an
    independent witness. It stays a candidate, scored and selectable, but is not pre-selected."""
    if rec.get("corpus") == "v02" and str(rec.get("book") or "").startswith("dorar-") and not (rec.get("sanad") or "").strip():
        return "dorar"
    return None


def preselected(c: dict) -> bool:
    return c["suggested"] in PRESELECTED and not c.get("reference")

DEFAULTS = dict(
    corpora=["v02", "kg"],
    rounds=2,                  # 1 = seed only; each further round expands from found witnesses
    part_query=None,           # list of clause indexes to restrict the FTS queries to
    clauses=None,              # the user's parts of the seed ([start, end) token spans); None = automatic
    fts_limit=3000,            # rows per FTS query
    pool_max=5000,             # candidates fetched for scoring per round
    prefilter=0.06,            # min share of seed 8-grams (or one clause >= 0.5) to be scored
    expand_max=10,             # witnesses used as expansion sources per round
    keep_min_cov=0.15,         # candidates below this seed coverage and with no clause hit are dropped
    pair_df_max=60000,         # terms more frequent than this are not used in queries
    phrase_df_max=20000,
    gram_n=8,
)


def run(seed: str, params: Optional[dict] = None, extra_ids: Sequence[str] = (), keep_ids: Sequence[str] = (), *,
        seeds: Sequence[str] = (), prior: Optional[dict] = None, retrieve: bool = True, texts: Optional[dict] = None) -> dict:
    """``keep_ids``: witnesses selected before the user re-cut the seed; they are scored whatever their score.
    ``extra_ids``: hadiths the user added; scored whatever their score. ``seeds``: further seed hadiths: searched from like the
    seed (their own words are queried), always candidates, never folded away. ``retrieve=False`` asks the corpora nothing: only
    ``prior`` (wid -> signals/round/via of an earlier search's candidates), the extras, kept and seed hadiths are scored again,
    against the seed's parts as they are now. ``texts``: wid -> the text the user edited, used instead of the corpus's."""
    p = {**DEFAULTS, **(params or {})}
    t0 = time.time()
    rec = corpus.get(seed)
    if rec is None:
        raise KeyError(f"seed {seed} not found")
    sp = pointers.split(rec)
    if sp["refs"] and sp["main"]:                 # «… حدثنا وكيع … بمثله، إلا أن وكيعا قال: …»: the seed is its own words, not the isnads after them
        rec = dict(rec, matn=sp["main"])
    s_tok, auto_spans = clauses(rec["matn"])
    auto_by, auto_owners = "rules", None
    hai = hadithai.parts(rec["hid"], s_tok) if rec["corpus"] == "v02" else None
    if hai:
        # hadith.ai's segments are semantic but coarse in lists («الزائد… والمكذب… والتارك…» is one segment), and a
        # part is the unit of presence: cut at the boundaries of both, each part keeping its segment's speaker
        h_spans, h_owners = hai
        auto_spans = union_cuts(s_tok, auto_spans, h_spans)
        auto_owners = [next((o for (x, y), o in zip(h_spans, h_owners) if x <= a < y), None) for a, _ in auto_spans]
        auto_by = "hadith.ai+rules"
    s_spans = auto_spans
    if p.get("clauses"):                          # the user's segmentation (step 2), checked by the API
        s_spans = [(int(a), int(b)) for a, b in p["clauses"] if 0 <= int(a) < int(b) <= len(s_tok)] or auto_spans
    flags: List[dict] = []
    if len(s_tok) < 6:
        flags.append(dict(kind="seed_short", ref=seed, message="seed has fewer than 6 tokens; search is unreliable"))
    if not s_spans:
        raise ValueError("seed has no text")
    n_seed = sum(y - x for x, y in s_spans)
    sel = p["part_query"]
    q_clauses = [c for c in range(len(s_spans)) if sel is None or c in sel]

    pool: Dict[str, dict] = {}
    queries: List[dict] = []
    rounds_info: List[dict] = []
    scored: Dict[str, dict] = {}
    seed_norm = norm(rec["matn"])

    texts = texts or {}
    sources = [(seed, s_tok, s_spans)]
    if retrieve:
        for w in seeds:                           # the further seeds are searched from like the seed, all their parts
            r2 = corpus.get(w) if w != seed else None
            if r2 is not None:
                t2, sp2 = clauses(texts.get(w, r2["matn"]))
                if sp2:
                    sources.append((w, t2, sp2))
    for rnd in range(1, (int(p["rounds"]) if retrieve else 1) + 1):
        new_pool: Dict[str, dict] = {}
        if retrieve:
            for src_wid, src_tok, src_spans in sources:
                _generate(src_wid, src_tok, src_spans, q_clauses if rnd == 1 and src_wid == seed else range(len(src_spans)),
                          p, rnd, new_pool, queries)
        elif rnd == 1:
            for w, d in (prior or {}).items():
                new_pool[w] = dict(signals=set(d.get("signals") or ()), nq=0, round=d.get("round") or 1, via=d.get("via"))
        for w in extra_ids:
            new_pool.setdefault(w, dict(signals=set(), nq=0, round=rnd, via=None))["signals"].add("user")
        for w in seeds:
            new_pool.setdefault(w, dict(signals=set(), nq=0, round=rnd, via=None))["signals"].add("seed")
        for w in keep_ids:
            new_pool.setdefault(w, dict(signals=set(), nq=0, round=rnd, via=None))["signals"].add("kept")
        for w in list(new_pool):
            if w == seed or w in scored:
                del new_pool[w]
        n_cand = len(new_pool)
        batch = _score_pool(seed, s_tok, s_spans, n_seed, new_pool, p, scored, flags, texts)
        rounds_info.append(dict(round=rnd, n_candidates=n_cand, n_scored=batch["n_scored"],
                                n_dropped=batch["n_dropped"], n_witness_like=batch["n_witness_like"]))
        if not retrieve or rnd == int(p["rounds"]):
            break
        sources = _next_sources(scored, p, seed_norm)
        if not sources:
            break

    n_ptr = (retrieve or prior) and _pointers_after([seed] + [w for w, c in scored.items() if c["suggested"] != "different"], s_tok, s_spans, n_seed, p,
                            scored, flags, len(rounds_info) + 1, texts=texts)
    if n_ptr:
        rounds_info.append(dict(round=len(rounds_info) + 1, kind="pointers", n_candidates=n_ptr, n_scored=n_ptr, n_dropped=0,
                                n_witness_like=sum(1 for c in scored.values() if c.get("ref") and c["suggested"] in PRESELECTED)))
    cands = _fold_copies(scored, seed, rec)
    cands.sort(key=lambda c: (-_rank(c), c["wid"]))
    seed_out = dict(wid=seed, hid=rec["hid"], corpus=rec["corpus"], book=rec["book"], work=rec["work"],
                    title=rec["title"], author=rec["author"], death=rec["death"], tradition=rec["tradition"],
                    vol=rec["vol"], page=rec["page"], matn=rec["matn"], sanad=rec["sanad"],
                    copies=[c["wid"] for c in cands if c.get("copy_of") == seed])
    cands = [c for c in cands if c.get("copy_of") != seed]
    if len(cands) > 300:
        flags.append(dict(kind="family_large", ref=None, message=f"{len(cands)} candidates"))
    return dict(
        seed=seed_out, tokens=s_tok, clauses=[dict(i=i, span=list(sp), text=" ".join(s_tok[sp[0]:sp[1]]))
                                              for i, sp in enumerate(s_spans)],
        auto_clauses=[list(sp) for sp in auto_spans], clauses_by="user" if s_spans is not auto_spans else "auto",
        auto_by=auto_by, clause_owners=auto_owners if s_spans is auto_spans else None,
        params=p, seeds=[w for w in seeds if w != seed], retrieved=bool(retrieve), candidates=cands, queries=queries, rounds=rounds_info, flags=flags,
        seconds=round(time.time() - t0, 1))


# ---------------------------------------------------------------------------
def _rank(c: dict) -> float:
    order = {"witness": 5, "extended": 4.5, "abridged": 4, "variant": 3.5, "partial": 2, "different": 0}
    sc = c["score"]
    return order.get(c["suggested"], 0) * 10 + sc["cov_seed"] + 0.2 * sc["cov_cand"]


def _best_terms(tok: Sequence[str], dfs: Dict[str, Tuple[int, int]], idx: int, df_max: int, k: int = 2) -> List[str]:
    cand = sorted({t for t in tok if len(t) >= 3 and 0 < dfs.get(t, (0, 0))[idx] <= df_max},
                  key=lambda t: (dfs[t][idx], -len(t)))
    return cand[:k]


def _generate(wid: str, tok: List[str], spans, q_clauses: Iterable[int], p: dict, rnd: int,
              pool: Dict[str, dict], queries: List[dict]) -> None:
    """Fill ``pool`` with candidate ids proposed for source ``wid``."""
    def add(w: str, sig: str):
        d = pool.setdefault(w, dict(signals=set(), nq=0, round=rnd, via=None if rnd == 1 else wid))
        d["signals"].add(sig)
        d["nq"] += 1

    for sig, ids in relations.signals(wid).items():
        for w in ids:
            add(w, "rel:" + sig)
    all_terms = sorted({t for t in tok if len(t) >= 3})
    dfs = corpus.term_df(all_terms)
    for c in q_clauses:
        x, y = spans[c]
        ct = tok[x:y]
        for idx, name in enumerate(("v02", "kg")):
            if name not in p["corpora"]:
                continue
            exprs: List[Tuple[str, str]] = []
            best = _best_terms(ct, dfs, idx, p["pair_df_max"])
            if len(best) == 2:
                exprs.append(("pair", corpus.fts_query(best)))
            elif len(best) == 1 and len(ct) <= 4:
                exprs.append(("pair", corpus.fts_query(best)))
            # distinctive phrases: 3-token windows ranked by their rarest term
            wins = []
            for i in range(len(ct) - 2):
                w3 = ct[i:i + 3]
                d = [dfs.get(t, (0, 0))[idx] for t in w3]
                if min(d) > 0 and min(d) <= p["phrase_df_max"]:
                    wins.append((min(d), i))
            for _, i in sorted(wins)[:2]:
                exprs.append(("phrase", '"%s"' % " ".join(ct[i:i + 3])))
            for kind, expr in exprs:
                if not expr:
                    continue
                ids = corpus.fts_v02(expr, p["fts_limit"]) if name == "v02" else corpus.fts_kg(expr, p["fts_limit"])
                queries.append(dict(round=rnd, source=wid, clause=c, kind=kind, corpus=name, expr=expr, hits=len(ids),
                                    capped=len(ids) >= p["fts_limit"]))
                for i in ids:
                    add(f"{name}:{i}", "q:" + kind)


def _score_pool(seed: str, s_tok: List[str], s_spans, n_seed: int, pool: Dict[str, dict], p: dict,
                scored: Dict[str, dict], flags: List[dict], texts: Optional[dict] = None) -> dict:
    order = sorted(pool, key=lambda w: (-len(pool[w]["signals"]), -pool[w]["nq"]))
    capped = len(order) > p["pool_max"]
    if capped:
        flags.append(dict(kind="pool_capped", ref=None,
                          message=f"{len(order)} candidates; the {p['pool_max']} with most signals were scored"))
        order = order[:p["pool_max"]]
    seed_grams = grams(" ".join(s_tok), p["gram_n"])
    clause_grams = [grams(" ".join(s_tok[x:y]), p["gram_n"]) for x, y in s_spans]
    n_scored = n_dropped = n_wl = 0
    recs = corpus.get_many(order)
    for w, t in (texts or {}).items():                                  # the user's own text of a hadith (step 2 «ویرایش»)
        if w in recs:
            recs[w] = dict(recs[w], matn=t)
    # references («مثله / نحوه وزاد فيه …», pointers.py): a record that is one is scored on the text it stands for; one
    # inside a record («… و رواه الصدوق عن … مثله») is a candidate of its own, «<host>~k», and its host keeps its own words
    refd: Dict[str, dict] = {}
    for w in list(order):
        rec = recs.get(w)
        if rec is None:
            continue
        if "~" in w:                                                    # «<host>~k» given as such (the seed's inner references)
            d = pointers.derive(w)
            if d:
                refd[w] = {k: (v[:600] if k in ("isnad", "own") else v) for k, v in d.items() if k != "wid"}
            continue
        sp = pointers.split(rec)
        for r in sp["refs"]:
            pw = w if r["k"] == 0 else f"{w}~{r['k']}"
            if r["k"] == 0 and pool[w].get("ref_of"):                  # found right after the text it refers to
                host = corpus.get(pool[w]["ref_of"])
                src = dict(wid=pool[w]["ref_of"], text=pointers.own_text(host) if host else "", conf="ok")
            elif r["of"] == "host":
                src = dict(wid=w, text=pointers.own_text(rec), conf="ok")
            else:
                src = pointers.referent(w, r)
            if not src["wid"]:
                continue
            b = pointers.build(src["text"], r["tpl"], r["arg"], r["incipit"])
            refd[pw] = dict(ref=src["wid"], of=r["of"], conf=src["conf"], tpl=r["tpl"], base=r["base"], approx=r["approx"], arg=r["arg"],
                            incipit=r["incipit"], marker=r["marker"], isnad=r["isnad"][:600], own=r["own"][:600], text=b["text"],
                            note=b["note"], mark=b["mark"])
            if r["k"]:
                recs[pw] = pointers.record(rec, r["k"])
                pool.setdefault(pw, dict(pool[w], signals=set(pool[w]["signals"]) | {"inner"}, ref_of=None))
                order.append(pw)
        if sp["refs"] and sp["main"] is not None:
            recs[w] = dict(rec, matn=sp["main"])                     # the host: its own words only
    for w in order:
        rec = recs.get(w)
        if rec is None:
            continue
        pt = refd.get(w)
        if pt is not None:             # its words are those the template builds from the text referred to
            rec = dict(rec, matn=pt["text"])
        c_tok = tokens(rec["matn"])
        if not c_tok:
            continue
        g = grams(" ".join(c_tok), p["gram_n"])
        share = len(seed_grams & g) / max(len(seed_grams), 1)
        cl_hit = any(cg and len(cg & g) / len(cg) >= 0.5 for cg in clause_grams)
        user = bool({"user", "kept", "ref", "seed"} & pool[w]["signals"])
        if share < p["prefilter"] and not cl_hit and not user:
            n_dropped += 1
            continue
        res = align_clauses(s_tok, s_spans, c_tok)
        cls, score = classify(res, n_seed, len(c_tok))
        score["share_grams"] = round(share, 3)
        if cls == "different" and score["cov_seed"] < p["keep_min_cov"] and not user:
            n_dropped += 1
            continue
        n_scored += 1
        if cls in PRESELECTED:
            n_wl += 1
        sw = res["matched_b"]
        window = [sw[0], sw[-1] + 1] if sw else [0, len(c_tok)]
        window = [max(0, window[0] - 3), min(len(c_tok), window[1] + 3)]
        scored[w] = dict(
            wid=w, corpus=rec["corpus"], hid=rec["hid"], book=rec["book"], work=rec["work"], title=rec["title"],
            author=rec["author"], death=rec["death"], tradition=rec["tradition"], vol=rec["vol"], page=rec["page"],
            no=rec.get("no"), suggested=cls, score=score, parts=res["parts"],
            signals=sorted(pool[w]["signals"]), round=pool[w]["round"], via=pool[w]["via"],
            window=window, window_text=" ".join(c_tok[window[0]:window[1]]), reference=is_reference(rec),
            n_tokens=len(c_tok), matn=rec["matn"][:1500], matn_len=len(rec["matn"]),
            sanad=(rec["sanad"] or "")[:800],
            norm_hash=hashlib.sha1((" ".join(c_tok) + "|" + norm(rec["sanad"] or "")).encode()).hexdigest()[:16],
            text_hash=hashlib.sha1((" ".join(c_tok) + ("|ref:" + w if pt is not None else "")).encode()).hexdigest()[:16])
        if pt is not None:
            scored[w]["ref"] = pt
        if texts and w in texts:
            scored[w]["edited"] = True
    return dict(n_scored=n_scored, n_dropped=n_dropped, n_witness_like=n_wl)


def _pointers_after(bases: Sequence[str], s_tok: List[str], s_spans, n_seed: int, p: dict, scored: Dict[str, dict],
                    flags: List[dict], rnd: int, look: int = 10, texts: Optional[dict] = None) -> int:
    """The hadiths right after a found text that only point back at it («… عن أبي عبد الله عليه السلام مثله»): no words
    of the seed are in them, so no query finds them. Each is scored on the text it points at; returns how many."""
    pool: Dict[str, dict] = {}
    seed_rec = corpus.get(bases[0])
    for r in pointers.split(seed_rec)["refs"] if seed_rec else []:          # the seed's own inner references («<seed>~k»)
        if r["k"] and f"{bases[0]}~{r['k']}" not in scored:
            pool[f"{bases[0]}~{r['k']}"] = dict(signals={"ref", "inner"}, nq=0, round=rnd, via=bases[0], ref_of=None)
    for b in bases:
        if "~" in b:                                 # a reference inside a record has no place in the book of its own
            continue
        nxt = corpus.neighbours(b, look)
        recs = corpus.get_many(nxt)
        for w in nxt:                                # a run of references all refer to the text before the run
            r = recs.get(w)
            if r is None or pointers.lone_ref(r) is None:
                break
            if w not in scored and w not in pool:
                pool[w] = dict(signals={"ref"}, nq=0, round=rnd, via=b, ref_of=b)
    if not pool:
        return 0
    before = len(scored)
    _score_pool(bases[0], s_tok, s_spans, n_seed, pool, p, scored, flags, texts)
    return len(scored) - before


def _next_sources(scored: Dict[str, dict], p: dict, seed_norm: str):
    """Witnesses with the most divergent wording, used to expand the search."""
    ws = [c for c in scored.values() if preselected(c) and not c.get("ref")]      # a pointer has no words of its own
    seen, out = set(), []
    for c in sorted(ws, key=lambda c: -(c["score"]["s"] + 3 * (1 - c["score"]["cov_seed"]))):
        if c["text_hash"] in seen:
            continue
        seen.add(c["text_hash"])
        rec = corpus.get(c["wid"])
        tk, sp = clauses(rec["matn"])
        if sp:
            out.append((c["wid"], tk, sp))
        if len(out) >= p["expand_max"]:
            break
    return out


def mine(c: dict) -> bool:
    """A hadith the user added or named a seed: never folded into another row (others fold into it)."""
    return bool({"user", "seed"} & set(c["signals"]))


def _fold_copies(scored: Dict[str, dict], seed: str, seed_rec: dict) -> List[dict]:
    """Collapse byte-identical (normalised matn + sanad) candidates into one row with ``copies``."""
    seed_hash = hashlib.sha1((" ".join(tokens(seed_rec["matn"]))).encode()).hexdigest()[:16]
    by_hash: Dict[str, dict] = {}
    out: List[dict] = []
    for c in sorted(scored.values(), key=lambda c: (not mine(c), c["wid"])):
        if c["text_hash"] == seed_hash and c["score"]["cov_seed"] >= 0.99 and not mine(c):
            c["copy_of"] = seed
            out.append(c)
            continue
        key = c["norm_hash"]
        first = by_hash.get(key)
        if first is None:
            c["copies"] = []
            by_hash[key] = c
            out.append(c)
        else:
            first["copies"].append(c["wid"])
    return _fold_across_corpora(out)


def _title_set(t: Optional[str]) -> set:
    return set(norm(t or "").split())


FOLD_SIM = 0.92        # token similarity above which two texts of one work in the two corpora are one witness


def _fold_across_corpora(rows: List[dict]) -> List[dict]:
    """The same book appears in v02 and in the KG (since v02 took sunnah.com and Dorar from hadith.ai, often):
    titles one inside the other («الدعاء» / «الدعاء للطبراني»), the same text or nearly (another edition's
    diacritics and spacing). They are one witness; the KG row is kept (it carries exact narrator ids)."""
    kg = [r for r in rows if r["corpus"] == "kg"]
    if not kg:
        return rows
    titles = {}
    for r in rows:
        titles.setdefault(frozenset(_title_set(r["title"])), []).append(r)
    toks = {}

    def tk(r):
        if r["wid"] not in toks:
            toks[r["wid"]] = [match_key(t) for t in tokens(r["matn"])]
        return toks[r["wid"]]

    drop = set()
    keys = [k for k in titles if k]
    for ka in keys:
        for kb in keys:
            if not (ka <= kb or kb <= ka):
                continue
            for a in titles[ka]:
                if a["corpus"] != "kg" or a["wid"] in drop:
                    continue
                for b in titles[kb]:
                    if b["corpus"] == "kg" or b["wid"] in drop or mine(b):
                        continue
                    if a["text_hash"] != b["text_hash"]:
                        la, lb = a["n_tokens"], b["n_tokens"]
                        if not la or not lb or not 0.85 <= la / lb <= 1.18:
                            continue
                        if SequenceMatcher(None, tk(a), tk(b), autojunk=False).ratio() < FOLD_SIM:
                            continue
                    a["copies"] = a.get("copies", []) + [b["wid"]] + b.get("copies", [])
                    drop.add(b["wid"])
    return [r for r in rows if r["wid"] not in drop]


def with_marks(S: dict, parts: Optional[dict] = None) -> dict:
    """Add ``marks`` (``[start, end, clause]`` character ranges, see ``text.marks``) to the seed and to every
    candidate of a search result, for display: where each seed clause is in each text. Not stored (stage files
    stay as computed); the API adds it when serving the stage."""
    seed = S["seed"]
    lab = [-1] * len(S["tokens"])
    for c in S["clauses"]:
        x, y = c["span"]
        lab[x:y] = [c["i"]] * (y - x)
    seed["matn"] = strip_tags(seed["matn"])
    seed["marks"] = marks(seed["matn"], lab)
    for cand in S["candidates"]:
        cand["matn"] = strip_tags(cand["matn"])
    from . import align                                          # (align does not import search)
    sx = align.seed_extras(S, parts)                             # the parts the other hadiths of step 1's list add
    S["seed_extras"], S["seed_extras_dropped"] = sx["extras"], sx["dropped"]
    for cand in S["candidates"]:
        cand.pop("marks_all", None)
        lab = [-1] * cand["n_tokens"]
        # the best-matched clause claims a token first (two clauses of the seed may share words)
        for part in sorted(cand["parts"], key=lambda q: -q["cov"]):
            if part["state"] == "absent" or not part["window"]:
                continue
            x, y = part["window"]
            for j in range(x, min(y, len(lab))):
                if lab[j] < 0:
                    lab[j] = part["clause"]
        cand["marks"] = marks(cand["matn"], lab)
        if cand["wid"] in sx["found"]:                           # for step 1 only (steps that list the parts know the first hadith's)
            for i, x, y in sx["found"][cand["wid"]]:
                for j in range(x, min(y, len(lab))):
                    if lab[j] < 0:
                        lab[j] = i
            cand["marks_all"] = marks(cand["matn"], lab)
    return S
