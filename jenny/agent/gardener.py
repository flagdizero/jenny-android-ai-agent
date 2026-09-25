"""Il giardiniere — la passata che trasforma il diario di un progetto in pagine.

Passo **T4.2** di ``roadmap/taccuino-passi.md``. La cattura (T2) scrive righe di
diario mentre si conversa; qui quelle righe diventano pagine e la mappa torna
vera. Due mestieri separati di proposito: la cattura deve costare una chiamata e
non decidere niente, il giardiniere decide (nomi, struttura, cosa merita una
pagina) e per farlo ha bisogno di essere solo, a sessione ferma.

**Ha la forma di Dream**, e la somiglianza è deliberata fino ai nomi dei metodi:
inventario deterministico costruito in Python e messo nel prompt (al modello resta
il giudizio, non l'esplorazione), superficie di scrittura chiusa da un
``ToolRegistry`` costruito a mano, un solo runner condiviso fra il comando manuale
e — in T4.3 — il job cron, e il predicato di commit di Dream per decidere se il
cursore può avanzare.

Tre cose che questo modulo sa e che vale scrivere:

1. **Il confinamento è il registry, non lo scope.** Un turno interno gira su
   ``INTERNAL_CHANNEL``, e ``WorkspaceScopeResolver.for_turn`` per ogni canale che
   non sia la WebUI restituisce lo scope **di default** — l'intera installazione
   scrivibile. Quel che tiene il giardiniere dentro ``wiki/`` è la cassetta dei
   tool che gli si passa, e nient'altro. Da cui la regola: **una porta nella
   cassetta è una via d'uscita**, e ``spawn_subagent``/``python_exec``/``message``
   non ci entrano.
2. **I percorsi sono relativi al workspace**, non al progetto — per la stessa
   ragione per cui Dream scrive ``memory/MEMORY.md`` e non un assoluto. La base dei
   percorsi relativi è ``project_path`` dello scope legato, che per un turno
   interno è la radice dell'installazione; e su Android un assoluto viene
   rifiutato comunque, perché la dir dati è raggiungibile sotto due nomi
   (``/data/user/0`` e ``/data/data``) e la allowlist ne conosce uno.
3. **Il log lo scrive il codice, non il modello.** Toglie una destinazione dalla
   superficie di scrittura (che resta ``wiki/`` e basta) e rende la riga di log
   affidabile: un modello che scrive il proprio registro può raccontare una
   passata che non ha fatto. Il prezzo è che la riga dice quel che il codice sa —
   quante righe ha digerito, quante scritture sono riuscite — e non i nomi delle
   pagine.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Collection, Iterator
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from loguru import logger

from jenny.agent.gardener_state import (
    COMMITTED_STATUSES,
    MAX_DELTA_LINES,
    GardenerState,
    JournalDelta,
    read_journal_delta,
    read_state,
    record_attempt,
    write_state,
)
from jenny.agent.internal_run import (
    internal_run_completed,
    internal_run_should_commit,
    prune_internal_sessions,
)

# Il gancio si compone qui come sempre; il suo corpo è stato estratto in un modulo
# foglia il 26/08 perché ora lo monta anche ``_FsTool``, che importando questo
# modulo si tirerebbe dentro ``internal_run`` — v. la docstring di
# ``jenny/agent/wiki_provenance.py``.
from jenny.agent.wiki_provenance import _provenance_guard
from jenny.session.keys import GARDENER_SESSION_PREFIX
from jenny.utils.helpers import safe_zoneinfo
from jenny.utils.prompt_templates import render_template
from jenny.utils.wiki_paths import (
    WIKI_INDEX_FILENAME,
    is_wiki_root,
    iter_wiki_pages,
    page_chars_if_over,
    wiki_journal_dir,
)

# Tetto sull'elenco delle pagine messo nel prompt. Serve al giardiniere per non
# creare un doppione di una pagina che esiste, quindi è materiale di lavoro e non
# decorazione — ma oltre trecento voci il prompt costa più del servizio che rende,
# e il taglio si dice invece di tacerlo.
_MAX_INVENTORY_ENTRIES = 300

# Tetti sui documenti che entrano interi. Generosi: si pagano una volta per
# passata, non una volta per turno come la mappa del passo T3.
#
# Quello della **mappa** è il doppio di quello dei diari, e la ragione è la
# potatura: la mappa è l'artefatto che questa passata rimette in riga, e non si
# pota quel che non si vede. Sulle otto wiki vere la mappa più grossa è 12.298
# caratteri (misurata il 23/08): a 8000 sarebbe arrivata **tagliata** proprio
# alla passata che deve accorciarla, cioè col troncamento a nascondere la prosa
# da promuovere.
_MAX_MAP_CHARS = 16000
_MAX_JOURNAL_CHARS = 8000
_MAX_AGENTS_CHARS = 4000

# Il bersaglio della mappa, in caratteri. **Non è un numero scelto qui**: è la
# soglia oltre la quale il blocco di progetto smette di iniettare la mappa
# intera a ogni turno (``jenny/agent/context.py::_PROJECT_MAP_MAX_CHARS``, e il
# lint di T5 tiene lo stesso numero). Oltre, il modello vede la testa della
# mappa e nient'altro: su ``patreon-creator`` (12.298 caratteri, misurati il
# 23/08) il troncamento lascia **5 pagine su 51** fra quelle che la mappa
# nomina. L'elenco nudo delle stesse 51 costerebbe 1.495 caratteri — il tetto è
# giusto, ed è la prosa nella mappa a non doverci stare.
#
# È duplicato e non importato perché ``jenny/agent/context.py`` tira dentro
# mezzo repo (v. la nota su ``_CRON_TOOL_NAME`` lì) e questo modulo lo
# importerebbe per un intero: i due numeri li tiene uguali un test, come già
# fa il lint.
MAP_TARGET_CHARS = 2000


def page_ceiling() -> int:
    """Il tetto della **singola pagina**, in caratteri, letto da dove si paga.

    Passo **T3.14**. Oltre questa soglia una pagina non entra nel blocco di
    progetto **per niente**: non si accorcia e non si tronca, si salta intera —
    a ogni turno di ogni conversazione del progetto, e la selezione è
    alfabetica, quindi nessuna domanda dell'utente può richiamarla (v.
    ``ContextBuilder._read_project_pages``). È il numero che la regola SPLIT del
    prompt nomina, ed è il numero con cui questa passata decide **quali** pagine
    la regola riguarda.

    **Importato e non copiato**, al contrario di :data:`MAP_TARGET_CHARS`: la
    soglia è la stessa in tre posti — il blocco di progetto che la paga, il lint
    della wiki (``PAGE_MAX_CHARS``) e da qui il prompt — e una quarta copia
    sarebbe una quarta cosa da tenere allineata. **E' questa funzione il lettore
    per chi ne ha bisogno altrove**, non un secondo import di
    ``_PROJECT_PAGES_MAX_CHARS``: da T9.12 la chiama anche
    ``_FsTool._wiki_page_ceiling_note``, cosi' l'avviso che un tool di scrittura
    dà e l'annotazione che questa passata si mette nel prompt non possono parlare
    di due soglie diverse. L'import sta **dentro la
    funzione** per la ragione scritta sopra a ``MAP_TARGET_CHARS``:
    ``jenny/agent/context.py`` tira dentro mezzo repo, e questo modulo lo
    caricherebbe all'import per un intero. Dentro la funzione si paga alla prima
    chiamata (poi è ``sys.modules``) e si legge l'attributo ogni volta, che è
    anche quel che rende la condivisione **provabile** invece che dichiarata.
    """
    from jenny.agent.context import _PROJECT_PAGES_MAX_CHARS

    return _PROJECT_PAGES_MAX_CHARS


# Il marcatore con cui la passata chiude. **Un marcatore e non prosa**:
# interpretare testo libero e' il modo in cui questo genere di cose smette di
# funzionare senza che nessuno se ne accorga — un giorno il modello scrive la
# stessa cosa con altre parole e il canale e' morto in silenzio.
_FLAG_MARKER = "FLAG:"
_NO_FLAG_MARKER = "NOTHING TO FLAG"

# Le due righe di chiusura riconosciute **con la decorazione markdown addosso**.
# Il marcatore resta un marcatore — non si interpreta prosa — ma cercarlo nudo e
# a inizio riga costava il canale nel caso piu' ordinario che ci sia: dopo un
# prompt fatto di grassetti e di elenchi, ``**FLAG:** due pagine litigano`` e
# ``> FLAG: …`` sono la forma che un modello scrive per bella copia. E siccome
# «nessun marcatore» vuol dire di proposito «niente da segnalare», quel report
# si perdeva **senza traccia**: nessun errore, nessuna riga di log, e la
# contraddizione restava solo nella sezione aperta della mappa.
#
# Cosa ammette il prefisso, e perche' solo questo: spazi e tabulazioni (rientro),
# ``>`` (citazione), ``*``/``_``/`` ` `` (enfasi e codice), ``-`` e ``#`` (voce di
# elenco e intestazione). **Non** ``\s``, che comprende ``\n``: la ricerca e' riga
# per riga e una classe che attraversa i capoversi trasformerebbe la scansione
# dal fondo in una scansione del testo intero.
#
# Dopo i due punti si ammette la sola decorazione **attaccata** al marcatore
# (``**FLAG:** testo``), che e' chiusura del grassetto e non contenuto. Uno
# spazio in mezzo e la si tiene: in ``FLAG: **due pagine**`` gli asterischi sono
# del messaggio, e mangiarli e' riscrivere quel che una persona deve leggere.
_FLAG_LINE_RE = re.compile(
    r"^[ \t>*_`#-]*" + _FLAG_MARKER + r"[*_`]*[ \t]*(.*?)[ \t]*$", re.IGNORECASE
)
_NO_FLAG_LINE_RE = re.compile(r"^[ \t>*_`#-]*" + _NO_FLAG_MARKER, re.IGNORECASE)

# Tetto della riga di segnalazione nel log. Il log e' "una riga per operazione":
# un paragrafo qui lo rende illeggibile, ed e' l'unico registro che c'e'.
_MAX_FLAG_CHARS = 300

# Quanti messaggi **dell'utente** entrano nel controllo incrociato, e il tetto in
# caratteri che li contiene comunque. Non c'e' un cursore sul transcript, e la
# scelta e' deliberata: le righe del transcript non portano un timestamp e il file
# attivo **ruota** in segmenti, quindi un conteggio di righe si azzererebbe senza
# dirlo. Le ultime N invece sono sempre leggibili, e lo stato del confronto e' il
# **diario stesso**: quel che e' stato recuperato ci sta dentro, quindi il giro
# dopo non si recupera due volte. Idempotente per costruzione, senza stato nuovo.
#
# **Ma solo se il diario che si vede copre questa coda**, e fino a T2.10 non era
# vero: la coda e' sempre "adesso", il diario mostrato erano i giorni del delta,
# cioe' dove sta il cursore. Passata la riga recuperata sotto il cursore, il giro
# dopo non la vedeva piu' e la recuperava di nuovo. La condizione che rende vera
# la frase qui sopra sta in ``read_journal_days`` e nel budget del suo blocco:
# **la coda non si puo' datare, quindi la finestra la definisce il diario** — e
# deve arrivare piu' indietro di quanto la coda si estenda.
#
# Da cui anche perche' la strada opposta — restringere la coda alla finestra del
# delta — non e' costruibile: una riga di transcript non porta ne' data ne' ora,
# e datarla dal file non si puo' perche' il file attivo ruota. Servirebbe un
# campo nuovo nel transcript, cioe' un altro passo, e non risolverebbe il caso
# del cursore perso (una coda ristretta a giorni vecchi confronta il detto di
# allora, che nel transcript e' esattamente la parte che ruota via).
_RECENT_USER_MESSAGES = 40
_MAX_TRANSCRIPT_CHARS = 6000

# Il tetto del **blocco intero** di diario nel controllo incrociato, non del
# singolo giorno (quello e' ``_MAX_JOURNAL_CHARS``, ed e' un'altra cosa: il
# giorno grosso si tronca, il blocco grosso *lascia fuori dei giorni*). Senza
# questo numero il blocco non ne aveva nessuno: il tetto era ``MAX_DELTA_LINES``,
# duecento voci, e duecento voci possono stare in duecento file giornalieri —
# duecento giorni a 8.000 caratteri sono un prompt da un megabyte e mezzo, cioe'
# una finestra di contesto sfondata (che innesca il ciclo di ritentativi) oppure
# una passata che costa come un mese di passate.
#
# **Il doppio del lato "detto"**, e non e' un numero tondo scelto a caso: e' la
# proprieta' su cui poggia il confronto di T2.10. I due lati devono coprire la
# stessa finestra temporale, ma solo uno dei due si puo' datare — i giorni di
# diario hanno la data nel nome, le righe del transcript non portano niente (v.
# ``read_recent_user_messages``). Quindi la finestra la definisce il lato diario,
# e deve arrivare **almeno tanto indietro** quanto la coda dei messaggi. Il
# diario e' una distillazione della conversazione, non la sua trascrizione: N
# caratteri di diario rappresentano sempre piu' conversazione di N caratteri di
# messaggi. A parita' di budget la finestra del diario coprirebbe gia' piu'
# giorni della coda; al doppio il margine c'e' anche su un progetto in cui la
# cattura scrive molto.
#
# Ed e' **strettamente maggiore di ``_MAX_JOURNAL_CHARS``**, cosi' il giorno piu'
# recente — quello che il controllo incrociato puo' davvero usare — ci sta sempre
# dentro anche se da solo arriva al suo tetto.
_MAX_JOURNAL_BLOCK_CHARS = 2 * _MAX_TRANSCRIPT_CHARS

# Il marcatore di una riga di diario nata da un recupero e non dalla
# conversazione. Sta nel codice (v. ``JournalAppendTool.origin_marker``).
RECOVERED_MARKER = "[recovered]"

# Cosa sta al posto delle righe nuove quando non ce ne sono. **Non una sezione
# vuota:** il prompt apre dicendo «ti sono date le righe che nessuno ha letto», e
# una sezione vuota lascerebbe il modello a cercare del lavoro che non c'è — o a
# inventarlo, che è la cosa che il prompt vieta in fondo. Una passata a delta
# vuoto esiste per una ragione sola (v. ``GardenerStore.map_needs_pruning``) e la
# ragione va detta, altrimenti l'istruzione di potatura resta una regola fra le
# altre invece del compito.
_NO_JOURNAL_LINES = (
    "_(none: the journal has nothing this pass has not already read.)_\n\n"
    "**This pass is here for the map alone.** Nothing is waiting to be promoted, so step 1 of "
    "the work has no material and the map is the whole job: bring it under its ceiling by "
    "moving its prose into the pages it is about, exactly as the rule below says, and change "
    "nothing else. Do not go looking for other work, and do not invent a page."
)


@dataclass(frozen=True)
class GardenerOutcome:
    """L'esito di una passata, nella forma che i chiamanti sanno tradurre."""

    status: str
    elapsed: float = 0.0
    lines: int = 0
    writes: int = 0
    detail: str = ""
    map_pass: bool = False
    """Se questa passata è partita **per la mappa** e non per il diario.

    Da T3.5: a diario vuoto una mappa oltre il tetto è comunque una ragione per
    girare (v. ``GardenerStore.map_needs_pruning``). Chi scrive il messaggio
    all'utente ha bisogno di saperlo, perché quasi tutte le frasi degli esiti
    parlano di righe di diario — e su una passata così ce ne sono zero.
    """

    map_before: int = 0
    map_after: int = 0
    """La mappa prima e dopo, in caratteri. È il numero che questa passata esiste
    per muovere, quindi è il numero che va detto a chi l'ha chiesta."""

    # Quante passate di fila non hanno registrato niente, **questa compresa**.
    # Zero su una passata che ha committato (la serie si azzera) e su una che non
    # ha mai chiamato il provider (non c'è niente da contare). Chi lo legge è chi
    # decide se allarmare: il conto sta qui e non nel chiamante perché la strada a
    # mano e quella del cron devono contare la stessa cosa.
    failures: int = 0

    @property
    def ran(self) -> bool:
        """Se una chiamata al provider è avvenuta.

        ``already_running`` sta fra i «no» e non fra i fallimenti: la passata non è
        nemmeno partita perché un'altra era in volo su quello stesso progetto,
        quindi non c'è niente da timbrare né da contare — il lavoro lo sta facendo
        qualcun altro adesso.
        """
        return self.status not in (
            "skipped_no_delta", "skipped_not_a_project", "already_running",
        )


