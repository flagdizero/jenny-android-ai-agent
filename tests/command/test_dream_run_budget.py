"""Il budget di memoria dentro il `/dream` manuale.

Il budget e il review pass erano cablati sul solo percorso cron. `/dream`
lanciato a mano costruiva i tool di Dream **senza** il guard, il prompt senza
gauge, e non leggeva né scriveva lo stato del review: un'intera feature di
enforcement con una porta di servizio aperta, e per giunta annunciata — `/dream
budget` stampa all'utente tetti e percentuali che l'altra metà dello stesso
comando ignorava.

Il test che conta è il primo: una scrittura che sfora, lanciata da `/dream`,
deve essere rifiutata. Il secondo che conta è l'ultimo,
``TestTheShippedDefaultsEnforce``: da quando i tetti di ``MEMORY.md`` e
``USER.md`` valgono 2.000 il rifiuto non aspetta più che qualcuno configuri
qualcosa, e ``SOUL.md`` è l'unico rimasto a "misurato ma non applicato".
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jenny.agent.memory import MemoryStore
from jenny.bus.events import InboundMessage
from jenny.command.builtin import register_builtin_commands
from jenny.command.router import CommandContext, CommandRouter
from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config
from jenny.utils.helpers import sync_workspace_templates

_REVIEW_TARGET = "jenny.agent.dream_review.run_dream_review"

# Il seme di MEMORY.md è ciò che il "modello" sostituisce nei test di scrittura:
# ``edit_file`` rifiuta ``old_text=""`` su un file non vuoto, quindi serve un
# ancoraggio reale invece di un append.
_SEED = "- seed fact"
_MEMORY_TEXT = f"# Memory\n{_SEED}\n"


@pytest.fixture()
def router() -> CommandRouter:
    r = CommandRouter()
    register_builtin_commands(r)
    return r


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Workspace vero con i template estratti e un `config.json` di default.

    Come in ``test_dream_budget_command.py``: ``render_template`` legge da
    ``get_workspace_path()``, non dal package, e l'ambiente Jinja è memoizzato
    per processo.
    """
    from jenny.runtime.context import get_runtime_context
    from jenny.utils import prompt_templates

    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    sync_workspace_templates(ws, silent=True)

    ctx = get_runtime_context()
    monkeypatch.setattr(ctx, "workspace_dir", ws)
    monkeypatch.setattr(ctx, "config_path", ws / "config.json")
    prompt_templates._environment.cache_clear()
    save_config(Config(), ws / "config.json")
    yield ws
    prompt_templates._environment.cache_clear()


@pytest.fixture()
def memory(workspace: Path) -> MemoryStore:
    store = MemoryStore(workspace)
    store.memory_file.write_text(_MEMORY_TEXT, encoding="utf-8")
    store.user_file.write_text("# User\n", encoding="utf-8")
    store.soul_file.write_text("# Soul\n", encoding="utf-8")
    # Senza storia da processare ``build_dream_prompt`` ritorna None e il run
    # incrementale non parte affatto.
    store.append_history("user: ricordati che il gateway gira su Android")
    return store


class _FakeLoop:
    """Il minimo di ``AgentLoop`` che il ramo senza argomento di `/dream` tocca.

    ``on_turn`` è il posto del modello: riceve il registry di tool costruito per
    il run, così un test può far *davvero* tentare una scrittura e osservare
    cosa il guard risponde. È l'unico modo di verificare l'enforcement da questo
    percorso invece di verificarne il cablaggio contro se stesso.
    """

    def __init__(self, workspace: Path, memory: MemoryStore) -> None:
        self.bus = SimpleNamespace(publish_outbound=self._publish)
        self.context = SimpleNamespace(memory=memory, timezone=None)
        self.sessions = SimpleNamespace(sessions_dir=workspace / "sessions")
        self.published: list[Any] = []
        self.prompts: list[str] = []
        self.tool_results: list[str] = []
        self.on_turn = None
        self.stop_reason = "completed"

    async def _publish(self, message: Any) -> None:
        self.published.append(message)

    async def process_direct(self, prompt: str, *, tools: Any = None, **_kwargs: Any):
        self.prompts.append(prompt)
        if self.on_turn is not None and tools is not None:
            await self.on_turn(tools)
        return SimpleNamespace(
            content="done", metadata={"_stop_reason": self.stop_reason}
        )

    def evict_pruned_sessions(self, _keys: Any) -> None:
        pass


