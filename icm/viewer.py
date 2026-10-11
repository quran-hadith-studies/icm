"""The results viewer (the standalone page of scripts/make_html.py) for a viz bundle."""
from __future__ import annotations

import importlib.util
import os

from hadith_phylogeny.report import finite

from . import config

_mod = None


def render(bundle: dict) -> str:
    global _mod
    if _mod is None:
        path = os.path.join(config.HERE, "scripts", "make_html.py")
        spec = importlib.util.spec_from_file_location("icm_make_html", path)
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
    b = finite(bundle)                                       # JSON has no -Infinity: null is read as -inf
    b.setdefault("title_fa", b.get("title") or b.get("family") or "ICM")
    b.setdefault("heading_fa", f"{b.get('heading') or b.get('family')} — قدیمی‌ترین منبع قابل‌بازیابی")
    return _mod.render(b, standalone=True)
