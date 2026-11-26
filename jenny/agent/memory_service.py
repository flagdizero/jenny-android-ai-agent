"""Cosa ricorda Jenny, visto e corretto dall'utente: la logica della stanza.

La stanza «Cosa ricorda di te» della casa (``home-memory.js``) e i suoi comandi
(``webui/memory_commands.py``) passano tutti di qui. Le voci sono quelle di
``USER.md`` («Di te») e ``memory/MEMORY.md`` («I suoi appunti»), lette con lo
stesso parser dello strumento di Dream (``tools/memory_entries.py``).

**Ogni scrittura passa dall'archivio, mai da una scrittura di file nuda.** Una
voce corretta lascia in ``memory/archive/`` la formulazione vecchia, legata alla
nuova (``superseded_by``); una «non era vera» anche ``retracted``; una tolta
``forgotten``, che il richiamo per nome (``facts.py``) e ``recall`` non
riportano piu'. E una voce che l'utente ha scritto o corretto prende il
lucchetto (``memory_locks.py``): Dream non la riordina.

**Ogni gesto e' un confronto e scambio.** Gli id sono impronte del testo: se nel
frattempo la voce e' cambiata (Dream, la chat, l'editor dell'officina), l'id
chiesto non c'e' piu' e la risposta e' ``conflict`` — la stanza rilegge, invece di
scrivere sopra una voce che l'utente non ha visto.

Tutto sotto ``ENTRIES_LOCK``, la stessa serratura dello strumento di Dream e di
``memory_locks.reassert``/``reconcile``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from jenny.agent.memory_archive import (
    ArchivedEntry,
    archive_entry,
    find_archived,
    list_archived,
    read_archived,
    unarchive,
)
from jenny.agent.memory_locks import MemoryLocks
from jenny.agent.memory_themes import THEMED_SOURCE, MemoryThemes
from jenny.agent.tools.memory_entries import (
    _FRAGMENT_NOTE,
    ENTRIES_LOCK,
    MEMORY_TARGETS,
    Entry,
    add_entry,
    entry_id,
    pair_successors,
    parse_entries,
    remove_entry,
    replace_entry,
)
from jenny.utils.path import atomic_write

# «Dimenticato di recente»: quanto indietro, e quante righe al massimo.
RECENT_DAYS = 28
RECENT_MAX = 50
# Il testo di una voce che la stanza accetta: una frase, non un documento.
MAX_ENTRY_CHARS = 1_000

_BULLET = re.compile(r"^[-*+][ \t]+")


class MemoryEditError(Exception):
    """Un gesto che non si puo' fare. ``code`` e' quello del comando."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _File:
    key: str        # ``user`` | ``memory``
    rel: str        # ``USER.md`` | ``memory/MEMORY.md``
    path: Path


def _files(workspace: Path) -> list[_File]:
    return [_File(key, rel, Path(workspace) / rel) for key, rel in MEMORY_TARGETS.items()]


def _file(workspace: Path, key: str) -> _File:
    for f in _files(workspace):
        if f.key == key:
            return f
    raise MemoryEditError("bad_request", f"unknown memory file {key!r}")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def plain(text: str) -> str:
    """Il testo di una voce senza il segno del bullet: come lo legge una persona."""
    lines = (text or "").strip().splitlines()
    if not lines:
        return ""
    lines[0] = _BULLET.sub("", lines[0])
    return "\n".join(line.strip() for line in lines).strip()


def _clean_input(text: Any) -> str:
    if not isinstance(text, str):
        raise MemoryEditError("bad_request", "text must be a string")
    value = " ".join(text.split())
    if not value:
        raise MemoryEditError("bad_request", "text is empty")
    if len(value) > MAX_ENTRY_CHARS:
        raise MemoryEditError("too_large", "an entry is a sentence")
    return value


def _memory_dir(workspace: Path) -> Path:
    return Path(workspace) / "memory"


def _find(text: str, wanted: str) -> Entry | None:
    return next((e for e in parse_entries(text) if e.id == wanted), None)


# ── Lettura ──────────────────────────────────────────────────────────────────


