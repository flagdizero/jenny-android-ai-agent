"""Jenny App manifest loading and validation.

An app is a folder in ``<workspace>/apps/<slug>/`` with an ``app.json``
manifest optionally declaring typed actions (see ``.agent/jenny-apps.md`` and
the ``app-creator`` skill reference). Loading never raises: malformed apps come
back as ``LoadedApp(broken=True, error=...)`` so the grid can show them as
broken without ever crashing the gateway.

``actions`` is optional: an app whose only job is to draw a screen has nothing
agent-facing to declare, and requiring a non-empty list made that app
impossible to write — the observed result was an *invented* action added only to
satisfy the schema. ``.agent/jenny-apps.md`` always described the agent-facing
side as something an app "can" carry; the code disagreed until Sept 2026.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Kept character-identical to skills/app-creator/scripts/validate_app.py
SLUG_RE = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")
ACTION_NAME_RE = re.compile(r"\A[a-z][a-z0-9_]*\Z")
COLLECTION_RE = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")
PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")
STORAGE_OPS = {"append", "set", "update", "delete", "query"}
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
MAX_SLUG_LEN = 32
# Kind ammessi per il blocco ``view``. "external" vuol dire: lo schermo dell'app
# e' la UI del suo ``server.baseUrl``, servita attraverso il proxy su loopback
# (``apps/proxy.py``) invece di ``app/index.html``. Esiste perche' la policy
# cleartext dell'APK rifiuta un iframe verso un ``http://`` non-loopback, quindi
# "incornicia il mio server" non era esprimibile in nessun modo.
VIEW_KINDS = {"external"}


@dataclass
class AppAction:
    name: str
    description: str
    kind: str  # "storage" | "http"
    params: dict = field(default_factory=dict)
    required: list = field(default_factory=list)
    # storage
    op: str | None = None
    collection: str | None = None
    # http
    method: str | None = None
    path: str | None = None


@dataclass
class AppManifest:
    name: str
    description: str
    icon: str = "ti-apps"
    server_base_url: str | None = None
    server_auth: dict | None = None
    actions: list[AppAction] = field(default_factory=list)
    # "external" quando lo schermo dell'app *e'* il suo server, non
    # ``app/index.html``. None per un'app normale. V. VIEW_KINDS.
    view_kind: str | None = None


@dataclass
class LoadedApp:
    slug: str
    dir: Path
    manifest: AppManifest | None = None
    broken: bool = False
    error: str = ""


def action_param_schema(action: AppAction) -> dict:
    """Root JSON Schema for an action's parameters (provider/tool compatible).

    Storage ops get their reserved params injected when the manifest doesn't
    declare them, so the LLM and the UI see the full contract: ``id`` for
    set/update/delete (required at runtime), ``limit`` for query (page size,
    not a filter). Undeclared params are rejected at validation time.
    """
    properties = dict(action.params)
    required = list(action.required)
    if action.kind == "storage":
        if action.op in ("set", "update", "delete") and "id" not in properties:
            properties["id"] = {
                "type": "string",
                "description": "Record id (auto-assigned on append)",
            }
            required.append("id")
        elif action.op == "query" and "limit" not in properties:
            properties["limit"] = {
                "type": "integer",
                "description": "Max records returned (default 200); not a filter",
            }
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _parse_action(raw: object, index: int, has_server: bool) -> AppAction:
    """Parse one action entry; raises ValueError with a readable message."""
    where = f"actions[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: must be an object")

    name = raw.get("name")
    if not isinstance(name, str) or not ACTION_NAME_RE.match(name):
        raise ValueError(f"{where}: 'name' must be snake_case (got {name!r})")
    where = f"actions[{index}] ({name})"

    description = raw.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"{where}: 'description' is required")

    params = raw.get("params", {})
    if not isinstance(params, dict):
        raise ValueError(f"{where}: 'params' must be an object mapping name -> JSON Schema")
    for pname, schema in params.items():
        if not isinstance(schema, dict) or "type" not in schema:
            raise ValueError(f"{where}: param '{pname}' must be a JSON Schema object with 'type'")
    required = raw.get("required", [])
    if not isinstance(required, list) or any(r not in params for r in required):
        raise ValueError(f"{where}: 'required' must list a subset of params {sorted(params)}")

    kind = raw.get("kind")
    action = AppAction(
        name=name,
        description=description.strip(),
        kind=str(kind),
        params=params,
        required=required,
    )

    if kind == "storage":
        op = raw.get("op")
        if op not in STORAGE_OPS:
            raise ValueError(f"{where}: 'op' must be one of {sorted(STORAGE_OPS)}")
        collection = raw.get("collection")
        if not isinstance(collection, str) or not COLLECTION_RE.match(collection):
            raise ValueError(f"{where}: 'collection' must be lowercase alphanumeric/hyphens")
        action.op = op
        action.collection = collection
    elif kind == "http":
        method = raw.get("method")
        if method not in HTTP_METHODS:
            raise ValueError(f"{where}: 'method' must be one of {sorted(HTTP_METHODS)}")
        path = raw.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError(f"{where}: 'path' must start with '/'")
        missing = [p for p in PLACEHOLDER_RE.findall(path) if p not in params]
        if missing:
            raise ValueError(f"{where}: path placeholders not declared in params: {missing}")
        if not has_server:
            raise ValueError(f"{where}: kind 'http' requires a top-level 'server.baseUrl'")
        action.method = method
        action.path = path
    else:
        raise ValueError(f"{where}: 'kind' must be 'storage' or 'http' (got {kind!r})")

    return action


def _parse_manifest(data: object) -> AppManifest:
    """Parse a decoded app.json; raises ValueError with a readable message."""
    if not isinstance(data, dict):
        raise ValueError("app.json: top level must be an object")

    for fname in ("name", "description"):
        if not isinstance(data.get(fname), str) or not data[fname].strip():
            raise ValueError(f"app.json: '{fname}' is required")

    server = data.get("server")
    base_url: str | None = None
    server_auth: dict | None = None
    if server is not None:
        if not isinstance(server, dict) or not isinstance(server.get("baseUrl"), str):
            raise ValueError("app.json: 'server' must be an object with a 'baseUrl' string")
        base_url = server["baseUrl"]
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("app.json: server.baseUrl must start with http:// or https://")
        if server.get("auth") is not None:
            # Rifiutato in fase di caricamento, non solo di esecuzione. Il
            # credential store non esiste (roadmap) e ``execute_http_action`` e'
            # fail-closed, quindi un manifest con ``auth`` produceva un'app che
            # passava la validazione, si apriva, e aveva *tutte* le azioni http
            # morte con un 501 — visibile solo a chi tentava un'azione. Un
            # contratto irrealizzabile e' un'app rotta, e va detto qui, dove
            # l'errore finisce sotto gli occhi di chi ha scritto il manifest.
            raise ValueError(
                "app.json: server.auth is declared but app-server credentials are not "
                "supported yet — remove the 'auth' block (every http action would be "
                "refused with 501)"
            )

    raw_actions = data.get("actions") or []
    if not isinstance(raw_actions, list):
        raise ValueError("app.json: 'actions' must be an array")
    actions = [
        _parse_action(raw, i, has_server=base_url is not None)
        for i, raw in enumerate(raw_actions)
    ]
    names = [a.name for a in actions]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError(f"app.json: duplicate action names: {dupes}")

    view = data.get("view")
    view_kind: str | None = None
    if view is not None:
        if not isinstance(view, dict):
            raise ValueError("app.json: 'view' must be an object")
        view_kind = view.get("kind")
        if view_kind not in VIEW_KINDS:
            raise ValueError(
                f"app.json: view.kind must be one of {sorted(VIEW_KINDS)} (got {view_kind!r})"
            )
        if view_kind == "external" and base_url is None:
            raise ValueError("app.json: view.kind 'external' requires a 'server.baseUrl'")

    icon = data.get("icon")
    return AppManifest(
        name=data["name"].strip(),
        description=data["description"].strip(),
        icon=icon if isinstance(icon, str) and icon else "ti-apps",
        server_base_url=base_url,
        server_auth=server_auth,
        actions=actions,
        view_kind=view_kind,
    )


def load_app(app_dir: Path) -> LoadedApp:
    """Load one app folder. Never raises: problems come back as broken=True."""
    slug = app_dir.name
    app = LoadedApp(slug=slug, dir=app_dir)

    if not SLUG_RE.match(slug) or len(slug) > MAX_SLUG_LEN:
        app.broken = True
        app.error = f"folder name '{slug}' is not a valid slug (lowercase, hyphens, <=32 chars)"
        return app

    manifest_path = app_dir / "app.json"
    if not manifest_path.is_file():
        app.broken = True
        app.error = "app.json is missing"
        return app

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        app.broken = True
        app.error = f"app.json: invalid JSON ({exc})"
        return app

    try:
        app.manifest = _parse_manifest(data)
    except ValueError as exc:
        app.broken = True
        app.error = str(exc)
    return app


def scan_apps(workspace: Path) -> list[LoadedApp]:
    """Scan ``<workspace>/apps`` for app folders, sorted by slug. Never raises."""
    apps_root = Path(workspace) / "apps"
    if not apps_root.is_dir():
        return []
    apps: list[LoadedApp] = []
    for entry in sorted(apps_root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        apps.append(load_app(entry))
    return apps


def find_app(workspace: Path, slug: str) -> LoadedApp | None:
    """Load a single app by slug, or None when the folder doesn't exist."""
    if not SLUG_RE.match(slug):
        return None
    app_dir = Path(workspace) / "apps" / slug
    if not app_dir.is_dir():
        return None
    return load_app(app_dir)
