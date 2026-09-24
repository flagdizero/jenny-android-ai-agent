"""Lo slash command ``/dream``, che è un verbo e nient'altro.

Portava anche le manopole (``/dream budget``: i tre tetti della memoria lunga e
la cadenza del review pass). Dal 31/08/2026 quelle stanno in **Impostazioni →
Memoria** — una manopola è una preferenza che sopravvive al turno, e sta dove
stanno le altre; un comando fa qualcosa adesso. Le prove di quelle manopole sono
in ``tests/webui/test_worker_settings.py``, portate insieme al codice.

Qui resta la parte che conta di più, e che è sopravvissuta a entrambi i giri:
**`/dream` nudo consolida davvero.** Più il ramo di migrazione, che esiste
perché il prefisso resta registrato nel router: senza, `/dream budget` battuto a
memoria non sarebbe più un comando e finirebbe al modello come messaggio.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.agent.memory import MemoryStore
from jenny.bus.events import InboundMessage
from jenny.command.builtin import register_builtin_commands
from jenny.command.router import CommandContext, CommandRouter
from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config
from jenny.utils.helpers import sync_workspace_templates

# MEMORY.md deve essere abbastanza grande da poter finire *sopra* un budget
# plausibile: è lo stato reale sul device ed è il caso che la conferma deve
# segnalare.
_MEMORY_TEXT = "# Memory\n" + "".join(f"- fact number {i}\n" for i in range(40))
_USER_TEXT = "# User\n- Name: Ludovico\n"
_SOUL_TEXT = "# Soul\n- Helpful, concise.\n"


@pytest.fixture()
def router() -> CommandRouter:
    r = CommandRouter()
    register_builtin_commands(r)
    return r


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Workspace vero con i template estratti e un `config.json` di default.

    ``render_template`` legge da ``get_workspace_path()``, non dal package: il
    ramo senza argomento non riuscirebbe nemmeno a costruire il prompt di Dream
    senza i template su disco. L'ambiente Jinja è memoizzato per processo, quindi
    la cache va invalidata o la prima chiamata della suite fissa la root per
    tutte le altre.
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
    store.user_file.write_text(_USER_TEXT, encoding="utf-8")
    store.soul_file.write_text(_SOUL_TEXT, encoding="utf-8")
    return store


@pytest.fixture()
def published() -> list:
    return []


@pytest.fixture()
def loop(workspace: Path, memory: MemoryStore, published: list) -> SimpleNamespace:
    """Il minimo di ``AgentLoop`` che ``cmd_dream`` tocca, su entrambi i rami."""
    calls: list[str] = []

    async def _publish(message):
        published.append(message)

    async def _process_direct(prompt, **_kwargs):
        calls.append(prompt)
        # ``_stop_reason: completed`` è ciò che ``internal_run_completed``
        # guarda; senza, il ramo senza argomento riporterebbe un turno mozzato.
        return SimpleNamespace(content="done", metadata={"_stop_reason": "completed"})

    return SimpleNamespace(
        bus=SimpleNamespace(publish_outbound=_publish),
        context=SimpleNamespace(memory=memory, timezone=None),
        sessions=SimpleNamespace(sessions_dir=workspace / "sessions"),
        process_direct=_process_direct,
        evict_pruned_sessions=lambda _keys: None,
        prompts=calls,
    )


def _ctx(loop: SimpleNamespace, raw: str) -> CommandContext:
    msg = InboundMessage(
        channel="websocket", sender_id="u", chat_id="default", content=raw
    )
    return CommandContext(msg=msg, session=None, key="k", raw=raw, loop=loop)


async def _drain(timeout: float = 30.0) -> None:
    """Aspetta il task fire-and-forget del ramo senza argomento. Davvero.

    Era ``for _ in range(50): await asyncio.sleep(0)``, e ha fatto rosso la CI
    (3.12, 28/08/2026) su un commit che non toccava niente di tutto questo: il
    prompt di Dream era arrivato al provider e "Dream completed" non era ancora
    stato pubblicato. Un ``sleep(0)`` cede il controllo, **non** lascia passare
    tempo: il ciclo di Dream attraversa ``asyncio.to_thread``, e su una macchina
    carica il thread non ha finito dopo cinquanta giri di event loop. Passava per
    fortuna, e la fortuna sulla CI finisce.

    Qui si aspetta l'oggetto giusto — i task vivi che non sono questo test —
    finché non sono finiti. ``cmd_dream`` fa `asyncio.create_task` senza tenere
    il manico e senza appoggiarlo al loop, quindi ``all_tasks()`` è l'unico modo
    che il test ha di nominarli; e si rilegge a ogni giro perché un task ne può
    aprire un altro. Il tetto esiste solo per non appendere la suite: se scatta è
    un difetto, e lo dice invece di lasciare fallire un ``assert ([])`` tre righe
    più in là.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    current = asyncio.current_task()
    while True:
        pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
        if not pending:
            return
        remaining = deadline - loop.time()
        assert remaining > 0, f"task ancora in volo dopo {timeout}s: {pending}"
        await asyncio.wait(pending, timeout=remaining)


