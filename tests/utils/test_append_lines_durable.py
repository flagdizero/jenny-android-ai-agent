"""``append_lines_durable`` e ``append_with_header``: accodare a un file di testo."""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.utils import path as path_mod
from jenny.utils.path import append_lines_durable, append_with_header


def test_lines_are_appended_each_with_its_newline(tmp_path: Path) -> None:
    target = tmp_path / "log.jsonl"
    target.write_text("prima\n", encoding="utf-8")
    append_lines_durable(target, ['{"a": 1}', '{"b": 2}'])
    assert target.read_text(encoding="utf-8") == 'prima\n{"a": 1}\n{"b": 2}\n'


def test_the_file_is_fsynced(tmp_path: Path, monkeypatch) -> None:
    calls: list[int] = []
    real = path_mod.os.fsync
    monkeypatch.setattr(path_mod.os, "fsync", lambda fd: (calls.append(fd), real(fd)))
    append_lines_durable(tmp_path / "x", ["riga"])
    assert len(calls) == 1


def test_an_fsync_failure_raises_by_default(tmp_path: Path, monkeypatch) -> None:
    def boom(_fd):
        raise OSError("disco pieno")

    monkeypatch.setattr(path_mod.os, "fsync", boom)
    with pytest.raises(OSError):
        append_lines_durable(tmp_path / "x", ["riga"])


def test_an_fsync_failure_can_be_tolerated_and_the_line_is_there(tmp_path: Path, monkeypatch) -> None:
    def boom(_fd):
        raise OSError("disco pieno")

    monkeypatch.setattr(path_mod.os, "fsync", boom)
    target = tmp_path / "x"
    append_lines_durable(target, ["riga"], tolerate_fsync_error=True)
    assert target.read_text(encoding="utf-8") == "riga\n"


def test_a_torn_last_line_is_closed_before_the_new_ones(tmp_path: Path) -> None:
    """Un append interrotto lascia l'ultima riga senza a capo: la riga nuova non
    le si attacca, così si perde solo quella rotta e non anche la nuova."""
    target = tmp_path / "log.jsonl"
    target.write_text('{"a": 1}\n{"b": ', encoding="utf-8")
    append_lines_durable(target, ['{"c": 3}'])
    assert target.read_text(encoding="utf-8") == '{"a": 1}\n{"b": \n{"c": 3}\n'


def test_a_new_file_starts_without_a_blank_line(tmp_path: Path) -> None:
    target = tmp_path / "new.jsonl"
    append_lines_durable(target, ["riga"])
    assert target.read_text(encoding="utf-8") == "riga\n"


def test_the_header_goes_only_on_an_empty_file(tmp_path: Path) -> None:
    page = tmp_path / "journal" / "2026-11-02.md"
    assert append_with_header(page, "- prima\n", header="# 2026-11-02\n\n") is True
    assert append_with_header(page, "- seconda\n", header="# 2026-11-02\n\n") is False
    assert page.read_text(encoding="utf-8") == "# 2026-11-02\n\n- prima\n- seconda\n"


def test_a_file_that_exists_but_is_empty_still_gets_its_header(tmp_path: Path) -> None:
    """Il vuoto lo dice il file aperto, non ``exists()`` chiesto prima."""
    page = tmp_path / "page.md"
    page.write_text("", encoding="utf-8")
    assert append_with_header(page, "riga\n", header="# titolo\n") is True
    assert page.read_text(encoding="utf-8") == "# titolo\nriga\n"
