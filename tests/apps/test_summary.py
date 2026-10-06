"""Tests for the agent-context apps summary."""

from __future__ import annotations

import json

from jenny.apps.summary import build_apps_summary

MANIFEST = {
    "name": "Note",
    "description": "Note veloci",
    "actions": [
        {"name": "add_note", "description": "Aggiunge", "kind": "storage",
         "op": "append", "collection": "notes",
         "params": {"testo": {"type": "string"}}, "required": ["testo"]},
    ],
}


def _write_app(workspace, slug="note", manifest=MANIFEST):
    app_dir = workspace / "apps" / slug
    app_dir.mkdir(parents=True)
    (app_dir / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    return app_dir


class TestBuildAppsSummary:
    def test_empty_workspace_is_empty_string(self, tmp_path):
        assert build_apps_summary(tmp_path) == ""

    def test_app_line_has_name_tools_and_data_path(self, tmp_path):
        _write_app(tmp_path)
        summary = build_apps_summary(tmp_path)
        assert "**Note** (`note`)" in summary
        assert "`note_add_note`" in summary
        assert "`apps/note/data/`" in summary
        assert "AGENT.md" not in summary

    def test_agent_md_referenced_by_path_only(self, tmp_path):
        app_dir = _write_app(tmp_path)
        (app_dir / "AGENT.md").write_text("Il basilico va annaffiato.", encoding="utf-8")
        summary = build_apps_summary(tmp_path)
        assert "`apps/note/AGENT.md`" in summary
        # Progressive disclosure: the content itself must never be inlined.
        assert "basilico" not in summary

    def test_display_only_app_line_is_well_formed(self, tmp_path):
        """Zero azioni non deve produrre un `— tools: ` vuoto in mezzo alla riga.

        Un segmento vuoto si legge come un elenco che non è stato caricato, cioè
        come un guasto, proprio nel contesto che l'agente usa per decidere se ha
        un tool a disposizione.
        """
        _write_app(tmp_path, slug="vista", manifest={
            "name": "Vista", "description": "Solo uno schermo",
        })
        summary = build_apps_summary(tmp_path)
        assert "**Vista** (`vista`)" in summary
        assert "tools: —" not in summary
        assert "tools:  " not in summary
        assert "display-only" in summary
        assert "`apps/vista/data/`" in summary

    def test_broken_app_line(self, tmp_path):
        app_dir = tmp_path / "apps" / "rotta"
        app_dir.mkdir(parents=True)
        (app_dir / "app.json").write_text("{broken", encoding="utf-8")
        summary = build_apps_summary(tmp_path)
        assert "**rotta** — BROKEN" in summary
        assert "apps/rotta/app.json" in summary


def test_in_a_notebook_the_placed_apps_come_first_and_the_others_are_read_only(tmp_path):
    """In un quaderno si usano le app posate sulla sua bacheca; le altre si nominano."""
    import json

    from jenny.board.store import mutate_board

    for slug, name in (("etf", "ETF"), ("spesa", "Spesa")):
        d = tmp_path / "apps" / slug
        d.mkdir(parents=True)
        (d / "app.json").write_text(json.dumps({
            "name": name, "description": "x",
            "actions": [{"name": "add", "description": "x", "kind": "storage",
                         "op": "append", "collection": "items"}]}))
    root = tmp_path / "wikis" / "soldi"
    (root / "wiki").mkdir(parents=True)
    mutate_board(root, lambda b: b["cards"].append({
        "id": "etf", "title": "ETF", "pos": {"x": 1, "y": 1}, "app": {"slug": "etf"}}))
    summary = build_apps_summary(tmp_path, notebook_root=root)
    placed, others = summary.split("Other apps")
    assert "`etf_add`" in placed and "spesa" not in placed
    assert "Spesa (`spesa`)" in others and "read-only" in others
