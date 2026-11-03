"""Layout su disco del sottosistema snapshot/backup.

Tutto ciò che deve SOPRAVVIVERE allo swap atomico del workspace (store degli
snapshot, staging, marker, copie di sicurezza) vive nella *runtime root*: la
directory che contiene il workspace (su Android è ``filesDir``, la stessa
``data_dir`` passata a ``run_gateway``).

    <runtime_root>/
      workspace/               ← l'albero versionato/scambiato al restore
      snapshots/               ← store content-addressed (storia)
      plans.migrated/          ← i piani di prima di ``undo.sqlite``, per 30 giorni
      db/                      ← lo stato in SQLite (``jenny/db``): state, undo
      map_tiles/               ← le tessere delle schede dei posti in chat (cache)
      backup_staging/          ← file .jbk in export/import + albero staged
      restore_pending.json     ← marker del protocollo di restore al boot
      workspace_pre_restore_*  ← copie di sicurezza del workspace sostituito
"""

from __future__ import annotations

from pathlib import Path

STAGED_WORKSPACE_DIR_NAME = "workspace_staged"
STAGED_SNAPSHOTS_DIR_NAME = "snapshots_staged"
MARKER_FILE_NAME = "restore_pending.json"
SAFETY_DIR_PREFIX = "workspace_pre_restore_"
SANITY_FILE_NAME = ".jenny_restore_manifest.json"

# Dove stavano i piani a lista (``jenny/plan/``) fino alla 2.x, un
# ``plan.json`` per cartella: ora sono righe di ``undo.sqlite`` accanto al
# diario dell'annulla (``plan/store.py``), e la cartella la importa una volta
# ``db_migration/plans.py``. Resta il nome per chi la cerca.
PLANS_DIR_NAME = "plans"


def runtime_root_for(workspace_path: Path) -> Path:
    return Path(workspace_path).resolve().parent


def snapshots_dir_for(workspace_path: Path) -> Path:
    return runtime_root_for(workspace_path) / "snapshots"


def backup_staging_dir_for(workspace_path: Path) -> Path:
    return runtime_root_for(workspace_path) / "backup_staging"


def plans_dir_for(workspace_path: Path) -> Path:
    return runtime_root_for(workspace_path) / PLANS_DIR_NAME


# Le tessere della mappa (``jenny/runtime/map_tiles.py``): una cache, fuori dal
# workspace perché non è memoria — niente snapshot, niente backup.
MAP_TILES_DIR_NAME = "map_tiles"


def map_tiles_dir_for(workspace_path: Path) -> Path:
    return runtime_root_for(workspace_path) / MAP_TILES_DIR_NAME


# Lo stato di Jenny in SQLite (``jenny/db``). Fuori dal workspace come i piani:
# i tool file del modello non ci arrivano, e lo swap di un ripristino non lo
# porta via insieme al workspace (lo scambia ``restore_marker`` a parte).
DB_DIR_NAME = "db"
STAGED_DB_DIR_NAME = "db_staged"


def db_dir_for_runtime_root(runtime_root: Path) -> Path:
    return Path(runtime_root) / DB_DIR_NAME


def db_dir_for(workspace_path: Path) -> Path:
    return db_dir_for_runtime_root(runtime_root_for(workspace_path))
