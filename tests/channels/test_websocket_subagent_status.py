"""Test del frame WS ``subagent_status`` (ramo ``_subagent_status`` di ws_sender).

Regressione bloccata qui: lo snapshot dei subagent arriva con ``content=""`` e,
prima del ramo dedicato, cadeva nel percorso generico di ``send`` diventando una
riga vuota nella WebUI *e* nel transcript persistito. I test verificano che
produca un frame dedicato e che non tocchi mai il transcript.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from port_alloc import free_port
from support.ws_channel import make_ws_channel

from jenny.bus.events import OUTBOUND_META_SUBAGENT_STATUS, OutboundMessage
from jenny.channels.websocket import WebSocketChannel

_SNAPSHOT: dict[str, Any] = {
    "running": [{
        "task_id": "d2ee4342",
        "lineage_id": "aa94c60b",
        "attempt": 1,
        "label": "fix parser",
        "task": "fix the parser so it accepts trailing commas",
        "agent_type": "coder",
        "state": "running",
        "phase": "awaiting_tools",
        "iteration": 2,
        "elapsed_s": 12.5,
        "idle_s": 0.5,
        "last_tool": "grep",
        "tool_events": [{"name": "grep", "status": "ok", "detail": "3 matches"}],
    }],
    "recent": [{
        "task_id": "822ead40",
        "lineage_id": "b202f4e6",
        "attempt": 1,
        "label": "price research",
        "task": "find the current price of a Titan 2",
        "agent_type": "researcher",
        "state": "failed",
        "stop_reason": "error",
        "result_summary": "page not reachable",
        "ended_at": 1785841304.462998,
        "can_restart": True,
    }],
}


def _channel(bus: Any = None) -> WebSocketChannel:
    cfg: dict[str, Any] = {
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        "port": free_port(),
        "path": "/ws",
        "websocketRequiresToken": False,
    }
    channel = make_ws_channel(cfg, bus or MagicMock())
    # Il transcript reale scriverebbe su disco: qui interessa solo *se* viene
    # chiamato (la riga vuota persistita era il bug).
    channel._transcripts = MagicMock()
    return channel


def _subscribed_conn(channel: WebSocketChannel, chat_id: str = "default") -> MagicMock:
    conn = MagicMock()
    conn.send = AsyncMock()
    channel._subs[chat_id] = {conn}
    channel._conn_chats[conn] = {chat_id}
    return conn


def _msg(payload: Any = None) -> OutboundMessage:
    return OutboundMessage(
        channel="websocket",
        chat_id="default",
        content="",
        metadata={OUTBOUND_META_SUBAGENT_STATUS: _SNAPSHOT if payload is None else payload},
    )


class TestSubagentStatusFrame:
    async def test_emits_dedicated_frame(self):
        channel = _channel()
        conn = _subscribed_conn(channel)

        pending = await channel.send(_msg())

        assert pending == []
        conn.send.assert_awaited_once()
        frame = json.loads(conn.send.await_args.args[0])
        assert frame["event"] == "subagent_status"
        assert frame["chat_id"] == "default"
        assert frame["running"] == _SNAPSHOT["running"]
        assert frame["recent"] == _SNAPSHOT["recent"]
        # Nessun campo da bolla di chat: non è un messaggio.
        assert "text" not in frame
        assert "kind" not in frame

    async def test_never_persists_a_transcript_row(self):
        channel = _channel()
        _subscribed_conn(channel)

        await channel.send(_msg())

        channel._transcripts.prepare_and_append.assert_not_called()

    async def test_generic_empty_message_would_persist(self):
        """Contro-prova: senza il flag, un content vuoto persiste (era il bug)."""
        channel = _channel()
        _subscribed_conn(channel)
        channel._media.localize_remote_media = AsyncMock(return_value=("", []))
        channel._media.rewrite_local_markdown_images = MagicMock(return_value="")

        await channel.send(OutboundMessage(
            channel="websocket", chat_id="default", content="", metadata={},
        ))

        channel._transcripts.prepare_and_append.assert_called_once()

    async def test_no_subscribers_is_a_no_op(self):
        channel = _channel()

        pending = await channel.send(_msg())

        assert pending == []
        channel._transcripts.prepare_and_append.assert_not_called()

    async def test_empty_snapshot_still_emits_a_frame(self):
        # Il pannello deve poter svuotarsi: l'ultimo subagent che finisce
        # produce {"running": [], "recent": [...]}, non "nessun frame".
        channel = _channel()
        conn = _subscribed_conn(channel)

        await channel.send(_msg({"running": [], "recent": []}))

        frame = json.loads(conn.send.await_args.args[0])
        assert frame == {
            "event": "subagent_status",
            "chat_id": "default",
            "running": [],
            "recent": [],
        }

    async def test_malformed_payload_is_dropped_not_rendered(self):
        channel = _channel()
        conn = _subscribed_conn(channel)

        pending = await channel.send(_msg("not-a-dict"))

        assert pending == []
        conn.send.assert_not_awaited()
        channel._transcripts.prepare_and_append.assert_not_called()

    async def test_non_list_members_degrade_to_empty_lists(self):
        channel = _channel()
        conn = _subscribed_conn(channel)

        await channel.send(_msg({"running": None, "recent": "x"}))

        frame = json.loads(conn.send.await_args.args[0])
        assert frame["running"] == []
        assert frame["recent"] == []

    async def test_transition_hint_stays_a_tool_hint_row(self):
        """Le transizioni del manager restano righe di trace, non bolle.

        Il manager emette **una** riga per transizione di stato (non una per
        tool call: cinque subagent renderebbero la chat illeggibile). Qui si
        verifica che quella riga arrivi marcata ``kind: "tool_hint"``, che è
        come il client la rende subordinata alla risposta.
        """
        channel = _channel()
        conn = _subscribed_conn(channel)
        channel._media.localize_remote_media = AsyncMock(
            return_value=("subagent stalled (no progress): fix parser", [])
        )
        channel._media.rewrite_local_markdown_images = MagicMock(
            return_value="subagent stalled (no progress): fix parser"
        )

        await channel.send(OutboundMessage(
            channel="websocket",
            chat_id="default",
            content="subagent stalled (no progress): fix parser",
            metadata={"_progress": True, "_tool_hint": True},
        ))

        frame = json.loads(conn.send.await_args.args[0])
        assert frame["kind"] == "tool_hint"
        assert frame["text"] == "subagent stalled (no progress): fix parser"

    async def test_send_subagent_status_reaches_every_subscriber(self):
        channel = _channel()
        conns = [_subscribed_conn(channel) for _ in range(3)]
        # _subscribed_conn sostituisce il set: ricostruiscilo con tutte e tre.
        channel._subs["default"] = set(conns)

        await channel.send_subagent_status("default", _SNAPSHOT)

        for conn in conns:
            conn.send.assert_awaited_once()
