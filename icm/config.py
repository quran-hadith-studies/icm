"""Paths and presets. Everything is overridable by environment so the same code runs on
the laptop (defaults) and on the server (ICM_* variables from config.env)."""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # analysis/
PROJECT = os.path.dirname(HERE)                                              # hadith-project/
EBAD = os.environ.get("ICM_EBADOLLAH", os.path.join(os.path.dirname(PROJECT), "ebadollah-project", "ebadollah"))

DATA_DIR = os.environ.get("ICM_DATA_DIR", os.path.join(HERE, "external", "icm"))
CACHE_DIR = os.environ.get("ICM_CACHE_DIR", os.path.join(PROJECT, "icm-cache"))

V02_DB = os.environ.get("ICM_V02_DB", os.path.join(EBAD, "data", "v02", "hadith.sqlite"))
CONSENSUS_DB = os.environ.get("ICM_CONSENSUS_DB", os.path.join(EBAD, "web", "quran", "data", "hadith", "v02", "consensus.sqlite"))
BOOKS_JSON = os.environ.get("ICM_V02_BOOKS", os.path.join(EBAD, "web", "quran", "data", "hadith", "v02", "books.json"))

KG_SRC = os.environ.get("ICM_KG_SRC", os.path.join(HERE, "external", "hadith-kg.db"))   # builder input only
KG_DB = os.environ.get("ICM_KG_DB", os.path.join(DATA_DIR, "kg.sqlite"))
TERMDF_DB = os.environ.get("ICM_TERMDF_DB", os.path.join(DATA_DIR, "termdf.sqlite"))
NOOR_REL_DB = os.environ.get("ICM_NOOR_REL_DB", os.path.join(DATA_DIR, "noor_relations.sqlite"))
BOOKS_META = os.environ.get("ICM_BOOKS_META", os.path.join(DATA_DIR, "books_meta.json"))
HADITHAI_DB = os.environ.get("ICM_HADITHAI_DB", os.path.join(DATA_DIR, "hadithai.sqlite"))   # hadith.ai v2 segments + narrator tags
NARRATORS_DB = os.environ.get("ICM_NARRATORS_DB", os.path.join(HERE, "external", "narrators.sqlite"))
HUB_BOOKS = os.environ.get("ICM_HUB_BOOKS", os.path.join(PROJECT, "hadith-hub", "books"))
# narrator database versions (built by ``python3 -m narwiki version build``): one read-only SQLite per merged version; "raw" needs no file
NARRATOR_VERSIONS_DIR = os.environ.get("ICM_NARRATOR_VERSIONS_DIR", os.path.join(DATA_DIR, "narrators_versions"))
NARRATOR_VERSION_DEFAULT = os.environ.get("ICM_NARRATOR_VERSION_DEFAULT", "v1m")      # used when its file exists, else "raw"
JOBS_DB = os.environ.get("ICM_JOBS_DB", os.path.join(DATA_DIR, "icm_jobs.sqlite"))

# open mode (for now): no login needed, every visitor may do anything; ICM_OPEN=0 restores the user rules
OPEN = os.environ.get("ICM_OPEN", "1") != "0"

# Noor hadith ids inside v02 are offset (convert_afreshte.py)
NOOR_OFFSET = 800_000_000

# SMC presets: (n_particles, n_steps, n_mcmc) by number of narrator nodes
PRESETS = {"thorough": (300, 20, 4), "standard": (150, 15, 3), "quick": (60, 8, 2),
           "tiny": (16, 4, 1)}          # tests and smoke runs only; the UI does not offer it


def preset_for(n_nodes: int) -> str:
    """Run time grows with narrators x candidate sources: 60 -> thorough, 150 -> standard, else quick."""
    return "thorough" if n_nodes <= 60 else "standard" if n_nodes <= 150 else "quick"

# bump when the corpus files change in a way that should invalidate cached searches
DATA_VERSION = "2026-10-14"      # v02 with the hadith.ai v2 corpus (Noor replaced, Dorar and sunnah.com added); hadithai.sqlite with narrator mentions
