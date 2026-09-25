"""Recupero dal tier freddo: come si ritrova un fatto che è stato degradato.

La fase 2 ha reso la cancellazione una degradazione, e ha mantenuto la promessa
sul disco — 16 voci spostate in un solo pomeriggio, zero perse. Ma "non è perso"
è un'affermazione sul filesystem, non su ciò che Jenny sa: finché l'unico modo
di rientrarci era un ``grep`` che il modello doveva ricordarsi di fare,
l'archivio era un debito che si accumulava senza che nessuno lo aprisse.

Questo modulo lo salda, e tre scelte lo distinguono dal ``grep`` che sostituisce.

**Nessuna corrispondenza per sottostringa, da nessuna parte.** Era il difetto
vero, non un dettaglio: questa memoria è bilingue, e un fatto scritto in
italiano è invisibile a una domanda in inglese sotto ricerca testuale. In più
``grep`` salta i file grandi in silenzio — un falso negativo che si legge
esattamente come "non l'ho mai saputo". Qui l'elenco torna intero e a scegliere
è il modello, che di lingue ne parla due.

**Nessun secondo modello, e niente ``spawn``.** Il piano prevedeva un subagent
sull'indice, e la macchina esiste; ma ``spawn`` è asincrono per contratto — il
risultato arriva da sé, non si aspetta — e un recupero che risponde tre turni
dopo non è un recupero. Soprattutto: chi deve giudicare la salienza è il modello
che ha in mano la conversazione, perché è la conversazione a dire quale dei 41
fatti c'entra. Un subagent guadagna il suo posto solo quando l'indice smette di
stare in un risultato di tool, che è la stessa soglia oltre la quale il piano
prevede gli embedding.

**Nessun indice su disco.** L'elenco si deriva dalla cartella a ogni chiamata.
Un file di indice sarebbe una seconda verità da tenere allineata, e la prima
volta che va fuori sincrono mente esattamente sul punto in cui questo tool deve
essere affidabile. L'archivio è fatto di file piccoli, uno per voce, proprio
perché rileggerli costi poco.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.agent.memory_archive import ArchivedEntry, list_archived
from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import ArraySchema, StringSchema, tool_parameters_schema
from jenny.security.workspace_access import current_tool_workspace

# Tetto dell'elenco, in caratteri. Non è il costo di un turno: è il costo di un
# turno *in cui il modello ha deciso di cercare*, che è raro, quindi può essere
# generoso. A ~90 caratteri per voce copre qualche centinaio di fatti, cioè molto
# oltre lo stato attuale (41).
_INDEX_MAX_CHARS = 24_000

# Sopra questa frazione del tetto si logga. È il segnale che dice *quando*
# costruire il passaggio LLM sull'indice (fase 7.2), invece di indovinarlo: la
# soglia del piano — "quando l'indice smette di stare in un contesto" — non è
# osservabile se nessuno la misura.
_INDEX_CROWDED_SHARE = 0.75


def _index_line(entry: ArchivedEntry) -> str:
    """Una riga per voce: l'id per richiamarla, e il fatto per riconoscerla.

    Il fatto viene troncato a riga, non a carattere: una voce multi-riga si
    presenta con la prima, che è quella che la introduce. Tagliare a metà frase
    darebbe da leggere un fatto diverso da quello archiviato.
    """
    first = entry.text.strip().splitlines()[0] if entry.text.strip() else ""
    when = f" [{entry.demoted}]" if entry.demoted else ""
    return f"- {entry.id}{when} {first}"


def _render_entry(entry: ArchivedEntry) -> str:
    """Una voce aperta, col suo indirizzo di provenienza.

    ``source``/``heading`` sono la ragione per cui l'archivio li conserva: un
    fatto riletto fra sei mesi senza sapere da quale file e da quale sezione
    veniva è una frase senza contesto, e rimetterlo al suo posto diventa un
    indovinello.
    """
    where = " › ".join(p for p in (entry.source, entry.heading) if p)
    head = f"{entry.id}"
    if where:
        head += f" — from {where}"
    if entry.demoted:
        head += f", demoted {entry.demoted}"
    return f"{head}\n{entry.text.strip()}"


@tool_parameters(
    tool_parameters_schema(
        ids=ArraySchema(
            StringSchema("An entry id from the list"),
            description=(
                "Ids to open in full. Omit to list everything in the archive "
                "first — the list is what you pick ids from."
            ),
        ),
    )
)
class MemoryRecallTool(Tool):
    """Elenca l'archivio, e apre le voci che il modello ha riconosciuto."""

    # Solo l'agente principale. È lì che succede "ti ricordi quando…", ed è un
    # tool di sola lettura: non può rompere niente, quindi non serve la cautela
    # che la fase 1.12 aveva usato al contrario (dare prima a Dream ciò che
    # scrive). A Dream non va: il suo prompt si è già dimostrato sensibile alla
    # superficie che gli si aggiunge, e non c'è ancora una misura che dica che
    # gli serve.
    _scopes = {"core", "orchestrator"}

    def __init__(self, workspace: str | Path):
        self._memory_dir = Path(workspace) / "memory"

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
            "files and moved to the archive. Call it with no arguments to see "
            "every archived fact, one line each, then call it again with the ids "
            "you want in full. "
            "Use it before telling the user you do not remember something, and "
            "whenever a question is about the past — what was decided, what "
            "something used to be, why a thing was set up that way. "
            "The list is not filtered by keyword, so a fact stored in one "
            "language is found by a question asked in another. "
            "This holds facts that were kept and later demoted; for the verbatim "
            "wording of a turn, kept or not, use recall_history."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, ids: list[str] | None = None, **kwargs: Any) -> str:
        entries = list_archived(self._memory_dir)
        if not entries:
            return (
                "The memory archive is empty: no fact has been demoted yet. "
                "Nothing was lost — there is simply nothing here to recall."
            )
        if ids:
            return self._open(entries, ids)
        return self._list(entries)

    def _open(self, entries: list[ArchivedEntry], ids: list[str]) -> str:
        wanted = [str(i).strip() for i in ids if str(i).strip()]
        by_id = {e.id: e for e in entries}
        found = [_render_entry(by_id[i]) for i in wanted if i in by_id]
        missing = [i for i in wanted if i not in by_id]
        parts = found
        if missing:
            # Un id che non esiste si dice, non si ignora: silenziosamente
            # restituire "solo le altre" farebbe concludere al modello che quel
            # fatto non c'è, che è la bugia precisa da cui parte tutto questo.
            parts.append(
                f"No archived entry has id {', '.join(missing)}. "
                "Call recall with no arguments to see the current ids."
            )
        return "\n\n".join(parts)

    def _list(self, entries: list[ArchivedEntry]) -> str:
        lines: list[str] = []
        used = 0
        for entry in entries:
            line = _index_line(entry)
            if used + len(line) + 1 > _INDEX_MAX_CHARS:
                break
            lines.append(line)
            used += len(line) + 1

        header = (
            f"{len(entries)} facts have been moved out of the active memory files "
            "and kept here. Newest first. Call recall again with the ids you want "
            "in full."
        )
        body = "\n".join(lines)
        if len(lines) < len(entries):
            # Mai troncare in silenzio: un elenco tagliato senza dirlo è
            # indistinguibile da un archivio più piccolo, ed è il modo in cui
            # ``grep`` falliva.
            body += (
                f"\n\n{len(entries) - len(lines)} older entries are not listed here "
                "because the list hit its size limit. They are still in the archive; "
                "say so if the answer might be among them."
            )
        if used > _INDEX_MAX_CHARS * _INDEX_CROWDED_SHARE:
            logger.info(
                "Memory archive index at {}/{} chars over {} entries: past "
                "{:.0%} of the cap, the point where a selection pass over the "
                "index starts to earn its place (plan phase 7.2)",
                used, _INDEX_MAX_CHARS, len(entries), _INDEX_CROWDED_SHARE,
            )
        return f"{header}\n\n{body}"


