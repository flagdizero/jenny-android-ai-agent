"""Il tool ``micro_app``: Jenny scrive, prova e ripara una micro app del quaderno.

Una micro app è una sezione del cassetto con un piccolo ``run.py`` (v.
``jenny.board.micro``). Questo tool è **l'unica strada** per scriverne il codice:
la cartella ``board/`` non la scrive nessuno strumento generico.

**Prova prima di salvare.** ``draft`` scrive una bozza e la esegue subito: torna
il valore vero letto (con la sua provenienza) o l'errore. Se la prova riesce, la
micro app compare nel cassetto **in prova**, e la tiene l'utente con [Tienilo]:
è quel tocco ad approvarne il codice. Una riparazione (``update``) che lascia
uguale ciò che l'utente vede — campi, unità, host, skill — gira subito (decisione
dell'utente del 05/10/2026); se lo cambia, torna in prova.

Funziona solo nella chat di un quaderno con la bacheca, e solo nei turni
dell'utente: mai dal giardiniere né da un cron.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import current_request_session_key
from jenny.agent.tools.schema import (
    ArraySchema,
    IntegerSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    current_turn_is_readonly,
    current_workspace_scope,
)

# Quante prove per micro app in pochi minuti, prima di fermarsi a parlarne.
_MAX_DRAFTS = 3
_DRAFT_WINDOW_S = 600

_NOT_HERE = (
    "Not here: a micro app lives in a notebook's board, and this conversation is not a "
    "notebook with a board. Nothing was written. Tell the user to open the notebook and ask "
    "there; do not build it another way."
)

_PARAMETERS = tool_parameters_schema(
    action=StringSchema(
        "draft: write a new micro app (or a new version) and run it once now; update: change "
        "an existing one (a fix) and run it; run: read it now; show: its spec, state, last "
        "error and code.",
        enum=["draft", "update", "run", "show"],
    ),
    card=StringSchema("The card (title or id) whose drawer it goes in. Needed for draft."),
    id=StringSchema("The micro app id (`m-...`). Omit on the first draft."),
    title=StringSchema("Short name shown in the drawer, e.g. 'Acerello'."),
    code=StringSchema(
        "run.py: `def run(ctx):` returning a dict with exactly the declared fields, plus an "
        "optional `_source` text saying where the value came from ('hub WaterBot, Acerello "
        "seen 14:01'). ctx.now (ISO), ctx.prev (last reading or None), ctx.http.get_json / "
        "get_text / post_json (only to hosts in `net`). It can read files but never write. "
        "At most 6 KB."
    ),
    fields=ObjectSchema(
        description="What run() returns: {name: {label, type: number|bool|text|rows, unit?, "
        "min?, max?}}, at most 8. A rows field is a list of dicts with declared `columns` "
        "({col: {label, type: number|bool|text, unit?}}, at most 4 columns, 20 rows). A missing "
        "or out-of-range value is an error, never a value.",
        additional_properties=True,
    ),
    view=StringSchema(
        "How the drawer draws it — no HTML, the drawer has five shapes: value (one big number "
        "with trend and alarm, the default), chips (several values side by side), progress (a "
        "number towards its `max`, e.g. words written), list (a rows field: first column is "
        "the title, the others the line below — new listings, news), table (a rows field as a "
        "small table — prices). For list and table the drawer also counts the rows.",
        enum=["value", "chips", "progress", "list", "table"],
    ),
    primary=StringSchema("The field shown big in the drawer (value and progress)."),
    every=IntegerSchema(description="Minutes between readings: at least 15, default 60.",
                        minimum=15),
    alarm=ObjectSchema(
        description="Optional: {field, op: < <= > >= == !=, value} — when the drawer turns red.",
        additional_properties=True,
    ),
    net=ArraySchema(StringSchema(""), description="Hostnames ctx.http may reach."),
    uses=ArraySchema(
        StringSchema(""),
        description="At most one installed skill whose scripts run() imports (e.g. "
        "['waterbot-imh']): reuse it instead of re-implementing an API, and its credentials "
        "stay where they are.",
    ),
    required=["action"],
)


@tool_parameters(_PARAMETERS)
class MicroAppTool(Tool):
    """Scrive, prova e ripara le micro app del quaderno."""

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
        return "micro_app"

    @property
    def description(self) -> str:
        return (
            "A micro app is a small Python program attached to a card's drawer in this "
            "notebook: it reads one thing (a sensor, a price, a count) every N minutes, with no "
            "model turn, and the drawer shows its value, trend and alarms. Use it when the user "
            "wants to SEE a value in a drawer — not a full Jenny App. `draft` writes it and "
            "runs it once: tell the user the real value and where it came from; it appears in "
            "the drawer as a trial and the user keeps it with the Keep button there. Reuse an "
            "installed skill via `uses` when one already talks to the device. Only inside a "
            "notebook with a board."
        )

    # -- dove ---------------------------------------------------------------

    @staticmethod
    def _where() -> tuple[Path, Path] | str:
        from jenny.board.store import read_board
        from jenny.utils.wiki_paths import is_wiki_root

        key = current_request_session_key() or ""
        if not key.startswith("project:"):
            return _NOT_HERE
        scope = current_workspace_scope()
        root = scope.write_root() if scope is not None else None
        if root is None or not is_wiki_root(root):
            return _NOT_HERE
        board = read_board(root)
        if board is None or not board["cards"]:
            return _NOT_HERE
        from jenny.config.paths import get_workspace_path

        return get_workspace_path(), root

    def _throttled(self, micro_id: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self._drafts.get(micro_id, []) if now - t < _DRAFT_WINDOW_S]
        if len(recent) >= _MAX_DRAFTS:
            self._drafts[micro_id] = recent
            return True
        self._drafts[micro_id] = [*recent, now]
        return False

    # -- azioni -------------------------------------------------------------

    async def execute(self, action: str = "", **kw: Any) -> str:
        where = self._where()
        if isinstance(where, str):
            return where
        workspace, root = where
        if action in ("draft", "update") and current_turn_is_readonly():
            return READONLY_TOOL_REFUSAL
        if action == "draft":
            return await self._draft(workspace, root, kw, update=False)
        if action == "update":
            return await self._draft(workspace, root, kw, update=True)
        if action == "run":
            return await self._run(workspace, root, str(kw.get("id") or ""))
        if action == "show":
            return self._show(workspace, root, str(kw.get("id") or ""))
        return "Unknown action: use draft, update, run or show."

    async def _draft(self, workspace: Path, root: Path, kw: dict[str, Any], *,
                     update: bool) -> str:
        from jenny.board import micro as mc
        from jenny.board import model
        from jenny.board.store import read_board

        board = read_board(root)
        assert board is not None
        micro_id = str(kw.get("id") or "").strip()
        live = mc.load_micro(root, micro_id) if micro_id else None
        draft = mc.load_micro(root, micro_id, draft=True) if micro_id else None
        base = draft or live
        if update and base is None:
            return f"No micro app `{micro_id}` in this notebook: use action=draft to create one."
        # La scheda: quella data, o quella in cui sta gia'.
        card = None
        if kw.get("card"):
            card = mc.find_card(board, str(kw["card"]))
            if card is None:
                titles = ", ".join(f"«{c['title']}»" for c in board["cards"])
                return (f"No card {kw['card']!r} on this board (cards: {titles}). Ask the user "
                        "which drawer it goes in.")
        elif base is not None:
            found = mc.section_of(board, base.id)
            card = found[0] if found else model.card_by_id(board, base.spec.get("card", ""))
        if card is None:
            return "Say which card's drawer this goes in (`card`)."
        if not micro_id:
            taken = set(mc.list_micros(root))
            drafts = mc.micros_dir(root) / mc.DRAFTS_DIRNAME
            if drafts.is_dir():
                taken |= {p.name for p in drafts.iterdir()}
            micro_id = model.unique_id(
                "m-" + model.slugify(str(kw.get("title") or card["title"])), taken)
        if self._throttled(micro_id):
            return (f"Three tries on `{micro_id}` in a few minutes: stop here, tell the user "
                    "what is failing and ask how to go on.")
        spec = dict(base.spec) if base is not None else {}
        for key in ("title", "fields", "view", "primary", "every", "alarm", "net", "uses"):
            if kw.get(key) not in (None, "", [], {}):
                spec[key] = kw[key]
        spec["card"] = card["id"]
        code = kw.get("code") or (base.code if base is not None else "")
        try:
            new = mc.write_micro(root, micro_id, spec, str(code), draft=True)
        except mc.MicroError as err:
            return f"Not written: {err}"
        result = await mc.execute_micro(workspace, root, new)
        now = datetime.now(timezone.utc)
        mc.record_run(new, result, now=now)
        if result.status != "ok":
            return self._failed(new, result)
        same_contract = (
            live is not None and mc.is_approved(workspace, root, live)
            and mc.contract_of(new.spec) == mc.contract_of(live.spec)
        )
        if update and same_contract:
            # Una riparazione che lascia uguale cio' che l'utente vede: gira subito.
            promoted = mc.keep(workspace, root, micro_id)
            self._since(root, card, new.spec["title"])
            return (f"Fixed and running again: {self._reading(new, result)}. It shows the "
                    f"same fields as before, so it is live already in «{card['title']}» — "
                    "tell the user in one line what you fixed."
                    if promoted else "Fixed, but the micro app could not be promoted.")
        mc.set_section(root, card["id"], micro_id, pending=True)
        what = self._reach(new)
        return (
            f"Trial run OK — {self._reading(new, result)}. `{micro_id}` is now in the drawer "
            f"of «{card['title']}» as a trial; it runs every {new.spec['every']} minutes once "
            f"the user taps Keep there. Tell them the value, where it came from{what}, and "
            "that they keep it from the drawer."
        )

    @staticmethod
    def _reading(micro: Any, result: Any) -> str:
        values = result.values or {}
        parts = []
        for name, f in micro.spec["fields"].items():
            value = values.get(name)
            if isinstance(value, list):
                first = ", ".join(str(v) for v in (value[0].values() if value else []))
                parts.append(f"{f['label']}: {len(value)} rows" + (f" (first: {first})"
                                                                   if first else ""))
                continue
            parts.append(f"{f['label']} {value}{f.get('unit') or ''}")
        source = values.get("_source")
        return "; ".join(parts) + (f" (source: {source})" if source else "")

    @staticmethod
    def _reach(micro: Any) -> str:
        bits = []
        if micro.spec["uses"]:
            bits.append(f"it uses the skill {micro.spec['uses'][0]}, which goes on the network "
                        "by itself")
        if micro.spec["net"]:
            bits.append("it reaches " + ", ".join(micro.spec["net"]))
        return (" — " + "; ".join(bits)) if bits else ""

    @staticmethod
    def _failed(micro: Any, result: Any) -> str:
        head = {
            "unreachable": "The network or the host did not answer (that is not a code bug)",
            "quarantined": "The run never stopped and was quarantined",
        }.get(result.status, "The trial run failed")
        return (
            f"{head}; nothing was put in the drawer. `{micro.id}` is saved as a draft.\n\n"
            f"{result.error}\n\n"
            "Contract: `def run(ctx):` returns a dict with exactly the declared fields "
            f"({', '.join(micro.spec['fields'])}), right types, no None; optional `_source`. "
            "Fix it with action=update and the same id."
        )

    def _since(self, root: Path, card: dict[str, Any], title: str) -> None:
        from jenny.board import model
        from jenny.board.store import mutate_board

        def apply(board: dict[str, Any]) -> None:
            model.add_since(board, "micro_fixed", card=card["id"], title=card["title"],
                            app=title)

        mutate_board(root, apply, actor=model.JENNY)

    async def _run(self, workspace: Path, root: Path, micro_id: str) -> str:
        from jenny.board import micro as mc

        micro = mc.load_micro(root, micro_id)
        if micro is None:
            return f"No live micro app `{micro_id}` (a trial is read with action=draft)."
        if not mc.is_approved(workspace, root, micro):
            return "Its code is not approved: the user has to Keep it in the drawer first."
        result = await mc.execute_micro(workspace, root, micro)
        mc.record_run(micro, result, now=datetime.now(timezone.utc))
        if result.status == "ok":
            return f"Read now — {self._reading(micro, result)}."
        return self._failed(micro, result)

    def _show(self, workspace: Path, root: Path, micro_id: str) -> str:
        from jenny.board import micro as mc

        micro = mc.load_micro(root, micro_id) or mc.load_micro(root, micro_id, draft=True)
        if micro is None:
            ids = ", ".join(mc.list_micros(root)) or "none"
            return f"No micro app `{micro_id}` (in this notebook: {ids})."
        state = mc.read_state(micro.dir)
        return (
            f"`{micro.id}` ({'trial' if micro.draft else 'live'}, "
            f"{'approved' if mc.is_approved(workspace, root, micro) else 'not approved'})\n"
            f"spec: {json.dumps(micro.spec, ensure_ascii=False)}\n"
            f"state: {json.dumps(state, ensure_ascii=False)}\n"
            f"file: {micro.dir.relative_to(root).as_posix()}/run.py\n\n"
            f"```python\n{micro.code}\n```"
        )


TOOLS = [MicroAppTool]
