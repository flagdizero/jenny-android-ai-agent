"""Il promemoria di un quaderno si scrive senza la chat recente.

Il 25/11/2026 una seduta serale programmata in un quaderno si è aperta con «ci
siamo lasciati con una patch a metà»: il turno del cron gira nella chat del
quaderno e aveva davanti due ore di lavoro tecnico. ``CRON_FRESH_START_META``
toglie al turno la cronologia (e il riassunto archiviato), ma la riga del
trigger e la risposta restano nella sessione: quando l'utente risponde, il
turno dopo rivede l'apertura.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from support.agent import make_loop, make_provider

from jenny.agent.loop import AgentLoop
from jenny.bus.events import InboundMessage
from jenny.cron.session_turns import (
    CRON_FRESH_START_META,
    CRON_HISTORY_META,
    CRON_TRIGGER_META,
)
from jenny.providers.base import LLMResponse

KEY = "websocket:auto"


def _loop(tmp_path: Path, *replies: str) -> AgentLoop:
    provider = make_provider(bare=True)
    provider.chat_with_retry = AsyncMock(
        side_effect=[LLMResponse(content=r, tool_calls=[], usage={}) for r in replies])
    loop = make_loop(tmp_path, provider=provider, context_window_tokens=None)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    return loop


def _technical_evening(loop: AgentLoop) -> None:
    session = loop.sessions.get_or_create(KEY)
    session.add_message("user", "sistema la patch del check")
    session.add_message("assistant", "Scrittura rifiutata: ecco la patch da applicare.")
    loop.sessions.save(session)


def _cron(*, fresh: bool) -> InboundMessage:
    metadata: dict = {CRON_TRIGGER_META: {
        "job_id": "job-s", "job_name": "Seduta", "run_id": "job-s:1",
        "persist_content": "Scheduled cron job triggered: Seduta\n\napri la seduta",
    }}
    if fresh:
        metadata[CRON_FRESH_START_META] = True
    return InboundMessage(channel="websocket", sender_id="cron", chat_id="auto",
                          content="Cron job: apri la seduta", metadata=metadata,
                          session_key_override=KEY)


def _sent(loop: AgentLoop, call: int = -1) -> str:
    messages = loop.provider.chat_with_retry.await_args_list[call].kwargs["messages"]
    return "\n".join(str(m.get("content")) for m in messages)


@pytest.mark.asyncio
async def test_a_fresh_cron_turn_does_not_see_the_chat(tmp_path: Path) -> None:
    loop = _loop(tmp_path, "come stai stasera?")
    _technical_evening(loop)

    result = await loop._process_message(_cron(fresh=True))

    assert result.message is not None and result.message.content == "come stai stasera?"
    sent = _sent(loop)
    assert "apri la seduta" in sent
    assert "patch" not in sent
    # La lista è quella che il runner allunga con la risposta: conta ciò che c'è prima.
    roles = [m["role"] for m in loop.provider.chat_with_retry.await_args.kwargs["messages"]]
    assert roles[:2] == ["system", "user"] and roles.count("user") == 1


@pytest.mark.asyncio
async def test_the_opener_stays_in_the_chat_for_the_reply(tmp_path: Path) -> None:
    loop = _loop(tmp_path, "come stai stasera?", "capisco")
    _technical_evening(loop)
    await loop._process_message(_cron(fresh=True))

    session = loop.sessions.get_or_create(KEY)
    trigger, opener = session.messages[-2], session.messages[-1]
    assert trigger["role"] == "user" and trigger[CRON_HISTORY_META] is True
    assert opener["role"] == "assistant" and opener["content"] == "come stai stasera?"
    # La chat di prima resta dov'era: il turno non l'ha vista, non l'ha cancellata.
    assert session.messages[0]["content"] == "sistema la patch del check"

    await loop._process_message(InboundMessage(
        channel="websocket", sender_id="user", chat_id="auto", content="stanco",
        session_key_override=KEY))
    assert "come stai stasera?" in _sent(loop)


@pytest.mark.asyncio
async def test_without_the_flag_a_cron_turn_still_sees_the_chat(tmp_path: Path) -> None:
    """Un promemoria della chat personale riprende il discorso: niente è cambiato per lui."""
    loop = _loop(tmp_path, "ricordati la patch")
    _technical_evening(loop)

    await loop._process_message(_cron(fresh=False))

    assert "patch del check" in _sent(loop)
