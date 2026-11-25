"""Cosa ha toccato un lavoro, e il suo annulla: il pezzo comune.

Un lavoro fatto per conto di qualcosa — la richiesta di un'altra Jenny
(``jenny.peers.acting``) — scrive in uno o più registri dell'annulla
(``agent.write_journal``): il suo, e quelli degli aiutanti che ha lanciato.
Qui stanno le due cose che chi tiene quel lavoro chiede ai registri, senza
sapere di chi sono: l'elenco dei file toccati, scritto dal codice, e l'annulla
di tutti i registri insieme, in un registro suo.

Funzioni sincrone, senza stato: i chiamanti async passano da
``asyncio.to_thread``.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from jenny.agent.write_journal import UndoReport, journal_for, undo_set


def journal_file_items(root: Path, keys: Iterable[str]) -> list[dict[str, Any]]:
    """I file toccati dai registri *keys*, uno per percorso, nell'ordine in cui sono stati toccati.

    Ogni voce ha la forma dell'elenco di «cosa ha toccato»:
    ``{"kind": "file", "label": <percorso>, "undoable": True}``. Le cartelle non
    sono voci; un registro che non si legge si salta.
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in keys:
        try:
            entries = journal_for(root, key).entries()
        except Exception:  # noqa: BLE001 — un registro illeggibile non ferma l'elenco
            continue
        for entry in entries:
            path = str(entry.get("path") or "")
            if not path or path.endswith("/") or path in seen:
                continue
            seen.add(path)
            items.append({"kind": "file", "label": path, "undoable": True})
    return items


def undo_journals(
    root: Path, keys: Iterable[str], into_key: str, *, by: str = "user",
    dry_run: bool = False, token: str | None = None,
) -> UndoReport | None:
    """Rimette com'erano i file dei registri *keys*, insieme, nel registro *into_key*.

    Solo i registri che hanno righe entrano nell'annulla; senza nessuno,
    ``None``. *dry_run* e *token* sono quelli di :func:`undo_set` (l'anteprima
    col gettone, e la conferma che lo verifica).
    """
    journals = []
    for key in keys:
        journal = journal_for(root, key)
        if journal.entries():
            journals.append(journal)
    if not journals:
        return None
    into = journal_for(root, into_key)
    return undo_set(journals, by=by, into=into, dry_run=dry_run, token=token)


__all__ = ["journal_file_items", "undo_journals"]
