"""Stage 2a: fetch rijāl books that only exist as web pages (lib.eshia.ir) into
``external/datasets/shia-rijal-eshia/<book_id>/v{vol}_p{page}.html``.

Reuses the hub's polite fetcher (``hadith-hub/src/fetch.py``: user agent, page-shell
detection). Resumable: pages already on disk are skipped. One request every ``delay``
seconds. The last page of a volume is read from the «صفحه‌آخر» link of its first page.

    python3 -m narrators fetch --book 14036 --vols 1          # probe one volume
    python3 -m narrators fetch --book 14036                   # all 24 volumes of Khoei
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import List, Optional

from .common import DATASETS, HUB

sys.path.insert(0, os.path.join(HUB, "src"))
import fetch as hub_fetch  # noqa: E402

ROOT = os.path.join(DATASETS, "shia-rijal-eshia")
_LAST = re.compile(r'href="/(\d+)/(\d+)/(\d+)"[^>]*title="نمایش صفحه‌آخر"')
_VOLS = re.compile(r'<option value="(\d+)"')

KNOWN = {
    14036: "معجم رجال الحديث (الخوئي)",
    10508: "قاموس الرجال (التستري)",
    14021: "جامع الرواة (الأردبيلي)",
    12156: "مستدركات علم رجال الحديث (النمازي)",
}


def volume_info(book: int, vol: int):
    h = hub_fetch.fetch_page(book, vol, 1)
    m = _LAST.search(h)
    last = int(m.group(3)) if m else None
    vols = sorted({int(v) for v in _VOLS.findall(h)}) or [1]
    return last, vols, h


def fetch_book(book: int, vols: Optional[List[int]] = None, delay: float = 0.6, out_root: str = ROOT) -> None:
    bdir = os.path.join(out_root, str(book))
    os.makedirs(bdir, exist_ok=True)
    manifest_path = os.path.join(bdir, "manifest.json")
    manifest = json.load(open(manifest_path)) if os.path.exists(manifest_path) else {
        "book": book, "title": KNOWN.get(book), "source": "https://lib.eshia.ir", "volumes": {}}
    last, all_vols, first_html = volume_info(book, vols[0] if vols else 1)
    for v in (vols or all_vols):
        last, _, first_html = volume_info(book, v) if v != (vols[0] if vols else 1) else (last, all_vols, first_html)
        if not last:
            print(f"vol {v}: could not read the last-page link", file=sys.stderr)
            continue
        got = skipped = bad = 0
        t0 = time.time()
        for p in range(1, last + 1):
            path = os.path.join(bdir, f"v{v}_p{p}.html")
            if os.path.exists(path) and os.path.getsize(path) > 0:
                skipped += 1
                continue
            h = first_html if p == 1 else None
            for attempt in range(3):
                try:
                    h = h or hub_fetch.fetch_page(book, v, p)
                    break
                except Exception as e:  # noqa: BLE001
                    print(f"  v{v} p{p}: {e} (retry {attempt + 1})", file=sys.stderr)
                    time.sleep(3 * (attempt + 1))
            if not h:
                bad += 1
                continue
            if not hub_fetch.looks_like_real_page(h):
                bad += 1
            open(path, "w", encoding="utf-8").write(h)
            got += 1
            time.sleep(delay)
            if got % 50 == 0:
                print(f"  v{v}: {p}/{last} ({time.time() - t0:.0f}s)", file=sys.stderr)
        manifest["volumes"][str(v)] = {"last_page": last, "fetched": got + skipped, "shell_pages": bad,
                                       "fetched_at": time.strftime("%Y-%m-%d")}
        json.dump(manifest, open(manifest_path, "w"), ensure_ascii=False, indent=1)
        print(f"vol {v}: {last} pages, new {got}, cached {skipped}, shell/empty {bad}")
