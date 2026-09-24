"""Guard sui manifest statici di templates e skills (packaging Android).

Gotcha noto del progetto: un file bundlato ma assente dal manifest non viene
mai estratto sul device e sparisce in silenzio (la SPA maschera i 404). Il
manifest UI ha già i suoi guard in ``tests/webui/test_ui_manifest.py``; qui
si proteggono ``_TEMPLATES_MANIFEST`` e ``_SKILLS_MANIFEST`` in entrambe le
direzioni: ogni voce esiste su disco, ogni file su disco è nel manifest.
"""

from __future__ import annotations

from pathlib import Path

from jenny.utils.android_assets import (
    _SKILLS_MANIFEST,
    _TEMPLATES_MANIFEST,
    bundled_skill_names,
)

_JENNY_DIR = Path(__file__).resolve().parents[2] / "jenny"
TEMPLATES_DIR = _JENNY_DIR / "templates"
SKILLS_DIR = _JENNY_DIR / "skills"

# File presenti nel package ma volutamente NON estratti sul device.
_SKILLS_EXCLUDED_NAMES = {"__init__.py", "README.md"}


def _templates_on_disk() -> set[str]:
    return {
        p.relative_to(TEMPLATES_DIR).as_posix()
        for p in TEMPLATES_DIR.rglob("*.md")
        if "ui" not in p.relative_to(TEMPLATES_DIR).parts[:1]
    }


def _skills_on_disk() -> set[str]:
    files: set[str] = set()
    for pattern in ("*.md", "*.py"):
        for p in SKILLS_DIR.rglob(pattern):
            rel = p.relative_to(SKILLS_DIR)
            if p.name in _SKILLS_EXCLUDED_NAMES or "tests" in rel.parts:
                continue
            files.add(rel.as_posix())
    return files


def test_templates_manifest_entries_exist_on_disk() -> None:
    missing = [entry for entry in _TEMPLATES_MANIFEST if not (TEMPLATES_DIR / entry).is_file()]
    assert not missing, f"voci di _TEMPLATES_MANIFEST senza file su disco: {missing}"


def test_templates_on_disk_are_in_manifest() -> None:
    unlisted = sorted(_templates_on_disk() - set(_TEMPLATES_MANIFEST))
    assert not unlisted, (
        f"template su disco assenti da _TEMPLATES_MANIFEST "
        f"(non arriverebbero mai sul device): {unlisted}"
    )


def test_skills_manifest_entries_exist_on_disk() -> None:
    missing = [entry for entry in _SKILLS_MANIFEST if not (SKILLS_DIR / entry).is_file()]
    assert not missing, f"voci di _SKILLS_MANIFEST senza file su disco: {missing}"


def test_skills_on_disk_are_in_manifest() -> None:
    unlisted = sorted(_skills_on_disk() - set(_SKILLS_MANIFEST))
    assert not unlisted, (
        f"file skill su disco assenti da _SKILLS_MANIFEST "
        f"(non arriverebbero mai sul device): {unlisted}"
    )


def test_bundled_skill_names_are_the_skill_folders_that_ship() -> None:
    """Chi decide se una skill si puo' spegnere chiede qui: una cartella in piu'
    o in meno e' un interruttore che mente, in un verso o nell'altro."""
    on_disk = {p.parent.name for p in SKILLS_DIR.glob("*/SKILL.md")}
    assert bundled_skill_names() == on_disk


def test_manifests_have_no_duplicates() -> None:
    for name, manifest in (
        ("_TEMPLATES_MANIFEST", _TEMPLATES_MANIFEST),
        ("_SKILLS_MANIFEST", _SKILLS_MANIFEST),
    ):
        duplicates = sorted({e for e in manifest if manifest.count(e) > 1})
        assert not duplicates, f"voci duplicate in {name}: {duplicates}"


def test_write_bytes_force_overwrites_readonly_mirror(tmp_path: Path) -> None:
    """La sync del mirror non deve crashare su un file reso read-only al boot."""
    from jenny.utils.android_assets import _write_bytes_force

    target = tmp_path / "index.html"
    target.write_bytes(b"old")
    target.chmod(0o444)  # simula il mirror read-only che crash-loopava il gateway

    _write_bytes_force(target, b"new")

    assert target.read_bytes() == b"new"
