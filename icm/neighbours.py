"""Who stands next to whom: the databases' teacher/student pairs, used for the places the written name alone cannot settle.

Two rules read them (both measured leave-book-out on Noor's linked isnads and the KG's tagged ones, see ``README`` and
``evaluate.neighbours``):

``father_of_prev``   «عن أبيه» after a narrator we know: the person with the father's first name (the name after «بن» in the
                     narrator's name) who is most often the narrator's teacher in the databases' chains. With three times the
                     runner-up's pairs it was right in 99.6% of Noor's «ابيه» slots (86% of them); with any lead of two
                     pairs, 94% (96%): then only a suggestion.
``adj``              an ambiguous name between two known neighbours: each candidate scores (pairs with the neighbours + 1) × how
                     often the name is written for him; a candidate twenty times ahead is right in 82% (Noor) and 95% (KG)
                     of the places it speaks for: below the middle level's 98%, so it is only a *suggestion*.

    around(db, pid)                  -> (teachers, students)  {person: hadiths}
    father_candidates(db, pid, k=)   -> [(person, pairs)]     persons with the father's first name next to ``pid``, most pairs first
    rank(db, key, cands, prev, next) -> [(person, score, pairs, prior)]
"""
from __future__ import annotations

import sqlite3
import threading
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from . import config, corpus, names

REL_RATIO = 3.0                                    # father_of_prev: pairs ahead of the runner-up (the middle level)
REL_PREC, REL_SUGG_PREC = 0.998, 0.976             # measured (Noor, leave-book-out: evaluate.neighbours)
ADJ_RATIO = 20.0
ADJ_PREC = {"noor": 0.825, "kg": 0.954}
ADJ_ONE_PREC = {"noor": 0.989, "kg": 0.958}
MAJOR_PREC = {"noor": 0.957, "kg": 0.959}           # name_major alone (a suggestion)
MAJOR_ADJ_PREC = {"noor": 0.980, "kg": 0.991}      # name_major (99%+ of ≥20 hadiths, every group of books) with ≥3 hadiths next to a neighbour        # both neighbours known and one candidate only ever next to them (≥3 hadiths)
_local = threading.local()


def _nconn() -> sqlite3.Connection:
    c = getattr(_local, "n", None)
    if c is None or getattr(_local, "np", None) != config.NARRATORS_DB:
        c = sqlite3.connect(f"file:{config.NARRATORS_DB}?mode=ro&immutable=1", uri=True, check_same_thread=False)
        _local.n, _local.np = c, config.NARRATORS_DB
    return c


