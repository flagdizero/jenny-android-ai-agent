"""`/new` azzera anche quel che il modello vede, non solo la lista dei messaggi.

Il difetto che questi test tengono chiuso (issue #11): `cmd_new` svuotava
`session.messages`, ma il system prompt del turno successivo continuava a portare
il blocco `# Recent History` — cioe' i riassunti di ogni auto-compattazione della
conversazione appena buttata, piu' quello che `/new` stesso archivia di essa. Chi
azzerava per uscire da un argomento se lo ritrovava riassunto un turno dopo, ed
era anche il motivo per cui una regola di stile scritta a meta' conversazione non
attaccava: il modello aveva sotto gli occhi il sommario delle proprie risposte
vecchie e le imitava.

Le due meta' vanno lette insieme, perche' coprono due insiemi di voci diversi e
nessuna delle due basta da sola:

- **il pavimento** (`HISTORY_FLOOR_METADATA_KEY`) esclude le voci gia' scritte
  quando il comando arriva;
- **`prompt_visible=False`** esclude quella che l'archiviazione scrive *dopo*, con
  un cursore piu' alto, e che nessun pavimento fissato prima potrebbe prendere.

E c'e' una terza cosa da non rompere, l'unica per cui questa non e' una
cancellazione: **il cursore di Dream non si muove**. Quelle voci devono ancora
finire in `MEMORY.md`; se qualcuno "semplificasse" spostando il cursore, questi
test restano verdi tranne l'ultimo, che e' li' per quello.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.context import ContextBuilder
from jenny.agent.memory import HISTORY_FLOOR_METADATA_KEY, MemoryStore
from jenny.bus.events import InboundMessage
from jenny.command.builtin import cmd_new
from jenny.command.router import CommandContext
from jenny.session.manager import Session

PERSONAL = "unified:default"

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")


def _builder(tmp_path) -> ContextBuilder:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return ContextBuilder(workspace)


# ── il pavimento ─────────────────────────────────────────────────────────────


class TestTheJournalFloor:
    def test_entries_below_the_floor_do_not_enter_the_prompt(self, tmp_path):
        builder = _builder(tmp_path)
        builder.memory.append_history("prima del reset", session_key=PERSONAL)
        floor = builder.memory.current_history_cursor()

        prompt = builder.build_system_prompt(session_key=PERSONAL, history_floor=floor)

        assert "prima del reset" not in prompt
        # Nessuna voce visibile, quindi nessun blocco: un'assenza non si puo'
        # sbagliare (stessa forma del confine di progetto).
        assert "# Recent History" not in prompt

    def test_what_comes_after_the_floor_is_visible(self, tmp_path):
        """Controllo: il pavimento non spegne il blocco per sempre."""
        builder = _builder(tmp_path)
        builder.memory.append_history("prima del reset", session_key=PERSONAL)
        floor = builder.memory.current_history_cursor()
        builder.memory.append_history("dopo il reset", session_key=PERSONAL)

        prompt = builder.build_system_prompt(session_key=PERSONAL, history_floor=floor)

        assert "dopo il reset" in prompt
        assert "prima del reset" not in prompt

    def test_without_floor_nothing_changes(self, tmp_path):
        """Il default e' il comportamento di sempre, per ogni sessione mai azzerata."""
        builder = _builder(tmp_path)
        builder.memory.append_history("storia personale", session_key=PERSONAL)

        prompt = builder.build_system_prompt(session_key=PERSONAL)

        assert "storia personale" in prompt

    def test_a_corrupt_floor_counts_as_zero(self, tmp_path):
        """I metadata sono un file su disco: una chiave assurda non ferma il turno.

        Il ripiego e' *nessun pavimento*, cioe' il comportamento di sempre: il
        contrario — trattare l'illeggibile come "nascondi tutto" — sarebbe una
        sessione che perde silenziosamente la propria coda per un carattere
        sbagliato.
        """
        builder = _builder(tmp_path)
        builder.memory.append_history("storia personale", session_key=PERSONAL)

        for junk in ("molti", None, True, -3):
            messages = builder.build_messages(
                history=[],
                current_message="ciao",
                session_key=PERSONAL,
                session_metadata={HISTORY_FLOOR_METADATA_KEY: junk},
            )
            assert "storia personale" in messages[0]["content"], junk

    def test_the_floor_comes_from_the_session_metadata(self, tmp_path):
        """La strada vera: `build_messages` lo legge da solo, il loop non lo passa."""
        builder = _builder(tmp_path)
        builder.memory.append_history("prima del reset", session_key=PERSONAL)
        floor = builder.memory.current_history_cursor()

        messages = builder.build_messages(
            history=[],
            current_message="ciao",
            session_key=PERSONAL,
            session_metadata={HISTORY_FLOOR_METADATA_KEY: floor},
        )

        assert "prima del reset" not in messages[0]["content"]


