#!/usr/bin/env bash
# One-time (idempotent) installation of the ICM wizard on the server. Run ON the server:
#
#   bash ~/ebadollah/deploy/install_icm.sh
#
# deploy.sh pushes the whole workspace and the live files may differ from the repository copies,
# so the shared files (web/app.py, nginx) are patched IN PLACE here, with backups and a rollback.
# What it does:
#   1. web/app.py        include the /icm router (guarded: the site works without it)
#   2. nginx             add `location /icm` next to /h in every server block that has one; nginx -t; reload
#   3. systemd           install and enable icm-worker.service
# Prerequisites: ~/ebadollah/icm (git clone of quran-hadith-studies/icm), ~/ebadollah/data/icm/*.sqlite
# (deploy/sync-icm-data.sh), web/icm_routes.py and deploy/systemd/icm-worker.service in place.
set -euo pipefail
cd "$HOME/ebadollah"
STAMP="$(date +%Y%m%d-%H%M%S)"
PY="$HOME/.venv-ebad/bin/python"

echo "== 1. web/app.py"
mkdir -p ".deploy-bak/$STAMP/web"
cp -a web/app.py ".deploy-bak/$STAMP/web/app.py"
"$PY" - <<'PYEOF'
import re
p = "web/app.py"
s = open(p, encoding="utf-8").read()
if "web.icm_routes" in s:
    print("   already patched")
else:
    anchor = "app.include_router(crowdtadabbor_router)\n"
    assert s.count(anchor) == 1, "include point not found exactly once"
    block = anchor + '''
# ICM: hadith-family analysis wizard at /icm (code in its own repository; see web/icm_routes.py)
try:
    from web.icm_routes import router as icm_router
    app.include_router(icm_router)
except Exception as _icm_err:                     # optional: the rest of the site must not depend on it
    print(f"ICM disabled: {_icm_err}", flush=True)
'''
    open(p, "w", encoding="utf-8").write(s.replace(anchor, block))
    print("   patched")
PYEOF
echo "   import test (service interpreter)"
set -a; . ./config.env 2>/dev/null; set +a
"$PY" -c 'import web.app' || { echo "ERROR: web.app does not import; restoring app.py"; cp -a ".deploy-bak/$STAMP/web/app.py" web/app.py; exit 1; }

echo "== 2. nginx"
LIVE=/etc/nginx/conf.d/ebadollah.ir.conf
if grep -q "location /icm" "$LIVE"; then
  echo "   already has /icm"
else
  sudo -n cp "$LIVE" "/root/ebadollah.ir.conf.bak-$STAMP"
  cp "$LIVE" "/tmp/ebad.conf.new" 2>/dev/null || cp "$LIVE" "$HOME/ebad.conf.new"
  NEW="$([ -f /tmp/ebad.conf.new ] && echo /tmp/ebad.conf.new || echo $HOME/ebad.conf.new)"
  "$PY" - "$NEW" <<'PYEOF'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
block = '''    # ICM — hadith-family analysis wizard (quran-hadith-studies/icm).
    location /icm {
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
# insert after every `location /h/ { ... }` block
out, i, n = [], 0, 0
while True:
    j = s.find("    location /h/ {", i)
    if j < 0:
        out.append(s[i:]); break
    k = s.find("\n    }\n", j) + len("\n    }\n")
    out.append(s[i:k]); out.append(block); i = k; n += 1
open(p, "w", encoding="utf-8").write("".join(out))
print(f"   inserted {n} location block(s)")
PYEOF
  sudo -n cp "$NEW" "$LIVE"
  if sudo -n nginx -t 2>/dev/null; then
    sudo -n systemctl reload nginx; echo "   nginx reloaded (backup /root/ebadollah.ir.conf.bak-$STAMP)"
  else
    echo "   nginx -t FAILED; restoring"; sudo -n cp "/root/ebadollah.ir.conf.bak-$STAMP" "$LIVE"; sudo -n nginx -t && sudo -n systemctl reload nginx; exit 1
  fi
  rm -f "$NEW"
fi

echo "== 3. systemd"
sudo -n cp deploy/systemd/icm-worker.service /etc/systemd/system/icm-worker.service
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now icm-worker.service
sleep 3
systemctl is-active icm-worker.service || true
echo "== restart the web service so /icm is served"
sudo -n systemctl restart ebadollah-web.service
sleep 5
printf "   GET /icm/api/me -> "; curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8078/icm/api/me
printf "   GET /icm -> ";        curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8078/icm
echo "done. backups: ~/ebadollah/.deploy-bak/$STAMP and /root/ebadollah.ir.conf.bak-$STAMP"
