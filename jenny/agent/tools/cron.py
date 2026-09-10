"""Cron tool for scheduling reminders and tasks."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import ContextAware, RequestContext
from jenny.agent.tools.schema import (
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from jenny.cron.purposes import system_job_purpose
from jenny.cron.service import CronService
from jenny.cron.types import CronJob, CronJobState, CronSchedule
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    current_turn_is_readonly,
)
from jenny.session.keys import is_project_session_key
from jenny.utils.helpers import safe_zoneinfo, validate_timezone_name

# Modi accettati da action='add'. Vive qui e non in cron/types.py perché è la
# lista che il tool valida e mostra all'LLM; il tipo canonico resta CronPayload.
_JOB_MODES = ("reminder", "monitor")
_DEFAULT_JOB_MODE = "reminder"

# Dentro un progetto non si programma niente, e questo e' l'unico posto in cui
# Jenny lo viene a sapere: nessuna riga lo dice nel prompt (deciso il 22/08 —
# v. ``roadmap/progetti-passi.md``, passo 3). Il blocco di sistema si paga a
# ogni turno di ogni progetto, un promemoria capita una volta al mese, e
# scoprirlo cosi' costa una chiamata e niente da ripianificare.
#
# Il testo dice **dove si fa**, non solo che qui non si fa: e' la parte che
# Jenny ridice all'utente, ed e' la differenza fra "chiedimelo nella chat
# personale" e un "non posso" che manderebbe via a mani vuote.
_PROJECT_REFUSAL = (
    "Not here: this is a project conversation, and a project cannot schedule anything — "
    "not add, not list, not remove. Reminders, monitors and recurring checks all live in "
    "the personal chat, which is also the only place they are delivered where you will "
    "actually see them. Tell the user to switch to the personal chat (the chip at the top "
    "of the screen) and ask for it there; do not try another way of scheduling it from here."
)

_CRON_PARAMETERS = tool_parameters_schema(
    action=StringSchema("Action to perform", enum=["add", "list", "remove"]),
    name=StringSchema(
        "Optional short human-readable label for the job "
        "(e.g., 'weather-monitor', 'daily-standup'). Defaults to first 30 chars of message."
    ),
    message=StringSchema(
        "REQUIRED when action='add'. Instruction for the agent to execute when the job triggers "
        "(e.g., 'Send a reminder at 9am' or 'Check system status and report'). "
        "Not used for action='list' or action='remove'."
    ),
    every_seconds=IntegerSchema(0, description="Interval in seconds (for recurring tasks)"),
    cron_expr=StringSchema("Cron expression like '0 9 * * *' (for scheduled tasks)"),
    tz=StringSchema(
        "Optional IANA timezone for cron expressions (e.g. 'America/Vancouver'). "
        "When omitted with cron_expr, the tool's default timezone applies."
    ),
    at=StringSchema(
        "ISO datetime for one-time execution (e.g. '2026-02-12T10:30:00'). "
        "Naive values use the tool's default timezone."
    ),
    job_id=StringSchema("REQUIRED when action='remove'. Job ID to remove (obtain via action='list')."),
    mode=StringSchema(
        "Optional for action='add'. 'reminder' (default) always messages the user when it fires; "
        "'monitor' runs silently and speaks only when the check finds something worth reporting. "
        "Use 'monitor' whenever the request is CONDITIONAL — 'only tell me if...', 'warn me when...', "
        "'let me know if it drops below...': staying silent is then the expected outcome of most runs, "
        "and a 'reminder' would post filler like 'All clear.' every single time. "
        "'monitor' requires every_seconds or cron_expr; it cannot be used with at.",
        enum=["reminder", "monitor"],
    ),
    required=["action"],
    description=(
        "Action-specific parameters: add requires a non-empty message plus one schedule "
        "(every_seconds, cron_expr, or at); remove requires job_id; list only needs action. "
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
            "Schedule reminders and recurring tasks. Actions: add, list, remove. "
            "mode='reminder' (default) always speaks when it fires; mode='monitor' is a recurring "
            "check that runs silently and speaks only when something changed or is worth "
            "reporting (needs every_seconds or cron_expr, never at). "
            f"If tz is omitted, cron expressions and naive ISO times default to {self._default_timezone}."
        )

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors = super().validate_params(params)
        action = params.get("action")
        if action == "add" and not str(params.get("message") or "").strip():
            errors.append("message is required when action='add'")
        if action == "remove" and not str(params.get("job_id") or "").strip():
            errors.append("job_id is required when action='remove'")
        # Il valore ammesso lo controlla già l'enum dello schema; qui resta solo
        # la regola che lo schema non sa esprimere.
        mode = str(params.get("mode") or "").strip()
        # Un one-shot che decide di tacere non avvisa mai nessuno: la
        # combinazione non ha un comportamento sensato, quindi si rifiuta invece
        # di crearla e lasciarla scattare a vuoto.
        if mode == "monitor" and str(params.get("at") or "").strip():
            errors.append("mode='monitor' cannot be used with at; use every_seconds or cron_expr")
        return errors

    async def execute(
        self,
        action: str,
        name: str | None = None,
        message: str = "",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        tz: str | None = None,
        at: str | None = None,
        job_id: str | None = None,
        mode: str | None = None,
        **kwargs: Any,
    ) -> str:
        # Prima di ogni azione, ``add`` compresa: dentro un progetto il tool e'
        # chiuso in tutte e tre le direzioni. ``list`` mostrerebbe la sveglia
        # personale a una conversazione di lavoro ("chi sei viaggia, dove altro
        # lavori no"), e ``remove`` la cancellerebbe da li' dentro.
        if is_project_session_key(self._session_key.get()):
            return _PROJECT_REFUSAL
        # Seconda chiusura, e regola diversa: quella sopra dice *dove* si
        # programma, questa dice *se questo turno* puo' cambiare qualcosa. Vale
        # anche nella chat personale, ed e' la sola lettura del passo 4. Un job
        # e' fra le cose piu' durature che Jenny possa creare: sopravvive al
        # turno, alla conversazione e al riavvio.
        if current_turn_is_readonly():
            return READONLY_TOOL_REFUSAL
        if action == "add":
            if self._in_cron_context.get():
                return "Error: cannot schedule new jobs from within a cron job execution"
            return self._add_job(name, message, every_seconds, cron_expr, tz, at, mode)
        elif action == "list":
            return self._list_jobs()
        elif action == "remove":
            return self._remove_job(job_id)
        return f"Unknown action: {action}"

    def _add_job(
        self,
        name: str | None,
        message: str,
        every_seconds: int | None,
        cron_expr: str | None,
        tz: str | None,
        at: str | None,
        mode: str | None = None,
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
        if tz and not cron_expr:
            return "Error: tz can only be used with cron_expr"
        if tz:
            if err := self._validate_timezone(tz):
                return err

        # Build schedule
        delete_after = False
        if every_seconds:
            schedule = CronSchedule(kind="every", every_ms=every_seconds * 1000)
        elif cron_expr:
            effective_tz = tz or self._default_timezone
            if err := self._validate_timezone(effective_tz):
                return err
            schedule = CronSchedule(kind="cron", expr=cron_expr, tz=effective_tz)
        elif at:
            # Un monitor tace quando non c'è niente da dire: se scatta una volta
            # sola, quel silenzio è definitivo e il job non serve a nulla.
            if job_mode == "monitor":
                return (
                    "Error: mode=\"monitor\" cannot be scheduled with 'at', because a one-shot "
                    "job that decides to stay silent never reports anything. Use every_seconds "
                    "or cron_expr for a monitor, or mode=\"reminder\" for a one-time reminder."
                )
            try:
                dt = datetime.fromisoformat(at)
            except ValueError:
                return f"Error: invalid ISO datetime format '{at}'. Expected format: YYYY-MM-DDTHH:MM:SS"
            if dt.tzinfo is None:
                if err := self._validate_timezone(self._default_timezone):
                    return err
                dt = dt.replace(tzinfo=safe_zoneinfo(self._default_timezone))
            at_ms = int(dt.timestamp() * 1000)
            schedule = CronSchedule(kind="at", at_ms=at_ms)
            delete_after = True
        else:
            return "Error: either every_seconds, cron_expr, or at is required"

        job = self._cron.add_job(
            name=name or message[:30],
            schedule=schedule,
            message=message,
            mode=job_mode,
            delete_after_run=delete_after,
            session_key=session_key,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            origin_metadata=dict(self._origin_metadata.get() or {}),
        )
        return f"Created job '{job.name}' (id: {job.id})"

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
        lines.extend(self._format_task_checks(state, display_tz))
        if state.next_run_at_ms:
            lines.append(f"  Next run: {self._format_timestamp(state.next_run_at_ms, display_tz)}")
        return lines

    def _format_task_checks(self, state: CronJobState, display_tz: str) -> list[str]:
        """I singoli controlli dell'heartbeat, uno per riga. Vuoto per tutto il resto.

        Il job ``heartbeat`` esegue N controlli scritti a mano in ``HEARTBEAT.md``,
        e i tre contatori del job qui sopra ne sono soltanto il riassunto ("almeno
        un controllo non è partito"). Quale sia stava in ``state.task_checks``, che
        lo store salva e ricarica da commit e che **non raggiungeva nessuna
        superficie**: né questa, né la WebUI. "Il controllo delle piante sta
        funzionando?" si rispondeva solo leggendo logcat sul telefono.

        Le voci esistono solo per i controlli rotti (assente = sano), quindi su un
        heartbeat in salute questo blocco è vuoto e la risposta all'elenco resta
        identica a prima.

        Dice anche se all'utente è già stato detto, ed è la metà che serve di più:
        un controllo rotto da giorni di cui nessuno ha parlato è un guasto diverso
        da uno rotto da giorni e annunciato, e sono i due che il difetto del
        timbro dedotto rendeva indistinguibili.
        """
        lines: list[str] = []
        for entry in state.task_checks.values():
            if entry.pending_since_ms is not None and not entry.consecutive_could_not_check:
                lines.append(
                    f"  - {entry.label or 'unnamed check'}: handed to a subagent, "
                    "waiting for its result"
                )
                continue
            if not entry.consecutive_could_not_check:
                continue
            detail = (
                f"  - {entry.label or 'unnamed check'}: "
                f"{entry.consecutive_could_not_check} consecutive run(s) not carried out"
            )
            if entry.since_ms:
                detail += f", since {self._format_timestamp(entry.since_ms, display_tz)}"
            if entry.escalated:
                detail += "; the user has been warned"
                if entry.escalated_at_ms:
                    detail += f" on {self._format_timestamp(entry.escalated_at_ms, display_tz)}"
            else:
                detail += "; not reported to the user yet"
            lines.append(detail)
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

    def _list_jobs(self) -> str:
        jobs = self._cron.list_jobs()
        if not jobs:
            return "No scheduled jobs."
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
            parts = [f"- {j.name} (id: {j.id}, {timing}{mode_note})"]
            if j.payload.kind == "system_event":
                parts.append(f"  Purpose: {self._system_job_purpose(j)}")
                parts.append("  Protected: visible for inspection, but cannot be removed.")
            parts.extend(self._format_state(j.state, j.schedule))
            lines.append("\n".join(parts))
        return "Scheduled jobs:\n" + "\n".join(lines)

    def _remove_job(self, job_id: str | None) -> str:
        if not job_id:
            return "Error: job_id is required for remove"
        result = self._cron.remove_job(job_id)
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


# Registrazione esplicita dei tool di questo modulo (Fase 5.3): il
# ToolLoader legge questa lista invece della reflection dir(). Un nuovo
# tool va aggiunto qui esplicitamente.
TOOLS = [CronTool]
