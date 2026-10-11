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
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import corpus, relations
from .similarity import align_clauses, classify
from .text import clauses, grams, marks, norm, squeeze, strip_tags, tokens

PRESELECTED = ("witness", "extended", "abridged", "variant")

DEFAULTS = dict(
    corpora=["v02", "kg"],
    rounds=2,                  # 1 = seed only; each further round expands from found witnesses
    part_query=None,           # list of clause indexes to restrict the FTS queries to
    fts_limit=3000,            # rows per FTS query
    pool_max=5000,             # candidates fetched for scoring per round
    prefilter=0.06,            # min share of seed 8-grams (or one clause >= 0.5) to be scored
    expand_max=10,             # witnesses used as expansion sources per round
    keep_min_cov=0.15,         # candidates below this seed coverage and with no clause hit are dropped
    pair_df_max=60000,         # terms more frequent than this are not used in queries
    phrase_df_max=20000,
    gram_n=8,
)


def run(seed: str, params: Optional[dict] = None, extra_ids: Sequence[str] = ()) -> dict:
    p = {**DEFAULTS, **(params or {})}
    t0 = time.time()
    rec = corpus.get(seed)
    if rec is None:
        raise KeyError(f"seed {seed} not found")
    s_tok, s_spans = clauses(rec["matn"])
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

    sources = [(seed, s_tok, s_spans)]
    for rnd in range(1, int(p["rounds"]) + 1):
        new_pool: Dict[str, dict] = {}
        for src_wid, src_tok, src_spans in sources:
            _generate(src_wid, src_tok, src_spans, q_clauses if rnd == 1 else range(len(src_spans)),
                      p, rnd, new_pool, queries)
        for w in extra_ids:
            new_pool.setdefault(w, dict(signals=set(), nq=0, round=rnd, via=None))["signals"].add("user")
        for w in list(new_pool):
            if w == seed or w in scored:
                del new_pool[w]
        n_cand = len(new_pool)
        batch = _score_pool(seed, s_tok, s_spans, n_seed, new_pool, p, scored, flags)
        rounds_info.append(dict(round=rnd, n_candidates=n_cand, n_scored=batch["n_scored"],
                                n_dropped=batch["n_dropped"], n_witness_like=batch["n_witness_like"]))
        if rnd == int(p["rounds"]):
            break
        sources = _next_sources(scored, p, seed_norm)
        if not sources:
            break

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
        params=p, candidates=cands, queries=queries, rounds=rounds_info, flags=flags,
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
                scored: Dict[str, dict], flags: List[dict]) -> dict:
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
    for w in order:
        rec = recs.get(w)
        if rec is None:
            continue
        c_tok = tokens(rec["matn"])
        if not c_tok:
            continue
        g = grams(" ".join(c_tok), p["gram_n"])
        share = len(seed_grams & g) / max(len(seed_grams), 1)
        cl_hit = any(cg and len(cg & g) / len(cg) >= 0.5 for cg in clause_grams)
        user = "user" in pool[w]["signals"]
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
            window=window, window_text=" ".join(c_tok[window[0]:window[1]]),
            n_tokens=len(c_tok), matn=rec["matn"][:1500], matn_len=len(rec["matn"]),
            sanad=(rec["sanad"] or "")[:800],
            norm_hash=hashlib.sha1((" ".join(c_tok) + "|" + norm(rec["sanad"] or "")).encode()).hexdigest()[:16],
            text_hash=hashlib.sha1(" ".join(c_tok).encode()).hexdigest()[:16])
    return dict(n_scored=n_scored, n_dropped=n_dropped, n_witness_like=n_wl)


def _next_sources(scored: Dict[str, dict], p: dict, seed_norm: str):
    """Witnesses with the most divergent wording, used to expand the search."""
    ws = [c for c in scored.values() if c["suggested"] in PRESELECTED]
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


def _fold_copies(scored: Dict[str, dict], seed: str, seed_rec: dict) -> List[dict]:
    """Collapse byte-identical (normalised matn + sanad) candidates into one row with ``copies``."""
    seed_hash = hashlib.sha1((" ".join(tokens(seed_rec["matn"]))).encode()).hexdigest()[:16]
    by_hash: Dict[str, dict] = {}
    out: List[dict] = []
    for c in sorted(scored.values(), key=lambda c: c["wid"]):
        if c["text_hash"] == seed_hash and c["score"]["cov_seed"] >= 0.99:
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


def _fold_across_corpora(rows: List[dict]) -> List[dict]:
    """The same book appears in v02 and in the KG: identical text, titles one inside the other
    («الدعاء» / «الدعاء للطبراني»). They are one witness; the KG row is kept (it carries exact narrator ids)."""
    by_text: Dict[str, List[dict]] = {}
    for r in rows:
        by_text.setdefault(r["text_hash"], []).append(r)
    drop = set()
    for group in by_text.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: (r["corpus"] != "kg", r["wid"]))
        for i, a in enumerate(group):
            if a["wid"] in drop:
                continue
            for b in group[i + 1:]:
                if b["wid"] in drop or a["corpus"] == b["corpus"]:
                    continue
                ta, tb = _title_set(a["title"]), _title_set(b["title"])
                if ta and tb and (ta <= tb or tb <= ta):
                    a["copies"] = a.get("copies", []) + [b["wid"]] + b.get("copies", [])
                    drop.add(b["wid"])
    return [r for r in rows if r["wid"] not in drop]


def with_marks(S: dict) -> dict:
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
        lab = [-1] * cand["n_tokens"]
        # the best-matched clause claims a token first (two clauses of the seed may share words)
        for part in sorted(cand["parts"], key=lambda q: -q["cov"]):
            if part["state"] == "absent" or not part["window"]:
                continue
            x, y = part["window"]
            for j in range(x, min(y, len(lab))):
                if lab[j] < 0:
                    lab[j] = part["clause"]
        cand["matn"] = strip_tags(cand["matn"])
        cand["marks"] = marks(cand["matn"], lab)
    return S
