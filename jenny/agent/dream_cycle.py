"""Il ciclo di un run di Dream, nella parte che i suoi due percorsi condividono.

Un run di Dream parte da due posti: il job cron (``jenny/runtime/cron_dispatch.py``)
e lo slash command ``/dream`` (``jenny/command/builtin.py``). Erano due
implementazioni parallele della stessa cosa, e ogni volta che una cresceva
l'altra restava indietro in silenzio — il guard del budget montato solo sul
cron, il gauge assente dal prompt manuale, i contatori del review che non
avanzavano lanciando Dream a mano. Nessuna di quelle era una feature spenta: la
prima era l'enforcement che si aggirava usando il comando, l'ultima un'installazione
in cui il review pass non sarebbe partito mai. Sono state trovate una alla volta
e allineate a mano, ed è l'allineamento a mano la ragione per cui ne sarebbe
arrivata un'altra.

Qui stanno il prologo — misura, riga di log, trigger del review, checkpoint,
ricostruzione delle misure dopo il review — e l'epilogo, cioè l'aritmetica dei
contatori: la parte che per costruzione deve essere la stessa. Più la presa che
tiene i due percorsi a **un ciclo per volta** (:func:`claim_dream_cycle`), che sta
qui per la stessa ragione: duplicarla nei due chiamanti sarebbe due copie da
tenere d'accordo. Dal 24/09/2026 c'è anche il turno incrementale
(:func:`run_dream_turn`): prompt, snapshot, tool, ``process_direct``, gate del
cursore e batch trattenuto erano ancora copiati nei due chiamanti, con la
normalizzazione dei rifiuti già divergente. Ai chiamanti resta ciò che è davvero
loro: ``compact_history`` più il pruning, e la traduzione dell'esito — una riga
di log per il cron, una frase in chat per il comando, che è il motivo per cui
``DreamPrologue.review`` e :class:`DreamOutcome` viaggiano fino a loro invece di
essere consumati qui.

Il modulo vive sotto ``jenny/agent`` e non sotto ``jenny/runtime`` di proposito:
``jenny/command`` importava da ``jenny/runtime/cron_dispatch`` la costante del
trigger, ed era la traccia visibile del problema. Da qui nessuno dei due
pacchetti importa l'altro.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.agent import dream_review
from jenny.agent.memory_budget import budget_report, count_chars, make_write_size_guard
from jenny.session.turn_visibility import silent_progress

if TYPE_CHECKING:
    from jenny.agent.memory import MemoryStore
    from jenny.agent.memory_budget import FileBudget, WriteSizeGuard
    from jenny.config.schema import DreamConfig

# Numero di run consecutivi senza avanzamento del cursore oltre il quale il
# review pass viene forzato. Due e non uno: un singolo run che non avanza è
# ordinario (una scrittura bloccata dalla policy, un turno andato storto) e
# pagare un review pass ogni volta costerebbe più del problema. Due di fila
# invece è una configurazione che si ripete, ed è quella che si autoalimenta.
STUCK_FORCES_REVIEW = 2

# Oltre quanti run consecutivi senza consolidamento **e senza rifiuti** vale la
# pena dirlo. Stessa scala di ``STUCK_IS_ALARMING`` di proposito: sono lo stesso
# fenomeno visto da due cause, e due numeri diversi chiederebbero di ricordare
# quale vale per quale. La differenza sta nel rimedio, non nella soglia — qui non
# si forza niente e non si avvisa l'utente, perché non c'è niente che possa fare.
NOTHING_NEW_IS_NOTABLE = 4

# Oltre questa soglia il livelock non è più un'ipotesi: il review è già stato
# forzato (a 2) e non è bastato. Log a ERROR, perché da qui in poi ogni run è
# un turno LLM che non consolida nulla.
#
# Perché ``stuck`` NON viene azzerato dal review, che sarebbe la cosa istintiva:
# azzerandolo il contatore oscillava 1,2,1,2 e questa soglia non si raggiungeva
# mai — l'allarme era codice morto, e per giunta l'unico allarme che questo
# meccanismo abbia. Un contatore che il review azzera risponde a "da quanto
# aspetto il prossimo review", che è una domanda a cui ``runs_since_review``
# risponde già. Quella a cui deve rispondere ``stuck`` è "da quanti run di fila
# Dream non consolida", e a quella un review che non ha risolto niente non è una
# risposta. Lo azzera solo un cursore che avanza, cioè il problema che finisce.
STUCK_IS_ALARMING = 4

# Fra quanti run si ritenta un review pass che è **fallito**.
#
# Un review che dichiara ``STATUS_FAILED`` non ha fatto la manutenzione, quindi
# azzerare la cadenza come se l'avesse fatta gli regala l'intero intervallo (con
# il default, dodici run: circa un giorno). Ma nemmeno "riprova subito": il
# fallimento più probabile è una migrazione che il budget ha troncato, e
# ritentarla a ogni run è un turno LLM ogni due ore — la stessa spesa a vuoto che
# il modulo esiste per evitare. Due run è il compromesso, e non è un numero
# nuovo: è la stessa distanza con cui ``STUCK_FORCES_REVIEW`` reagisce a un
# problema che si ripete.
REVIEW_RETRY_AFTER_RUNS = 2


# ---------------------------------------------------------------------------
# Un ciclo di Dream per volta
# ---------------------------------------------------------------------------

# Chi tiene la presa, o vuoto se nessuno: l'``AbstractEventLoop`` su cui il ciclo
# in volo sta girando. Vuoto = libero.
#
# **Perché serve.** I due percorsi di Dream — il job cron e ``/dream`` — non si
# escludono per costruzione. I job cron sono serializzati fra loro, quindi il caso
# raggiungibile è ``/dream`` battuto mentre il job delle due ore gira, oppure
# ``/dream`` due volte: ``cmd_dream`` faceva ``asyncio.create_task`` senza chiedere
# niente a nessuno. E il lock per sessione non li separa, perché
# ``MemoryStore.dream_session_key()`` è ``dream:%Y%m%d-%H%M%S``, cioè una chiave
# diversa a ogni run — di proposito, v. la decisione lì.
#
# Cosa costano due cicli sovrapposti, nell'ordine in cui pesano: entrambi leggono
# ``runs_since_review`` e, se è dovuto, entrambi chiamano ``run_dream_review``, cioè
# due passate di review consecutive sugli stessi file; entrambi prendono lo stesso
# batch dallo stesso ``.dream_cursor``; e il read-modify-write dei contatori in
# ``finish_dream_cycle`` perde un tick.
#
# **Quanto costano, misurato.** Non fatti irrecuperabili: da T2.4b
# ``make_entry_archiver`` è montato al confine del file su tutti e quattro i tool di
# Dream e archivia in ``memory/archive/`` ogni voce che esce da ``USER.md`` /
# ``MEMORY.md`` / ``SOUL.md`` prima che la scrittura atterri — verificato sul device
# con ``reviewEveryRuns: 1``, dieci voci archiviate e nessuna persa (piano memoria,
# fase 2, defect D4). Due review di fila costano quindi **token e rumore**, non
# informazione. Che è un motivo sufficiente per una presa, e non per una paura.
#
# **Un registro di processo e non un ``asyncio.Lock``**, per la stessa ragione del
# giardiniere (``agent/gardener.py::_PASSES_IN_FLIGHT``): il secondo ciclo non deve
# mettersi in coda, deve essere **rifiutato**. Mettersi in coda vorrebbe dire che
# ``/dream`` risponde fra un minuto con il lavoro di qualcun altro già fatto, e che
# il tick del cron dopo trova la coda ancora piena. Non serve un lock intorno:
# controllo e inserimento stanno nella stessa istruzione sincrona, senza ``await`` in
# mezzo, e l'event loop è uno.
#
# **Perché memorizzare l'event loop e non solo un booleano.** Una presa che resta
# presa è peggio di nessuna presa: Dream sarebbe spento fino al riavvio del processo,
# in silenzio. I ``try``/``finally`` dei due chiamanti coprono ogni uscita del
# lavoro — ritorno, eccezione, ``CancelledError`` — ma non l'unico caso in cui il
# ``finally`` non gira affatto: un task cancellato *prima* del suo primo passo, cioè
# lo smontaggio dell'event loop. Un loop che non c'è più non può avere un ciclo in
# volo, quindi una presa che non appartiene al loop corrente è morta e si recupera.
_CYCLE_IN_FLIGHT: list[Any] = []


def claim_dream_cycle() -> bool:
    """Prende la presa sul ciclo di Dream, o ritorna ``False`` se è già presa.

    Chi ottiene ``True`` **deve** chiamare :func:`release_dream_cycle` in un
    ``finally``. Non è un context manager perché in ``cmd_dream`` i due momenti
    stanno in due frame diversi: la presa si prende nell'handler, prima di
    ``create_task``, o due ``/dream`` di fila passerebbero entrambi il controllo
    prima che il primo task inizi; il rilascio sta nel task.
    """
    try:
        running: Any = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover — entrambi i chiamanti sono async
        running = None
    if _CYCLE_IN_FLIGHT:
        if _CYCLE_IN_FLIGHT[0] is running:
            logger.warning("Dream: a cycle is already in flight; this one does not start")
            return False
        # La presa appartiene a un event loop che non è più quello corrente: il
        # ciclo che l'aveva non può riprendere, e il suo ``finally`` non girerà
        # mai. Recuperarla qui è la sola cosa che impedisce a Dream di restare
        # spento per sempre dietro un guasto invisibile.
        logger.warning(
            "Dream: lock left behind by an event loop that no longer exists; reclaimed"
        )
    _CYCLE_IN_FLIGHT[:] = [running]
    return True


def release_dream_cycle() -> None:
    """Rilascia la presa. Idempotente: chiamarla senza averla non è un errore."""
    _CYCLE_IN_FLIGHT.clear()


# La frase che legge chi ha battuto ``/dream`` mentre un ciclo era già in volo.
# Dice il costo vero — token spesi due volte — e non quello di prima di T2.4b:
# l'archivio al confine del file rende la seconda passata di review costosa, non
# distruttiva, e un rifiuto che spaventa più del dovuto è un rifiuto che mente.
DREAM_ALREADY_RUNNING = (
    "A Dream cycle is already running, so this one did not start — Dream runs one cycle "
    "at a time, or both take the same batch off the same cursor and each pays for its own "
    "review pass. Nothing would be lost, only spent twice. Try again once it has finished."
)


# I tag con cui il Consolidator marca un fatto destinato a **restare**. Sono la
# firma di un batch che vale consolidare, e servono a distinguerlo da uno che non
# chiede niente: le voci `(nothing)` e quelle di soli `[skip]` esistono davvero —
# sul Titan 2 i cursori 97 e 98 sono letteralmente `(nothing)` — e su quelle un
# run che non scrive nulla ha fatto la cosa giusta.
#
# Senza questa distinzione il controllo qui sotto terrebbe fermo il cursore su
# un'installazione tranquilla, replayando per sempre un batch che non ha niente
# da dire: lo specchio del livelock che questo modulo esiste per chiudere.
_RETAINED_TAGS = ("[durable]", "[permanent]", "[correction]")


class _NoEntries:
    """Esito in voci di un run che non aveva il tool per voci.

    Zero su tutti e tre, che è la lettura giusta: nessuna voce è entrata,
    nessuna sostituita, nessuna trovata già presente. Esiste perche i doppi di
    ``build_dream_tools`` nei test non espongono ``memory_entries``, e un
    ``getattr`` che tornasse ``None`` costringerebbe ogni chiamante a un ramo.
    """

    entries_added = 0
    entries_replaced = 0
    entries_already_present = 0


NO_ENTRIES = _NoEntries()


def batch_carries_retained_facts(history_text: str) -> bool:
    """True se il batch porta almeno un fatto che il Consolidator vuole conservare.

    *history_text* è la **sola** parte di storia del prompt, quella che
    ``MemoryStore.dream_prompt_history`` ritaglia. Passare il prompt intero è un
    errore che non si vede: il template nomina tutti e tre i tag nella sua
    sezione "History attribute tags", quindi risponderebbe sempre True.
    """
    return any(tag in history_text for tag in _RETAINED_TAGS)


def consolidation_landed(before: Sequence["FileBudget"]) -> bool:
    """True se almeno uno dei file misurati è **cresciuto** dall'inizio del run.

    La crescita è il solo indizio a costo zero che un fatto nuovo sia arrivato su
    disco: i path e le dimensioni di partenza stanno già in *before*, il report
    che il prologo ha costruito, e rileggere tre file corti alla fine del turno
    non costa niente.

    Il limite è dichiarato, non nascosto: una scrittura che *sostituisce* una
    riga con una più corta portandosi dentro il fatto nuovo è un consolidamento
    riuscito che questa misura legge come mancato. Non è distinguibile dalla
    dimensione, e nemmeno da un hash — un hash dice "il file è cambiato", non
    "è cambiato in meglio". Il costo del falso positivo è limitato per
    costruzione (v. :func:`batch_was_not_consolidated`): qualche run in più sullo
    stesso batch, e poi si avanza. Il costo del falso *negativo* — che è lo stato
    di oggi — è un fatto perso per sempre.
    """
    return any(count_chars(item.path) > item.chars for item in before)


def batch_was_not_consolidated(
    *,
    before: Sequence["FileBudget"],
    history_text: str,
    stuck: int,
    added: int = 0,
    replaced: int = 0,
    already_present: int = 0,
    attempted: int = 0,
) -> bool:
    """True quando il cursore **non** va avanzato pur avendo il run scritto qualcosa.

    Il buco che chiude è stato misurato sul Titan 2 il 2026-08-18 alle 12:01, con
    ``USER.md`` a 1.999/2.000 caratteri. In coda c'era un batch di dieci fatti —
    una conversazione lunga della sera prima, incluso un ``[permanent]``. Dream ha
    fatto **una** ``edit_file``: ha riscritto una riga già presente 27 caratteri
    più corta, e si è fermato. Ha fatto esattamente la prima metà di quel che il
    messaggio di rifiuto chiede — liberare spazio — e poi non ha usato lo spazio.

    Quel run è passato per sano da ogni controllo esistente, e non per una
    dimenticanza: ``writes_ok == 1``, nessun rifiuto rimasto aperto, ``stuck``
    fermo a 0. ``internal_run_should_commit`` chiede "hai scritto?", che è la
    domanda sbagliata quando l'unità di scrittura è il *file* e non il *fatto*: il
    cursore è avanzato a 101 e quelle dieci voci non torneranno in nessun batch.
    Hermes non ha questo problema perché il suo tool è indirizzato per entry
    (``memory add <testo>``), quindi "il fatto è atterrato?" ha una risposta; qui
    la si deve stimare, e la stima è la crescita dei file.

    Da qui in poi la domanda ha una **risposta**, e non più una stima. I tre
    contatori arrivano da ``MemoryEntryTool`` (fase 1 del piano):

    ``added`` è il segnale positivo che mancava: una voce è entrata, quindi il
    batch è atterrato. Niente da dedurre dalle dimensioni.

    ``already_present`` è quello che ha permesso di **cancellare la soglia di
    pressione**. Prima, un batch di soli duplicati — la maggioranza, perché la
    Consolidator ri-estrae gli stessi fatti a ogni giro — era indistinguibile da
    un batch mancato, e l'unico modo di non trattenerlo era escluderlo per
    statistica: "sotto il 90% di riempimento credo al modello". Quella soglia
    stava su tre osservazioni di un modello solo. Ora il tool dice che il fatto è
    già su disco, che è la stessa conclusione ottenuta guardando invece che
    indovinando, e il numero magico se n'è andato con lei.

    ``replaced`` conta **solo se il batch porta una** ``[correction]``, e la
    condizione è il punto: il fallimento misurato è una riscrittura cosmetica —
    una riga esistente accorciata, senza il fatto nuovo — che è una ``replace``
    a tutti gli effetti. Contarla sempre riammetterebbe dalla finestra proprio
    ciò che questa funzione esiste per prendere. Ma quando il batch *chiede* una
    correzione, sostituire in place è la mossa giusta e il prompt la chiede
    esplicitamente, quindi lì la ``replace`` è il consolidamento.

    ``batch_carries_retained_facts`` resta: un batch che non chiede niente non è
    stato mancato.

    ``consolidation_landed`` resta anch'essa, come rete: i tool file sono ancora
    montati e un fatto può ancora arrivare da lì.

