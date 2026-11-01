"""Guardia sull'avanzamento del cursore Dream nel ``CronDispatcher``.

Il contratto: il cursore avanza solo se il turno Dream è completato pulito
**e** ha davvero scritto (o non ha mai provato a scrivere). Se ogni tentativo
di scrittura è stato bloccato dalla policy, avanzare perderebbe per sempre
quelle voci di ``history.jsonl``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from jenny.agent.memory import DREAM_HISTORY_HEADER
from jenny.agent.tools.file_state import FileStates
from jenny.config.schema import Config
from jenny.runtime.cron_dispatch import CronDispatcher

_DREAM_JOB = SimpleNamespace(
    name="dream", id="dream", payload=SimpleNamespace(kind="system_event")
)


class _FakeMemory:
    """Memory store minimale: espone il registry Dream con i suoi ``file_states``."""

    # ``budget_report`` misura questi tre file: non esistono, e va bene —
    # ``count_chars`` legge 0 su file assente, e con i budget a 0 del default
    # nessuno puo' risultare oltre soglia.
    memory_file = Path("no-such-MEMORY.md")
    user_file = Path("no-such-USER.md")
    soul_file = Path("no-such-SOUL.md")

    def get_review_state(self) -> tuple[int, int]:
        return (0, 0)

    _nothing_new = 0

    def get_nothing_new_runs(self) -> int:
        return self._nothing_new

    def get_review_forced_at_stuck(self) -> int:
        return 0

    def set_review_state(self, **_kwargs) -> None:
        pass

    def __init__(
        self,
        file_states: FileStates | None,
        *,
        prompt: str = "prompt di consolidamento",
        on_turn=None,
        entries=None,
    ) -> None:
        self.cursor: int | None = None
        # ``memory_entries`` è l'esito del run in voci. Assente di default, come
        # nei doppi che non lo espongono: quel caso deve continuare a funzionare
        # ricadendo sulla misura delle dimensioni.
        self.tools = SimpleNamespace(file_states=file_states)
        if entries is not None:
            self.tools.memory_entries = entries
        self._prompt = prompt
        # Cosa fa il turno ai file misurati, se fa qualcosa: serve a distinguere
        # un run che ha fatto atterrare il batch da uno che ha solo potato.
        self.on_turn = on_turn

    def build_dream_prompt(self, **_kwargs):
        return (self._prompt, 42)

    def build_dream_tools(self, **_kwargs):
        return self.tools

    def set_last_dream_cursor(self, cursor: int) -> None:
        self.cursor = cursor

    def get_last_dream_cursor(self) -> int:
        return 7

    def compact_history(self) -> None:
        pass


class _FakeAgent:
    def __init__(self, sessions_dir: Path, memory: _FakeMemory, stop_reason: str) -> None:
        self.context = SimpleNamespace(memory=memory)
        self.sessions = SimpleNamespace(sessions_dir=sessions_dir)
        self._stop_reason = stop_reason

    async def process_direct(self, prompt: str, **_kwargs):
        on_turn = getattr(self.context.memory, "on_turn", None)
        if on_turn is not None:
            on_turn()
        return SimpleNamespace(metadata={"_stop_reason": self._stop_reason}, usage={})

    def evict_pruned_sessions(self, keys) -> None:
        pass


def _dispatch(
    tmp_path: Path,
    file_states: FileStates | None,
    stop_reason: str,
    *,
    memory: "_FakeMemory | None" = None,
) -> tuple[_FakeMemory, CronDispatcher]:
    memory = memory if memory is not None else _FakeMemory(file_states)
    dispatcher = CronDispatcher(
        get_agent=lambda: _FakeAgent(tmp_path, memory, stop_reason),
        config=Config(),
        cron=MagicMock(),
    )
    return memory, dispatcher


async def test_cursor_advances_when_dream_wrote(tmp_path: Path) -> None:
    file_states = FileStates()
    file_states.record_write_attempt()
    file_states.record_write(tmp_path / "written.md")
    memory, dispatcher = _dispatch(tmp_path, file_states, "completed")

    await dispatcher.dispatch(_DREAM_JOB)

    assert memory.cursor == 42


async def test_cursor_advances_when_nothing_was_attempted(tmp_path: Path) -> None:
    """Nulla da consolidare: nessun tentativo di scrittura, si avanza."""
    memory, dispatcher = _dispatch(tmp_path, FileStates(), "completed")

    await dispatcher.dispatch(_DREAM_JOB)

    assert memory.cursor == 42


async def test_cursor_held_when_every_write_was_blocked(tmp_path: Path) -> None:
    """Regressione: turno "completed" ma scritture tutte bloccate → non avanzare."""
    file_states = FileStates()
    file_states.record_write_attempt()
    memory, dispatcher = _dispatch(tmp_path, file_states, "completed")

    await dispatcher.dispatch(_DREAM_JOB)

    assert memory.cursor is None


async def test_cursor_held_when_turn_did_not_complete(tmp_path: Path) -> None:
    file_states = FileStates()
    file_states.record_write_attempt()
    file_states.record_write(tmp_path / "written.md")
    memory, dispatcher = _dispatch(tmp_path, file_states, "error")

    await dispatcher.dispatch(_DREAM_JOB)

    assert memory.cursor is None


# ---------------------------------------------------------------------------
# "Ha scritto" non è "il batch è atterrato" — il buco misurato il 2026-08-18
# ---------------------------------------------------------------------------

# Un batch con la firma di chi chiede di essere salvato, nella forma che il prompt
# di Dream gli dà davvero (header incluso: senza, il ritaglio non trova storia).
_BATCH_WITH_FACTS = (
    "template di Dream"
    + DREAM_HISTORY_HEADER
    + "[2026-08-18 11:02] - [permanent] Preferisce le riunioni corte del mattino"
)


def _wrote_something(tmp_path: Path) -> FileStates:
    file_states = FileStates()
    file_states.record_write_attempt()
    file_states.record_write(tmp_path / "written.md")
    return file_states


async def test_cursor_held_when_the_batch_did_not_land(tmp_path: Path) -> None:
    """Il run di 12:01: scrive, accorcia un file, non aggiunge il fatto nuovo.

    Ogni contatore lo dichiara sano — ``writes_ok == 1``, zero rifiuti, ``stuck``
    a 0 — ed è per questo che passava. Il cursore ora resta fermo e quelle voci
    tornano al run seguente.
    """
    memory = _FakeMemory(_wrote_something(tmp_path), prompt=_BATCH_WITH_FACTS)
    memory.user_file = tmp_path / "USER.md"
    memory.user_file.write_text("y" * 2990, encoding="utf-8")  # 99% di 3.000
    memory.on_turn = lambda: memory.user_file.write_text("y" * 2963, encoding="utf-8")
    _, dispatcher = _dispatch(tmp_path, None, "completed", memory=memory)

    await dispatcher.dispatch(_DREAM_JOB)

    assert memory.cursor is None


async def test_cursor_advances_when_the_batch_landed(tmp_path: Path) -> None:
    """Stesso batch, ma un file cresce: consolidamento riuscito, si avanza."""
    memory = _FakeMemory(_wrote_something(tmp_path), prompt=_BATCH_WITH_FACTS)
    memory.user_file = tmp_path / "USER.md"
    memory.user_file.write_text("y" * 2990, encoding="utf-8")
    memory.on_turn = lambda: memory.user_file.write_text("y" * 2999, encoding="utf-8")
    _, dispatcher = _dispatch(tmp_path, None, "completed", memory=memory)

    await dispatcher.dispatch(_DREAM_JOB)

    assert memory.cursor == 42


async def test_a_batch_with_nothing_to_save_still_advances(tmp_path: Path) -> None:
    """Il freno: su un batch `(nothing)` un run che non aggiunge non è un errore.

    Senza questo ramo un'installazione in pari resterebbe bloccata a replayare
    per sempre un batch che non chiede niente.
    """
    memory = _FakeMemory(
        _wrote_something(tmp_path),
        prompt="template" + DREAM_HISTORY_HEADER + "[2026-08-17 17:24] (nothing)",
    )
    memory.user_file = tmp_path / "USER.md"
    memory.user_file.write_text("y" * 2990, encoding="utf-8")
    memory.on_turn = lambda: memory.user_file.write_text("y" * 2963, encoding="utf-8")
    _, dispatcher = _dispatch(tmp_path, None, "completed", memory=memory)

    await dispatcher.dispatch(_DREAM_JOB)

    assert memory.cursor == 42


async def test_a_batch_already_on_disk_advances(tmp_path: Path) -> None:
    """Il falso positivo trovato on-device il 2026-08-18 alle 14:01, chiuso in un
    altro modo.

    Batch di fatti già tutti su disco — la Consolidator li ri-estrae a ogni giro
    — e modello che non aggiunge niente: decisione corretta, e prima costava
    quattro run di replay a vuoto più un review pass forzato su file che non
    avevano niente da liberare. La difesa era una soglia di riempimento tarata su
    tre osservazioni. Ora è il tool a dire che quel contenuto è in memoria, il che
    è la stessa conclusione senza il numero inventato.
    """
    entries = SimpleNamespace(
        entries_added=0, entries_replaced=0, entries_already_present=6,
    )
    memory = _FakeMemory(
        _wrote_something(tmp_path), prompt=_BATCH_WITH_FACTS, entries=entries,
    )
    memory.user_file = tmp_path / "USER.md"
    memory.user_file.write_text("y" * 2316, encoding="utf-8")
    memory.on_turn = lambda: memory.user_file.write_text("y" * 2300, encoding="utf-8")
    _, dispatcher = _dispatch(tmp_path, None, "completed", memory=memory)

    await dispatcher.dispatch(_DREAM_JOB)

    assert memory.cursor == 42


async def test_an_added_entry_advances_even_if_the_file_shrank(tmp_path: Path) -> None:
    """Il falso negativo dichiarato del vecchio metodo, end-to-end.

    Una voce entra mentre il file cala nello stesso turno — una correzione più
    corta che si porta dentro il fatto nuovo. La dimensione la leggeva come
    "niente è atterrato", e il costo di quel verso dell'errore è un batch
    rigiocato per niente.
    """
    entries = SimpleNamespace(
        entries_added=1, entries_replaced=0, entries_already_present=0,
    )
    memory = _FakeMemory(
        _wrote_something(tmp_path), prompt=_BATCH_WITH_FACTS, entries=entries,
    )
    memory.user_file = tmp_path / "USER.md"
    memory.user_file.write_text("y" * 2316, encoding="utf-8")
    memory.on_turn = lambda: memory.user_file.write_text("y" * 2000, encoding="utf-8")
    _, dispatcher = _dispatch(tmp_path, None, "completed", memory=memory)

    await dispatcher.dispatch(_DREAM_JOB)

    assert memory.cursor == 42


async def test_a_run_that_reports_no_entry_at_all_is_held(tmp_path: Path) -> None:
    """L'allargamento noto, messo per iscritto invece che scoperto sul telefono.

    Senza la soglia, un run che porta fatti e non produce **nessuna** voce — né
    aggiunta, né già presente — viene trattenuto anche a riempimento basso, dove
    prima veniva creduto. È il caso che la fase 5 del piano separerà davvero; qui
    costa al massimo quattro run, il log lo dice ogni volta, e il prompt ora
    chiede di chiamare ``add`` anche solo per sentirsi rispondere "already
    present", che è la chiamata che chiude il caso.
    """
    entries = SimpleNamespace(
        entries_added=0, entries_replaced=0, entries_already_present=0,
    )
    memory = _FakeMemory(
        _wrote_something(tmp_path), prompt=_BATCH_WITH_FACTS, entries=entries,
    )  # ``_wrote_something``: il run ha tentato una scrittura, quindi il freno
    # ``attempted`` non lo salva — ed è giusto, è il caso delle 12:01.
    memory.user_file = tmp_path / "USER.md"
    memory.user_file.write_text("y" * 2316, encoding="utf-8")
    memory.on_turn = lambda: memory.user_file.write_text("y" * 2300, encoding="utf-8")
    _, dispatcher = _dispatch(tmp_path, None, "completed", memory=memory)

    await dispatcher.dispatch(_DREAM_JOB)

    assert memory.cursor is None


async def test_a_held_batch_logs_exactly_one_outcome(tmp_path: Path) -> None:
    """Regressione su un difetto che solo il device ha mostrato.

    La prima stesura teneva il ramo del batch a parte, quindi il ramo dei rifiuti
    scattava comunque: in logcat, alle 14:02:35, uscivano due WARNING sullo stesso
    run e il secondo diceva "attempts blocked/refused" su un run in cui nessuna
    scrittura era stata né bloccata né rifiutata. Una diagnosi falsa accanto a una
    vera manda chi legge a controllare la cosa sbagliata, che è esattamente il
    difetto per cui il testo dell'allarme è già stato riscritto due volte.
    """
    from loguru import logger

    memory = _FakeMemory(_wrote_something(tmp_path), prompt=_BATCH_WITH_FACTS)
    memory.user_file = tmp_path / "USER.md"
    memory.user_file.write_text("y" * 2990, encoding="utf-8")
    memory.on_turn = lambda: memory.user_file.write_text("y" * 2963, encoding="utf-8")
    _, dispatcher = _dispatch(tmp_path, None, "completed", memory=memory)

    records: list[str] = []
    sink_id = logger.add(lambda m: records.append(m.record["message"]), level="INFO")
    try:
        await dispatcher.dispatch(_DREAM_JOB)
    finally:
        logger.remove(sink_id)

    outcomes = [
        r for r in records
        if "cursor held at" in r or "cursor remains at" in r or "cursor advanced to" in r
    ]
    assert len(outcomes) == 1, outcomes
    assert "cursor held at" in outcomes[0]
    assert "blocked/refused" not in outcomes[0]
    assert "wrote to disk" not in outcomes[0]