@pytest.fixture()
def loop(workspace: Path, memory: MemoryStore) -> _FakeLoop:
    return _FakeLoop(workspace, memory)


class _ReviewSpy:
    """Sostituto di ``run_dream_review`` che registra cosa gli è arrivato."""

    def __init__(
        self,
        *,
        shrink_to: str | None = None,
        status: str = "completed",
        demoted_ids: tuple[str, ...] = (),
        unresolved_refusals: int = 0,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._shrink_to = shrink_to
        self._status = status
        self._demoted_ids = demoted_ids
        self._unresolved_refusals = unresolved_refusals

    async def __call__(self, agent, *, store, report, snapshotted, write_size_guard=None):
        self.calls.append({
            "agent": agent,
            "report": report,
            "snapshotted": snapshotted,
            "guard": write_size_guard,
        })
        before = {item.label: item.chars for item in report}
        if self._shrink_to is not None:
            store.memory_file.write_text(self._shrink_to, encoding="utf-8")
        after = {item.label: len(item.path.read_text(encoding="utf-8")) for item in report}
        return SimpleNamespace(
            status=self._status,
            before=before,
            after=after,
            freed=sum(before[label] - after[label] for label in before),
            demoted_ids=self._demoted_ids,
            unresolved_refusals=self._unresolved_refusals,
        )

    @property
    def ran(self) -> bool:
        return bool(self.calls)


def _install_review(monkeypatch: pytest.MonkeyPatch, spy: _ReviewSpy) -> None:
    monkeypatch.setattr(_REVIEW_TARGET, spy)


def _set_dream_config(workspace: Path, **fields: int) -> None:
    path = workspace / "config.json"
    config = load_config(path)
    for name, value in fields.items():
        setattr(config.agents.defaults.dream, name, value)
    save_config(config, path)


def _ctx(loop: _FakeLoop, raw: str) -> CommandContext:
    args = raw[len("/dream"):].strip()
    msg = InboundMessage(
        channel="websocket", sender_id="u", chat_id="default", content=raw
    )
    return CommandContext(msg=msg, session=None, key="k", raw=raw, args=args, loop=loop)


async def _drain(loop: _FakeLoop, *, timeout: float = 5.0) -> None:
    """Attende la risposta del task fire-and-forget lanciato da `/dream`.

    La pubblicazione è l'ultima cosa che il run fa (il ramo "niente da
    processare" pubblica prima del ``finally``, che però non tocca più lo stato
    osservato dai test), quindi aspettarla equivale ad aspettare il run.
    """
    deadline = time.monotonic() + timeout
    while not loop.published and time.monotonic() < deadline:
        await asyncio.sleep(0.005)
    assert loop.published, "`/dream` non ha pubblicato nessuna risposta"


def _replace_seed(memory: MemoryStore, new_text: str):
    """Un "modello" che sostituisce il seme di MEMORY.md con *new_text*."""

    async def turn(tools) -> None:
        tool = tools.get("edit_file")
        assert tool is not None
        result = await tool.execute(
            path=str(memory.memory_file), old_text=_SEED, new_text=new_text
        )
        turn.results.append(result)  # type: ignore[attr-defined]

    turn.results = []  # type: ignore[attr-defined]
    return turn


# ---------------------------------------------------------------------------
# 1. L'enforcement
# ---------------------------------------------------------------------------


class TestTheBackDoor:
    """Il test che conta: `/dream` a mano non deve poter aggirare il budget."""

    @pytest.mark.asyncio
    async def test_a_write_over_budget_is_refused(self, router, loop, memory, workspace):
        _set_dream_config(workspace, memory_budget_chars=50)
        turn = _replace_seed(memory, "x" * 300)
        loop.on_turn = turn

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert turn.results and "Write refused" in turn.results[0]
        assert "over its 50 char budget" in turn.results[0]
        # E il rifiuto non è solo una frase: il file su disco è intatto.
        assert memory.memory_file.read_text(encoding="utf-8") == _MEMORY_TEXT

    @pytest.mark.asyncio
    async def test_a_write_within_budget_still_lands(
        self, router, loop, memory, workspace
    ):
        """Il guard rifiuta chi sfora, non chi scrive."""
        _set_dream_config(workspace, memory_budget_chars=500)
        turn = _replace_seed(memory, "- gateway runs on Android")
        loop.on_turn = turn

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert turn.results and "Write refused" not in turn.results[0]
        assert "gateway runs on Android" in memory.memory_file.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_a_refused_write_leaves_the_cursor_where_it_was(
        self, router, loop, memory, workspace
    ):
        """Il fatto non è stato consolidato: avanzare lo perderebbe."""
        _set_dream_config(workspace, memory_budget_chars=50)
        loop.on_turn = _replace_seed(memory, "x" * 300)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert memory.get_last_dream_cursor() == 0
        assert "wrote nothing" in loop.published[0].content


# ---------------------------------------------------------------------------
# 2. Il gauge
# ---------------------------------------------------------------------------


class TestGauge:
    @pytest.mark.asyncio
    async def test_the_prompt_carries_the_gauge(self, router, loop, memory, workspace):
        """Senza la misura il modello non sa quanto spazio ha."""
        _set_dream_config(workspace, memory_budget_chars=500)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        prompt = loop.prompts[0]
        assert "Long-term memory budget (characters)" in prompt
        assert f"MEMORY.md [{len(_MEMORY_TEXT) * 100 // 500}% — {len(_MEMORY_TEXT)}/500" in prompt


# ---------------------------------------------------------------------------
# 3-4. I contatori
# ---------------------------------------------------------------------------


class TestReviewCounters:
    @pytest.mark.asyncio
    async def test_runs_since_review_advances(self, router, loop, memory):
        """Senza questo, su un'installazione a `/dream` manuale il review non parte mai."""
        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert memory.get_review_state() == (1, 0)

    @pytest.mark.asyncio
    async def test_stuck_rises_when_the_cursor_does_not_advance(
        self, router, loop, memory, workspace
    ):
        _set_dream_config(workspace, memory_budget_chars=50)
        loop.on_turn = _replace_seed(memory, "x" * 300)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert memory.get_review_state() == (1, 1)

    @pytest.mark.asyncio
    async def test_stuck_resets_on_a_run_that_advances(self, router, loop, memory):
        memory.set_review_state(runs_since_review=3, stuck_runs=1)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert memory.get_review_state() == (4, 0)


# ---------------------------------------------------------------------------
# 5-6. Il review pass
# ---------------------------------------------------------------------------


class TestReviewPass:
    @pytest.mark.asyncio
    async def test_the_periodic_cadence_fires_from_the_command(
        self, router, loop, memory, monkeypatch
    ):
        spy = _ReviewSpy()
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert spy.ran
        # Il review azzera, poi il turno incrementale riconta da lì.
        assert memory.get_review_state() == (1, 0)

    @pytest.mark.asyncio
    async def test_two_stuck_runs_force_a_review(self, router, loop, memory, monkeypatch):
        spy = _ReviewSpy()
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=0, stuck_runs=2)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert spy.ran

    @pytest.mark.asyncio
    async def test_the_review_is_told_there_is_no_snapshot(
        self, router, loop, memory, monkeypatch
    ):
        """La verità, non una comodità.

        Il checkpoint pre-Dream lo prende il container e lo passa al dispatcher
        cron; da un comando non c'è nessun gancio. Il prompt del review ha due
        rami e ``True`` sceglierebbe quello che promette modifiche reversibili,
        attaccato proprio alla frase il cui scopo è far cancellare di più.
        """
        spy = _ReviewSpy()
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert spy.calls[0]["snapshotted"] is False

    @pytest.mark.asyncio
    async def test_the_reply_says_the_review_ran_and_how_much_it_freed(
        self, router, loop, memory, monkeypatch
    ):
        """Un turno LLM in più dentro un comando manuale non parte in silenzio."""
        spy = _ReviewSpy(shrink_to="# Memory\n")
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        freed = len(_MEMORY_TEXT) - len("# Memory\n")
        content = loop.published[0].content
        assert "A memory review pass ran first" in content
        assert f"freed {freed:,} chars" in content
        # E la risposta del run incrementale resta.
        assert "Dream completed in" in content

    @pytest.mark.asyncio
    async def test_a_review_with_nothing_to_shrink_still_says_so(
        self, router, loop, memory, monkeypatch
    ):
        spy = _ReviewSpy()
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert "nothing was freed" in loop.published[0].content

    @pytest.mark.asyncio
    async def test_the_reply_names_the_facts_the_review_moved_away(
        self, router, loop, memory, monkeypatch
    ):
        """"Quanto ha liberato" non è "cosa ha tolto di mezzo", e l'utente riconosce
        la seconda.

        ``ReviewOutcome.demoted`` esisteva e non lo leggeva nessuno: una passata
        che spostava sei fatti personali in archivio riferiva "nothing was freed".
        Il numero da solo non basta — gli id sono ciò che ``recall`` accetta,
        quindi la frase è già l'istruzione per riaverli.
        """
        spy = _ReviewSpy(demoted_ids=("a1b2c3d4", "e5f60718"))
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        content = loop.published[0].content
        assert "moved 2 fact(s) into `memory/archive/`" in content
        assert "recall a1b2c3d4, e5f60718" in content
        # E la riga sui caratteri resta: sono due notizie, non una che sostituisce
        # l'altra.
        assert "A memory review pass ran first" in content

    @pytest.mark.asyncio
    async def test_a_review_that_moved_nothing_does_not_invent_a_demotion(
        self, router, loop, memory, monkeypatch
    ):
        spy = _ReviewSpy()
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert "memory/archive/" not in loop.published[0].content

    @pytest.mark.asyncio
    async def test_the_reply_says_a_write_was_refused_by_its_budget(
        self, router, loop, memory, monkeypatch
    ):
        """Un rifiuto aperto è la sola metà dell'esito su cui l'utente possa agire.

        ``unresolved_refusals`` esisteva, era valorizzato su ogni percorso e non lo
        leggeva nessuno in ``jenny/``: la review che lasciava un fatto fuori da
        tutti i file riferiva "nothing was freed" — vero come numero, muto sul
        fatto che una scrittura era stata *bloccata*. A differenza di una
        degradazione, questa non si ripara con ``recall``: si ripara alzando il
        tetto o potando, e la frase deve dirlo.
        """
        spy = _ReviewSpy(status="no-change", unresolved_refusals=1)
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        content = loop.published[0].content
        assert "1 write(s) were refused by their size budget" in content
        # Era "`/dream budget`", comando rimosso il 31/08/2026: la risposta deve
        # mandare alla superficie che esiste.
        assert "**Settings \u2192 Memory**" in content
        # E la riga sui caratteri resta: il rifiuto la *spiega*, non la sostituisce.
        assert "nothing was freed" in content

    @pytest.mark.asyncio
    async def test_a_review_with_no_refusal_says_nothing_about_budgets(
        self, router, loop, memory, monkeypatch
    ):
        spy = _ReviewSpy(shrink_to="# Memory\n")
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert "refused by their size budget" not in loop.published[0].content

    @pytest.mark.asyncio
    async def test_a_really_refused_write_reaches_the_reply(
        self, router, loop, memory, workspace
    ):
        """Il giro completo, senza doppio del review: rifiuto vero, frase vera.

        Le altre asserzioni di questa sezione sostituiscono ``run_dream_review``,
        quindi provano la nota contro un esito costruito a mano. Qui il review
        gira davvero, il guard rifiuta davvero la scrittura del "modello", e
        ``unresolved_refusals`` arriva dall'unico posto che conta —
        ``FileStates`` — fino alla riga che l'utente legge.
        """
        _set_dream_config(workspace, memory_budget_chars=50)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)
        loop.on_turn = _replace_seed(memory, "x" * 300)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        content = loop.published[0].content
        assert "A memory review pass ran first" in content
        assert "write(s) were refused by their size budget" in content

    @pytest.mark.asyncio
    async def test_the_refusal_comes_before_the_demotions(
        self, router, loop, memory, monkeypatch
    ):
        """Le due notizie convivono, e l'ordine non è estetico.

        Il rifiuto sta attaccato alla riga dei caratteri perché la spiega — "non
        ha liberato niente" con un rifiuto aperto non è "non c'era niente da
        fare" — mentre le degradazioni sono un esito riuscito e vanno in coda.
        """
        spy = _ReviewSpy(
            status="no-change", unresolved_refusals=2, demoted_ids=("a1b2c3d4",)
        )
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        content = loop.published[0].content
        assert "2 write(s) were refused" in content
        assert "moved 1 fact(s) into `memory/archive/`" in content
        assert content.index("refused by their size budget") < content.index(
            "into `memory/archive/`"
        )

    @pytest.mark.asyncio
    async def test_a_pathological_pass_does_not_become_a_wall_of_hashes(
        self, router, loop, memory, monkeypatch
    ):
        """Il tetto sugli id nominati, e la coda che dice quanti sono gli altri.

        Tacere il resto sarebbe la stessa bugia in piccolo; ``recall`` senza
        argomenti li elenca tutti, e quelli di questa passata sono in testa.
        """
        ids = tuple(f"{i:08x}" for i in range(14))
        spy = _ReviewSpy(demoted_ids=ids)
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        content = loop.published[0].content
        assert "moved 14 fact(s)" in content
        assert ids[9] in content
        assert ids[10] not in content
        assert "and 4 more" in content

    @pytest.mark.asyncio
    async def test_the_review_is_reported_even_with_no_history_to_process(
        self, router, loop, memory, monkeypatch, workspace
    ):
        """Il review gira prima di sapere se c'è storia: se gira, va detto comunque."""
        spy = _ReviewSpy(shrink_to="# Memory\n")
        _install_review(monkeypatch, spy)
        # Cursore oltre l'unica voce: ``build_dream_prompt`` ritorna None.
        memory.set_last_dream_cursor(9999)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        content = loop.published[0].content
        assert "A memory review pass ran first" in content
        assert "no conversation history to process" in content

    @pytest.mark.asyncio
    async def test_report_and_guard_are_rebuilt_after_the_review(
        self, router, loop, memory, monkeypatch, workspace
    ):
        """Il review ha appena riscritto quei file: riusarli mentirebbe al turno dopo.

        Il gauge del turno incrementale mostrerebbe altrimenti un riempimento
        che il review ha già smontato — cioè chiederebbe al modello di far
        spazio che è già stato fatto.
        """
        _set_dream_config(workspace, memory_budget_chars=500)
        shrunk = "# Memory\n"
        spy = _ReviewSpy(shrink_to=shrunk)
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        guards: list[Any] = []
        real_build = memory.build_dream_tools

        def _record(*, write_size_guard=None, scope="personal"):
            guards.append(write_size_guard)
            return real_build(write_size_guard=write_size_guard, scope=scope)

        monkeypatch.setattr(memory, "build_dream_tools", _record)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        # Il gauge del turno incrementale è misurato DOPO il review.
        assert f"MEMORY.md [{len(shrunk) * 100 // 500}% — {len(shrunk)}/500" in loop.prompts[0]
        # E il guard non è lo stesso oggetto passato al review.
        assert guards and guards[0] is not spy.calls[0]["guard"]


