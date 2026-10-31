"""L'imbuto delle scritture della bacheca.

``board.json`` lo scrivono in due: l'interfaccia (un trascinamento, una sezione,
una risposta alla busta) e il giardiniere. È il problema che ``config.json`` ha
risolto con ``config/store.py::mutate``, e la soluzione è la stessa:

- **un imbuto unico** (:func:`mutate_board`, :func:`mutate_reminders`) che
  **rilegge il file dentro il lock** con cui scrive, così nessuno può tenere in
  mano una copia vecchia e salvarla sopra una nuova;
- **scrittura atomica**, con un ``.bak`` dell'ultimo contenuto valido;
- **niente I/O lento dentro il callback**: chi deve chiamare un modello lo fa
  prima, e qui applica soltanto;
- **le chiavi sconosciute si conservano** (lo fa la normalizzazione di
  :mod:`jenny.board.model`).

Senza, un trascinamento fatto mentre il giardiniere gira verrebbe cancellato in
silenzio dalla sua scrittura.

**Il lock è un ``threading.Lock`` e le funzioni sono sincrone**, al contrario di
``config/store.py``. Le scritture arrivano dall'event loop (i comandi RPC, gli
strumenti del giardiniere) ma anche da thread (``asyncio.to_thread``), e il
corpo critico è una lettura e una scrittura di un file piccolo: un lock di
thread vale per tutti e due i casi e non si lega a un event loop, quindi non
ha il problema del loop morto che ``reset_config_store_state`` esiste per
evitare.

Le scritture di Jenny (``actor="jenny"``) passano un controllo in più: se il
callback ha toccato qualcosa che è dell'utente, la scrittura non avviene e
parte :class:`BoardOwnershipError`. Il rispetto della forma dell'utente lo
garantisce questo controllo, non il prompt.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.board.model import (
    BOARD_DIR,
    BOARD_FILE,
    CARDS_DIR,
    JENNY,
    REMINDERS_FILE,
    USER,
    is_valid_id,
    normalize_board,
    normalize_card_summary,
    normalize_reminders,
    ownership_violations,
    reminder_violations,
)
from jenny.security.subagent_policy import journal_touch
from jenny.utils.path import atomic_write


class BoardOwnershipError(Exception):
    """Una scrittura di Jenny ha toccato qualcosa che è dell'utente."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve(strict=False))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def board_dir(project_root: Path) -> Path:
    return project_root / BOARD_DIR


def board_path(project_root: Path) -> Path:
    return board_dir(project_root) / BOARD_FILE


def reminders_path(project_root: Path) -> Path:
    return board_dir(project_root) / REMINDERS_FILE


def card_summary_path(project_root: Path, card_id: str) -> Path:
    if not is_valid_id(card_id):
        raise ValueError(f"invalid card id: {card_id!r}")
    return board_dir(project_root) / CARDS_DIR / f"{card_id}.json"


