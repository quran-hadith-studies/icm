"""Step 4: who are the narrators? Candidates for every name in every chain.

For each selected witness the chains are assembled (collector first, as recorded):

  noor_slots  Noor's own parsed isnad (``narrators.sqlite`` slot rows), with the words
              between slots re-parsed so that narrators Noor folded into a connective
              («عن أبيه ع عن علي بن الحسين ع») reappear as elements
  kg          the Hadith Knowledge Graph's chains (narrator ids)
  hadithai    hadith.ai v2's narrator tags of the isnad (``icm.hadithai``; Noor books without parsed
              slots, sunnah.com), each name classified by the isnad grammar; the author as collector.
              A name whose neighbours always go with one Noor narrator in Noor's linked isnads gets him
              as a candidate (``hai``, ``hadithai.sqlite`` mention suggestions; a suggestion, never exact)
  text        everything else: ``sanad_parse.parse`` of the recorded isnad text; the work's
              author is added as the collector

Every element gets candidate persons (``noor:<RaviId>`` or ``kg:<rawi id>``) with a score
from name agreement, chain context and popularity. Elements whose id is given by the
source are *exact*. The others are grouped into **name cards** (one per distinct name) for
the user to decide; nothing is guessed silently.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from narrators.common import STOP, bare, name_tokens
from . import authors, config, corpus, hadithai, names, narrator_versions, neighbours, sanad_parse
from .text import norm, strip_tags, token_offsets, tokens

_IMAMS = json.load(open(config.HERE + "/narrators/data/imam_reigns.json", encoding="utf-8"))["imams"]
INFALLIBLE_IDS = {v["ravi_id"] for k, v in _IMAMS.items() if v.get("ravi_id") and k not in ("unspecified", "lam_yarwi")}
PROPHET_ID = _IMAMS["prophet"]["ravi_id"]
# father of each Imam (for «عن أبيه»)
FATHER = {"hasan": "ali", "husayn": "ali", "sajjad": "husayn", "baqir": "sajjad", "sadiq": "baqir", "kazim": "sadiq",
          "rida": "kazim", "jawad": "rida", "hadi": "jawad", "askari": "hadi", "mahdi": "askari", "fatima": "prophet"}
_ID2KEY = {v["ravi_id"]: k for k, v in _IMAMS.items() if v.get("ravi_id")}

_noor = None
_kg_tok = None


def _nconn() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{config.NARRATORS_DB}?mode=ro&immutable=1", uri=True, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def noor_index() -> dict:
    global _noor
    if _noor is None:
        c = _nconn()
        ravis, post, titles = {}, defaultdict(list), defaultdict(dict)
        for r in c.execute("select ravi_id, name, kunya, laqab_json, mazhab, grade, n_slots from ravi"):
            names = [r["name"]]
            if r["laqab_json"]:
                try:
                    names += list(json.loads(r["laqab_json"]))
                except Exception:
                    pass
            toks = name_tokens(r["name"])
            alt = [name_tokens(x) for x in names[1:] if isinstance(x, str)]
            ravis[r["ravi_id"]] = dict(id=r["ravi_id"], name=r["name"], toks=toks, set=set(toks), alts=alt,
                                       n=r["n_slots"] or 0, mazhab=r["mazhab"], grade=r["grade"])
            for t in set(toks):
                post[t].append(r["ravi_id"])
        # a title under which a narrator's slot often appeared is another form of his name; rare titles are
        # mostly Noor's own linking slips (one «علي بن إبراهيم» slot on Muhammad b. Yahya), and relative or
        # anonymous titles («أبيه», «عدة من أصحابنا») are not names
        for rid, title, n in c.execute("select ravi_id, title, count(*) from slot where ravi_id is not null and title is not null "
                                       "group by ravi_id, title"):
            r = ravis.get(rid)
            if r is None or n < 2 or relative_word(title) or sanad_parse.is_anon(title):
                continue
            titles[title_key(title)][rid] = titles[title_key(title)].get(rid, 0) + n
            if n < 0.01 * r["n"]:
                continue
            toks = name_tokens(title)
            if toks and toks != r["toks"] and len(r["alts"]) < 30:
                r["alts"].append(toks)
                for t in set(toks):
                    if rid not in post[t]:
                        post[t].append(rid)
        _noor = dict(ravis=ravis, post=post, titles=dict(titles))
        c.close()
    return _noor


def title_key(name: str) -> str:
    """A name exactly as written, without honorifics: the key of Noor's title index («ابن عباس» keeps «ابن»)."""
    return " ".join(sanad_parse._strip_honorific(norm(name or "").split())[0])


def noor_title_hits(name: str) -> List[Tuple[int, int, float]]:
    """[(ravi_id, n, share)] of the narrators Noor's linked isnads write exactly this name for, most often first."""
    got = noor_index().get("titles", {}).get(title_key(name), {})
    tot = sum(got.values())
    return [(rid, n, n / tot) for rid, n in sorted(got.items(), key=lambda x: -x[1])]


# ---------------------------------------------------------------------------
# relatives: «عن أبيه», «حدثني أبي», «عن جده» ... name a different person in every chain
# ---------------------------------------------------------------------------
RELATION = {}
for _r, _ws in dict(father=("ابیه", "ابیها", "ابیهما", "ابی"), grandfather=("جده", "جدها", "جدی"), brother=("اخیه", "اخیها", "اخی"),
                    uncle=("عمه", "عمی"), maternal_uncle=("خاله", "خالی"), mother=("امه", "امی"), son=("ابنه", "ابنها")).items():
    RELATION.update({w: _r for w in _ws})


def relative_word(name: str) -> Optional[str]:
    """The relative word a name consists of («أبيه عليه السلام» -> «ابیه»), else None."""
    toks, _ = sanad_parse._strip_honorific(norm(name or "").split())
    s = " ".join(toks)
    return s if s in sanad_parse.RELATIVES and s != "عنه" else None


_noor_rel = None


def noor_relatives() -> Dict[Tuple[int, str], Dict[int, int]]:
    """(narrator, relation) -> {person: n}: whom Noor's linked chains resolve «his father» etc. of a narrator to.

    Learned from slots whose title starts with a relative word and whose student slot is linked; a
    grandfather right after «عن أبيه» is counted as the father of that father."""
    global _noor_rel
    if _noor_rel is None:
        c = _nconn()
        out: Dict[Tuple[int, str], Dict[int, int]] = defaultdict(lambda: defaultdict(int))
        q = ("select s2.title, s2.ravi_id, s1.ravi_id, s1.title from slot s2 "
             "join slot s1 on s1.hadith_id=s2.hadith_id and s1.sanad_idx=s2.sanad_idx and s1.pos=s2.pos-1 "
             "where s2.ravi_id is not null and (s2.title like 'أب%' or s2.title like 'جد%' or s2.title like 'أخ%' "
             "or s2.title like 'عم%' or s2.title like 'خال%' or s2.title like 'أم%' or s2.title like 'ابن%')")
        for title, r, p, ptitle in c.execute(q):
            toks, _ = sanad_parse._strip_honorific(norm(title).split())
            # «أبيه محمد بن يحيى» names the father; «أبي عبد الله» is a kunya, so «أبي» counts only alone
            rel = RELATION.get(toks[0]) if toks and (len(toks) == 1 or toks[0] != "ابی") else None
            if not rel or not p:
                continue
            if rel == "grandfather" and RELATION.get(relative_word(ptitle) or "") == "father":
                rel = "father"                         # «عن أبيه عن جده»: the father of the father
            out[(p, rel)][r] += 1
        c.close()
        _noor_rel = {k: dict(v) for k, v in out.items()}
    return _noor_rel


def _cov(q: List[str], c: List[str], patro: bool = False) -> float:
    """Share of the query's name tokens found in the candidate, with the father/son guard. ``patro``: the query is written
    «ابن X» (son of X): X is the candidate's father, so a candidate named X himself is the wrong generation."""
    if not q or not c:
        return 0.0
    cs = set(c)
    inter = [t for t in q if t in cs]
    cov = len(inter) / len(q)
    if q[0] not in cs:
        return 0.0
    if (c[0] == q[0]) if patro else (c[0] != q[0]):
        cov *= 0.4                                  # the query's first name is only the candidate's father etc.
    cov -= 0.01 * len(cs - set(q))
    return max(cov, 0.0)


def _patronymic(name: str) -> bool:
    toks, _ = sanad_parse._strip_honorific(norm(name or "").split())
    return len(toks) > 1 and toks[0] in ("ابن", "بن")


def noor_candidates(name: str, imam_hint: bool = False, k: int = 10) -> List[dict]:
    q = name_tokens(name)
    if not q:
        return []
    patro = _patronymic(name)
    idx = noor_index()
    rare = sorted(set(q), key=lambda t: len(idx["post"].get(t, ())))[:2]
    ids = set()
    for t in rare:
        ids.update(idx["post"].get(t, ()))
    out = []
    for rid in ids:
        r = idx["ravis"][rid]
        cov = max([_cov(q, r["toks"], patro)] + [_cov(q, a) for a in r["alts"]])      # (a title the narrator is often written under is his, not his father's)
        if cov < 0.5:
            continue
        inf = rid in INFALLIBLE_IDS
        score = cov + 0.03 * math.log10(1 + r["n"]) + (0.4 if (imam_hint and inf) else 0.0)
        out.append(dict(id=f"noor:{rid}", label=r["name"], cov=round(cov, 3), score=round(score, 3), n=r["n"],
                        mazhab=r["mazhab"], grade=r["grade"]))
    out.sort(key=lambda d: -d["score"])
    return out[:k]


_STOP_N = {norm(x) for x in STOP}


def _ntoks(s: str) -> List[str]:
    return [bare("ابو" if t in ("ابی", "ابا") else t) for t in norm(s).split() if t not in _STOP_N and len(t) > 1]


def kg_candidates(name: str, death: Optional[int] = None, k: int = 10) -> List[dict]:
    q = _ntoks(name)
    if not q:
        return []
    patro = _patronymic(name)
    c = corpus.conn("kg")
    cnt = {}
    for t in set(q):
        vs = {t, "ال" + t}
        n = sum(r[0] for r in c.execute("select count(*) from kg_name_token where token in (?,?)", tuple(vs)))
        cnt[t] = n
    rare = sorted((t for t in cnt if cnt[t] > 0), key=lambda t: cnt[t])[:2]
    if not rare:
        return []
    ids = set()
    for t in rare:
        ids.update(r[0] for r in c.execute("select rawi_id from kg_name_token where token in (?,?)", (t, "ال" + t)))
    out = []
    for chunk in _chunks(sorted(ids), 800):
        q_ = "select id,name,nickname,death_year,riwaya_qty,tabaka,rank from rawis where id in (%s)" % ",".join("?" * len(chunk))
        for r in c.execute(q_, chunk):
            toks = _ntoks(r["name"] or "")
            cov = _cov(q, toks, patro)
            if r["nickname"]:
                cov = max(cov, _cov(q, _ntoks(r["nickname"])))
            if cov < 0.5:
                continue
            score = cov + 0.03 * math.log10(1 + (r["riwaya_qty"] or 0))
            dy = r["death_year"]
            if death and dy:
                d = abs(dy - death)
                score += 0.6 if d <= 3 else (-0.6 if d > 10 else 0.0)
            out.append(dict(id=f"kg:{r['id']}", label=r["name"], cov=round(cov, 3), score=round(score, 3),
                            n=r["riwaya_qty"] or 0, death=dy, rank=r["rank"], tabaka=r["tabaka"]))
    out.sort(key=lambda d: -d["score"])
    return out[:k]


def _chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ---------------------------------------------------------------------------
# chain assembly
# ---------------------------------------------------------------------------
def _elem(i: int, name: str, term, kind="name", exact=None, imam_hint=False, raw=None, **kw) -> dict:
    return dict(i=i, name=name, raw=raw or name, term=term, kind=kind, exact=exact, imam_hint=imam_hint, cands=[], **kw)


