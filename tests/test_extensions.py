"""Le estensioni (``jenny/extensions.py``) viste dal core.

Due promesse:

- **Il core non contiene niente di un'estensione.** La build ``foss`` impacchetta
  solo ``jenny/`` e ``android/app/src/{main,foss}``: se lì dentro comparisse un
  pezzo del Drive, la versione libera lo conterrebbe. Lo si guarda nei sorgenti
  (qui) e nell'APK (``scripts/check_foss_apk.py``).
- **Il core funziona senza estensioni.** Sul Mac e in CI ``jenny_extensions/`` c'è
  sempre, quindi la build senza si simula con ``extensions.reset_for_tests(())``.

E quando un'estensione c'è, i suoi file WebUI si servono con le stesse regole di
quelli del core: dai byte del pacchetto, mai dalla copia su disco scrivibile.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from support.gateway_http import make_handler, make_request

from jenny import extensions
from jenny.utils.android_assets import (
    _UI_MANIFEST,
    extract_extension_ui,
    retire_withdrawn_ui_files,
    ui_sources,
)

ROOT = Path(__file__).resolve().parents[1]

# Le parole del Drive che il core non deve contenere: identificatori, non prosa
# (un commento che dice «la build Play ha la copia su Google Drive» non è codice).
DRIVE_IDENTIFIERS = (
    "cloudBackup", "jennyCloud", "connectDrive", "DriveBridge", "DriveExtension",
    "googleapis", "play-services", "cloud_backup", "jenny_extensions.drive",
    "jenny_extensions/drive", "cloud-backup.js", "cloud-backup-dialogs",
)
CORE_TREES = (
    ROOT / "jenny",
    ROOT / "android" / "app" / "src" / "main",
    ROOT / "android" / "app" / "src" / "foss",
)
TEXT_SUFFIXES = {".py", ".js", ".css", ".html", ".json", ".md", ".kt", ".xml", ".txt", ".j2"}


@pytest.fixture
def no_extensions() -> Iterator[None]:
    """Una build senza estensioni (il flavor ``foss``)."""
    extensions.reset_for_tests(())
    try:
        yield
    finally:
        extensions.reset_for_tests(None)


@pytest.fixture
def with_extensions() -> Iterator[None]:
    """Le estensioni di questo checkout, ricaricate."""
    extensions.reset_for_tests(None)
    try:
        yield
    finally:
        extensions.reset_for_tests(None)


# ── Il confine ──────────────────────────────────────────────────────────────


def _core_files() -> Iterator[Path]:
    for tree in CORE_TREES:
        for path in tree.rglob("*"):
            if path.is_file() and path.suffix in TEXT_SUFFIXES and "__pycache__" not in path.parts:
                yield path


def test_the_core_names_nothing_of_the_drive() -> None:
    hits = [
        f"{path.relative_to(ROOT)}: {word}"
        for path in _core_files()
        for word in DRIVE_IDENTIFIERS
        if word in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert not hits, "il core nomina il Drive, e la build foss lo conterrebbe:\n" + "\n".join(hits)


def test_only_one_core_module_knows_the_extensions_package() -> None:
    """Il core conosce il pacchetto delle estensioni per nome, in un posto solo."""
    knowers = sorted(
        str(path.relative_to(ROOT))
        for path in (ROOT / "jenny").rglob("*")
        if path.is_file() and path.suffix in TEXT_SUFFIXES
        and "jenny_extensions" in path.read_text(encoding="utf-8", errors="ignore")
    )
    assert knowers == ["jenny/extensions.py"], knowers


def test_the_drive_native_code_lives_in_the_play_source_set() -> None:
    play = ROOT / "android" / "app" / "src" / "play" / "java" / "com" / "flagdizero" / "jenny"
    assert (play / "DriveBridge.kt").is_file()
    assert (play / "DriveExtension.kt").is_file()
    gradle = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    deps = [line.strip() for line in gradle.splitlines() if "com.google.android.gms" in line]
    assert deps and all(line.startswith('"playImplementation"') for line in deps), deps


# ── Senza estensioni ────────────────────────────────────────────────────────


def test_without_extensions_nothing_extra_is_offered(no_extensions) -> None:
    assert extensions.load() == ()
    assert extensions.system_jobs() == {}
    assert extensions.bootstrap_info() == []
    assert set(ui_sources()) == set(_UI_MANIFEST)


async def test_without_extensions_the_routes_are_not_there(tmp_path, no_extensions) -> None:
    from jenny.config.schema import SnapshotConfig
    from jenny.snapshot.engine import SnapshotEngine
    from jenny.snapshot.service import SnapshotService

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = SnapshotService(SnapshotEngine(workspace, tmp_path / "snapshots"), SnapshotConfig())
    handler = make_handler(tmp_path / "skills", snapshot_service=service)
    assert handler.extension_routes == []
    path = "/api/backup/cloud"
    assert await handler._dispatch_misc_routes(None, make_request(path, "test-secret"), path) is None


def test_an_absent_extension_leaves_nothing_servable(tmp_path, no_extensions) -> None:
    """Ci arriva da una build che l'aveva: va via la cartella intera,
    traduzioni comprese, non solo il codice."""
    ui = tmp_path / "ui"
    drive = ui / "assets" / "ext" / "drive"
    (drive / "i18n").mkdir(parents=True)
    (drive / "home.js").write_text("x")
    (drive / "i18n" / "it.json").write_text("{}")

    removed = retire_withdrawn_ui_files(ui)

    assert "assets/ext/drive/" in removed
    assert not drive.exists()


# ── Con le estensioni di questo checkout ────────────────────────────────────


def test_each_extension_ui_manifest_matches_its_files(with_extensions) -> None:
    for ext in extensions.load():
        if ext.ui is None:
            continue
        package_dir = ROOT.joinpath(*ext.ui.package.split("."))
        on_disk = {
            p.relative_to(package_dir).as_posix()
            for p in package_dir.rglob("*")
            if p.is_file() and p.name != "__init__.py" and "__pycache__" not in p.parts
        }
        assert set(ext.ui.files) == on_disk, ext.name
        assert ext.ui.entry in ext.ui.files
        assert set(ext.ui.css) <= set(ext.ui.files)
        assert set(ext.ui.i18n.values()) <= set(ext.ui.files)


def test_extension_modules_import_only_what_is_served(with_extensions) -> None:
    """Un import relativo si risolve sull'URL: deve cadere su un file del core
    (``/html-mobile/assets/shared/…``, la stessa istanza della shell) o
    dell'estensione stessa. Altrimenti sul telefono è un 404 silenzioso."""
    served = set(ui_sources())
    for ext in extensions.load():
        if ext.ui is None:
            continue
        for rel in ext.ui.files:
            if not rel.endswith(".js"):
                continue
            url = ext.ui_url(rel)
            source = (ROOT.joinpath(*ext.ui.package.split(".")) / rel).read_text(encoding="utf-8")
            for target in re.findall(r"(?:from\s+|import\s*\(\s*)['\"](\.{1,2}/[^'\"]+)['\"]", source):
                resolved = Path("/" + url).parent.joinpath(target).resolve().as_posix().lstrip("/")
                assert resolved in served, f"{url} imports {target} → {resolved}, not served"


