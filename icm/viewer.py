"""The results viewer (the standalone page of scripts/make_html.py) for a viz bundle."""
from __future__ import annotations

import importlib.util
import os

from . import config

_mod = None


def render(bundle: dict) -> str:
    global _mod
    if _mod is None:
        path = os.path.join(config.HERE, "scripts", "make_html.py")
        spec = importlib.util.spec_from_file_location("icm_make_html", path)
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
    return _mod.render(bundle, standalone=True)
