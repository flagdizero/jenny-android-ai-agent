"""Validate a Jenny App folder (app.json manifest + files).

Usage:
    python validate_app.py apps/<slug>

Prints errors (must fix) and warnings (should fix), then VALID/INVALID.
Designed to be run via python_exec with cwd = workspace root.
"""

import json
import re
import sys
from pathlib import Path

SLUG_RE = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")
ACTION_NAME_RE = re.compile(r"\A[a-z][a-z0-9_]*\Z")
COLLECTION_RE = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")
PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")
STORAGE_OPS = {"append", "set", "update", "delete", "query"}
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
SECRET_KEYS = {"token", "password", "apikey", "api_key", "key", "secret", "bearer"}

# Colori scritti a mano nel CSS dell'app. La regola "colori sempre dalle
# variabili, mai hex fissi" era in references/manifest.md ma non la applicava
# nessuno: un'app poteva essere interamente indaco e passare con 0 rilievi,
# restando dello stesso colore su tutti e 7 i temi.
HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3}(?:[0-9a-fA-F]{2})?)?(?![0-9a-fA-F])")
# `#f0f {` è un selettore d'id, non un colore: a destra di un hex-colore non si
# arriva mai a una graffa aperta restando fra i caratteri di un selettore.
SELECTOR_TAIL_RE = re.compile(r"""^[\s,>+~.#\w\[\]='"()-]*\{""")


def css_regions(html):
    """I punti dell'HTML dove un hex è un colore: <style> e attributi style."""
    for match in re.finditer(r"<style\b[^>]*>(.*?)</style>", html, re.S | re.I):
        yield match.group(1)
    for match in re.finditer(r"""\sstyle\s*=\s*(["'])(.*?)\1""", html, re.S | re.I):
        yield match.group(2)


def hardcoded_colors(html):
    """Colori esadecimali nel CSS dell'app, in ordine di apparizione, senza duplicati."""
    found = []
    for css in css_regions(html):
        for match in HEX_COLOR_RE.finditer(css):
            if SELECTOR_TAIL_RE.match(css[match.end():]):
                continue
            value = match.group(0).lower()
            if value not in found:
                found.append(value)
    return found


def validate_action(action, index, has_server, errors, warnings):
    where = f"actions[{index}]"
    if not isinstance(action, dict):
        errors.append(f"{where}: must be an object")
        return None

    name = action.get("name")
    if not isinstance(name, str) or not ACTION_NAME_RE.match(name):
        errors.append(f"{where}: 'name' must be snake_case (got {name!r})")
    else:
        where = f"actions[{index}] ({name})"
    if not isinstance(action.get("description"), str) or not action["description"].strip():
        errors.append(f"{where}: 'description' is required")

    params = action.get("params", {})
    if not isinstance(params, dict):
        errors.append(f"{where}: 'params' must be an object mapping name -> JSON Schema")
        params = {}
    for pname, schema in params.items():
        if not isinstance(schema, dict) or "type" not in schema:
            errors.append(f"{where}: param '{pname}' must be a JSON Schema object with 'type'")
    required = action.get("required", [])
    if not isinstance(required, list) or any(r not in params for r in required):
        errors.append(f"{where}: 'required' must list a subset of params {sorted(params)}")

    kind = action.get("kind")
    if kind == "storage":
        if action.get("op") not in STORAGE_OPS:
            errors.append(f"{where}: 'op' must be one of {sorted(STORAGE_OPS)}")
        if action.get("op") == "query" and "limit" in params:
            warnings.append(
                f"{where}: param 'limit' is reserved on query actions (page size, "
                "not a filter) — rename the param if you meant to filter by it"
            )
        collection = action.get("collection")
        if not isinstance(collection, str) or not COLLECTION_RE.match(collection):
            errors.append(f"{where}: 'collection' must be lowercase alphanumeric/hyphens")
    elif kind == "http":
        if action.get("method") not in HTTP_METHODS:
            errors.append(f"{where}: 'method' must be one of {sorted(HTTP_METHODS)}")
        path = action.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            errors.append(f"{where}: 'path' must start with '/'")
        else:
            missing = [p for p in PLACEHOLDER_RE.findall(path) if p not in params]
            if missing:
                errors.append(f"{where}: path placeholders not declared in params: {missing}")
        if not has_server:
            errors.append(f"{where}: kind 'http' requires a top-level 'server.baseUrl'")
    else:
        errors.append(f"{where}: 'kind' must be 'storage' or 'http' (got {kind!r})")
    return name


