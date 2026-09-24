"""Il terzo esito dell'heartbeat, **per singolo task** (B13).

B8 ha dato ai monitor un terzo stato — "non ho potuto controllare" — ma si è
fermato prima dell'heartbeat, che è però il posto dove il guasto è stato
osservato davvero: il controllo delle piante gira da ``HEARTBEAT.md``, non da un
job ``mode='monitor'``.

La differenza che rende l'heartbeat un problema a sé è la granularità. Un run
copre **N task** scritti a mano in un file Markdown, e un contatore solo per
tutto il file sarebbe la forma sbagliata: un task rotto su quattro sani farebbe
oscillare il contatore, e all'utente arriverebbe "l'heartbeat è rotto" mentre
tre quarti funziona. Qui lo stato è per task.

**Identità di un task fra un run e l'altro.** ``HEARTBEAT.md`` è un file libero
scritto dall'utente: non c'è un id, e non lo si vuole chiedere (l'utente non
deve riformattare il proprio file perché noi abbiamo bisogno di una chiave).
L'identità è quindi l'hash del **testo normalizzato** del task — marcatori di
lista e checkbox via, spazi compressi, minuscole.

Il modo in cui questo può sbagliare, scelto sapendo:

- riscrivere un task (anche solo correggere un refuso) produce un id nuovo,
  quindi la sequenza di guasti riparte da zero. Si perde al più un avviso
  ritardato di K cicli; **non** si eredita una sequenza che parlava di un altro
  controllo, e non si azzera per un motivo invisibile. È la direzione sicura:
  un falso allarme su un task che l'utente ha appena riscritto sarebbe peggio.
- spostare un task nel file non cambia niente (l'id non dipende dalla
  posizione), che è il caso frequente quando si riordina l'elenco.
- due task con lo stesso identico testo collassano su un id solo: sono la stessa
  istruzione scritta due volte.

Lo stato si **autoripara**: a ogni run le voci dei task che non esistono più nel
file vengono buttate, quindi lo store non cresce all'infinito e un task
cancellato non lascia niente dietro di sé.

**Un run dell'heartbeat non è un turno solo.** L'agente principale gira in
``orchestrator_mode`` e non ha ``python_exec``: un task che deve *fare* qualcosa
viene delegato con ``spawn``, che ritorna subito. Il turno dell'heartbeat
finisce quindi prima che il subagent abbia iniziato, e il suo testo finale non
può contenere il verdetto di quel task. Il ciclo è:

- **T0**, il turno dell'heartbeat: legge il file, esegue ciò che può, delega il
  resto. Per i task delegati dichiara ``CHECK_DELEGATED <n>``, che qui diventa
  ``pending_since_ms`` — la voce non viene potata e la sequenza non riparte.
- **T1…Tn**, i turni d'annuncio dei subagent: arrivano più tardi nella stessa
  sessione, hanno il risultato in mano e sono l'unico posto in cui quel task può
  essere giudicato. Ci pensa :func:`record_followup_outcomes`, che scrive il
  verdetto nei due versi — ``CHECK_FAILED`` conta un guasto, ``CHECK_OK`` chiude
  la voce — ma non pota nient'altro, perché un turno d'annuncio vede un
  risultato, non il file.

**Perché il successo si dichiara invece di dedursi.** Su un controllo delegato
il silenzio del turno d'annuncio ha due letture — "è andato bene" e "è ancora
rotto ma mi sono dimenticato di dirlo" — e sono la stessa osservazione. Finché
lo sono, ogni regola scritta sopra sceglie fra due errori: riavvisare dello
stesso guasto a ogni ciclo, o non riavvisare mai più dopo il primo. Il
marcatore positivo le separa alla fonte, ed è per questo che esiste.

Se un verdetto non arriva affatto — nessuno dei due marcatori, o il subagent
perso — la voce resta ``pending`` fino al run dopo, che la risolve in modo
**ottimistico**: il task conta come eseguito. È la stessa direzione d'errore di
tutto il resto del modulo — al massimo si tace su un guasto che nessuno ha
dichiarato, mai si avvisa di un guasto che non c'è — ed è ciò che impedisce a
una voce in sospeso di restare appesa più di un ciclo. Una cosa sola sopravvive
a quella risoluzione, ed è l'unica che non riguarda il controllo: il fatto che
all'utente si sia già parlato (v. :func:`resolve_pending_delegations`).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace

from loguru import logger

from jenny.cron.could_not_check import (
    COULD_NOT_CHECK_MARKER,
    DELEGATED_MARKER,
    ESCALATE_AFTER_FAILURES,
    ESCALATION_ASK_LIMIT,
    OK_MARKER,
    REASON_MAX_CHARS,
    WARNED_MARKER,
    CouldNotCheckMark,
)
from jenny.cron.types import CronJobState, CronTaskCheckState

# Un elemento di lista di primo livello apre un task nuovo: ``- ``, ``* ``,
# ``+ ``, ``1. ``, ``1) ``. Le righe rientrate sono continuazioni dello stesso.
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")

# ``- [ ] task`` / ``- [x] task``: il checkbox non fa parte del testo.
_CHECKBOX_RE = re.compile(r"^\[[ xX]\]\s*")

# Quanto di un task finisce nell'elenco numerato del prompt e nello stato: è
# un'etichetta per riconoscerlo, non il task.
_LABEL_MAX_CHARS = 90

_LABEL_DECORATION = "*_`#> \t"


@dataclass(frozen=True)
class HeartbeatTask:
    """Un task di ``HEARTBEAT.md``, come lo vede questo modulo."""

    id: str
    index: int
    """Posizione 1-based: è il numero che il modello scrive nel marcatore."""
    label: str
    text: str
    failed_runs: int = field(default=0, compare=False)
    """Run mancati di fila, e li scrive solo :func:`tasks_due_for_escalation`.

    Sta qui e non nel solo stato perché è il motivo per cui il task è finito
    nell'elenco di :func:`escalation_block`, e quel blocco lo deve *dire*: il
    numero che il modello riferisce all'utente ("non parte da N giri") deve
    essere quello vero. Fuori da quella lista vale ``0``, che significa "non
    l'ha chiesto nessuno", non "zero guasti".

    ``compare=False``: un task è lo stesso task quale che sia la sua sequenza —
    l'identità resta ``id``/``index``/``label``/``text``, e ``__eq__`` con
    ``__hash__`` continuano a rispondere come prima.
    """


@dataclass
class HeartbeatCheckOutcome:
    """Cosa ha prodotto un run dell'heartbeat, dal punto di vista dei guasti."""

    failed: list[HeartbeatTask] = field(default_factory=list)
    """Task che il modello ha dichiarato non eseguiti in questo run."""

    reasons: dict[str, str] = field(default_factory=dict)
    """``task.id`` → motivo dichiarato (può essere vuoto)."""

    unattributed: list[str] = field(default_factory=list)
    """Marcatori senza un numero di task riconoscibile, con più task in gioco."""

    pending: list[HeartbeatTask] = field(default_factory=list)
    """Task delegati a un subagent: l'esito arriverà col turno d'annuncio."""

    recovered: list[HeartbeatTask] = field(default_factory=list)
    """Task delegati che il turno d'annuncio ha dichiarato riusciti (``CHECK_OK``)."""

    escalated: bool = False
    """Il run doveva avvisare l'utente e ha dichiarato di averlo fatto.

    La dichiarazione è la riga ``CHECK_WARNED`` del task: non "è uscito un
    messaggio in questo turno", che è vero anche per un messaggio su altro.
    """

    @property
    def any_failure(self) -> bool:
        return bool(self.failed or self.unattributed)

    def summary(self) -> str:
        """Riga corta per ``CronJobState.last_error`` e per la run record."""
        parts = [f"{t.label}: {self.reasons.get(t.id) or 'no reason given'}" for t in self.failed]
        parts += [r or "no reason given" for r in self.unattributed]
        return "; ".join(parts)[:REASON_MAX_CHARS]


