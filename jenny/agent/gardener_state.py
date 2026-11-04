"""Il cursore del giardiniere, e il delta di diario che ne esce.

Passo **T4.1** del piano del taccuino: la metà del giardiniere che non
chiama nessun modello. Risponde a una domanda sola — *«di questo diario, cosa non
ho ancora letto?»* — e tiene su disco la risposta.

Lo stato vive in ``<progetto>/.jenny/gardener.json``. Il posto non è nuovo: la
cartella nascosta di un progetto ospita già i risultati dei tool
(``.jenny/tool-results/``, v. ``session/project_rename.py``), e sta **fuori da
tutto** senza che nessuno debba impararlo — ``iter_wiki_pages`` cammina solo
``wiki/`` e salta i file nascosti. Il quaderno è materiale umano, il cursore è macchinario.

**Righe, non byte.** Un conteggio di righe è significativo *perché* il diario è
append-only, e sopravvive a un editor che riscrive la coda del file; un offset in
byte no. E il conteggio è di righe **fisiche**: quel che si promuove sono le voci,
ma quel che si conta è il file, così il cursore resta una cosa che si verifica
con ``wc -l``. Quella verificabilità è una proprietà del **come** si divide, non
un modo di dire: la divide :func:`journal_lines`, e la sua docstring dice contro
quali tre modi ovvi di sbagliarla.

**Il numero da solo non basta: porta un testimone.** L'append-only è vero dal
lato del giardiniere, non dal lato del progetto — un turno può scrivere
``raw/journal/*.md`` con ``write_file``/``edit_file``, e solo ``journal_append``
è append-only per costruzione. Cancellata *una* riga già letta da un giorno che
resta più lungo del cursore, il conteggio torna plausibile e la prima riga non
letta scivola sotto il cursore: persa in silenzio, per sempre, perché nessuno
rilegge un diario. Da cui il testimone: il digest del prefisso consumato, salvato
accanto al conteggio. Se non torna, quel giorno si rilegge da riga zero — costa
una ripromozione, che è idempotente, invece di una riga perduta, che non lo è.

**Perso il cursore si rilegge da capo.** Non è un caso da evitare, è il
comportamento: lo stato è una cache di lavoro, e la correttezza sta
nell'idempotenza della passata (ripassare le stesse righe non duplica pagine),
non nella durabilità di questo file. Da cui il tetto: una rilettura da zero su
mesi di diario non deve poter diventare un prompt da diecimila righe.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger

from jenny.utils.path import atomic_write, read_json
from jenny.utils.wiki_paths import wiki_journal_dir

# Lo stato, relativo alla radice del progetto.
GARDENER_STATE_REL = ".jenny/gardener.json"

_STATE_VERSION = 2

# Quanto del digest si tiene. Sedici cifre esadecimali sono 64 bit: il testimone
# non difende da una collisione cercata — chi può riscrivere il diario può anche
# cancellare lo stato — ma da un editor distratto, e per quello 64 bit sono
# abbondanti. Il resto sarebbe rumore in un file che si legge a occhio.
_WITNESS_CHARS = 16

# Gli esiti in cui il cursore è avanzato, cioè quelli in cui il timbro del
# tentativo lo mette già ``GardenerState.advanced``. Il vocabolario degli stati è
# di ``gardener.py``, ma la domanda che questo insieme risponde — «questa passata
# ha registrato qualcosa nel file di stato?» — è di questo modulo, che è quello
# che il file di stato lo scrive.
COMMITTED_STATUSES = frozenset({"written", "nothing_to_promote"})

# Dopo quante passate consecutive senza registrazione l'insuccesso smette di
# essere un incidente e va detto **fuori dal log**.
#
# Il numero si giustifica contro il tetto di Dream, che è ``STUCK_IS_ALARMING =
# 4`` su un ciclo di due ore (``jenny/agent/dream_cycle.py``): Dream allarma dopo
# circa otto ore. Qui la cadenza fra due passate sulla stessa wiki è
# ``min_hours_between_passes``, sei ore al default, quindi:
#
# * copiare il **conteggio** di Dream (4) vorrebbe dire ventiquattro ore di
#   silenzio — un giorno intero in cui il diario di un progetto smette di
#   diventare pagine senza che nessuno lo sappia;
# * copiare il suo **orologio** (otto ore) vorrebbe dire allarmare alla prima o
#   alla seconda passata, e la prima è ordinaria: un provider giù per un minuto,
#   un turno andato storto. Dream stesso non reagisce a una sola
#   (``STUCK_FORCES_REVIEW = 2``, «una è ordinaria, due di fila è una
#   configurazione che si ripete»).
#
# Tre sta in mezzo — circa diciotto ore — ed è strettamente più di «è successo
# due volte». E c'è un'asimmetria che spinge verso il basso e non verso l'alto:
# Dream, a 2, ha un rimedio automatico da spendere (il review pass forzato) e
# allarma solo quando quello non è bastato; il giardiniere non ha niente da
# forzare, quindi l'avviso non è l'ultima carta dopo il rimedio — è il rimedio,
# ed è quello che porta la cosa davanti a chi può leggere l'errore.
GARDENER_FAILURES_ARE_ALARMING = 3

# Quante voci di diario può portarsi una passata. Il caso che questo tetto
# difende non è il diario di una giornata parlante — sono venti righe — ma la
# rilettura da capo dopo un cursore perso: mesi di diario in un prompt solo.
# Duecento voci sono una settimana molto densa, e quel che resta torna al giro
# dopo (v. ``JournalDelta.left_behind``, che il chiamante *deve* dire nel prompt:
# troncare zitti è il difetto che questo ramo ha già pagato due volte).
MAX_DELTA_LINES = 200


def journal_lines(text: str) -> list[str]:
    """Le righe **fisiche** di *text*, contate come le conta ``wc -l``.

    Sostituisce ``str.splitlines()``, che divide anche su ``\\v``, ``\\f``,
    ``\\x1c``-``\\x1e``, ``\\x85``, U+2028 e U+2029: una riga di diario che
    contenga uno di quei caratteri diventava **due** voci, e il cursore smetteva
    di essere il numero che una persona verifica con ``wc -l`` — che è la
    proprietà su cui poggia la scelta «righe, non byte» (v. la docstring del
    modulo). Nessuno scrittore di Jenny li produce (``journal_append``
    normalizza con ``str.split()``, che li mangia tutti), ma il diario è
    scrivibile da un turno con ``write_file``, e il diario è testo copiato da
    mezzo mondo.

    Tre dettagli, e ognuno risponde a una domanda che l'implementazione ovvia
    sbaglia:

    * **si divide su ``"\\n"`` e basta**, quindi ``\\v`` e U+2028 restano dentro
      la riga in cui si trovano, che è dove il file li ha messi;
    * **l'ultima riga vuota di un file che finisce con ``\\n`` non si conta.**
      È il punto in cui ``text.split("\\n")`` nudo sarebbe stato *peggio* di
      ``splitlines()``: su un file normale di cinque righe darebbe sei elementi,
      cioè un cursore di sei dove ``wc -l`` dice cinque — l'esatto contrario di
      quel che si sta riparando;
    * **un ``\\r`` finale si toglie**, così su un file CRLF il conteggio e il
      contenuto delle righe restano identici a quelli di ``splitlines()``: il
      testimone di un diario già letto non cambia per il solo aggiornamento, e
      nessun progetto si rilegge da capo per niente.

    Resta una differenza dichiarata: un file con righe terminate dal solo ``\\r``
    (Mac OS 9) qui è una riga sola. Non è raggiungibile da nessuno scrittore di
    questo albero, e la strada in cui finisce — una voce lunghissima — è visibile,
    al contrario di una voce inventata.
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def journal_witness(lines: Sequence[str]) -> str:
    """Il testimone del prefisso *lines*: cambia se una di quelle righe cambia.

    Sul **prefisso consumato**, non sul file intero: un digest di tutto il file
    non distinguerebbe «cresciuto» — che è il caso normale, ogni voce nuova — da
    «riscritto». È la stessa ragione per cui il lint del diario tiene un
    ``head_digest`` invece del digest della pagina
    (``jenny/skills/llm-wiki/scripts/lint_wiki.py``).

    Sul prefisso **intero** e non sull'ultima riga consumata: l'ultima riga da
    sola vede le cancellazioni — cancellare fa scorrere in su tutto il resto —
    ma non una riga già letta *modificata* al centro, che è il caso in cui una
    pagina promossa resta a dire quel che il diario non dice più. Il costo è
    nullo in pratica: il file è già stato letto per intero e diviso in righe
    poche istruzioni prima, quindi il testimone aggiunge uno SHA-256 su qualche
    kilobyte per giorno di diario.

    Il conteggio entra nel materiale insieme al testo: così il testimone lega
    prefisso *e* lunghezza, e nessuna coppia diversa può presentarsi come la
    stessa.
    """
    material = f"{len(lines)}\n" + "\n".join(lines)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:_WITNESS_CHARS]


