"""Il giro ``cloud_backup`` dell'estensione Drive, passato dal ``CronDispatcher``.

Due requisiti: il giro gira **anche senza agente** (spedire una copia non ha
bisogno del modello), e l'avviso dopo tre notti lo dà Jenny con un turno
silenzioso — o, senza agente, la notifica nuda.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from support.sessions import FakeSessions

from jenny.extensions import ExtensionHost
from jenny.runtime.cron_dispatch import CronDispatcher
from jenny.session.turn_visibility import TurnVisibility
from jenny_extensions.drive import cloud_service, cron
from jenny_extensions.drive.cron import ALERT_SESSION_KEY as CLOUD_ALERT_SESSION_KEY
from jenny_extensions.drive.cron import JOB_ID as CLOUD_BACKUP_JOB_ID

_JOB = SimpleNamespace(
    name=CLOUD_BACKUP_JOB_ID, id=CLOUD_BACKUP_JOB_ID, payload=SimpleNamespace(kind="system_event")
)
_ALERT = {"days": 3, "error": "consent_needed", "never": False}


class _FakeAgent:
    def __init__(self) -> None:
        self.sessions = FakeSessions(shared=True)
        self.calls: list[dict] = []

    async def process_direct(self, prompt: str, **kwargs: Any):
        self.calls.append({"prompt": prompt, **kwargs})


class _FakeCron:
    monitor_followup = None


@pytest.fixture
def make_dispatcher(monkeypatch):
    def make(agent, outcome: dict) -> tuple[CronDispatcher, list[int]]:
        ticks: list[int] = []

        async def tick(_snapshot) -> dict:
            ticks.append(1)
            return outcome

        monkeypatch.setattr(cloud_service, "cloud_tick", tick)
        host = ExtensionHost(snapshot=object(), get_agent=lambda: agent)
        dispatcher = CronDispatcher(
            get_agent=lambda: agent,
            config=SimpleNamespace(workspace_path=None),
            cron=_FakeCron(),
            extension_jobs={CLOUD_BACKUP_JOB_ID: lambda: cron.run(host)},
        )
        return dispatcher, ticks

    return make


async def test_the_backup_runs_without_an_agent(make_dispatcher) -> None:
    dispatcher, ticks = make_dispatcher(None, {"action": "ran", "alert": None})

    assert await dispatcher.dispatch(_JOB) is None
    assert ticks == [1]


async def test_jenny_says_it_in_a_silent_turn(make_dispatcher) -> None:
    agent = _FakeAgent()
    dispatcher, _ = make_dispatcher(agent, {"action": "failed", "alert": _ALERT})

    await dispatcher.dispatch(_JOB)

    (call,) = agent.calls
    assert call["session_key"] == CLOUD_ALERT_SESSION_KEY
    assert call["visibility"] is TurnVisibility.SILENT
    assert call["channel"] == "websocket" and call["chat_id"] == "default"
    assert "MUST call it exactly once" in call["prompt"]
    assert "Days without a successful copy: 3" in call["prompt"]
    assert "Reason code: consent_needed" in call["prompt"]


async def test_no_alert_no_turn(make_dispatcher) -> None:
    agent = _FakeAgent()
    dispatcher, _ = make_dispatcher(agent, {"action": "wait", "alert": None})

    await dispatcher.dispatch(_JOB)

    assert agent.calls == []


async def test_without_an_agent_the_alert_is_a_bare_notification(
    monkeypatch, make_dispatcher
) -> None:
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "jenny.runtime.notifier.notify_delivery", lambda text, meta: sent.append((text, meta))
    )
    dispatcher, _ = make_dispatcher(None, {"action": "failed", "alert": _ALERT})

    await dispatcher.dispatch(_JOB)

    (text, _meta), = sent
    assert "3 days" in text


async def test_without_a_snapshot_service_nothing_runs(monkeypatch) -> None:
    async def tick(_snapshot) -> dict:  # pragma: no cover — non deve partire
        raise AssertionError("ticked without snapshots")

    monkeypatch.setattr(cloud_service, "cloud_tick", tick)

    assert await cron.run(ExtensionHost(snapshot=None, get_agent=lambda: None)) is None