def _is_section_heading(stripped: str) -> bool:
    """``## Titolo``, e non ``### Titolo``: il livello che delimita una sezione."""
    return stripped.startswith("##") and not stripped.startswith("###")


def _opens_active_tasks(stripped: str) -> bool:
    """La riga che apre la sezione dei task, a qualunque punto del file compaia.

    È il delimitatore su cui il parser si orienta, cioè roba nostra: chi la
    incontra la tratta come struttura, mai come una riga che l'utente ha scritto
    per dire qualcosa.
    """
    return (
        _is_section_heading(stripped)
        and stripped.lstrip("#").strip().lower().startswith("active tasks")
    )


def _uncommented_lines(content: str, source: str | None = None) -> Iterator[tuple[str, bool]]:
    """Le righe del file senza i commenti HTML, ognuna con "sono in Active Tasks".

    La macchina a stati dei commenti — quelli su una riga sola e quelli su più
    righe — vive qui e in nessun altro posto. I lettori di questo formato sono
    due (i task, e il testo che va nel prompt) e devono restare d'accordo: se
    divergessero, al modello finirebbe sotto gli occhi un task che il parser non
    conta, o viceversa.

    Le righe di intestazione escono da qui come tutte le altre — è chi chiama a
    decidere se sono contenuto o solo struttura — e lo stato si aggiorna **prima**
    dello ``yield``, quindi ``## Active Tasks`` appartiene alla sezione che apre
    e ``## Archive`` a quella che apre lui, non a quella che chiude. Invertire le
    due righe non rompe nessun test di attribuzione dei task (una sezione che
    finisce non ne contiene) ma fa colare nel prompt l'intestazione che chiude
    l'elenco: v. ``test_a_heading_that_closes_the_section_stays_out``.

    ``source`` è il nome del file da cui viene *content*, e serve solo a poterlo
    nominare in un avviso: un commento HTML mai chiuso si mangia in silenzio
    tutto ciò che gli sta sotto. Lo passa il chiamante che costruisce il prompt —
    uno solo, altrimenti lo stesso file rotto produrrebbe due avvisi per run.
    """
    in_comment = False
    in_active_section = False
    stray = 0
    for line in content.splitlines():
        stripped = line.strip()
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        if stripped.startswith("<!--"):
            if "-->" not in stripped[4:]:
                in_comment = True
            continue
        if _is_section_heading(stripped):
            in_active_section = _opens_active_tasks(stripped)
        if not in_active_section and stripped and not stripped.startswith("#"):
            stray += 1
        yield line, in_active_section
    if stray and source is not None:
        # Prosa fuori dalla sezione. Non è un errore — un file può avere
        # un'introduzione, un ``## Notes``, un ``## Archive`` — ma è anche la forma
        # che prende un guasto silenzioso: solo ``## Active Tasks`` viene letta,
        # quindi un task scritto altrove non gira **e non lo dice nessuno**. Un
        # file con un task nel posto sbagliato è indistinguibile da un file con un
        # task in meno.
        #
        # Il caso concreto: ``agent/scheduling.md`` dice all'agente di aggiungere
        # una riga a ``HEARTBEAT.md``, e appendere a fine file — la cosa più
        # naturale — su un file che ha una sezione dopo l'elenco scrive un task che
        # non partirà mai. Il prompt ora dice dove va; questa riga è il presidio
        # per quando quell'istruzione non viene seguita.
        #
        # Livello ``debug`` e non ``warning``: quelle righe sono legittime nella
        # maggioranza dei casi, e un warning a ogni run su ogni file con delle note
        # sarebbe il rumore da cui si scappa. Basta che sia grep-abile quando si va
        # a cercare perché un task non è partito.
        logger.debug(
            "{}: {} line(s) outside the '## Active Tasks' section are not read as "
            "tasks — check that nothing meant to run was written there",
            source, stray,
        )
    if in_comment and source is not None:
        # Il file è malformato, non lo è il parser: da un ``<!--`` senza chiusura
        # in poi non arriva più niente né al conteggio né al prompt. Il conteggio
        # faceva già così, ma finché il prompt portava il file grezzo il modello
        # quei task li vedeva lo stesso e li eseguiva; ora non li vede nessuno.
        # Non si indovina dove l'utente voleva chiudere il commento — si dice
        # quale file guardare.
        logger.warning(
            "{}: an HTML comment is never closed, so everything after it is "
            "ignored — any task written below that point will not run",
            source,
        )