@dataclass(frozen=True)
class JournalFileDelta:
    """Le voci non ancora lette di **un** giorno di diario."""

    path: str
    """Percorso relativo alla radice del progetto, POSIX: ``raw/journal/20260822.md``."""

    lines: tuple[str, ...]
    """Le voci, già ripulite: nessuna riga vuota, nessuna intestazione."""

    cursor_after: int
    """Righe fisiche del file consumate da questo delta — il cursore che ne esce."""

    witness_after: str = ""
    """Il testimone delle ``cursor_after`` righe consumate. Vuoto vale «non
    verificabile», cioè rilettura da capo al giro dopo: mai un cursore creduto
    sulla parola."""


@dataclass(frozen=True)
class JournalDelta:
    """Quel che una passata ha da leggere, in ordine cronologico."""

    files: tuple[JournalFileDelta, ...] = ()
    left_behind: int = 0
    """Voci che il tetto ha lasciato fuori. Va **detto**, non ingoiato."""

    @property
    def line_count(self) -> int:
        return sum(len(f.lines) for f in self.files)

    @property
    def is_empty(self) -> bool:
        return not self.files

    def cursor(self) -> dict[str, int]:
        """Il cursore che questo delta produce, una volta consumato."""
        return {f.path: f.cursor_after for f in self.files}

    def witnesses(self) -> dict[str, str]:
        """I testimoni che questo delta produce. I vuoti non si salvano: uno
        stato senza testimone dice «rileggi», e va detto togliendo la voce, non
        scrivendoci una stringa vuota."""
        return {f.path: f.witness_after for f in self.files if f.witness_after}


