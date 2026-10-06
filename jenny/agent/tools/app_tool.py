"""Il tool ``app``: Jenny scrive, prova, ripara e posa le app.

Dal 06/10/2026 c'è un concetto solo, «app»: generale (vive in
``workspace/apps``), con uno schermo, un lettore, un riassunto e delle azioni,
tutti facoltativi. Questo tool è **l'unica strada** per scriverne il codice:
gli strumenti generici non toccano ``app.json``, ``run.py`` né ``app/``
(``jenny/security/workspace_policy.py::is_app_code_path``).

**Prova prima di salvare.** ``draft`` scrive in ``apps/.drafts/``, valida e,
se c'è un lettore, lo gira una volta: se va, l'app (o la sua versione nuova)
**esiste subito** — nella pagina App, con i suoi tool — e torna il valore vero;
se no torna l'errore, e l'app di prima resta com'era
(``jenny/apps/drafts.py``). Nessuna approvazione: un'app che non piace si
butta, nel cestino.

Funziona dalla chat personale e da quella di un quaderno, solo nei turni
dell'utente: mai dal giardiniere, da un cron o da un subagent. Da un quaderno
non si cancellano app.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import current_request_session_key
from jenny.agent.tools.schema import ObjectSchema, StringSchema, tool_parameters_schema
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    current_turn_is_readonly,
    current_workspace_scope,
)

_MAX_DRAFTS = 4
_DRAFT_WINDOW_S = 600
MAX_CODE_BYTES = 6 * 1024
MAX_SCREEN_BYTES = 512 * 1024
_ALLOWED_TOP = {"app.json", "run.py", "AGENT.md"}

_PARAMETERS = tool_parameters_schema(
    action=StringSchema(
        "draft: write a new app (or a new version): it is validated, its reader runs once, "
        "and when it works it is live at once; edit: replace one exact string in one file "
        "of the app (same checks, then live); show: manifest, state and code; run: read the "
        "reader now; "
        "place: put the app on a notebook board as a card of its own (its value on the "
        "polaroid, a tap opens it), optionally tied to an existing card; trash: move an app to the trash (personal chat only).",
        enum=["draft", "edit", "show", "run", "place", "trash"],
    ),
    slug=StringSchema("The app's folder name: lowercase, digits and hyphens, at most 32."),
    files=ObjectSchema(
        description="For draft: {path: content}. Paths: app.json (required for a new app), "
        "run.py (the reader), AGENT.md, and app/<file> for the screen (app/index.html). "
        "On a new version you may send only the files that change.",
        additional_properties=True,
    ),
    path=StringSchema("For edit: the file to change (app.json, run.py, AGENT.md, app/…)."),
    old=StringSchema("For edit: the exact text to replace (must occur once)."),
    new=StringSchema("For edit: the replacement text."),
    notebook=StringSchema("For place from the personal chat: the notebook name."),
    card=StringSchema("For place (optional): an existing card (title or id) the app belongs "
                      "with — the app's card is tied to it by a thread. The app never goes "
                      "inside another card's drawer."),
    params=ObjectSchema(
        description="For place: the summary's params for this placement (e.g. which "
        "sensor), {name: value}.",
        additional_properties=True,
    ),
    required=["action", "slug"],
)


@tool_parameters(_PARAMETERS)
class AppTool(Tool):
    """Scrive, prova, ripara e posa le app."""

    _scopes = {"core", "orchestrator"}

    def __init__(self) -> None:
        self._drafts: dict[str, list[float]] = {}

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls()

    @property
    def name(self) -> str:
        return "app"

    @property
    def description(self) -> str:
        return (
            "Write, try, fix and place the user's apps. An app lives in workspace/apps/<slug>/ "
            "and may have: a screen (app/index.html + declared actions in app.json), a reader "
            "(run.py, run every N minutes by the clock with no model turn) and a summary (how "
            "it shows on its own card: the value in place of the photo). `draft` validates the "
            "files and runs the reader once: when they work the app is live at once (in the "
            "App page, with its tools) — tell the user the real value; when they do not, "
            "nothing changes and you get the error. Any app that keeps state MUST "
            "declare a `summary`. Use the app-creator skill for the manifest format. `place` "
            "puts it on a notebook board as a card of its own (a tap opens its screen), tied by "
            "a thread to the card it belongs with: placing it in a notebook also lets that "
            "notebook's chat use it."
        )

    # -- dove ---------------------------------------------------------------

    @staticmethod
    def _session() -> str | None:
        """La sessione, se e' un turno dell'utente (personale o di un quaderno)."""
        from jenny.session.keys import is_personal_session_key, is_project_session_key

        key = current_request_session_key() or ""
        if is_project_session_key(key) or is_personal_session_key(key):
            return key
        return None

    def _throttled(self, slug: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self._drafts.get(slug, []) if now - t < _DRAFT_WINDOW_S]
        if len(recent) >= _MAX_DRAFTS:
            self._drafts[slug] = recent
            return True
        self._drafts[slug] = [*recent, now]
        return False

    async def execute(self, action: str = "", slug: str = "", **kw: Any) -> str:
        from jenny.apps.manifest import SLUG_RE
        from jenny.config.paths import get_workspace_path

        session = self._session()
        if session is None:
            return ("Not from here: apps are written only in a conversation with the user "
                    "(the personal chat or a notebook's chat). Nothing was written.")
        if not isinstance(slug, str) or not SLUG_RE.match(slug) or len(slug) > 32:
            return "`slug` must be lowercase letters, digits and hyphens, at most 32."
        workspace = get_workspace_path()
        if action in ("draft", "edit", "place", "trash") and current_turn_is_readonly():
            return READONLY_TOOL_REFUSAL
        if action == "draft":
            return await self._draft(workspace, slug, kw.get("files") or {})
        if action == "edit":
            return await self._edit(workspace, slug, kw)
        if action == "show":
            return self._show(workspace, slug)
        if action == "run":
            return await self._run(workspace, slug)
        if action == "place":
            return self._place(workspace, slug, session, kw)
        if action == "trash":
            return self._trash(workspace, slug, session)
        return "Unknown action: use draft, edit, show, run, place or trash."

    # -- scrivere -----------------------------------------------------------

    @staticmethod
    def _check_files(files: dict[str, Any]) -> str | None:
        from jenny.board.micro import find_secrets

        screen = 0
        for path, content in files.items():
            if not isinstance(path, str) or not isinstance(content, str):
                return "`files` maps a path to its text content."
            parts = Path(path).parts
            if ".." in parts or path.startswith("/") or not parts:
                return f"{path}: paths are relative to the app folder."
            if path not in _ALLOWED_TOP and parts[0] != "app":
                return (f"{path}: an app has app.json, run.py, AGENT.md and app/… — data/ is "
                        "written by its actions, not here.")
            if path == "run.py" and len(content.encode()) > MAX_CODE_BYTES:
                return f"run.py is over {MAX_CODE_BYTES // 1024} KB: keep the reader small."
            if parts[0] == "app":
                screen += len(content.encode())
            if path in ("run.py", "app.json") and find_secrets(content):
                return (f"{path} contains what looks like a credential: never put one in an "
                        "app. Import the installed skill that holds it (reader `uses`).")
        if screen > MAX_SCREEN_BYTES:
            return f"The screen is over {MAX_SCREEN_BYTES // 1024} KB."
        return None

    @staticmethod
    def _seed_draft(workspace: Path, slug: str) -> Path:
        """La cartella della bozza: vuota per un'app nuova, il codice di adesso per una vecchia."""
        from jenny.apps import drafts

        d = drafts.draft_dir(workspace, slug)
        if d.exists():
            return d
        live = drafts.app_dir(workspace, slug)
        d.mkdir(parents=True)
        if live.is_dir():
            for name in (*drafts.CODE_FILES, "AGENT.md"):
                if (live / name).is_file():
                    (d / name).write_bytes((live / name).read_bytes())
            if (live / "app").is_dir():
                for src in (live / "app").rglob("*"):
                    if src.is_file():
                        dst = d / src.relative_to(live)
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        dst.write_bytes(src.read_bytes())
        return d

    async def _draft(self, workspace: Path, slug: str, files: Any) -> str:

        if not isinstance(files, dict) or not files:
            return "`files` is required: at least app.json for a new app."
        problem = self._check_files(files)
        if problem:
            return f"Not written: {problem}"
        if self._throttled(slug):
            return (f"Four tries on `{slug}` in a few minutes: stop here, tell the user what is "
                    "failing and ask how to go on.")
        d = self._seed_draft(workspace, slug)
        for path, content in files.items():
            target = d / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return await self._try(workspace, slug)

    async def _edit(self, workspace: Path, slug: str, kw: dict[str, Any]) -> str:
        path, old, new = kw.get("path"), kw.get("old"), kw.get("new")
        if not all(isinstance(x, str) for x in (path, old, new)) or not old:
            return "edit needs `path`, `old` (exact, non-empty) and `new`."
        assert isinstance(path, str) and isinstance(old, str) and isinstance(new, str)
        problem = self._check_files({path: new})
        if problem:
            return f"Not written: {problem}"
        from jenny.apps import drafts

        if not drafts.draft_dir(workspace, slug).exists() and not drafts.app_dir(
                workspace, slug).is_dir():
            return f"No app `{slug}`: create it with action=draft."
        d = self._seed_draft(workspace, slug)
        target = d / path
        try:
            text = target.read_text(encoding="utf-8")
        except OSError:
            return f"{path} does not exist in `{slug}`."
        if text.count(old) != 1:
            return f"`old` occurs {text.count(old)} times in {path}: it must occur exactly once."
        updated = text.replace(old, new)
        problem = self._check_files({path: updated})
        if problem:
            return f"Not written: {problem}"
        target.write_text(updated, encoding="utf-8")
        return await self._try(workspace, slug)

    async def _try(self, workspace: Path, slug: str) -> str:
        """Valida la bozza, gira il lettore una volta e, se va, la fa diventare l'app."""
        from jenny.apps import drafts
        from jenny.apps.manifest import find_app
        from jenny.apps.reader import run_reader
        from jenny.apps.summary_view import summary_view

        draft = drafts.load_draft(workspace, slug)
        assert draft is not None
        app = draft.app
        if app.broken or app.manifest is None:
            return (f"`{slug}` is not valid: {app.error}\nFix it with action=edit (or draft "
                    "again); nothing changed until it is valid.")
        m = app.manifest
        stateful = m.reader is not None or any(
            a.kind == "storage" and a.op in ("append", "set", "update", "delete")
            for a in m.actions)
        if stateful and m.section is None:
            return ("Not ready: this app keeps state, so it must declare a `summary` — how it "
                    "shows on its card (see the app-creator skill). Add it with "
                    "action=edit.")
        existed = (drafts.app_dir(workspace, slug) / "app.json").is_file()
        if m.reader is not None:
            state = await run_reader(workspace, slug, force=True, app_dir=draft.dir)
            if not state or state.get("status") != "ok":
                error = (state or {}).get("lastError", "the reader did not run")
                head = ("The network or the host did not answer (not a code bug)"
                        if (state or {}).get("status") == "unreachable" else
                        "The test run of the reader failed")
                return (f"{head}. Nothing changed{' in the live app' if existed else ''}.\n\n"
                        f"{error}\n\nContract: `def run(ctx):` "
                        "returns a dict with exactly the reader's fields (a list of dicts when "
                        "the reader has a `key`); ctx.now, ctx.prev, ctx.http (only the hosts in "
                        "`net`), ctx.data(collection), ctx.wanted. Fix it with action=edit.")
        drafts.promote_draft(workspace, slug)
        # Le schede che la mostrano prendono la taglia della striscia nuova.
        from jenny.board.place import refresh_app_cards

        try:
            refresh_app_cards(workspace, slug)
        except Exception:  # noqa: BLE001 — una taglia da riallineare non ferma l'app
            from loguru import logger

            logger.opt(exception=True).warning("apps: cards of {} not resized", slug)
        reading = ""
        live = find_app(workspace, slug)
        if m.reader is not None and live is not None:
            if existed:
                # La versione nuova legge sui dati veri, non su quelli della bozza.
                await run_reader(workspace, slug, force=True)
            # Un lettore a serie (un sensore per posa): la prima lettura e' quella
            # dell'ultima serie letta, coi parametri che il riassunto vuole.
            from jenny.apps.reader import read_readings

            last = (read_readings(live.dir) or [{}])[-1]
            params = {name: last[name] for name in (m.section.params if m.section else {})
                      if name in last}
            reading = self._reading(summary_view(workspace, live, params))
        what = "updated" if existed else "live"
        section = m.section
        strip = (f" On a board it is a strip {section.size} ("
                 + ", ".join(b["kind"] for b in section.blocks) + ").") \
            if section is not None and section.blocks else ""
        return (f"`{slug}` is {what}{' — ' + reading if reading else ''}.{strip} It is in the "
                "user's App page now, with its tools. Tell them in one or two lines what it does"
                + ("" if existed else " — and, if it is about a notebook's subject, offer to put "
                   "it on that board with action=place") + ".")

    @staticmethod
    def _reading(view: dict[str, Any]) -> str:
        if view.get("value") is None:
            return "no value yet"
        unit = view.get("unit") or ""
        parts = [f"{view.get('label') or view.get('field')}: {view['value']}{unit}"]
        for extra in view.get("extras") or []:
            parts.append(f"{extra['label']}: {extra['value']}{extra.get('unit') or ''}")
        source = view.get("source")
        return "; ".join(parts) + (f" (source: {source})" if source else "")

    # -- leggere ------------------------------------------------------------

    def _show(self, workspace: Path, slug: str) -> str:
        from jenny.apps import drafts
        from jenny.apps.manifest import find_app
        from jenny.apps.reader import read_state
        from jenny.board.micro import redact

        app = find_app(workspace, slug)
        draft = drafts.load_draft(workspace, slug)
        if app is None and draft is not None:
            app = draft.app
            state = "an unfinished draft (not an app yet: fix it with action=edit)"
        elif draft is not None:
            state = "live, with an unfinished new version (fix it with action=edit)"
        else:
            state = "live"
        if app is None:
            return f"No app `{slug}`."
        out = [f"`{slug}` — {state}" + (f" (broken: {app.error})" if app.broken else "")]
        for name in ("app.json", "run.py"):
            path = app.dir / name
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                out.append(f"--- {name}\n{redact(text) if name == 'run.py' else text}")
        if app.manifest is not None and app.manifest.reader is not None:
            reader_state = read_state(app.dir)
            out.append("--- reader state\n" + json.dumps(reader_state, ensure_ascii=False))
        screen = sorted(p.relative_to(app.dir).as_posix() for p in (app.dir / "app").rglob("*")
                        if p.is_file()) if (app.dir / "app").is_dir() else []
        if screen:
            out.append("--- screen files: " + ", ".join(screen))
        return "\n\n".join(out)

    async def _run(self, workspace: Path, slug: str) -> str:
        from jenny.apps.manifest import find_app
        from jenny.apps.reader import run_reader
        from jenny.apps.summary_view import summary_view

        app = find_app(workspace, slug)
        if app is None or app.manifest is None or app.manifest.reader is None:
            return f"`{slug}` has no reader."
        state = await run_reader(workspace, slug, force=True)
        if state and state.get("status") == "ok":
            return "Read now — " + self._reading(summary_view(workspace, app, {}))
        return f"The reader failed: {(state or {}).get('lastError', 'did not run')}"

    # -- posare -------------------------------------------------------------

    def _place(self, workspace: Path, slug: str, session: str, kw: dict[str, Any]) -> str:
        from jenny.apps.drafts import app_dir
        from jenny.board.placements import placements
        from jenny.utils.wiki_paths import discover_wiki_roots, is_wiki_root

        if not (app_dir(workspace, slug) / "app.json").is_file():
            return f"No app `{slug}`: write it with action=draft first."
        root = None
        if session.startswith("project:"):
            scope = current_workspace_scope()
            root = scope.write_root() if scope is not None else None
            if root is None or not is_wiki_root(root):
                return "This notebook has no folder I can find."
        else:
            name = kw.get("notebook")
            from jenny.board.placements import _wikis_dirs

            roots: dict[str, Path] = {}
            for wikis in _wikis_dirs(workspace):
                roots.update(discover_wiki_roots(wikis))
            if not isinstance(name, str) or name not in roots:
                names = ", ".join(sorted(roots)) or "none"
                return f"Say which notebook (`notebook`): {names}."
            root = roots[name]
        params = kw.get("params") if isinstance(kw.get("params"), dict) else {}
        params = {k: v for k, v in params.items() if isinstance(v, (str, int, float, bool))}
        if any(p["app"] == slug and p["params"] == params for p in placements(root)):
            return f"`{slug}` is already on this board with these params."
        from jenny.board.place import place_app

        try:
            where = place_app(root, slug, params=params, link=kw.get("card"),
                              by="jenny-on-request")
        except ValueError as exc:
            return str(exc)
        return (f"Placed {where}. The notebook's chat can now use `{slug}`; a tap on the card "
                "opens the app.")

    def _trash(self, workspace: Path, slug: str, session: str) -> str:
        from jenny.apps.trash import trash_app

        if session.startswith("project:"):
            return ("Not from a notebook: deleting an app is the user's call, from the App page "
                    "or the personal chat. Nothing was deleted.")
        try:
            entry = trash_app(workspace, slug)
        except (FileNotFoundError, ValueError) as exc:
            return f"Not deleted: {exc}"
        drafts = workspace / "apps" / ".drafts" / slug
        if drafts.exists():
            shutil.rmtree(drafts)
        return (f"`{slug}` is in the trash ({entry}), data included. The user can put it back "
                "from the Trash row at the bottom of the App page.")


TOOLS = [AppTool]
