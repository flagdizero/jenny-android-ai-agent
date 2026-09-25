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
from jenny.webui import project_delete as module
from jenny.webui.commands import CommandError


async def test_the_command_refuses_while_someone_writes_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    touched: list[str] = []
    monkeypatch.setattr(module, "delete_project", lambda **kw: touched.append("delete"))
    monkeypatch.setattr(commands, "_require_wiki_enabled", lambda: None)
    ctx = SimpleNamespace(
        get_workspace_root=lambda: tmp_path,
        invalidate_session=lambda k: touched.append(k),
        busy_session_keys=lambda: ("project:piante", "unified:default"),
    )
    with pytest.raises(CommandError) as err:
        await commands.project_delete(ctx, {"name": "piante"})
    assert err.value.code == "conflict"
    assert touched == []


async def test_another_notebook_busy_does_not_stop_the_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jenny.webui import home_pages

    async def _no_page(kind, ref):
        return 0

    monkeypatch.setattr(module, "delete_project", lambda **kw: {"name": kw["name"]})
    monkeypatch.setattr(commands, "_require_wiki_enabled", lambda: None)
    monkeypatch.setattr(home_pages, "detach_pages_of", _no_page)
    ctx = SimpleNamespace(
        get_workspace_root=lambda: tmp_path,
        invalidate_session=lambda k: None,
        busy_session_keys=lambda: ("project:orto",),
    )
    assert await commands.project_delete(ctx, {"name": "piante"}) == {"name": "piante"}


async def test_a_page_that_cannot_be_taken_does_not_undo_the_notebook_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lo stesso involucro tollerante della cancellazione di un'app
    (``home_pages.detach_pages_quietly``): il quaderno e' gia' cancellato."""
    from jenny.webui import home_pages

    async def _broken(kind, ref):
        raise RuntimeError("disco pieno")

    monkeypatch.setattr(module, "delete_project", lambda **kw: {"name": kw["name"]})
    monkeypatch.setattr(commands, "_require_wiki_enabled", lambda: None)
    monkeypatch.setattr(home_pages, "detach_pages_of", _broken)
    ctx = SimpleNamespace(
        get_workspace_root=lambda: tmp_path,
        invalidate_session=lambda k: None,
        busy_session_keys=lambda: (),
    )
    assert await commands.project_delete(ctx, {"name": "piante"}) == {"name": "piante"}


def test_the_commands_do_not_import_http_routes() -> None:
    """``commands.py`` di trasporti non sa niente: le pagine della casa le tocca
    da ``home_pages``, il modulo neutro, non da ``home_routes``."""
    import inspect

    from jenny.webui import apps_routes

    assert "home_routes" not in inspect.getsource(commands)
    assert not hasattr(apps_routes, "_stacca_la_pagina")
