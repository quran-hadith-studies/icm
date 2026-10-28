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

from . import corpus, family, lex, pointers, relations, semantic
from .similarity import align_clauses, classify, match_key
from .text import clauses, grams, marks, norm, squeeze, strip_tags, token_offsets, tokens, union_cuts

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
    lex=False,                 # root-aware search (``lex.sqlite``): roots in the queries and in the scoring (see ``_generate``)
    root_df_max=150000,        # a root in more documents than this is not used to ask for candidates
    rnear_width=8,             # words within which the roots of a NEAR query must stand
    root_min_cov=0.6,          # share of a clause's roots a candidate must have for it to be scored when the 8-grams say no
    sem=False,                 # semantic search (``semantic.py``): hadiths of the meaning of a part in other words
    sem_k_part=40,             # nearest hadiths asked for per ticked part ...
    sem_k_all=80,              # ... and for all the ticked parts of a hadith together
    sem_occ_max=2000,          # hadiths of one ticked part that join the pool (a very common part stands in thousands)
    sem_items=60,              # hadiths listed under one part text in step 4
    sem_min=0.75,              # least cosine (bge-m3) for a hadith found only by meaning to be a candidate: keeps 91% of the hadiths of one consensus set with other wording, see ``evaluate.hard_pairs``
)


