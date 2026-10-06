"""Il cestino delle app: «Elimina» sposta, «Rimetti» torna, solo «Svuota» cancella."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jenny.apps.manifest import find_app, scan_apps
from jenny.apps.trash import list_trash, purge_app, restore_app, trash_app, trashed_entry
from jenny.board.sections import app_section_view


def _app(workspace: Path, slug: str = "orto", records: int = 2) -> Path:
    d = workspace / "apps" / slug
    (d / "app").mkdir(parents=True)
    (d / "data").mkdir()
    (d / "app.json").write_text(json.dumps({
        "name": "Orto", "description": "x",
        "section": {"value": {"collection": "letture", "field": "umidita"}},
    }))
    (d / "app" / "index.html").write_text("<p>orto</p>")
    (d / "data" / "letture.jsonl").write_text(
        "".join(json.dumps({"id": str(i), "ts": "2026-10-06T08:00:00Z", "umidita": 40 + i}) + "\n"
                for i in range(records)))
    return d


def test_delete_moves_the_app_and_its_data_to_the_trash(tmp_path: Path) -> None:
    _app(tmp_path)
    at = datetime(2026, 10, 6, 10, 15, tzinfo=timezone.utc)
    entry = trash_app(tmp_path, "orto", now=at)
    assert entry == "orto-20261006T101500Z"
    assert find_app(tmp_path, "orto") is None
    # Nel cestino non si vede: niente app, niente tool.
    assert [a.slug for a in scan_apps(tmp_path)] == []
    [listed] = list_trash(tmp_path)
    assert listed == {"id": entry, "slug": "orto", "name": "Orto",
                      "at": "2026-10-06T10:15:00+00:00", "records": 2}
    # Due eliminazioni nello stesso secondo non si pestano.
    _app(tmp_path)
    assert trash_app(tmp_path, "orto", now=at) == "orto-20261006T101501Z"


def test_restore_puts_it_back_with_its_data(tmp_path: Path) -> None:
    _app(tmp_path)
    entry = trash_app(tmp_path, "orto")
    assert restore_app(tmp_path, entry) == "orto"
    app = find_app(tmp_path, "orto")
    assert app is not None and not app.broken
    assert len((app.dir / "data" / "letture.jsonl").read_text().splitlines()) == 2
    assert list_trash(tmp_path) == []


def test_restore_refuses_a_taken_name_and_bad_ids(tmp_path: Path) -> None:
    _app(tmp_path)
    entry = trash_app(tmp_path, "orto")
    _app(tmp_path)
    with pytest.raises(FileExistsError):
        restore_app(tmp_path, entry)
    for bad in ("../orto", "orto", "orto-2026", ""):
        with pytest.raises(ValueError):
            restore_app(tmp_path, bad)
    with pytest.raises(FileNotFoundError):
        restore_app(tmp_path, "mai-20261006T101500Z")


def test_purge_is_the_only_way_out(tmp_path: Path) -> None:
    _app(tmp_path)
    entry = trash_app(tmp_path, "orto")
    assert purge_app(tmp_path, entry) == "orto"
    assert list_trash(tmp_path) == []
    assert not (tmp_path / "apps" / ".trash" / entry).exists()


def test_a_drawer_section_of_a_trashed_app_offers_to_put_it_back(tmp_path: Path) -> None:
    _app(tmp_path)
    entry = trash_app(tmp_path, "orto")
    view = app_section_view(tmp_path, "orto", {})
    assert view == {"state": "trashed", "slug": "orto", "name": "Orto", "trashId": entry}
    assert trashed_entry(tmp_path, "mai") is None
    purge_app(tmp_path, entry)
    assert app_section_view(tmp_path, "orto", {})["state"] == "missing"


async def test_the_trash_commands(tmp_path: Path) -> None:
    from jenny.webui.commands import CommandContext, CommandError, dispatch_command

    workspace = tmp_path
    ctx = CommandContext(get_workspace_root=lambda: workspace,
                         invalidate_session=lambda _key: None, busy_session_keys=lambda: ())
    _app(workspace)
    entry = trash_app(workspace, "orto")
    out = await dispatch_command(ctx, "apps.trash", {})
    assert [e["id"] for e in out["trash"]] == [entry]
    assert (await dispatch_command(ctx, "apps.restore", {"id": entry}))["slug"] == "orto"
    entry = trash_app(workspace, "orto")
    await dispatch_command(ctx, "apps.purge", {"id": entry})
    with pytest.raises(CommandError) as err:
        await dispatch_command(ctx, "apps.restore", {"id": entry})
    assert err.value.code == "not_found"
