"""/icm — the hadith-family analysis wizard (quran-hadith-studies/icm).

The application code lives in its own repository, cloned next to this one on the server
(``~/ebadollah/icm``, override with EBAD_ICM_CODE). This module only adapts it to the site:
it puts the checkout on ``sys.path``, points it at the data directory, and tells it who is
logged in (``request.state.user``, set by the ``load_user`` middleware in app.py).

Imported defensively by app.py: if the checkout or its dependencies are missing, the rest of
the site is unaffected and /icm is simply absent.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HOME = Path(__file__).resolve().parent.parent
_CODE = os.environ.get("EBAD_ICM_CODE", str(_HOME / "icm"))
_DATA = os.environ.get("EBAD_ICM_DATA", str(_HOME / "data" / "icm"))

if not os.path.isdir(_CODE):
    raise ImportError(f"ICM code not found at {_CODE}")
if _CODE not in sys.path:
    sys.path.insert(0, _CODE)

os.environ.setdefault("ICM_DATA_DIR", _DATA)
os.environ.setdefault("ICM_CACHE_DIR", os.path.join(_DATA, "cache"))
os.environ.setdefault("ICM_V02_DB", str(_HOME / "data" / "v02" / "hadith.sqlite"))
os.environ.setdefault("ICM_CONSENSUS_DB", str(_HOME / "web" / "quran" / "data" / "hadith" / "v02" / "consensus.sqlite"))
os.environ.setdefault("ICM_NARRATORS_DB", os.path.join(_DATA, "narrators.sqlite"))

from icm.api import make_router  # noqa: E402


def _user(request):
    u = getattr(request.state, "user", None)
    if not u or not u.get("username"):
        return None
    return {"username": u["username"], "is_admin": bool(u.get("is_admin"))}


router = make_router(_user, prefix="/icm")
