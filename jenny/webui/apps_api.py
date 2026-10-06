"""WebUI payload builders for Jenny Apps (list + typed-action execution).

Transport note: the gateway's HTTP layer (websockets http11) rejects any
non-GET method and any request body at the parser level, so app actions are
executed via ``GET /api/apps/<slug>/actions/<name>?params=<url-encoded JSON>``.
Do NOT "fix" this to POST — it cannot work without replacing the HTTP layer.
The jenny-sdk.js hides the transport from app HTML.
"""

from __future__ import annotations

import json
from pathlib import Path

from jenny.apps.executor import AppActionError, execute_action
from jenny.apps.manifest import scan_apps
from jenny.apps.trust import app_trust_state, list_drafts, load_draft


def list_apps_payload(workspace: Path) -> dict:
    """Payload for GET /api/webui/apps. Never raises."""
    apps = []
    for app in scan_apps(workspace):
        entry: dict = {
            "slug": app.slug,
            "broken": app.broken,
        }
        if app.manifest is not None:
            entry.update(
                {
                    "name": app.manifest.name,
                    "description": app.manifest.description,
                    "icon": app.manifest.icon,
                    "has_server": app.manifest.server_base_url is not None,
                    # La SPA deve saperlo *prima* di aprire: una vista esterna
                    # non incornicia app/index.html, chiede l'URL del proxy e
                    # usa un sandbox diverso (v. openApp in mobile-apps.js).
                    "view_kind": app.manifest.view_kind,
                    # La vista compatta per un cassetto della bacheca, se l'app
                    # ne dichiara una: il menu «Aggiungi sezione» propone solo
                    # queste, e chiede i parametri che la sezione vuole.
                    "section": (
                        {"params": app.manifest.section.params}
                        if app.manifest.section is not None
                        else None
                    ),
                }
            )
        else:
            entry.update(
                {
                    "name": app.slug,
                    "description": "",
                    "icon": "ti-alert-triangle",
                    "has_server": False,
                    "view_kind": None,
                }
            )
        if app.broken:
            entry["error"] = app.error
        entry.update(app_trust_state(workspace, app.slug))
        apps.append(entry)
    # Le app nuove ancora in prova: nella griglia col cartellino «in prova», e
    # dal loro foglio si tengono o si scartano. Niente tool, niente schermo.
    live = {a["slug"] for a in apps}
    for slug in list_drafts(workspace):
        if slug in live:
            continue
        draft = load_draft(workspace, slug)
        if draft is None:
            continue
        m = draft.app.manifest
        apps.append({
            "slug": slug, "broken": draft.app.broken,
            "name": m.name if m else slug, "description": m.description if m else "",
            "icon": m.icon if m else "ti-apps", "has_server": bool(m and m.server_base_url),
            "view_kind": m.view_kind if m else None, "section": None,
            **({"error": draft.app.error} if draft.app.broken else {}),
            **app_trust_state(workspace, slug),
        })
    return {"apps": apps}


def delete_app(workspace: Path, slug: str) -> dict:
    """«Elimina» un'app: la sposta nel cestino (``apps/.trash/``), dati compresi.

    Fino al 06/10/2026 era ``rmtree``, senza ritorno. Adesso si rimette con
    ``apps.restore`` e si butta davvero solo con ``apps.purge`` (v.
    :mod:`jenny.apps.trash`). ``ValueError`` per uno slug non valido,
    ``FileNotFoundError`` se l'app non c'e'.
    """
    from jenny.apps.trash import trash_app

    trashed = trash_app(workspace, slug)
    return {"deleted": True, "trashed": trashed}


async def execute_app_action_payload(
    workspace: Path,
    slug: str,
    action_name: str,
    raw_params_json: str,
    *,
    http_timeout_s: float,
    max_collection_bytes: int,
) -> tuple[dict, int]:
    """Execute one action; returns (payload, http_status).

    Errors come back as ``{"ok": False, "error": ...}`` payloads with the
    matching status so the sandboxed iframe can always read a structured body.
    """
    if raw_params_json:
        try:
            params = json.loads(raw_params_json)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"params is not valid JSON: {exc}"}, 400
        if not isinstance(params, dict):
            return {"ok": False, "error": "params must be a JSON object"}, 400
    else:
        params = {}

    try:
        result = await execute_action(
            workspace,
            slug,
            action_name,
            params,
            http_timeout_s=http_timeout_s,
            max_collection_bytes=max_collection_bytes,
        )
    except AppActionError as exc:
        return {"ok": False, "error": str(exc)}, exc.status
    return result, 200
