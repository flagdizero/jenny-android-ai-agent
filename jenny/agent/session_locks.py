"""Dominio di lock per-sessione condiviso tra AgentLoop e Consolidator.

Prima di questo modulo esistevano DUE domini di lock indipendenti sulla stessa
sessione — ``AgentLoop._session_locks`` (il turno) e ``Consolidator._locks`` (la
consolidation) — così una consolidation detached poteva mutare
``session.messages`` mentre un nuovo turno la mutava sotto un lock diverso
(data-race). Qui c'è un unico registro condiviso: per una data session key,
turno e consolidation acquisiscono lo STESSO lock, quindi si escludono a vicenda.

Il lock è **rientrante per-task**: la consolidation attesa *inline* dentro un
turno gira nello stesso task che già tiene il lock e rientra senza deadlock
(``asyncio.Lock`` non è rientrante); una consolidation *detached* gira in un task
diverso e attende normalmente. L'ownership è per identità di task
(``asyncio.current_task()``), non per contextvar — così un task creato con
``asyncio.create_task`` (che copia il context) NON eredita erroneamente
l'ownership del lock.
"""

from __future__ import annotations

import asyncio

from loguru import logger


class ReentrantSessionLock:
    """Lock async rientrante per-task su una singola sessione."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        self._depth = 0
        # Quanti task sono dentro ``acquire()``: v. :meth:`busy`.
        self._waiting = 0

    async def __aenter__(self) -> "ReentrantSessionLock":
        current = asyncio.current_task()
        if self._depth > 0 and self._owner is current and current is not None:
            self._depth += 1
            return self
        self._waiting += 1
        try:
            await self._lock.acquire()
        finally:
            self._waiting -= 1
        self._owner = current
        self._depth = 1
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self._depth -= 1
        if self._depth <= 0:
            self._depth = 0
            self._owner = None
            self._lock.release()
        return False

    def locked(self) -> bool:
        return self._lock.locked()

    def busy(self) -> bool:
        """Tenuto, **o atteso**: qualcuno ha in mano la sessione o sta per averla.

        ``locked()`` da solo ha un giro cieco: ``asyncio.Lock.release`` libera il
        lock e sveglia il primo in attesa, che lo prende solo quando il loop gli
        ridà il passo. In mezzo il lock risulta libero, e chi aspettava sta per
        scrivere. Il conto di chi è dentro ``acquire()`` copre quel giro.
        """
        return self._lock.locked() or self._waiting > 0


class SessionLocks:
    """Registro dei lock per-sessione (un solo lock per key, condiviso)."""

    def __init__(self) -> None:
        self._locks: dict[str, ReentrantSessionLock] = {}

    def get(self, key: str) -> ReentrantSessionLock:
        """Ritorna (creandolo se serve) il lock condiviso per *key*."""
        lock = self._locks.get(key)
        if lock is None:
            lock = ReentrantSessionLock()
            self._locks[key] = lock
        return lock

    def is_locked(self, key: str) -> bool:
        """True se esiste un lock per *key* ed è attualmente tenuto."""
        lock = self._locks.get(key)
        return lock is not None and lock.locked()

    def busy_keys(self) -> tuple[str, ...]:
        """Le chiavi il cui lock è tenuto o atteso **adesso** (v. ``busy``).

        È la domanda di ``AgentLoop.busy_session_keys``: il lock di sessione è
        la regola per scrivere una sessione, quindi chi lo tiene è uno
        scrittore da qualunque porta sia entrato. Un lock ruotato non conta: lo
        zombie tiene quello vecchio, che non è più nel registro.
        """
        return tuple(key for key, lock in self._locks.items() if lock.busy())

    def evict(self, key: str) -> None:
        """Rimuove il lock per *key* se non è tenuto (no-op altrimenti)."""
        lock = self._locks.get(key)
        if lock is not None and not lock.locked():
            self._locks.pop(key, None)

    def rotate(self, key: str) -> bool:
        """Sostituisce il lock per *key* se è attualmente tenuto.

        Usato quando un turno abbandonato (task cancellato che non muore,
        bloccato in un thread non interrompibile) resta owner del lock: i
        turni successivi non devono serializzarsi dietro lo zombie. Il lock
        orfano resta in mano allo zombie (che lo rilascerà nel vuoto quando
        il thread finisce); tutti i futuri acquirer — inclusa la consolidation,
        che risolve dallo stesso registro — prendono il lock nuovo.

        Finestra residua accettata: una consolidation detached GIÀ in attesa
        sul lock orfano potrebbe poi girare in concorrenza con un turno nuovo;
        lo zombie stesso non può più schedularne (i suoi punti di rientro sono
        guardati dall'epoch di turno).

        Ritorna True se ha ruotato, False se il lock era libero o assente.
        """
        lock = self._locks.get(key)
        if lock is None or not lock.locked():
            return False
        self._locks[key] = ReentrantSessionLock()
        logger.warning(
            "Session lock for {} rotated: abandoned turn still holds the old lock",
            key,
        )
        return True

    def __contains__(self, key: str) -> bool:
        return key in self._locks