def noor_chains(rec: dict) -> List[dict]:
    hid = rec["hid"] - config.NOOR_OFFSET
    c = _nconn()
    rows = c.execute("select sanad_idx,pos,title,term,ravi_id,cand_json from slot where hadith_id=? order by sanad_idx,pos", (hid,)).fetchall()
    c.close()
    by: Dict[int, list] = defaultdict(list)
    for r in rows:
        by[r["sanad_idx"]].append(r)
    chains = []
    for sidx, slots in sorted(by.items()):
        elems: List[dict] = []
        speaker = None
        for r in slots:
            if r["term"]:                              # words before this narrator: may hide narrators
                for e in sanad_parse.parse(r["term"])["elems"]:
                    if e["kind"] in ("gap", "prev"):
                        continue
                    elems.append(_elem(len(elems), e["name"], e["term"], e["kind"], imam_hint=e["honorific"], raw=e["raw"], hidden=True))
            cands = json.loads(r["cand_json"]) if r["cand_json"] else []
            if r["ravi_id"] == PROPHET_ID:
                speaker = dict(kind="prophet", id=f"noor:{PROPHET_ID}")
                continue
            kind = "anon" if (not r["ravi_id"] and sanad_parse.is_anon(r["title"] or "")) else "name"
            el = _elem(len(elems), r["title"], r["term"], kind, imam_hint=bool(re.search(r"علیه|عليه|\bع\b|صلوات", r["title"] or "")),
                       raw=r["title"])
            if r["ravi_id"]:
                el["exact"] = f"noor:{r['ravi_id']}"
            elif cands:
                el["amb"] = [f"noor:{x}" for x in cands]
            elems.append(el)
        for k, e in enumerate(elems):
            e["i"] = k
        if elems:
            elems[0]["role"] = "collector"
        chains.append(dict(aid=f"{rec['wid']}#{sidx}", wid=rec["wid"], source="noor_slots", elems=elems,
                           speaker=speaker, quoted_work=None, flags=[]))
    return chains


def _kg_gap(name: str) -> bool:
    return name.startswith("موضع ") or name.strip() == "اسم مبهم"


def kg_chains(rec: dict) -> List[dict]:
    c = corpus.conn("kg")
    sids = [r[0] for r in c.execute("select id from sanads where hadith_id=? order by id", (rec["hid"],))]
    chains = []
    for sid in sids:
        rows = c.execute("select sr.pos, sr.rawi_id, r.name from sanad_rawis sr join rawis r on r.id=sr.rawi_id "
                         "where sr.sanad_id=? order by sr.pos", (sid,)).fetchall()
        if not rows:
            continue
        # the KG's gap markers («موضع تعليق», «موضع إرسال», «اسم مبهم») are unnamed narrators of this chain, not one person
        elems = [_elem(k, r["name"], None, "anon") if _kg_gap(r["name"]) else _elem(k, r["name"], None, exact=f"kg:{r['rawi_id']}")
                 for k, r in enumerate(rows)]
        if elems[0]["kind"] == "anon" and rec.get("author"):         # «موضع تعليق» at the collector: the author dropped the start
            elems.insert(0, _elem(0, rec["author"], None, kind="collector", role="collector", death=rec.get("death")))
            for k, e in enumerate(elems):
                e["i"] = k
        elems[0]["role"] = "collector"
        chains.append(dict(aid=f"{rec['wid']}#{sid}", wid=rec["wid"], source="kg", elems=elems, speaker=None,
                           quoted_work=None, flags=[]))
    return chains


def text_chain(rec: dict) -> Optional[dict]:
    p = sanad_parse.parse(rec["sanad"])
    elems = []
    if rec.get("author"):
        a = rec["author"]
        if "،" in a:                                   # "family، given" -> "given family"
            parts = [x.strip() for x in a.split("،")]
            a = " ".join(reversed(parts))
        elems.append(_elem(0, a, None, kind="collector", raw=rec["author"], role="collector", death=rec.get("death")))
    flags = list(p["flags"])
    for e in p["elems"]:
        if e["kind"] in ("gap", "prev"):
            flags.append(f"gap:{e['raw']}")
            continue
        if e["kind"] == "anon":
            elems.append(_elem(len(elems), e["name"], e["term"], "anon", raw=e["raw"]))
        else:
            elems.append(_elem(len(elems), e["name"], e["term"], e["kind"], imam_hint=e["honorific"], raw=e["raw"],
                               alts=e.get("alts")))
    if len(elems) < 2 and not p["speaker"]:
        flags.append("no_sanad")
    return dict(aid=f"{rec['wid']}#t", wid=rec["wid"], source="text", elems=elems, speaker=p["speaker"],
                quoted_work=p["quoted_work"], flags=flags)


def hadithai_chain(rec: dict) -> Optional[dict]:
    """The isnad as hadith.ai v2 tagged it (``icm.hadithai``): its narrator names, classified by the isnad grammar."""
    got = hadithai.chain_elems(rec["hid"])
    if not got:
        return None
    tagged, speaker = got
    elems = []
    if rec.get("author"):
        a = rec["author"]
        if "،" in a:                                   # "family، given" -> "given family"
            a = " ".join(reversed([x.strip() for x in a.split("،")]))
        elems.append(_elem(0, a, None, kind="collector", raw=rec["author"], role="collector", death=rec.get("death")))
    for e in tagged:
        kw = dict(hai=e["hai"]) if e.get("hai") else {}
        elems.append(_elem(len(elems), e["name"], e["term"], e["kind"], imam_hint=e.get("honorific", False), raw=e["raw"],
                           alts=e.get("alts"), **kw))
    if speaker and speaker.get("elem") is not None:
        speaker = dict(speaker, elem=speaker["elem"] + (1 if rec.get("author") else 0))
    flags = [] if len(elems) >= 2 or speaker else ["no_sanad"]
    return dict(aid=f"{rec['wid']}#t", wid=rec["wid"], source="hadithai", elems=elems, speaker=speaker,
                quoted_work=None, flags=flags)


def chains_for(rec: dict) -> List[dict]:
    """Noor's linked isnads, then the KG's, then hadith.ai's narrator tags, then the rule-based parse of the text.

    A reference inside a record («… و رواه الصدوق عن … مثله», ``pointers.record``: wid «<host>~k») has the isnad written
    before its formula: Noor's isnad k of the host when Noor read one isnad per reference, else the parse of that isnad.
    The host then keeps only its own (first) Noor isnad."""
    if rec.get("host"):
        from . import pointers
        host = corpus.get(rec["host"])
        if host is not None and host["corpus"] == "v02" and host["hid"] >= config.NOOR_OFFSET:
            n_in = sum(1 for r in pointers.split(host)["refs"] if r["of"] == "host")
            ch = noor_chains(host)
            if n_in and len(ch) == 1 + n_in and rec["ref_k"] < len(ch):
                c = ch[rec["ref_k"]]
                return [dict(c, aid=f"{rec['wid']}#{rec['ref_k']}", wid=rec["wid"])]
        t = text_chain(rec)
        return [t] if t else []
    if rec["corpus"] == "kg":
        ch = kg_chains(rec)
        if ch:
            return ch
    elif rec["hid"] >= config.NOOR_OFFSET:
        ch = noor_chains(rec)
        if len(ch) > 1:
            from . import pointers
            n_in = sum(1 for r in pointers.split(rec)["refs"] if r["of"] == "host")
            if n_in and len(ch) == 1 + n_in:           # the others are the isnads of its inner references («<wid>~k»)
                ch = ch[:1]
        if ch:
            return ch
    if rec["corpus"] == "v02":
        h = hadithai_chain(rec)
        if h:
            return [h]
    t = text_chain(rec)
    return [t] if t else []


# ---------------------------------------------------------------------------
# candidates and context
# ---------------------------------------------------------------------------
def attach_candidates(chains: List[dict], tradition_of: Dict[str, str]) -> None:
    """Fill ``cands`` of every element; exact ones get a single certain candidate."""
    for ch in chains:
        trad = tradition_of.get(ch["wid"]) or ("sunni" if ch["source"] == "kg" else "shia")
        for e in ch["elems"]:
            if e["exact"]:
                e["cands"] = [dict(id=e["exact"], label=e["name"], score=9.0, cov=1.0, exact=True)]
            elif e["kind"] == "anon":
                e["cands"] = []
            elif e.get("amb"):
                idx = noor_index()["ravis"]
                e["cands"] = [dict(id=i, label=idx[int(i.split(":")[1])]["name"], score=1.0, cov=1.0, n=idx[int(i.split(":")[1])]["n"])
                              for i in e["amb"]]
            elif e["kind"] == "relative":
                e["cands"] = []                             # derived from the narrator before it (_derive_relatives)
            elif e["kind"] == "collector":
                _collector(e, trad)
            else:
                core = _clean(e)
                e["cands"] = match(core, trad, e["imam_hint"], e.get("death")) if core else []
                _form_cands(e, core, trad)
                if not e["cands"] and names.fragment(core or e["name"]):
                    e["frag"] = True                      # no narrator's name looks like this: a sentence of commentary, matn glued to the isnad
        _derive_relatives(ch)
        for e in ch["elems"]:
            _hai_candidate(e)
        _add_context(ch)


def _glued_conj(vocab: dict, t: str) -> bool:
    """«ومحمد»: a «و» glued to the next name. The word without it is far more common in the databases' names than the glued word is («ومحمد» 2376,
    «محمد» 103888), or it opens an unnamed group («وعدة من أصحابنا»); a name that begins with «و» («وهب», «وكيع») has no such stem."""
    stem = names.written_key(t[1:])
    n_stem, n_word = vocab.get(stem, 0), vocab.get(t, 0)
    return (n_stem >= 20 and n_stem >= 10 * n_word) or stem in ("عده", "عدة", "جماعه", "رجل", "رجال", "بعض")


def clean_parts(name: str) -> List[str]:
    """The names a reading holds, cleaned of the isnad words glued to it («قلت لابی عبد الله», «نبا عمرو بن خالد», «… انه»): split at
    a «و» (also glued to the next name, «وزکریا», when the word is no name but what follows it is one). [] when nothing is left."""
    core = sanad_parse.strip_nonname(name)["core"]
    v = names.vocab()
    parts: List[List[str]] = [[]]
    for t in core.split():
        if t == "و":
            parts.append([])
        elif v and t[0] == "و" and len(t) > 3 and parts[-1] and _glued_conj(v, t):
            parts.append([t[1:]])
        else:
            parts[-1].append(t)
    out = [" ".join(p) for p in parts if p]
    return [p for p in out if not sanad_parse.not_a_name(p)] or out[:1]


def _clean(e: dict) -> str:
    """The name to look up for a place: the first of its cleaned parts (``e["clean"]``; the other names of a place that holds several
    are in ``e["parts"]``: the chain editor splits them)."""
    parts = clean_parts(e["name"])
    core = parts[0] if parts else ""
    if core and core != " ".join(norm(e["name"]).split()):
        e["clean"] = core
    if len(parts) > 1:
        e["parts"] = parts
    return core


def _form_cands(e: dict, core: str, trad: str) -> None:
    """Persons the name has been written for in the databases' isnads, when the name matching found no one close: «الثوري», «الشيباني»
    are nobody's *name*, but isnads write them for one person (at least 3 hadiths). They are ``low`` candidates, never a default."""
    if not core or any(c.get("cov", 0) >= 0.72 for c in e["cands"]) or not names.available():
        return
    k = names.written_key(core)
    have = {c["id"] for c in e["cands"]}
    for db in (("noor", "kg") if trad != "sunni" else ("kg", "noor")):
        for pid, n in names.persons_written(db, k, 3):
            cid = f"{db}:{pid}"
            if n >= 3 and cid not in have:
                have.add(cid)
                e["cands"].append(dict(id=cid, label=_label(cid), cov=0.5, score=round(0.5 + 0.03 * math.log10(1 + n), 3), n=n, form=True,
                                       reason=f"«{core}» در {n} حدیث برای او نوشته شده"))
    e["cands"].sort(key=lambda d: -d["score"])
    e["cands"] = e["cands"][:10]


