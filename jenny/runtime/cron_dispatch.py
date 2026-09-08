"""Dispatch dei cron job (estratto dalla god-function ``on_cron_job`` in
``gateway_runtime._run_gateway``).

``on_cron_job`` era una closure di ~135 righe con tre rami inline (dream /
heartbeat / bound). Qui diventa una classe con le dipendenze INIETTATE.
L'``agent`` arriva come *getter* (``get_agent``) e non come valore catturato: nel
gateway è un nonlocal riassegnato dall'onboarding (creazione differita
dell'agente quando manca il provider), quindi catturarne il valore romperebbe il
flusso onboarding→cron. Il getter preserva esattamente il late-binding originale.

Nessun ramo consegna più all'utente da fuori il turno: un job schedulato è
lavoro interno e silenzioso (:mod:`jenny.session.turn_visibility`), e l'unico
modo di parlare è il tool ``message`` chiamato dentro il turno.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger

from jenny.bus.events import OutboundMessage
from jenny.cron.bound_runner import (
    CRON_WAKELOCK_TIMEOUT_S,
    BoundCronAgent,
    run_bound_cron_job,
)
from jenny.cron.could_not_check import (
    parse_could_not_check_marks,
    parse_delegated_marks,
    parse_warned_marks,
)
from jenny.cron.heartbeat_followup import HeartbeatFollowup
from jenny.cron.heartbeat_tasks import (
    active_section_text,
    already_warned_block,
    escalation_block,
    parse_heartbeat_tasks,
    rearm_after_user_message,
    record_task_outcomes,
    resolve_pending_delegations,
    task_index_block,
    tasks_already_warned,
    tasks_due_for_escalation,
)
from jenny.cron.service import CronJobSkippedError
from jenny.cron.session_turns import is_bound_cron_job
from jenny.cron.types import CronMonitorCouldNotCheckError
from jenny.runtime.power import keep_awake
from jenny.session.keys import HEARTBEAT_SESSION_KEY, UNIFIED_SESSION_KEY
from jenny.session.manager import last_user_message_ms
from jenny.session.turn_visibility import TurnVisibility

if TYPE_CHECKING:
    from jenny.agent.context import ContextBuilder
    from jenny.agent.gardener import GardenerOutcome, GardenerStore
    from jenny.agent.tools.registry import ToolRegistry
    from jenny.agent.turn_types import TurnOutcome
    from jenny.config.schema import Config
    from jenny.cron.service import CronService
    from jenny.cron.types import CronJob
    from jenny.session.manager import SessionManager


class CronCapableAgent(BoundCronAgent, Protocol):
    """Contratto strutturale dei membri dell'agente usati dal ``CronDispatcher``.

    ``AgentLoop`` lo soddisfa per costruzione. Usare un ``Protocol`` (structural
    typing) evita di importare ``AgentLoop`` a runtime da questo modulo, e con
    esso i cicli di import; i tipi delle annotazioni interne vivono sotto
    ``TYPE_CHECKING``. Estende ``BoundCronAgent`` (``tools`` + ``submit_cron_turn``),
    già usato dal ramo bound in ``run_bound_cron_job``.
    """

    context: "ContextBuilder"  # con ``.memory`` (MemoryStore)
    sessions: "SessionManager"  # con get_or_create / save / sessions_dir

    def active_session_keys(self) -> tuple[str, ...]:
        """Le sessioni con un turno in volo: il giardiniere non entra li'."""
        ...

    async def process_direct(
        self,
        content: str,
        session_key: str = ...,
        channel: str = ...,
        chat_id: str = ...,
        media: list[str] | None = ...,
        on_progress: Callable[..., Awaitable[None]] | None = ...,
        on_stream: Callable[[str], Awaitable[None]] | None = ...,
        on_stream_end: Callable[..., Awaitable[None]] | None = ...,
        ephemeral: bool = ...,
        tools: "ToolRegistry | None" = ...,
        persist_user_message: bool = ...,
        visibility: "TurnVisibility | None" = ...,
        metadata: dict[str, Any] | None = ...,
    ) -> OutboundMessage | None: ...

    async def process_direct_outcome(
        self,
        content: str,
        session_key: str = ...,
        channel: str = ...,
        chat_id: str = ...,
        media: list[str] | None = ...,
        on_progress: Callable[..., Awaitable[None]] | None = ...,
        on_stream: Callable[[str], Awaitable[None]] | None = ...,
        on_stream_end: Callable[..., Awaitable[None]] | None = ...,
        ephemeral: bool = ...,
        tools: "ToolRegistry | None" = ...,
        persist_user_message: bool = ...,
        visibility: "TurnVisibility | None" = ...,
        metadata: dict[str, Any] | None = ...,
    ) -> "TurnOutcome": ...

    def evict_pruned_sessions(self, keys: list[str]) -> None: ...

