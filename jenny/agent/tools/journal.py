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

import asyncio
from contextlib import suppress
from datetime import date, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import current_request_context
from jenny.agent.tools.schema import StringSchema, tool_parameters_schema
from jenny.security.subagent_policy import current_subagent_policy, current_turn_taint
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    current_turn_is_readonly,
    current_workspace_scope,
)
from jenny.session.keys import PROJECT_SESSION_PREFIX, UNIFIED_SESSION_KEY
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

# La copia nel diario e' un indice, non la riga: la fonte resta nel journal, e
# la chat personale deve poterne vedere tante senza pagarle per intero.
_MIRROR_MAX_CHARS = 300

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
    notebook=StringSchema(
        "From the personal chat: the notebook this fact belongs to (its name, as in the Wikis "
        "list). The line goes into that notebook's journal. Leave it empty for something about "
        "the user themselves or that belongs to no notebook: it is kept in your own diary. "
        "Inside a notebook, leave it empty (only this notebook's journal can be written)."
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
        memory_store: Any = None,
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
        # Il diario della memoria (``history.jsonl``), dove ogni riga detta qui
        # lascia la sua copia breve: v. :meth:`_mirror`.
        self._memory = memory_store

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(timezone=getattr(ctx, "timezone", None),
                   memory_store=getattr(ctx, "memory_store", None))

    @property
    def name(self) -> str:
        return "journal_append"

    @property
    def description(self) -> str:
        return (
            "Append one line to a notebook's working journal (raw/journal/<today>.md) — this "
            "notebook's, or from the personal chat the one named in `notebook` — or, from the "
            "personal chat with no notebook, to your own diary. Use it the moment the user says "
            "something that will still be true next week — a constraint, a decision, a "
            "preference, a name, a date, something done — before answering them; and, from the "
            "personal chat, the answer when the user asks you to find something out about a "
            "notebook's subject, even one you knew. Append-only by "
            "construction: it can neither rewrite a line nor touch any other file, so it is "
            "always safe to call. It never creates a notebook."
        )

    async def execute(self, text: str = "", attribution: str = "", notebook: str = "",
                      **_: Any) -> str:
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
        own = self._root or (scope.write_root() if scope is not None else None)
        own = own if own is not None and is_wiki_root(own) else None
        target, refusal = self._target(own, (notebook or "").strip())
        if refusal:
            return refusal
        if current_turn_is_readonly():
            return READONLY_TOOL_REFUSAL
        fact = " ".join(text.split())
        if target is None:
            return await self._personal(fact, choice)

        written = await self._append(target, line, text=fact,
                                     attribution="" if self._origin_marker else choice)
        if fallback:
            return (
                f"{written}\n\nNote: attribution {fallback!r} is not one of 'said' / "
                f"'inferred', so this line was recorded as '{_DEFAULT_ATTRIBUTION}' — it "
                "cannot later become a decided page. If the user stated this themselves, "
                "append it again with attribution='said'; the journal is append-only, so "
                "the line above stays either way."
            )
        return written

    def _target(self, own: Path | None, notebook: str) -> tuple[Path | None, str]:
        """Dove va la riga: ``(radice del quaderno, "")``, ``(None, "")`` per il
        diario personale, o ``(None, rifiuto)``.

        **I cassetti** (dal 12/10/2026): dalla chat personale Jenny mette un
        fatto nel quaderno a cui appartiene, senza che l'utente debba aprirlo.
        Da un quaderno si scrive solo il suo journal — il confine di scrittura
        del quaderno, detto qui perche' questo tool scrive senza passare dai
        tool di file. Un subagent non riempie i cassetti: lavora per un compito,
        e cosa e' di quale quaderno lo decide la conversazione. E un quaderno
        che non c'e' non si crea: lo dice, con l'elenco di quelli che ci sono.
        """
        if own is not None:
            if notebook and notebook != own.name:
                return None, (
                    f"From the notebook '{own.name}' only its own journal can be written, not "
                    f"'{notebook}'. Nothing was written. Leave `notebook` empty to write here; "
                    "to note something for another notebook, tell the user to say it there or "
                    "in the personal chat."
                )
            return own, ""
        if not notebook:
            if self._memory is None:
                return None, _NO_PROJECT_REFUSAL
            return None, ""
        if current_subagent_policy() is not None:
            return None, (
                "A subagent does not write into the notebooks' journals from outside them. "
                "Nothing was written: put the fact in your result, and Jenny will file it."
            )
        roots = self._notebooks()
        if notebook not in roots:
            known = ", ".join(sorted(roots)) or "none"
            return None, (
                f"No notebook named '{notebook}' (existing: {known}). Nothing was written. "
                "Never create a notebook on your own: if none fits, leave `notebook` empty "
                "to keep it in your diary, or ask the user whether to open one."
            )
        return roots[notebook], ""

    def _notebooks(self) -> dict[str, Path]:
        from jenny.board.placements import _wikis_dirs
        from jenny.utils.wiki_paths import discover_wiki_roots

        workspace = Path(getattr(self._memory, "workspace", "") or ".")
        roots: dict[str, Path] = {}
        for wikis in _wikis_dirs(workspace):
            with suppress(OSError):
                roots.update(discover_wiki_roots(wikis))
        return roots

    async def _personal(self, fact: str, attribution: str) -> str:
        """Una riga del diario personale: un fatto che non e' di nessun quaderno."""
        request = current_request_context()
        content = fact if len(fact) <= _MIRROR_MAX_CHARS else (
            fact[: _MIRROR_MAX_CHARS - 1].rstrip() + "…")
        try:
            await asyncio.to_thread(
                self._memory.append_history, content,
                session_key=(request.session_key if request is not None else None)
                or UNIFIED_SESSION_KEY,
                kind=attribution, untrusted=bool(current_turn_taint()),
            )
        except Exception as exc:  # noqa: BLE001 — una riga persa si dice
            logger.warning("journal_append: personal diary failed: {}", exc)
            return f"Could not write to your diary: {exc}"
        return f"Noted in your diary: {content}"

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

    async def _append(self, root: Path, line: str, *, text: str = "",
                      attribution: str = "") -> str:
        # Un colpo d'orologio, due usi: la pagina e l'ora della riga.
        stamp = self._stamp()
        day = self._today() if self._today is not None else stamp.date()
        page = wiki_journal_dir(root) / journal_page_name(day)
        entry = f"- {stamp.strftime('%H:%M')} — {line}\n"
        try:
            lineno = append_journal_entry(page, day, entry)
        except OSError as exc:
            logger.warning("journal_append on {} failed: {}", page, exc)
            return f"Could not append to the journal: {exc}"

        logger.info("journal_append: {} <- {}", page.name, line[:60])
        if attribution:
            await self._mirror(root, page, lineno, text, attribution)
        return f"Appended to {page.parent.name}/{page.name}: {entry.strip()}"

    async def _mirror(self, root: Path, page: Path, lineno: int, text: str,
                      attribution: str) -> None:
        """La copia breve della riga nel diario della memoria (``history.jsonl``).

        Il journal resta la fonte del quaderno; questa e' l'indice che la chat
        personale vede: il fatto in una riga, chi l'ha detto, di quale quaderno
        parla, dove sta l'originale. Senza, quel che l'utente diceva in un
        quaderno arrivava al diario solo con la raccolta all'inattivita' (un
        riassunto, quindici minuti dopo), e la chat personale non lo vedeva
        comunque — misurato l'11/10/2026 sul caso del film.

        Le righe ``[recovered]`` del giardiniere non si specchiano: sono fatti
        gia' detti e persi dalla cattura, recuperati a posteriori, e il diario
        datato al recupero mentirebbe sul quando. Un fallimento qui non tocca la
        riga gia' scritta: il journal e' la fonte, il diario un indice.
        """
        store = self._memory
        if store is None:
            return
        workspace = Path(getattr(store, "workspace", "") or "")
        try:
            where = page.relative_to(workspace)
        except ValueError:
            where = page
        request = current_request_context()
        session_key = request.session_key if request is not None else None
        content = text if len(text) <= _MIRROR_MAX_CHARS else (
            text[: _MIRROR_MAX_CHARS - 1].rstrip() + "…")
        try:
            await asyncio.to_thread(
                store.append_history, content,
                session_key=session_key or f"{PROJECT_SESSION_PREFIX}{root.name}",
                kind=attribution, about=root.name, ref=f"{where.as_posix()}:{lineno}",
                untrusted=bool(current_turn_taint()),
            )
        except Exception as exc:  # noqa: BLE001 — v. la docstring
            logger.warning("journal_append: diary mirror failed: {}", exc)


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


# ── «Ci spostiamo?» ──────────────────────────────────────────────────────────
#
# Dalla chat personale, quando la conversazione e' diventata lavoro su un
# quaderno, Jenny propone di passarci: un tasto sotto la risposta, e decide
# l'utente (scelta del 12/10/2026: proposta, non spostamento automatico). Il
# passaggio porta con se' il contesto: una riga di diario che parla di quel
# quaderno, e che il quaderno vede nel suo blocco «Diary» — sa da dove venite.

_SUGGEST_PARAMETERS = tool_parameters_schema(
    notebook=StringSchema("The notebook to offer, by its name in the Wikis list."),
    context=StringSchema(
        "One line for the notebook to pick up from: what you and the user were working on, "
        "and where you had got to. It is what you will find there if they accept."
    ),
    required=["notebook", "context"],
)


@tool_parameters(_SUGGEST_PARAMETERS)
class SuggestNotebookTool(Tool):
    """Offer to move the conversation into a notebook: a button under the reply."""

    # Solo l'agente con cui l'utente parla: un subagent non decide dove si
    # conversa.
    _scopes = {"core", "orchestrator"}

    def __init__(self, memory_store: Any = None) -> None:
        self._memory = memory_store
        self._lookup = JournalAppendTool(memory_store=memory_store)

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return getattr(ctx, "memory_store", None) is not None

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(memory_store=getattr(ctx, "memory_store", None))

    @property
    def name(self) -> str:
        return "suggest_notebook"

    @property
    def description(self) -> str:
        return (
            "Offer to continue in one of the user's notebooks: a button to open it appears "
            "under your reply, and the user decides. Use it only when the conversation has "
            "become work on that notebook's subject — several exchanges, pages or a board to "
            "write — never for a passing fact (file that with journal_append). Offer it once: "
            "if the user stays here, go on here. Personal chat in the app only."
        )

    async def execute(self, notebook: str = "", context: str = "", **_: Any) -> str:
        name = (notebook or "").strip()
        line = " ".join((context or "").split())
        request = current_request_context()
        scope = current_workspace_scope()
        own = scope.write_root() if scope is not None else None
        if own is not None and is_wiki_root(own):
            return "You are already in a notebook: there is nowhere to move. Nothing was offered."
        if request is not None and request.channel != "websocket":
            return ("A notebook opens only in the app, not from here. Nothing was offered: "
                    "go on in this conversation.")
        if current_subagent_policy() is not None:
            return "A subagent cannot move the user's conversation. Nothing was offered."
        roots = self._lookup._notebooks()
        if name not in roots:
            known = ", ".join(sorted(roots)) or "none"
            return f"No notebook named '{name}' (existing: {known}). Nothing was offered."
        if not line:
            return "Say in `context` what you were working on: it is what the notebook picks up."
        if len(line) > _MIRROR_MAX_CHARS:
            line = line[: _MIRROR_MAX_CHARS - 1].rstrip() + "…"
        try:
            await asyncio.to_thread(
                self._memory.append_history, f"From the personal chat: {line}",
                session_key=(request.session_key if request is not None else None)
                or UNIFIED_SESSION_KEY,
                kind="inferred", about=name, untrusted=bool(current_turn_taint()),
            )
        except Exception as exc:  # noqa: BLE001 — il tasto vale anche senza la riga
            logger.warning("suggest_notebook: handoff line not written: {}", exc)
        return (
            f"The user now sees a button under your reply that opens the notebook '{name}'; "
            "there the conversation picks up from what you wrote in `context`. Do not repeat "
            "the offer in this conversation, and do not move anything yourself: if they stay "
            "here, go on here."
        )


TOOLS = [JournalAppendTool, SuggestNotebookTool]
