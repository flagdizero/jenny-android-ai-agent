"""I comandi ``memory.*`` della stanza «Cosa ricorda di te».

Si passa da ``dispatch_command``: la famiglia ``memory`` si carica, i
parametri si controllano, e gli errori del servizio diventano codici che il
client sa dire (``conflict`` per una voce cambiata nel frattempo).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.agent.tools.memory_entries import entry_id
from jenny.webui import commands
from jenny.webui.commands import CommandError, dispatch_command

USER = "# User\n\n## Basics\n\n- Lives in Cremona\n"
CREMONA = entry_id("- Lives in Cremona")


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch):
    (tmp_path / "USER.md").write_text(USER, encoding="utf-8")
    (tmp_path / "memory").mkdir()
    monkeypatch.setattr(commands, "_require_workspace_flag", lambda *a, **k: None)
    return SimpleNamespace(get_workspace_root=lambda: tmp_path)


async def test_list_correct_remove_restore(ctx) -> None:
    listed = await dispatch_command(ctx, "memory.list", {})
    assert listed["files"][0]["entries"][0]["id"] == CREMONA

    out = await dispatch_command(ctx, "memory.correct",
                                 {"file": "user", "id": CREMONA, "text": "Lives in Lodi", "reason": "changed"})
    new_id = out["id"]
    assert (await dispatch_command(ctx, "memory.list", {}))["files"][0]["entries"][0]["locked"] is True

    await dispatch_command(ctx, "memory.remove", {"file": "user", "id": new_id})
    assert (await dispatch_command(ctx, "memory.list", {}))["files"][0]["entries"] == []
    back = await dispatch_command(ctx, "memory.restore", {"id": new_id})
    assert back["file"] == "user"


async def test_a_stale_id_is_a_conflict(ctx) -> None:
    with pytest.raises(CommandError) as err:
        await dispatch_command(ctx, "memory.remove", {"file": "user", "id": "deadbeef"})
    assert err.value.code == "conflict"


async def test_bad_params_are_refused(ctx) -> None:
    for method, params in (
        ("memory.remove", {"file": "user", "id": "../x"}),
        ("memory.remove", {"id": CREMONA}),
        ("memory.correct", {"file": "user", "id": CREMONA, "text": "", "reason": "changed"}),
        ("memory.restore", {"id": None}),
        ("memory.restore", {"id": CREMONA, "after": "a/b"}),
    ):
        with pytest.raises(CommandError) as err:
            await dispatch_command(ctx, method, params)
        assert err.value.code == "bad_request", (method, params)


async def test_writes_are_refused_when_workspace_writes_are_off(ctx, monkeypatch) -> None:
    def refuse(attr, code, message):
        raise CommandError(code, message)

    monkeypatch.setattr(commands, "_require_workspace_flag", refuse)
    monkeypatch.setattr("jenny.webui.memory_commands._require_workspace_flag", refuse)
    with pytest.raises(CommandError) as err:
        await dispatch_command(ctx, "memory.remove", {"file": "user", "id": CREMONA})
    assert err.value.code == "forbidden"


async def test_the_workshop_editor_saving_user_md_goes_through_the_archive(ctx) -> None:
    """Fino al 25/11/2026 l'editor dell'officina riscriveva ``USER.md`` senza
    lasciare traccia: una riga cancellata a mano spariva per sempre."""
    from jenny.agent.memory_archive import find_archived, read_archived

    root = ctx.get_workspace_root()
    await dispatch_command(ctx, "workspace.write",
                           {"path": "USER.md", "content": "# User\n\n## Basics\n", "base": USER})
    gone = read_archived(find_archived(root / "memory", CREMONA))
    assert gone is not None and gone.forgotten is True
    assert "Cremona" not in (root / "USER.md").read_text(encoding="utf-8")


async def test_a_conflicting_editor_save_archives_nothing(ctx) -> None:
    from jenny.agent.memory_archive import list_archived

    root = ctx.get_workspace_root()
    with pytest.raises(CommandError) as err:
        await dispatch_command(ctx, "workspace.write",
                               {"path": "USER.md", "content": "", "base": "something else"})
    assert err.value.code == "conflict"
    assert list_archived(root / "memory") == []
