"""Chi scrive adesso sotto quale sessione: ``AgentLoop.busy_session_keys``.

La chiede ``project.rename``, che non deve spostare una conversazione di progetto
mentre qualcuno ci sta scrivendo: altrimenti a fine lavoro quel qualcuno scrive
sotto il nome vecchio, e resta una chat senza cartella. La prima guardia
(``41c7d20``) leggeva solo i turni di chat in volo (``active_session_keys``), e
gli scrittori erano altri due in piu':

- **un subagent** lanciato dal quaderno sopravvive al turno che l'ha creato, e a
  fine lavoro scrive i suoi record e annuncia il risultato sotto la chiave
  d'origine;
- **una passata del giardiniere** gira sotto una chiave sua (``gardener:…``) ma
  scrive nella cartella della wiki.

Poi se n'e' trovato un quarto: **l'autocompact**, che compattando
o raccogliendo il diario rilegge e salva la sessione dopo una chiamata LLM.

E un quinto, il 12/11/2026: **la consolidazione di fine turno**. Parte staccata
quando il turno salva, aspetta il lock di sessione e salva la sessione dopo una
chiamata LLM, cioe' quando il turno non e' piu' in volo da secondi. Con lei sta
ogni altro lavoro staccato che salva una sessione (un avviso consegnato a
sessione occupata): il loop li conta tutti nello stesso registro.

``active_session_keys`` resta com'era: l'autocompact e il giardiniere la leggono
con il significato «un turno di chat sta usando la sessione».
"""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.agent import gardener
from jenny.agent.autocompact import AutoCompact
from jenny.agent.loop import AgentLoop
from jenny.agent.subagent import SubagentManager
from jenny.runtime.container import GatewayContainer


def _subagents(sessions: dict[str, dict[str, bool]]) -> SubagentManager:
    """Un manager con soli i due dizionari che contano: origine → task, vivo o no."""
    manager = SubagentManager.__new__(SubagentManager)
    manager._session_tasks = {key: set(tasks) for key, tasks in sessions.items()}
    manager._running_tasks = {
        tid: SimpleNamespace(done=lambda finished=finished: finished)
        for tasks in sessions.values()
        for tid, finished in tasks.items()
    }
    return manager


def test_a_subagent_still_running_keeps_its_origin_busy() -> None:
    manager = _subagents({
        "project:viaggio": {"t1": False},          # vivo
        "project:orto": {"t2": True},              # finito, non ancora ripulito
        "unified:default": {"t3": True, "t4": False},
    })
    assert set(manager.active_origin_session_keys()) == {"project:viaggio", "unified:default"}


def _autocompact(*, archiving: tuple[str, ...] = (), harvesting: tuple[str, ...] = ()) -> AutoCompact:
    compact = AutoCompact.__new__(AutoCompact)
    compact._archiving = set(archiving)
    compact._harvesting = set(harvesting)
    return compact


def _loop(
    *,
    turns: tuple[str, ...] = (),
    subagent_origins: tuple[str, ...] = (),
    auto_compact: AutoCompact | None = None,
) -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)
    loop._pending_queues = {key: asyncio.Queue() for key in turns}
    loop.subagents = SimpleNamespace(active_origin_session_keys=lambda: subagent_origins)
    loop.auto_compact = auto_compact or _autocompact()
    loop._detached_session_writes = Counter()
    return loop


def test_busy_is_turns_plus_subagents_plus_gardener_passes(monkeypatch) -> None:
    monkeypatch.setattr(gardener, "_PASSES_IN_FLIGHT", {"orto"})
    loop = _loop(turns=("unified:default",), subagent_origins=("project:viaggio",))

    assert set(loop.busy_session_keys()) == {
        "unified:default", "project:viaggio", "project:orto",
    }
    # La domanda stretta non cambia: solo i turni di chat.
    assert loop.active_session_keys() == ("unified:default",)


def test_a_key_busy_twice_is_listed_once(monkeypatch) -> None:
    monkeypatch.setattr(gardener, "_PASSES_IN_FLIGHT", {"viaggio"})
    loop = _loop(turns=("project:viaggio",), subagent_origins=("project:viaggio",))
    assert loop.busy_session_keys() == ("project:viaggio",)


def test_busy_includes_what_the_autocompact_is_rewriting(monkeypatch) -> None:
    monkeypatch.setattr(gardener, "_PASSES_IN_FLIGHT", set())
    compact = _autocompact(archiving=("unified:default",), harvesting=("project:orto",))
    loop = _loop(auto_compact=compact)

    assert set(loop.busy_session_keys()) == {"unified:default", "project:orto"}
    assert loop.active_session_keys() == ()


