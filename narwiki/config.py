"""Paths and switches of the narrator wiki, overridable by environment.

Kept free of imports beyond the standard library: ``narwiki.lookup`` (imported by ICM) depends on it.

  NARWIKI_DB     the writable wiki database. Laptop default ``<hadith-project>/narwiki-data/wiki.sqlite``
                 (outside the repository); server ``~/ebadollah/user/narwiki/wiki.sqlite`` (set by
                 web/narwiki_routes.py; ``user/`` is never rsynced or deployed over).
  NARWIKI_OPEN   "1" (default): every visitor acts as an admin (anonymous ones as "guest"), like ICM today;
                 "0": writes need a login, reviews and reverts need an admin.
  NARWIKI_VERSIONS_DIR  where narrator-database versions live (default: ICM's narrators_versions directory).
  ICM_CACHE_DIR  where ICM keeps its projects; ``narrator_index.json`` there lists the ICM projects that
                 use each narrator id (read by the wiki's «پروژه‌های ICM»).
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # analysis/
PROJECT = os.path.dirname(HERE)                                              # hadith-project/

DB = os.environ.get("NARWIKI_DB", os.path.join(PROJECT, "narwiki-data", "wiki.sqlite"))
OPEN = os.environ.get("NARWIKI_OPEN", "1") != "0"
# narrator-database versions (one SQLite per version, rebuilt after every manual decision); same directory ICM reads
# (icm.config.NARRATOR_VERSIONS_DIR, duplicated here so this module stays import-light)
VERSIONS_DIR = os.environ.get("NARWIKI_VERSIONS_DIR") or os.environ.get("ICM_NARRATOR_VERSIONS_DIR") or os.path.join(
    os.environ.get("ICM_DATA_DIR", os.path.join(HERE, "external", "icm")), "narrators_versions")
ICM_CACHE_DIR = os.environ.get("ICM_CACHE_DIR", os.path.join(PROJECT, "icm-cache"))

# external id kinds the UI knows by name; any other kind is shown as is
KINDS = {"noor": "نور (جامع الاحادیث)", "kg": "گراف دانش", "dirayah": "درایة النور", "wiki": "ویکی"}
NOOR_OFFSET = 800_000_000          # Noor hadith ids inside v02 / the public /h/i/<id> pages


def narrators_db() -> str:
    """Noor-side narrator database (ICM's config.NARRATORS_DB)."""
    from icm import config as icm_config
    return icm_config.NARRATORS_DB


def hadithai_db() -> str:
    """hadith.ai v2's segmentation and narrator mentions (ICM's config.HADITHAI_DB)."""
    from icm import config as icm_config
    return icm_config.HADITHAI_DB


def dirayah_db() -> str:
    """Dirayat al-Noor 3 with its links to Noor (``python3 -m narrators dirayah``); ``NARWIKI_DIRAYAH_DB`` overrides."""
    if os.environ.get("NARWIKI_DIRAYAH_DB"):
        return os.environ["NARWIKI_DIRAYAH_DB"]
    from narrators import dirayah
    return dirayah.OUT


def kg_db() -> str:
    """The Hadith Knowledge Graph extract (ICM's config.KG_DB)."""
    from icm import config as icm_config
    return icm_config.KG_DB
