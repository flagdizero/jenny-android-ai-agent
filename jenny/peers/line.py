"""La linea passata: l'utente scrive a un'altra sua Jenny attraverso questa.

Una **domanda** (``jenny.peers.questions``) è Jenny che chiede a Jenny: chi
risponde lo fa in un turno suo, in sola lettura, e il testo che arriva è un
dato. Una **linea** è un'altra cosa: l'utente dice «passami Lavoro» e da lì in
poi quello che scrive su questo telefono arriva all'altra Jenny come se lo
scrivesse sul suo. Questa non lo legge e non risponde.

**Chi passa** (``out``). La linea è di una chat (canale e ``chat_id``): finché è
aperta, i messaggi di quella chat non diventano un turno qui. Li porta il
**codice** (:meth:`PeerLines.route`), mai uno strumento: il modello di questa
Jenny può aprire una linea, non scrivere all'altra a nome dell'utente. Lo stato
sta in ``state.sqlite``, perché il gateway su Android riparte spesso e una linea
dimenticata manderebbe il messaggio dopo alla Jenny sbagliata. Il **testo** no:
resta in memoria finché la linea è aperta, per ridisegnare la chat, e non entra
né nella sessione né nel transcript di qui. Alla chiusura resta un segnaposto
(«hai parlato con Lavoro, dalle… alle…») e, se l'altra lo manda, un riassunto di
due righe scritto da lei.

**Chi riceve** (``in``). Solo con il permesso ``PeerConfig.line``, che decide
questo telefono. Il messaggio entra nella chat vera — quella di sempre, o
quella di un quaderno — come un messaggio scritto da un'altra finestra della
WebUI (``WebSocketChannel.inject_user_message``): stessa sessione, stessi
strumenti, stesso transcript, con l'etichetta di chi l'ha mandato. La risposta
finale torna all'altra Jenny come ``line_text`` (:meth:`PeerLines.on_outbound`).

**Un salto solo.** Un turno arrivato da una linea non ne apre un'altra e non fa
``peer_ask``: gli strumenti lo vedono dai metadati del turno (:func:`line_of`).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from loguru import logger

from .link import PeerError, PeerLink
from .model import (
    KIND_JENNY,
    KIND_LINE_CHATS,
    KIND_LINE_CLOSE,
    KIND_LINE_LIST,
    KIND_LINE_MOVE,
    KIND_LINE_OPEN,
    KIND_LINE_SAY,
    KIND_LINE_STATE,
    KIND_LINE_TEXT,
    KIND_LINE_TRACE,
    is_id,
    peer_kind,
    text_field,
)
from .store import new_id, now_ms

# Il canale d'origine di un messaggio arrivato da una linea, come lo vedono il
# transcript e l'etichetta della WebUI (``shared/origin.js``).
LINE_ORIGIN = "peer_line"
# Nei metadati del turno: da quale Jenny arriva, e di quale linea.
LINE_PEER_META = "_line_peer"
LINE_ID_META = "_line_lid"

MAX_SAY_CHARS = 8000
MAX_TEXT_CHARS = 16000
MAX_TRACE_CHARS = 400
MAX_PLACES = 200
# Una linea ferma da tanto si chiude da sola, come un planning dimenticato.
IDLE_CLOSE_MS = 12 * 3600 * 1000
# Quanto si aspetta l'elenco delle chat dell'altra prima di dire che non risponde.
CHATS_TIMEOUT_S = 8.0
# Quanto `peer_line` aspetta il sì o il no dell'altra prima di rispondere al modello.
OPEN_WAIT_S = 4.0
# Il turno che scrive le due righe: se non finisce, resta il segnaposto nudo.
TRACE_TIMEOUT_S = 120.0
# Quanto del filo si tiene in memoria per ridisegnare la chat, e per il riassunto.
BUFFER_KEEP = 200
# Le parole con cui si torna a casa. Le riconosce il codice, prima di tutto:
# a linea aperta questa Jenny non legge, quindi non può capirlo lei.
RETURN_COMMANDS = frozenset({"/torna", "/back"})
# I canali da cui una chat si può passare.
LINE_CHANNELS = frozenset({"websocket", "telegram"})

STATES = ("opening", "open")

Emit = Callable[[str, str, dict[str, Any]], Awaitable[None]]
Inject = Callable[[str, str, dict[str, str], dict[str, Any]], Awaitable[None]]
Deliver = Callable[[Any], Awaitable[None]]
Places = Callable[[], list[str]]


# Un rimando a una pagina, com'è scritto in chat: ``[[quaderno/pagina]]`` o
# ``[[quaderno/pagina|etichetta]]``.
_PAGE_LINK = re.compile(r"\[\[([^\[\]|\n]+)(?:\|([^\[\]\n]+))?\]\]")


def plain_links(text: str) -> str:
    """Il testo senza i rimandi alle pagine: resta il nome, fra virgolette basse.

    Sull'altro telefono le pagine di questa Jenny non si aprono (sulla linea
    passa solo il testo), e ``[[piante/acero/concimazione]]`` lì sarebbe una
    riga di simboli: resta «acero/concimazione», o l'etichetta se c'è.
    """
    def name(match: re.Match[str]) -> str:
        label = (match.group(2) or "").strip()
        if label:
            return label
        parts = [p for p in match.group(1).strip().split("/") if p]
        return "«" + "/".join(parts[1:] or parts) + "»"

    return _PAGE_LINK.sub(name, text)


def line_of(metadata: Any) -> str | None:
    """L'id della Jenny da cui arriva questo turno, se arriva da una linea."""
    if not isinstance(metadata, dict):
        return None
    peer = metadata.get(LINE_PEER_META)
    return peer if is_id(peer) else None


