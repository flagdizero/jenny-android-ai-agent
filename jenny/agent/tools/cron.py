"""Cron tool for scheduling reminders and tasks."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import ContextAware, RequestContext
from jenny.agent.tools.schema import (
    IntegerSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from jenny.cron.purposes import system_job_purpose
from jenny.cron.service import CronService
from jenny.cron.types import CronJob, CronJobState, CronSchedule, job_display_name
from jenny.security.subagent_policy import current_turn_taint
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    current_turn_is_readonly,
)
from jenny.session.keys import PROJECT_SESSION_PREFIX, is_project_session_key
from jenny.utils.helpers import safe_zoneinfo, validate_timezone_name

# Modi accettati da action='add'. Vive qui e non in cron/types.py perché è la
# lista che il tool valida e mostra all'LLM; il tipo canonico resta CronPayload.
_JOB_MODES = ("reminder", "monitor")
_DEFAULT_JOB_MODE = "reminder"

# Il «dove» di un job decide solo il recinto e la chat in cui consegna, non come
# funziona (decisione del 31/10/2026): un job per un quaderno si crea da
# qualunque chat, e un quaderno senza bacheca lo accetta sul quaderno intero.
_NOTEBOOK_NOTE = (
    "A job belongs to the personal chat, or to a notebook (`notebook`; in a notebook chat it "
    "is that notebook by default) and then to the card it is about (`card`), or to the whole "
    "notebook when `card` is empty; you decide, do not ask. A notebook job runs inside that "
    "notebook and delivers in its chat, wherever it was asked."
)

_CRON_PARAMETERS = tool_parameters_schema(
    notebook=StringSchema(
        "The notebook this job belongs to, by name. In a notebook chat it defaults to that "
        "notebook; leave it empty in the personal chat for a personal job."
    ),
    card=StringSchema(
        "Only for a notebook job: the card (title or id) this job is about — it hangs from "
        "that card on the board. Leave it empty when the job concerns the whole notebook, "
        "several cards, or nothing on the board. Decide; do not ask the user."
    ),
    action=StringSchema(
        "Action to perform", enum=["add", "list", "remove", "pause", "resume"],
    ),
    name=StringSchema(
        "Short human label for the job, in the user's language, as the user would name it "
        "(e.g. 'Annaffiare le piante', 'Weekly report'). It is the title of the phone "
        "notification, so never put the instruction from `message` here. Omit it and the job "
        "has no name."
    ),
    message=StringSchema(
        "REQUIRED when action='add'. Instruction for the agent to execute when the job triggers "
        "(e.g., 'Send a reminder at 9am' or 'Check system status and report'). "
        "Not used for the other actions."
    ),
    every_seconds=IntegerSchema(
        description="Interval in seconds (for recurring tasks)", minimum=1
    ),
    cron_expr=StringSchema("Cron expression like '0 9 * * *' (for scheduled tasks)"),
    tz=StringSchema(
        "Optional IANA timezone for cron expressions (e.g. 'America/Vancouver'). "
        "When omitted with cron_expr, the tool's default timezone applies."
    ),
    date=StringSchema(
        "One-time job: the day, 'YYYY-MM-DD', or just the month, 'YYYY-MM' ('around March'). "
        "Without `time` a day rings at 09:00 and a month at 09:00 on the 1st, and the user "
        "sees them as a day or a month. For 'in 20 minutes' or 'tonight at 9', compute the "
        "day from the current time and give `time` too."
    ),
    time=StringSchema(
        "Only with a `date` day, and only when the user said a time (or a delay like 'in 20 "
        "minutes'): 'HH:MM', 24-hour, in the tool's default timezone. Omit it when the user "
        "named only a day: never pick an hour for them."
    ),
    condition=ObjectSchema(
        {
            "app": StringSchema("The mini app's slug."),
            "params": ObjectSchema(description="Which series of the app (e.g. which sensor).",
                                   additional_properties=True),
            "field": StringSchema("The field of the app's data to watch."),
            "op": StringSchema("Comparison.", enum=["<", "<=", ">", ">=", "==", "!="]),
            "value": {"type": ["number", "string"], "description": "The threshold."},
        },
        description=(
            "Only with every_seconds or cron_expr: a filter on a mini app's latest value. At "
            "each run the app is read first, and the job speaks only when the condition has "
            "just become true (once, until it goes back to false). Use the cadence the user "
            "said; if they did not say one, pick the widest that does the job and tell them "
            "which in your reply."
        ),
        required=["app", "field", "op", "value"],
    ),
    why=StringSchema(
        "Only for a notebook job: the wiki page (path under wiki/) that explains why this "
        "matters. The job reads it when it fires, to say what and why."
    ),
    job_id=StringSchema(
        "REQUIRED when action='remove', action='pause' or action='resume'. Job ID (obtain via "
        "action='list')."
    ),
    mode=StringSchema(
        "Optional for action='add'. 'reminder' (default) always messages the user when it fires; "
        "'monitor' runs silently and speaks only when the check finds something worth reporting. "
        "Use 'monitor' whenever the request is CONDITIONAL — 'only tell me if...', 'warn me when...', "
        "'let me know if it drops below...': staying silent is then the expected outcome of most runs, "
        "and a 'reminder' would post filler like 'All clear.' every single time. "
        "'monitor' requires every_seconds or cron_expr; it cannot be used with date.",
        enum=["reminder", "monitor"],
    ),
    required=["action"],
    description=(
        "Action-specific parameters: add requires a non-empty message plus one schedule "
        "(every_seconds, cron_expr, or date with an optional time); remove, pause and resume "
        "require job_id; "
        "list only needs action. "
        "Per-action requirements are enforced at runtime (see field descriptions) so the "
        "top-level schema stays compatible with providers (e.g. OpenAI Codex/Responses) that "
        "reject oneOf/anyOf/allOf/enum/not at the root of function parameters."
    ),
)


@tool_parameters(_CRON_PARAMETERS)
class CronTool(Tool, ContextAware):
    """Tool to schedule reminders and recurring tasks."""

    _scopes = {"core", "orchestrator"}

    def __init__(self, cron_service: CronService, default_timezone: str = "UTC"):
        self._cron = cron_service
        self._default_timezone = default_timezone
        self._session_key: ContextVar[str] = ContextVar("cron_session_key", default="")
        self._origin_channel: ContextVar[str] = ContextVar("cron_origin_channel", default="")
        self._origin_chat_id: ContextVar[str] = ContextVar("cron_origin_chat_id", default="")
        self._origin_metadata: ContextVar[dict[str, Any] | None] = ContextVar(
            "cron_origin_metadata",
            default=None,
        )
        self._in_cron_context: ContextVar[bool] = ContextVar("cron_in_context", default=False)

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.cron_service is not None

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(cron_service=ctx.cron_service, default_timezone=ctx.timezone)

    def set_context(self, ctx: RequestContext) -> None:
        """Set the current session context for scheduled cron job ownership.

        La chiave registrata e' **quella del turno**, senza eccezioni. Fino al
        2026-08-21 un turno sulla conversazione unica veniva registrato come
        ``f"{channel}:{chat_id}"``: il job restava "attaccato alla chat che lo
        ha creato", ma quella forma non e' una sessione — ``bound_runner`` la usa
        come chiave del turno, quindi un promemoria creato dalla WebUI girava in
        ``websocket:default``, un secondo file di sessione accanto alla
        conversazione a cui il promemoria appartiene. L'attaccamento alla chat
        vive dove e' sempre vissuto e dove serve alla consegna:
        ``origin_channel`` / ``origin_chat_id``, qui sotto.

        Una chiave di sessione esplicita del canale (thread) resta invece
        l'owner del job, come prima: quella *e'* una sessione.
        """
        self._session_key.set(ctx.session_key or "")
        self._origin_channel.set(ctx.channel or "")
        self._origin_chat_id.set(ctx.chat_id or "")
        self._origin_metadata.set(dict(ctx.metadata or {}))

    def set_cron_context(self, active: bool):
        """Mark whether the tool is executing inside a cron job callback."""
        return self._in_cron_context.set(active)

    def reset_cron_context(self, token) -> None:
        """Restore previous cron context."""
        self._in_cron_context.reset(token)

    @staticmethod
    def _validate_timezone(tz: str) -> str | None:
        # Degrada (accetta) quando il database tzdata manca del tutto.
        msg = validate_timezone_name(tz)
        return f"Error: {msg}" if msg else None

    def _display_timezone(self, schedule: CronSchedule) -> str:
        """Pick the most human-meaningful timezone for display."""
        return schedule.tz or self._default_timezone

    @staticmethod
    def _format_timestamp(ms: int, tz_name: str) -> str:
        tz = safe_zoneinfo(tz_name)
        dt = datetime.fromtimestamp(ms / 1000, tz=tz)
        return f"{dt.isoformat()} ({tz_name})"

    @property
    def name(self) -> str:
        return "cron"

    @property
    def description(self) -> str:
        return (
            "Schedule reminders and recurring tasks. Actions: add, list, remove, pause, resume. "
            "Two kinds only: one-time (date, plus time only if the user said one) and recurring "
            "(every_seconds or cron_expr). A one-time job disappears once it has fired. "
            "mode='reminder' (default) always speaks when it fires; mode='monitor' is a recurring "
            "check that runs silently and speaks only when something changed or is worth "
            "reporting (needs every_seconds or cron_expr, never date). A recurring job can carry a "
            "`condition` on a mini app's value: it is checked in code at each run, at no cost, "
            "and the job speaks only when it becomes true. Pick the widest interval that does "
            "the job. "
            f"If tz is omitted, cron expressions and a date's time use {self._default_timezone}. "
            + _NOTEBOOK_NOTE
        )

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors = super().validate_params(params)
        action = params.get("action")
        if action == "add" and not str(params.get("message") or "").strip():
            errors.append("message is required when action='add'")
        if action in ("remove", "pause", "resume") and not str(params.get("job_id") or "").strip():
            errors.append(f"job_id is required when action='{action}'")
        # Il valore ammesso lo controlla già l'enum dello schema; qui resta solo
        # la regola che lo schema non sa esprimere.
        mode = str(params.get("mode") or "").strip()
        one_time = str(params.get("date") or "").strip()
        recurring = params.get("every_seconds") or params.get("cron_expr")
        # ``at`` non c'è più: una sessione che lo usava per abitudine lo rimanda.
        # Si risponde con la forma nuova, così si corregge alla prima chiamata.
        if "at" in params:
            errors.append(
                "`at` does not exist: use date='YYYY-MM-DD', and time='HH:MM' only if the "
                "user said a time"
            )
        if str(params.get("time") or "").strip() and not one_time:
            errors.append("time needs a date (the day it belongs to)")
        if one_time and recurring:
            errors.append("date is for a one-time job: not together with every_seconds or cron_expr")
        # Un one-shot che decide di tacere non avvisa mai nessuno: la
        # combinazione non ha un comportamento sensato, quindi si rifiuta invece
        # di crearla e lasciarla scattare a vuoto.
        if mode == "monitor" and one_time:
            errors.append(
                "mode='monitor' cannot be used with date; use every_seconds or cron_expr"
            )
        if params.get("condition") and not (params.get("every_seconds") or params.get("cron_expr")):
            errors.append(
                "a condition needs every_seconds or cron_expr: the cadence it is checked at"
            )
        return errors

    async def execute(
        self,
        action: str,
        name: str | None = None,
        message: str = "",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        tz: str | None = None,
        date: str | None = None,
        time: str | None = None,
        job_id: str | None = None,
        mode: str | None = None,
        **kwargs: Any,
    ) -> str:
        in_notebook_chat = is_project_session_key(self._session_key.get())
        # Un job e' fra le cose piu' durature che Jenny possa creare: sopravvive
        # al turno, alla conversazione e al riavvio. In sola lettura non si tocca
        # niente; dalla chat di un quaderno resta la lettura dei suoi job.
        if current_turn_is_readonly() and not (action == "list" and in_notebook_chat):
            return READONLY_TOOL_REFUSAL
        notebook, root, err = self._target_notebook(kwargs.get("notebook"))
        if err:
            return err
        if action == "list":
            return self._list_jobs(notebook if in_notebook_chat else None)
        if action in ("remove", "pause", "resume"):
            job = self._cron.get_job(str(job_id or ""))
            # Dalla chat di un quaderno si toccano solo i job di quel quaderno.
            if job is None or (in_notebook_chat
                               and (job.payload.board or {}).get("notebook") != notebook):
                where = "in this notebook" if in_notebook_chat else ""
                return f"No job `{job_id}` {where} (action='list' shows them).".replace("  ", " ")
            if action == "remove":
                return await self._remove_job(job_id)
            return await self._pause_job(job, paused=action == "pause")
        if action != "add":
            return f"Unknown action: {action}"
        if self._in_cron_context.get():
            return "Error: cannot schedule new jobs from within a cron job execution"
        try:
            schedule = self._schedule(every_seconds, cron_expr, tz, date, time, mode)
        except ValueError as exc:
            return f"Error: {exc}"
        condition = kwargs.get("condition") or None
        why = str(kwargs.get("why") or "").strip() or None
        if notebook is None:
            if why:
                return "Error: `why` is a page of a notebook's wiki; it needs `notebook`."
            return self._add_personal_job(name, message, schedule, mode, condition=condition)
        return await self._add_notebook_job(notebook, root, kwargs.get("card"), name, message,
                                            schedule, mode, condition=condition, why=why)

    def _target_notebook(self, requested: Any) -> tuple[str | None, Any, str | None]:
        """Il quaderno del job: ``(nome, cartella, errore)``; tutto ``None`` = personale.

        Dalla chat di un quaderno la cartella è la radice scrivibile del turno
        (``WorkspaceScope.write_root``): lo stesso confine di ogni altra scrittura,
        e da lì non si programma per un altro quaderno. Dalla chat personale il
        quaderno si cerca per nome.
        """
        from jenny.board.cron_link import notebook_root
        from jenny.security.workspace_access import current_workspace_scope
        from jenny.utils.wiki_paths import is_wiki_root

        session = self._session_key.get()
        here = session[len(PROJECT_SESSION_PREFIX):] if is_project_session_key(session) else None
        name = str(requested or "").strip() or here
        if name is None:
            return None, None, None
        if here is not None:
            if name != here:
                return None, None, (
                    f"Not from here: this chat is the notebook «{here}». Ask in the personal "
                    f"chat or in «{name}» for a job of that notebook.")
            scope = current_workspace_scope()
            root = scope.write_root() if scope is not None else None
            if root is None or not is_wiki_root(root):
                return None, None, f"No notebook «{name}» behind this chat."
            return name, root, None
        root = notebook_root(name)
        if root is None:
            return None, None, f"No notebook «{name}»."
        return name, root, None

    def _schedule(self, every_seconds: int | None, cron_expr: str | None, tz: str | None,
                  date: Any, time: Any, mode: str | None) -> CronSchedule:
        """Il quando di un job nuovo. Solleva ``ValueError`` col testo per il modello."""
        from jenny.cron.dates import date_schedule

        if tz and not cron_expr:
            raise ValueError("tz can only be used with cron_expr")
        if tz and (err := self._validate_timezone(tz)):
            raise ValueError(err.removeprefix("Error: "))
        if every_seconds:
            return CronSchedule(kind="every", every_ms=every_seconds * 1000)
        if cron_expr:
            effective_tz = tz or self._default_timezone
            if err := self._validate_timezone(effective_tz):
                raise ValueError(err.removeprefix("Error: "))
            return CronSchedule(kind="cron", expr=cron_expr, tz=effective_tz)
        when = str(date or "").strip()
        if not when:
            raise ValueError("either every_seconds, cron_expr, or date is required")
        # La data e' solo una data: l'ora, se c'e', sta in ``time``. Un ISO
        # completo qui rimetterebbe l'ora inventata dove non si vede.
        if "T" in when or " " in when:
            raise ValueError("date is only the day (YYYY-MM-DD) or the month (YYYY-MM): "
                             "put the hour in time='HH:MM', and only if the user said one")
        # Un monitor tace quando non c'è niente da dire: se scatta una volta
        # sola, quel silenzio è definitivo e il job non serve a nulla.
        if (mode or "").strip() == "monitor":
            raise ValueError(
                "mode=\"monitor\" cannot be scheduled once (date), because a one-shot "
                "job that decides to stay silent never reports anything. Use every_seconds "
                "or cron_expr for a monitor, or mode=\"reminder\" for a one-time reminder."
            )
        if err := self._validate_timezone(self._default_timezone):
            raise ValueError(err.removeprefix("Error: "))
        return date_schedule(when, self._default_timezone, str(time or "").strip() or None)

    async def _add_notebook_job(self, notebook: str, root: Any, card_ref: Any, name: str | None,
                                message: str, schedule: CronSchedule, mode: str | None, *,
                                condition: Any, why: str | None) -> str:
        """Un job del quaderno: su una scheda o sul quaderno intero, da qualunque chat."""
        from jenny.board.cron_link import add_notebook_job
        from jenny.board.micro import find_card
        from jenny.board.model import NOTEBOOK
        from jenny.board.notify import board_changed
        from jenny.board.store import read_board

        if not message:
            return "Error: cron action='add' requires a non-empty 'message' parameter."
        job_mode = (mode or _DEFAULT_JOB_MODE).strip() or _DEFAULT_JOB_MODE
        if job_mode not in _JOB_MODES:
            return f"Error: invalid mode '{mode}'. Use \"reminder\" or \"monitor\"."
        board = read_board(root)
        ref = str(card_ref or "").strip()
        card = find_card(board, ref) if ref and board is not None else None
        if ref and card is None:
            titles = ", ".join(f"«{c['title']}»" for c in (board or {}).get("cards") or [])
            return (f"No card «{ref}» on the board of «{notebook}». Cards: {titles or 'none'}. "
                    "Use one of them, or leave `card` empty if the job is for the whole notebook.")
        origin_metadata: dict[str, Any] = {}
        if turn_taint := current_turn_taint():
            origin_metadata["_taint"] = list(turn_taint)
        try:
            job = add_notebook_job(
                self._cron, notebook, card["id"] if card else NOTEBOOK,
                name=(name or "").strip(), message=message, schedule=schedule, mode=job_mode,
                why=why, condition=condition, origin_metadata=origin_metadata,
            )
        except ValueError as exc:
            return f"Error: {exc}"
        await board_changed(notebook)
        out = self._created(job)
        where = (f"on the card «{card['title']}»" if card is not None
                 else "for the whole notebook")
        return f"{out} — in the notebook «{notebook}», {where}; it runs and delivers there."

    async def _pause_job(self, job: CronJob, *, paused: bool) -> str:
        from jenny.board.notify import board_changed

        result = self._cron.set_paused(job.id, paused)
        if job.payload.board:
            await board_changed(str(job.payload.board.get("notebook")))
        return {
            "paused": f"Paused job {job.id}.",
            "resumed": f"Resumed job {job.id}.",
            "unchanged": f"Job {job.id} was already {'paused' if paused else 'running'}.",
            "expired": f"Job {job.id} cannot resume: its time passed while it was paused.",
            "protected": f"Job {job.id} is a protected system job.",
        }.get(result, f"Job {job.id} not found")

    def _add_job(
        self,
        name: str | None,
        message: str,
        every_seconds: int | None,
        cron_expr: str | None,
        tz: str | None,
        date: str | None = None,
        mode: str | None = None,
        *,
        time: str | None = None,
        condition: Any = None,
    ) -> str:
        """Un job personale dai parametri del tool (il quando ancora da costruire)."""
        try:
            schedule = self._schedule(every_seconds, cron_expr, tz, date, time, mode)
        except ValueError as exc:
            return f"Error: {exc}"
        return self._add_personal_job(name, message, schedule, mode, condition=condition)

    def _add_personal_job(
        self,
        name: str | None,
        message: str,
        schedule: CronSchedule,
        mode: str | None = None,
        *,
        condition: Any = None,
    ) -> str:
        if not message:
            return (
                "Error: cron action='add' requires a non-empty 'message' parameter "
                "describing what to do when the job triggers "
                "(e.g. the reminder text). Retry including message=\"...\"."
            )
        session_key = self._session_key.get()
        if not session_key:
            return "Error: scheduled cron jobs must be created from a chat session"
        origin_channel = self._origin_channel.get()
        origin_chat_id = self._origin_chat_id.get()
        if not origin_channel or not origin_chat_id:
            return "Error: scheduled cron jobs must be created from a chat session"
        job_mode = (mode or _DEFAULT_JOB_MODE).strip() or _DEFAULT_JOB_MODE
        if job_mode not in _JOB_MODES:
            return (
                f"Error: invalid mode '{mode}'. Use mode=\"reminder\" for a job that always "
                "messages the user, or mode=\"monitor\" for a recurring check that stays silent "
                "unless it finds something worth reporting."
            )
        origin_metadata = dict(self._origin_metadata.get() or {})
        if turn_taint := current_turn_taint():
            # Un promemoria scritto con contenuto non fidato in vista: il suo testo
            # può venire da lì, e girerà da solo. I lavori che i suoi turni
            # avvieranno nascono contaminati (``security.subagent_policy``).
            origin_metadata["_taint"] = list(turn_taint)
        try:
            job = self._cron.add_job(
                name=(name or "").strip(),
                schedule=schedule,
                message=message,
                mode=job_mode,
                session_key=session_key,
                origin_channel=origin_channel,
                origin_chat_id=origin_chat_id,
                origin_metadata=origin_metadata,
                condition=condition,
            )
        except ValueError as exc:
            # Il rifiuto del servizio (un'espressione che non si legge o non
            # scatta mai) torna al modello come le altre risposte d'errore di
            # questo tool, da correggere e riprovare.
            return f"Error: {exc}"
        return self._created(job)

    def _created(self, job: CronJob) -> str:
        """La risposta a un ``add``: il job e **quando** suona, com'è stato detto.

        Il modello riprende questa riga nella risposta all'utente: un singolo
        senza ora detto «alle 09:00» diventerebbe un appuntamento che l'utente
        non ha mai dato. Per questo un giorno dice che l'ora non c'è.
        """
        head = f"Created job '{job.name}' (id: {job.id})" if job.name else f"Created job (id: {job.id})"
        if job.schedule.kind != "at":
            return head
        if job.schedule.precision in ("day", "month"):
            return f"{head}, {self._format_timing(job.schedule)} (no time: it rings at 09:00)"
        return f"{head}, {self._format_timing(job.schedule)}"

    def _format_timing(self, schedule: CronSchedule) -> str:
        """Format schedule as a human-readable timing string."""
        if schedule.kind == "cron":
            tz = f" ({schedule.tz})" if schedule.tz else ""
            return f"cron: {schedule.expr}{tz}"
        if schedule.kind == "every" and schedule.every_ms:
            ms = schedule.every_ms
            if ms % 3_600_000 == 0:
                return f"every {ms // 3_600_000}h"
            if ms % 60_000 == 0:
                return f"every {ms // 60_000}m"
            if ms % 1000 == 0:
                return f"every {ms // 1000}s"
            return f"every {ms}ms"
        if schedule.kind == "at" and schedule.at_ms:
            if schedule.precision in ("day", "month"):
                from jenny.cron.dates import schedule_date

                return f"once, on {schedule_date(schedule, self._default_timezone)}"
            return f"at {self._format_timestamp(schedule.at_ms, self._display_timezone(schedule))}"
        return schedule.kind

    def _format_state(self, state: CronJobState, schedule: CronSchedule) -> list[str]:
        """Format job run state as display lines."""
        lines: list[str] = []
        display_tz = self._display_timezone(schedule)
        if state.last_run_at_ms:
            info = (
                f"  Last run: {self._format_timestamp(state.last_run_at_ms, display_tz)}"
                f" — {state.last_status or 'unknown'}"
            )
            if state.last_error:
                info += f" ({state.last_error})"
            lines.append(info)
        # Un monitor rotto deve saperlo dire quando gli si chiede l'elenco:
        # ``last_status`` da solo racconta l'ultimo giro, non da quanto dura.
        if state.consecutive_could_not_check:
            note = f"  Could not check: {state.consecutive_could_not_check} consecutive run(s)"
            if state.could_not_check_since_ms:
                note += f", since {self._format_timestamp(state.could_not_check_since_ms, display_tz)}"
            if state.could_not_check_escalated:
                note += "; the user has been warned"
            lines.append(note)
        if state.pending_since_ms:
            lines.append("  The last check was handed to a helper; waiting for its result")
        if state.next_run_at_ms:
            lines.append(f"  Next run: {self._format_timestamp(state.next_run_at_ms, display_tz)}")
        return lines

    @staticmethod
    def _system_job_purpose(job: CronJob) -> str:
        """La riga con cui un job di sistema si presenta nell'elenco.

        La tabella sta in ``jenny/cron/purposes.py`` e non qui da quando i lettori
        sono due: questo elenco, che la mostra al modello, e la WebUI, che la
        mostra all'utente. Due copie divergono, e la prima a divergere e' stata
        questa — le mancava il giardiniere.

        Per ``id`` e non per ``name``: e' l'id che il container passa a
        ``register_system_job`` ed e' per id che ``retire_system_job`` toglie. I
        due coincidono per tutti e quattro i lavoratori, ma il nome e' una
        stringa che un promemoria dell'utente puo' riusare.
        """
        return system_job_purpose(job.id)

    def _list_jobs(self, notebook: str | None = None) -> str:
        """Tutti i job, o — dalla chat di un quaderno — solo quelli di *notebook*."""
        # I job spenti restano fuori (un job senza sessione), tranne quelli **in
        # pausa**: l'utente li ha fermati e torneranno. Senza, Jenny non li
        # vedrebbe e ne creerebbe un doppione.
        jobs = [
            j for j in self._cron.list_jobs(include_disabled=True)
            if (j.enabled or j.paused_at_ms is not None)
            and (notebook is None or (j.payload.board or {}).get("notebook") == notebook)
        ]
        if not jobs:
            return "No scheduled jobs in this notebook." if notebook else "No scheduled jobs."
        lines = []
        for j in jobs:
            timing = self._format_timing(j.schedule)
            # Solo i monitor si annunciano: un reminder è il caso normale e la
            # sua riga resta identica a prima.
            mode = getattr(j.payload, "mode", _DEFAULT_JOB_MODE)
            mode_note = (
                ", monitor (silent unless it has something to report)"
                if mode == "monitor"
                else ""
            )
            parts = [f"- {job_display_name(j)} (id: {j.id}, {timing}{mode_note})"]
            if j.payload.board:
                parts.append(
                    f"  In the notebook {j.payload.board.get('notebook')}, "
                    f"{_where(j.payload.board)}: it belongs there."
                )
                if j.payload.why:
                    parts.append(f"  Why: wiki/{j.payload.why}")
            if j.payload.condition:
                from jenny.cron.condition import describe

                parts.append(f"  Only when {describe(j.payload.condition)} "
                             f"(app {j.payload.condition.get('app')})")
            if j.payload.kind == "system_event":
                parts.append(f"  Purpose: {self._system_job_purpose(j)}")
                parts.append("  Protected: visible for inspection, but cannot be removed.")
            if j.paused_at_ms is not None:
                since = self._format_timestamp(j.paused_at_ms, self._display_timezone(j.schedule))
                parts.append(
                    f"  Paused since {since}: it does not run until it is resumed "
                    "(action='resume', or the workshop). Do not recreate it; "
                    "remove it only if they ask to drop it for good."
                )
            parts.extend(self._format_state(j.state, j.schedule))
            lines.append("\n".join(parts))
        return "Scheduled jobs:\n" + "\n".join(lines)

    async def _remove_job(self, job_id: str | None) -> str:
        """Toglie un job; se era di un quaderno, anche quel che ne resta sulla bacheca."""
        from jenny.board.cron_link import detach_job

        if not job_id:
            return "Error: job_id is required for remove"
        return self._remove_message(job_id, await detach_job(self._cron, job_id))

    def _remove_message(self, job_id: str, result: str) -> str:
        if result == "removed":
            return f"Removed job {job_id}"
        if result == "protected":
            job = self._cron.get_job(job_id)
            if job and job.name == "dream":
                return (
                    "Cannot remove job `dream`.\n"
                    "This is a system-managed Dream memory consolidation job for long-term memory.\n"
                    "It remains visible so you can inspect it, but it cannot be removed."
                )
            return (
                f"Cannot remove job `{job_id}`.\n"
                "This is a protected system-managed cron job."
            )
        return f"Job {job_id} not found"


def _where(link: dict[str, Any] | None) -> str:
    """Dove sta un cron del quaderno, in una frase per il modello."""
    from jenny.board.cron_link import scope_of
    from jenny.board.model import NOTEBOOK

    scope = scope_of(link or {})
    return "for the whole notebook" if scope == NOTEBOOK else f"on the card `{scope}`"


# Registrazione esplicita dei tool di questo modulo (Fase 5.3): il
# ToolLoader legge questa lista invece della reflection dir(). Un nuovo
# tool va aggiunto qui esplicitamente.
TOOLS = [CronTool]

