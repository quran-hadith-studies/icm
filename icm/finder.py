"""The wizard's hadith search for people: a query in the language of ``icm.query`` (or only words), ranked best first
over both corpora, with the places to mark in every hit. Used by ``GET /api/find`` (and ``/api/search``).

``mode="semantic"`` finds hadiths by meaning (``icm.semantic``) instead of by words.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional

from . import config, corpus, lex, project, query
from .text import norm, strip_tags, tokens

SCOPES = ("any", "matn", "sanad")
CORPORA = ("all", "v02", "kg")
RERANK = 500          # the best hits by bm25 that are read and put in order by how close together they have the words
ID_RE = re.compile(r"(?:v02:|/h/i/(?:angel/)?|^)(\d{3,12})$")


def direct(q: str) -> List[str]:
    """A hadith id (or the link to one on the site) is a search for that hadith."""
    m = ID_RE.search(q)
    if m and re.fullmatch(r"(v02:|kg:)?\d+|.*/h/i/.*", q):
        n = int(m.group(1))
        wids = [f"v02:{n}", f"v02:{config.NOOR_OFFSET + n}"] if n < config.NOOR_OFFSET else [f"v02:{n}"]
        return [f"kg:{n}"] if q.startswith("kg:") else wids
    return []


def _record(rec: dict, hl: Optional[query.Highlight], score=None, copies=None) -> dict:
    raw = rec["matn"]
    mk = query.marks(raw, hl) if hl is not None else []
    text, mk, cb, ca = query.snippet(raw, mk)
    return dict(wid=rec["wid"], title=rec["title"], author=rec["author"], tradition=rec["tradition"], vol=rec["vol"], page=rec["page"],
                matn=text, marks=mk, cut_before=cb, cut_after=ca, score=score, copies=copies or [], projects=project.projects_of(rec["wid"]))


def _fold(wids: List[str], recs: Dict[str, dict]):
    """Identical texts (the same book twice, or in both corpora) are one hit; the others are its copies."""
    seen: Dict[str, str] = {}
    copies: Dict[str, List[str]] = {}
    keep: List[str] = []
    for w in wids:
        r = recs.get(w)
        if r is None:
            continue
        h = hashlib.sha1(" ".join(tokens(r["matn"])).encode()).hexdigest()
        if h in seen:
            copies[seen[h]].append(w)
        else:
            seen[h] = w
            copies[w] = []
            keep.append(w)
    return keep, copies


def _extras(qq: query.Query, name: str, scope: str) -> List[str]:
    """More ways to ask for the hadiths that have the words of a plain query close together, which a ranking by bm25 over
    thousands of hits can bury: the words as typed, as a phrase; the roots of its words near each other. Only for a query
    of words (and phrases) with no field, alternative or exclusion in it."""
    its = qq.positive
    if len(its) != len(qq.items) or any(i.kind not in ("word", "phrase") or i.field or i.prefix for i in its):
        return []
    words = [w for i in its for w in i.words]
    if len(words) < 2 or len(words) > 12:
        return []
    cols = query._cols(None, scope)
    out = [f'{cols}:"{" ".join(words)}"']
    core = [lex.core_of(w) for w in words]
    rts = lex.roots(core)
    idx = 0 if name == "v02" else 1
    ks = [rts[c] for c in core if c in rts and len(c) >= lex.MIN_ROOT_WORD]
    ks = list(dict.fromkeys(k for k in ks if lex.root_df([k])[k][idx] > 0 and not lex.is_stop_root(k)))
    if len(ks) >= 2:
        out.append("root: NEAR(%s, %d)" % (" ".join('"%s"' % lex.root_tok(k) for k in ks[:6]), 2 * len(words) + 2))
    return out


def find(q: str, mode: str = "lexical", scope: str = "any", corpus_name: str = "all", limit: int = 20, offset: int = 0, fold: bool = True) -> dict:
    """``{hits, total, exact, parsed, warnings, capped, timed_out, mode}``. Raises ``query.QueryError`` for a query that cannot be read."""
    q = (q or "").strip()
    limit, offset = max(1, min(int(limit), 100)), max(0, min(int(offset), 1000))
    scope = scope if scope in SCOPES else "any"
    names = ["v02", "kg"] if corpus_name == "all" else [corpus_name] if corpus_name in ("v02", "kg") else ["v02", "kg"]
    wids = direct(q)
    if wids:
        recs = corpus.get_many(wids)
        return dict(mode="direct", hits=[_record(recs[w], None) for w in wids if w in recs][:limit], total=len([w for w in wids if w in recs]),
                    exact=True, parsed=None, warnings=[], capped=False, timed_out=False)
    if mode == "semantic":
        from . import semantic
        return semantic.find(q, scope, names, limit, offset)
    if not lex.available():
        return _legacy(q, limit)
    qq = query.parse(q)
    warns: List[str] = []
    hl = query.highlight(qq)
    per, total, exact, capped, timed_out = [], 0, True, False, False
    extra: List[str] = []
    for name in names:
        expr = query.to_fts(qq, name, scope, warns)
        if not expr:
            continue
        r = lex.search(name, expr, limit + offset, 0, rank=True, scores=True)
        n, ex = lex.count(name, expr)
        total += n
        exact &= ex
        capped |= r["capped"]
        timed_out |= r["timed_out"]
        sc = r.get("scores") or [None] * len(r["ids"])
        per.append([(f"{name}:{i}", s) for i, s in zip(r["ids"], sc)])
        if r["capped"]:                                       # more hits than ranked: also ask the narrower ways (``_extras``)
            for ex in _extras(qq, name, scope):
                extra += [f"{name}:{i}" for i in lex.search(name, ex, 300, rank="auto")["ids"]]
    merged: List = []
    if len(per) == 2 and all(s is not None for _, s in per[0] + per[1]):
        merged = sorted(per[0] + per[1], key=lambda x: x[1])            # bm25 of the two tables: good enough to interleave
    else:
        merged = [x for p in per for x in p]
    sc = dict(merged)
    # the best ``K`` by bm25 are read and put in order by how close together they have the words (bm25 does not see that)
    K = min(len(merged), max(limit + offset + 12, RERANK))
    recs = corpus.get_many([w for w, _ in merged[:K]])
    pool = [w for w, _ in merged[:K]]
    seen = set(pool)
    pool += [w for w in dict.fromkeys(extra) if w not in seen]
    seen = set(pool)
    if len(pool) > len(recs):
        recs.update(corpus.get_many([w for w in pool if w not in recs]))
    top = query.rerank(qq, recs, pool) + [w for w, _ in merged[K:] if w not in seen]
    page = top[offset:offset + limit + 12]
    if len(page) > sum(1 for w in page if w in recs):
        recs.update(corpus.get_many([w for w in page if w not in recs]))
    keep, copies = _fold(page, recs) if fold else ([w for w in page if w in recs], {w: [] for w in page})
    hits = [_record(recs[w], hl, None if sc.get(w) is None else round(-sc[w], 3), copies[w]) for w in keep[:limit]]
    return dict(mode="lexical", hits=hits, total=total, exact=exact, parsed=qq.as_dict(), warnings=warns, capped=capped, timed_out=timed_out)


def _legacy(q: str, limit: int) -> dict:
    """The word search the wizard had before ``lex.sqlite``: all the words, exactly, 300 + 100 hits."""
    expr = corpus.fts_query([q])
    wids = [f"v02:{i}" for i in corpus.fts_v02(expr, 300)] + [f"kg:{i}" for i in corpus.fts_kg(expr, 100)] if expr else []
    recs = corpus.get_many(wids[:400])
    qn = len(norm(q).split())
    rows = []
    for w in wids:
        rec = recs.get(w)
        if rec:
            rows.append((-min(qn / max(len(norm(rec["matn"]).split()), 1), 1.0), rec))
    rows.sort(key=lambda x: x[0])
    return dict(mode="legacy", hits=[_record(r, None) for _, r in rows[:limit]], total=len(rows), exact=False, parsed=None, warnings=[],
                capped=False, timed_out=False)
