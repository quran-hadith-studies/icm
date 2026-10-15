#!/usr/bin/env bash
# Upload the ICM data files (narrator database, KG extract, term frequencies, Noor relations, hadith.ai segments, and the
# narrator-database versions directory narrators_versions/) to the server.
# They are databases, so deploy.sh never ships them. Resumable (rsync --partial --inplace).
#
#   ./deploy/sync-icm-data.sh          # preview
#   ./deploy/sync-icm-data.sh --yes    # upload
#
# Source: analysis/external/icm/ of the quran-hadith-studies/icm checkout; build it with
#   python3 -m icm build all          (kg.sqlite, termdf.sqlite, noor_relations.sqlite, books_meta.json, hadithai.sqlite)
#   python3 -m narwiki version build v1m   (narrators_versions/v1m.sqlite; the server may build its own, see README)
# narrators.sqlite there is a standalone copy:  VACUUM INTO 'external/icm/narrators.sqlite'  (no WAL files).
set -euo pipefail
HOST="${EBAD_DEPLOY_HOST:-ubuntu@132.145.99.75}"
DEST="${EBAD_DEPLOY_DEST:-ebadollah}"
SRC="${ICM_DATA_SRC:-$HOME/Downloads/hadith-project/analysis/external/icm}"
FILES=(narrators.sqlite kg.sqlite noor_relations.sqlite termdf.sqlite books_meta.json hadithai.sqlite)
for f in "${FILES[@]}"; do [ -f "$SRC/$f" ] || { echo "missing $SRC/$f" >&2; exit 2; }; done
FLAGS=(-ah --partial --inplace --progress)
[ "${1:-}" = "--yes" ] || FLAGS+=(--dry-run)
ssh -o BatchMode=yes "$HOST" "mkdir -p ~/$DEST/data/icm"
cd "$SRC" && rsync "${FLAGS[@]}" "${FILES[@]}" "$HOST:$DEST/data/icm/"
if [ -d "$SRC/narrators_versions" ]; then
  rsync "${FLAGS[@]}" narrators_versions "$HOST:$DEST/data/icm/"
else
  echo "==> no $SRC/narrators_versions/ (build one with: python3 -m narwiki version build v1m); ICM on the server uses raw until it has a version file"
fi
[ "${1:-}" = "--yes" ] || echo "==> preview only; re-run with --yes"
