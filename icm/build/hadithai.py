"""hadithai.sqlite: the hadith.ai v2 segmentation and narrator tags, for every hadith the hub took from it.

Source: the hub books converted by ``hadith-hub/src/convert_hadithai_v2.py`` (meta ``source.via`` =
api.hadith.ai): Noor (id = Noor hadith id), Dorar (600M+) and sunnah.com (700M+). A v02 hadith finds its
row by its id, or, for a Noor hadith served from Jami' al-Ahadith (``angel``, id 800M + Noor id), by
``id - NOOR_OFFSET``: the two editions are the same Noor record.

    seg(hid PRIMARY KEY, source, z)      z = zlib(json):
        segs   the matn's segments: [{t: normalised text, ty: types, o: owner, oc: owner_canonical, q: seg_qael}]
        chain  the opening isnad (``isnad_end``) as tagged: [{name: raw text, term: words before it,
               tag: Narrator | Exporter}] from Noor's inline tags, else the isnad segment's owner list
        speaker  owner_canonical of the first segment said by a Ma'sum (seg_qael I), or None

hadith.ai has no narrator identities (its tags are character spans; its "owner" catalogue is a list of
strings), so its narrators are kept as *mentions*, every one of them, and are tied to a person only where
that is certain:

    hadith(hid PRIMARY KEY, source, book)        book -> book(id, slug, title)
    mention(hid, i, name, nkey, pk, nk, term, tag, ravi_id, link, slot_pos, sugg_ravi, sugg, sugg_n)
        one row per tagged name of ``chain`` (i = its position, 0 at the collector's side); nkey = the name
        normalised (``narrators.common.norm_name``), pk / nk = the keys of the names before and after it
        ("^" / "$" at the ends).
        link = 'noor_slot': the same Noor record has Noor's own linked isnad (narrators.sqlite ``slot``,
               sanad 0) and this mention has the same normalised name as a slot, in the same order (the
               two name lists aligned by ``SequenceMatcher``). ``ravi_id`` is that slot's RaviId (None when
               Noor itself left the slot unresolved) and ``slot_pos`` its position. Certain: same record,
               same words, same place in the chain.
        sugg_ravi / sugg / sugg_n: a *suggestion*, never a link, for a Noor mention whose record has no
               linked isnad (Bihar, Wafi, Mustadrak, the tafsirs ...): the RaviId every certain mention with
               the same name and neighbours has (``ctx3``: both neighbours, at least 2 such mentions;
               ``ctx_prev`` / ``ctx_next``: one neighbour, at least 3), all of them the same person. Measured
               leave-book-out by ``icm.evaluate.mentions``. A name alone suggests nothing, nor does a relative
               word that opens the chain («عن أبيه»: whose father depends on the author).
        Mentions in Dorar and sunnah.com are kept without either: no source with identities shares their
        records, and the Shia chains the suggestions learn from say nothing certain about Sunni ones.
    speaker(hid, i, owner, canonical, category, n_segs)
        the speakers of the matn's segments (consecutive segments of one owner are one row; i = the first
        segment's index). hadith.ai's ``owner_canonical`` / ``seg_qael`` are its own labels and are not
        mapped to persons: outside Noor they are often wrong (e.g. «محمد الباقر بن علي بن الحسين» labelled
        امام سجاد, «علي بن الحسين بن علي» امام حسین, «الحسن بن علي الحلواني» امام حسن).

    python3 -m icm build hadithai
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import time
import zlib
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Dict, Iterable, Optional, Tuple

from .. import config
from ..text import norm

SUPPORT = (("ctx3", 2), ("ctx_prev", 3), ("ctx_next", 3))     # suggestion tiers, strongest first, and their minimum support
# relative words («أبيه», «جده», «أبي» ...; ``norm_name`` spells أبي as ابو): at the head of a chain they name a
# relative of whoever opens it, the work's author, whom the tags do not show
_RELATIVE = {"ابيه", "ابيهما", "ابيهم", "ابويه", "جده", "جدي", "اخيه", "اخي", "عمه", "عمي", "خاله", "ابنه", "امه", "امي"}
_HONORIFIC = {"عليه", "عليهما", "عليهم", "رحمه", "رضي", "ره", "رض"}


def _row(r: dict) -> dict:
    text = r.get("text") or ""
    cut = len(r.get("clean_sanad") or "") if (r.get("clean_sanad") and text.startswith(r["clean_sanad"])) else 0   # where the matn starts
    isnad_end = r.get("isnad_end") or 0          # the opening isnad (may cover a record that stays its own matn)
    segs = []
    for s in r.get("segments_v2") or []:
        if "Isnad" in (s.get("types") or []) or s.get("end", 0) <= cut:
            continue
        t = norm(text[max(s["start"], cut):s["end"]])
        if t:
            segs.append(dict(t=t, ty=s.get("types") or [], o=s.get("owner"), oc=s.get("owner_canonical"), q=s.get("seg_qael")))
    chain = []
    cut = isnad_end
    if cut:
        ts = r.get("tag_spans") or {}
        marks = sorted([(sp[0], sp[1], "Narrator") for sp in ts.get("Narrator", []) if sp[1] <= cut]
                       + [(sp[0], sp[1], "Exporter") for sp in ts.get("Exporter", []) if sp[1] <= cut])
        at = 0
        for a, b, tag in marks:
            if a < at:                                  # nested tags (an Exporter inside a Narrator): keep the outer one
                continue
            chain.append(dict(name=text[a:b].strip(), term=text[at:a].strip(), tag=tag))
            at = b
    if not chain and r.get("isnad_owner"):          # no Narrator tags (sunnah.com): the isnad segment's owner list
        chain = [dict(name=x, term="", tag="Narrator") for x in r["isnad_owner"]]
    return dict(segs=segs, chain=chain, speaker=r.get("speaker"))


def _mentions(hid: int, chain) -> list:
    from narrators.common import norm_name
    keys = [norm_name(c["name"]) for c in chain]
    return [(hid, i, c["name"], keys[i], keys[i - 1] if i else "^", keys[i + 1] if i + 1 < len(keys) else "$",
             c["term"] or None, c["tag"]) for i, c in enumerate(chain)]


def _speakers(hid: int, segs) -> list:
    out = []
    for k, s in enumerate(segs):
        who = (s.get("o"), s.get("oc"), s.get("q"))
        if not any(who):
            continue
        if out and out[-1][2:5] == who and out[-1][1] + out[-1][5] == k:
            out[-1] = out[-1][:5] + (out[-1][5] + 1,)
            continue
        out.append((hid, k) + who + (1,))
    return out


# ---------------------------------------------------------------------------
# context suggestions (also used by icm.evaluate.mentions)
# ---------------------------------------------------------------------------
def context_model(linked: Iterable[Tuple[str, str, str, int]]) -> Dict[str, Dict[tuple, Counter]]:
    """From certain (nkey, pk, nk, ravi_id) mentions: who each name is next to which neighbours."""
    m: Dict[str, Dict[tuple, Counter]] = {t: defaultdict(Counter) for t, _ in SUPPORT}
    for k, p, n, rid in linked:
        m["ctx3"][(k, p, n)][rid] += 1
        m["ctx_prev"][(k, p)][rid] += 1
        m["ctx_next"][(k, n)][rid] += 1
    return m


def is_relative(k: str) -> bool:
    t = k.split()
    return bool(t) and (t[0] in _RELATIVE or t[0] == "ابو" and (len(t) == 1 or t[1] in _HONORIFIC))


def suggest(model, k: str, p: str, n: str) -> Optional[Tuple[int, str, int]]:
    """(ravi_id, tier, support) when every certain mention of the strongest matching context is one person."""
    if p == "^" and is_relative(k):
        return None
    for tier, need in SUPPORT:
        key = (k, p, n) if tier == "ctx3" else (k, p) if tier == "ctx_prev" else (k, n)
        c = model[tier].get(key)
        if c:
            if len(c) == 1 and sum(c.values()) >= need:
                rid, sup = next(iter(c.items()))
                return rid, tier, sup
            if len(c) > 1:                              # the context is ambiguous: a weaker one cannot overrule it
                return None
    return None


def _link_noor(c: sqlite3.Connection, log) -> set:
    """Certain links: hadith.ai mentions in a Noor record with Noor's linked isnad, aligned by identical names."""
    from narrators.common import norm_name
    nc = sqlite3.connect(f"file:{config.NARRATORS_DB}?mode=ro&immutable=1", uri=True)
    slots: Dict[int, list] = defaultdict(list)
    for hid, pos, title, rid in nc.execute("select hadith_id, pos, title, ravi_id from slot where sanad_idx=0 order by hadith_id, pos"):
        slots[hid].append((norm_name(title or ""), rid, pos))
    nc.close()
    upd = []
    cur = c.execute("select m.hid, m.i, m.nkey from mention m join hadith h on h.hid=m.hid where h.source='noor' order by m.hid, m.i")
    last, keys = None, []

    def flush(hid, keys):
        sl = slots.get(hid)
        if not sl:
            return
        sm = SequenceMatcher(None, [k for _, k in keys], [s[0] for s in sl], autojunk=False)
        for b in sm.get_matching_blocks():
            for d in range(b.size):
                s = sl[b.b + d]
                upd.append((s[1], s[2], hid, keys[b.a + d][0]))
    for hid, i, k in cur:
        if hid != last:
            if last is not None:
                flush(last, keys)
            last, keys = hid, []
        keys.append((i, k))
    if last is not None:
        flush(last, keys)
    c.executemany("update mention set ravi_id=?, link='noor_slot', slot_pos=? where hid=? and i=?", upd)
    log(f"  certain links (same Noor record, same name, same order): {len(upd)}")
    return {h for h, v in slots.items() if v}


