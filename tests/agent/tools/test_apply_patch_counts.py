"""I numeri ``(+N/-M)`` che ``apply_patch`` riporta, e che siano quelli della WebUI.

``apply_patch`` contava le righe con una copia sua del diff, mentre la riga che
la WebUI mostra durante la modifica le conta con ``file_edit_events``: due
conti dello stesso fatto. Qui si fissano i casi normali — LF, CRLF, file nuovo,
append, riga finale senza a capo — e la scelta per l'unico caso in cui le due
copie divergevano: un separatore Unicode (``\\f``) in un file che parte vuoto.
Vale il conto di ``file_edit_events``, cioè ``\\f`` non è un a capo.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from jenny.agent.tools.apply_patch import ApplyPatchTool


def _stats(result: str, path: str) -> tuple[int, int]:
    m = re.search(rf"{re.escape(path)} \(\+(\d+)/-(\d+)\)", result)
    if m is None:
        assert f" {path}" in result, result
        return 0, 0
    return int(m.group(1)), int(m.group(2))


def _run(tmp_path: Path, edits: list[dict]) -> str:
    return asyncio.run(ApplyPatchTool(workspace=tmp_path).execute(edits=edits))


def test_a_replacement_counts_one_in_one_out(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    result = _run(tmp_path, [{"path": "a.py", "action": "replace", "old_text": "y = 2", "new_text": "y = 3"}])
    assert _stats(result, "a.py") == (1, 1)


def test_crlf_files_count_like_lf_files(tmp_path: Path) -> None:
    (tmp_path / "w.txt").write_bytes(b"uno\r\ndue\r\n")
    result = _run(tmp_path, [{"path": "w.txt", "action": "replace", "old_text": "due", "new_text": "due\ntre"}])
    # «due» resta, «tre» si aggiunge: una riga in più e nessuna in meno.
    assert _stats(result, "w.txt") == (1, 0)
    assert (tmp_path / "w.txt").read_bytes() == b"uno\r\ndue\r\ntre\r\n"


@pytest.mark.parametrize(("text", "lines"), [
    ("DEBUG = True", 1),
    ("uno\ndue\n", 2),
    ("uno\n\n", 2),
    ("uno\r\ndue", 2),
])
def test_a_new_file_counts_its_lines(tmp_path: Path, text: str, lines: int) -> None:
    result = _run(tmp_path, [{"path": "n.txt", "action": "add", "new_text": text}])
    assert _stats(result, "n.txt") == (lines, 0)


def test_an_append_counts_only_the_new_lines(tmp_path: Path) -> None:
    (tmp_path / "log.md").write_text("riga\n", encoding="utf-8")
    result = _run(tmp_path, [{"path": "log.md", "action": "add", "new_text": "altra\nancora\n"}])
    assert _stats(result, "log.md") == (2, 0)


def test_a_form_feed_is_not_a_line_break(tmp_path: Path) -> None:
    """La scelta, fissata: ``\\f`` dentro una riga non la spezza in due."""
    result = _run(tmp_path, [{"path": "ff.txt", "action": "add", "new_text": "pagina1\fpagina2\n"}])
    assert _stats(result, "ff.txt") == (1, 0)