def line_runtime_lines(metadata: Any) -> list[str]:
    """La riga del runtime context di un turno arrivato da una linea.

    Il modello deve sapere chi scrive e da dove: sui due emulatori (23/11), a
    «chiedi a Personale…» detto dentro il quaderno di Lavoro, Lavoro ha cercato
    un *quaderno* chiamato Personale. Niente regole qui (stanno negli strumenti,
    che rifiutano col motivo): solo il fatto, col nome.
    """
    if line_of(metadata) is None:
        return []
    device = metadata.get("origin_device") if isinstance(metadata, dict) else None
    name = device.strip()[:80] if isinstance(device, str) and device.strip() else "another Jenny"
    return [
        f"Line: the user is writing to you from their other phone, through their Jenny "
        f"\"{name}\" (a direct line): these are the user's own words, not {name}'s. "
        f"From a line turn no other Jenny can be asked or reached, {name} included, in "
        f"any of your chats: for that the user goes back to {name} first (the back arrow, "
        f"or /torna) and asks there."
    ]


def line_mark(metadata: Any) -> dict[str, str] | None:
    """Il segno della linea di un turno (``{"peer", "lid"}``), o ``None``.

    Lo porta un aiutante lanciato da quel turno: il turno che legge il suo
    risultato risponde ancora sulla linea (``SubagentSpec.peer_line``).
    """
    peer = line_of(metadata)
    lid = metadata.get(LINE_ID_META) if isinstance(metadata, dict) else None
    if peer is None or not is_id(lid):
        return None
    return {"peer": peer, "lid": lid}


def line_metadata(mark: Any) -> dict[str, str]:
    """I metadati di turno che rimettono il segno *mark* (vuoti se non è valido)."""
    if not isinstance(mark, dict):
        return {}
    peer, lid = mark.get("peer"), mark.get("lid")
    if not (is_id(peer) and is_id(lid)):
        return {}
    return {LINE_PEER_META: peer, LINE_ID_META: lid}


def out_key(channel: str, chat_id: str) -> str:
    return f"{channel}\n{chat_id}"


def _split_key(key: str) -> tuple[str, str]:
    channel, _, chat = key.partition("\n")
    return channel, chat


def chat_id_of(place: str) -> str:
    """Il ``chat_id`` della WebUI per *place*: la chat di sempre o un quaderno."""
    from jenny.session.keys import project_session_key

    return project_session_key(place) if place else "default"


def _clock(ms: int) -> str:
    return time.strftime("%H:%M", time.localtime(ms / 1000))