def _backup(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def _read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _read_with_recovery(path: Path) -> Any | None:
    """Il JSON di *path*; se è rotto, quello del ``.bak``; se manca, ``None``.

    Un file rotto non si cancella: si mette da parte con un nome che dice quando
    (``board.corrupt-<istante>.json``), come fa la config — serve a capire cosa
    è successo, e la prossima scrittura ripartirà dal ``.bak``.
    """
    if not path.exists():
        return None
    try:
        return _read_json(path)
    except (OSError, ValueError) as exc:
        logger.error("board: {} is unreadable ({}); falling back to the backup", path, exc)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        try:
            os.replace(path, path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}"))
        except OSError:
            pass
    try:
        return _read_json(_backup(path))
    except (OSError, ValueError):
        return None


def _write(path: Path, data: Any) -> None:
    """Scrittura atomica, ruotando prima il ``.bak`` sull'ultimo contenuto valido."""
    # Dentro un aiutante (l'editore dei quaderni): il «prima» per l'annulla
    # (``agent.subagent_journal``). Fuori è una no-op. Il ``.bak`` no: è la copia
    # di rotazione dell'ultimo contenuto valido, non il lavoro di qualcuno.
    journal_touch(path)
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8")
            json.loads(content)
        except (OSError, ValueError):
            content = None
        if content is not None:
            try:
                atomic_write(_backup(path), content, fsync_dir=False)
            except OSError as exc:
                logger.warning("board: backup of {} not refreshed: {}", path, exc)
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def rewrite_json(path: Path, change: Callable[[Any], Any]) -> None:
    """Riscrive la bacheca o le attese con ``change(contenuto di adesso)``.

    Per l'annulla di un aiutante (``agent.subagent_journal``), che toglie il suo
    cambiamento da quel che c'è adesso: lettura e scrittura sotto il lock degli
    altri scrittori, così un tocco dell'utente arrivato nel frattempo non va
    perso. Se *change* solleva non si scrive niente. Nessuna normalizzazione:
    il contenuto è già quello che uno scrittore normale aveva lasciato.
    """
    with _lock_for(path):
        current = _read_json(path) if path.exists() else None
        _write(path, change(current))


def read_board(project_root: Path) -> dict[str, Any] | None:
    """La bacheca del quaderno, normalizzata; ``None`` se il quaderno non ne ha una."""
    raw = _read_with_recovery(board_path(project_root))
    return None if raw is None else normalize_board(raw)


def read_reminders(project_root: Path) -> dict[str, Any]:
    """Le attese del quaderno; un file assente è un elenco vuoto."""
    return normalize_reminders(_read_with_recovery(reminders_path(project_root)))


def _mutate(
    path: Path,
    normalize: Callable[[Any], dict[str, Any]],
    violations: Callable[[dict[str, Any], dict[str, Any]], list[str]],
    apply: Callable[[dict[str, Any]], bool | None],
    actor: str,
) -> dict[str, Any]:
    if actor not in (USER, JENNY):
        raise ValueError(f"unknown actor {actor!r}")
    with _lock_for(path):
        current = normalize(_read_with_recovery(path))
        before = copy.deepcopy(current)
        if apply(current) is False:
            return before
        after = normalize(current)
        if actor == JENNY:
            problems = violations(before, after)
            if problems:
                raise BoardOwnershipError(problems)
        _write(path, after)
        return after


def mutate_board(
    project_root: Path,
    apply: Callable[[dict[str, Any]], bool | None],
    *,
    actor: str = USER,
) -> dict[str, Any]:
    """Applica *apply* alla bacheca e la salva, in serie con gli altri scrittori.

    *apply* riceve la bacheca appena letta (vuota se il file non c'è) e la
    cambia sul posto. Non deve fare I/O lento: il lock resta preso per tutta la
    sua durata. Se solleva, non si scrive niente. Se restituisce ``False`` non
    c'è niente da salvare e il file resta com'è.

    Con ``actor="jenny"`` la scrittura avviene solo se non tocca niente di
    dell'utente (:func:`jenny.board.model.ownership_violations`).

    Restituisce la bacheca risultante.
    """
    return _mutate(board_path(project_root), normalize_board, ownership_violations, apply, actor)


def mutate_reminders(
    project_root: Path,
    apply: Callable[[dict[str, Any]], bool | None],
    *,
    actor: str = USER,
) -> dict[str, Any]:
    """Come :func:`mutate_board`, per ``reminders.json``."""
    return _mutate(
        reminders_path(project_root), normalize_reminders, reminder_violations, apply, actor
    )


def read_card_summary(project_root: Path, card_id: str) -> dict[str, Any] | None:
    """Il riassunto del cassetto *card_id*, o ``None`` se non è ancora stato scritto."""
    try:
        path = card_summary_path(project_root, card_id)
    except ValueError:
        return None
    try:
        raw = _read_json(path)
    except (OSError, ValueError):
        return None
    return normalize_card_summary(raw, card_id)


def write_card_summary(project_root: Path, card_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    """Scrive il riassunto di un cassetto. È derivato: niente ``.bak``, niente imbuto.

    Un solo scrittore (la passata della bacheca, una alla volta per quaderno) e
    nessuna fonte di verità dentro: se si perde, la passata dopo lo rigenera.
    """
    path = card_summary_path(project_root, card_id)
    data = normalize_card_summary(summary, card_id) or {"card": card_id}
    journal_touch(path)
    with _lock_for(path):
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return data


def delete_card_summary(project_root: Path, card_id: str) -> None:
    try:
        path = card_summary_path(project_root, card_id)
        journal_touch(path)
        path.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass
