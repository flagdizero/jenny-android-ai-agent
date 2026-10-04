"""``journal_append`` — la cattura di un progetto, e l'unica scrittura che
l'agente principale sa fare da sé.

Passo **T2.5** del piano del taccuino, e nasce da una misura, non da un
disegno. Il 22/08 la politica di cattura ha funzionato al primo colpo — fatto
stabile detto, riga nel diario, stesso turno — ma **passando da uno spawn**:
``orchestrator_mode`` è acceso di default e toglie all'agente principale
``python_exec``, la scrittura e le patch, quindi l'unica strada per appendere una
riga era delegare a un subagent. Una corsa di subagent intera per una riga di
testo, a ogni turno che contiene un fatto: l'opposto esatto della premessa su cui
poggia il passo («costa poco per costruzione, o non succede»).

Da cui un tool **stretto**, nello scope dell'orchestratore. Stretto è la
caratteristica, non un limite:

- **Un solo file possibile**, e non è un argomento: la pagina di oggi del diario
  del progetto del turno. Niente path da sbagliare, niente traversal da validare,
  niente modo di scrivere altrove.
- **Solo in coda.** Non c'è modo di riscrivere una riga esistente, ed è il
  contratto append-only del diario reso vero dal codice invece che promesso da un
  prompt. Il giardiniere (T4) legge quel file assumendolo immobile.
- **Solo dentro un progetto.** Fuori, la cattura non ha una cartella dove
  andare, e il rifiuto lo dice invece di inventarne una — la lezione del passo 6.
- **Niente in sola lettura**, come ogni altra scrittura (passo 4).

Non sostituisce la scrittura vera: creare pagine, riordinare, curare la mappa
restano lavoro da subagent, e devono restarlo — sono le decisioni che il passo
T4 vuole prese a mente fredda. Questo tool fa una cosa sola, ed è la cosa che
capita a ogni turno.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import StringSchema, tool_parameters_schema
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    current_turn_is_readonly,
    current_workspace_scope,
)
from jenny.utils.helpers import safe_zoneinfo
from jenny.utils.wiki_paths import is_wiki_root, journal_page_name, wiki_journal_dir

# Non c'e' un progetto: il rifiuto dice **dove** vive il diario invece di dire
# solo che qui non c'e'. E' la parte che Jenny ridice all'utente, e la
# differenza fra "aprine uno" e un "non posso" che manda via a mani vuote.
_NO_PROJECT_REFUSAL = (
    "No journal here: the working journal belongs to a project, and this conversation is not "
    "one. Nothing was written. If this is worth keeping, it belongs either in the personal "
    "memory (say so and it goes to MEMORY.md) or in a project — the chip above the composer "
    "opens one. Do not look for another file to append to."
)

# Il tetto di una riga. Una riga di diario e' un fatto, non un paragrafo: quel
# che non ci sta e' materiale da pagina, e il posto di una pagina non lo decide
# la cattura (v. la docstring del modulo).
_MAX_TEXT_CHARS = 500

# ── Attribuzione: di chi e' il fatto che la riga registra ────────────────────
#
# Il difetto che questi due token chiudono (**D1**): il 24/08 Jenny ha chiesto
# «il telescopio te lo porti, *o quello resta a casa*?», l'utente ha risposto «il
# telescopio che cenrtra?» — una domanda, nessuna scelta — e la cattura ha
# registrato «Il telescopio non c'entra col viaggio — **resta a casa**». Cioe'
# l'opzione B della domanda di Jenny, scritta come decisione dell'utente. Poi il
# giardiniere l'ha promossa a ``state: decided`` e la mappa l'ha messa sotto
# «Decided», dove entra a ogni turno. Nessuno dei due messaggi dell'utente
# contiene la parola «casa».
#
# **Perche' un bit dichiarato e non una verifica.** Tre varianti di «controlla la
# citazione contro le parole dell'utente» sono state provate su quel caso e
# cadono tutte, l'ultima in modo istruttivo: chiedere che le parole di contenuto
# della pagina compaiano nei messaggi dell'utente boccia la fabbricazione **e**
# boccia ``fibra.md``, che registra una decisione vera e detta chiaramente, solo
# parafrasata. La parafrasi e' legittima e pervasiva, quindi nessun controllo a
# livello di stringa separa una parafrasi onesta da una fabbricazione: chi vuole
# "rafforzare" questo con un confronto di stringhe boccerebbe proprio le
# parafrasi oneste.
#
# **Perche' qui e non a valle.** La cattura e' l'unico momento in cui chi giudica
# ha davanti la **propria domanda** e la risposta dell'utente insieme. A valle il
# giardiniere vede solo la riga, dove le due cose sono tipograficamente
# identiche: la regola «only the user's own words can justify anything stronger
# than ``open``» non era rispettabile per costruzione.
SAID_MARKER = "[said]"
INFERRED_MARKER = "[inferred]"
# ``[recovered]`` (``gardener.py``) vale **come detto**: la passata recupera solo
# fatti che l'utente ha detto e che la cattura ha perso — e' il contratto del suo
# prompt, non una scelta che le si concede, quindi la sua cassetta non riceve
# questo parametro affatto.
_ATTRIBUTIONS = {"said": SAID_MARKER, "inferred": INFERRED_MARKER}

# **Il default e' il lato conservativo, non il comodo.** Un'attribuzione mancante
# diventa ``inferred``, cioe' una riga che non potra' giustificare niente di piu'
# forte di ``open``. Il verso opposto — dedurre "detto" dal silenzio — e' il modo
# in cui un'omissione diventa una certificazione falsa, che e' esattamente D1. E
# la cattura non fallisce mai per questo: un fatto perduto costa piu' di un fatto
# sottostimato, e il valore ripiegato viene **detto nella risposta** al modello,
# non applicato di nascosto.
_DEFAULT_ATTRIBUTION = "inferred"

_PARAMETERS = tool_parameters_schema(
    text=StringSchema(
        "The fact to record, as one short line and in the user's own terms — what will still "
        "be true next week, not what was said. No leading '- ', no timestamp: both are added. "
        f"At most {_MAX_TEXT_CHARS} characters; anything longer belongs on a page, not here."
    ),
    attribution=StringSchema(
        "Whose fact this is. 'said' when the user stated it themselves; 'inferred' when you "
        "concluded it, including when you offered the options and they only rejected one — an "
        "answer to your own question is not their statement. Only a 'said' line can ever "
        "become a decided page, so 'inferred' costs nothing but a wrong 'said' certifies "
        "something the user never chose. Defaults to 'inferred' when omitted.",
        enum=["said", "inferred"],
    ),
    required=["text"],
)


@tool_parameters(_PARAMETERS)
class JournalAppendTool(Tool):
    """Append one line to the current project's journal page for today."""

    # Nello scope dell'orchestratore **di proposito**: e' l'unica scrittura che
    # l'agente principale deve poter fare senza delegare, e la ragione per cui
    # esiste. Anche nel core e nel subagent, cosi' un turno con i tool pieni non
    # si ritrova a fare a mano quel che qui e' una chiamata.
    _scopes = {"core", "orchestrator", "subagent"}

    def __init__(
        self,
        today: Any = None,
        root: Path | None = None,
        origin_marker: str = "",
        timezone: str | None = None,
        now: Any = None,
    ) -> None:
        # Iniettabile per i test; in produzione nessuno lo passa. Resta la
        # sola **data**: l'istante lo dà ``_stamp`` (v. ``_append``), e chi
        # inietta questo inietta il giorno di quell'istante.
        self._today = today
        # Il fuso in cui il modello legge l'ora (``context.py`` gli mette in
        # testa ``current_time_str(config.agents.defaults.timezone)``). Senza,
        # la riga di diario portava l'ora **di sistema**: su un telefono in
        # viaggio le due differiscono di ore, e il diario è il documento in cui
        # quell'ora diventa un fatto storico.
        self._timezone = timezone
        # Un solo colpo d'orologio, iniettabile per i test: il giorno della
        # pagina e l'ora della riga devono venire dallo **stesso** istante.
        self._now = now
        # **Il progetto iniettato invece di dedotto.** Su un turno dell'utente il
        # progetto si ricava dallo scope legato, ed e' giusto: la cartella non
        # deve poter divergere dalla conversazione. Ma una **passata interna**
        # gira con lo scope di default (l'installazione), quindi la deduzione
        # darebbe "nessun progetto" e il tool rifiuterebbe. Chi costruisce la
        # cassetta di una passata sa su quale progetto sta lavorando, e lo passa.
        self._root = root
        # Marcatore d'origine, per una riga che non arriva dalla conversazione ma
        # da un recupero a posteriori. Sta nel **codice** e non nel prompt perche'
        # e' l'unico modo che ha di non essere dimenticato, e una riga di diario
        # senza origine e' indistinguibile da una detta a voce quel giorno.
        self._origin_marker = origin_marker

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(timezone=getattr(ctx, "timezone", None))

    @property
    def name(self) -> str:
        return "journal_append"

    @property
    def description(self) -> str:
        return (
            "Append one line to this project's working journal (raw/journal/<today>.md). "
            "Use it the moment the user says something that will still be true next week — a "
            "constraint, a decision, a preference, a name, a date — before answering them. "
            "Append-only by construction: it can neither rewrite a line nor touch any other "
            "file, so it is always safe to call. Only inside a project."
        )

    async def execute(self, text: str = "", attribution: str = "", **_: Any) -> str:
        line = " ".join((text or "").split())
        if not line:
            return "Nothing to append: `text` was empty."
        # **Il tetto si misura su ``text``, e il marcatore si mette dopo.**
        # Prefissato prima, ``[recovered] `` (dodici caratteri che il modello non
        # scrive e non vede) restringeva l'allowance a 488: una riga da 495
        # veniva rifiutata citando un limite di 500 che il chiamante non aveva
        # sforato, cioè un rifiuto su cui non si può agire. Il limite è del
        # **fatto**; l'origine è annotazione del codice e non se ne paga il conto.
        if len(line) > _MAX_TEXT_CHARS:
            return (
                f"Too long for a journal line ({len(line)} characters, max {_MAX_TEXT_CHARS}). "
                "A journal line is one fact; this is page material."
            )
        # **Un solo marcatore per riga, e chi ce l'ha fisso vince.** La cassetta
        # di una passata monta ``origin_marker="[recovered]"``, che vale *come
        # detto* per contratto del suo prompt: chiederle anche l'attribuzione
        # sarebbe offrirle una scelta che non ha, e due marcatori in fila sarebbero
        # ventidue caratteri di prefisso su una riga che ne ha cinquecento in
        # tutto. Il parametro resta nello schema per tutti — lo schema e' del tool,
        # non dell'istanza — e qui viene semplicemente ignorato: e' l'unico posto in
        # cui la differenza esiste.
        fallback = ""
        if self._origin_marker:
            line = f"{self._origin_marker} {line}"
        else:
            choice = (attribution or "").strip().lower()
            if choice not in _ATTRIBUTIONS:
                # Non un rifiuto: v. ``_DEFAULT_ATTRIBUTION``. Ma detto, perche' un
                # ripiegamento silenzioso e' come si perde un fatto davvero detto.
                fallback = choice or "(omitted)"
                choice = _DEFAULT_ATTRIBUTION
            line = f"{_ATTRIBUTIONS[choice]} {line}"

        # L'ordine dei due gate non e' indifferente. Prima "non c'e' un
        # progetto", che e' vero indipendentemente dal modo del turno: dire
        # "sola lettura" a chi non ha nemmeno una cartella dove scrivere lo
        # mette a cercare l'interruttore per un problema che l'interruttore non
        # risolve.
        scope = current_workspace_scope()
        # ``write_root()`` e non ``project_path``: dal passo T4.4 la radice
        # scrivibile del turno la dice un solo metodo, anche a chi — come questo
        # tool — ha una destinazione fissa e non un percorso da validare.
        root = self._root or (scope.write_root() if scope is not None else None)
        if root is None or not is_wiki_root(root):
            return _NO_PROJECT_REFUSAL
        if current_turn_is_readonly():
            return READONLY_TOOL_REFUSAL

        written = await self._append(root, line)
        if fallback:
            return (
                f"{written}\n\nNote: attribution {fallback!r} is not one of 'said' / "
                f"'inferred', so this line was recorded as '{_DEFAULT_ATTRIBUTION}' — it "
                "cannot later become a decided page. If the user stated this themselves, "
                "append it again with attribution='said'; the journal is append-only, so "
                "the line above stays either way."
            )
        return written

    def _stamp(self) -> datetime:
        """L'istante della riga, nel fuso in cui il modello legge l'ora.

        ``datetime.now()`` nudo era sbagliato due volte. La prima: **due letture
        dell'orologio**, una per il giorno della pagina e una per l'ora della
        riga, quindi un turno a cavallo della mezzanotte scriveva ``- 00:00 —``
        in fondo alla pagina di *ieri* — una riga datata a un giorno in cui non è
        stata detta, in un file append-only che nessuno rilegge. La seconda: il
        fuso di **sistema**, mentre l'ora che il modello ha in testa è quella
        configurata (``context.py``, ``current_time_str``); su un device fuori
        dal proprio fuso il fatto entrava nel diario con un'ora che non è quella
        in cui è stato detto.

        ``safe_zoneinfo`` e non ``ZoneInfo``: su Android il database tzdata può
        mancare, e una cattura che solleva è un fatto perduto.
        """
        if self._now is not None:
            return self._now()
        tz = safe_zoneinfo(self._timezone) if self._timezone else None
        return datetime.now(tz=tz) if tz else datetime.now().astimezone()

    async def _append(self, root: Path, line: str) -> str:
        # Un colpo d'orologio, due usi: la pagina e l'ora della riga.
        stamp = self._stamp()
        day = self._today() if self._today is not None else stamp.date()
        page = wiki_journal_dir(root) / journal_page_name(day)
        entry = f"- {stamp.strftime('%H:%M')} — {line}\n"
        try:
            append_journal_entry(page, day, entry)
        except OSError as exc:
            logger.warning("journal_append on {} failed: {}", page, exc)
            return f"Could not append to the journal: {exc}"

        logger.info("journal_append: {} <- {}", page.name, line[:60])
        return f"Appended to {page.parent.name}/{page.name}: {entry.strip()}"


def append_journal_entry(page: Path, day: date, entry: str) -> int:
    """Appende *entry* (una riga già formata) alla pagina di diario *page*.

    Restituisce il numero di righe fisiche del file dopo l'aggiunta, cioè il
    numero della riga appena scritta contato come lo conta il cursore del
    giardiniere: chi scrive una riga per conto dell'utente (una nota della
    bacheca) ci lega «quando questa riga è stata letta».

    Append in ``a``, e non "leggi-modifica-riscrivi": il contratto append-only
    vale anche contro se stessi, e una riscrittura potrebbe perdere quel che un
    altro scrittore ha appeso nel frattempo.
    """
    page.parent.mkdir(parents=True, exist_ok=True)
    fresh = not page.exists()
    with page.open("a", encoding="utf-8") as fh:
        if fresh:
            fh.write(f"# {day.isoformat()}\n\n")
        fh.write(entry)
    with page.open(encoding="utf-8") as fh:
        return sum(1 for _ in fh)


TOOLS = [JournalAppendTool]
