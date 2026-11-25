"""Il richiamo per nome dentro il turno: dove arriva, dove no, e cosa non tocca.

Tre promesse: le righe stanno nel contesto del turno e **mai nel prompt di
sistema** (il prefisso in cache); arrivano solo al messaggio dell'utente nella
chat personale o in quella di un quaderno; e un catalogo che manca o si rompe
costa le righe, non il turno. Nomi inventati: il repository è pubblico.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.context import ContextBuilder
from jenny.agent.facts import HEADER, MAX_BLOCKS_IN_HISTORY, NOTEBOOK_NOTE
from jenny.agent.memory_archive import ArchivedEntry, archive_entry
from jenny.session.keys import UNIFIED_SESSION_KEY

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")

_LONG = "Piano di vita: mesi da Oreste a Cremona o in Islanda."
_SHORT = "Piano: mesi da Oreste o in Islanda."
_MESSAGE = "stasera sono dal mio amico oreste"


@pytest.fixture()
def builder(tmp_path: Path) -> ContextBuilder:
    ws = tmp_path / "workspace"
    ws.mkdir()
    archive_entry(ws / "memory", ArchivedEntry(
        id="aaaa0001", text=_LONG, source="USER.md", superseded_by="live0001",
        demoted="2026-10-10",
    ))
    (ws / "USER.md").write_text(f"# User\n\n- {_SHORT}\n", encoding="utf-8")
    built = ContextBuilder(ws)
    built.facts.refresh()
    return built


def _user_text(messages: list[dict]) -> str:
    content = messages[-1]["content"]
    return content if isinstance(content, str) else "\n".join(
        b.get("text", "") for b in content if isinstance(b, dict))


def _build(builder: ContextBuilder, *, key: str | None = UNIFIED_SESSION_KEY,
           recall: bool = True, history: list | None = None, role: str = "user",
           message: str = _MESSAGE) -> list[dict]:
    return builder.build_messages(
        history=history or [], current_message=message, session_key=key,
        recall_names=recall, current_role=role,
    )


class TestWhereItArrives:
    def test_the_personal_chat_gets_the_lost_city(self, builder):
        text = _user_text(_build(builder))
        assert HEADER in text
        assert "Cremona" in text
        assert NOTEBOOK_NOTE not in text

    def test_a_notebook_gets_it_with_the_do_not_journal_note(self, builder):
        text = _user_text(_build(builder, key="project:giardino"))
        assert "Cremona" in text
        assert NOTEBOOK_NOTE in text

    @pytest.mark.parametrize("key", ["cron:abc123", "dream:1", "subagent:x", "peer:p1", None])
    def test_an_internal_turn_gets_nothing(self, builder, key):
        assert "Cremona" not in _user_text(_build(builder, key=key))

    def test_nothing_unless_the_caller_asks(self, builder):
        assert "Cremona" not in _user_text(_build(builder, recall=False))

    def test_nothing_on_a_turn_that_is_not_the_user_s(self, builder):
        messages = _build(builder, role="assistant")
        assert "Cremona" not in _user_text(messages)


class TestTheCacheIsUntouched:
    def test_the_system_prompt_is_the_same_with_and_without(self, builder):
        with_recall = _build(builder)[0]["content"]
        without = _build(builder, recall=False)[0]["content"]
        assert with_recall == without
        assert "Cremona" not in with_recall

    def test_a_second_turn_does_not_repeat_what_the_first_sent(self, builder):
        first = _user_text(_build(builder))
        history = [{"role": "user", "content": first},
                   {"role": "assistant", "content": "Salutamelo!"}]
        second = _user_text(_build(builder, history=history, message="e domani ancora da oreste"))
        assert HEADER not in second

    def test_no_new_block_once_history_holds_enough(self, builder):
        history = [
            {"role": "user", "content": f"x\n{HEADER}\n- y"},
            {"role": "assistant", "content": "ok"},
        ] * MAX_BLOCKS_IN_HISTORY
        assert "Cremona" not in _user_text(_build(builder, history=history))


class TestItNeverCostsTheTurn:
    def test_a_catalogue_not_ready_yet_recalls_nothing(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        fresh = ContextBuilder(ws)
        fresh.facts.refresh_async = lambda: None  # type: ignore[method-assign]
        assert HEADER not in _user_text(_build(fresh))

    def test_a_catalogue_that_raises_costs_only_the_lines(self, builder, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("broken archive")

        monkeypatch.setattr(builder.facts, "recall_lines", boom)
        text = _user_text(_build(builder))
        assert _MESSAGE in text
        assert HEADER not in text


class TestOnlyTheUserTurnAsks:
    """``AgentLoop._build_initial_messages`` accende il richiamo, e solo per l'utente."""

    @staticmethod
    def _flag(*, channel: str = "websocket", metadata: dict | None = None,
              include: bool = True) -> bool:
        from types import SimpleNamespace

        from jenny.agent.loop import AgentLoop
        from jenny.bus.events import InboundMessage

        seen: dict = {}
        fake = SimpleNamespace(
            workspace_scopes=SimpleNamespace(
                for_message=lambda *_a: SimpleNamespace(project_path=None)),
            _runtime_chat_id=lambda m: m.chat_id,
            tools=SimpleNamespace(tool_names=[]),
            orchestrator_mode=False,
            context=SimpleNamespace(build_messages=lambda **kw: seen.update(kw) or []),
            _location_runtime_lines=lambda *_a: [],
            _plan_runtime_lines=lambda *_a: [],
        )
        msg = InboundMessage(channel=channel, sender_id="u", chat_id="default",
                             content="ciao oreste", metadata=metadata or {})
        session = SimpleNamespace(key=UNIFIED_SESSION_KEY, metadata={}, messages=[])
        AgentLoop._build_initial_messages(  # type: ignore[arg-type]
            fake, msg, session, [], None, include_memory_recent_history=include)
        return seen["recall_names"]

    def test_a_user_message_asks_for_it(self):
        assert self._flag() is True

    def test_an_ephemeral_internal_run_does_not(self):
        assert self._flag(include=False) is False

    def test_a_system_turn_does_not(self):
        assert self._flag(channel="system") is False

    def test_a_message_from_another_jenny_on_a_line_does_not(self):
        assert self._flag(metadata={"_line_peer": "p1"}) is False


class TestAFreshArchiveIsSeenAtOnce:
    def test_a_wording_archived_just_now_is_recalled_by_the_next_turn(self, builder):
        # Dream ha appena spostato una formulazione: il turno dopo aspetta la
        # ricostruzione (al più ``WAIT_FOR_REBUILD_S``) invece di usare il
        # catalogo di prima.
        archive_entry(builder.workspace / "memory", ArchivedEntry(
            id="bbbb0002", text="Amica Tiziana, vive a Lodi con due gatti.", source="USER.md",
            demoted="2026-11-20",
        ))
        assert builder.facts.stale()
        text = _user_text(_build(builder, message="domani ceno da tiziana"))
        assert "Lodi" in text
