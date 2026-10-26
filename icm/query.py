"""The search language of the wizard's hadith search (step 1 and step 2's «افزودن حدیث»), modelled on Jami' al-Ahadith
(Noor): every part is an AND, exclusion with «-», phrases, distance, prefixes, alternatives, roots, scopes.

    صلاه زکاه            all the words (each also as «والصلاة / بالصلاة / للصلاة …»: another conjunction, preposition, article)
    =الصلاه              exactly this form
    "اقیموا الصلاه"      this phrase, the words next to each other
    "صلاه زکاه"~5        these words within 5 words of each other, in any order
    صلا*                 words that begin so
    صلاه|زکاه            either
    -کذب  -"لا صلاه"    not (needs at least one positive part)
    ریشه:زکو  r:ز-ک-و    every word of this root (a word may be given instead of the root: ریشه:یزکی)
    ریشه:"قوم صلو"       a phrase of roots (``~N``: within N words)
    متن:  سند:           where to look (m: s:): the matn / the chain; the default is both, ``scope`` of the call
    کتاب:کافی            only the books whose title, author or code has these words (b:)

``parse(text)`` -> ``Query``; ``to_fts(query, "v02")`` -> the FTS5 expression for one corpus' table of ``lex.sqlite``;
``highlight(query)`` / ``marks(text, hl)`` -> the places to mark in a text.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import lex
from .text import norm, strip_tags, token_offsets, tokens

FIELDS = {"متن": "matn", "m": "matn", "matn": "matn", "سند": "sanad", "s": "sanad", "sanad": "sanad",
          "ریشه": "root", "ریشه‌ی": "root", "r": "root", "root": "root", "کتاب": "book", "b": "book", "book": "book"}
_ITEM = re.compile(r'(?P<neg>[-−–])?(?:(?P<field>[^\s:"\-][^\s:"]*):)?(?:"(?P<phrase>[^"]*)"(?:~(?P<near>\d+))?|(?P<word>[^\s"]+))')
MAX_ITEMS = 24
MAX_ALTS = 16


class QueryError(ValueError):
    """What is wrong with the text of a query, in words the user can read (Persian)."""


class Item:
    __slots__ = ("kind", "neg", "field", "words", "near", "exact", "prefix", "alts", "raw")

    def __init__(self, kind: str, neg: bool, field: Optional[str], words: List[str], near: Optional[int] = None,
                 exact: bool = False, prefix: bool = False, alts: Optional[List["Item"]] = None, raw: str = ""):
        self.kind, self.neg, self.field, self.words, self.near, self.exact, self.prefix, self.alts, self.raw = \
            kind, neg, field, words, near, exact, prefix, alts, raw

    def as_dict(self) -> dict:
        d = dict(kind=self.kind, neg=self.neg, field=self.field, words=self.words, raw=self.raw)
        if self.near:
            d["near"] = self.near
        if self.exact:
            d["exact"] = True
        if self.prefix:
            d["prefix"] = True
        if self.alts:
            d["alts"] = [a.as_dict() for a in self.alts]
        return d


class Query:
    def __init__(self, items: List[Item], text: str):
        self.items, self.text = items, text

    @property
    def positive(self) -> List[Item]:
        return [i for i in self.items if not i.neg]

    def as_dict(self) -> dict:
        return dict(text=self.text, items=[i.as_dict() for i in self.items])


def _words(s: str) -> List[str]:
    return norm(s).split()


def _word_item(w: str, neg: bool, field: Optional[str]) -> Item:
    """One bare term: ``=w`` exact, ``w*`` prefix, ``a|b`` alternatives, otherwise a word."""
    raw = w
    if "|" in w:
        alts = [_word_item(x, False, field) for x in w.split("|") if x.strip("=*")]
        if len(alts) > MAX_ALTS:
            raise QueryError("حداکثر %d گزینه با «|» ممکن است" % MAX_ALTS)
        if not alts:
            raise QueryError("بعد از «|» واژه‌ای نیامده است")
        return Item("alt", neg, field, [], alts=alts, raw=raw)
    exact = w.startswith("=")
    prefix = w.endswith("*")
    ws = _words(w.strip("=*"))
    if not ws:
        raise QueryError("واژه‌ی «%s» چیزی برای جستجو ندارد" % raw)
    if len(ws) > 1:                                      # «ال‌صلاه»-like input that norm splits: a phrase
        return Item("phrase", neg, field, ws, raw=raw)
    if prefix and len(ws[0]) < 2:
        raise QueryError("پیشوند «%s» باید دست‌کم دو حرف باشد" % raw)
    return Item("word", neg, field, ws, exact=exact, prefix=prefix, raw=raw)


def parse(text: str) -> Query:
    """Raises ``QueryError``."""
    text = re.sub(r"\s*\|\s*", "|", (text or "").strip())
    if not text:
        raise QueryError("متن جستجو خالی است")
    if text.count('"') % 2:
        raise QueryError("گیومه‌ی «\"» بسته نشده است")
    items: List[Item] = []
    pos = 0
    for m in _ITEM.finditer(text):
        if text[pos:m.start()].strip():
            raise QueryError("«%s» قابل خواندن نیست" % text[pos:m.start()].strip())
        pos = m.end()
        neg = bool(m.group("neg"))
        fld = FIELDS.get((m.group("field") or "").strip()) if m.group("field") else None
        if m.group("field") and fld is None:
            raise QueryError("«%s:» را نمی‌شناسم؛ می‌توان متن، سند، ریشه یا کتاب نوشت" % m.group("field"))
        if m.group("phrase") is not None:
            ws = _words(m.group("phrase"))
            if not ws:
                raise QueryError("عبارت میان گیومه خالی است")
            near = int(m.group("near")) if m.group("near") else None
            if near is not None and not (1 <= near <= 50):
                raise QueryError("فاصله باید میان ۱ و ۵۰ باشد")
            if near is not None and len(ws) < 2:
                raise QueryError("برای فاصله دست‌کم دو واژه لازم است")
            items.append(Item("near" if near else "phrase", neg, fld, ws, near=near, raw=m.group(0)))
        else:
            w = m.group("word")
            if fld == "book":
                items.append(Item("book", neg, "book", _words(w), raw=m.group(0)))
            elif fld == "root":
                items.append(Item("root", neg, "root", _words(w), raw=m.group(0)))
            else:
                items.append(_word_item(w, neg, fld))
    if text[pos:].strip():
        raise QueryError("«%s» قابل خواندن نیست" % text[pos:].strip())
    for it in items:
        if it.kind == "phrase" and it.field == "root":
            it.kind = "root"
        elif it.kind == "near" and it.field == "root":
            it.kind = "root"
        elif it.kind in ("phrase", "near") and it.field == "book":
            it.kind = "book"
    if not items:
        raise QueryError("چیزی برای جستجو نیست")
    if len(items) > MAX_ITEMS:
        raise QueryError("حداکثر %d بخش در یک جستجو ممکن است" % MAX_ITEMS)
    if not [i for i in items if not i.neg]:
        raise QueryError("دست‌کم یک بخش بدون «-» لازم است")
    return Query(items, text)


# ---------------------------------------------------------------------------
# FTS5
# ---------------------------------------------------------------------------
def _q(w: str) -> str:
    return '"%s"' % w.replace('"', "")


def _cols(field: Optional[str], scope: str) -> str:
    f = field if field in ("matn", "sanad") else {"matn": "matn", "sanad": "sanad"}.get(scope)
    return f if f else "{matn sanad}"


def resolve_root(w: str) -> Tuple[str, str]:
    """(root key, how): ``w`` as a root when the index has it, else the root of the word ``w``."""
    k = root_key(w)
    if k and lex.root_df([k])[k] != (0, 0):
        return k, "root"
    nw = norm(w)
    r = lex.roots([nw]).get(nw)
    if r:
        return r, "word"
    raise QueryError("برای «%s» ریشه‌ای پیدا نشد" % w)


from .lex import root_key  # noqa: E402  (below the class, used by resolve_root)


def _item_expr(it: Item, corpus_name: str, scope: str, warns: List[str]) -> str:
    cols = _cols(it.field, scope)
    if it.kind == "alt":
        parts = [_item_expr(a, corpus_name, scope, warns) for a in it.alts]
        parts = [p for p in parts if p]
        return "(" + " OR ".join(parts) + ")" if parts else ""
    if it.kind == "word":
        w = it.words[0]
        if it.prefix:
            return f'{cols}:{_q(w)}*'
        if it.exact or cols == "sanad":
            return f"{cols}:{_q(w)}"
        forms = lex.clitic_forms(w, corpus_name)
        return f"{cols}:(" + " OR ".join(_q(f) for f in forms) + ")" if len(forms) > 1 else f"{cols}:{_q(w)}"
    if it.kind == "phrase":
        return f'{cols}:"{" ".join(it.words)}"'
    if it.kind == "near":
        return f"{cols}: NEAR({' '.join(_q(w) for w in it.words)}, {it.near})"
    if it.kind == "root":
        ks = []
        for w in it.words:
            k, how = resolve_root(w)
            ks.append(k)
            if how == "word" and corpus_name == "v02" and f"ریشه‌ی «{w}»: {k}" not in warns:
                warns.append(f"ریشه‌ی «{w}»: {k}")
        if len(ks) == 1:
            forms = lex.forms_of(ks[0], 40, corpus_name)
            alt = f' OR matn:({" OR ".join(_q(f) for f in forms)})' if forms else ""
            return f'(root:{_q(lex.root_tok(ks[0]))}{alt})'
        toks = [_q(lex.root_tok(k)) for k in ks]
        return f"root: NEAR({' '.join(toks)}, {it.near})" if it.near else f'root:"{" ".join(lex.root_tok(k) for k in ks)}"'
    if it.kind == "book":
        toks = lex.books_matching(" ".join(it.words)).get(corpus_name) or []
        if not toks:
            if corpus_name == "v02" and f"کتابی با «{' '.join(it.words)}» در v02 نیست" not in warns:
                warns.append(f"کتابی با «{' '.join(it.words)}» پیدا نشد")
            return 'book:"b0"'
        return "book:(" + " OR ".join(_q(t) for t in toks[:200]) + ")"
    return ""


def to_fts(q: Query, corpus_name: str, scope: str = "any", warns: Optional[List[str]] = None) -> str:
    """The FTS5 expression of ``q`` for the table of ``corpus_name`` (a part that must not match: ``NOT``). ``scope``:
    any | matn | sanad, where a part without ``متن:`` / ``سند:`` looks."""
    warns = warns if warns is not None else []
    pos = [e for e in (_item_expr(i, corpus_name, scope, warns) for i in q.items if not i.neg) if e]
    neg = [e for e in (_item_expr(i, corpus_name, scope, warns) for i in q.items if i.neg) if e]
    if not pos:
        return ""
    e = " AND ".join(pos)
    if neg:
        e = f"({e}) NOT ({' OR '.join(neg)})"
    return e


# ---------------------------------------------------------------------------
# marks
# ---------------------------------------------------------------------------
class Highlight:
    def __init__(self):
        self.words: Set[str] = set()
        self.prefixes: List[str] = []
        self.roots: Set[str] = set()


def highlight(q: Query) -> Highlight:
    """What to mark in a hit: the words of the positive parts (with their clitic forms), prefixes, the words of the roots."""
    h = Highlight()

    def walk(it: Item):
        if it.neg:
            return
        if it.kind == "alt":
            for a in it.alts:
                walk(a)
        elif it.kind == "word":
            if it.prefix:
                h.prefixes.append(it.words[0])
            elif it.exact or it.field == "sanad":
                h.words.add(it.words[0])
            else:
                h.words.update(lex.clitic_forms(it.words[0]))
        elif it.kind in ("phrase", "near"):
            h.words.update(it.words)
        elif it.kind == "root":
            for w in it.words:
                try:
                    h.roots.add(resolve_root(w)[0])
                except QueryError:
                    pass
    for it in q.items:
        walk(it)
    return h


def marks(raw: str, h: Highlight) -> List[List[int]]:
    """``[start, end, 0]`` ranges of ``strip_tags(raw)`` for the words ``h`` marks (see ``text.marks``)."""
    from .text import marks as _marks
    toks = tokens(raw)
    lab = [-1] * len(toks)
    rts = lex.roots(toks) if h.roots else {}
    for i, t in enumerate(toks):
        if t in h.words or any(t.startswith(p) for p in h.prefixes) or (rts.get(t) in h.roots if h.roots else False):
            lab[i] = 0
    return _marks(strip_tags(raw), lab)


def snippet(raw: str, mk: List[List[int]], width: int = 420) -> Tuple[str, List[List[int]], bool, bool]:
    """(text, marks, cut_before, cut_after): ``strip_tags(raw)`` cut to ``width`` characters around the first mark."""
    t = strip_tags(raw)
    if len(t) <= width:
        return t, mk, False, False
    first = mk[0][0] if mk else 0
    a = max(0, min(first - width // 4, len(t) - width))
    a = t.rfind(" ", 0, a) + 1 if a > 0 and t.rfind(" ", 0, a) >= 0 else a
    b = min(len(t), a + width)
    b = t.find(" ", b) if t.find(" ", b) >= 0 and t.find(" ", b) < b + 20 else b
    out = [[max(x, a) - a, min(y, b) - a, c] for x, y, c in mk if y > a and x < b]
    return t[a:b], out, a > 0, b < len(t)


# ---------------------------------------------------------------------------
# proximity re-rank
# ---------------------------------------------------------------------------
def _item_matches(it: Item, toks: Sequence[str], rts: Dict[str, str], cache: dict) -> List[Tuple[int, int]]:
    """[(first, last)] token positions where the matn matches ``it`` (a root, a word with its forms, a phrase …)."""
    if it.kind == "alt":
        out: List[Tuple[int, int]] = []
        for a in it.alts:
            out += _item_matches(a, toks, rts, cache)
        return sorted(out)
    if it.kind == "word":
        w = it.words[0]
        if it.prefix:
            return [(i, i) for i, t in enumerate(toks) if t.startswith(w)]
        if it.exact:
            return [(i, i) for i, t in enumerate(toks) if t == w]
        forms = cache.setdefault(w, set(lex.clitic_forms(w)))
        return [(i, i) for i, t in enumerate(toks) if t in forms]
    if it.kind == "phrase":
        n = len(it.words)
        return [(i, i + n - 1) for i in range(len(toks) - n + 1) if list(toks[i:i + n]) == it.words]
    if it.kind == "near":
        ws = set(it.words)
        return [(i, i) for i, t in enumerate(toks) if t in ws]
    if it.kind == "root":
        ks = cache.setdefault(tuple(it.words), [resolve_root(w)[0] for w in it.words])
        if len(ks) == 1:
            return [(i, i) for i, t in enumerate(toks) if rts.get(t) == ks[0]]
        n = len(ks)
        return [(i, i + n - 1) for i in range(len(toks) - n + 1) if all(rts.get(toks[i + j]) == ks[j] for j in range(n))]
    return []


def proximity(raw: str, q: Query, cache: Optional[dict] = None) -> Optional[Tuple[float, int]]:
    """(tightness, words): how close together the matn of ``raw`` has the positive parts of ``q`` looked for in the matn: the
    words the parts take over the length of the smallest stretch that has them all (1.0 = next to each other). None when no
    part looks in the matn alone or one is missing (a hit through the chain, a book…)."""
    its = [i for i in q.positive if i.kind != "book" and i.field != "sanad"]
    if len(its) < 2:
        return None
    toks = tokens(raw)
    rts = lex.roots(toks) if any(i.kind == "root" for i in its) else {}
    cache = {} if cache is None else cache
    lists = [_item_matches(i, toks, rts, cache) for i in its]
    if any(not l for l in lists):
        return None
    ev = sorted((s, e, k) for k, l in enumerate(lists) for s, e in l)
    need = len(lists)
    have: Dict[int, int] = {}
    best = None
    lo = 0
    for hi in range(len(ev)):
        have[ev[hi][2]] = have.get(ev[hi][2], 0) + 1
        while len(have) == need:
            span = max(e for _, e, _ in ev[lo:hi + 1]) - ev[lo][0] + 1
            if best is None or span < best:
                best = span
            k = ev[lo][2]
            have[k] -= 1
            if not have[k]:
                del have[k]
            lo += 1
    words = sum(max(e - s + 1 for s, e in l) for l in lists) if best else 0
    return (min(1.0, words / best), words) if best else None


def rerank(q: Query, recs: Dict[str, dict], order: Sequence[str]) -> List[str]:
    """``order`` (best first by bm25) with the hits that have the parts closest together moved up: 3 × tightness + the
    bm25 position (1 for the best, 0 for the last). Hits with nothing to measure keep their place among the others."""
    cache: dict = {}
    n = max(len(order) - 1, 1)
    sc = {}
    for rank, w in enumerate(order):
        r = recs.get(w)
        p = proximity(r["matn"], q, cache) if r else None
        sc[w] = (3 * p[0] if p else 1.5) + (1 - rank / n)
    return sorted(order, key=lambda w: -sc[w])
