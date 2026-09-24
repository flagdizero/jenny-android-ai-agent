"""Tests del ripudio di turno via epoch (turn_epochs + guardie in loop.py).

Un turno il cui epoch è stato bumpato (/stop, /new) è "ripudiato": può
continuare a girare (task abbandonato bloccato in un thread), ma i suoi
effetti — outbound, turn_completed, stream delta, checkpoint, history —
devono essere scartati in silenzio. I messaggi utente rimasti in coda NON
vanno persi: il re-publish dei leftover resta incondizionato.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from support.agent import make_loop, make_provider

from jenny.agent.loop import AgentLoop
from jenny.bus.events import InboundMessage, OutboundMessage


def _make_loop(tmp_path) -> AgentLoop:
    loop = make_loop(
        tmp_path, provider=make_provider(spec=False), model=None,
        context_window_tokens=None, patch_deps=True,
    )
    loop.runtime_event_publisher = MagicMock(
        turn_completed=AsyncMock(),
        run_status_changed=AsyncMock(),
        clear_turn=MagicMock(),
        record_turn_latency=MagicMock(),
    )
    loop.bus.publish_outbound = AsyncMock()
    loop.bus.publish_inbound = AsyncMock()
    return loop


def _msg(content: str = "work") -> InboundMessage:
    return InboundMessage(channel="test", sender_id="u1", chat_id="c1", content=content)


KEY = "unified:default"


async def test_repudiated_turn_skips_outbound_and_turn_completed(tmp_path):
    """Bump a metà turno: la risposta tardiva dello zombie non viene pubblicata,
    niente turn_completed né run_status idle dal turno ripudiato."""
    loop = _make_loop(tmp_path)
    release = asyncio.Event()
    started = asyncio.Event()

    async def _blocked(msg, **_kwargs):
        started.set()
        await release.wait()
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="late")

    loop._process_message = _blocked
    task = asyncio.create_task(loop._dispatch(_msg()))
    await started.wait()

    loop._turn_epochs.bump(KEY)
    release.set()
    await task

    loop.bus.publish_outbound.assert_not_awaited()
    loop.runtime_event_publisher.turn_completed.assert_not_awaited()
    loop.runtime_event_publisher.run_status_changed.assert_not_awaited()
    loop.runtime_event_publisher.clear_turn.assert_not_called()


async def test_unbumped_turn_behaves_identically(tmp_path):
    """Senza bump (epoch 0) il percorso felice è invariato: outbound pubblicato
    e turn_completed emesso."""
    loop = _make_loop(tmp_path)

    async def _respond(msg, **_kwargs):
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="ok")

    loop._process_message = _respond
    await loop._dispatch(_msg())

    loop.bus.publish_outbound.assert_awaited_once()
    loop.runtime_event_publisher.turn_completed.assert_awaited_once()


async def test_repudiated_turn_finally_republishes_leftovers(tmp_path):
    """I messaggi rimasti nella pending queue di un turno ripudiato vengono
    comunque ri-pubblicati come inbound (mai persi)."""
    loop = _make_loop(tmp_path)
    release = asyncio.Event()
    started = asyncio.Event()

    async def _blocked(msg, **_kwargs):
        started.set()
        await release.wait()
        return None

    loop._process_message = _blocked
    pending: asyncio.Queue = asyncio.Queue()
    leftover = _msg("follow-up")
    pending.put_nowait(leftover)
    loop._pending_queues[KEY] = pending

    task = asyncio.create_task(loop._dispatch(_msg(), pending))
    await started.wait()
    loop._turn_epochs.bump(KEY)
    release.set()
    await task

    loop.bus.publish_inbound.assert_awaited_once_with(leftover)


async def test_repudiated_turn_drops_stream_deltas_and_checkpoint(tmp_path):
    """Le callback (progress/stream) e il checkpoint emessi DOPO il bump
    vengono scartati dalle guardie di _run_agent_loop."""
    loop = _make_loop(tmp_path)
    session = SimpleNamespace(key=KEY, metadata={})
    token = loop._turn_epochs.issue(KEY)
    seen: list[str] = []

    async def _collect(delta, *args, **kwargs):
        seen.append(delta)

    async def _fake_run(spec):
        await spec.checkpoint_callback({"phase": "awaiting_tools"})
        await spec.progress_callback("before-bump")
        loop._turn_epochs.bump(KEY)
        session.metadata.clear()  # simula il restore+clear fatto da /stop
        await spec.checkpoint_callback({"phase": "tools_completed"})
        await spec.progress_callback("after-bump")
        return SimpleNamespace(
            final_content="done", tools_used=[], messages=[],
            stop_reason="end_turn", usage={}, had_injections=False,
            images_stripped=False, goal_stalled=False,
        )

    loop.runner.run = _fake_run
    await loop._run_agent_loop(
        [{"role": "user", "content": "hi"}],
        on_progress=_collect,
        session=session,
        session_key=KEY,
        turn_token=token,
    )

    assert seen == ["before-bump"], "il delta post-bump doveva essere scartato"
    assert session.metadata == {}, "il checkpoint post-bump doveva essere scartato"