def run(seed: str, params: Optional[dict] = None, extra_ids: Sequence[str] = (), keep_ids: Sequence[str] = (), *,
        seeds: Sequence[str] = (), prior: Optional[dict] = None, retrieve: bool = True, texts: Optional[dict] = None, semantic_only: bool = False,
        sem_passages: bool = False, sem_picks: Optional[Sequence[dict]] = None) -> dict:
    """``seed`` and ``seeds`` are the hadiths of step 1's list, all equal: each is cut into parts on its own
    (``params["clauses"]``: the user's cuts, ``{wid: [[start, end), ...]}``, or a list for ``seed``) and the cuts that are
    the same part make the family's parts (``family.py``); candidates are compared with those. ``seed`` of the result is
    the one the family is named by (``family.canonical``), whatever the order they were given in.
    ``keep_ids``: witnesses selected before the user re-cut the hadiths; they are scored whatever their score.
    ``extra_ids``: hadiths the user added; scored whatever their score. ``seeds`` are searched from like ``seed``
    (their own words are queried), always candidates, never folded away. ``retrieve=False`` asks the corpora nothing: only
    ``prior`` (wid -> signals/round/via of an earlier search's candidates), the extras, kept and source hadiths are scored again,
    against the parts as they are now. ``texts``: wid -> the text the user edited, used instead of the corpus's.
    ``semantic_only``: with ``retrieve=False``, still ask the semantic index (step 4's own press) and add what it finds to ``prior``'s pool.
    ``sem_passages``: step 4's first press: ask the semantic index for the passages of other hadiths that resemble each ticked part and
    return them as ``sem_parts`` (no hadith joins the pool). ``sem_picks``: its second press: the passages the user ticked
    (``[dict(wid, clause, cos, first, copies)]``); their hadiths join the pool with the signal ``q:sem``."""
    p = {**DEFAULTS, **(params or {})}
    t0 = time.time()
    texts = texts or {}
    order = list(dict.fromkeys([seed, *seeds]))
    src = {w: s for w in order for s in [family.source(w, texts)] if s is not None}
    if seed not in src:
        raise KeyError(f"seed {seed} not found")
    user = p.get("clauses")                       # the user's cuts (checked by the API): one list = the first hadith's
    cuts, by_user = {}, {}
    for w, s in src.items():
        cuts[w], by_user[w] = family.cut(s, user.get(w) if isinstance(user, dict) else user if w == seed else None)
    cuts_tok = {w: (src[w]["tok"], cuts[w]) for w in src}
    join = p.get("join")                          # None: the hadiths' cuts are not joined (the button was not pressed); a list of groups: they are
    if join is True:
        join = family.groups(cuts_tok)
    fresh = family.groups(cuts_tok) if len(src) > 1 else []
    parts = family.build(cuts_tok, join)
    if not parts:
        raise ValueError("seed has no text")
    seed = family.canonical({w: s for w, s in src.items() if any(w in q["members"] for q in parts)}, parts)
    seeds = [w for w in src if w != seed]
    rec, s_tok_seed = src[seed]["rec"], src[seed]["tok"]
    s_tok, s_spans = family.reference(parts)
    owners = None                                 # who speaks each part (hadith.ai), from a hadith whose automatic cut it is
    for q in parts:
        for w in sorted(q["members"], key=lambda x: x != seed):
            if not by_user[w] and src[w]["owners"] and list(q["members"][w]) in [list(x) for x in cuts[w]]:
                owners = owners or [None] * len(parts)
                owners[q["i"]] = src[w]["owners"][[list(x) for x in cuts[w]].index(list(q["members"][w]))]
                break
    flags: List[dict] = []
    if len(s_tok) < 6:
        flags.append(dict(kind="seed_short", ref=seed, message="seed has fewer than 6 tokens; search is unreliable"))
    n_seed = sum(y - x for x, y in s_spans)
    sel = p["part_query"]
    q_clauses = [c for c in range(len(s_spans)) if sel is None or c in sel]

    pool: Dict[str, dict] = {}
    queries: List[dict] = []
    rounds_info: List[dict] = []
    scored: Dict[str, dict] = {}
    sem_parts: Optional[List[dict]] = None
    seed_norm = norm(rec["matn"])

    def own_parts(w):                             # the ticked parts a source hadith has, in its own words
        return [(q["i"], *q["members"][w]) for q in parts if w in q["members"] and q["i"] in q_clauses]
    sources = [(w, src[w]["tok"], own_parts(w)) for w in [seed, *seeds]] if retrieve else []
    for rnd in range(1, (int(p["rounds"]) if retrieve else 1) + 1):
        new_pool: Dict[str, dict] = {}
        if retrieve:
            for src_wid, src_tok, src_parts in sources:
                _generate(src_wid, src_tok, src_parts, p, rnd, new_pool, queries)
        elif rnd == 1:
            for w, d in (prior or {}).items():
                new_pool[w] = dict(signals=set(d.get("signals") or ()), nq=0, round=d.get("round") or 1, via=d.get("via"), sem=d.get("sem"),
                                sem_hits={h["clause"]: (h["cos"], h.get("span") or h.get("first", 0)) for h in d.get("sem_hits") or ()})
        if (retrieve or semantic_only) and rnd == 1 and p.get("sem") and semantic.available():
            _semantic(src, own_parts, [seed, *seeds], p, new_pool, queries, flags)
        if rnd == 1 and sem_passages and semantic.available():
            sem_parts = _sem_passages(src, own_parts, [seed, *seeds], p, queries, flags)
        if rnd == 1 and sem_picks:
            _picked(src, sem_picks, new_pool, p, flags)
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
        batch = _score_pool(seed, s_tok, s_spans, n_seed, new_pool, p, scored, flags, texts, rnd)
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
        flags.append(dict(kind="family_large", ref=None, n=len(cands), message=f"{len(cands)} candidates"))
    return dict(
        seed=seed_out, tokens=s_tok, seed_tokens=s_tok_seed,
        clauses=[dict(i=q["i"], span=list(s_spans[q["i"]]), text=q["text"], members=q["members"]) for q in parts],
        cuts={w: [list(x) for x in cuts[w]] for w in src}, auto_cuts={w: [list(x) for x in src[w]["auto"]] for w in src},
        cuts_by={w: ("user" if by_user[w] else "auto") for w in src}, cut_tokens={w: len(src[w]["tok"]) for w in src},
        clauses_by="user" if any(by_user.values()) else "auto",
        join=dict(built=join is not None, groups=join or [], fresh=fresh, stale=join is not None and _norm_groups(join, cuts_tok) != fresh),
        auto_by={w: src[w]["by"] for w in src}, clause_owners=owners,
        params=p, seeds=seeds, retrieved=bool(retrieve), candidates=cands, queries=queries, rounds=rounds_info, flags=flags,
        seconds=round(time.time() - t0, 1), **({"sem_parts": sem_parts} if sem_parts is not None else {}))