class GardenerStore:
    """File I/O, prompt e cassetta dei tool di una passata su **un** progetto."""

    def __init__(
        self,
        root: Path,
        workspace: Path,
        *,
        max_delta_lines: int = MAX_DELTA_LINES,
        today: Any = None,
        now: Any = None,
    ) -> None:
        # **Entrambi risolti, qui e non ai punti d'uso.** Su Android la dir dati
        # e' raggiungibile come ``/data/user/0/<pkg>`` e ``Path.resolve()`` la
        # riscrive in ``/data/data/<pkg>``: se una delle due radici arriva
        # risolta e l'altra no, ``relative_to`` alza ``ValueError`` e il percorso
        # che finisce nel prompt e' sbagliato. Misurato sul telefono il 23/08 —
        # il modello ha scritto quattro pagine perfette in ``zz-t4/wiki/`` invece
        # di ``wikis/zz-t4/wiki/``, e sono state rifiutate tutte.
        self.root = root.resolve(strict=False)
        self.workspace = workspace.resolve(strict=False)
        self.max_delta_lines = max_delta_lines
        # Iniettabili per i test; in produzione nessuno li passa. ``_today`` resta
        # la sola **data**: l'istante lo dà ``_stamp``, e chi inietta la data
        # inietta il giorno di quell'istante. ``None`` e non ``date.today``,
        # perché è la differenza fra "il giorno lo decide chi ha iniettato" e
        # "il giorno è una **seconda** lettura dell'orologio" — che è il difetto
        # che ``log_pass`` aveva (v. la sua docstring).
        self._today = today
        self._now = now
        # Se l'**ultimo** prompt costruito da questo store portava una finestra
        # del controllo incrociato tagliata. Un attributo e non un valore di
        # ritorno perché ``build_prompt`` torna la stringa del prompt e i suoi
        # punti di chiamata sono tredici: allargarne la firma per un booleano
        # avrebbe cambiato tredici righe per farne arrivare una. Uno store è di
        # una passata (``for_project`` lo costruisce il tick, e ``/gardener`` il
        # comando), quindi «l'ultimo» è «questo».
        self.cross_check_truncated = False

    @classmethod
    def for_project(
        cls, workspace: Path, name: str, *, wikis_dir_name: str = "wikis"
    ) -> "GardenerStore | None":
        """Lo store del progetto *name*, o ``None`` se quella cartella non è un progetto.

        ``None`` e non un'eccezione: il chiamante che itera i progetti non deve
        avvolgere ogni giro in un ``try``, e quello che risponde a un comando ha
        un rifiuto da scrivere, non un errore da propagare.
        """
        root = (workspace / wikis_dir_name / name).resolve(strict=False)
        wikis_root = (workspace / wikis_dir_name).resolve(strict=False)
        # Il nome arriva da una chiave di sessione o da un argomento di comando:
        # un ``..`` non deve poter far uscire la passata dalla cartella dei
        # progetti (stessa guardia di ``WorkspaceScopeResolver.for_project``).
        if root == wikis_root or wikis_root not in root.parents:
            logger.warning("gardener: {} cade fuori da {}", name, wikis_root)
            return None
        if not is_wiki_root(root):
            return None
        return cls(root, workspace)

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def rel_root(self) -> str:
        """La radice del progetto **relativa al workspace**: ``wikis/viaggio``.

        È la forma in cui i percorsi vanno nel prompt (v. la docstring del
        modulo), e non è una preferenza estetica: un assoluto verrebbe rifiutato
        dalla guardia.
        """
        try:
            return self.root.relative_to(self.workspace).as_posix()
        except ValueError:
            # Non deve capitare — le due radici sono risolte alla costruzione, e
            # un progetto sta sempre dentro il workspace. Se capita e' un difetto
            # di programmazione, e va **detto**: il fallback restituisce un
            # percorso dall'aria giusta su cui ogni scrittura verra' rifiutata in
            # silenzio, che e' esattamente come questo bug si e' presentato la
            # prima volta.
            logger.error(
                "gardener: {} is not inside the workspace {}: prompt paths "
                "will be refused",
                self.root, self.workspace,
            )
            return self.root.name

    # -- input ---------------------------------------------------------------

    def read_delta(self) -> JournalDelta:
        return read_journal_delta(
            self.root, read_state(self.root), max_lines=self.max_delta_lines
        )

    def commit(
        self,
        delta: JournalDelta,
        *,
        at: datetime | None = None,
        map_chars: int | None = None,
    ) -> None:
        """Registra il delta come letto. Da chiamare **solo** a passata riuscita.

        *map_chars* è la misura della mappa a passata finita, e va passata anche
        quando il delta è vuoto: è quel che disarma il secondo innesco (v.
        ``map_needs_pruning`` e ``GardenerState.map_left_at``). Una potatura a
        metà è un **commit**, quindi il contatore degli insuccessi non la vede: se
        la misura non atterrasse qui, la stessa mappa tornerebbe candidata alla
        distanza minima dopo, per sempre.
        """
        write_state(
            self.root, read_state(self.root).advanced(delta, at=at, map_chars=map_chars)
        )

    def build_inventory(self) -> str:
        """L'elenco delle pagine, con **le troppo lunghe segnalate**.

        Passo **T3.14**. T3.3 ha insegnato al prompt che una pagina che sfonda il
        budget di iniezione si **taglia**; l'elenco però diceva percorso e titolo
        e nient'altro, quindi la passata aveva la regola e non i suoi soggetti —
        su ``main`` (52 pagine) non poteva sapere quali nove fossero oltre il
        tetto senza aprirle tutte e cinquantadue. E nessun segnale in scrittura
        le nominerà mai: delle 188 pagine vere misurate il 23/08 le 23 oltre il
        tetto le hanno scritte le **conversazioni**, non il giardiniere.

        **Si annotano solo quelle oltre il tetto.** La misura di ogni pagina,
        accanto a ogni pagina, sarebbe un elenco di numeri in cui i nove che
        contano non si vedono — e il tetto sull'elenco (:data:`_MAX_INVENTORY_ENTRIES`)
        conta le *voci*, non i caratteri, quindi l'annotazione non può tagliare
        una pagina fuori dall'elenco: costa solo prompt. Misurato sulla wiki vera
        più grande (``main``, 52 pagine di cui 9 oltre il tetto): l'elenco passa
        da 2.846 a 3.889 caratteri, **+1.043** — nove annotazioni e la nota in
        fondo, dentro un prompt che porta 16.000 caratteri di sola mappa.
        """
        entries = iter_wiki_pages(self.root / "wiki")
        if not entries:
            return "_(no pages yet — this project is starting from the journal)_"
        shown = entries[: _MAX_INVENTORY_ENTRIES]
        ceiling = page_ceiling()
        lines: list[str] = []
        over = 0
        for rel, title in shown:
            chars = page_chars_if_over(self.root / "wiki" / rel, ceiling)
            if chars is None:
                lines.append(f"- `{rel}` — {title}")
                continue
            over += 1
            # **Il marcatore prima della misura.** Un titolo vero contiene già dei
            # trattini lunghi (``Nakasendo — 中山道``, misurato sulle wiki vere):
            # aperta con le parole invece che col numero, l'annotazione si legge
            # come annotazione e non come la coda del titolo.
            lines.append(
                f"- `{rel}` — {title} — **over the ceiling: {chars} characters, "
                "cannot be injected at all; split it**"
            )
        if len(entries) > len(shown):
            lines.append(
                f"- _(list truncated: {len(entries)} pages in all, {len(shown)} shown)_"
            )
        if over:
            # Il perché una volta sola, in fondo: la regola SPLIT sta nel prompt
            # sopra, e qui servono i **soggetti**. Ripetere l'argomento su ogni
            # riga costerebbe nove volte e si leggerebbe una.
            lines.append(
                f"- _(the {over} page(s) marked **over the ceiling** are what the SPLIT rule "
                f"above is about: each is past the {ceiling} characters a turn in this project "
                "can inject, so it is skipped whole in every conversation here — it exists on "
                "disk and nobody can read it. Conversations grew them, not you; splitting one "
                "is a promotion, and it is work this pass may do.)_"
            )
        return "\n".join(lines)

    def _read_capped(self, path: Path, cap: int, label: str) -> str:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return ""
        if len(text) <= cap:
            return text
        # Mai troncare zitti: la nota è la differenza fra "questo è tutto" e
        # "questo è quanto ci stava" (la lezione del tetto di T3).
        return text[:cap] + (
            f"\n\n[{label} continues — {len(text)} characters in all; read the file for the rest]"
        )

    @property
    def map_path(self) -> Path:
        # Il nome del file viene dalla costante e non da qui (T3.12): questo è il
        # **secondo** lettore della mappa, e il primo — l'iniettore di
        # ``context.py`` — la escludeva già dall'elenco delle pagine leggendo la
        # costante. Scritto a mano in due posti, cambiare la costante avrebbe
        # tolto la mappa al prompt e messo ``index.md`` fra le pagine.
        return self.root / "wiki" / WIKI_INDEX_FILENAME

    def map_chars(self) -> int:
        """La misura della mappa, in caratteri. ``0`` se non c'è o non si legge.

        La stessa misura che il blocco di progetto confronta col suo tetto, cioè
        il file **spogliato** ai bordi (``strip``) e non la sua dimensione su
        disco: due numeri diversi per la stessa cosa sarebbero il modo di far
        potare una passata che non doveva, o di non farla potare quando doveva.
        """
        try:
            return len(self.map_path.read_text(encoding="utf-8").strip())
        except (OSError, UnicodeDecodeError):
            return 0

    def map_needs_pruning(self, state: GardenerState | None = None) -> bool:
        """Se la mappa, **da sola**, vale una passata.

        Passo **T3.5**. Fino al 23/08/2026 l'unica ragione per girare era il delta
        di diario, e su un telefono vero quella ragione non arriva: le otto wiki
        misurate quel giorno hanno tutte ``raw/journal/`` **vuota** e sette mappe
        su otto oltre il tetto di iniezione. Il produttore era stato riparato (T3.4
        insegna a potare) e l'artefatto no, perché nessuna passata partiva: su un
        progetto che l'utente non sta usando «finché la cattura non scrive righe»
        vuol dire mai. Da qui il secondo innesco.

        **Due condizioni, e la seconda è il freno.** Oltre il tetto, *e* più grossa
        di come l'ultima passata l'ha lasciata. Senza la seconda l'innesco sarebbe
        un livelock: una ragione che resta vera dopo la passata si ripresenta al
        cancello della distanza dopo, per sempre, e non la fermerebbe nessuno dei
        due freni esistenti — il timbro del tentativo *ritarda* di
        ``min_hours_between_passes`` e non esclude (è il suo contratto), e
        ``failures`` non conta le potature a metà, perché quelle **committano**.
        Con la seconda condizione l'innesco vale una passata per episodio, e a
        riarmarlo resta solo una mappa che ricresce — cioè l'unico caso in cui c'è
        del lavoro nuovo.

        *state* si passa quando il chiamante lo ha già in mano (la selezione), per
        non rileggere due volte lo stesso file piccolo.
        """
        chars = self.map_chars()
        if chars <= MAP_TARGET_CHARS:
            return False
        left_at = (read_state(self.root) if state is None else state).map_left_at
        return left_at is None or chars > left_at

    @staticmethod
    def _render_delta(delta: JournalDelta) -> str:
        blocks: list[str] = []
        for page in delta.files:
            blocks.append(f"**{page.path}**\n\n" + "\n".join(page.lines))
        body = "\n\n".join(blocks)
        if delta.left_behind:
            body += (
                f"\n\n_({delta.left_behind} further journal lines were left for the next pass: "
                "this pass has a ceiling. Do not try to read them — they will arrive.)_"
            )
        return body

    def read_journal_days(self, *, max_chars: int = _MAX_JOURNAL_BLOCK_CHARS) -> str:
        """I giorni di diario **più recenti** che stanno nel budget, per interi.

        Serve al controllo incrociato e non alla promozione: il delta contiene
        solo le righe *nuove*, e un fatto già catturato sta spesso **sotto** il
        cursore. Confrontare il detto col solo delta segnalerebbe come mancante
        tutto quello che una passata precedente aveva già letto — cioè, sulla
        seconda passata di una giornata, quasi tutto.

        **I giorni recenti, e non i giorni che il delta tocca** (passo T2.10). Il
        lato "detto" del confronto è sempre la coda dei messaggi, cioè *adesso*;
        il lato "registrato" era invece dove stava il cursore, e le due cose
        coincidono solo quando il delta *è* la coda. Quando non coincidono il
        confronto mente in entrambi i versi:

        * cursore perso su un progetto con mesi di diario, il delta è il fondo —
          i giorni **più vecchi** accanto ai messaggi di questo mese. Ogni fatto
          recente si legge come «detto e mai registrato», e la passata appende
          una dozzina di righe ``[recovered]`` per fatti che stanno già nel
          diario e già sulle pagine: pagine doppie, o «una pagina che litiga con
          se stessa», che è la cosa che il prompt vieta;
        * regime, progetto tranquillo: una riga recuperata viene promossa, il
          cursore le passa sopra, e il delta del giro dopo è solo il file di
          domani. La riga non si vede più, mentre la coda dei messaggi — invariata,
          perché il progetto è tranquillo — contiene ancora il fatto. **Si
          recupera di nuovo**, e di nuovo, per sempre.

        La rivendicazione di idempotenza («lo stato del confronto è il diario
        stesso») vale solo se il diario che si vede copre la coda dei messaggi che
        si vede. Qui la finestra è quella: i giorni più recenti che
        :data:`_MAX_JOURNAL_BLOCK_CHARS` permette, e siccome quel budget è il
        doppio di quello dei messaggi la finestra arriva più indietro della coda
        (v. la nota sulla costante). Su un progetto piccolo — cioè il caso del
        secondo punto — il budget copre il diario **intero**, quindi la riga
        recuperata resta visibile e non si recupera due volte.

        Nessun tetto sul *numero* di giorni, solo sui caratteri: un tetto sui
        giorni riaprirebbe il secondo caso proprio sui progetti sparsi, dove
        cento giorni di diario sono pochi kilobyte e stanno tutti nel budget.

        *max_chars* è iniettabile per i test, come ``limit`` e ``max_chars`` di
        ``read_recent_user_messages``: coi numeri di produzione la guardia «il
        giorno più recente entra comunque» non è raggiungibile (il tetto del
        blocco è più largo di quello del singolo giorno), e una guardia che
        nessun test può battere è una guardia di cui nessuno sa se funziona.
        """
        journal = wiki_journal_dir(self.root)
        if not journal.is_dir():
            return ""
        days = [page for page in sorted(journal.glob("*.md")) if not page.name.startswith(".")]
        blocks: list[str] = []
        omitted = 0
        total = 0
        # **Dal più recente all'indietro**, ed è il verso in cui si spende il
        # budget: i giorni recenti sono quelli su cui il confronto può agire.
        # Reso poi in ordine cronologico, come il diario si legge.
        #
        # Il ciclo **esce** appena il budget è finito, quindi i file letti sono
        # quelli della finestra e non tutti: su un progetto con mille giorni di
        # diario il ``glob`` elenca mille nomi e si aprono i pochi che entrano.
        for index in range(len(days) - 1, -1, -1):
            text = self._read_capped(days[index], _MAX_JOURNAL_CHARS, "The journal")
            if not text:
                continue
            block = f"**{days[index].relative_to(self.root).as_posix()}**\n\n{text}"
            total += len(block)
            # ``and blocks``: il giorno più recente entra comunque. Un blocco
            # vuoto che annuncia «tre giorni non mostrati» è peggio di niente, ed
            # è la stessa guardia che ha già il tetto dei messaggi.
            if total > max_chars and blocks:
                omitted = index + 1
                break
            blocks.append(block)
        if not blocks:
            return ""
        body = "\n\n".join(reversed(blocks))
        if not omitted:
            return body
        # Il taglio si dice, e si dice **dove è avvenuto**: i giorni che mancano
        # stanno prima di questi, quindi la nota va in testa e non in coda.
        return (
            f"_({omitted} earlier journal day(s) are not shown: this block has a ceiling. What "
            "follows is the most recent record — the part this cross-check can act on. A fact you "
            "cannot find below may well have been recorded on one of the days left out.)_\n\n"
        ) + body

    def build_prompt(self, delta: JournalDelta) -> str:
        """Prompt completo della passata: meccanismo, poi i dati, ognuno recintato.

        I dati sono **recintati a quattro backtick** per la ragione del passo T3:
        una riga di diario o una pagina possono contenere intestazioni ``#``, e
        senza recinto sbucherebbero nella struttura del prompt come se fossero
        sezioni di istruzioni. Quel che sta in un file dell'utente è dato, e va
        nel canale dei dati.

        **La misura della mappa e il suo bersaglio entrano sempre, l'ordine di
        potare solo quando serve.** La misura sta nell'intestazione della sezione
        (accanto al file di cui parla, che è l'unico posto dove non si può
        leggere staccata dal suo oggetto); il ritaglio nel prompt è dentro un
        ``{% if %}``, perché su una mappa che sta nel suo tetto una regola sulla
        potatura è un invito a potare per niente — e la potatura muove prosa
        dentro le pagine, cioè non è gratis.
        """
        map_chars = self.map_chars()
        over_budget = map_chars > MAP_TARGET_CHARS
        parts = [
            render_template(
                "agent/gardener.md",
                strip=True,
                project_path=self.rel_root,
                project_name=self.name,
                map_chars=map_chars,
                map_target=MAP_TARGET_CHARS,
                map_over_budget=over_budget,
                # Il tetto della pagina **dalla stessa fonte dell'inventario**: la
                # regola SPLIT lo nominava con un letterale suo, e una regola che
                # dice una soglia accanto a un elenco che segnala a un'altra è una
                # regola che il modello non può applicare.
                page_max=page_ceiling(),
            ),
            "## New journal lines\n\n" + (
                self._render_delta(delta) if delta.files else _NO_JOURNAL_LINES
            ),
            (
                "## The map as it stands (`{}/wiki/index.md`) — {} characters, against a "
                "ceiling of {}\n\n````markdown\n{}\n````"
            ).format(
                self.rel_root,
                map_chars,
                MAP_TARGET_CHARS,
                self._read_capped(self.map_path, _MAX_MAP_CHARS, "The map") or "_(empty)_",
            ),
            "## Pages that already exist\n\n" + self.build_inventory(),
        ]
        # **Il controllo incrociato solo su una passata di diario.** Questa
        # guardia è tutto quel che lo tiene fuori da una passata per la mappa: da
        # T2.10 il lato "registrato" c'è comunque (``read_journal_days`` legge i
        # giorni recenti, non i giorni del delta), quindi la ragione non è più «non
        # ci sarebbe niente contro cui misurare» ma il compito e la spesa. Una
        # passata a delta vuoto gira **per la mappa** e ha un compito solo (v.
        # ``_NO_JOURNAL_LINES``): metterle davanti quel che l'utente ha detto è
        # aprire un secondo cantiere, e sono fino a 18.000 caratteri di prompt —
        # 6.000 di messaggi più 12.000 di diario — per un lavoro che non le
        # appartiene.
        said, said_truncated = (
            read_recent_user_messages(self.name) if delta.files else ([], False)
        )
        # Il taglio serve **anche** a chi rilegge il registro del progetto, non
        # solo al modello: v. ``log_pass``. Si segna solo se la finestra è
        # arrivata al prompt — su una passata per la mappa non c'è controllo
        # incrociato, quindi non c'è niente di tagliato da raccontare.
        self.cross_check_truncated = bool(said) and said_truncated
        if said:
            # Il lato "detto" e il lato "registrato", **accanto**: il confronto è
            # una lettura, non una ricerca.
            lines = "\n".join(f"- {message}" for message in said)
            if said_truncated:
                lines += "\n- _(older messages not shown: this is the recent tail)_"
            parts.append(
                "## What the user actually said, most recent last\n\n````text\n"
                + lines
                + "\n````"
            )
            recorded = self.read_journal_days()
            if recorded:
                # L'intestazione dice **quale finestra**: «those days» nominava i
                # giorni del delta, che da T2.10 non sono più questi. Un modello a
                # cui si dice «il diario di quei giorni» e si dà un'altra finestra
                # legge un'assenza come una mancanza.
                parts.append(
                    "## What the journal already holds, over the same recent stretch, in full"
                    "\n\n````markdown\n"
                    + recorded
                    + "\n````"
                )
        agents = self._read_capped(
            self.root / "AGENTS.md", _MAX_AGENTS_CHARS, "This project's instructions"
        )
        if agents:
            parts.append(
                "## This project's own instructions (`{}/AGENTS.md`)\n\n````markdown\n{}\n````"
                .format(self.rel_root, agents)
            )
        return "\n\n---\n\n".join(parts)

    # -- sandbox -------------------------------------------------------------

    def build_tools(self, *, write_guard: Any = None):
        """La cassetta della passata: legge dentro il progetto, scrive in ``wiki/``.

        **Questa funzione è il confinamento** (v. la docstring del modulo), quindi
        due proprietà vanno lette qui e non altrove: l'elenco dei tool è chiuso —
        nessuno spawn, nessun ``python_exec``, nessun ``message``, cioè nessuna
        porta verso una scrittura per interposta persona — e la sola directory
        scrivibile è ``wiki/``.

        **Ma "confinamento" è dove si può scrivere, non che cosa si può scrivere,
        e la differenza è un invariante intero.** Quel che questa cassetta
        garantisce da sé è che nessun byte esca da ``wiki/`` e che **nessun file
        possa essere rimosso**: fra i tre tool di scrittura non c'è un'azione di
        cancellazione (``apply_patch`` conosce ``add`` e ``replace``; l'``unlink``
        che ha dentro è il suo rollback), e senza ``python_exec`` non c'è un'altra
        strada. Quel che **non** garantisce è il «non cancellare mai una pagina»
        che il prompt chiama assoluto: ``write_file`` con ``content=""`` su una
        pagina che esiste riesce, e la lascia vuota — così come la riscrive
        ``edit_file``, o un ``replace`` da una riga. La rete di quel caso è
        l'istantanea ``pre_gardener`` (v. ``_checkpoint``), non questa funzione.

        E la rete resta la scelta giusta: un rifiuto della scrittura vuota
        chiuderebbe la lettera dell'invariante e non la sostanza — «una pagina da
        cinquemila caratteri sostituita da un punto» è la stessa perdita e non è
        distinguibile da una promozione legittima senza leggere nel merito —
        mentre farebbe *sembrare* enforced una regola che resta del prompt. Il
        difetto qui era la frase, non la cassetta.

        ``.resolve()`` su entrambe le radici per la ragione imparata sul telefono: su
        Android la dir dati è esposta come ``/data/user/0/<pkg>`` ma ``resolve()``
        la riscrive in ``/data/data/<pkg>``, e se la base di risoluzione e la
        allowlist restano in forme diverse la guardia anti-symlink scatta e la
        passata non riesce a scrivere niente.

        *write_guard* è il gancio **pre-scrittura** dei tre tool di scrittura:
        ``(path, testo) -> None`` per lasciar passare, o la frase di rifiuto che
        il modello legge. Il parametro dei tool si chiama ``write_size_guard``
        perché il primo (e finora unico) uso era il budget dei file di memoria,
        ma il contratto è generico — «questa scrittura può andare su disco?» — ed
        è l'unico punto che i tre tool condividono *prima* di toccare il file.
        Qui serve a ``run_gardener`` per cedere il passo all'utente
        (v. ``_yield_to_user_guard``); montarne un secondo, gemello e con un
        altro nome, avrebbe voluto dire allargare la firma di ``_FsTool`` per
        duplicarne la semantica.
        """
        from jenny.agent.tools.apply_patch import ApplyPatchTool
        from jenny.agent.tools.file_state import FileStates
        from jenny.agent.tools.filesystem import (
            EditFileTool,
            ListDirTool,
            ReadFileTool,
            WriteFileTool,
        )
        from jenny.agent.tools.registry import ToolRegistry
        from jenny.agent.tools.search import FindFilesTool, GrepTool

        tools = ToolRegistry()
        file_states = FileStates()
        root = self.root.resolve()
        pages = (root / "wiki").resolve()

        # Lettura: dentro il progetto. Non l'intera installazione come Dream —
        # il giardiniere non ha niente da leggere in un altro progetto, e il
        # prompt di progetto dice che il lavoro fra progetti non esiste.
        #
        # ``read_media_dir=False`` perché fino al 24/08/2026 quella frase era
        # falsa: ``_FsTool._resolve_read`` metteva ``<workspace>/.jenny/media``
        # fra le radici ammesse per **ogni** tool di lettura, e quella cartella la
        # condividono tutte le conversazioni. Misurato: con la forma di percorso
        # che il prompt stesso insegna (relativa al workspace), la cassetta
        # elencava ``.jenny/media``, la percorreva con ``grep`` e ne leggeva
        # dentro — tre tool su quattro, non uno. Non è una via d'uscita (la
        # scrittura resta rifiutata) ma è il verso rovesciato di T7.8: un
        # artefatto personale di un'altra conversazione a portata della passata
        # che scrive le pagine di *questo* progetto. La ragione con cui T4.13
        # tenne quella cartella raggiungibile — «un subagent a cui si chiede di
        # guardare un'immagine» — qui non si applica: a questa passata non chiede
        # niente nessuno, e non ha nemmeno un utente con cui parlare.
        for read_only_tool in (ReadFileTool, ListDirTool, FindFilesTool, GrepTool):
            tools.register(read_only_tool(
                workspace=root,
                allowed_dir=root,
                file_states=file_states,
                read_media_dir=False,
            ))
        # Scrittura: solo ``wiki/``. Il diario resta fuori (è l'input, ed è
        # append-only), ``AGENTS.md`` resta fuori (le premesse le cambia
        # l'utente), ``raw/`` e ``audit/`` restano fuori. Il log non è qui perché
        # lo scrive il codice.
        for write_tool in (WriteFileTool, EditFileTool, ApplyPatchTool):
            tools.register(write_tool(
                workspace=root,
                allowed_dir=pages,
                file_states=file_states,
                restrict_to_workspace=True,
                write_size_guard=write_guard,
            ))
        # ``journal_append`` è **l'unica scrittura fuori da ``wiki/``** che si
        # concede, e la ragione è che non può violare la regola che protegge:
        # appende in coda per costruzione, quindi non riscrive la fonte da cui sta
        # promuovendo. Il progetto è iniettato perché una passata interna gira con
        # lo scope di default e la deduzione darebbe "nessun progetto".
        from jenny.agent.tools.journal import JournalAppendTool

        tools.register(JournalAppendTool(root=root, origin_marker=RECOVERED_MARKER))
        tools.file_states = file_states
        return tools

    # -- tracce --------------------------------------------------------------

    def session_key(self) -> str:
        """Chiave della passata, es. ``gardener:viaggio-20260823-213000``."""
        return f"{GARDENER_SESSION_PREFIX}{self.name}-{datetime.now():%Y%m%d-%H%M%S}"

    def _stamp(self, timezone: str | None) -> datetime:
        """L'istante della passata, nel fuso in cui il modello legge l'ora.

        Stessa forma di ``JournalAppendTool._stamp`` (passo B18), e per le stesse
        due ragioni. ``safe_zoneinfo`` e non ``ZoneInfo``: su Android il database
        tzdata può mancare, e non si perde una riga di registro per un fuso.
        """
        if self._now is not None:
            return self._now()
        tz = safe_zoneinfo(timezone) if timezone else None
        return datetime.now(tz=tz) if tz else datetime.now().astimezone()

    def log_pass(
        self,
        delta: JournalDelta,
        *,
        elapsed: float,
        writes: int,
        timezone: str | None,
        flag: str | None = None,
        refused: int = 0,
        map_before: int = 0,
        map_after: int = 0,
    ) -> None:
        """Una riga in ``log/AAAAMMGG.md``: il solo posto dove si vede la passata.

        Si scrive **se la passata ha letto righe di diario**, che abbia promosso
        o no. La regola di prima — solo a scrittura avvenuta — proteggeva il
        registro da «una riga per ogni giro a vuoto», e la protezione era contro
        un caso che qui non arriva mai: senza delta la passata esce a
        ``skipped_no_delta``, prima del modello. Chi arriva fin qui ha letto
        righe vere; se non ha promosso niente **ha comunque bruciato il cursore
        su quelle righe**, e il diario è append-only, quindi nessun giro futuro
        le rivedrà. Quello è l'evento più consequenziale che questa passata possa
        produrre, ed era l'unico a non lasciare traccia: il 25/08 tre passate su
        ``viaggio-pazzo`` ne hanno lasciata **una**, e dal registro non si
        distingueva «non è mai passato» da «è passato e ha deciso di no».

        La regola era già stata forzata una volta, per le segnalazioni, con
        questo stesso argomento — una cosa importante non si perde per non aver
        promosso niente. Questa è la seconda metà di quella stessa correzione.

        ``refused`` sono le scritture volute e non atterrate. Quando ce ne sono la
        riga **lo dice**, e dice anche che il cursore è fermo: la forma di prima
        («N journal lines → M writes») raccontava M come se fossero tutte, cioè
        presentava una passata riuscita a metà come una riuscita — e questo log è
        l'unico registro che c'è, quindi il difetto non sarebbe emerso da nessun
        altro posto.

        ``map_before``/``map_after`` sono la misura della mappa prima e dopo, e la
        riga **le dice solo quando è calata**: la potatura è l'unica manovra di
        questa passata che *toglie* del testo da un file dell'utente, quindi è
        quella che una persona deve poter rileggere. Una mappa cresciuta non si
        annota — crescere è il caso normale, e sta già nel conto delle scritture.

        **La finestra tagliata del controllo incrociato si annota come
        sottoriga**, e non come una voce sua. La rete del controllo incrociato è
        lossy per costruzione — le ultime 40 cose dette, 6.000 caratteri, nessun
        cursore perché il transcript ruota — e sei ore chiacchierone spingono la
        parte iniziale fuori portata **per sempre**: un fatto può cadere da
        *entrambe* le reti (la cattura non l'ha visto, il controllo incrociato non
        lo vede più) e finora nessuna persona lo sapeva. Sottoriga e non voce
        perché su un progetto vissuto la finestra si taglia quasi a ogni passata:
        una voce per volta direbbe sempre la stessa cosa e renderebbe illeggibile
        l'unico registro che c'è. Attaccata alla riga della passata dice invece
        una cosa che si usa — *queste* promozioni sono state decise guardando
        soltanto la coda.

        **Un colpo d'orologio, due usi: la pagina e l'ora della riga.** Prima
        erano due letture separate — ``date.today()`` per il nome della pagina e
        ``datetime.now()`` per l'``## [HH:MM]`` — quindi una passata a cavallo
        della mezzanotte scriveva ``## [00:0x]`` nella pagina di *ieri*: una
        passata datata a un giorno in cui non è girata, nell'unico registro che
        c'è. È il difetto che il passo B18 ha chiuso in ``journal.py``, e questo
        è lo stesso posto un piano più in su.

        *timezone* è **obbligatorio e senza default**, e non per pedanteria: era
        la seconda metà di B18 (l'ora di sistema mentre il modello legge l'ora
        del fuso configurato, ``context.py::current_time_str``), e un default
        avrebbe rimesso l'ora di sistema in mano al prossimo ramo che si
        dimentica di passarlo — cioè esattamente il modo in cui questo difetto è
        arrivato fino a qui. Lo sa solo ``run_gardener`` (``_timezone_of``): chi
        costruisce lo store — la selezione del tick, ``/gardener``, i test — non
        ha un agente da cui leggerlo, quindi il fuso è un argomento **di
        chiamata** e non del costruttore.
        """
        stamp = self._stamp(timezone)
        day = self._today() if self._today is not None else stamp.date()
        page = self.root / "log" / f"{day.strftime('%Y%m%d')}.md"
        days = ", ".join(Path(f.path).stem for f in delta.files)
        if refused:
            outcome = (
                f"{writes} of {writes + refused} writes ({refused} refused, journal "
                "left unread)"
            )
        elif writes:
            outcome = f"{writes} writes"
        else:
            # «0 writes» direbbe il numero e non il fatto. Il fatto è che quelle
            # righe sono state lette, giudicate e consumate: il cursore le ha
            # passate, e non torneranno.
            outcome = "nothing promoted"
        # A delta vuoto la passata è girata **per la mappa**: «0 journal lines ()»
        # sarebbe una riga di registro che non dice cosa è successo, ed è l'unico
        # registro che c'è.
        subject = f"{delta.line_count} journal lines ({days})" if delta.files else "the map alone"
        entry = (
            f"## [{stamp:%H:%M}] gardener | {subject} → {outcome} in {elapsed:.1f}s\n"
        )
        if map_before and map_after < map_before:
            entry += (
                f"- map pruned: {map_before} → {map_after} characters "
                f"(-{map_before - map_after})\n"
            )
        if self.cross_check_truncated:
            entry += (
                "- cross-check window truncated: only the recent tail of what the user said was "
                "in reach, so a fact said earlier and never captured could not be recovered here\n"
            )
        if flag:
            entry += f"- flagged: {flag}\n"
        try:
            page.parent.mkdir(parents=True, exist_ok=True)
            fresh = not page.exists()
            with page.open("a", encoding="utf-8") as fh:
                if fresh:
                    fh.write(f"# {day.isoformat()}\n\n")
                fh.write(entry)
        except OSError as exc:
            # Il log è una traccia, non il lavoro: se non si scrive, la passata
            # resta valida e il cursore avanza comunque.
            logger.warning("gardener: log not written to {}: {}", page, exc)


