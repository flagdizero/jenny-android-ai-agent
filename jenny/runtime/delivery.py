"""Consegna di messaggi verso i canali + mirroring nella sessione (estratto dalla
closure ``_deliver_to_channel`` di ``gateway_runtime._run_gateway``).

Pubblica un messaggio user-visible sul bus e, quando ``record`` è attivo, lo
rispecchia nella sessione del canale così la cronologia resta coerente.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.bus.events import INTERNAL_CHANNEL, OutboundMessage
from jenny.session.keys import session_key_for_channel
from jenny.session.turn_visibility import is_silent_turn
from jenny.webui.metadata import WEBUI_DEFAULT_CHAT_ID, WEBUI_TURN_METADATA_KEY

if TYPE_CHECKING:
    from jenny.bus.queue import MessageBus
    from jenny.session.manager import SessionManager

# Firma dell'hook di registrazione fornito dall'AgentLoop
# (``AgentLoop.record_channel_delivery``).
RecordHook = Callable[..., Awaitable[None]]

WEBSOCKET_CHANNEL = "websocket"


class ChannelDeliverer:
    def __init__(
        self,
        *,
        bus: "MessageBus",
        session_manager: "SessionManager",
        extra_targets: "Callable[[], list[tuple[str, str]]] | None" = None,
        record_hook: "Callable[[], RecordHook | None] | None" = None,
    ) -> None:
        self._bus = bus
        self._session_manager = session_manager
        # Target aggiuntivi (canale, chat_id) per il fan-out proattivo,
        # es. la chat Telegram accoppiata. Late-binding: letto a ogni deliver.
        self._extra_targets = extra_targets
        # Getter late-binding dell'hook di registrazione (l'agente nasce dopo il
        # deliverer, e con l'onboarding può nascere molto dopo). Quando c'è, la
        # scrittura in sessione passa da lui: è l'unico che possa serializzarla
        # col lock di sessione del turno. Senza hook resta la scrittura diretta
        # qui sotto — vale per la fase pre-onboarding e per i test.
        self._record_hook = record_hook

    def _channel_session_key(self, channel: str, chat_id: str) -> str:
        return session_key_for_channel(channel, chat_id)

    async def _close_webui_turn(self, chat_id: str, metadata: dict[str, Any]) -> None:
        """Chiude il turno WebUI aperto da una consegna proattiva.

        Solo se il turno che l'ha prodotta è SILENT, e la condizione non è
        prudenziale: è esattamente il complemento di chi chiude i turni normali.
        ``webui_view_target`` non dà nessuna vista a un turno silenzioso, quindi
        ``WebuiTurnCoordinator.handle_turn_end`` per quel turno non emette
        niente; per ogni altro turno la emette lui, e un secondo ``turn_end``
        qui troncherebbe una risposta ancora in streaming (caso reale: l'utente
        in chat chiede di mandare qualcosa su Telegram, e quella consegna è
        proattiva pur vivendo dentro un turno visibile).

        Un avviso proattivo è dunque un turno a sé — ha il suo ``turn_id``,
        coniato in ``deliver`` — che prima si apriva e non si chiudeva mai,
        perché nel protocollo live ``turn_end`` è l'unico frame che chiude un
        turno. Le conseguenze, entrambe osservate in chat:

        - la mascotte passa a ``talking`` sul messaggio, dopo un secondo di
          silenzio l'animatore della bocca la mette in ``thinking`` — e lì
          restava per sempre, perché solo ``turn_end``/``error`` la riportano a
          ``idle``;
        - il client non azzera lo stato di stream (``_resetStreamState``), così
          l'avviso proattivo successivo riusava la bolla del precedente e ne
          **sovrascriveva** il testo: quattro avvisi live, una bolla sola con
          dentro l'ultimo.

        Il frame vale anche a zero client connessi: ``send_turn_end`` persiste
        comunque il record, ed è quel record a delimitare i turni nello split
        del transcript (``transcript_store``, ``transcript_replay``).

        Nessuna ``latency_ms``: non c'è nessuna attesa dell'utente da misurare.
        """
        if not is_silent_turn(metadata):
            return
        turn_id = metadata.get(WEBUI_TURN_METADATA_KEY)
        if not isinstance(turn_id, str) or not turn_id:
            return
        await self._bus.publish_outbound(
            OutboundMessage(
                channel=WEBSOCKET_CHANNEL,
                chat_id=chat_id,
                content="",
                metadata={WEBUI_TURN_METADATA_KEY: turn_id, "_turn_end": True},
            )
        )

    async def deliver(
        self,
        msg: OutboundMessage,
        *,
        record: bool = False,
        session_key: str | None = None,
        proactive: bool = False,
    ) -> None:
        """Publish a user-visible message and mirror it into that channel's session.

        Il fan-out cross-canale (verso gli ``extra_targets``, es. la chat
        Telegram accoppiata) avviene SOLO su intento proattivo esplicito
        (``proactive=True`` dai chiamanti cron, oppure il flag
        ``_proactive_fanout`` nei metadata impostato dal tool ``message`` per
        gli invii davvero cross-canale). Una consegna nella conversazione
        corrente (risposta con allegato) resta sul solo canale d'origine e non
        deve "sfuggire" agli altri canali dell'utente.

        Con ``record`` attivo il testo viene anche registrato come messaggio
        ``assistant`` nella sessione del canale (marker ``_channel_delivery``),
        così il turno successivo dell'utente trova nel proprio contesto quello
        che Jenny gli ha appena detto. La registrazione avviene UNA sola volta,
        qui: le copie ``_mirror`` del fan-out vanno dritte sul bus.
        """
        session_manager = self._session_manager
        metadata = dict(msg.metadata or {})
        record = record or bool(metadata.pop("_record_channel_delivery", False))
        proactive = proactive or bool(metadata.pop("_proactive_fanout", False))
        # Ogni consegna proattiva è un turno a sé, e deve dirlo. Il turno che la
        # produce è interno (cron, Dream) e i suoi metadata non
        # portano la chiave del turno WebUI, quindi
        # ``TranscriptRecorder._annotate_turn`` esce senza stampare né
        # ``turn_id`` né ``turn_phase``/``turn_seq`` sul record — e nel replay
        # ``_same_turn`` considera "stesso turno" qualunque record privo di id.
        # Misurato sul dispositivo il 2026-08-13: quattro avvisi heartbeat
        # consegnati fra 01:31 e 05:02 stanno nel transcript come quattro record
        # ``message`` consecutivi (righe 17720-17723) **tutti** con
        # ``turn_id: None``, incastonati fra un turno utente e un turno cron che
        # il proprio id l'avevano. In chat ne compariva solo l'ultimo: a
        # scartare gli altri tre era il percorso **live**, che senza ``turn_end``
        # non azzerava lo stato di stream e riusava la bolla precedente
        # sovrascrivendone il testo (v. ``_close_webui_turn``, che quel
        # ``turn_end`` ora lo emette).
        # Chi ha già coniato il proprio id (``cron_proactive_delivery_metadata``,
        # per il monitor cron) lo mantiene: qui si riempie solo il buco.
        if proactive and not metadata.get(WEBUI_TURN_METADATA_KEY):
            metadata[WEBUI_TURN_METADATA_KEY] = f"proactive:{uuid.uuid4().hex}"
        if metadata != (msg.metadata or {}):
            msg = OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=msg.content,
                media=msg.media,
                metadata=metadata,
                buttons=msg.buttons,
            )
        if record and msg.channel != INTERNAL_CHANNEL and msg.content.strip():
            key = session_key or self._channel_session_key(msg.channel, msg.chat_id)
            hook = self._record_hook() if self._record_hook is not None else None
            if hook is not None:
                # Un errore qui non deve far fallire la consegna: il messaggio
                # all'utente parte comunque, e perdere la riga di cronologia è
                # meno grave che perdere l'avviso.
                try:
                    await hook(
                        session_key=key,
                        content=msg.content,
                        media=list(msg.media) if msg.media else None,
                    )
                except Exception:
                    logger.exception("Failed to record channel delivery for session {}", key)
            elif hasattr(session_manager, "get_or_create") and hasattr(session_manager, "save"):
                session = session_manager.get_or_create(key)
                extra: dict[str, Any] = {"_channel_delivery": True}
                if msg.media:
                    extra["media"] = list(msg.media)
                session.add_message("assistant", msg.content, **extra)
                session_manager.save(session)

        if msg.channel == INTERNAL_CHANNEL or self._extra_targets is None or not proactive:
            await self._bus.publish_outbound(msg)
            if proactive and msg.channel == WEBSOCKET_CHANNEL:
                await self._close_webui_turn(msg.chat_id, metadata)
            return

        # Fan-out proattivo: il target websocket è SEMPRE incluso (è lui a
        # scrivere la riga nel transcript WebUI); gli altri target ricevono
        # copie marcate ``_mirror`` che non ri-persistono nulla.
        targets: dict[str, str] = {msg.channel: msg.chat_id}
        targets.setdefault(WEBSOCKET_CHANNEL, WEBUI_DEFAULT_CHAT_ID)
        try:
            for name, chat in self._extra_targets():
                if name and chat:
                    targets.setdefault(name, chat)
        except Exception:  # pragma: no cover - difensivo sul callback
            pass
        primary = WEBSOCKET_CHANNEL if WEBSOCKET_CHANNEL in targets else msg.channel
        primary_chat = targets.pop(primary)
        await self._bus.publish_outbound(
            OutboundMessage(
                channel=primary,
                chat_id=primary_chat,
                content=msg.content,
                media=msg.media,
                metadata=metadata,
                buttons=msg.buttons,
            )
        )
        if primary == WEBSOCKET_CHANNEL:
            await self._close_webui_turn(primary_chat, metadata)
        for name, chat in targets.items():
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=name,
                    chat_id=chat,
                    content=msg.content,
                    media=msg.media,
                    metadata={**metadata, "_mirror": True},
                    buttons=msg.buttons,
                )
            )
