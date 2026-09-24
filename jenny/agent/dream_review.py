"""Review pass di Dream — il run che non aggiunge niente e può soltanto ridurre.

Dream gira ogni due ore e vede *una* voce di storia per run: la domanda che gli
viene posta è sempre «questo fatto nuovo dove lo instrado?», e la risposta è
quasi sempre «da qualche parte». La domanda «questo file è diventato troppo
grande?» non gliela pone nessuno, mai — ed è il motivo per cui le regole di
potatura che il suo prompt già contiene restano teoriche mentre MEMORY.md cresce
in modo monotono.

Questo modulo è il secondo tipo di run: nessuna storia nel prompt, nessun fatto
da instradare, un solo mestiere — far rientrare i file nel budget. La forma è
quella del giardiniere: tutta la logica qui, al dispatcher cron
restano l'instradamento e il log.

Tre cose che questo run **non** fa, ognuna con il suo test in
``tests/agent/test_dream_review.py``:

1. **Non tocca ``.dream_cursor``**, né in lettura né in scrittura. Non processa
   storia, quindi non ha nessun cursore da avanzare. Toccarlo qui perderebbe
   per sempre le voci che il run incrementale non ha ancora consolidato, ed è
   esattamente il livelock che il review pass esiste per rompere: Dream che
   rimacina lo stesso batch perché le sue scritture sforano il budget.
2. **Non consulta ``dream_should_advance_cursor`` /
   ``internal_run_should_commit``** per decidere il proprio esito. Quelle
   regole rispondono a «posso dichiarare digerito questo input?», domanda che
   qui non ha un input a cui riferirsi: un review pass che non scrive niente è
   un esito valido, non un progresso da trattenere. L'esito lo dicono i byte su
   disco, prima e dopo. Di quel gruppo di helper serve solo
   ``internal_run_completed``, per distinguere un run finito male.
3. **Non fa lo snapshot.** Lo fa il chiamante, l'unico a sapere se gli snapshot
   sono attivi; qui arriva solo il flag ``snapshotted`` per dire la verità al
   prompt. Il template ha due rami e quello "reversibile" è attaccato alla
   frase il cui scopo è far cancellare di più: passarlo a vanvera significa
   promettere al modello un checkpoint che non esiste.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.agent.memory import MemoryStore
from jenny.agent.memory_archive import archived_ids, summarize_archived
from jenny.agent.memory_budget import count_chars, render_gauge
from jenny.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from jenny.agent.memory_budget import FileBudget, WriteSizeGuard

# Valori di ``ReviewOutcome.status``. Stringhe come in ``GardenerOutcome`` e non
# enum: finiscono nei log e nelle asserzioni dei test, e un enum qui
# obbligherebbe ogni chiamante a importare questo modulo solo per confrontare.
STATUS_COMPLETED = "completed"
STATUS_NO_CHANGE = "no-change"
STATUS_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    """Esito di un review pass: cosa è successo, e di quanto.

    ``before``/``after`` sono mappe ``label -> caratteri`` sugli stessi file del
    report ricevuto, misurate rispettivamente prima e dopo il run. Viaggiano
    anche quando lo status è ``failed``: un run interrotto a metà può aver già
    scritto, e il chiamante che deve decidere se riprovare ha bisogno di sapere
    se qualcosa si è mosso.
    """

    status: str
    before: dict[str, int]
    after: dict[str, int]
    # I file d'archivio comparsi durante questo run: le voci che il passaggio ha
    # **spostato** invece di cancellare. Viaggiano nell'esito perché "quanto ha
    # liberato" e "cosa ha tolto di mezzo" sono due domande diverse, e dopo la
    # fase 2 la seconda ha finalmente una risposta esatta invece di un delta in
    # caratteri.
    #
    # Valorizzato su **ogni** uscita di :func:`run_dream_review`, compresa quella
    # per eccezione. Per tre commit lo era solo sui due rami ``failed``: i due
    # esiti normali — "ha liberato spazio" e "non c'era niente da potare" — non lo
    # portavano, cioè proprio quelli in cui una degradazione è più probabile.
    # ``/dream`` rispondeva quindi "nothing was freed" a una passata che aveva
    # spostato sei fatti personali dell'utente, e l'unica traccia era una riga di
    # log.
    demoted: tuple[str, ...] = ()
    # Scritture rifiutate dal budget il cui contenuto non è mai atterrato. Se è
    # > 0 lo status non è ``completed``: ``failed`` quando qualcosa è anche calato
    # (possibile migrazione troncata), altrimenti ``no-change`` — che è l'esito
    # corretto quando il run ha lasciato stare la fonte, come il prompt gli chiede.
    # V. :func:`run_dream_review`.
    #
    # Lo legge la nota di ``/dream`` (``command/builtin.py::_format_dream_refusals``)
    # perché è la sola metà azionabile dell'esito: una degradazione l'utente la
    # ritrova con ``recall``, un rifiuto no — il fatto non è in nessun file, e
    # l'unica mossa che lo sblocca è alzare il tetto o potare a mano. Per tre
    # commit il campo era valorizzato su ogni percorso e letto da nessuno in
    # ``jenny/``: una review che lasciava aperto un rifiuto riferiva "nothing was
    # freed", che è vero come numero e muto sull'unica cosa da fare.
    unresolved_refusals: int = 0

    @property
    def freed(self) -> int:
        """Caratteri liberati in totale; negativo se i file sono cresciuti."""
        return sum(chars - self.after.get(label, 0) for label, chars in self.before.items())

    @property
    def demoted_ids(self) -> tuple[str, ...]:
        """Gli id con cui il tool ``recall`` ritrova le voci di :attr:`demoted`.

        Il nome del file d'archivio è ``<data>-<id>.md`` e l'id è un hash esadecimale
        (``tools/memory_entries.py::entry_id``), quindi l'ultimo segmento dello stem
        *è* l'id — la stessa derivazione che fa ``memory_archive.read_archived``
        quando rilegge una voce. Sta qui perché un numero non è azionabile: chi
        legge "sei fatti spostati" deve poter chiedere *quali*, e ``recall`` prende
        id, non nomi di file.
        """
        return tuple(name.rsplit(".", 1)[0].rsplit("-", 1)[-1] for name in self.demoted)


def review_session_key() -> str:
    """Session key di un review pass, es. ``dream:review-20260816-100000``.

    Il prefisso ``dream:`` non è cosmesi ed è la ragione per cui la chiave è
    coniata così e non come ``review:...``: ``is_internal_session_key`` lo copre
    già (quindi la sessione non compare negli elenchi user-facing) e
    ``MemoryStore.prune_dream_sessions`` globba ``dream_*.jsonl`` (quindi il file
    viene ripulito dal meccanismo esistente). Con un prefisso tutto suo ogni
    review pass lascerebbe dietro un file di sessione per sempre, e servirebbe un
    secondo pruner che nessuno si ricorderebbe di scrivere.
    """
    return f"dream:review-{datetime.now():%Y%m%d-%H%M%S}"


async def _silent(*_args: Any, **_kwargs: Any) -> None:
    """``on_progress`` no-op: un run interno non ha nessuno a cui riferire."""


def _measure(report: Sequence[FileBudget]) -> dict[str, int]:
    """Rimisura i file del report, ``label -> caratteri``.

    Riusa ``count_chars`` invece di rileggere a modo suo: è la stessa funzione
    con cui ``budget_report`` ha prodotto le misure di partenza (stesso
    ``errors="ignore"``, stesso 0 per file assente), e due implementazioni
    diverse ai due capi del confronto produrrebbero delta inventati sul primo
    byte malformato.
    """
    return {item.label: count_chars(item.path) for item in report}


# Oltre quante voci spostate in un solo passaggio la cosa va detta per nome.
#
# Cinque è circa un quarto di un ``USER.md`` tipico (19 voci sul Titan 2 il
# 2026-08-19), e questa soglia esiste perché la degradazione **non è gratis**: una
# voce archiviata è recuperabile ma non è più nel prompt, quindi l'effetto che
# l'utente osserva non è "ho perso un fatto" ma "Jenny non se lo ricorda più".
# Il permesso di potare più a fondo (v. la sezione *Never lose* di ``dream.md``)
# è arrivato insieme a questa riga di proposito: allargare il permesso senza la
# sua visibilità è l'unico ordine, fra i due, che potrebbe fare danno.
DEMOTION_IS_NOTABLE = 5


def _report_demotions(store: "MemoryStore", before: set[str]) -> tuple[str, ...]:
    """Cosa il passaggio ha spostato in archivio, e lo dice se è tanto.

    Ritorna i nomi dei file comparsi durante il run. Sopra
    :data:`DEMOTION_IS_NOTABLE` il log li elenca **con il fatto dentro**, non solo
    con il conteggio: il numero dice quanto, e chi legge un avviso deve sapere
    *cosa*, o non può decidere se andare a guardare.
    """
    moved = sorted(archived_ids(store.memory_dir) - before)
    if not moved:
        return ()
    if len(moved) > DEMOTION_IS_NOTABLE:
        logger.warning(
            "Dream review demoted {} entries to memory/archive/ in one pass: {}",
            len(moved),
            "; ".join(summarize_archived(store.memory_dir, name) for name in moved),
        )
    else:
        logger.info("Dream review demoted {} entries to memory/archive/", len(moved))
    return tuple(moved)


async def run_dream_review(
    agent: Any,
    *,
    store: MemoryStore,
    report: Sequence[FileBudget],
    snapshotted: bool,
    write_size_guard: WriteSizeGuard | None = None,
) -> ReviewOutcome:
    """Esegue un review pass sui file di memoria e restituisce l'esito.

    *report* è il report di budget già costruito dal chiamante: le misure di
    partenza si prendono di lì e non si rimisurano, così il gauge nel prompt e
    il ``before`` dell'outcome sono lo stesso numero — se divergessero, il
    modello e il log racconterebbero due storie diverse dello stesso run.

    *snapshotted* dice soltanto la verità al prompt: lo snapshot lo fa il
    chiamante (v. il punto 3 del docstring di modulo). *write_size_guard* è il
    gancio che impone il budget sulle scritture ed è opzionale come altrove:
    senza, il run misura e pota ma nessuna scrittura viene mai rifiutata.
    """
    before = {item.label: item.chars for item in report}
    prompt = render_template(
        "agent/dream_review.md",
        # ``for_review=True`` non è un dettaglio: la variante incrementale del
        # gauge dice "consolida prima di aggiungere", e in un run dove per
        # definizione non si aggiunge niente quella riga descrive un'azione che
        # non esiste.
        budget_gauge=render_gauge(report, for_review=True),
        snapshotted=snapshotted,
    )
    # Stesso tool set di Dream: il prompt rimanda a ``agent/dream.md`` per i
    # criteri di cancellazione invece di ricopiarli, e quel rimando sta in piedi
    # solo perché ``ReadFileTool`` qui dentro è montato sull'intero workspace.
    tools = store.build_dream_tools(write_size_guard=write_size_guard)
    # Fotografia dell'archivio prima del turno: la differenza dirà quali voci il
    # passaggio ha spostato, che è una domanda diversa da "quanto ha liberato".
    archived_before = archived_ids(store.memory_dir)

    resp = None
    try:
        resp = await agent.process_direct(
            prompt,
            session_key=review_session_key(),
            ephemeral=True,
            tools=tools,
            on_progress=_silent,
        )
    except Exception:  # noqa: BLE001 — l'esito viaggia nell'outcome, non in un raise
        # Un review pass è un lavoro di manutenzione: farlo esplodere in faccia
        # al cron porterebbe via anche ciò che viene dopo nello stesso tick,
        # mentre il ``failed`` qui sotto è già tutto quello che il chiamante può
        # farci. Le misure si prendono comunque: il modello può aver scritto
        # prima di morire.
        logger.exception("Dream review pass failed")
        refused = getattr(getattr(tools, "file_states", None), "unrecovered_refusals", 0)
        return ReviewOutcome(
            status=STATUS_FAILED,
            before=before,
            after=_measure(report),
            unresolved_refusals=refused if isinstance(refused, int) else 0,
            # Anche qui, e per la stessa ragione per cui si rimisurano i file: un
            # run morto a metà può aver già spostato delle voci, e sono esattamente
            # quelle di cui nessuno saprebbe niente — il turno è finito male,
            # quindi non c'è nemmeno un riepilogo a valle che le nomini.
            demoted=_report_demotions(store, archived_before),
        )
    # La contabilita' dei token **non passa da qui**: la fa
    # ``TokenUsageHook.after_iteration`` sul turno stesso, che e' l'unico punto
    # in cui l'``usage`` del provider esiste davvero. Qui c'era una
    # ``record_response_token_usage(resp, ...)`` con sopra il ragionamento su
    # quale bucket usare — e quel ragionamento era senza oggetto: ``resp`` e' un
    # ``OutboundMessage``, che un campo ``usage`` non ce l'ha, quindi la riga non
    # ha mai registrato niente. La revisione ricade sotto ``dream`` perche' la
    # sua chiave e' ``dream:review-...``, cioe' per la mappa delle chiavi e non
    # per una scelta fatta qui.

    after = _measure(report)
    demoted = _report_demotions(store, archived_before)
    # Rifiuti di budget il cui contenuto non è atterrato. ``unrecovered_refusals``
    # e non il contatore cumulativo: un file rifiutato e poi riscritto con dentro
    # il fatto è stato recuperato, e contarlo qui trasformerebbe in allarme il caso
    # in cui il modello ha fatto esattamente ciò che il messaggio gli chiede.
    #
    # ``getattr`` perché ``ToolRegistry.file_states`` è dichiarato
    # ``FileStates | None``: il registry qui sopra lo costruisce sempre valorizzato,
    # quindi il ramo ``None`` è irraggiungibile e questa è solo la lettura che non
    # obbliga a un ``assert`` per accontentare il type checker.
    file_states = getattr(tools, "file_states", None)
    outstanding = getattr(file_states, "unrecovered_refusals", 0)
    if not isinstance(outstanding, int):
        outstanding = 0

    # ``failed`` ha la precedenza su qualsiasi riduzione osservata: lo status
    # descrive la salute del run, non il suo effetto collaterale. Un turno
    # interrotto che per caso ha accorciato un file resta un turno interrotto —
    # di quanto abbia ridotto lo dicono ``before``/``after``, che ci sono
    # comunque.
    if not MemoryStore.internal_run_completed(resp):
        logger.warning("Dream review: run did not complete cleanly")
        return ReviewOutcome(
            status=STATUS_FAILED, before=before, after=after,
            unresolved_refusals=outstanding, demoted=demoted,
        )

    # Un rifiuto rimasto aperto vale come il turno interrotto, e per una ragione
    # più stretta: il route down di ``dream_review.md`` sposta un fatto da
    # ``USER.md`` a ``memory/MEMORY.md``, e la cancellazione dalla fonte è una
    # scrittura che *rimpicciolisce* — quindi sempre accettata — mentre l'aggiunta
    # alla destinazione può essere rifiutata perché il tetto è già superato. Se
    # succede in quest'ordine il fatto non è in nessuno dei due file, e i soli
    # ``before``/``after`` lo raccontano come un successo: ``USER.md`` è
    # diminuito, quindi ``freed > 0``.
    #
    shrank = any(after[label] < chars for label, chars in before.items())

    # La firma distruttiva è la **congiunzione**: un rifiuto rimasto aperto *e*
    # qualcosa che si è rimpicciolito. Il route down sposta un fatto da ``USER.md``
    # a ``memory/MEMORY.md``: la cancellazione dalla fonte è una scrittura che
    # rimpicciolisce, quindi sempre accettata, mentre l'aggiunta alla destinazione
    # può essere rifiutata perché il tetto è già superato. Se succede in
    # quest'ordine il fatto non è in nessuno dei due file, e i soli
    # ``before``/``after`` lo raccontano come un successo — ``USER.md`` è
    # diminuito, quindi ``freed > 0``.
    #
    # Il rifiuto da solo non basta a dire "fallito", e una prima versione di questa
    # riga lo faceva: segnalava i due comportamenti che il prompt *raccomanda*. Un
    # solo ``apply_patch`` con entrambe le metà rifiutato rolla indietro tutto e non
    # perde niente; e la via di riserva scritta due righe sopra — "se la
    # destinazione viene rifiutata lascia la fonte dov'è" — è obbedienza, non
    # guasto. Entrambe finivano in ``failed``, in contraddizione con quel che dice
    # la fine di quel template: *"un run che non cambia niente è un esito valido"*.
    #
    # "Rimasto aperto" è comunque una misura di contenuto, non di tentativi (v.
    # ``FileStates.record_write_refused``): un modello che pota e si riporta dentro
    # il fatto non arriva qui. Quel che resta è il caso che il prompt non può
    # garantire di evitare, e per quello lo status esiste.
    if outstanding > 0 and shrank:
        logger.warning(
            "Dream review: {} write(s) refused by their budget never landed while a "
            "source file shrank — a fact may have been deleted without reaching its "
            "destination. The pre-Dream snapshot is the only copy of the previous state",
            outstanding,
        )
        return ReviewOutcome(
            status=STATUS_FAILED, before=before, after=after,
            unresolved_refusals=outstanding, demoted=demoted,
        )

    if outstanding > 0:
        # Rifiuto aperto ma nulla è calato: la migrazione non è partita, che è
        # l'esito che il prompt chiede quando la destinazione è piena. Non è un
        # fallimento e non è un successo — resta ``no-change``, con il numero in
        # chiaro nell'outcome perché il chiamante possa dirlo.
        logger.info(
            "Dream review: nothing shrank; {} write(s) were refused by their budget "
            "and the run correctly left their source alone",
            outstanding,
        )
        return ReviewOutcome(
            status=STATUS_NO_CHANGE, before=before, after=after,
            unresolved_refusals=outstanding, demoted=demoted,
        )

    if shrank:
        # ``outstanding`` è 0 qui e nell'uscita finale: i due rami che lo vedono
        # positivo sono già ritornati sopra. Passarlo comunque, invece di lasciare
        # il default, è la differenza fra "il campo è giusto" e "il campo è giusto
        # finché nessuno riordina i rami": è la stessa mutezza che ``demoted``
        # aveva su quattro uscite su sei, e costava una parola evitarla.
        outcome = ReviewOutcome(
            status=STATUS_COMPLETED, before=before, after=after, demoted=demoted,
            unresolved_refusals=outstanding,
        )
        logger.info(
            "Dream review: freed {:,} chars ({})",
            outcome.freed,
            ", ".join(f"{label} {before[label]:,}->{after[label]:,}" for label in before),
        )
        return outcome

    # Nessun file è diminuito. Non è un fallimento e il codice deve dire la
    # stessa cosa che il prompt dice al modello ("a review run that changes
    # nothing is a valid outcome"): un run che non manifattura un edit pur di
    # giustificarsi ha fatto la cosa giusta.
    #
    # In questo ramo cade anche il caso raro in cui qualcosa è *cresciuto* senza
    # che nulla calasse — una ristrutturazione che sposta testo fra i tre file
    # misurati. Non ha un nome suo nel contratto a tre stati, e "no-change" è la
    # collocazione meno fuorviante: nulla è stato liberato. Il delta vero resta
    # leggibile in ``before``/``after``.
    logger.info("Dream review: nothing to shrink")
    return ReviewOutcome(
        status=STATUS_NO_CHANGE, before=before, after=after, demoted=demoted,
        unresolved_refusals=outstanding,
    )