@dataclass(frozen=True)
class GardenerState:
    """Fin dove il diario di un progetto è stato letto, e quando."""

    cursor: dict[str, int] = field(default_factory=dict)
    last_run_at: str | None = None
    witness: dict[str, str] = field(default_factory=dict)
    """Il testimone del prefisso consumato, per giorno. Una voce assente vale
    «non verificabile», cioè rilettura da capo di quel giorno."""

    last_attempt_at: str | None = None
    """Quando una passata è stata **tentata** su questo progetto, riuscita o no.

    Separato da ``last_run_at`` perché rispondono a due domande diverse, e fino
    al 23/08/2026 ce n'era una sola. ``last_run_at`` dice fin dove il lavoro è
    stato registrato, e quindi lo scrive solo :meth:`advanced`, insieme al
    cursore — che è giusto: le passate che tengono il cursore fermo lo tengono
    fermo di proposito. Ma la distanza fra due passate è un fatto di **spesa**,
    non di cursore: senza questo campo una passata che non registra niente
    lasciava il file di stato intatto, e il tick di mezz'ora dopo la rifaceva
    identica — misurate 48 volte in un giorno sullo stesso progetto rotto, con un
    prompt intero ogni volta e gli altri progetti in coda dietro.
    """

    failures: int = 0
    """Passate consecutive che hanno chiamato il provider senza registrare nulla.

    Azzerata da :meth:`advanced`, cioè dal problema che finisce, e non da un
    tentativo qualunque — la stessa scelta di ``stuck`` in ``dream_cycle``, e per
    la stessa ragione: un contatore che si azzera da sé non arriva mai a una
    soglia, e l'allarme che ne dipende è codice morto.
    """

    map_left_at: int | None = None
    """Quanto misurava la mappa **quando l'ultima passata l'ha lasciata**.

    Non è una statistica: è il freno del secondo innesco. Da T3.5 una mappa oltre
    il suo tetto è una ragione per giardinare anche a diario vuoto — altrimenti su
    un progetto che l'utente non usa la potatura non arriva mai — e una ragione che
    *resta vera dopo la passata* è un livelock: una mappa che il modello non riesce
    a portare sotto il tetto tornerebbe candidata a ogni distanza minima, per
    sempre, e nessun contatore di insuccessi la fermerebbe (una potatura a metà
    **committa**, quindi azzera ``failures``).

    Da cui la regola, in ``GardenerStore.map_needs_pruning``: la mappa vale una
    passata solo se è **più grossa** di come l'ultima l'ha lasciata. Una passata
    per episodio, e il riarmo lo fa solo la mappa che ricresce — che è l'unico caso
    in cui c'è del lavoro nuovo da fare.

    ``None`` vuol dire «nessuna passata l'ha ancora vista», cioè innesco armato: è
    il valore che hanno oggi tutti i progetti sul telefono, ed è il verso giusto
    per il caso da servire per primo.
    """

    @property
    def last_touch(self) -> str:
        """Il più recente fra passata registrata e tentativo, per **l'ordine**.

        Stringa e non data, e il confronto è lessicografico: i due timbri li
        scrive questo modulo con lo stesso ``isoformat(timespec="seconds")``,
        quindi sull'ISO l'ordine alfabetico *è* l'ordine cronologico, e
        l'assente (``""``) ordina sotto qualunque data — che è il comportamento
        voluto, il progetto mai toccato va servito per primo.

        Serve solo a ordinare i candidati: chi deve decidere se è passato
        abbastanza tempo guarda i due campi separatamente, perché là un timbro
        illeggibile deve valere «non lo so» e non «vince il massimo».
        """
        return max(self.last_run_at or "", self.last_attempt_at or "")

    def advanced(
        self,
        delta: JournalDelta,
        *,
        at: datetime | None = None,
        map_chars: int | None = None,
    ) -> "GardenerState":
        """Lo stesso stato con *delta* consumato.

        Il cursore si **fonde**, non si sostituisce: un delta tocca i giorni che
        hanno righe nuove, e gli altri restano fin dove erano.

        I testimoni si fondono allo stesso modo, con una cura in più: il
        testimone di un giorno che avanza si **butta** prima di riscriverlo, così
        un conteggio nuovo non può restare appaiato a un testimone vecchio (che
        sarebbe una rilettura, e un avviso, per niente).

        Il timbro va su **entrambi** gli orologi: una passata riuscita è anche
        una passata tentata, e lasciare ``last_attempt_at`` indietro farebbe
        della registrazione del successo un tentativo vecchio. E la serie di
        insuccessi finisce qui: il cursore che avanza è il problema che si
        chiude.

        *map_chars* è quanto misura la mappa **adesso**, a passata finita, e
        ``None`` vuol dire «non l'ho guardata»: v. ``map_left_at``, e il default
        conserva il valore di prima invece di azzerarlo, perché un innesco che si
        riarma da sé è il livelock che quel campo esiste per chiudere.
        """
        merged = dict(self.cursor)
        merged.update(delta.cursor())
        seals = {rel: seal for rel, seal in self.witness.items() if rel not in delta.cursor()}
        seals.update(delta.witnesses())
        stamp = (at or datetime.now()).isoformat(timespec="seconds")
        return GardenerState(
            cursor=merged, last_run_at=stamp, witness=seals, last_attempt_at=stamp, failures=0,
            map_left_at=self.map_left_at if map_chars is None else map_chars,
        )

    def attempted(
        self, *, at: datetime | None = None, map_chars: int | None = None
    ) -> "GardenerState":
        """Lo stesso stato dopo una passata che **non** ha registrato niente.

        Il gemello di :meth:`advanced` che non tocca il cursore, ed è tutto il
        punto: ``partial_write`` e ``commit_failed`` tengono il cursore fermo di
        proposito — righe non promosse che devono tornare — e il tentativo va
        registrato comunque, altrimenti la scelta di tenere il cursore diventa la
        scelta di rifare la passata ogni mezz'ora per sempre.

        *map_chars* come in :meth:`advanced`, e qui è il ramo che conta di più:
        una passata che ha visto l'ordine di potare e non ha potato niente
        (``no_write``) è esattamente quella che, senza questo campo, tornerebbe
        alla distanza minima dopo, con lo stesso prompt e lo stesso esito.
        """
        stamp = (at or datetime.now()).isoformat(timespec="seconds")
        return GardenerState(
            cursor=dict(self.cursor),
            last_run_at=self.last_run_at,
            witness=dict(self.witness),
            last_attempt_at=stamp,
            failures=self.failures + 1,
            map_left_at=self.map_left_at if map_chars is None else map_chars,
        )

    def payload(self) -> dict[str, object]:
        return {
            "version": _STATE_VERSION,
            "cursor": dict(sorted(self.cursor.items())),
            "last_run_at": self.last_run_at,
            "witness": dict(sorted(self.witness.items())),
            "last_attempt_at": self.last_attempt_at,
            "failures": self.failures,
            "map_left_at": self.map_left_at,
        }


def gardener_state_file(root: Path) -> Path:
    """Lo stato del giardiniere per *root*. Non garantisce che esista."""
    return root / GARDENER_STATE_REL


def read_state(root: Path) -> GardenerState:
    """Lo stato su disco, o uno stato vuoto.

    Uno stato illeggibile — troncato, JSON invalido, scritto da una versione che
    non conosciamo — vale **stato vuoto**, cioè rilettura da capo. È la scelta
    giusta perché l'unico costo è ripassare righe già viste, che l'idempotenza
    della passata rende innocuo, mentre indovinare un cursore da un file rotto
    salterebbe righe in silenzio.
    """
    path = gardener_state_file(root)
    data = read_json(path)
    if not isinstance(data, dict) or data.get("version") != _STATE_VERSION:
        if data:
            logger.warning("gardener: unrecognized state in {}, re-reading from the start", path)
        return GardenerState()
    raw = data.get("cursor")
    cursor = {
        key: value
        for key, value in (raw.items() if isinstance(raw, dict) else ())
        if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)
        and value >= 0
    }
    seals = data.get("witness")
    # Un testimone che non è una stringa non è un testimone: si scarta, e il
    # giorno si rilegge. Vale la stessa asimmetria del cursore — scartare costa
    # righe ripassate, fidarsi costerebbe righe saltate.
    witness = {
        key: value
        for key, value in (seals.items() if isinstance(seals, dict) else ())
        if isinstance(key, str) and isinstance(value, str) and value
    }
    stamp = data.get("last_run_at")
    # I due campi del tentativo si leggono **come gli altri**, ognuno per conto
    # suo e con un default sicuro: assenti valgono «nessun tentativo, zero
    # insuccessi», che è esattamente il comportamento di prima che esistessero.
    # È la ragione per cui aggiungerli non ha chiesto un ``_STATE_VERSION`` nuovo:
    # il gate di versione serve quando cambia il significato di un campo che c'è
    # già — leggerlo alla vecchia maniera sarebbe insicuro — e non quando se ne
    # aggiunge uno indipendente. Un bump costerebbe a ogni progetto già sul
    # telefono una rilettura del diario da capo, cioè una passata LLM per niente.
    attempt = data.get("last_attempt_at")
    failures = data.get("failures")
    # Anche questo campo è indipendente e ha un default sicuro, quindi non chiede
    # un ``_STATE_VERSION`` nuovo (v. la nota qui sopra). Ma il default sicuro qui
    # è ``None``, cioè **innesco armato**: un valore illeggibile deve valere «la
    # mappa non l'ha ancora vista nessuno» e non «lasciala stare», perché il costo
    # del primo è una passata di troppo e quello del secondo è la mappa tagliata
    # per sempre.
    left_at = data.get("map_left_at")
    return GardenerState(
        cursor=cursor,
        last_run_at=stamp if isinstance(stamp, str) else None,
        witness=witness,
        last_attempt_at=attempt if isinstance(attempt, str) else None,
        failures=(
            failures if isinstance(failures, int) and not isinstance(failures, bool)
            and failures >= 0 else 0
        ),
        map_left_at=(
            left_at if isinstance(left_at, int) and not isinstance(left_at, bool)
            and left_at >= 0 else None
        ),
    )


