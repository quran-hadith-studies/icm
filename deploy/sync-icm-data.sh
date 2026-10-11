#!/usr/bin/env bash
# Upload the ICM data files (narrator database, KG extract, term frequencies, Noor relations) to the server.
# They are databases, so deploy.sh never ships them. Resumable (rsync --partial --inplace).
#
#   ./deploy/sync-icm-data.sh          # preview
#   ./deploy/sync-icm-data.sh --yes    # upload
#
# Source: analysis/external/icm/ of the quran-hadith-studies/icm checkout; build it with
#   python3 -m icm build all          (kg.sqlite, termdf.sqlite, noor_relations.sqlite, books_meta.json)
# narrators.sqlite there is a standalone copy:  VACUUM INTO 'external/icm/narrators.sqlite'  (no WAL files).
set -euo pipefail
HOST="${EBAD_DEPLOY_HOST:-ubuntu@132.145.99.75}"
DEST="${EBAD_DEPLOY_DEST:-ebadollah}"
SRC="${ICM_DATA_SRC:-$HOME/Downloads/hadith-project/analysis/external/icm}"
FILES=(narrators.sqlite kg.sqlite noor_relations.sqlite termdf.sqlite books_meta.json)
for f in "${FILES[@]}"; do [ -f "$SRC/$f" ] || { echo "missing $SRC/$f" >&2; exit 2; }; done
FLAGS=(-ah --partial --inplace --progress)
[ "${1:-}" = "--yes" ] || FLAGS+=(--dry-run)
ssh -o BatchMode=yes "$HOST" "mkdir -p ~/$DEST/data/icm"
cd "$SRC" && rsync "${FLAGS[@]}" "${FILES[@]}" "$HOST:$DEST/data/icm/"
[ "${1:-}" = "--yes" ] || echo "==> preview only; re-run with --yes"