def _suggest_noor(c: sqlite3.Connection, slot_hids: set, log) -> None:
    model = context_model(c.execute("select nkey, pk, nk, ravi_id from mention where link='noor_slot' and ravi_id is not null"))
    upd, tiers = [], Counter()
    rows = c.execute("select m.hid, m.i, m.nkey, m.pk, m.nk from mention m join hadith h on h.hid=m.hid "
                     "where h.source='noor' and m.link is null").fetchall()
    for hid, i, k, p, n in rows:
        if hid in slot_hids:                            # Noor read this isnad itself: its slots decide, not a guess
            continue
        s = suggest(model, k, p, n)
        if s:
            upd.append((s[0], s[1], s[2], hid, i))
            tiers[s[1]] += 1
    c.executemany("update mention set sugg_ravi=?, sugg=?, sugg_n=? where hid=? and i=?", upd)
    log(f"  context suggestions: {dict(tiers)} of {len(rows)} unlinked Noor mentions")


def run(dst: str = None, log=print) -> None:
    dst = dst or os.path.join(config.DATA_DIR, "hadithai.sqlite")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    c = sqlite3.connect(tmp)
    c.executescript("""
        create table seg(hid integer primary key, source text, z blob);
        create table meta(k text primary key, v text);
        create table book(id integer primary key, slug text, title text);
        create table hadith(hid integer primary key, source text, book integer);
        create table mention(hid integer, i integer, name text, nkey text, pk text, nk text, term text, tag text,
                             ravi_id integer, link text, slot_pos integer, sugg_ravi integer, sugg text, sugg_n integer,
                             primary key (hid, i)) without rowid;
        create table speaker(hid integer, i integer, owner text, canonical text, category text, n_segs integer,
                             primary key (hid, i)) without rowid;
    """)
    t0, n, books = time.time(), 0, 0
    for mp in sorted(glob.glob(os.path.join(config.HUB_BOOKS, "*", "meta.json"))):
        m = json.load(open(mp, encoding="utf-8"))
        if (m.get("source") or {}).get("via") != "api.hadith.ai":
            continue
        books += 1
        c.execute("insert into book values (?,?,?)", (books, m.get("slug"), m.get("book_name") or m.get("title_ar") or m.get("slug")))
        rows, hads, ments, spk = [], [], [], []
        for line in open(os.path.join(os.path.dirname(mp), "hadiths.jsonl"), encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            hid, d = int(r["id"]), _row(r)
            rows.append((hid, r.get("source"), zlib.compress(json.dumps(d, ensure_ascii=False).encode(), 6)))
            hads.append((hid, r.get("source"), books))
            ments += _mentions(hid, d["chain"])
            spk += _speakers(hid, d["segs"])
        c.executemany("insert or replace into seg values (?,?,?)", rows)
        c.executemany("insert or replace into hadith values (?,?,?)", hads)
        c.executemany("insert or replace into mention(hid, i, name, nkey, pk, nk, term, tag) values (?,?,?,?,?,?,?,?)", ments)
        c.executemany("insert or replace into speaker values (?,?,?,?,?,?)", spk)
        n += len(rows)
    c.commit()
    log(f"{n} hadiths from {books} api.hadith.ai books ({time.time() - t0:.0f}s)")
    slot_hids = _link_noor(c, log)
    _suggest_noor(c, slot_hids, log)
    c.executescript("""
        create index mention_ravi on mention(ravi_id) where ravi_id is not null;
        create index mention_sugg on mention(sugg_ravi) where sugg_ravi is not null;
        create index mention_nkey on mention(nkey);
    """)
    st = dict(rows=n, built=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    for k, q in (("mentions", "select count(*) from mention"),
                 ("mentions_linked", "select count(*) from mention where link is not null"),
                 ("mentions_linked_ravi", "select count(*) from mention where ravi_id is not null"),
                 ("mentions_suggested", "select count(*) from mention where sugg_ravi is not null"),
                 ("speakers", "select count(*) from speaker")):
        st[k] = c.execute(q).fetchone()[0]
    c.executemany("insert into meta values (?,?)", [(k, str(v)) for k, v in st.items()])
    c.commit()
    c.execute("vacuum")
    c.close()
    os.replace(tmp, dst)
    log(f"-> {dst} {st} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    run()