async def test_a_diary_harvest_keeps_its_project_busy_until_it_saves(tmp_path) -> None:
    """La finestra vera: la sessione si rilegge e si salva **dopo** la chiamata LLM.

    Si entra da ``check_expired``, cioe' dalla porta vera, e non si aggiunge la
    chiave a mano: il test che la aggiungeva da se'
    restava verde anche togliendo l'``add`` di ``check_expired``, cioe' proprio
    la riga che la regressione di M1 aveva perso.
    """
    from datetime import datetime, timedelta

    from jenny.session.manager import SessionManager
    from tests.support.aio import wait_until

    entered, release = asyncio.Event(), asyncio.Event()

    class _Consolidator:
        def messages_fitting_budget(self, messages, *, session_key):
            return len(messages)

        async def archive(self, messages, *, session_key, raw_dump_on_failure):
            entered.set()
            await release.wait()
            return "- riassunto"

    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("project:orto")
    for i in range(3):
        session.add_message("user", f"riga {i}")
    session.updated_at = datetime.now() - timedelta(hours=6)
    sessions.save(session)
    compact = AutoCompact(sessions, _Consolidator(), session_ttl_minutes=30)  # type: ignore[arg-type]
    tasks: list[asyncio.Task] = []

    compact.check_expired(lambda coro: tasks.append(asyncio.ensure_future(coro)))

    assert len(tasks) == 1
    await asyncio.wait_for(entered.wait(), timeout=5.0)
    assert compact.busy_session_keys() == ("project:orto",)
    release.set()
    await asyncio.wait_for(tasks[0], timeout=5.0)
    await wait_until(lambda: compact.busy_session_keys() == (), timeout=5.0)


# ── La consolidazione staccata di fine turno ────────────────────────────────


class _HeldArchive:
    """Il riassunto del Consolidator, fermo sulla chiamata LLM finche' il test vuole."""

    def __init__(self) -> None:
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def __call__(self, messages, **kwargs) -> str:
        self.entered.set()
        await self.release.wait()
        return "- riassunto"


def _end_a_turn(loop: AgentLoop, key: str) -> None:
    """La fine di un turno dalla porta vera: ``_finalize_turn_save``.

    La sessione ha gia' piu' messaggi di quanti ne rimanda la finestra
    (``max_messages``), quindi la consolidazione che il salvataggio lancia ha
    davvero un pezzo da riassumere, come nel log da cui viene il difetto.
    """
    session = loop.sessions.get_or_create(key)
    for i in range(4):
        session.add_message("user", f"domanda {i}")
        session.add_message("assistant", f"risposta {i}")
    loop.sessions.save(session)
    loop._finalize_turn_save(session, [], 0, turn_latency_ms=1, session_key=key)


async def test_the_consolidation_a_turn_leaves_behind_keeps_its_session_busy(
    tmp_path: Path, monkeypatch
) -> None:
    """Occupata **da quando il turno la lancia**, non da quando parte.

    Fra le due cose il turno finisce: una guardia che guardasse solo chi ha gia'
    il lock avrebbe li' la sua finestra.
    """
    from tests.support.agent import make_loop

    monkeypatch.setattr(gardener, "_PASSES_IN_FLIGHT", set())
    loop = make_loop(tmp_path, max_messages=2)
    held = _HeldArchive()
    monkeypatch.setattr(loop.consolidator, "archive", held)

    _end_a_turn(loop, "project:orto")

    # Nessun turno in volo, e il task non ha ancora fatto un passo.
    assert loop.active_session_keys() == ()
    assert loop.busy_session_keys() == ("project:orto",)
    await asyncio.wait_for(held.entered.wait(), timeout=5.0)
    assert loop.busy_session_keys() == ("project:orto",)

    held.release.set()
    await loop.close_background_tasks()
    assert loop.busy_session_keys() == ()


async def test_two_turns_in_a_row_stay_busy_until_the_second_consolidation_ends(
    tmp_path: Path, monkeypatch
) -> None:
    """Un conto, non un insieme: la prima che finisce non libera la seconda."""
    from tests.support.aio import wait_until

    monkeypatch.setattr(gardener, "_PASSES_IN_FLIGHT", set())
    loop = _loop()
    loop._background_tasks = []
    first, second = asyncio.Event(), asyncio.Event()

    loop._schedule_session_write("project:orto", first.wait)
    loop._schedule_session_write("project:orto", second.wait)
    assert loop.busy_session_keys() == ("project:orto",)

    first.set()
    await wait_until(lambda: len(loop._background_tasks) == 1, timeout=5.0)
    assert loop.busy_session_keys() == ("project:orto",)

    second.set()
    await wait_until(lambda: loop.busy_session_keys() == (), timeout=5.0)


