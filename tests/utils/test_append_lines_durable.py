"""``append_lines_durable``: accodare e portare su disco, come la chiedono tre chiamanti."""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.utils import path as path_mod
from jenny.utils.path import append_lines_durable


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