def _collector(e: dict, trad: str) -> None:
    """The book's author. When ``icm.authors`` is sure who he is (name and death year agree with exactly one person) he is
    exact; otherwise the name-matched candidates stay, except persons whose recorded death is years from the book author's."""
    got = authors.lookup(e["raw"], e.get("death")) or authors.lookup(e["name"], e.get("death"))
    if got:
        ids = sorted(got["ids"], key=lambda i: not i.startswith("noor:" if trad == "shia" else "kg:"))      # his own tradition's database first
        e["exact"] = ids[0]
        e["cands"] = [dict(id=i, label=_label(i), score=9.0, cov=1.0, exact=True, author=got["by"]) for i in ids[:1]]
        return
    cs = match(e["name"], trad, False, e.get("death"), True)
    e["cands"] = [c for c in cs if not authors.contradicts(c["id"], e.get("death"))]
    nm = authors.name_link(e["raw"], e.get("death")) or authors.name_link(e["name"], e.get("death"))
    if nm:
        e["sugg"] = dict(id=nm, rule="author_name", src="authors", prec=AUTHOR_NAME_PREC)
        _sugg_candidate(e)


def _label(pid: str) -> str:
    kind, _, raw = pid.partition(":")
    if kind == "wiki":                                  # a person made in the narrator wiki (no database holds him)
        try:
            from narwiki import lookup
            return (lookup.wiki_people([pid]).get(pid) or {}).get("name") or pid
        except Exception:
            return pid
    if kind == "noor":
        r = noor_index()["ravis"].get(int(raw))
        return r["name"] if r else pid
    r = corpus.conn("kg").execute("select name from rawis where id=?", (int(raw),)).fetchone()
    return r["name"] if r else pid


HAI_FA = {"ctx3": "با همان راوی پیش و پس از آن", "ctx_prev": "با همان راوی پیش از آن", "ctx_next": "با همان راوی پس از آن"}


def _hai_candidate(e: dict) -> None:
    """The Noor narrator a hadith.ai mention is tied to (``e['hai']``): Noor's own slot of the same record is
    exact; a context suggestion (``icm.build.hadithai``: the name with the same neighbours is this person in
    every one of at least 2-3 linked Noor isnads) is put first among the candidates, never made exact."""
    m = e.get("hai")
    if not m or e.get("exact"):
        return
    idx = noor_index()["ravis"]
    if m.get("link") == "noor_slot" and m.get("ravi_id") in idx:
        e["exact"] = f"noor:{m['ravi_id']}"
        e["cands"] = [dict(id=e["exact"], label=idx[m["ravi_id"]]["name"], score=9.0, cov=1.0, exact=True)]
        return
    rid = m.get("sugg_ravi")
    if rid not in idx:
        return
    cid, r = f"noor:{rid}", idx[rid]
    reason = f"«{e['raw'] or e['name']}» {HAI_FA.get(m['sugg'], '')} در {m['sugg_n']} سند پیوندخورده‌ی نور همیشه همین راوی است"
    top = max((c["score"] for c in e["cands"] if c["id"] != cid), default=0.0)
    c = next((c for c in e["cands"] if c["id"] == cid), None)
    if c is None:
        c = dict(id=cid, label=r["name"], cov=round(max([_cov(name_tokens(e["name"]), r["toks"])] + [_cov(name_tokens(e["name"]), a) for a in r["alts"]]), 3),
                 n=r["n"], mazhab=r["mazhab"], grade=r["grade"], score=0.0)
        e["cands"].append(c)
    c["reason"] = (c["reason"] + "؛ " + reason) if c.get("reason") else reason
    if any(x.get("derived") and x["id"] != cid for x in e["cands"]):
        return                                         # a relative already derived from a certain narrator stays first
    c.update(hai=m["sugg"], hai_n=m["sugg_n"], score=round(max(c["score"], top + 0.2), 3))
    e["cands"].sort(key=lambda d: -d["score"])


# ---------------------------------------------------------------------------
# the middle level: a person used automatically, to be approved by the user (``e["auto"]``)
# ---------------------------------------------------------------------------
AUTO_SRC_FA = dict(family="در سندی دیگر از همین پروژه، همین نام کنار همین همسایه‌ها این راوی است",
                   twin="همین حدیث در منبعی که راویانش پیوند خورده، با همین نام‌ها در همین ترتیب")
AUTO_RULE_FA = dict(ctx3="همین نام با همان راوی پیش و پس از آن", prev="همین نام با همان راوی پیش از آن", next="همین نام با همان راوی پس از آن",
                    name="این نام همیشه همین راوی است", pprev="پس از راوی شناخته‌شده‌ی پیشین", pnext="پیش از راوی شناخته‌شده‌ی پسین",
                    name_adj="تقریباً همیشه همین راوی است (در هر دسته از کتاب‌ها) و در سندهای پایگاه کنار همسایه‌های این سند آمده")
AUTO_SRC_FA["twin_fuzzy"] = "همین حدیث در منبعی دیگر از همین پروژه که راویانش پیوند خورده: نام‌های هم‌خوان (کوتاه‌شده) به همان ترتیب، کنار جایی دیگر که هم‌خوان است"
AUTO_SRC_FA["imams"] = "پدرِ امام، از جدول امامان"
AUTO_SRC_FA["father"] = "پدرِ راوی پیشین: نامِ پدر در نام او و بیشترین بار استادِ او در سندهای پایگاه (با فاصله‌ی روشن از نامزد بعدی)"
AUTHOR_NAME_PREC = 0.97             # measured: name only (death years ignored) gave the same person as name + death year in 110 of 110 authors (95% lower bound 97%)
SUGG_FA = dict(author_name="نامِ گردآورنده با آغازِ نامِ این شخص می‌خواند و او در پایگاه سال وفات ندارد (بررسی وفات ممکن نبود)",
               father_of_prev="پدرِ راوی پیشین: نامِ پدر در نام او و بیشترین بار استادِ او در سندهای پایگاه",
               name_major="«{name}» در {n} از {tot} حدیثِ پایگاه این راوی است (در هر دسته از کتاب‌ها)",
               adj="از میان نامزدهای این نام، بیشترین هم‌نشینی با راویان پیش و پس از آن در سندهای پایگاه و بیشترین نوشته‌شدن این نام برای او",
               adj_one="تنها نامزدی که در سندهای پایگاه کنار هر دو همسایه‌ی این جا آمده")
TRUSTED_NEIGHBOUR = ("family", "twin", "ctx3")             # an automatic link that may vouch for its neighbour's pprev / pnext


def _written(e: dict) -> str:
    raw = e.get("raw") or e["name"]
    parts = clean_parts(raw)
    return names.written_key(parts[0] if parts else raw)


def _auto_places(ch: dict, kgw: Optional[dict] = None) -> Tuple[List[dict], List[str]]:
    """The places of a chain that stand for names (not the collector, not isnad wording) and their written keys.
    A KG chain names its narrators by the database's name; the words the hadith wrote come from ``names.kg_written``."""
    places, ks, used = [], [], {}
    for e in ch["elems"]:
        if e.get("role") == "collector" or e["kind"] == "collector" or sanad_parse.not_a_name(e["name"]):
            continue
        k = None
        ex = e.get("exact") or ""
        if kgw and ex.startswith("kg:"):
            rid = int(ex[3:])
            got = kgw.get(rid) or []
            j = used.get(rid, 0)
            used[rid] = j + 1
            k = got[j] if j < len(got) else (got[-1] if got else None)
        places.append(e)
        ks.append(k or _written(e))
    return places, ks


def _family_index(rows) -> dict:
    """(written name, neighbours) -> {person: n} over the places of this project the databases already settled."""
    idx: Dict[tuple, Dict[str, int]] = defaultdict(dict)
    for places, ks in rows:
        for i, e in enumerate(places):
            ex = e.get("exact")
            if not ex or e["kind"] == "anon":
                continue
            p, n = (ks[i - 1] if i else "^"), (ks[i + 1] if i + 1 < len(ks) else "$")
            for key in (("3", ks[i], p, n), ("p", ks[i], p), ("n", ks[i], n)):
                idx[key][ex] = idx[key].get(ex, 0) + 1
    return idx


def _family(idx: dict, k: str, p: str, n: str) -> Optional[dict]:
    one = lambda key: next(iter(idx[key])) if key in idx and len(idx[key]) == 1 else (False if key in idx else None)
    a = one(("3", k, p, n))
    if a:
        return dict(id=a, rule="family", n=idx[("3", k, p, n)][a], tot=idx[("3", k, p, n)][a], src="family")
    b, c = one(("p", k, p)), one(("n", k, n))
    if b is False or c is False or (b and c and b != c):
        return None
    got = b or c
    key = ("p", k, p) if b else ("n", k, n)
    return dict(id=got, rule="family", n=idx[key][got], tot=idx[key][got], src="family") if got else None


def _fuzzy_twins(ch: dict, places: List[dict], ks: List[str], rows, need: int = 3, debug: Optional[dict] = None) -> Dict[int, set]:
    """place index -> {ids} the same hadith in another chain of the project that carries ids says for it, when the names are only
    *compatible*: the Wafi's «علي عن أبيه و محمد عن أحمد و العدة عن سهل» against the Kafi's chains «علي بن إبراهيم عن أبيه», «محمد بن يحيى عن
    أحمد بن محمد», «عدة من أصحابنا عن سهل بن زياد». The two are put in order (longest common subsequence): a pair matches when the
    written names are the same, or when the other chain's person is one of the place's own candidates (the name matching found him),
    or when both are unnamed groups. A chain counts only when at least ``need`` places match, two of them by the same written name,
    and at least half of this chain's named places. A place with two different persons from different chains gets none."""
    out: Dict[int, set] = defaultdict(set)
    named = [i for i, e in enumerate(places) if e["kind"] in ("name", "relative")]
    if len(named) < need:
        return {}
    cand = [{c["id"] for c in e.get("cands") or []} for e in places]
    for och, orec, oplaces, oks in rows:
        if och is ch or och["wid"] == ch["wid"] or not any(e.get("exact") for e in oplaces):
            continue
        n, m = len(places), len(oplaces)
        score = [[0] * (m + 1) for _ in range(n + 1)]
        how = [[None] * (m + 1) for _ in range(n + 1)]
        for i in range(n):
            for j in range(m):
                w = 0
                if ks[i] and ks[i] == oks[j]:
                    w = 2
                elif oplaces[j].get("exact") and oplaces[j]["exact"] in cand[i]:
                    w = 1
                elif places[i]["kind"] == "anon" and oplaces[j]["kind"] == "anon":
                    w = 1
                best, h = score[i][j + 1], "up"
                if score[i + 1][j] > best:
                    best, h = score[i + 1][j], "left"
                if w and score[i][j] + w > best:
                    best, h = score[i][j] + w, "diag"
                score[i + 1][j + 1], how[i + 1][j + 1] = best, h
        pairs, i, j = [], n, m
        while i and j:
            h = how[i][j]
            if h == "diag":
                pairs.append((i - 1, j - 1))
                i, j = i - 1, j - 1
            elif h == "up":
                i -= 1
            else:
                j -= 1
        same = sum(1 for a, b in pairs if ks[a] and ks[a] == oks[b])
        if len(pairs) < need or same < 2 or sum(1 for a, _ in pairs if a in named) * 2 < len(named):
            continue
        pset = set(pairs)
        for a, b in pairs:
            if oplaces[b].get("exact"):
                nb = [(a + d, b + d) for d in (-1, 1) if (a + d, b + d) in pset]
                if debug is not None:
                    debug.setdefault(a, []).append((len(pairs), same, bool(ks[a] and ks[a] == oks[b]), len(named),
                                                   len(nb), sum(1 for x, y in nb if ks[x] and ks[x] == oks[y])))
                if ks[a] in GENERIC_FIRST or places[a]["kind"] == "relative" or relative_word(places[a]["name"]):
                    continue                               # «ابو», «ابيه»: not a name of one person
                if ks[a] and ks[a] == oks[b] or nb:             # a loose match stands only next to another match (alone it is right in 62%, with one in 98%)
                    out[a].add(oplaces[b]["exact"])
    return out