async def test_a_detached_write_that_fails_still_frees_its_session(monkeypatch) -> None:
    from tests.support.aio import wait_until

    monkeypatch.setattr(gardener, "_PASSES_IN_FLIGHT", set())
    loop = _loop()
    loop._background_tasks = []

    async def _broken() -> None:
        raise RuntimeError("provider giu'")

    loop._schedule_session_write("project:orto", _broken)
    assert loop.busy_session_keys() == ("project:orto",)
    await wait_until(lambda: loop.busy_session_keys() == (), timeout=5.0)


async def test_a_notice_waiting_for_a_busy_session_keeps_it_busy(
    tmp_path: Path, monkeypatch
) -> None:
    """L'avviso consegnato a sessione occupata si scrive dopo, staccato: stesso registro."""
    from tests.support.agent import make_loop

    monkeypatch.setattr(gardener, "_PASSES_IN_FLIGHT", set())
    loop = make_loop(tmp_path)
    key = "project:orto"

    async with loop._session_locks.get(key):
        await loop.record_channel_delivery(session_key=key, content="E' ora di annaffiare")
        assert loop.busy_session_keys() == (key,)

    await loop.close_background_tasks()
    assert loop.busy_session_keys() == ()
    assert [m["content"] for m in loop.sessions.get_or_create(key).messages] == [
        "E' ora di annaffiare"
    ]


async def test_a_notebook_is_not_deleted_under_the_consolidation_of_its_last_turn(
    tmp_path: Path, monkeypatch
) -> None:
    """La corsa del 12/11/2026, dall'inizio alla fine, col comando vero.

    Dal log: il turno finisce, la consolidazione parte staccata, 25 ms dopo
    ``project.delete`` passa la guardia e cancella, 18 secondi dopo la
    consolidazione salva la sessione — e resta una chat ``project:<nome>``
    senza cartella. Qui il comando deve rifiutare finche' quel salvataggio non
    e' avvenuto, e dopo non deve restare niente.
    """
    from jenny.config.paths import get_wikis_path
    from jenny.session.project_traces import describe_project_traces
    from jenny.webui import commands, home_pages
    from jenny.webui.commands import CommandError
    from tests.support.agent import make_loop

    async def _no_page(kind, ref):
        return 0

    monkeypatch.setattr(gardener, "_PASSES_IN_FLIGHT", set())
    monkeypatch.setattr(commands, "_require_wiki_enabled", lambda: None)
    monkeypatch.setattr(home_pages, "detach_pages_of", _no_page)
    workspace = tmp_path / "workspace"
    folder = get_wikis_path(workspace) / "orto"
    (folder / "wiki").mkdir(parents=True)
    loop = make_loop(workspace, max_messages=2)
    held = _HeldArchive()
    monkeypatch.setattr(loop.consolidator, "archive", held)
    ctx = SimpleNamespace(
        get_workspace_root=lambda: workspace,
        invalidate_session=loop.sessions.invalidate,
        busy_session_keys=loop.busy_session_keys,
        get_plan_driver=None,
    )

    _end_a_turn(loop, "project:orto")
    await asyncio.wait_for(held.entered.wait(), timeout=5.0)

    with pytest.raises(CommandError) as err:
        await commands.project_delete(ctx, {"name": "orto"})
    assert err.value.code == "conflict"
    with pytest.raises(CommandError) as err:
        await commands.project_rename(ctx, {"name": "orto", "new_name": "serra"})
    assert err.value.code == "conflict"
    assert folder.is_dir()

    held.release.set()
    await loop.close_background_tasks()
    assert loop.busy_session_keys() == ()

    outcome = await commands.project_delete(ctx, {"name": "orto"})
    assert outcome["removed_tree"] is True
    assert not folder.exists()
    assert not describe_project_traces(workspace, "project:orto").exists


def test_the_gardener_hands_out_a_copy_of_its_passes(monkeypatch) -> None:
    monkeypatch.setattr(gardener, "_PASSES_IN_FLIGHT", {"orto"})
    copy = gardener.passes_in_flight()
    assert copy == frozenset({"orto"})
    with pytest.raises(AttributeError):
        copy.add("altro")  # type: ignore[attr-defined]


# ── Il collegamento nel container ───────────────────────────────────────────


def test_the_container_asks_the_agent_and_says_nobody_without_one() -> None:
    """Senza questo test un rinomino del metodo passerebbe verde e romperebbe
    ``project.rename`` solo sul telefono."""
    container = GatewayContainer.__new__(GatewayContainer)
    container._agent = None
    assert container._busy_session_keys() == ()

    container._agent = _loop(subagent_origins=("project:viaggio",))
    assert container._busy_session_keys() == ("project:viaggio",)