def write_state(root: Path, state: GardenerState) -> None:
    """Salva lo stato, potato dei giorni che non esistono più.

    ``atomic_write`` e non ``write_text`` per la stessa ragione del cursore di Dream: uno
    stato troncato a metà si rilegge come JSON invalido, cioè cursore perso, cioè
    una rilettura da capo che nessuno ha chiesto.

    **Si pota solo quel che non c'è più.** La tentazione era potare per età — una
    voce al giorno per sempre *sembra* una perdita — ed è sbagliata: buttare la
    voce di un giorno di diario che esiste ancora significa rileggerlo, cioè
    pagare per la pulizia. Un progetto usato ogni giorno per tre anni tiene mille
    voci, che sono una quarantina di kilobyte: meno di qualunque meccanismo che
    rischi una rilettura.
    """
    pruned = {rel: seen for rel, seen in state.cursor.items() if (root / rel).is_file()}
    if len(pruned) != len(state.cursor):
        logger.debug(
            "gardener: pruned {} cursor entries with no file", len(state.cursor) - len(pruned)
        )
    # Il testimone segue il cursore: senza il suo conteggio non vuol dire niente.
    seals = {rel: seal for rel, seal in state.witness.items() if rel in pruned}
    payload = GardenerState(
        cursor=pruned,
        last_run_at=state.last_run_at,
        witness=seals,
        last_attempt_at=state.last_attempt_at,
        failures=state.failures,
        map_left_at=state.map_left_at,
    ).payload()
    atomic_write(
        gardener_state_file(root), json.dumps(payload, ensure_ascii=False, indent=2)
    )


