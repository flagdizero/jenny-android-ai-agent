"""Le skill nostre che il package non spedisce piu' escono da ``workspace/skills``.

Il caso che ha aperto la regola: il goal sostenuto, tolto il 01/11/2026.
``extract_package_dir`` scrive e non cancella, quindi ``skills/long-goal/SKILL.md``
restava sul telefono, e il caricatore delle skill (che legge solo da li') la dava
ancora al modello: una skill che dice di chiamare un tool che non esiste. Ma
``skills/`` e' anche dell'utente, quindi si toglie solo una copia identica a una
versione spedita.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jenny.utils import android_assets
from jenny.utils.android_assets import (
    _RETIRED_SKILL_DIGESTS,
    _SKILLS_MANIFEST,
    retire_withdrawn_skills,
    template_digest,
)

_SHIPPED = "---\nname: old-skill\n---\n\n# Old skill\n\nCall `old_tool`.\n"


@pytest.fixture
def retired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        android_assets, "_RETIRED_SKILL_DIGESTS",
        {"old-skill/SKILL.md": {template_digest(_SHIPPED): "v1.0.0 (abc1234)"}},
    )


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_copy_as_we_shipped_it_leaves_with_its_folder(tmp_path: Path, retired) -> None:
    skill = _write(tmp_path / "old-skill" / "SKILL.md", _SHIPPED)

    assert retire_withdrawn_skills(tmp_path) == ["old-skill/SKILL.md"]
    assert not skill.exists()
    assert not skill.parent.exists()


def test_a_copy_the_user_changed_stays(tmp_path: Path, retired) -> None:
    skill = _write(tmp_path / "old-skill" / "SKILL.md", _SHIPPED + "\nMy own note.\n")

    assert retire_withdrawn_skills(tmp_path) == []
    assert skill.exists()


def test_a_file_of_the_user_beside_it_keeps_the_folder(tmp_path: Path, retired) -> None:
    _write(tmp_path / "old-skill" / "SKILL.md", _SHIPPED)
    mine = _write(tmp_path / "old-skill" / "notes.md", "mine")

    assert retire_withdrawn_skills(tmp_path) == ["old-skill/SKILL.md"]
    assert mine.exists()


def test_a_symlink_is_not_followed(tmp_path: Path, retired) -> None:
    real = _write(tmp_path / "elsewhere" / "SKILL.md", _SHIPPED)
    (tmp_path / "old-skill").mkdir()
    os.symlink(real, tmp_path / "old-skill" / "SKILL.md")

    assert retire_withdrawn_skills(tmp_path) == []
    assert real.exists()


def test_nothing_there_is_nothing_to_do(tmp_path: Path, retired) -> None:
    assert retire_withdrawn_skills(tmp_path) == []


def test_a_retired_skill_is_never_one_we_still_ship() -> None:
    """Una skill ancora nel manifest si riscriverebbe e si cancellerebbe a ogni avvio."""
    assert not set(_RETIRED_SKILL_DIGESTS) & set(_SKILLS_MANIFEST)


def test_the_sync_retires_it(tmp_path: Path, retired) -> None:
    from jenny.utils.helpers import sync_workspace_templates

    skill = _write(tmp_path / "skills" / "old-skill" / "SKILL.md", _SHIPPED)

    sync_workspace_templates(tmp_path, silent=True)

    assert not skill.exists()