def find_raw_secrets(node, path, errors):
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else key
            # Nessuna eccezione per `secretRef`: non e' piu' un campo ammesso
            # (v. il rifiuto di server.auth sopra), e "secretref" non e'
            # comunque in SECRET_KEYS — la condizione non scattava mai e
            # lasciava credere che quella forma fosse ancora sanzionata.
            if key.lower() in SECRET_KEYS and isinstance(value, str) and value.strip():
                errors.append(
                    f"{child}: looks like a raw secret — an app server must not need credentials "
                    "(app-server auth is not supported); point baseUrl at an open endpoint"
                )
            find_raw_secrets(value, child, errors)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            find_raw_secrets(item, f"{path}[{i}]", errors)


def validate_app(app_dir):
    app_dir = Path(app_dir)
    errors, warnings = [], []

    if not app_dir.is_dir():
        return [f"{app_dir}: not a directory"], warnings
    slug = app_dir.name
    if not SLUG_RE.match(slug) or len(slug) > 32:
        errors.append(f"folder name '{slug}' must be a valid slug (lowercase, hyphens, <=32 chars)")
    if "ui" in app_dir.parts:
        errors.append("app lives under ui/ — it will be overwritten on startup; move it to apps/")

    manifest_path = app_dir / "app.json"
    if not manifest_path.is_file():
        return errors + ["app.json is missing"], warnings
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return errors + [f"app.json: invalid JSON ({exc})"], warnings
    if not isinstance(manifest, dict):
        return errors + ["app.json: top level must be an object"], warnings

    for field in ("name", "description"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            errors.append(f"app.json: '{field}' is required")

    server = manifest.get("server")
    has_server = False
    if server is not None:
        if not isinstance(server, dict) or not isinstance(server.get("baseUrl"), str):
            errors.append("app.json: 'server' must be an object with a 'baseUrl' string")
        else:
            has_server = True
            if not server["baseUrl"].startswith(("http://", "https://")):
                errors.append("app.json: server.baseUrl must start with http:// or https://")
            if server.get("auth") is not None:
                # Il gateway non ha un credential store e l'esecutore http e'
                # fail-closed: un'app con `auth` si apre e ha tutte le azioni
                # http morte con un 501. Va detto qui, mentre il manifest si
                # scrive, non scoperto tentando un'azione.
                errors.append(
                    "app.json: server.auth is declared but app-server credentials are "
                    "not supported yet — remove the 'auth' block (every http action "
                    "would be refused with 501)"
                )

    find_raw_secrets(manifest, "app.json", errors)

    actions = manifest.get("actions") or []
    if not isinstance(actions, list):
        errors.append("app.json: 'actions' must be an array")
    elif actions:
        names = [validate_action(a, i, has_server, errors, warnings) for i, a in enumerate(actions)]
        dupes = {n for n in names if n and names.count(n) > 1}
        if dupes:
            errors.append(f"app.json: duplicate action names: {sorted(dupes)}")

    view = manifest.get("view")
    external_view = False
    if view is not None:
        if not isinstance(view, dict):
            errors.append("app.json: 'view' must be an object")
        elif view.get("kind") != "external":
            errors.append(
                f"app.json: view.kind must be 'external' (got {view.get('kind')!r})"
            )
        else:
            external_view = True
            if not has_server:
                errors.append("app.json: view.kind 'external' requires a 'server.baseUrl'")
            elif isinstance(server, dict) and str(server.get("baseUrl", "")).startswith("https://"):
                warnings.append(
                    "view.kind 'external' with an https baseUrl: the loopback proxy is only "
                    "needed to get around the cleartext policy — frame the URL directly"
                )

    index_path = app_dir / "app" / "index.html"
    if external_view:
        # Lo schermo di questa app e' la UI del suo server, servita dal proxy su
        # loopback: non c'e' un `app/index.html` da scrivere, e pretenderlo
        # avrebbe costretto a un file finto.
        if index_path.is_file():
            warnings.append(
                "app/index.html is present but view.kind is 'external' — it will never be "
                "shown; delete it or drop the 'view' block"
            )
    elif not index_path.is_file():
        errors.append("app/index.html is missing (the UI lives in the app/ subfolder)")
    else:
        html = index_path.read_text(encoding="utf-8", errors="replace")
        externals = re.findall(r'(?:src|href)\s*=\s*["\']https?://[^"\']+', html)
        if externals:
            errors.append(
                f"index.html references {len(externals)} external URL(s) — no external hosts "
                "allowed; only inline code and gateway paths (/html-mobile/assets/...)"
            )
        # Capacità della sandbox. L'iframe delle app è `sandbox="allow-scripts"` e
        # nient'altro (vedi mobile-apps.js), quindi i pattern qui sotto sono morti a
        # runtime *in silenzio*: l'app sembra finita e non fa niente quando la tocchi.
        # Erano documentati in references/manifest.md ma nessuno li applicava, così
        # un'app rotta passava la validazione senza un rilievo.
        if re.search(r"<form\b", html, re.IGNORECASE):
            errors.append(
                "index.html contains a <form> — the app iframe has no 'allow-forms', so "
                "submission is blocked before the 'submit' event is fired and "
                "event.preventDefault() never runs. Drop the <form>: use a "
                '<button type="button"> with a click handler, add a keydown listener for '
                "Enter on the input, and call jenny.action() from the handler"
            )
        modals = [fn for fn in ("alert", "confirm", "prompt") if re.search(rf"\b{fn}\s*\(", html)]
        if modals:
            warnings.append(
                "app/index.html looks like it calls "
                + ", ".join(f"{fn}()" for fn in modals)
                + " — the iframe has no 'allow-modals', so these silently do nothing; "
                "build dialogs with <dialog> or kit markup"
            )
        if "jenny-kit.css" not in html:
            warnings.append(
                "app/index.html does not link the Jenny Kit "
                "(/html-mobile/assets/apps/jenny-kit.css) — the UI will not match the app theme"
            )
        if "jenny-sdk.js" not in html:
            warnings.append(
                "app/index.html does not load the SDK (/html-mobile/assets/apps/jenny-sdk.js) "
                "— use jenny.action() for all action calls"
            )
        hexes = hardcoded_colors(html)
        if hexes:
            shown = ", ".join(hexes[:6]) + ("..." if len(hexes) > 6 else "")
            warnings.append(
                f"app/index.html hardcodes {len(hexes)} color(s) in CSS ({shown}) — these "
                "ignore the user's theme and stay the same on all 7 of them; color with the "
                "kit tokens instead (var(--accent), var(--text), var(--bg2), ...)"
            )
        if re.search(r"fetch\s*\(\s*[`'\"][^)]*?/api/apps/", html):
            warnings.append(
                "app/index.html calls /api/apps/ with fetch directly — the gateway is GET-only "
                "and CORS-preflight-free; always go through jenny.action()"
            )
    if not (app_dir / "AGENT.md").is_file():
        warnings.append("AGENT.md is missing — add 5-15 lines of context for the agent")

    return errors, warnings


def main(argv):
    if len(argv) < 2:
        print("Usage: validate_app.py apps/<slug>")
        return
    errors, warnings = validate_app(argv[1])
    for msg in errors:
        print(f"ERROR: {msg}")
    for msg in warnings:
        print(f"WARNING: {msg}")
    print("INVALID" if errors else "VALID", f"({len(errors)} errors, {len(warnings)} warnings)")


if __name__ == "__main__":
    main(sys.argv)
