"""Memory system: pure file I/O store and lightweight Consolidator."""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, NamedTuple

from loguru import logger

from jenny.agent.internal_run import internal_run_completed as _internal_run_completed
from jenny.agent.internal_run import (
    internal_run_should_commit as _internal_run_should_commit,
)
from jenny.agent.internal_run import prune_internal_sessions as _prune_internal_sessions
from jenny.agent.memory_archive import archive_dir
from jenny.session.keys import (
    DREAM_SESSION_PREFIX,
    internal_session_kind,
    is_internal_session_key,
    is_personal_session_key,
    is_project_session_key,
    session_kind,
)
from jenny.utils.helpers import (
    ensure_dir,
    strip_think,
    truncate_text,
)
from jenny.utils.path import atomic_write
from jenny.utils.prompt_templates import render_template

# Separatore fra il template di Dream e il batch di storia, dentro il prompt che
# Quante voci di progetto gia' consumate vengono rimostrate a un run di progetto
# (v. :meth:`MemoryStore._with_project_replay`). Tre e non uno: la misura
# dell'08/09/2026 dice che l'unione di quattro letture recupera tutto e che una
# sola ne prende fra un quarto e tre quarti, quindi due passaggi in piu' sono il
# punto in cui la curva si appiattisce. Zero disattiva la finestra.
_DREAM_PROJECT_REPLAY = 3


class DreamBatch(NamedTuple):
    """Un batch pronto per un run di Dream.

    ``scope`` e' ``"personal"`` o ``"project"``, e non e' un'etichetta
    descrittiva: sceglie il template del prompt **e** la cassetta
    (:meth:`MemoryStore.build_dream_tools`). Viaggia nel valore di ritorno e non
    in un attributo dello store perche' due run possono essere in volo insieme,
    e uno stato condiviso li farebbe leggere la scelta dell'altro.
    """

    prompt: str
    cursor: int
    scope: str


# ``MemoryStore.build_dream_prompt`` incolla. È una costante perché lo legge anche
# ``dream_prompt_history``, che sul prompt fa il taglio inverso.
DREAM_HISTORY_HEADER = "\n\n## Conversation History\n"

# Chiave nei metadata di sessione: il cursore sotto il quale il diario non entra
# più nel prompt *di quella sessione*.
#
# Esiste perché ``/new`` non azzerava quel che il modello vede. Il comando
# archivia la conversazione scartata in ``history.jsonl``
# (``Consolidator.archive``), e il blocco ``# Recent History`` reinietta ogni
# voce dall'ultimo cursore di Dream: il primo turno della sessione "nuova"
# portava quindi il riassunto di quella appena buttata, più quelli di ogni
# auto-compattazione precedente, finché Dream non passava. Con centinaia di file
# letti prima del reset è esattamente il «continua a citare le note» della issue
# #11 — e la stessa ragione per cui una regola di stile nuova non attaccava: il
# modello aveva sotto gli occhi il riassunto delle proprie risposte vecchie.
#
# **Il cursore di Dream non si tocca**, e questa chiave esiste proprio per non
# toccarlo: spostarlo avrebbe fatto saltare a Dream quelle voci, cioè avrebbe
# pagato la pulizia del prompt con un buco nella memoria di lungo periodo. Qui si
# alza solo il pavimento di *lettura per il prompt*; ``read_unprocessed_history``
# e Dream continuano a vedere tutto.
HISTORY_FLOOR_METADATA_KEY = "_history_floor"


def is_gardener_session_key(key: str | None) -> bool:
    """True se *key* è la sessione di una passata del giardiniere. **T7.8.**

    **Il posto giusto per questa funzione è** :mod:`jenny.session.keys`, accanto a
    ``is_project_session_key`` / ``is_personal_session_key``: là sta il
    vocabolario delle chiavi e là sta il letterale ``"gardener"``. Sta qui e non
    là perché ci sono due chiamanti — la regola della coda qui sotto
    (:meth:`MemoryStore.read_recent_history_for_prompt`) e il cancello della
    rubrica in ``ContextBuilder.build_system_prompt`` — e due confronti con lo
    stesso letterale scritti in due file sono la cosa che poi divergono; questo
    modulo è l'unico dei due che l'altro importa già. Va spostata.

    Funzione di modulo e non ``@staticmethod`` su ``MemoryStore``, che è
    documentato come "pure file I/O for memory files": è la lezione di T7.3.
    """
    return internal_session_kind(key or "") == "gardener"

# Il blocco "già registrato" che la fase 4 del piano aggiunge al prompt del
# Consolidator. Tre numeri, e nessuno è arbitrario:
#
# ``_KNOWN_FACTS_MAX_TOKENS`` è **misurato, non stimato**. Era 1.200, scelto
# sommando i tetti dei due file caldi; sul Titan 2 il blocco reale è uscito a
# 5.239 caratteri contro i 4.800 concessi, perché quel conto dimenticava le
# istruzioni in testa e la coda in attesa. 1.600 li copre con margine, e resta
# comunque irrilevante contro la finestra da cui viene sottratto (v.
# ``Consolidator._truncate_to_token_budget``). Il taglio non è lì per il costo:
# è lì per il giorno in cui i tetti dei file si alzano senza che nessuno guardi
# qui.
#
# ``_KNOWN_FACTS_PENDING_ENTRIES`` limita quante voci di history si leggono, e
# ``_KNOWN_FACTS_PENDING_SHARE`` quanto blocco possono occupare. Servono i due
# insieme e per ragioni opposte: la coda è la fonte dominante del difetto — una
# conversazione lunga viene consolidata più volte prima che Dream giri una sola
# — quindi le va garantito uno spazio; ma è anche l'unica delle due sorgenti
# senza un tetto proprio, e un Dream fermo da giorni (cioè esattamente il
# guasto per cui esiste questo piano) le farebbe altrimenti prendere il blocco
# intero, cancellando i file e facendo riestrarre tutto USER.md. La quota è un
# pavimento per la coda, non un soffitto per il resto: ciò che non spende torna
# ai file.
_KNOWN_FACTS_MAX_TOKENS = 1600
_KNOWN_FACTS_PENDING_ENTRIES = 20
_KNOWN_FACTS_PENDING_SHARE = 0.4

# Oltre quante voci in ``memory/archive/`` la crescita della cartella va detta.
#
# L'archivio **non ha ritenzione**, di proposito e documentato: il testo costa
# poco e il punto della fase 2 è che niente sia più irrecuperabile
# (``agent/memory_archive.py``). Ma ``entry_id`` è un hash del contenuto, quindi
# ogni riformulazione dello stesso fatto deposita un file nuovo: la crescita
# segue il churn del review pass, non le rimozioni vere, e nessuno se ne
# accorgerebbe.
#
# 300 non è il punto in cui la cartella diventa pesante — contare 300 dirent costa
# frazioni di millisecondo. È il primo conteggio a cui **l'archivio non sta più in
# una chiamata di ``recall``**: quel tool rende l'indice entro 24.000 caratteri a
# ~90 per voce (v. ``tools/memory_recall.py``), cioè intorno alle 265, e da lì in
# poi il modello non vede più l'archivio intero in una volta — che è la soglia
# oltre la quale il piano prevede un passaggio di selezione sull'indice (fase 7.2).
# Con 41 voci sul device il margine resta di oltre 7 volte.
#
# Il segnale sta **anche qui** e non solo là: ``MemoryRecallTool._list`` logga
# quando *il modello cerca*, cosa che può non succedere per settimane, mentre
# questo passa a ogni system prompt — è l'unico dei due che si accorge da sé.
_ARCHIVE_NOTABLE_ENTRIES = 300

# I mark che il Consolidator scrive davanti a ogni fatto. Elencati qui e non
# dedotti da una regex generica perché questa lista è anche il filtro che tiene
# fuori dal blocco i raw-dump: quando la chiamata LLM fallisce, in history
# finisce una conversazione intera sotto ``[RAW]``, e reiniettarla nel prompt
# della consolidation successiva sarebbe rimettere in circolo esattamente ciò
# che la consolidation esiste per togliere.
_FACT_LINE = re.compile(
    r"^[-*][ \t]+\[(permanent|durable|ephemeral|correction|skip)\][ \t]*(\S.*)$"
)


def _entries_cost(entries: list[str]) -> int:
    return sum(len(entry) + 1 for entry in entries)


def _pack_entries(entries: list[str], budget_chars: int | None) -> tuple[list[str], int]:
    """Le voci che entrano nel budget, intere, e quante ne restano fuori.

    Il taglio a carattere di ``truncate_text_to_tokens`` qui non va bene: mezza
    voce in un elenco di fatti già registrati si legge come un fatto diverso, e
    il modello confronterebbe con qualcosa che nessuno ha mai scritto.
    """
    if budget_chars is None:
        return list(entries), 0
    kept: list[str] = []
    used = 0
    for entry in entries:
        cost = len(entry) + 1
        if used + cost > budget_chars:
            break
        kept.append(entry)
        used += cost
    return kept, len(entries) - len(kept)


def iter_fact_lines(text: str) -> list[tuple[str, str]]:
    """I fatti annotati dentro una voce di history, come ``(mark, fatto)``.

    Serve a due chiamanti con lo stesso bisogno da lati opposti: il blocco
    "già registrato" li legge per dire cosa è in attesa, e la misura della
    fase 4 li conta per dire quanti ne sono usciti da un run.
    """
    found: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        match = _FACT_LINE.match(line.strip())
        if match:
            found.append((match.group(1), match.group(2).strip()))
    return found


