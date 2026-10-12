"""Render an analysis bundle (outputs/viz_bundle.json) as a Persian, right-to-left page: the results of step 6 of the
wizard (icm/static/results.js, tree.js) on a page of their own."""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(HERE, "icm", "static")


def _asset(name: str) -> str:
    return open(os.path.join(ASSETS, name), encoding="utf-8").read()


SKELETON_OPEN = """<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>:root{color-scheme:light}body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>
"""
SKELETON_MID = "</head>\n<body>\n"
SKELETON_CLOSE = "\n</body>\n</html>\n"

HEAD = r"""<title>__TITLE__</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;500;600;700&family=Noto+Naskh+Arabic:wght@400;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
__CSS__</style>
"""

BODY = r"""
<header class="top"><div class="wrap"><h1>__HEADING__</h1></div></header>
<main class="wrap" id="results"></main>
<script id="bundle" type="application/json">__DATA__</script>
<script>
__JS__</script>
"""


def render(bundle: dict, standalone: bool = True) -> str:
    data = json.dumps(bundle, ensure_ascii=False).replace("</", "<\\/")
    title = bundle.get("title_fa", "درخت حدیث ستة لعنهم الله")
    heading = bundle.get("heading_fa", "حدیث «ستة لعنهم الله» — قدیمی‌ترین منبع قابل‌بازیابی")
    css = "\n".join(_asset(f) for f in ("viewer.css", "tree.css", "results.css"))
    js = "\n".join(_asset(f) for f in ("help_fa.js", "tree.js", "recon.js", "results.js", "viewer.js"))
    head = HEAD.replace("__TITLE__", title).replace("__CSS__", css)
    body = BODY.replace("__DATA__", data).replace("__HEADING__", heading).replace("__JS__", js)
    if standalone:
        return SKELETON_OPEN + head + SKELETON_MID + body + SKELETON_CLOSE
    return head + body


def main():
    bundle = json.load(open(os.path.join(HERE, "outputs", "viz_bundle.json"), encoding="utf-8"))
    for name, standalone in (("sitta_viz.html", True), ("sitta_viz.fragment.html", False)):
        html = render(bundle, standalone)
        out = os.path.join(HERE, "outputs", name)
        open(out, "w", encoding="utf-8").write(html)
        print("written", out, round(len(html) / 1024), "KB")


if __name__ == "__main__":
    main()
