"""Jenny App actions, reached by the agent through one tool: ``use_app``.

Each valid action in ``<workspace>/apps/<slug>/app.json`` is callable as
``use_app(app=<slug>, action=<action>, args={...})``. The prompt lists every
app's actions as short signatures (``apps/summary.py``); ``use_app`` with only
``app`` returns the full parameters.

Fino al 03/11/2026 ogni azione era un tool nativo ``<slug>_<action>``, con la
sua descrizione completa in ogni chiamata: sul telefono di prova 32 tool su 56,
~12 mila caratteri, che crescevano con ogni app installata, usata o no. E nella
chat di un quaderno la lista cambiava quando si posava un'app, cioe' la cache
del provider si perdeva (misura nel piano della migrazione, tappa 7). Ora la
lista dei tool resta la stessa: c'e' un tool solo, e i permessi (la posa, la
sola lettura) li controllano esecutore e storage, come prima.

``AppToolsSyncer`` diffs the apps folder on every turn: ``use_app`` exists
while at least one app has an action, so a user without apps pays nothing.
It is not in the loader's ``TOOLS`` lists: the syncer owns its lifecycle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.agent.tools.base import Tool
from jenny.agent.tools.result import ToolResult
from jenny.apps.executor import AppActionError, execute_action
from jenny.apps.manifest import (
    AppAction,
    LoadedApp,
    action_param_schema,
    action_signature,
    scan_apps,
)

USE_APP_TOOL = "use_app"


def _apps_runtime_config() -> tuple[bool, float, int]:
    """(enabled, http_timeout_s, max_collection_bytes) — lazy, safe defaults."""
    try:
        from jenny.config.loader import load_config

        apps = load_config().apps
        return apps.enabled, apps.http_timeout_s, apps.max_collection_bytes
    except Exception:
        return True, 20.0, 5_000_000


def _is_write(action: AppAction) -> bool:
    return not (action.op == "query" or (action.method or "").upper() == "GET")


class UseAppTool(Tool):
    """Every action of every Jenny App, behind one tool."""

    def __init__(self, workspace: Path, syncer: AppToolsSyncer) -> None:
        self._workspace = Path(workspace)
        self._syncer = syncer

    @property
    def name(self) -> str:
        return USE_APP_TOOL

    @property
    def description(self) -> str:
        return (
            "Use one of the user's Jenny Apps: run one of its actions. The Jenny Apps section "
            "lists each app's actions as `action(param, optional?)`. Pass `app` (the slug), "
            "`action` and `args` (the parameters, as an object). With only `app` you get its "
            "actions and their full parameters. App data changes only through this tool, never "
            "by editing files under apps/. This is not the `app` tool: `app` writes, fixes and "
            "places an app's code; `use_app` uses the app."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "The app's slug."},
                "action": {
                    "type": "string",
                    "description": "The action to run. Omit it to list the app's actions.",
                },
                "args": {
                    "type": "object",
                    "description": "The action's parameters.",
                    "additionalProperties": True,
                },
            },
            "required": ["app"],
        }

    def _app(self, slug: str) -> LoadedApp | None:
        return self._syncer.apps().get(slug)

    def _apps_list(self) -> str:
        names = sorted(s for s, a in self._syncer.apps().items() if a.manifest and a.manifest.actions)
        return ", ".join(f"`{s}`" for s in names) or "none"

    def _describe(self, app: LoadedApp) -> str:
        assert app.manifest is not None
        lines = [f"App {app.manifest.name} (`{app.slug}`): {app.manifest.description}"]
        for action in app.manifest.actions:
            kind = "changes data" if _is_write(action) else "reads"
            lines.append(f"\n- `{action_signature(action)}` ({kind}): {action.description}")
            lines.append("  parameters: " + json.dumps(
                action_param_schema(action)["properties"], ensure_ascii=False))
        if (app.dir / "AGENT.md").is_file():
            lines.append(f"\nBefore working with it, read `apps/{app.slug}/AGENT.md`.")
        return "\n".join(lines)

    async def execute(self, app: str = "", action: str | None = None,
                      args: Any = None, **_extra: Any) -> ToolResult:
        loaded = self._app(app)
        if loaded is None or loaded.manifest is None or not loaded.manifest.actions:
            return ToolResult.failure(
                f"No app `{app}` with actions. Apps you can use: {self._apps_list()}.",
                code="app_action")
        if not action:
            return ToolResult.success(self._describe(loaded))
        match = next((a for a in loaded.manifest.actions if a.name == action), None)
        if match is None:
            known = ", ".join(f"`{action_signature(a)}`" for a in loaded.manifest.actions)
            return ToolResult.failure(
                f"`{app}` has no action `{action}`. Its actions: {known}.", code="app_action")
        if isinstance(args, str):
            # Un modello che manda gli argomenti come testo JSON: si legge, invece di
            # rispondere con un errore che lo costringe a rifare la chiamata.
            try:
                args = json.loads(args) if args.strip() else {}
            except ValueError:
                return ToolResult.failure("`args` must be an object.", code="app_action")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return ToolResult.failure("`args` must be an object.", code="app_action")
        _, http_timeout_s, max_collection_bytes = _apps_runtime_config()
        try:
            result = await execute_action(
                self._workspace, app, action, args,
                http_timeout_s=http_timeout_s, max_collection_bytes=max_collection_bytes,
            )
        except AppActionError as exc:
            message = str(exc)
            if message.startswith("invalid params"):
                message += (f". Expected `{action_signature(match)}`, parameters: "
                            + json.dumps(action_param_schema(match)["properties"],
                                         ensure_ascii=False))
            return ToolResult.failure(message, code="app_action")
        # L'avviso alla WebUI lo manda ``execute_action`` (``apps.notify``), come
        # per una scrittura dallo schermo: da qualunque canale venga il turno, e
        # dopo il ricalcolo se l'app ha un lettore.
        return ToolResult.success(json.dumps(result, ensure_ascii=False))


class AppToolsSyncer:
    """Keeps ``use_app`` and the apps it reaches in sync with ``<workspace>/apps``.

    Cheap when nothing changed: one stat per app.json. On change, the apps are
    rescanned; broken apps contribute no actions. ``use_app`` is registered
    while at least one app has an action, and removed otherwise.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = Path(workspace)
        self._mtimes: dict[str, object] = {}
        self._apps: dict[str, LoadedApp] = {}
        self._tool = UseAppTool(self._workspace, self)

    def _manifest_mtimes(self) -> dict[str, object]:
        """Una firma per app: la data del suo manifest."""
        apps_root = self._workspace / "apps"
        mtimes: dict[str, object] = {}
        if not apps_root.is_dir():
            return mtimes
        for entry in apps_root.iterdir():
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            manifest = entry / "app.json"
            try:
                mtime = manifest.stat().st_mtime_ns
            except OSError:
                mtime = -1  # folder without readable manifest
            mtimes[entry.name] = mtime
        return mtimes

    def apps(self) -> dict[str, LoadedApp]:
        """The apps as of the last sync, by slug (broken ones included)."""
        return self._apps

    def sync(self, registry: Any) -> tuple[list[str], bool]:
        """Rescan the apps when a manifest changed; returns (``slug.action`` names, changed)."""
        enabled, _, _ = _apps_runtime_config()
        current = self._manifest_mtimes() if enabled else {}
        changed = current != self._mtimes
        if changed:
            apps = {app.slug: app for app in scan_apps(self._workspace)} if current else {}
            for app in apps.values():
                if app.broken:
                    logger.warning("App '{}' is broken, no actions: {}", app.slug, app.error)
            self._apps = {slug: app for slug, app in apps.items() if slug in current}
            self._mtimes = current
        actions = [
            f"{slug}.{action.name}"
            for slug, app in sorted(self._apps.items())
            if app.manifest is not None and not app.broken
            for action in app.manifest.actions
        ]
        existing = registry.get(USE_APP_TOOL)
        if actions and existing is None:
            registry.register(self._tool)
        elif not actions and isinstance(existing, UseAppTool):
            registry.unregister(USE_APP_TOOL)
        elif actions and not isinstance(existing, UseAppTool):
            logger.warning("Tool '{}' already exists and is not the apps tool; apps unreachable",
                           USE_APP_TOOL)
        return actions, changed