if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# MemoryStore — pure file I/O layer
# ---------------------------------------------------------------------------

class MemoryStore:
    """Pure file I/O for memory files: MEMORY.md, history.jsonl, SOUL.md, USER.md."""

    _DEFAULT_MAX_HISTORY = 1000

    def __init__(self, workspace: Path, max_history_entries: int = _DEFAULT_MAX_HISTORY):
        self.workspace = workspace
        self.max_history_entries = max_history_entries
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "history.jsonl"
        self.soul_file = workspace / "SOUL.md"
        self.user_file = workspace / "USER.md"
        self._cursor_file = self.memory_dir / ".cursor"
        self._dream_cursor_file = self.memory_dir / ".dream_cursor"
        self._review_state_file = self.memory_dir / ".dream_review"
        self._corruption_logged = False  # rate-limit non-int cursor warning
        self._malformed_entry_logged = False  # rate-limit bad history shape warning
        self._oversize_logged = False  # rate-limit oversized-entry warning
        self._archive_size_logged = False  # rate-limit archive-growth notice
        self._append_lock = threading.Lock()  # serialize cursor allocation + append

    # -- generic helpers -----------------------------------------------------

    @staticmethod
    def read_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    # -- MEMORY.md (long-term facts) -----------------------------------------

    def read_memory(self) -> str:
        return self.read_file(self.memory_file)

    # -- context injection (used by context.py) ------------------------------

    def get_memory_context(self) -> str:
        long_term = self.read_memory()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    def get_memory_pointer_context(self) -> str:
        """Una riga per dire che ``MEMORY.md`` esiste, a chi non lo riceve intero.

        Una conversazione di progetto non riceve piu' il blocco ``Long-term
        Memory`` (il cancello sta in ``ContextBuilder.build_system_prompt``):
        quel file e' l'inventario di *dove altro si lavora*, e misurando le sue
        voci una per una ognuna serve a **un** progetto solo — cioe' appartiene a
        quel progetto, non a tutti.

        Ma toglierlo e basta lo renderebbe irraggiungibile in pratica, ed e'
        esattamente il difetto che :meth:`get_archive_context` esiste per non
        fare: un file che il modello non sa esistere non viene mai aperto, quindi
        dal suo punto di vista non e' "non iniettato", e' cancellato. E ``recall``
        non copre il buco — legge ``memory/archive/``, cioe' il tier *freddo*, i
        fatti che Dream ha **tolto** da ``MEMORY.md``, non il file vivo.

        Da cui questa riga: il percorso, e il tool con cui si apre. Piatta nella
        dimensione del file, come quella dell'archivio, e assente quando il file
        e' vuoto.

        **E dice che non e' materiale di progetto**, per la stessa ragione per cui
        quella dell'archivio dice che li' non si scrive. Senza, il percorso
        nominato davanti alla regola di cattura di ``agent/project.md`` («what the
        user tells you is material») e' un invito a promuovere la vita personale
        dentro la wiki di un progetto: riaprirebbe dal lato della **scrittura** il
        confine che il cancello chiude dal lato della lettura.

        **Al giardiniere non va, e non e' una svista.** I suoi quattro tool di
        lettura hanno ``allowed_dir = wikis/<nome>``
        (``GardenerStore.build_tools``, sotto il commento «Lettura: dentro il
        progetto»), quindi quel percorso la sua cassetta lo rifiuta comunque:
        indicargli un file che non puo' aprire e' peggio dell'assenza. E il suo
        template non nomina ``MEMORY.md`` in nessun punto, quindi togliergli il
        blocco non lascia scoperta nessuna promessa.
        """
        if not self.read_memory().strip():
            return ""
        return (
            "## Long-term Memory\n"
            "The user's long-term memory is in `memory/MEMORY.md`, outside this project, "
            "and is not shown here: it is the inventory of where else they work, and each "
            "fact in it belongs to one project rather than to all of them. Open it with "
            "`read_file` when a question actually turns on something outside this project. "
            "What you read there is background, not this project's material — do not "
            "journal it and do not promote it into a page."
        )

    def get_archive_context(self) -> str:
        """Una riga sola per dire che il tier freddo esiste, o stringa vuota.

        Serve per la stessa ragione dell'elenco delle wiki: **un indice che
        nessuno sa esistere non viene mai aperto**. Un archivio invisibile al
        modello è indistinguibile, dal suo punto di vista, da una cancellazione —
        e allora tanto varrebbe cancellare.

        Tre vincoli, e sono ciò che tiene la riga onesta:

        - **È piatta nella dimensione dell'archivio.** Un abstract per voce
          spenderebbe il budget caldo che questa fase esiste per proteggere. Qui
          c'è un numero, e cresce di zero token quando l'archivio raddoppia.
        - **Sparisce quando l'archivio è vuoto**, così un'installazione nuova non
          paga niente per una cartella che non esiste ancora.
        - **Dice esplicitamente che lì non si scrive.** Il percorso, nominato in un
          prompt che riceve anche Dream, sarebbe altrimenti un invito: la sua
          allowlist non lo comprende, e per lui una scrittura rifiutata non è un
          tentativo a vuoto ma un run intero che non commette niente. La
          degradazione la fa il runtime (v. ``tools/memory_entries.py``), e questa
          riga lo dichiara invece di lasciarlo indovinare.

        **Il conteggio si rifà a ogni build di system prompt, e non è in cache.**
        Non per distrazione: la cartella è fatta di file piccoli e contare le
        dirent costa ~44 µs a 41 voci e ~2,7 ms a 5.000, contro un turno che ne
        spende secondi dal provider. Una cache validata sull'mtime della
        directory risparmierebbe una ``scandir`` pagando uno ``stat``, cioè
        niente, e comprerebbe in cambio una finestra di staleness il cui modo di
        sbagliare è esattamente quello che questa riga esiste per impedire: un
        conteggio stantio a 0 fa **sparire il blocco**, e un modello che non vede
        l'archivio torna a trattare una degradazione come una cancellazione.
        Quando questa cartella diventerà davvero costosa la risposta è la
        ritenzione o un indice (fase 7.2 del piano), non una cache — e
        :data:`_ARCHIVE_NOTABLE_ENTRIES` è il campanello che dice quando.
        """
        directory = archive_dir(self.memory_dir)
        try:
            count = sum(1 for _ in directory.glob("*.md"))
        except OSError:
            return ""
        if not count:
            return ""
        if count >= _ARCHIVE_NOTABLE_ENTRIES and not self._archive_size_logged:
            # Una volta per processo, come gli altri avvisi rate-limitati di
            # questa classe: qui si passa a ogni turno, e un avviso per turno è
            # rumore che si impara a ignorare. Su un telefono il gateway
            # riparte spesso, quindi "una volta per processo" resta una riga che
            # si rivede.
            self._archive_size_logged = True
            logger.info(
                "Memory archive holds {} entries (>= {}): past the point where the "
                "recall index can list them all in one call, so retention or an "
                "index pass over the archive starts to earn its place "
                "(plan phase 7.2). Nothing is lost meanwhile — the directory has "
                "no retention by design",
                count, _ARCHIVE_NOTABLE_ENTRIES,
            )
        return (
            "## Archive\n"
            f"Facts removed from long-term memory are moved to `memory/archive/` "
            f"({count} so far), never deleted — one file each, the fact itself as the body. "
            "The runtime files them there; you never write to that directory. "
            "Use the `recall` tool to read it — not `grep`, which matches on "
            "substrings and so cannot find an Italian fact from an English "
            "question, and skips large files without saying so."
        )

    def get_known_facts_context(
        self,
        *,
        max_tokens: int = _KNOWN_FACTS_MAX_TOKENS,
        pending_entries: int = _KNOWN_FACTS_PENDING_ENTRIES,
        session_key: str | None = None,
    ) -> str:
        """Ciò che la memoria già registra, per il prompt del Consolidator.

        Il difetto che questo blocco chiude (``D5``) è che il Consolidator
        estrae alla cieca: non vede cosa è già stato consolidato, quindi
        riestrae gli stessi fatti a ogni passaggio. Il costo non è teorico — è
        un turno LLM, rumore in ``history.jsonl``, e un batch di soli duplicati
        che a valle si legge come "Dream non ha consolidato niente", che è
        falso e che è la ragione per cui a un certo punto è servita una soglia
        di pressione per indovinarlo.

        ``session_key`` è la sessione del consolidamento per cui il blocco viene
        costruito, e serve solo a filtrare la coda con la stessa regola del
        prompt di turno (:meth:`read_recent_history_for_prompt`): la propria
        coda più quella della conversazione personale, non quella di terzi.
        Omesso, non filtra niente.

        **Per una sessione di progetto il blocco non esiste**, e la ragione non è
        il costo in token. La coda era già filtrata (quella funzione risponde
        lista vuota a un progetto), ma i due file caldi si leggevano comunque:
        il profilo personale finiva nel prompt di consolidamento di un progetto
        insieme all'istruzione che chiede di estrarre un ``[correction]`` verso
        quei fatti. Quel ``[correction]`` non ha dove andare — il riassunto di un
        progetto arriva a ``_last_summary`` nei metadati della sessione e
        ``append_history`` non lo scrive (v. la sua docstring), quindi Dream non
        lo vede mai — cioè una correzione alla memoria di lungo periodo estratta
        qui è una correzione **persa**. Un blocco che invita a produrre qualcosa
        che il percorso poi butta è peggio di un blocco assente.

        L'assenza è anche già prevista dal template: ``consolidator_archive.md``
        dice "if this prompt shows you that memory, use what it shows; if it does
        not, extract the fact and let Dream deduplicate". Niente da riscrivere là.

        **Due sorgenti, e la seconda è quella che conta.** I file caldi dicono
        cosa Dream ha già archiviato; la coda di history oltre il cursore di
        Dream dice cosa è già stato estratto e sta aspettando. Con i soli file,
        una conversazione lunga — consolidata tre volte prima che Dream giri
        una — resterebbe duplicata esattamente come prima, perché al momento
        della seconda estrazione nei file non c'è ancora niente.

        **Quindi la coda si serve per prima, con una quota sua.** Misurato sul
        Titan 2 il 2026-08-19: il blocco stava a 5.239 caratteri contro un
        tetto di 4.800, e il troncamento tagliava proprio le voci in attesa,
        che stavano in fondo — la sorgente dominante nella posizione che si
        perde per prima. Le due quote non sono simmetriche e non devono
        esserlo: se il tetto lo prendesse tutto la coda, un Dream fermo da
        giorni — cioè esattamente il guasto per cui esiste questo piano —
        cancellerebbe i file dal blocco e farebbe riestrarre tutto USER.md.

        **Solo voci intere.** Un fatto tagliato a metà in un elenco che dice
        "questi sono già registrati" è peggio di un fatto assente: si legge
        come un fatto *diverso*, e il confronto che il blocco esiste per
        permettere diventa un confronto con qualcosa che nessuno ha mai
        scritto. Ciò che non entra viene contato in chiaro, così il modello sa
        di leggere un elenco parziale invece di dedurlo.

        **Le istruzioni stanno prima dell'elenco**, e non è impaginazione: ciò
        che si perde per primo dev'essere un fatto in meno da confrontare, mai
        la regola su come confrontarli.

        Stringa vuota quando non c'è niente da mostrare, per la stessa ragione
        di :meth:`get_archive_context`: un'installazione nuova non paga token
        per dichiarare che la memoria è vuota.
        """
        from jenny.agent.tools.memory_entries import entry_id, parse_entries

        # Il gate sta **prima** delle due letture, non dentro il filtro della
        # coda: la coda era già chiusa, i file caldi no, ed è da lì che passava
        # il profilo personale. La chiave ce l'abbiamo in mano.
        if session_key and is_project_session_key(session_key):
            return ""

        seen: set[str] = set()

        pending_facts: list[str] = []
        # Stessa regola di visibilità del prompt di turno, e volutamente la
        # stessa funzione: questo blocco finisce nel prompt di un consolidamento,
        # che appartiene a una sessione precisa. Senza ``session_key`` (chiamate
        # dirette, test) non filtra niente, come prima.
        pending = self.read_recent_history_for_prompt(
            self.get_last_dream_cursor(), session_key=session_key,
        )
        for record in pending[-pending_entries:] if pending_entries > 0 else []:
            for mark, fact in iter_fact_lines(record.get("content", "")):
                # ``[skip]`` non è un fatto registrato: è un fatto che il
                # Consolidator ha già giudicato non degno. Mostrarlo qui
                # direbbe "questo è in memoria", che è il contrario del vero.
                if mark == "skip":
                    continue
                bullet = f"- {fact}"
                fid = entry_id(bullet)
                if fid not in seen:
                    seen.add(fid)
                    pending_facts.append(bullet)

        file_facts: list[str] = []
        for text in (self.read_file(self.user_file), self.read_memory()):
            for entry in parse_entries(text):
                if entry.id not in seen:
                    seen.add(entry.id)
                    file_facts.append(entry.text.strip())

        if not pending_facts and not file_facts:
            return ""

        head = "\n".join([
            "## Already recorded",
            "",
            "These facts are already in long-term memory, or are already extracted and "
            "waiting to be filed. Do not extract them again: a chunk of duplicates costs "
            "a full consolidation turn and is discarded downstream.",
            "",
            "Match on substance, not on wording. Two things are still worth extracting, "
            "and they are why this is a list rather than a blanket \"skip what you have "
            "seen before\":",
            "",
            # Numerate, non puntate: sotto, ogni riga che comincia con un
            # trattino è un fatto registrato, e due istruzioni travestite da
            # voci dell'elenco sarebbero due fatti che la memoria non contiene.
            "1. A fact that **changes or contradicts** one of these. Mark it [correction] "
            "and say what changed — a memory that cannot be updated is worse than one "
            "that repeats itself.",
            "2. A fact that **adds** to one of these: a detail, a limit, a case where it "
            "does not hold. That is new information about a known subject, not a repeat.",
            "",
        ])

        budget = (max_tokens * 4 - len(head)) if max_tokens > 0 else None
        if budget is not None and budget <= 0:
            return head.rstrip() + "\n"

        pending_budget = None if budget is None else int(budget * _KNOWN_FACTS_PENDING_SHARE)
        kept_pending, dropped = _pack_entries(pending_facts, pending_budget)
        # Ciò che la coda non ha speso torna ai file: la quota è un pavimento
        # per la coda, non un soffitto per il resto.
        file_budget = None if budget is None else budget - _entries_cost(kept_pending)
        kept_files, dropped_files = _pack_entries(file_facts, file_budget)
        dropped += dropped_files

        lines = [head, *kept_pending, *kept_files]
        if dropped:
            # Riga piatta e senza trattino: sopra, un trattino significa "fatto
            # registrato", e questa non lo è.
            lines.append(f"\n({dropped} further recorded facts are not listed here.)")
        return "\n".join(lines)

    # -- history.jsonl — append-only, JSONL format ---------------------------

    def append_history(
        self,
        entry: str,
        *,
        max_chars: int | None = None,
        session_key: str | None = None,
        prompt_visible: bool = True,
    ) -> int:
        """Append *entry* to history.jsonl and return its auto-incrementing cursor.

        Entries are passed through `strip_think` to drop template-level leaks
        (e.g. unclosed `<think` prefixes, `<channel|>` markers) before being
        persisted. If the cleaned content is empty but the raw entry wasn't,
        the record is persisted with an empty string rather than falling back
        to the raw leak — otherwise `strip_think`'s guarantees would be
        undone by history replay / consolidation downstream.

        A defensive cap (*max_chars*, default ``_HISTORY_ENTRY_HARD_CAP``) is
        applied as a final safety net: individual callers should cap their own
        content more tightly; this default only exists to catch unintentional
        large writes (e.g. an LLM echoing its input back as a "summary").

        **Una sessione di progetto scrive qui, con la propria chiave** (08/09/2026,
        v. ``.agent/project-memory-plan.md``). Fino a quel giorno questo metodo
        rifiutava una chiave ``project:`` e ritornava ``0``, e l'isolamento di un
        progetto era un'*assenza*. Adesso e' **una chiave piu' una destinazione**,
        ed e' un confine piu' stretto e non piu' largo: la chiave tiene la voce
        fuori da ogni prompt (:meth:`read_recent_history_for_prompt`), e a valle
        Dream puo' scriverne solo in ``USER.md`` (:meth:`build_dream_tools`).

        Il cancello e' caduto perche' guardava l'asse sbagliato. La riga
        dichiarata in ``.agent/security.md`` e' «chi sei viaggia, dove altro
        lavori no»: e' una regola sulla **categoria del fatto**, e questo era un
        cancello sull'**origine della sessione**. Nel verso in uscita le due
        coincidono, perche' esce solo l'identita'; in entrata no — un fatto
        identitario detto dentro un progetto e' identita', cioe' esattamente la
        classe autorizzata a viaggiare, e veniva fermato lo stesso. Misurato sul
        Titan 2: 39 righe su 72 dei journal di progetto erano fatti sulla persona,
        e 18 su 23 campionati non stavano in nessun file di memoria.

        **Quel che non e' cambiato**: ci passano sempre entrambi gli scrittori —
        il riassunto di ``Consolidator.archive`` e il dump di ``raw_archive``
        quando la chiamata LLM fallisce — e continua a essere un imbuto solo. E
        per una sessione **interna** la scrittura resta voluta, perche' un job
        cron rilegge le proprie voci per ricordarsi dei run passati.

        **E resta l'asimmetria grande** (T7.8): il diario esce ovunque, e di
        proposito. ``SOUL.md``, ``USER.md`` e ``MEMORY.md`` li compone
        ``ContextBuilder`` dalla radice dell'installazione per **ogni** tipo di
        sessione, e ``MemoryRecallTool`` prende l'archivio di quella radice alla
        costruzione ignorando lo scope del workspace. Quel che si chiude sulla
        sessione e' l'inventario fra progetti, non l'identita'. Il ragionamento
        intero sta in ``.agent/security.md``.

        ``prompt_visible=False`` scrive la voce **per Dream e non per i prompt**:
        :meth:`read_recent_history_for_prompt` la salta. Serve a ``/new``, che
        archivia qui la conversazione che l'utente ha appena buttato — senza il
        flag quel riassunto tornava nel blocco ``# Recent History`` del turno
        successivo, cioe' il reset restituiva al modello quel che aveva appena
        smesso di ricordare. Il flag e' sulla *voce* e non un cursore da
        aggiornare dopo, perche' l'archiviazione gira in background: qualunque
        seconda scrittura arriverebbe a sessione ormai ricaricata, e salvare
        l'oggetto vecchio vorrebbe dire riscrivere sopra i messaggi del turno
        intanto arrivato. Dream continua a vederla: e' la sola cosa che questo
        flag non tocca.
        """
        limit = max_chars if max_chars is not None else _HISTORY_ENTRY_HARD_CAP
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        raw = entry.rstrip()
        if len(raw) > limit:
            if not self._oversize_logged:
                self._oversize_logged = True
                logger.warning(
                    "history entry exceeds {} chars ({}); truncating. "
                    "Usually means a caller forgot its own cap; "
                    "further occurrences suppressed.",
                    limit, len(raw),
                )
            raw = truncate_text(raw, limit)
        content = strip_think(raw)
        # Cursor allocation and the append must be atomic: concurrent writers
        # could otherwise read the same current cursor and emit duplicates.
        with self._append_lock:
            cursor = self._next_cursor()
            if raw and not content:
                logger.debug(
                    "history entry {} stripped to empty (likely template leak); "
                    "persisting empty content to avoid re-polluting context",
                    cursor,
                )
            record: dict[str, Any] = {
                "cursor": cursor, "timestamp": ts, "content": content,
            }
            if session_key:
                record["session_key"] = session_key
            # Scritto solo quando e' falso: il diario e' append-only e riletto a
            # ogni turno, quindi una chiave in piu' su ogni riga si paga per
            # sempre, e l'assenza vuol dire "visibile" — cioe' come si sono
            # sempre comportate le voci gia' su disco.
            if not prompt_visible:
                record["prompt_visible"] = False
            with open(self.history_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            # Small full-file replacement each append: use the same atomic
            # temp-file+fsync+rename helper as every other on-disk cursor/state
            # file in this codebase (cron store, session manager, sidebar
            # state, ...), rather than a bare write_text with no durability.
            atomic_write(self._cursor_file, str(cursor))
        return cursor

    @staticmethod
    def _valid_cursor(value: Any) -> int | None:
        """Int cursors only — reject bool (``isinstance(True, int)`` is True)."""
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    def _iter_valid_entries(self) -> Iterator[tuple[dict[str, Any], int]]:
        """Yield ``(entry, cursor)`` for well-formed entries; warn once on corruption."""
        poisoned: Any = None
        malformed_cursor: int | None = None
        for entry in self._read_entries():
            raw = entry.get("cursor")
            if raw is None:
                continue
            cursor = self._valid_cursor(raw)
            if cursor is None:
                poisoned = raw
                continue
            if not self._valid_history_payload(entry):
                malformed_cursor = cursor
                continue
            yield entry, cursor
        if poisoned is not None and not self._corruption_logged:
            self._corruption_logged = True
            logger.warning(
                "history.jsonl contains a non-int cursor ({!r}); dropping it. "
                "Usually caused by an external writer; further occurrences suppressed.",
                poisoned,
            )
        if malformed_cursor is not None and not self._malformed_entry_logged:
            self._malformed_entry_logged = True
            logger.warning(
                "history.jsonl contains a malformed entry at cursor {}; dropping it. "
                "Usually caused by an external writer; further occurrences suppressed.",
                malformed_cursor,
            )

    @staticmethod
    def _valid_history_payload(entry: dict[str, Any]) -> bool:
        if not isinstance(entry.get("timestamp"), str):
            return False
        if not isinstance(entry.get("content"), str):
            return False
        session_key = entry.get("session_key")
        return session_key is None or isinstance(session_key, str)

    def _next_cursor(self) -> int:
        """Return the next cursor value, robust to a crash between the history
        append and the ``.cursor`` write.

        ``append_history`` fsyncs the new entry *before* atomically rewriting
        ``.cursor``; a process kill in that window (frequent on Android) leaves
        ``.cursor`` one behind the last persisted entry.  Trusting ``.cursor``
        alone would then re-allocate a cursor already on disk, producing
        duplicates that break ``read_unprocessed_history`` and the Dream
        cursor.  So we always consider *both* sources — the ``.cursor`` file
        and the last persisted entry — and take the maximum.  ``max`` also
        preserves monotonicity in the inverse case (history externally
        truncated below a higher ``.cursor``): a cursor is never reused.
        """
        candidates: list[int] = []
        if self._cursor_file.exists():
            with suppress(ValueError, OSError):
                file_cursor = self._valid_cursor(
                    int(self._cursor_file.read_text(encoding="utf-8").strip())
                )
                if file_cursor is not None:
                    candidates.append(file_cursor)
        last = self._read_last_entry() or {}
        entry_cursor = self._valid_cursor(last.get("cursor"))
        if entry_cursor is not None:
            candidates.append(entry_cursor)
        if candidates:
            return max(candidates) + 1
        # Both fast paths unusable — scan the whole file and take ``max``,
        # which stays correct even if the monotonic invariant was broken by
        # external writes.
        return max((c for _, c in self._iter_valid_entries()), default=0) + 1

    def current_history_cursor(self) -> int:
        """Il cursore dell'ultima voce di diario, ``0`` se il diario è vuoto.

        Serve a chi vuole dire «da qui in avanti»: ``/new`` se lo segna come
        pavimento del prompt (:data:`HISTORY_FLOOR_METADATA_KEY`). Passa da
        :meth:`_next_cursor` e non dall'ultima riga del file perché quel metodo è
        già la risposta robusta alla stessa domanda — tiene il massimo fra
        ``.cursor`` e l'ultima voce persistita, che su Android divergono a ogni
        kill fra l'append e la riscrittura del cursore.
        """
        return self._next_cursor() - 1

    def read_unprocessed_history(self, since_cursor: int) -> list[dict[str, Any]]:
        """Return history entries with a valid cursor > *since_cursor*."""
        return [e for e, c in self._iter_valid_entries() if c > since_cursor]

    @classmethod
    def _is_internal_history_session(cls, session_key: str | None) -> bool:
        """True se la voce di history viene da lavoro interno, non dall'utente.

        Il vocabolario e' quello unico di :mod:`jenny.session.keys`: qui resta
        solo la guardia su ``None``/stringa vuota, che il predicato canonico non
        ha perche' lavora su chiavi di sessione sempre presenti, mentre il
        ``session_key`` di una voce di history e' opzionale.
        """
        if not session_key:
            return False
        return is_internal_session_key(session_key)

    @classmethod
    def _is_personal_history_session(cls, session_key: str | None) -> bool:
        """True se la voce di history appartiene alla conversazione personale.

        Whitelist, e non la negazione di :meth:`_is_internal_history_session`,
        per la ragione spiegata in :func:`jenny.session.keys.is_personal_session_key`:
        chi la usa decide cosa entra nella memoria di lungo periodo, e per quella
        decisione l'elenco giusto è quello di chi *può*, non quello di chi non può.

        Una voce senza ``session_key`` conta come personale. È la convenzione
        conservativa: quel campo è opzionale e assente in tutte le voci scritte
        prima che l'attribuzione esistesse, e trattarle come non-personali
        renderebbe invisibile a Dream la storia già sul disco.
        """
        if not session_key:
            return True
        return is_personal_session_key(session_key)

    @classmethod
    def _history_entry_scope(cls, session_key: str | None) -> str:
        """La categoria di una voce di history: ``personal``, ``project``, ``internal``.

        Il vocabolario e' quello unico di :mod:`jenny.session.keys`; qui si
        aggiunge solo la convenzione su ``None``, che quel modulo non ha perche'
        lavora su chiavi sempre presenti. **Una voce senza chiave conta come
        personale**, ed e' la stessa scelta conservativa di
        :meth:`_is_personal_history_session`: quel campo e' opzionale e assente in
        tutte le voci scritte prima che l'attribuzione esistesse, e trattarle
        altrimenti renderebbe invisibile a Dream la storia gia' sul disco.
        """
        if not session_key:
            return "personal"
        return session_kind(session_key)

    def read_recent_history_for_prompt(
        self,
        since_cursor: int,
        *,
        session_key: str | None,
    ) -> list[dict[str, Any]]:
        """Return unprocessed history entries safe to inject into a turn prompt.

        La regola e' quaternaria, una risposta per categoria:

        - **progetto**: niente, e non "niente di altrui" ma proprio niente. Questa
          coda e' la contabilita della conversazione personale e la finestra e' il
          suo cursore di Dream; un progetto non condivide ne' l'una ne' l'altro,
          quindi il blocco "Recent History" del suo prompt non esiste invece di
          essere filtrato. Un'assenza non si puo' sbagliare, un filtro si.
        - **giardiniere**: le proprie voci, e **non** la conversazione personale
          (T7.8). Vedi sotto: e' l'unico ramo interno con questa restrizione.
        - **interna**: le proprie voci *piu'* la conversazione personale. Il primo
          ramo e' la cura dell'amnesia dell'heartbeat — un job rilegge i propri run
          — e ha un test che lo nomina
          (``test_cron_recent_history_can_see_own_history_and_unified_context``).
        - **personale**: la conversazione personale.

        Gli ultimi due rami sono la stessa riga, perche' il secondo membro della
        condizione e' la whitelist e non "non e' interna": la differenza si vede
        solo su una voce di progetto, che con la negazione sarebbe entrata in ogni
        prompt. Oggi nessuna voce di progetto puo' esistere — la scrittura e'
        chiusa in ``append_history`` — e questo e' il secondo giro di chiave, non
        una ridondanza inutile: chiude anche le voci scritte da una versione
        precedente o a mano.

        **Perche' il giardiniere e' l'eccezione fra gli interni** (T7.8, misurato
        il 23/08). Il ramo interno esiste perche' un job rilegga *i propri* run;
        la conversazione personale ci e' dentro perche' un job che gira nella chat
        personale ne fa parte. Una passata del giardiniere no: gira su **un**
        progetto, la sua cassetta legge dentro quella cartella e scrive solo in
        ``wiki/`` (``GardenerStore.build_tools``), e ``agent/gardener.md`` le dice
        «work only from those» — il diario del progetto, la mappa, l'inventario. Il
        verso che ne veniva era rovesciato: la **conversazione** di quel progetto
        non prende niente da questa coda (primo ramo), mentre la passata di
        manutenzione, che non ha nemmeno un utente con cui parlare, si prendeva la
        meta' personale. Nessun altro ramo cambia, e il giardiniere continua a
        rileggere le proprie voci: quel che sparisce e' solo la coda di qualcun
        altro. Il filtro e non un ``return []`` perche' la semantica vera e'
        «le sue si', quelle personali no»; oggi la chiave della passata porta
        l'orologio, quindi il blocco esce comunque vuoto — ma il giorno in cui
        diventasse stabile un ``return []`` gli negherebbe i propri run in
        silenzio.

        **E la restrizione e' un cancello davanti a un tool, non una tenda**: la
        passata puo' comunque *chiedere* la memoria personale — ``recall`` e i tre
        file di identita' restano dove sono, per la ragione scritta in
        ``.agent/security.md``. Questo ramo toglie quel che arrivava **non
        richiesto** dentro il prompt.
        """
        if session_key is not None and is_project_session_key(session_key):
            return []
        entries = [
            entry
            for entry in self.read_unprocessed_history(since_cursor=since_cursor)
            # Voce scritta "per Dream e non per i prompt" — oggi solo il
            # riassunto che ``/new`` archivia della conversazione buttata. Il
            # gate sta qui, prima di ogni ramo, perche' vale per **tutti** i tipi
            # di sessione: quel riassunto non e' roba di cui nessuno deve essere
            # informato a meta' turno, chiunque stia chiedendo. V.
            # :meth:`append_history` (``prompt_visible``).
            if entry.get("prompt_visible") is not False
        ]
        if session_key is None:
            return entries
        own_only = is_gardener_session_key(session_key)
        return [
            entry
            for entry in entries
            if (entry_session := entry.get("session_key")) == session_key
            or (not own_only and self._is_personal_history_session(entry_session))
        ]

    def compact_history(self) -> None:
        """Drop oldest entries if the file exceeds *max_history_entries*.

        The read→rewrite must hold ``_append_lock``: ``append_history`` runs on
        real threads (Consolidator via ``asyncio.to_thread``) and fsyncs a new
        entry before returning. Without the lock, an append landing between our
        ``_read_entries`` and the atomic ``_write_entries`` rewrite would be
        silently dropped by the rename. Holding the lock serializes the two:
        a concurrent append blocks until the rewrite completes, then appends on
        top of the compacted file.

        No self-deadlock: ``_append_lock`` is a non-reentrant ``threading.Lock``
        and nothing under it re-enters ``append_history`` or ``compact_history``
        (``_read_entries`` / ``_write_entries`` are pure file I/O). No caller
        holds the lock when invoking this method.
        """
        if self.max_history_entries <= 0:
            return
        with self._append_lock:
            entries = self._read_entries()
            if len(entries) <= self.max_history_entries:
                return
            kept = entries[-self.max_history_entries:]
            self._write_entries(kept)

    # -- JSONL helpers -------------------------------------------------------

    def _read_entries(self) -> list[dict[str, Any]]:
        """Read all entries from history.jsonl."""
        entries: list[dict[str, Any]] = []
        with suppress(FileNotFoundError):
            with open(self.history_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

        return entries

    def _read_last_entry(self) -> dict[str, Any] | None:
        """Read the last entry from the JSONL file efficiently."""
        try:
            with open(self.history_file, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return None
                read_size = min(size, 4096)
                f.seek(size - read_size)
                data = f.read().decode("utf-8")
                lines = [line for line in data.split("\n") if line.strip()]
                if not lines:
                    return None
                return json.loads(lines[-1])
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _write_entries(self, entries: list[dict[str, Any]]) -> None:
        """Overwrite history.jsonl with the given entries (atomic write)."""
        content = "".join(
            json.dumps(entry, ensure_ascii=False) + "\n"
            for entry in entries
        )
        atomic_write(self.history_file, content)

    # -- dream cursor --------------------------------------------------------

    def get_last_dream_cursor(self) -> int:
        if self._dream_cursor_file.exists():
            with suppress(ValueError, OSError):
                return int(self._dream_cursor_file.read_text(encoding="utf-8").strip())
        return 0

    def set_last_dream_cursor(self, cursor: int) -> None:
        # Stesso helper del cursore di history (vedi ``append``): un
        # write_text nudo qui lascerebbe, se il processo muore a metà, un file
        # vuoto o parziale — cioè un cursore che ``get_last_dream_cursor``
        # legge come 0 e Dream ricomincia da capo su tutta la storia.
        atomic_write(self._dream_cursor_file, str(cursor))

    # -- review pass state ---------------------------------------------------

    @classmethod
    def _review_counter(cls, value: Any) -> int:
        """Normalizza un contatore del review state: qualunque cosa strana → 0.

        Il rifiuto di ``bool`` viene da ``_valid_cursor`` (``isinstance(True,
        int)`` è True, e un ``true`` finito nel JSON conterebbe come 1). I
        negativi vanno a 0 e non passano così come sono: un contatore negativo
        letto dal disco rimanderebbe il review pass indietro nel tempo invece
        che avanti, cioè lo spegnerebbe in silenzio per N cicli.
        """
        counter = cls._valid_cursor(value)
        if counter is None or counter < 0:
            return 0
        return counter

    def get_review_state(self) -> tuple[int, int]:
        """Return ``(runs_since_review, stuck_runs)`` for the Dream review pass.

        Lettura tollerante quanto ``get_last_dream_cursor``: file assente, JSON
        troncato, radice non-dict, chiavi mancanti o di tipo sbagliato danno
        tutti ``(0, 0)`` e mai un'eccezione. Su Android il processo può essere
        ucciso in qualsiasi momento e questo file resta a metà: se la lettura
        sollevasse, il run di Dream che la chiama morirebbe prima di consolidare
        nulla — molto peggio del ripartire da zero, che al più ritarda un review
        pass di N cicli.

        ``runs_since_review`` conta i cicli dall'ultimo review pass;
        ``stuck_runs`` i run consecutivi in cui Dream ha tentato scritture senza
        riuscirne nessuna (v. ``dream_should_advance_cursor``: quei run non
        avanzano il cursore, e se si ripetono serve forzare un review invece di
        rimacinare per sempre lo stesso batch). Entrambi i contatori li consuma
        il chiamante: qui c'è solo lo stato su disco.
        """
        try:
            raw = self._review_state_file.read_text(encoding="utf-8")
        except OSError:
            return (0, 0)
        try:
            data = json.loads(raw)
        except ValueError:  # include JSONDecodeError
            return (0, 0)
        if not isinstance(data, dict):
            return (0, 0)
        return (
            self._review_counter(data.get("runs_since_review")),
            self._review_counter(data.get("stuck_runs")),
        )

    def get_nothing_new_runs(self) -> int:
        """Run consecutivi in cui Dream non ha consolidato **senza** rifiuti.

        Quarto campo di ``.dream_review``, letto a parte come
        :meth:`get_review_forced_at_stuck` e per la stessa ragione: non cambiare
        la firma di :meth:`get_review_state` e i suoi chiamanti.

        Perché è separato da ``stuck_runs``. Quel contatore conta va bene per
        "Dream non consolida", ma la domanda che governa il rimedio è un'altra:
        **un review pass può servire a qualcosa?** Se una scrittura è stata
        rifiutata dal tetto, sì — c'è spazio da liberare e forzarlo ha senso. Se
        invece non è stato rifiutato niente e comunque non è atterrato nulla, un
        review non ha nessuna leva: potherebbe file che non hanno niente da dare.
        Misurato sul Titan 2 il 2026-08-18, un review forzato su file al 77% e
        79% che non avevano niente da liberare.

        Uno stato scritto prima che questo campo esistesse legge ``0``, ed è la
        lettura giusta: il vecchio ``stuck_runs`` si eredita come *no room*, che è
        il ramo che tiene armata la via d'uscita dal livelock.
        """
        try:
            data = json.loads(self._review_state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if not isinstance(data, dict):
            return 0
        return self._review_counter(data.get("nothing_new_runs"))

    def get_review_forced_at_stuck(self) -> int:
        """A quale valore di ``stuck_runs`` il review è stato forzato l'ultima volta.

        Terzo campo dello stesso file, letto a parte per non cambiare la firma di
        :meth:`get_review_state` e i suoi chiamanti. ``0`` significa "mai forzato",
        che è anche il valore che si legge da uno stato scritto prima che questo
        campo esistesse: la prima volta il review si riforza, e da lì in poi il
        conto è giusto.

        Perché serve. Il review forzato dal livelock scatta su
        ``stuck % STUCK_FORCES_REVIEW == 0``, e ``stuck`` **non** viene toccato
        quando non c'era storia da consolidare (``advanced is None``). Su
        un'installazione in pari, con ``stuck`` fermo su un multiplo della soglia,
        quella condizione è vera a *ogni* run: un turno LLM di review ogni due ore
        per sempre — lo specchio esatto del livelock che il contatore esiste per
        chiudere. Ricordando a che valore si è già forzato, non si riforza finché
        quel valore non cambia, cioè finché Dream non manca un altro consolidamento.

        Il campo vale però solo dentro la salita di ``stuck`` che lo ha prodotto, e
        :meth:`set_review_state` lo azzera insieme al contatore: sopravvivergli lo
        trasformerebbe da freno in blocco — v. il commento lì.
        """
        try:
            data = json.loads(self._review_state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if not isinstance(data, dict):
            return 0
        return self._review_counter(data.get("forced_at_stuck"))

    def set_review_state(
        self,
        *,
        runs_since_review: int,
        stuck_runs: int,
        forced_at_stuck: int | None = None,
        nothing_new_runs: int | None = None,
    ) -> None:
        """Persist the review-pass counters to ``.dream_review``.

        *forced_at_stuck* a ``None`` — il default — **conserva** il valore su
        disco: i chiamanti che aggiornano solo i due contatori non devono
        conoscerlo, e non devono poterlo azzerare per omissione. Lo passa solo chi
        forza il review (v. :meth:`get_review_forced_at_stuck`). Con un'eccezione, che
        è un invariante del file e non una scelta del chiamante: scrivere
        ``stuck_runs=0`` azzera anche ``forced_at_stuck``.
        """
        # Stesso helper del cursore di Dream (v. ``set_last_dream_cursor``): un
        # write_text nudo lascerebbe, se il processo muore a metà, un JSON
        # troncato — che ``get_review_state`` legge come (0, 0), cioè un review
        # pass appena fatto anche se non è mai partito. Con ``atomic_write`` il
        # file o è quello vecchio o è quello nuovo, mai una via di mezzo.
        # I valori si normalizzano anche in scrittura: un contatore negativo
        # arrivato da un chiamante non deve nemmeno toccare il disco.
        forced = (
            self.get_review_forced_at_stuck() if forced_at_stuck is None else forced_at_stuck
        )
        stuck = self._review_counter(stuck_runs)
        # Stessa convenzione di ``forced_at_stuck``: ``None`` conserva. I due
        # contatori si azzerano però **insieme**, perché a azzerarli è lo stesso
        # evento — il cursore che avanza — e uno dei due rimasto su per omissione
        # racconterebbe un blocco che non c'è.
        nothing_new = (
            self.get_nothing_new_runs() if nothing_new_runs is None else nothing_new_runs
        )
        nothing_new = self._review_counter(nothing_new)
        # ``forced_at_stuck`` indicizza la salita di ``stuck`` che lo ha prodotto:
        # dice "a questo valore il review l'ho già forzato, non riforzarlo". Azzerato
        # ``stuck`` — cioè quando il cursore è avanzato e quella salita è finita — il
        # valore resta a galleggiare, e alla salita successiva ``dream_cycle`` ritrova
        # ``stuck == forced_at`` proprio al run in cui il review servirebbe: la
        # condizione ``stuck != forced_at`` è falsa e il freno diventa un blocco.
        # Misurato sul Titan 2 il 2026-08-18: ``forced_at_stuck: 2`` avanzato da un
        # episodio già chiuso, ``stuck`` di nuovo a 2, nessun review — è arrivato solo
        # a 4, sulla soglia d'allarme, due run oltre il suo scopo.
        #
        # Sta qui e non nel chiamante perché è una proprietà del file, non una
        # politica: i due campi non possono raccontare stati diversi. E non scarta la
        # scelta di nessuno — l'unico chiamante che passa un ``forced_at_stuck``
        # esplicito è il ramo livelock, che richiede ``stuck > 0``.
        if stuck == 0:
            forced = 0
        payload = json.dumps({
            "runs_since_review": self._review_counter(runs_since_review),
            "stuck_runs": stuck,
            "forced_at_stuck": self._review_counter(forced),
            "nothing_new_runs": nothing_new,
        })
        atomic_write(self._review_state_file, payload)

    def build_dream_prompt(
        self, *, max_entries: int = 20, gauge: str = "",
    ) -> "DreamBatch | None":
        """Build the Dream prompt with unprocessed history context.

        Ritorna un :class:`DreamBatch` — che resta spacchettabile come
        ``(prompt, cursor)`` solo per i primi due campi — o ``None`` se non c'e'
        niente da processare.

        *gauge* è la riga di riempimento dei file di memoria (es.
        ``MEMORY.md [67% — 1.474/2.200 char]``) da rendere nel prompt come
        ``budget_gauge``; con la stringa vuota — il default — la sezione Budget
        del template sparisce e il prompt resta identico a prima. Il calcolo
        sta nel chiamante di proposito: ``MemoryStore`` è uno strato di I/O
        puro e dargli qui la config per misurare il budget lo legherebbe al
        modulo che quel budget lo impone.
        """
        last_cursor = self.get_last_dream_cursor()
        # **Il filtro che tiene personale il diario personale.** Dream è l'unico
        # consumatore di ``history.jsonl`` che scrive in ``MEMORY.md``: quello che
        # passa da qui diventa un fatto che Jenny sa dell'utente, per sempre. Le
        # voci di una sessione interna stanno in quel file di proposito — è così
        # che un job cron ricorda i propri run precedenti (v.
        # ``read_recent_history_for_prompt``) — ma sono lavoro del sistema, non
        # cose dette dall'utente, e nel diario non ci devono entrare.
        #
        # Il cursore avanza solo fino all'ultima voce *ammessa* del batch: una
        # coda di sole voci interne lascia il cursore fermo e le fa rileggere
        # (e riscartare) al run seguente. E' il compromesso conservativo giusto
        # — costa la rilettura di poche righe, mentre saltare in avanti
        # rischierebbe di consumare una voce personale senza averla mai letta.
        pending = [
            (entry, scope)
            for entry in self.read_unprocessed_history(since_cursor=last_cursor)
            if (scope := self._history_entry_scope(entry.get("session_key"))) != "internal"
        ]
        if not pending:
            return None

        # **Un batch, un tipo solo.** Si prende la sequenza iniziale dello stesso
        # tipo e ci si ferma al primo cambio: le due categorie hanno prompt
        # diversi, cassette diverse e destinazioni diverse, quindi mescolarle in
        # un batch vorrebbe dire scegliere quale delle due regole applicare a
        # materiale dell'altra.
        #
        # Il prezzo e' un batch corto quando i tipi si alternano, e si paga con un
        # run in piu' — il cursore avanza comunque a ogni giro, quindi non c'e'
        # nessuno stallo, solo qualche ciclo di Dream in piu' per drenare. E'
        # il prezzo giusto: l'alternativa che tiene i batch grandi e' un cursore
        # per tipo, cioe' due filigrane che possono divergere su un file
        # append-only riscritto anche a mano.
        scope = pending[0][1]
        batch_entries = []
        for entry, entry_scope in pending[:max_entries]:
            if entry_scope != scope:
                break
            batch_entries.append(entry)

        history_text = "\n".join(
            f"[{e['timestamp']}] {truncate_text(e['content'], 500)}"
            for e in batch_entries
        )
        if scope == "project":
            template = render_template(
                "agent/dream_project.md", strip=True, budget_gauge=gauge,
            )
            history_text = self._with_project_replay(history_text, last_cursor)
        else:
            skill_creator_path = str(
                self.workspace / "skills" / "skill-creator" / "SKILL.md"
            )
            template = render_template(
                "agent/dream.md",
                strip=True,
                skill_creator_path=skill_creator_path,
                budget_gauge=gauge,
            )
        prompt = f"{template}{DREAM_HISTORY_HEADER}{history_text}"
        return DreamBatch(prompt, batch_entries[-1]["cursor"], scope)

    def _with_project_replay(self, history_text: str, last_cursor: int) -> str:
        """La storia del batch, preceduta dalle ultime voci di progetto gia' consumate.

        **Misurato l'08/09/2026**, e la ragione per cui esiste: su materiale
        ambiguo — quello in cui ogni fatto e' vestito da progetto — quattro
        estrazioni identiche hanno restituito **sottoinsiemi diversi** (1, 2, 3 e 2
        fatti su 4, intersezione vuota, unione completa). Su materiale non ambiguo
        la stessa estrazione era stabile, 10 su 10 ogni volta. Con un cursore che
        avanza una volta sola, quella meta' persa e' persa per sempre, e ogni notte
        e' una meta' diversa.

        Rimostrare le ultime voci gia' consumate da' alla stessa materia un secondo
        e un terzo passaggio. Non serve nessuna deduplica nuova: un fatto gia'
        atterrato viene riproposto e ``memory add`` risponde "already present"
        senza scrivere, che e' esattamente il conto su cui si regge
        ``batch_was_not_consolidated``.

        **E il cursore avanza lo stesso**, che e' la differenza fra questo e un
        cursore che arretra. Arretrare di N su un batch di N o meno vuol dire non
        avanzare mai: livelock, e silenzioso. Qui la finestra e' *contesto in
        piu'* dentro un batch che resta quello nuovo, quindi non c'e' nessuno
        stato per-voce da tenere e nessun modo di restare fermi.

        Costa qualche centinaio di token per run di progetto e niente altro.
        """
        if _DREAM_PROJECT_REPLAY <= 0:
            return history_text
        seen = [
            entry
            for entry, cursor in self._iter_valid_entries()
            if cursor <= last_cursor
            and self._history_entry_scope(entry.get("session_key")) == "project"
        ][-_DREAM_PROJECT_REPLAY:]
        if not seen:
            return history_text
        replay = "\n".join(
            f"[{e['timestamp']}] {truncate_text(e['content'], 500)}" for e in seen
        )
        return "\n".join([
            "### Already processed, shown again",
            "",
            "These entries were read by an earlier run. They are here because a single "
            "reading of project material misses a different half each time, so a second "
            "look is cheaper than a fact lost for good. Everything already saved will "
            "come back as 'already present' — propose it anyway rather than deciding "
            "here what was kept.",
            "",
            replay,
            "",
            "### New in this batch",
            "",
            history_text,
        ])

    @staticmethod
    def dream_prompt_history(prompt: str) -> str:
        """La sola parte di *storia* di un prompt di Dream, senza il template.

        Serve a chi deve decidere qualcosa **sul batch** — oggi
        ``dream_cycle.batch_carries_retained_facts`` — e vive qui perché qui il
        prompt viene incollato: dove finisce il template e comincia la storia è
        una cosa che sa un modulo solo.

        E non è una comodità. Il template di Dream *nomina* i tag di ritenzione
        (``[durable]``, ``[permanent]``, ``[correction]``) nella sua sezione
        "History attribute tags": cercarli nel prompt intero li trova **sempre**,
        su qualunque batch, anche su uno che non ne contiene nessuno. Un predicato
        costruito così è vero per costruzione, cioè non è un predicato. Da qui il
        ritaglio, e il test che lo prova su un batch senza tag.

        Su un prompt che l'header non contiene ritorna stringa vuota: chi non ha
        storia non ha batch, ed è la risposta conservativa giusta.
        """
        _, _, history = prompt.partition(DREAM_HISTORY_HEADER)
        return history

    def build_dream_tools(
        self,
        *,
        write_size_guard: Callable[[Path, str], str | None] | None = None,
        scope: str = "personal",
    ):
        """Build the restricted tool registry used by Dream runs.

        *scope* dice **su che tipo di batch** gira questo run, e cambia la
        cassetta invece del prompt (08/09/2026, v. ``.agent/project-memory-plan.md``):

        - ``"personal"`` — il default e il comportamento di sempre: i quattro tool
          file sui tre file di memoria piu' ``skills/``, e il tool per voci su
          entrambe le destinazioni;
        - ``"project"`` — un batch di voci ``project:``. La cassetta e' ``read_file``
          piu' il tool per voci **sulla sola ``USER.md``**, e nient'altro.

        **Perche' togliere i tool invece di dirlo nel prompt.** La regola che
        questo run deve rispettare — da un progetto puo' uscire identita', mai
        inventario — e' la stessa che ``.agent/security.md`` dichiara, e finora
        era garantita da un'*assenza* (un progetto non scriveva in ``history``).
        Aperta quella porta, la garanzia deve stare da qualche parte, e un
        paragrafo in un template non e' una garanzia: e' una richiesta. Con la
        cassetta ridotta, ``memory/MEMORY.md`` e ``SOUL.md`` non sono
        raggiungibili nemmeno da un modello che ci provasse, e la cosa si prova
        con un test invece che con una lettura del prompt.

        Niente ``edit_file``/``apply_patch``/``write_file`` nel ramo di progetto,
        e non e' zelo: quei tre scrivono per *file interi* su una allowlist, e
        ridurre la allowlist a ``USER.md`` lascerebbe comunque tre strade per
        riscriverla tutta quando la strada giusta e' una voce alla volta. Il
        prompt di quel ramo non li nomina, il budget si libera con
        ``memory remove``/``replace``, e ``read_file`` resta perche' leggere non
        e' scrivere.

        Il ``FileStates`` creato per il run viene esposto come attributo
        ``file_states`` del registry restituito: è per-run (nessuna condivisione
        tra Dream concorrenti) e traccia scritture tentate/riuscite, così il
        chiamante può decidere via :meth:`dream_should_advance_cursor` se
        avanzare il cursore.

        *write_size_guard* è il gancio pre-scrittura che impone il budget dei
        file di memoria: riceve il path risolto e il testo che finirebbe su
        disco, ritorna ``None`` per lasciar passare o il messaggio di rifiuto
        da restituire al modello. Il tipo è dichiarato per struttura e non
        importato da chi lo costruisce: questo modulo è I/O puro e non deve
        dipendere dal modulo che misura il budget.
        """
        from jenny.agent.tools.apply_patch import ApplyPatchTool
        from jenny.agent.tools.file_state import FileStates
        from jenny.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
        from jenny.agent.tools.memory_entries import MemoryEntryTool, make_entry_archiver
        from jenny.agent.tools.registry import ToolRegistry

        tools = ToolRegistry()
        file_states = FileStates()
        # Canonicalizza la radice del workspace e i file editabili. Su Android il
        # filesystem espone la dir dati come ``/data/user/0/<pkg>`` ma ``.resolve()``
        # la canonicalizza in ``/data/data/<pkg>``: se la base di risoluzione dei
        # path e la allowlist di file esatti (``extra_write_allowed_files``) restano
        # in forme diverse, il guard anti-symlink di ``_is_path_exactly_allowed``
        # (logico via ``abspath`` vs risolto via ``.resolve()``) scatta e Dream non
        # riesce a scrivere MEMORY/SOUL/USER. Risolvendo entrambi i lati qui le due
        # forme coincidono, senza indebolire la protezione contro gli escape via
        # symlink *interni* al workspace (lì logico e risolto continuano a divergere).
        workspace = self.workspace.resolve()
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        extra_read = [skills_dir] if skills_dir.exists() else None
        # Degradazione al **confine del file**, non dentro un tool. Il review pass
        # pota con ``apply_patch``/``edit_file`` sui file interi — gli serve per
        # ristrutturare — quindi una difesa che vivesse solo in ``memory remove``
        # lascerebbe scoperto proprio lo scrittore i cui errori sono definitivi
        # (v. 2.4b del piano). Passandolo a tutti e quattro, qualunque cosa
        # riscriva USER.md o memory/MEMORY.md fa passare dall'archivio le voci che
        # sta per togliere, senza che nessuno debba ricordarsene.
        entry_archiver = make_entry_archiver(workspace)
        is_project_scope = scope == "project"
        editable_files = (
            [self.user_file.resolve()]
            if is_project_scope
            else [
                self.memory_file.resolve(),
                self.soul_file.resolve(),
                self.user_file.resolve(),
            ]
        )

        # Il guard va a tutti e quattro, ``ReadFileTool`` compreso, dove oggi
        # non fa nulla: è il costruttore che decide cosa passa, non un elenco
        # di tool "scrivibili" tenuto a mano. Un tool che domani diventasse
        # write-capable — o un ``read_file`` con un ``--write-back`` — sfuggirebbe
        # al budget solo perché nessuno si è ricordato di aggiungerlo qui.
        # **Nessun ``read_media_dir=False`` qui, e non per dimenticanza** (T9.10).
        # T9.2 l'ha spento nella cassetta del giardiniere, la cui radice di lettura
        # è *un progetto*: là ``<workspace>/.jenny/media`` cadeva fuori dal confine
        # dichiarato e il flag lo riportava dentro. Qui la radice di lettura è il
        # **workspace intero**, e la media dir sta dentro il workspace — misurato:
        # con e senza il flag, questo ``read_file`` apre ``.jenny/media/...``
        # identicamente. Metterlo sarebbe un placebo: una riga che fa *sembrare*
        # un confine quel che non ne mette nessuno. L'argomento con cui T9.2 ha
        # chiuso il giardiniere — «una passata a cui nessuno chiede niente non ha
        # bisogno di guardare un'immagine» — vale anche qui: semplicemente non ha
        # niente da spegnere.
        #
        # E chiudere davvero quella cartella a Dream vuol dire stringere
        # ``allowed_dir``, che è un'altra decisione e più grossa di così: sotto
        # questa radice ci sono anche ``wikis/<progetto>/**`` — le pagine, i
        # diari, gli ``AGENTS.md`` di ogni progetto — quindi la media dir non
        # aggiunge un verso che la radice non conceda già. Il confine
        # progetto → personale che T7.8 ha misurato solido è quello della
        # **cronologia** (il cancello di ``append_history`` più il filtro in
        # ``build_dream_prompt``), non quello della cassetta: chi volesse
        # chiuderlo anche qui deve partire da ``wikis/``, non da ``.jenny/media``.
        tools.register(ReadFileTool(
            workspace=workspace,
            allowed_dir=workspace,
            extra_read_allowed_dirs=extra_read,
            file_states=file_states,
            write_size_guard=write_size_guard,
            entry_archiver=entry_archiver,
        ))
        tools.register(EditFileTool(
            workspace=workspace,
            allowed_dir=skills_dir,
            extra_write_allowed_files=editable_files,
            file_states=file_states,
            write_size_guard=write_size_guard,
            entry_archiver=entry_archiver,
        ))
        tools.register(ApplyPatchTool(
            workspace=workspace,
            allowed_dir=skills_dir,
            extra_write_allowed_files=editable_files,
            file_states=file_states,
            write_size_guard=write_size_guard,
            entry_archiver=entry_archiver,
        ))
        # ``extra_write_allowed_files`` anche qui, e non è simmetria per il gusto
        # della simmetria: senza, il registry smentiva il prompt. ``dream.md``
        # dice "your registry allows exactly ``SOUL.md``, ``USER.md``,
        # ``memory/MEMORY.md`` and ``skills/<name>/SKILL.md``", e ``SOUL.md`` —
        # prosa senza tool per voci, che il review pass deve accorciare — non
        # aveva altro modo naturale che ``write_file``. La risposta era un
        # ``WorkspaceBoundaryError`` con in coda "do not retry with alternative
        # tools": un vicolo chiuso, con il tentativo già contato, quindi cursore
        # fermo e ``stuck`` in salita — il "rifiuto di *path*" che il commento di
        # ``format_stuck_alarm`` dice di aver visto sul Titan 2 con tutti i file
        # all'81% o meno.
        #
        # Non allarga il perimetro: sono gli stessi tre file che ``edit_file`` e
        # ``apply_patch`` qui sopra riscrivono già interi. Cambia due cose, entrambe
        # volute. La scrittura passa da ``atomic_write`` (``_commit_write`` decide
        # su ``_is_exact_allowed_file``), come per gli altri due e per la stessa
        # ragione: è stato che Jenny rilegge da sé, e un processo ucciso a metà
        # lascerebbe un file troncato che si legge come integro. E l'archivio delle
        # voci in uscita continua a valere, perché ``entry_archiver`` è per-tool e
        # ``WriteFileTool.execute`` chiama ``_archive_departing`` prima di scrivere
        # esattamente come gli altri: una riscrittura intera di ``MEMORY.md`` non
        # scavalca la degradazione.
        #
        # Che riscrivere quei file per intero sia comunque la strada peggiore per
        # i due file a voci resta detto dove va detto, cioè nel prompt ("Propose
        # entries, do not rewrite files"): è una preferenza, e una preferenza si
        # insegna, non si trasforma in un vicolo cieco.
        tools.register(WriteFileTool(
            workspace=workspace,
            allowed_dir=skills_dir,
            extra_write_allowed_files=editable_files,
            file_states=file_states,
            write_size_guard=write_size_guard,
            entry_archiver=entry_archiver,
        ))
        # Scrittura per voce su USER.md e memory/MEMORY.md, accanto — non al posto
        # — dei tool sopra: il review pass ristruttura davvero quei file, e per
        # farlo gli serve riscriverli interi. Quello che cambia è il turno
        # incrementale, dove aggiungere un fatto smette di essere una riscrittura
        # e diventa un'aggiunta, con un id da citare.
        #
        # Montato qui e non in ``_HARDCODED_TOOL_MODULES``: quella lista è il
        # registry dell'agente principale, e la decisione del 2026-08-18 è di
        # dare il tool prima a Dream soltanto. Se ha un difetto, scoprirlo in un
        # run notturno costa un batch rigiocato; scoprirlo in chat costa il turno
        # dell'utente.
        memory_entries = MemoryEntryTool(
            workspace,
            file_states=file_states,
            write_size_guard=write_size_guard,
            entry_archiver=entry_archiver,
            allowed_targets={"user"} if is_project_scope else None,
        )
        tools.register(memory_entries)
        if is_project_scope:
            # I tre scrittori di file interi via, e per esclusione invece che per
            # costruzione condizionale: le loro registrazioni qui sopra portano
            # ciascuna il proprio ragionamento, e infilarle dentro un ``if`` per
            # una variante avrebbe spostato tre argomenti dentro il ramo di un
            # quarto. Un elenco di nomi accanto alla ragione si legge; tre blocchi
            # rientrati no.
            #
            # ``read_file`` resta: leggere non e' scrivere, e questo run legge il
            # workspace intero come tutti gli altri (v. il commento di T9.10).
            for whole_file_writer in ("edit_file", "apply_patch", "write_file"):
                tools.unregister(whole_file_writer)
        # Esposto per ``dream_should_advance_cursor``: stesso oggetto usato da
        # tutti i tool sopra (passato esplicitamente ai costruttori), quindi
        # riflette le scritture del run.
        tools.file_states = file_states
        # Esposto come ``file_states`` e per la stessa ragione: il chiamante deve
        # poter chiedere com'è andato il run senza rifare le misure. Qui però la
        # risposta è in voci — quante ne sono entrate, quante sostituite, quante
        # erano già lì — che è ciò che rende ``batch_was_not_consolidated`` una
        # verifica invece di una stima.
        tools.memory_entries = memory_entries
        return tools

    # Le tre regole dei run interni vivono in ``jenny/agent/internal_run.py``:
    # non sono I/O sui file di memoria (il giardiniere non ne apre
    # nessuno), e stavano qui solo perché Dream è stato il primo a servirsene.
    # Restano raggiungibili da ``MemoryStore`` come alias: i test e
    # ``docs/internals/architecture.md`` le nominano così, e questo spostamento
    # non compra un rename di massa.
    internal_run_completed = staticmethod(_internal_run_completed)
    internal_run_should_commit = staticmethod(_internal_run_should_commit)
    prune_internal_sessions = staticmethod(_prune_internal_sessions)

    @staticmethod
    def dream_run_completed(resp: object | None) -> bool:
        """Return True only when an ephemeral Dream agent turn completed cleanly."""
        return MemoryStore.internal_run_completed(resp)

    @staticmethod
    def dream_should_advance_cursor(
        resp: object | None,
        file_states: object | None,
    ) -> bool:
        """Return True only when the Dream cursor may safely advance.

        Alias di :meth:`internal_run_should_commit` con il nome che Dream usa: il
        cursore su ``history.jsonl`` è il "progresso" di cui quella regola parla.
        Il perché sta lì, in un posto solo — questa funzione ne ha portato per
        mesi una seconda copia da 28 righe, ed è la forma di documentazione che
        drifta prima di qualunque altra.
        """
        return MemoryStore.internal_run_should_commit(resp, file_states)

    # -- message formatting utility ------------------------------------------

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        lines = []
        for message in messages:
            if not message.get("content"):
                continue
            tools = f" [tools: {', '.join(message['tools_used'])}]" if message.get("tools_used") else ""
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {message['role'].upper()}{tools}: {message['content']}"
            )
        return "\n".join(lines)

    def raw_archive(
        self,
        messages: list[dict],
        *,
        max_chars: int | None = None,
        session_key: str | None = None,
        prompt_visible: bool = True,
    ) -> None:
        """Fallback: dump raw messages to history.jsonl without LLM summarization.

        ``prompt_visible`` viaggia fino a :meth:`append_history` per la stessa
        ragione che vale la': questa e' la strada che ``/new`` prende quando la
        chiamata di riassunto fallisce, e un dump grezzo della conversazione
        appena azzerata nel prompt del turno dopo e' il difetto in versione
        peggiore, non in versione attenuata.
        """
        limit = max_chars if max_chars is not None else _RAW_ARCHIVE_MAX_CHARS
        formatted = truncate_text(self._format_messages(messages), limit)
        self.append_history(
            f"[RAW] {len(messages)} messages\n"
            f"{formatted}",
            session_key=session_key,
            prompt_visible=prompt_visible,
        )
        logger.warning(
            "Memory consolidation degraded: raw-archived {} messages", len(messages)
        )

    # ------------------------------------------------------------------
    # Dream helpers
    # ------------------------------------------------------------------

    @staticmethod
    def dream_session_key() -> str:
        """Return a unique session key for a Dream run, e.g. ``dream:20260528-100000``."""
        return f"{DREAM_SESSION_PREFIX}{datetime.now():%Y%m%d-%H%M%S}"

    @classmethod
    def prune_dream_sessions(cls, sessions_dir: Path, *, keep: int = 10) -> list[str]:
        """Remove the oldest Dream session files, keeping only the N most recent."""
        return cls.prune_internal_sessions(sessions_dir, "dream", keep=keep)


# ---------------------------------------------------------------------------
# Consolidator — lightweight token-budget triggered consolidation
# ---------------------------------------------------------------------------

# Individual history.jsonl writers cap their own payloads tightly; the
# _HISTORY_ENTRY_HARD_CAP at append_history() is a belt-and-suspenders default
# that catches any new caller that forgot to set its own cap.
_RAW_ARCHIVE_MAX_CHARS = 16_000       # fallback dump (LLM failed)
_ARCHIVE_SUMMARY_MAX_CHARS = 8_000    # LLM-produced consolidation summary
_HISTORY_ENTRY_HARD_CAP = 64_000      # emergency cap in append_history


# Consolidator vive in ``consolidator.py`` (importa MemoryStore qui sopra).
# Re-export in coda per preservare l'API storica; a questo punto MemoryStore
# e le costanti sono già definite, quindi l'import di ritorno non trova un
# modulo a metà inizializzazione.
from jenny.agent.consolidator import Consolidator as Consolidator  # noqa: E402
