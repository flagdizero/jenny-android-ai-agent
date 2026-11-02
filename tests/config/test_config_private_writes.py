"""``config.json`` e il suo ``.bak`` non esistono mai, nemmeno un istante, oltre il 600.

Portano le chiavi API e il secret che emette i token della WebUI. Prima si
scriveva il file, lo si rinominava al suo posto e solo dopo si faceva ``chmod``:
per quella finestra il file definitivo aveva i permessi di default.
Qui si guarda il temporaneo **nel momento della rename**.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from jenny.config import store
from jenny.config.bootstrap import ensure_minimal_config
from jenny.config.loader import load_config
from jenny.runtime.context import get_runtime_context
from jenny.utils.path import backup_path as _backup_path


@pytest.fixture
def renames(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    """Il nome di destinazione e i permessi del sorgente a ogni ``os.replace``."""
    seen: list[tuple[str, int]] = []
    real_replace = os.replace

    def spy(src, dst, *a, **k):
        seen.append((Path(dst).name, stat.S_IMODE(os.stat(src).st_mode)))
        return real_replace(src, dst, *a, **k)

    old_umask = os.umask(0o022)  # i permessi di default sarebbero 644
    monkeypatch.setattr(os, "replace", spy)
    yield seen
    os.umask(old_umask)


def _config_renames(seen: list[tuple[str, int]]) -> list[tuple[str, int]]:
    return [(name, mode) for name, mode in seen if name in ("config.json", "config.json.bak")]


async def test_a_mutate_renames_file_and_backup_already_private(tmp_path, renames) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"agents": {"defaults": {"maxTokens": 1}}}), encoding="utf-8")

    def change(cfg) -> None:
        cfg.agents.defaults.max_tokens = 2

    await store.mutate(change, config_path=path)

    written = _config_renames(renames)
    assert {name for name, _ in written} == {"config.json", "config.json.bak"}
    assert all(mode == 0o600 for _, mode in written), written


def test_the_bootstrap_writes_the_new_file_already_private(tmp_path, renames) -> None:
    ensure_minimal_config(tmp_path)

    written = _config_renames(renames)
    assert written == [("config.json", 0o600)]


def test_the_backup_promotion_writes_the_file_already_private(tmp_path, renames) -> None:
    ctx = get_runtime_context()
    path = tmp_path / "config.json"
    path.write_text("{troncato", encoding="utf-8")
    _backup_path(path).write_text(json.dumps({"agents": {}}), encoding="utf-8")
    try:
        load_config(path)
    finally:
        ctx.config_recovered_from = None
        ctx.config_quarantine_path = None

    written = [(n, m) for n, m in _config_renames(renames) if n == "config.json"]
    assert written[-1] == ("config.json", 0o600)


def test_a_filesystem_that_refuses_chmod_still_gets_the_file(tmp_path, monkeypatch) -> None:
    """Best-effort come prima: su FAT il ``chmod`` fallisce, la scrittura no."""
    from jenny.config.bootstrap import write_private_file

    def refuse(*a, **k):
        raise PermissionError("chmod not supported")

    monkeypatch.setattr(os, "chmod", refuse)
    monkeypatch.setattr(Path, "chmod", refuse)
    path = tmp_path / "config.json"

    write_private_file(path, "{}")

    assert path.read_text(encoding="utf-8") == "{}"
