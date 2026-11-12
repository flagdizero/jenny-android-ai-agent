"""Fase 1.4 — Unit test del lock rientrante per-task e del registro."""

from __future__ import annotations

import asyncio

from jenny.agent.session_locks import ReentrantSessionLock, SessionLocks


def test_registry_returns_same_lock_per_key() -> None:
    locks = SessionLocks()
    assert locks.get("a") is locks.get("a")
    assert locks.get("a") is not locks.get("b")


def test_registry_evict_only_when_unheld() -> None:
    locks = SessionLocks()
    lock = locks.get("a")
    assert locks.is_locked("a") is False
    locks.evict("a")
    # dopo evict, get() crea un nuovo oggetto
    assert locks.get("a") is not lock


async def test_registry_evict_noop_while_held() -> None:
    locks = SessionLocks()
    lock = locks.get("a")
    async with lock:
        assert locks.is_locked("a") is True
        locks.evict("a")  # no-op perché tenuto
        assert locks.get("a") is lock


async def test_same_task_reenters() -> None:
    lock = ReentrantSessionLock()
    async with lock:
        assert lock.locked()
        async with lock:  # rientro nello stesso task
            async with lock:
                pass
        assert lock.locked()  # ancora tenuto al primo livello
    assert not lock.locked()  # rilasciato a profondità 0


async def test_different_tasks_are_mutually_exclusive() -> None:
    lock = ReentrantSessionLock()
    order: list[str] = []
    a_holds = asyncio.Event()
    a_release = asyncio.Event()

    async def task_a() -> None:
        async with lock:
            order.append("a:enter")
            a_holds.set()
            await a_release.wait()
            order.append("a:exit")

    async def task_b() -> None:
        await a_holds.wait()
        async with lock:
            order.append("b:enter")

    a = asyncio.create_task(task_a())
    b = asyncio.create_task(task_b())
    await asyncio.wait_for(a_holds.wait(), timeout=1.0)
    await asyncio.sleep(0.02)
    assert "b:enter" not in order  # B bloccato finché A tiene il lock

    a_release.set()
    await asyncio.wait_for(asyncio.gather(a, b), timeout=1.0)
    assert order == ["a:enter", "a:exit", "b:enter"]


async def test_lock_released_on_exception() -> None:
    lock = ReentrantSessionLock()
    try:
        async with lock:
            raise ValueError("boom")
    except ValueError:
        pass
    assert not lock.locked()


class TestRotate:
    """rotate(): sostituzione del lock orfano di un turno abbandonato."""

    async def test_rotate_replaces_held_lock(self):
        locks = SessionLocks()
        old = locks.get("k")
        acquired = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with old:
                acquired.set()
                await release.wait()

        task = asyncio.create_task(holder())
        await acquired.wait()

        assert locks.rotate("k") is True
        new = locks.get("k")
        assert new is not old
        assert not new.locked()
        release.set()
        await task

    async def test_rotate_noop_when_unheld(self):
        locks = SessionLocks()
        lock = locks.get("k")
        assert locks.rotate("k") is False
        assert locks.get("k") is lock

    async def test_rotate_noop_when_absent(self):
        locks = SessionLocks()
        assert locks.rotate("missing") is False


# ── Chi ha in mano una sessione: ``busy_keys`` ───────────────────────────────


async def test_busy_keys_are_the_locks_held_now() -> None:
    locks = SessionLocks()
    locks.get("ferma")
    assert locks.busy_keys() == ()
    async with locks.get("project:orto"):
        assert locks.busy_keys() == ("project:orto",)
    assert locks.busy_keys() == ()


async def test_a_lock_handed_to_a_waiter_never_looks_free() -> None:
    """Il giro fra il rilascio e la ripresa di chi aspettava.

    ``asyncio.Lock.release`` libera il lock e sveglia il primo in attesa, che lo
    prende solo quando il loop gli ridà il passo: in mezzo ``locked()`` è falso.
    Chi chiede «qualcuno ha in mano questa sessione?» in quel giro non deve
    sentirsi dire di no — chi aspettava sta per scriverci.
    """
    locks = SessionLocks()
    lock = locks.get("project:orto")
    inside, leave = asyncio.Event(), asyncio.Event()

    async def waiter() -> None:
        async with lock:
            inside.set()
            await leave.wait()

    async with lock:
        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)  # il secondo è in coda sul lock
        assert locks.busy_keys() == ("project:orto",)
    # Rilasciato, e chi aspettava non ha ancora ripreso.
    assert not lock.locked()
    assert not inside.is_set()
    assert locks.busy_keys() == ("project:orto",)

    await inside.wait()
    assert locks.busy_keys() == ("project:orto",)
    leave.set()
    await task
    assert locks.busy_keys() == ()


async def test_a_waiter_cancelled_in_the_queue_does_not_stay_busy() -> None:
    locks = SessionLocks()
    lock = locks.get("project:orto")

    async def waiter() -> None:
        async with lock:
            pass

    async with lock:
        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert locks.busy_keys() == ()


async def test_a_rotated_lock_is_not_busy_for_its_zombie() -> None:
    """Il turno abbandonato tiene il lock vecchio, che non è più nel registro:
    la sessione non resta occupata per sempre dietro di lui."""
    locks = SessionLocks()
    async with locks.get("project:orto"):
        assert locks.rotate("project:orto") is True
        assert locks.busy_keys() == ()

