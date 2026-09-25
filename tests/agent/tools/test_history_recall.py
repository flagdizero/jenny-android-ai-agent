"""``recall_history``: il verbale grezzo, e il confine che non attraversa.

Perché esiste accanto a ``recall``: l'archivio contiene fatti *retrocessi* dai
file di identità, cioè roba che Dream ha giudicato degna. ``history.jsonl`` è il
verbale turno per turno, prima di qualunque giudizio — ed è la parte **sotto** il
cursore di Dream che non legge nessuno, perché quella sopra finisce già nel
prompt di ogni turno.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.agent.tools.memory_recall import (
    _INDEX_MAX_CHARS,
    HistoryRecallTool,
    MemoryRecallTool,
)
from jenny.security.workspace_access import enter_workspace_scope


@dataclass
class _Scope:
    """Scope minimale: ``enter_workspace_scope`` lo tratta come opaco."""

    project_path: Path
    restrict_to_workspace: bool = False
    writable: bool = True


def _workspace(tmp_path: Path, entries: list[dict]) -> Path:
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "history.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _entry(cursor: int, content: str, ts: str = "2026-08-21 18:24") -> dict:
    return {"cursor": cursor, "timestamp": ts, "content": content,
            "session_key": "unified:default"}


def _tool(root: Path) -> HistoryRecallTool:
    return HistoryRecallTool(workspace=root)


class TestIndex:
    async def test_newest_first(self, tmp_path):
        root = _workspace(tmp_path, [_entry(1, "- prima"), _entry(9, "- ultima")])
        out = await _tool(root).execute()
        assert out.index("[9]") < out.index("[1]")

    async def test_one_line_per_turn_with_the_cursor(self, tmp_path):
        root = _workspace(tmp_path, [_entry(120, "- [durable] Soglia acero: sotto 15%")])
        out = await _tool(root).execute()
        assert "[120]" in out
        assert "Soglia acero" in out

    async def test_says_how_many_other_notes_it_carries(self, tmp_path):
        """Tre quarti delle voci vere hanno piu' di una nota (74 su 108, misurate).

        Senza il conteggio, il modello crede che la voce sia solo la prima riga.
        """
        root = _workspace(tmp_path, [_entry(5, "- una\n- due\n- tre")])
        out = await _tool(root).execute()
        assert "(+2)" in out

    async def test_a_single_note_carries_no_count(self, tmp_path):
        root = _workspace(tmp_path, [_entry(5, "- una sola")])
        assert "(+" not in await _tool(root).execute()

    async def test_the_cut_is_announced_and_drops_the_oldest(self, tmp_path):
        long = [_entry(i, "- " + "x" * 200) for i in range(1, 400)]
        root = _workspace(tmp_path, long)
        out = await _tool(root).execute()
        assert "older turns are not listed" in out
        assert len(out) < _INDEX_MAX_CHARS + 2_000
        assert "[399]" in out          # la piu' recente resta
        assert "[1] " not in out       # la piu' vecchia e' quella che cade


class TestOpening:
    async def test_opens_by_cursor(self, tmp_path):
        root = _workspace(tmp_path, [_entry(7, "- [durable] tutto il testo lungo")])
        out = await _tool(root).execute(cursors=["7"])
        assert "tutto il testo lungo" in out

    async def test_a_cursor_that_does_not_exist_is_reported(self, tmp_path):
        """Restituire in silenzio "solo le altre" fa concludere che la voce non c'e'."""
        root = _workspace(tmp_path, [_entry(7, "- c'e'")])
        out = await _tool(root).execute(cursors=["7", "999"])
        assert "c'e'" in out
        assert "999" in out

    async def test_too_many_entries_stops_and_says_so(self, tmp_path):
        big = [_entry(i, "- " + "y" * 5_000) for i in range(1, 8)]
        root = _workspace(tmp_path, big)
        out = await _tool(root).execute(cursors=[str(i) for i in range(1, 8)])
        assert "stopped at" in out


class TestDifficultFiles:
    async def test_no_file(self, tmp_path):
        (tmp_path / "memory").mkdir()
        out = await _tool(tmp_path).execute()
        assert "nothing here to read" in out

    async def test_a_broken_line_does_not_hide_the_healthy_ones(self, tmp_path):
        """Un append-only troncato da uno spegnimento non deve azzerare il resto."""
        root = _workspace(tmp_path, [_entry(1, "- sana")])
        p = root / "memory" / "history.jsonl"
        p.write_text(p.read_text() + '{"cursor": 2, "conte\n', encoding="utf-8")
        out = await _tool(root).execute()
        assert "sana" in out


class TestProjectBoundary:
    async def test_inside_a_project_stays_silent(self, tmp_path):
        root = _workspace(tmp_path, [_entry(1, "- personale")])
        project = tmp_path / "wikis" / "qualcosa"
        project.mkdir(parents=True)
        with enter_workspace_scope(_Scope(project_path=project)):
            out = await _tool(root).execute()
        assert "scoped to a project" in out
        assert "personale" not in out

    async def test_outside_a_project_reads(self, tmp_path):
        root = _workspace(tmp_path, [_entry(1, "- personale")])
        with enter_workspace_scope(_Scope(project_path=root)):
            out = await _tool(root).execute()
        assert "personale" in out

    async def test_without_scope_reads(self, tmp_path):
        root = _workspace(tmp_path, [_entry(1, "- personale")])
        assert "personale" in await _tool(root).execute()

    async def test_also_when_opening_a_cursor(self, tmp_path):
        """Il confine vale sull'apertura come sull'elenco, non solo sulla vetrina."""
        root = _workspace(tmp_path, [_entry(1, "- personale")])
        project = tmp_path / "wikis" / "qualcosa"
        project.mkdir(parents=True)
        with enter_workspace_scope(_Scope(project_path=project)):
            out = await _tool(root).execute(cursors=["1"])
        assert "personale" not in out


class TestAlongsideRecall:
    def test_the_same_scopes(self):
        assert HistoryRecallTool._scopes == MemoryRecallTool._scopes == {"core", "orchestrator"}

    def test_both_read_only(self, tmp_path):
        assert _tool(tmp_path).read_only is True

    def test_the_two_names_are_not_confused(self, tmp_path):
        assert _tool(tmp_path).name == "recall_history"

    @pytest.mark.parametrize("expected", ["verbatim", "recall holds facts"])
    def test_the_description_says_which_window_it_covers(self, tmp_path, expected):
        """Due popolazioni diverse: se le descrizioni non lo dicono, sceglie a caso."""
        assert expected in _tool(tmp_path).description

    def test_recall_also_says_where_its_own_ends(self, tmp_path):
        """Il rimando e' nei due sensi, o il modello non sa che l'altro esiste."""
        assert "recall_history" in MemoryRecallTool(workspace=tmp_path).description

    def test_create_takes_the_root(self):
        t = HistoryRecallTool.create(SimpleNamespace(workspace="/tmp/radice"))
        assert t._memory_dir == Path("/tmp/radice/memory")