async def _silent(*_args: Any, **_kwargs: Any) -> None:
    pass


def _lines_backwards(path: Path, *, chunk: int = 64 * 1024) -> Iterator[str]:
    """Le righe di *path* dalla fine verso l'inizio, un blocco alla volta.

    Serve a ``read_recent_user_messages``, che vuole **la coda** di un file che
    arriva a 8 MB (``transcript_store._MAX_TRANSCRIPT_FILE_BYTES``): leggerlo in
    avanti significa parsarlo tutto per buttarne via il 99% a ogni passata.

    Si divide sui byte e si decodifica **per riga intera**: nessuna sequenza
    UTF-8 multibyte contiene ``0x0A``, quindi tagliare su ``b"\\n"`` non può
    spezzare un carattere a metà. Il resto del blocco (la prima riga, che
    continua nel blocco precedente) si porta indietro e si unisce là.
    """
    with path.open("rb") as fh:
        fh.seek(0, 2)
        pos = fh.tell()
        head = b""
        while pos > 0:
            step = min(chunk, pos)
            pos -= step
            fh.seek(pos)
            parts = (fh.read(step) + head).split(b"\n")
            head = parts[0]
            for raw in reversed(parts[1:]):
                yield raw.decode("utf-8", "replace")
        if head:
            yield head.decode("utf-8", "replace")