def _active_task_lines(content: str) -> Iterator[str]:
    """Righe della sezione "Active Tasks", righe vuote comprese.

    Intestazioni e commenti HTML fuori; le righe vuote della sezione invece
    vengono restituite: servono a separare un task dall'altro.
    """
    for line, in_active_section in _uncommented_lines(content):
        if not in_active_section:
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        yield line if stripped else ""


def active_section_text(content: str, source: str | None = None) -> str:
    """La sezione "Active Tasks" com'è scritta, meno i commenti HTML.

    Serve al prompt dell'heartbeat, e la differenza da :func:`_active_task_lines`
    è tutta in cosa si considera nostro e cosa dell'utente. I commenti HTML sono
    nostri: il template ne spedisce uno che spiega come funziona il file, e finché
    il prompt si costruiva dal file grezzo il modello se lo rileggeva a ogni run,
    per sempre, su ogni installazione. Le intestazioni **no**: un
    ``### WaterBot: monitoraggio umidità piante`` sopra quattro righe è ciò che
    dice di cosa parlano quelle righe, e toglierlo lascerebbe un "notifica una
    sola volta per pianta" senza soggetto.

    Quindi qui esce tutto il resto verbatim — intestazioni, righe vuote,
    rientri — e la sola cosa che ci si aggiunge è togliere le righe vuote agli
    estremi, che dopo la rimozione di un commento sono quasi sempre le sue.
    "Vuote" include quelle di soli spazi: un ``strip("\\n")`` sul testo unito ne
    lasciava passare una intatta, e sono esattamente quelle che restano dove
    stava un commento indentato — cioè il caso per cui questa potatura esiste.
    Fra le due letture della frase si è scelta quella che la rende vera, invece
    di riscrivere la frase: il costo è nullo e il risultato resta deterministico.

    Cosa **non** fa: decidere che cos'è un task. Quello resta di
    :func:`parse_heartbeat_tasks`, che non è cambiata e non deve cambiare —
    l'identità di un task è l'hash del suo testo, e con essa è indicizzato lo
    stato dell'escalation già scritto sul dispositivo dell'utente.

    ``source`` viaggia fino a :func:`_uncommented_lines` e serve solo a nominare
    il file in un avviso: v. lì.
    """
    # Via l'intestazione ``## Active Tasks`` — ogni sua occorrenza, non solo la
    # prima. È il delimitatore su cui il parser si orienta, non una riga che
    # l'utente ha scritto per dire qualcosa, e un file che riapre la sezione più
    # avanti (dopo ``## Notes``, tipicamente) se la ritrovava in mezzo al prompt.
    lines = [
        line
        for line, in_active_section in _uncommented_lines(content, source)
        if in_active_section and not _opens_active_tasks(line.strip())
    ]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _strip_markers(line: str) -> str:
    return _CHECKBOX_RE.sub("", _LIST_ITEM_RE.sub("", line, count=1).strip()).strip()


