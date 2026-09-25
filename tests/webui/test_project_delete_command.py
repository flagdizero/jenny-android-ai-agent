"""``project.delete`` rifiuta un quaderno sotto cui qualcuno sta scrivendo.

La stessa guardia di ``project.rename`` (v. ``tests/agent/test_busy_session_keys.py``):
sgomberare la cache non ferma chi ha gia' la sessione in mano — un turno, un
subagent, una passata del giardiniere, l'autocompact — e a fine lavoro la
salverebbe di nuovo, facendo rinascere come chat orfana il quaderno appena tolto.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.webui import commands
from jenny.webui import project_delete as modulo
from jenny.webui.commands import CommandError


async def test_the_command_refuses_while_someone_writes_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    toccato: list[str] = []
    monkeypatch.setattr(modulo, "delete_project", lambda **kw: toccato.append("delete"))
    monkeypatch.setattr(commands, "_require_wiki_enabled", lambda: None)
    ctx = SimpleNamespace(
        get_workspace_root=lambda: tmp_path,
        invalidate_session=lambda k: toccato.append(k),
        busy_session_keys=lambda: ("project:piante", "unified:default"),
    )
    with pytest.raises(CommandError) as err:
        await commands.project_delete(ctx, {"name": "piante"})
    assert err.value.code == "conflict"
    assert toccato == []


async def test_another_notebook_busy_does_not_stop_the_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jenny.webui import casa_routes

    async def _nessuna_pagina(kind, ref):
        return 0

    monkeypatch.setattr(modulo, "delete_project", lambda **kw: {"name": kw["name"]})
    monkeypatch.setattr(commands, "_require_wiki_enabled", lambda: None)
    monkeypatch.setattr(casa_routes, "stacca_pagine_di", _nessuna_pagina)
    ctx = SimpleNamespace(
        get_workspace_root=lambda: tmp_path,
        invalidate_session=lambda k: None,
        busy_session_keys=lambda: ("project:orto",),
    )
    assert await commands.project_delete(ctx, {"name": "piante"}) == {"name": "piante"}
