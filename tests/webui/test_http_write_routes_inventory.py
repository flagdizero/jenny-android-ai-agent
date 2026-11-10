"""Le route ``/api/`` che scrivono sono un elenco chiuso: non se ne aggiungono.

``CONTRIBUTING.md`` vuole le scritture sui comandi del WebSocket: ``/api/`` non
legge un corpo, quindi una scrittura lì porta i suoi parametri nella query, dove
finiscono nei log e hanno un tetto di 8 kB. Le route qui sotto esistono da prima
della regola — interruttori e campi corti delle impostazioni, i tasti degli
aiutanti e dei job — e restano finché qualcuno non le porta su RPC. Questo test
è il dente d'arresto: una route nuova che scrive lo fa diventare rosso, e la
risposta giusta è un comando in ``jenny/webui/*_commands.py``, non una riga in
più qui.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SOURCES = (
    *sorted((_REPO / "jenny" / "webui").glob("*.py")),
    *sorted((_REPO / "jenny" / "channels").glob("*.py")),
    *sorted((_REPO / "jenny_extensions").rglob("*.py")),
)

# Un segmento che dice «questa route cambia qualcosa».
_WRITE_WORDS = frozenset({
    "update", "delete", "create", "restore", "retention", "import", "generate", "accept",
    "unpair", "install", "uninstall", "launch", "restart", "cancel", "continue", "undo",
    "pause", "resume", "remove", "close", "mkdir", "set", "enable", "disable", "write",
    "save", "rename", "move", "copy", "add", "drop", "reset", "export",
})

# Le scritture che ``/api/`` aveva quando la regola è stata scritta. Si accorcia,
# non si allunga.
_KNOWN_WRITES = frozenset({
    "/api/backup/export",
    "/api/backup/import",
    "/api/backup/snapshots/create",
    "/api/backup/snapshots/restore",
    "/api/backup/snapshots/retention",
    "/api/settings/floating/update",
    "/api/settings/location/update",
    "/api/settings/memory/update",
    "/api/settings/power/update",
    "/api/settings/provider/delete",
    "/api/settings/ssh/host-key/accept",
    "/api/settings/ssh/host/delete",
    "/api/settings/ssh/key/generate",
    "/api/settings/ssh/update",
    "/api/settings/update",
    "/api/settings/web-search/update",
    "/api/settings/workers/update",
    "/api/telegram/unpair",
    "/api/telegram/update",
    "/api/updates/install",
    "/api/workspace/mkdir",
    "/api/subagents/*/cancel",
    "/api/subagents/*/continue",
    "/api/subagents/*/restart",
    "/api/subagents/*/undo",
    "/api/webui/android-apps/*/launch",
    "/api/webui/android-apps/*/uninstall",
    "/api/webui/apps/*/view/close",
    "/api/webui/cron/*/pause",
    "/api/webui/cron/*/remove",
    "/api/webui/cron/*/resume",
    "/api/webui/skills/*/update",
})

_LITERAL = re.compile(r'"(/api/[A-Za-z0-9_/<>{}.()-]+)"')
_PATTERN = re.compile(r'r"\^(/api/[^"]+?)\$"')


def _expand(pattern: str) -> list[str]:
    """Una route scritta come regex, in una o più route con ``*`` al posto dei pezzi liberi."""
    routes = [""]
    for part in re.split(r"(\([^()]*\))", pattern):
        if not part.startswith("("):
            routes = [r + part for r in routes]
            continue
        words = part[1:-1].split("|")
        if all(re.fullmatch(r"[a-z-]+", w) for w in words):
            routes = [r + w for r in routes for w in words]
        else:
            routes = [r + "*" for r in routes]
    return routes


def _routes() -> set[str]:
    found: set[str] = set()
    for source in _SOURCES:
        text = source.read_text(encoding="utf-8")
        for literal in _LITERAL.findall(text):
            found.add(re.sub(r"<[^>]+>|\{[^}]+\}", "*", literal).rstrip("/"))
        for pattern in _PATTERN.findall(text):
            found.update(_expand(pattern))
    return found


def _writes(route: str) -> bool:
    return any(segment in _WRITE_WORDS for segment in route.split("/"))


def test_the_scan_sees_the_routes() -> None:
    routes = _routes()
    assert "/api/settings" in routes and "/api/subagents/*/undo" in routes
    assert len(routes) > 60, sorted(routes)


def test_no_new_http_route_writes() -> None:
    writes = {route for route in _routes() if _writes(route)}
    new = sorted(writes - _KNOWN_WRITES)
    assert not new, (
        f"new /api/ route(s) that write: {new}. A write is a WebSocket command "
        "(jenny/webui/*_commands.py, see CONTRIBUTING.md), not an HTTP route."
    )


def test_the_inventory_names_only_routes_that_exist() -> None:
    gone = sorted(_KNOWN_WRITES - _routes())
    assert not gone, f"no longer routes, take them off the inventory: {gone}"
