"""Invio outbound del canale WebSocket (estratto da websocket.py).

`OutboundSenderMixin` raccoglie la consegna verso i client: fan-out sicuro
(`_fanout`), buffer di streaming, e tutti i metodi ``send_*``
(delta, reasoning, file-edit, turn-end, goal, session/app/model updates). Mixato
in ``WebSocketChannel``: ``self`` risolve via MRO a stato/collaboratori del canale.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from contextlib import suppress
from pathlib import Path
from typing import Any

from websockets.exceptions import ConnectionClosed

from jenny.bus.events import (
    OUTBOUND_META_AGENT_UI,
    OUTBOUND_META_SUBAGENT_ACTIVITY,
    OUTBOUND_META_SUBAGENT_STATUS,
    OutboundMessage,
)
from jenny.channels.subagent_activity_wire import (
    ACTIVITY_PUMP_INTERVAL_S,
    MAX_FRAME_EVENTS,
    SubagentWatchRegistry,
    activity_frame,
    empty_window_payload,
    normalize_since,
    normalize_task_id,
    slice_for_cursor,
    window_payload,
)
from jenny.config.runtime_env import ws_send_timeout_s
from jenny.runtime.notifier import notify_delivery
from jenny.webui.metadata import WEBUI_DEFAULT_CHAT_ID, WEBUI_TURN_METADATA_KEY

# Timeout wall-clock per un singolo `connection.send()`. Modulo-level (non
# per-call) così i test possono monkeypatchare `ws_sender._SEND_TIMEOUT_S` con
# un valore basso senza toccare l'ambiente. Vedi `runtime_env.ws_send_timeout_s`.
_SEND_TIMEOUT_S: float = ws_send_timeout_s()


class OutboundSenderMixin:
    """Metodi di consegna outbound (mixin di WebSocketChannel)."""

    # Stato posseduto da ``WebSocketChannel.__init__`` e usato da questo mixin.
    # Annotato qui (senza assegnazione, nessun effetto a runtime) perché il
    # contratto fra i due sia esplicito e verificabile invece che implicito
    # nell'MRO.
    _reasoning_text_buffers: dict[tuple[str, str], list[str]]
    # chat_id -> connessioni iscritte (target del fan-out).
    _subs: dict[str, set[Any]]
    # connessione -> chat_id a cui è iscritta (usato anche come test di vita).
    _conn_chats: dict[Any, set[str]]
    # Chi guarda quale subagent, con il cursore di ognuno.
    _subagent_watches: SubagentWatchRegistry
    # Unico pump di attività del canale; ``None`` quando nessuno guarda nulla.
    _activity_pump_task: asyncio.Task[None] | None
    # Collaboratori del canale usati dai metodi qui sotto.
    gateway: Any
    logger: Any

    def _drop_stalled_connection(self, connection: Any, *, label: str = "") -> None:
        """Close and discard a connection whose ``send`` timed out (backpressure).

        ``websockets`` applies TCP backpressure: a client with a full send
        buffer blocks ``send`` indefinitely, and the serial outbound dispatcher
        would stall delivery to *every* client behind it. When ``wait_for``
        cancels a blocked ``send``, the frame is left half-written on the wire,
        so the connection is now in an inconsistent state and MUST NOT be
        reused. We therefore schedule a fire-and-forget ``close()`` and remove
        the connection from every subscription set, treating it exactly like a
        ``ConnectionClosed`` (a dead conn — never retried). A held reference
        keeps the close task from being garbage-collected before it runs.
        """
        # Lazy-init the reference set here (the mixin has no __init__) so a held
        # reference keeps each close task alive until it finishes.
        tasks: set[Any] = self.__dict__.setdefault("_stalled_close_tasks", set())
        with suppress(RuntimeError):
            task = asyncio.ensure_future(connection.close())
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        self._cleanup_connection(connection)
        self.logger.warning("connection stalled (send timeout), dropping{}", label)

    async def _fanout(self, conns: list[Any], raw: str, *, label: str = "") -> list[Any]:
        """Deliver *raw* to every connection in *conns*.

        A failure on one connection does not abort
        delivery to the rest: every connection in *conns* is attempted exactly
        once. Connections that raise a non-``ConnectionClosed`` error are
        returned so the caller (``_send_with_retry`` in the dispatcher) can
        retry delivery to just those connections instead of resending to peers
        that already received it — resending to everyone on retry is what
        caused duplicate/garbled messages for multi-connection fan-out.

        Each ``send`` is bounded by ``_SEND_TIMEOUT_S``: a client whose TCP
        buffer is full blocks ``send`` forever, and since the outbound
        dispatcher is serial that would stall delivery to *all* peers. A
        timed-out connection is closed and dropped (like ``ConnectionClosed``),
        NOT added to ``pending`` — it is treated as dead, never retried.
        """
        pending: list[Any] = []
        for connection in conns:
            try:
                await asyncio.wait_for(connection.send(raw), timeout=_SEND_TIMEOUT_S)
            except ConnectionClosed:
                self._cleanup_connection(connection)
                self.logger.warning("connection gone{}", label)
            except TimeoutError:
                # Stalled client: drop it (dead), do NOT append to pending so
                # the dispatcher never retries a connection we just closed.
                self._drop_stalled_connection(connection, label=label)
            except asyncio.CancelledError:
                self._drop_stalled_connection(connection, label=label)
                raise
            except Exception as e:
                self.logger.warning("send failed{} (will retry): {}", label, e)
                pending.append(connection)
        return pending

    async def send_ui_query(self, conn_id: str, correlation_id: str) -> bool:
        """Chiedi la vista corrente alla singola connessione ``conn_id``.

        Mirato a una connessione (non fan-out per chat_id): l'RPC del tool
        ``ui_view`` attende la Future risolta dal frame ``ui_result``. Ritorna
        ``False`` se quella connessione non è (più) presente.
        """
        connection = self._conns_by_id.get(conn_id)
        if connection is None:
            return False
        await self._send_event(connection, "ui_query", correlation_id=correlation_id)
        return True

    # -- Reasoning: un record per segmento, non per chunk --------------------

    @staticmethod
    def _reasoning_key(chat_id: str, stream_id: Any) -> tuple[str, str]:
        return (chat_id, str(stream_id or ""))

    def _reasoning_buffer_for(self, chat_id: str, stream_id: Any) -> list[str]:
        return self._reasoning_text_buffers.setdefault(
            self._reasoning_key(chat_id, stream_id), []
        )

    def _flush_reasoning_buffer(
        self,
        chat_id: str,
        stream_id: Any,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persisti il segmento accumulato come singolo ``reasoning_delta``.

        Idempotente: svuota il buffer, quindi una seconda chiamata (o un
        ``reasoning_end`` doppio) non duplica il record. Il record mantiene la
        forma ``reasoning_delta`` di sempre, così nessun lettore cambia — un
        transcript vecchio pieno di chunk continua a renderizzare identico.
        """
        buffered = self._reasoning_text_buffers.pop(
            self._reasoning_key(chat_id, stream_id), None
        )
        if not buffered:
            return
        text = "".join(buffered)
        if not text:
            return
        body: dict[str, Any] = {
            "event": "reasoning_delta",
            "chat_id": chat_id,
            "text": text,
        }
        if stream_id is not None:
            body["stream_id"] = stream_id
        self._transcripts.prepare_and_append(
            chat_id,
            body,
            metadata=metadata or {},
            phase="reasoning",
        )

    def _flush_all_reasoning_buffers(self, chat_id: str) -> None:
        """Scarica ogni segmento ancora aperto per questa chat.

        Rete di sicurezza per i turni che finiscono senza ``reasoning_end``
        (errore del provider, cancellazione): senza questo il pensiero
        accumulato resterebbe solo in memoria e il transcript perderebbe un
        segmento che prima, un chunk per volta, veniva salvato comunque.
        """
        for key in [k for k in self._reasoning_text_buffers if k[0] == chat_id]:
            self._flush_reasoning_buffer(key[0], key[1] or None)

    def discard_stream_buffer(self, chat_id: str, stream_id: Any) -> None:
        """Drop a stream's buffered text once delivery is permanently abandoned.

        Called by the dispatcher after retries for a ``stream_end`` frame are
        exhausted, so an undeliverable stream doesn't leak its buffer forever.
        """
        self._stream_text_buffers.pop((chat_id, str(stream_id or "")), None)

    async def send(
        self,
        msg: OutboundMessage,
        *,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        if msg.metadata.get("_runtime_model_updated"):
            await self.send_runtime_model_updated(
                model_name=msg.metadata.get("model"),
                model_preset=msg.metadata.get("model_preset"),
                provider=msg.metadata.get("provider"),
            )
            return []
        if msg.metadata.get("_app_data_changed"):
            slug = msg.metadata.get("app_slug")
            if isinstance(slug, str) and slug:
                await self.send_app_data_changed(slug)
            return []
        if msg.metadata.get("_apps_list_changed"):
            await self.send_apps_list_changed()
            return []
        if msg.metadata.get("_user_echo"):
            return await self._send_user_echo(
                msg, only_conns=only_conns, skip_persist=skip_persist
            )

        # Snapshot the subscriber set so ConnectionClosed cleanups mid-iteration are safe.
        conns = list(self._subs.get(msg.chat_id, ()))
        if not conns:
            if (
                msg.metadata.get("_progress")
                or msg.metadata.get("_file_edit_events")
                or msg.metadata.get("_turn_end")
                or msg.metadata.get("_goal_status")
                or msg.metadata.get("_mascot_mood")
                or msg.metadata.get(OUTBOUND_META_SUBAGENT_STATUS) is not None
                or msg.metadata.get(OUTBOUND_META_SUBAGENT_ACTIVITY) is not None
            ):
                self.logger.debug("no active subscribers for chat_id={}", msg.chat_id)
            else:
                self.logger.warning("no active subscribers for chat_id={}", msg.chat_id)
        # Snapshot dei subagent: frame dedicato, mai una bolla di chat. Arriva con
        # ``content=""``, quindi senza questo early-return finirebbe nel percorso
        # generico qui sotto e si materializzerebbe come riga vuota sia nella
        # WebUI sia nel transcript persistito.
        subagent_status = msg.metadata.get(OUTBOUND_META_SUBAGENT_STATUS)
        if subagent_status is not None:
            if conns:
                await self.send_subagent_status(msg.chat_id, subagent_status)
            return []
        # Attività fine di un subagent: stessa disciplina dello snapshot (frame
        # dedicato, mai una bolla, mai nel transcript) più una regola in più —
        # va **solo** ai watcher di quel task, non a tutti gli iscritti alla
        # chat. Vive qui perché anche una finestra pubblicata sul bus non deve
        # poter cadere nel percorso generico e materializzarsi come riga vuota.
        subagent_activity = msg.metadata.get(OUTBOUND_META_SUBAGENT_ACTIVITY)
        if subagent_activity is not None:
            task_id = None
            if isinstance(subagent_activity, dict):
                task_id = normalize_task_id(subagent_activity.get("task_id"))
            if task_id is None:
                self.logger.warning("subagent activity payload without a usable task_id")
                return []
            await self.send_subagent_activity(task_id, subagent_activity)
            return []
        # L'umore della mascotte: frame dedicato, mai una bolla, mai nel
        # transcript (un reload riparte da ``idle``: l'umore e' del momento).
        if msg.metadata.get("_mascot_mood"):
            if conns:
                mood = msg.metadata.get("mascot_mood")
                if isinstance(mood, str) and mood:
                    turn_id = msg.metadata.get(WEBUI_TURN_METADATA_KEY)
                    await self.send_mascot_mood(
                        msg.chat_id,
                        mood,
                        turn_id=turn_id if isinstance(turn_id, str) and turn_id else None,
                    )
            return []
        if msg.metadata.get("_goal_status"):
            if conns:
                status = msg.metadata.get("goal_status")
                if status in ("running", "idle"):
                    started_raw = msg.metadata.get("started_at", msg.metadata.get("goal_started_at"))
                    await self.send_goal_status(
                        msg.chat_id,
                        status,
                        started_at=float(started_raw) if isinstance(started_raw, int | float) else None,
                    )
            return []
        # Signal that the agent has fully finished processing the current turn.
        if msg.metadata.get("_turn_end"):
            lat = msg.metadata.get("latency_ms")
            lat_i = int(lat) if isinstance(lat, (int, float)) else None
            pending = await self.send_turn_end(
                msg.chat_id,
                latency_ms=lat_i,
                metadata=msg.metadata,
                only_conns=only_conns,
                skip_persist=skip_persist,
            )
            return pending
        if msg.metadata.get("_file_edit_events"):
            edits = msg.metadata.get("_file_edit_events")
            return await self.send_file_edit_events(
                msg.chat_id,
                edits if isinstance(edits, list) else [],
                msg.metadata,
                only_conns=only_conns,
                skip_persist=skip_persist,
            )
        text = msg.content
        # Scarica in locale le immagini remote referenziate (markdown inline o
        # entry `media` con URL) e sostituiscile con path locali, così il resto
        # della pipeline le firma/rifirma come qualsiasi media locale. Idempotente
        # sui retry: il dedup nell'ingest salta il fetch se già presente.
        if text or msg.media:
            text, new_media = await self._media.localize_remote_media(text, msg.media)
            if new_media != msg.media:
                msg = dataclasses.replace(msg, media=new_media)
        wire_text = self._media.rewrite_local_markdown_images(text)
        payload: dict[str, Any] = {
            "event": "message",
            "chat_id": msg.chat_id,
            "text": wire_text,
        }
        if msg.media:
            # Import dentro la funzione, e non in testa al modulo: importare
            # ``jenny.webui.media_api`` a freddo passa da
            # ``jenny.channels.http_utils``, quindi esegue
            # ``jenny/channels/__init__.py``, che importa ``websocket`` → questo
            # modulo → di nuovo ``media_api``, ancora a metà inizializzazione.
            # Tre moduli non sopravvivevano a un ``import`` isolato per questa
            # sola riga (v. ``tests/session/test_cold_imports.py``); in un
            # processo che parte dal gateway il ciclo non si vedeva, perché
            # ``webui`` era già caricato.
            #
            # La sede del simbolo resta quella giusta: è un helper della
            # superficie media della WebUI. Ciò che va tolto è il *momento*
            # dell'import, non il posto.
            from jenny.webui.media_api import media_attachment_kind

            payload["media"] = msg.media
            urls: list[dict[str, str]] = []
            for entry in msg.media:
                signed = self._media.sign_or_stage_media_path(Path(entry))
                if signed is not None:
                    # kind (image/video/file) guida il rendering nel client;
                    # path locale serve al chip file per l'apertura nativa.
                    name = signed.get("name") or Path(entry).name
                    signed.setdefault("kind", media_attachment_kind(name))
                    signed.setdefault("path", str(entry))
                    urls.append(signed)
            if urls:
                payload["media_urls"] = urls
        origin = msg.metadata.get("origin_channel")
        if isinstance(origin, str) and origin:
            # Provenienza del turno (es. "telegram"): persiste nel transcript e
            # arriva ai client per il badge di origine.
            payload["origin"] = origin
        lat = msg.metadata.get("latency_ms")
        if isinstance(lat, (int, float)):
            payload["latency_ms"] = int(lat)
        if msg.metadata.get("_tool_events"):
            payload["tool_events"] = msg.metadata["_tool_events"]
        agent_ui = msg.metadata.get(OUTBOUND_META_AGENT_UI)
        if agent_ui is not None:
            payload["agent_ui"] = agent_ui
        if msg.metadata.get("_session_boundary"):
            # /new: il client rende un separatore al posto della bolla. Finisce
            # anche nel transcript persistito (payload è ciò che viene salvato),
            # così il confine resta visibile dopo un reload.
            payload["session_boundary"] = True
        # Mark intermediate agent breadcrumbs (tool-call hints, generic
        # progress strings) so WS clients can render them as subordinate
        # trace rows rather than conversational replies.
        if msg.metadata.get("_tool_hint"):
            payload["kind"] = "tool_hint"
        elif msg.metadata.get("_progress"):
            payload["kind"] = "progress"
        phase = "activity" if payload.get("kind") in ("tool_hint", "progress") else "answer"
        if not skip_persist:
            # Persist exactly once per logical send: a retry (skip_persist=True)
            # must never re-append this message to the transcript, or a
            # partial multi-connection failure would duplicate the row.
            self._transcripts.prepare_and_append(
                msg.chat_id,
                payload,
                metadata=msg.metadata,
                phase=phase,
                transcript_overrides={"text": text},
            )
            if phase == "answer" and not payload.get("origin"):
                # Alert di sistema Android (fire-and-forget, no-op fuori da
                # Android): deve partire anche a zero subscriber — è proprio il
                # caso "WebView chiusa" in cui la notifica serve di più. Dentro
                # `not skip_persist` così un retry non ri-squilla; il gate
                # foreground sta nel bridge Kotlin. I messaggi con origin sono
                # proiezioni di turni già consegnati sul canale d'origine
                # (l'utente sta chattando lì): nessuno squillo sul server.
                notify_delivery(text, msg.metadata)
        raw = json.dumps(payload, ensure_ascii=False)
        target_conns = only_conns if only_conns is not None else conns
        if not target_conns:
            return []
        return await self._fanout(target_conns, raw, label=" ")

    async def _send_user_echo(
        self,
        msg: OutboundMessage,
        *,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        """Eco di un messaggio utente entrato da un altro canale (es. Telegram).

        Persiste la riga ``user`` nel transcript (fonte di verità della storia
        WebUI) e la trasmette live ai client connessi con ``origin`` per il
        badge di provenienza. Stessa disciplina di ``send``: persistenza una
        sola volta per invio logico, i retry (skip_persist) rifanno solo la
        consegna alle connessioni mancate.

        **Transcript e filo vogliono due forme diverse, e questa è l'unica
        funzione in cui la differenza conta.** Sul disco vanno i path
        (``media_paths``): ``transcript_replay`` li ripassa a
        ``augment_user_media`` a ogni ricarico, quindi la URL firmata non
        invecchia mai dentro un file che vive per sempre. Sul filo i path non
        servono a niente — il client non può leggere il filesystem — e serve
        invece la URL firmata. Finché nessun canale d'origine ha mandato
        allegati il difetto non era raggiungibile: la prima foto da Telegram
        l'ha reso visibile come una bolla senza immagine.
        """
        text = msg.content or ""
        if not text.strip() and not msg.media:
            return []
        payload: dict[str, Any] = {
            "event": "user",
            "chat_id": msg.chat_id,
            "text": text,
        }
        origin = msg.metadata.get("origin_channel")
        if isinstance(origin, str) and origin:
            payload["origin"] = origin
        if msg.media:
            payload["media_paths"] = [str(p) for p in msg.media if p]
        if not skip_persist:
            self._transcripts.prepare_and_append(
                msg.chat_id,
                payload,
                metadata=msg.metadata,
                phase="user",
            )
        conns = only_conns if only_conns is not None else list(self._subs.get(msg.chat_id, ()))
        if not conns:
            return []
        raw = json.dumps(self._user_echo_wire(payload), ensure_ascii=False)
        return await self._fanout(conns, raw, label=" user_echo ")

    def _user_echo_wire(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Copia del record con gli allegati firmati, per i soli client.

        Copia e non mutazione: ``payload`` è già stato consegnato al transcript
        e aggiungergli le URL firmate significherebbe scriverci dentro un HMAC
        che scade alla prima rotazione del segreto. Il ramo assistant le
        persiste per ragioni storiche, ma anche là il replay preferisce i path
        e tratta ``media_urls`` come ripiego (``transcript_replay`` riga 606).
        """
        paths = payload.get("media_paths")
        if not isinstance(paths, list) or not paths:
            return payload
        # Import dentro la funzione per la stessa ragione documentata in
        # ``_send_message_payload``: a freddo ``jenny.webui.media_api`` richiude
        # il ciclo su questo modulo (v. tests/session/test_cold_imports.py).
        from jenny.webui.media_api import media_attachment_kind

        urls: list[dict[str, str]] = []
        for entry in paths:
            signed = self._media.sign_or_stage_media_path(Path(entry))
            if signed is None:
                continue
            name = signed.get("name") or Path(entry).name
            signed.setdefault("kind", media_attachment_kind(name))
            signed.setdefault("path", str(entry))
            urls.append(signed)
        if not urls:
            return payload
        return {**payload, "media_urls": urls}

    async def send_reasoning_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
        *,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        """Push one chunk of model reasoning. Mirrors ``send_delta`` shape so
        clients receive a stream that opens, updates in place, and closes —
        rendered above the active assistant bubble with a shimmer header
        until the matching ``reasoning_end`` arrives.

        Il chunk va live ai client ma **non** finisce da solo nel transcript: si
        accumula e viene persistito in un record unico da ``send_reasoning_end``.
        Un pensiero lungo arrivava a ~1400 chunk per segmento, e ogni lettura del
        thread li ripercorreva tutti — 97% dei record di un transcript reale.
        """
        if not delta:
            return []
        meta = metadata or {}
        body: dict[str, Any] = {
            "event": "reasoning_delta",
            "chat_id": chat_id,
            "text": delta,
        }
        stream_id = meta.get("_stream_id")
        if stream_id is not None:
            body["stream_id"] = stream_id
        if not skip_persist:
            self._reasoning_buffer_for(chat_id, stream_id).append(delta)
        conns = only_conns if only_conns is not None else list(self._subs.get(chat_id, ()))
        if not conns:
            return []
        raw = json.dumps(body, ensure_ascii=False)
        return await self._fanout(conns, raw, label=" reasoning ")

    async def send_reasoning_end(
        self,
        chat_id: str,
        metadata: dict[str, Any] | None = None,
        *,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        """Close the current reasoning stream segment for in-place renderers.

        È qui che il segmento accumulato da ``send_reasoning_delta`` diventa un
        record solo. Il replay lo fold identico: ``attach_reasoning_chunk``
        accumula, quindi un record col testo intero e N record coi pezzi
        producono lo stesso messaggio.
        """
        meta = metadata or {}
        body: dict[str, Any] = {
            "event": "reasoning_end",
            "chat_id": chat_id,
        }
        stream_id = meta.get("_stream_id")
        if stream_id is not None:
            body["stream_id"] = stream_id
        if not skip_persist:
            self._flush_reasoning_buffer(chat_id, stream_id, metadata=meta)
            self._transcripts.prepare_and_append(
                chat_id,
                body,
                metadata=meta,
                phase="reasoning",
            )
        conns = only_conns if only_conns is not None else list(self._subs.get(chat_id, ()))
        if not conns:
            return []
        raw = json.dumps(body, ensure_ascii=False)
        return await self._fanout(conns, raw, label=" reasoning_end ")

    async def send_file_edit_events(
        self,
        chat_id: str,
        edits: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
        *,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        payload: dict[str, Any] = {
            "event": "file_edit",
            "chat_id": chat_id,
            "edits": edits,
        }
        if not skip_persist:
            self._transcripts.prepare_and_append(
                chat_id,
                payload,
                metadata=metadata,
                phase="activity",
            )
        conns = only_conns if only_conns is not None else list(self._subs.get(chat_id, ()))
        if not conns:
            return []
        raw = json.dumps(payload, ensure_ascii=False)
        return await self._fanout(conns, raw, label=" file_edit ")

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
        *,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        meta = metadata or {}
        stream_key = (chat_id, str(meta.get("_stream_id") or ""))
        is_stream_end = bool(meta.get("_stream_end"))
        if is_stream_end:
            body: dict[str, Any] = {"event": "stream_end", "chat_id": chat_id}
            # Peek, don't pop: a retry after a partial multi-connection
            # failure must still see the full buffered text. The entry is
            # only dropped once delivery has actually fully succeeded (or is
            # permanently abandoned via discard_stream_buffer), so a retry
            # never finalizes with truncated/lost text.
            buffered = list(self._stream_text_buffers.get(stream_key, []))
            if delta:
                buffered.append(delta)
            full_text = "".join(buffered)
            rewritten = self._media.rewrite_local_markdown_images(full_text)
            if delta or rewritten != full_text:
                body["text"] = rewritten
        else:
            body = {
                "event": "delta",
                "chat_id": chat_id,
                "text": delta,
            }
            if not skip_persist:
                self._stream_text_buffers.setdefault(stream_key, []).append(delta)
        if meta.get("_stream_id") is not None:
            body["stream_id"] = meta["_stream_id"]
        if not skip_persist:
            self._transcripts.prepare_and_append(
                chat_id,
                body,
                metadata=meta,
                phase="answer",
            )
        conns = only_conns if only_conns is not None else list(self._subs.get(chat_id, ()))
        if not conns:
            if is_stream_end:
                self._stream_text_buffers.pop(stream_key, None)
            return []
        raw = json.dumps(body, ensure_ascii=False)
        pending = await self._fanout(conns, raw, label=" stream ")
        if is_stream_end and not pending:
            # Fully delivered — safe to release the buffered text now.
            self._stream_text_buffers.pop(stream_key, None)
        return pending

    async def send_turn_end(
        self,
        chat_id: str,
        latency_ms: int | None = None,
        *,
        metadata: dict[str, Any] | None = None,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        """Signal that the agent has fully finished processing the current turn."""
        body: dict[str, Any] = {"event": "turn_end", "chat_id": chat_id}
        if latency_ms is not None:
            body["latency_ms"] = int(latency_ms)
        if not skip_persist:
            # Prima del marcatore di fine turno: un segmento di reasoning rimasto
            # aperto va scritto *dentro* il suo turno, non nel successivo — è
            # ``turn_end`` che delimita i turni per lo split del transcript.
            self._flush_all_reasoning_buffers(chat_id)
            self._transcripts.prepare_and_append(
                chat_id,
                body,
                metadata=metadata,
                phase="complete",
            )
        conns = only_conns if only_conns is not None else list(self._subs.get(chat_id, ()))
        if not conns:
            return []
        raw = json.dumps(body, ensure_ascii=False)
        return await self._fanout(conns, raw, label=" turn_end ")

    async def send_goal_status(
        self,
        chat_id: str,
        status: str,
        *,
        started_at: float | None = None,
    ) -> None:
        """Notify subscribed clients that a turn started or finished (wall-clock hint)."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {
            "event": "goal_status",
            "chat_id": chat_id,
            "status": status,
        }
        if status == "running" and started_at is not None:
            body["started_at"] = started_at
        raw = json.dumps(body, ensure_ascii=False)
        # Idempotent refresh-hint: discard pending, no retry (next status replaces it).
        await self._fanout(conns, raw, label=" goal_status ")

    async def send_mascot_mood(
        self,
        chat_id: str,
        mood: str,
        *,
        turn_id: str | None = None,
    ) -> None:
        """L'umore di Jenny per il turno appena chiuso, agli iscritti della chat.

        Stessa disciplina di ``goal_status``: nessun retry (il turno dopo lo
        sostituisce) e nessuna persistenza. ``turn_id`` c'e' quando il turno lo
        aveva; il client lo usa per scartare la reazione a una risposta che non
        e' piu' l'ultima.
        """
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {"event": "mascot_mood", "chat_id": chat_id, "mood": mood}
        if turn_id:
            body["turn_id"] = turn_id
        raw = json.dumps(body, ensure_ascii=False)
        await self._fanout(conns, raw, label=" mascot_mood ")

    async def send_subagent_status(self, chat_id: str, payload: Any) -> None:
        """Manda ai client lo snapshot dei subagent (running + terminati recenti).

        Il payload è quello di ``SubagentManager.status_snapshot``, servito
        identico da ``GET /api/subagents``: una sola forma, due trasporti — il
        pannello WebUI consuma lo stesso oggetto da entrambi. Non persiste nel
        transcript e non si ritenta: è stato ricalcolabile e il prossimo snapshot
        rimpiazza quello perso (stessa disciplina di ``send_goal_status``).
        """
        conns = list(self._subs.get(chat_id, ()))
        if not conns or not isinstance(payload, dict):
            return
        running = payload.get("running")
        recent = payload.get("recent")
        body: dict[str, Any] = {
            "event": "subagent_status",
            "chat_id": chat_id,
            "running": running if isinstance(running, list) else [],
            "recent": recent if isinstance(recent, list) else [],
        }
        raw = json.dumps(body, ensure_ascii=False)
        # Idempotent refresh-hint: discard pending, no retry (next snapshot replaces it).
        await self._fanout(conns, raw, label=" subagent_status ")

    # -- Attività fine di un subagent (solo per chi la sta guardando) --------
    #
    # Dove nasce la spinta, e perché qui. I produttori (fase 2) appendono al ring
    # dentro il task del subagent; qualcosa deve trasformare "appeso" in
    # "spedito". Tre possibilità, e i tre criteri che le separano:
    #
    # * **il log notifica** (callback su ``append``): il subagent finirebbe a
    #   fare fan-out verso i client dentro il proprio task — un client lento
    #   rallenterebbe il lavoro, e per evitarlo servirebbe comunque una coda;
    #   inoltre spedirebbe un frame per evento, senza coalescenza;
    # * **il bus lo trasporta**: un messaggio outbound per evento su una coda
    #   condivisa con la chat, che nessun tetto locale può coalizzare, e il
    #   produttore dovrebbe sapere chi sta guardando (che è esattamente
    #   l'informazione che vive solo qui);
    # * **il canale legge** (scelta fatta): un solo task per canale, vivo solo
    #   mentre almeno una connessione guarda, che a ogni tick legge il ring dei
    #   soli task guardati. Il subagent non blocca mai su un client, perché non
    #   lo conosce; il burst si coalizza per costruzione (un frame per tick, con
    #   tetto ``MAX_FRAME_EVENTS``); un watcher disconnesso non accumula nulla,
    #   perché l'unico stato è il suo cursore e ``forget`` lo cancella.
    #
    # Il prezzo dichiarato: la latenza è al massimo un tick
    # (``ACTIVITY_PUMP_INTERVAL_S``) invece di essere immediata. Con il modal
    # aperto è invisibile; con il modal chiuso il costo è zero, che è
    # l'invariante che si è scelto di proteggere.

    def _chat_id_for(self, connection: Any) -> str:
        """chat_id da mettere nel frame di una connessione specifica.

        I frame di attività sono mirati (non fan-out per chat), ma portano
        comunque ``chat_id`` come ogni altro frame, così il router del client
        non ha bisogno di un caso speciale.
        """
        chats = self._conn_chats.get(connection)
        return min(chats) if chats else WEBUI_DEFAULT_CHAT_ID

    def _subagent_activity_log(self) -> Any | None:
        """Il ``SubagentActivityLog``, o ``None`` se non è (ancora) disponibile.

        Risolto a ogni chiamata e mai memorizzato: durante l'onboarding l'agente
        non esiste ancora e il gateway serve già la WebUI. Dipendenza opaca
        (duck-typing su ``tail_window``): il canale non importa ``jenny/agent``.
        """
        getter = getattr(self.gateway, "get_subagent_manager", None)
        if getter is None:
            return None
        try:
            manager = getter()
        except Exception as e:  # noqa: BLE001 — un getter rotto non ferma il canale
            self.logger.warning("subagent manager lookup failed: {}", e)
            return None
        log = getattr(manager, "activity", None)
        return log if callable(getattr(log, "tail_window", None)) else None

    def _read_activity_window(
        self,
        log: Any,
        task_id: str,
        *,
        since: int,
        limit: int = MAX_FRAME_EVENTS,
    ) -> dict[str, Any] | None:
        """Legge il ring e ne ritorna la forma di filo, o ``None``.

        Chiamata sincrona dal loop: ``tail_window`` è una scansione in RAM di un
        deque da ≤200 elementi sotto un lock tenuto per la sola copia — costa
        meno di un ``await``, e farla in un thread aggiungerebbe uno switch per
        niente.
        """
        try:
            window = log.tail_window(task_id, since_seq=since, limit=limit)
        except Exception as e:  # noqa: BLE001 — la telemetria non rompe il canale
            self.logger.warning("subagent activity read failed for {}: {}", task_id, e)
            return None
        return window_payload(window, limit=limit)

    async def send_subagent_activity(self, task_id: str, window: Any) -> None:
        """Manda la finestra di attività ai **soli** watcher di ``task_id``.

        Ogni watcher riceve la propria fetta (``slice_for_cursor``) a partire dal
        suo cursore, ricavata da **una** lettura del ring: due schede sullo
        stesso subagent non raddoppiano il lavoro. Non persiste nel transcript e
        non si ritenta: sono eventi ad alta frequenza, e ripeterli riempirebbe il
        transcript di righe che nessuno rileggerà — il ``seq`` più la risync HTTP
        sono il modo giusto di recuperare un frame perso.
        """
        payload = window_payload(window, limit=MAX_FRAME_EVENTS)
        if payload is None or not payload["events"]:
            return
        for connection, cursor in self._subagent_watches.cursors(task_id):
            fragment = slice_for_cursor(payload, cursor)
            if fragment is None:
                continue
            frame = activity_frame(task_id, self._chat_id_for(connection), fragment)
            raw = json.dumps(frame, ensure_ascii=False)
            pending = await self._fanout([connection], raw, label=" subagent_activity ")
            if pending:
                # Invio non riuscito su una connessione ancora viva: il cursore
                # NON avanza, così il tick successivo riprova dagli stessi
                # eventi. Il lavoro resta limitato (il ring e il tetto del frame
                # sono i due argini), e un client davvero morto esce comunque dal
                # registro via ``_cleanup_connection``.
                continue
            self._subagent_watches.advance(connection, task_id, fragment["last_seq"])

    async def send_subagent_activity_window(
        self,
        connection: Any,
        task_id: str,
        *,
        since: int = 0,
    ) -> int:
        """Risposta immediata a un watch: la finestra corrente, anche se vuota.

        È ciò che fa apparire subito del contenuto nel modal invece di una lista
        vuota in attesa del prossimo evento. Il frame parte **anche** a finestra
        vuota, perché "non è ancora successo niente" (``latest_seq == 0``) è
        un'informazione, e il client deve poterla distinguere da un buco.

        Ritorna il cursore da cui il watch deve partire: il chiamante registra
        il watch *dopo* questo invio, così il pump non può infilare un delta
        davanti alla risposta iniziale.
        """
        since = normalize_since(since)
        log = self._subagent_activity_log()
        payload = None
        if log is not None:
            payload = self._read_activity_window(log, task_id, since=since)
        if payload is None:
            payload = empty_window_payload(since)
        frame = activity_frame(
            task_id, self._chat_id_for(connection), payload, initial=True
        )
        raw = json.dumps(frame, ensure_ascii=False)
        await self._fanout([connection], raw, label=" subagent_activity ")
        return max(since, int(payload["last_seq"]))

    def _ensure_subagent_activity_pump(self) -> None:
        """Avvia il pump se serve. Idempotente: un solo task per canale."""
        if not self._subagent_watches.active:
            return
        task = self._activity_pump_task
        if task is not None and not task.done():
            return
        try:
            self._activity_pump_task = asyncio.ensure_future(self._subagent_activity_pump())
        except RuntimeError:
            # Nessun event loop (uso sincrono nei test): senza pump il watch
            # resta registrato e la risposta iniziale è già partita.
            self._activity_pump_task = None

    def stop_subagent_activity_pump(self) -> None:
        """Ferma il pump e svuota il registro (shutdown del canale)."""
        task = self._activity_pump_task
        self._activity_pump_task = None
        if task is not None and not task.done():
            task.cancel()
        self._subagent_watches.clear()

    async def _subagent_activity_pump(self) -> None:
        """Un tick ogni ``ACTIVITY_PUMP_INTERVAL_S`` finché qualcuno guarda.

        Il ciclo **si spegne da solo** quando l'ultimo watch sparisce: è questa
        la forma dell'invariante "costo proporzionale a ciò che si guarda". Non
        c'è nessun ``await`` fra la condizione che risulta falsa e la
        cancellazione dell'handle, quindi ``_ensure_subagent_activity_pump`` non
        può osservare un pump morto e crederlo vivo.
        """
        try:
            while self._subagent_watches.active:
                await asyncio.sleep(ACTIVITY_PUMP_INTERVAL_S)
                try:
                    await self._pump_subagent_activity_once()
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 — un tick rotto non chiude il pump
                    self.logger.warning("subagent activity pump tick failed: {}", e)
        finally:
            self._activity_pump_task = None

    async def _pump_subagent_activity_once(self) -> None:
        """Un tick: una lettura del ring per task guardato, poi le fette."""
        log = self._subagent_activity_log()
        if log is None:
            return
        for task_id in self._subagent_watches.tasks():
            cursor = self._subagent_watches.min_cursor(task_id)
            payload = self._read_activity_window(log, task_id, since=cursor)
            if payload is None or not payload["events"]:
                continue
            await self.send_subagent_activity(task_id, payload)

    async def send_app_data_changed(self, slug: str) -> None:
        """Broadcast that a Jenny App's data changed (open app iframes refresh)."""
        conns = list(self._conn_chats)
        if not conns:
            return
        raw = json.dumps({"event": "app_data_changed", "slug": slug}, ensure_ascii=False)
        # Idempotent refresh-hint: discard pending, no retry (next change replaces it).
        await self._fanout(conns, raw, label=" app_data_changed ")

    async def send_apps_list_changed(self) -> None:
        """Broadcast that the Jenny Apps list changed (WebUI grid refreshes)."""
        conns = list(self._conn_chats)
        if not conns:
            return
        raw = json.dumps({"event": "apps_list_changed"}, ensure_ascii=False)
        # Idempotent refresh-hint: discard pending, no retry (next change replaces it).
        await self._fanout(conns, raw, label=" apps_list_changed ")

    async def send_runtime_model_updated(
        self,
        *,
        model_name: Any,
        model_preset: Any = None,
        provider: Any = None,
    ) -> None:
        """Broadcast runtime model changes to every open websocket connection."""
        conns = list(self._conn_chats)
        if not conns or not isinstance(model_name, str) or not model_name.strip():
            return
        body: dict[str, Any] = {
            "event": "runtime_model_updated",
            "model_name": model_name.strip(),
        }
        if isinstance(model_preset, str) and model_preset.strip():
            body["model_preset"] = model_preset.strip()
        if isinstance(provider, str) and provider.strip():
            body["provider"] = provider.strip()
        raw = json.dumps(body, ensure_ascii=False)
        # Idempotent refresh-hint: discard pending, no retry (next update replaces it).
        await self._fanout(conns, raw, label=" runtime_model_updated ")
