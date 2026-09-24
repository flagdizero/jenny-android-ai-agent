"""La regola che decide quando una riga di modifica «live» si riemette.

Era scritta tre volte in ``file_edit_streaming.py`` (emissione live, conteggio
prima del path, un file di una patch): prima volta sempre, stessi numeri mai,
un salto di almeno ``_LIVE_EMIT_LINE_STEP`` righe subito, altrimenti non prima
di ``_LIVE_EMIT_INTERVAL_S``. Qui si fissa sui tre punti.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.utils import file_edit_streaming as fes
from jenny.utils.file_edit_events import FileEditTracker, read_file_snapshot

STEP = fes._LIVE_EMIT_LINE_STEP
INTERVAL = fes._LIVE_EMIT_INTERVAL_S


def _live():
    state = fes._StreamingFileEditState(key="k")
    return state.should_emit, state.mark_emitted, state


def _pending():
    state = fes._StreamingFileEditState(key="k")
    return state.should_emit_pending, state.mark_pending_emitted, state


def _patch(tmp_path: Path):
    path = tmp_path / "f.txt"
    tracker = FileEditTracker(call_id="c", tool="apply_patch", path=path,
                              display_path="f.txt", before=read_file_snapshot(path))
    state = fes._StreamingPatchFileState(tracker=tracker)
    return state.should_emit, state.mark_emitted, state


@pytest.fixture(params=["live", "pending", "patch"])
def gate(request, tmp_path):
    return {"live": _live, "pending": _pending, "patch": lambda: _patch(tmp_path)}[request.param]()


def test_the_rule(gate) -> None:
    should, mark, _state = gate
    t = 100.0
    assert should(1, 0, t) is True, "la prima volta si emette sempre"
    mark(1, 0, t)
    assert should(1, 0, t + INTERVAL * 10) is False, "stessi numeri: mai"
    assert should(2, 0, t + INTERVAL / 10) is False, "piccolo cambio, troppo presto"
    assert should(2, 0, t + INTERVAL) is True, "piccolo cambio, intervallo passato"
    assert should(1 + STEP, 0, t) is True, "salto di righe: subito"
    assert should(1, STEP, t) is True, "anche sulle righe tolte"
    mark(1 + STEP, 0, t + 1)
    assert should(1 + STEP, 0, t + 1 + INTERVAL * 10) is False


def test_a_patch_file_remembers_the_last_counts_it_saw(tmp_path: Path) -> None:
    """``flush`` rilegge ``last_added``/``last_deleted``: vanno aggiornati anche
    quando la riga non si emette, o il conto finale resta indietro."""
    should, mark, state = _patch(tmp_path)
    mark(1, 0, 100.0)
    assert should(2, 1, 100.0 + INTERVAL / 10) is False
    assert (state.last_added, state.last_deleted) == (2, 1)
