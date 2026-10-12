#!/usr/bin/env bash
# One-time (idempotent) installation of the narrator wiki (/narrator) on the server. Run ON the server, after
# `git pull --ff-only` in ~/ebadollah/icm (the narwiki package lives in that checkout):
#
#   bash ~/ebadollah/icm/deploy/install_narwiki.sh
#
# The shared files (web/app.py, nginx) are patched IN PLACE, with backups and a rollback, as install_icm.sh does.
#   0. checks: the checkout has narwiki/; no route of the site already starts with /narrator (the nginx prefix
#      match `location /narrator` would also catch /narrators...)
#   1. web/narwiki_routes.py   copied from the checkout
#   2. web/app.py              include the /narrator router right after the ICM block (guarded: the site works without it)
#   3. nginx                   `location /narrator` after every `location /icm` block; nginx -t; reload (or restore)
#   4. ~/ebadollah/user/narwiki  (the writable database goes here: deploy/sync-narwiki-db.sh, run on the laptop)
#   5. restart ebadollah-web; GET /narrator/api/me
# No new service and no worker: the wiki is served by the site's own process.
set -euo pipefail
cd "$HOME/ebadollah"
STAMP="$(date +%Y%m%d-%H%M%S)"
PY="$HOME/.venv-ebad/bin/python"
CODE="${EBAD_ICM_CODE:-$HOME/ebadollah/icm}"
LIVE=/etc/nginx/conf.d/ebadollah.ir.conf

echo "== 0. checks"
[ -d "$CODE/narwiki" ] || { echo "ERROR: $CODE/narwiki missing (git pull the icm checkout first)"; exit 1; }
[ -f "$CODE/deploy/narwiki_routes.py" ] || { echo "ERROR: $CODE/deploy/narwiki_routes.py missing"; exit 1; }
grep -q "web.icm_routes" web/app.py || { echo "ERROR: the ICM block is not in web/app.py (run install_icm.sh first)"; exit 1; }
if grep -rnE "[\"'](/narrator)" web --include='*.py' | grep -v "web/narwiki_routes.py" ; then
  echo "ERROR: the site already has routes starting with /narrator (listed above); /narrator would shadow them"; exit 1
fi
if grep -nE "location[[:space:]]+[=~^]*[[:space:]]*/narrator" "$LIVE" | grep -vq "location /narrator {"; then
  echo "ERROR: nginx already has another /narrator location"; exit 1
fi
echo "   ok"

echo "== 1. web/narwiki_routes.py"
mkdir -p ".deploy-bak/$STAMP/web"
[ -f web/narwiki_routes.py ] && cp -a web/narwiki_routes.py ".deploy-bak/$STAMP/web/narwiki_routes.py"
cp "$CODE/deploy/narwiki_routes.py" web/narwiki_routes.py
echo "   copied"

echo "== 2. web/app.py"
cp -a web/app.py ".deploy-bak/$STAMP/web/app.py"
"$PY" - <<'PYEOF'
p = "web/app.py"
s = open(p, encoding="utf-8").read()
if "web.narwiki_routes" in s:
    print("   already patched")
else:
    anchor = '    print(f"ICM disabled: {_icm_err}", flush=True)\n'
    assert s.count(anchor) == 1, "the end of the ICM include block was not found exactly once"
    block = anchor + '''
# Narrator wiki at /narrator (package narwiki in the ICM checkout; see web/narwiki_routes.py)
try:
    from web.narwiki_routes import router as narwiki_router
    app.include_router(narwiki_router)
except Exception as _narwiki_err:                 # optional: the rest of the site must not depend on it
    print(f"narrator wiki disabled: {_narwiki_err}", flush=True)
'''
    open(p, "w", encoding="utf-8").write(s.replace(anchor, block))
    print("   patched")
PYEOF
echo "   import test (service interpreter)"
set -a; . ./config.env 2>/dev/null; set +a
"$PY" -c 'import web.app' || { echo "ERROR: web.app does not import; restoring app.py"; cp -a ".deploy-bak/$STAMP/web/app.py" web/app.py; exit 1; }
"$PY" -c 'import web.app as a, sys; sys.exit(0 if any(str(getattr(r, "path", "")).startswith("/narrator") for r in a.app.routes) else 1)' \
  && echo "   /narrator routes registered" || echo "   WARNING: web.app imports but /narrator is not registered (see the 'narrator wiki disabled' message above)"

echo "== 3. nginx"
if grep -q "location /narrator {" "$LIVE"; then
  echo "   already has /narrator"
else
  sudo -n cp "$LIVE" "/root/ebadollah.ir.conf.bak-$STAMP"
  NEW="$(mktemp "$HOME/ebad.conf.XXXXXX")"
  cp "$LIVE" "$NEW"
  "$PY" - "$NEW" <<'PYEOF'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
block = '''    # Narrator wiki (package narwiki of quran-hadith-studies/icm).
    location /narrator {
        proxy_pass http://127.0.0.1:8078;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;
        proxy_read_timeout 300s;
    }
'''
# insert after every `location /icm { ... }` block (one per server block)
out, i, n = [], 0, 0
while True:
    j = s.find("    location /icm {", i)
    if j < 0:
        out.append(s[i:]); break
    k = s.find("\n    }\n", j)
    assert k > 0, "unterminated location /icm block"
    k += len("\n    }\n")
    out.append(s[i:k]); out.append(block); i = k; n += 1
assert n >= 1, "no `location /icm` block found (run install_icm.sh first)"
open(p, "w", encoding="utf-8").write("".join(out))
print(f"   inserted {n} location block(s)")
PYEOF
  sudo -n cp "$NEW" "$LIVE"
  rm -f "$NEW"
  if sudo -n nginx -t 2>/dev/null; then
    sudo -n systemctl reload nginx; echo "   nginx reloaded (backup /root/ebadollah.ir.conf.bak-$STAMP)"
  else
    echo "   nginx -t FAILED; restoring"; sudo -n cp "/root/ebadollah.ir.conf.bak-$STAMP" "$LIVE"; sudo -n nginx -t && sudo -n systemctl reload nginx; exit 1
  fi
fi

echo "== 4. data directory"
mkdir -p "$HOME/ebadollah/user/narwiki"
if [ -f "$HOME/ebadollah/user/narwiki/wiki.sqlite" ]; then
  echo "   wiki.sqlite present"
else
  echo "   wiki.sqlite NOT installed yet: run deploy/sync-narwiki-db.sh --yes on the laptop (the API answers 503 until then)"
fi

echo "== 5. restart the web service so /narrator is served"
sudo -n systemctl restart ebadollah-web.service
sleep 5
printf "   GET /narrator/api/me -> "; curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8078/narrator/api/me
printf "   GET /narrator -> ";        curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8078/narrator
echo "done. backups: ~/ebadollah/.deploy-bak/$STAMP and /root/ebadollah.ir.conf.bak-$STAMP"
