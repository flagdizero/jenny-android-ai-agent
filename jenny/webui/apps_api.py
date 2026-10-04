"""WebUI payload builders for Jenny Apps (list + typed-action execution).

Transport note: the gateway's HTTP layer (websockets http11) rejects any
non-GET method and any request body at the parser level, so app actions are
executed via ``GET /api/apps/<slug>/actions/<name>?params=<url-encoded JSON>``.
Do NOT "fix" this to POST — it cannot work without replacing the HTTP layer.
The jenny-sdk.js hides the transport from app HTML.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from jenny.apps.executor import AppActionError, execute_action
from jenny.apps.manifest import SLUG_RE, scan_apps


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
        apps.append(entry)
    return {"apps": apps}


def delete_app(workspace: Path, slug: str) -> dict:
    """Delete a Jenny app by removing its ``<workspace>/apps/<slug>/`` folder.

    Uninstalling a Jenny app permanently deletes it (unlike Android apps, which
    only open the system uninstaller). Raises ``ValueError`` on an invalid slug
    and ``FileNotFoundError`` when the app folder does not exist.
    """
    if not slug or SLUG_RE.match(slug) is None:
        raise ValueError("invalid app slug")
    apps_root = (Path(workspace) / "apps").resolve()
    app_dir = (apps_root / slug).resolve()
    # Defence-in-depth: the validated slug cannot escape, but confirm anyway.
    if app_dir.parent != apps_root:
        raise ValueError("invalid app slug")
    if not app_dir.is_dir():
        raise FileNotFoundError(f"app '{slug}' not found")
    shutil.rmtree(app_dir)
    return {"deleted": True}


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