# ---------------------------------------------------------------------------
def _rank(c: dict) -> float:
    order = {"witness": 5, "extended": 4.5, "abridged": 4, "variant": 3.5, "partial": 2, "semantic": 1, "different": 0}
    sc = c["score"]
    return order.get(c["suggested"], 0) * 10 + sc["cov_seed"] + 0.2 * sc["cov_cand"] + 3 * (sc.get("sem") or 0)


def _best_terms(tok: Sequence[str], dfs: Dict[str, Tuple[int, int]], idx: int, df_max: int, k: int = 2) -> List[str]:
    cand = sorted({t for t in tok if len(t) >= 3 and 0 < dfs.get(t, (0, 0))[idx] <= df_max},
                  key=lambda t: (dfs[t][idx], -len(t)))
    return cand[:k]


def _norm_groups(join, cuts_tok) -> list:
    """The groups the user asked for, as they stand now (a group's cut that is not a cut of its hadith any more has left it)."""
    have = {(w, a, b) for w, (tok, sp) in cuts_tok.items() for a, b in sp}
    out = [[g for g in grp if tuple(g) in have] for grp in join]
    return sorted(sorted(g) for g in out if len(g) > 1)


def _generate(wid: str, tok: List[str], parts: Iterable[Tuple[int, int, int]], p: dict, rnd: int,
              pool: Dict[str, dict], queries: List[dict]) -> None:
    """Fill ``pool`` with candidate ids proposed for source ``wid``; ``parts``: (part, start, end) of the words to query."""
    def add(w: str, sig: str):
        d = pool.setdefault(w, dict(signals=set(), nq=0, round=rnd, via=None if rnd == 1 else wid))
        d["signals"].add(sig)
        d["nq"] += 1

    for sig, ids in (relations.signals(wid).items() if p.get("rel", True) else ()):       # (``rel=False``: evaluation without the ready-made groups)
        for w in ids:
            add(w, "rel:" + sig)
    if p.get("lex") and lex.available():
        _generate_lex(wid, tok, parts, p, rnd, add, queries)
        return
    all_terms = sorted({t for t in tok if len(t) >= 3})
    dfs = corpus.term_df(all_terms)
    for c, x, y in parts:
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


