"""Il tool ``app``: Jenny scrive, prova, ripara e posa le app.

Dal 06/10/2026 c'è un concetto solo, «app»: generale (vive in
``workspace/apps``), con uno schermo, un lettore, un riassunto e delle azioni,
tutti facoltativi. Questo tool è **l'unica strada** per scriverne il codice:
gli strumenti generici non toccano ``app.json``, ``run.py`` né ``app/``
(``jenny/security/workspace_policy.py::is_app_code_path``).

**Prova prima di salvare.** ``draft`` scrive in ``apps/.drafts/``, valida e,
se c'è un lettore, lo gira una volta sui dati veri dell'app. Poi dipende dalla
faccia del tool: quella ``core`` (un agente solo) rende subito viva la versione
che funziona; quella dell'``app-builder`` la lascia in bozza, e la pubblica
l'agente principale con ``publish`` (``AppBuildTool``, ``AppPublishTool``). Una
versione che non funziona non tocca l'app di prima (``jenny/apps/drafts.py``).

Lavora per una conversazione con l'utente, personale o di un quaderno: anche
dall'``app-builder``, che e' un subagent ma porta la sessione da cui e' partito.
Mai per il giardiniere o un cron. Da un quaderno non si cancellano app.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import current_request_session_key
from jenny.agent.tools.schema import ObjectSchema, StringSchema, tool_parameters_schema
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    current_turn_is_readonly,
    current_workspace_scope,
)

_MAX_FAILURES = 6
_FAILURE_WINDOW_S = 600
# I tentativi **falliti** recenti per app, condivisi fra istanze del tool: un
# costruttore nasce con un registro nuovo, e un contatore per istanza si
# azzerava a ogni spawn. Solo i falliti: il 07/10/2026 sul Titan contava ogni
# draft, e tre prove riuscite di un costruttore bastavano a bloccare il
# successivo per dieci minuti; Jenny ha passato quei minuti a leggere il proprio
# codice sorgente per capire quando si sbloccava. Una prova che passa, o una
# pubblicazione, azzera il conto.
_RECENT_FAILURES: dict[str, list[float]] = {}
# Una bozza ferma da tanto e' abbandonata: il costruttore che arriva riparte dal
# codice vivo. Una recente invece e' il lavoro in corso, e il costruttore dopo
# (un ritocco alla schermata) ci parte sopra: fino al 07/10/2026 la si
# cancellava sempre, e un draft col solo ``app/index.html`` buttava via
# ``app.json`` e ``run.py`` dell'app nuova che il primo aveva scritto.
_ABANDONED_DRAFT_S = 6 * 3600
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
        "polaroid, a tap opens it), optionally tied to an existing card; trash: move an app to the trash (personal chat only); "
        "set: change one value of app.json by its path; publish: make a tested new version live.",
        enum=["draft", "edit", "set", "show", "run", "place", "trash", "publish"],
    ),
    slug=StringSchema("The app's folder name: lowercase, digits and hyphens, at most 32."),
    files=ObjectSchema(
        description="For draft: {path: content}. Paths: app.json (required for a new app), "
        "run.py (the reader), AGENT.md, and app/<file> for the screen (app/index.html). "
        "On a new version you may send only the files that change.",
        additional_properties=True,
    ),
    path=StringSchema("For edit: the file to change (app.json, run.py, AGENT.md, app/…)."),
    key=StringSchema("For set: a dotted path in app.json, e.g. reader.net or reader.every."),
    value=StringSchema("For set: the new value as JSON (a list, a number, a string in quotes); "
                       "null removes the key."),
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


def _silent_screen_failure(text: str) -> str | None:
    """Cio' che nell'iframe dell'app fallisce **senza un errore**, e quindi va
    fermato qui. Erano i controlli di ``skills/app-creator/scripts/validate_app.py``,
    uno script che nessuno eseguiva: stanno dove la bozza si valida davvero."""
    import re

    if re.search(r"<form\b", text, re.IGNORECASE):
        return ("a <form> never submits in the app iframe (no allow-forms): use "
                "<button type=\"button\"> with a click handler, plus Enter on the input.")
    for fn in ("alert", "confirm", "prompt"):
        if re.search(rf"(?<![\w.$])(?:window\.)?{fn}\s*\(", text):
            return (f"{fn}() does nothing in the app iframe (no allow-modals): use a "
                    "<dialog> or kit markup.")
    if re.search(r"fetch\s*\(\s*[`'\"][^)]*?/api/apps/", text):
        return "call the app's actions with jenny.action(...), not fetch('/api/apps/…')."
    return None


def _readable(path: str, content: str) -> str:
    """``app.json`` si salva indentato: ``edit`` lavora per righe, e un JSON su
    una riga sola chiedeva al modello di ricopiarne un pezzo esatto."""
    if path != "app.json":
        return content
    try:
        return json.dumps(json.loads(content), ensure_ascii=False, indent=2) + "\n"
    except ValueError:
        return content


@tool_parameters(_PARAMETERS)
class AppTool(Tool):
    """Scrive, prova, ripara e posa le app."""

    _scopes = {"core"}

    # Le azioni di questa faccia del tool, e se una bozza provata diventa subito
    # l'app (la faccia ``core``, un agente solo) o aspetta ``publish``.
    _ACTIONS = frozenset({"draft", "edit", "set", "show", "run", "place", "trash", "publish"})
    _publishes = True
    _reseeds_abandoned = False

    def __init__(self, origin_session_key: str | None = None) -> None:
        # La conversazione per cui si lavora quando chi chiama e' un subagent
        # (``ToolContext.origin_session_key``): dentro il suo giro la sessione di
        # richiesta e' la sua, non quella dell'utente.
        self._origin = origin_session_key
        self._touched: set[str] = set()
        # Chi tiene il registro degli strumenti lo aggiorna qui quando un'app
        # cambia (``AgentLoop``): le azioni di un'app appena pubblicata servono
        # nello stesso turno, non dal messaggio dopo.
        self.on_apps_changed: Callable[[], Awaitable[None]] | None = None

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(origin_session_key=getattr(ctx, "origin_session_key", None))

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
            "files and runs the reader once on the app's real data: when they work the app is "
            "live at once (in the App page, with its tools) and the reply carries the values it "
            "read — check them; when they do not, nothing changes and you get the error. Any app that keeps state MUST "
            "declare a `summary`. Use the app-creator skill for the manifest format. `place` "
            "puts it on a notebook board as a card of its own (a tap opens its screen), tied by "
            "a thread to the card it belongs with: placing it in a notebook also lets that "
            "notebook's chat use it."
        )

    # -- dove ---------------------------------------------------------------

    def _session(self) -> str | None:
        """La sessione, se e' un turno dell'utente (personale o di un quaderno)."""
        from jenny.session.keys import is_personal_session_key, is_project_session_key

        key = self._origin or current_request_session_key() or ""
        if is_project_session_key(key) or is_personal_session_key(key):
            return key
        return None

    @staticmethod
    def _throttled(slug: str) -> str | None:
        """Il rifiuto, con l'ora in cui si potra' riprovare, o ``None``."""
        now = time.time()
        recent = [t for t in _RECENT_FAILURES.get(slug, []) if now - t < _FAILURE_WINDOW_S]
        _RECENT_FAILURES[slug] = recent
        if len(recent) < _MAX_FAILURES:
            return None
        free_at = time.strftime("%H:%M", time.localtime(recent[0] + _FAILURE_WINDOW_S))
        return (f"Six failed tries on `{slug}` in ten minutes: stop here and report what keeps "
                f"failing, with the last error, instead of trying again (the next try is "
                f"accepted after {free_at}).")

    @staticmethod
    def _note_try(slug: str, ok: bool) -> None:
        if ok:
            _RECENT_FAILURES.pop(slug, None)
        else:
            _RECENT_FAILURES.setdefault(slug, []).append(time.time())

    @staticmethod
    def _abandoned(d: Path) -> bool:
        """Una bozza in cui niente e' cambiato da :data:`_ABANDONED_DRAFT_S`."""
        try:
            newest = max(p.stat().st_mtime for p in [d, *d.rglob("*")])
        except (OSError, ValueError):
            return True
        return time.time() - newest > _ABANDONED_DRAFT_S

    async def execute(self, action: str = "", slug: str = "", **kw: Any) -> str:
        from jenny.apps.manifest import SLUG_RE
        from jenny.config.paths import get_workspace_path

        session = self._session()
        if session is None:
            return ("Not from here: apps are written only in a conversation with the user "
                    "(the personal chat or a notebook's chat). Nothing was written.")
        if not isinstance(slug, str) or not SLUG_RE.match(slug) or len(slug) > 32:
            return "`slug` must be lowercase letters, digits and hyphens, at most 32."
        if action not in self._ACTIONS:
            return f"Unknown action here: use {', '.join(sorted(self._ACTIONS))}."
        workspace = get_workspace_path()
        if action in ("draft", "edit", "set", "place", "trash", "publish") and \
                current_turn_is_readonly():
            return READONLY_TOOL_REFUSAL
        if action in ("draft", "edit", "set"):
            if refusal := self._throttled(slug):
                return refusal
            if self._reseeds_abandoned and slug not in self._touched:
                # Il primo tocco di un costruttore riparte dal codice vivo solo se
                # la bozza e' abbandonata: una recente e' il lavoro che continua.
                self._touched.add(slug)
                from jenny.apps import drafts

                stale = drafts.draft_dir(workspace, slug)
                if stale.exists() and self._abandoned(stale):
                    shutil.rmtree(stale)
        if action == "set":
            return await self._set(workspace, slug, kw)
        if action == "publish":
            return await self._publish(workspace, slug)
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
                if path.endswith((".html", ".js")) and (problem := _silent_screen_failure(content)):
                    return f"{path}: {problem}"
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
        from jenny.apps import drafts

        has_manifest = (drafts.draft_dir(workspace, slug) / "app.json").is_file() or (
            drafts.app_dir(workspace, slug) / "app.json").is_file()
        if "app.json" not in files and not has_manifest:
            return (f"`{slug}` is a new app: send app.json in this call too (with run.py and "
                    "the screen when it has them). Nothing was written.")
        d = self._seed_draft(workspace, slug)
        for path, content in files.items():
            target = d / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_readable(path, content), encoding="utf-8")
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
        reply, ok = await self._try_draft(workspace, slug)
        self._note_try(slug, ok)
        if ok and self._publishes:
            from jenny.apps import drafts

            draft = drafts.load_draft(workspace, slug)
            if draft is not None and draft.app.manifest is not None:
                existed = (drafts.app_dir(workspace, slug) / "app.json").is_file()
                return await self._promote(workspace, slug, draft.app.manifest, existed)
        return reply

    async def _try_draft(self, workspace: Path, slug: str) -> tuple[str, bool]:
        """La prova della bozza: la risposta per il costruttore, e se e' passata."""
        from jenny.apps import drafts
        from jenny.apps.reader import run_reader

        draft = drafts.load_draft(workspace, slug)
        if draft is None:
            # Una bozza senza ``app.json`` (prima un ``assert``, che arrivava al
            # modello come «AssertionError:» e basta).
            return (f"`{slug}` has no app.json in its new version: write it with action=draft "
                    "(app.json, plus run.py and the screen when it has them)."), False
        # Un lettore che non si era fermato resta in quarantena finche' qualcuno
        # non lo modifica, come promette il suo messaggio («edit it to retry»):
        # questo e' quel qualcuno. Due chiavi, perche' bozza e app vera girano
        # sotto nomi diversi (``reader.execute_reader``).
        from jenny.board import micro as mc

        mc._stuck.discard(f"apps/{drafts.DRAFTS_DIRNAME}/{slug}")
        mc._stuck.discard(f"apps/apps/{slug}")
        app = draft.app
        if app.broken or app.manifest is None:
            return (f"`{slug}` is not valid: {app.error}\nFix it with action=edit (or draft "
                    "again); nothing changed until it is valid."), False
        m = app.manifest
        stateful = m.reader is not None or any(
            a.kind == "storage" and a.op in ("append", "set", "update", "delete")
            for a in m.actions)
        if stateful and m.section is None:
            return ("Not ready: this app keeps state, so it must declare a `summary` — how it "
                    "shows on its card (see the app-creator skill). Add it with "
                    "action=edit."), False
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
                        "`net`), ctx.data(collection), ctx.wanted. Fix it with action=edit."), False
        return self._tested_reply(workspace, slug, draft.app), True

    def _tested_reply(self, workspace: Path, slug: str, app: Any) -> str:
        """Cosa dice al costruttore una bozza che ha passato la prova: i valori letti."""
        m = app.manifest
        reading = self._latest_reading(workspace, app) if m.reader is not None else ""
        return (f"`{slug}`: the new version passed its checks"
                + (f" and its test run on the app's real data read — {reading}" if reading else "")
                + ". It is **not live yet**: the main agent publishes it. Check the values against "
                "what the app should show; when they are right, stop and report what you built "
                "and what it read.")

    async def _promote(self, workspace: Path, slug: str, m: Any, existed: bool) -> str:
        """La bozza provata diventa l'app, e il primo giro sui dati veri conta."""
        from jenny.apps import drafts
        from jenny.apps.manifest import find_app
        from jenny.apps.reader import run_reader

        try:
            drafts.promote_draft(workspace, slug)
        except OSError as exc:
            # Prima risaliva grezzo («Error: [(…/app, …/app, Permission denied)]»):
            # Jenny lo prendeva per i permessi dei file dell'utente e gli
            # chiedeva di sistemarli o di cancellare l'app.
            from loguru import logger

            logger.opt(exception=True).warning("apps: promoting the draft of {} failed", slug)
            return (f"`{slug}`: the new version passed its test run, but copying it into the "
                    f"{'live app' if existed else 'apps folder'} failed: {exc}. This is a fault "
                    "in Jenny, not in the user's files: say so, and do not ask them to change "
                    "permissions or delete the app. Some of the new files may already be in "
                    "place. The new version stays in the draft, and publishing it again retries.")
        self._note_try(slug, True)
        if self.on_apps_changed is not None:
            try:
                await self.on_apps_changed()
            except Exception:  # noqa: BLE001 — gli strumenti arrivano al turno dopo
                from loguru import logger

                logger.opt(exception=True).warning("apps: tools of {} not synced", slug)
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
                # La versione nuova legge sui dati veri. L'esito conta: fino al
                # 07/10/2026 si ignorava, e un primo giro fallito sull'app vera
                # tornava come «updated» con l'ultima lettura della versione vecchia.
                state = await run_reader(workspace, slug, force=True)
                if not state or state.get("status") != "ok":
                    error = (state or {}).get("lastError", "the reader did not run")
                    return (f"`{slug}` is published, but its first reading on the real data "
                            f"failed:\n\n{error}\n\nThe card still shows the previous value. "
                            "Fix the reader before telling the user it works.")
            reading = self._latest_reading(workspace, live)
        what = "updated" if existed else "live"
        section = m.section
        strip = (f" On a board it is a strip {section.size} ("
                 + ", ".join(b["kind"] for b in section.blocks) + ").") \
            if section is not None and section.blocks else ""
        # I fatti, non un copione: il «Tell them in one or two lines what it
        # does» spingeva a dire «funziona» anche quando la stessa riga diceva
        # «prezzi non letti».
        return (f"`{slug}` is {what}{' — ' + reading if reading else ''}.{strip} It is in the "
                "user's App page, with its tools."
                + ("" if existed else " If it is about a notebook's subject, it can go on that "
                   "board with action=place.")
                + (" Check the values above before saying it works." if reading else ""))

    async def _publish(self, workspace: Path, slug: str) -> str:
        """Pubblica la bozza che un ``app-builder`` ha scritto e provato."""
        from jenny.apps import drafts
        from jenny.apps.reader import read_state

        draft = drafts.load_draft(workspace, slug)
        if draft is None:
            return (f"No new version of `{slug}` to publish: an app-builder writes and tests it "
                    "first (spawn agent_type=app-builder).")
        if draft.app.broken or draft.app.manifest is None:
            return (f"The new version of `{slug}` is not valid ({draft.app.error}): send it back "
                    "to its app-builder with subagent_send.")
        m = draft.app.manifest
        if m.reader is not None:
            state = read_state(draft.dir)
            if state.get("status") != "ok":
                why = state.get("lastError") or "it never ran"
                return (f"The new version of `{slug}` has not passed its test run ({why}): send "
                        "it back to its app-builder with subagent_send.")
        existed = (drafts.app_dir(workspace, slug) / "app.json").is_file()
        return await self._promote(workspace, slug, m, existed)

    async def _set(self, workspace: Path, slug: str, kw: dict[str, Any]) -> str:
        """Un valore di ``app.json`` per percorso, senza riscrivere il file a memoria.

        Il 07/10/2026, per cambiare due host di ``net``, un modello piccolo
        riscriveva tutto ``app.json`` con le sue undici azioni dentro una stringa
        JSON; ``edit`` invece voleva il testo esatto di un JSON su una riga sola.
        """
        path, value = kw.get("key"), kw.get("value")
        if not isinstance(path, str) or not path.strip():
            return "set needs `key`, a dotted path in app.json (e.g. reader.net), and `value`."
        from jenny.apps import drafts

        if not drafts.draft_dir(workspace, slug).exists() and not drafts.app_dir(
                workspace, slug).is_dir():
            return f"No app `{slug}`: create it with action=draft."
        d = self._seed_draft(workspace, slug)
        try:
            data = json.loads((d / "app.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return f"app.json cannot be read ({exc}): rewrite it with action=draft."
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        node: Any = data
        keys = path.strip().split(".")
        for key in keys[:-1]:
            if isinstance(node, list) and key.isdigit() and int(key) < len(node):
                node = node[int(key)]
            elif isinstance(node, dict):
                node = node.setdefault(key, {})
            else:
                return f"`{path}`: `{key}` is not an object in app.json."
        last = keys[-1]
        if isinstance(node, list) and last.isdigit() and int(last) < len(node):
            node[int(last)] = value
        elif isinstance(node, dict):
            if value is None:
                node.pop(last, None)
            else:
                node[last] = value
        else:
            return f"`{path}`: cannot set `{last}` there."
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        problem = self._check_files({"app.json": text})
        if problem:
            return f"Not written: {problem}"
        (d / "app.json").write_text(text, encoding="utf-8")
        return await self._try(workspace, slug)

    def _latest_reading(self, workspace: Path, app: Any) -> str:
        """L'ultima lettura come la mostra la scheda.

        Un lettore a serie (un sensore per posa) mostra l'ultima serie letta,
        coi parametri che il riassunto vuole: senza, ``summary_view`` filtra su
        valori vuoti e risponde «no value yet» anche dopo un giro riuscito.
        """
        from jenny.apps.reader import read_readings
        from jenny.apps.summary_view import summary_view

        section = app.manifest.section if app.manifest is not None else None
        last = (read_readings(app.dir) or [{}])[-1]
        params = {name: last[name] for name in (section.params if section else {})
                  if name in last}
        return self._reading(summary_view(workspace, app, params))

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
        """Il codice e lo stato dell'app: della **bozza**, quando ce n'e' una.

        Fino al 07/10/2026 mostrava sempre l'app viva, e chiamava «unfinished» anche
        una bozza gia' provata: sul Titan un costruttore ha fatto `set` dell'allarme
        a 175, ha guardato con `show`, ha visto 130 e ha rifatto il lavoro finche'
        il limite dei tentativi l'ha fermato.
        """
        from jenny.apps import drafts
        from jenny.apps.manifest import find_app
        from jenny.apps.reader import read_state

        live = find_app(workspace, slug)
        draft = drafts.load_draft(workspace, slug)
        if draft is None and drafts.draft_dir(workspace, slug).is_dir() and live is None:
            return (f"`{slug}` is not an app yet: its new version has no app.json. Write it "
                    "with action=draft (app.json, plus run.py and the screen).")
        if live is None and draft is None:
            return f"No app `{slug}`."
        out: list[str] = []
        if draft is not None:
            out.append(f"`{slug}` — {self._draft_state(workspace, slug, draft)}")
            if live is not None and live.manifest is not None and live.manifest.reader:
                out.append(f"The live version still shows: "
                           f"{self._latest_reading(workspace, live)}.")
            app = draft.app
        else:
            assert live is not None
            app = live
            out.append(f"`{slug}` — live" + (f" (broken: {app.error})" if app.broken else ""))
        from jenny.board.micro import redact

        for name in ("app.json", "run.py"):
            path = app.dir / name
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                out.append(f"--- {name}\n{redact(text) if name == 'run.py' else text}")
        if draft is None and app.manifest is not None and app.manifest.reader is not None:
            out.append("--- reader state\n" + json.dumps(read_state(app.dir), ensure_ascii=False))
        screen = sorted(p.relative_to(app.dir).as_posix() for p in (app.dir / "app").rglob("*")
                        if p.is_file()) if (app.dir / "app").is_dir() else []
        if screen:
            out.append("--- screen files: " + ", ".join(screen))
        return "\n\n".join(out)

    def _draft_state(self, workspace: Path, slug: str, draft: Any) -> str:
        """A che punto e' una versione nuova, detto per chi deve decidere cosa fare."""
        from jenny.apps import drafts
        from jenny.apps.reader import read_state

        app = draft.app
        exists = (drafts.app_dir(workspace, slug) / "app.json").is_file()
        what = "a new version of a live app" if exists else "a new app"
        if app.broken or app.manifest is None:
            return f"{what}, not valid: {app.error}. Fix it with action=edit or draft."
        publish = ("action=publish makes it live" if "publish" in self._ACTIONS
                   else "the main agent publishes it")
        if app.manifest.reader is None:
            return f"{what}, valid and not live yet: {publish}."
        state = read_state(draft.dir)
        if state.get("status") == "ok":
            return (f"{what}, tested and not live yet: its test run read "
                    f"{self._latest_reading(workspace, app)}; {publish}.")
        why = state.get("lastError") or "it has not run yet"
        return f"{what}, not live: its test run failed ({why}). Fix it with action=edit."

    async def _run(self, workspace: Path, slug: str) -> str:
        from jenny.apps.manifest import find_app
        from jenny.apps.reader import run_reader

        app = find_app(workspace, slug)
        if app is None or app.manifest is None or app.manifest.reader is None:
            return f"`{slug}` has no reader."
        state = await run_reader(workspace, slug, force=True)
        if state and state.get("status") == "ok":
            return "Read now — " + self._latest_reading(workspace, app)
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


_BUILD_PARAMETERS = tool_parameters_schema(
    action=StringSchema(
        "draft: write files of the app's new version (app.json, run.py, AGENT.md, app/<file>); "
        "send one or two files per call — a big call gets cut. edit: replace one exact string "
        "in one file. set: change one value of app.json by its dotted path. Each of these "
        "validates the version and runs its reader once on the app's real data. show: the "
        "app's code and state. run: read the live app's reader now.",
        enum=["draft", "edit", "set", "show", "run"],
    ),
    slug=StringSchema("The app's folder name: lowercase, digits and hyphens, at most 32."),
    files=ObjectSchema(
        description="For draft: {path: content}. app.json is required for a new app; after "
        "that send only the files that change.",
        additional_properties=True,
    ),
    path=StringSchema("For edit: the file to change (app.json, run.py, AGENT.md, app/…)."),
    old=StringSchema("For edit: the exact text to replace (must occur once)."),
    new=StringSchema("For edit: the replacement text."),
    key=StringSchema("For set: a dotted path in app.json, e.g. reader.net or reader.every."),
    value=StringSchema("For set: the new value as JSON (a list, a number, a string in quotes); "
                       "null removes the key."),
    required=["action", "slug"],
)


@tool_parameters(_BUILD_PARAMETERS)
class AppBuildTool(AppTool):
    """La faccia del costruttore: scrive e prova una versione nuova, non la pubblica."""

    _scopes = {"apps"}
    _ACTIONS = frozenset({"draft", "edit", "set", "show", "run"})
    _publishes = False
    _reseeds_abandoned = True

    @property
    def description(self) -> str:
        return (
            "Write and test a new version of one of the user's apps (workspace/apps/<slug>/: "
            "app.json, run.py for the reader, app/index.html for the screen). Every write is "
            "validated and the reader runs once on the app's real data; the reply shows the "
            "values it read. Your version is not live until the main agent publishes it: "
            "check the values, then report."
        )


_PUBLISH_PARAMETERS = tool_parameters_schema(
    action=StringSchema(
        "publish: make live the new version an app-builder wrote and tested. show: the app's "
        "code and state. run: read the app's reader now. place: put the app on a notebook "
        "board as a card of its own, optionally tied to an existing card. trash: move an app "
        "to the trash (personal chat only).",
        enum=["publish", "show", "run", "place", "trash"],
    ),
    slug=StringSchema("The app's folder name: lowercase, digits and hyphens, at most 32."),
    notebook=StringSchema("For place from the personal chat: the notebook name."),
    card=StringSchema("For place (optional): an existing card (title or id) the app belongs "
                      "with — the app's card is tied to it by a thread."),
    params=ObjectSchema(
        description="For place: the summary's params for this placement (e.g. which "
        "sensor), {name: value}.",
        additional_properties=True,
    ),
    required=["action", "slug"],
)


@tool_parameters(_PUBLISH_PARAMETERS)
class AppPublishTool(AppTool):
    """La faccia dell'orchestratore: pubblica, legge, posa. Le app le scrive l'``app-builder``.

    Fino al 07/10/2026 l'orchestratore le scriveva da se': un modello piccolo
    ricostruiva a memoria file interi dentro argomenti JSON, e ognuno restava
    nella conversazione del quaderno per sempre (64 k caratteri nella chat ETF).
    """

    _scopes = {"orchestrator"}
    _ACTIONS = frozenset({"publish", "show", "run", "place", "trash"})

    @property
    def description(self) -> str:
        return (
            "Publish, read and place the user's apps. To create or change an app, spawn an "
            "app-builder subagent (agent_type=app-builder) with what the user wants; it "
            "writes and tests a new version and reports the values it read. Then `publish` "
            "makes it live (the reply shows its first real reading: check it before saying it "
            "works), and `place` puts it on a notebook board as a card of its own."
        )


TOOLS = [AppTool, AppBuildTool, AppPublishTool]
