"""Le voci di memoria che l'utente ha corretto: Dream non le riordina.

**Il problema.** ``USER.md`` e ``memory/MEMORY.md`` li riscrive Dream, a ogni
passata e di più nel review pass: accorcia, unisce, riformula. Una voce che
l'utente ha corretto a mano dalla stanza «Cosa ricorda di te» tornerebbe così a
dire, qualche giorno dopo, quel che Dream pensa che dica — e chi l'ha corretta
lo prenderebbe per un difetto dell'app.

**Lo schema è quello di** ``agent/soul_rules.py``: la verità sta fuori da dove
Dream scrive, e si riproietta dopo ogni passata. Qui la verità è una riga della
tabella ``memory_locks`` di ``state.sqlite`` (fuori dal workspace: nessuno
strumento di Dream ci arriva), con il testo della voce, la sua sezione e il
file. Nei Markdown non c'è nessun segno: gli id sono impronte del testo
(``memory_entries.entry_id``), quindi la riga riconosce la voce senza toccarla.

**Tre momenti.**

- *Mentre Dream lavora*: ``MemoryEntryTool`` rifiuta ``replace``/``remove`` su
  una voce col lucchetto; se la toglie uno strumento di file, la rete di
  ``make_entry_archiver`` annota su quale voce è finita (``displaced_by``).
- *Dopo ogni passata* (:func:`reassert`): una voce spostata da Dream torna com'era
  e dov'era, e la formulazione di Dream va in archivio, legata a lei.
- *A inizio ciclo* (:func:`reconcile`): una voce col lucchetto che non c'è più e
  che Dream non ha toccato l'ha cambiata qualcun altro — la chat, l'editor
  dell'officina, la stanza. Quel cambio **non si disfa mai**: il lucchetto segue
  la frase che l'ha sostituita, o cade.

**Il lucchetto cede a una correzione detta in chat.** Se il lotto che Dream
consolida porta un ``[correction]`` (una cosa che l'utente ha detto essere
cambiata), lo strumento per voci lascia passare ``replace``/``remove`` e il
lucchetto si sposta sulla frase nuova. Deciso dall'utente il 25/11/2026, con un
rischio accettato: in quella passata Dream può ritoccare anche un'altra voce col
lucchetto.

Una serratura sola (``memory_entries.ENTRIES_LOCK``) tiene insieme lettura e
scrittura dei due file per lo strumento per voci e per le due funzioni qui sotto.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from jenny.agent.memory_archive import (
    ArchivedEntry,
    archive_entry,
    find_archived,
    read_archived,
    unarchive,
)
from jenny.agent.tools.memory_entries import (
    ENTRIES_LOCK,
    MEMORY_TARGETS,
    Entry,
    add_entry,
    pair_successors,
    parse_entries,
    remove_entry,
    replace_entry,
)
from jenny.db.schema import Schema
from jenny.utils.path import atomic_write

SCHEMA = Schema(
    "memory_locks",
    (
        "CREATE TABLE memory_locks ("
        " source TEXT NOT NULL,"
        " entry_id TEXT NOT NULL,"
        " text TEXT NOT NULL,"
        " heading TEXT NOT NULL DEFAULT '',"
        " locked_at INTEGER NOT NULL,"
        " displaced_by TEXT NOT NULL DEFAULT '',"
        " PRIMARY KEY (source, entry_id));",
    ),
)

# ``displaced_by`` quando Dream ha tolto la voce senza metterne un'altra al suo
# posto: la voce torna in coda alla sua sezione.
NO_SUCCESSOR = "-"

# Le frasi con una lapide (``forgotten``) che una scrittura di Dream ha rimesso,
# per workspace: ``(file, id)``. In memoria e non nel database, di proposito:
# vivono dalla scrittura di Dream al ``reassert`` della stessa passata, nello
# stesso processo. Un processo che muore in mezzo lascia la frase nel file, che
# è il guasto innocuo dei due (la stanza la mostra, e la si toglie di nuovo).
_READDED: dict[str, set[tuple[str, str]]] = {}
_READDED_GUARD = threading.Lock()


@dataclass(frozen=True, slots=True)
class MemoryLock:
    """Una voce col lucchetto: dove sta, cosa dice, e se Dream l'ha spostata.

    ``source`` è il percorso relativo del file (``USER.md``,
    ``memory/MEMORY.md``), lo stesso che l'archivio scrive in ``source:``.
    ``displaced_by`` è vuoto finché Dream non la tocca; poi è l'id della voce che
    ha preso il suo posto, o :data:`NO_SUCCESSOR`.
    """

    source: str
    entry_id: str
    text: str
    heading: str
    locked_at: int
    displaced_by: str = ""


class MemoryLocks:
    """La tabella ``memory_locks`` di un'installazione.

    Senza cache: le righe sono poche (le voci che l'utente ha corretto), e ogni
    lettura va al database, così lo strumento di Dream e la stanza non possono
    vedere due verità diverse.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = Path(workspace)

    def _db(self):
        from jenny import db

        return db.store_database(self._workspace, db.STATE, SCHEMA)

    # -- lettura -------------------------------------------------------------

    def rows(self, source: str | None = None) -> list[MemoryLock]:
        sql = (
            "SELECT source, entry_id, text, heading, locked_at, displaced_by "
            "FROM memory_locks"
        )
        params: tuple[str, ...] = ()
        if source is not None:
            sql += " WHERE source = ?"
            params = (source,)
        sql += " ORDER BY locked_at, entry_id"
        return [
            MemoryLock(
                source=row["source"], entry_id=row["entry_id"], text=row["text"],
                heading=row["heading"], locked_at=int(row["locked_at"]),
                displaced_by=row["displaced_by"],
            )
            for row in self._db().read(sql, params)
        ]

    def locked_ids(self, source: str) -> frozenset[str]:
        return frozenset(row.entry_id for row in self.rows(source))

    # -- scrittura -----------------------------------------------------------

    def lock(self, source: str, entry: Entry, *, at: int | None = None) -> None:
        """Mette il lucchetto a *entry* (o lo rinnova, con il testo di adesso)."""
        with self._db().write() as conn:
            conn.execute(
                "INSERT INTO memory_locks(source, entry_id, text, heading, locked_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(source, entry_id) DO UPDATE SET text = excluded.text, "
                "heading = excluded.heading, displaced_by = ''",
                (source, entry.id, entry.text, entry.heading,
                 int(at if at is not None else time.time())),
            )

    def unlock(self, source: str, entry_id: str) -> bool:
        with self._db().write() as conn:
            cur = conn.execute(
                "DELETE FROM memory_locks WHERE source = ? AND entry_id = ?",
                (source, entry_id),
            )
            return cur.rowcount > 0

    def move(self, source: str, old_id: str, new: Entry) -> bool:
        """Il lucchetto passa da *old_id* alla voce *new*, con la data di prima.

        La data resta quella della correzione dell'utente: è da allora che la
        voce è sua. Un *old_id* senza lucchetto non fa niente (``False``).
        """
        with self._db().write() as conn:
            row = conn.execute(
                "SELECT locked_at FROM memory_locks WHERE source = ? AND entry_id = ?",
                (source, old_id),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                "DELETE FROM memory_locks WHERE source = ? AND entry_id = ?",
                (source, old_id),
            )
            conn.execute(
                "INSERT INTO memory_locks(source, entry_id, text, heading, locked_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(source, entry_id) DO UPDATE SET text = excluded.text, "
                "heading = excluded.heading, displaced_by = ''",
                (source, new.id, new.text, new.heading, int(row["locked_at"])),
            )
            return True

    def mark_displaced(self, source: str, entry_id: str, by: str) -> None:
        with self._db().write() as conn:
            conn.execute(
                "UPDATE memory_locks SET displaced_by = ? WHERE source = ? AND entry_id = ?",
                (by or NO_SUCCESSOR, source, entry_id),
            )

    def note_readded(self, source: str, entry_id: str) -> None:
        """Dream ha riscritto una frase che l'utente aveva tolto (v. :func:`reassert`)."""
        with _READDED_GUARD:
            _READDED.setdefault(self._key(), set()).add((source, entry_id))

    def take_readded(self, source: str) -> set[str]:
        """Le frasi annotate da :meth:`note_readded` per *source*, che si svuotano."""
        with _READDED_GUARD:
            pending = _READDED.get(self._key(), set())
            taken = {entry_id for src, entry_id in pending if src == source}
            pending.difference_update((source, entry_id) for entry_id in taken)
            return taken

    def _key(self) -> str:
        try:
            return str(self._workspace.resolve())
        except OSError:
            return str(self._workspace)

    def clear_displaced(self, source: str, entry_id: str) -> None:
        with self._db().write() as conn:
            conn.execute(
                "UPDATE memory_locks SET displaced_by = '' WHERE source = ? AND entry_id = ?",
                (source, entry_id),
            )


def budget_exemptions(workspace: Path) -> dict[str, frozenset[str]]:
    """Gli id col lucchetto per etichetta di budget (``USER.md``, ``MEMORY.md``).

    Le voci col lucchetto non contano nel budget di Dream (``memory_budget``),
    come già le regole dell'utente in ``SOUL.md``: sono parole dell'utente, non
    di Dream, e contate una correzione lunga porterebbe il file oltre il tetto e
    farebbe rifiutare ogni scrittura di Dream. Un database illeggibile esenta
    niente, e lo dice nel log.
    """
    try:
        rows = MemoryLocks(workspace).rows()
    except (sqlite3.Error, OSError):
        logger.opt(exception=True).warning("Memory locks unreadable: no budget exemption")
        return {}
    out: dict[str, set[str]] = {}
    for row in rows:
        out.setdefault(Path(row.source).name, set()).add(row.entry_id)
    return {label: frozenset(ids) for label, ids in out.items()}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def reassert(workspace: Path) -> int:
    """Rimette le voci col lucchetto che Dream ha spostato. Non solleva mai.

    Per ogni riga con ``displaced_by``:

    - se la voce è di nuovo viva (Dream l'ha riscritta identica) non c'è niente
      da fare;
    - se la voce che ha preso il suo posto è viva, la voce dell'utente torna lì,
      e la formulazione di Dream va in archivio legata a lei (``superseded_by``);
    - altrimenti torna in coda alla sua sezione.

    Poi il file d'archivio della voce rimessa si toglie (è di nuovo viva). E una
    frase con una lapide che una scrittura di Dream ha rimesso (la rete l'ha
    annotata, ``MemoryLocks.note_readded``) si toglie dal file: è la stessa
    regola dello strumento per voci, per le strade che lo scavalcano. Solo
    quelle di Dream: la stessa frase rimessa dalla chat resta, perché un cambio
    fatto fuori da Dream non si disfa.

    Ritorna quante voci ha cambiato, per il log e per le prove. Si chiama dopo il
    review pass e alla fine di ogni ciclo, accanto a ``sync_soul``.
    """
    try:
        with ENTRIES_LOCK:
            return _reassert(Path(workspace))
    except Exception:  # noqa: BLE001 — una riparazione non deve far fallire il ciclo
        logger.exception("Memory locks: could not put the locked entries back")
        return 0


def _reassert(root: Path) -> int:
    locks = MemoryLocks(root)
    memory_dir = root / "memory"
    changed = 0
    for source in MEMORY_TARGETS.values():
        path = root / source
        original = _read(path)
        text = original
        rows = locks.rows(source)
        locked = {row.entry_id for row in rows}
        restored: list[MemoryLock] = []
        for row in rows:
            if not row.displaced_by:
                continue
            live = {entry.id: entry for entry in parse_entries(text)}
            if row.entry_id not in live:
                successor = live.get(row.displaced_by)
                if successor is not None and successor.id not in locked:
                    new_text, why = replace_entry(text, successor.id, row.text)
                    if not why:
                        # Prima l'archivio, poi il file: la formulazione di Dream
                        # non deve sparire senza passare da nessuna parte.
                        archive_entry(memory_dir, ArchivedEntry(
                            id=successor.id, text=successor.text, source=source,
                            heading=successor.heading, superseded_by=row.entry_id,
                        ))
                        text = new_text
                else:
                    new_text, why = add_entry(text, row.text, heading=row.heading or None)
                    if not why:
                        text = new_text
            if row.entry_id in {entry.id for entry in parse_entries(text)}:
                restored.append(row)
            else:
                logger.warning(
                    "Memory locks: could not put {} back in {}", row.entry_id, source,
                )
        live_ids = {e.id for e in parse_entries(text)}
        stale = {i for i in locks.take_readded(source) if i in live_ids and i not in locked}
        for entry_id in sorted(stale):
            new_text, why, _ = remove_entry(text, entry_id)
            if not why:
                text = new_text
                changed += 1
                logger.info("Memory locks: {} was removed by the user, dropped again", entry_id)
        if text != original:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(path, text)
        # La riga si chiude solo a file scritto: se la scrittura salta, la
        # passata dopo ci riprova.
        for row in restored:
            unarchive(memory_dir, row.entry_id)
            locks.clear_displaced(source, row.entry_id)
            changed += 1
            logger.info(
                "Memory locks: {} in {} put back as the user wrote it (Dream had {})",
                row.entry_id, source,
                "removed it" if row.displaced_by == NO_SUCCESSOR
                else f"replaced it with {row.displaced_by}",
            )
    return changed


def reconcile(workspace: Path) -> int:
    """Allinea i lucchetti ai file, quando a cambiarli è stato qualcun altro.

    Una riga senza ``displaced_by`` la cui voce non c'è più non l'ha tolta Dream
    (la rete l'avrebbe annotata): l'ha cambiata la chat, l'editor dell'officina o
    la stanza. Quel cambio si rispetta sempre. Il lucchetto segue la voce nuova
    quando la si riconosce — dal legame in archivio (``superseded_by``), o dalla
    somiglianza di ``pair_successors`` — e cade quando la voce è stata smentita,
    tolta dall'utente o non ha un seguito. Non solleva mai; ritorna quanti
    lucchetti ha spostato o tolto.

    Il limite è detto: la somiglianza può portare il lucchetto su una voce
    vicina (soglia 0,2 di ``pair_successors``).
    """
    try:
        with ENTRIES_LOCK:
            return _reconcile(Path(workspace))
    except Exception:  # noqa: BLE001 — come reassert
        logger.exception("Memory locks: could not reconcile the locks with the files")
        return 0


def _reconcile(root: Path) -> int:
    locks = MemoryLocks(root)
    memory_dir = root / "memory"
    changed = 0
    for source in MEMORY_TARGETS.values():
        rows = locks.rows(source)
        if not rows:
            continue
        entries = parse_entries(_read(root / source))
        live = {entry.id: entry for entry in entries}
        for row in rows:
            if row.displaced_by or row.entry_id in live:
                continue
            locked = locks.locked_ids(source)
            target = _follow(memory_dir, row, live, locked)
            if target is not None:
                locks.move(source, row.entry_id, target)
                logger.info(
                    "Memory locks: {} in {} was changed outside Dream; the lock follows {}",
                    row.entry_id, source, target.id,
                )
            else:
                locks.unlock(source, row.entry_id)
                logger.info(
                    "Memory locks: {} in {} was removed outside Dream; lock dropped",
                    row.entry_id, source,
                )
            changed += 1
    return changed


def _follow(memory_dir: Path, row: MemoryLock, live: dict[str, Entry],
            locked: frozenset[str]) -> Entry | None:
    """La voce viva che ha preso il posto di *row*, o ``None`` se il lucchetto cade."""
    found = find_archived(memory_dir, row.entry_id)
    archived = read_archived(found) if found is not None else None
    if archived is not None:
        if archived.retracted or archived.forgotten:
            return None
        successor = live.get(archived.superseded_by)
        if successor is not None and successor.id not in locked:
            return successor
    free = [entry for entry in live.values() if entry.id not in locked]
    gone = Entry(id=row.entry_id, heading=row.heading, text=row.text, start=0, end=0)
    pairs = pair_successors([gone], free)
    return live.get(pairs.get(row.entry_id, ""))