def _generate_lex(wid: str, tok: List[str], parts: Iterable[Tuple[int, int, int]], p: dict, rnd: int, add, queries: List[dict]) -> None:
    """``_generate`` over ``lex.sqlite``: per part and corpus, queries on words (a distinctive pair, widened to the forms
    with another conjunction / preposition / article; an exact phrase; the three rarest words near each other) and on roots
    (a distinctive pair; the three rarest roots near each other; a phrase of roots), each ranked best first when more
    hits exist than ``fts_limit``. Only the matn is searched (the served index also holds the translation)."""
    names = [n for n in ("v02", "kg") if n in p["corpora"]]
    wdf = lex.term_df(sorted({t for t in tok if len(t) >= 3}))
    rts = lex.roots(tok)
    rdf = lex.root_df(set(rts.values()))
    for c, x, y in parts:
        ct = tok[x:y]
        for name in names:
            idx = 0 if name == "v02" else 1
            exprs: List[Tuple[str, str]] = []
            best = _best_terms(ct, wdf, idx, p["pair_df_max"], 3)
            if len(best) >= 2 or (len(best) == 1 and len(ct) <= 4):
                grp = ["matn:(%s)" % " OR ".join('"%s"' % f for f in lex.clitic_forms(t, name)) for t in best[:2]]
                exprs.append(("pair", " AND ".join(grp)))
            wins = []
            for i in range(len(ct) - 2):
                d = [wdf.get(t, (0, 0))[idx] for t in ct[i:i + 3]]
                if min(d) > 0 and min(d) <= p["phrase_df_max"]:
                    wins.append((min(d), i))
            for _, i in sorted(wins)[:2]:
                exprs.append(("phrase", 'matn:"%s"' % " ".join(ct[i:i + 3])))
            if len(best) >= 3:
                exprs.append(("near", 'matn: NEAR(%s, %d)' % (" ".join('"%s"' % t for t in best[:3]), p["rnear_width"])))
            # roots: the distinctive ones of the part, rarest first
            rk = sorted({rts[t] for t in ct if t in rts and len(t) >= lex.MIN_ROOT_WORD and 0 < rdf[rts[t]][idx] <= p["root_df_max"]},
                        key=lambda k: (rdf[k][idx], k))
            if len(rk) >= 2:
                exprs.append(("root", " AND ".join('root:"%s"' % lex.root_tok(k) for k in rk[:2])))
            if len(rk) >= 3:
                exprs.append(("rnear", 'root: NEAR(%s, %d)' % (" ".join('"%s"' % lex.root_tok(k) for k in rk[:3]), p["rnear_width"])))
            rwin = []
            for i in range(len(ct) - 2):
                ks = [rts.get(t) for t in ct[i:i + 3]]
                if all(ks) and min(rdf[k][idx] for k in ks) > 0:
                    rwin.append((min(rdf[k][idx] for k in ks), i, ks))
            for _, i, ks in sorted(rwin)[:1]:
                exprs.append(("rphrase", 'root:"%s"' % " ".join(lex.root_tok(k) for k in ks)))
            seen = set()
            if rnd > 1:                                       # expansion from a found witness: only the tight questions (a broad one
                exprs = [(k, e) for k, e in exprs if k in ("phrase", "near", "rnear", "rphrase")]     # would add thousands of candidates for a few)
            for kind, expr in exprs:
                if not expr or expr in seen:
                    continue
                seen.add(expr)
                r = lex.search(name, expr, p["fts_limit"] if rnd == 1 else min(p["fts_limit"], 800), rank="auto")
                queries.append(dict(round=rnd, source=wid, clause=c, kind=kind, corpus=name, expr=expr, hits=len(r["ids"]),
                                    capped=r["capped"], ranked=r["ranked"], timed_out=r["timed_out"]))
                for i in r["ids"]:
                    add(f"{name}:{i}", "q:" + kind)


def _semantic(src: dict, own_parts, order: Sequence[str], p: dict, pool: Dict[str, dict], queries: List[dict], flags: List[dict]) -> None:
    """Hadiths of the meaning of the ticked parts: for every source hadith each ticked part (in the words of the hadith, not
    the normalised ones) and all its ticked parts together are looked up in the semantic index; a hadith with a cosine of
    ``sem_min`` or more joins the pool with the signal ``q:sem`` and its cosine (the best of its finds)."""
    texts: List[str] = []
    tags: List[Tuple[str, int]] = []
    ks: List[int] = []
    for w in order:
        raw = src[w]["rec"]["matn"]
        offs, st = token_offsets(raw), strip_tags(raw)
        got = [(c, st[offs[x][0]:offs[y - 1][1]]) for c, x, y in own_parts(w) if 0 <= x < y <= len(offs)]
        for c, t in got:
            texts.append(t)
            tags.append((w, c))
            ks.append(p["sem_k_part"])
        if len(got) > 1:
            texts.append(" ".join(t for _, t in got))
            tags.append((w, -1))
            ks.append(p["sem_k_all"])
    if not texts:
        return
    t0 = time.time()
    try:
        res = semantic.hadith_hits(texts, k=max(ks), min_cos=p["sem_min"], exclude=set(src))
    except Exception as e:                                   # a missing or broken index must not fail the search
        flags.append(dict(kind="semantic_unavailable", ref=None, message=f"semantic search failed: {e}"))
        return
    for (w, c), k, hits in zip(tags, ks, res):
        n_new = 0
        for wid, cos, span, _ in hits[:k]:
            if wid in src:
                continue
            d = pool.setdefault(wid, dict(signals=set(), nq=0, round=1, via=None))
            n_new += "q:sem" not in d["signals"]
            d["signals"].add("q:sem")
            d["nq"] += 1
            d["sem"] = max(d.get("sem") or 0.0, cos)
            if cos > d.setdefault("sem_hits", {}).get(c, (0.0, 0))[0]:      # per part of the sources (-1: all parts together): the best
                d["sem_hits"][c] = (cos, span)
        queries.append(dict(round=1, source=w, clause=c, kind="sem", corpus="v02+kg", expr=texts[tags.index((w, c))][:90], hits=min(len(hits), k),
                            capped=len(hits) >= k, new=n_new))


