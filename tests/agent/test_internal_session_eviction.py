"""Lo sfratto delle sessioni interne, e il quarto registro che dimenticava.

**T2.11**, seguito diretto di T2.5. ``AgentLoop`` tiene quattro registri indicizzati
per chiave di sessione, e ``evict_pruned_sessions`` ne sgomberava **tre**:
``_active_tasks``, ``_session_locks`` e la cache di ``SessionManager``. Il quarto —
``_file_state_store`` — restava, e ``AgentLoop`` una voce ce la mette *sempre*, a
ogni turno. Ogni run interno che conia una chiave nuova lasciava quindi una voce
morta per la vita del processo.

**Chi conia davvero una chiave per esecuzione, misurato e non dedotto:**

| job | chiave | quante volte |
|---|---|---|
| Dream | ``dream:<timestamp>`` | ogni 2 h (``DreamConfig.interval_h``) |
| giardiniere | ``gardener:<progetto>-<timestamp>`` | fino a una per progetto ogni 6 h |
| cron | ``cron:<job_id>`` | **una sola, stabile** |

L'ultima riga è la correzione: le sue chiavi non portano timestamp, quindi
il loro spazio è finito per costruzione e non entrano in questo discorso.

**Due rimedi, e non uno**, perché la potatura sgombera le chiavi *potate*:

1. la riga in ``evict_pruned_sessions`` rende lo spazio **limitato** — con
   ``keep=10`` restano dieci voci più quella in corso, invece di una per run;
2. chi non può aspettare dieci run se la dimentica da sé nel proprio ``finally``
   (il giardiniere: provato in ``test_gardener.py``, T2.5).

Per Dream il ritardo di dieci run resta, ed è una decisione: gira ogni due ore, quindi
dieci run sono meno di un giorno, e la sua chiave la conia ``MemoryStore`` — un file
di cui questo cambiamento non ha bisogno.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.agent.loop import AgentLoop
from jenny.agent.loop_tasks import LoopTasksMixin
from jenny.agent.session_locks import SessionLocks
from jenny.agent.tools.file_state import FileStateStore

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")


class _RegistryAgent:
    """Un agente coi **registri veri** di ``AgentLoop``, e nient'altro.

    Stessa costruzione del ``_RegistryAgent`` di ``test_gardener.py`` (T2.5), qui
    per i run interni: le classi dei registri sono quelle di produzione e i due metodi di
    sgombero sono le funzioni di ``AgentLoop`` prese come sono. Quel che il fake
    fa a mano sono le due righe che ``AgentLoop`` esegue all'ingresso di ogni
    turno — ``_session_locks.get(key)`` e ``_file_state_store.for_session(key)``
    — perché sono loro a creare la voce che si sta contando.
    """

    evict_pruned_sessions = LoopTasksMixin.evict_pruned_sessions
    forget_file_reads = AgentLoop.forget_file_reads

    def __init__(self, sessions_dir: Path) -> None:
        self._session_locks = SessionLocks()
        self._file_state_store = FileStateStore()
        self._active_tasks: dict[str, list] = {}
        self.sessions = SimpleNamespace(
            sessions_dir=sessions_dir, invalidate=lambda _key: None
        )
        self.context = SimpleNamespace(timezone=None)
        self.calls: list[str] = []

    async def process_direct(self, prompt: str, **kwargs):  # noqa: ARG002
        key = kwargs.get("session_key")
        assert isinstance(key, str)
        self.calls.append(key)
        self._session_locks.get(key)
        self._file_state_store.for_session(key)
        return SimpleNamespace(metadata={"_stop_reason": "completed"})

    @property
    def file_state_keys(self) -> int:
        return len(self._file_state_store._states_by_key)


# ── Il quarto registro ───────────────────────────────────────────────────────


def test_the_eviction_drops_the_file_state_entry_too(tmp_path: Path) -> None:
    """La riga che T2.11 aggiunge, misurata da sola.

    Le chiavi qui sono quelle vere di Dream e del giardiniere: la potatura deve
    raggiungere anche una chiave che il suo run non ha fatto in tempo a scordare.
    """
    agent = _RegistryAgent(tmp_path)
    keys = ["dream:20260823-020000", "gardener:orto-20260823-030000"]
    for key in keys:
        agent._file_state_store.for_session(key)
        agent._session_locks.get(key)

    assert agent.file_state_keys == 2

    agent.evict_pruned_sessions(keys)

    assert agent.file_state_keys == 0
    assert agent._session_locks._locks == {}


def test_a_key_with_work_in_flight_is_left_alone(tmp_path: Path) -> None:
    """Il cancello che c'era prima non è stato allargato: una sessione con il lock
    in mano non si sfratta, e il ``FileStateStore`` non deve fare eccezione — sono
    le letture di un turno **vivo**."""
    agent = _RegistryAgent(tmp_path)
    key = "dream:20260823-020000"
    agent._file_state_store.for_session(key)
    lock = agent._session_locks.get(key)

    async def _hold() -> None:
        async with lock:
            agent.evict_pruned_sessions([key])

    import asyncio

    asyncio.run(_hold())

    assert agent.file_state_keys == 1


def test_dream_keys_become_bounded_instead_of_disappearing(tmp_path: Path) -> None:
    """Per Dream il rimedio è il **tetto**, non lo sfratto immediato: è la decisione.

    Venti run lasciano dieci voci e non venti — e le dieci sono il ``keep`` che la
    potatura ha già scelto. Con Dream ogni due ore quel ritardo è meno di un
    giorno, e la sua chiave la conia ``MemoryStore``: portarci un ``finally``
    vorrebbe dire toccare quel file per 1,5 kB di byte morti.
    """
    from jenny.agent.memory import MemoryStore

    agent = _RegistryAgent(tmp_path)
    base = datetime(2026, 8, 23, 2, 0, 0)
    for run in range(20):
        stamp = (base + timedelta(hours=2 * run)).strftime("%Y%m%d-%H%M%S")
        path = tmp_path / f"dream_{stamp}.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        # mtime a mano: venti write nello stesso secondo non hanno un ordine.
        import os

        os.utime(path, (run + 1, run + 1))
        agent._file_state_store.for_session(f"dream:{stamp}")

    assert agent.file_state_keys == 20

    agent.evict_pruned_sessions(MemoryStore.prune_dream_sessions(tmp_path))

    assert agent.file_state_keys == 10
