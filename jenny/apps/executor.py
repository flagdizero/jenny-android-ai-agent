"""Single entry point to execute a Jenny App action.

Shared by the WebUI actions route and the agent-facing AppActionTool: both
resolve the app, validate params against the manifest's JSON Schema, and
dispatch to the storage or http executor.
"""

from __future__ import annotations

from pathlib import Path

from jenny.agent.tools.base import Schema
from jenny.apps.http import (
    DEFAULT_TIMEOUT_S,
    HttpActionError,
    execute_http_action,
)
from jenny.apps.manifest import (
    AppAction,
    LoadedApp,
    action_param_schema,
    find_app,
)
from jenny.apps.storage import (
    DEFAULT_MAX_COLLECTION_BYTES,
    StorageError,
    execute_storage_action,
)


class AppActionError(Exception):
    """Structured action failure; ``status`` maps to an HTTP-ish code."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def resolve_action(app: LoadedApp, action_name: str) -> AppAction:
    """Find one action in a loaded app; raises AppActionError."""
    if app.broken or app.manifest is None:
        raise AppActionError(f"app '{app.slug}' is broken: {app.error}", status=409)
    for action in app.manifest.actions:
        if action.name == action_name:
            return action
    raise AppActionError(f"app '{app.slug}' has no action '{action_name}'", status=404)


def validate_action_params(action: AppAction, params: dict) -> None:
    """Validate params against the action's JSON Schema; raises AppActionError."""
    if not isinstance(params, dict):
        raise AppActionError("params must be a JSON object")
    schema = action_param_schema(action)
    errors = Schema.validate_json_schema_value(params, schema)
    # The shared validator ignores additionalProperties; storage params become
    # record fields (append/set/update) so undeclared keys must not slip through.
    unknown = sorted(set(params) - set(schema["properties"]))
    if unknown:
        errors = list(errors) + [f"unknown params: {', '.join(unknown)}"]
    if errors:
        raise AppActionError("invalid params: " + "; ".join(errors))


async def execute_action(
    workspace: Path,
    slug: str,
    action_name: str,
    params: dict,
    *,
    http_timeout_s: float = DEFAULT_TIMEOUT_S,
    max_collection_bytes: int = DEFAULT_MAX_COLLECTION_BYTES,
    announce: bool = True,
) -> dict:
    """Execute one action of one app; returns a JSON-safe result dict.

    With *announce* a storage write tells the WebUI (``jenny/apps/notify.py``);
    the app's own tool passes ``False`` because it announces by itself.

    Raises AppActionError with a readable message and an HTTP-ish status on
    any failure. Never half-executes: validation happens before any write.
    """
    app = find_app(workspace, slug)
    if app is None:
        raise AppActionError(f"app '{slug}' not found", status=404)
    action = resolve_action(app, action_name)
    validate_action_params(action, params)

    assert app.manifest is not None
    try:
        if action.kind == "storage":
            result = await execute_storage_action(
                app.dir, action, params, max_bytes=max_collection_bytes
            )
            if action.op != "query" and app.manifest.reader is not None:
                # Un lettore che aggrega (il totale dalle operazioni) ricalcola
                # dopo la scrittura: il riassunto e' sempre quello dei dati, e
                # l'avviso alla WebUI parte dopo il ricalcolo (``run_reader``).
                from jenny.apps.reader import schedule_reader

                schedule_reader(workspace, slug)
            elif action.op != "query" and announce:
                # Senza lettore il riassunto si legge dai dati: la polaroid e lo
                # schermo aperto possono rileggersi subito.
                from jenny.apps.notify import data_changed

                await data_changed(slug)
            return result
        if (action.method or "GET").upper() != "GET":
            # Un'azione http che cambia qualcosa (annaffia, registra) segue la
            # stessa regola della posa delle azioni storage.
            from jenny.apps.permissions import write_refusal

            refusal = write_refusal(app.dir)
            if refusal is not None:
                raise AppActionError(refusal, status=403)
        return await execute_http_action(
            slug, app.manifest, action, params, timeout_s=http_timeout_s
        )
    except (StorageError, HttpActionError) as exc:
        raise AppActionError(str(exc), status=exc.status) from exc
