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
    assert await commands.project_delete(ctx, {"name": "piante"}) == {
        "name": "piante", "removed_crons": [],
    }


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
    assert await commands.project_delete(ctx, {"name": "piante"}) == {
        "name": "piante", "removed_crons": [],
    }


def test_a_folder_that_cannot_be_removed_says_why_without_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``str(OSError)`` porta il percorso assoluto della cartella privata
    dell'app, e il messaggio finisce nel toast: resta solo il perche'."""
    wikis = tmp_path / "wikis"
    (wikis / "piante" / "wiki").mkdir(parents=True)
    (wikis / "piante" / "wiki" / "index.md").write_text("# p", encoding="utf-8")
    secret_path = str(wikis / "piante" / "wiki" / "index.md")

    def _denied(path, *args, **kwargs):
        raise PermissionError(13, "Permission denied", secret_path)

    monkeypatch.setattr(module.shutil, "rmtree", _denied)
    with pytest.raises(module.ProjectDeleteError) as err:
        module.delete_project(
            wikis_dir=wikis,
            scripts_dir=tmp_path / "scripts",
            workspace=tmp_path,
            name="piante",
            invalidate_session=lambda _key: None,
        )
    message = str(err.value)
    assert "Permission denied" in message
    assert str(tmp_path) not in message


def test_the_commands_do_not_import_http_routes() -> None:
    """``commands.py`` di trasporti non sa niente: le pagine della casa le tocca
    da ``home_pages``, il modulo neutro, non da ``home_routes``."""
    import inspect

    from jenny.webui import apps_routes

    assert "home_routes" not in inspect.getsource(commands)
    assert not hasattr(apps_routes, "_stacca_la_pagina")


async def test_the_delete_removes_the_cron_jobs_of_its_cards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I cron delle schede se ne vanno col quaderno, anche quelli in pausa.

    Restando nello store fallivano a ogni giro con «notebook gone», e il cassetto
    da cui si toglievano non esisteva piu'. Gli altri — un altro quaderno, la
    chat personale, i job di sistema — restano. Servizio vero, sul loop (come in
    produzione: dal thread della cancellazione riarmare il timer solleverebbe).
    """
    from jenny.cron.service import CronService
    from jenny.cron.types import CronSchedule
    from jenny.webui import home_pages

    async def _no_page(kind, ref):
        return 0

    cron = CronService(tmp_path / "cron" / "jobs.json")
    await cron.start()
    try:
        every = CronSchedule(kind="every", every_ms=3_600_000)

        def add(name: str, board: dict | None) -> str:
            return cron.add_job(
                name=name, schedule=every, message="m", session_key="project:x",
                origin_channel="websocket", origin_chat_id="default", board=board,
            ).id

        mine = add("acqua", {"notebook": "piante", "card": "c1", "section": "s1"})
        paused = add("concime", {"notebook": "piante", "card": "c2", "section": "s2"})
        cron.set_paused(paused, True)
        other = add("semina", {"notebook": "orto", "card": "c1", "section": "s1"})
        personal = add("pane", None)

        monkeypatch.setattr(module, "delete_project", lambda **kw: {"name": kw["name"]})
        monkeypatch.setattr(commands, "_require_wiki_enabled", lambda: None)
        monkeypatch.setattr(home_pages, "detach_pages_of", _no_page)
        ctx = SimpleNamespace(
            get_workspace_root=lambda: tmp_path,
            invalidate_session=lambda k: None,
            busy_session_keys=lambda: (),
            get_cron_service=lambda: cron,
        )

        outcome = await commands.project_delete(ctx, {"name": "piante"})

        assert sorted(outcome["removed_crons"]) == sorted([mine, paused])
        left = {j.id for j in cron.list_jobs(include_disabled=True)}
        assert mine not in left and paused not in left
        assert {other, personal} <= left
    finally:
        cron.stop()


def test_without_a_cron_service_nothing_is_removed() -> None:
    assert module.remove_notebook_crons(None, "piante") == []
