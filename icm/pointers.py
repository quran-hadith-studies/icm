"""Hadiths that refer to another text instead of quoting it, and the text each one stands for.

A compiler often gives a second isnad and, instead of the words, a formula: «… عن أبي عبد الله عليه السلام مثله»,
«فذكر نحوه», «مثله وزاد فيه: …», «مثله إلا أنه قال: …», «سمعت مجاهدا مثله يستتر من بوله». Such a *reference* stands

* alone, as a record of its own whose words are only the formula: it refers to the hadith before it in the book
  (Noor keeps Wasa'il's «و رواه الشيخ بإسناده عن … مثله» this way, the isnad in the text field), or
* inside a record, after that record's own text («… الحديث. و رواه الصدوق في العلل عن … مثله»): it refers to that text.

``split(rec)`` finds them: the record's own text (``main``, None when the record is only a reference) and the references,
each with its isnad, the formula (``marker``), a *template* (how the text is built) and its argument X:

    copy      «مثله / بمثله / بعينه / مثل ذلك»                 the text referred to
    similar   «نحوه / بنحوه / بمعناه / نحو ذلك»                 that text, but the words are not known (approx)
    add       «مثله وزاد فيه X»                                 that text + X
    change    «مثله إلا أنه قال X», «مثله X»                     the passage of that text most like X becomes X
    omit      «مثله ولم يذكر X»                                 that text without the passage most like X
    upto      «مثله إلى قوله X»                                 that text up to X
    incipit   «X … فذكر الحديث / وذكر مثله / الحديث بطوله»       its own opening words, then the rest of that text
    explicit  «مثله [أي مثل حديث: X]»                           X

A reference after «نحوه» keeps ``approx``: what the template builds is a proposal, its words count only once the user
confirms them (step 3). ``build`` makes the text, ``referent`` finds the hadith a lone reference points at.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from . import corpus
from .similarity import align_clause
from .text import norm, strip_tags, token_offsets, tokens

N = lambda s: norm(s)
SAME = {N(x) for x in "مثله بمثله مثلها بمثلها بعینه بعینها بلفظه".split()}
SIMILAR = {N(x) for x in "نحوه بنحوه نحوها بنحوها بمعناه بمعناها".split()}
PAIRS = {(N("مثل"), N("ذلک")): "copy", (N("بمثل"), N("ذلک")): "copy", (N("مثل"), N("حدیث")): "copy", (N("بمثل"), N("حدیث")): "copy",
         (N("مثل"), N("حدیثه")): "copy", (N("نحو"), N("ذلک")): "similar", (N("بنحو"), N("ذلک")): "similar", (N("نحو"), N("حدیث")): "similar",
         (N("بنحو"), N("حدیث")): "similar", (N("نحو"), N("حدیثه")): "similar"}
SAID = {N(x) for x in "ذکر فذکر وذکر قال وقال فقال ساق وساق فساق".split()}
MENTIONED = {N(x) for x in "ذکر فذکر وذکر".split()}
MEANING = N("معناه")
# «the hadith goes on as known»: the record has its own opening words only
ABRIDGED = [tuple(N(x).split()) for x in ("فذکر الحدیث", "وذکر الحدیث", "ثم ذکر الحدیث", "وساق الحدیث", "فساق الحدیث", "ثم ساق الحدیث",
                                          "الحدیث بطوله", "فذکر القصه", "وذکر القصه", "الی آخر الحدیث")]      # «إلى آخره» is as often «to its end»
# words that may stand before a formula at the start of a matn: the speaker formula and «he mentioned»
LEAD = {N(x) for x in ("قال فقال وقال قالا عن عنه عنها عنهما النبی رسول الله صلی علیه علیهم علیهما وآله آله وسلم سلم السلام "
                        "ع ص و ف ذکر فذکر وذکر ثم باسناده باسناد بهذا هذا الاسناد بالاسناد الحدیث مرفوعا یرفعه").split()}
# words that open a second isnad inside a record
OPEN = {N(x) for x in "رواه ورواه رواها ورواها روی وروی یروی ویروی عنه وعنه وعن باسناده وباسناده حدثنا وحدثنا اخبرنا واخبرنا حدثنی وحدثنی".split()}
STRONG = {N(x) for x in "رواه ورواه رواها ورواها روی وروی باسناده وباسناده حدثنا وحدثنا اخبرنا واخبرنا حدثنی وحدثنی سمعت وسمعت انا نا ثنا".split()}
ISNADISH = {N(x) for x in "عن حدثنا اخبرنا حدثنی اخبرنی باسناده سمعت عنه رواه روی یرفعه مرفوعا".split()}
NOTE = [tuple(N(x).split()) for x in ("قال ابو", "هذا حدیث", "وفی الباب", "و فی الباب", "ولم یرفعه", "و لم یرفعه", "رواه", "ورواه", "و رواه",
                                      "قال الترمذی", "وهذا", "و هذا", "وهو", "و هو", "قال الشیخ", "اقول", "قلت", "مرسلا", "موقوفا", "باختصار",
                                      "مختصرا", "وقد روی", "و قد روی", "قد روی", "وروی", "و روی", "حدثنا", "وحدثنا", "اخبرنا", "واخبرنا",
                                      "باسناده", "بهذا الاسناد", "ا ه", "اه", "م", "صحیح", "حسن", "ضعیف", "اسناده")]      # «م: …» a heading, «( صحيح )» a grading
MOD = [("add", ("الا انه زاد فیه", "الا انه زاد", "غیر انه زاد فیه", "غیر انه زاد", "و زاد فی اخره", "وزاد فی اخره", "و قال فی اخره", "وقال فی اخره", "و زاد فیه", "وزاد فیه", "و زاد", "وزاد", "زاد فیه", "زاد")),
       ("omit", ("الا انه لم یذکر", "غیر انه لم یذکر", "الا انه ترک قوله", "الا انه ترک", "و لم یذکر قوله", "ولم یذکر قوله", "و لم یذکر", "ولم یذکر",
                 "و لم یقل", "ولم یقل", "و ترک", "وترک", "و اسقط", "واسقط")),
       ("upto", ("الی قوله", "الی ان قال")),
       ("change", ("الا انه قال فیه", "الا انه قال", "غیر انه قال", "الا انه قال فی حدیثه", "الا ان فیه", "غیر ان فیه", "الا انه", "غیر انه",
                   "الا ان", "غیر ان", "و قال فیه", "وقال فیه", "و قال", "وقال", "و فیه", "وفیه")),
       ("explicit", ("ای مثل حدیث", "ای نحو حدیث", "یعنی حدیث", "ای حدیث", "ای مثل", "ای نحو"))]
MOD = [(k, [tuple(N(x).split()) for x in xs]) for k, xs in MOD]
BFORM = {N(x) for x in "بمثله بمثلها بنحوه بنحوها بمعناه بمعناها".split()}      # always a formula, never a word of a text
BRACKET_LEAD = {N(x) for x in "ای یعنی مثل نحو معنی حدیث حدیثه فی من".split()}
MAX_ISNAD = 45            # tokens from the word that opens an inner isnad to its formula
MAX_INCIPIT = 14          # own opening words before «فذكر الحديث / وذكر مثله»
TEMPLATES = ("copy", "similar", "add", "change", "omit", "upto", "incipit", "explicit")


def _starts(toks: List[str], i: int, pat: Tuple[str, ...]) -> bool:
    return tuple(toks[i:i + len(pat)]) == pat


def _marker(toks: List[str], i: int) -> Optional[Tuple[str, int]]:
    """(base, length) of a formula at token i: base copy | similar | abridged."""
    t = toks[i]
    if t in SAME:
        return "copy", 1
    if t in SIMILAR or (t == MEANING and i and toks[i - 1] in MENTIONED):     # «معناه» alone (or «قال معناه») is «its meaning is»
        return "similar", 1
    if i + 1 < len(toks) and (t, toks[i + 1]) in PAIRS:
        return PAIRS[(t, toks[i + 1])], 2
    for p in ABRIDGED:
        if _starts(toks, i, p):
            return "abridged", len(p)
    return None


def _opener(toks: List[str], j: int) -> bool:
    return toks[j] in OPEN or (toks[j] in ("و", "ف") and j + 1 < len(toks) and toks[j + 1] in OPEN)


def _tail(toks: List[str], a: int, b: int, bracket: bool = False) -> Tuple[str, int, Optional[int]]:
    """What follows a formula, tokens [a, b): (template modifier, where its argument starts, None if no argument)."""
    if a >= b:
        return "", a, None
    for p in NOTE:
        if _starts(toks, a, p):
            return "", a, None                      # a comment of the compiler («قال أبو عيسى», «ولم يرفعه»), not text
    if _starts(toks, a, (N("سواء"),)):
        return "", a, None
    if bracket:                                     # Dorar: «مِثلُه [إنَّ مِنَ الشِّعرِ حِكمةً]»
        return "explicit", a, a
    for kind, pats in MOD:
        for p in pats:
            if _starts(toks, a, p):
                x = a + len(p)
                if kind in ("omit", "upto") and x < b and toks[x] == N("قوله"):
                    x += 1
                if kind == "change" and x < b and toks[x] in (N("قال"), N("فقال")):
                    x += 1
                elif kind in ("change", "add") and p[-1] in (N("ان"), N("انه")):      # «إلا أن وكيعا قال: …»: a narrator's name, then his words
                    q = next((j for j in range(x, min(b, x + 5)) if toks[j] in (N("قال"), N("فقال"), N("یقول"), N("زاد"))), None)
                    if q is not None:
                        x = q + 1
                if x < b and toks[x] == N("فیه") and kind != "explicit":
                    x += 1
                return (kind, x, x) if x < b else ("", a, None)
    if b - a <= 15:                                 # «سمعت مجاهدا مثله يستتر من بوله»: the words that differ
        return "change", a, a
    return "text", a, None                          # a running text goes on: the formula was a word of it («أن يكون له مثله، فصرت إليه …»)


def split(rec: dict) -> dict:
    """The record's own text and the references in it.

    ``main``: the raw matn up to the first inner reference (the whole matn when there is none), None when the record is
    itself only a reference. ``refs``: [{k, of: prev | host, isnad, marker, base, tpl, arg, approx, own, incipit}] in text
    order; k = 0 is the record itself when it is a lone reference, inner references are numbered from 1."""
    sanad, matn = strip_tags(rec.get("sanad") or ""), strip_tags(rec.get("matn") or "")
    full = (sanad + " ¶ " + matn) if sanad.strip() else matn
    toks, offs = tokens(full), token_offsets(full)
    ns = len(tokens(sanad)) if sanad.strip() else 0
    m0 = len(full) - len(matn)                       # where the matn starts in ``full``
    raw = lambda a, b: full[offs[a][0]:offs[b - 1][1]].strip(" :،,.؛«»\"'") if a < b <= len(offs) else ""
    hits: List[Tuple[int, int, str, int, Optional[int]]] = []      # (start of its isnad, formula at, base, formula length, incipit end)
    own_from = ns                                    # the first token of the record's own words
    while own_from < len(toks) and (toks[own_from].isdigit() or toks[own_from] in LEAD):
        own_from += 1
    i = 0
    while i < len(toks):
        mk = _marker(toks, i)
        if not mk:
            i += 1
            continue
        base, ln = mk
        start = None
        if base == "abridged":
            if ns <= i and i - own_from <= MAX_INCIPIT and i - own_from >= 1 and not hits:
                hits.append((0, i, base, ln, i))
            i += ln
            continue
        op = next((j for j in range(i - 1, max(-1, i - MAX_ISNAD - 1), -1) if _opener(toks, j)), None)
        isn = own_from < len(toks) and toks[own_from] in OPEN                    # the matn begins with an isnad («و رواه الشيخ …»)
        if ns - 3 <= i < ns or (ns <= i <= own_from) or (isn and not hits and i - own_from <= MAX_ISNAD):
            start = 0                                                           # end of the sanad, or the matn is only an isnad
        elif ns <= i and not hits and (i - own_from <= 7 and toks[i - 1] in SAID          # «إذا أفلس الرجل فذكر مثله»
                                       or 0 < i - own_from <= 12 and toks[i] in BFORM and _tail(toks, i + ln, len(toks))[0] != "text"):   # «مر رسول الله بقبرين بمثله»
            hits.append((0, i, base, ln, i - 1 if toks[i - 1] in SAID else i))
            i += ln
            continue
        elif op is not None and op >= ns and (any(t in STRONG for t in toks[op:i]) or sum(t in ISNADISH for t in toks[op:i]) >= 2) and i - op <= 30:
            while True:                              # «قال ابن المثنى: وحدثنا وكيع قال: حدثنا الأعمش …»: the earlier openers too
                prev = next((j for j in range(op - 1, max(own_from - 1, op - 6), -1) if _opener(toks, j) or toks[j] in SAID), None)
                if prev is None:
                    break
                op = prev
            own = [t for t in toks[own_from:op] if t not in LEAD and not t.isdigit()]
            start = 0 if not own and not hits else op
        if start is not None and (not hits or start > hits[-1][1]):
            hits.append((start, i, base, ln, None))
        i += ln
    tails = {}
    while True:                                      # a formula followed by running text is a word of that text
        bad = None
        for n, (s0, at, base, ln, inc) in enumerate(hits):
            end = hits[n + 1][0] if n + 1 < len(hits) else len(toks)
            a = at + ln
            if base != "abridged" and ((a < end and toks[a] == N("حدیث")) or toks[at:at + ln][-1:] == [N("حدیث")]):
                a = next((j for j in range(a, min(end, a + 8)) if any(_starts(toks, j, p) for _, ps in MOD for p in ps)), end)   # «نحو حديث أبي أمامة»: a name
            after = full[offs[a - 1][1]:offs[end - 1][1]] if end > a else ""
            tails[n] = ("", a, None) if base == "abridged" else _tail(toks, a, end, after.lstrip(" ،,:.").startswith("["))
            if tails[n][0] == "text" and inc is None:
                bad = n
                break
        if bad is None:
            break
        hits.pop(bad)
    if not hits:
        return dict(main=matn, refs=[])
    lone = hits[0][0] == 0
    refs = []
    for n, (s, at, base, ln, inc) in enumerate(hits):
        end = hits[n + 1][0] if n + 1 < len(hits) else len(toks)
        mod, _, x = tails[n]
        mod = "" if mod == "text" else mod
        tpl = "incipit" if base == "abridged" or inc is not None else mod or base
        if tpl == "explicit":
            br = re.search(r"\[([^\]]+)\]?", full[offs[at][0]:offs[end - 1][1]] if end > at else "")
            if br:                                   # Dorar quotes the hadith meant: «مِثلَه [أي: حَديثَ: …]»
                ws = br.group(1).split()
                while ws and N(ws[0]) in BRACKET_LEAD | {""}:
                    ws.pop(0)
                arg = " ".join(ws)
            else:                                    # «يعني حديث محمد بن طريف»: a narrator, not the text
                tpl, arg = base, ""
        else:
            arg = raw(x, end) if x is not None else ""
        k = n if lone else n + 1
        refs.append(dict(
            k=k, of="prev" if lone else "host", base=base, tpl=tpl, approx=base == "similar", arg=arg,
            marker=" ".join(toks[at:at + ln]),
            isnad=(raw(0, at) if s == 0 else raw(s, at)) if inc is None else raw(0, ns) if ns else "",
            incipit=raw(own_from, inc) if inc is not None else "",
            own=raw(own_from if s == 0 else at, end) if s == 0 and inc is not None else raw(at, end)))
    main = None if lone else matn[:max(0, offs[hits[0][0]][0] - m0)].rstrip(" ،,.؛:")
    return dict(main=main, refs=refs)


def own_text(rec: dict) -> str:
    """The record's own words: ``split(rec)["main"]`` without an isnad written at its start (Noor keeps Wasa'il's isnads in
    the text field: «28514 - و عن علي بن إبراهيم عن أبيه … قال: …»)."""
    main = split(rec)["main"] or ""
    if (rec.get("sanad") or "").strip():
        return main
    tk, of = tokens(main), token_offsets(main)
    k = 0
    while k < len(tk) and tk[k].isdigit():
        k += 1
    if k >= len(tk) or not (tk[k] in OPEN or tk[k] in ISNADISH or (tk[k] == "و" and k + 1 < len(tk) and tk[k + 1] in ISNADISH | OPEN)):
        return main
    last = max((j for j in range(k, min(len(tk), 60)) if tk[j] in ISNADISH), default=None)
    said = next((j for j in range(last + 1, min(len(tk), last + 14)) if tk[j] in (N("قال"), N("قالا"), N("یقول"))), None) if last is not None else None
    return main[of[said][1]:].lstrip(" :،") if said is not None and said + 1 < len(tk) else main


def lone_ref(rec: dict) -> Optional[dict]:
    """The reference a record is (k = 0), or None when it has words of its own."""
    r = split(rec)["refs"]
    return r[0] if r and r[0]["k"] == 0 else None


# ---------------------------------------------------------------------------
def _window(ref_toks: List[str], arg_toks: List[str]) -> Optional[Tuple[int, int]]:
    """The tokens [a, b) of the text referred to that are most like ``arg``: its matched words, plus as many on each side
    as ``arg`` has unmatched words there."""
    if not arg_toks or not ref_toks:
        return None
    r = align_clause(arg_toks, ref_toks)
    pairs = [(i, j) for i, j in r["pairs"] if r["match_a"][i]]
    if not pairs or len(pairs) < max(1, (len(arg_toks) + 1) // 2):      # at least half its words
        return None
    a, b = pairs[0][1] - pairs[0][0], pairs[-1][1] + 1 + (len(arg_toks) - 1 - pairs[-1][0])
    return max(0, min(a, pairs[0][1])), min(len(ref_toks), max(b, pairs[-1][1] + 1))


def build(ref_text: str, tpl: str, arg: str = "", incipit: str = "", at: str = "") -> dict:
    """The text a reference stands for: {text, note, mark: [start, end] of what the template changed in ``text``}.
    ``at``: for «change», the passage of the text referred to that ``arg`` replaces, when its words are not like ``arg``'s
    («فدعا بعسيب رطب» for «ثم أخذ جريدة رطبة»); empty: the passage most like ``arg``."""
    ref = strip_tags(ref_text or "").strip()
    rt, ro = tokens(ref), token_offsets(ref)
    tk = tokens(arg)
    out = lambda text, note="", mark=None: dict(text=text, note=note, mark=mark)
    if tpl in ("copy", "similar"):
        return out(ref)
    if tpl == "explicit":
        return out(arg.strip() or ref, "" if arg.strip() else "متن داده نشده است؛ متن ارجاعی آمد", [0, len(arg.strip())] if arg.strip() else None)
    if tpl == "add":
        if not arg.strip():
            return out(ref, "افزوده خالی است")
        t = ref.rstrip() + " " + arg.strip()
        return out(t, "", [len(ref.rstrip()) + 1, len(t)])
    if tpl == "incipit":
        it = tokens(incipit)
        w = _window(rt, it) if it else None
        if w is None or w[0] > 3:
            return out(ref, "آغاز این حدیث در متن ارجاعی پیدا نشد؛ متن ارجاعی آمد")
        rest = ref[ro[w[1] - 1][1]:]
        t = incipit.strip() + rest
        return out(t, "", [0, len(incipit.strip())])
    w = _window(rt, tokens(at) if tpl == "change" and at.strip() else tk)
    if w is None:
        what = at.strip() if tpl == "change" and at.strip() else arg
        return out(ref, f"«{what}» در متن ارجاعی پیدا نشد؛ متن ارجاعی بی‌تغییر آمد" + ("؛ بخشی را که جایش می‌نشیند در «به‌جای» بنویسید" if tpl == "change" else ""))
    a, b = ro[w[0]][0], ro[w[1] - 1][1]
    if tpl == "change":
        t = ref[:a] + arg.strip() + ref[b:]
        return out(t, "", [a, a + len(arg.strip())])
    if tpl == "omit":
        t = (ref[:a].rstrip() + " " + ref[b:].lstrip()).strip()
        return out(t, "", [len(ref[:a].rstrip()), len(ref[:a].rstrip()) + 1])
    if tpl == "upto":
        return out(ref[:b], "", [b - 1, b])
    return out(ref, "قالب ناشناخته")


# ---------------------------------------------------------------------------
def _entry(wid: str) -> Optional[str]:
    co, i = corpus.wid_parts(wid)
    if co != "v02":
        return None
    r = corpus.conn("v02").execute("select entry from hadith where id=?", (str(i),)).fetchone()
    return r[0] if r else None


def referent(wid: str, ref: dict, back: int = 6) -> dict:
    """The hadith a lone reference points at: the nearest one before it in its book that has words of its own (for
    «فذكر الحديث» and its kin, the nearest that begins with the same words). {wid, text, conf: ok | other_bab | no_incipit |
    none, before: [wids looked at]}."""
    prev = corpus.neighbours(wid, back, after=False)
    recs = corpus.get_many(prev)
    first = None
    it = tokens(ref.get("incipit") or "")
    for w in prev:
        r = recs.get(w)
        if r is None:
            break
        if split(r)["main"] is None:
            continue
        txt = own_text(r)
        if not tokens(txt):
            continue
        if first is None:
            first = (w, txt)
        if not it:
            break
        win = _window(tokens(txt), it)
        if win is not None and win[0] <= 3:
            first = (w, txt)
            break
    else:
        if it and first:
            return dict(wid=first[0], text=first[1], conf="no_incipit", before=prev)
    if first is None:
        return dict(wid=None, text="", conf="none", before=prev)
    e0, e1 = _entry(wid), _entry(first[0])
    return dict(wid=first[0], text=first[1], conf="other_bab" if e0 and e1 and e0 != e1 else "ok", before=prev)


def pseudo(wid: str) -> Tuple[str, int]:
    """«v02:123~2» -> («v02:123», 2); a plain id -> (id, 0)."""
    host, _, k = wid.partition("~")
    return host, int(k) if k else 0


def record(host: dict, k: int) -> Optional[dict]:
    """The record of inner reference k of ``host``: its isnad as the sanad and the formula and what follows as the matn."""
    r = next((x for x in split(host)["refs"] if x["k"] == k), None)
    if r is None:
        return None
    return dict(host, wid=f"{host['wid']}~{k}", sanad=r["isnad"], matn=r["own"], host=host["wid"], ref_k=k)


def derive(wid: str, rec: Optional[dict] = None) -> Optional[dict]:
    """Everything step 3 proposes for a witness that is a reference: the hadith referred to, the template, its argument
    and the text built. None for a witness with words of its own."""
    host_id, k = pseudo(wid)
    host = rec if rec is not None and rec.get("wid") == host_id else corpus.get(host_id)
    if host is None:
        return None
    sp = split(host)
    r = next((x for x in sp["refs"] if x["k"] == k), None)
    if r is None:
        return None
    if r["of"] == "host":
        src = dict(wid=host_id, text=own_text(host), conf="ok", before=[])
    else:
        src = referent(host_id, r)
    b = build(src["text"], r["tpl"], r["arg"], r["incipit"])
    return dict(wid=wid, at="", ref=src["wid"], of=r["of"], conf=src["conf"], tpl=r["tpl"], base=r["base"], approx=r["approx"], arg=r["arg"],
                incipit=r["incipit"], marker=r["marker"], isnad=r["isnad"], own=r["own"], text=b["text"], note=b["note"], mark=b["mark"])
