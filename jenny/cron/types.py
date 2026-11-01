"""Cron types."""

from dataclasses import dataclass, field
from typing import Any, Literal


class CronJobSilencedError(Exception):
    """Il job monitor ha deciso di non parlare: è un esito **normale**.

    Non segnala un guasto — al contrario, è il caso previsto per un job in
    ``mode="monitor"``: ha guardato, non c'era niente da riferire, e tacere è
    la risposta giusta. Viaggia come eccezione per lo stesso motivo di
    ``CronJobSkippedError`` (``cron/service.py``): la decisione nasce in fondo
    alla callback, dove l'unico modo di dire "chiudi qui senza consegnare
    niente" senza far passare un valore sentinella per tutti gli strati
    intermedi è sollevare.

    Sta in ``types.py`` e non accanto a ``CronJobSkippedError`` perché la
    sollevano i chiamanti (il runner del turno), che importano già i tipi cron
    ma non devono dipendere dal servizio.
    """


class CronMonitorCouldNotCheckError(CronJobSilencedError):
    """Il monitor ha girato ma **il controllo non è avvenuto**: terzo stato.

    Fino a qui un monitor aveva due esiti soli, e producevano lo stesso identico
    output — niente. "Ho guardato e va tutto bene" e "non sono riuscito a
    guardare" erano indistinguibili, quindi un monitor rotto sembrava un
    giardino sano. Questa eccezione è quel terzo stato, e resta **muta**: come
    ``CronJobSilencedError`` non consegna nulla all'utente, si limita a farsi
    registrare (v. ``CronJobState.consecutive_could_not_check``).

    Sottoclasse di ``CronJobSilencedError`` di proposito: chiunque oggi tratti
    il silenzio come "nessuna consegna è avvenuta" continua ad avere ragione
    anche qui. Chi vuole distinguere lo fa mettendo il proprio ``except`` per
    primo — è ciò che fa ``CronService._execute_job``.

    Attributes:
        reason: riga breve scritta dal modello su cosa lo ha bloccato.
        escalated: il turno **dichiara** di aver avvisato l'utente di questo
            guasto (riga ``CHECK_WARNED``). Il servizio lo usa per non ripetere
            l'avviso a ogni ciclo finché lo stato non cambia. Non è "è uscito un
            messaggio": quello era il difetto di prima, e un messaggio che
            parlava d'altro zittiva il guasto per sempre.
    """

    def __init__(
        self,
        message: str = "",
        *,
        reason: str | None = None,
        escalated: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.escalated = escalated


class CronMonitorDelegatedError(CronJobSilencedError):
    """Il monitor ha passato il controllo a un aiutante (``spawn``): l'esito arriva dopo.

    Muto come il silenzio, ma **non** è la prova che il controllo sia avvenuto:
    ``spawn`` ritorna subito, e il turno finisce prima che l'aiutante cominci.
    Leggerlo come ``silenced`` azzerava la sequenza dei controlli mancati e
    perdeva l'esito. Il verdetto lo scrive il turno d'annuncio dell'aiutante
    (:mod:`jenny.cron.monitor_followup`); se non arriva mai, lo chiude il run
    dopo (``resolve_pending_delegation``).
    """


# Vocabolario degli esiti registrati per un run. ``silenced`` è il "ho
# controllato e non c'era niente da dire" del piano (``no_finding``); il nome
# resta quello già scritto negli store esistenti e nella WebUI. ``delegated``:
# il controllo è nelle mani di un aiutante, l'esito arriva col suo annuncio.
CronRunStatus = Literal["ok", "error", "skipped", "silenced", "could_not_check", "delegated"]

# Quanto è precisa la data di un singolo: ``exact`` (giorno e ora), ``day`` (un
# giorno, suona alle 9), ``month`` («verso marzo», suona il primo alle 9). Serve
# solo a mostrarla: l'``at_ms`` è già calcolato.
CronPrecision = Literal["exact", "day", "month"]


@dataclass
class CronSchedule:
    """Schedule definition for a cron job."""
    kind: Literal["at", "every", "cron"]
    # For "at": timestamp in ms
    at_ms: int | None = None
    # For "every": interval in ms
    every_ms: int | None = None
    # For "cron": cron expression (e.g. "0 9 * * *")
    expr: str | None = None
    # Timezone for cron expressions
    tz: str | None = None
    # Solo per un ``at``: come mostrare la data (v. ``CronPrecision``). ``None``
    # vale ``exact``.
    precision: CronPrecision | None = None


@dataclass
class CronPayload:
    """What to do when the job runs."""
    kind: Literal["system_event", "agent_turn"] = "agent_turn"
    # "reminder" parla sempre; "monitor" gira in silenzio e parla solo se ha
    # qualcosa da dire. Default "reminder": gli store scritti prima di questo
    # campo si ricaricano con il comportamento che avevano.
    mode: Literal["reminder", "monitor"] = "reminder"
    message: str = ""
    session_key: str | None = None  # original session key for correct session recording
    origin_channel: str | None = None
    origin_chat_id: str | None = None
    origin_metadata: dict[str, Any] = field(default_factory=dict)
    # Un cron del quaderno: ``{notebook, card, section}``. Il job sta nel cassetto
    # di quella scheda, gira col recinto del quaderno e vi lascia il suo esito
    # (v. ``jenny/agent/tools/cron.py`` e ``bound_runner``). ``None`` per i cron
    # della chat personale.
    board: dict[str, Any] | None = None
    # La pagina della wiki del quaderno che spiega il perché del job (percorso
    # relativo a ``wiki/``). Il turno la legge e la usa per dire «cosa e perché».
    why: str | None = None
    # Il filtro di un ricorrente su un dato di un'app: ``{app, params, field, op,
    # value}``. A ogni scadenza il codice fa girare il lettore dell'app e
    # confronta; il turno parte solo quando la condizione diventa vera (v.
    # :mod:`jenny.cron.condition`). Mai su un ``at``.
    condition: dict[str, Any] | None = None


@dataclass
class CronRunRecord:
    """A single execution record for a cron job."""
    run_at_ms: int
    status: CronRunStatus
    duration_ms: int = 0
    error: str | None = None


@dataclass
class CronJobState:
    """Runtime state of a job."""
    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_status: CronRunStatus | None = None
    last_error: str | None = None
    run_history: list[CronRunRecord] = field(default_factory=list)

    # ── monitor: "non ho potuto controllare" ──
    # Il conteggio vive qui, nello store dei job, e non in un file a parte: è lo
    # stato per-job che la WebUI, il tool ``cron`` e il servizio leggono già
    # insieme al resto, e che eredita atomicità, backup e quarantena dello store.
    # Azzerato da un run che dimostra che il controllo è avvenuto (``ok`` o
    # ``silenced``); ``error`` e ``skipped`` lo lasciano com'è, perché non
    # dimostrano né l'una né l'altra cosa.
    consecutive_could_not_check: int = 0
    # Inizio della sequenza in corso: è la risposta a "da quando è rotto?", che
    # il conteggio da solo non dà.
    could_not_check_since_ms: int | None = None
    # L'utente è già stato avvisato di QUESTA sequenza. Ciò che rende l'avviso
    # uno solo per guasto invece di uno per ciclo.
    could_not_check_escalated: bool = False
    # *Quando* glielo si è detto. Senza, la sentinella dei controlli morti
    # (``silence_watchdog``) non sa se l'avviso è di adesso o di ieri, e
    # dalla sesta mancata riavvisava a ogni giro chi era già stato avvisato.
    could_not_check_escalated_at_ms: int | None = None

    # Il run precedente ha **delegato** il controllo a un aiutante e il verdetto
    # non è ancora tornato. Impedisce che quel silenzio azzeri la sequenza; vive
    # al massimo un ciclo (v. :mod:`jenny.cron.monitor_followup`).
    pending_since_ms: int | None = None

    # ── condizione: già avvisato ──
    # Un ricorrente con ``payload.condition`` ha parlato perché la condizione è
    # diventata vera, e non riparla finché non torna falsa. Una lettura falsa
    # lo riarma.
    condition_notified: bool = False


@dataclass
class CronJob:
    """A scheduled job."""
    id: str
    name: str
    enabled: bool = True
    schedule: CronSchedule = field(default_factory=lambda: CronSchedule(kind="every"))
    payload: CronPayload = field(default_factory=CronPayload)
    state: CronJobState = field(default_factory=CronJobState)
    created_at_ms: int = 0
    updated_at_ms: int = 0
    delete_after_run: bool = False
    # Quando l'utente l'ha messo in pausa dall'officina; ``None`` se non lo e'.
    # **E' la sola cosa che distingue la pausa** dagli altri due modi in cui un
    # job e' ``enabled = False``: un ``at`` gia' eseguito e un job senza sessione
    # a cui consegnare. Senza, «Riprendi» su un promemoria concluso lo rifarebbe
    # partire. Assente negli store scritti prima del 26/09/2026: vale «non in
    # pausa», e una versione vecchia che non lo conosce lascia il job spento.
    paused_at_ms: int | None = None

    @classmethod
    def from_dict(cls, kwargs: dict):
        state_kwargs = dict(kwargs.get("state", {}))
        state_kwargs["run_history"] = [
            record if isinstance(record, CronRunRecord) else CronRunRecord(**record)
            for record in state_kwargs.get("run_history", [])
        ]
        state_kwargs.pop("task_checks", None)
        kwargs["schedule"] = CronSchedule(**kwargs.get("schedule", {"kind": "every"}))
        kwargs["payload"] = CronPayload(**kwargs.get("payload", {}))
        kwargs["state"] = CronJobState(**state_kwargs)
        return cls(**kwargs)


@dataclass
class CronStore:
    """Persistent store for cron jobs."""
    version: int = 1
    jobs: list[CronJob] = field(default_factory=list)


# Fino al 10/10/2026 un job senza nome prendeva come nome i primi 30 caratteri
# del suo ``message``, cioe' l'istruzione inglese per l'agente: e quel nome
# finiva nel titolo della notifica («Remind the user to water the p»). Ora un
# job senza nome resta senza; per quelli gia' nello store si riconosce il
# ripiego vecchio confrontandolo col messaggio.
_LEGACY_NAME_CHARS = 30


def job_user_label(job: CronJob) -> str | None:
    """Il nome del job da mostrare all'utente, o ``None`` se non ne ha uno vero.

    ``None`` per un nome vuoto e per il ripiego di una volta (``message[:30]``):
    e' quel che va nel titolo di una notifica, dove un'istruzione per l'agente
    non deve mai arrivare.
    """
    name = (job.name or "").strip()
    if not name:
        return None
    message = job.payload.message or ""
    if message and name == message[:_LEGACY_NAME_CHARS].strip():
        return None
    return name


def job_display_name(job: CronJob) -> str:
    """Un nome sempre non vuoto per elenchi e log: il nome, o l'inizio del messaggio.

    Per le superfici che hanno bisogno di *qualcosa* da scrivere (l'elenco del
    tool ``cron``, il pannello dei job) — mai per il titolo di una notifica, che
    usa :func:`job_user_label`.
    """
    name = (job.name or "").strip()
    if name:
        return name
    return (job.payload.message or "")[:_LEGACY_NAME_CHARS].strip() or job.id
