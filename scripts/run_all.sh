#!/bin/sh
# Full pipeline: biographies -> model runs -> viewer bundle -> HTML
set -e
cd "$(dirname "$0")/.."
python3 external/match_narrators.py
python3 scripts/run_sitta.py 150
python3 scripts/run_branches.py 150
python3 scripts/run_matn_only.py
python3 scripts/export_viz.py 150
python3 scripts/make_html.py
