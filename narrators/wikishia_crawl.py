"""Stage 2b: WikiShia (fa / ar / en) narrator, companion and scholar infoboxes → ``wikishia``.

Pages are found through the MediaWiki API (``list=embeddedin`` on each infobox template in
``data/wikishia_fields.json``), fetched once (``action=parse``, wikitext + langlinks) and
cached as JSON under ``external/datasets/wikishia/<lang>/<pageid>.json``; the crawl is
resumable and makes one request per second. Loading the cache into the table is a separate,
offline step so field mapping can be changed without re-crawling.

    python3 -m narrators wikishia                 # crawl (resumable) then load
    python3 -m narrators wikishia --load-only
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

from .common import DATASETS, ascii_digits, data_file, insert_many, load_json, open_db, reset_tables, table_count

ROOT = os.path.join(DATASETS, "wikishia")
UA = "hadith-project narrator pipeline/0.1 (research; contact via github.com/csquranics)"
TABLES = ["wikishia"]


def api(url: str, **params):
    params["format"] = "json"
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def embedded_pages(url: str, template_title: str) -> List[dict]:
    out, cont = [], {}
    while True:
        r = api(url, action="query", list="embeddedin", eititle=template_title, einamespace=0, eilimit=500, **cont)
        out.extend(r["query"]["embeddedin"])
        if "continue" not in r:
            return out
        cont = {"eicontinue": r["continue"]["eicontinue"]}
        time.sleep(1)


def crawl(langs: Optional[List[str]] = None, delay: float = 1.0) -> None:
    cfg = load_json(data_file("wikishia_fields.json"))["langs"]
    for lang, lc in cfg.items():
        if langs and lang not in langs:
            continue
        ldir = os.path.join(ROOT, lang)
        os.makedirs(ldir, exist_ok=True)
        want: Dict[int, Tuple[str, str]] = {}
        for tpl in lc["templates"]:
            pages = embedded_pages(lc["api"], f'{lc["template_ns"]}:{tpl}')
            for p in pages:
                want.setdefault(p["pageid"], (p["title"], tpl))
            print(f"{lang}: {tpl}: {len(pages)} pages", file=sys.stderr)
            time.sleep(delay)
        got = skipped = 0
        for pid, (title, tpl) in want.items():
            path = os.path.join(ldir, f"{pid}.json")
            if os.path.exists(path):
                skipped += 1
                continue
            for attempt in range(3):
                try:
                    r = api(lc["api"], action="parse", pageid=pid, prop="wikitext|langlinks")["parse"]
                    break
                except Exception as e:  # noqa: BLE001
                    print(f"  {lang} {pid}: {e}", file=sys.stderr)
                    time.sleep(5 * (attempt + 1))
                    r = None
            if r is None:
                continue
            json.dump({"pageid": pid, "lang": lang, "title": title, "template": tpl,
                       "wikitext": r["wikitext"]["*"],
                       "langlinks": {l["lang"]: l["*"] for l in r.get("langlinks", [])},
                       "fetched": time.strftime("%Y-%m-%d")},
                      open(path, "w", encoding="utf-8"), ensure_ascii=False)
            got += 1
            time.sleep(delay)
            if got % 100 == 0:
                print(f"  {lang}: {got} fetched, {skipped} cached", file=sys.stderr)
        print(f"{lang}: {got} fetched, {skipped} cached, {len(want)} wanted")


# ----------------------------------------------------------------------------- parsing

def infobox_params(wikitext: str, template: str) -> Dict[str, str]:
    """Parameters of the first ``{{template …}}`` block, with nested braces respected."""
    key = template.split(":")[-1]
    m = re.search(r"\{\{\s*" + re.escape(key) + r"\s*(?=[|\n}])", wikitext)
    if not m:
        return {}
    i, depth, start = m.start(), 0, m.start()
    while i < len(wikitext):
        if wikitext.startswith("{{", i):
            depth += 1
            i += 2
            continue
        if wikitext.startswith("}}", i):
            depth -= 1
            i += 2
            if depth == 0:
                break
            continue
        i += 1
    block = wikitext[start + 2:i - 2]
    # split on top-level pipes
    parts, depth, cur = [], 0, []
    j = 0
    while j < len(block):
        ch = block[j]
        if block.startswith("{{", j) or block.startswith("[[", j):
            depth += 1
            cur.append(block[j:j + 2])
            j += 2
            continue
        if block.startswith("}}", j) or block.startswith("]]", j):
            depth -= 1
            cur.append(block[j:j + 2])
            j += 2
            continue
        if ch == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        j += 1
    parts.append("".join(cur))
    out = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def strip_markup(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<ref[^>]*>.*?</ref>|<ref[^/]*/>", " ", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\{\{[^{}]*\}\}", " ", s)
    s = re.sub(r"\{\{[^{}]*\}\}", " ", s)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)
    s = s.replace("'''", "").replace("''", "")
    return re.sub(r"\s+", " ", s).strip()


def split_names(s: str) -> List[str]:
    s = strip_markup(s)
    parts = re.split(r"\s*(?:•|،|,|;|؛|\n|/| and | و )\s*", s)
    return [p.strip(" .") for p in parts if p and len(p.strip(" .")) > 1 and "..." not in p]


_YEAR_MARK = re.compile(r"(\d{1,4})\s*(?:ق\b|ه\s*\.?\s*ق|هـ|هجری|هجري|AH|\s*/\s*\d)")
_YEAR_ANY = re.compile(r"(?<![\d/])(\d{1,4})(?![\d/])")


def hijri_year(s: str) -> Optional[int]:
    """First Hijri year in an infobox date field («۱۴۱ق», «[[148هـ]]», «148/765-6», «[[سال ۱۴۱ هجری قمری|۱۴۱ق]]»)."""
    if not s:
        return None
    t = ascii_digits(strip_markup(s))
    if re.search(r"ق\s*\.?\s*هـ|ق\.ه\b|قبل الهجر|BH\b|پیش از هجرت", t):   # «57 ق.هـ» = before the Hijra
        return None
    t = re.sub(r"\(\s*\d{3,4}\s*م\s*\)", " ", t)          # «(758م)» Gregorian in brackets
    for m in _YEAR_MARK.finditer(t):
        y = int(m.group(1))
        if 1 <= y <= 1500:
            return y
    for m in _YEAR_ANY.finditer(t):
        y = int(m.group(1))
        if 1 <= y <= 1500 and not re.search(r"\d{1,4}\s*م\b", t):
            return y
    return None


def load(db_path: str) -> None:
    cfg = load_json(data_file("wikishia_fields.json"))["langs"]
    con = open_db(db_path)
    reset_tables(con, TABLES)
    rows = []
    for lang, lc in cfg.items():
        ldir = os.path.join(ROOT, lang)
        if not os.path.isdir(ldir):
            continue
        for fn in os.listdir(ldir):
            if not fn.endswith(".json"):
                continue
            d = load_json(os.path.join(ldir, fn))
            tpl = d["template"]
            fmap = lc["templates"].get(tpl)
            if not fmap:
                continue
            params = infobox_params(d["wikitext"], tpl)

            def pick(col):
                for k in fmap.get(col, []):
                    if params.get(k, "").strip():
                        return params[k]
                return ""
            death_text = strip_markup(pick("death"))
            rows.append((d["pageid"], lang, d["title"], tpl, strip_markup(pick("name_full")) or d["title"],
                         strip_markup(pick("kunya")), strip_markup(pick("laqab")),
                         strip_markup(pick("birthplace")), strip_markup(pick("residence")),
                         death_text, hijri_year(pick("death")),
                         json.dumps(split_names(pick("imams")), ensure_ascii=False),
                         json.dumps(split_names(pick("teachers")), ensure_ascii=False),
                         json.dumps(split_names(pick("students")), ensure_ascii=False),
                         strip_markup(pick("grade")), json.dumps(d.get("langlinks", {}), ensure_ascii=False),
                         d["wikitext"]))
    insert_many(con, "wikishia", ["page_id", "lang", "title", "template", "name_full", "kunya", "laqab",
                                  "birthplace", "residence", "death_text", "death_year", "imams_json",
                                  "teachers_json", "students_json", "grade", "interwiki_json", "raw_wikitext"], rows)
    con.commit()
    n = table_count(con, "wikishia")
    print(f"wikishia rows {n}")
    for col in ("name_full", "death_year", "residence", "birthplace", "teachers_json", "imams_json"):
        q = f"select lang, count(*) from wikishia where {col} is not null and {col} not in ('', '[]') group by lang"
        print(f"  {col}:", dict(con.execute(q).fetchall()))
