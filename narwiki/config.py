"""Paths and switches of the narrator wiki, overridable by environment.

Kept free of imports beyond the standard library: ``narwiki.lookup`` (imported by ICM) depends on it.

  NARWIKI_DB     the writable wiki database. Laptop default ``<hadith-project>/narwiki-data/wiki.sqlite``
                 (outside the repository); server ``~/ebadollah/user/narwiki/wiki.sqlite`` (set by
                 web/narwiki_routes.py; ``user/`` is never rsynced or deployed over).
  NARWIKI_OPEN   "1" (default): every visitor acts as an admin (anonymous ones as "guest"), like ICM today;
                 "0": writes need a login, reviews and reverts need an admin.
  ICM_CACHE_DIR  where ICM keeps its projects; ``narrator_index.json`` there lists the ICM projects that
                 use each narrator id (read by the wiki's «پروژه‌های ICM»).
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # analysis/
PROJECT = os.path.dirname(HERE)                                              # hadith-project/

DB = os.environ.get("NARWIKI_DB", os.path.join(PROJECT, "narwiki-data", "wiki.sqlite"))
OPEN = os.environ.get("NARWIKI_OPEN", "1") != "0"
ICM_CACHE_DIR = os.environ.get("ICM_CACHE_DIR", os.path.join(PROJECT, "icm-cache"))

# external id kinds the UI knows by name; any other kind is shown as is
KINDS = {"noor": "نور (جامع الاحادیث)", "kg": "گراف دانش", "wiki": "ویکی"}
NOOR_OFFSET = 800_000_000          # Noor hadith ids inside v02 / the public /h/i/<id> pages


def narrators_db() -> str:
    """Noor-side narrator database (ICM's config.NARRATORS_DB)."""
    from icm import config as icm_config
    return icm_config.NARRATORS_DB


def kg_db() -> str:
    """The Hadith Knowledge Graph extract (ICM's config.KG_DB)."""
    from icm import config as icm_config
    return icm_config.KG_DB