def _twin_rows(rec: Optional[dict], limit: int = 8) -> list:
    """The isnads of the hadiths that are the same text and carry ids, as chains for ``_fuzzy_twins``: Noor's (the matched hadiths of the v02
    consensus sets, and the twins ``twins.sqlite`` found by the opening words) and the KG's tagged ones (their written names from the names file)."""
    if not rec or rec.get("corpus") not in ("v02", "kg"):
        return []
    from . import relations, twins
    corp, hid = rec["corpus"], int(rec["hid"])
    noor_ids: List[int] = []
    kg_ids: List[int] = []
    if corp == "v02":
        for t in sorted(relations.consensus_set(hid)):
            nid = _noor_twin(t)
            if nid is not None:
                noor_ids.append(nid)
    for db, tid in twins.of(corp, hid):
        if db == "noor" and tid not in noor_ids:
            noor_ids.append(tid)
        elif db == "kg" and not (corp == "kg" and tid == hid):
            kg_ids.append(tid)
    rows, done = [], 0
    c = _nconn()
    try:
        for nid in noor_ids:
            if done >= limit:
                break
            chains: Dict[int, list] = defaultdict(list)
            for r in c.execute("select sanad_idx, title, ravi_id, type from slot where hadith_id=? order by sanad_idx, pos", (nid,)):
                if r["type"] != 4 and r["ravi_id"] != PROPHET_ID:
                    chains[r["sanad_idx"]].append(r)
            if not chains:
                continue
            done += 1
            for idx, sl in chains.items():
                places = [dict(name=r["title"] or "", kind="anon" if sanad_parse.is_anon(r["title"] or "") else "name",
                               exact=f"noor:{r['ravi_id']}" if r["ravi_id"] else None, cands=[]) for r in sl]
                rows.append((dict(wid=f"twin:noor{nid}#{idx}"), None, places, [names.written_key(p["name"]) for p in places]))
    finally:
        c.close()
    for kid in kg_ids[:max(0, limit - done)]:
        ch = names.kg_chain(kid)
        if len(ch) >= 2:
            places = [dict(name=k, kind="name", exact=f"kg:{rid}" if rid else None, cands=[]) for k, rid in ch]
            rows.append((dict(wid=f"twin:kg{kid}"), None, places, [k for k, _ in ch]))
    return rows


def _noor_twin(hid: int) -> Optional[int]:
    o = config.NOOR_OFFSET
    return hid - o if o <= hid < o + 100_000_000 else hid if hid < 600_000_000 else None


def _twins(rec: dict, ks: List[str], limit: int = 8) -> Dict[int, set]:
    """place index -> {noor ids} the same hadith in another v02 book says for it: the other book's Noor isnad (linked by Noor), aligned
    with this chain's written names in order."""
    if rec.get("corpus") != "v02":
        return {}
    from . import relations
    from difflib import SequenceMatcher
    out: Dict[int, set] = defaultdict(set)
    c = _nconn()
    try:
        done = 0
        for t in sorted(relations.consensus_set(int(rec["hid"]))):
            nid = _noor_twin(t)
            if nid is None or done >= limit:
                continue
            chains: Dict[int, list] = defaultdict(list)
            for r in c.execute("select sanad_idx, title, ravi_id, type from slot where hadith_id=? order by sanad_idx, pos", (nid,)):
                if r["type"] != 4 and r["ravi_id"] != PROPHET_ID:
                    chains[r["sanad_idx"]].append((names.written_key(r["title"] or ""), r["ravi_id"]))
            if not chains:
                continue
            done += 1
            for sl in chains.values():
                blocks = [b for b in SequenceMatcher(None, ks, [x[0] for x in sl], autojunk=False).get_matching_blocks() if b.size]
                if sum(b.size for b in blocks) < min(2, len(ks)):
                    continue                                   # another isnad of the hadith, or another hadith
                for b in blocks:
                    for d in range(b.size):
                        if sl[b.b + d][1]:
                            out[b.a + d].add(f"noor:{sl[b.b + d][1]}")
    finally:
        c.close()
    return out


def auto_links(chains: List[dict], trad: Dict[str, str], wits: Sequence[dict], twins: bool = True) -> int:
    """Give every place the databases did not settle a person from what the chains that carry ids say (the middle level of certainty):
    the project's own settled chains (``family``), the same hadith in a book with linked isnads (``twin``), and the written name in
    company (``icm.names``: Noor's slots and the KG's tagged isnads, measured leave-book-out). ``e["auto"] = {id, rule, n, tot, src, prec}``
    and the person is put first among the place's candidates. Two sources that disagree give nothing. Returns the number of links."""
    recs = {w["wid"]: w for w in wits}
    kgw_of: Dict[str, dict] = {}
    rows = []
    for ch in chains:
        rec = recs.get(ch["wid"])
        kgw = None
        if ch["source"] == "kg" and rec is not None:
            kgw = kgw_of.get(ch["wid"])
            if kgw is None:
                kgw = kgw_of[ch["wid"]] = names.kg_written(rec["hid"])
        rows.append((ch, rec) + _auto_places(ch, kgw))
    wk = names.wiki()
    for ch, rec, places, ks in rows:                           # what the narrator wiki approved for a written name is as sure as the databases
        for e, k in zip(places, ks):
            i = wk["approved"].get(k)
            if i and not e.get("exact") and e["kind"] == "name":
                e["exact"], e["by"] = i, "wiki"
                e["cands"] = [dict(id=i, label=_label(i), score=9.0, cov=1.0, exact=True, tier="exact", reason="تأییدشده در ویکی راویان")]
    fam = _family_index([(pl, ks) for _, _, pl, ks in rows])
    gs = {db: (names._getter(db), names.cfg(db)) for db in ("noor", "kg")} if names.available() else {}
    todo = lambda e: not e.get("exact") and e["kind"] == "name" and not e.get("auto")
    n_auto = 0

    def known(e: Optional[dict], db: str) -> Optional[int]:
        """The database id of a neighbour the chain knows for sure enough to vouch for a place next to it."""
        if e is None:
            return None
        a = e.get("auto")
        i = e.get("exact") or (a["id"] if a and (a["src"] in ("family", "twin") or a["rule"] == "ctx3") else None)
        return int(i.split(":")[1]) if i and i.startswith(db + ":") and i.split(":")[1].isdigit() else None
    skip = set()                                              # places whose sources disagreed
    for rounds in range(2):                                   # the second round knows the persons the first one found next to a place
        for ch, rec, places, ks in rows:
            tw = None
            fz = _fuzzy_twins(ch, places, ks, rows + (_twin_rows(rec) if twins and rec is not None and any(todo(e) for e in places) else [])) if rounds == 0 else {}
            for i, e in enumerate(places):
                if not todo(e) or id(e) in skip:
                    continue
                p, n = (ks[i - 1] if i else "^"), (ks[i + 1] if i + 1 < len(ks) else "$")
                f = _family(fam, ks[i], p, n) if rounds == 0 else None
                t = None
                if rounds == 0 and rec is not None and twins:
                    tw = _twins(rec, ks) if tw is None else tw
                    ids = tw.get(i) or set()
                    t = dict(id=next(iter(ids)), rule="twin", n=1, tot=1, src="twin") if len(ids) == 1 else None
                    if len(ids) > 1:
                        t = False
                got = None
                if t is None and not f and len(fz.get(i) or ()) == 1:
                    t = dict(id=next(iter(fz[i])), rule="twin_fuzzy", n=1, tot=1, src="twin_fuzzy", prec=0.98)
                if f and t is not None and (t is False or f["id"] != t["id"]):
                    got = None                                   # the sources disagree: leave it to the user
                    skip.add(id(e))
                elif f or t:
                    got = f or t
                elif t is None and gs:
                    dbs = ("noor", "kg") if (trad.get(ch["wid"]) or ch.get("tradition") or "shia") == "shia" else ("kg", "noor")
                    for db in dbs:
                        g, cf = gs[db]
                        r, seen = names.decide(g, ks[i], p, n, known(places[i - 1] if i else None, db), known(places[i + 1] if i + 1 < len(places) else None, db), cf)
                        if r and (ks[i], f"{db}:{r['id']}") in wk["rejected"]:
                            r, seen = None, True                 # the wiki refused this reading of the name
                        if r:
                            rl = ((names.meta().get("eval") or {}).get(db) or {}).get("rules", {}).get(r["rule"], {})
                            got = dict(id=f"{db}:{r['id']}", rule=r["rule"], n=r["n"], tot=r["tot"], src=db,
                                       prec=rl.get("precision_at_need", rl.get("precision")))
                            break
                        if seen:
                            break
                if got and (not e.get("amb") or got["id"] in e["amb"]):
                    e["auto"] = got
                    n_auto += 1
    n_auto += _neighbour_links(rows, trad, wk)
    for ch, _, places, _ in rows:
        for e in places:
            _auto_candidate(e)
            _sugg_candidate(e)
    return n_auto


def _vouch(e: Optional[dict]) -> Optional[str]:
    """The person a neighbour place stands for, when it is settled enough to vouch for the place next to it."""
    if e is None:
        return None
    return e.get("exact") or (e["auto"]["id"] if e.get("auto") else None)


def _neighbour_links(rows, trad: Dict[str, str], wk: dict) -> int:
    """What the databases' teacher/student pairs (``icm.neighbours``) say for the places the written names did not settle:
    «عن أبيه» after a known narrator is his father when the databases show one person with the father's name next to him far more
    than any other (``father_of_prev``: the middle level); a name one person dominates (``name_major``) is that person when he is also
    next to a neighbour in at least 3 hadiths (``name_major_adj``: middle level); the weaker readings of both and the ambiguous-name
    ranking (``adj``, ``adj_one``) are ``e["sugg"]``, a suggestion that is used nowhere until the user approves it."""
    n = 0
    have = names.available()
    for _ in range(3):                                          # «عن أبيه عن جده»: the second relative needs the first
        changed = 0
        for ch, rec, places, ks in rows:
            order = ("noor", "kg") if (trad.get(ch["wid"]) or ch.get("tradition") or "shia") == "shia" else ("kg", "noor")
            for i, e in enumerate(places):
                if e.get("exact") or e.get("auto") or e["kind"] == "anon" or e.get("frag"):
                    continue
                pb, nb = _beside1(places, i, -1), _beside1(places, i, +1)
                prev = _vouch(pb) if pb else None
                nxt = _vouch(nb) if nb else None
                if e["kind"] == "relative":
                    rel = RELATION.get(e["name"])
                    base = pb
                    if base is None or not prev or any(c.get("derived") for c in e["cands"]) or rel not in ("father", "grandfather"):
                        continue
                    if rel == "grandfather" and not (base["kind"] == "relative" and RELATION.get(base["name"]) == "father"):
                        continue
                    key = _ID2KEY.get(int(prev.split(":")[1])) if prev.startswith("noor:") and prev.split(":")[1].isdigit() else None
                    if key:                                    # an Imam's father is in the table of Imams, not in the isnads (Sadiq's father is not a «محمد» who sat next to him)
                        fid = _NOOR_OF_KEY.get(FATHER.get(key) or "")
                        if fid:
                            e["auto"] = dict(id=fid, rule="father_of_prev", n=0, tot=0, src="imams", prec=0.999)
                            e.pop("sugg", None)
                            n += 1
                            changed += 1
                        continue
                    link, hint = neighbours.father_rule(prev)
                    if link:
                        e["auto"] = dict(link, src="father")
                        e.pop("sugg", None)
                        n += 1
                        changed += 1
                    elif hint and not e.get("sugg"):
                        e["sugg"] = dict(hint, src="father")
                    continue
                if e["kind"] != "name" or not have:
                    continue
                k = ks[i]
                got, best = None, None
                for db in order:
                    m = names.major_of(db, k)
                    if m and (k, m["id"]) not in wk["rejected"] and (not e.get("amb") or m["id"] in e["amb"]):
                        pr = neighbours.pairs(m["id"], prev, nxt)
                        if pr >= 3 and m["share"] >= 0.99:
                            got = dict(id=m["id"], rule="name_major_adj", n=m["n"], tot=m["tot"], src=db, pairs=pr, prec=neighbours.MAJOR_ADJ_PREC[db])
                            break
                        if m.get("prec") is not None and (best is None or m["prec"] > best["prec"]):
                            best = dict(m, src=db)
                    h = neighbours.hint(db, k, [c["id"] for c in e["cands"][:8]], prev, nxt)
                    if h and (not e.get("amb") or h["id"] in e["amb"]) and (best is None or h["prec"] > best["prec"]):
                        best = dict(h, src=db)
                if got:
                    e["auto"] = got
                    e.pop("sugg", None)
                    n += 1
                    changed += 1
                elif best and not e.get("sugg"):
                    e["sugg"] = best
        if not changed:
            break
    return n