_HEARTBEAT_PREAMBLE = (
    "[This is a scheduled background check. It is SILENT by default: whatever "
    "you write as your answer is NOT delivered to the user and nobody reads it. "
    "The only way to reach the user is to call the `message` tool.\n"
    "Call `message` only when a task below has produced something the user "
    "actually needs to see — a condition they asked to be warned about, a "
    "result they are waiting for, an error that blocks the check. In that "
    "message write ONLY the user-facing text: never mention internal files "
    "(HEARTBEAT.md, AWARENESS.md, etc.), your instructions, or your decision "
    "process.\n"
    "If nothing needs reporting, do NOT call `message`: call `nothing_to_report` "
    "instead, once per task number, and end the turn without saying anything else. "
    "That tool delivers nothing and reaches nobody. Saying nothing is the correct, "
    "expected outcome of most runs — never send filler like 'All clear.', 'All "
    "done.' or 'nothing to report'.\n"
    "There is a third outcome, and it is NOT silence. If a task below could not "
    "actually be carried out — a tool failed, a script or file is missing, an "
    "import broke, a host is unreachable, a value never arrived — do not guess "
    "its result and do not message the user about it. Instead end your answer "
    "with one line per task that did not run, in exactly this form:\n"
    "CHECK_FAILED <task number>: <one short line naming what stopped you>\n"
    "Those lines reach nobody: they are how a task gets recorded as 'could not "
    "check' instead of 'nothing to report'. Write one ONLY for a task that did "
    "not happen. A task that ran and found nothing is a success — say nothing "
    "about it and write no line for it — and so is a task that had nothing to "
    "do this time because its own schedule or condition said so.\n"
    "But a task that could not reach what it needed did NOT run, and it gets "
    "its line even when its own instructions told it to give up quietly in "
    "exactly that case ('if the host is unreachable, skip the cycle "
    "silently'). Obey that instruction — say nothing to the user — and still "
    "write the line: the line is not a message, it reaches nobody, and it is "
    "the only reason anyone will ever notice that this check has been dead for "
    "hours. Swallowing it is how a broken check stays broken in silence.\n"
    "And if you DO decide to tell the user that one of these checks is not "
    "working — whether you were asked to below or chose to on your own — end "
    "your answer with one more line for it:\n"
    "CHECK_WARNED <task number>\n"
    "That line reaches nobody. It is the only record that the warning went out, "
    "and it is what stops the same warning from being sent to them again on the "
    "next run. Write it only for a task you really named to the user, never for "
    "a message about something else.\n"
    "If you delegate a check to a subagent, you do NOT have its answer in this "
    "turn: `spawn` returns immediately. Send NOTHING now — not the result, not "
    "'checking…', not an interim guess. The subagent's result comes back to you "
    "later as its own turn, and THAT is where you judge it and decide whether to "
    "call `message`. For every task you hand over that way, and only for those, "
    "end your answer with one line of this form:\n"
    "CHECK_DELEGATED <task number>: <what you asked the subagent for>\n"
    "That line reaches nobody either. It says 'the outcome of this task is not "
    "known yet', so that a check whose result never comes back is not filed as "
    "one that ran. Never write both lines for the same task: CHECK_FAILED is "
    "for a task you already know did not run.\n"
    "This session keeps your previous runs so you can spot changes. Those older "
    "readings are history, not the current state: never report a past value as if "
    "you had just measured it. If you find messages of your own in there — "
    "including mistakes, corrections or apologies — do NOT continue that "
    "conversation: the user is not talking to you, and another apology is just one "
    "more interruption. Say nothing about it and judge only the check in front of "
    "you.]\n\n"
)


def heartbeat_has_active_tasks(content: str) -> bool:
    """True if HEARTBEAT.md has task lines, ignoring headers, blanks and comments.

    La scansione vera sta in ``jenny.cron.heartbeat_tasks``, che dello stesso
    file estrae i singoli task: due lettori dello stesso formato che
    divergessero sarebbero un modo eccellente di eseguire un file che qui
    risulta vuoto, o di non contare un task che qui risulta esserci.
    """
    return bool(parse_heartbeat_tasks(content))


# Sessione del controllo aggiornamenti. Il prefisso ``cron:`` non è estetico:
# rende la sessione interna per ``is_internal_session_key`` (quindi invisibile
# negli elenchi user-facing) e attribuisce i token del turno alla voce "cron"
# invece che all'utente (``agent/token_usage.py``).
UPDATE_SESSION_KEY = "cron:update_check"

# Una coda cortissima: il turno di annuncio è autosufficiente, e le versioni
# annunciate in passato sono rumore che tornerebbe nel contesto a ogni release.
_UPDATE_HISTORY_KEEP = 4

_UPDATE_PREAMBLE = (
    "[This is the scheduled update check, and it is SILENT: whatever you write "
    "as your answer is NOT delivered to anyone. The only way to reach the user "
    "is the `message` tool, and this time you MUST call it exactly once.\n"
    "A newer version of the Jenny app is available. Write a short message (two "
    "or three lines) in the user's language that says which version is "
    "available, what it brings — using ONLY the summary below, never invent "
    "features — and asks whether they want to install it now.\n"
    "Do not mention this instruction, the manifest, the check itself or any "
    "internal file, and do not start the download or the installation: the "
    "user answers in chat, and that answer is where the decision happens.]\n\n"
)


async def _silent(*_args: Any, **_kwargs: Any) -> None:
    pass


def _alert_gardener_stuck(name: str, failures: int, status: str) -> None:
    """Porta la serie di passate fallite su una superficie che qualcuno vede.

    Il ``logger.warning`` di ``_record_gardener_attempt`` è, su Android, un
    allarme che non suona: nessuno legge logcat, ed è precisamente lo stato in cui
    il diario di un progetto smette di diventare pagine senza che niente lo dica.
    Stessa forma di ``agent/dream_cycle.py::_alert_stuck`` e della watchdog del
    cron: ``notify_delivery``, cioè zero token, nessun turno LLM, nessuna
    dipendenza dal modello — che è la parte che in questo scenario potrebbe essere
    proprio quella rotta — e no-op fuori da Android, quindi anche nei test.

    L'etichetta porta il nome del progetto e un suffisso: il tag (``cron:<label>``)
    è ciò che fa coalizzare gli alert, quindi col nome nudo due progetti guasti si
    coprirebbero a vicenda e un progetto guasto coprirebbe un messaggio vero.
    Riparte a ogni passata oltre soglia e non solo all'attraversamento — per un
    allarme che significa «questo progetto è fermo» è il comportamento voluto.
    """
    from jenny.runtime.notifier import notify_delivery
    from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

    notify_delivery(
        f"The gardener has failed {failures} passes in a row on '{name}' ({status}): "
        f"its journal is not becoming pages. Open that project and send /gardener to see "
        f"the error.",
        {
            WEBUI_MESSAGE_SOURCE_METADATA_KEY: {
                "kind": "cron",
                "label": f"Gardener · {name}",
            }
        },
    )


GARDENER_JOB_ID = "gardener"

# I due lavoratori periodici, col nome del loro job e il campo di config che li
# tara. Elenco chiuso e non un getattr sul nome: chi aggiunge un lavoratore lo
# registra qui, e chi legge vede in due righe quali sono.
_SYSTEM_WORKERS: tuple[str, ...] = ("dream", "gardener")