# ── la voce che l'archiviazione scrive dopo ──────────────────────────────────


class TestTheEntryInvisibleToPrompts:
    def test_does_not_enter_the_prompt(self, tmp_path):
        builder = _builder(tmp_path)
        builder.memory.append_history(
            "riassunto della conversazione buttata",
            session_key=PERSONAL,
            prompt_visible=False,
        )

        prompt = builder.build_system_prompt(session_key=PERSONAL)

        assert "riassunto della conversazione buttata" not in prompt
        assert "# Recent History" not in prompt

    def test_but_dream_still_sees_it(self, tmp_path):
        """Il punto di tutto: si toglie dal prompt, **non** dalla memoria.

        Se questa asserzione cade, `/new` ha smesso di essere un reset del
        contesto e ha cominciato a cancellare la memoria di lungo periodo.
        """
        store = MemoryStore(tmp_path / "workspace")
        store.append_history(
            "riassunto della conversazione buttata",
            session_key=PERSONAL,
            prompt_visible=False,
        )

        entries = store.read_unprocessed_history(since_cursor=0)

        assert [e["content"] for e in entries] == ["riassunto della conversazione buttata"]

    def test_a_normal_entry_stays_visible(self, tmp_path):
        """Controllo: il flag e' un'eccezione dichiarata, non il nuovo default.

        L'auto-compattazione riassume una conversazione **che continua**, e quella
        coda il modello deve continuare a vederla.
        """
        builder = _builder(tmp_path)
        builder.memory.append_history("compattazione ordinaria", session_key=PERSONAL)

        prompt = builder.build_system_prompt(session_key=PERSONAL)

        assert "compattazione ordinaria" in prompt


# ── il comando ───────────────────────────────────────────────────────────────


def _new_ctx(tmp_path, session: Session):
    """`cmd_new` con il minimo indispensabile di loop attorno.

    `consolidator.archive` e' un `MagicMock` e non un `AsyncMock` perche' il
    comando **non** lo attende: lo passa a `_schedule_background`, che qui
    raccoglie la coroutine invece di girarla. Quel che il test guarda e' con che
    argomenti e' stata costruita.
    """
    msg = InboundMessage(channel="websocket", sender_id="u1", chat_id="chat1", content="/new")
    scheduled: list = []
    loop = SimpleNamespace(
        _cancel_active_tasks=AsyncMock(return_value=False),
        _restore_cancelled_turn=MagicMock(),
        _emit_stop_turn_end=AsyncMock(),
        sessions=MagicMock(save=MagicMock(), invalidate=MagicMock()),
        forget_file_reads=MagicMock(),
        context=SimpleNamespace(memory=MemoryStore(tmp_path / "workspace")),
        consolidator=MagicMock(archive=MagicMock(return_value=None)),
        _schedule_background=scheduled.append,
    )
    ctx = CommandContext(msg=msg, session=session, key=PERSONAL, raw="/new", loop=loop)
    return ctx, loop, scheduled


class TestCmdNew:
    async def test_writes_the_floor_into_the_metadata(self, tmp_path):
        session = Session(key=PERSONAL)
        session.messages = [{"role": "user", "content": "leggi le mie note"}]
        ctx, loop, _ = _new_ctx(tmp_path, session)
        loop.context.memory.append_history("una compattazione", session_key=PERSONAL)
        expected = loop.context.memory.current_history_cursor()

        await cmd_new(ctx)

        assert session.metadata[HISTORY_FLOOR_METADATA_KEY] == expected
        # Salvato, o al prossimo turno il pavimento non c'e' piu'.
        loop.sessions.save.assert_called_once_with(session)

    async def test_archives_without_showing_it_to_the_prompts(self, tmp_path):
        session = Session(key=PERSONAL)
        session.messages = [{"role": "user", "content": "leggi le mie note"}]
        ctx, loop, scheduled = _new_ctx(tmp_path, session)

        await cmd_new(ctx)

        assert loop.consolidator.archive.call_args.kwargs["prompt_visible"] is False
        assert len(scheduled) == 1

    async def test_does_not_move_the_dream_cursor(self, tmp_path):
        """L'unica cosa che rende questo un reset del contesto e non un oblio."""
        session = Session(key=PERSONAL)
        session.messages = [{"role": "user", "content": "leggi le mie note"}]
        ctx, loop, _ = _new_ctx(tmp_path, session)
        loop.context.memory.append_history("una compattazione", session_key=PERSONAL)
        before = loop.context.memory.get_last_dream_cursor()

        await cmd_new(ctx)

        assert loop.context.memory.get_last_dream_cursor() == before
        assert loop.context.memory.read_unprocessed_history(since_cursor=before)
