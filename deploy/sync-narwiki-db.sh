#!/usr/bin/env bash
# One-time upload of the seeded narrator wiki database to the server (run on the laptop):
#
#   ./deploy/sync-narwiki-db.sh                 # preview (nothing is copied)
#   ./deploy/sync-narwiki-db.sh --yes           # upload; refuses if the server already has a wiki database
#   ./deploy/sync-narwiki-db.sh --yes --force   # replace the server's database (its edits are LOST:
#                                               #   first `python3 -m narwiki export-revisions` there and
#                                               #   `import-revisions` here)
#
# Build it first:  python3 -m narwiki seed && python3 -m narwiki similar
# A standalone copy is made with VACUUM INTO (no -wal / -shm files, consistent even while a dev server runs),
# uploaded next to the target and moved into place. After that the server's copy is the live one: edits made
# on the site exist only there (nightly backup: see README "The narrator wiki").
set -euo pipefail
HOST="${EBAD_DEPLOY_HOST:-ubuntu@132.145.99.75}"
DEST="${EBAD_DEPLOY_DEST:-ebadollah}"
SRC="${NARWIKI_DB:-$HOME/Downloads/hadith-project/narwiki-data/wiki.sqlite}"
YES=0; FORCE=0
for a in "$@"; do
  case "$a" in --yes) YES=1 ;; --force) FORCE=1 ;; *) echo "unknown argument $a" >&2; exit 2 ;; esac
done
[ -f "$SRC" ] || { echo "missing $SRC (python3 -m narwiki seed)" >&2; exit 2; }
TMPD="$(mktemp -d)"; trap 'rm -rf "$TMPD"' EXIT
COPY="$TMPD/wiki.sqlite"
python3 - "$SRC" "$COPY" <<'PYEOF'
import sqlite3, sys
src, dst = sys.argv[1:]
c = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
c.execute("vacuum into ?", (dst,))
c.close()
d = sqlite3.connect(dst)
d.execute("pragma journal_mode=delete")
ok = d.execute("pragma integrity_check").fetchone()[0]
n = d.execute("select count(*) from person where status='active'").fetchone()[0]
s = d.execute("select count(*) from merge_suggestion where status='proposed'").fetchone()[0]
d.close()
assert ok == "ok", ok
print(f"standalone copy: {n} active persons, {s} open suggestions, integrity {ok}")
PYEOF
ls -lh "$COPY" | awk '{print "size: " $5}'
REMOTE_DIR="$DEST/user/narwiki"
set +e; ssh -o BatchMode=yes "$HOST" "test -e ~/$REMOTE_DIR/wiki.sqlite"; RC=$?; set -e
[ "$RC" = 0 ] || [ "$RC" = 1 ] || { echo "cannot reach $HOST (ssh exit $RC)" >&2; exit 1; }
if [ "$RC" = 0 ]; then
  if [ "$FORCE" != 1 ]; then
    echo "the server already has ~/$REMOTE_DIR/wiki.sqlite; refusing (it holds the site's edits). --force replaces it." >&2
    exit 1
  fi
  echo "the server's wiki.sqlite will be REPLACED (--force)"
fi
if [ "$YES" != 1 ]; then
  echo "==> preview only: would upload $COPY to $HOST:~/$REMOTE_DIR/wiki.sqlite; re-run with --yes"
  exit 0
fi
ssh -o BatchMode=yes "$HOST" "mkdir -p ~/$REMOTE_DIR"
rsync -ah --partial --progress "$COPY" "$HOST:$REMOTE_DIR/wiki.sqlite.upload"
ssh -o BatchMode=yes "$HOST" "cd ~/$REMOTE_DIR && rm -f wiki.sqlite-wal wiki.sqlite-shm && mv -f wiki.sqlite.upload wiki.sqlite && ls -l wiki.sqlite"
echo "uploaded. If the site had the old database open (--force), restart it: sudo systemctl restart ebadollah-web"