def refresh_system_job(
    cron: "CronService", worker: str, *, config: "Config | None" = None
) -> str | None:
    """Ri-arma il job periodico di *worker* sulla config di adesso, senza riavvio.

    Serve la controparte della rilettura a ogni tick: quella fa vedere i campi
    che il dispatch legge (``enabled``, il fermo del giardiniere, la distanza fra
    due passate), ma **non l'intervallo**. Quel numero non vive nel ``Config``: è
    diventato lo ``schedule`` del ``CronJob`` scritto nello store del cron alla
    registrazione, e da lì in poi nessuna lettura di ``config.json`` lo tocca.

    E c'è il caso peggiore, quello che rende questa funzione necessaria e non
    comoda: ``GatewayContainer.build`` registra ogni job **solo se acceso**. Su un
    gateway partito con un lavoratore spento, riaccenderlo scriverebbe un
    ``enabled=True`` che nessun job va a leggere — l'interruttore che riaccende
    resterebbe l'unico a chiedere un riavvio.

    ``register_system_job`` è idempotente e riparte da zero solo se la
    pianificazione è cambiata (v. il suo commento), quindi ri-registrare a
    intervallo identico non sposta la prossima scadenza.

    Ritorna la descrizione della pianificazione armata, o ``None`` se il
    lavoratore è spento: a spegnere non si deregistra niente — non esiste una
    controparte di ``register_system_job`` — e il cancello è quello di dispatch
    (``CronDispatcher._run_dream`` / ``_run_gardener``).

    **Vale per tutti e due e non solo per il giardiniere** (31/08/2026). Prima
    esisteva solo la versione del giardiniere, perché era l'unico con un
    interruttore raggiungibile; portando le manopole in Impostazioni, anche
    Dream ha guadagnato lo stesso interruttore e aveva bisogno della stessa
    controparte.
    """
    from jenny.config.loader import load_config
    from jenny.cron.types import CronJob, CronPayload

    if worker not in _SYSTEM_WORKERS:
        raise ValueError(f"unknown system worker {worker!r}; known: {_SYSTEM_WORKERS}")
    cfg = getattr((config or load_config()).agents.defaults, worker)
    if not cfg.enabled:
        return None
    cron.register_system_job(CronJob(
        id=worker,
        name=worker,
        schedule=cfg.build_schedule(),
        payload=CronPayload(kind="system_event"),
    ))
    return cfg.describe_schedule()