# ---------------------------------------------------------------------------
# Il verbale grezzo: history.jsonl
# ---------------------------------------------------------------------------
#
# Perché serve un secondo tool e non basta ``recall``. Sono due popolazioni
# diverse, e la differenza si misura sul telefono: l'archivio contiene **fatti
# retrocessi** dai file di identità (``source: USER.md``, ``demoted:`` nel
# frontmatter), cioè roba che Dream ha guardato, giudicato degna e poi spostata
# quando il file cresceva. ``history.jsonl`` è il verbale turno per turno, prima
# di qualunque giudizio.
#
# E il pezzo che nessuno legge è il **secondo**, non il primo. Le voci oltre il
# cursore di Dream finiscono già nel prompt di ogni turno
# (``read_recent_history_for_prompt``, tetto a 50 voci / 8.000 token); misurato
# il 29/08 erano 2 su 108. Le altre 106 stanno sotto il cursore: fuori dal
# prompt, e non nell'archivio, perché l'archivio non raccoglie righe di
# cronologia. Sono il verbale di ciò che è stato detto dopo che Dream ne ha
# tratto una sintesi — ed è lì che vive il dettaglio che la sintesi ha perso.
#
# **Non cerca per parola, e non è una svista.** È la stessa ragione per cui non
# lo fa ``recall``: questa memoria è bilingue, e una voce scritta in italiano è
# invisibile a una domanda posta in inglese sotto ricerca testuale. Un falso
# negativo qui si legge come "non l'ho mai saputo", che è la bugia precisa da
# evitare. Quindi l'elenco torna intero e a scegliere è il modello.

