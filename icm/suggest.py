"""Step 4 suggestions: what the databases say about the names the identify stage could not settle.

Every item is a proposal the user accepts or dismisses in step 4; nothing here changes a decision or a default.

  title_match            Noor's linked isnads write exactly this name for one narrator («ابن عباس» -> عبد الله بن العباس)
  strip_words            isnad words are glued to the name («عایشه ان رسول الله»); what is left matches a narrator
  not_a_name             the "name" is isnad wording («لما ثقل», «وقال رسول الله», «اقول»): drop it from the chain
  hidden_junk            the same, for words re-read from Noor's connectives («فقد», «به», «کلهم»)
  relative               «أبيه», «جده»: whom Noor names for the narrator before it, valid while that narrator is who we think
  same_person            a Noor id and a KG id of this family that the narrator database (or name and death year) link
  witness_without_chain  a witness whose isnad names nobody but its collector: leave it out in step 2?

    suggest(I) -> [{id, kind, aid, i, occ, wid, card, proposal: {op, id?, label?, pair?, wid?}, evidence, confidence,
                     depends_on?: {occ, id}}]

``id`` is stable across re-runs (kind + card key, place or pair), so a dismissed suggestion stays dismissed.
Candidates a suggestion proposes are appended to the card (``via``: title | strip) without changing its order.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from . import identity
from .graph import known_deaths
from .sanad_parse import _strip_honorific, not_a_name, strip_nonname
from .text import norm

REASON_FA = dict(prophet="آغاز سخن پیامبر", gap="نشانه‌ی سندِ کوتاه‌شده", connective="واژه‌ی سند")
CONF_FA = dict(high="زیاد", medium="متوسط", low="کم")


def _pct(x: float) -> str:
    return f"{round(100 * x)}٪"


def _trad(ch: dict) -> str:
    if ch.get("tradition"):
        return ch["tradition"]
    for e in ch["elems"]:                              # identify outputs before chains carried their tradition
        if e.get("card"):
            return e["card"].split(":", 1)[0]
    return "sunni" if ch["source"] == "kg" else "shia"


def _append(cd: Optional[dict], e: dict, c: dict, via: str) -> None:
    c = dict(c, tier="low", via=via, score=c.get("score", 0))
    if cd is not None and all(x["id"] != c["id"] for x in cd["candidates"]):
        cd["candidates"].append(c)
    if all(x["id"] != c["id"] for x in e["cands"]):
        e["cands"].append(c)


def _ctx(els: List[dict], k: int, pid: str) -> int:
    """How many neighbours of place k the databases record next to ``pid`` (teacher or student)."""
    pairs = []
    if k > 0:
        pairs += [(pid, s) for s in identity._top(els[k - 1])]
    if k + 1 < len(els):
        pairs += [(t, pid) for t in identity._top(els[k + 1])]
    if not pairs:
        return 0
    adj = identity._adjacent(pairs)
    return int(any((pid, s) in adj for s in (identity._top(els[k - 1]) if k > 0 else []))) + \
        int(any((t, pid) in adj for t in (identity._top(els[k + 1]) if k + 1 < len(els) else [])))


def _label(pid: str) -> str:
    if pid.startswith("noor:"):
        r = identity.noor_index()["ravis"].get(int(pid.split(":")[1]))
        return r["name"] if r else pid
    return pid


def suggest(I: dict) -> List[dict]:
    chains, cards = I["chains"], I["cards"]
    out: List[dict] = []
    seen = set()
    picks: Dict[str, List[Tuple[str, str]]] = defaultdict(list)    # place -> persons suggested for it (bases of relatives)

    def add(**s):
        if s["id"] not in seen:
            seen.add(s["id"])
            out.append(s)

    for ch in chains:
        trad, els = _trad(ch), ch["elems"]
        for k, e in enumerate(els):
            occ = f"{ch['aid']}|{e['i']}"
            if e["exact"] or e["kind"] == "anon" or e.get("role") == "collector":
                continue
            cd = cards.get(e.get("card"))
            base = dict(aid=ch["aid"], i=e["i"], occ=occ, wid=ch["wid"], card=e.get("card"))
            if e["kind"] == "relative":
                _relative(add, picks, els, k, e, cd, base)
                continue
            if not_a_name(e["name"]):
                kind = "hidden_junk" if e.get("hidden") else "not_a_name"
                ev = (f"«{e['name']}» از واژه‌های میان نام‌های نور خوانده شده و نام کسی نیست" if e.get("hidden")
                      else f"«{e['name']}» نام کسی نیست، واژه‌ی سند یا آغاز متن است")
                add(id=f"{kind}:{occ}", kind=kind, **base, proposal=dict(op="drop"), evidence=ev + "؛ از سند حذف می‌شود.",
                    confidence="high")
                continue
            default = cd["default"] if cd else None
            s = strip_nonname(e["name"])
            name_n = " ".join(norm(e["name"]).split())
            if s["core"] and s["core"] != name_n:
                best = _best(s["core"], trad, e)
                cur = cd["candidates"][0] if cd and cd["candidates"] else None
                if best and best["id"] != default and not (cur and (cur["id"] == best["id"] or cur.get("cov", 0) >= best["cov"])):
                    ctx = _ctx(els, k, best["id"])
                    _append(cd, e, best, "strip")
                    picks[occ].append((best["id"], best["label"]))
                    why = "، ".join(REASON_FA[r] for r in s["reasons"])
                    add(id=f"strip_words:{e.get('card') or occ}", kind="strip_words", **base,
                        proposal=dict(op="choice", id=best["id"], label=best["label"]),
                        evidence=f"«{' '.join(s['stripped'])}» جزو نام نیست ({why})؛ «{s['core']}» = {best['label']} "
                                 f"(هم‌خوانی نام {_pct(best['cov'])}" + ("، و همسایه‌های سند در پایگاه کنار او آمده‌اند)" if ctx else ")"),
                        confidence="medium" if ctx else "low")
                    continue
            hits = identity.noor_title_hits(e["name"]) if not _bare_word(e["name"]) else []
            if hits:
                rid, n, share = hits[0]
                pid = f"noor:{rid}"
                if n >= 5 and share >= 0.5 and pid != default:
                    lab = _label(pid)
                    _append(cd, e, dict(id=pid, label=lab, cov=1.0, n=n), "title")
                    picks[occ].append((pid, lab))
                    add(id=f"title_match:{e.get('card') or occ}", kind="title_match", **base,
                        proposal=dict(op="choice", id=pid, label=lab),
                        evidence=f"در سندهای پیونددار نور «{e['name']}» {n} بار آمده و {_pct(share)} آن‌ها {lab} است",
                        confidence="medium" if share >= 0.8 and n >= 20 else "low")
    _same_person(add, chains, cards, picks)
    _no_chain(add, chains)
    return out


def _bare_word(name: str) -> bool:
    """«ابن», «ابو» alone: a title that names nobody in particular."""
    t = identity.title_key(name).split()
    return len(t) < 2 and (not t or t[0] in ("ابن", "ابو", "ابی", "ام", "بنت", "ابا"))


def _best(core: str, trad: str, e: dict) -> Optional[dict]:
    hits = identity.noor_title_hits(core) if not _bare_word(core) else []
    if hits and hits[0][1] >= 5 and hits[0][2] >= 0.5:
        pid = f"noor:{hits[0][0]}"
        return dict(id=pid, label=_label(pid), cov=1.0, n=hits[0][1])
    cs = identity.match(core, trad, e.get("imam_hint", False), e.get("death"))
    return cs[0] if cs and cs[0].get("cov", 0) >= 0.88 else None


def _relative(add, picks, els, k, e, cd, base) -> None:
    rel = identity.RELATION.get(e["name"])
    if not rel or k == 0 or (e["cands"] and e["cands"][0].get("derived")):
        return
    prev = els[k - 1]
    if rel == "grandfather" and identity.RELATION.get(identity.relative_word(prev["name"]) or "") == "father":
        rel = "father"                                 # «عن أبيه عن جده»: the father of the father
    occb = f"{base['aid']}|{prev['i']}"
    bases = [(c["id"], c["label"], not c.get("exact")) for c in prev["cands"][:3]
             if c.get("exact") or c.get("derived") or c.get("cov", 0) >= 0.6]
    bases += [(i, lab, True) for i, lab in picks.get(occb, []) if all(i != b[0] for b in bases)]
    rel_fa = {"father": "پدر", "grandfather": "پدربزرگ", "brother": "برادر", "uncle": "عمو", "maternal_uncle": "دایی",
              "mother": "مادر", "son": "پسر"}.get(rel, rel)
    for bid, blab, cond in bases:
        if not bid.startswith("noor:"):
            continue
        rid = int(bid.split(":")[1])
        found: List[Tuple[str, str, float, str]] = []
        key = identity._ID2KEY.get(rid)
        anc = identity.FATHER.get(key) if rel in ("father", "grandfather") else None
        if anc and rel == "grandfather":
            anc = identity.FATHER.get(anc)
        if anc and anc in identity._NOOR_OF_KEY:
            found.append((identity._NOOR_OF_KEY[anc], identity._IMAMS[anc]["canonical"], 1.0, "جدول امامان"))
        else:
            got = identity.noor_relatives().get((rid, rel), {})
            tot = sum(got.values())
            for r, n in sorted(got.items(), key=lambda x: -x[1])[:3]:
                if n / tot >= 0.2:
                    found.append((f"noor:{r}", _label(f"noor:{r}"), n / tot, f"{n} از {tot} سند نور"))
        for pid, lab, share, src in found:
            if any(c["id"] == pid and c.get("derived") for c in e["cands"]):
                continue
            _append(cd, e, dict(id=pid, label=lab, cov=round(share, 3)), "relative")
            picks[base["occ"]].append((pid, lab))
            s = dict(id=f"relative:{base['occ']}:{pid}", kind="relative", **base,
                     proposal=dict(op="choice", id=pid, label=lab),
                     evidence=(f"اگر «{prev['name']}» همان {blab} باشد، " if cond else "") +
                              f"{rel_fa}ِ او {lab} است ({src}{'' if share >= 1 else '، ' + _pct(share)})",
                     confidence="medium" if share >= 0.8 else "low")
            if cond:
                s["depends_on"] = dict(occ=occb, id=bid)
            add(**s)


def _same_person(add, chains, cards, picks) -> None:
    """Noor and KG ids of this family that may be one person: the narrator database's links the identify stage does
    not apply by itself (low confidence), and pairs with the same name and death year."""
    used = {e["exact"] for ch in chains for e in ch["elems"] if e["exact"]}
    used |= {cd["default"] for cd in cards.values() if cd.get("default")}
    used |= {i for ps in picks.values() for i, _ in ps}
    cand = used | {c["id"] for cd in cards.values() for c in cd["candidates"][:3]}
    noor = {i for i in cand if i.startswith("noor:")}
    kg = {i for i in cand if i.startswith("kg:")}
    if not noor or not kg:
        return
    pairs: Dict[Tuple[str, str], str] = {}
    c = identity._nconn()
    for chunk in identity._chunks(sorted(i.split(":")[1] for i in kg), 500):
        q = ("select ravi_id, target_id, confidence, score from link where target_kind='kg' and target_id in (%s)"
             % ",".join("?" * len(chunk)))
        for r in c.execute(q, chunk):
            a, b = f"noor:{r[0]}", f"kg:{r[1]}"
            if a in noor and (a in used or b in used) and r[2] == "low":
                pairs[(a, b)] = f"پایگاه راویان این دو را با اطمینان کم یکی می‌داند (امتیاز {round(r[3] or 0, 2)})"
    c.close()
    # same name and death year: a Noor narrator and a KG narrator both used in this family
    nu, ku = sorted(i for i in noor if i in used), sorted(i for i in kg if i in used)
    deaths = known_deaths(nu + ku)
    idx = identity.noor_index()["ravis"]
    kc = identity.corpus.conn("kg")
    kname = {}
    for chunk in identity._chunks([int(i.split(":")[1]) for i in ku if i in deaths], 800):
        for r in kc.execute("select id, name from rawis where id in (%s)" % ",".join("?" * len(chunk)), chunk):
            kname[f"kg:{r[0]}"] = identity._ntoks(r[1] or "")
    for a in nu:
        if a not in deaths:
            continue
        r = idx.get(int(a.split(":")[1]))
        if not r:
            continue
        forms = [identity._ntoks(" ".join(_strip_honorific(norm(" ".join(t)).split())[0])) for t in [r["toks"]] + r["alts"]]
        da = sum(deaths[a]["death"]) / 2
        for b, kt in kname.items():
            db = sum(deaths[b]["death"]) / 2
            if (a, b) in pairs or abs(da - db) > 3 or not kt:
                continue
            if len(kt) >= 2 and any(len(f) >= 2 and max(identity._cov(f, kt), identity._cov(kt, f)) >= 0.88 for f in forms):
                pairs[(a, b)] = f"نام هم‌خوان و سال وفات نزدیک: {r['name']} (ف {round(da)}) و {_kglabel(kc, b)} (ف {round(db)})"
    for (a, b), ev in sorted(pairs.items()):
        add(id=f"same_person:{a}|{b}", kind="same_person", aid=None, i=None, occ=None, wid=None, card=None,
            proposal=dict(op="merge", pair=[a, b], label=f"{_label(a)} = {_kglabel(kc, b)}"), evidence=ev, confidence="low")
    kc.close()


def _kglabel(kc, kid: str) -> str:
    r = kc.execute("select name from rawis where id=?", (int(kid.split(":")[1]),)).fetchone()
    return r[0] if r else kid


def _no_chain(add, chains) -> None:
    by = defaultdict(list)
    for ch in chains:
        by[ch["wid"]].append(ch)
    for wid, cs in by.items():
        real = [e for ch in cs for e in ch["elems"] if e.get("role") != "collector" and e["kind"] in ("name", "relative")
                and not not_a_name(e["name"])]
        if real:
            continue
        coll = next((e["name"] for ch in cs for e in ch["elems"] if e.get("role") == "collector"), "")
        add(id=f"witness_without_chain:{wid}", kind="witness_without_chain", aid=cs[0]["aid"], i=None, occ=None, wid=wid,
            card=None, proposal=dict(op="exclude", wid=wid),
            evidence=f"سند این شاهد جز گردآورنده ({coll}) کسی را نام نمی‌برد؛ برای پرسشِ مبدأ، شاهدی بی‌سند است",
            confidence="medium")