def _sugg_candidate(e: dict) -> None:
    """The person of ``e["sugg"]`` first among the place's candidates (tier ``sugg``, between the automatic links and the name matches),
    with the reason. A place that has an automatic link has no suggestion."""
    a = e.get("sugg")
    if not a:
        e.pop("sugg", None)
        return
    if e.get("exact") or e.get("auto"):
        del e["sugg"]
        return
    cid = a["id"]
    why = SUGG_FA.get(a["rule"], "").format(name=e.get("clean") or e["name"], n=a.get("n"), tot=a.get("tot"))
    if a.get("prec") is not None:
        why += f" (دقت سنجیده‌شده {round(100 * a['prec'])}٪)"
    c = next((c for c in e["cands"] if c["id"] == cid), None)
    if c is None:
        c = dict(id=cid, label=_label(cid), cov=0.0, score=0.0, n=0)
        e["cands"].append(c)
    top = max((x["score"] for x in e["cands"] if x is not c), default=0.0)
    c.update(tier="sugg", sugg=a["rule"], score=round(max(c["score"], top + 0.2), 3), reason=why if not c.get("reason") else c["reason"] + "؛ " + why)
    e["cands"].sort(key=lambda d: -d["score"])


def _auto_candidate(e: dict) -> None:
    """The person of ``e["auto"]`` first among the place's candidates, with the reason and the new tier ``auto``."""
    a = e.get("auto")
    if not a:
        return
    cid = a["id"]
    why = AUTO_SRC_FA.get(a["src"]) or (f"«{e.get('raw') or e['name']}» {AUTO_RULE_FA.get(a['rule'].replace('_major', ''), '')} "
                                       f"در {a['n']} از {a['tot']} حدیث{' (بیشتر)' if a['rule'].endswith('_major') else ''} {'نور' if a['src'] == 'noor' else 'گراف دانش'}")
    c = next((c for c in e["cands"] if c["id"] == cid), None)
    if c is None:
        label = _label(cid)
        cov = 0.0
        if cid.startswith("noor:") and int(cid[5:]) in noor_index()["ravis"]:
            r = noor_index()["ravis"][int(cid[5:])]
            cov = round(max([_cov(name_tokens(e["name"]), r["toks"])] + [_cov(name_tokens(e["name"]), x) for x in r["alts"]]), 3)
        c = dict(id=cid, label=label, cov=cov, score=0.0, n=0)
        e["cands"].append(c)
    top = max((x["score"] for x in e["cands"] if x is not c), default=0.0)
    c.update(tier="auto", auto=a["rule"], score=round(max(c["score"], top + 0.3), 3), reason=why if not c.get("reason") else c["reason"] + "؛ " + why)
    e["cands"].sort(key=lambda d: -d["score"])


def _veto_defaults(cards: Dict[str, dict], trad: Dict[str, str]) -> int:
    """A card's default (a ``high`` name match) is taken back when the databases say the written name means someone else: one of its
    places has an automatic link to another person, or the name alone is, in at least 20 hadiths and 80% of them, another person
    («ابن عمر» is عبد الله بن عمر, not عمر بن الخطاب, who matches it by name). Its places then follow their own links."""
    n = 0
    gs = {db: names._getter(db) for db in ("noor", "kg")} if names.available() else {}
    for cd in cards.values():
        d = cd.get("default")
        if not d or cd.get("kind") == "relative":
            continue
        top = next((c for c in cd["candidates"] if c["id"] == d), None)
        mine = {d} | set((top or {}).get("also") or [])
        if any(i not in mine for i in (cd.get("autos") or {})):
            cd["default"], cd["veto"] = None, "auto"
            n += 1
            continue
        k = names.written_key(sanad_parse.strip_nonname(cd["name"])["core"] or cd["name"])
        for db, g in gs.items():
            r = g("name", (k,))
            if r and r[2] >= 20 and r[1] / r[2] >= 0.8 and f"{db}:{r[0]}" not in mine and d.startswith(db + ":"):
                cd["default"], cd["veto"] = None, db
                n += 1
                break
    return n


_LAQAB: Dict[str, str] = {}                  # an Imam's laqab written alone («الصادق») -> his noor id
for _k, _v in _IMAMS.items():
    if _v.get("ravi_id") and _k not in ("prophet", "fatima", "unspecified", "lam_yarwi"):
        for _a in _v.get("noor_aliases", []):
            if " " not in _a.strip():
                _LAQAB[title_key(_a)] = f"noor:{_v['ravi_id']}"


def match(name: str, trad: str, imam_hint: bool = False, death=None, collector: bool = False) -> List[dict]:
    """Candidate persons for a name in a chain of this tradition: the KG for Sunni chains (Noor too for an Imam),
    Noor for Shia chains (the KG too for a collector)."""
    if trad == "sunni":
        cs = kg_candidates(name, death) + (noor_candidates(name, imam_hint, 4) if imam_hint else [])
    else:
        cs = noor_candidates(name, imam_hint) + (kg_candidates(name, death, 3) if collector else [])
    lk = _LAQAB.get(title_key(name)) if trad != "sunni" and not collector else None
    if lk:                                         # «الصادق» written alone in an Imami chain is the Imam, whatever else it matches
        rid = int(lk.split(":")[1])
        cs = [c for c in cs if c["id"] != lk]
        r = noor_index()["ravis"].get(rid)
        cs.insert(0, dict(id=lk, label=r["name"] if r else lk, cov=1.0, score=3.0, n=r["n"] if r else 0, laqab=True,
                          reason="لقب امام در سند شیعه", **({"mazhab": r["mazhab"], "grade": r["grade"]} if r else {})))
    cs.sort(key=lambda d: -d["score"])
    return cs[:10]


_NOOR_OF_KEY = {k: f"noor:{v['ravi_id']}" for k, v in _IMAMS.items() if v.get("ravi_id")}


def _derive_relatives(ch: dict) -> None:
    """Who «عن أبيه», «عن جده» ... is, from the narrator the relative is counted from.

    An Imam's father and grandfather come from the table of Imams; anyone else's from Noor's linked chains
    (``noor_relatives``). The person is taken automatically (``derived``) only when the narrator it is
    counted from is itself certain and Noor (nearly) always names the same person; otherwise the people
    Noor names are offered as candidates."""
    els = ch["elems"]
    idx = None
    for k, e in enumerate(els):
        rel = RELATION.get(e["name"]) if e["kind"] == "relative" else None
        if not rel or k == 0 or rel not in ("father", "grandfather", "brother", "uncle", "maternal_uncle", "mother", "son"):
            continue
        base = _beside1(els, k, -1)
        if base is None:
            continue
        if rel == "grandfather" and RELATION.get(relative_word(base["name"]) or "") == "father":
            rel = "father"                             # «عن أبيه عن جده»: the father of the father
        bc = [c for c in base["cands"][:3] if c.get("exact") or c.get("derived") or c.get("cov", 0) >= 0.88]
        if not bc:
            continue
        certain = bool(bc[0].get("exact") or bc[0].get("certain"))
        out: Dict[str, dict] = {k_["id"]: k_ for k_ in e["cands"]}
        for j, p in enumerate(bc):
            pid = p["id"]
            if not pid.startswith("noor:"):
                continue
            key = _ID2KEY.get(int(pid.split(":")[1]))
            anc = FATHER.get(key) if rel in ("father", "grandfather") else None
            if anc and rel == "grandfather":
                anc = FATHER.get(anc)
            if anc and anc in _NOOR_OF_KEY:
                out[_NOOR_OF_KEY[anc]] = dict(id=_NOOR_OF_KEY[anc], label=_IMAMS[anc]["canonical"], score=5.0 - j, cov=1.0,
                                              derived=j == 0 and certain, certain=j == 0 and certain,
                                              reason=f"{'پدرِ' if rel == 'father' else 'پدربزرگِ'} {p['label']} (جدول امامان)")
                continue
            got = noor_relatives().get((int(pid.split(":")[1]), rel), {})
            tot = sum(got.values())
            for r, n in sorted(got.items(), key=lambda x: -x[1])[:4]:
                idx = idx or noor_index()["ravis"]
                if r not in idx or f"noor:{r}" in out:
                    continue
                share = n / tot
                auto = j == 0 and certain and share >= 0.8 and n >= 3
                out[f"noor:{r}"] = dict(id=f"noor:{r}", label=idx[r]["name"], score=round(3.0 - j + share, 3), cov=round(share, 3),
                                        n=idx[r]["n"], mazhab=idx[r]["mazhab"], grade=idx[r]["grade"], derived=auto, certain=auto,
                                        reason=f"«{e['raw'] or e['name']}» پس از {p['label']} در {n} از {tot} سند نور")
        e["cands"] = sorted(out.values(), key=lambda d: -d["score"])


def _adjacent(pairs: Iterable[Tuple[str, str]]) -> set:
    """Set of (teacher_id, student_id) pairs attested in Noor's or the KG's chains."""
    pairs = list(set(pairs))
    out = set()
    noor = [(int(a.split(":")[1]), int(b.split(":")[1])) for a, b in pairs if a.startswith("noor:") and b.startswith("noor:")]
    kg = [(int(a.split(":")[1]), int(b.split(":")[1])) for a, b in pairs if a.startswith("kg:") and b.startswith("kg:")]
    if noor:
        c = _nconn()
        ids = sorted({x for p in noor for x in p})
        q = "select teacher, student from adjacency where n_exact >= 1 and teacher in (%s) and student in (%s)" % (",".join("?" * len(ids)), ",".join("?" * len(ids)))
        have = {(r[0], r[1]) for r in c.execute(q, ids + ids)}
        c.close()
        out |= {(f"noor:{a}", f"noor:{b}") for a, b in noor if (a, b) in have}
    if kg:
        c = corpus.conn("kg")
        ids = sorted({x for p in kg for x in p})
        q = "select teacher, student from kg_adjacency where teacher in (%s) and student in (%s)" % (",".join("?" * len(ids)), ",".join("?" * len(ids)))
        have = {(r[0], r[1]) for r in c.execute(q, ids + ids)}
        out |= {(f"kg:{a}", f"kg:{b}") for a, b in kg if (a, b) in have}
    return out


def _level(els: Sequence[dict], k: int) -> List[int]:
    """Indices of the places standing at the same level as place ``k``: itself, or its parallel narrators («A و B عن C», ``par``)."""
    g = els[k].get("par")
    return [k] if g is None else [j for j, e in enumerate(els) if e.get("par") == g]


def _beside(els: Sequence[dict], k: int, d: int) -> List[dict]:
    """The places of the level before (``d`` = -1: the teachers' side of the isnad's text order) or after (+1) place ``k``, all the
    members of a parallel level."""
    lv = _level(els, k)
    j = lv[0] - 1 if d < 0 else lv[-1] + 1
    return [els[x] for x in _level(els, j)] if 0 <= j < len(els) else []


def _beside1(els: Sequence[dict], k: int, d: int) -> Optional[dict]:
    """The nearest place of that level (the last member before, the first after), None at the end of the chain."""
    b = _beside(els, k, d)
    return (b[-1] if d < 0 else b[0]) if b else None


def _top(e: dict, n: int = 3) -> List[str]:
    """Neighbour candidates that may vouch for context: exact, derived, or a strong name match."""
    return [c["id"] for c in e["cands"][:n] if c.get("exact") or c.get("derived") or c.get("cov", 0) >= 0.88]


def _add_context(ch: dict) -> None:
    els = ch["elems"]
    pairs = []
    for k in range(len(els)):                        # els[k] is the student of every place of the level after it
        for t_el in _beside(els, k, +1):
            for s in _top(els[k]):
                for t in _top(t_el):
                    pairs.append((t, s))
    adj = _adjacent(pairs)
    for k, e in enumerate(els):
        before, after = _beside(els, k, -1), _beside(els, k, +1)
        for c in e["cands"]:
            ctx = 0
            if any((c["id"], s) in adj for b in before for s in _top(b)):
                ctx += 1
            if any((t, c["id"]) in adj for a in after for t in _top(a)):
                ctx += 1
            c["ctx"] = ctx
            if not c.get("exact"):
                c["score"] = round(c["score"] + 0.35 * min(ctx, 3), 3)
        e["cands"].sort(key=lambda d: -d["score"])
        for j, c in enumerate(e["cands"]):
            c["tier"] = tier(c, e["cands"][j + 1] if j + 1 < len(e["cands"]) else None, first=(j == 0))