_HISTORY_ENTRY_MAX_CHARS = 12_000
_HISTORY_SNIPPET_CHARS = 110


def _read_history(memory_dir: Path) -> list[dict[str, Any]]:
    """Legge ``history.jsonl``, dalla più recente alla più vecchia.

    Lettore locale e non ``MemoryStore``: l'unico che quella classe espone è
    ``read_recent_history_for_prompt``, che è sagomato per il blocco del prompt
    (parte da un cursore, filtra per tipo di sessione, applica i suoi tetti).
    Qui serve il file intero.

    Una riga illeggibile si salta invece di far fallire la lettura: un file
    append-only troncato a metà da uno spegnimento non deve nascondere le
    centoquattro righe sane che lo precedono.
    """
    path = Path(memory_dir) / "history.jsonl"
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("history.jsonl illeggibile: {}", exc)
        return []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("content"):
            out.append(rec)
    out.sort(key=lambda r: r.get("cursor", 0), reverse=True)
    return out


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
        cursors=ArraySchema(
            StringSchema("Cursor of an entry from the index, e.g. '120'"),
            description="Open these entries in full. Omit to get the index.",
        ),
    )
)
class HistoryRecallTool(Tool):
    """Elenca il verbale grezzo, e apre le voci che il modello ha riconosciuto."""

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
            "Search the verbatim turn-by-turn log of the personal conversation — what was "
            "actually said, before Dream distilled it. Call it with no arguments to see the "
            "index, one line per turn, then call it again with the cursors you want in full. "
            "Use it when recall came back with nothing and the question is about a detail "
            "rather than a fact: an exact number, the wording of a decision, what was tried "
            "before. recall holds facts that were kept and later demoted from the identity "
            "files; this holds everything, kept or not. "
            "The index is not filtered by keyword, so an entry written in one language is "
            "found by a question asked in another."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, cursors: list[str] | None = None, **kwargs: Any) -> str:
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
        return self._list(entries)

    def _project_refusal(self) -> str | None:
        """Dentro un progetto questo tool tace, e lo dice.

        Non è privacy — è lo stesso utente da entrambe le parti. È che una
        sessione di progetto **scrive dentro il progetto**: pagine di wiki,
        diario, file che restano e che a volte stanno in un repository pubblico.
        ``recall`` può viaggiare perché restituisce fatti che Dream ha filtrato e
        ritenuto durevoli; il verbale non è filtrato da niente, e comprende
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

    def _list(self, entries: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        used = 0
        for rec in entries:
            line = _history_index_line(rec)
            if used + len(line) + 1 > _INDEX_MAX_CHARS:
                break
            lines.append(line)
            used += len(line) + 1

        header = (
            f"The conversation log holds {len(entries)} turns, newest first. "
            "Each line is one turn: [cursor] timestamp, the first note, and how many more "
            "it carries. Call recall_history again with the cursors you want in full."
        )
        if len(lines) < len(entries):
            # Il taglio si dice, e si dice da che parte: le più vecchie sono
            # quelle che cadono, ed è l'unica direzione in cui il taglio non
            # nasconde ciò che si cercava di solito.
            header += (
                f" Only the {len(lines)} most recent fit here; {len(entries) - len(lines)} "
                "older turns are not listed."
            )
        if used > _INDEX_MAX_CHARS * _INDEX_CROWDED_SHARE:
            logger.info(
                "recall_history: index at {:.0%} of its cap ({} entries)",
                used / _INDEX_MAX_CHARS, len(entries),
            )
        return header + "\n\n" + "\n".join(lines)


TOOLS = [MemoryRecallTool, HistoryRecallTool]