def test_bootstrap_points_the_shells_at_the_extension(with_extensions) -> None:
    info = {item["name"]: item for item in extensions.bootstrap_info()}
    for ext in extensions.load():
        if ext.ui is None:
            continue
        entry = info[ext.name]["entry"]
        assert entry.startswith("/html-mobile/assets/ext/"), entry
        assert entry.removeprefix("/html-mobile/") in ui_sources()


def test_extension_ui_is_extracted_and_survives_the_retirement(tmp_path, with_extensions) -> None:
    ui = tmp_path / "ui"
    assert extract_extension_ui(ui) > 0
    retire_withdrawn_ui_files(ui)
    for rel in extensions.ui_sources():
        assert (ui / rel).is_file(), rel


def test_tampered_extension_code_is_served_from_the_package(tmp_path, monkeypatch, with_extensions) -> None:
    """La copia su disco è scrivibile dai tool dell'agente; il codice che si
    serve è quello del pacchetto, per le estensioni come per il core."""
    from jenny.config import paths as paths_mod

    workspace = tmp_path / "workspace"
    monkeypatch.setattr(paths_mod, "get_workspace_path", lambda: workspace)
    extract_extension_ui(workspace / "ui")
    rel = next(r for r in extensions.ui_sources() if r.endswith(".js"))
    (workspace / "ui" / rel).write_text("alert('manomesso')", encoding="utf-8")

    handler = make_handler(tmp_path / "skills")
    response = handler._serve_static(f"/html-mobile/{rel}")

    assert response is not None and response.status_code == 200
    assert b"manomesso" not in response.body


async def test_extension_routes_need_the_token(tmp_path, with_extensions) -> None:
    handler = make_handler(tmp_path / "skills", snapshot_service=None)
    assert handler.extension_routes, "this checkout ships no extension with routes"
    for router in handler.extension_routes:
        path = router.prefix
        response = await handler._dispatch_misc_routes(None, make_request(path, None), path)
        assert response is not None and response.status_code == 401, path


def test_extension_settings_keep_their_own_key_names(tmp_path) -> None:
    """``extensions`` è un dict senza modelli: le chiavi interne restano come le
    scrive l'estensione, senza camelCase, e passano dal giro salva-rileggi."""
    from jenny.config.loader import load_config, save_config
    from jenny.config.schema import Config

    path = tmp_path / "config.json"
    config = Config()
    config.extensions = {"esempio": {"some_key": 1, "nested": {"other_key": "x"}}}
    save_config(config, path)

    raw = json.loads(path.read_text("utf-8"))
    assert raw["extensions"] == {"esempio": {"some_key": 1, "nested": {"other_key": "x"}}}
    assert load_config(path).extensions["esempio"]["some_key"] == 1
