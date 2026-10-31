"""I cron di un quaderno rinominato lo seguono: link della bacheca e chat di consegna.

Prima del 20/10/2026 un rinomino lasciava i job col nome vecchio: ogni giro
falliva con «notebook gone» (``cron/bound_runner.py``) e l'avviso, se partiva,
andava a ``project:<vecchio>``, una chat che non c'è più. Le strade del rinomino
sono tre — il comando della casa, la cartella rinominata a mano che la chat
insegue, la ripresa all'avvio — e passano tutte da
``cron_link.follow_renamed_notebook``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from jenny.board import cron_link, model
from jenny.cron.service import CronService
from jenny.cron.types import CronSchedule

OLD, NEW = "orto", "giardino"
EVERY = CronSchedule(kind="every", every_ms=3_600_000)


def _add(cron: CronService, name: str, *, notebook: str | None, scope: str = "",
         chat: str | None = None) -> str:
    chat = chat or (f"project:{notebook}" if notebook else "unified:default")
    return cron.add_job(
        name=name, schedule=EVERY, message="m", session_key=chat, origin_channel="websocket",
        origin_chat_id=chat,
        board=cron_link.make_link(notebook, scope) if notebook else None,
    ).id


def _jobs(cron: CronService) -> dict[str, object]:
    return {j.id: j for j in cron.list_jobs(include_disabled=True)}


async def test_both_scopes_follow_the_new_name_and_nothing_else_moves(tmp_path: Path) -> None:
    cron = CronService(tmp_path / "cron" / "jobs.json")
    await cron.start()
    try:
        card = _add(cron, "acqua", notebook=OLD, scope="basilico")
        whole = _add(cron, "gelo", notebook=OLD, scope=model.NOTEBOOK)
        cron.set_paused(card, True)
        other = _add(cron, "semina", notebook="frutteto")
        personal = _add(cron, "pane", notebook=None)
        # Un promemoria chiesto nella chat del quaderno prima che esistesse la bacheca.
        chat_only = _add(cron, "vivaio", notebook=None, chat=f"project:{OLD}")

        moved = cron_link.follow_renamed_notebook(cron, OLD, NEW)

        assert sorted(moved) == sorted([card, whole, chat_only])
        jobs = _jobs(cron)
        for jid in (card, whole):
            p = jobs[jid].payload
            assert p.board["notebook"] == NEW
            assert p.session_key == p.origin_chat_id == f"project:{NEW}"
        assert cron_link.scope_of(jobs[card].payload.board) == "basilico"
        assert cron_link.scope_of(jobs[whole].payload.board) == model.NOTEBOOK
        assert jobs[card].paused_at_ms is not None, "la pausa resta una pausa"
        assert jobs[chat_only].payload.board is None
        assert jobs[chat_only].payload.origin_chat_id == f"project:{NEW}"
        assert jobs[other].payload.board["notebook"] == "frutteto"
        assert jobs[personal].payload.session_key == "unified:default"
        # Su disco, non solo in memoria.
        reread = _jobs(CronService(tmp_path / "cron" / "jobs.json"))
        assert reread[whole].payload.board["card"] == cron_link.NOTEBOOK_CARD
        assert reread[whole].payload.board["notebook"] == NEW
    finally:
        cron.stop()


async def test_a_stopped_scheduler_journals_the_move_and_applies_it_at_start(
        tmp_path: Path) -> None:
    """All'avvio la ripresa di un rinomino arriva prima di ``start``."""
    path = tmp_path / "cron" / "jobs.json"
    seed = CronService(path)
    await seed.start()
    jid = _add(seed, "gelo", notebook=OLD)
    seed.stop()

    stopped = CronService(path)
    assert cron_link.follow_renamed_notebook(stopped, OLD, NEW) == [jid]
    await stopped.start()
    try:
        assert _jobs(stopped)[jid].payload.board["notebook"] == NEW
    finally:
        stopped.stop()
    assert _jobs(CronService(path))[jid].payload.session_key == f"project:{NEW}"


def test_a_moved_job_is_alive_under_the_new_name(tmp_path: Path, monkeypatch) -> None:
    from jenny.board.store import mutate_board

    root = tmp_path / "wikis" / NEW
    (root / "wiki").mkdir(parents=True)
    mutate_board(root, lambda b: b["cards"].append(
        {"id": "basilico", "title": "Basilico", "pages": [], "pos": {"x": 1, "y": 1}}))
    monkeypatch.setattr("jenny.board.cron_link.notebook_root",
                        lambda name: root if name == NEW else None)
    cron = CronService(tmp_path / "cron" / "jobs.json")
    jid = _add(cron, "acqua", notebook=OLD, scope="basilico")
    assert not cron_link.link_alive(cron.get_job(jid).payload.board)
    cron_link.follow_renamed_notebook(cron, OLD, NEW)
    assert cron_link.link_alive(cron.get_job(jid).payload.board)


