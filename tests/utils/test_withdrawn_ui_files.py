"""Il codice della WebUI che il package non spedisce piu' esce da ``workspace/ui``.

Il difetto misurato sul Titan 2 il 25/09/2026, dopo il rinomino in inglese:
``extract_package_dir`` scrive i file del manifest e non cancella niente, quindi
``officina.html`` e i ``casa-*.js`` restavano sul disco col nome vecchio — e il
gateway li serviva ancora, perche' per un file fuori manifest ricade sul disco.
"""

from __future__ import annotations

import os
from pathlib import Path

from jenny.utils.android_assets import _UI_MANIFEST, retire_withdrawn_ui_files
from jenny.utils.helpers import sync_workspace_templates


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_renamed_shell_and_its_modules_leave_the_disk(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    stale = [
        _write(ui / "officina.html"),
        _write(ui / "assets" / "casa-app.js"),
        _write(ui / "assets" / "casa-style.css"),
        _write(ui / "assets" / "shared" / "gesto-orizzontale.js"),
    ]
    removed = retire_withdrawn_ui_files(ui)
    assert sorted(removed) == [
        "assets/casa-app.js", "assets/casa-style.css",
        "assets/shared/gesto-orizzontale.js", "officina.html",
    ]
    assert not any(p.exists() for p in stale)


def test_what_the_package_ships_stays(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    kept = [_write(ui / rel) for rel in _UI_MANIFEST if rel.endswith((".html", ".js", ".css"))]
    assert retire_withdrawn_ui_files(ui) == []
    assert all(p.exists() for p in kept)


def test_only_code_is_retired_not_fonts_or_images(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    font = _write(ui / "assets" / "vendor" / "fonts" / "vecchio.woff2")
    image = _write(ui / "assets" / "img" / "vecchia.png")
    assert retire_withdrawn_ui_files(ui) == []
    assert font.exists() and image.exists()


def test_a_symlink_is_left_alone(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    target = _write(tmp_path / "fuori" / "mio.js")
    (ui / "assets").mkdir(parents=True)
    link = ui / "assets" / "link.js"
    os.symlink(target, link)
    assert retire_withdrawn_ui_files(ui) == []
    assert link.is_symlink() and target.exists()


def test_a_missing_ui_dir_is_not_an_error(tmp_path: Path) -> None:
    assert retire_withdrawn_ui_files(tmp_path / "ui") == []


def test_the_startup_sync_runs_it(tmp_path: Path) -> None:
    """Il collegamento vero: senza, la funzione sarebbe verde e il telefono sporco."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    stale = _write(workspace / "ui" / "officina.html")
    sync_workspace_templates(workspace, silent=True)
    assert not stale.exists()
    assert (workspace / "ui" / "workshop.html").is_file()