def list_memory(workspace: Path, *, today: date | None = None) -> dict[str, Any]:
    """Le due sezioni, il tema di ogni frase di «Di te», e «Tolte da te».

    ``locked`` non si mostra: serve all'«Annulla» del cestino, che rimette la
    voce com'era. ``unsorted`` e' quante frasi di ``USER.md`` non hanno ancora
    un tema (``agent/memory_themes.py``): chi chiama decide se farle ordinare.
    """
    from jenny.agent.dream_cycle import cycle_in_flight

    workspace = Path(workspace)
    today = today or date.today()
    locks = MemoryLocks(workspace)
    with ENTRIES_LOCK:
        texts = {f.key: _read(f.path) for f in _files(workspace)}
        archived = list_archived(_memory_dir(workspace))
    entries = {f.key: parse_entries(texts[f.key]) for f in _files(workspace)}
    themes = MemoryThemes(workspace).all()

    files = []
    unsorted = 0
    for f in _files(workspace):
        locked = locks.locked_ids(f.rel)
        themed = f.rel == THEMED_SOURCE
        rows = []
        for e in entries[f.key]:
            row: dict[str, Any] = {
                "id": e.id,
                "text": plain(e.text),
                "heading": e.heading,
                "locked": e.id in locked,
            }
            if themed:
                row["theme"] = themes.get(e.id, "")
                unsorted += not row["theme"]
            rows.append(row)
        files.append({"file": f.key, "entries": rows})
    return {
        "ok": True,
        "dreaming": cycle_in_flight(),
        "files": files,
        "unsorted": unsorted,
        "recent": _recent(archived, today),
    }


def _recent(archived: list[ArchivedEntry], today: date) -> list[dict[str, Any]]:
    """«Tolte da te»: le frasi che ha tolto l'utente, nelle ultime settimane.

    Solo ``forgotten``, che e' l'unico segno certo dell'archivio: il cestino
    della stanza o una riga cancellata a mano in officina. Fino al 26/11/2026
    qui c'erano anche le formulazioni cambiate e quelle tolte da Dream, cioe'
    cose che l'archivio in parte indovina (un legame fra due frasi scelto per
    somiglianza, una fusione scritta come una rimozione): 13 righe su 14 su
    jenny_s22, e nessuno le capiva.
    """
    since = today - timedelta(days=RECENT_DAYS)
    rows: list[dict[str, Any]] = []
    files = {rel: key for key, rel in MEMORY_TARGETS.items()}
    for entry in archived:
        if _FRAGMENT_NOTE in (entry.heading or ""):
            continue
        try:
            demoted = date.fromisoformat(entry.demoted) if entry.demoted else None
        except ValueError:
            demoted = None
        if demoted is None or demoted < since:
            continue
        if entry.source not in files:
            continue
        if not entry.forgotten:
            continue
        row: dict[str, Any] = {
            "kind": "removed",
            "id": entry.id,
            "text": plain(entry.text),
            "date": entry.demoted,
            "file": files[entry.source],
        }
        rows.append(row)
        if len(rows) >= RECENT_MAX:
            break
    return rows


# ── Scrittura ────────────────────────────────────────────────────────────────


def correct(
    workspace: Path, file_key: str, wanted: str, text: Any, reason: str,
) -> dict[str, Any]:
    """Cambia una voce: «È cambiato» (``changed``) o «Non era vero» (``wrong``).

    La formulazione vecchia va in archivio legata alla nuova; con ``wrong``
    anche ``retracted``, cosi' il richiamo per nome non la riporta. La voce
    nuova prende il lucchetto (o lo eredita, con la sua data).
    """
    if reason not in {"changed", "wrong"}:
        raise MemoryEditError("bad_request", "reason must be changed or wrong")
    value = _clean_input(text)
    workspace = Path(workspace)
    f = _file(workspace, file_key)
    locks = MemoryLocks(workspace)
    with ENTRIES_LOCK:
        current = _read(f.path)
        old = _find(current, wanted)
        if old is None:
            raise MemoryEditError("conflict", "that entry changed in the meantime")
        if plain(old.text) == value:
            return {"ok": True, "id": old.id, "unchanged": True}
        updated, why = replace_entry(current, old.id, value)
        if why:
            raise MemoryEditError("bad_request", why)
        new = next((e for e in parse_entries(updated) if e.id not in
                    {x.id for x in parse_entries(current)}), None)
        if new is None:
            raise MemoryEditError("conflict", "the same sentence is already there")
        archive_entry(_memory_dir(workspace), ArchivedEntry(
            id=old.id, text=old.text, source=f.rel, heading=old.heading,
            superseded_by=new.id, retracted=reason == "wrong",
        ))
        atomic_write(f.path, updated)
        if not locks.move(f.rel, old.id, new):
            locks.lock(f.rel, new)
        if f.rel == THEMED_SOURCE:
            # La frase corretta resta nel suo tema: e' la stessa cosa detta giusta.
            MemoryThemes(workspace).inherit(old.id, new.id)
    return {"ok": True, "id": new.id}


