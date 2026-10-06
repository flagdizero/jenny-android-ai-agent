"""Tests for AppToolsSyncer (per-turn registry diffing)."""

from __future__ import annotations

import json
import os

from jenny.agent.tools.app_actions import AppActionTool, AppToolsSyncer
from jenny.agent.tools.registry import ToolRegistry


def _write_manifest(workspace, slug="note", actions=None):
    app_dir = workspace / "apps" / slug
    app_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": slug.capitalize(),
        "description": "test",
        "actions": actions or [
            {"name": "add_note", "description": "Aggiunge", "kind": "storage",
             "op": "append", "collection": "notes",
             "params": {"testo": {"type": "string"}}, "required": ["testo"]},
        ],
    }
    path = app_dir / "app.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _bump_mtime(path):
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


class TestSyncer:
    def test_initial_registration(self, tmp_path):
        _write_manifest(tmp_path)
        registry = ToolRegistry()
        names, changed = AppToolsSyncer(tmp_path).sync(registry)
        assert names == ["note_add_note"]
        assert changed is True
        assert isinstance(registry.get("note_add_note"), AppActionTool)

    def test_unchanged_sync_is_stable(self, tmp_path):
        _write_manifest(tmp_path)
        registry = ToolRegistry()
        syncer = AppToolsSyncer(tmp_path)
        syncer.sync(registry)
        tool = registry.get("note_add_note")
        syncer.sync(registry)
        assert registry.get("note_add_note") is tool  # not re-created

    def test_manifest_change_reregisters(self, tmp_path):
        path = _write_manifest(tmp_path)
        registry = ToolRegistry()
        syncer = AppToolsSyncer(tmp_path)
        syncer.sync(registry)
        _write_manifest(tmp_path, actions=[
            {"name": "add_note", "description": "Aggiunge", "kind": "storage",
             "op": "append", "collection": "notes",
             "params": {"testo": {"type": "string"}}, "required": ["testo"]},
            {"name": "list_notes", "description": "Elenca", "kind": "storage",
             "op": "query", "collection": "notes"},
        ])
        _bump_mtime(path)
        names, changed = syncer.sync(registry)
        assert sorted(names) == ["note_add_note", "note_list_notes"]
        assert changed is True

    def test_deleted_app_unregisters(self, tmp_path):
        _write_manifest(tmp_path)
        registry = ToolRegistry()
        syncer = AppToolsSyncer(tmp_path)
        syncer.sync(registry)
        import shutil

        shutil.rmtree(tmp_path / "apps" / "note")
        names, changed = syncer.sync(registry)
        assert names == []
        assert changed is True
        assert registry.get("note_add_note") is None

    def test_broken_manifest_unregisters(self, tmp_path):
        path = _write_manifest(tmp_path)
        registry = ToolRegistry()
        syncer = AppToolsSyncer(tmp_path)
        syncer.sync(registry)
        path.write_text("{broken", encoding="utf-8")
        _bump_mtime(path)
        names, changed = syncer.sync(registry)
        assert names == []
        assert changed is True
        assert registry.get("note_add_note") is None

    def test_collision_with_non_app_tool_skipped(self, tmp_path):
        _write_manifest(tmp_path)
        registry = ToolRegistry()

        class FakeTool(AppActionTool):
            pass

        class NonAppTool:
            name = "note_add_note"
            description = "existing"

            def to_schema(self):
                return {"type": "function",
                        "function": {"name": self.name, "description": "x",
                                     "parameters": {"type": "object", "properties": {}}}}

        registry.register(NonAppTool())
        names, _ = AppToolsSyncer(tmp_path).sync(registry)
        assert names == []
        assert not isinstance(registry.get("note_add_note"), FakeTool)

    def test_no_apps_folder(self, tmp_path):
        registry = ToolRegistry()
        names, changed = AppToolsSyncer(tmp_path).sync(registry)
        assert names == []
        assert changed is False
