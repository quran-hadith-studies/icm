"""Who is this written name? What the chains that already carry narrator ids say about a name in a chain without them.

``external/icm/names.sqlite`` (``python -m icm build names``) counts, over Noor's linked isnads (``narrators.sqlite`` slots, ids
``noor:``) and the Hadith Knowledge Graph's tagged isnads (``hadith_rawis``, ids ``kg:``), which person a *written* name stood for,
in which company:

    ctx3   the name with the names before and after it         (at least 2 hadiths, all the same person)
    prev   the name with the name before it                    (at least 3)
    next   the name with the name after it                     (at least 3)
    name   the name alone                                      (at least 5, always the same person)
    name_major  the name alone, one person has 95% of at least 20 hadiths and also dominates in every group of books («الزهري»,
                «يزيد بن هارون»; not «الشيخ», who is Mufid or Tusi by the author)
    pprev  the name after a person the chain already knows     (at least 3)
    pnext  the name before a person the chain already knows    (at least 3)

A context seen with several people is *ambiguous*: it blocks the weaker ones, unless one person has 95% of at least 20 hadiths
(``*_major``). Every rule was measured leave-book-out (``eval_json`` of the file); a rule that is not at least 98% right, or a
support below which it is not, is switched off in the file itself (``cfg``).

The link it makes is the *middle level* of certainty: a person used automatically (``identity``: ``e["auto"]``) that the user
approves in step «راویان». Names are the honorific-less ``narrators.common.norm_name`` forms (``written_key``); the first
narrator has the name before it ``^`` and the last the name after it ``$``; the collector and the Prophet are not in the chain.

    available() / key() / meta() / cfg(db)
    written_key(name)
    decide(get, k, p, n, prev_pid, next_pid, cfg) -> (found, seen)
    lookup(db, k, p, n, prev_pid, next_pid)       -> (found, seen)
    kg_written(hid)                               -> {rawi_id: [written key, ...]} of a KG hadith's inline narrator mentions
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from typing import Callable, Dict, Optional, Tuple

from . import config, sanad_parse
from .text import norm

NEED = dict(ctx3=2, prev=3, next=3, name=5, pprev=3, pnext=3)
MAJOR = (0.95, 20)                  # share, hadiths: an ambiguous context one person dominates
GROUP_MIN, GROUP_SHARE = 3, 0.9     # name_major: in every group of books with the name this often, the person has this share
MIN_PREC = 0.98
TIERS = ("ctx3", "prev", "next", "name", "pprev", "pnext")
_local = threading.local()


def written_key(name: str) -> str:
    """A name as written in an isnad, without honorifics, the way Noor's slot titles and KG mentions are keyed."""
    from narrators.common import norm_name
    toks, _ = sanad_parse._strip_honorific(norm(name or "").split())
    return norm_name(" ".join(toks))


def hkey(db: str, tier: str, *parts) -> int:
    s = "\x1f".join((db, tier) + tuple(str(x) for x in parts))
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "big") >> 1


def _conn() -> Optional[sqlite3.Connection]:
    c = getattr(_local, "c", False)
    if c is False:
        c = None
        if os.path.exists(config.NAMES_DB):
            c = sqlite3.connect(f"file:{config.NAMES_DB}?mode=ro&immutable=1", uri=True, check_same_thread=False)
        _local.c = c
    return c


def available() -> bool:
    return _conn() is not None


_META: Dict[str, Tuple[tuple, dict]] = {}


def meta() -> dict:
    """The file's meta rows (``built``, counts, ``eval`` = measured precision per db and rule); {} without the file."""
    p = config.NAMES_DB
    try:
        st = os.stat(p)
    except OSError:
        return {}
    sig = (st.st_mtime_ns, st.st_size)
    got = _META.get(p)
    if got is None or got[0] != sig:
        c = _conn()
        out = {}
        if c is not None:
            for k, v in c.execute("select k, v from meta"):
                try:
                    out[k] = json.loads(v)
                except ValueError:
                    out[k] = v
        _META[p] = got = (sig, out)
    return got[1]