def remove(workspace: Path, file_key: str, wanted: str) -> dict[str, Any]:
    """Toglie una voce: in archivio come ``forgotten``, e senza lucchetto.

    Non torna col richiamo per nome; torna con :func:`restore` (l'«Annulla»
    della stanza, o «Rimetti» in «Dimenticato di recente»).
    """
    workspace = Path(workspace)
    f = _file(workspace, file_key)
    with ENTRIES_LOCK:
        current = _read(f.path)
        old = _find(current, wanted)
        if old is None:
            raise MemoryEditError("conflict", "that entry changed in the meantime")
        updated, why, _gone = remove_entry(current, old.id)
        if why:
            raise MemoryEditError("bad_request", why)
        archive_entry(_memory_dir(workspace), ArchivedEntry(
            id=old.id, text=old.text, source=f.rel, heading=old.heading, forgotten=True,
        ))
        atomic_write(f.path, updated)
        MemoryLocks(workspace).unlock(f.rel, old.id)
    return {"ok": True, "id": old.id}


def restore(workspace: Path, wanted: str, *, lock: bool = True) -> dict[str, Any]:
    """Rimette una frase tolta, come voce viva, sotto la sua intestazione.

    E' «Rimetti» di «Tolte da te» (col lucchetto: la scegli tu, e Dream non la
    riordina via di nuovo) e l'«Annulla» del cestino, che passa ``lock`` com'era
    la voce prima: senza, ogni voce rimessa con «Annulla» prendeva il lucchetto
    (visto sul Titan il 25/11/2026). Il file d'archivio se ne va (``unarchive``):
    l'archivio torna a voler dire «formulazioni non vive».
    """
    workspace = Path(workspace)
    memory_dir = _memory_dir(workspace)
    with ENTRIES_LOCK:
        path = find_archived(memory_dir, wanted)
        archived = read_archived(path) if path is not None else None
        if archived is None:
            raise MemoryEditError("not_found", "nothing archived with that id")
        rel = archived.source
        key = next((k for k, r in MEMORY_TARGETS.items() if r == rel), None)
        if key is None:
            raise MemoryEditError("not_found", "that entry was not in a memory file")
        f = _file(workspace, key)
        current = _read(f.path)
        body = plain(archived.text)
        if _find(current, entry_id(archived.text)) is None:
            updated, why = add_entry(current, body, heading=archived.heading or None)
            if why and not why.startswith("already present"):
                raise MemoryEditError("bad_request", why)
            if why:
                updated = current
            atomic_write(f.path, updated)
            current = updated
        live = _find(current, entry_id(archived.text))
        if live is None:
            # Il testo e' tornato con un'altra forma (spazi, bullet): la voce e'
            # quella appena scritta, riconosciuta per testo.
            live = next((e for e in parse_entries(current) if plain(e.text) == body), None)
        unarchive(memory_dir, archived.id)
        if live is not None and lock:
            MemoryLocks(workspace).lock(f.rel, live)
    return {"ok": True, "id": live.id if live else archived.id, "file": key}


def archive_hand_edit(workspace: Path, path: Path, new_text: str) -> None:
    """L'editor dell'officina riscrive ``USER.md`` o ``memory/MEMORY.md``.

    Prima della scrittura: la stessa rete al confine del file che usa Dream
    (``make_entry_archiver``), poi le voci sparite senza una che le sostituisca
    diventano ``forgotten`` — cancellarle a mano e' toglierle, e senza il
    segno tornerebbero col richiamo per nome. Non solleva mai: una scrittura
    dell'utente non si blocca per l'archivio.
    """
    from loguru import logger

    from jenny.agent.tools.memory_entries import make_entry_archiver

    workspace = Path(workspace)
    rel = next((r for r in MEMORY_TARGETS.values()
                if (workspace / r).resolve() == Path(path).resolve()), None)
    if rel is None:
        return
    try:
        with ENTRIES_LOCK:
            before = _read(workspace / rel)
            gone_entries = parse_entries(before)
            after_entries = parse_entries(new_text)
            after_ids = {e.id for e in after_entries}
            gone = [e for e in gone_entries if e.id not in after_ids]
            arrived = [e for e in after_entries if e.id not in {x.id for x in gone_entries}]
            make_entry_archiver(workspace)(workspace / rel, new_text)
            successors = pair_successors(gone, arrived) if gone else {}
            for e in gone:
                if e.id in successors:
                    continue
                archive_entry(_memory_dir(workspace), ArchivedEntry(
                    id=e.id, text=e.text, source=rel, heading=e.heading, forgotten=True,
                ))
    except Exception:  # noqa: BLE001 — una rete, non una condizione
        logger.opt(exception=True).warning("Memory: hand edit of {} not archived", rel)