def _shared_words(text: str, own: set) -> list:
    """``[[word, whether the part has it]]`` for the words of ``text`` (``own``: the ``match_key`` of the part's words)."""
    return [[w, bool(own & {match_key(t) for t in tokens(w)})] for w in text.split()]


def _has_words(hay: Sequence[str], needles: Sequence[Sequence[str]]) -> bool:
    """Does ``hay`` (a part's words) contain, in a row, all the words of one of ``needles`` (the wordings of the ticked part)?"""
    for nd in needles:
        n = len(nd)
        if n and any(tuple(hay[i:i + n]) == tuple(nd) for i in range(len(hay) - n + 1)):
            return True
    return False


def _sem_passages(src: dict, own_parts, order: Sequence[str], p: dict, queries: List[dict], flags: List[dict]) -> List[dict]:
    """The TEXTS of other hadiths' parts that resemble each ticked part: ``[dict(clause, variants=[...])]``, one entry per ticked
    part (the hadiths of step 1's list each ask with their own words of it; a part found by several keeps its best cosine), the
    nearest first, at most ``sem_k_part`` per part and none under ``sem_min``. A text that has the words of the ticked part as they are (``same_words`` counts them) is left out: step 3's word search finds its hadiths. A variant is one distinct part text: ``id`` (its row in
    the semantic index), ``text``, ``ntok``, ``cos``, ``n`` (hadiths that have it), ``words`` (``[[word, shared with the part]]``)
    and ``items`` (the first ``sem_items`` of those hadiths: ``wid``, ``span`` [a, b) in its words, ``src`` how the part was cut, book
    data). Nothing joins the pool: the user picks texts first."""
    texts: List[str] = []
    tags: List[Tuple[str, int]] = []
    for w in order:
        raw = src[w]["rec"]["matn"]
        offs, st = token_offsets(raw), strip_tags(raw)
        for c, x, y in own_parts(w):
            if 0 <= x < y <= len(offs):
                texts.append(st[offs[x][0]:offs[y - 1][1]])
                tags.append((w, c))
    if not texts:
        return []
    try:                                                     # more than will be shown: the near-copies are dropped below
        res = semantic.neighbours(texts, k=min(3 * p["sem_k_part"], 300), min_cos=p["sem_min"])
    except Exception as e:                                   # a missing or broken index must not fail the run
        flags.append(dict(kind="semantic_unavailable", ref=None, message=f"semantic search failed: {e}"))
        return []
    best: Dict[int, Dict[int, float]] = {}
    for (w, c), hits in zip(tags, res):
        for pid, cos in hits:
            if cos > best.setdefault(c, {}).get(pid, 0.0):
                best[c][pid] = cos
    got = semantic.parts_of(sorted({pid for d in best.values() for pid in d}), exclude=set(src), occ_max=p["sem_items"])
    own_keys: Dict[int, set] = {}
    own_seq: Dict[int, List[tuple]] = {}
    for (w, c), t in zip(tags, texts):
        ks = [match_key(x) for x in tokens(t)]
        own_keys.setdefault(c, set()).update(ks)
        own_seq.setdefault(c, []).append(tuple(ks))
    kept: Dict[int, List[Tuple[int, float]]] = {}
    dropped: Dict[int, int] = {}
    for c in sorted(best):
        for pid, cos in sorted(best[c].items(), key=lambda kv: -kv[1]):
            g = got.get(pid)
            if not g or not g["n"]:
                continue                                         # it stands only in the hadiths of step 1's list
            if _has_words([match_key(x) for x in tokens(g["text"])], own_seq.get(c, ())):
                dropped[c] = dropped.get(c, 0) + 1               # it has the part's words as they are: step 3's word search finds its hadiths
            elif len(kept.setdefault(c, [])) < p["sem_k_part"]:
                kept[c].append((pid, cos))
    recs = corpus.get_many(sorted({o[0] for d in kept.values() for pid, _ in d for o in got[pid]["occ"]}))
    out = []
    for c in sorted(best):
        vs = []
        for pid, cos in kept.get(c, ()):
            g = got[pid]
            items = []
            for wid, a, b, sc in g["occ"]:
                rec = recs.get(wid)
                if rec is None:
                    continue
                items.append(dict(wid=wid, span=[a, b], src=sc, book=rec["book"], title=rec["title"], author=rec["author"],
                                  tradition=rec["tradition"], vol=rec["vol"], page=rec["page"], copies=[]))
            vs.append(dict(id=pid, text=g["text"], ntok=g["ntok"], cos=round(cos, 3), n=g["n"], words=_shared_words(g["text"], own_keys.get(c, set())), items=items))
        out.append(dict(clause=c, variants=vs, same_words=dropped.get(c, 0)))
        queries.append(dict(round=1, source=order[0], clause=c, kind="sem", corpus="v02+kg", expr="parts", hits=len(vs), capped=len(vs) >= p["sem_k_part"], new=len(vs)))
    return out


