"""I tool che si portano la destinazione da sé, e che quindi devono chiedere.

Passo **4.2** del piano dei progetti, la metà che non passa dai
cancelli di percorso.

Questi quattro non risolvono niente con ``resolve_allowed_path``: la
destinazione ce l'hanno scritta dentro (``downloads/``, ``apps/<nome>/data/``,
``cron/jobs.json``, l'APK). Il cancello non li vede passare, quindi in sola
lettura si fermano da soli — e chi controlla che nessuno li dimentichi è
``tests/security/test_readonly_write_surfaces.py``.

Due di loro sono anche il **buco trovato il 22/08**: ``download`` e lo storage
delle app scrivono nella radice dell'installazione e non in quella del turno,
quindi da dentro un progetto scrivono *fuori*. La sola lettura li chiude; con la
scrittura accesa il buco resta, ed è la voce che rimane al passo 6.

Il rifiuto è **una risposta e non un'eccezione**, al contrario dei cancelli di
percorso: là un errore deve assomigliare a un errore di filesystem, qui a una
frase che il modello ridice all'utente.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.cron import CronTool
from jenny.agent.tools.download import DownloadFileTool
from jenny.agent.tools.long_task import CompleteGoalTool, LongTaskTool
from jenny.apps.manifest import AppAction
from jenny.apps.storage import StorageError, execute_storage_action
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    build_workspace_scope,
    enter_workspace_scope,
)
from jenny.session.goal_state import GOAL_STATE_KEY
from jenny.session.manager import SessionManager

_MARK = "read-only"


@pytest.fixture
def readonly(tmp_path: Path):
    with enter_workspace_scope(
        build_workspace_scope(tmp_path, "restricted").without_write_access()
    ):
        yield tmp_path


@pytest.fixture
def writable(tmp_path: Path):
    with enter_workspace_scope(build_workspace_scope(tmp_path, "restricted")):
        yield tmp_path


# ── download ─────────────────────────────────────────────────────────────


async def test_download_refuses_before_touching_the_network(readonly: Path) -> None:
    """Prima della rete, non dopo: 20 MB scaricati per poi rifiutare sono buttati."""
    client = MagicMock()
    client.get = MagicMock(side_effect=AssertionError("la rete non deve essere toccata"))
    result = await DownloadFileTool(str(readonly), client=client).execute(
        url="https://example.invalid/a.jpg"
    )
    assert _MARK in result
    assert not (readonly / "downloads").exists()


# ── lo storage delle mini-app ────────────────────────────────────────────


@pytest.mark.parametrize("op", ["append", "set", "update", "delete"])
async def test_the_four_app_mutations_are_refused(readonly: Path, op: str) -> None:
    """È la scrittura che il 22/08 è uscita da dentro un progetto: la Todo personale."""
    action = AppAction(name="add", description="", kind="storage", collection="todos", op=op)
    with pytest.raises(StorageError) as err:
        await execute_storage_action(readonly, action, {"text": "x", "id": "1"})
    assert _MARK in str(err.value)


async def test_an_app_can_still_show_its_data(readonly: Path) -> None:
    """``query`` resta aperta: in sola lettura una mini-app deve poter mostrare."""
    (readonly / "data").mkdir()
    (readonly / "data" / "todos.jsonl").write_text(
        json.dumps({"id": "1", "text": "ciao"}) + "\n", encoding="utf-8"
    )
    action = AppAction(name="q", description="", kind="storage", collection="todos", op="query")
    result = await execute_storage_action(readonly, action, {})
    assert result.get("ok") is not False


async def test_with_writing_on_the_app_mutation_lands(writable: Path) -> None:
    action = AppAction(name="add", description="", kind="storage", collection="todos", op="append")
    result = await execute_storage_action(writable, action, {"text": "x"})
    assert result["ok"] is True


# ── cron: due chiusure, due regole ───────────────────────────────────────


class _Cron:
    def __init__(self) -> None:
        self.added: list[dict[str, Any]] = []

    def add_job(self, **kwargs: Any):
        self.added.append(kwargs)
        raise AssertionError("nessun job deve nascere in sola lettura")

    def list_jobs(self):
        return []

    def get_job(self, _id: str):
        return None

    def remove_job(self, _id: str) -> str:
        raise AssertionError("nessun job deve essere rimosso in sola lettura")


def _cron_tool(session_key: str) -> tuple[CronTool, _Cron]:
    service = _Cron()
    tool = CronTool(service, default_timezone="UTC")
    tool.set_context(
        RequestContext(channel="websocket", chat_id="default", session_key=session_key)
    )
    return tool, service


@pytest.mark.parametrize(
    "params",
    [
        {"action": "add", "message": "x", "at": "2026-09-01T09:00:00"},
        {"action": "list"},
        {"action": "remove", "job_id": "j1"},
    ],
    ids=["add", "list", "remove"],
)
async def test_cron_is_closed_in_the_personal_chat_too(readonly: Path, params: dict) -> None:
    """Il passo 3 chiudeva solo i progetti; la sola lettura vale anche qui.

    Un job è fra le cose più durature che Jenny possa creare: sopravvive al
    turno, alla conversazione e al riavvio.
    """
    tool, service = _cron_tool("unified:default")
    result = await tool.execute(**params)
    assert _MARK in result
    assert service.added == []


# ── il goal sostenuto: la stessa famiglia di un job ──────────────────────
#
# Passo **T4.6**. ``long_task`` non scrive un file: scrive
# ``metadata[goal_state]`` e salva la sessione. Il cancello di percorso non lo
# vede, quindi deve chiedere da se' — come ``cron``, e per la stessa ragione: lo
# stato sopravvive al turno, alla conversazione e al riavvio, e cambia il
# comportamento futuro (wall timeout LLM, chip del goal, iniezione «keep
# working»).
#
# Rifiuto e non assenza dal registry: il registry dell'agente principale si
# costruisce **una volta** (``AgentLoop._register_default_tools``) e la sola
# lettura e' un flag **per messaggio**, quindi «assente in sola lettura» non e'
# una lista diversa ma un filtro per turno che non esiste. Anche ``CronTool``,
# che l'audit citava come esempio della forma forte, in realta' si rifiuta
# dentro ``execute``.


def _goal_tools(sessions: SessionManager) -> tuple[LongTaskTool, CompleteGoalTool]:
    lt, cg = LongTaskTool(sessions=sessions), CompleteGoalTool(sessions=sessions)
    rc = RequestContext(channel="websocket", chat_id="default", session_key="unified:default")
    lt.set_context(rc)
    cg.set_context(rc)
    return lt, cg


async def test_no_sustained_goal_is_born_in_a_read_only_turn(readonly: Path) -> None:
    """Il difetto: nessun controllo, e lo stato sopravviveva al riavvio."""
    sessions = SessionManager(readonly)
    lt, _cg = _goal_tools(sessions)

    result = await lt.execute(goal="Track the whole migration", ui_summary="migration")

    assert _MARK in result
    assert GOAL_STATE_KEY not in sessions.get_or_create("unified:default").metadata


async def test_the_refusal_comes_before_the_advice_to_shorten(readonly: Path) -> None:
    """Ordine dei controlli: un «accorcia e richiama» qui e' un giro a vuoto.

    In sola lettura *nessuna* chiamata puo' andare a buon fine, quindi il tetto
    di lunghezza non deve parlare per primo: manderebbe il modello a riscrivere
    l'obiettivo per poi trovarsi rifiutato uguale.
    """
    lt, _cg = _goal_tools(SessionManager(readonly))
    result = await lt.execute(goal="x" * 20_000)
    assert _MARK in result
    assert "characters" not in result


async def test_with_writing_on_the_goal_is_recorded(writable: Path) -> None:
    """Il cancello non deve toccare il caso normale."""
    sessions = SessionManager(writable)
    lt, _cg = _goal_tools(sessions)

    assert "Goal recorded" in await lt.execute(goal="Track the whole migration")
    blob = sessions.get_or_create("unified:default").metadata[GOAL_STATE_KEY]
    assert blob["status"] == "active"


async def test_closing_a_goal_stays_open_in_read_only(readonly: Path) -> None:
    """Decisione, non dimenticanza: si chiude la creazione, non la chiusura.

    ``complete_goal`` non puo' creare nessuna obbligazione — riscrive un blob
    che esiste gia', e la sola transizione possibile e' ``active -> completed``.
    Chiuderlo lascerebbe senza uscita il turno in sola lettura che ha davvero
    soddisfatto un obiettivo di sola lettura («scopri X e dimmelo»): l'iniezione
    «keep working» (v. ``_goal_continue`` in ``loop.py``) tornerebbe a spronarlo
    a ogni turno successivo verso qualcosa di gia' fatto.
    """
    sessions = SessionManager(readonly)
    lt, cg = _goal_tools(sessions)
    # Il goal nasce con la scrittura accesa, cioe' fuori dallo scope legato.
    sessions.get_or_create("unified:default").metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "objective": "Find out X and tell me",
        "ui_summary": "",
        "started_at": "2026-08-23T09:00:00",
    }

    result = await cg.execute(recap="Found X, reported in chat.")

    assert "marked complete" in result
    assert sessions.get_or_create("unified:default").metadata[GOAL_STATE_KEY]["status"] == (
        "completed"
    )
    # ...e resta l'unica direzione possibile: rinascere, no.
    assert _MARK in await lt.execute(goal="And now track Y")


# ── il rifiuto, come frase ───────────────────────────────────────────────


def test_the_refusal_says_where_the_switch_is() -> None:
    """È l'unico posto in cui il modello impara come si riattiva la scrittura."""
    low = READONLY_TOOL_REFUSAL.lower()
    assert "read-only" in low
    assert "switch" in low, "deve nominare l'interruttore, non solo negare"
    assert "describe" in low, "e dire cosa fare invece"