``attempted`` è l'ultimo freno, e ce l'ha messo il telefono. Il piano dava per
    scontato che un batch di duplicati si sarebbe dichiarato con un
    ``already_present``; misurato il 2026-08-18 alle 18:10, non succede, e non
    perché il modello preferisca ``list``: **non guarda affatto**. ``USER.md`` è
    iniettato nel suo prompt, quindi risponde dal contesto — "entrambi i fatti nel
    batch sono già presenti, non c'è nulla da scrivere" — con *zero* chiamate a
    tool e una sola iterazione. Nessuna evidenza per voce può esistere in un run
    così, per quanto si renda economica la ``add``.

    Il che indica il freno giusto: questa funzione esiste per prendere il run che
    **ha scritto** qualcosa di cosmetico e ha tirato dritto — il caso delle 12:01,
    una ``edit_file`` che accorciava una riga. Quel run un tentativo lo fa. Un run
    che non tenta nessuna scrittura non ha *mancato* un consolidamento: ha deciso
    che non ce n'era da fare, ed è la stessa lettura che ``dream_should_advance_cursor``
    dà già a ``writes_attempted == 0``. Trattenerlo significherebbe rigiocare un
    batch davanti allo stesso modello con lo stesso contesto, che risponderà lo
    stesso — quattro volte, più un review forzato su file che non hanno niente da
    liberare. Esattamente il costo osservato.

    Resta scoperto il run che *avrebbe dovuto* salvare e ha deciso di no senza
    toccare niente. Non è distinguibile da un "niente di nuovo" legittimo, e il
    giudizio su quello è ciò che al modello si delega per progetto; la fase 5 gli
    dà visibilità con ``nothing_new_runs`` invece di trattenerlo.

    E resta ``stuck``, che riusa la scala che c'è già: oltre
    ``STUCK_IS_ALARMING`` si rinuncia al batch e si avanza. Perché un freno serve:
    se il modello *non vuole* aggiungere, tenere il cursore fermo replaya lo stesso
    batch nello stesso file pieno davanti allo stesso modello, e nessun review
    pass lo convince. Bounded quindi: al massimo quattro run — circa otto ore con
    ``interval_h`` al default — durante i quali il review forzato a 2 ha la sua
    occasione di liberare spazio davvero. Poi si molla, e l'allarme di
    :func:`_alert_stuck` è già partito: l'utente lo sa.
    """
    if stuck >= STUCK_IS_ALARMING:
        return False
    if not batch_carries_retained_facts(history_text):
        return False
    if not attempted:
        return False
    if added or already_present:
        return False
    if replaced and "[correction]" in history_text:
        return False
    return not consolidation_landed(before)


def format_stuck_alarm(stuck: int) -> str:
    """La frase che descrive il livelock, condivisa dalle superfici che lo dicono.

    Una stesura sola perché sono due: l'alert di sistema che parte da
    :func:`finish_dream_cycle` e la vista di ``/dream budget``, che è dove si va
    a guardare dopo averlo letto. Se divergessero, la seconda smentirebbe la
    prima nel momento peggiore.

    Nessuna cifra dei file qui dentro: da ``finish_dream_cycle`` le misure non
    sono a portata — il report è del prologo, un turno LLM fa — e rifarle
    vorrebbe dire rileggere tre file per comporre una frase. Chi ha bisogno dei
    numeri li trova nella vista che questa frase gli dice di aprire.
    """
    # Nessuna causa nominata, e non è vaghezza. ``stuck`` conta i run in cui il
    # consolidamento non è arrivato su disco, e i modi sono **tre**: il tetto in
    # caratteri; il rifiuto di *path* — un file fuori dalla allowlist del registry
    # di Dream, visto sul Titan 2 con tutti i file all'81% o meno; e un run che
    # scrive soltanto potature senza aggiungere il fatto nuovo
    # (``batch_was_not_consolidated``, misurato il 2026-08-18 alle 12:01).
    #
    # La stesura precedente diceva "keep being refused"; poi, con il terzo caso,
    # è diventata il "non stanno atterrando" che copriva tutti e tre senza dirne
    # nessuno — una frase vera e inutile, che è il modo in cui una diagnosi muore.
    #
    # Con la fase 5 la copertura non serve più: questo allarme parte **solo** dal
    # contatore dei rifiuti di budget (``finish_dream_cycle`` manda le altre
    # cause su ``nothing_new_runs``, che logga e non allarma). Quindi la frase
    # può tornare a nominare la causa, e indicare il rimedio che esiste davvero:
    # c'è spazio da liberare, e chi legge può alzare un tetto.
    return (
        f"Dream has not consolidated anything for {stuck} runs in a row: the size cap "
        "is refusing its writes to long-term memory and the review pass is not "
        "freeing enough room."
    )


def _alert_stuck(stuck: int) -> None:
    """Porta l'allarme fuori dal log, su una superficie che qualcuno vede.

    Il ``logger.error`` di ``finish_dream_cycle`` è, su Android, un allarme che
    non suona: nessuno legge logcat, e questo è precisamente lo stato in cui
    Jenny smette di ricordare senza che niente lo dica. Restava solo la lettura
    su richiesta (``/dream budget``), che risponde a chi è già venuto a
    chiedere.

    ``notify_delivery`` è la stessa primitiva con cui il canale WS posta gli
    alert di consegna: fire-and-forget, zero token, e no-op fuori da Android o
    senza event loop — quindi anche nei test. Non è il tool ``message``
    dell'escalation dell'heartbeat, di proposito: quello costa un turno LLM e
    dipende dal modello che sceglie di chiamarlo, e il modello è esattamente la
    parte che in questo scenario non sta funzionando.

    Riparte a **ogni** run oltre soglia, non solo all'attraversamento. Il tag che
    ne deriva (``cron:Dream``) fa sostituire l'alert precedente invece di
    sommarlo, quindi un livelock lungo lascia sul telefono una notifica sola e
    sempre aggiornata; e chi l'ha scartata la rivede al giro dopo, che è il
    comportamento voluto per un allarme che significa "la memoria è ferma". Il
    tag è suo: con quello di default (``message``) andrebbe a coprire la
    notifica di un messaggio vero.

    Import locale come in ``runtime/cron_dispatch.py``: nel grafo dei moduli
    ``jenny/agent`` non dipende da ``jenny/runtime``, e una riga di allarme non
    è una buona ragione per cominciare.
    """
    from jenny.runtime.notifier import notify_delivery
    from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

    notify_delivery(
        f"{format_stuck_alarm(stuck)} Settings \u2192 Memory shows the sizes.",
        {WEBUI_MESSAGE_SOURCE_METADATA_KEY: {"kind": "cron", "label": "Dream"}},
    )


def format_budget(report: Sequence["FileBudget"]) -> str:
    """Riassumi il report di budget in una riga sola di log.

    Non riusa ``render_gauge``: quello è multiriga e scritto per il modello
    (con l'istruzione su cosa fare al 80%), qui serve una riga grezza che stia
    in logcat e si possa grep-are nel tempo per tarare i tetti.
    """
    if not report:
        return "no files"
    parts = []
    for item in report:
        if item.enforced:
            parts.append(f"{item.label} {item.chars}/{item.budget} ({item.pct}%)")
        else:
            parts.append(f"{item.label} {item.chars} (no budget)")
    return ", ".join(parts)


async def take_dream_snapshot(
    take_snapshot: Callable[[], Awaitable[bool]] | None,
) -> bool:
    """Checkpoint pre-Dream, fail-open, che dichiara se è davvero avvenuto.

    Dream può riscrivere MEMORY/SOUL/USER e le skill: uno snapshot prima
    rende ogni sua modifica reversibile. Fail-open perché un checkpoint
    guasto non deve impedire il consolidamento — ma l'esito **non** si
    perde: ritorna ``False``, e il review pass ne fa una frase diversa nel
    proprio prompt invece di promettere al modello una rete che non c'è.

    Un callback assente conta come checkpoint non avvenuto, e la traduzione sta
    qui perché nessun chiamante debba scriverla: un percorso che non ha modo di
    chiedere lo snapshot deve dire ``False``, non dimenticare la domanda.
    """
    if take_snapshot is None:
        return False
    try:
        return bool(await take_snapshot())
    except Exception:
        logger.exception("Pre-dream snapshot failed")
        return False


def _measure(store: "MemoryStore", cfg: "DreamConfig") -> list["FileBudget"]:
    """Report di budget dei tre file di memoria, con i tetti di *cfg*."""
    return budget_report(
        store,
        memory_chars=cfg.memory_budget_chars,
        user_chars=cfg.user_budget_chars,
        soul_chars=cfg.soul_budget_chars,
    )


@dataclass(frozen=True, slots=True)
class DreamPrologue:
    """Ciò che il prologo ha prodotto, quando il chiamante riprende il controllo.

    ``report`` e ``guard`` sono le misure **valide adesso**: se il review pass è
    girato sono state rifatte dopo, perché quelle di prima descrivono file che
    non esistono più in quella forma.

    ``runs_since_review`` e ``stuck`` sono i contatori già letti da disco (e
    azzerati, se il review è girato) da passare a :func:`finish_dream_cycle`
    invece di rileggerli: fra i due momenti c'è un turno LLM, e rileggerli
    significherebbe contare su uno stato che nel frattempo può essere stato
    riscritto da un altro run.

    ``review`` è ``None`` quando il review pass non è girato. Non è un dettaglio
    di comodo: è il gancio con cui ciascun chiamante racconta il fatto a modo
    suo — il cron l'ha già scritto nel log, il comando ne fa una frase per
    l'utente che sta aspettando in chat — e insieme il modo di sapere se lo
    snapshot del ciclo è già stato preso.
    """

    report: list["FileBudget"]
    guard: "WriteSizeGuard"
    runs_since_review: int
    stuck: int
    review: "dream_review.ReviewOutcome | None"
    # Il gemello di ``stuck``: run senza consolidamento e **senza** rifiuti. Non
    # forza niente — nessun review può liberare spazio che nessuno ha chiesto —
    # ma viaggia fino all'epilogo, che è l'unico posto che sa come è andata.
    # In coda perché ha un default e ``review`` no.
    nothing_new: int = 0


async def begin_dream_cycle(
    agent: Any,
    *,
    store: "MemoryStore",
    cfg: "DreamConfig",
    take_snapshot: Callable[[], Awaitable[bool]] | None = None,
) -> DreamPrologue:
    """Tutto ciò che precede il turno incrementale di Dream, per entrambi i percorsi.

    Misura i file, monta il guard, legge i contatori, decide se il review pass
    è dovuto e — se lo è — prende il checkpoint, lo esegue, azzera i contatori e
    **rimisura**.

    *take_snapshot* è il callback del checkpoint; ``None`` è un percorso che non
    ha modo di prenderlo e vale ``snapshotted=False`` (v.
    :func:`take_dream_snapshot`).
    """
    report = _measure(store, cfg)
    guard = make_write_size_guard(report)
    runs_since_review, stuck = store.get_review_state()
    nothing_new = store.get_nothing_new_runs()
    # Loggato a OGNI run, non solo quando qualcosa scatta. Con i tre
    # budget a 0 — il default di spedizione — questa riga è letteralmente
    # l'unica cosa che la feature produce, e sono i numeri da cui si
    # sceglieranno i tetti veri (la roadmap propone 4-6 kB, che è una
    # proposta, non una misura). Toglierla renderebbe la taratura una
    # stima a occhio, cioè lo stato da cui si è partiti.
    logger.info(
        "Dream memory budget: {} | runs since review: {}, stuck runs: {}",
        format_budget(report), runs_since_review, stuck,
    )

    # Due modi di arrivare al review pass. ``review_every_runs`` è la
    # manutenzione periodica, che deve girare anche su file sani: è
    # l'unico momento in cui qualcuno guarda il file *intero* invece
    # della voce di storia del momento. ``stuck`` è l'uscita di
    # emergenza dal livelock (v. il commento in ``finish_dream_cycle``).
    #
    # Un terzo trigger — "un file ha sforato il budget" — è stato tolto
    # di proposito, ed è la parte che vale spiegare. Sembra il più
    # ovvio dei tre e invece è l'unico che non sa fermarsi: un file può
    # restare sopra la soglia dopo un review che ha già fatto tutto il
    # possibile (il resto è roba che le regole marcano "never delete"),
    # e il prompt del review dichiara *valido* un run che non cambia
    # niente. La condizione resterebbe quindi vera per sempre e
    # farebbe partire un turno LLM ogni due ore, a vuoto, senza che
    # nessun contatore lo limiti — lo specchio esatto del livelock che
    # tutto questo lavoro esiste per chiudere, e per giunta su una
    # feature il cui scopo è contenere i costi.
    #
    # Non si perde niente di importante, perché ``stuck`` copre già il
    # caso in cui essere sopra budget fa *danno*: se il tetto blocca una
    # scrittura, il cursore non avanza, ``stuck`` sale e due cicli dopo
    # il review parte. Ed è un bersaglio migliore — un file sopra
    # soglia che non sta bloccando nessuna scrittura non è un'urgenza,
    # e può aspettare il giro periodico.
    #
    # Il ramo ``stuck`` usa il modulo e non ``>=``: il contatore non viene
    # azzerato dal review (v. ``STUCK_IS_ALARMING``), quindi senza il modulo un
    # livelock farebbe partire un review a *ogni* run da lì in poi. Con il
    # modulo la cadenza è la stessa di prima — un review ogni due run bloccati —
    # ma il contatore continua a salire, ed è ciò che rende raggiungibile
    # l'allarme.
    #
    # E il modulo da solo non basta, correzione del 2026-08-17: ``stuck`` resta
    # **fermo** quando non c'era storia da consolidare (``advanced is None`` in
    # ``finish_dream_cycle``), cioè su ogni installazione in pari. Fermo su un
    # multiplo della soglia, quella condizione è vera a ogni run: un review ogni
    # due ore per sempre, a vuoto, su file che nessuno ha toccato — lo specchio
    # del livelock, e su una feature il cui scopo è contenere i costi. Serve
    # quindi anche sapere a che valore si è già forzato: si riforza solo quando
    # ``stuck`` è cambiato, cioè quando Dream ha mancato un altro consolidamento.
    forced_at = store.get_review_forced_at_stuck()
    livelock_due = stuck > 0 and stuck % STUCK_FORCES_REVIEW == 0 and stuck != forced_at
    review_due = runs_since_review >= cfg.review_every_runs or livelock_due
    if not review_due:
        return DreamPrologue(
            report=report,
            guard=guard,
            runs_since_review=runs_since_review,
            stuck=stuck,
            nothing_new=nothing_new,
            review=None,
        )

    snapshotted = await take_dream_snapshot(take_snapshot)
    outcome = await dream_review.run_dream_review(
        agent,
        store=store,
        report=report,
        snapshotted=snapshotted,
        write_size_guard=guard,
    )
    # Si azzera solo ``runs_since_review``: il review è avvenuto, la cadenza
    # periodica riparte. ``stuck`` resta dov'è, perché dice un'altra cosa — da
    # quanti run di fila Dream non consolida — e un review appena girato non è
    # una risposta a quella domanda. Lo azzera ``finish_dream_cycle``, e solo
    # quando il cursore avanza davvero.
    #
    # Ma non si azzera a 0 se il review ha **fallito**, ed è la correzione del
    # 2026-08-17: la riga di prima si comprava dodici run di tregua qualunque
    # fosse l'esito, compreso lo ``STATUS_FAILED`` che segnala una migrazione
    # troncata — cioè il caso in cui tornare presto conta di più. Si riparte
    # invece a due run dalla prossima occasione: prima di dodici, e non "a ogni
    # run", che è la protezione di costo per cui l'azzeramento incondizionato era
    # stato scritto.
    if outcome.status == dream_review.STATUS_FAILED:
        runs_after = max(0, cfg.review_every_runs - REVIEW_RETRY_AFTER_RUNS)
    else:
        runs_after = 0
    store.set_review_state(
        runs_since_review=runs_after,
        stuck_runs=stuck,
        # Il valore a cui questo review è stato forzato, quando è il livelock ad
        # averlo chiesto: è ciò che impedisce di riforzarlo su uno ``stuck`` che
        # non si muove più. Un review periodico non lo tocca (``None``), perché
        # del livelock non dice niente.
        forced_at_stuck=stuck if livelock_due else None,
    )
    # ``freed`` è il delta dei **tre file misurati**, non del
    # workspace. Un review che sposta una task spec da USER.md a una
    # ``skills/<name>/SKILL.md`` — cosa che il suo prompt chiede
    # esplicitamente — la conta come liberata, perché le skill non
    # stanno nel report. È il numero giusto per tarare i budget (sono
    # quei tre file ad averne uno) e quello sbagliato per dire di
    # quanto è dimagrito il disco.
    logger.info(
        "Dream review pass: {} (snapshotted={}), {} chars freed across the "
        "budgeted files",
        outcome.status, snapshotted, outcome.freed,
    )
    # Report e guard si RICOSTRUISCONO, non si riusano: il review ha
    # appena riscritto quei file. Il gauge del turno incrementale
    # mostrerebbe altrimenti al modello un riempimento che il review
    # ha già smontato — cioè gli chiederebbe di far spazio che è già
    # stato fatto. Il guard rilegge comunque la dimensione da disco a
    # ogni scrittura, ma va rifatto insieme al report perché i due
    # restino derivati dalla stessa misura invece che da due momenti
    # diversi.
    report = _measure(store, cfg)
    return DreamPrologue(
        report=report,
        guard=make_write_size_guard(report),
        runs_since_review=0,
        stuck=stuck,
        nothing_new=nothing_new,
        review=outcome,
    )


class DreamOutcome(Enum):
    """Come è finito il turno incrementale. L'ordine dei rami conta (v. sotto)."""

    NO_INPUT = "no_input"        # niente storia nuova: il turno non è partito
    ADVANCED = "advanced"        # batch atterrato, cursore avanzato
    HELD_BATCH = "held_batch"    # ha scritto, ma il batch non è atterrato
    BLOCKED = "blocked"          # completato senza scritture riuscite (rifiuti/blocchi)
    INCOMPLETE = "incomplete"    # il turno non si è chiuso pulito


@dataclass(frozen=True)
class DreamTurnResult:
    """L'esito del turno, per chi lo chiude e per chi lo racconta.

    ``refused`` sono i rifiuti di budget rimasti aperti, cioè la **causa** che
    decide quale dei due contatori del livelock sale. ``last_cursor`` è quello
    del batch, anche quando non si è avanzati.
    """

    outcome: DreamOutcome
    refused: int
    last_cursor: int | None = None

    @property
    def advanced(self) -> bool | None:
        """Il valore per :func:`finish_dream_cycle`, dedotto dall'esito.

        ``None`` se non c'era niente da consolidare, ``True`` se il batch è
        atterrato, ``False`` altrimenti. Era un secondo campo da tenere allineato
        a ``outcome`` a mano, in ogni ``return``.
        """
        if self.outcome is DreamOutcome.NO_INPUT:
            return None
        return self.outcome is DreamOutcome.ADVANCED


def _int_or_zero(value: object) -> int:
    """Un contatore che arriva da un doppio può non essere un intero."""
    return value if isinstance(value, int) else 0


async def run_dream_turn(
    agent: Any,
    store: "MemoryStore",
    prologue: "DreamPrologue",
    *,
    take_snapshot: Callable[[], Awaitable[Any]] | None,
) -> DreamTurnResult:
    """Il turno incrementale di Dream, lo stesso per il cron e per ``/dream``.

    Non chiude il ciclo: :func:`finish_dream_cycle` va chiamato dal chiamante in
    un ``finally``, perché un turno che solleva deve comunque far avanzare
    ``runs_since_review`` (un Dream che fallisce sempre non arriverebbe mai a un
    review pass). Le eccezioni del turno risalgono: il chiamante le racconta a
    modo suo, e chiude con ``advanced=None``.
    """
    from jenny.agent.memory import MemoryStore
    from jenny.agent.memory_budget import render_gauge

    result = store.build_dream_prompt(gauge=render_gauge(prologue.report))
    if result is None:
        return DreamTurnResult(DreamOutcome.NO_INPUT, refused=0)
    prompt, last_cursor = result[0], result[1]
    # ``getattr`` con un default: ``build_dream_prompt`` è sostituito nei test da
    # doppi che ritornano una coppia nuda, e un batch che non dichiara il proprio
    # tipo è un batch personale, che è il comportamento di sempre. Il tipo del
    # batch sceglie la cassetta in cui Dream può scrivere.
    scope = getattr(result, "scope", "personal")
    if prologue.review is None:
        # Un solo checkpoint per ciclo. Se il review è appena girato lo snapshot
        # è già stato preso pochi secondi fa e copre anche il turno che segue;
        # rifarlo qui archivierebbe lo stato *dopo* il review sotto l'etichetta
        # "pre_dream", cioè un secondo checkpoint che non è pre-niente.
        await take_dream_snapshot(take_snapshot)
    dream_tools = store.build_dream_tools(write_size_guard=prologue.guard, scope=scope)
    # ``getattr``: il registry Dream espone ``file_states``, ma il contratto resta
    # tollerante verso registry di altra provenienza (e verso i doppi dei test).
    dream_file_states = getattr(dream_tools, "file_states", None)
    resp = await agent.process_direct(
        prompt,
        session_key=MemoryStore.dream_session_key(),
        ephemeral=True,
        tools=dream_tools,
        on_progress=silent_progress,
    )
    advanced = MemoryStore.dream_should_advance_cursor(resp, dream_file_states)
    # Il run ha scritto, ma il batch è atterrato? Sono due domande diverse e fino
    # al 2026-08-18 se ne faceva una sola (v. :func:`batch_was_not_consolidated`).
    # Sta dopo il gate e non dentro perché ``internal_run_should_commit`` è
    # condiviso col giardiniere, che non ha un batch di storia da far atterrare.
    # Il tool per voci del run: un doppio che non lo espone è un run a zero voci.
    entries = getattr(dream_tools, "memory_entries", None) or NO_ENTRIES
    held_batch = advanced and batch_was_not_consolidated(
        before=prologue.report,
        history_text=MemoryStore.dream_prompt_history(prompt),
        stuck=prologue.stuck + prologue.nothing_new,
        added=entries.entries_added,
        replaced=entries.entries_replaced,
        already_present=entries.entries_already_present,
        # Se non ha tentato nessuna scrittura non ha mancato niente: ha deciso
        # che non c'era da scrivere.
        attempted=getattr(dream_file_states, "writes_attempted", 0),
    )
    refused = _int_or_zero(getattr(dream_file_states, "unrecovered_refusals", 0))
    if held_batch:
        # Ramo **prima** di quello dei rifiuti, e non è ordine estetico: la prima
        # stesura lo teneva a parte e uscivano due diagnosi, la seconda "attempts
        # blocked/refused" su un run senza né blocchi né rifiuti (logcat,
        # 2026-08-18 14:02:35). Un run, un esito.
        return DreamTurnResult(DreamOutcome.HELD_BATCH, refused=refused, last_cursor=last_cursor)
    if advanced:
        store.set_last_dream_cursor(last_cursor)
        return DreamTurnResult(DreamOutcome.ADVANCED, refused=refused, last_cursor=last_cursor)
    outcome = (
        DreamOutcome.BLOCKED if MemoryStore.dream_run_completed(resp)
        else DreamOutcome.INCOMPLETE
    )
    return DreamTurnResult(outcome, refused=refused, last_cursor=last_cursor)


def finish_dream_cycle(
    store: "MemoryStore",
    *,
    advanced: bool | None,
    runs_since_review: int,
    stuck: int,
    nothing_new: int = 0,
    refused: int = 0,
) -> tuple[int, int]:
    """Aggiorna i contatori del review dopo il turno incrementale.

    *advanced* è l'esito di ``dream_should_advance_cursor``, con un terzo stato:
    ``None`` significa **non c'era storia da consolidare**, quindi il turno
    incrementale non è nemmeno partito. È diverso da ``False`` e va tenuto
    distinto, perché ``stuck`` conta i run in cui Dream *non è riuscito* a
    consolidare — e un run che non aveva niente da fare non ha fallito niente.

    ``runs_since_review`` invece avanza in tutti e tre i casi, ed è la ragione
    per cui questo terzo stato esiste. Il review pass è manutenzione sui *file*;
    farlo dipendere dall'arrivo di nuova *storia* lega due cose scorrelate, e
    sulla combinazione peggiore le lega male: su un'installazione in pari — dove
    Dream ha già digerito tutto e i file stanno fermi — il contatore non saliva
    mai e il review non partiva **mai**, che è esattamente lo stato in cui la
    manutenzione periodica avrebbe più senso. Misurato sul Titan 2 il
    2026-08-16: cursore a 88, storia a 23 voci, `.dream_review` inesistente
    dopo un `/dream` completo.

    Ritorna i contatori scritti, perché il chiamante possa dirne qualcosa senza
    rileggere il disco.
    """
    # Anti-livelock, ed è la ragione per cui ``stuck`` esiste.
    # ``_resolve_write`` conta il tentativo PRIMA di risolvere il path
    # (``agent/tools/filesystem.py``, ``record_write_attempt``) e
    # ``internal_run_should_commit`` avanza solo con ``writes_ok > 0``
    # oppure ``writes_attempted == 0``. Un run in cui il budget rifiuta
    # ogni scrittura ha quindi ``attempted > 0, ok == 0``: il cursore non
    # avanza, al run dopo torna lo stesso batch, che viene rifiutato di
    # nuovo. Un turno LLM completo ogni due ore, per sempre.
    #
    # Non avanzare È la semantica corretta — il fatto non è stato scritto
    # e avanzare lo perderebbe — quindi la via d'uscita è forzare il review,
    # non allentare il commit.
    #
    # Con una correzione, del 2026-08-17: quel che *non* si allenta è il caso
    # in cui il contenuto non è atterrato. Un modello che obbedisce al
    # messaggio di rifiuto, pota e riscrive portandosi dentro il fatto ora
    # commette, perché il rifiuto si chiude quando il contenuto arriva su
    # disco (``FileStates.record_write_refused``). Prima no, e quel run — che
    # ha fatto esattamente il lavoro — era la sorgente di livelock più
    # probabile di tutte.
    #
    # E il conto non è solo in token. ``compact_history`` gira comunque a
    # fine run, nel chiamante, e tiene le ultime ``max_history_entries`` voci
    # SENZA guardare il cursore (``agent/memory.py``): un livelock abbastanza
    # lungo non spreca soltanto chiamate, perde storia che non è mai stata
    # consolidata.
    #
    # Fase 5 del piano: il contatore si è **spaccato in due**, perché contava due
    # cose con rimedi opposti. ``refused > 0`` vuol dire che il tetto ha bloccato
    # una scrittura: c'è spazio da liberare, un review pass ha una leva, e
    # ``stuck`` — che è il ramo che lo forza — sale. ``refused == 0`` con niente
    # atterrato è un'altra bestia: nessuna scrittura è stata negata, quindi non
    # c'è spazio da liberare e forzare un review significa potare file che non
    # hanno niente da dare. Misurato il 2026-08-18: un review forzato su file al
    # 77% e 79%, a vuoto, più quattro run di replay.
    #
    # Si azzerano insieme, perché a azzerarli è lo stesso evento: il cursore che
    # avanza.
    if advanced is not None:
        if advanced:
            stuck = nothing_new = 0
        elif refused:
            stuck += 1
        else:
            nothing_new += 1

    # Le regole che l'utente ha dato a lei tornano dentro ``SOUL.md``.
    #
    # Dream quel file lo riscrive: misurato sugli snapshot del dispositivo di
    # prova, sei riscritture in 6,6 giorni, e sono potature *dentro* le sezioni
    # — nessuna intestazione tolta, ma righe sì. Il blocco dell'utente non si
    # difende chiedendolo nel prompt: si riscrive, e la verità sta in un file
    # che il registro di scrittura di Dream non ammette
    # (``.jenny/soul_rules.md``, v. ``agent/soul_rules.py``).
    #
    # Qui e non altrove perché questo è il punto che gira dopo **ogni** passata:
    # sta nel ``finally`` del chiamante, quindi vale anche per un turno che è
    # crashato a metà — che è il caso in cui il file può essere rimasto a metà.
    #
    # La radice si prende da ``soul_file.parent`` e non da ``store.workspace``:
    # sono la stessa cartella (``MemoryStore.__init__``), ma cosi' il file da
    # riparare e la cartella in cui si cercano le regole non possono
    # disaccordarsi — e' un attributo solo invece di due da tenere allineati.
    from jenny.agent.soul_rules import sync_soul

    sync_soul(store.soul_file.parent, store.soul_file)

    runs_since_review += 1
    store.set_review_state(
        runs_since_review=runs_since_review,
        stuck_runs=stuck,
        nothing_new_runs=nothing_new,
    )
    if nothing_new >= NOTHING_NEW_IS_NOTABLE:
        # Si logga e basta: un review non è il rimedio, e farlo partire qui
        # sarebbe il livelock che questa separazione esiste per chiudere. Non c'è
        # nemmeno l'avviso all'utente di ``_alert_stuck``, perché non c'è niente
        # che possa farci — nessun tetto da alzare, nessun file da sfoltire.
        logger.warning(
            "Dream has not consolidated for {} consecutive runs, with nothing refused: "
            "the batch is not landing and no review pass can help (cursor still at {})",
            nothing_new, store.get_last_dream_cursor(),
        )
    if stuck >= STUCK_IS_ALARMING:
        logger.error(
            "Dream has not advanced its cursor for {} consecutive runs; the forced "
            "review pass is not freeing enough space (cursor still at {})",
            stuck, store.get_last_dream_cursor(),
        )
        # Il log resta (è dove si legge il cursore), ma non è l'allarme: v.
        # ``_alert_stuck``.
        _alert_stuck(stuck)
    return runs_since_review, stuck
