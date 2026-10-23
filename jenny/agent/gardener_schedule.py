"""Chi viene giardinato a questo tick, e i tre orologi che lo decidono.

Passo **T4.3** del piano del taccuino. Il job cron batte ogni mezz'ora;
questo modulo risponde all'unica domanda che il battito pone: *su quale progetto,
se su nessuno.*

I tre cancelli, e ognuno risponde a una domanda diversa:

1. **Distanza** — sono passate almeno N ore dall'ultima passata su *questa* wiki?
   È la lezione del degrado del Dream scritta come numero, e va per materia
   perché il degrado è per materia. «Passata» vuol dire **tentata**, non
   riuscita: v. ``_far_enough``, e la ragione è che a fallire non si smette da
   soli.
2. **Fermo** — la conversazione di quel progetto è zitta da N minuti, **e non ha
   un turno in volo adesso**. La seconda metà non è una rifinitura della prima:
   ``run_gardener`` gira su una chiave sua (``gardener:<nome>``) e **non
   condivide il lock** della conversazione del progetto, quindi utente e
   giardiniere possono scrivere la mappa nello stesso momento e l'ultimo che
   salva cancella l'altro. **Questo cancello impedisce di cominciare, non di
   collidere:** una passata dura 14–26 secondi e un messaggio che arriva un
   secondo dopo l'inizio non lo vede nessuno. Da T2.3 la seconda metà della
   difesa sta dentro la passata — ``gardener._yield_to_user_guard`` ricontrolla
   prima di *ogni* scrittura e la fa ritirare se l'utente è tornato — e
   l'asimmetria è deliberata: quando i due si incontrano si sposta il
   giardiniere, non l'utente.
3. **Lavoro** — c'è qualcosa da fare? E qui le ragioni sono **due**, in ``or``:
   righe di diario non lette, oppure — a diario a posto — una **mappa** oltre il
   tetto di iniezione e più grossa di come l'ultima passata l'ha lasciata
   (``GardenerStore.map_needs_pruning``, che è anche dove sta l'argomento del
   perché la seconda non è un livelock). La seconda ragione è di T3.5, e la
   misura che l'ha chiesta è questa: sulle otto wiki vere del 23/08/2026
   ``raw/journal/`` è vuota su **tutte e otto** e la mappa è oltre il tetto su
   **sette**. Con la sola prima ragione la potatura di T3.4 non sarebbe mai
   partita su nessuna di quelle sette — e su un progetto che l'utente non sta
   usando «finché la cattura non scrive righe» vuol dire mai.

**In quest'ordine, e l'ordine è il costo.** Distanza e fermo si decidono con due
letture di un file piccolo (lo stato del giardiniere, i metadati di sessione); il
delta vuole aprire i diari, e la mappa un file in più — che si apre solo per chi è
arrivato fin qui senza niente da promuovere. Chiedere prima le cose che costano
meno significa che, in un'installazione con otto progetti fermi, un tick tocca
pochi byte.

**Una passata per tick, la meno recente per prima.** Il tetto non è prudenza
astratta: otto progetti con righe nuove farebbero otto turni LLM di fila su un
telefono. Con un tick ogni mezz'ora, gli altri aspettano trenta minuti — che su
un lavoro con sei ore di distanza minima non è un ritardo.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.agent.gardener import GardenerStore
from jenny.agent.gardener_state import GardenerState, JournalDelta, read_state
from jenny.session.keys import project_session_key
from jenny.utils.wiki_paths import discover_wiki_roots


@dataclass(frozen=True)
class GardenerPick:
    """Il progetto scelto e il perché, per il log."""

    store: GardenerStore
    delta: JournalDelta
    """Il delta **già letto**, che la passata riceve invece di rileggerlo (T2.5).

    Prima qui c'era solo ``delta_lines``, e ``run_gardener`` riapriva i diari da
    zero un istante dopo: ``read_journal_delta`` fa un ``read_text`` intero di
    ogni ``raw/journal/*.md`` prima di guardare il cursore, quindi un progetto con
    un anno di diario si faceva leggere due volte per passata, e una volta per
    tick per ogni progetto idoneo. Il conteggio serviva al log e la lettura la
    buttavamo via.

    Sul ramo ``map`` è il delta **vuoto** e non un segnaposto: è quel che la
    lettura ha davvero trovato, ed è la stessa cosa che la passata avrebbe letto.
    """

    last_run_at: str | None
    reason: str = "journal"
    """Perché questo progetto: ``journal`` (ci sono righe non lette) o ``map`` (il
    diario è a posto ma la mappa è oltre il tetto). Serve **solo al log**: la
    passata la stessa cosa la ricava da sé, e passarglielo vorrebbe dire due
    sorgenti di verità per una politica sola."""

    last_touch: str = ""
    """Il più recente fra passata registrata e tentativo: è questo che ordina.

    ``last_run_at`` resta accanto perché è il fatto che si racconta («giardinato
    l'ultima volta il...»), ma da solo non può ordinare: un progetto che non ha
    mai *registrato* niente lo tiene a ``None`` per sempre, quindi ordinerebbe
    primo a ogni giro e terrebbe fuori chiunque altro (v.
    ``GardenerState.last_touch``).
    """

    @property
    def delta_lines(self) -> int:
        """Le righe da leggere, per il log. **Derivato e non ricopiato**: era un
        campo accanto al delta, cioè due sorgenti per un numero solo."""
        return self.delta.line_count


# I timbri nel futuro già raccontati, per non ripetersi. Un orologio corso avanti
# resta avanti fino a che qualcuno non lo sistema, e il tick batte ogni mezz'ora:
# senza questo, la stessa riga tornerebbe un centinaio di volte al giorno e
# smetterebbe di essere un avviso. Il tetto c'è perché è un globale di processo,
# non perché ci si aspettino tanti timbri diversi.
_FUTURE_STAMPS_REMEMBERED = 32
_future_stamps_seen: set[str] = set()


def _note_future_stamp(stamp: str, ahead: float) -> None:
    """Dice una volta sola che *stamp* sta *ahead* secondi nel futuro."""
    if stamp in _future_stamps_seen:
        return
    if len(_future_stamps_seen) >= _FUTURE_STAMPS_REMEMBERED:
        _future_stamps_seen.clear()
    _future_stamps_seen.add(stamp)
    logger.warning(
        "gardener: timestamp {} is {:.0f}s in the future (a clock ran ahead: RTC "
        "before NTP sync, timezone jump); treating it as unknown",
        stamp, ahead,
    )


def _age_seconds(stamp: Any, now: datetime) -> float | None:
    """Secondi da *stamp* (ISO), o ``None`` se non è una data su cui contare.

    ``None`` vuol dire «non lo so», e i cancelli qui sotto lo trattano come «non
    blocca» — la stessa politica tollerante che ``read_state`` documenta per lo
    stato intero. Ci finiscono tre casi, e gli ultimi due sono quelli che sono
    costati:

    * quel che non è una stringa, o è una stringa vuota;
    * quel che ``fromisoformat`` non sa leggere, **e** quel che sa leggere ma non
      sa sottrarre: un timbro con fuso orario dà una data *aware*, e ``now -
      aware`` alza ``TypeError``, che ``except ValueError`` non prendeva. Non era
      un cancello chiuso, era un'eccezione fuori da ``pick_project``;
    * un timbro nel **futuro**. Un'età negativa i due cancelli la leggevano come
      «recentissimo», cioè l'esatto contrario di quel che è: un RTC ripartito
      avanti dopo una batteria a zero, o un salto di fuso, congelava il
      giardinaggio di quel progetto **per sempre** — con ``last_run_at`` a un
      anno da adesso il progetto non veniva scelto né subito né trecento giorni
      dopo, e l'unica traccia era una riga DEBUG e un file che su un telefono
      l'utente non raggiunge. Qui vale «non lo so», cioè ammesso; e si dice a
      WARNING, perché un orologio corso avanti è una cosa da sapere.

    Nessuna fascia di tolleranza sul negativo: fra due tick lo stesso orologio
    scrive e rilegge, quindi un negativo di frazioni di secondo non capita — un
    negativo è un salto, e va detto.
    """
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        age = (now - datetime.fromisoformat(stamp)).total_seconds()
    except (ValueError, TypeError):
        return None
    if age < 0:
        _note_future_stamp(stamp, -age)
        return None
    return age


def _far_enough(state: GardenerState, now: datetime, min_hours: int) -> bool:
    """Se è passato abbastanza dall'ultima passata su questa wiki.

    Mai giardinata (nessuno stato, o una data illeggibile) vuol dire **sì**: il
    caso da servire per primo è quello del progetto nuovo, e un cursore perso non
    deve poter bloccare il lavoro per sei ore.

    **Due orologi, e vince il più giovane.** Fino al 23/08/2026 questo cancello
    leggeva solo ``last_run_at``, che lo scrive solo un cursore che avanza: una
    passata che non registrava niente lasciava il file di stato intatto, e siccome
    gli altri due cancelli sono aperti per costruzione (il giardiniere lavora
    quando il progetto è zitto, e il delta non letto è ancora lì) la stessa
    passata ripartiva al tick dopo, per sempre. Il tentativo è la spesa, e la
    distanza è un fatto di spesa: qui si guarda quando si è **provato**, non
    quando è andata bene.

    Illeggibile o assente conta come «non lo so» e non blocca — ma se l'altro
    orologio una data ce l'ha, quella decide. Per questo si prende l'età minima e
    non il timbro massimo: un ``last_run_at`` corrotto non deve poter mangiarsi un
    ``last_attempt_at`` valido di un minuto fa.

    **Un timbro nel futuro sta fra gli illeggibili**, e i due orologi rendono la
    cosa migliore di così: da solo non blocca (era il congelamento a vita, v.
    ``_age_seconds``), ma accanto a un tentativo di mezz'ora fa non riapre niente
    — l'orologio sano vince il ``min`` e la distanza resta quella. Il caso
    peggiore di un RTC corso avanti è quindi *una* passata in più, non una ogni
    mezz'ora.
    """
    if min_hours <= 0:
        return True
    ages = [
        age
        for age in (
            _age_seconds(state.last_run_at, now),
            _age_seconds(state.last_attempt_at, now),
        )
        if age is not None
    ]
    return not ages or min(ages) >= min_hours * 3600


def _quiet_enough(sessions: Any, key: str, now: datetime, idle_min: int) -> bool:
    """Se la conversazione di quel progetto è ferma da abbastanza.

    Nessun metadato vuol dire che quella conversazione non è mai esistita, quindi
    non c'è niente che stia parlando: **fermo**. Una data illeggibile vale la
    stessa cosa — l'alternativa sarebbe un progetto che non viene mai giardinato
    per un timestamp corrotto, cioè un guasto che si nasconde. E qui il buco era
    identico a quello della distanza: un ``updated_at`` nel futuro dava un'età
    negativa, che questo cancello leggeva come «ha appena parlato», per sempre.
    """
    if idle_min <= 0:
        return True
    try:
        info = sessions.read_session_metadata(key)
    except Exception:  # noqa: BLE001 — la lettura dei metadati non deve fermare il tick
        logger.warning("gardener: session metadata unreadable for {}", key)
        return True
    if not isinstance(info, dict):
        return True
    age = _age_seconds(info.get("updated_at"), now)
    return age is None or age >= idle_min * 60


def _plan_held_notebooks(workspace: Path) -> set[str]:
    """I quaderni tenuti da un piano partito (``jenny/plan/model.py::HELD_STATUSES``).

    Dal disco e non dal driver: il giardiniere gira dal dispatcher del cron,
    che il driver non lo conosce, e ``plan.json`` è comunque l'unica fonte.
    """
    try:
        from jenny.plan.model import HELD_STATUSES
        from jenny.plan.store import PlanStore

        plans = PlanStore.for_workspace(workspace).list_all()
    except Exception:  # noqa: BLE001 — la lettura dei piani non deve fermare il tick
        logger.warning("gardener: plans unreadable, no notebook skipped for a plan")
        return set()
    return {p.notebook for p in plans if p.status in HELD_STATUSES}


def pick_project(
    workspace: Path,
    *,
    idle_min: int,
    min_hours_between_passes: int,
    sessions: Any,
    active_session_keys: Collection[str] = (),
    wikis_dir_name: str = "wikis",
    now: datetime | None = None,
) -> GardenerPick | None:
    """Il progetto da giardinare adesso, o ``None`` se nessuno è pronto.

    *sessions* è il ``SessionManager`` (serve solo ``read_session_metadata``);
    *active_session_keys* le sessioni con un turno in volo in questo istante.

    **Il delta letto qui viaggia dentro il ``GardenerPick``** e la passata lo
    riceve invece di rileggerlo (T2.5): la lettura dei diari è la parte cara di
    questa selezione, e farla due volte per la stessa passata era il costo che
    questo modulo esiste per contare.
    """
    at = now or datetime.now()
    candidates: list[GardenerPick] = []
    held = _plan_held_notebooks(workspace)

    for name in sorted(discover_wiki_roots(workspace / wikis_dir_name)):
        if name in held:
            # Un piano a lista partito tiene il quaderno: le scritture del
            # giardiniere finirebbero nelle foto come se le avesse fatte il piano,
            # e il ritorno indietro le disferebbe. Si giardina a piano chiuso.
            continue
        store = GardenerStore.for_project(workspace, name, wikis_dir_name=wikis_dir_name)
        if store is None:
            continue

        state = read_state(store.root)
        if not _far_enough(state, at, min_hours_between_passes):
            continue

        key = project_session_key(name)
        if key in active_session_keys:
            # Un turno in volo adesso: non è "quasi fermo", è il caso peggiore.
            continue
        if not _quiet_enough(sessions, key, at, idle_min):
            continue

        # Ultimo, perché è l'unico che apre dei file.
        delta = store.read_delta()
        if delta.is_empty:
            # **La seconda ragione**, e si chiede solo qui: la mappa è oltre il suo
            # tetto e più grossa di come l'ultima passata l'ha lasciata. Il
            # predicato — e l'argomento del perché non è un livelock — sta in
            # ``GardenerStore.map_needs_pruning``, che è lo stesso che chiede la
            # passata: due copie sarebbero due politiche, e un tick che scegliesse
            # un progetto che ``run_gardener`` poi rifiuta.
            #
            # Sotto il delta e non sopra, perché l'ordine è il costo (v. la
            # docstring del modulo): questo apre un file in più, e lo apre solo per
            # i progetti che sono già arrivati fin qui e non hanno niente da
            # promuovere.
            if store.map_needs_pruning(state):
                candidates.append(
                    GardenerPick(store=store, delta=delta, reason="map",
                                 last_run_at=state.last_run_at, last_touch=state.last_touch)
                )
                continue
            # **La terza ragione: la bacheca.** Una prima bacheca da proporre,
            # cassetti con le pagine cambiate, una correzione fatta dal cassetto,
            # una pagina chiesta in bacheca. La domanda e' la stessa che si fa la
            # passata (``board_pass.board_needs_pass``), freno compreso, e sta
            # per ultima perche' apre piu' file di tutte.
            from jenny.agent.board_pass import board_needs_pass

            if not board_needs_pass(store.root, store.workspace):
                continue
            candidates.append(
                GardenerPick(store=store, delta=delta, reason="board",
                             last_run_at=state.last_run_at, last_touch=state.last_touch)
            )
            continue
        candidates.append(
            GardenerPick(store=store, delta=delta,
                         last_run_at=state.last_run_at, last_touch=state.last_touch)
        )

    if not candidates:
        return None
    # La meno recente per prima; le mai giardinate prima di tutte (``""`` ordina
    # sotto qualunque data ISO). A pari merito decide il nome, così la scelta è
    # deterministica e un test può fissarla.
    #
    # «Meno recente» conta i **tentativi**, non i successi: ordinando per
    # ``last_run_at`` un progetto su cui la passata fallisce sempre resta a
    # ``None``, cioè primo a ogni giro, e un progetto sano fermo da tre giorni non
    # arriva mai in cima. Con due candidati basta uno rotto per non giardinare più
    # l'altro.
    candidates.sort(key=lambda c: (c.last_touch, c.store.name))
    pick = candidates[0]
    if len(candidates) > 1:
        # Detto, non taciuto: gli altri aspettano il tick dopo, e chi legge i log
        # deve poter vedere che non sono stati dimenticati.
        logger.info(
            "gardener: {} projects ready, this tick takes {} ({}, {} lines); the rest: {}",
            len(candidates), pick.store.name, pick.reason, pick.delta_lines,
            ", ".join(f"{c.store.name} ({c.reason})" for c in candidates[1:]),
        )
    return pick