def record_attempt(
    root: Path, *, at: datetime | None = None, map_chars: int | None = None
) -> int:
    """Registra una passata tentata e non registrata. Ritorna gli insuccessi di fila.

    Il timbro va su disco perché è l'unica cosa che ferma la ripetizione: senza,
    il tick dopo trova lo stesso stato e rifà la stessa passata.

    Un ``OSError`` qui **non** si propaga. Il chiamante ha già un esito da
    restituire e una passata è già stata spesa; far cadere il job cron
    aggiungerebbe solo una traccia sbagliata sullo stato del cron. E il conteggio
    si ritorna comunque, anche se non è atterrato: un disco che non prende
    quindici byte è, se possibile, più allarmante del motivo per cui lo stavamo
    scrivendo, e chi legge il numero deve poterlo dire.
    """
    state = read_state(root).attempted(at=at, map_chars=map_chars)
    try:
        write_state(root, state)
    except OSError as exc:
        logger.error(
            "gardener: the attempt on {} was not recorded ({}): the pass "
            "will restart on the next tick",
            root.name, exc,
        )
    return state.failures


def read_journal_delta(
    root: Path,
    state: GardenerState,
    *,
    max_lines: int = MAX_DELTA_LINES,
) -> JournalDelta:
    """Le voci di diario di *root* che *state* dichiara non lette.

    In ordine cronologico, che qui è l'ordine alfabetico dei nomi
    (``AAAAMMGG.md``) — è la ragione per cui il nome del file è fatto così.
    """
    journal = wiki_journal_dir(root)
    if not journal.is_dir():
        return JournalDelta()

    budget = max(0, max_lines)
    files: list[JournalFileDelta] = []
    left_behind = 0

    for page in sorted(journal.glob("*.md")):
        if page.name.startswith("."):
            continue
        rel = page.relative_to(root).as_posix()
        try:
            physical = journal_lines(page.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            # ``UnicodeDecodeError`` **non** e' un ``OSError``, ed e' l'eccezione
            # piu' probabile delle due: un diario e' testo scritto da un modello e
            # copiato a mano da mezzo mondo. Senza questo ramo un solo file mal
            # codificato non saltava una pagina — faceva cadere la passata intera,
            # e quindi congelava il giardiniere su quel progetto per sempre.
            logger.warning("gardener: diario illeggibile {}: {}", rel, exc)
            continue

        seen = state.cursor.get(rel, 0)
        if seen > len(physical):
            # Il file si è accorciato: qualcuno ha riscritto un diario, che
            # l'append-only vieta. Non si rilegge da capo — rileggere
            # ripromuoverebbe roba già promossa — e non si tace: il lint
            # (T5) è il posto che deve trovarlo, questo è il posto che lo
            # racconta.
            #
            # L'asimmetria col testimone qui sotto è voluta: là, sotto il
            # cursore, ci sono righe **non lette** che il conteggio sbagliato
            # farebbe saltare, e allora la ripromozione è il prezzo giusto.
            # Qui il file finisce prima del cursore: non c'è niente da
            # perdere, quindi rileggere sarebbe solo un costo.
            logger.warning(
                "gardener: {} is shorter than the cursor ({} lines, cursor {}): "
                "the journal's append-only rule was violated",
                rel, len(physical), seen,
            )
            continue

        # **Il testimone si verifica anche a file finito** (``seen ==
        # len(physical)``), e prima quel caso si saltava di corsa. Il buco che ne
        # nasceva: un file salvato **senza newline finale** — cosa che
        # ``journal_append`` non fa mai ma un ``write_file`` sì — ha la sua ultima
        # riga incompleta, e quella riga viene letta e consumata. Il primo
        # ``journal_append`` successivo la *completa* incollandosi in coda
        # (``- 09:00 — a`` e ``- 11:00 — b`` sulla stessa riga fisica): il
        # conteggio non si muove, quindi il vecchio ``seen >= len`` saltava il
        # giorno e **il fatto appena catturato non veniva promosso mai**. Il
        # testimone è esattamente il meccanismo per questo — «il prefisso non è
        # più quello di allora» — e gli mancava solo il permesso di parlare
        # sull'ultima riga.
        #
        # **Ma a file finito un testimone *assente* non vale un dubbio**, ed è
        # l'unico punto in cui la regola «non verificabile vuol dire rileggi»
        # non si applica: è la stessa asimmetria del ramo qui sopra — sotto il
        # cursore non c'è niente da perdere — e senza questa clausola ogni
        # cursore scritto da una versione senza testimoni si sarebbe fatto
        # ripromuovere un diario intero, una volta, per niente.
        recorded = state.witness.get(rel)
        finished = seen == len(physical)
        if finished and recorded is None:
            continue
        if seen and journal_witness(physical[:seen]) != recorded:
            # Il prefisso consumato non è più quello di allora: il file è stato
            # riscritto *sopra* il cursore, e restando più lungo del cursore non
            # se n'era accorto nessuno. Da qui il conteggio non vuol dire niente
            # — la prima riga non letta può essere già scivolata sotto — quindi
            # si riparte da zero. È una ripromozione, che la passata sa
            # assorbire, al posto di una riga persa, che non torna più.
            #
            # Testimone assente vale allo stesso modo: «non posso verificare» è
            # una rilettura, non un cursore creduto sulla parola. È anche il caso
            # del primo giro dopo l'aggiornamento (stato di una versione che il
            # testimone non lo teneva), e si ripaga da sé — la passata dopo il
            # testimone c'è.
            logger.warning(
                "gardener: the read prefix of {} does not match (cursor {}): "
                "the journal was rewritten over the cursor, re-reading from the start",
                rel, seen,
            )
            seen = 0
        elif seen == len(physical):
            # Letto fino in fondo e il prefisso torna: è il caso normale di ogni
            # giorno già digerito, e costa un digest.
            continue

        taken: list[str] = []
        consumed = seen
        stop = len(physical)
        for index in range(seen, len(physical)):
            line = physical[index].strip()
            if not line or line.startswith("#"):
                # Vuoto e intestazione non sono voci: si consumano senza
                # spendere budget e senza finire nel prompt.
                consumed = index + 1
                continue
            if not budget:
                # **Si esce**, non si continua a scorrere. Continuando, una riga
                # vuota dopo questo punto avrebbe fatto avanzare ``consumed``
                # oltre una voce non letta: quella voce sarebbe finita sotto il
                # cursore senza essere mai stata promossa, cioè persa in
                # silenzio. Il tetto deve fermare la lettura, non filtrarla.
                stop = index
                break
            taken.append(line)
            consumed = index + 1
            budget -= 1

        left_behind += sum(
            1 for raw in physical[stop:] if raw.strip() and not raw.strip().startswith("#")
        )

        if taken:
            files.append(JournalFileDelta(
                path=rel,
                lines=tuple(taken),
                cursor_after=consumed,
                witness_after=journal_witness(physical[:consumed]),
            ))

    delta = JournalDelta(files=tuple(files), left_behind=left_behind)
    if left_behind:
        logger.info(
            "gardener: delta for {} truncated to {} entries, {} left for the next round",
            root.name, delta.line_count, left_behind,
        )
    return delta
