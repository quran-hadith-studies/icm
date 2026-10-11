"""Step 4: who are the narrators? Candidates for every name in every chain.

For each selected witness the chains are assembled (collector first, as recorded):

  noor_slots  Noor's own parsed isnad (``narrators.sqlite`` slot rows), with the words
              between slots re-parsed so that narrators Noor folded into a connective
              («عن أبيه ع عن علي بن الحسين ع») reappear as elements
  kg          the Hadith Knowledge Graph's chains (narrator ids)
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
from . import config, corpus, sanad_parse
from .text import norm

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
        ravis, post = {}, defaultdict(list)
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
        # every title under which a narrator's slot appeared is another form of his name
        for rid, title, n in c.execute("select ravi_id, title, count(*) from slot where ravi_id is not null and title is not null "
                                       "group by ravi_id, title"):
            r = ravis.get(rid)
            if r is None:
                continue
            toks = name_tokens(title)
            if toks and toks != r["toks"] and len(r["alts"]) < 30:
                r["alts"].append(toks)
                for t in set(toks):
                    if rid not in post[t]:
                        post[t].append(rid)
        _noor = dict(ravis=ravis, post=post)
        c.close()
    return _noor


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


def kg_chains(rec: dict) -> List[dict]:
    c = corpus.conn("kg")
    sids = [r[0] for r in c.execute("select id from sanads where hadith_id=? order by id", (rec["hid"],))]
    chains = []
    for sid in sids:
        rows = c.execute("select sr.pos, sr.rawi_id, r.name from sanad_rawis sr join rawis r on r.id=sr.rawi_id "
                         "where sr.sanad_id=? order by sr.pos", (sid,)).fetchall()
        if not rows:
            continue
        elems = [_elem(k, r["name"], None, exact=f"kg:{r['rawi_id']}") for k, r in enumerate(rows)]
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


def chains_for(rec: dict) -> List[dict]:
    if rec["corpus"] == "kg":
        ch = kg_chains(rec)
        if ch:
            return ch
    elif rec["hid"] >= config.NOOR_OFFSET:
        ch = noor_chains(rec)
        if ch:
            return ch
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
            elif e["kind"] == "relative":
                e["cands"] = []                             # resolved from the previous element after decisions
            elif e.get("amb"):
                idx = noor_index()["ravis"]
                e["cands"] = [dict(id=i, label=idx[int(i.split(":")[1])]["name"], score=1.0, cov=1.0, n=idx[int(i.split(":")[1])]["n"])
                              for i in e["amb"]]
            else:
                cs = []
                death = e.get("death")
                if trad == "sunni":
                    cs = kg_candidates(e["name"], death) + (noor_candidates(e["name"], e["imam_hint"], 4) if e["imam_hint"] else [])
                else:
                    cs = noor_candidates(e["name"], e["imam_hint"]) + (kg_candidates(e["name"], death, 3) if e["kind"] == "collector" else [])
                cs.sort(key=lambda d: -d["score"])
                e["cands"] = cs[:10]
        _derive_relatives(ch)
        _add_context(ch)


_NOOR_OF_KEY = {k: f"noor:{v['ravi_id']}" for k, v in _IMAMS.items() if v.get("ravi_id")}


def _derive_relatives(ch: dict) -> None:
    """«عن أبيه» after an Imam is his father; for anyone else the relative stays unresolved."""
    els = ch["elems"]
    for k, e in enumerate(els):
        if e["kind"] != "relative" or k == 0 or e["name"] not in ("ابیه", "ابیها"):
            continue
        prev = els[k - 1]["cands"][:1]
        if not prev or not prev[0]["id"].startswith("noor:"):
            continue
        key = _ID2KEY.get(int(prev[0]["id"].split(":")[1]))
        if key in FATHER and (prev[0].get("exact") or prev[0].get("cov", 0) >= 0.88 or prev[0].get("derived")):
            father = FATHER[key]
            e["cands"] = [dict(id=_NOOR_OF_KEY[father], label=_IMAMS[father]["canonical"], score=5.0, cov=1.0,
                               derived=True, reason=f"father of {prev[0]['label']}")]


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


def build(wits: Sequence[dict]) -> dict:
    """identities.json: chains with candidates, and the name cards to decide.

    ``wits`` are witness records (``corpus.get``) of the selected witnesses (copies excluded)."""
    chains: List[dict] = []
    trad = {w["wid"]: (w.get("tradition") or ("sunni" if w["corpus"] == "kg" else "shia")) for w in wits}
    flags: List[dict] = []
    for w in wits:
        ch = chains_for(w)
        if not ch:
            flags.append(dict(kind="no_chain", ref=w["wid"], message="no isnad could be read for this witness"))
        chains.extend(ch)
    attach_candidates(chains, trad)
    cards: Dict[str, dict] = {}
    for ch in chains:
        t = trad.get(ch["wid"], "shia")
        for e in ch["elems"]:
            if e["exact"] or e["kind"] == "anon":
                continue
            key = card_key(t, e)
            e["card"] = key
            cd = cards.setdefault(key, dict(key=key, name=e["name"], kind=e["kind"], tradition=t, occurrences=[], candidates={}))
            cd["occurrences"].append([ch["aid"], e["i"]])
            for c in e["cands"]:
                old = cd["candidates"].get(c["id"])
                if old is None or c["score"] > old["score"]:
                    cd["candidates"][c["id"]] = dict(c)
    for cd in cards.values():
        cl = sorted(cd["candidates"].values(), key=lambda c: -c["score"])[:8]
        cd["candidates"] = cl
        top = cl[0] if cl else None
        cd["default"] = top["id"] if top and (top.get("tier") == "high" or top.get("derived")) else None
        cd["tier"] = (top.get("tier") if top else None) or ("derived" if top and top.get("derived") else "none")
    n_exact = sum(1 for ch in chains for e in ch["elems"] if e["exact"])
    n_el = sum(len(ch["elems"]) for ch in chains)
    return dict(chains=chains, cards=cards, flags=flags,
                stats=dict(chains=len(chains), elements=n_el, exact=n_exact, cards=len(cards),
                           undecided_by_default=sum(1 for c in cards.values() if not c["default"])))


def _same_person(ids: Iterable[str]) -> Dict[str, str]:
    """kg id -> noor id for narrators the narrator database linked (high/medium)."""
    kg = sorted({i.split(":")[1] for i in ids if i.startswith("kg:")})
    out: Dict[str, str] = {}
    if not kg:
        return out
    c = _nconn()
    for chunk in _chunks(kg, 500):
        q = ("select ravi_id, target_id from link where target_kind='kg' and confidence in ('high','medium') "
             "and target_id in (%s)" % ",".join("?" * len(chunk)))
        for r in c.execute(q, chunk):
            out.setdefault(f"kg:{r[1]}", f"noor:{r[0]}")
    c.close()
    return out


def resolve(ident: dict, decisions: Optional[dict] = None) -> dict:
    """Apply the user's choices. Returns chains of node ids plus the node table.

    decisions = {choices: {card_key: "noor:12" | "kg:7" | "none"}, merges: [[nid, nid]], overrides: {"aid|i": nid}}
    A card without a choice takes its default (a ``high`` candidate), else becomes a name-only node
    and is listed in ``undecided``."""
    d = decisions or {}
    choices, overrides = d.get("choices", {}), d.get("overrides", {})
    cards = ident["cards"]
    nodes: Dict[str, dict] = {}
    chains_out = []
    undecided = set()
    all_ids = [c["id"] for ch in ident["chains"] for e in ch["elems"] for c in e["cands"][:1]]
    all_ids += [v for v in choices.values() if isinstance(v, str)]
    same = _same_person(all_ids)
    for ch in ident["chains"]:
        out = []
        for e in ch["elems"]:
            occ = f"{ch['aid']}|{e['i']}"
            tier = None
            if occ in overrides:
                nid, tier = overrides[occ], "chosen"
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
                       else f"name:{' '.join(norm(e['name']).split())}")
            nid = same.get(nid, nid)
            node = nodes.setdefault(nid, dict(id=nid, names=[], tiers=set(), n=0))
            if e["name"] not in node["names"]:
                node["names"].append(e["name"])
            node["tiers"].add(tier)
            node["n"] += 1
            out.append(dict(i=e["i"], nid=nid, tier=tier, name=e["name"], collector=e.get("role") == "collector"))
        chains_out.append(dict(aid=ch["aid"], wid=ch["wid"], source=ch["source"], elems=out, speaker=ch["speaker"],
                               quoted_work=ch["quoted_work"], flags=ch["flags"]))
    for n in nodes.values():
        n["tiers"] = sorted(n["tiers"])
    for a, b in d.get("merges", []):
        pass                                        # merges are applied by the graph stage (node renaming)
    return dict(chains=chains_out, nodes=nodes, undecided=sorted(undecided), same_person=same)
