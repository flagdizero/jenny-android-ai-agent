"""Tests for jenny.agent.skills.SkillsLoader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jenny.agent.skills import SkillsLoader


def _write_skill(
    base: Path,
    name: str,
    *,
    metadata_json: dict | None = None,
    body: str = "# Skill\n",
) -> Path:
    """Create ``base / name / SKILL.md`` with optional jenny metadata JSON."""
    skill_dir = base / name
    skill_dir.mkdir(parents=True)
    lines = ["---"]
    if metadata_json is not None:
        payload = json.dumps({"jenny": metadata_json}, separators=(",", ":"))
        lines.append(f'metadata: {payload}')
    lines.extend(["---", "", body])
    path = skill_dir / "SKILL.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_list_skills_empty_when_skills_dir_missing(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    loader = SkillsLoader(workspace)
    assert loader.list_skills(filter_unavailable=False) == []


def test_list_skills_empty_when_skills_dir_exists_but_empty(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "skills").mkdir(parents=True)
    loader = SkillsLoader(workspace)
    assert loader.list_skills(filter_unavailable=False) == []


def test_list_skills_workspace_entry_shape_and_source(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    skills_root = workspace / "skills"
    skills_root.mkdir(parents=True)
    skill_path = _write_skill(skills_root, "alpha", body="# Alpha")
    loader = SkillsLoader(workspace)
    entries = loader.list_skills(filter_unavailable=False)
    assert entries == [
        {"name": "alpha", "path": str(skill_path), "source": "workspace", "disabled": False},
    ]


def test_list_skills_skips_non_directories_and_missing_skill_md(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    skills_root = workspace / "skills"
    skills_root.mkdir(parents=True)
    (skills_root / "not_a_dir.txt").write_text("x", encoding="utf-8")
    (skills_root / "no_skill_md").mkdir()
    ok_path = _write_skill(skills_root, "ok", body="# Ok")
    loader = SkillsLoader(workspace)
    entries = loader.list_skills(filter_unavailable=False)
    names = {entry["name"] for entry in entries}
    assert names == {"ok"}
    assert entries[0]["path"] == str(ok_path)








def test_list_skills_filter_unavailable_excludes_unmet_bin_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    skills_root = workspace / "skills"
    skills_root.mkdir(parents=True)
    _write_skill(
        skills_root,
        "needs_bin",
        metadata_json={"requires": {"bins": ["jenny_test_fake_binary"]}},
    )
    loader = SkillsLoader(workspace)
    assert loader.list_skills(filter_unavailable=True) == []


def test_list_skills_filter_unavailable_includes_when_bin_requirement_met(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    skills_root = workspace / "skills"
    skills_root.mkdir(parents=True)
    skill_path = _write_skill(
        skills_root,
        "has_bin",
        metadata_json={"requires": {"bins": ["python3"]}},
    )

    loader = SkillsLoader(workspace)
    entries = loader.list_skills(filter_unavailable=True)
    assert entries == [
        {"name": "has_bin", "path": str(skill_path), "source": "workspace", "disabled": False},
    ]


def test_android_skill_availability_excludes_desktop_bins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Android only python/python3 are expected to be available."""
    workspace = tmp_path / "ws"
    skills_root = workspace / "skills"
    skills_root.mkdir(parents=True)
    _write_skill(
        skills_root,
        "needs_desktop",
        metadata_json={"requires": {"bins": ["docker", "npx", "uvx", "node"]}},
    )
    python_skill_path = _write_skill(
        skills_root,
        "has_python",
        metadata_json={"requires": {"bins": ["python3"]}},
    )

    loader = SkillsLoader(workspace)
    entries = loader.list_skills(filter_unavailable=True)
    names = {entry["name"] for entry in entries}
    assert "needs_desktop" not in names
    assert "has_python" in names
    assert entries == [
        {"name": "has_python", "path": str(python_skill_path), "source": "workspace", "disabled": False},
    ]