def tier(c: dict, nxt: Optional[dict], first: bool) -> str:
    if c.get("exact"):
        return "exact"
    if c.get("laqab"):
        return "high"
    if c.get("sugg"):
        return "sugg"
    if c.get("form"):
        return "low"
    if c.get("hai"):                                   # measured leave-book-out: 99.7% (ctx3), 99.2% (one neighbour)
        t = "high" if first and c["hai"] == "ctx3" else "medium"
        own = tier(dict(c, hai=None), nxt, first)      # the name and Noor-context evidence may say more on its own
        return own if _TIER_RANK.get(own, 0) > _TIER_RANK[t] else t
    cov, ctx = c.get("cov", 0), c.get("ctx", 0)
    margin = (c["score"] - nxt["score"]) if nxt else 1.0
    if first and cov >= 0.88 and ctx >= 1 and margin >= 0.15:
        return "high"
    if cov >= 0.72 and ctx >= 1 or cov >= 0.6 and ctx >= 2:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# cards and decisions
# ---------------------------------------------------------------------------
def card_key(trad: str, e: dict) -> str:
    return f"{trad}:{' '.join(norm(e['name']).split())}"


_TIER_RANK = {"exact": 4, "auto": 3.5, "high": 3, "derived": 3, "sugg": 2.5, "medium": 2, "low": 1}


def make_cards(chains: Sequence[dict], trad: Dict[str, str]) -> Dict[str, dict]:
    """One card per (tradition, name): the places it occurs and its candidates merged over them; a relative
    («أبيه») is its own card in every chain.

    A candidate keeps its best score, context and tier over the places; the list is ordered by tier, then
    score, so a candidate that is ``high`` in one chain (its neighbours vouch for it) is not pushed under a
    looser name match that scored higher elsewhere (e.g. a collector matched against another database)."""
    cards: Dict[str, dict] = {}
    for ch in chains:
        t = trad.get(ch["wid"], "shia")
        for e in ch["elems"]:
            if e["exact"] or e["kind"] == "anon":
                continue
            key = card_key(t, e) + (f"@{ch['aid']}|{e['i']}" if e["kind"] == "relative" else "")
            e["card"] = key
            cd = cards.setdefault(key, dict(key=key, name=e["name"], kind=e["kind"], tradition=t, occurrences=[], candidates={}))
            cd["occurrences"].append([ch["aid"], e["i"]])
            if e.get("auto"):
                cd.setdefault("autos", {}).setdefault(e["auto"]["id"], []).append([ch["aid"], e["i"]])
            elif e.get("sugg"):
                cd.setdefault("suggs", {}).setdefault(e["sugg"]["id"], []).append([ch["aid"], e["i"]])
            for c in e["cands"]:
                old = cd["candidates"].get(c["id"])
                if old is None:
                    cd["candidates"][c["id"]] = dict(c)
                    continue
                best = dict(c) if c["score"] > old["score"] else old
                best["ctx"] = max(c.get("ctx", 0), old.get("ctx", 0))
                best["tier"] = max(c.get("tier"), old.get("tier"), key=lambda x: _TIER_RANK.get(x, 0))
                cd["candidates"][c["id"]] = best
    for cd in cards.values():
        cl = sorted(cd["candidates"].values(), key=lambda c: (-_TIER_RANK.get(c.get("tier"), 0), -c["score"]))[:8]
        cd["candidates"] = cl
        top = cl[0] if cl else None
        cd["default"] = top["id"] if top and (top.get("tier") == "high" or top.get("derived")) else None
        cd["tier"] = (top.get("tier") if top else None) or ("derived" if top and top.get("derived") else "none")
    return cards


def collapse(cands: List[dict], version) -> List[dict]:
    """One candidate per person of the narrator database version: the best (tier, then score) of the ids that
    version treats as one, under the group's canon id, with ``also`` naming the group's other ids. The list keeps
    the position of each group's best candidate. A candidate outside every group is left as it is."""
    canon = version.canon(c["id"] for c in cands)
    if not canon:
        return cands
    groups: Dict[str, List[Tuple[int, dict]]] = {}
    for k, c in enumerate(cands):
        groups.setdefault(canon.get(c["id"], c["id"]), []).append((k, c))
    out = []
    for cid, grp in groups.items():
        if cid not in canon.values():
            out.append(grp[0])
            continue
        k, best = max(grp, key=lambda kc: (_TIER_RANK.get(kc[1].get("tier"), 0), kc[1]["score"], -kc[0]))
        seen = {m["id"] for m in version.members(cid) if m["kind"] in ("noor", "kg")} | {c["id"] for _, c in grp}
        out.append((k, dict(best, id=cid, also=sorted(seen - {cid}))))
    out.sort(key=lambda kc: kc[0])
    return [c for _, c in out]


def _collapse_chains(chains: List[dict], version) -> None:
    """Express every element in the version's ids: candidates collapsed, an exact id canonicalised (the original
    stays in ``exact_src``)."""
    for ch in chains:
        for e in ch["elems"]:
            e["cands"] = collapse(e["cands"], version)
            if e["exact"]:
                c = version.canon([e["exact"]]).get(e["exact"])
                if c and c != e["exact"]:
                    e["exact_src"], e["exact"] = e["exact"], c
            for key in ("auto", "sugg"):
                if e.get(key):
                    c = version.canon([e[key]["id"]]).get(e[key]["id"])
                    if c and c != e[key]["id"]:
                        e[key] = dict(e[key], id=c, id_src=e[key]["id"])


def apply_renames(chains: Sequence[dict], renames: Optional[dict]) -> int:
    """The user's reading of a place (``"<aid>|<i>"`` -> ``{name, term}``): which words are the name and which the
    transmission phrase («قرأت على» + «أبو محمد»). The place keeps its index, so decisions made on it stay valid; a place
    the source fixed (``exact``) is left alone. Returns how many places changed."""
    n = 0
    for ch in chains:
        for e in ch["elems"]:
            r = (renames or {}).get(f"{ch['aid']}|{e['i']}")
            name = " ".join(norm(r.get("name", "")).split()) if r else ""
            if not name or e.get("exact"):
                continue
            e.update(name=name, raw=name, term=" ".join(norm(r.get("term", "")).split()) or None, renamed=True,
                     kind="collector" if e["kind"] == "collector" else "name")
            for k in ("hai", "alts", "amb"):                # tied to the words the source tagged, not to these
                e.pop(k, None)
            n += 1
    return n


# ---------------------------------------------------------------------------
# the chain in its text: where each place stands (token spans), and the user's reading of it (parts.chain)
# ---------------------------------------------------------------------------
ADDED_BASE = 1000                     # a place the user added is 1000 + its position in parts.chain[aid].add, for good
_EQUIV = {"ابن": "بن", "ابی": "ابو", "ابا": "ابو"}
_EDGE_PUNCT = re.compile(r"^[\s،,.:;؛؟?!«»()\[\]{}\"'\-–—/]+|[\s،,.:;؛؟?!«»()\[\]{}\"'\-–—/]+$")   # not \W: harakat are not word characters


def hadith_words(rec: dict):
    """(tokens, words, n_sanad): the whole text of a hadith as ``tokens(sanad) + tokens(matn)``, with the raw spelling
    of each token (the normalised token when several tokens come from one raw word), and how many are the sanad's."""
    toks: List[str] = []
    words: List[str] = []
    n_sanad = 0
    for seg in ("sanad", "matn"):
        s = strip_tags(rec.get(seg) or "")
        tk, offs = tokens(s), token_offsets(s)
        for j, t in enumerate(tk):
            o = offs[j] if j < len(offs) else None
            w = s[o[0]:o[1]] if o and not (j and offs[j - 1][0] == o[0]) else t
            words.append(_EDGE_PUNCT.sub("", w) or w)
        toks += tk
        if seg == "sanad":
            n_sanad = len(tk)
    return toks, words, n_sanad


def _akey(t: str) -> str:
    return _EQUIV.get(t, t)


def _find(keys: List[str], nm: List[str], start: int) -> Optional[int]:
    n = len(nm)
    for p in range(start, len(keys) - n + 1):
        if keys[p:p + n] == nm:
            return p
    return None


def anchor_elems(toks: List[str], elems: Sequence[dict]) -> Dict[int, Optional[dict]]:
    """``{i: {t, a, b} | None}``: where each place of a chain stands in ``toks`` (``hadith_words``), left to right, each
    after the one before: the name as written without honorifics (ابن≡بن, ابی/ابا≡ابو), the transmission phrase
    [t, a) in front of it (the source's words when they are there, else the transmission words directly before it). The collector (the book's author) is never looked for."""
    keys = [_akey(t) for t in toks]
    out: Dict[int, Optional[dict]] = {}
    cur = 0
    for e in elems:
        if e.get("role") == "collector" or e.get("kind") == "collector":
            out[e["i"]] = None
            continue
        nm = [_akey(t) for t in sanad_parse._strip_honorific(norm(e.get("raw") or e.get("name") or "").split())[0]]
        a = _find(keys, nm, cur) if nm else None
        if a is None:
            out[e["i"]] = None
            continue
        b = a + len(nm)
        tm = [_akey(t) for t in norm(e.get("term") or "").split()]
        if tm and a - len(tm) >= cur and keys[a - len(tm):a] == tm:
            t = a - len(tm)
        else:                                                              # the source gave no phrase (KG): the transmission words in front of the name
            t = a
            while t > cur and a - t < 4 and sanad_parse.is_transmit(toks[t - 2] if t >= 2 else None, toks[t - 1]):
                t -= 1
        out[e["i"]] = dict(t=t, a=a, b=b)
        cur = b
    return out


def _span_ok(s, top: int) -> bool:
    return (isinstance(s, dict) and all(isinstance(s.get(k), int) and not isinstance(s.get(k), bool) for k in ("t", "a", "b"))
            and 0 <= s["t"] <= s["a"] < s["b"] <= top)


def merge_drops(chain_edits: Optional[dict], drops: Optional[Sequence[str]]) -> Optional[dict]:
    """``chain_edits`` (parts.chain) with the places removed on step 4's main page (identities.drops, ``"<aid>|<i>"``)
    added to their chain's ``drop``: one meaning for a removal wherever it was made, the place leaves the chain."""
    out = {aid: dict(ed) for aid, ed in (chain_edits or {}).items()}
    for occ in drops or ():
        aid, _, i = str(occ).rpartition("|")
        if aid and i.isdigit():
            ed = out.setdefault(aid, {})
            ed["drop"] = sorted(set(ed.get("drop") or []) | {int(i)})
    return out or None


def fill_terms(elems: Sequence[dict], toks: List[str], words: List[str], n_sanad: int, pins: dict) -> None:
    """The transmission phrase of each placed name: the words between it and the name before it in the text (never
    reaching back over the start of the matn; a blessing such as «رضي الله عنه» right after the name before belongs to
    that name, so the phrase then starts at the first transmission word), or the user's choice among those words
    (``pins`` {i: [t, e]}: words [t, e), t == e: no phrase). A choice no longer inside them (a name moved, added or
    removed) falls back to the default."""
    prev = 0
    for e in sorted((e for e in elems if e.get("span") and e.get("role") != "collector"), key=lambda e: e["span"]["a"]):
        a = e["span"]["a"]
        lo = n_sanad if prev < n_sanad <= a else prev
        p = pins.get(str(e["i"]), pins.get(e["i"]))
        if isinstance(p, (list, tuple)) and len(p) == 2 and lo <= p[0] <= p[1] <= a:
            t, te = p
        else:
            t, te = lo, a
            if t < a and toks[t] in sanad_parse.HONORIFIC:
                t = next((j for j in range(t + 1, a) if sanad_parse.is_transmit(toks[j - 1], toks[j])), t)
        e["span"] = dict(e["span"], t=t, **({"te": te} if te != a else {}))
        e["term"] = " ".join(norm(" ".join(words[t:te])).split()) or None
        prev = e["span"]["b"]