class CronDispatcher:
    """Instrada un ``CronJob`` al gestore giusto (dream / heartbeat / bound)."""

    def __init__(
        self,
        *,
        get_agent: Callable[[], "CronCapableAgent | None"],
        config: "Config",
        cron: "CronService",
        heartbeat_cfg: Any,
        # Ritorna ``True`` solo se il checkpoint è stato davvero eseguito. Non è
        # ``None`` perché il prompt del review pass ne ha bisogno per scegliere
        # fra "pota, è reversibile" e "è definitivo, nel dubbio tieni": un
        # callback che con gli snapshot spenti non fa nulla e tace verrebbe
        # letto come uno che ha fatto il suo lavoro.
        snapshot_before_dream: Callable[[], Awaitable[bool]] | None = None,
        # L'orologio, iniettabile per una ragione sola: da quando l'heartbeat
        # confronta due istanti — quello dell'avviso e quello dell'ultimo
        # messaggio dell'utente — un test che non può muoverlo non può provare
        # niente su quel confronto.
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._get_agent = get_agent
        self._config = config
        self._cron = cron
        self._hb_cfg = heartbeat_cfg
        self._snapshot_before_dream = snapshot_before_dream
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        # Un task delegato con ``spawn`` non ha un esito dentro il turno che lo
        # delega, e il turno che quell'esito ce l'ha — l'annuncio del subagent —
        # arriva dal bus e non passa mai di qui. Il servizio cron è l'aggancio
        # che i due condividono: v. ``jenny/cron/heartbeat_followup.py``.
        cron.heartbeat_followup = HeartbeatFollowup(
            cron=cron,
            heartbeat_file=lambda: self._config.workspace_path / "HEARTBEAT.md",
            now_ms=self._now_ms,
            user_spoke_at_ms=self._user_spoke_at_ms,
        )

    def _user_spoke_at_ms(self) -> int | None:
        """Quando l'utente ha scritto l'ultima volta, o ``None``.

        L'agente si chiede al getter e non si cattura: prima dell'onboarding non
        esiste, e senza agente non ci sono sessioni da leggere — che è anche la
        risposta giusta, "non risulta che abbia scritto", cioè nessun riarmo.

        La conversazione è **una** (``session_key_for_channel`` fa collassare
        ogni canale su ``unified:default``), quindi questa riga vale anche per
        Telegram: chi ha letto l'avviso lì l'ha letto lo stesso.
        """
        agent = self._get_agent()
        if agent is None:
            return None
        return last_user_message_ms(agent.sessions.get_or_create(UNIFIED_SESSION_KEY))

    async def dispatch(self, job: "CronJob") -> str | None:
        """Execute a cron job through the agent.

        Il wakelock sta **qui** e non solo in ``run_bound_cron_job`` perché dream
        e heartbeat non passano affatto da quel modulo: entrano da
        ``process_direct``, che non è il percorso di turno coperto da
        ``AgentLoop._dispatch``. Questo è l'unico punto attraversato da tutti e
        quattro i tipi di job. Sul ramo bound i due blocchi si annidano sullo
        stesso tag, che per costruzione acquisisce una volta sola.
        """
        async with keep_awake("cron", timeout_s=CRON_WAKELOCK_TIMEOUT_S):
            return await self._dispatch(job)

    async def _dispatch(self, job: "CronJob") -> str | None:
        agent = self._get_agent()
        if not agent:
            logger.warning("Cron: skipped job '{}' - no provider configured", job.name)
            raise CronJobSkippedError("no provider configured")

        if job.name == "dream":
            return await self._run_dream(agent)
        if job.name == "gardener":
            return await self._run_gardener(agent)
        if job.name == "heartbeat":
            return await self._run_heartbeat(agent, job)
        if job.name == "update_check":
            return await self._run_update_check(agent)
        if is_bound_cron_job(job):
            return await run_bound_cron_job(job, agent=agent, cron=self._cron)

        reason = "unbound agent cron job must be recreated from a chat session"
        logger.warning(
            "Cron: skipped unbound agent job '{}' ({}): {}", job.name, job.id, reason
        )
        raise CronJobSkippedError(reason)

    async def _run_gardener(self, agent: "CronCapableAgent") -> str | None:
        """Il giardiniere: una passata su un progetto, se uno è pronto.

        Come per Dream, qui resta solo l'instradamento: i tre orologi stanno in
        ``agent/gardener_schedule.py`` e la passata in ``agent/gardener.py``,
        condivisa con lo slash command ``/gardener``.

        Il controllo su ``enabled`` è **qui e non solo alla registrazione**:
        ``register_system_job`` non ha una controparte che deregistri, quindi un
        job registrato da un avvio precedente resta nello store del cron anche
        dopo che la sezione è stata spenta. È la stessa ragione per cui
        ``_run_update_check`` esce prima della rete.

        Quel che **non** è solo instradamento è la coda: una passata che non
        registra niente va segnata come tentata, e la serie di insuccessi va
        detta fuori dal log. V. ``_record_gardener_attempt``.

        La mutua esclusione fra due passate sullo stesso progetto **non** è qui:
        sta in ``run_gardener``, perché i chiamanti sono due e la guardia deve
        essere una. Conseguenza da conoscere: ``pick_project`` non sa niente delle
        passate in volo, quindi un tick che incontra un ``/gardener`` a mano paga
        la selezione e poi si ritira con ``already_running``. Una lettura di file
        piccoli, e in cambio nessuna copia della guardia in due posti.
        """
        from jenny.agent.gardener import run_gardener
        from jenny.agent.gardener_schedule import pick_project
        from jenny.config.loader import load_config

        # I tre orologi si rileggono **da disco a ogni tick**, esattamente come i
        # knob di Dream in ``_run_dream`` e per la stessa ragione: il ``Config``
        # che questo dispatcher tiene in mano è quello catturato quando il
        # container si è costruito, e nessuno lo aggiorna —
        # ``container._on_settings_changed`` ricarica modello e provider, non
        # questo.
        #
        # Senza la rilettura ``enabled=False`` non è raggiungibile: è il valore
        # che l'utente cambia per **fermare** una cosa, quindi è precisamente
        # quello che non può chiedere un riavvio del gateway. Vale lo stesso per
        # ``idle_min`` e ``min_hours_between_passes``, che decidono se entrare in
        # un progetto e sono l'altra metà del freno.
        #
        # Solo i knob: ``workspace_path`` e ``wiki.wikis_dir`` restano da
        # ``self._config``, perché cambiarli è un trasloco del workspace e non una
        # taratura (v. la nota gemella in ``_run_dream``).
        cfg = load_config().agents.defaults.gardener
        if not cfg.enabled:
            logger.debug("Gardener: disabled")
            return None

        pick = pick_project(
            self._config.workspace_path,
            idle_min=cfg.idle_min,
            min_hours_between_passes=cfg.min_hours_between_passes,
            sessions=agent.sessions,
            active_session_keys=agent.active_session_keys(),
            wikis_dir_name=getattr(self._config.wiki, "wikis_dir", "wikis") or "wikis",
        )
        if pick is None:
            logger.debug("Gardener: no project ready")
            return None

        # Il delta lo porta il ``pick``: la selezione ha già aperto i diari per
        # decidere, e prima di T2.5 ``run_gardener`` li riapriva da zero un istante
        # dopo. Così il prompt e il commit parlano della **stessa** lettura, e le
        # righe arrivate nel frattempo restano non lette invece di finire sotto un
        # cursore che le dichiara digerite.
        outcome = await run_gardener(agent, pick.store, delta=pick.delta)
        return self._record_gardener_attempt(pick.store, outcome)

    def _record_gardener_attempt(
        self, store: "GardenerStore", outcome: "GardenerOutcome"
    ) -> str | None:
        """Timbra il tentativo, allarma se la serie si allunga, e **dice l'esito**.

        Tre cose che prima non succedevano, e tutte e tre per lo stesso motivo:
        una passata che non registra niente era invisibile.

        1. **Il timbro.** Il cursore lo tengono fermo di proposito
           ``partial_write`` e ``commit_failed`` — ci sono righe non promosse che
           devono tornare — ma senza timbro «tenere il cursore» diventava «rifare
           la passata ogni mezz'ora», perché la distanza si misurava sul cursore.
           Il timbro lo mette ``run_gardener`` (v. il suo ``_stamped``), così la
           strada a mano e questa contano la stessa cosa; qui si legge
           ``outcome.failures`` e si decide se la serie vale una notifica.
        2. **L'esito nel record del cron.** Ritornando sempre ``None`` il servizio
           cron segnava ``last_status="ok"`` su una passata fallita, e lo stato del
           job — che è dove si va a guardare — diceva che tutto funzionava.
        3. **L'allarme.** Il log su Android non lo legge nessuno: è la stessa
           ragione per cui Dream ha ``_alert_stuck``, ed è la stessa primitiva
           (zero token, e no-op fuori da Android, quindi anche nei test).
        """
        from jenny.agent.gardener_state import (
            COMMITTED_STATUSES,
            GARDENER_FAILURES_ARE_ALARMING,
        )

        if outcome.status == "already_running":
            # Una passata su quel progetto è già in volo — quasi sempre un
            # ``/gardener`` lanciato a mano un attimo prima, perché il cancello del
            # fermo tiene fuori due tick di fila. Non è un insuccesso e non si
            # timbra (l'ha già fatto, o lo farà, la passata che sta lavorando), ma a
            # INFO e non a DEBUG: è l'unico posto da cui si vede che il tick non ha
            # lavorato *per un motivo*, e su Android i DEBUG non arrivano.
            logger.info(
                "Gardener cron job: a pass on {} is already in flight; this tick stands down",
                store.name,
            )
            return None
        if not outcome.ran:
            # Nessuna chiamata al provider: niente da timbrare e niente da
            # contare. ``skipped_no_delta`` è il caso *normale* di un tick, non un
            # insuccesso, e timbrarlo sposterebbe la distanza in avanti per una
            # passata che non è mai partita.
            logger.debug("Gardener cron job: {} on {}", outcome.status, store.name)
            return None
        if outcome.status in COMMITTED_STATUSES:
            # Il timbro del tentativo l'ha già messo ``GardenerState.advanced``,
            # insieme al cursore e all'azzeramento della serie.
            logger.info(
                "Gardener cron job: {} on {} ({} lines, {} writes)",
                outcome.status, store.name, outcome.lines, outcome.writes,
            )
            return None

        # Il timbro e il conto li ha già messi ``run_gardener``, perché la strada
        # a mano deve contare la stessa cosa di questa. Qui resta la sola parte
        # che è davvero del cron: decidere che la serie è abbastanza lunga da
        # valere una notifica.
        failures = outcome.failures
        logger.warning(
            "Gardener cron job: {} on {} ({} consecutive passes with nothing recorded); "
            "the next pass on it is not due for another min_hours_between_passes",
            outcome.status, store.name, failures,
        )
        if failures >= GARDENER_FAILURES_ARE_ALARMING:
            _alert_gardener_stuck(store.name, failures, outcome.status)
        return f"gardener: {store.name} {outcome.status}"

    async def _run_dream(self, agent: "CronCapableAgent") -> str | None:
        """Il job Dream, sotto la presa che tiene un solo ciclo per volta.

        La presa è **la stessa** di ``/dream`` (``agent/dream_cycle.py``), e il caso
        raggiungibile è proprio l'incrocio fra i due: i job cron sono serializzati
        fra loro, un ``/dream`` battuto a mano non lo è con niente. Il rifiuto è un
        log e un ritorno, non un'eccezione: un tick che non parte perché il lavoro è
        già in corso non è un job fallito.
        """
        from jenny.agent.dream_cycle import claim_dream_cycle, release_dream_cycle

        if not claim_dream_cycle():
            logger.warning(
                "Dream cron job skipped: a Dream cycle is already running"
            )
            return "dream: already running"
        try:
            return await self._dream_cycle(agent)
        finally:
            # Un livello fuori dal lavoro, come nel percorso del comando: nessun
            # cammino dentro ``_dream_cycle`` può saltarlo, eccezioni e
            # cancellazione comprese. La presa la si rende sempre.
            release_dream_cycle()

    async def _dream_cycle(self, agent: "CronCapableAgent") -> str | None:
        # Dream is an internal job — run directly, not through the agent loop.
        #
        # Il prologo e l'epilogo del ciclo stanno in ``jenny/agent/dream_cycle.py``,
        # condivisi con lo slash command ``/dream``: erano due copie della stessa
        # sequenza e divergevano una divergenza alla volta. Qui resta il mestiere
        # di questo percorso — il turno incrementale, i log, la pulizia.
        from jenny.agent.dream_cycle import begin_dream_cycle, finish_dream_cycle
        from jenny.agent.memory import MemoryStore

        prune_dream_sessions = MemoryStore.prune_dream_sessions

        store = agent.context.memory
        # I knob di Dream si rileggono da disco a ogni run, e non si prendono da
        # ``self._config``. Quel Config è catturato quando il container si
        # costruisce, e **niente lo aggiorna**: ``_on_settings_changed``
        # (``runtime/container.py``) ricarica modello e provider, non questo.
        #
        # Senza la rilettura la superficie di taratura non funziona.
        # ``/dream budget memory 6000`` scrive ``config.json`` e il run manuale
        # lo applica subito (fa un ``load_config()`` fresco), mentre *questo*
        # run — quello che gira ogni due ore ed è il consumatore vero —
        # continuerebbe a usare il numero vecchio fino al riavvio del gateway.
        # L'utente vedrebbe il budget confermato in chat e nessun effetto per
        # ore, che è il modo peggiore in cui una manopola può non funzionare.
        #
        # Vale solo per i knob di Dream: il resto di ``self._config`` resta com'è
        # (v. il chip aperto sulla staleness generale del Config del cron), e il
        # costo è una lettura di un file piccolo ogni due ore.
        from jenny.config.loader import load_config

        cfg = load_config().agents.defaults.dream
        resp = None
        try:
            prologue = await begin_dream_cycle(
                agent,
                store=store,
                cfg=cfg,
                take_snapshot=self._snapshot_before_dream,
            )
            # Il turno incrementale ha un ``try`` suo, e il ciclo si chiude nel
            # suo ``finally``: correzione del 2026-08-17. Con
            # ``finish_dream_cycle`` come ultima istruzione del try esterno, un
            # ``process_direct`` che solleva finiva nell'``except`` e i contatori
            # non venivano scritti — quindi ``runs_since_review`` non avanzava, e
            # un Dream che crasha a ogni run non arrivava **mai** a un review
            # pass, senza che niente lo dicesse. È lo stesso guasto che la
            # docstring di ``finish_dream_cycle`` racconta di aver chiuso per il
            # caso "niente storia da consolidare"; mancava questo.
            #
            # ``advanced=None`` significa "il turno non ha mancato niente": è il
            # valore del ramo senza storia, e anche quello di un turno crashato.
            # Su un crash **non** si usa ``False``: quello incrementa ``stuck``,
            # il cui allarme dice "le scritture continuano a essere rifiutate dal
            # budget" — una diagnosi sbagliata per un'eccezione, e su una
            # superficie che l'utente vede. Il crash ha già il suo
            # ``logger.exception``.
            advanced: bool | None = None
            # Zero finché il turno non dice altro: un turno che crasha prima di
            # scrivere non ha avuto nessun rifiuto, e attribuirgliene uno lo
            # manderebbe nel ramo "manca spazio" con la diagnosi sbagliata.
            refused = 0
            try:
                resp, advanced, refused = await self._dream_turn(agent, store, prologue)
            finally:
                finish_dream_cycle(
                    store,
                    advanced=advanced,
                    runs_since_review=prologue.runs_since_review,
                    stuck=prologue.stuck,
                    nothing_new=prologue.nothing_new,
                    # La causa, che è ciò che decide quale dei due contatori sale:
                    # un rifiuto rimasto aperto significa "manca spazio" e un
                    # review può liberarlo; senza rifiuti non c'è niente da
                    # liberare, e forzarlo poterebbe file che non hanno da dare.
                    refused=refused,
                )
        except Exception:
            logger.exception("Dream cron job failed")
        # La contabilita' dei token **non passa da qui**: la fa
        # ``TokenUsageHook.after_iteration`` sul turno stesso, che e' l'unico punto
        # in cui l'``usage`` del provider esiste davvero. Qui c'era un ``finally``
        # con ``record_response_token_usage(resp, source="dream")``: ``resp`` e' un
        # ``OutboundMessage``, che un campo ``usage`` non ce l'ha e non l'ha mai
        # avuto, quindi quella riga non ha mai registrato niente — in nessuno dei
        # cinque punti in cui era stata scritta. Toglierla non perde una misura, ne
        # toglie una finta.
        # compact_history now acquires a threading.Lock and rewrites the whole
        # file; run it off the event loop so a concurrent append holding the
        # lock (on another thread) can't stall the loop on the blocking wait.
        await asyncio.to_thread(store.compact_history)
        pruned_keys = prune_dream_sessions(agent.sessions.sessions_dir)
        if pruned_keys:
            agent.evict_pruned_sessions(pruned_keys)
        return None

    async def _dream_turn(
        self, agent: "CronCapableAgent", store: Any, prologue: Any
    ) -> tuple[Any, bool | None, int]:
        """Il turno incrementale di Dream. Ritorna ``(risposta, avanzato, rifiuti)``.

        Il terzo elemento sono i rifiuti di budget rimasti aperti, e viaggia fin
        qui perché è la **causa**: decide quale dei due contatori del livelock
        sale, e quindi se un review pass forzato ha una leva o girerebbe a vuoto.

        Estratto in un metodo perché la chiusura del ciclo va in un ``finally`` e
        il ramo "niente storia" esce con un ``return`` di mezzo: inline, quel
        ``return`` costringeva a ripetere la chiusura in due punti — che è
        esattamente il genere di duplicazione da cui questo sottosistema è nato
        (due copie della stessa sequenza, divergenti una divergenza alla volta).

        ``None`` come secondo valore vuol dire "non c'era niente da consolidare",
        che è diverso da ``False`` (ha provato e non ce l'ha fatta).
        """
        from jenny.agent.dream_cycle import (
            NO_ENTRIES,
            batch_was_not_consolidated,
            take_dream_snapshot,
        )
        from jenny.agent.memory import MemoryStore
        from jenny.agent.memory_budget import render_gauge

        result = store.build_dream_prompt(gauge=render_gauge(prologue.report))
        if result is None:
            logger.info("Dream: nothing to process")
            return None, None, 0
        prompt, last_cursor = result[0], result[1]
        # ``getattr`` con un default, come per ``file_states`` poco sotto e per la
        # stessa ragione: ``build_dream_prompt`` e' sostituito nei test da doppi
        # che ritornano una coppia nuda. Un batch che non dichiara il proprio tipo
        # e' un batch personale, che e' il comportamento di sempre.
        scope = getattr(result, "scope", "personal")
        if prologue.review is None:
            # Un solo checkpoint per ciclo. Se il review è appena girato lo
            # snapshot è già stato preso pochi secondi fa e copre anche il turno
            # incrementale che segue; rifarlo qui archivierebbe lo stato *dopo* il
            # review sotto la stessa etichetta "pre_dream", cioè un secondo
            # checkpoint che non è pre-niente.
            await take_dream_snapshot(self._snapshot_before_dream)
        dream_tools = store.build_dream_tools(
            write_size_guard=prologue.guard, scope=scope,
        )
        resp = await agent.process_direct(
            prompt,
            session_key=MemoryStore.dream_session_key(),
            ephemeral=True,
            tools=dream_tools,
            on_progress=_silent,
        )
        # ``getattr``: il registry Dream espone ``file_states``, ma il contratto
        # resta tollerante verso registry di altra provenienza.
        dream_file_states = getattr(dream_tools, "file_states", None)
        advanced = MemoryStore.dream_should_advance_cursor(resp, dream_file_states)
        # Il run ha scritto, ma il batch è atterrato? Sono due domande diverse e
        # fino al 2026-08-18 se ne faceva una sola (v.
        # ``dream_cycle.batch_was_not_consolidated``). Sta dopo il gate e non
        # dentro perché ``internal_run_should_commit`` è condiviso col giardiniere, che
        # non ha un batch di storia da far atterrare.
        # Il tool per voci del run appena concluso. ``getattr`` con un default
        # perché ``build_dream_tools`` è sostituito nei test da doppi che non lo
        # espongono: un run senza quel tool è un run con zero voci, non un errore.
        entries = getattr(dream_tools, "memory_entries", None) or NO_ENTRIES
        held_batch = advanced and batch_was_not_consolidated(
            before=prologue.report,
            history_text=MemoryStore.dream_prompt_history(prompt),
            stuck=prologue.stuck + prologue.nothing_new,
            # L'esito in voci del run, dal tool esposto sul registry: sono questi
            # numeri a rendere la domanda una verifica invece di una stima.
            added=entries.entries_added,
            replaced=entries.entries_replaced,
            already_present=entries.entries_already_present,
            # Se non ha tentato nessuna scrittura non ha mancato niente:
            # ha deciso che non c'era da scrivere. V. il docstring.
            attempted=getattr(dream_file_states, "writes_attempted", 0),
        )
        if held_batch:
            advanced = False
        if advanced:
            store.set_last_dream_cursor(last_cursor)
            logger.info("Dream cron job completed, cursor advanced to {}", last_cursor)
        elif held_batch:
            # Ramo **prima** di quello dei rifiuti, e non è ordine estetico: la
            # prima stesura lo teneva a parte e le due righe uscivano insieme, la
            # seconda dicendo "attempts blocked/refused" su un run in cui nessuna
            # scrittura era stata né bloccata né rifiutata (visto in logcat il
            # 2026-08-18 alle 14:02:35). Una diagnosi falsa accanto a una vera è
            # peggio di nessuna diagnosi, ed è lo stesso difetto per cui il testo
            # di ``format_stuck_alarm`` è già stato riscritto due volte. Un run,
            # una riga.
            #
            # E niente "wrote to disk" nel testo: quel run non aveva scritto
            # nulla (``writes_attempted == 0``, solo ``read_file``). Il fatto che
            # conta è che il batch non è atterrato, non se qualcosa è stato
            # scritto — le due cose sono indipendenti, ed è per questo che la
            # guardia esiste.
            logger.warning(
                "Dream cron job consolidated nothing from its batch "
                "(no memory file grew while a memory file was near its budget); "
                "cursor held at {} so the entries come back",
                store.get_last_dream_cursor(),
            )
        elif MemoryStore.dream_run_completed(resp):
            # Completato pulito ma senza scritture riuscite pur avendole tentate:
            # blocco/rifiuto. Non avanzare: le voci vanno riprocessate al
            # prossimo run.
            logger.warning(
                "Dream cron job completed without writing (attempts blocked/refused); "
                "cursor remains at {}",
                store.get_last_dream_cursor(),
            )
        else:
            logger.warning(
                "Dream cron job did not complete; cursor remains at {}",
                store.get_last_dream_cursor(),
            )
        refused = getattr(dream_file_states, "unrecovered_refusals", 0)
        return resp, advanced, refused if isinstance(refused, int) else 0

    async def _run_update_check(self, agent: "CronCapableAgent") -> str | None:
        """Update check: annuncia una versione nuova UNA volta sola, poi tace.

        La regola centrale è la seconda esecuzione: un utente che ha già visto
        (e magari rimandato) l'annuncio di 0.7.0 non deve ritrovarselo ogni
        giorno. Chi decide è ``notified_code`` nello stato dell'updater, non il
        modello.

        Il turno segue il contratto dell'heartbeat: silenzioso, con la chat
        WebUI come indirizzo, e l'unica consegna possibile è il tool ``message``
        chiamato dentro il turno.
        """
        if not self._config.updates.enabled:
            # Lo spegnimento va fatto valere **qui**, non solo alla
            # registrazione. Il job sopravvive alla configurazione che lo ha
            # creato: ``register_system_job`` non ha una controparte che
            # deregistri e ``remove_job`` protegge i ``system_event``, quindi il
            # job registrato al primo avvio (il default è acceso) resta nello
            # store del cron anche dopo che l'utente ha spento la sezione.
            # Senza questa uscita l'unico percorso periodico che tocca la rete
            # senza che nessuno l'abbia chiesto continuerebbe a girare — e con
            # esso il turno LLM e i token che costa.
            logger.debug("Update check: disabled in config, nothing to do")
            return None

        from jenny.runtime.update_check import (
            check_for_update,
            mark_notified,
            notified_version_code,
        )

        info = await check_for_update(self._config)
        if info is None:
            logger.debug("Update check: nothing to propose")
            return None
        if not self._config.updates.notify_in_chat:
            # Niente ``mark_notified``: l'annuncio non è avvenuto, e se l'utente
            # riaccende la notifica deve ancora poterlo ricevere.
            logger.info(
                "Update check: {} available, chat notification disabled", info.version_name
            )
            return None
        if notified_version_code() == info.version_code:
            logger.debug("Update check: {} was already announced", info.version_name)
            return None

        from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

        source_metadata = {WEBUI_MESSAGE_SOURCE_METADATA_KEY: {"kind": "update"}}
        size_mb = info.size / (1024 * 1024)
        prompt = (
            _UPDATE_PREAMBLE
            + f"New version: {info.version_name}\n"
            + f"Summary: {info.summary or '(no summary provided)'}\n"
            + f"Download size: {size_mb:.1f} MB\n"
            + (f"Release notes: {info.notes_url}\n" if info.notes_url else "")
            + (
                "This is a critical security update: say so plainly.\n"
                if info.critical
                else ""
            )
        )

        await agent.process_direct(
            prompt,
            session_key=UPDATE_SESSION_KEY,
            channel="websocket",
            chat_id="default",
            on_progress=_silent,
            visibility=TurnVisibility.SILENT,
            metadata=source_metadata,
        )

        # Marcato **incondizionatamente**, anche se il modello non avesse
        # chiamato ``message``. È un compromesso, non una svista, e va detto per
        # intero perché il costo cade su un utente che non vedrà mai il log.
        #
        # Il fatto esiste: il turno lo calcola come ``TurnOutcome.spoke``
        # (``agent/turn_types.py``, da ``ctx.spoke_via_tool``). Non arriva qui
        # perché ``process_direct`` restituisce per contratto il *payload* e non
        # l'esito — scelta deliberata, documentata nella sua docstring — e
        # cambiarla vorrebbe dire toccare la firma condivisa da Dream,
        # heartbeat e dai comandi. La strada alternativa (un deliverer iniettato
        # nel dispatcher) è chiusa apposta: v. la docstring di questo modulo e
        # ``runtime/container.py``.
        #
        # Legandolo a ``spoke`` si scambierebbe comunque un difetto con un
        # altro: un modello che sistematicamente non chiama ``message`` farebbe
        # ripartire un turno LLM a ogni controllo, per sempre. Così invece si
        # perde al più *una* spinta in chat — e la versione resta visibile
        # altrove, nel badge delle impostazioni (``webui/settings_api.py``) e nel
        # tool ``update_status``, che leggono lo stesso ``cached_update()``.
        mark_notified(info.version_code)

        session = agent.sessions.get_or_create(UPDATE_SESSION_KEY)
        session.retain_recent_legal_suffix(_UPDATE_HISTORY_KEEP)
        agent.sessions.save(session)

        if info.critical:
            # Una fix di sicurezza deve squillare anche se l'utente non aveva la
            # chat aperta: l'alert implicito della consegna parte solo se il
            # turno ha davvero prodotto un messaggio. Stesso tag, quindi le due
            # notifiche si sostituiscono invece di sommarsi.
            from jenny.runtime.notifier import post_alert

            await post_alert(
                f"Aggiornamento critico {info.version_name} disponibile",
                source_metadata,
            )

        logger.info(
            "Update check: announced {} (versionCode {})",
            info.version_name, info.version_code,
        )
        return None

    async def _run_heartbeat(self, agent: "CronCapableAgent", job: "CronJob") -> str | None:
        # Heartbeat is a system job that checks HEARTBEAT.md for active tasks.
        heartbeat_file = self._config.workspace_path / "HEARTBEAT.md"
        try:
            content = heartbeat_file.read_text(encoding="utf-8")
        except OSError:
            logger.debug("Heartbeat: HEARTBEAT.md missing")
            return None
        tasks = parse_heartbeat_tasks(content)
        if not tasks:
            logger.debug("Heartbeat: HEARTBEAT.md has no active tasks")
            return None

        # Target unico e routable dei messaggi heartbeat: la chat WebUI condivisa.
        # Resta il canale del turno anche se il turno è silenzioso — è dove il
        # tool ``message`` consegna quando una condizione scatta davvero.
        channel, chat_id = "websocket", "default"

        # Prima di tutto il resto: le deleghe del ciclo precedente di cui non è
        # mai arrivato un verdetto si chiudono qui, e si chiudono in favore del
        # task. Va fatto prima di leggere lo stato — un controllo delegato che si
        # è ripreso lascia dietro di sé il conteggio vecchio, e leggerlo prima di
        # risolverlo metterebbe nel prompt la richiesta di avvisare l'utente di un
        # guasto che non c'è più.
        #
        # Da qui passa ormai solo il verdetto mai arrivato: il recupero lo
        # dichiara il turno d'annuncio con ``CHECK_OK``. Il conteggio si azzera
        # comunque, ma il ricordo di aver già avvisato l'utente sopravvive — è la
        # differenza fra un avviso per guasto e uno ogni due ore.
        unresolved = resolve_pending_delegations(job.state)
        if unresolved:
            still_remembered = [
                e.label for e in job.state.task_checks.values() if e.escalated
            ]
            logger.debug(
                "Heartbeat: {} delegated check(s) never reported back, counted as run: {}"
                "{}",
                len(unresolved),
                "; ".join(unresolved),
                (
                    f" (user already warned about: {'; '.join(still_remembered)})"
                    if still_remembered
                    else ""
                ),
            )

        # E l'altra cosa successa fra un run e l'altro: l'utente si è fatto vivo.
        # Se ha scritto dopo che gli abbiamo parlato, l'avviso l'ha letto, e un
        # guasto ancora aperto torna a essere una notizia. Prima di leggere lo
        # stato per gli stessi motivi della riga sopra — dopo, il prompt
        # porterebbe la domanda di ieri.
        rearmed = rearm_after_user_message(
            job.state, user_spoke_at_ms=self._user_spoke_at_ms()
        )
        if rearmed:
            logger.info(
                "Heartbeat: the user has written since being warned, {} check(s) can be "
                "reported again: {}",
                len(rearmed),
                "; ".join(rearmed),
            )

        # L'escalation si decide PRIMA del turno, perché è una riga di prompt:
        # solo il modello, dentro il turno, sa se il controllo è riuscito adesso,
        # ed è anche l'unico che possa consegnare (tool ``message``). Con nessun
        # task in sequenza di guasto il blocco è vuoto e il prompt di un run sano
        # resta byte-identico a quello del run precedente.
        escalating = tasks_due_for_escalation(job.state, tasks)
        # E il suo complemento: i task di cui l'utente è già stato avvisato. Non
        # chiedere di parlare non basta a far tacere — v. ``already_warned_block``.
        already_warned = tasks_already_warned(job.state, tasks)

        # Nel prompt entra la sezione dei task, non il file. ``HEARTBEAT.md`` ha
        # due proprietari — l'elenco è dell'utente, i commenti HTML che lo
        # spiegano sono nostri — e finché ci finiva grezzo il modello si
        # rileggeva la nostra spiegazione a ogni run, per sempre, su ogni
        # installazione. La macchina a stati che salta quei commenti esiste già
        # ed è già usata da questo stesso ramo (``parse_heartbeat_tasks``, poco
        # sopra, per ``task_index_block``): qui guadagna un secondo chiamante
        # invece di una seconda copia.
        #
        # Sparisce più dei commenti, e va detto: se ne va anche tutto ciò che sta
        # **fuori** dalla sezione ``## Active Tasks``, intestazioni comprese —
        # nella sezione, invece, un ``### WaterBot`` dell'utente resta, perché è
        # ciò che dice di cosa parlano le righe sotto.
        #
        # Cosa NON cambia, e sono due cose. Che cosa conta come task: lo decide
        # ``parse_heartbeat_tasks``, invariata, la stessa che poche righe sopra
        # decide se il run parte — cambia ciò che il modello vede, non ciò che il
        # sistema conta, e l'identità dei task (l'hash del testo, con cui è
        # indicizzato lo stato dell'escalation già sul dispositivo) resta quella
        # di prima. E il determinismo: con nessun task in sequenza di guasto il
        # prompt di un run sano è byte-identico a quello del run precedente, che
        # è ciò su cui si regge la cache di prefisso del provider.
        #
        # Il path passa perché è l'unico posto che ce l'ha: un commento HTML mai
        # chiuso nasconde tutto ciò che gli sta sotto, e un avviso che non nomina
        # il file non serve a nessuno.
        listed_tasks = active_section_text(content, str(heartbeat_file))

        prompt = (
            _HEARTBEAT_PREAMBLE
            + (already_warned_block(already_warned) if already_warned else "")
            + (escalation_block(escalating) if escalating else "")
            + f"Review the following HEARTBEAT.md and report any active tasks:\n\n{listed_tasks}\n"
            + task_index_block(tasks)
        )

        from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

        outcome = await agent.process_direct_outcome(
            prompt,
            session_key=HEARTBEAT_SESSION_KEY,
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
            # Il contratto dell'heartbeat, dichiarato una volta e fatto valere
            # dal turno: niente consegna implicita. Prima si diceva al modello di
            # produrre un riempitivo ("All clear.") e poi si pagava una seconda
            # chiamata LLM per indovinare se nasconderlo — un giudice che con un
            # modello reasoning finiva in ``finish_reason='length'`` e non
            # decideva mai. Ora l'unica consegna possibile è il tool ``message``.
            visibility=TurnVisibility.SILENT,
            # Sorgente proattiva: dà titolo/tag all'alert di sistema
            # (jenny/runtime/notifier.py) e origine al transcript. Viaggia nei
            # metadata del turno perché è da lì che il tool ``message`` li
            # eredita per il proprio invio.
            metadata={WEBUI_MESSAGE_SOURCE_METADATA_KEY: {"kind": "heartbeat"}},
        )

        # Keep a small tail of heartbeat history so the loop stays bounded.
        session = agent.sessions.get_or_create(HEARTBEAT_SESSION_KEY)
        session.retain_recent_legal_suffix(self._hb_cfg.keep_recent_messages)
        agent.sessions.save(session)

        # Il payload del turno è ``None`` per costruzione (turno silenzioso): il
        # testo del modello non è mai stato la consegna. Quello che serve qui è
        # ``final_text``, dove il modello dichiara i task che non ha potuto
        # eseguire, e ``spoke``, che dice se l'avviso è davvero uscito.
        check = record_task_outcomes(
            job.state,
            tasks,
            parse_could_not_check_marks(outcome.final_text),
            now_ms=self._now_ms(),
            escalating=escalating,
            # Non ``outcome.spoke``: quello dice che in questo turno è uscito un
            # messaggio, non di che cosa parlava. V. ``WARNED_MARKER``.
            warned=parse_warned_marks(outcome.final_text),
            delegated=parse_delegated_marks(outcome.final_text),
        )
        if check.pending:
            # Detto per intero, perché la riga di prima ("check completed") su un
            # turno che aveva solo delegato è esattamente il genere di
            # affermazione sicura e falsa che rende lento un debug: il turno è
            # finito, il controllo no. Il verdetto arriverà col turno d'annuncio
            # del subagent (``jenny/cron/heartbeat_followup.py``).
            logger.info(
                "Heartbeat: turn finished, {} task(s) delegated and still pending: {}",
                len(check.pending),
                "; ".join(t.label for t in check.pending),
            )
        if not check.any_failure:
            if not check.pending:
                logger.debug("Heartbeat: check completed")
            return None

        for task in check.failed:
            entry = job.state.task_checks[task.id]
            logger.warning(
                "Heartbeat: task '{}' could not run ({} in a row): {}",
                task.label,
                entry.consecutive_could_not_check,
                check.reasons.get(task.id) or "no reason given",
            )
        if check.unattributed:
            # Un marcatore che non si riesce ad attribuire non incolpa nessuno:
            # sarebbe un avviso su un controllo sano. Resta nel riassunto del
            # run, che è il posto giusto per un fatto che non sappiamo assegnare.
            logger.warning(
                "Heartbeat: {} unattributed CHECK_FAILED line(s): {}",
                len(check.unattributed),
                "; ".join(r or "no reason given" for r in check.unattributed),
            )

        # Riassunto a livello di job: ``last_status='could_not_check'`` e il
        # motivo, così "il controllo delle piante sta funzionando?" si risponde
        # dallo stato del cron invece che da logcat. La mappa per-task, appena
        # aggiornata su ``job.state``, dice *quale*; il ``CronService`` la salva
        # insieme al resto dello store.
        raise CronMonitorCouldNotCheckError(
            f"heartbeat: {len(check.failed) + len(check.unattributed)} task(s) could not run",
            reason=check.summary() or None,
            escalated=check.escalated,
        )
