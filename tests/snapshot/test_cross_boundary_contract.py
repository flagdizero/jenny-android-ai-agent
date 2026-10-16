"""Contract test cross-boundary: costanti condivise tra Python, Kotlin e JS.

Il lato Kotlin (``MainActivity``) e il JS della WebUI non girano in CI:
questi test pinnano le stringhe letterali che i tre mondi devono condividere,
così una rinomina fatta da un lato solo fallisce qui invece che sul device.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from support.kotlin_source import read_source

from jenny.snapshot.backup import IMPORT_STAGED_FILENAME
from jenny.snapshot.locations import backup_staging_dir_for
from jenny.webui.backup_routes import BACKUP_DATA_HEADER

REPO_ROOT = Path(__file__).resolve().parents[2]
KOTLIN_MAIN = REPO_ROOT / "android/app/src/main/java/com/flagdizero/jenny/MainActivity.kt"
UI_ASSETS = REPO_ROOT / "jenny/templates/ui/assets"
BACKUP_FLOW_JS = UI_ASSETS / "shared/backup-flow.js"
API_CLIENT_JS = UI_ASSETS / "shared/api-client.js"

_STAGING_DIR_NAME = backup_staging_dir_for(Path("/x/workspace")).name


def _kotlin_source() -> str:
    if not KOTLIN_MAIN.is_file():
        pytest.skip("sorgente Android non presente in questo checkout")
    return read_source(KOTLIN_MAIN)


def _js(path: Path) -> str:
    assert path.is_file(), f"asset UI mancante: {path}"
    return path.read_text("utf-8")


# -- path di staging condiviso Python ↔ Kotlin -------------------------------------


def test_import_staged_path_matches_kotlin() -> None:
    """Kotlin deposita il file scelto dall'utente dove Python se lo aspetta."""
    assert IMPORT_STAGED_FILENAME == "import.jbk"
    assert f'"{_STAGING_DIR_NAME}/{IMPORT_STAGED_FILENAME}"' in _kotlin_source()


def test_kotlin_anti_traversal_guard_uses_same_staging_dir() -> None:
    assert f'"{_STAGING_DIR_NAME}"' in _kotlin_source()


# -- callback JS condivise Kotlin ↔ WebUI ------------------------------------------


def test_js_backup_callbacks_shared() -> None:
    kotlin = _kotlin_source()
    js = _js(BACKUP_FLOW_JS)
    # ``notifyJs`` compone ``window.<global>.<callback>``: il nome è il suo primo argomento.
    assert 'notifyJs("jennyBackup"' in kotlin
    assert "window.jennyBackup" in js
    for callback in ("onExportDone", "onImportPicked"):
        assert callback in kotlin, f"callback {callback} assente in MainActivity.kt"
        assert callback in js, f"callback {callback} assente in backup-flow.js"


# -- API HTTP condivise WebUI ↔ route Python ---------------------------------------


def test_api_client_uses_backup_header_and_routes() -> None:
    api_client = _js(API_CLIENT_JS)
    assert BACKUP_DATA_HEADER in api_client
    for route in (
        "/api/backup/export",
        "/api/backup/import",
        "/api/backup/snapshots",
        "/api/backup/snapshots/create",
        "/api/backup/snapshots/restore",
    ):
        assert route in api_client, f"route {route} assente in api-client.js"


# -- i18n: ogni chiave backup.* usata dal JS esiste in entrambe le lingue ----------


def _i18n_keys_used() -> set[str]:
    pattern = re.compile(r"t\(['\"]([A-Za-z0-9_.]+)['\"]")
    keys: set[str] = set()
    for path in (BACKUP_FLOW_JS, UI_ASSETS / "mobile-settings.js"):
        keys.update(pattern.findall(_js(path)))
    return {key for key in keys if key.startswith("backup.")}


def test_i18n_backup_keys_exist_in_both_locales() -> None:
    keys = _i18n_keys_used()
    assert keys, "nessuna chiave backup.* trovata nel JS: regex o file cambiati?"
    for locale in ("it", "en"):
        data = json.loads((UI_ASSETS / "i18n" / f"{locale}.json").read_text("utf-8"))
        for key in sorted(keys):
            node = data
            for part in key.split("."):
                assert isinstance(node, dict) and part in node, (
                    f"chiave i18n mancante in {locale}.json: {key}"
                )
                node = node[part]


# -- packaging: l'asset JS deve essere nel manifest UI -----------------------------


def test_backup_flow_js_in_ui_manifest() -> None:
    """Un asset fuori manifest non arriva sul device e la SPA maschera il 404."""
    from jenny.utils.android_assets import _UI_MANIFEST

    assert "assets/shared/backup-flow.js" in _UI_MANIFEST