def test_without_a_scheduler_or_with_a_broken_one_nothing_raises() -> None:
    assert cron_link.follow_renamed_notebook(None, OLD, NEW) == []
    broken = MagicMock()
    broken.move_notebook.side_effect = OSError("disco pieno")
    assert cron_link.follow_renamed_notebook(broken, OLD, NEW) == []


async def test_the_rename_command_brings_the_crons_along(tmp_path: Path, monkeypatch) -> None:
    from jenny.webui import commands, home_pages
    from jenny.webui import project_rename as module

    async def _no_pages(kind, ref, new_ref):
        return 0

    cron = CronService(tmp_path / "cron" / "jobs.json")
    await cron.start()
    try:
        jid = _add(cron, "gelo", notebook=OLD)
        monkeypatch.setattr(module, "rename_project", lambda **kw: {"new_name": kw["new_name"]})
        monkeypatch.setattr(commands, "_require_wiki_enabled", lambda: None)
        monkeypatch.setattr(home_pages, "rename_pages_of", _no_pages)
        ctx = SimpleNamespace(get_workspace_root=lambda: tmp_path,
                              invalidate_session=lambda k: None, busy_session_keys=lambda: (),
                              get_cron_service=lambda: cron)
        outcome = await commands.project_rename(ctx, {"name": OLD, "new_name": NEW})
        assert outcome["moved_crons"] == [jid]
        assert cron.get_job(jid).payload.board["notebook"] == NEW
    finally:
        cron.stop()


async def test_a_refused_rename_leaves_the_crons_alone(tmp_path: Path, monkeypatch) -> None:
    import pytest

    from jenny.webui import commands
    from jenny.webui import project_rename as module
    from jenny.webui.commands import CommandError

    def _refuses(**kw):
        raise module.ProjectRenameError("taken", code="name_taken")

    cron = CronService(tmp_path / "cron" / "jobs.json")
    jid = _add(cron, "gelo", notebook=OLD)
    monkeypatch.setattr(module, "rename_project", _refuses)
    monkeypatch.setattr(commands, "_require_wiki_enabled", lambda: None)
    ctx = SimpleNamespace(get_workspace_root=lambda: tmp_path, invalidate_session=lambda k: None,
                          busy_session_keys=lambda: (), get_cron_service=lambda: cron)
    with pytest.raises(CommandError):
        await commands.project_rename(ctx, {"name": OLD, "new_name": NEW})
    assert cron.get_job(jid).payload.board["notebook"] == OLD


def test_a_rename_resumed_at_boot_is_followed_by_the_crons(tmp_path: Path, monkeypatch) -> None:
    from jenny.runtime.container import GatewayContainer

    monkeypatch.setattr(
        "jenny.session.project_rename.repair_pending_project_renames",
        lambda workspace: [(f"project:{OLD}", f"project:{NEW}"), ("unified:default", "x")],
    )
    container = GatewayContainer.__new__(GatewayContainer)
    container.config = MagicMock()
    container.config.workspace_path = tmp_path
    container._repair_pending_renames()
    assert container._resumed_renames == [(OLD, NEW)]


def test_the_boot_follows_resumed_renames_once_the_scheduler_exists() -> None:
    """Il cablaggio: lo spostamento dei cron sta dopo la costruzione dello scheduler."""
    import inspect

    from jenny.runtime.container import GatewayContainer

    src = inspect.getsource(GatewayContainer.build)
    built = src.index("self.cron = CronService(")
    assert src.index("self._repair_pending_renames()") < built
    assert built < src.index("follow_renamed_notebook(self.cron")


def test_a_chat_that_follows_a_hand_renamed_folder_brings_the_crons(tmp_path: Path,
                                                                    monkeypatch) -> None:
    from jenny.agent import loop as loop_module
    from jenny.agent.loop import AgentLoop
    from jenny.session.project_traces import PROJECT_WIKI_ID_KEY

    target = tmp_path / "wikis" / NEW
    target.mkdir(parents=True)
    monkeypatch.setattr(loop_module, "find_wiki_by_id", lambda wikis, wid: target)
    monkeypatch.setattr(loop_module, "follow_renamed_project", lambda ws, old, new: (True, ""))
    cron = CronService(tmp_path / "cron" / "jobs.json")
    jid = _add(cron, "gelo", notebook=OLD)

    agent = AgentLoop.__new__(AgentLoop)
    session = SimpleNamespace(metadata={PROJECT_WIKI_ID_KEY: "wiki-1"})
    agent.sessions = SimpleNamespace(get_or_create=lambda key: session,
                                     invalidate=lambda key: None)
    agent.workspace_scopes = SimpleNamespace(for_project=lambda key: SimpleNamespace(
        project_path=tmp_path / "wikis" / OLD))
    agent.workspace = tmp_path
    agent.cron_service = cron

    outcome = agent._follow_renamed_project(f"project:{OLD}")

    assert outcome.moved_to == NEW
    assert cron.get_job(jid).payload.board["notebook"] == NEW