def _user_said_backwards(path: Path) -> Iterator[str]:
    """I messaggi dell'utente in *path*, dal più recente al più vecchio."""
    for raw in _lines_backwards(path):
        raw = raw.strip()
        if not raw or "user" not in raw:
            # Filtro grezzo prima di parsare: un transcript e' fatto in gran
            # parte di delta, e parsarli tutti per buttarli costa.
            #
            # **Soprainsieme, e non quasi-esatto.** Prima cercava ``'"user"'``
            # con le virgolette, che e' quasi la condizione finale — e siccome
            # ``json.dumps`` scappa le virgolette nei testi, nessuna riga di
            # ragionamento realistica poteva passare qui. Risultato: il controllo
            # vero sotto era irraggiungibile, quindi **non provabile** (tre
            # mutazioni di fila sopravvissute prima di capirlo). Un filtro grezzo
            # deve ammettere piu' del necessario e lasciar decidere il controllo
            # vero; il prezzo e' parsare i delta che nominano l'utente, che e' una
            # minoranza.
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if row.get("event") != "user" and row.get("role") != "user":
            continue
        text = row.get("text") or row.get("content")
        if isinstance(text, str) and text.strip():
            yield " ".join(text.split())


def read_recent_user_messages(
    name: str,
    *,
    limit: int = _RECENT_USER_MESSAGES,
    max_chars: int = _MAX_TRANSCRIPT_CHARS,
) -> tuple[list[str], bool]:
    """Gli ultimi messaggi **dell'utente** in quel progetto, e se sono stati tagliati.

    Letti dal codice e messi nel prompt, **non** esposti come file: la cassetta
    del giardiniere resta chiusa sulla cartella del progetto, e il transcript sta
    fuori (``.jenny/webui/``). Allargargli la superficie di lettura per questa
    cosa sola sarebbe pagare in permessi quel che si puo' pagare in prompt.

    Il transcript non e' il registro del modello: la compattazione riscrive
    ``sessions/``, non questo file. E' per questo che serve — e' il solo posto
    dove resta quel che l'utente ha detto davvero.

    **Si legge dal fondo** (``_lines_backwards``), e si smette appena la finestra
    è piena: la coda è tutto quel che serve, il file attivo arriva a 8 MB, e
    prima si parsava da riga uno accumulando ogni messaggio dell'utente della
    storia del progetto per poi tenerne quaranta. Il costo era per passata e per
    progetto.

    **E il taglio dice la verità anche quando il file è ruotato.** Superati gli
    8 MB, ``transcript_store`` sposta i turni vecchi in
    ``<chiave>.segments/NNNNNN.jsonl`` e lascia sul posto solo la coda: la
    finestra risultava allora «intera» — ``truncated=False`` — mentre metà
    conversazione era in un altro file. Un segmento esiste solo perché una
    rotazione è avvenuta, e ogni turno comincia con un messaggio dell'utente
    (``_split_transcript_turns``), quindi la sua presenza *è* la prova che
    esistono messaggi più vecchi.
    """
    from jenny.session.keys import WEBUI_CHANNEL, project_session_key
    from jenny.webui.transcript_store import (
        webui_transcript_path,
        webui_transcript_segments_dir,
    )

    try:
        key = f"{WEBUI_CHANNEL}:{project_session_key(name)}"
        path = webui_transcript_path(key)
        rotated = any(webui_transcript_segments_dir(key).glob("*.jsonl"))
    except Exception:  # noqa: BLE001 — senza transcript il controllo salta, non rompe
        return [], False
    if not path.is_file():
        return [], False

    # Il tetto in caratteri toglie **dalla testa**: i messaggi piu' recenti sono
    # quelli che la cattura puo' aver mancato adesso. Da qui il verso della
    # lettura, che e' anche il verso in cui si spende il budget.
    kept: list[str] = []
    truncated = rotated
    total = 0
    try:
        for message in _user_said_backwards(path):
            if len(kept) >= limit:
                # Un messaggio in piu' del tetto: e' come si sa che ce n'erano
                # altri, senza tenerli.
                truncated = True
                break
            total += len(message)
            if total > max_chars and kept:
                truncated = True
                break
            kept.append(message)
    except OSError as exc:
        logger.warning("gardener: transcript di {} illeggibile: {}", name, exc)
        return [], False

    if truncated:
        # A INFO e nel log di processo: qui si sa **quanto** si è lasciato
        # fuori, e il registro del progetto lo dice solo se quella passata
        # scrive una riga (v. ``GardenerStore.log_pass``).
        logger.info(
            "gardener: cross-check window for {} is truncated "
            "({} messages kept{})",
            name, len(kept), ", transcript rotated" if rotated else "",
        )
    return list(reversed(kept)), truncated