def _config_path(workspace: Path) -> Path:
    return workspace / "config.json"


def _dream_config(workspace: Path):
    return load_config(_config_path(workspace)).agents.defaults.dream


class TestRegistration:
    def test_the_prefix_stays_dispatchable_on_purpose(self, router):
        """Il prefisso resta registrato **per intercettare** le forme vecchie.

        Togliendolo, `/dream budget` non sarebbe più un comando e il router lo
        lascerebbe passare: finirebbe al modello come messaggio, che è il modo
        peggiore di dire "non esiste più".
        """
        assert router.is_dispatchable_command("/dream")
        assert router.is_dispatchable_command("/dream budget")
        assert router.is_dispatchable_command("/dream budget memory 6000")

    def test_the_palette_no_longer_offers_an_argument(self):
        from jenny.command.specs import BUILTIN_COMMAND_SPECS

        spec = {s.command: s for s in BUILTIN_COMMAND_SPECS}["/dream"]

        assert spec.arg_hint == ""
        assert "budget" not in spec.description.lower() or "Settings" in spec.description


class TestPlainDreamIsUntouched:
    """Regressione — il test più importante del file.

    Aggiungere un argomento a `/dream` significa infilare un ramo davanti a un
    percorso che esisteva già e che funziona. Se `/dream` nudo smettesse di
    consolidare, o cominciasse a rispondere con dei budget, la feature avrebbe
    rotto la sola cosa che il comando faceva prima.
    """

    @pytest.mark.asyncio
    async def test_bare_dream_still_runs_the_consolidation(
        self, router, loop, memory, published
    ):
        memory.append_history("user: ricordati che il gateway gira su Android")

        ack = await router.dispatch(_ctx(loop, "/dream"))
        await _drain()

        assert ack.content == "Dreaming..."
        # Il consolidamento è partito davvero: il prompt di Dream è arrivato al
        # provider, non solo un messaggio di stato in chat.
        assert loop.prompts and "Conversation History" in loop.prompts[0]
        assert published and "Dream completed" in published[0].content

    @pytest.mark.asyncio
    async def test_bare_dream_prints_no_budget(self, router, loop, memory, published):
        memory.append_history("user: qualcosa da consolidare")

        ack = await router.dispatch(_ctx(loop, "/dream"))
        await _drain()

        everything = ack.content + "".join(m.content for m in published)
        assert "budget" not in everything.lower()

    @pytest.mark.asyncio
    async def test_bare_dream_does_not_write_config(self, router, loop, memory, workspace):
        memory.append_history("user: qualcosa da consolidare")
        before = _config_path(workspace).stat().st_mtime_ns

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain()

        assert _config_path(workspace).stat().st_mtime_ns == before


class TestNothingNewToDream:
    """`/dream` senza storia nuova: un messaggio, e il ciclo si chiude lo stesso.

    Il ramo esce dal ``try`` prima che il turno parta, e il ``finally`` che
    chiude il ciclo legge lo stato file del turno. Finché quel nome nasceva solo
    più in basso, il ``finally`` sollevava ``UnboundLocalError``: il ciclo non si
    chiudeva (``runs_since_review`` fermo, quindi un review pass che non arriva
    mai per chi ha poca storia) e all'utente arrivava, dopo «niente da fare»,
    anche un «Dream failed» che parlava di una variabile.
    """

    @pytest.mark.asyncio
    async def test_one_message_and_the_cycle_still_closes(
        self, router, loop, memory, published
    ):
        runs_before, _ = memory.get_review_state()

        ack = await router.dispatch(_ctx(loop, "/dream"))
        await _drain()

        assert ack.content == "Dreaming..."
        assert loop.prompts == [], "senza storia il turno non deve partire"
        assert len(published) == 1, [m.content for m in published]
        assert "no conversation history" in published[0].content
        assert "failed" not in published[0].content.lower()
        runs_after, _ = memory.get_review_state()
        assert runs_after == runs_before + 1


class TestTheFormsThatMoved:
    """Chi digita `/dream budget` deve trovare la strada, non il silenzio."""

    @pytest.mark.asyncio
    async def test_an_argument_says_where_the_knobs_went(self, router, loop, workspace):
        reply = await router.dispatch(_ctx(loop, "/dream budget"))

        assert "Settings" in reply.content and "Memory" in reply.content
        # E dice cosa fa ancora il comando nudo: un rifiuto che non lo dice manda
        # a cercare un altro comando che non c'è.
        assert "/dream" in reply.content

    @pytest.mark.asyncio
    async def test_it_starts_no_consolidation_and_writes_nothing(
        self, router, loop, workspace
    ):
        before = _config_path(workspace).stat().st_mtime_ns

        await router.dispatch(_ctx(loop, "/dream budget memory 6000"))
        await _drain()

        assert loop.prompts == []
        assert _config_path(workspace).stat().st_mtime_ns == before

    @pytest.mark.asyncio
    async def test_the_old_form_never_reaches_the_model(self, router, loop, workspace):
        """Il punto del ramo: non è cortesia, è che l'alternativa è peggio."""
        reply = await router.dispatch(_ctx(loop, "/dream budget review 1"))

        assert reply is not None
