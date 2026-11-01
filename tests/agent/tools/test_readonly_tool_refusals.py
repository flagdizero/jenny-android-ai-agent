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
from jenny.apps.manifest import AppAction
from jenny.apps.storage import StorageError, execute_storage_action
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    build_workspace_scope,
    enter_workspace_scope,
)

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
        {"action": "add", "message": "x", "date": "2026-09-01", "time": "09:00"},
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


# ── il rifiuto, come frase ───────────────────────────────────────────────


def test_the_refusal_says_where_the_switch_is() -> None:
    """È l'unico posto in cui il modello impara come si riattiva la scrittura."""
    low = READONLY_TOOL_REFUSAL.lower()
    assert "read-only" in low
    assert "switch" in low, "deve nominare l'interruttore, non solo negare"
    assert "describe" in low, "e dire cosa fare invece"