def extract_flag(reply: Any) -> str | None:
    """La riga di segnalazione con cui la passata ha chiuso, o ``None``.

    Il canale nasce da un buco: la risposta della passata serviva al predicato di
    commit e alla contabilita' token, e il **testo** veniva buttato — quindi il
    prompt diceva «se due pagine si contraddicono, dillo» e quel report non
    arrivava a nessuno.

    Adesso la contraddizione ha due destinazioni, per due pubblici diversi: la
    sezione aperta della **mappa**, che il modello scrive da se' e che entra nel
    prompt di ogni turno (quindi raggiunge la conversazione), e una riga nel
    **log**, che e' la storia che una persona rilegge. Qui si estrae la seconda.

    Si cerca il marcatore e nient'altro. Un testo senza marcatore non e' un
    errore — una passata che ha scritto le pagine giuste e si e' scordata la
    formula ha fatto il lavoro — e vale "niente da segnalare".

    **Il marcatore si riconosce vestito** (v. :data:`_FLAG_LINE_RE`): la riga
    puo' arrivare in grassetto, citata o come voce di elenco, che e' la forma
    ordinaria in coda a un prompt fatto di markdown. Restano intatte le due
    proprieta' che questo canale ha: la scansione **dal fondo** e il tetto sulla
    riga.
    """
    text = getattr(reply, "content", None)
    if not isinstance(text, str):
        return None
    # Dal fondo: il marcatore chiude la risposta, e cercandolo dall'inizio si
    # prenderebbe la riga in cui il modello *cita* il contratto ragionando.
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if _NO_FLAG_LINE_RE.match(line):
            return None
        found = _FLAG_LINE_RE.match(line)
        if found:
            return found.group(1).strip()[:_MAX_FLAG_CHARS] or None
    return None


