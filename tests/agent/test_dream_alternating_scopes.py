"""Dream progredisce anche quando gli ambiti si alternano.

Un batch di Dream ha un tipo solo — personale o di progetto — perché i due tipi
hanno prompt, cassetta e destinazioni diverse. Fino al 26/09 il batch si fermava
al primo cambio di tipo e il cursore, unico, avanzava fin lì: con la chat
personale e un quaderno usati a turno ogni run digeriva **una** voce, mentre il
diario ne riceveva di più, e ``compact_history`` — che teneva le ultime
``max_history_entries`` senza guardare il cursore — finiva per buttare voci che
Dream non aveva mai letto.

Ora un run lavora su una finestra sola in due batch, uno per tipo, e il cursore
va a fine finestra solo quando atterrano entrambi; ``compact_history`` non tocca
le voci che Dream deve ancora leggere.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jenny.agent.dream_cycle import DreamOutcome, DreamPrologue, run_dream_turn
from jenny.agent.memory import MemoryStore
from jenny.agent.memory_budget import make_write_size_guard

PERSONAL = "unified:default"
PROJECT = "project:esempio"
CRON = "cron:job-1"

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")


def _alternate(store: MemoryStore, pairs: int) -> None:
    for i in range(pairs):
        store.append_history(f"- [durable] personale {i:02d}", session_key=PERSONAL)
        store.append_history(f"- [durable] progetto {i:02d}", session_key=PROJECT)


class _Agent:
    """Un agente che chiude ogni turno pulito senza scrivere niente.

    ``completed`` e zero tentativi di scrittura è il caso «non c'era niente da
    cambiare», che per ``internal_run_should_commit`` fa avanzare il cursore.
    *fail_on* è il numero del turno (da 1) che invece non si chiude.
    """

    def __init__(self, *, fail_on: int | None = None) -> None:
        self.prompts: list[str] = []
        self.fail_on = fail_on

    async def process_direct(self, prompt: str, **_kwargs: Any):
        self.prompts.append(prompt)
        reason = "error" if len(self.prompts) == self.fail_on else "completed"
        return SimpleNamespace(metadata={"_stop_reason": reason}, usage={})


def _prologue() -> DreamPrologue:
    return DreamPrologue(
        report=[], guard=make_write_size_guard([]), runs_since_review=0, stuck=0,
        review=None,
    )


class TestTheBatch:
    def test_the_head_batch_takes_every_entry_of_its_kind_in_the_window(self, tmp_path: Path):
        store = MemoryStore(tmp_path)
        _alternate(store, 10)

        head = store.build_dream_prompt()

        assert head is not None
        assert head.scope == "personal"
        history = MemoryStore.dream_prompt_history(head.prompt)
        assert all(f"personale {i:02d}" in history for i in range(10))
        assert "progetto" not in history
        # Da solo, il batch può dichiarare digerita solo la testa omogenea.
        assert head.cursor == 1
        assert head.rest_scope == "project"
        assert head.window_cursor == 20

    def test_the_second_batch_covers_the_other_kind_up_to_the_window_end(self, tmp_path: Path):
        store = MemoryStore(tmp_path)
        _alternate(store, 10)
        head = store.build_dream_prompt()
        assert head is not None
        # Una voce arrivata fra i due batch non entra nella finestra.
        store.append_history("- [durable] progetto tardivo", session_key=PROJECT)

        rest = store.build_dream_prompt(scope="project", until_cursor=head.window_cursor)

        assert rest is not None
        assert rest.scope == "project"
        history = MemoryStore.dream_prompt_history(rest.prompt)
        assert all(f"progetto {i:02d}" in history for i in range(10))
        assert "personale" not in history
        assert "tardivo" not in history
        assert rest.cursor == 20

    def test_a_single_kind_window_has_no_rest(self, tmp_path: Path):
        store = MemoryStore(tmp_path)
        for i in range(3):
            store.append_history(f"personale {i}", session_key=PERSONAL)
        head = store.build_dream_prompt()
        assert head is not None
        assert head.rest_scope is None
        assert head.cursor == head.window_cursor == 3


class TestTheRun:
    async def test_alternating_kinds_are_drained_in_one_run(self, tmp_path: Path):
        store = MemoryStore(tmp_path)
        _alternate(store, 10)
        agent = _Agent()

        result = await run_dream_turn(agent, store, _prologue(), take_snapshot=None)

        assert result.outcome is DreamOutcome.ADVANCED
        assert len(agent.prompts) == 2
        assert store.get_last_dream_cursor() == 20
        assert result.last_cursor == 20
        second = MemoryStore.dream_prompt_history(agent.prompts[1])
        assert "progetto 09" in second and "personale" not in second

    async def test_a_failed_second_batch_keeps_only_what_the_first_covered(
        self, tmp_path: Path,
    ):
        store = MemoryStore(tmp_path)
        _alternate(store, 3)
        agent = _Agent(fail_on=2)

        result = await run_dream_turn(agent, store, _prologue(), take_snapshot=None)

        assert result.outcome is DreamOutcome.ADVANCED
        # Solo la testa omogenea del primo batch: la prima voce di progetto torna.
        assert store.get_last_dream_cursor() == 1
        nxt = store.build_dream_prompt()
        assert nxt is not None and nxt.scope == "project"

    @pytest.mark.parametrize(
        "exc", [RuntimeError("boom"), asyncio.CancelledError()], ids=["error", "cancelled"],
    )
    async def test_a_second_batch_that_raises_keeps_the_first_cursor(
        self, tmp_path: Path, exc: BaseException,
    ):
        """Il primo batch è atterrato nei file: il suo cursore non dipende dal secondo.

        Scritto solo a fine run, un secondo batch che solleva — un errore, o il
        run cancellato — lasciava il cursore dov'era, e il run dopo rifaceva da
        capo il batch già scritto.
        """
        store = MemoryStore(tmp_path)
        _alternate(store, 3)

        class _Raising(_Agent):
            async def process_direct(self, prompt: str, **kwargs: Any):
                if len(self.prompts) == 1:
                    self.prompts.append(prompt)
                    raise exc
                return await super().process_direct(prompt, **kwargs)

        agent = _Raising()
        with pytest.raises(type(exc)):
            await run_dream_turn(agent, store, _prologue(), take_snapshot=None)

        assert len(agent.prompts) == 2
        assert store.get_last_dream_cursor() == 1
        # Il secondo batch ha visto la stessa finestra di sempre: tutte le voci
        # di progetto, nessuna personale.
        second = MemoryStore.dream_prompt_history(agent.prompts[1])
        assert "progetto 02" in second and "personale" not in second

    async def test_a_failed_first_batch_does_not_start_the_second(self, tmp_path: Path):
        store = MemoryStore(tmp_path)
        _alternate(store, 3)
        agent = _Agent(fail_on=1)

        result = await run_dream_turn(agent, store, _prologue(), take_snapshot=None)

        assert result.outcome is DreamOutcome.INCOMPLETE
        assert len(agent.prompts) == 1
        assert store.get_last_dream_cursor() == 0


class TestCompactHistory:
    def test_it_keeps_what_dream_has_not_read(self, tmp_path: Path):
        store = MemoryStore(tmp_path, max_history_entries=5)
        for i in range(8):
            store.append_history(f"- [durable] fatto {i}", session_key=PERSONAL)

        store.compact_history()

        left = [e["cursor"] for e in store.read_unprocessed_history(0)]
        assert left == list(range(1, 9))

    def test_it_still_drops_what_dream_already_consumed(self, tmp_path: Path):
        store = MemoryStore(tmp_path, max_history_entries=5)
        for i in range(8):
            store.append_history(f"- [durable] fatto {i}", session_key=PERSONAL)
        store.set_last_dream_cursor(6)

        store.compact_history()

        left = [e["cursor"] for e in store.read_unprocessed_history(0)]
        assert left == [4, 5, 6, 7, 8]
        archived = "".join(
            p.read_text(encoding="utf-8")
            for p in (tmp_path / "memory" / "history-archive").glob("*.jsonl")
        )
        assert [json.loads(line)["cursor"] for line in archived.splitlines()] == [1, 2, 3]

    def test_internal_entries_are_not_held_back(self, tmp_path: Path):
        """Dream non legge le voci interne: tenerle vorrebbe dire non tagliarle mai."""
        store = MemoryStore(tmp_path, max_history_entries=3)
        for i in range(6):
            store.append_history(f"heartbeat {i}", session_key=CRON)

        store.compact_history()

        left = [e["cursor"] for e in store.read_unprocessed_history(0)]
        assert left == [4, 5, 6]
