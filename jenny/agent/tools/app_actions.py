"""Jenny App actions exposed as agent tools.

Each valid action in ``<workspace>/apps/<slug>/app.json`` becomes a native
LLM tool named ``<slug>_<action>`` (slug hyphens are kept as-is — never
normalized, to avoid ``my-app``/``my_app`` collisions). ``AppToolsSyncer``
diffs the apps folder against the registry on every turn, so manifest edits
become live tools on the next turn without a restart.

These tools are not in the loader's ``TOOLS`` lists: the syncer owns their
lifecycle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.agent.tools.base import Tool
from jenny.agent.tools.result import ToolResult
from jenny.apps.executor import AppActionError, execute_action
from jenny.apps.manifest import AppAction, action_param_schema, scan_apps


def _apps_runtime_config() -> tuple[bool, float, int]:
    """(enabled, http_timeout_s, max_collection_bytes) — lazy, safe defaults."""
    try:
        from jenny.config.loader import load_config

        apps = load_config().apps
        return apps.enabled, apps.http_timeout_s, apps.max_collection_bytes
    except Exception:
        return True, 20.0, 5_000_000


class AppActionTool(Tool):
    """One typed action of one Jenny App, exposed as a native tool."""

    def __init__(
        self,
        workspace: Path,
        slug: str,
        app_name: str,
        action: AppAction,
    ) -> None:
        self._workspace = Path(workspace)
        self._slug = slug
        self._app_name = app_name
        self._action = action

    @property
    def name(self) -> str:
        return f"{self._slug}_{self._action.name}"

    @property
    def description(self) -> str:
        return f"[App {self._app_name}] {self._action.description}"

    @property
    def parameters(self) -> dict[str, Any]:
        return action_param_schema(self._action)

    @property
    def read_only(self) -> bool:
        return self._action.op == "query" or self._action.method == "GET"

    async def execute(self, **kwargs: Any) -> ToolResult:
        _, http_timeout_s, max_collection_bytes = _apps_runtime_config()
        try:
            result = await execute_action(
                self._workspace,
                self._slug,
                self._action.name,
                kwargs,
                http_timeout_s=http_timeout_s,
                max_collection_bytes=max_collection_bytes,
            )
        except AppActionError as exc:
            return ToolResult.failure(str(exc), code="app_action")
        # L'avviso alla WebUI lo manda ``execute_action`` (``apps.notify``), come
        # per una scrittura dallo schermo: da qualunque canale venga il turno, e
        # dopo il ricalcolo se l'app ha un lettore.
        return ToolResult.success(json.dumps(result, ensure_ascii=False))


class AppToolsSyncer:
    """Keeps the tool registry in sync with ``<workspace>/apps``.

    Cheap when nothing changed: one stat per app.json. On change, the app's
    tools are unregistered and re-registered from the fresh manifest; broken
    apps contribute no tools. Names colliding with non-app tools are skipped
    with a warning.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = Path(workspace)
        # slug -> (app.json mtime_ns, [registered tool names])
        self._state: dict[str, tuple[object, list[str]]] = {}

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

    def sync(self, registry: Any) -> tuple[list[str], bool]:
        """Diff apps against the registry; returns (tool names, changed)."""
        enabled, _, _ = _apps_runtime_config()
        current = self._manifest_mtimes() if enabled else {}
        changed = False

        # Removed or changed apps: drop their tools first.
        for slug in list(self._state):
            mtime, tool_names = self._state[slug]
            if current.get(slug) == mtime:
                continue
            changed = True
            for name in tool_names:
                if isinstance(registry.get(name), AppActionTool):
                    registry.unregister(name)
            del self._state[slug]

        # New or changed apps: register from fresh manifests.
        stale = [slug for slug in current if slug not in self._state]
        if stale:
            changed = True
            by_slug = {app.slug: app for app in scan_apps(self._workspace)}
            for slug in stale:
                app = by_slug.get(slug)
                registered: list[str] = []
                if app is not None and not app.broken and app.manifest is not None:
                    for action in app.manifest.actions:
                        tool = AppActionTool(self._workspace, slug, app.manifest.name, action)
                        existing = registry.get(tool.name)
                        if existing is not None and not isinstance(existing, AppActionTool):
                            logger.warning(
                                "App tool '{}' collides with an existing tool; skipped",
                                tool.name,
                            )
                            continue
                        registry.register(tool)
                        registered.append(tool.name)
                elif app is not None and app.broken:
                    logger.warning("App '{}' is broken, no tools registered: {}",
                                   slug, app.error)
                self._state[slug] = (current[slug], registered)

        return [name for _, names in self._state.values() for name in names], changed