def resolve_picks(sem_parts: Sequence[dict], picks: Sequence) -> List[dict]:
    """The texts the user ticked, ``[[part id, clause], ...]``, as ``_sem_passages`` found them (a pair it did not find is ignored).
    Parts found by the first index (``items`` per hadith, picks ``[wid, clause]``) resolve as they did."""
    by, old = {}, {}
    for q in sem_parts or ():
        for v in q.get("variants") or ():
            by[(str(v["id"]), q["clause"])] = v
        for it in q.get("items") or ():
            old[(it["wid"], q["clause"])] = it
    out = []
    for pid, c in picks or ():
        if (pid, c) in by:
            out.append(dict(id=int(pid), clause=c, cos=by[(pid, c)]["cos"]))
        elif (pid, c) in old:
            it = old[(pid, c)]
            out.append(dict(wid=pid, clause=c, cos=it["cos"], first=it["first"], copies=it.get("copies") or []))
    return out


def _picked(src: dict, picks: Sequence[dict], pool: Dict[str, dict], p: dict, flags: List[dict]) -> None:
    """The hadiths that have the ticked texts join the pool as found by meaning, with the part and where the text stands in them."""
    def put(wid, clause, cos, loc):
        if wid in src:
            return
        d = pool.setdefault(wid, dict(signals=set(), nq=0, round=1, via=None))
        d["signals"].add("q:sem")
        d["nq"] += 1
        d["sem"] = max(d.get("sem") or 0.0, cos)
        if cos > d.setdefault("sem_hits", {}).get(clause, (0.0, 0))[0]:
            d["sem_hits"][clause] = (cos, loc)
    for pk in picks:
        if "id" in pk:
            g = semantic.parts_of([pk["id"]], exclude=set(src), occ_max=p["sem_occ_max"])[pk["id"]]
            if g["n"] > p["sem_occ_max"]:
                flags.append(dict(kind="sem_part_large", ref=None, n=g["n"], kept=p["sem_occ_max"],
                                  message=f"a ticked text stands in {g['n']} hadiths; the first {p['sem_occ_max']} were taken"))
            for wid, a, b, _ in g["occ"]:
                put(wid, pk["clause"], pk["cos"], [a, b])
        else:
            for wid in [pk["wid"], *pk.get("copies", ())]:
                put(wid, pk["clause"], pk["cos"], pk["first"])


def _shown(matn: str, sem_hits: List[dict]) -> int:
    """How many characters of a candidate's text are kept: 1500, or more when a part matched in it by meaning lies further on."""
    end, offs = 0, None
    for h in sem_hits:
        if h["clause"] < 0:
            continue
        if "span" in h:
            offs = offs or token_offsets(matn)
            b = min(h["span"][1], len(offs))
            end = max(end, offs[b - 1][1] + 20 if b > 0 else 0)
        else:
            r = semantic.chunk_range(matn, h["first"])
            end = max(end, r[1] + 20 if r else 0)
    return min(max(1500, end + len(matn) - len(strip_tags(matn))), 6000)


