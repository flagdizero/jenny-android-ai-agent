"""Tests for AppActionTool (manifest action → native agent tool)."""

from __future__ import annotations

import json

from jenny.agent.tools.app_actions import AppActionTool
from jenny.apps.manifest import AppAction


def _storage_action(op="append") -> AppAction:
    return AppAction(
        name="add_note", description="Aggiunge una nota", kind="storage",
        op=op, collection="notes",
        params={"testo": {"type": "string"}}, required=["testo"],
    )


def _write_note_app(workspace):
    app_dir = workspace / "apps" / "note"
    app_dir.mkdir(parents=True)
    (app_dir / "app.json").write_text(json.dumps({
        "name": "Note", "description": "Note veloci",
        "actions": [
            {"name": "add_note", "description": "Aggiunge", "kind": "storage",
             "op": "append", "collection": "notes",
             "params": {"testo": {"type": "string"}}, "required": ["testo"]},
            {"name": "list_notes", "description": "Elenca", "kind": "storage",
             "op": "query", "collection": "notes"},
        ],
    }), encoding="utf-8")
    return app_dir


class TestToolShape:
    def test_name_description_parameters(self, tmp_path):
        tool = AppActionTool(tmp_path, "note", "Note", _storage_action())
        assert tool.name == "note_add_note"
        assert tool.description == "[App Note] Aggiunge una nota"
        assert tool.parameters == {
            "type": "object",
            "properties": {"testo": {"type": "string"}},
            "required": ["testo"],
            "additionalProperties": False,
        }

    def test_hyphenated_slug_not_normalized(self, tmp_path):
        tool = AppActionTool(tmp_path, "le-piante", "Piante", _storage_action())
        assert tool.name == "le-piante_add_note"

    def test_read_only_flags(self, tmp_path):
        assert AppActionTool(tmp_path, "n", "N", _storage_action("query")).read_only
        assert not AppActionTool(tmp_path, "n", "N", _storage_action("append")).read_only
        http_get = AppAction(name="a", description="d", kind="http",
                             method="GET", path="/x")
        assert AppActionTool(tmp_path, "n", "N", http_get).read_only

    def test_to_schema(self, tmp_path):
        schema = AppActionTool(tmp_path, "note", "Note", _storage_action()).to_schema()
        assert schema["function"]["name"] == "note_add_note"
        assert schema["function"]["parameters"]["required"] == ["testo"]


class TestExecute:
    async def test_storage_round_trip(self, tmp_path):
        _write_note_app(tmp_path)
        add = AppActionTool(tmp_path, "note", "Note", _storage_action())
        outcome = await add.execute(testo="ciao")
        assert outcome.ok is True
        result = json.loads(outcome.render())
        assert result["ok"] is True
        assert result["record"]["testo"] == "ciao"
        stored = (tmp_path / "apps" / "note" / "data" / "notes.jsonl").read_text()
        assert "ciao" in stored

    async def test_error_surfaces_as_tool_result(self, tmp_path):
        _write_note_app(tmp_path)
        add = AppActionTool(tmp_path, "note", "Note", _storage_action())
        outcome = await add.execute()
        assert outcome.ok is False
        assert outcome.error is not None and outcome.error.code == "app_action"
        assert "testo" in outcome.render()

    async def test_missing_app_is_error_result(self, tmp_path):
        add = AppActionTool(tmp_path, "note", "Note", _storage_action())
        outcome = await add.execute(testo="x")
        assert outcome.ok is False

    async def test_storage_mutation_announces_data_changed_from_any_channel(self, tmp_path):
        """L'avviso va alla WebUI (``apps.notify``), non sul canale del turno: da
        un turno Telegram lo schermo aperto dell'app si rilegge lo stesso."""
        from jenny.agent.tools.context import (
            RequestContext,
            bind_request_context,
            reset_request_context,
        )
        from jenny.runtime import ui_events

        heard: list[dict] = []

        async def note(event: dict) -> None:
            heard.append(event)

        _write_note_app(tmp_path)
        ui_events.set_publisher(note)
        req = bind_request_context(RequestContext(channel="telegram", chat_id="42",
                                                  session_key="unified:default"))
        try:
            add = AppActionTool(tmp_path, "note", "Note", _storage_action())
            await add.execute(testo="ciao")
            query = AppActionTool(tmp_path, "note", "Note", _storage_action("query"))
            await query.execute()
        finally:
            reset_request_context(req)
            ui_events.set_publisher(None)
        assert heard == [{"_app_data_changed": True, "app_slug": "note"}]