def key() -> str:
    """Goes into the identify stage's key: the stage is rebuilt when the file, or the wiki's published decisions, are."""
    m = meta()
    w = _wiki_sig()
    return (str(m.get("built") or "") if m else "") + (f"|{w[0]}:{w[1]}" if w else "")


def _wiki_sig() -> Optional[tuple]:
    try:
        st = os.stat(config.NAMES_WIKI_DB)
    except OSError:
        return None
    return (st.st_mtime_ns // 1_000_000_000, st.st_size)


_WIKI: Dict[str, tuple] = {}


def wiki() -> dict:
    """The narrator wiki's published decisions on written names (``names_wiki.sqlite``): ``approved`` {name: person id} (a person the
    wiki says that name is, anywhere: as sure as the databases), ``rejected`` {(name, person id)} (an automatic link the wiki refuses)."""
    sig = _wiki_sig()
    if sig is None:
        return dict(approved={}, rejected=set())
    got = _WIKI.get(config.NAMES_WIKI_DB)
    if got is None or got[0] != sig:
        c = sqlite3.connect(f"file:{config.NAMES_WIKI_DB}?mode=ro&immutable=1", uri=True)
        try:
            rows = c.execute("select k, id, decision from decided").fetchall()
        finally:
            c.close()
        ap: Dict[str, set] = {}
        for k, i, d in rows:
            if d == "approve":
                ap.setdefault(k, set()).add(i)
        got = _WIKI[config.NAMES_WIKI_DB] = (sig, dict(approved={k: next(iter(v)) for k, v in ap.items() if len(v) == 1},
                                                      rejected={(k, i) for k, i, d in rows if d == "reject"}))
    return got[1]


def cfg(db: str) -> dict:
    """{need: {tier: support}, off: {tier...}} of a database as measured (the defaults without the file)."""
    e = (meta().get("eval") or {}).get(db) or {}
    return dict(need=dict(NEED, **(e.get("need") or {})), off=set(e.get("off") or []))


def decide(get: Callable, k: str, p: str, n: str, prev_pid=None, next_pid=None, cfg: Optional[dict] = None, major: bool = False):
    """``get(tier, parts) -> (pid, support, total, n_people) | None``. Returns ``(found, seen)``: ``found`` =
    ``{id, rule, n, tot}`` or None, ``seen`` = the database knows this name in some company (so another database is not asked).
    ``name_major`` (below the 98% the middle level needs: 84% Noor, 90% KG) is only asked for when ``major``; ``identity`` reads it as a
    suggestion, and as a link when a neighbour vouches for it (``major_of``)."""
    from .build.hadithai import is_relative
    cfg = cfg or dict(need=NEED, off=set())
    need, off = cfg["need"], cfg["off"]
    if p == "^" and is_relative(k):                      # whose father depends on the work's author
        return None, True
    seen = False
    for tier, parts in (("ctx3", (k, p, n)), ("prev", (k, p)), ("next", (k, n))):
        g = get(tier, parts)
        if not g:
            continue
        pid, sup, tot, npl = g
        seen = True
        if npl == 1:
            if tier not in off and sup >= need[tier]:
                return dict(id=pid, rule=tier, n=sup, tot=tot), True
            continue
        if sup / tot >= MAJOR[0] and tot >= MAJOR[1] and tier + "_major" not in off:
            return dict(id=pid, rule=tier + "_major", n=sup, tot=tot), True
        return None, True                                # an ambiguous context: a weaker one cannot overrule it
    g = get("name", (k,))
    if g:
        seen = True
        pid, sup, tot, npl = g
        if npl == 1 and "name" not in off and sup >= need["name"]:
            return dict(id=pid, rule="name", n=sup, tot=tot), True
    for tier, nb in (("pprev", prev_pid), ("pnext", next_pid)):
        if nb is None or tier in off:
            continue
        g = get(tier, (k, nb))
        if g and g[3] == 1 and g[1] >= need[tier]:
            return dict(id=g[0], rule=tier, n=g[1], tot=g[2]), True
    if major:
        g = get("namem", (k,))
        if g:
            return dict(id=g[0], rule="name_major", n=g[1], tot=g[2]), True
    return None, seen


def _getter(db: str) -> Optional[Callable]:
    c = _conn()
    if c is None:
        return None

    def get(tier, parts):
        r = c.execute("select pid, sup, tot, np from ctx where h=?", (hkey(db, tier, *parts),)).fetchone()
        return tuple(r) if r else None
    return get


def lookup(db: str, k: str, p: str, n: str, prev_pid=None, next_pid=None):
    """``decide`` over the file for ``db`` ('noor' | 'kg'); (None, False) without the file."""
    g = _getter(db)
    if g is None:
        return None, False
    return decide(g, k, p, n, prev_pid, next_pid, cfg(db))


def kg_written(hid: int) -> Dict[int, list]:
    """The written forms (keys) of the narrators a KG hadith names inline, by narrator id, in text order."""
    c = _conn()
    if c is None:
        return {}
    out: Dict[int, list] = {}
    try:
        for rid, k in c.execute("select m.rawi_id, n.k from kgm m join name n on n.id=m.nid where m.hid=? order by m.seq", (int(hid),)):
            out.setdefault(rid, []).append(k)
    except sqlite3.OperationalError:
        return {}
    return out


def kg_chain(hid: int) -> list:
    """[(written key, narrator id or 0)] of a KG hadith's inline mentions in text order."""
    c = _conn()
    if c is None:
        return []
    try:
        return [(k, rid or 0) for rid, k in c.execute("select m.rawi_id, n.k from kgm m join name n on n.id=m.nid where m.hid=? order by m.seq", (int(hid),))]
    except sqlite3.OperationalError:
        return []


_VOCAB: Dict[str, Tuple[tuple, dict]] = {}


def vocab() -> dict:
    """{token: number of written names it is in} over the names file: what words a narrator's name is made of."""
    p = config.NAMES_DB
    try:
        st = os.stat(p)
    except OSError:
        return {}
    sig = (st.st_mtime_ns, st.st_size)
    got = _VOCAB.get(p)
    if got is None or got[0] != sig:
        c = _conn()
        out: Dict[str, int] = {}
        if c is not None:
            for (k,) in c.execute("select k from name"):
                for t in k.split():
                    out[t] = out.get(t, 0) + 1
        _VOCAB[p] = got = (sig, out)
    return got[1]


def fragment(name: str) -> bool:
    """A reading that has no place among narrators' names: two or more of its words are in no narrator's written name (the
    sentence of a commentary, matn glued into the isnad). Measured on the open places of Rezwan samples: 0 of the 130 places whose name
    matched a narrator well and 1 of 768 weak ones, 11% of those with no candidate at all."""
    v = vocab()
    if not v:
        return False
    toks = written_key(name).split()
    return sum(1 for t in toks if v.get(t, 0) < 3) >= 2


def persons_written(db: str, k: str, limit: int = 6) -> list:
    """[(person id, hadiths)] of the persons whose isnad name is written exactly ``k`` in the database ``db``, most often first
    (at least 2 hadiths each): who the name has been, whatever its database name looks like."""
    c = _conn()
    if c is None:
        return []
    try:
        return [(r[0], r[1]) for r in c.execute("select f.pid, f.n from form f join name nn on nn.id=f.nid where nn.k=? and f.db=? "
                                                "order by f.n desc limit ?", (k, db, limit))]
    except sqlite3.OperationalError:
        return []


def major_of(db: str, k: str) -> Optional[dict]:
    """The person that dominates the name ``k`` in every group of books of the database (``name_major``), ``{id, rule, n, tot, share}``."""
    g = _getter(db)
    r = g("namem", (k,)) if g else None
    if not r:
        return None
    pid, sup, tot, _ = r
    from .neighbours import MAJOR_PREC
    return dict(id=f"{db}:{pid}", rule="name_major", n=sup, tot=tot, share=sup / tot, prec=MAJOR_PREC.get(db))
