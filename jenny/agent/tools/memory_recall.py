"""Recupero dal tier freddo: come si ritrova un fatto che è stato degradato.

La fase 2 ha reso la cancellazione una degradazione, e ha mantenuto la promessa
sul disco — 16 voci spostate in un solo pomeriggio, zero perse. Ma "non è perso"
è un'affermazione sul filesystem, non su ciò che Jenny sa.

**Due modi di entrare, e nessuno sostituisce l'altro.** Senza ``query`` l'elenco
torna intero, a pagine, e a scegliere è il modello: è il modo che regge il
bilinguismo, perché un fatto scritto in italiano si riconosce anche da una
domanda in inglese. Con ``query`` il tool cerca per sottostringa le parole che il
modello passa, in tutte e due le lingue (v. ``jenny.agent.memory_search``). La
ricerca è arrivata quando l'elenco ha smesso di bastare: sul telefono, il
12/10/2026, la prima pagina mostrava 119 fatti archiviati su 479, gli ultimi
otto giorni. Una ricerca vuota non dice mai "non c'è": dice di provare altre
parole, o di sfogliare.

**Una riga per fatto, non per formulazione.** Una voce riscritta lascia in
archivio la versione vecchia, legata alla nuova (``superseded_by``): 271 file su
479 erano versioni di 65 fatti. L'elenco ne mostra la più recente, col numero
delle precedenti; aprirla le mostra tutte, e la formulazione viva se c'è. Un
fatto smentito (``retracted``) si presenta come tale.

**Nessun secondo modello, e niente ``spawn``.** ``spawn`` è asincrono per
contratto, e un recupero che risponde tre turni dopo non è un recupero.

**Nessun indice su disco.** L'elenco si deriva dalla cartella a ogni chiamata:
un file di indice sarebbe una seconda verità da tenere allineata.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.agent.memory_archive import ArchivedEntry, history_archive_dir, list_archived
from jenny.agent.memory_search import query_terms, search
from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.memory_entries import MEMORY_TARGETS, parse_entries
from jenny.agent.tools.schema import (
    ArraySchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from jenny.security.workspace_access import current_tool_workspace

# Una pagina dell'elenco, in caratteri. Sotto i 16.000 di ``max_tool_result_chars``
# di proposito: oltre, il risultato finisce in un file e il modello ne vede solo
# l'anteprima — successo sull'emulatore il 12/10/2026 con la pagina da 24.000.
_INDEX_MAX_CHARS = 12_000

# Quante voci trovate si mostrano al più: le altre si dicono, non si tacciono.
_SEARCH_MAX_RESULTS = 40

_QUERY_DESCRIPTION = (
    "Words to search for. Give them in both Italian and English, with synonyms "
    "(e.g. [\"boat\", \"barca\", \"sailing\", \"vela\"]): matching is by word, "
    "and the memory is written in both languages. Omit to browse instead."
)
_PAGE_DESCRIPTION = "Page of the full list when browsing without a query, from 1 (the newest)."
_NO_MATCH = (
    "Nothing matched {terms}. That does not mean it was never said: try other words, "
    "the other language, or browse the list without a query."
)


@dataclass
class _Chain:
    """Le formulazioni archiviate di uno stesso fatto, dalla più recente."""

    versions: list[ArchivedEntry] = field(default_factory=list)

    @property
    def latest(self) -> ArchivedEntry:
        return self.versions[0]


def _chains(entries: list[ArchivedEntry]) -> list[_Chain]:
    """Raggruppa le voci per catena di ``superseded_by``, nell'ordine dell'archivio.

    Ogni voce segue i legami finché restano in archivio; dove escono (la voce
    nuova è viva, o non c'è più) quella è la testa della catena. Un ciclo — la
    stessa formulazione tolta, rimessa e ritolta — si ferma alla prima voce già
    vista. La catena prende il posto della sua voce più recente nell'elenco.
    """
    by_id = {e.id: e for e in entries}

    def head(entry: ArchivedEntry) -> str:
        seen = {entry.id}
        current = entry
        while current.superseded_by in by_id and current.superseded_by not in seen:
            current = by_id[current.superseded_by]
            seen.add(current.id)
        return current.id

    # ``entries`` arriva già dalla più recente, e un dict tiene l'ordine
    # d'inserimento: ogni catena prende il posto della sua prima voce vista.
    groups: dict[str, _Chain] = {}
    for entry in entries:
        groups.setdefault(head(entry), _Chain()).versions.append(entry)
    return list(groups.values())


def _first_line(text: str) -> str:
    stripped = text.strip()
    return stripped.splitlines()[0] if stripped else ""


def _status(entry: ArchivedEntry) -> str:
    if entry.retracted:
        return " [RETRACTED: the user said this was never true]"
    if entry.superseded_by:
        return " [replaced by a newer wording]"
    return ""


def _index_line(chain: _Chain) -> str:
    """Una riga per fatto: l'id per aprirlo, e la formulazione più recente.

    Il fatto viene troncato a riga, non a carattere: una voce multi-riga si
    presenta con la prima, che è quella che la introduce.
    """
    entry = chain.latest
    when = f" [{entry.demoted}]" if entry.demoted else ""
    older = len(chain.versions) - 1
    more = f" (+{older} earlier wording{'s' if older > 1 else ''})" if older else ""
    return f"- {entry.id}{when} {_first_line(entry.text)}{more}{_status(entry)}"


def _render_entry(entry: ArchivedEntry) -> str:
    """Una voce aperta, col suo indirizzo di provenienza e il suo stato."""
    where = " › ".join(p for p in (entry.source, entry.heading) if p)
    head = f"{entry.id}"
    if where:
        head += f" — from {where}"
    if entry.demoted:
        head += f", demoted {entry.demoted}"
    return f"{head}{_status(entry)}\n{entry.text.strip()}"


def _pages(lines: list[str]) -> list[list[str]]:
    pages: list[list[str]] = [[]]
    used = 0
    for line in lines:
        if pages[-1] and used + len(line) + 1 > _INDEX_MAX_CHARS:
            pages.append([])
            used = 0
        pages[-1].append(line)
        used += len(line) + 1
    return pages


def _page_number(page: Any) -> int:
    try:
        return max(1, int(page))
    except (TypeError, ValueError):
        return 1


@tool_parameters(
    tool_parameters_schema(
        query=ArraySchema(StringSchema("A word to search for"), description=_QUERY_DESCRIPTION),
        ids=ArraySchema(
            StringSchema("An entry id from the list"),
            description="Ids to open in full, with every earlier wording.",
        ),
        page=IntegerSchema(description=_PAGE_DESCRIPTION, minimum=1),
    )
)
class MemoryRecallTool(Tool):
    """Cerca o sfoglia l'archivio, e apre le voci che il modello ha riconosciuto."""

    # Solo l'agente principale. È lì che succede "ti ricordi quando…", ed è un
    # tool di sola lettura. A Dream non va: il suo prompt si è già dimostrato
    # sensibile alla superficie che gli si aggiunge.
    _scopes = {"core", "orchestrator"}

    def __init__(self, workspace: str | Path):
        self._root = Path(workspace)
        self._memory_dir = self._root / "memory"

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(workspace=ctx.workspace)

    @property
    def name(self) -> str:
        return "recall"

    @property
    def description(self) -> str:
        return (
            "Search long-term memory for facts that were removed from the active "
            "files and moved to the archive. Pass `query` with words in both Italian "
            "and English to search it; omit it to browse every archived fact, one "
            "line each, newest first, a page at a time. Then call it again with the "
            "ids you want in full. "
            "Use it before telling the user you do not remember something, and "
            "whenever a question is about the past — what was decided, what "
            "something used to be, why a thing was set up that way. "
            "A fact marked RETRACTED was never true; one marked as replaced has a "
            "newer wording. "
            "This holds facts that were kept and later demoted; for details of a "
            "past conversation that Dream did not keep, use recall_history."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        ids: list[str] | None = None,
        query: list[str] | None = None,
        page: int | None = None,
        **kwargs: Any,
    ) -> str:
        entries = list_archived(self._memory_dir)
        if not entries:
            return (
                "The memory archive is empty: no fact has been demoted yet. "
                "Nothing was lost — there is simply nothing here to recall."
            )
        chains = _chains(entries)
        if ids:
            return self._open(chains, ids)
        if query:
            return self._search(chains, query)
        return self._list(chains, _page_number(page))

    def _live_entries(self) -> dict[str, tuple[str, str]]:
        """Le voci vive dei due file a voci: id → (file, testo)."""
        out: dict[str, tuple[str, str]] = {}
        for rel in MEMORY_TARGETS.values():
            try:
                text = (self._root / rel).read_text(encoding="utf-8")
            except OSError:
                continue
            for entry in parse_entries(text):
                out[entry.id] = (rel, entry.text)
        return out

    def _open(self, chains: list[_Chain], ids: list[str]) -> str:
        wanted = [str(i).strip() for i in ids if str(i).strip()]
        by_id = {e.id: chain for chain in chains for e in chain.versions}
        live = self._live_entries()
        parts: list[str] = []
        opened: set[int] = set()
        for i in wanted:
            chain = by_id.get(i)
            if chain is None or id(chain) in opened:
                continue
            opened.add(id(chain))
            block = [_render_entry(chain.latest)]
            current = live.get(chain.latest.superseded_by)
            if current is not None:
                block.append(f"Current wording, in {current[0]}: {current[1].lstrip('-* ').strip()}")
            if len(chain.versions) > 1:
                block.append("Earlier wordings, newest first:")
                block += [_render_entry(e) for e in chain.versions[1:]]
            parts.append("\n".join(block))
        missing = [i for i in wanted if i not in by_id]
        if missing:
            # Un id che non esiste si dice, non si ignora: silenziosamente
            # restituire "solo le altre" farebbe concludere al modello che quel
            # fatto non c'è, che è la bugia precisa da cui parte tutto questo.
            parts.append(
                f"No archived entry has id {', '.join(missing)}. "
                "Call recall with no arguments to see the current ids."
            )
        return "\n\n".join(parts)

    def _search(self, chains: list[_Chain], query: list[str]) -> str:
        terms = query_terms(query)
        if not terms:
            return "The query has no word of three letters or more to search for."
        # Un documento per catena, con tutte le sue formulazioni: un fatto si
        # trova anche se la parola c'era solo in una versione vecchia.
        docs = ["\n".join(e.text for e in chain.versions) for chain in chains]
        ranked = search(docs, terms)
        if not ranked:
            return _NO_MATCH.format(terms=", ".join(terms))
        shown = [_index_line(chains[i]) for i, _, _ in ranked[:_SEARCH_MAX_RESULTS]]
        header = (
            f"{len(ranked)} of {len(chains)} archived facts match {', '.join(terms)}, "
            "best match first. Call recall again with the ids you want in full."
        )
        if len(ranked) > len(shown):
            header += f" Only the first {len(shown)} are listed."
        return header + "\n\n" + "\n".join(shown)

    def _list(self, chains: list[_Chain], page: int) -> str:
        pages = _pages([_index_line(c) for c in chains])
        page = min(page, len(pages))
        lines = pages[page - 1]
        header = (
            f"{len(chains)} facts have been moved out of the active memory files "
            "and kept here. Newest first. Call recall again with the ids you want "
            "in full, or with `query` to search."
        )
        if len(pages) > 1:
            # Mai troncare in silenzio: un elenco tagliato senza dirlo è
            # indistinguibile da un archivio più piccolo.
            header += f" Page {page} of {len(pages)}; pass `page` for the others."
            if page == 1:
                logger.info(
                    "Memory archive index spans {} pages over {} facts", len(pages), len(chains),
                )
        return header + "\n\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Gli appunti sulla conversazione: history.jsonl
# ---------------------------------------------------------------------------
#
# Perché serve un secondo tool e non basta ``recall``. Sono due popolazioni
# diverse, e la differenza si misura sul telefono: l'archivio contiene **fatti
# retrocessi** dai file di identità (``source: USER.md``, ``demoted:`` nel
# frontmatter), cioè roba che Dream ha guardato, giudicato degna e poi spostata
# quando il file cresceva. ``history.jsonl`` sono gli appunti del consolidatore,
# una voce per ogni tratto di conversazione compattato: riassunti e non le parole
# dette, ma presi prima del giudizio di Dream.
#
# E il pezzo che nessuno legge è il **secondo**, non il primo. Le voci oltre il
# cursore di Dream finiscono già nel prompt di ogni turno
# (``read_recent_history_for_prompt``, tetto a 50 voci / 8.000 token); misurato
# il 29/08 erano 2 su 108. Le altre 106 stanno sotto il cursore: fuori dal
# prompt, e non nell'archivio, perché l'archivio non raccoglie righe di
# cronologia. Sono il verbale di ciò che è stato detto dopo che Dream ne ha
# tratto una sintesi — ed è lì che vive il dettaglio che la sintesi ha perso.
#
# **Cerca anche per parola, ma non solo.** Come ``recall``: con ``query`` cerca le
# parole che il modello passa fra le singole note — la nota che contiene la
# parola, non la prima della voce, che è quella che l'indice mostra — e senza
# ``query`` l'elenco torna intero, a pagine, perché una voce scritta in italiano
# resti riconoscibile a una domanda posta in inglese.

_HISTORY_ENTRY_MAX_CHARS = 12_000
_HISTORY_SNIPPET_CHARS = 110
# Una nota trovata si mostra intera fino a qui: le note sono righe del
# consolidatore, di solito sotto i 300 caratteri.
_HISTORY_NOTE_MAX_CHARS = 600


def _read_history(memory_dir: Path) -> list[dict[str, Any]]:
    """Legge la cronologia intera, dalla voce più recente alla più vecchia.

    Intera vuol dire ``history.jsonl`` più ``history-archive/*.jsonl``, dove
    ``MemoryStore.compact_history`` sposta quel che esce dal tetto: per chi
    chiede "ti ricordi?" sono la stessa cronologia, e un cursore archiviato
    che risponde "non esiste" sarebbe una bugia. Un cursore presente in tutti e
    due i posti (un crash fra l'archiviazione e la riscrittura) conta una volta.

    Lettore locale e non ``MemoryStore``: l'unico che quella classe espone è
    ``read_recent_history_for_prompt``, che è sagomato per il blocco del prompt
    (parte da un cursore, filtra per tipo di sessione, applica i suoi tetti).
    Qui serve tutto.

    Una riga illeggibile si salta invece di far fallire la lettura: un file
    append-only troncato a metà da uno spegnimento non deve nascondere le
    centoquattro righe sane che lo precedono.
    """
    memory_dir = Path(memory_dir)
    paths = sorted(history_archive_dir(memory_dir).glob("*.jsonl"))
    paths.append(memory_dir / "history.jsonl")
    by_cursor: dict[Any, dict[str, Any]] = {}
    for path in paths:
        if not path.exists():
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("{} illeggibile: {}", path.name, exc)
            continue
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("content"):
                by_cursor[rec.get("cursor")] = rec
    out = list(by_cursor.values())
    out.sort(key=_cursor_key, reverse=True)
    return out


def _cursor_key(rec: dict[str, Any]) -> int:
    cursor = rec.get("cursor")
    return cursor if isinstance(cursor, int) else 0


def _history_index_line(rec: dict[str, Any]) -> str:
    """Una riga per voce: il cursore per riaprirla, e abbastanza per riconoscerla."""
    content = str(rec.get("content", "")).strip()
    notes = [n.strip() for n in content.splitlines() if n.strip()]
    head = notes[0] if notes else ""
    head = head.lstrip("- ").strip()
    if len(head) > _HISTORY_SNIPPET_CHARS:
        head = head[: _HISTORY_SNIPPET_CHARS - 1].rstrip() + "…"
    # Il conteggio delle altre note non è decorazione: tre quarti delle voci ne
    # hanno più di una (74 su 108, misurate), quindi senza questo il modello
    # crederebbe che la voce sia solo la sua prima riga.
    extra = f" (+{len(notes) - 1})" if len(notes) > 1 else ""
    return f"[{rec.get('cursor', '?')}] {rec.get('timestamp', '')} {head}{extra}"


@tool_parameters(
    tool_parameters_schema(
        query=ArraySchema(StringSchema("A word to search for"), description=_QUERY_DESCRIPTION),
        cursors=ArraySchema(
            StringSchema("Cursor of an entry from the index, e.g. '120'"),
            description="Open these entries in full.",
        ),
        page=IntegerSchema(description=_PAGE_DESCRIPTION, minimum=1),
    )
)
class HistoryRecallTool(Tool):
    """Elenca gli appunti della conversazione, e apre le voci che il modello ha riconosciuto."""

    # Gli stessi di ``recall``, e per la stessa ragione: è lì che succede "ti
    # ricordi quando…". A Dream non va — il suo prompt si è già dimostrato
    # sensibile alla superficie aggiunta, e la cronologia ce l'ha in ingresso.
    _scopes = {"core", "orchestrator"}

    def __init__(self, workspace: str | Path):
        self._root = Path(workspace)
        self._memory_dir = self._root / "memory"

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(workspace=ctx.workspace)

    @property
    def name(self) -> str:
        return "recall_history"

    @property
    def description(self) -> str:
        return (
            "Search the notes taken on the personal conversation: one entry per stretch "
            "that was compacted, written before Dream chose what to keep. They are "
            "summaries, not the exact words said. Pass `query` with words in both Italian "
            "and English to get the notes that contain them; omit it to browse the index, "
            "one line per entry, newest first, a page at a time. Then call it again with "
            "the cursors you want in full. Use it when recall came back with nothing and "
            "the question is about a detail rather than a fact: a number, a decision, what "
            "was tried before. recall holds facts that were kept and later demoted from the "
            "identity files; this holds the notes on everything, kept or not."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        cursors: list[str] | None = None,
        query: list[str] | None = None,
        page: int | None = None,
        **kwargs: Any,
    ) -> str:
        if (refusal := self._project_refusal()) is not None:
            return refusal
        entries = _read_history(self._memory_dir)
        if not entries:
            return (
                "The conversation log is empty: there is no history.jsonl yet, or it holds "
                "no entries. Nothing was lost — there is simply nothing here to read."
            )
        if cursors:
            return self._open(entries, cursors)
        if query:
            return self._search(entries, query)
        return self._list(entries, _page_number(page))

    def _project_refusal(self) -> str | None:
        """Dentro un progetto questo tool tace, e lo dice.

        Non è privacy — è lo stesso utente da entrambe le parti. È che una
        sessione di progetto **scrive dentro il progetto**: pagine di wiki,
        diario, file che restano e che a volte stanno in un repository pubblico.
        ``recall`` può viaggiare perché restituisce fatti che Dream ha filtrato e
        ritenuto durevoli; gli appunti non passano da quel filtro, e comprendono
        quello che è stato detto di sfuggita.

        È anche la direzione in cui il resto del codice si muove già: il ramo
        progetto di ``read_recent_history_for_prompt`` toglie questa coda dal
        prompt di un progetto, e la modifica al giardiniere del 23/08 le ha
        tolto la metà personale per la stessa forma di problema.

        Allargare dopo è una riga; restringere dopo è togliere qualcosa a cui ci
        si è abituati.
        """
        scoped = current_tool_workspace(self._root).project_path
        if scoped is None:
            return None
        try:
            same = Path(scoped).resolve() == self._root.resolve()
        except OSError:
            same = False
        if same:
            return None
        return (
            "recall_history only reads the personal conversation, and this turn is scoped to "
            "a project. Use recall for durable facts, or ask in the personal chat if the "
            "exact wording matters."
        )

    def _open(self, entries: list[dict[str, Any]], cursors: list[str]) -> str:
        wanted = [str(c).strip() for c in cursors if str(c).strip()]
        by_cursor = {str(e.get("cursor")): e for e in entries}
        parts: list[str] = []
        used = 0
        shown: list[str] = []
        for c in wanted:
            rec = by_cursor.get(c)
            if rec is None:
                continue
            block = f"[{rec.get('cursor')}] {rec.get('timestamp', '')}\n{rec.get('content', '')}"
            if used + len(block) > _HISTORY_ENTRY_MAX_CHARS:
                parts.append(
                    f"(stopped at {len(shown)} of {len(wanted)} entries: the rest would not "
                    "fit in one answer — ask for fewer cursors)"
                )
                break
            parts.append(block)
            used += len(block)
            shown.append(c)
        missing = [c for c in wanted if c not in by_cursor]
        if missing:
            # Un cursore che non esiste si dice, non si ignora: restituire in
            # silenzio "solo le altre" fa concludere che quella voce non c'è.
            parts.append(
                f"No entry has cursor {', '.join(missing)}. "
                "Call recall_history with no arguments to see the current index."
            )
        return "\n\n".join(parts) if parts else "Nothing to show."

    def _search(self, entries: list[dict[str, Any]], query: list[str]) -> str:
        terms = query_terms(query)
        if not terms:
            return "The query has no word of three letters or more to search for."
        # Il documento è la nota, non la voce: una voce porta tre o quattro fatti
        # diversi, e chi cerca il veterinario vuole la riga del veterinario.
        notes: list[tuple[int, str]] = []
        for index, rec in enumerate(entries):
            for line in str(rec.get("content", "")).splitlines():
                if line.strip():
                    notes.append((index, line.strip()))
        ranked = search([text for _, text in notes], terms)
        if not ranked:
            return _NO_MATCH.format(terms=", ".join(terms))
        by_entry: dict[int, list[str]] = {}
        for i, _, _ in ranked:
            index, text = notes[i]
            by_entry.setdefault(index, []).append(text)
        blocks: list[str] = []
        used = 0
        for index, found in by_entry.items():  # nell'ordine della nota migliore
            rec = entries[index]
            block = f"[{rec.get('cursor', '?')}] {rec.get('timestamp', '')}\n" + "\n".join(
                n[:_HISTORY_NOTE_MAX_CHARS] for n in found
            )
            if blocks and used + len(block) > _INDEX_MAX_CHARS:
                break
            blocks.append(block)
            used += len(block)
        header = (
            f"{len(ranked)} notes in {len(by_entry)} of {len(entries)} turns match "
            f"{', '.join(terms)}, best match first; each turn shows only its matching notes. "
            "Call recall_history again with the cursors you want in full."
        )
        if len(blocks) < len(by_entry):
            header += f" Only the first {len(blocks)} turns fit here."
        return header + "\n\n" + "\n\n".join(blocks)

    def _list(self, entries: list[dict[str, Any]], page: int) -> str:
        pages = _pages([_history_index_line(rec) for rec in entries])
        page = min(page, len(pages))
        header = (
            f"The conversation log holds {len(entries)} turns, newest first. "
            "Each line is one turn: [cursor] timestamp, the first note, and how many more "
            "it carries. Call recall_history again with the cursors you want in full, or "
            "with `query` to search every note."
        )
        if len(pages) > 1:
            # Il taglio si dice: una pagina sola senza dirlo è indistinguibile da
            # una cronologia più corta.
            header += f" Page {page} of {len(pages)}; pass `page` for older turns."
        return header + "\n\n" + "\n".join(pages[page - 1])


TOOLS = [MemoryRecallTool, HistoryRecallTool]