def apply_chain_edits(chains: Sequence[dict], texts: Dict[str, tuple], edits: Optional[dict]) -> int:
    """The user's reading of a chain in its text (``parts.chain``, keyed by chain ``aid``): ``span`` ``{i: {t, a, b}}``
    moves the boundaries of a place (transmission phrase [t, a), name [a, b), in the tokens of ``hadith_words``);
    ``add`` ``[{t, a, b}]`` are places the user added (the k-th is place ``1000 + k``, for good); ``drop`` ``[i]``
    removes places, ``term`` ``{i: [t, e]}`` is the user's choice of a place's transmission phrase (``fill_terms``: by
    default every word between it and the name before it). Place ids never change, so decisions keyed by ``aid|i`` stay valid; the chain is reordered by
    position in the text (a place not found in the text stays behind the one before it). A place whose words the
    user changed loses the source's link (kept as ``exact_was``). The collector is left alone. ``texts`` maps wid ->
    (tokens, words). Every place keeps its automatic reading in ``span_auto`` (what «back to automatic» would give).
    No two places share a word: an unedited place whose automatic words a user span now covers is left unplaced
    (``span`` None, behind the place before it) and the chain is flagged ``chain_overlap``. Returns how many places changed."""
    n = 0
    for ch in chains:
        ed = (edits or {}).get(ch["aid"])
        if not ed or ch["wid"] not in texts:
            continue
        tw = texts[ch["wid"]]
        toks, words, n_sanad = tw[0], tw[1], (tw[2] if len(tw) > 2 else 0)
        spans = anchor_elems(toks, ch["elems"])
        for e in ch["elems"]:
            e["span"] = e["span_auto"] = spans.get(e["i"])

        def set_words(e: dict, s: dict):
            raw = " ".join(words[s["a"]:s["b"]])
            e.update(raw=raw, name=" ".join(norm(raw).split()), term=" ".join(norm(" ".join(words[s["t"]:s["a"]])).split()) or None,
                     span=dict(t=s["t"], a=s["a"], b=s["b"]), renamed=True, kind="collector" if e.get("kind") == "collector" else "name",
                     imam_hint=s["b"] < len(toks) and toks[s["b"]] in sanad_parse.IMAM_HON)
            if e.get("exact"):
                e["exact_was"], e["exact"] = e["exact"], None
            for k in ("hai", "alts", "amb"):
                e.pop(k, None)

        by_i = {e["i"]: e for e in ch["elems"]}
        edited = set()
        for i, s in (ed.get("span") or {}).items():
            e = by_i.get(int(i))
            if e is None or e.get("role") == "collector" or not _span_ok(s, len(toks)):
                continue
            set_words(e, s)
            edited.add(e["i"])
            n += 1
        added = []
        for k, s in enumerate(ed.get("add") or []):
            if not _span_ok(s, len(toks)):
                continue
            e = _elem(ADDED_BASE + k, "", None, "name", added=True)
            e["span_auto"] = None
            set_words(e, s)
            added.append(e)
            n += 1
        drop = {int(i) for i in (ed.get("drop") or [])}
        orig = {id(e): k for k, e in enumerate(ch["elems"])}
        keep = [e for e in ch["elems"] if e["i"] not in drop or e.get("role") == "collector"]
        n += len(ch["elems"]) - len(keep)
        added = [e for e in added if e["i"] not in drop]
        # no two places share a word: the user's spans win over the automatic reading of a place they did not touch
        mine = [e["span"] for e in keep + added if e["i"] in edited or e.get("added")]
        for e in keep:
            sp = e.get("span")
            if sp and e["i"] not in edited and e.get("role") != "collector" and any(sp["a"] < u["b"] and u["a"] < sp["b"] for u in mine):
                e["span"] = None
                if "chain_overlap" not in ch["flags"]:
                    ch["flags"].append("chain_overlap")
        pos: Dict[int, float] = {}
        last = -1
        for e in keep:
            if e.get("role") == "collector":
                pos[id(e)] = -2
                continue
            if e.get("span"):
                last = e["span"]["a"]
            pos[id(e)] = last
        for e in added:
            pos[id(e)] = e["span"]["a"]
        if added or ed.get("span"):                    # a removal alone leaves the order as the source gave it
            ch["elems"] = sorted(keep + added, key=lambda e: (pos[id(e)], 1 if e.get("added") else 0, orig.get(id(e), 10 ** 9)))
        else:
            ch["elems"] = keep
        fill_terms(ch["elems"], toks, words, n_sanad, ed.get("term") or {})
        if ch.get("speaker"):
            ch["speaker"]["elem"] = None                   # written by the parser, read nowhere; stale after a reorder
        if len(ch["elems"]) < 2 and not ch.get("speaker") and "no_sanad" not in ch["flags"]:
            ch["flags"].append("no_sanad")
    return n


GENERIC_FIRST = {names.written_key(x) for x in sanad_parse._DATA["generic_first"]}
MATN_OPEN = {norm(x) for x in sanad_parse._DATA["matn_open"]}
ASK_WORDS = {norm(x) for x in ("سئل", "وسئل", "سأل", "سألت", "سألته", "وسألت", "فسألت", "سألنا")}      # the isnad turns into a question


def _asks(e: dict) -> bool:
    """The place is the one asked: «سئل» or «سألت» stands in its words or in the transmission phrase before it."""
    return bool(ASK_WORDS & set(norm(f"{e.get('raw') or ''} {e.get('term') or ''}").split()))


def _without_wording(elems: List[dict]) -> List[dict]:
    """The places without isnad wording read as narrators: words Noor kept between two narrators that are no name
    («قال سئل»: they stay in the link's phrase), and what follows the one asked («سئل أبو عبد الله عن الخمر فقال»): «عن»
    there introduces the question's subject, not a narrator. A place with a database id is never removed."""
    out, asked, imam = [], False, False
    for e in elems:
        free = not e["exact"] and e.get("role") != "collector"
        if free and e.get("hidden") and sanad_parse.not_a_name(e["name"]):
            continue
        w = norm(e["name"]).split()
        if free and imam and norm(e.get("term") or "").split()[:1] in (["قال"], ["وقال"], ["فقال"]) and w and (w[0] in MATN_OPEN or w[0] in ("وقال", "فقال", "قال")):
            continue                       # «عن أبي عبد الله قال ما بعث الله …»: the Imam's words, not a narrator (several Imams may follow one another)
        imam = bool(w) and e.get("role") != "collector" and (sanad_parse._starts_with(w, sanad_parse.IMAM_KUNYA) or bool(e.get("imam_hint")))
        if free and asked and norm(e.get("term") or "").split()[:1] == ["عن"]:
            asked = False
            continue
        asked = _asks(e) and not sanad_parse.not_a_name(e["name"])
        out.append(e)
    return out


BACKREF = set(sanad_parse.GAPS_STRIP) | {norm(x) for x in sanad_parse._DATA["back_ref"]}


def _without_backref(ch: dict) -> int:
    """«وبه قال: حدثنا …», «وبهذا الإسناد», «وعنه» as the first words of an isnad point to the isnad of the hadith before it in the book:
    they name nobody. The place is taken out (its number stays unused, as for any removal) and the chain says it begins by pointing back
    (flag ``gap:…``, as the parser does for «بهذا الاسناد»). A place with a database id is never taken out. Returns 1 when one was."""
    for k, e in enumerate(ch["elems"]):
        if e.get("role") == "collector" or e.get("kind") == "collector":
            continue
        if e["exact"] or e["kind"] != "name" or norm(e["name"]) not in BACKREF:
            return 0
        del ch["elems"][k]
        ch["flags"] = list(ch.get("flags") or []) + [f"gap:{e['raw']}"]
        return 1
    return 0


PAR_BASE = 500                       # the k-th further name of a place that held several is 500 + 8 × place + k − 1: below ADDED_BASE, stable


REPORT_BASE = PAR_BASE + 7             # the place a report names («… أن X حدثهم») is 500 + 8 × place + 7: the parallel split's own numbers stop at +5
REPORT_VERBS = {norm(x) for x in sanad_parse._DATA["report_verbs"]}


_ACCUSATIVE = {"ابا": "ابو", "ابنا": "ابن", "اباه": "ابیه", "اخاه": "اخیه"}


def _after_an(toks: Sequence[str]) -> List[str]:
    """The words after «أن» are in the accusative: «ابا هريرة» is «ابو هريره», «مالكا» is «مالك» (when the shorter word is the name the
    databases write), «اباه» is «ابیه» (his father)."""
    v = names.vocab()
    out = []
    for t in toks:
        if t in _ACCUSATIVE:
            t = _ACCUSATIVE[t]
        elif v and len(t) >= 4 and t.endswith("ا") and v.get(names.written_key(t[:-1]), 0) > 3 * v.get(names.written_key(t), 0):
            t = t[:-1]
        out.append(t)
    return out


def _is_nameish(toks: Sequence[str]) -> bool:
    """At most one word that no narrator's written name has (the vocabulary is empty without the names file: then no test)."""
    v = names.vocab()
    return not v or sum(1 for t in toks if v.get(names.written_key(t), 0) < 3) <= (1 if len(toks) > 1 else 0)


def _split_reported(chains: Sequence[dict], wits: Sequence[dict]) -> int:
    """«قرأت على أحمد بن قاسم أن قاسم بن اصبغ حدثهم»: A says that B told them, so B is the next narrator, A's teacher. The parser keeps
    «A أن B» as one place (the words «حدثهم» are left out of it). It is split in two when the words after the place are an isnad verb
    of that kind (a plain «قال» or «كان» is the story's, not the isnad's: «أن عمر سأل النبي» stays as it is). A keeps the place's
    number, B is a new place 500 + 8 × place + 7 with the phrase «ان». Places with a database id, the user's own readings and the
    places already split are left alone. Returns the number of places made."""
    made = 0
    recs = {w["wid"]: w for w in wits}
    for ch in chains:
        if ch["wid"] not in recs or not any(e["kind"] == "name" and not e["exact"] and "ان" in (e.get("name") or "").split()[1:-1] for e in ch["elems"]):
            continue
        tw = hadith_words(recs[ch["wid"]])
        spans = anchor_elems(tw[0], ch["elems"])
        out = []
        for e in ch["elems"]:
            sp = spans.get(e["i"])
            t = (e.get("name") or "").split()
            k = t.index("ان", 1) if "ان" in t[1:-1] else 0
            left, right = " ".join(t[:k]), " ".join(_after_an(t[k + 1:]))
            ok = (k and sp and not e["exact"] and e.get("role") != "collector" and e["kind"] == "name" and not e.get("renamed") and not e.get("added")
                  and not e.get("par") and sp["b"] < len(tw[0]) and tw[0][sp["b"]] in REPORT_VERBS
                  and REPORT_BASE + 8 * e["i"] < ADDED_BASE
                  and sanad_parse.strip_nonname(left)["core"] == left and sanad_parse.strip_nonname(right)["core"] == right
                  and not sanad_parse.not_a_name(left) and not sanad_parse.not_a_name(right) and "ان" not in right.split()
                  and len(right.split()) <= 8 and _is_nameish(left.split()) and _is_nameish(right.split()))
            if not ok:
                out.append(e)
                continue
            out.append(dict(e, name=left, raw=" ".join(e["raw"].split()[:k]) if len(e["raw"].split()) == len(t) else left, cands=[],
                            kind="anon" if sanad_parse.is_anon(left) else "name", **{"report_of": REPORT_BASE + 8 * e["i"]}))
            out.append(dict(e, name=right, raw=right, cands=[], i=REPORT_BASE + 8 * e["i"], term="ان", report_from=e["i"],
                            kind="anon" if sanad_parse.is_anon(right) else "name"))
            for x in out[-2:]:
                for key in ("clean", "parts", "frag", "span", "span_auto"):
                    x.pop(key, None)
            made += 1
        ch["elems"] = out
    return made


