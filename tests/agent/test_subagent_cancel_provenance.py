"""Provenienza di una cancellazione: durevole, e leggibile da chi decide.

Osservato sul device: l'utente ferma un subagent, il gateway si riavvia,
l'orchestratore chiama ``subagent_status``, vede un job ``cancelled`` e lo rilegge
come lavoro interrotto da riprendere — rilanciando qualcosa che l'utente aveva
fermato di proposito. ``state="cancelled"`` da solo non distingue le tre
cancellazioni possibili, e dopo un riavvio i record su disco sono l'unica memoria
che resta.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.runner import AgentRunResult
from jenny.agent.subagent import SubagentManager
from jenny.agent.subagent_records import (
    CANCEL_REASON_SHUTDOWN,
    CANCEL_REASON_SUPERSEDED,
    CANCEL_REASON_USER,
    SubagentRecord,
    SubagentRecordStore,
    SubagentSpec,
)
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMProvider


def _manager(tmp_path: Path, **kw) -> SubagentManager:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    defaults = dict(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        model="test-model",
        max_tool_result_chars=16_000,
        max_concurrent_subagents=8,
    )
    defaults.update(kw)
    manager = SubagentManager(**defaults)
    manager.bus.publish_inbound = AsyncMock()
    return manager


def _blocking_runner(sm: SubagentManager) -> asyncio.Event:
    block = asyncio.Event()

    async def _slow_run(spec):
        await block.wait()
        return AgentRunResult(final_content="done", messages=[], stop_reason="completed")

    sm.runner.run = _slow_run
    return block


async def _drain(sm: SubagentManager) -> None:
    tasks = list(sm._running_tasks.values())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Round-trip su disco
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_cancel_provenance_survives_a_round_trip_through_disk(tmp_path):
    """Il record riletto da disco deve ancora dire CHI ha fermato il subagent.

    Il round-trip passa da uno store nuovo sulla stessa directory: e la
    simulazione del riavvio del gateway, dove la RAM e vuota e resta solo il
    JSONL.
    """
    sm = _manager(tmp_path)
    block = _blocking_runner(sm)

    await sm.spawn("research the price", session_key="s1")
    task_id = next(iter(sm._running_tasks))
    assert await sm.cancel_task(task_id, grace_s=0.5) is True
    block.set()
    await _drain(sm)

    reloaded = SubagentRecordStore(tmp_path).load("s1")
    assert len(reloaded) == 1
    record = reloaded[0]
    assert record.state == "cancelled"
    assert record.cancel_reason == CANCEL_REASON_USER
    # ``stop_reason`` e ``result_summary`` sono i campi che ``subagent_status``
    # stampa: la regola viaggia nei dati che l'orchestratore legge comunque, non
    # solo in un campo nuovo che potrebbe ignorare.
    assert record.stop_reason == "cancelled_by_user"
    assert "do not restart it" in record.result_summary.lower()


@pytest.mark.asyncio
async def test_shutdown_cancel_is_recorded_as_restartable_provenance(tmp_path):
    """Un drain di shutdown non e uno stop dell'utente: distinguerli e il punto.

    E questo il caso che l'orchestratore puo legittimamente rilanciare dopo un
    riavvio, e prima era indistinguibile da quello che non deve toccare.
    """
    sm = _manager(tmp_path)
    _blocking_runner(sm)

    await sm.spawn("long job", session_key="s1")
    assert await sm.drain(timeout_s=0.01) == 1
    await _drain(sm)

    record = SubagentRecordStore(tmp_path).load("s1")[0]
    assert record.state == "cancelled"
    assert record.cancel_reason == CANCEL_REASON_SHUTDOWN
    assert record.stop_reason == "cancelled_at_shutdown"
    assert "safe to restart" in record.result_summary.lower()


@pytest.mark.asyncio
async def test_session_stop_records_the_user_as_the_source(tmp_path):
    sm = _manager(tmp_path)
    block = _blocking_runner(sm)

    await sm.spawn("job a", session_key="s1")
    await sm.spawn("job b", session_key="s1")
    assert await sm.cancel_by_session("s1", grace_s=0.5) == 2
    block.set()
    await _drain(sm)

    records = SubagentRecordStore(tmp_path).load("s1")
    assert len(records) == 2
    assert {r.cancel_reason for r in records} == {CANCEL_REASON_USER}


@pytest.mark.asyncio
async def test_superseded_attempt_is_not_confused_with_a_user_stop(tmp_path):
    """Un rilancio cancella il tentativo precedente: non e l'utente che ha fermato."""
    sm = _manager(tmp_path)
    block = _blocking_runner(sm)

    await sm.spawn("job", session_key="s1")
    lineage_id = next(iter(sm._lineages))
    await sm._supersede(lineage_id, grace_s=0.5)
    block.set()
    await _drain(sm)

    record = SubagentRecordStore(tmp_path).load("s1")[0]
    assert record.cancel_reason == CANCEL_REASON_SUPERSEDED
    assert record.stop_reason == "superseded_by_new_attempt"