# ---------------------------------------------------------------------------
# 7-8. Regressioni
# ---------------------------------------------------------------------------


class TestTheShippedDefaultsEnforce:
    """I default non sono più inerti, e questa classe è dove il cambio si vede.

    ``memory_budget_chars`` e ``user_budget_chars`` sono nati a 0 — "misurato ma
    non applicato" — perché servivano le misure vere, e perché un rifiuto poteva
    ancora far avanzare il cursore di Dream buttando via il fatto rifiutato. Ora
    valgono 3.000, il numero letto sul device — la dimensione *non potata* dei
    due file, non il pavimento che toccavano appena dopo un review — e la
    precondizione è chiusa (``internal_run_should_commit``).

    ``SOUL.md`` resta l'unico a 0, e non per dimenticanza: mescola identità e
    vincoli di piattaforma, e un tetto non sa su quale delle due sta premendo.
    """

    @pytest.mark.asyncio
    async def test_a_write_over_the_shipped_cap_is_refused_without_configuring_anything(
        self, router, loop, memory
    ):
        # Nessun ``_set_dream_config``: è il default di spedizione a rifiutare.
        turn = _replace_seed(memory, "y" * 5000)
        loop.on_turn = turn

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert turn.results and "Write refused" in turn.results[0]
        assert "over its 3,000 char budget" in turn.results[0]
        assert memory.memory_file.read_text(encoding="utf-8") == _MEMORY_TEXT

    @pytest.mark.asyncio
    async def test_a_write_under_the_shipped_cap_still_lands(
        self, router, loop, memory
    ):
        turn = _replace_seed(memory, "- gateway runs on Android")
        loop.on_turn = turn

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert turn.results and "Successfully edited" in turn.results[0]
        assert "gateway runs on Android" in memory.memory_file.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_the_gauge_shows_two_caps_and_soul_without_one(self, router, loop):
        """Il gauge distingue i due stati, e sono entrambi presenti di default.

        ``render_gauge`` mostra i tre file comunque; ciò che cambia con i tetti
        di spedizione è che due righe su tre ora portano una percentuale, cioè
        una soglia che il modello deve rispettare. La terza no, ed è la
        controprova che "misurato ma non applicato" esiste ancora.
        """
        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        prompt = loop.prompts[0]
        assert "Long-term memory budget" in prompt
        assert f"MEMORY.md [{len(_MEMORY_TEXT) * 100 // 3000}% — {len(_MEMORY_TEXT)}/3,000" in prompt
        assert "SOUL.md [7 chars — no budget]" in prompt

    @pytest.mark.asyncio
    async def test_the_reply_is_the_one_from_before(self, router, loop):
        ack = await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert ack.content == "Dreaming..."
        content = loop.published[0].content
        assert content.startswith("Dream completed in")
        assert "budget" not in content.lower()
        assert "review" not in content.lower()

    @pytest.mark.asyncio
    async def test_no_review_pass_on_a_fresh_install(self, router, loop, monkeypatch):
        spy = _ReviewSpy()
        _install_review(monkeypatch, spy)

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert not spy.ran

    @pytest.mark.asyncio
    async def test_the_config_is_not_rewritten(self, router, loop, workspace):
        before = (workspace / "config.json").stat().st_mtime_ns

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert (workspace / "config.json").stat().st_mtime_ns == before


