"""Tests for AppToolsSyncer: ``use_app`` and the apps it reaches, per turn."""

from __future__ import annotations

import json
import os

from jenny.agent.tools.app_actions import AppToolsSyncer, UseAppTool
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
        assert names == ["note.add_note"]
        assert changed is True
        assert isinstance(registry.get("use_app"), UseAppTool)
        assert registry.tool_names == ["use_app"]

    def test_unchanged_sync_is_stable(self, tmp_path):
        _write_manifest(tmp_path)
        registry = ToolRegistry()
        syncer = AppToolsSyncer(tmp_path)
        syncer.sync(registry)
        tool = registry.get("use_app")
        definitions = registry.get_definitions()
        _names, changed = syncer.sync(registry)
        assert changed is False
        assert registry.get("use_app") is tool
        assert registry.get_definitions() is definitions  # the cached list stays

    def test_a_second_app_does_not_change_the_tool_list(self, tmp_path):
        """La lista dei tool non cambia con le app: cambia solo il prompt."""
        _write_manifest(tmp_path)
        registry = ToolRegistry()
        syncer = AppToolsSyncer(tmp_path)
        syncer.sync(registry)
        before = registry.get_definitions()
        _write_manifest(tmp_path, slug="spesa")
        names, changed = syncer.sync(registry)
        assert changed is True and sorted(names) == ["note.add_note", "spesa.add_note"]
        assert registry.get_definitions() == before

    def test_manifest_change_is_seen(self, tmp_path):
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
        assert sorted(names) == ["note.add_note", "note.list_notes"]
        assert changed is True

    def test_deleted_app_removes_the_tool(self, tmp_path):
        _write_manifest(tmp_path)
        registry = ToolRegistry()
        syncer = AppToolsSyncer(tmp_path)
        syncer.sync(registry)
        import shutil

        shutil.rmtree(tmp_path / "apps" / "note")
        names, changed = syncer.sync(registry)
        assert names == []
        assert changed is True
        assert registry.get("use_app") is None

    def test_broken_manifest_has_no_actions(self, tmp_path):
        path = _write_manifest(tmp_path)
        registry = ToolRegistry()
        syncer = AppToolsSyncer(tmp_path)
        syncer.sync(registry)
        path.write_text("{broken", encoding="utf-8")
        _bump_mtime(path)
        names, changed = syncer.sync(registry)
        assert names == []
        assert changed is True
        assert registry.get("use_app") is None

    def test_display_only_apps_bring_no_tool(self, tmp_path):
        path = _write_manifest(tmp_path)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**manifest, "actions": []}), encoding="utf-8")
        registry = ToolRegistry()
        names, _ = AppToolsSyncer(tmp_path).sync(registry)
        assert names == [] and registry.get("use_app") is None

    def test_collision_with_non_app_tool_skipped(self, tmp_path):
        _write_manifest(tmp_path)
        registry = ToolRegistry()

        class NonAppTool:
            name = "use_app"
            description = "existing"

            def to_schema(self):
                return {"type": "function",
                        "function": {"name": self.name, "description": "x",
                                     "parameters": {"type": "object", "properties": {}}}}

        existing = NonAppTool()
        registry.register(existing)
        AppToolsSyncer(tmp_path).sync(registry)
        assert registry.get("use_app") is existing

    def test_no_apps_folder(self, tmp_path):
        registry = ToolRegistry()
        names, changed = AppToolsSyncer(tmp_path).sync(registry)
        assert names == []
        assert changed is False