async def _checkpoint(agent: Any) -> None:
    """Checkpoint del workspace prima che la passata scriva. **Fail-open.**

    Il giardiniere è il primo lavoro periodico che scrive dentro le cartelle
    *dell'utente* e non in un file derivato: un file derivato si ricostruisce
    al run dopo, una pagina scritta a mano che venisse sovrascritta non si
    ricostruisce da niente — il diario copre solo quel che dal diario è nato.

    **Fail-open, e non è pigrizia:** il verso opposto («nessuna passata senza
    checkpoint») trasformerebbe uno store di snapshot pieno in un taccuino che
    smette di lavorare in silenzio, che è un guasto peggiore di quello che
    previene. Il checkpoint è una rete, non un permesso.

    E **al modello non si dice niente.** Dream ha un ramo di prompt che promette
    la reversibilità, e serve a fargli potare di più; qui non c'è e non ci va:
    aggiungere-e-promuovere è la regola *anche* con la rete, e prometterla
    sposterebbe il giudizio nella direzione sbagliata.
    """
    hook = getattr(agent, "take_snapshot", None)
    if not callable(hook):
        # Fuori dal gateway (test, ispezione) la rete non c'è. Si dice a DEBUG e
        # si prosegue: che in produzione ci sia lo garantisce il container, e un
        # test sul cablaggio.
        logger.debug("gardener: no snapshot hook, pass runs without a net")
        return
    try:
        # ``cast`` e non un'annotazione su ``hook``: il gancio è duck-typed di
        # proposito — il container lo monta, i test no — e ``callable()`` lo
        # restringe comunque a una funzione che ritorna ``object``, che non si
        # può attendere. Il tipo vero lo conosce solo il chiamante.
        await cast(Any, hook)("pre_gardener")
    except Exception:
        logger.exception("gardener: pre-pass snapshot failed; continuing")


# Le passate in volo, **per nome di progetto**.
#
# La chiave è il nome e non la chiave di sessione, ed è tutto il punto: la chiave
# di sessione di una passata è ``gardener:<nome>-<timestamp>``, cioè diversa a
# ogni giro, quindi il registro dei lock per sessione non serializza *niente* fra
# due passate sullo stesso progetto. Un ``/gardener viaggio`` lanciato durante un
# tick del cron dava due passate concorrenti che riscrivevano lo stesso
# ``wiki/index.md`` da due ``FileStates`` separati, e ``write_file`` sovrascrive
# senza chiedere: l'ultima che salvava cancellava l'altra, e ognuna committava lo
# stesso delta.
#
# Un ``set`` di processo e non un lock, e non è pigrizia: la seconda passata **non
# deve mettersi in coda**, deve essere rifiutata. Mettersi in coda vorrebbe dire
# che ``/gardener`` risponde fra trenta secondi con il lavoro di qualcun altro
# già fatto, e che il tick del cron dopo trova la coda ancora piena.
#
# Non serve un ``asyncio.Lock`` intorno: il controllo e l'inserimento stanno nella
# stessa istruzione sincrona (nessun ``await`` in mezzo) e l'event loop è uno.
_PASSES_IN_FLIGHT: set[str] = set()


def passes_in_flight() -> frozenset[str]:
    """I nomi dei progetti con una passata in corso adesso.

    Una copia, non il set: chi chiede (il rinomino di un quaderno, che non deve
    spostare la cartella sotto una passata che ci sta scrivendo) non deve poter
    togliere o aggiungere voci alla presa.
    """
    return frozenset(_PASSES_IN_FLIGHT)

# La frase che il modello legge quando l'utente è tornato. È un rifiuto di
# scrittura perché è il solo punto che i tre tool condividono prima di toccare il
# file: la passata si chiude dicendogli di fermarsi, e il codice — non lui —
# decide che il cursore non avanza.
_YIELD_REFUSAL = (
    "Refused: the user is back in this project's conversation right now, so this pass "
    "is giving way. Do not write anything else and end your turn — the journal will be "
    "read again by the next pass."
)



def _compose_write_guards(*guards: Any) -> Any:
    """Un gancio dai molti, perche' lo slot e' **uno**.

    ``_FsTool`` ha un solo parametro pre-scrittura (``write_size_guard``), e la
    docstring di ``GardenerStore.build_tools`` dice che montarne un secondo gemello
    e' stato rifiutato di proposito: allargare quella firma vorrebbe dire
    duplicarne la semantica in tutti i tool di scrittura del repo per un bisogno di
    un solo chiamante. Si compone qui.

    **L'ordine conta e non e' alfabetico.** La cessione del passo va per prima:
    quando l'utente e' rientrato, quel rifiuto e' l'unica cosa vera da dire, e
    ``aborted`` va riempito con *quel* motivo. Un rifiuto di provenienza che
    arrivasse prima racconterebbe alla passata una storia diversa da quella per cui
    e' stata fermata — la proprieta' che la docstring di ``_yield_to_user_guard``
    chiama «al primo rifiuto la passata e' decisa».
    """
    active = [g for g in guards if g is not None]
    if not active:
        return None
    if len(active) == 1:
        return active[0]

    def _guard(path: Any, text: str) -> str | None:
        for guard in active:
            refusal = guard(path, text)
            if refusal is not None:
                return refusal
        return None

    return _guard


def _yield_to_user_guard(agent: Any, name: str, aborted: list[str]) -> Any:
    """Il gancio pre-scrittura che cede il passo all'utente, o ``None``.

    ``None`` quando l'agente non sa dire chi è in volo (fuori dal gateway): senza
    quel segnale non c'è niente da controllare, e un gancio che rifiuta sempre o
    mai sarebbe peggio di nessun gancio.

    **Perché prima di *ogni* scrittura e non una volta sola.** Il cancello del
    fermo in ``gardener_schedule.py`` si valuta alla *selezione*, e la sua stessa
    docstring dice che è l'unica cosa che tiene utente e giardiniere lontani dalla
    stessa mappa: un messaggio dell'utente che arriva un secondo dopo l'inizio
    trovava una passata lunga fra i 14 e i 26 secondi e nessuno a fermarla. Qui il
    cancello si richiude a ogni scrittura, che è l'unico momento in cui riaprirlo
    cambia qualcosa.

    *aborted* è la lista in cui si deposita il motivo: al primo rifiuto la passata
    è decisa, e i rifiuti successivi non devono raccontare una storia diversa.
    """
    from jenny.session.keys import project_session_key

    active = getattr(agent, "active_session_keys", None)
    if not callable(active):
        return None
    key = project_session_key(name)

    def _guard(_path: Any, _text: str) -> str | None:
        if aborted:
            return _YIELD_REFUSAL
        try:
            in_flight = key in cast("Collection[str]", active() or ())
        except Exception:  # noqa: BLE001 — un segnale illeggibile non ferma la passata
            logger.warning("gardener: in-flight state is unreadable; the pass continues")
            return None
        if not in_flight:
            return None
        aborted.append(
            f"the user's own turn on {name} started while the pass was writing"
        )
        return _YIELD_REFUSAL

    return _guard


