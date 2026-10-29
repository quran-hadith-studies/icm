"""Shared paths, database access and text helpers for the narrator pipeline."""
from __future__ import annotations

import glob
import gzip
import json
import os
import re
import sqlite3
import sys
import unicodedata
from typing import Iterable, Iterator, List

def _project_dir(analysis: str) -> str:
    """The folder that holds the shared data directories (dirayah3-data, narwiki-data, icm-cache, hadith-hub ...).
    $ICM_PROJECT wins; checked out beside the other ebadollah projects they still live in
    ~/Downloads/hadith-project; elsewhere it is the parent of this checkout."""
    env = os.environ.get("ICM_PROJECT")
    if env:
        return env
    parent = os.path.dirname(analysis)
    if os.path.basename(parent) == "ebadollah-project":
        return os.path.join(os.path.dirname(parent), "hadith-project")
    return parent


HERE = os.path.dirname(os.path.abspath(__file__))
ANALYSIS = os.path.dirname(HERE)
PROJECT = _project_dir(ANALYSIS)
HUB = os.path.join(PROJECT, "hadith-hub")
EXTERNAL = os.path.join(ANALYSIS, "external")
DATASETS = os.path.join(EXTERNAL, "datasets")
DATA_DIR = os.path.join(HERE, "data")
DB_PATH = os.path.join(EXTERNAL, "narrators.sqlite")
NOOR_SHARDS = os.path.join(HUB, "afreshte", "data", "jamiahadith4_hadith", "shard-*.ndjson.gz")
OPENITI_DIR = os.path.join(DATASETS, "openiti-shia-rijal")
SCHEMA_PATH = os.path.join(HERE, "schema.sql")

for p in (ANALYSIS, os.path.join(ANALYSIS, "external")):
    if p not in sys.path:
        sys.path.insert(0, p)
from hadith_phylogeny.collation import normalize_arabic  # noqa: E402

_DIGITS = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_DIGITS.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})


def ascii_digits(s: str) -> str:
    """Arabic-Indic and Persian digits to ASCII (NFKC does not do this)."""
    return s.translate(_DIGITS)


# Name normalisation. Same rules as external/match_narrators.py except that the Hijri
# marker «ق» is only dropped after a digit («287ق»), never at the end of a name
# (إسحاق، الصادق، رزق would lose their last letter).
STOP = {"بن", "بنت", "ابن"}
_NOISE = re.compile(r"\([^)]*\)|[0-9]+\s*ق\b|[0-9]+|[()،؛:.«»\"'\[\]]")


def norm_name(s: str) -> str:
    s = _NOISE.sub(" ", ascii_digits(s or ""))
    s = normalize_arabic(s)
    s = re.sub(r"\bاب[يى]\b", "ابو", s)
    s = re.sub(r"\bابا\b", "ابو", s)
    return re.sub(r"\s+", " ", s).strip()


def bare(t: str) -> str:
    """Article-insensitive token: الحسين and حسين are the same name."""
    return t[2:] if t.startswith("ال") and len(t) > 4 else t


def name_tokens(s: str) -> List[str]:
    return [bare(t) for t in norm_name(s).split() if t not in STOP and len(t) > 1]


def bigrams_ibn(s: str) -> List[str]:
    """'ابن وهب' -> 'بن وهب' so that it hits '... بن وهب' in a full name."""
    t = norm_name(s).split()
    return [f"بن {t[i + 1]}" for i in range(len(t) - 1) if t[i] in ("ابن", "بن")]


def as_list(value) -> list:
    """Funnel for list-shaped Noor fields that Elasticsearch collapses to a bare value.

    Copied from hadith-hub/src/convert_afreshte.py: a single-element array arrives as a
    scalar or dict, and iterating it walks characters or keys. Every list field of
    ``sanadInfo`` (``RaviList`` twice nested, ``InfoList``, ``InfoList2``, ``Text``,
    ``BookName``, ``SanadList``, ``Sanad``) goes through here before use.
    """
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def norm_key(s: str) -> str:
    """Comparison key for names: NFKC, no diacritics, Persian letters folded, noise removed."""
    if not s:
        return ""
    return norm_name(normalize_arabic(ascii_digits(s)))


def norm_text(s: str) -> str:
    """Comparison form for running text (keeps digits and punctuation out of the way)."""
    if not s:
        return ""
    t = normalize_arabic(ascii_digits(s))
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def iter_ndjson_gz(pattern: str) -> Iterator[dict]:
    for path in sorted(glob.glob(pattern)):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)


def write_json(path: str, obj, indent=1) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=indent)


def load_json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def data_file(name: str) -> str:
    return os.path.join(DATA_DIR, name)


# --- database -------------------------------------------------------------------------

def open_db(path: str = DB_PATH, readonly: bool = False) -> sqlite3.Connection:
    """Open the narrators database, creating any missing table from schema.sql.

    Unlike convert_afreshte.open_db this never deletes an existing file: stages are
    re-runnable and each one clears only its own tables (``reset_tables``).
    """
    if readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        con = sqlite3.connect(path)
        con.execute("pragma journal_mode = wal")
        con.execute("pragma synchronous = normal")
        con.executescript(open(SCHEMA_PATH, encoding="utf-8").read())
    con.row_factory = sqlite3.Row
    return con


def reset_tables(con: sqlite3.Connection, names: Iterable[str]) -> None:
    for n in names:
        con.execute(f'drop table if exists "{n}"')
    con.executescript(open(SCHEMA_PATH, encoding="utf-8").read())


def insert_many(con: sqlite3.Connection, table: str, cols: List[str], rows, batch: int = 20000,
                ignore: bool = False) -> int:
    verb = "insert or ignore" if ignore else "insert or replace"
    q = f'{verb} into "{table}" ({",".join(cols)}) values ({",".join("?" * len(cols))})'
    buf, n = [], 0
    for r in rows:
        buf.append(r)
        if len(buf) >= batch:
            con.executemany(q, buf)
            n += len(buf)
            buf = []
    if buf:
        con.executemany(q, buf)
        n += len(buf)
    return n


def table_count(con: sqlite3.Connection, table: str) -> int:
    return con.execute(f'select count(*) from "{table}"').fetchone()[0]


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")
