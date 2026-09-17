"""Event types for the message bus."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from jenny.session.keys import session_key_for_channel

# Optional ``OutboundMessage.metadata`` key for structured, channel-agnostic UI
# payloads. Value is JSON-serializable with at least ``kind``; rich clients may
# render it and other channels may ignore unknown keys.
OUTBOUND_META_AGENT_UI = "_agent_ui"

# Snapshot dello stato dei subagent (running + terminati recenti). Il valore è il
# payload JSON descritto in ``SubagentManager.status_snapshot``, identico a quello
# servito da ``GET /api/subagents``: una sola forma, due trasporti. Chi produce lo
# snapshot (il manager) e chi lo rende (WebUI) non si conoscono, quindi la chiave
# vive qui, nel modulo foglia che entrambi importano.
OUTBOUND_META_SUBAGENT_STATUS = "_subagent_status"

# Attività fine di **un** subagent (la finestra di eventi descritta da
# ``SubagentActivityLog.tail_window``, più ``task_id``). Distinta dallo snapshot
# qui sopra per una ragione di costo, non di stile: lo snapshot è coarse e va a
# tutti i client, questo è high-frequency e va **solo** alle connessioni che
# stanno guardando quel task. Il trasporto normale è il pump del canale WS
# (``ws_sender._pump_subagent_activity_once``), che legge il ring direttamente;
# questa chiave esiste perché un produttore che pubblichi la finestra sul bus
# atterri comunque come frame dedicato invece che come bolla vuota — è la stessa
# rete di sicurezza che ``OUTBOUND_META_SUBAGENT_STATUS`` fornisce allo snapshot.
OUTBOUND_META_SUBAGENT_ACTIVITY = "_subagent_activity"

# Channel name for messages that stay inside the agent (cron, Dream, heartbeat,
# subagents) and are never delivered to a real chat channel. Defined here — a
# leaf module with no internal deps — so core (loop/cron/delivery) can import it
# without depending on the gateway entry-point.
INTERNAL_CHANNEL = "internal"

# Channel name della tendina delle notifiche Android: la superficie da cui si
# risponde a un alert senza aprire l'app. Sta qui, accanto a
# ``INTERNAL_CHANNEL`` e per lo stesso motivo, perché lo importano i due lati
# opposti del giro — l'ingresso (``runtime/native_input.py``) e l'uscita
# (``channels/notification.py``) — e una stringa di protocollo scritta due
# volte è una stringa che prima o poi diverge.
NOTIFICATION_CHANNEL = "notification"

# Channel name della mascotte flottante: la finestra che sta sopra le altre app,
# dove un tap apre un campo e la risposta compare in un fumetto. Sta qui per la
# stessa ragione di ``NOTIFICATION_CHANNEL`` — la importano l'ingresso
# (``runtime/native_input.py``) e l'uscita (``channels/floating.py``), che sono
# i due lati opposti dello stesso giro.
FLOATING_CHANNEL = "floating"

# Flag di metadata che marcano un outbound come coordinamento/streaming: tutto
# ciò che NON è un messaggio finale user-visible. Sorgente unica condivisa dal
# dispatcher (che vi aggiunge ``_mirror``) e dal canale Telegram (webui-only),
# così l'aggiunta di un nuovo flag di coordinamento non può più divergere fra
# le due liste.
COORDINATION_FLAGS = (
    "_progress", "_stream_delta", "_stream_end", "_streamed",
    "_reasoning_delta", "_reasoning_end", "_retry_wait",
    "_turn_end", "_file_edit_events", "_goal_status",
    "_session_updated", "_runtime_model_updated", "_app_data_changed",
    "_apps_list_changed", "_user_echo",
    "_subagent_status", "_subagent_activity",
)


@dataclass
class InboundMessage:
    """Message received from a chat channel."""

    channel: str  # e.g. "websocket"
    sender_id: str  # User identifier
    chat_id: str  # Chat/channel identifier
    content: str  # Message text
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)  # Media URLs
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data
    session_key_override: str | None = None  # Optional override for thread-scoped sessions

    @property
    def session_key(self) -> str:
        """La sessione in cui questo messaggio viene lavorato.

        Senza override e' **la** conversazione dell'utente, qualunque canale
        l'abbia portato: e' la regola di :func:`~jenny.session.keys.session_key_for_channel`,
        letta qui dal suo unico posto invece di essere ricostruita.

        Prima questa property fabbricava ``f"{channel}:{chat_id}"``, una chiave
        che nessun file di sessione porta piu'. Non era innocua: ``CronTool``
        la persisteva nei payload dei job, e un promemoria creato dalla WebUI
        girava poi in ``websocket:default`` — un secondo file di sessione
        accanto alla conversazione a cui appartiene, con il suo consolidamento
        e il suo riassunto.
        """
        return self.session_key_override or session_key_for_channel(
            self.channel, self.chat_id
        )


@dataclass
class OutboundMessage:
    """Message to send to a chat channel.

    ``metadata`` can carry routing (``message_id``, …), trace flags (``_progress``),
    and optional ``OUTBOUND_META_AGENT_UI`` blobs for rich clients; non-WebUI
    channels may ignore unknown keys.
    """

    channel: str
    chat_id: str
    content: str
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    buttons: list[list[str]] = field(default_factory=list)