def test_list_skills_filter_unavailable_false_keeps_unmet_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    skills_root = workspace / "skills"
    skills_root.mkdir(parents=True)
    skill_path = _write_skill(
        skills_root,
        "blocked",
        metadata_json={"requires": {"bins": ["jenny_test_fake_binary"]}},
    )

    loader = SkillsLoader(workspace)
    entries = loader.list_skills(filter_unavailable=False)
    assert entries == [
        {"name": "blocked", "path": str(skill_path), "source": "workspace", "disabled": False},
    ]


def test_list_skills_filter_unavailable_excludes_unmet_env_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    skills_root = workspace / "skills"
    skills_root.mkdir(parents=True)
    _write_skill(
        skills_root,
        "needs_env",
        metadata_json={"requires": {"env": ["JENNY_SKILLS_TEST_ENV_VAR"]}},
    )
    monkeypatch.delenv("JENNY_SKILLS_TEST_ENV_VAR", raising=False)

    loader = SkillsLoader(workspace)
    assert loader.list_skills(filter_unavailable=True) == []


def test_list_skills_openclaw_metadata_parsed_for_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    skills_root = workspace / "skills"
    skills_root.mkdir(parents=True)
    skill_dir = skills_root / "openclaw_skill"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    oc_payload = json.dumps({"jenny": {"requires": {"bins": ["jenny_oc_bin"]}}}, separators=(",", ":"))
    skill_path.write_text(
        "\n".join(["---", f"metadata: {oc_payload}", "---", "", "# OC"]),
        encoding="utf-8",
    )

    loader = SkillsLoader(workspace)
    assert loader.list_skills(filter_unavailable=True) == []

    available_payload = json.dumps(
        {"jenny": {"requires": {"bins": ["python3"]}}, "always": True},
        separators=(",", ":"),
    )
    skill_path.write_text(
        "\n".join(["---", f"metadata: {available_payload}", "---", "", "# OC"]),
        encoding="utf-8",
    )
    entries = loader.list_skills(filter_unavailable=True)
    assert entries == [
        {"name": "openclaw_skill", "path": str(skill_path), "source": "workspace", "disabled": False},
    ]