async def run_gardener(
    agent: Any, store: GardenerStore, *, delta: JournalDelta | None = None
) -> GardenerOutcome:
    """Esegue una passata su un progetto e restituisce l'esito.

    Unico punto di ingresso: lo usano lo slash command
    ``/gardener`` e il job cron. Non c'è un ``force``: i tre orologi
    dell'innesco (fermo, distanza, e la scelta di *quale* progetto) stanno nel
    chiamante, e qui resta la sola condizione che è del lavoro e non della
    politica — **se non c'è niente da fare non si parte**.

    E «niente da fare» sono **due** cose, non una. Fino al 23/08/2026 era solo il
    delta vuoto, e la conseguenza si è misurata sul telefono: otto progetti veri,
    otto ``raw/journal/`` vuote, sette mappe su otto oltre il tetto di iniezione, e
    nessuna passata che partisse mai — quindi l'istruzione di potatura di T3.4
    davanti a nessun modello, per sempre. Da T3.5 una mappa oltre il tetto è la
    seconda ragione, e il predicato con il suo freno sta in
    ``GardenerStore.map_needs_pruning``.

    Un ``force`` continua a non servire, e adesso per un motivo più forte: chi
    lancia ``/gardener`` a mano passa da qui, quindi ottiene la passata sulla mappa
    quando è dovuta *senza chiedere niente*; e un ``force`` sarebbe esattamente il
    permesso di rifare una passata che lo stato dice inutile — cioè il livelock,
    esposto come comando.

    **Una passata per progetto alla volta, e il rifiuto sta qui e non nei due
    chiamanti.** I chiamanti sono due proprio perché il runner è uno: duplicare la
    guardia vorrebbe dire due copie da tenere d'accordo, e un terzo chiamante
    domani senza nessuna. Chi arriva secondo si porta indietro
    ``already_running`` — un esito, non un'eccezione, perché è la stessa forma con
    cui gli altri rifiuti tornano a ``/gardener``.

    **Perché un registro per nome e non il lock della sessione del progetto.**
    Girare la passata sotto ``project:<nome>`` la serializzerebbe con l'utente
    *e* con se stessa in un colpo solo, che è più pulito su carta. Ma quel lock lo
    prende ``AgentLoop`` per la durata **intera** di un turno: un messaggio
    dell'utente arrivato a passata iniziata resterebbe fermo fino a 26 secondi
    prima di ricevere una risposta, e senza dire perché. Il verso giusto è
    l'opposto — quando i due si incontrano è il giardiniere che si sposta
    (v. ``_yield_to_user_guard``), non l'utente che aspetta.

    **Il delta si può portare già letto** (T2.5), e chi ce l'ha è il cron: la
    selezione (``gardener_schedule.pick_project``) apre i diari per decidere, e
    fino al 23/08/2026 la passata li riapriva da zero un istante dopo.
    ``read_journal_delta`` fa un ``read_text`` **intero** di ogni
    ``raw/journal/*.md`` prima di guardare il cursore — anche sui giorni già
    letti fino in fondo — quindi un progetto con un anno di diario quotidiano si
    faceva leggere due volte per passata, e una volta per tick per ogni progetto
    idoneo.

    Passarlo non è solo un risparmio: **il prompt e il commit parlano dello stesso
    delta**. Con due letture la seconda poteva vedere righe arrivate nel
    frattempo, e il cursore committato copriva righe che il modello non ha mai
    visto. Un delta è ``frozen`` e porta dentro di sé il cursore che produce
    (``JournalDelta.cursor``), quindi committarne uno leggermente vecchio registra
    esattamente quel che è stato mostrato — le righe nuove restano non lette, che
    è il verso giusto.

    ``None`` vuol dire «leggilo tu», ed è la strada di ``/gardener``: non c'è
    nessuna selezione davanti, quindi la sola lettura è questa. La lettura sta
    **dentro** la guardia di ``_PASSES_IN_FLIGHT``, come prima: rifiutare una
    passata concorrente non deve costare l'apertura dei diari.
    """
    if store.name in _PASSES_IN_FLIGHT:
        logger.warning(
            "gardener: a pass over {} is already in flight; this one does not start", store.name
        )
        return GardenerOutcome(status="already_running")
    _PASSES_IN_FLIGHT.add(store.name)
    try:
        return await _run_pass(agent, store, delta)
    finally:
        # **Su ogni uscita, eccezioni comprese.** È la stessa forma del
        # ``try``/``finally`` che garantisce ``_prune_sessions`` qui sotto, e per
        # la stessa ragione: una voce che resta vuol dire che quel progetto non è
        # più giardinabile fino al riavvio del processo, cioè un guasto
        # silenzioso e permanente. Sta *fuori* da quel ``finally`` e non dentro
        # perché la presa va fatta prima del delta e dello snapshot: rifiutare una
        # passata concorrente non deve costare una lettura dei diari e una
        # scansione del workspace.
        _PASSES_IN_FLIGHT.discard(store.name)


