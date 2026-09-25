"""Chi scrive adesso sotto quale sessione: ``AgentLoop.busy_session_keys``.

La chiede ``project.rename``, che non deve spostare una conversazione di progetto
mentre qualcuno ci sta scrivendo: altrimenti a fine lavoro quel qualcuno scrive
sotto il nome vecchio, e resta una chat senza cartella. La prima guardia
(``41c7d20``) leggeva solo i turni di chat in volo (``active_session_keys``); la
seconda revisione ha trovato gli altri due scrittori:

- **un subagent** lanciato dal quaderno sopravvive al turno che l'ha creato, e a
  fine lavoro scrive i suoi record e annuncia il risultato sotto la chiave
  d'origine;
- **una passata del giardiniere** gira sotto una chiave sua (``gardener:…``) ma
  scrive nella cartella della wiki.

La revisione profonda (M1) ha trovato il quarto: **l'autocompact**, che compattando
o raccogliendo il diario rilegge e salva la sessione dopo una chiamata LLM.

``active_session_keys`` resta com'era: l'autocompact e il giardiniere la leggono
con il significato «un turno di chat sta usando la sessione».
"""

from __future__ import annotations

import asyncio
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
        tid: SimpleNamespace(done=lambda finito=finito: finito)
        for tasks in sessions.values()
        for tid, finito in tasks.items()
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


async def test_a_diary_harvest_keeps_its_project_busy_until_it_saves() -> None:
    """La finestra vera: la sessione si rilegge e si salva **dopo** la chiamata LLM."""
    entered, release = asyncio.Event(), asyncio.Event()

    class _Consolidator:
        async def archive(self, messages, *, session_key):
            entered.set()
            await release.wait()

    session = SimpleNamespace(messages=[{"role": "user"}] * 3, metadata={})
    sessions = SimpleNamespace(get_or_create=lambda key: session, save=lambda s: None)
    compact = AutoCompact(sessions, _Consolidator())  # type: ignore[arg-type]
    compact._harvesting.add("project:orto")  # come fa ``check_expired``

    task = asyncio.create_task(compact._harvest_project_diary("project:orto"))
    await entered.wait()
    assert compact.busy_session_keys() == ("project:orto",)
    release.set()
    await task
    assert compact.busy_session_keys() == ()


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
