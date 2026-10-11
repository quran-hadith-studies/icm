"""Render an analysis bundle (outputs/viz_bundle.json) as a Persian, right-to-left viewer."""
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
<header class="top"><div class="wrap">
  <h1>__HEADING__</h1>
  <div class="sub" id="genline"></div>
  <div class="verdicts" id="verdicts"></div>
  <details class="explain"><summary>این اعداد چه می‌گویند؟</summary><dl id="explainlist"></dl></details>
  <nav class="tabs" id="tabs" role="tablist"></nav>
</div></header>
<div class="wrap">
  <section class="panel" id="panel-tree">
    <div class="explorer">
      <div class="card">
        <h2>درخت انتقال، آن‌گونه که در اسناد ثبت شده است</h2>
        <div class="toolbar">
          <input id="q" type="search" placeholder="جست‌وجوی راوی یا عبارت…" aria-label="جست‌وجو">
          <select id="branchsel" aria-label="نمایش یک طریق"></select>
          <button id="expandall" type="button">باز کردن همه</button>
          <button id="collapseall" type="button">بستن تا سطح صحابه</button>
          <span class="hint">کشیدن برای جابه‌جایی · ⌘/Ctrl + چرخ برای بزرگ‌نمایی · روی نام یا یال کلیک کنید</span>
        </div>
        <div id="treebox">
          <svg id="treesvg" role="img" aria-label="درخت انتقال، از راست به چپ"></svg>
          <div class="zoomer">
            <button type="button" id="zfit" title="نمایش کل درخت">کل درخت</button>
            <button type="button" id="zin" title="بزرگ‌نمایی" aria-label="بزرگ‌نمایی">+</button>
            <button type="button" id="zout" title="کوچک‌نمایی" aria-label="کوچک‌نمایی">−</button>
          </div>
        </div>
        <div class="legend">
          <span><i class="map"></i>یال در محتمل‌ترین درخت (MAP)</span>
          <span><i></i>احتمال پسین کمتر از ۱</span>
          <span><i class="low"></i>احتمال پسین کمتر از ۰٫۵</span>
          <span><span class="sw"></span>متن حدیث (شاهد)</span>
          <span>ضخامت یال = احتمال پسین · عدد کنار نام = سال وفات (ق) · شمار شاهدهای زیرین</span>
        </div>
      </div>
      <div class="card" id="inspector">
        <h2>جزئیات</h2>
        <div class="body" id="inspect"></div>
      </div>
    </div>
  </section>
  <section class="panel" id="panel-texts" hidden></section>
  <section class="panel" id="panel-sources" hidden></section>
  <section class="panel" id="panel-graph" hidden></section>
  <section class="panel" id="panel-sens" hidden></section>
  <section class="panel" id="panel-method" hidden></section>
  <section class="panel" id="panel-gloss" hidden></section>
</div>
<script id="bundle" type="application/json">__DATA__</script>
<script>
__JS__</script>
"""


def render(bundle: dict, standalone: bool = True) -> str:
    data = json.dumps(bundle, ensure_ascii=False).replace("</", "<\\/")
    title = bundle.get("title_fa", "درخت حدیث ستة لعنهم الله")
    heading = bundle.get("heading_fa", "حدیث «ستة لعنهم الله» — قدیمی‌ترین منبع قابل‌بازیابی")
    head = HEAD.replace("__TITLE__", title).replace("__CSS__", _asset("viewer.css"))
    body = (BODY.replace("__DATA__", data).replace("__HEADING__", heading)
            .replace("__JS__", _asset("viewer.js")))
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
