"""Il parametro ``mode`` del tool ``cron``, visto dal lato del modello.

Il tool è l'unica porta da cui un monitor può nascere, quindi è anche l'unico
punto in cui una combinazione senza senso — un monitor one-shot — può essere
fermata prima di diventare un job che non riferirà mai niente.
"""

from __future__ import annotations

from typing import Any

import pytest

from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.cron import CronTool
from jenny.agent.tools.registry import ToolRegistry
from jenny.cron.types import CronJob, CronPayload, CronSchedule


class _RecordingCronService:
    """CronService finto: tiene gli argomenti di ``add_job`` e i job da elencare."""

    def __init__(self, jobs: list[CronJob] | None = None) -> None:
        self.added: list[dict[str, Any]] = []
        self._jobs = jobs or []

    def add_job(self, **kwargs: Any) -> CronJob:
        self.added.append(kwargs)
        return CronJob(id="job-1", name=kwargs.get("name", "x"))

    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        # La firma del servizio vero: l'elenco del tool chiede anche i job in
        # pausa, e li filtra da se'.
        return list(self._jobs)

    def get_job(self, _job_id: str) -> None:
        return None

    def remove_job(self, _job_id: str) -> str:
        return "not-found"


def _tool(service: _RecordingCronService) -> CronTool:
    tool = CronTool(service, default_timezone="UTC")
    tool.set_context(
        RequestContext(channel="websocket", chat_id="chat-1", session_key="unified:default")
    )
    return tool


def _registry(tool: CronTool) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(tool)
    return registry


class TestMonitorCannotBeOneShot:
    """Un monitor che scatta una volta sola e tace non avvisa mai nessuno."""

    def test_the_schema_layer_rejects_monitor_combined_with_at(self) -> None:
        tool = _tool(_RecordingCronService())

        errors = tool.validate_params(
            {"action": "add", "message": "controlla", "mode": "monitor", "at": "2030-01-01T00:00:00"}
        )

        assert any("cannot be used with at" in err for err in errors)

    def test_the_call_is_refused_before_the_job_exists(self) -> None:
        service = _RecordingCronService()
        registry = _registry(_tool(service))

        _name, _args, err = registry.prepare_call(
            "cron",
            {
                "action": "add",
                "message": "controlla",
                "mode": "monitor",
                "at": "2030-01-01T00:00:00",
            },
        )

        assert err is not None
        assert service.added == []

    async def test_the_runtime_refuses_it_too_and_explains_the_alternative(self) -> None:
        """Doppia guardia: la validazione dello schema si può aggirare, questa no."""
        service = _RecordingCronService()
        tool = _tool(service)

        out = await tool.execute(
            action="add", message="controlla", mode="monitor", at="2030-01-01T00:00:00"
        )

        assert out.startswith("Error:")
        assert "every_seconds" in out and "cron_expr" in out
        assert service.added == []

    async def test_a_one_shot_reminder_is_still_allowed(self) -> None:
        """Controprova: il divieto riguarda ``monitor``, non ``at``."""
        service = _RecordingCronService()
        tool = _tool(service)

        out = await tool.execute(
            action="add", message="ricordami", mode="reminder", at="2030-01-01T00:00:00"
        )

        assert out.startswith("Created job")
        assert service.added[0]["mode"] == "reminder"


class TestModeReachesTheService:
    async def test_an_omitted_mode_creates_a_reminder(self) -> None:
        service = _RecordingCronService()
        tool = _tool(service)

        await tool.execute(action="add", message="ricordami", every_seconds=60)

        assert service.added[0]["mode"] == "reminder"

    async def test_monitor_is_forwarded_for_a_recurring_schedule(self) -> None:
        service = _RecordingCronService()
        tool = _tool(service)

        await tool.execute(action="add", message="controlla", mode="monitor", every_seconds=60)

        assert service.added[0]["mode"] == "monitor"

    async def test_monitor_is_forwarded_for_a_cron_expression(self) -> None:
        service = _RecordingCronService()
        tool = _tool(service)

        await tool.execute(action="add", message="controlla", mode="monitor", cron_expr="0 9 * * *")

        assert service.added[0]["mode"] == "monitor"

    async def test_a_padded_mode_is_normalized_before_reaching_the_service(self) -> None:
        service = _RecordingCronService()
        tool = _tool(service)

        await tool.execute(action="add", message="controlla", mode="  monitor  ", every_seconds=60)

        assert service.added[0]["mode"] == "monitor"

    async def test_an_empty_mode_falls_back_to_reminder(self) -> None:
        service = _RecordingCronService()
        tool = _tool(service)

        await tool.execute(action="add", message="ricordami", mode="   ", every_seconds=60)

        assert service.added[0]["mode"] == "reminder"

    async def test_an_unknown_mode_is_refused_with_both_options_spelled_out(self) -> None:
        service = _RecordingCronService()
        tool = _tool(service)

        out = await tool.execute(
            action="add", message="controlla", mode="telepathy", every_seconds=60
        )

        assert out.startswith("Error:")
        assert "reminder" in out and "monitor" in out
        assert service.added == []


class TestModeIsDiscoverable:
    """Il modello sceglie il modo leggendo lo schema: deve poterlo trovare."""

    def test_the_schema_offers_exactly_the_two_modes(self) -> None:
        tool = _tool(_RecordingCronService())

        assert tool.parameters["properties"]["mode"]["enum"] == ["reminder", "monitor"]

    def test_the_schema_warns_that_monitor_needs_a_recurring_schedule(self) -> None:
        tool = _tool(_RecordingCronService())

        description = tool.parameters["properties"]["mode"]["description"]
        assert "cannot be used with at" in description

    def test_mode_stays_out_of_the_top_level_required_list(self) -> None:
        """Renderlo obbligatorio spezzerebbe ``list``/``remove`` (cfr. #3113)."""
        tool = _tool(_RecordingCronService())

        assert tool.parameters["required"] == ["action"]

    def test_listing_marks_monitors_and_leaves_reminders_alone(self) -> None:
        jobs = [
            CronJob(
                id="j1",
                name="Promemoria",
                schedule=CronSchedule(kind="every", every_ms=60_000),
                payload=CronPayload(kind="agent_turn", mode="reminder", message="x"),
            ),
            CronJob(
                id="j2",
                name="Guardiano",
                schedule=CronSchedule(kind="every", every_ms=60_000),
                payload=CronPayload(kind="agent_turn", mode="monitor", message="y"),
            ),
        ]
        tool = _tool(_RecordingCronService(jobs))

        listing = tool._list_jobs()

        monitor_line = next(line for line in listing.splitlines() if "Guardiano" in line)
        reminder_line = next(line for line in listing.splitlines() if "Promemoria" in line)
        assert "monitor" in monitor_line
        assert "monitor" not in reminder_line


@pytest.mark.parametrize("mode", ["reminder", "monitor"])
def test_both_modes_pass_schema_validation(mode: str) -> None:
    registry = _registry(_tool(_RecordingCronService()))

    _name, _args, err = registry.prepare_call(
        "cron", {"action": "add", "message": "x", "every_seconds": 60, "mode": mode}
    )

    assert err is None
