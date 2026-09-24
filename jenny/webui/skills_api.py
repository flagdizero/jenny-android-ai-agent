"""Lightweight skill summaries for the WebUI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jenny.agent.skills import SkillsLoader
from jenny.utils.android_assets import bundled_skill_names


def webui_skills_payload(
    workspace_path: Path,
    *,
    disabled_skills: set[str] | None = None,
) -> dict[str, Any]:
    """Return agent skills without leaking local filesystem paths."""
    loader = SkillsLoader(workspace_path, disabled_skills=disabled_skills)
    entries = sorted(
        loader.list_skills(filter_unavailable=False, include_disabled=True),
        key=lambda entry: (entry.get("source") != "workspace", entry["name"]),
    )
    return {"skills": [_skill_payload(loader, entry) for entry in entries]}


def update_workspace_skill(
    workspace_path: Path,
    name: str,
    *,
    description: str | None = None,
    content: str | None = None,
    disabled: bool | None = None,
) -> dict[str, Any]:
    """Update a workspace skill and return its payload."""
    _refuse_bundled(name)
    loader = SkillsLoader(workspace_path)
    loader.update_skill(name, description=description, content=content, disabled=disabled)
    return _skill_payload_for(loader, name, source="workspace")


def delete_workspace_skill(workspace_path: Path, name: str) -> None:
    """Delete a workspace skill."""
    _refuse_bundled(name)
    loader = SkillsLoader(workspace_path)
    loader.delete_skill(name)


def _refuse_bundled(name: str) -> None:
    """Una skill che viene con l'app non si modifica né si cancella da qui.

    ``sync_workspace_templates`` la ri-estrae a ogni avvio sovrascrivendola:
    un ``disabled: true`` nel suo frontmatter varrebbe fino al riavvio e poi
    sparirebbe senza dirlo, e una cancellata tornerebbe. Meglio un rifiuto che
    un'impostazione che mente.
    """
    if name in bundled_skill_names():
        raise PermissionError("bundled skill: re-extracted at startup")


def _skill_payload_for(loader: SkillsLoader, name: str, *, source: str = "workspace") -> dict[str, Any]:
    """Build payload for a single skill by name."""
    metadata = loader.get_skill_metadata(name)
    available, unavailable_reason = loader.get_skill_availability(name)
    return {
        "name": name,
        "description": _description(metadata, name),
        "source": source,
        "available": available,
        "unavailable_reason": unavailable_reason,
        "disabled": bool(metadata and metadata.get("disabled")),
        "bundled": name in bundled_skill_names(),
        **_visibility_fields(metadata),
    }


def _skill_payload(loader: SkillsLoader, entry: dict[str, str]) -> dict[str, Any]:
    name = entry["name"]
    metadata = loader.get_skill_metadata(name)
    available, unavailable_reason = loader.get_skill_availability(name)
    return {
        "name": name,
        "description": _description(metadata, name),
        "source": entry.get("source", "unknown"),
        "available": available,
        "unavailable_reason": unavailable_reason,
        "disabled": bool(metadata and metadata.get("disabled")),
        "bundled": name in bundled_skill_names(),
        **_visibility_fields(metadata),
    }


def _visibility_fields(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Campi che governano la visibilità/gestibilità nella WebUI (non l'uso da parte dell'agente).

    ``internal``: nascosta dalla lista salvo Modalità avanzata (skill bundle di puro
    "plumbing", es. self-awareness, bookkeeping di sessione).
    ``locked`` + ``user_summary``: visibile in lista ma senza azioni di gestione
    (modifica/disabilita/elimina) fuori dalla Modalità avanzata — il tap mostra
    ``user_summary`` invece dell'editor.
    """
    user_summary = metadata.get("user_summary") if metadata else None
    return {
        "internal": bool(metadata and metadata.get("internal")),
        "locked": bool(metadata and metadata.get("locked")),
        "user_summary": user_summary if isinstance(user_summary, dict) else None,
    }


def _description(metadata: dict[str, Any] | None, fallback: str) -> str:
    if metadata is None:
        return fallback
    value = metadata.get("description")
    return value.strip() if isinstance(value, str) and value.strip() else fallback
