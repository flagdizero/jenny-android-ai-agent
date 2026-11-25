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
    archive_chains,
    archive_entry,
    find_archived,
    list_archived,
    lost_details,
    read_archived,
    unarchive,
)
from jenny.agent.memory_locks import MemoryLocks
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
    """Le due sezioni, le voci col lucchetto, i dettagli persi e «di recente»."""
    from jenny.agent.dream_cycle import cycle_in_flight

    workspace = Path(workspace)
    today = today or date.today()
    locks = MemoryLocks(workspace)
    with ENTRIES_LOCK:
        texts = {f.key: _read(f.path) for f in _files(workspace)}
        archived = list_archived(_memory_dir(workspace))
    live_text = "\n".join(texts.values())
    entries = {f.key: parse_entries(texts[f.key]) for f in _files(workspace)}
    live_ids = {e.id: f.key for f in _files(workspace) for e in entries[f.key]}

    lost_by_entry = _lost_by_live_entry(archived, live_text, live_ids)

    files = []
    for f in _files(workspace):
        locked = locks.locked_ids(f.rel)
        files.append({
            "file": f.key,
            "entries": [
                {
                    "id": e.id,
                    "text": plain(e.text),
                    "heading": e.heading,
                    "locked": e.id in locked,
                    "lost": lost_by_entry.get(e.id, []),
                }
                for e in entries[f.key]
            ],
        })
    return {
        "ok": True,
        "dreaming": cycle_in_flight(),
        "files": files,
        "recent": _recent(archived, live_ids, lost_by_entry, today),
    }


def _lost_by_live_entry(
    archived: list[ArchivedEntry], live_text: str, live_ids: dict[str, str],
) -> dict[str, list[dict[str, Any]]]:
    """Per ogni voce viva, i nomi e i numeri che le sue formulazioni vecchie
    dicevano e che nessun file vivo dice piu'.

    Una catena dell'archivio finisce in una voce viva quando la sua versione
    piu' recente punta (``superseded_by``) a un id che e' vivo: e' li' che la
    stanza mette «Rimetti».
    """
    chains = archive_chains(archived)
    by_chain: dict[int, str] = {}
    for index, chain in enumerate(chains):
        target = chain.latest.superseded_by
        if target and target in live_ids:
            by_chain[index] = target
    if not by_chain:
        return {}
    lost = lost_details(chains, live_text, only=set(by_chain))
    # Una riga per **frase vecchia**, con tutte le parole che porta: «Metal»,
    # «Gear» e «Solid» vengono dalla stessa formulazione, e tre righe con tre
    # «Rimetti» che rimettono la stessa frase erano rumore (visto sui dati veri
    # di jenny_s22 il 25/11/2026: 51 righe su 62 voci).
    out: dict[str, list[dict[str, Any]]] = {}
    for index, details in lost.items():
        live_id = by_chain[index]
        bucket = out.setdefault(live_id, [])
        by_version = {item["id"]: item for item in bucket}
        seen = {w for item in bucket for w in item["words"]}
        for detail in details:
            if detail.shown in seen:
                continue
            seen.add(detail.shown)
            item = by_version.get(detail.version.id)
            if item is None:
                item = {"words": [], "id": detail.version.id,
                        "text": plain(detail.version.text), "date": detail.version.demoted}
                by_version[detail.version.id] = item
                bucket.append(item)
            item["words"].append(detail.shown)
    return out


def _recent(
    archived: list[ArchivedEntry],
    live_ids: dict[str, str],
    lost_by_entry: dict[str, list[dict[str, Any]]],
    today: date,
) -> list[dict[str, Any]]:
    """«Dimenticato di recente»: tolte, cambiate, non vere, e i dettagli persi."""
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
        if entry.retracted:
            kind = "wrong"
        elif entry.forgotten or not entry.superseded_by:
            kind = "removed"
        else:
            kind = "changed"
        row: dict[str, Any] = {
            "kind": kind,
            "id": entry.id,
            "text": plain(entry.text),
            "date": entry.demoted,
            "file": files[entry.source],
        }
        if kind == "changed":
            now = entry.superseded_by
            row["now"] = now if now in live_ids else ""
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


def restore(workspace: Path, wanted: str, *, after: str | None = None) -> dict[str, Any]:
    """Rimette una formulazione archiviata, come voce viva e col lucchetto.

    Sotto la sua intestazione, o subito dopo la voce viva *after*: e' «Rimetti»
    accanto a un dettaglio perso, dove la frase vecchia torna **accanto** a
    quella nuova invece di sovrascriverla — la nuova puo' sapere cose che la
    vecchia non sapeva. Il file d'archivio se ne va (``unarchive``): l'archivio
    torna a voler dire «formulazioni non vive». L'«Annulla» del cestino e'
    questa stessa funzione.
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
            anchor = _find(current, after) if after else None
            if anchor is not None:
                lines = current.splitlines()
                bullet = f"- {body}" if "\n" not in body else "- " + body.replace("\n", "\n  ")
                updated = "\n".join(lines[: anchor.end] + [bullet] + lines[anchor.end:]) + "\n"
            else:
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
        if live is not None:
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
