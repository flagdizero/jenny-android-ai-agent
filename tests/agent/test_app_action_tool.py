"""Tests for ``use_app``: every action of every Jenny App behind one tool."""

from __future__ import annotations

import json

from jenny.agent.tools.app_actions import AppToolsSyncer, UseAppTool
from jenny.agent.tools.registry import ToolRegistry
from jenny.apps.manifest import AppAction, action_signature


def _write_note_app(workspace, slug="note", agent_md=False):
    app_dir = workspace / "apps" / slug
    app_dir.mkdir(parents=True)
    (app_dir / "app.json").write_text(json.dumps({
        "name": "Note", "description": "Note veloci",
        "actions": [
            {"name": "add_note", "description": "Aggiunge", "kind": "storage",
             "op": "append", "collection": "notes",
             "params": {"testo": {"type": "string"}, "quando": {"type": "string"}},
             "required": ["testo"]},
            {"name": "list_notes", "description": "Elenca", "kind": "storage",
             "op": "query", "collection": "notes"},
            {"name": "del_note", "description": "Elimina", "kind": "storage",
             "op": "delete", "collection": "notes"},
        ],
    }), encoding="utf-8")
    if agent_md:
        (app_dir / "AGENT.md").write_text("# Note\n", encoding="utf-8")
    return app_dir


def _tool(workspace) -> UseAppTool:
    registry = ToolRegistry()
    AppToolsSyncer(workspace).sync(registry)
    tool = registry.get("use_app")
    assert isinstance(tool, UseAppTool)
    return tool


class TestSignature:
    def test_required_then_optional(self):
        action = AppAction(name="add_todo", description="d", kind="storage", op="append",
                           collection="t", params={"due": {"type": "string"},
                                                   "text": {"type": "string"}},
                           required=["text"])
        assert action_signature(action) == "add_todo(text, due?)"

    def test_storage_reserved_params_are_there(self):
        delete = AppAction(name="drop", description="d", kind="storage", op="delete",
                           collection="t")
        query = AppAction(name="list", description="d", kind="storage", op="query",
                          collection="t")
        assert action_signature(delete) == "drop(id)"
        assert action_signature(query) == "list(limit?)"


class TestExecute:
    async def test_storage_round_trip(self, tmp_path):
        _write_note_app(tmp_path)
        outcome = await _tool(tmp_path).execute(app="note", action="add_note",
                                                args={"testo": "ciao"})
        assert outcome.ok is True
        result = json.loads(outcome.render())
        assert result["ok"] is True and result["record"]["testo"] == "ciao"
        assert "ciao" in (tmp_path / "apps" / "note" / "data" / "notes.jsonl").read_text()

    async def test_args_sent_as_json_text_are_read(self, tmp_path):
        _write_note_app(tmp_path)
        outcome = await _tool(tmp_path).execute(app="note", action="add_note",
                                                args='{"testo": "ciao"}')
        assert outcome.ok is True

    async def test_wrong_args_answer_with_the_real_schema(self, tmp_path):
        _write_note_app(tmp_path)
        outcome = await _tool(tmp_path).execute(app="note", action="add_note",
                                                args={"text": "ciao"})
        assert outcome.ok is False
        assert outcome.error is not None and outcome.error.code == "app_action"
        text = outcome.render()
        assert "add_note(testo, quando?)" in text and '"testo"' in text

    async def test_only_the_app_describes_it(self, tmp_path):
        _write_note_app(tmp_path, agent_md=True)
        outcome = await _tool(tmp_path).execute(app="note")
        assert outcome.ok is True
        text = outcome.render()
        assert "`add_note(testo, quando?)` (changes data)" in text
        assert "`list_notes(limit?)` (reads)" in text
        assert '"quando": {"type": "string"}' in text
        assert "apps/note/AGENT.md" in text

    async def test_an_unknown_app_lists_the_ones_there_are(self, tmp_path):
        _write_note_app(tmp_path)
        outcome = await _tool(tmp_path).execute(app="todo", action="add")
        assert outcome.ok is False and "`note`" in outcome.render()

    async def test_an_unknown_action_lists_the_app_actions(self, tmp_path):
        _write_note_app(tmp_path)
        outcome = await _tool(tmp_path).execute(app="note", action="add")
        assert outcome.ok is False and "`add_note(testo, quando?)`" in outcome.render()

    async def test_a_read_only_turn_reads_and_does_not_write(self, tmp_path):
        from jenny.security.workspace_access import (
            READONLY_TOOL_REFUSAL,
            build_workspace_scope,
            enter_workspace_scope,
        )

        _write_note_app(tmp_path)
        tool = _tool(tmp_path)
        scope = build_workspace_scope(tmp_path, "restricted").without_write_access()
        with enter_workspace_scope(scope):
            write = await tool.execute(app="note", action="add_note", args={"testo": "x"})
            read = await tool.execute(app="note", action="list_notes")
        assert write.ok is False and READONLY_TOOL_REFUSAL in write.render()
        assert read.ok is True

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
        tool = _tool(tmp_path)
        ui_events.set_publisher(note)
        req = bind_request_context(RequestContext(channel="telegram", chat_id="42",
                                                  session_key="unified:default"))
        try:
            await tool.execute(app="note", action="add_note", args={"testo": "ciao"})
            await tool.execute(app="note", action="list_notes")
        finally:
            reset_request_context(req)
            ui_events.set_publisher(None)
        assert heard == [{"_app_data_changed": True, "app_slug": "note"}]
