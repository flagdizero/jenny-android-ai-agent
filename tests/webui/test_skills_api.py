"""Test della logica pura di ``jenny.webui.skills_api``.

Copre il parsing/elenco delle skill da una directory workspace su ``tmp_path``,
senza passare per l'handler HTTP (quello è coperto in
``tests/webui/test_skills_routes.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.webui.skills_api import (
    update_workspace_skill,
    webui_skills_payload,
)


def _write_skill(
    skills_dir: Path,
    name: str,
    *,
    description: str | None = "una descrizione",
    body: str = "Corpo della skill.\n",
    extra_frontmatter: str = "",
) -> Path:
    """Crea ``<skills_dir>/<name>/SKILL.md`` con frontmatter opzionale."""
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    if description is None and not extra_frontmatter:
        # Nessun frontmatter: file markdown puro.
        skill_file.write_text(body, encoding="utf-8")
        return skill_file
    lines = ["---"]
    if description is not None:
        lines.append(f'description: "{description}"')
    if extra_frontmatter:
        lines.append(extra_frontmatter)
    lines.append("---")
    skill_file.write_text("\n".join(lines) + "\n" + body, encoding="utf-8")
    return skill_file


@pytest.fixture()
def skills_dir(tmp_path: Path) -> Path:
    d = tmp_path / "skills"
    d.mkdir()
    return d


# -- webui_skills_payload -----------------------------------------------------


def test_payload_lists_workspace_skills_sorted_by_name(tmp_path: Path, skills_dir: Path) -> None:
    _write_skill(skills_dir, "zeta", description="Skill zeta")
    _write_skill(skills_dir, "alpha", description="Skill alpha")

    payload = webui_skills_payload(tmp_path)

    names = [s["name"] for s in payload["skills"]]
    assert names == ["alpha", "zeta"]
    alpha = payload["skills"][0]
    assert alpha["description"] == "Skill alpha"
    assert alpha["source"] == "workspace"
    assert alpha["available"] is True
    assert alpha["disabled"] is False


def test_payload_empty_workspace_returns_empty_list(tmp_path: Path) -> None:
    # skills/ non esiste nemmeno: SkillsLoader deve gestirlo senza esplodere.
    payload = webui_skills_payload(tmp_path)
    assert payload == {"skills": []}


def test_payload_excludes_names_in_disabled_skills_set(tmp_path: Path, skills_dir: Path) -> None:
    _write_skill(skills_dir, "foo")
    _write_skill(skills_dir, "bar")

    payload = webui_skills_payload(tmp_path, disabled_skills={"foo"})

    names = [s["name"] for s in payload["skills"]]
    assert names == ["bar"]


def test_payload_includes_skills_disabled_via_frontmatter(tmp_path: Path, skills_dir: Path) -> None:
    # A differenza del "disabled_skills" set (esclusione totale), il flag
    # "disabled: true" in frontmatter deve comparire nel payload della WebUI
    # (che gestisce l'abilitazione), non sparire dalla lista.
    _write_skill(skills_dir, "off", extra_frontmatter="disabled: true")

    payload = webui_skills_payload(tmp_path)

    assert len(payload["skills"]) == 1
    assert payload["skills"][0]["name"] == "off"
    assert payload["skills"][0]["disabled"] is True


def test_payload_marks_skill_internal_via_frontmatter(tmp_path: Path, skills_dir: Path) -> None:
    _write_skill(skills_dir, "plumbing", extra_frontmatter="internal: true")

    payload = webui_skills_payload(tmp_path)

    assert payload["skills"][0]["internal"] is True


def test_payload_internal_defaults_to_false(tmp_path: Path, skills_dir: Path) -> None:
    _write_skill(skills_dir, "visible")

    payload = webui_skills_payload(tmp_path)

    assert payload["skills"][0]["internal"] is False


def test_payload_marks_skill_locked_with_user_summary_via_frontmatter(
    tmp_path: Path, skills_dir: Path
) -> None:
    _write_skill(
        skills_dir,
        "cron-like",
        extra_frontmatter=(
            "locked: true\n"
            "user_summary:\n"
            "  it: \"Testo italiano.\"\n"
            "  en: \"English text.\"\n"
        ),
    )

    payload = webui_skills_payload(tmp_path)

    entry = payload["skills"][0]
    assert entry["locked"] is True
    assert entry["user_summary"] == {"it": "Testo italiano.", "en": "English text."}


def test_payload_locked_and_user_summary_default_to_false_and_none(
    tmp_path: Path, skills_dir: Path
) -> None:
    _write_skill(skills_dir, "visible")

    payload = webui_skills_payload(tmp_path)

    entry = payload["skills"][0]
    assert entry["locked"] is False
    assert entry["user_summary"] is None


def test_payload_description_falls_back_to_name_without_frontmatter(
    tmp_path: Path, skills_dir: Path
) -> None:
    _write_skill(skills_dir, "plain", description=None, body="Solo testo, niente frontmatter.\n")

    payload = webui_skills_payload(tmp_path)

    assert payload["skills"][0]["description"] == "plain"


def test_payload_description_falls_back_to_name_when_blank(
    tmp_path: Path, skills_dir: Path
) -> None:
    _write_skill(skills_dir, "blank", description="   ")

    payload = webui_skills_payload(tmp_path)

    assert payload["skills"][0]["description"] == "blank"


def test_payload_reports_unavailable_skill_with_reason(tmp_path: Path, skills_dir: Path) -> None:
    extra = (
        "metadata:\n"
        "  jenny:\n"
        "    requires:\n"
        "      bins: [nonexistent_binary_xyz]\n"
    )
    _write_skill(skills_dir, "needs-binary", extra_frontmatter=extra.rstrip("\n"))

    payload = webui_skills_payload(tmp_path)

    entry = payload["skills"][0]
    assert entry["available"] is False
    assert "nonexistent_binary_xyz" in entry["unavailable_reason"]


# -- update_workspace_skill -----------------------------------------------------


def test_update_workspace_skill_updates_description_content_and_disabled(
    tmp_path: Path, skills_dir: Path
) -> None:
    skill_file = _write_skill(skills_dir, "editable", description="vecchia", body="vecchio corpo\n")

    payload = update_workspace_skill(
        tmp_path,
        "editable",
        description="nuova descrizione",
        content="nuovo corpo\n",
        disabled=True,
    )

    assert payload["name"] == "editable"
    assert payload["description"] == "nuova descrizione"
    assert payload["disabled"] is True
    assert payload["source"] == "workspace"

    # Verifica che la modifica sia stata persistita su disco.
    on_disk = skill_file.read_text(encoding="utf-8")
    assert "nuova descrizione" in on_disk
    assert "nuovo corpo" in on_disk


def test_update_workspace_skill_missing_skill_raises_permission_error(tmp_path: Path) -> None:
    # SkillsLoader.is_workspace_skill() usa la stessa condizione di esistenza
    # sia per il controllo "builtin" sia per quello "not found": una skill
    # inesistente risulta quindi non-workspace e solleva PermissionError,
    # non FileNotFoundError (il ramo FileNotFoundError in update_skill pare
    # perciò irraggiungibile con l'implementazione attuale).
    with pytest.raises(PermissionError):
        update_workspace_skill(tmp_path, "does-not-exist", description="x")


# -- le skill che vengono con l'app ---------------------------------------------
#
# Le integrate vivono in ``workspace/skills/`` come quelle dell'utente, e
# ``sync_workspace_templates`` le ri-estrae a ogni avvio sovrascrivendole. Un
# interruttore su di loro varrebbe fino al riavvio: il payload deve dire quali
# sono, e l'API rifiutare di toccarle.


def test_payload_marks_bundled_skills(tmp_path: Path, skills_dir: Path) -> None:
    _write_skill(skills_dir, "cron", description="Promemoria")
    _write_skill(skills_dir, "mia-skill", description="Scritta per me")

    by_name = {s["name"]: s for s in webui_skills_payload(tmp_path)["skills"]}

    assert by_name["cron"]["bundled"] is True
    assert by_name["mia-skill"]["bundled"] is False


def test_update_payload_carries_bundled_too(tmp_path: Path, skills_dir: Path) -> None:
    """Due costruttori dello stesso dizionario: il campo sta in tutti e due."""
    _write_skill(skills_dir, "mia-skill")

    payload = update_workspace_skill(tmp_path, "mia-skill", disabled=True)

    assert payload["bundled"] is False


def test_update_refuses_a_bundled_skill_without_touching_it(
    tmp_path: Path, skills_dir: Path
) -> None:
    skill_file = _write_skill(skills_dir, "cron", description="Promemoria")
    before = skill_file.read_bytes()

    with pytest.raises(PermissionError):
        update_workspace_skill(tmp_path, "cron", disabled=True)

    assert skill_file.read_bytes() == before