def _score_pool(seed: str, s_tok: List[str], s_spans, n_seed: int, pool: Dict[str, dict], p: dict,
                scored: Dict[str, dict], flags: List[dict], texts: Optional[dict] = None, rnd: Optional[int] = None) -> dict:
    order = sorted(pool, key=lambda w: (-len(pool[w]["signals"]), -pool[w]["nq"]))
    capped = len(order) > p["pool_max"]
    if capped:
        flags.append(dict(kind="pool_capped", ref=None, n=len(order), kept=p["pool_max"], round=rnd,
                          message=f"{len(order)} candidates; the {p['pool_max']} with most signals were scored"))
        order = order[:p["pool_max"]]
    seed_grams = grams(" ".join(s_tok), p["gram_n"])
    clause_grams = [grams(" ".join(s_tok[x:y]), p["gram_n"]) for x, y in s_spans]
    lex_on = bool(p.get("lex")) and lex.available()
    keyf = lex.search_key if lex_on else match_key
    clause_roots: List[set] = []
    if lex_on:                                                  # words of one root are one word when texts are compared
        lex.prime(s_tok)
        clause_roots = [lex.root_set(s_tok[x:y]) for x, y in s_spans]
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
        via_root = False
        if lex_on and not cl_hit and share < p["prefilter"]:     # the same roots in other forms (another inflection, clitic …)
            lex.prime(c_tok)
            croots = lex.root_set(c_tok)
            cl_hit = via_root = any(len(cr) >= 2 and len(cr & croots) / len(cr) >= p["root_min_cov"] for cr in clause_roots)
        user = bool({"user", "kept", "ref", "seed", "q:sem"} & pool[w]["signals"])       # (found by meaning: scored whatever the words)
        if share < p["prefilter"] and not cl_hit and not user:
            n_dropped += 1
            continue
        if lex_on:
            lex.prime(c_tok)
        res = align_clauses(s_tok, s_spans, c_tok, keyf)
        cls, score = classify(res, n_seed, len(c_tok))
        score["share_grams"] = round(share, 3)
        sem = pool[w].get("sem")
        sem_hits = [dict(clause=c, cos=round(v[0], 3), **({"span": list(v[1])} if isinstance(v[1], (list, tuple)) else {"first": v[1]})) for c, v in sorted((pool[w].get("sem_hits") or {}).items(), key=lambda kv: -kv[1][0])]
        if sem is not None:
            score["sem"] = round(sem, 3)
            if cls in ("different", "partial") and not any(g.startswith(("q:", "rel:", "ref")) and g != "q:sem" for g in pool[w]["signals"]):
                cls = "semantic"                               # found only by its meaning, not by words: its own class
        if cls == "different" and (score["cov_seed"] < p["keep_min_cov"] or via_root) and not user:
            n_dropped += 1                                         # (one asked for only by its roots must resemble the seed)
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
            n_tokens=len(c_tok), matn=rec["matn"] if "seed" in pool[w]["signals"] else rec["matn"][:_shown(rec["matn"], sem_hits)], matn_len=len(rec["matn"]),
            sanad=(rec["sanad"] or "")[:800],
            norm_hash=hashlib.sha1((" ".join(c_tok) + "|" + norm(rec["sanad"] or "")).encode()).hexdigest()[:16],
            text_hash=hashlib.sha1((" ".join(c_tok) + ("|ref:" + w if pt is not None else "")).encode()).hexdigest()[:16])
        if sem_hits:
            scored[w]["sem_hits"] = sem_hits
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
            out.append((c["wid"], tk, [(i, x, y) for i, (x, y) in enumerate(sp)]))
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


