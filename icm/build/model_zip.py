"""icm/static/model.zip: the model code a browser (Pyodide) or the helper needs, and nothing else.

    python3 -m icm build model

Contents: hadith_phylogeny/ and icm/{__init__,config,infer,paths,client}.py. numpy comes from Pyodide itself.
tests/test_icm_client.py fails when the committed zip no longer matches the sources."""
from __future__ import annotations

import hashlib
import os
import zipfile

from .. import config

FILES = ["icm/__init__.py", "icm/config.py", "icm/infer.py", "icm/paths.py", "icm/client.py"]
OUT = os.path.join(config.HERE, "icm", "static", "model.zip")


def sources():
    out = []
    pk = os.path.join(config.HERE, "hadith_phylogeny")
    for f in sorted(os.listdir(pk)):
        if f.endswith(".py"):
            out.append(f"hadith_phylogeny/{f}")
    return out + FILES


def digest() -> str:
    h = hashlib.sha256()
    for rel in sources():
        h.update(rel.encode())
        h.update(open(os.path.join(config.HERE, rel), "rb").read())
    return h.hexdigest()[:16]


def build() -> str:
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in sources():
            zi = zipfile.ZipInfo(rel, date_time=(2026, 1, 1, 0, 0, 0))     # fixed time: reproducible zip
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, open(os.path.join(config.HERE, rel), "rb").read())
        z.writestr("MODEL_DIGEST", digest())
    return f"{OUT} {os.path.getsize(OUT)} bytes digest {digest()}"


def zip_digest() -> str:
    with zipfile.ZipFile(OUT) as z:
        return z.read("MODEL_DIGEST").decode()