class TestBudgetBranchRunsNothing:
    """`/dream budget ...` legge e scrive numeri, non consolida."""

    @pytest.mark.asyncio
    async def test_reading_the_budget_starts_no_run(self, router, loop, monkeypatch):
        spy = _ReviewSpy()
        _install_review(monkeypatch, spy)

        await router.dispatch(_ctx(loop, "/dream budget"))
        await asyncio.sleep(0)

        assert loop.prompts == []
        assert loop.published == []
        assert not spy.ran

    @pytest.mark.asyncio
    async def test_writing_a_budget_starts_no_run(self, router, loop, memory, monkeypatch):
        spy = _ReviewSpy()
        _install_review(monkeypatch, spy)
        memory.set_review_state(runs_since_review=99, stuck_runs=9)

        await router.dispatch(_ctx(loop, "/dream budget memory 500"))
        await asyncio.sleep(0)

        assert loop.prompts == []
        assert not spy.ran
        # E nemmeno i contatori si muovono: non è passato nessun run.
        assert memory.get_review_state() == (99, 9)


# ---------------------------------------------------------------------------
# "Ha scritto" non è "il batch è atterrato", anche da `/dream`
# ---------------------------------------------------------------------------


class TestTheManualRunHoldsAnUnconsolidatedBatch:
    """La stessa guardia del percorso cron, su questo percorso.

    Non è simmetria per il gusto della simmetria: questo modulo esiste perché il
    guard, il gauge e i contatori del review sono arrivati qui **tre commit dopo**
    che erano nel cron, e ogni volta in silenzio. Una guardia cablata su un solo
    percorso è la stessa porta di servizio di allora, con un altro nome.
    """

    @pytest.fixture()
    def batch_with_facts(self, memory: MemoryStore) -> MemoryStore:
        # Il seme del fixture non ha tag di ritenzione, quindi da solo non
        # attiverebbe la guardia: serve una voce che chieda di essere salvata.
        memory.append_history("- [permanent] Preferisce le riunioni corte del mattino")
        return memory

    @pytest.mark.asyncio
    async def test_a_run_that_only_shrinks_does_not_advance_the_cursor(
        self, router, loop, batch_with_facts, workspace
    ):
        """Il run di 12:01: una edit che accorcia, nessun fatto nuovo.

        Il tetto a 23 mette ``MEMORY.md`` (21 caratteri) al 91%: è la pressione
        senza la quale la guardia crede al modello, come deve — v.
        ``test_no_pressure_means_the_model_is_believed`` nel percorso cron.
        """
        _set_dream_config(workspace, memory_budget_chars=23)
        memory = batch_with_facts
        before_cursor = memory.get_last_dream_cursor()
        # Più corto del seme che sostituisce: il file rimpicciolisce, come sul
        # device (1.999 → 1.972 caratteri).
        loop.on_turn = _replace_seed(memory, "- seed")

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert memory.get_last_dream_cursor() == before_cursor
        assert "consolidated nothing" in loop.published[0].content
        assert "come back next run" in loop.published[0].content

    @pytest.mark.asyncio
    async def test_a_run_that_adds_something_advances(
        self, router, loop, batch_with_facts
    ):
        """Controprova: lo stesso batch, ma il file cresce."""
        memory = batch_with_facts
        before_cursor = memory.get_last_dream_cursor()
        loop.on_turn = _replace_seed(
            memory, "- seed fact\n- [nuovo] preferisce le riunioni corte del mattino"
        )

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        assert memory.get_last_dream_cursor() > before_cursor
        assert "Dream completed" in loop.published[0].content
        assert "consolidated nothing" not in loop.published[0].content

    @pytest.mark.asyncio
    async def test_the_entries_really_come_back(
        self, router, loop, batch_with_facts, workspace
    ):
        """Il punto di tenere il cursore: il batch deve ripresentarsi."""
        _set_dream_config(workspace, memory_budget_chars=23)
        memory = batch_with_facts
        loop.on_turn = _replace_seed(memory, "- seed")

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain(loop)

        again = memory.build_dream_prompt()
        assert again is not None
        assert "Preferisce le riunioni corte del mattino" in MemoryStore.dream_prompt_history(again[0])