@pytest.mark.asyncio
async def test_a_completed_subagent_carries_no_cancel_reason(tmp_path):
    sm = _manager(tmp_path)
    sm.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content="the answer", messages=[], stop_reason="completed",
    ))

    await sm.spawn("compute", session_key="s1")
    await _drain(sm)

    record = SubagentRecordStore(tmp_path).load("s1")[0]
    assert record.state == "done"
    assert record.cancel_reason is None


# ---------------------------------------------------------------------------
# Tolleranza ai record vecchi
# ---------------------------------------------------------------------------


def _legacy_line(**overrides) -> str:
    """Riga JSONL nella forma scritta PRIMA di ``cancel_reason``.

    I timestamp sono relativi a ``time.time()``, non date fisse: ``load()`` pota
    in lettura tutto ciò che è più vecchio di ``RECORD_TTL_S`` (7 giorni), quindi
    un istante scritto a mano rende il test verde finché non scade e rosso per
    sempre dopo — con un fallimento che non c'entra niente con ciò che verifica.

    Restano un minuto indietro rispetto a ``time.time()``: ``_prune`` riordina
    per ``ended_at``, quindi un record "vecchio" deve davvero essere più vecchio
    di quello corrente con cui viene mescolato.
    """
    now = time.time() - 60.0
    payload = {
        "task_id": "aa11bb22",
        "lineage_id": "cc33dd44",
        "attempt": 1,
        "spec": {"task": "research the price", "label": "price research"},
        "state": "cancelled",
        "phase": "done",
        "stop_reason": None,
        "error": None,
        "result_summary": "",
        "iteration": 4,
        "started_at": now - 4.0,
        "ended_at": now,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False) + "\n"


def test_a_record_written_before_the_field_existed_still_loads(tmp_path):
    """Contratto del modulo: un campo mancante non solleva.

    Un record di una versione precedente non ha ``cancel_reason``. Se non
    caricasse, tutto lo storico su disco diventerebbe irrilanciabile dopo
    l'aggiornamento — che e proprio il contrario dello scopo della retention.
    """
    record = SubagentRecord.from_dict(json.loads(_legacy_line()))

    assert record.state == "cancelled"
    assert record.cancel_reason is None


def test_an_unknown_cancel_reason_degrades_to_none(tmp_path):
    """Un valore che questa versione non conosce non si propaga come verita."""
    record = SubagentRecord.from_dict(json.loads(_legacy_line(cancel_reason="martians")))

    assert record.cancel_reason is None


def test_legacy_and_current_records_coexist(tmp_path):
    store = SubagentRecordStore(tmp_path)
    current = SubagentRecord(
        task_id="ee55ff66",
        lineage_id="ee55ff66",
        attempt=1,
        spec=SubagentSpec(task="new job", label="new job"),
        state="cancelled",
        cancel_reason=CANCEL_REASON_USER,
        ended_at=time.time(),
    )
    with store._db.write() as conn:
        for line in (_legacy_line(), json.dumps(current.to_dict(), ensure_ascii=False)):
            data = json.loads(line)
            conn.execute("INSERT INTO subagent_records(task_id, session_key, ended_at, data) "
                         "VALUES (?, 'internal:direct', ?, ?)",
                         (data["task_id"], data.get("ended_at") or 0, line))

    loaded = store.load("internal:direct")
    assert [r.cancel_reason for r in loaded] == [None, CANCEL_REASON_USER]


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_exposes_the_cancel_reason_to_the_orchestrator(tmp_path):
    sm = _manager(tmp_path)
    block = _blocking_runner(sm)

    await sm.spawn("research the price", session_key="s1")
    task_id = next(iter(sm._running_tasks))
    await sm.cancel_task(task_id, grace_s=0.5)
    block.set()
    await _drain(sm)

    recent = sm.status_snapshot("s1")["recent"]
    assert len(recent) == 1
    assert recent[0]["state"] == "cancelled"
    assert recent[0]["cancel_reason"] == CANCEL_REASON_USER
    # Lo snapshot resta JSON-only: la route /api/subagents lo serve verbatim.
    json.dumps(recent)