def test_disabled_skills_excluded_from_list(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    _write_skill(ws_skills, "alpha", body="# Alpha")
    beta_path = _write_skill(ws_skills, "beta", body="# Beta")
    loader = SkillsLoader(workspace, disabled_skills={"alpha"})
    entries = loader.list_skills(filter_unavailable=False)
    assert len(entries) == 1
    assert entries[0]["name"] == "beta"
    assert entries[0]["path"] == str(beta_path)


def _write_disabled_skill(base: Path, name: str, *, body: str = "# Skill\n") -> Path:
    """Write a skill with `disabled: true` at the top level of its frontmatter,
    matching the shape SkillsLoader.update_skill() actually persists (a
    top-level key, not nested under `metadata.jenny`)."""
    skill_dir = base / name
    skill_dir.mkdir(parents=True)
    path = skill_dir / "SKILL.md"
    path.write_text(f"---\ndisabled: true\n---\n\n{body}", encoding="utf-8")
    return path


def test_frontmatter_disabled_skill_excluded_by_default(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    _write_skill(ws_skills, "alpha", body="# Alpha")
    _write_disabled_skill(ws_skills, "beta", body="# Beta")
    loader = SkillsLoader(workspace)
    entries = loader.list_skills(filter_unavailable=False)
    assert [e["name"] for e in entries] == ["alpha"]


def test_frontmatter_disabled_skill_included_when_requested(tmp_path: Path) -> None:
    """Management UIs (e.g. /api/webui/skills) need to see disabled skills so
    they can show them as toggle-able rather than have them vanish entirely."""
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    _write_skill(ws_skills, "alpha", body="# Alpha")
    _write_disabled_skill(ws_skills, "beta", body="# Beta")
    loader = SkillsLoader(workspace)
    entries = loader.list_skills(filter_unavailable=False, include_disabled=True)
    by_name = {e["name"]: e for e in entries}
    assert set(by_name) == {"alpha", "beta"}
    assert by_name["alpha"]["disabled"] is False
    assert by_name["beta"]["disabled"] is True


def test_disabled_skills_empty_set_no_effect(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    _write_skill(ws_skills, "alpha", body="# Alpha")
    _write_skill(ws_skills, "beta", body="# Beta")
    loader = SkillsLoader(workspace, disabled_skills=set())
    entries = loader.list_skills(filter_unavailable=False)
    assert len(entries) == 2


def test_disabled_skills_excluded_from_build_skills_summary(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    _write_skill(ws_skills, "alpha", body="# Alpha")
    _write_skill(ws_skills, "beta", body="# Beta")
    loader = SkillsLoader(workspace, disabled_skills={"alpha"})
    summary = loader.build_skills_summary()
    assert "alpha" not in summary
    assert "beta" in summary


def test_disabled_skills_excluded_from_get_always_skills(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    _write_skill(ws_skills, "alpha", metadata_json={"always": True}, body="# Alpha")
    _write_skill(ws_skills, "beta", metadata_json={"always": True}, body="# Beta")
    loader = SkillsLoader(workspace, disabled_skills={"alpha"})
    always = loader.get_always_skills()
    assert "alpha" not in always
    assert "beta" in always


# -- multiline description tests (YAML folded > and literal |) -----------------


def test_build_skills_summary_folded_description(tmp_path: Path) -> None:
    """description: > (YAML folded scalar) should be parsed correctly."""
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    skill_dir = ws_skills / "pdf"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\n"
        "name: pdf\n"
        "description: >\n"
        "  Use this skill when visual quality and design identity matter for a PDF.\n"
        "  CREATE (generate from scratch): \"make a PDF\".\n"
        "---\n\n# PDF Skill\n",
        encoding="utf-8",
    )
    loader = SkillsLoader(workspace)
    summary = loader.build_skills_summary()
    assert "pdf" in summary
    assert "visual quality" in summary


def test_build_skills_summary_literal_description(tmp_path: Path) -> None:
    """description: | (YAML literal scalar) should be parsed correctly."""
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    skill_dir = ws_skills / "multi"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\n"
        "name: multi\n"
        "description: |\n"
        "  Line one of description.\n"
        "  Line two of description.\n"
        "---\n\n# Multi\n",
        encoding="utf-8",
    )
    loader = SkillsLoader(workspace)
    meta = loader.get_skill_metadata("multi")
    assert meta is not None
    desc = meta.get("description")
    assert isinstance(desc, str)
    assert "Line one" in desc
    assert "Line two" in desc


def test_get_skill_metadata_handles_yaml_types(tmp_path: Path) -> None:
    """yaml.safe_load returns native types; always should be True, not 'true'."""
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    skill_dir = ws_skills / "typed"
    skill_dir.mkdir(parents=True)
    payload = json.dumps({"jenny": {"requires": {"bins": ["gh"]}, "always": True}}, separators=(",", ":"))
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\n"
        "name: typed\n"
        f"metadata: {payload}\n"
        "always: true\n"
        "---\n\n# Typed\n",
        encoding="utf-8",
    )
    loader = SkillsLoader(workspace)
    meta = loader.get_skill_metadata("typed")
    assert meta is not None
    # YAML parsed 'true' to Python True
    assert meta.get("always") is True
    # metadata is a parsed dict, not a JSON string
    assert isinstance(meta.get("metadata"), dict)


def test_update_skill_failed_write_leaves_the_skill_intact(tmp_path: Path) -> None:
    """update_skill riscrive SKILL.md intero: deve farlo atomicamente.

    Con un write_text nudo, un processo ucciso a metà lasciava un frontmatter
    troncato — cioè una skill che non si carica più. Il raise finto dimostra sia
    che l'helper atomico è sulla strada, sia che la versione precedente resta.
    """
    workspace = tmp_path / "ws"
    ws_skills = workspace / "skills"
    ws_skills.mkdir(parents=True)
    skill_path = _write_skill(ws_skills, "alpha", body="# Alpha original")
    before = skill_path.read_text(encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise OSError("no space left on device")

    loader = SkillsLoader(workspace)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("jenny.agent.skills.atomic_write", boom)
        with pytest.raises(OSError):
            loader.update_skill("alpha", description="new description")

    assert skill_path.read_text(encoding="utf-8") == before
    assert loader.load_skill("alpha") is not None
