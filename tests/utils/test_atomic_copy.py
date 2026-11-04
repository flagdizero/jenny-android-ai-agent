"""``atomic_copy``: una copia a metà non prende mai il nome buono."""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.utils import path as path_mod
from jenny.utils.path import atomic_copy


def test_the_copy_lands_whole_and_leaves_no_temporary(tmp_path: Path) -> None:
    source = tmp_path / "a.bin"
    source.write_bytes(b"x" * 10_000)
    dest = tmp_path / "out" / "a.bin"

    atomic_copy(source, dest)

    assert dest.read_bytes() == source.read_bytes()
    assert sorted(p.name for p in dest.parent.iterdir()) == ["a.bin"]


def test_a_failed_copy_keeps_the_old_file_and_removes_the_temporary(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "new.bin"
    source.write_bytes(b"nuovo")
    dest = tmp_path / "old.bin"
    dest.write_bytes(b"vecchio")

    def half_copy(_src, dst):
        Path(dst).write_bytes(b"nu")
        raise OSError("disco pieno")

    monkeypatch.setattr(path_mod.shutil, "copyfile", half_copy)
    with pytest.raises(OSError):
        atomic_copy(source, dest)

    assert dest.read_bytes() == b"vecchio"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new.bin", "old.bin"]