def _task_id(text: str) -> str:
    """Hash del testo normalizzato: v. la nota sull'identità nel docstring."""
    normalized = " ".join(_strip_markers(line).lower() for line in text.splitlines())
    normalized = " ".join(normalized.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _task_label(text: str) -> str:
    label = " ".join(_strip_markers(text.splitlines()[0]).split()).strip(_LABEL_DECORATION)
    if len(label) > _LABEL_MAX_CHARS:
        label = label[: _LABEL_MAX_CHARS - 1].rstrip() + "…"
    return label


def parse_heartbeat_tasks(content: str) -> list[HeartbeatTask]:
    """Spezza ``HEARTBEAT.md`` nei task che l'utente ci ha scritto.

    Un task è un blocco: un elemento di lista di primo livello con le sue righe
    rientrate, oppure — per chi scrive in prosa — un paragrafo separato da righe
    vuote. Nessuna convenzione nuova imposta al file: entrambe le forme che un
    utente usa già danno un task per unità di senso.
    """
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in _active_task_lines(content):
        if not line.strip():
            if current:
                blocks.append(current)
                current = []
            continue
        opens_item = bool(_LIST_ITEM_RE.match(line)) and not line[:1].isspace()
        if opens_item and current:
            blocks.append(current)
            current = []
        current.append(line.strip())
    if current:
        blocks.append(current)

    tasks: list[HeartbeatTask] = []
    for index, block in enumerate(blocks, start=1):
        text = "\n".join(block)
        tasks.append(
            HeartbeatTask(id=_task_id(text), index=index, label=_task_label(text), text=text)
        )
    return tasks


def task_index_block(tasks: list[HeartbeatTask]) -> str:
    """L'elenco numerato che dà al modello un modo di nominare un task.

    Deterministico a parità di file: un run sano lo vede identico ogni volta, e
    non contiene niente che riguardi i guasti.
    """
    lines = "\n".join(f"{t.index}. {t.label}" for t in tasks)
    return (
        f"\nThe tasks above, numbered. Use these numbers ONLY in a "
        f"{COULD_NOT_CHECK_MARKER} or {DELEGATED_MARKER} line, never in a "
        f"message to the user:\n{lines}\n"
    )


def resolve_pending_delegations(state: CronJobState) -> list[str]:
    """Chiude le deleghe di cui non è mai arrivato **nessun** verdetto. Ritorna le etichette.

    Va chiamata **all'inizio** di un run, prima che qualcuno legga lo stato: una
    voce ancora in sospeso qui significa che il ciclo precedente si è chiuso
    senza che nessuno abbia dichiarato niente su quel task, e vale quindi la
    regola generale del modulo — nessuna dichiarazione, quindi il controllo è
    avvenuto. La sequenza riparte.

    Prima che decidere l'escalation, perché è ciò che impedisce il difetto
    speculare a quello che tutto questo esiste per correggere: un controllo
    delegato che *si è ripreso* lascia dietro di sé una voce con il conteggio
    vecchio, e leggerla prima di risolverla farebbe entrare nel prompt la
    richiesta di avvisare l'utente di un guasto che non c'è più.

    **Cosa arriva qui, e cosa no.** Da quando il turno d'annuncio dichiara anche
    il successo (``CHECK_OK``, v. :func:`record_followup_outcomes`), un recupero
    ha una strada propria e non passa più di qua: qui resta il caso in cui il
    verdetto non è arrivato affatto — il subagent non è mai tornato, o il
    modello non ha scritto nessuno dei due marcatori. Di quel silenzio non si
    può dedurre niente sul guasto, e infatti la sequenza si azzera; ma si può
    dedurre tutto su **ciò che è già stato detto all'utente**, che è un fatto
    nostro e non del controllo. Quello si conserva.
    """
    stale = [
        task_id
        for task_id, entry in state.task_checks.items()
        if entry.pending_since_ms is not None
    ]
    labels = [state.task_checks[task_id].label for task_id in stale]
    for task_id in stale:
        entry = state.task_checks[task_id]
        if not entry.escalated:
            del state.task_checks[task_id]
            continue
        # Con l'utente già avvisato resta una cosa sola da ricordare, ed è
        # quella. ``escalated`` non è un'affermazione sul guasto — è il verbale
        # di aver parlato — e buttarlo qui non serve a nessuna delle due
        # garanzie per cui questa risoluzione esiste: faceva solo ricominciare
        # la sequenza da capo fino a riavvisare, ogni due ore, dello stesso
        # guasto. La sequenza riparte comunque da zero, quindi la direzione
        # dell'errore non cambia: da uno stato vecchio non può nascere un
        # allarme.
        entry.consecutive_could_not_check = 0
        entry.since_ms = None
        entry.pending_since_ms = None
    return labels


def rearm_after_user_message(
    state: CronJobState, *, user_spoke_at_ms: int | None
) -> list[str]:
    """L'utente ha scritto dopo l'avviso: quel guasto torna a essere una notizia.

    Va chiamata **prima** di leggere lo stato per costruire un prompt, dove la
    chiama :func:`resolve_pending_delegations` — e per lo stesso motivo: sono
    entrambe cose successe *fra* un turno e l'altro, e leggere lo stato prima di
    applicarle metterebbe nel prompt una domanda già scaduta.

    Perché una transizione di stato e non una condizione dentro
    :func:`tasks_due_for_escalation`. Se ha scritto, l'avviso l'ha letto: la
    decisione è "ricominciamo da capo su questo task", e ricominciare da capo
    include la **sequenza**. Il tetto che si crede di avere — la soglia di
    ``ESCALATE_AFTER_FAILURES``, cioè un avviso ogni novanta minuti — durante un
    guasto prolungato non esiste: dopo sei ore la sequenza vale 12 e la soglia
    non si riattraversa più, quindi un task riarmato sarebbe dovuto già al run
    successivo e tre messaggi in una serata varrebbero tre avvisi. Azzerandola
    qui il tetto torna a valere, e il costo è che il primo dei due avvisi
    possibili arriva mezz'ora più tardi.

    Ciò che **non** riarma, e sono la stessa cosa vista da due lati: una voce
    senza timbro (``escalated_at_ms is None``) è una voce scritta prima che il
    timbro esistesse, e "gli si è parlato, non si sa quando" non è confrontabile
    con niente. Restano zitte, che è la direzione sicura.

    Ritorna le etichette dei task riarmati, per il log.
    """
    if user_spoke_at_ms is None:
        return []
    rearmed: list[str] = []
    for entry in state.task_checks.values():
        if not entry.escalated or entry.escalated_at_ms is None:
            continue
        if user_spoke_at_ms <= entry.escalated_at_ms:
            continue
        entry.escalated = False
        entry.escalated_at_ms = None
        entry.consecutive_could_not_check = 0
        entry.since_ms = None
        rearmed.append(entry.label)
    return rearmed


def _escalation_already_given(entry: CronTaskCheckState | None) -> bool:
    """L'unica lettura di ``escalated``, per i due blocchi che si contraddirebbero.

    :func:`tasks_due_for_escalation` e :func:`tasks_already_warned` sono il
    complemento l'una dell'altra e finiscono nello **stesso prompt**: se
    divergessero di un caso, quel prompt chiederebbe insieme di parlare e di
    tacere dello stesso controllo. Negano quindi la stessa funzione, e non due
    condizioni scritte a mano che si somigliano
    (v. ``test_a_task_is_never_both_due_and_already_warned``).
    """
    return entry is not None and entry.escalated


def tasks_due_for_escalation(
    state: CronJobState, tasks: list[HeartbeatTask]
) -> list[HeartbeatTask]:
    """Task per cui è QUESTO run a dover avvisare, se anche lui non li esegue.

    Il conteggio nello stato riguarda i run già conclusi, quindi la soglia si
    confronta con ``K - 1``: con K=3 l'istruzione entra nel prompt del terzo
    tentativo, ed è quel turno — l'unico che sappia se il controllo è riuscito
    adesso — a decidere se chiamare ``message``. Nessun turno in più, e nessuna
    consegna generata da fuori il turno: il dispatcher cron non ne ha una, per
    scelta (v. la docstring di ``jenny/runtime/cron_dispatch.py``).

    I task tornano con la propria sequenza in ``failed_runs``, perché è l'unico
    posto che la conosce e :func:`escalation_block` la deve scrivere nel prompt.
    Copie: ``HeartbeatTask`` è ``frozen`` e ``failed_runs`` non entra nel
    confronto, quindi restano uguali agli originali per chiunque le guardi.

    Ed è una finestra, non una semiretta: si chiede per ``ESCALATION_ASK_LIMIT``
    run e poi si smette, per lo stesso motivo del monitor (v.
    ``should_escalate_could_not_check`` in ``jenny/cron/bound_runner.py``). Il
    timbro che chiuderebbe la richiesta lo scrive il modello con
    ``CHECK_WARNED``; dove la finestra finisce comincia ``silence_watchdog``.
    """
    due: list[HeartbeatTask] = []
    first_ask = ESCALATE_AFTER_FAILURES - 1
    for task in tasks:
        entry = state.task_checks.get(task.id)
        if entry is None or _escalation_already_given(entry):
            continue
        if first_ask <= entry.consecutive_could_not_check < first_ask + ESCALATION_ASK_LIMIT:
            due.append(replace(task, failed_runs=entry.consecutive_could_not_check))
    return due


def tasks_already_warned(
    state: CronJobState, tasks: list[HeartbeatTask]
) -> list[HeartbeatTask]:
    """Task di cui l'utente è già stato avvisato e che non sono ancora rientrati.

    Il complemento di :func:`tasks_due_for_escalation` sullo stesso stato: là i
    task per cui parlare, qui quelli per cui **non** parlare più.
    """
    return [
        task
        for task in tasks
        if _escalation_already_given(state.task_checks.get(task.id))
    ]


def already_warned_block(tasks: list[HeartbeatTask]) -> str:
    """Le righe che chiedono di tacere. Simmetriche a :func:`escalation_block`.

    Perché serve dirlo, invece di limitarsi a non chiedere di parlare: fino a
    ieri un task già annunciato semplicemente non entrava nel blocco di
    escalation, e il prompt su di lui taceva. Ma "non ti sto chiedendo di
    parlare" non è "non parlare" per un modello che il guasto ce l'ha davanti —
    la coda della sessione dell'heartbeat contiene il proprio avviso di due ore
    prima e il guasto ancora lì. Misurato sul device: al quarto ciclo
    consecutivo il modello ha chiamato ``message`` di propria iniziativa, con
    l'escalation già data, raddoppiando ogni avviso.

    Compare solo quando c'è almeno un guasto già annunciato, cioè quasi mai: il
    prompt di un run sano resta byte-identico a quello del run precedente, che è
    ciò su cui si regge la cache di prefisso del provider.
    """
    listed = "\n".join(f"- {t.index}. {t.label}" for t in tasks)
    return (
        "[The user has ALREADY been told that these recurring checks are not "
        "working, and nothing has changed since:\n"
        f"{listed}\n"
        "Do not tell them again. Not a reminder, not an update, not a shorter "
        "version — saying it twice is what makes a useful warning into noise, "
        "and repeating it every cycle is how a person learns to ignore it. Say "
        "nothing about these, whatever you find about them this time. You will "
        "be asked to speak again if one of them starts working and then breaks "
        "a second time, or if the user writes to you and it is still broken — "
        "and either way you will be asked, so until then, nothing.\n"
        "Everything else about this run is unaffected: keep writing the "
        f"{COULD_NOT_CHECK_MARKER} / {OK_MARKER} lines as usual, and if a "
        "DIFFERENT check needs the user, that is a separate matter and the "
        "instructions above apply to it.]\n\n"
    )


def escalation_block(tasks: list[HeartbeatTask]) -> str:
    """Le righe di prompt che chiedono di parlare. Solo quando c'è un guasto.

    Un messaggio solo per tutti i task rotti: N task che si rompono insieme —
    tipicamente per la stessa causa, un host giù — devono costare un'interruzione
    sola. E l'avviso nomina il controllo, non l'heartbeat: "il controllo delle
    piante non parte" è utile, "l'heartbeat è rotto" no.

    Il conto sta su ogni riga, e non una volta sola nella frase d'apertura, per
    due motivi che sono lo stesso. Non è ``ESCALATE_AFTER_FAILURES``: la soglia
    scatta a ``K - 1``, quindi qui i run mancati sono due e la costante ne
    dichiarava tre — un numero che il modello riferisce all'utente, di una cosa
    non ancora successa. E non è nemmeno un numero solo: la lista può nominare
    un task alla soglia insieme a uno di cui il modello aveva già ignorato
    l'istruzione, e le due sequenze non coincidono. Prende quindi ``failed_runs``
    da :func:`tasks_due_for_escalation`, che è la sola a saperlo.
    """
    listed = "\n".join(f"- {t.index}. {t.label} ({t.failed_runs} runs in a row)" for t in tasks)
    return (
        "[These recurring tasks have now failed to run, and the user has not "
        "been told:\n"
        f"{listed}\n"
        "If one of them fails again this time, the user must find out. Call the "
        "`message` tool EXACTLY ONCE — one message for all of them, never one "
        "per task — and say in their language which recurring check has not been "
        "working and what is stopping it: plainly, in their terms, with no task "
        "numbers, no internal file names and no jargon. Say nothing about the "
        "tasks that are working, and if one of the listed tasks works this time "
        "do not mention it either: past failures are not news. Then still end "
        f"your answer with the {COULD_NOT_CHECK_MARKER} line for every task that "
        "failed, and — for every task your message actually told them about — one "
        "more line:\n"
        f"{WARNED_MARKER} <number>\n"
        "That line reaches nobody. It is the only record that this warning went "
        "out, and without it you will be asked to send the same warning again on "
        "the next run. Write it only for the tasks you really named to the user, "
        "never for one you decided to leave out.\n"
        "One exception, and it is the usual one: for a task you are handing to a "
        "subagent you do not know yet whether it failed again, so send nothing "
        "now. You will be asked the same question again when its result reaches "
        "you, and that is the turn that decides.]\n\n"
    )


def followup_block(
    pending: list[HeartbeatTask],
    escalating: list[HeartbeatTask],
    already_warned: list[HeartbeatTask] | None = None,
) -> str:
    """Il blocco che chiede il verdetto al turno d'annuncio di un subagent.

    Compare **solo** quando quel turno cade nella sessione dell'heartbeat e c'è
    almeno un task in sospeso: un annuncio qualunque, e un heartbeat che non ha
    delegato niente, vedono il prompt di sempre.

    Porta con sé lo stesso paio di istruzioni condizionali del run — parla di
    questi, taci di quelli — perché è un turno che può consegnare esattamente
    come l'altro, ed è anzi quello che il modulo indica come il turno che
    *decide* per un controllo delegato.
    """
    listed = "\n".join(f"{t.index}. {t.label}" for t in pending)
    return (
        "\n\n[This subagent was doing the work of a scheduled check, and this "
        "turn is where its outcome gets recorded. The checks waiting for a "
        "result, with the numbers to name them by:\n"
        f"{listed}\n"
        "If one of them could NOT actually be carried out — the subagent hit an "
        "error, a script or a file is missing, an import broke, a host was "
        "unreachable, the value never arrived — then for THESE checks, and "
        "overriding anything above about reporting an error that blocks a "
        "check, do not message the user about it. End your answer with one "
        "line per check that did not run, in exactly this form:\n"
        f"{COULD_NOT_CHECK_MARKER} <number>: <one short line naming what stopped it>\n"
        "That line reaches nobody: it is how a check gets recorded as 'could "
        "not check' instead of 'nothing to report', and a streak of them is "
        "what eventually warns the user.\n"
        "And for every check that actually produced its answer — whether that "
        "answer was worth saying or was 'nothing to report' — say so. The direct "
        "way is to call `nothing_to_report` with that check's number, once per "
        "number; writing the equivalent line at the end of your answer works too, "
        "in exactly this form:\n"
        f"{OK_MARKER} <number>\n"
        "Neither reaches anybody, and neither is a message to the user: "
        "it is how a check that had been failing gets recorded as working "
        "again. Without it a check that recovers stays remembered as broken, "
        "and the next real fault goes unreported.\n"
        "The test is whether you learned what the check exists to tell you — "
        "not whether the run ended tidily. A check that had nothing to do this "
        "time because its own schedule or condition said so did its job: "
        f"{OK_MARKER}. But a check that could not reach what it needed did NOT "
        "produce an answer, and stays "
        f"{COULD_NOT_CHECK_MARKER} even when its own instructions told it to "
        "give up quietly in exactly that case — being told not to make noise "
        "about an unreachable host is about sparing the user, never a claim "
        "that the check worked. If you are unsure which of the two a result "
        f"is, it is {COULD_NOT_CHECK_MARKER}: saying a broken check is fine is "
        "the one mistake here that nothing downstream can catch.\n"
        "Every check in the list gets exactly one verdict, one or the other, and "
        "never both — a `nothing_to_report` call counts as its line.]"
        + "\n\n"
        + (already_warned_block(already_warned) if already_warned else "")
        + (escalation_block(escalating) if escalating else "")
    )


def attribute_marks(
    tasks: list[HeartbeatTask],
    marks: list[CouldNotCheckMark],
    *,
    default: HeartbeatTask | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Associa ogni marcatore al suo task. Ritorna ``(motivi, non attribuiti)``.

    Un marcatore senza numero viene attribuito al task quando ce n'è **uno
    solo**: è il caso comune (un file con un task), e chiedere un numero dove
    non c'è niente da distinguere sarebbe solo un'occasione di sbagliarlo. Con
    più task, invece, un marcatore anonimo resta non attribuito: incolpare il
    task sbagliato produrrebbe un avviso su un controllo sano.

    ``default`` restringe quella stessa regola a un candidato scelto da chi
    chiama: sul turno d'annuncio di un subagent il file può avere N task, ma se
    ne è in sospeso **uno solo** un marcatore anonimo non è ambiguo — è quello.
    """
    by_index = {t.index: t for t in tasks}
    fallback = default if default is not None else (tasks[0] if len(tasks) == 1 else None)
    reasons: dict[str, str] = {}
    unattributed: list[str] = []
    for mark in marks:
        task: HeartbeatTask | None = None
        if mark.ref is not None:
            task = by_index.get(int(mark.ref))
        else:
            task = fallback
        if task is None:
            unattributed.append(mark.reason)
            continue
        # Due marcatori sullo stesso task: vince il primo con un motivo scritto.
        if not reasons.get(task.id):
            reasons[task.id] = mark.reason
    return reasons, unattributed


def _sole_candidate(
    tasks: list[HeartbeatTask], candidate_ids: set[str]
) -> HeartbeatTask | None:
    """Il task cui attribuire un ``CHECK_WARNED`` scritto senza numero.

    Un modello che scrive ``CHECK_WARNED`` nudo su un file con quattro task non
    sta essendo ambiguo: sta rispondendo alla sola cosa di cui il turno aveva
    motivo di parlare. Candidato è ogni task per cui quel motivo esiste — un
    guasto dichiarato adesso, uno che il prompt ha chiesto di annunciare, uno di
    cui l'utente è già stato avvisato (``already_warned_block`` esiste perché il
    modello ne riparla lo stesso) — e con **uno solo** il marcatore anonimo non è
    ambiguo. Con due o più resta non attribuito, che vuol dire nessun timbro e
    quindi l'avviso ripetuto al giro dopo: rumore, non silenzio.

    È la stessa regola che :func:`attribute_marks` applica già ai marcatori di
    guasto, ristretta con il suo parametro ``default`` invece di riscritta. Ciò
    che **non** è: l'euristica ``sole_failure`` che questa sostituisce. Là il
    fatto da attribuire era ``spoke``, cioè "in questo turno è uscito un
    messaggio, di qualunque cosa parlasse"; qui è una dichiarazione esplicita di
    aver avvisato, e attribuirla all'unico candidato non inventa niente.
    """
    if len(candidate_ids) != 1:
        return None
    only = next(iter(candidate_ids))
    return next((t for t in tasks if t.id == only), None)


def _count_failure(
    entry: CronTaskCheckState,
    task: HeartbeatTask,
    reason: str,
    *,
    now_ms: int,
    escalating_ids: set[str],
    warned_ids: set[str],
    outcome: HeartbeatCheckOutcome,
) -> None:
    """Un controllo mancato in più per ``task``. Condiviso dai due registratori."""
    entry.consecutive_could_not_check += 1
    entry.label = task.label
    # Il verdetto è arrivato: la voce non è più in attesa di uno.
    entry.pending_since_ms = None
    if entry.since_ms is None:
        entry.since_ms = now_ms
    if task.id in warned_ids:
        # L'avviso è "dato" solo se è davvero uscito, e da chi lo dichiara: un
        # modello che ignora l'istruzione deve ritrovarsela al giro dopo. Vale
        # anche per un avviso di **propria iniziativa** — ``escalated`` è il
        # verbale di aver parlato all'utente, e per chi lo riceve un messaggio
        # spontaneo e uno richiesto sono lo stesso messaggio. Misurato sul Titan
        # 2 il 2026-08-16: al secondo ciclo di guasto, con il blocco di
        # escalation ancora assente dal prompt, il modello ha chiamato
        # ``message`` di testa sua ("il server WaterBot non si raggiunge…").
        #
        # Il soggetto arriva da ``CHECK_WARNED``, e non più da ``spoke`` più
        # un'euristica. ``spoke`` riguarda il TURNO e non il task: era vero per
        # qualunque ``message`` riuscito, e con due candidati nello stesso turno
        # non diceva di quale il modello avesse parlato. L'euristica che lo
        # rendeva attribuibile (``sole_failure``, "se il candidato è uno solo era
        # per lui") sbagliava dalla parte peggiore ogni volta che il messaggio
        # riguardava altro: timbrava un guasto di cui nessuno aveva parlato, e da
        # lì ``already_warned_block`` lo zittiva per sempre. V.
        # ``WARNED_MARKER`` in :mod:`jenny.cron.could_not_check` per la misura.
        entry.escalated = True
        # Timbrato **anche** quando era già ``True``: questo è un avviso nuovo,
        # e lasciargli addosso l'ora del precedente vorrebbe dire che lo stesso
        # messaggio dell'utente — già più recente di quel timbro vecchio — lo
        # riarma di nuovo al ciclo dopo, e a ogni ciclo dopo ancora.
        entry.escalated_at_ms = now_ms
    if task.id in escalating_ids and task.id in warned_ids:
        # Distinto da sopra: questo dice "l'escalation che il run aveva chiesto
        # è davvero uscita", ed è ciò che il dispatcher riporta nella run
        # record. Un avviso spontaneo non è un'escalation richiesta.
        outcome.escalated = True
    outcome.failed.append(task)
    outcome.reasons[task.id] = reason


def record_task_outcomes(
    state: CronJobState,
    tasks: list[HeartbeatTask],
    marks: list[CouldNotCheckMark],
    *,
    now_ms: int,
    escalating: list[HeartbeatTask],
    warned: list[CouldNotCheckMark] | None = None,
    delegated: list[CouldNotCheckMark] | None = None,
) -> HeartbeatCheckOutcome:
    """Aggiorna lo stato per-task dopo il turno dell'heartbeat, e dice cosa è successo.

    Un task senza marcatore conta come eseguito, ed è la stessa fiducia che B8
    fa sul monitor: l'assenza del marcatore è la sola prova disponibile che il
    controllo è avvenuto. La direzione dell'errore è quella giusta — al massimo
    si tace su un guasto che il modello non ha dichiarato, mai si avvisa di un
    guasto che non c'è.

    L'eccezione, e l'unica, sono i task **delegati**: lì l'assenza del marcatore
    non è una prova di niente, perché il turno è finito prima del subagent. Un
    ``CHECK_DELEGATED`` tiene la voce in vita in attesa del verdetto, che
    scriverà :func:`record_followup_outcomes`.

    Presuppone che :func:`resolve_pending_delegations` sia già passata: le
    deleghe del ciclo precedente sono chiuse, e ogni voce ancora qui riguarda
    guasti dichiarati.
    """
    reasons, unattributed = attribute_marks(tasks, marks)
    # Del marcatore di delega interessa solo *quale* task: il testo che il
    # modello ci scrive accanto è per il log, non per lo stato.
    delegated_by_id, _ = attribute_marks(tasks, delegated or [])
    escalating_ids = {t.id for t in escalating}
    # Fotografia PRIMA del ciclo: ``_count_failure`` muta le voci in loco e la
    # riassegnazione in fondo pota i task senza marcatore, quindi un già-avvisato
    # può sparire proprio nel turno in cui serve contarlo.
    already_warned_ids = {tid for tid, e in state.task_checks.items() if e.escalated}
    warned_by_id, _ = attribute_marks(
        tasks,
        warned or [],
        default=_sole_candidate(tasks, reasons.keys() | escalating_ids | already_warned_ids),
    )
    outcome = HeartbeatCheckOutcome(unattributed=unattributed)

    updated: dict[str, CronTaskCheckState] = {}
    for task in tasks:
        entry = state.task_checks.get(task.id)
        if task.id in reasons:
            entry = entry or CronTaskCheckState()
            _count_failure(
                entry, task, reasons[task.id],
                now_ms=now_ms, escalating_ids=escalating_ids,
                warned_ids=set(warned_by_id), outcome=outcome,
            )
            updated[task.id] = entry
            continue
        if task.id in delegated_by_id:
            entry = entry or CronTaskCheckState()
            entry.label = task.label
            entry.pending_since_ms = now_ms
            updated[task.id] = entry
            outcome.pending.append(task)
            continue
        # Eseguito: la voce sparisce, e "assente" vuol dire sano. Tenere uno
        # zero non aggiungerebbe informazione e farebbe crescere lo store.

    # Riassegnazione e non mutazione in loco: è anche la potatura dei task che
    # nel file non ci sono più (rinominati o cancellati).
    state.task_checks = updated
    return outcome


def pending_tasks(state: CronJobState, tasks: list[HeartbeatTask]) -> list[HeartbeatTask]:
    """Task delegati da un run e ancora senza verdetto."""
    pending: list[HeartbeatTask] = []
    for task in tasks:
        entry = state.task_checks.get(task.id)
        if entry is not None and entry.pending_since_ms is not None:
            pending.append(task)
    return pending


def record_followup_outcomes(
    state: CronJobState,
    tasks: list[HeartbeatTask],
    marks: list[CouldNotCheckMark],
    *,
    now_ms: int,
    escalating: list[HeartbeatTask],
    warned: list[CouldNotCheckMark] | None = None,
    ok: list[CouldNotCheckMark] | None = None,
) -> HeartbeatCheckOutcome:
    """Registra il verdetto di un controllo delegato, dal turno d'annuncio.

    Tre differenze dal registratore del run, e tutte seguono da cosa vede questo
    turno:

    - **non pota**. Un turno d'annuncio ha in mano il risultato di *un*
      subagent, non il file: dedurre da qui che gli altri task sono sani
      cancellerebbe sequenze che nessuno ha smentito. La potatura resta al run
      successivo, che il file ce l'ha.
    - **il marcatore anonimo si attribuisce al task in sospeso**, quando ce n'è
      uno solo. È il caso normale: un controllo delegato per volta.
    - **chiude la voce su un verdetto positivo**, ed è l'unico posto del modulo
      che lo fa. Non è potatura per deduzione — la contraddizione con la riga
      sopra è solo apparente: qui non si sta concludendo qualcosa su un task
      *altrui* dal silenzio, si sta registrando ciò che questo turno ha
      dichiarato del task di cui è il verdetto. Sparita la voce sparisce anche
      ``escalated``, ed è così che un guasto nuovo mesi dopo torna ad avvisare.

    Un ``CHECK_OK`` su un task che non è in sospeso viene ignorato: la voce di un
    task non delegato la chiude già il run successivo con la sua regola
    (assente = sano), e prendere qui una scorciatoia darebbe a un turno
    d'annuncio il potere di azzerare la sequenza di un controllo che non ha
    nemmeno guardato.
    """
    pending = pending_tasks(state, tasks)
    default = pending[0] if len(pending) == 1 else None
    reasons, unattributed = attribute_marks(tasks, marks, default=default)
    # Stessa regola di attribuzione dei guasti: con due o più controlli in
    # sospeso un marcatore anonimo non chiude niente. Non toccare è la direzione
    # sicura anche qui — al massimo si aspetta un ciclo in più.
    ok_by_id, _ = attribute_marks(tasks, ok or [], default=default)
    escalating_ids = {t.id for t in escalating}
    # Come nel run, e per lo stesso motivo: il turno d'annuncio porta con sé
    # entrambi i blocchi condizionali, quindi un task già avvisato è un
    # candidato anche qui. Fotografia prima del ciclo, che le voci le muta e le
    # cancella.
    already_warned_ids = {tid for tid, e in state.task_checks.items() if e.escalated}
    warned_by_id, _ = attribute_marks(
        tasks,
        warned or [],
        default=_sole_candidate(tasks, reasons.keys() | escalating_ids | already_warned_ids),
    )
    outcome = HeartbeatCheckOutcome(unattributed=unattributed)
    pending_ids = {t.id for t in pending}
    for task in tasks:
        if task.id in reasons:
            entry = state.task_checks.get(task.id) or CronTaskCheckState()
            _count_failure(
                entry, task, reasons[task.id],
                now_ms=now_ms, escalating_ids=escalating_ids,
                warned_ids=set(warned_by_id), outcome=outcome,
            )
            state.task_checks[task.id] = entry
            continue
        # Il guasto vince sul successo se il modello scrive entrambi: una
        # dichiarazione di guasto è un'informazione, un ``CHECK_OK`` in più è
        # rumore, e l'errore da evitare resta tacere di un guasto.
        if task.id in ok_by_id and task.id in pending_ids:
            del state.task_checks[task.id]
            outcome.recovered.append(task)
    return outcome