class PeerLines:
    """Le linee di questa Jenny: quelle che ha passato e quelle che ha ricevuto."""

    def __init__(
        self,
        link: PeerLink,
        workspace: Path,
        *,
        get_agent: Callable[[], Any],
        deliver: Deliver | None = None,
        emit: Emit | None = None,
        inject: Inject | None = None,
        places: Places | None = None,
    ) -> None:
        self._link = link
        self._workspace = Path(workspace)
        self._get_agent = get_agent
        self._deliver = deliver
        self._emit_fn = emit
        self._inject = inject
        self._places = places
        # Il testo delle linee aperte, in memoria: ``out`` per chiave di chat,
        # ``in`` per id della Jenny. Un riavvio lo perde, ed è voluto.
        self._out_items: dict[str, list[dict[str, Any]]] = {}
        self._in_items: dict[str, list[dict[str, Any]]] = {}
        self._chats_waiting: dict[str, asyncio.Future[dict[str, Any]]] = {}
        # Perché si è chiusa l'ultima linea di una chat: `settled` lo legge
        # per dire al modello com'è finita un'apertura rifiutata subito.
        self._last_close: dict[str, tuple[str, str]] = {}
        # Le chat di cui il filo è già stato letto da quando il processo è su.
        self._seen_out: set[str] = set()
        self._tasks: set[asyncio.Task[Any]] = set()
        link.lines = self
        link.on_nack = self._on_nack
        for kind, handler in (
            (KIND_LINE_OPEN, self._on_open),
            (KIND_LINE_SAY, self._on_say),
            (KIND_LINE_MOVE, self._on_move),
            (KIND_LINE_LIST, self._on_list),
            (KIND_LINE_CLOSE, self._on_close),
            (KIND_LINE_STATE, self._on_state),
            (KIND_LINE_TEXT, self._on_text),
            (KIND_LINE_CHATS, self._on_chats),
            (KIND_LINE_TRACE, self._on_trace),
        ):
            link.register(kind, handler)

    @property
    def store(self) -> Any:
        return self._link.store

    async def stop(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _db(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def _emit(self, channel: str, chat_id: str, frame: dict[str, Any]) -> None:
        if self._emit_fn is None:
            return
        try:
            await self._emit_fn(channel, chat_id, frame)
        except Exception:
            logger.opt(exception=True).warning("peers: a line frame could not be shown")

    async def _peer_by_ref(self, ref: str) -> Any:
        """La Jenny che *ref* nomina: il suo id, il suo nome, o l'unica che c'è."""
        cfg = await self._link._config()
        if not cfg.enabled:
            raise PeerError("disabled")
        peers = list(cfg.peers)
        if not peers:
            raise PeerError("no_peers")
        if is_id(ref):
            found = next((p for p in peers if p.id == ref), None)
            if found is None:
                raise PeerError("not_found")
            return found
        name = " ".join((ref or "").split()).casefold()
        if not name:
            if len(peers) == 1:
                return peers[0]
            raise PeerError("which_peer")
        exact = [p for p in peers if p.name.casefold() == name]
        if len(exact) == 1:
            return exact[0]
        partial = [p for p in peers if name in p.name.casefold()]
        if len(partial) == 1:
            return partial[0]
        raise PeerError("which_peer" if partial else "not_found")

    # ======================================================================
    # Chi passa la linea
    # ======================================================================

    async def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        peer = await self._link._peer(row["peer"])
        state = await self._db(self.store.state, row["peer"])
        queued = await self._db(self.store.queued_count, row["peer"])
        return {
            "peer": row["peer"],
            "name": peer.name if peer is not None else row["peer"][:8],
            "kind": peer_kind(peer.kind) if peer is not None else KIND_JENNY,
            "lid": row["lid"],
            "place": row["place"],
            "state": row["state"] if row["state"] in STATES else "open",
            "since": int(row["since_ms"]),
            "queued": int(queued),
            # L'altra non si raggiunge: l'ultimo tentativo è fallito dopo
            # l'ultima volta che l'abbiamo vista.
            "unreachable": bool(
                state["last_error"] and state["last_try_ms"] > state["last_seen_ms"]
            ),
        }

    async def status(self, channel: str, chat_id: str) -> dict[str, Any] | None:
        """La linea aperta su quella chat, col suo filo; ``None`` se non c'è."""
        key = out_key(channel, chat_id)
        row = await self._db(self.store.line, "out", key)
        if row is None:
            return None
        public = await self._public(row)
        if key not in self._seen_out:
            # La prima lettura dopo un riavvio: i messaggi ancora in coda
            # tornano in testa al filo.
            self._seen_out.add(key)
            have = {item.get("id") for item in self._out_items.get(key, ())}
            unsent = [item for item in await self._unsent(row) if item["id"] not in have]
            self._out_items[key] = unsent + self._out_items.get(key, [])
        public["items"] = list(self._out_items.get(key, ()))
        return public

    async def _unsent(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        """Dopo un riavvio: quel che l'utente ha scritto e non è ancora partito.

        Il filo sta in memoria e un riavvio lo perde, ma i messaggi che
        aspettano l'altra Jenny sono nella coda: senza di loro la sua risposta
        arriverebbe sotto a niente.
        """
        waiting = await self._db(self.store.pending, row["peer"])
        return [
            {"id": str(msg["body"].get("mid") or msg["id"]), "role": "user",
             "text": str(msg["body"].get("text", "")),
             "at": int(msg["created_ms"])}
            for msg in waiting
            if msg["kind"] == KIND_LINE_SAY and msg["body"].get("lid") == row["lid"]
        ]

    async def has_out(self, channel: str, chat_id: str) -> bool:
        return await self._db(self.store.line, "out", out_key(channel, chat_id)) is not None

    async def open(
        self, channel: str, chat_id: str, peer_ref: str, place: str = "",
    ) -> dict[str, Any]:
        """Passa la chat *chat_id* di *channel* a un'altra Jenny."""
        from jenny.session.keys import is_valid_project_name

        if channel not in LINE_CHANNELS:
            raise PeerError("bad_channel")
        peer = await self._peer_by_ref(peer_ref)
        place = (place or "").strip()
        if peer_kind(peer.kind) != KIND_JENNY:
            # Un computer con opencode ha una chat sola: niente quaderni.
            place = ""
        if place and not is_valid_project_name(place):
            raise PeerError("bad_place")
        key = out_key(channel, chat_id)
        before = await self._db(self.store.line, "out", key)
        if before is not None:
            if before["peer"] == peer.id:
                # La stessa Jenny: è uno spostamento, non una linea nuova.
                if before["place"] != place:
                    await self.move(channel, chat_id, place)
                return await self._public(await self._db(self.store.line, "out", key))
            await self.close(channel, chat_id)
        lid = new_id()
        await self._db(
            self.store.line_put, "out", key, peer=peer.id, lid=lid, place=place, state="opening",
        )
        self._out_items[key] = []
        self._seen_out.add(key)
        await self._link.send(peer.id, KIND_LINE_OPEN, {"lid": lid, "place": place})
        await self._db(self.store.log, peer.id, "out", KIND_LINE_OPEN, {"place": place})
        self._link.wake(peer.id)
        public = await self._public(await self._db(self.store.line, "out", key))
        await self._emit(channel, chat_id, {"kind": "state", "line": public})
        return public

    async def settled(
        self, channel: str, chat_id: str, lid: str, *, timeout: float = OPEN_WAIT_S,
    ) -> str:
        """Com'è finita l'apertura di *lid*: ``open``, il motivo del rifiuto o ``waiting``.

        L'altra Jenny risponde in un attimo se c'è: chi apre a parole
        (``peer_line``) aspetta qui quel poco, così Jenny non dice «ti passo
        Lavoro» di una linea che Lavoro ha già rifiutato. Se non risponde entro
        *timeout* la linea resta aperta da questo lato e i messaggi aspettano.
        """
        key = out_key(channel, chat_id)
        deadline = time.monotonic() + timeout
        while True:
            row = await self._db(self.store.line, "out", key)
            if row is None or row["lid"] != lid:
                closed = self._last_close.get(key)
                return closed[1] if closed is not None and closed[0] == lid else "closed"
            if row["state"] == "open":
                return "open"
            if time.monotonic() >= deadline:
                return "waiting"
            await asyncio.sleep(0.15)

    async def route(self, channel: str, chat_id: str, text: str) -> bool:
        """Vero se *text* era per una linea aperta, e ci è andato (o l'ha chiusa)."""
        key = out_key(channel, chat_id)
        row = await self._db(self.store.line, "out", key)
        if row is None:
            return False
        clean = (text or "").strip()
        if clean.casefold() in RETURN_COMMANDS:
            await self.close(channel, chat_id)
            return True
        if not clean:
            return True
        await self._say(key, row, clean[:MAX_SAY_CHARS])
        return True

    async def _say(self, key: str, row: dict[str, Any], text: str) -> None:
        mid = new_id()
        await self._link.send(
            row["peer"], KIND_LINE_SAY, {"lid": row["lid"], "mid": mid, "text": text},
        )
        self._link.wake(row["peer"])
        await self._db(self.store.line_update, "out", key, last_ms=now_ms())
        item = {"id": mid, "role": "user", "text": text, "at": now_ms()}
        self._remember(self._out_items, key, item)
        channel, chat_id = _split_key(key)
        await self._emit(channel, chat_id, {"kind": "item", "item": item})

    @staticmethod
    def _remember(items: dict[str, list[dict[str, Any]]], key: str, item: dict[str, Any]) -> None:
        rows = items.setdefault(key, [])
        rows.append(item)
        if len(rows) > BUFFER_KEEP:
            del rows[: len(rows) - BUFFER_KEEP]

    async def move(self, channel: str, chat_id: str, place: str) -> None:
        """Chiede all'altra di spostare la linea in un'altra sua chat."""
        from jenny.session.keys import is_valid_project_name

        place = (place or "").strip()
        if place and not is_valid_project_name(place):
            raise PeerError("bad_place")
        row = await self._db(self.store.line, "out", out_key(channel, chat_id))
        if row is None:
            raise PeerError("no_line")
        await self._link.send(row["peer"], KIND_LINE_MOVE, {"lid": row["lid"], "place": place})
        self._link.wake(row["peer"])

    async def retry(self, channel: str, chat_id: str) -> None:
        row = await self._db(self.store.line, "out", out_key(channel, chat_id))
        if row is not None:
            self._link.wake(row["peer"])

    async def close(self, channel: str, chat_id: str, *, reason: str = "user",
                    tell_peer: bool = True) -> bool:
        """Chiude la linea di quella chat: lo dice all'altra e lascia il segnaposto."""
        key = out_key(channel, chat_id)
        row = await self._db(self.store.line, "out", key)
        if row is None:
            return False
        await self._db(self.store.line_drop, "out", key)
        self._last_close[key] = (str(row["lid"]), reason)
        self._seen_out.discard(key)
        items = self._out_items.pop(key, [])
        peer = await self._link._peer(row["peer"])
        name = peer.name if peer is not None else row["peer"][:8]
        if tell_peer and peer is not None:
            try:
                await self._link.send(row["peer"], KIND_LINE_CLOSE, {"lid": row["lid"]})
            except PeerError:
                pass
        await self._db(self.store.log, row["peer"], "out", KIND_LINE_CLOSE, {"reason": reason})
        await self._emit(channel, chat_id, {
            "kind": "closed", "reason": reason, "name": name, "lid": row["lid"],
        })
        # Il segnaposto resta solo se qualcosa è stato detto: una linea aperta
        # e richiusa non lascia una riga nella chat.
        if any(item.get("role") == "user" for item in items):
            await self._deliver_stub(
                name=name, peer_id=row["peer"], lid=row["lid"],
                since=int(row["since_ms"]), until=now_ms(),
            )
        return True

    async def close_for_peer(self, peer_id: str, *, reason: str) -> None:
        """Chiude ogni linea verso *peer_id* (scollegata, rifiuto, versione vecchia)."""
        for row in await self._db(self.store.lines, "out"):
            if row["peer"] == peer_id:
                channel, chat_id = _split_key(row["key"])
                await self.close(channel, chat_id, reason=reason, tell_peer=False)

    async def peers_changed(self) -> None:
        """Le Jenny collegate sono cambiate: la WebUI rilegge lo stato."""
        from jenny.bus.events import WEBSOCKET_CHANNEL, WEBUI_DEFAULT_CHAT_ID

        await self._emit(WEBSOCKET_CHANNEL, WEBUI_DEFAULT_CHAT_ID, {"kind": "peers"})

    async def peer_gone(self, peer_id: str) -> None:
        """*peer_id* si scollega: via le linee nei due versi, senza scriverle più."""
        await self.close_for_peer(peer_id, reason="unpaired")
        row = await self._db(self.store.line, "in", peer_id)
        if row is not None:
            await self._db(self.store.line_drop, "in", peer_id)
            self._in_items.pop(peer_id, None)

    async def chats(self, peer_ref: str) -> list[str]:
        """Le chat dell'altra Jenny (i nomi dei suoi quaderni), chieste adesso."""
        peer = await self._peer_by_ref(peer_ref)
        if peer_kind(peer.kind) != KIND_JENNY:
            return []
        rid = new_id()
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._chats_waiting[rid] = future
        try:
            await self._link.send(peer.id, KIND_LINE_LIST, {"rid": rid})
            self._link.wake(peer.id)
            try:
                body = await asyncio.wait_for(future, timeout=CHATS_TIMEOUT_S)
            except asyncio.TimeoutError:
                raise PeerError("unreachable") from None
        finally:
            self._chats_waiting.pop(rid, None)
        if body.get("ok") is not True:
            raise PeerError("refused")
        return [str(name) for name in body.get("chats") or [] if isinstance(name, str)]

    async def _deliver_stub(
        self, *, name: str, peer_id: str, lid: str, since: int, until: int,
    ) -> None:
        """«Hai parlato con Lavoro»: una scheda, e una riga nella storia di Jenny.

        Sempre nella chat di sempre della WebUI, che è la vista della
        conversazione: da Telegram la chiusura è già detta con una riga sua
        (il frame ``closed``), e il testo qui sotto è per il modello.
        """
        from jenny.bus.events import WEBSOCKET_CHANNEL, WEBUI_DEFAULT_CHAT_ID

        channel, chat_id = WEBSOCKET_CHANNEL, WEBUI_DEFAULT_CHAT_ID
        card = {"peer": name, "peerId": peer_id, "lid": lid, "from": since, "to": until}
        # Il testo per la storia del modello: sa che c'è stato un buco, e di chi è.
        text = (
            f"[The user talked directly with {name} (another of their Jennys) from "
            f"{_clock(since)} to {_clock(until)}. That conversation is on {name}'s side, "
            f"not here: to know what was said, ask {name}.]"
        )
        await self._deliver_card(channel, chat_id, {"line_stub": card}, text)

    async def _deliver_card(
        self, channel: str, chat_id: str, agent_ui: dict[str, Any], text: str,
        *, taint: str | None = None,
    ) -> None:
        from jenny.bus.events import OUTBOUND_META_AGENT_UI, OutboundMessage
        from jenny.runtime.delivery import RECORD_TAINT_META
        from jenny.session.turn_visibility import mark_silent_turn

        if self._deliver is None:
            return
        metadata: dict[str, Any] = {
            OUTBOUND_META_AGENT_UI: agent_ui,
            "_record_channel_delivery": True,
        }
        if taint:
            metadata[RECORD_TAINT_META] = [taint]
        mark_silent_turn(metadata)
        try:
            await self._deliver(OutboundMessage(
                channel=channel, chat_id=chat_id, content=text, metadata=metadata,
            ))
        except Exception:
            logger.opt(exception=True).warning("peers: a line card could not be delivered")

    # -- Quel che torna dall'altra ------------------------------------------

    async def _out_row(self, peer_id: str, lid: Any) -> dict[str, Any] | None:
        if not is_id(lid):
            raise ValueError("bad line id")
        row = await self._db(self.store.line_by_lid, "out", lid)
        if row is None or row["peer"] != peer_id:
            return None
        return row

    async def _on_state(self, peer_id: str, body: dict[str, Any]) -> None:
        row = await self._out_row(peer_id, body.get("lid"))
        if row is None:
            return
        channel, chat_id = _split_key(row["key"])
        state = body.get("state")
        if state != "open":
            reason = text_field(body, "reason", limit=40) or "closed"
            await self.close(channel, chat_id, reason=reason, tell_peer=False)
            return
        place = text_field(body, "place", limit=64)
        moved = place != row["place"]
        await self._db(self.store.line_update, "out", row["key"], state="open", place=place)
        fresh = await self._db(self.store.line, "out", row["key"])
        if fresh is None:
            return
        if moved:
            item = {"id": new_id(), "role": "place", "text": place, "at": now_ms()}
            self._remember(self._out_items, row["key"], item)
            await self._emit(channel, chat_id, {"kind": "item", "item": item})
        refused = text_field(body, "reason", limit=40)
        public = await self._public(fresh)
        frame: dict[str, Any] = {"kind": "state", "line": public}
        if refused:
            # Lo spostamento non è riuscito: la linea resta dov'era, e si dice.
            frame["note"] = refused
        await self._emit(channel, chat_id, frame)

    async def _on_text(self, peer_id: str, body: dict[str, Any]) -> None:
        row = await self._out_row(peer_id, body.get("lid"))
        if row is None:
            return
        text = text_field(body, "text", limit=MAX_TEXT_CHARS)
        if not text:
            return
        channel, chat_id = _split_key(row["key"])
        item = {"id": new_id(), "role": "peer", "text": text, "at": now_ms()}
        self._remember(self._out_items, row["key"], item)
        await self._db(self.store.line_update, "out", row["key"], last_ms=now_ms())
        peer = await self._link._peer(peer_id)
        await self._emit(channel, chat_id, {
            "kind": "item", "item": item, "name": peer.name if peer is not None else "",
        })

    async def _on_chats(self, peer_id: str, body: dict[str, Any]) -> None:
        future = self._chats_waiting.get(body.get("rid"))  # type: ignore[arg-type]
        if future is not None and not future.done():
            future.set_result(body)

    async def _on_trace(self, peer_id: str, body: dict[str, Any]) -> None:
        """Le due righe dell'altra Jenny su una linea chiusa: sotto il segnaposto."""
        lid = body.get("lid")
        if not is_id(lid):
            raise ValueError("bad line id")
        text = " ".join(text_field(body, "text", limit=MAX_TRACE_CHARS * 2).split())
        if not text:
            return
        text = text[:MAX_TRACE_CHARS]
        peer = await self._link._peer(peer_id)
        name = peer.name if peer is not None else peer_id[:8]
        from jenny.bus.events import WEBSOCKET_CHANNEL, WEBUI_DEFAULT_CHAT_ID

        # Il testo l'ha scritto l'altra Jenny: nella storia di questa è un dato
        # non fidato, come la risposta di ``peer_ask``.
        await self._deliver_card(
            WEBSOCKET_CHANNEL, WEBUI_DEFAULT_CHAT_ID,
            {"line_trace": {"peer": name, "peerId": peer_id, "lid": lid, "text": text}},
            f"[{name}'s own summary of that conversation: «{text}»]",
            taint=f"peer:{name}",
        )
        await self._db(self.store.log, peer_id, "in", KIND_LINE_TRACE, {})

    async def _out_of_peer(self, peer_id: str) -> dict[str, Any] | None:
        for row in await self._db(self.store.lines, "out"):
            if row["peer"] == peer_id:
                return row
        return None

    async def show_ask(self, peer_id: str, cid: str, ask: dict[str, Any]) -> None:
        """Una domanda dell'altra Jenny («sto per usare Gmail, ok?») nel filo della linea.

        La stessa che arriva come notifica (``jenny.peers.phone_consent``): qui
        ha i suoi tasti dove l'utente sta scrivendo. La scrive il codice, non
        il modello dell'altra.
        """
        row = await self._out_of_peer(peer_id)
        if row is None:
            return
        channel, chat_id = _split_key(row["key"])
        await self._emit(channel, chat_id, {"kind": "ask", "ask": {"cid": cid, **ask}})

    async def hide_ask(self, peer_id: str, cid: str) -> None:
        row = await self._out_of_peer(peer_id)
        if row is None:
            return
        channel, chat_id = _split_key(row["key"])
        await self._emit(channel, chat_id, {"kind": "ask_closed", "cid": cid})

    async def _on_nack(self, peer_id: str, kind: str, reason: str) -> None:
        """L'altra non conosce i messaggi della linea (una versione prima)."""
        if kind == KIND_LINE_OPEN and reason == "unsupported":
            await self.close_for_peer(peer_id, reason="unsupported")

    # ======================================================================
    # Chi riceve la linea
    # ======================================================================

    async def _reply_state(
        self, peer_id: str, lid: str, state: str, *, place: str = "", reason: str = "",
    ) -> None:
        body: dict[str, Any] = {"lid": lid, "state": state, "place": place}
        if reason:
            body["reason"] = reason
        await self._link.send(peer_id, KIND_LINE_STATE, body)

    def _resolve_place(self, place: str) -> str | None:
        """Il nome vero del quaderno *place* su questo telefono, o ``None`` se non c'è.

        Senza badare alle maiuscole: a parole si dice «il quaderno fornitori»
        anche quando si chiama «Fornitori» (visto sui due emulatori il 23/11).
        ``""`` è la chat di sempre, e c'è sempre.
        """
        from jenny.session.keys import is_valid_project_name

        if not place:
            return ""
        if self._places is None:
            return None
        try:
            names = list(self._places())
        except Exception:
            logger.opt(exception=True).warning("peers: could not list the notebooks")
            return None
        if place in names:
            return place
        folded = place.casefold()
        found = [name for name in names if name.casefold() == folded]
        if len(found) == 1 and is_valid_project_name(found[0]):
            return found[0]
        return None

    async def _in_row(self, peer_id: str, lid: Any) -> dict[str, Any] | None:
        """La linea aperta da *peer_id*, se è *lid* e non è ferma da troppo."""
        if not is_id(lid):
            raise ValueError("bad line id")
        row = await self._db(self.store.line, "in", peer_id)
        if row is None or row["lid"] != lid:
            return None
        if now_ms() - int(row["last_ms"]) > IDLE_CLOSE_MS:
            await self._close_in(peer_id, row, trace=False)
            # Il motivo vero prima del «no_line» del chiamante, che all'altra
            # arriva a linea già chiusa e non dice più niente: senza, l'utente
            # leggeva «aveva già chiuso la linea».
            await self._reply_state(peer_id, lid, "closed", reason="idle")
            return None
        return row

    async def _on_open(self, peer_id: str, body: dict[str, Any]) -> None:
        lid = body.get("lid")
        if not is_id(lid):
            raise ValueError("bad line id")
        place = text_field(body, "place", limit=64)
        cfg = await self._link._config()
        peer = next((p for p in cfg.peers if p.id == peer_id), None)
        if peer is None or not cfg.enabled or not peer.line or self._inject is None:
            await self._db(self.store.log, peer_id, "sys", "refused", {"kind": KIND_LINE_OPEN})
            await self._reply_state(peer_id, lid, "refused", reason="not_allowed")
            return
        before = await self._db(self.store.line, "in", peer_id)
        if before is not None and before["lid"] == lid:
            # Un secondo arrivo dello stesso ``line_open``: la linea c'è già.
            await self._reply_state(peer_id, lid, "open", place=before["place"])
            return
        reason = ""
        found = await asyncio.to_thread(self._resolve_place, place)
        if found is None:
            place, reason = "", "no_place"
        else:
            place = found
        await self._db(self.store.line_put, "in", peer_id, peer=peer_id, lid=lid, place=place)
        self._in_items[peer_id] = []
        await self._db(self.store.log, peer_id, "in", KIND_LINE_OPEN, {"place": place})
        await self._reply_state(peer_id, lid, "open", place=place, reason=reason)
        await self._deliver_marker(peer.name, "open", place)

    async def _deliver_marker(self, name: str, event: str, place: str = "") -> None:
        """«Personale ha la linea», nella chat di questo telefono dove sta la linea.

        Quella di *place*: sui due emulatori (23/11) il segno di chiusura di una
        linea rimasta nel quaderno piante compariva nella chat di sempre, dove
        di quella conversazione non c'era niente.
        """
        from jenny.bus.events import WEBSOCKET_CHANNEL

        at = now_ms()
        if event == "open":
            text = (
                f"[From {_clock(at)} the user is writing here from their other phone, through "
                f"{name}: those messages are the user's own.]"
            )
        else:
            text = f"[{_clock(at)}: the user stopped writing here through {name}.]"
        await self._deliver_card(
            WEBSOCKET_CHANNEL, chat_id_of(place),
            {"line_in": {"peer": name, "event": event, "at": at}}, text,
        )

    async def _on_say(self, peer_id: str, body: dict[str, Any]) -> None:
        row = await self._in_row(peer_id, body.get("lid"))
        lid = body["lid"]
        if row is None:
            await self._reply_state(peer_id, lid, "closed", reason="no_line")
            return
        text = text_field(body, "text", limit=MAX_SAY_CHARS)
        if not text or self._inject is None:
            return
        peer = await self._link._peer(peer_id)
        if peer is None or not peer.line:
            await self._close_in(peer_id, row, trace=False)
            await self._reply_state(peer_id, lid, "closed", reason="not_allowed")
            return
        place = row["place"]
        if place and await asyncio.to_thread(self._resolve_place, place) is None:
            # Il quaderno non c'è più: si torna alla chat di sempre, e si dice.
            place = ""
            await self._db(self.store.line_update, "in", peer_id, place="")
            await self._reply_state(peer_id, lid, "open", place="", reason="no_place")
        await self._db(self.store.line_update, "in", peer_id, last_ms=now_ms())
        self._remember(self._in_items, peer_id, {"role": "user", "text": text})
        await self._inject(
            chat_id_of(place),
            text,
            {"origin": LINE_ORIGIN, "device": peer.name},
            {LINE_PEER_META: peer_id, LINE_ID_META: lid},
        )

    async def _on_move(self, peer_id: str, body: dict[str, Any]) -> None:
        row = await self._in_row(peer_id, body.get("lid"))
        lid = body["lid"]
        if row is None:
            await self._reply_state(peer_id, lid, "closed", reason="no_line")
            return
        found = await asyncio.to_thread(self._resolve_place, text_field(body, "place", limit=64))
        if found is None:
            await self._reply_state(peer_id, lid, "open", place=row["place"], reason="no_place")
            return
        place = found
        await self._db(self.store.line_update, "in", peer_id, place=place, last_ms=now_ms())
        await self._reply_state(peer_id, lid, "open", place=place)

    async def move_here(self, peer_id: str, place: str) -> str | None:
        """Lo spostamento chiesto a parole, dal turno della linea: ``None`` se è riuscito.

        Lo chiama lo strumento ``line_move``. Il messaggio dopo dell'utente
        entra nella chat nuova; questo turno finisce dov'è cominciato.
        """
        row = await self._db(self.store.line, "in", peer_id)
        if row is None:
            return "no_line"
        found = await asyncio.to_thread(self._resolve_place, place)
        if found is None:
            return "no_place"
        await self._db(self.store.line_update, "in", peer_id, place=found, last_ms=now_ms())
        await self._reply_state(peer_id, row["lid"], "open", place=found)
        return None

    def place_names(self) -> list[str]:
        if self._places is None:
            return []
        try:
            return list(self._places())[:MAX_PLACES]
        except Exception:
            logger.opt(exception=True).warning("peers: could not list the notebooks")
            return []

    async def _on_list(self, peer_id: str, body: dict[str, Any]) -> None:
        rid = body.get("rid")
        if not is_id(rid):
            raise ValueError("bad request id")
        peer = await self._link._peer(peer_id)
        if peer is None or not peer.line:
            await self._link.send(peer_id, KIND_LINE_CHATS, {"rid": rid, "ok": False})
            return
        names = await asyncio.to_thread(self.place_names)
        await self._link.send(peer_id, KIND_LINE_CHATS, {"rid": rid, "ok": True, "chats": names})

    async def _on_close(self, peer_id: str, body: dict[str, Any]) -> None:
        lid = body.get("lid")
        if not is_id(lid):
            raise ValueError("bad line id")
        row = await self._db(self.store.line, "in", peer_id)
        if row is None or row["lid"] != lid:
            return
        await self._close_in(peer_id, row, trace=True)

    async def _close_in(self, peer_id: str, row: dict[str, Any], *, trace: bool) -> None:
        await self._db(self.store.line_drop, "in", peer_id)
        items = self._in_items.pop(peer_id, [])
        await self._db(self.store.log, peer_id, "in", KIND_LINE_CLOSE, {})
        peer = await self._link._peer(peer_id)
        if peer is None:
            return
        await self._deliver_marker(peer.name, "close", str(row.get("place") or ""))
        if trace and peer.line_trace and any(i.get("role") == "user" for i in items):
            self._spawn(self._trace(peer, row["lid"], items))

    async def permission_revoked(self, peer_id: str) -> None:
        """Il permesso tolto dall'utente di qui: la linea si chiude adesso."""
        row = await self._db(self.store.line, "in", peer_id)
        if row is None:
            return
        await self._close_in(peer_id, row, trace=False)
        try:
            await self._reply_state(peer_id, row["lid"], "closed", reason="not_allowed")
        except PeerError:
            pass

    def incoming(self) -> list[dict[str, Any]]:
        """Le linee che altre Jenny tengono aperte qui (per l'Officina)."""
        return self.store.lines("in")

    async def on_outbound(self, msg: Any) -> None:
        """Il finale di un turno arrivato da una linea torna a chi l'ha passata.

        Lo chiama il dispatcher dopo la consegna alla WebUI di qui. Passano solo
        i messaggi con un testo: i pezzi di uno streaming, il ragionamento, gli
        avvisi di avanzamento e i marcatori di fine turno restano qui.
        """
        metadata = getattr(msg, "metadata", None) or {}
        peer_id = line_of(metadata)
        if peer_id is None:
            return
        if any(metadata.get(flag) for flag in _NOT_A_FINAL):
            return
        text = (getattr(msg, "content", "") or "").strip()
        if not text:
            return
        row = await self._db(self.store.line, "in", peer_id)
        lid = metadata.get(LINE_ID_META)
        if row is None or row["lid"] != lid:
            return
        self._remember(self._in_items, peer_id, {"role": "peer", "text": text})
        try:
            await self._link.send(
                peer_id, KIND_LINE_TEXT,
                {"lid": lid, "text": plain_links(text)[:MAX_TEXT_CHARS]},
            )
            self._link.wake(peer_id)
        except PeerError:
            logger.info("peers: a line reply had nowhere to go")

    async def _trace(self, peer: Any, lid: str, items: list[dict[str, Any]]) -> None:
        """Due righe su cosa si è detto e fatto, scritte da questa Jenny e mandate all'altra."""
        try:
            text = await asyncio.wait_for(self._trace_text(peer, items), timeout=TRACE_TIMEOUT_S)
        except Exception:
            logger.opt(exception=True).info("peers: no summary for the line with {}", peer.name)
            return
        text = " ".join(text.split())[:MAX_TRACE_CHARS]
        if not text:
            return
        try:
            await self._link.send(peer.id, KIND_LINE_TRACE, {"lid": lid, "text": text})
        except PeerError:
            pass

    async def _trace_text(self, peer: Any, items: list[dict[str, Any]]) -> str:
        from jenny.agent.tools.registry import ToolRegistryView
        from jenny.bus.events import WEBSOCKET_CHANNEL
        from jenny.security.workspace_access import WORKSPACE_READONLY_METADATA_KEY
        from jenny.session.keys import PEER_SESSION_PREFIX
        from jenny.session.turn_visibility import TurnVisibility, silent_progress
        from jenny.utils.prompt_templates import render_template

        agent = self._get_agent()
        if agent is None:
            return ""
        lines = []
        for item in items[-60:]:
            who = "USER" if item.get("role") == "user" else "YOU"
            body = " ".join(str(item.get("text") or "").split())[:600]
            lines.append(f"{who}: {_fence(body)}")
        prompt = render_template(
            "agent/line_trace.md", peer_name=peer.name, conversation="\n".join(lines),
            max_chars=MAX_TRACE_CHARS,
        )
        outcome = await agent.process_direct_outcome(
            prompt,
            session_key=f"{PEER_SESSION_PREFIX}{peer.id}",
            channel=WEBSOCKET_CHANNEL,
            chat_id="default",
            on_progress=silent_progress,
            # Nessuno strumento: è un riassunto di quel che ha già davanti.
            tools=ToolRegistryView(agent.tools, lambda _name, _tool: True),
            visibility=TurnVisibility.SILENT,
            metadata={WORKSPACE_READONLY_METADATA_KEY: True},
        )
        return (outcome.final_text or "").strip()


# I marcatori che il dispatcher vede passare e che non sono la risposta.
_NOT_A_FINAL = (
    "_progress", "_stream_delta", "_stream_end", "_reasoning_delta", "_reasoning_end",
    "_retry_wait", "_turn_end", "_file_edit_events", "_goal_status",
    "_runtime_model_updated", "_app_data_changed", "_board_changed", "_apps_list_changed",
    "_staged_changed", "_plan_changed", "_user_echo", "_subagent_status",
    "_subagent_activity", "_work_status", "_mirror", "_tool_hint",
)


def _fence(text: str) -> str:
    """Il filo non può chiudere da sé il recinto in cui il prompt lo mette."""
    return text.replace("END CONVERSATION", "END-CONVERSATION").replace(
        "BEGIN CONVERSATION", "BEGIN-CONVERSATION")


__all__ = [
    "LINE_CHANNELS",
    "LINE_ID_META",
    "line_mark",
    "line_runtime_lines",
    "line_metadata",
    "LINE_ORIGIN",
    "LINE_PEER_META",
    "RETURN_COMMANDS",
    "PeerLines",
    "chat_id_of",
    "line_of",
    "out_key",
]