def _num(pid) -> Optional[int]:
    try:
        return int(str(pid).split(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _ids(db: str, side: Dict[int, int]) -> Dict[str, int]:
    return {f"{db}:{k}": v for k, v in side.items()}


@lru_cache(maxsize=20000)
def _around(db: str, rid: int) -> Tuple[tuple, tuple]:
    try:
        if db == "noor":
            c = _nconn()
            t = c.execute("select teacher, n_hadith from adjacency where student=?", (rid,)).fetchall()
            s = c.execute("select student, n_hadith from adjacency where teacher=?", (rid,)).fetchall()
        else:
            c = corpus.conn("kg")
            t = c.execute("select teacher, n from kg_adjacency where student=?", (rid,)).fetchall()
            s = c.execute("select student, n from kg_adjacency where teacher=?", (rid,)).fetchall()
    except sqlite3.Error:
        return (), ()
    return tuple((a, n or 0) for a, n in t), tuple((a, n or 0) for a, n in s)


def around(pid: str):
    """(teachers, students) of a person: {id: hadiths they are next to him in}. ({}, {}) for an id of neither database."""
    db, rid = (pid.split(":", 1)[0] if ":" in pid else ""), _num(pid)
    if db not in ("noor", "kg") or rid is None:
        return {}, {}
    t, s = _around(db, rid)
    return {f"{db}:{a}": n for a, n in t}, {f"{db}:{a}": n for a, n in s}


@lru_cache(maxsize=50000)
def _tokens(db: str, rid: int) -> tuple:
    try:
        if db == "noor":
            r = _nconn().execute("select name from ravi where ravi_id=?", (rid,)).fetchone()
        else:
            r = corpus.conn("kg").execute("select name from rawis where id=?", (rid,)).fetchone()
    except sqlite3.Error:
        return ()
    return tuple(names.written_key(r[0]).split()) if r and r[0] else ()


def tokens(pid: str) -> tuple:
    db, rid = (pid.split(":", 1)[0] if ":" in pid else ""), _num(pid)
    return _tokens(db, rid) if db in ("noor", "kg") and rid is not None else ()


def father_token(pid: str) -> Optional[str]:
    """The first name of the father named in the person's name («محمد بن يعقوب بن …» -> «يعقوب»)."""
    t = tokens(pid)
    for j in range(1, len(t) - 1):
        if t[j] in ("بن", "ابن"):
            return t[j + 1]
    return None


def father_candidates(pid: str, limit: int = 400) -> List[Tuple[str, int]]:
    """[(person, pairs)] persons whose own first name is the father's first name and who are the teachers of ``pid`` in the databases'
    chains (a son narrates from his father, not the other way: the narrator's own son has the same names), most pairs first."""
    f = father_token(pid)
    if not f:
        return []
    teachers = dict(around(pid)[0])
    teachers.pop(pid, None)
    out = []
    for k, n in sorted(teachers.items(), key=lambda x: -x[1])[:limit]:
        tk = tokens(k)
        if tk and tk[0] == f:
            out.append((k, n))
    return out


def father_rule(pid: str) -> Tuple[Optional[dict], Optional[dict]]:
    """(link, hint) for «ابيه» after ``pid``: ``link`` = the middle level (a clear margin), ``hint`` = the weaker one (any lead of at
    least 2 pairs). Each ``{id, rule, n, tot, prec}`` or None."""
    cs = father_candidates(pid)
    if not cs or cs[0][1] < 2:
        return None, None
    (c1, n1), n2 = cs[0], (cs[1][1] if len(cs) > 1 else 0)
    tot = sum(n for _, n in cs)
    got = dict(id=c1, rule="father_of_prev", n=n1, tot=tot)
    if n2 == 0 or n1 >= REL_RATIO * n2:
        return dict(got, prec=REL_PREC), None
    if n1 > n2:
        return None, dict(got, prec=REL_SUGG_PREC)
    return None, None


def pairs(c: str, prev: Optional[str], nxt: Optional[str]) -> int:
    """Hadiths in which ``c`` is next to the known person before the place (his teacher) or after it (his student)."""
    db = c.split(":", 1)[0]
    t = around(prev)[0] if prev and prev.startswith(db + ":") else {}
    s = around(nxt)[1] if nxt and nxt.startswith(db + ":") else {}
    return t.get(c, 0) + s.get(c, 0)


def rank(db: str, k: str, cands, prev: Optional[str], nxt: Optional[str]) -> List[Tuple[str, float, int, float]]:
    """[(person, score, pairs, prior)] best first, over ``cands`` (ids of ``db``) and the persons the name ``k`` is written for in the
    names file; ``prev`` / ``nxt`` the known persons before / after the place. Needs at least one neighbour."""
    if not (prev or nxt):
        return []
    written = dict(names.persons_written(db, k, 12))
    pool = {f"{db}:{p}": n for p, n in written.items()}
    for c in cands:
        if c.startswith(db + ":"):
            pool.setdefault(c, 0)
    if len(pool) < 2:
        return []
    tot = sum(pool.values()) + 0.5 * len(pool)
    teachers = around(prev)[0] if prev and prev.startswith(db + ":") else {}
    students = around(nxt)[1] if nxt and nxt.startswith(db + ":") else {}
    out = []
    for c, n in pool.items():
        pairs = teachers.get(c, 0) + students.get(c, 0)
        prior = (n + 0.5) / tot
        out.append((c, (pairs + 1) * prior, pairs, prior))
    out.sort(key=lambda x: -x[1])
    return out


def hint(db: str, k: str, cands, prev: Optional[str], nxt: Optional[str]) -> Optional[dict]:
    """The suggestion ``adj`` for a place: ``{id, rule, n, tot, prec}`` or None. ``n`` = pairs with the neighbours of the person."""
    r = rank(db, k, cands, prev, nxt)
    if len(r) < 2:
        return None
    (c1, v1, p1, _), (c2, v2, p2, _) = r[0], r[1]
    if prev and nxt and p1 >= 3 and all(x[2] == 0 for x in r[1:]):
        return dict(id=c1, rule="adj_one", n=p1, tot=p1, prec=ADJ_ONE_PREC[db])
    if p1 >= 2 and v1 >= ADJ_RATIO * v2:
        return dict(id=c1, rule="adj", n=p1, tot=sum(x[2] for x in r), prec=ADJ_PREC[db])
    return None