def sem_variants(S: dict) -> List[dict]:
    """``S["sem_parts"]`` (one item per hadith) as the user reads it: per part, the distinct TEXTS of the passages found (the same
    words, whatever the diacritics and spelling, are one text) with the hadiths that have each. ``words``: the passage's words,
    each with whether the part itself has it (``match_key``). Shown by the semantic step; not stored."""
    out = []
    own = {c["i"]: {match_key(t) for t in c["text"].split()} for c in S.get("clauses") or []}
    for q in S.get("sem_parts") or []:
        have, groups = own.get(q["clause"], set()), {}
        for it in q["items"]:
            g = groups.setdefault(" ".join(tokens(it["text"])), dict(text=it["text"], cos=it["cos"], cut_before=it["cut_before"], cut_after=it["cut_after"], items=[]))
            g["items"].append({k: it[k] for k in ("wid", "cos", "copies", "book", "title", "author", "tradition", "vol", "page")})
        vs = []
        for g in groups.values():
            g["words"] = [[w, bool(have & {match_key(t) for t in tokens(w)})] for w in g["text"].split()]
            g["cos"] = max(i["cos"] for i in g["items"])
            g["n"] = sum(1 + len(i["copies"]) for i in g["items"])
            vs.append(g)
        out.append(dict(clause=q["clause"], variants=sorted(vs, key=lambda g: -g["cos"])))
    return out


def with_marks(S: dict) -> dict:
    """Add ``marks`` (``[start, end, clause]`` character ranges, see ``text.marks``) to the seed and to every
    candidate of a search result, for display: where each part is in each text. A hadith of step 1's list shows its own
    cut (``clauses[i].members``), any other text where the part was found in it. Not stored (stage files
    stay as computed); the API adds it when serving the stage."""
    seed = S["seed"]
    members = {w: [] for w in (S.get("cuts") or {})}
    for c in S["clauses"]:
        if c.get("members"):
            for w, (x, y) in c["members"].items():
                members.setdefault(w, []).append((c["i"], x, y))
        else:                                                    # a search from before every hadith of the list was cut: the seed's
            members.setdefault(seed["wid"], []).append((c["i"], *c["span"]))
    def own(w: str, n: int) -> List[int]:
        lab = [-1] * n
        for i, x, y in members.get(w, []):
            lab[x:y] = [i] * max(0, min(y, n) - x)
        return lab
    seed["matn"] = strip_tags(seed["matn"])
    seed["marks"] = marks(seed["matn"], own(seed["wid"], len(S.get("seed_tokens") or S["tokens"])))
    for cand in S["candidates"]:
        cand["matn"] = strip_tags(cand["matn"])
        if "seed" in cand["signals"] and cand["wid"] in members and S.get("cuts"):
            lab = own(cand["wid"], cand["n_tokens"])
        else:
            lab = [-1] * cand["n_tokens"]
            # the best-matched clause claims a token first (two clauses of the seed may share words)
            for part in sorted(cand["parts"], key=lambda q: -q["cov"]):
                if part["state"] == "absent" or not part["window"]:
                    continue
                x, y = part["window"]
                for j in range(x, min(y, len(lab))):
                    if lab[j] < 0:
                        lab[j] = part["clause"]
            known = {c["i"] for c in S["clauses"]}
            offs = token_offsets(cand["matn"])
            for h in sorted(cand.get("sem_hits") or (), key=lambda h: -h["cos"]):         # where a part matched by its meaning
                if h["clause"] not in known:
                    continue
                if "span" in h:                                  # a part of the index: its words
                    for j in range(h["span"][0], min(h["span"][1], len(lab))):
                        if lab[j] < 0:
                            lab[j] = h["clause"]
                    continue
                r = semantic.chunk_range(cand["matn"], h["first"])      # a window of the first index
                for j, (a, b) in enumerate(offs[:len(lab)]):
                    if r and lab[j] < 0 and a >= r[0] and b <= r[1]:
                        lab[j] = h["clause"]
        cand["marks"] = marks(cand["matn"], lab)
    if S.get("sem_parts"):
        S["sem_variants"] = S["sem_parts"] if any("variants" in q for q in S["sem_parts"]) else sem_variants(S)
    return S