def _split_parallel(chains: Sequence[dict]) -> int:
    """«ابيه ومحمد بن يحيى» is two places, narrators at the same level (``par`` = the first one's place number on all of them): each
    its own name card, its own person; the isnad's other places see the level as one (``_beside``) and the graph makes one chain
    of every combination. The first name keeps the place's number, so decisions made on the place stay with it. Places with a
    database id are never split. Returns the number of places made."""
    made = 0
    for ch in chains:
        out = []
        for e in ch["elems"]:
            parts = clean_parts(e["name"]) if (not e["exact"] and e.get("role") != "collector" and e["kind"] == "name"
                                               and not e.get("par") and not e.get("added")) else []
            if len(parts) < 2 or len(parts) > 7 or PAR_BASE + 8 * e["i"] + len(parts) - 1 >= ADDED_BASE:
                out.append(e)
                continue
            for j, part in enumerate(parts):
                x = dict(e, name=part, raw=part, cands=[], par=e["i"], term=e["term"] if j == 0 else "و",
                         i=e["i"] if j == 0 else PAR_BASE + 8 * e["i"] + j - 1,
                         kind="anon" if sanad_parse.is_anon(part) or sanad_parse.is_anon_group(part) else "name")
                for k in ("clean", "parts", "frag", "span", "span_auto"):
                    x.pop(k, None)
                out.append(x)
            made += len(parts) - 1
        ch["elems"] = out
    return made


def build(wits: Sequence[dict], version=None, renames: Optional[dict] = None, chain_edits: Optional[dict] = None) -> dict:
    """identities.json: chains with candidates, and the name cards to decide.

    ``wits`` are witness records (``corpus.get``) of the selected witnesses (copies excluded). ``version`` is a
    ``narrator_versions.Version`` (None: raw, every id apart). ``renames`` are the user's corrections of where a name
    begins and ends in a place (``apply_renames``); ``chain_edits`` their reading of whole chains in the text
    (``apply_chain_edits``), which wins over a rename of the same place."""
    version = version or narrator_versions.RAW
    chains: List[dict] = []
    trad = {w["wid"]: (w.get("tradition") or ("sunni" if w["corpus"] == "kg" else "shia")) for w in wits}
    flags: List[dict] = []
    for w in wits:
        ch = chains_for(w)
        if not ch:
            flags.append(dict(kind="no_chain", ref=w["wid"], message="no isnad could be read for this witness"))
        chains.extend(ch)
    for ch in chains:                                  # Places keep their numbers, as with a removal in the chain editor.
        ch["elems"] = _without_wording(ch["elems"])
        _without_backref(ch)
    if chain_edits:
        wids = {aid.split("#")[0] for aid in chain_edits}
        apply_chain_edits(chains, {w["wid"]: hadith_words(w) for w in wits if w["wid"] in wids}, chain_edits)
        moved = {f"{aid}|{i}" for aid, ed in chain_edits.items() for i in (ed.get("span") or {})}
        renames = {k: v for k, v in (renames or {}).items() if k not in moved}
    apply_renames(chains, renames)
    _split_reported(chains, wits)
    _split_parallel(chains)
    for ch in chains:                                  # «أبيه عليه السلام» in Noor's slots, «حدثني أبي» ...
        for e in ch["elems"]:
            if not e["exact"] and e["kind"] == "name" and sanad_parse.is_anon_group(e["name"]):
                e["kind"] = "anon"                                 # «العدة» (the Kafi's abbreviation): an unnamed group
            if not e["exact"] and e["kind"] == "name" and relative_word(e["name"]):
                e["kind"], e["name"] = "relative", relative_word(e["name"])
    attach_candidates(chains, trad)
    n_auto = auto_links(chains, trad, wits)
    _collapse_chains(chains, version)
    for ch in chains:
        ch["tradition"] = trad.get(ch["wid"]) or ("sunni" if ch["source"] == "kg" else "shia")
    cards = make_cards(chains, trad)
    n_veto = _veto_defaults(cards, trad)
    n_exact = sum(1 for ch in chains for e in ch["elems"] if e["exact"])
    n_el = sum(len(ch["elems"]) for ch in chains)
    return dict(chains=chains, cards=cards, flags=flags,
                stats=dict(chains=len(chains), elements=n_el, exact=n_exact, cards=len(cards),
                           undecided_by_default=sum(1 for c in cards.values() if not c["default"]), auto=n_auto, vetoed=n_veto,
                           sugg=sum(1 for ch in chains for e in ch["elems"] if e.get("sugg"))))


def resolve(ident: dict, decisions: Optional[dict] = None, version=None) -> dict:
    """Apply the user's choices. Returns chains of node ids plus the node table.

    decisions = {choices: {card_key: "noor:12" | "kg:7" | "none"}, merges: [[nid, nid]], overrides: {"aid|i": nid},
                 drops: ["aid|i"], splits: {"split:1": {label, from}}}
    Merges may name any node (noor:/kg: people, name:/rel:/anon: places with no person, split: people). A split
    person is a set of places the user moved off an identity (``overrides[place] = "split:<n>"``); ``splits`` only
    labels them.
    A card without a choice takes its default (a ``high`` candidate), else becomes a name-only node
    and is listed in ``undecided``. A dropped place is no narrator (isnad wording read as a name): it stays in
    the chain with ``dropped`` and no node. Merged ids are one person, named by the Noor id when there is one.
    The narrator database ``version`` decides which Noor/KG ids are one person (``same``: id -> canon id); a node's
    ``ext`` lists every id of its person, which is where the graph looks facts up."""
    version = version or narrator_versions.RAW
    d = decisions or {}
    choices, overrides = d.get("choices", {}), d.get("overrides", {})
    drops = set(d.get("drops", []))
    approved = d.get("approved", {}) or {}                  # place -> the person the user confirmed for an automatic link
    splits = d.get("splits", {}) or {}
    cards = ident["cards"]
    nodes: Dict[str, dict] = {}
    chains_out = []
    undecided = set()
    all_ids = [c["id"] for ch in ident["chains"] for e in ch["elems"] for c in e["cands"][:1]]
    all_ids += [v for v in choices.values() if isinstance(v, str)]
    all_ids += [v for v in overrides.values() if isinstance(v, str)] + [x for m in d.get("merges", []) for x in m]
    all_ids += [v for v in approved.values() if isinstance(v, str)]
    all_ids += list((d.get("dates") or {}))
    same = version.canon(all_ids)
    # the user's merges are taken after the version's mapping, so a merge naming any member id still applies
    canon = _union([[same.get(a, a), same.get(b, b)] for a, b in d.get("merges", [])])
    for ch in ident["chains"]:
        out = []
        for e in ch["elems"]:
            occ = f"{ch['aid']}|{e['i']}"
            tier = None
            if occ in drops:
                out.append(dict(i=e["i"], nid=None, tier="dropped", name=e["name"], collector=e.get("role") == "collector", dropped=True,
                                **({"par": e["par"]} if e.get("par") is not None else {}), **({"rep": 1} if e.get("report_from") else {})))
                continue
            if occ in overrides:
                v = overrides[occ]
                nid, tier = (None, "name") if v in (None, "none") else (v, "chosen")
            elif e["exact"]:
                nid, tier = e["exact"], "exact"
            elif e["kind"] == "anon":
                nid, tier = f"anon:{ch['aid']}:{e['i']}", "anon"
            else:
                cd = cards.get(e.get("card"))
                if occ in approved and (e.get("auto") or e.get("sugg")):
                    nid, tier = approved[occ], "chosen"
                elif e["kind"] == "relative" and e["cands"] and e["cands"][0].get("derived") and e.get("card") not in choices:
                    nid, tier = e["cands"][0]["id"], "derived"
                elif cd is not None and cd["key"] in choices:
                    v = choices[cd["key"]]
                    nid, tier = (None, "name") if v in (None, "none") else (v, "chosen")
                elif e.get("auto"):                           # the middle level: used, and waiting for the user's approval
                    nid, tier = e["auto"]["id"], "default"
                elif cd is not None and cd["default"]:
                    nid, tier = cd["default"], "default"
                else:
                    nid, tier = None, "name"
                    if cd is not None:
                        undecided.add(cd["key"])
            if nid is None:
                nid = (f"rel:{ch['aid']}:{e['i']}" if e["kind"] == "relative"
                       else f"name:{' '.join(norm(e['name']).split())}")      # = node_name (anon places have their id)
            src = nid
            nid = same.get(nid, nid)
            nid = canon.get(nid, nid)
            node = nodes.setdefault(nid, dict(id=nid, names=[], tiers=set(), n=0, ext=[]))
            for x in (nid, src):
                for m in _ext_ids(x, version):
                    if m not in node["ext"]:
                        node["ext"].append(m)
            if nid.startswith("split:") and "label" not in node:
                node["label"] = str((splits.get(nid) or {}).get("label") or e["name"])
            if e["name"] not in node["names"]:
                node["names"].append(e["name"])
            node["tiers"].add(tier)
            node["n"] += 1
            out.append(dict(i=e["i"], nid=nid, tier=tier, name=e["name"], collector=e.get("role") == "collector",
                            **({"par": e["par"]} if e.get("par") is not None else {}), **({"rep": 1} if e.get("report_from") else {}),
                            **({"auto": e["auto"]["rule"]} if tier == "default" and e.get("auto") and nid == e["auto"]["id"] else {})))
        chains_out.append(dict(aid=ch["aid"], wid=ch["wid"], source=ch["source"], elems=out, speaker=ch["speaker"],
                               quoted_work=ch["quoted_work"], flags=ch["flags"]))
    for n in nodes.values():
        n["tiers"] = sorted(n["tiers"])
        if n.get("label") and n["label"] in n["names"]:     # a split person is named by the user's label
            n["names"].remove(n["label"])
        if n.get("label"):
            n["names"].insert(0, n["label"])
    alias = dict(same)
    alias.update({a: canon.get(b, b) for a, b in same.items()})
    alias.update(canon)
    return dict(chains=chains_out, nodes=nodes, undecided=sorted(undecided), same_person=same, alias=alias,
                dropped=sorted(drops & {f"{ch['aid']}|{e['i']}" for ch in ident["chains"] for e in ch["elems"]}))


def _ext_ids(nid: str, version) -> List[str]:
    """Every database id of the person ``nid`` stands for (itself, for an id the version does not merge)."""
    mem = [m["id"] for m in version.members(nid)]
    return mem or ([nid] if nid.startswith(("noor:", "kg:", "wiki:")) else [])


def node_name(ch: dict, e: dict) -> str:
    """The node id a place resolves to when no person is chosen for it (what ``resolve`` falls back to)."""
    if e.get("kind") == "anon":
        return f"anon:{ch['aid']}:{e['i']}"
    if e.get("kind") == "relative":
        return f"rel:{ch['aid']}:{e['i']}"
    return f"name:{' '.join(norm(e['name']).split())}"


def with_node_ids(ident: dict) -> dict:
    """Add ``node_name`` to every element (for display, not stored): the step-4 page merges and splits
    name-only people by these ids and must not recompute ``norm`` in JavaScript."""
    for ch in ident.get("chains", []):
        for e in ch["elems"]:
            e["node_name"] = node_name(ch, e)
    return ident


def _union(pairs) -> Dict[str, str]:
    """id -> the id its group is known by (Noor id first, then kg, a split person, a name, ...) for the user's merges."""
    up: Dict[str, str] = {}

    def find(x):
        while up.setdefault(x, x) != x:
            x = up[x]
        return x
    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            up[ra] = rb
    groups: Dict[str, List[str]] = defaultdict(list)
    for x in list(up):
        groups[find(x)].append(x)
    out = {}
    rank = {"noor": 0, "kg": 1, "wiki": 1.5, "split": 2, "name": 3, "rel": 4, "anon": 5}
    for g in groups.values():
        name = min(g, key=lambda x: (rank.get(x.split(":")[0], 6), x))
        out.update({x: name for x in g if x != name})
    return out


def approved_pins(ident: dict, approved: Optional[dict]) -> dict:
    """The approvals the graph has to know: those naming another person than the place's automatic link, and every approval of a
    suggestion (below the middle level: used nowhere until it is approved). An approval of the automatic link itself changes nothing in
    the tree, so it is not part of the graph's key; one the model has since left behind is."""
    auto = {f"{ch['aid']}|{e['i']}": e["auto"]["id"] for ch in ident.get("chains", []) for e in ch["elems"] if e.get("auto")}
    sugg = {f"{ch['aid']}|{e['i']}" for ch in ident.get("chains", []) for e in ch["elems"] if e.get("sugg") and not e.get("auto")}
    return {k: v for k, v in (approved or {}).items() if (k in auto and auto[k] != v) or k in sugg}      # (a suggestion used nowhere until approved: it changes the tree)