async def _run_pass(
    agent: Any, store: GardenerStore, delta: JournalDelta | None = None
) -> GardenerOutcome:
    """La passata vera, con la presa su ``_PASSES_IN_FLIGHT`` già ottenuta."""

    # ``None`` solo da ``/gardener``: v. l'argomento in ``run_gardener``.
    if delta is None:
        delta = store.read_delta()
    # **Due ragioni per girare, e la seconda non è nel diario.** A delta vuoto la
    # passata parte comunque se la mappa è oltre il tetto e più grossa di come
    # l'ultima l'ha lasciata: il predicato sta in ``map_needs_pruning``, insieme
    # all'argomento del perché non è un livelock, e sta lì e non qui perché la
    # selezione del cron deve chiedere **la stessa cosa** (due copie sarebbero due
    # politiche, e un tick che scegliesse un progetto che la passata poi rifiuta).
    map_pass = delta.is_empty
    if map_pass and not store.map_needs_pruning():
        logger.debug("gardener: nothing to read in {}, and the map fits its budget", store.name)
        return GardenerOutcome(status="skipped_no_delta")

    # Dopo il cancello del delta e prima di qualunque scrittura: una passata che
    # non parte non ha niente da proteggere, e uno snapshot per tick a vuoto
    # sarebbe una scansione del workspace ogni mezz'ora per niente.
    await _checkpoint(agent)

    t0 = time.monotonic()
    resp = None
    # La misura della mappa **prima**. Si prende qui e non dentro ``log_pass``
    # perché lì la passata è già finita: il «dopo» si legge sempre, il «prima»
    # solo adesso.
    map_before = store.map_chars()
    # Il «dopo», dichiarato qui perché ``_stamped`` lo legge: ``None`` vuol dire
    # «il modello non ha risposto», che è il solo ramo (``failed``) in cui la
    # misura non va registrata — il provider è caduto, l'ordine di potare non l'ha
    # visto nessuno, e disarmare l'innesco lì vorrebbe dire perdere la potatura per
    # un guasto di rete.
    map_after: int | None = None
    # Il motivo dell'abbandono, se l'utente torna mentre la passata scrive. Una
    # lista e non un booleano perché il motivo va raccontato all'utente, e la
    # scrive il gancio dentro i tool: è l'unico modo che ha di parlare a questa
    # funzione, che nel frattempo è dentro ``process_direct``.
    aborted: list[str] = []
    tools = store.build_tools(
        write_guard=_compose_write_guards(
            # Ordine: v. ``_compose_write_guards``. La cessione del passo prima.
            _yield_to_user_guard(agent, store.name, aborted),
            _provenance_guard(store.root.resolve(), (store.root / "wiki").resolve()),
        )
    )
    # **Una volta sola, e messa da parte**: ``store.session_key()`` legge
    # l'orologio a ogni chiamata, quindi due chiamate nella stessa passata danno
    # due chiavi diverse — e la seconda non è la chiave sotto cui il turno è
    # girato. La ripulitura qui sotto ne ha bisogno di quella vera (T2.5).
    session_key = store.session_key()
    # **Da qui in avanti la passata ha lasciato una traccia da ripulire**, e la
    # potatura va su *ogni* uscita — da cui il ``try``/``finally`` che avvolge
    # tutta la coda invece del solo tratto felice. La chiave di sessione porta il
    # timestamp, quindi ogni giro ne crea una nuova: un ramo che torna prima di
    # ``_prune_sessions`` lascia per sempre un ``gardener_<nome>-<ora>.jsonl`` e
    # una voce in ``AgentLoop._session_locks``. Era il caso di ``failed``, cioè
    # esattamente del ramo che su un provider giù si prende ogni mezz'ora.
    def _stamped(outcome: GardenerOutcome) -> GardenerOutcome:
        """Timbra il tentativo e attacca all'esito la lunghezza della serie.

        **Sta qui e non nel chiamante** perché i chiamanti sono due — il job cron
        e ``/gardener`` — e una passata a mano che fallisce costa quanto una
        automatica: lo stesso prompt, la stessa istantanea del workspace. Contarla
        solo nel cron voleva dire che tre tentativi a mano su un progetto rotto
        non muovevano il conto, e il quarto automatico ripartiva da uno.

        Il timbro serve a chi misura la *distanza* fra le passate: il cursore lo
        tengono fermo di proposito ``partial_write``, ``commit_failed`` e
        ``aborted_user_active`` (ci sono righe che devono tornare), ma finché la
        distanza si leggeva sul cursore «tenere il cursore» diventava «rifare la
        passata ogni mezz'ora». Vale **anche** per la passata ceduta all'utente, ed
        è il verso giusto: un progetto su cui si sta lavorando adesso è l'ultimo su
        cui riprovare fra mezz'ora, e il turno LLM speso l'ha speso comunque.

        Non timbra chi non ha chiamato il provider — ``skipped_no_delta`` è
        l'esito *normale* di un tick, e spostargli la distanza in avanti
        rimanderebbe una passata che non è mai partita. Non timbra chi ha
        committato: quello l'ha già fatto ``GardenerState.advanced``, insieme al
        cursore e all'azzeramento della serie.

        **E porta con sé la misura della mappa**, che è l'altro freno e non lo
        stesso: il timbro ritarda di ``min_hours_between_passes``, la misura
        disarma. Su una passata girata per la mappa e finita senza potare
        (``no_write``) è la misura a impedire che la stessa passata torni alla
        distanza dopo, identica, per sempre — v. ``GardenerState.map_left_at``.
        """
        if not outcome.ran or outcome.status in COMMITTED_STATUSES:
            return outcome
        return replace(
            outcome, failures=record_attempt(store.root, map_chars=map_after)
        )

    try:
        try:
            resp = await agent.process_direct(
                store.build_prompt(delta),
                session_key=session_key,
                ephemeral=True,
                tools=tools,
                on_progress=_silent,
            )
        except Exception as exc:  # noqa: BLE001 — l'esito viaggia nell'outcome
            logger.exception("gardener: pass over {} failed", store.name)
            return _stamped(GardenerOutcome(
                status="failed",
                elapsed=time.monotonic() - t0,
                lines=delta.line_count,
                detail=str(exc),
                map_pass=map_pass,
                map_before=map_before,
            ))
        finally:
            # La contabilita' dei token **non passa da qui**: la fa
            # ``TokenUsageHook.after_iteration`` sul turno, che e' l'unico punto in
            # cui l'``usage`` del provider esiste. Qui c'era una
            # ``record_response_token_usage(resp, source="gardener")``: ``resp`` e'
            # un ``OutboundMessage``, che ``usage`` non ce l'ha — e per una passata
            # e' anche ``None``, perche' non c'e' niente da consegnare. Non ha mai
            # contato un token. La passata finisce nel bucket ``gardener`` per la
            # mappa in ``token_usage._INTERNAL_KIND_TO_SOURCE``, dalla sua chiave.
            pass

        elapsed = time.monotonic() - t0
        # Il fuso in cui il registro va datato, letto **una volta** qui: è il solo
        # posto della passata che vede l'agente, e le righe di ``log_pass`` sotto
        # sono tre (v. la docstring di ``log_pass``).
        tz_name = _timezone_of(agent)
        file_states = getattr(tools, "file_states", None)
        writes = int(getattr(file_states, "writes_ok", 0) or 0)
        attempted = int(getattr(file_states, "writes_attempted", 0) or 0)
        # Le scritture che la passata ha voluto e che non sono atterrate: i blocchi
        # di policy (un percorso fuori da ``wiki/``) e gli errori di I/O —
        # ``record_write_attempt`` li conta, e ``record_write`` non li chiude. Nella
        # cassetta del giardiniere non c'è il budget dei file di memoria, quindi il
        # solo gancio pre-scrittura montato è quello che cede il passo all'utente:
        # i suoi rifiuti finiscono anche qui dentro, ed è il ramo ``aborted`` — che
        # viene prima — a raccontarli per quel che sono.
        refused = max(attempted - writes, 0)
        map_after = store.map_chars()
        detail = ""

        if aborted:
            # **L'utente è tornato mentre la passata scriveva, e vince lui.**
            #
            # Il ramo sta **prima** di tutti gli altri di proposito: dall'esterno
            # una passata ceduta somiglia a ``partial_write`` (qualcosa è
            # atterrato, qualcosa è stato rifiutato) e si racconterebbe come una
            # riuscita a metà, che è vero solo del disco e falso di tutto il
            # resto — il motivo non è un rifiuto della cassetta, è che questa
            # passata non doveva continuare.
            #
            # Il cursore resta dove è: le righe dietro la scrittura ceduta non
            # sono diventate pagine, e il diario è append-only. Il prezzo è una
            # ripromozione, che l'inventario nel prompt e la regola
            # «aggiungi e promuovi» rendono idempotente.
            detail = aborted[0]
            status = "aborted_user_active"
            if writes:
                # Le pagine già atterrate sono su disco, e il log è l'unico
                # registro che c'è: la riga si scrive, con il conto dei rifiuti,
                # perché «cursore fermo con pagine nuove» è lo stato che il giro
                # dopo va spiegato.
                store.log_pass(
                    delta,
                    elapsed=elapsed,
                    writes=writes,
                    timezone=tz_name,
                    refused=refused,
                    map_before=map_before,
                    map_after=map_after,
                )
            logger.warning(
                "gardener: {} yielded to the user after {} writes; cursor "
                "held after {:.1f}s — {}",
                store.name, writes, elapsed, detail,
            )
        elif internal_run_completed(resp) and writes and refused:
            # **Passata riuscita a metà: il cursore non avanza.**
            #
            # ``internal_run_should_commit`` qui dice sì — ha visto ``writes_ok > 0``
            # e nessun rifiuto di budget aperto — ma "qualcosa è atterrato" non è
            # "tutto è atterrato". Il diario è append-only e nessuno lo rilegge: le
            # righe dietro la scrittura rifiutata, passato il cursore, non tornano in
            # nessuna passata. Meglio ripromuovere che perdere, e il prezzo della
            # ripromozione è già pagato — l'inventario delle pagine sta nel prompt e
            # la regola è «aggiungi e promuovi, non riscrivere», che è quel che rende
            # una passata ripetuta idempotente invece che distruttiva.
            #
            # **La condizione sta qui e non dentro il predicato** per la stessa
            # ragione per cui ci sta quella di Dream in ``runtime/cron_dispatch.py``:
            # ``internal_run_should_commit`` è condiviso con Dream, la cui
            # semantica non cambia, e un parametro con un default lascerebbe una
            # funzione con due contratti e il default sbagliato a portata del
            # prossimo chiamante. In più il confronto serve comunque qui, per la riga
            # di log: calcolarlo in due posti sarebbe il modo di farli divergere.
            flag = extract_flag(resp)
            if flag:
                logger.warning("gardener: {} segnala — {}", store.name, flag)
            store.log_pass(
                delta,
                elapsed=elapsed,
                writes=writes,
                timezone=tz_name,
                flag=flag,
                refused=refused,
                map_before=map_before,
                map_after=map_after,
            )
            status = "partial_write"
            detail = (
                f"{writes} of {attempted} writes landed; {refused} refused, so the journal "
                "was left unread and the next pass will see those lines again"
            )
            logger.warning(
                "gardener: {} — {} lines, {} writes to {} ({} refused); cursor held "
                "after {:.1f}s",
                store.name, delta.line_count, writes, attempted, refused, elapsed,
            )
        elif internal_run_should_commit(resp, file_states):
            # Il cursore avanza anche a zero scritture: «niente da promuovere» è un
            # esito, e riproporre le stesse righe al giro dopo darebbe la stessa
            # risposta a un costo nuovo.
            #
            # **Il commit sta dentro la guardia**, e non fuori: è l'unica riga di
            # questa coda che scrive su disco per conto proprio, quindi l'unica che
            # può alzare ``OSError`` (disco pieno, dir dati non scrivibile). Fuori,
            # quell'errore usciva da ``run_gardener`` intero — nessun log, nessuna
            # riga di registro, nessuna potatura, e un'eccezione al posto di un
            # esito per i due chiamanti — *dopo* che le pagine erano già scritte.
            try:
                store.commit(delta, map_chars=map_after)
            except OSError as exc:
                # Il lavoro c'è e la sua registrazione no: si dice esattamente
                # questo. A ERROR e non a WARNING perché non è un esito previsto
                # come «bloccato»: è il disco che non prende quel che gli si dà, e
                # il giro dopo ripromuoverà le stesse righe finché dura.
                logger.error(
                    "gardener: {} wrote {} pages but the cursor was not "
                    "recorded: {}",
                    store.name, writes, exc,
                )
                return _stamped(GardenerOutcome(
                    status="commit_failed",
                    elapsed=elapsed,
                    lines=delta.line_count,
                    writes=writes,
                    detail=str(exc),
                    map_pass=map_pass,
                    map_before=map_before,
                    map_after=map_after,
                ))
            flag = extract_flag(resp)
            if flag:
                # A WARNING: e' la sola cosa che una passata puo' dire e che vale la
                # pena vedere passando dai log, senza aprire il file.
                logger.warning("gardener: {} segnala — {}", store.name, flag)
            # Il log si scrive se qualcosa e' stato scritto, se c'e' una
            # segnalazione, **oppure se la passata ha letto righe di diario** —
            # anche senza promuovere niente. Quel terzo caso e' il cursore che
            # avanza su righe che nessun giro rivedra': v. ``log_pass``.
            if writes or flag or delta.line_count:
                store.log_pass(
                    delta,
                    elapsed=elapsed,
                    writes=writes,
                    timezone=tz_name,
                    flag=flag,
                    map_before=map_before,
                    map_after=map_after,
                )
            status = "written" if writes else "nothing_to_promote"
            logger.info(
                "gardener: {} — {} lines, {} writes, {} in {:.1f}s",
                store.name, delta.line_count, writes, status, elapsed,
            )
        elif internal_run_completed(resp):
            # Completata ma con le scritture bloccate o rifiutate: il cursore **non**
            # avanza, altrimenti quelle righe risulterebbero digerite da una passata
            # che non ha prodotto niente, e nessun giro successivo le rivedrebbe.
            logger.warning("gardener: {} finished without writing; cursor held", store.name)
            status = "no_write"
        else:
            logger.warning("gardener: {} did not finish; cursor held", store.name)
            status = "incomplete"

        return _stamped(GardenerOutcome(
            status=status,
            elapsed=elapsed,
            lines=delta.line_count,
            writes=writes,
            detail=detail,
            map_pass=map_pass,
            map_before=map_before,
            map_after=map_after,
        ))
    finally:
        _prune_sessions(agent, session_key)


def _timezone_of(agent: Any) -> str | None:
    context = getattr(agent, "context", None)
    return getattr(context, "timezone", None)


def _prune_sessions(agent: Any, session_key: str | None = None) -> None:
    """Ripulisce quel che la passata ha lasciato dietro: i file e la memoria.

    **La chiave della passata si dimentica subito** (T2.5), e non fra dieci
    passate. ``prune_internal_sessions`` tiene i dieci ``gardener_*.jsonl`` più
    recenti e ``evict_pruned_sessions`` sgombera per le chiavi *potate* il lock,
    i task e la cache delle sessioni — quindi ``_session_locks`` è limitato per
    costruzione, non cresce all'infinito. Ma quella strada non passa da
    ``FileStateStore``, e lì ``AgentLoop`` una voce per chiave di sessione la crea
    **sempre**, a ogni turno: la chiave del giardiniere porta il timestamp, quindi
    era una voce morta per passata per la vita del processo. Sono byte e non
    megabyte — la cassetta del giardiniere porta un ``FileStates`` suo, quindi
    quella voce non viene nemmeno usata — ma è illimitata per costruzione su un
    processo pensato per stare su settimane.

    Si passa da ``forget_file_reads``, che è il nome pubblico di quel gesto e dice
    la cosa vera: chiusa la passata, quella sessione non contiene più il contenuto
    di nessun file, perché quella sessione non c'è più.

    **La chiave stabile era l'altra strada, e non regge.** ``gardener:<nome>``
    senza timestamp avrebbe reso finito lo spazio delle chiavi da sé, e la mutua
    esclusione non ne ha bisogno (``_PASSES_IN_FLIGHT`` è per *nome di progetto*,
    non per chiave). Ma un turno effimero salva comunque la sua sessione — la
    replica della cronologia in ``turn_states._state_build`` **non** guarda
    ``ephemeral`` — quindi con una chiave stabile ogni passata si ritroverebbe
    davanti il prompt e la risposta di quella prima, per sempre: su una passata
    misurata a ~19.000 token di richiesta è una crescita senza fine, e con dieci
    progetti o meno ``prune_internal_sessions`` (che tiene 10) non toccherebbe mai
    quei file. Non generalizza nemmeno alle chiavi dei subagent, che *devono*
    tenere la loro storia per essere riprese e hanno una potatura loro
    (``agent/subagent_history.py``).
    """
    sessions = getattr(agent, "sessions", None)
    sessions_dir = getattr(sessions, "sessions_dir", None)
    if session_key and hasattr(agent, "forget_file_reads"):
        agent.forget_file_reads(session_key)
    if sessions_dir is None:
        return
    pruned = prune_internal_sessions(sessions_dir, "gardener")
    if pruned and hasattr(agent, "evict_pruned_sessions"):
        agent.evict_pruned_sessions(pruned)
