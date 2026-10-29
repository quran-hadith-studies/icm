# Site-side files of the ICM wizard

These live in the **ebadollah** workspace (`/Users/mfo19/Downloads/ebadollah-data`, the old flat tree, not a git
repository) and on the server `ubuntu@132.145.99.75:~/ebadollah`. They are copies for reference; the live
ones are the ones that matter. Do **not** run `deploy/deploy.sh` of that workspace blindly: on 2026-10-11 a dry
run listed ~79 unrelated differing files, and it would overwrite server-only edits.

| File here | Lives at | What it is |
|---|---|---|
| `icm_routes.py` | `web/icm_routes.py` | adapter: puts `~/ebadollah/icm` on `sys.path`, sets `ICM_*` env defaults, takes the user from `request.state.user`, exposes `router` |
| `icm-worker.service` | `/etc/systemd/system/icm-worker.service` | the job worker (`python -m icm.worker`), `ICM_GIT=0`, memory limits |
| `install_icm.sh` | `deploy/install_icm.sh` | idempotent installer run ON the server: patches `web/app.py` (adds the guarded `include_router`), adds `location /icm` to the live nginx file (backup, `nginx -t`, rollback), installs and starts the unit, restarts the web service |
| `sync-icm-data.sh` | `deploy/sync-icm-data.sh` | uploads the data files (`narrators.sqlite`, `kg.sqlite`, `noor_relations.sqlite`, `termdf.sqlite`, `books_meta.json`, `hadithai.sqlite`, `lex.sqlite` (the root / word index of both corpora), and the directories `narrators_versions/` (merged narrator-database versions such as `v1m.sqlite`) and `vec/` (the semantic index and the query encoder) when present) from `analysis/external/icm/` to `~/ebadollah/data/icm/` |

Also changed in place: `web/quran/hadith-app.html` (the `/h` detail sheet fetches `/icm/api/hadith/v02:<id>/projects`
and shows the project badge; original saved on the server in `~/ebadollah/.deploy-bak/icm-badge/`).

## Root and semantic search (2026-10-26)

Built on the laptop, shipped with `sync-icm-data.sh`: `python -m icm build roots` (conda env with camel-tools), `build lex`
(`lex.sqlite`, 0.75 GB), `build vec --model BAAI/bge-m3` (`vec/`, hours on a GPU). The server needs `numpy` and `tokenizers`
(`pip install tokenizers` in the ICM virtualenv); no torch: `icm/embed_np.py` runs the encoder. Without `lex.sqlite` /
`vec/` everything falls back to the word search of before (`GET /icm/api/search-info` says what is on). After shipping,
restart `icm-worker` and the web service. Pressing «جستجو» again in a project uses the roots (the page says so); every
project's alignment is recomputed once (`STAGE_VERSIONS["align"]` 9).
