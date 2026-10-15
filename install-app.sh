#!/bin/sh
# One-time setup of the ICM desktop app (macOS, Linux): private Python environment + icm:// link registration.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/.icm_app/venv"
command -v python3 >/dev/null || { echo "python3 is required (https://www.python.org/downloads/)"; exit 1; }
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip numpy
"$VENV/bin/python" -m pip install --quiet pywebview || echo "(no native window available here: the app will open in a Chrome/Edge window instead)"
cd "$HERE"
"$VENV/bin/python" -m icm app install
echo "Done. On the website, open your project's waiting page and press «اجرا با برنامه‌ی رومیزی»."
echo "To update later: git pull in $HERE (the app reads the code from there)."
