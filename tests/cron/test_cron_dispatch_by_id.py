"""Il dispatcher sceglie il gestore dal job di sistema, non dal nome.

Il nome di un job dell'utente lo sceglie l'utente (o il modello, dal tool
``cron``): un promemoria chiamato «dream» faceva partire un ciclo
di Dream al posto del promemoria. I job di sistema sono
``system_event`` con l'id del lavoratore (``GatewayContainer.build``,
``refresh_system_job``); un job dell'utente non e' mai ``system_event`` e il suo
id e' un uuid troncato.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jenny.cron.types import CronJob, CronPayload
from jenny.runtime import cron_dispatch
from jenny.runtime.cron_dispatch import CronDispatcher

_WORKERS = ("dream", "gardener", "reminders", "update_check")


@pytest.fixture
def routed(monkeypatch: pytest.MonkeyPatch) -> tuple[CronDispatcher, list[str]]:
    calls: list[str] = []
    dispatcher = CronDispatcher(
        get_agent=lambda: SimpleNamespace(),
        config=MagicMock(),
        cron=MagicMock(),
    )

    def spy(label):
        async def _handler(*_a, **_k):
            calls.append(label)
            return None
        return _handler

    monkeypatch.setattr(dispatcher, "_run_dream", spy("dream"))
    monkeypatch.setattr(dispatcher, "_run_gardener", spy("gardener"))
    monkeypatch.setattr(dispatcher, "_run_reminders", spy("reminders"))
    monkeypatch.setattr(dispatcher, "_run_update_check", spy("update_check"))
    monkeypatch.setattr(cron_dispatch, "run_bound_cron_job", spy("bound"))
    return dispatcher, calls


@pytest.mark.parametrize("name", _WORKERS)
async def test_a_reminder_named_like_a_worker_runs_as_a_reminder(routed, name) -> None:
    dispatcher, calls = routed
    reminder = CronJob(
        id="a1b2c3d4",
        name=name,
        payload=CronPayload(
            kind="agent_turn",
            message="ricordami l'ombrello",
            session_key="websocket:chat-1",
            origin_channel="websocket",
            origin_chat_id="chat-1",
        ),
    )

    await dispatcher._dispatch(reminder)

    assert calls == ["bound"]


@pytest.mark.parametrize("worker", _WORKERS)
async def test_a_system_job_is_routed_by_its_id(routed, worker) -> None:
    dispatcher, calls = routed
    job = CronJob(id=worker, name=f"renamed {worker}", payload=CronPayload(kind="system_event"))

    await dispatcher._dispatch(job)

    assert calls == [worker]
