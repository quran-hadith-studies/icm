"""Stage 2: rijāl texts → ``entry`` (one row per biography) and ``page_text``.

OpenITI mARkdown (``external/datasets/openiti-shia-rijal``):
  * everything before ``#META#Header#End#`` is metadata;
  * ``### | …`` / ``### || …`` / ``### ||| …`` are section headings (level 1–3);
  * ``### $ …`` opens a biography; the heading pattern after ``### $`` differs per book
    and is given in ``data/books.json`` (``entry_regex``);
  * body lines start with ``# `` (paragraph) or ``~~`` (continuation);
  * ``PageVxxPyyy`` **closes** page yyy of volume xx: text before the marker is on that
    page. An entry's ``page_start`` is therefore the page of the first marker at or after
    its heading line, and ``page_end`` the page of the first marker at or after its last
    body line.

Books whose OpenITI file has no ``### $`` markers (``entry_regex`` null) are stored page
by page in ``page_text`` so a Noor citation can at least show the page.

Khoei (معجم رجال الحديث) and the other eshia books are parsed from the fetched HTML
pages (see ``rijal_fetch.py``) by ``parse_eshia_khoei``.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

from .common import (DATASETS, OPENITI_DIR, ascii_digits, data_file, insert_many, load_json, norm_key,
                     open_db, table_count)

TABLES = ["entry", "page_text"]
_MARKER = re.compile(r"PageV(\d+)P(\d+)")
_NOISE = re.compile(r"ms\d+|PageV\d+P\d+|NoteV\d+P\d+N\d+\s*-?|\(\s*\d+\s*\)|\[\s*\]|\s\d+(?=[\s،.:;]|$)")
_HEAD_CUT = re.compile(r"[،.:؛(]| - |\s-\s")
_IMAM_ALIASES: Optional[List[Tuple[str, re.Pattern]]] = None


def _imam_aliases() -> List[Tuple[str, re.Pattern]]:
    global _IMAM_ALIASES
    if _IMAM_ALIASES is None:
        d = load_json(data_file("imam_reigns.json"))
        out = []
        for key, info in d["imams"].items():
            pats = [re.escape(norm_key(a)) for a in info.get("section_aliases", [])]
            if pats:
                out.append((key, re.compile("|".join(pats))))
        _IMAM_ALIASES = out
    return _IMAM_ALIASES


def imam_of_section(text: str) -> Optional[str]:
    k = norm_key(text or "")
    for key, pat in _imam_aliases():
        if pat.search(k):
            return key
    return None


def clean_heading(s: str) -> str:
    s = _NOISE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" -–—:،.")
    m = _HEAD_CUT.search(s)
    if m and m.start() > 0:
        s = s[: m.start()]
    return s.strip(" -–—:،.\"'«»")


def clean_body(lines: List[str]) -> str:
    out = []
    for ln in lines:
        t = ln
        if t.startswith("~~"):
            t = t[2:]
        elif t.startswith("# "):
            t = t[2:]
        elif t.startswith("#"):
            t = t[1:]
        t = _MARKER.sub(" ", t)
        t = re.sub(r"ms\d+", " ", t)
        out.append(t.strip())
    return re.sub(r"\s+", " ", " ".join(out)).strip()


def parse_openiti(path: str, book: str, entry_regex: Optional[str]):
    """Return (entries, pages). entries: dicts for ``entry``; pages: {(vol,page): text}."""
    lines = open(path, encoding="utf-8-sig").read().split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("#META#Header#End#")) + 1
    except StopIteration:
        start = 0
    # page markers in document order: (line_idx, vol, page)
    markers = [(i, int(m.group(1)), int(m.group(2)))
               for i in range(start, len(lines)) for m in _MARKER.finditer(lines[i])]

    def page_at(line_idx: int) -> Tuple[Optional[int], Optional[int]]:
        for li, vol, page in markers:          # first marker at/after the line closes its page
            if li >= line_idx:
                return vol, page
        if markers:
            return markers[-1][1], markers[-1][2] + 1
        return None, None

    ereg = re.compile(entry_regex) if entry_regex else None
    entries, pages = [], {}
    section = subsection = None
    cur = None
    page_buf: Dict[Tuple[int, int], List[str]] = {}

    def close():
        nonlocal cur
        if cur is None:
            return
        v1, p1 = page_at(cur["line_no"])
        v2, p2 = page_at(cur["last_line"])
        cur["vol"], cur["page_start"], cur["page_end"] = v1, p1, p2
        cur["body"] = clean_body(cur["body_lines"])
        del cur["body_lines"], cur["last_line"]
        entries.append(cur)
        cur = None

    for i in range(start, len(lines)):
        ln = lines[i]
        if ln.startswith("### |"):
            close()
            level = len(ln) - len(ln.lstrip("#")) and (len(re.match(r"### (\|+)", ln).group(1)))
            text = _MARKER.sub("", ln.split("|" * level, 1)[1]).strip()
            text = re.sub(r"ms\d+", "", text).strip()
            if level == 1:
                section, subsection = text, None
            else:
                subsection = text
            continue
        if ln.startswith("### $") and ereg is not None:
            close()
            rest = ln[5:].strip()
            rest = re.sub(r"^NoteV\d+P\d+N\d+\s*-\s*", "", rest)      # Ghadairi note anchors
            m = ereg.match(rest)
            if not m:
                # keep the entry anyway, unnumbered
                entry_no, head = None, rest
            else:
                entry_no, head = int(ascii_digits(m.group(1))), m.group(2)
            heading = clean_heading(head)
            cur = dict(book=book, entry_no=entry_no, section=section, subsection=subsection,
                       imam=imam_of_section(section) if book == "tusi_rijal" else None,
                       heading=heading, heading_key=norm_key(heading), line_no=i,
                       body_lines=[head], last_line=i)
            continue
        if ln.startswith("### "):
            close()
            continue
        if cur is not None and (ln.startswith("# ") or ln.startswith("~~") or ln.startswith("#")):
            cur["body_lines"].append(ln)
            cur["last_line"] = i
        # page text for unmarked books (and as a fallback for all)
        if ereg is None and (ln.startswith("# ") or ln.startswith("~~") or ln.startswith("#")):
            v, p = page_at(i)
            if v is not None:
                page_buf.setdefault((v, p), []).append(ln)
    close()
    for k, buf in page_buf.items():
        pages[k] = clean_body(buf)
    return entries, pages


# ----------------------------------------------------------------------------- eshia

_ESHIA_CONTENT = re.compile(r'<td class="book-page-show"[^>]*>(.*?)</td>', re.S)
_ESHIA_TOOLBAR = re.compile(r'<div class="sticky-menue".*?</div>\s*', re.S)


def eshia_page_paragraphs(raw_html: str) -> List[str]:
    m = _ESHIA_CONTENT.search(raw_html)
    if not m:
        return []
    inner = _ESHIA_TOOLBAR.sub("", m.group(1), count=1)
    paras = re.split(r"</p>|<br\s*/?>", inner)
    out = []
    for p in paras:
        t = re.sub(r"<[^>]+>", " ", p)
        t = html.unescape(t)
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            out.append(t)
    return out


def eshia_pages(book_dir: str):
    """Yield (vol, page, paragraphs) over a fetched eshia book directory, in order."""
    files = []
    for fn in os.listdir(book_dir):
        m = re.match(r"v(\d+)_p(\d+)\.html$", fn)
        if m:
            files.append((int(m.group(1)), int(m.group(2)), fn))
    for vol, page, fn in sorted(files):
        raw = open(os.path.join(book_dir, fn), encoding="utf-8", errors="replace").read()
        yield vol, page, eshia_page_paragraphs(raw)


_KHOEI_HEAD = re.compile(r"^\s*(\d{1,5})\s*[-–—]\s*(.+)$")
_ESHIA_HEADER = re.compile(r"الناشر\s*:|^تألیف$|^تأليف$|^معجم رجال الحديث$|^السيد أبو القاسم الموسوي الخوئي$")


def parse_eshia_khoei(book_dir: str, book: str = "khoei"):
    """Khoei's entries are numbered «N - name:» paragraphs; a page carries several.

    The numbering runs through the whole book (1 = آدم), so a paragraph is an entry only
    when its number continues the sequence (a jump of at most 3 is tolerated for a
    missed paragraph). That drops the numbered sections of the introduction, footnotes
    («2 - …») and numbered mentions. Page headers (title/author/publisher) are removed.
    """
    entries, pages = [], {}
    cur = None
    expected = None
    for vol, page, paras in eshia_pages(book_dir):
        paras = [p for p in paras if not _ESHIA_HEADER.search(p.strip())]
        pages[(vol, page)] = " ".join(paras)
        for para in paras:
            t = ascii_digits(para)
            m = _KHOEI_HEAD.match(t)
            ok = False
            if m:
                n = int(m.group(1))
                if expected is None:
                    ok = n == 1 and m.group(2).lstrip().startswith("آدم")
                else:
                    ok = expected <= n <= expected + 3
            if ok:
                expected = int(m.group(1)) + 1
                if cur:
                    cur["body"] = re.sub(r"\s+", " ", " ".join(cur["body_lines"])).strip()
                    del cur["body_lines"]
                    entries.append(cur)
                heading = clean_heading(m.group(2))
                cur = dict(book=book, entry_no=int(m.group(1)), vol=vol, page_start=page, page_end=page,
                           section=None, subsection=None, imam=None, heading=heading,
                           heading_key=norm_key(heading), line_no=vol * 100000 + page, body_lines=[m.group(2)])
            elif cur:
                cur["body_lines"].append(para)
                cur["page_end"] = page
    if cur:
        cur["body"] = re.sub(r"\s+", " ", " ".join(cur["body_lines"])).strip()
        del cur["body_lines"]
        entries.append(cur)
    return entries, pages


# ----------------------------------------------------------------------------- driver

def run(db_path: str, only: Optional[List[str]] = None, eshia_root: Optional[str] = None) -> None:
    con = open_db(db_path)
    books = load_json(data_file("books.json"))["books"]
    if only:
        for b in only:
            con.execute("delete from entry where book = ?", (b,))
            con.execute("delete from page_text where book = ?", (b,))
    else:
        from .common import reset_tables
        reset_tables(con, TABLES)
    eshia_root = eshia_root or os.path.join(DATASETS, "shia-rijal-eshia")
    for key, cfg in books.items():
        if only and key not in only:
            continue
        if cfg.get("openiti"):
            path = os.path.join(OPENITI_DIR, cfg["openiti"])
            if not os.path.exists(path):
                print(f"{key}: missing {path}", file=sys.stderr)
                continue
            entries, pages = parse_openiti(path, key, cfg.get("entry_regex"))
        elif key == "khoei":
            bdir = os.path.join(eshia_root, "14036")
            if not os.path.isdir(bdir):
                print(f"{key}: not fetched yet ({bdir})", file=sys.stderr)
                continue
            entries, pages = parse_eshia_khoei(bdir, key)
        else:
            continue
        insert_many(con, "entry", ["book", "entry_no", "vol", "page_start", "page_end", "section",
                                   "subsection", "imam", "heading", "heading_key", "body", "line_no"],
                    [(e["book"], e["entry_no"], e.get("vol"), e.get("page_start"), e.get("page_end"),
                      e.get("section"), e.get("subsection"), e.get("imam"), e["heading"], e["heading_key"],
                      e["body"], e["line_no"]) for e in entries])
        insert_many(con, "page_text", ["book", "vol", "page", "text"],
                    [(key, v, p, t) for (v, p), t in pages.items()])
        # page monotonicity check
        viol = sum(1 for a, b in zip(entries, entries[1:])
                   if a.get("page_start") and b.get("page_start") and (b["vol"], b["page_start"]) < (a["vol"], a["page_start"]))
        print(f"{key}: {len(entries)} entries, {len(pages)} pages, page-order violations {viol}")
    con.commit()
    print("entry rows", table_count(con, "entry"), "page_text rows", table_count(con, "page_text"))
