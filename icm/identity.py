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
from . import config, corpus, hadithai, narrator_versions, sanad_parse
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


def _cov(q: List[str], c: List[str]) -> float:
    """Share of the query's name tokens found in the candidate, with the father/son guard."""
    if not q or not c:
        return 0.0
    cs = set(c)
    inter = [t for t in q if t in cs]
    cov = len(inter) / len(q)
    if q[0] not in cs:
        return 0.0
    if c[0] != q[0]:
        cov *= 0.4                                  # the query's first name is only the candidate's father etc.
    cov -= 0.01 * len(cs - set(q))
    return max(cov, 0.0)


def noor_candidates(name: str, imam_hint: bool = False, k: int = 10) -> List[dict]:
    q = name_tokens(name)
    if not q:
        return []
    idx = noor_index()
    rare = sorted(set(q), key=lambda t: len(idx["post"].get(t, ())))[:2]
    ids = set()
    for t in rare:
        ids.update(idx["post"].get(t, ()))
    out = []
    for rid in ids:
        r = idx["ravis"][rid]
        cov = max([_cov(q, r["toks"])] + [_cov(q, a) for a in r["alts"]])
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
            cov = _cov(q, toks)
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
    """Noor's linked isnads, then the KG's, then hadith.ai's narrator tags, then the rule-based parse of the text."""
    if rec["corpus"] == "kg":
        ch = kg_chains(rec)
        if ch:
            return ch
    elif rec["hid"] >= config.NOOR_OFFSET:
        ch = noor_chains(rec)
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
            else:
                e["cands"] = match(e["name"], trad, e["imam_hint"], e.get("death"), e["kind"] == "collector")
        _derive_relatives(ch)
        for e in ch["elems"]:
            _hai_candidate(e)
        _add_context(ch)


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


def match(name: str, trad: str, imam_hint: bool = False, death=None, collector: bool = False) -> List[dict]:
    """Candidate persons for a name in a chain of this tradition: the KG for Sunni chains (Noor too for an Imam),
    Noor for Shia chains (the KG too for a collector)."""
    if trad == "sunni":
        cs = kg_candidates(name, death) + (noor_candidates(name, imam_hint, 4) if imam_hint else [])
    else:
        cs = noor_candidates(name, imam_hint) + (kg_candidates(name, death, 3) if collector else [])
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
        base = els[k - 1]
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


def _top(e: dict, n: int = 3) -> List[str]:
    """Neighbour candidates that may vouch for context: exact, derived, or a strong name match."""
    return [c["id"] for c in e["cands"][:n] if c.get("exact") or c.get("derived") or c.get("cov", 0) >= 0.88]


def _add_context(ch: dict) -> None:
    els = ch["elems"]
    pairs = []
    for k in range(len(els) - 1):                    # els[k] is the student of els[k+1]
        for s in _top(els[k]):
            for t in _top(els[k + 1]):
                pairs.append((t, s))
    adj = _adjacent(pairs)
    for k, e in enumerate(els):
        for c in e["cands"]:
            ctx = 0
            if k > 0 and any((c["id"], s) in adj for s in _top(els[k - 1])):
                ctx += 1
            if k + 1 < len(els) and any((t, c["id"]) in adj for t in _top(els[k + 1])):
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


_TIER_RANK = {"exact": 4, "high": 3, "derived": 3, "medium": 2, "low": 1}


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
    if chain_edits:
        wids = {aid.split("#")[0] for aid in chain_edits}
        apply_chain_edits(chains, {w["wid"]: hadith_words(w) for w in wits if w["wid"] in wids}, chain_edits)
        moved = {f"{aid}|{i}" for aid, ed in chain_edits.items() for i in (ed.get("span") or {})}
        renames = {k: v for k, v in (renames or {}).items() if k not in moved}
    apply_renames(chains, renames)
    for ch in chains:                                  # «أبيه عليه السلام» in Noor's slots, «حدثني أبي» ...
        for e in ch["elems"]:
            if not e["exact"] and e["kind"] == "name" and relative_word(e["name"]):
                e["kind"], e["name"] = "relative", relative_word(e["name"])
    attach_candidates(chains, trad)
    _collapse_chains(chains, version)
    for ch in chains:
        ch["tradition"] = trad.get(ch["wid"]) or ("sunni" if ch["source"] == "kg" else "shia")
    cards = make_cards(chains, trad)
    n_exact = sum(1 for ch in chains for e in ch["elems"] if e["exact"])
    n_el = sum(len(ch["elems"]) for ch in chains)
    return dict(chains=chains, cards=cards, flags=flags,
                stats=dict(chains=len(chains), elements=n_el, exact=n_exact, cards=len(cards),
                           undecided_by_default=sum(1 for c in cards.values() if not c["default"])))


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
    splits = d.get("splits", {}) or {}
    cards = ident["cards"]
    nodes: Dict[str, dict] = {}
    chains_out = []
    undecided = set()
    all_ids = [c["id"] for ch in ident["chains"] for e in ch["elems"] for c in e["cands"][:1]]
    all_ids += [v for v in choices.values() if isinstance(v, str)]
    all_ids += [v for v in overrides.values() if isinstance(v, str)] + [x for m in d.get("merges", []) for x in m]
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
                out.append(dict(i=e["i"], nid=None, tier="dropped", name=e["name"], collector=e.get("role") == "collector", dropped=True))
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
                if e["kind"] == "relative" and e["cands"] and e["cands"][0].get("derived") and e.get("card") not in choices:
                    nid, tier = e["cands"][0]["id"], "derived"
                elif cd is not None and cd["key"] in choices:
                    v = choices[cd["key"]]
                    nid, tier = (None, "name") if v in (None, "none") else (v, "chosen")
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
            out.append(dict(i=e["i"], nid=nid, tier=tier, name=e["name"], collector=e.get("role") == "collector"))
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
    return mem or ([nid] if nid.startswith(("noor:", "kg:")) else [])


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
    rank = {"noor": 0, "kg": 1, "split": 2, "name": 3, "rel": 4, "anon": 5}
    for g in groups.values():
        name = min(g, key=lambda x: (rank.get(x.split(":")[0], 6), x))
        out.update({x: name for x in g if x != name})
    return out
