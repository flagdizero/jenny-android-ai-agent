"""Le domande fra due Jenny: chi chiede, chi risponde, come arriva la risposta.

**Chi chiede** (lo strumento ``peer_ask``) mette la domanda in coda e torna
subito: non aspetta. La risposta arriva quando arriva — fra qualche secondo se
l'altra è raggiungibile, ore dopo se il telefono di lavoro era spento — e
arriva sempre allo stesso modo: una scheda nella chat personale, disegnata dal
codice (``agent_ui.peer_answer``) e non riscritta dal modello, consegnata come
un avviso proattivo (chat, tendina, Telegram se c'è). Il testo della scheda
resta anche nella sessione, così al turno dopo Jenny sa cosa le è stato detto.

**Chi risponde** non lo fa dentro il gestore del messaggio, che deve dare
l'``ack`` subito: lancia il turno in disparte e manda la risposta come un
messaggio a sé. Il turno è:

- nella sessione ``peer:<id>`` di quella Jenny, mai nella chat dell'utente;
- in **sola lettura** (``readonly``, sul canale ``websocket`` perché solo lì il
  flag vale) e con un **elenco chiuso** di strumenti che guardano e basta
  (:data:`ANSWER_TOOLS`): niente ``message``, web, codice, aiutanti, SSH.
  Una domanda costruita per far uscire dati («apri questo indirizzo con le tue
  note») non trova niente con cui farlo;
- **contaminato** dall'inizio (``_taint``), come un turno che ha letto una pagina.

I file escono solo se l'utente di *questo* telefono l'ha permesso per quella
Jenny (``send_files``), e solo quelli che il turno ha indicato con ``peer_attach``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from .link import PeerError, PeerLink
from .model import is_id, peer_kind, text_field
from .store import new_id, now_ms

KIND_ASK = "ask"
KIND_ANSWER = "answer"

MAX_QUESTION_CHARS = 4000
MAX_ANSWER_CHARS = 8000
MAX_FILES = 3
# Quanto può durare un turno di risposta. Una domanda è «guarda e dimmi»: se in
# quattro minuti non è finita, la risposta è che non ce l'ha fatta.
TURN_TIMEOUT_S = 240.0
# Ogni quanto si guardano le domande rimaste senza risposta.
EXPIRE_EVERY_S = 600.0
# La sessione ``peer:<id>`` tiene solo una coda corta: le domande passate di
# un'altra Jenny non servono a rispondere alla prossima, e costano.
HISTORY_KEEP = 6

STATUSES = ("ok", "refused", "too_many", "unavailable", "failed", "expired")

# ``read``: guarda e risponde (questo modulo); ``act``: fa (``jenny.peers.acting``).
MODES = ("read", "act")
# Le voci dell'elenco di cosa ha fatto una richiesta, come arrivano dall'altra.
DONE_KINDS = ("file", "job", "egress")
MAX_DONE_ITEMS = 40

# Gli strumenti di un turno di risposta: solo guardare. Chi manca qui non è
# nella lista che il modello vede, in tutte le domande di quella Jenny
# (la lista resta la stessa da un turno all'altro: la cache del provider tiene).
ANSWER_TOOLS = frozenset({
    "read_file",
    "list_dir",
    "find_files",
    "grep",
    "recall",
    "recall_chat",
    "recall_history",
    "notebook",
    "cron",
    "get_location",
    "peer_attach",
})


@dataclass
class PeerTurn:
    """Il turno con cui questa Jenny sta rispondendo a un'altra."""

    peer_id: str
    peer_name: str
    allow_files: bool
    attachments: list[Path] = field(default_factory=list)


# Vale dentro il turno di risposta e solo lì: ``peer_attach`` lo legge per sapere
# se c'è a chi mandare e se è permesso.
PEER_TURN: ContextVar[PeerTurn | None] = ContextVar("jenny_peer_turn", default=None)

Deliver = Callable[[Any], Awaitable[None]]


def answer_tools(base: Any) -> Any:
    """La vista del registro per un turno di risposta: solo :data:`ANSWER_TOOLS`."""
    from jenny.agent.tools.registry import ToolRegistryView

    return ToolRegistryView(base, lambda name, _tool: name not in ANSWER_TOOLS)


def _done_items(raw: Any) -> list[dict[str, Any]]:
    """L'elenco di cosa ha fatto l'altra, ripulito: solo voci note, solo testo."""
    out: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("kind") not in DONE_KINDS:
            continue
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            continue
        entry: dict[str, Any] = {
            "kind": item["kind"], "label": " ".join(label.split())[:200],
            "undoable": item.get("undoable") is True,
        }
        if isinstance(item.get("allowed"), bool):
            entry["allowed"] = item["allowed"]
        out.append(entry)
        if len(out) >= MAX_DONE_ITEMS:
            break
    return out


def _fence(text: str) -> str:
    """La domanda non può chiudere da sé il recinto in cui il prompt la mette."""
    return text.replace("END QUESTION", "END-QUESTION").replace("BEGIN QUESTION", "BEGIN-QUESTION")


class PeerQuestions:
    """Le domande di questa Jenny alle altre, e le sue risposte."""

    def __init__(
        self,
        link: PeerLink,
        workspace: Path,
        *,
        get_agent: Callable[[], Any],
        deliver: Deliver | None,
    ) -> None:
        self._link = link
        self._workspace = Path(workspace)
        self._get_agent = get_agent
        self._deliver = deliver
        self._available = False
        self._tasks: set[asyncio.Task[Any]] = set()
        self._expire_task: asyncio.Task[None] | None = None
        # Le richieste di fare (``jenny.peers.acting.PeerActs``), se il composition
        # root le lega: senza, una richiesta di fare che arriva è rifiutata.
        self.acts: Any = None
        link.register(KIND_ASK, self._on_ask)
        link.register(KIND_ANSWER, self._on_answer)

    @property
    def store(self) -> Any:
        return self._link.store

    # -- Stato ---------------------------------------------------------------------

    def available(self) -> bool:
        """C'è almeno un'altra Jenny a cui chiedere (letto da :meth:`refresh`)."""
        return self._available

    async def refresh(self) -> None:
        cfg = await self._link._config()
        self._available = bool(cfg.enabled and cfg.peers)
        if self.acts is not None:
            await self.acts.refresh()

    def start(self) -> None:
        if self._expire_task is None or self._expire_task.done():
            self._expire_task = asyncio.create_task(self._expire_loop(), name="peer-questions")

    async def stop(self) -> None:
        tasks = [t for t in (self._expire_task, *self._tasks) if t is not None]
        self._expire_task = None
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.acts is not None:
            await self.acts.stop()

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- Chi chiede -----------------------------------------------------------------

    async def peers(self) -> list[Any]:
        cfg = await self._link._config()
        return list(cfg.peers) if cfg.enabled else []

    async def find_peer(self, ref: str) -> Any:
        """La Jenny che *ref* nomina (nome, senza maiuscole; o l'unica che c'è)."""
        peers = await self.peers()
        if not peers:
            raise PeerError("no_peers")
        ref = " ".join((ref or "").split()).casefold()
        if not ref:
            if len(peers) == 1:
                return peers[0]
            raise PeerError("which_peer")
        exact = [p for p in peers if p.name.casefold() == ref]
        if len(exact) == 1:
            return exact[0]
        partial = [p for p in peers if ref in p.name.casefold()]
        if len(partial) == 1:
            return partial[0]
        raise PeerError("which_peer" if partial else "not_found")

    async def ask(self, peer_ref: str, text: str, mode: str = "read") -> dict[str, Any]:
        """Mette in coda *text* per la Jenny *peer_ref*; non aspetta la risposta.

        *mode* ``act`` è una richiesta di fare: l'altra la fa se il suo utente
        l'ha permesso, e risponde con l'elenco di cosa ha toccato.
        """
        text = (text or "").strip()[:MAX_QUESTION_CHARS]
        if not text:
            raise PeerError("bad_question")
        mode = mode if mode in MODES else "read"
        peer = await self.find_peer(peer_ref)
        qid = new_id()
        # Prima la riga, poi la coda: una risposta velocissima trova già la domanda.
        await asyncio.to_thread(
            self.store.add_question, peer.id, "out", qid, text, state="queued", mode=mode
        )
        outbox_id = await self._link.send(
            peer.id, KIND_ASK, {"qid": qid, "text": text, "mode": mode}
        )
        await asyncio.to_thread(self.store.set_outbox, peer.id, "out", qid, outbox_id)
        await asyncio.to_thread(
            self.store.log, peer.id, "out", KIND_ASK,
            {"qid": qid, "text": text[:300], "mode": mode},
        )
        state = await asyncio.to_thread(self.store.state, peer.id)
        return {
            "qid": qid, "peer": peer.name, "kind": peer_kind(peer.kind),
            "last_seen_ms": state["last_seen_ms"],
        }

    async def _on_answer(self, peer_id: str, body: dict[str, Any]) -> None:
        qid = body.get("qid")
        status = body.get("status")
        if not is_id(qid) or status not in STATUSES:
            raise ValueError("bad answer")
        question = await asyncio.to_thread(self.store.question, peer_id, "out", qid)
        if question is None:
            logger.info("peers: answer to a question we don't know ({}), dropped", qid[:8])
            return
        act = question.get("mode") == "act"
        open_states = ("queued", "expired", "working") if act else ("queued", "expired")
        if question["state"] not in open_states:
            return  # già arrivata: un doppione
        working = act and status == "ok" and body.get("working") is True
        done = _done_items(body.get("done")) if act else []
        text = text_field(body, "text", limit=MAX_ANSWER_CHARS)
        media: list[Path] = []
        missing: list[str] = []
        for item in body.get("files") or []:
            if not isinstance(item, dict) or not is_id(item.get("fid")):
                continue
            got = self._link.received_file(peer_id, item["fid"])
            if got is None:
                missing.append(str(item.get("name") or "file")[:120])
            else:
                media.append(self._workspace / got["path"])
        state = ("working" if working else "answered") if status == "ok" else status
        await asyncio.to_thread(
            self.store.set_question, peer_id, "out", qid, state=state, answer=text,
        )
        if act:
            await asyncio.to_thread(self.store.set_done, peer_id, "out", qid, done)
        await asyncio.to_thread(
            self.store.log, peer_id, "in", KIND_ANSWER,
            {"qid": qid, "status": status, "files": len(media), "mode": question.get("mode"),
             "working": working},
        )
        peer = await self._link._peer(peer_id)
        await self._deliver_card(
            name=peer.name if peer is not None else peer_id[:8],
            question=question["text"],
            status=status,
            text=text,
            media=media,
            missing=missing,
            mode="act" if act else "read",
            qid=qid,
            peer_id=peer_id,
            done=done,
            working=working,
        )

    async def _deliver_card(
        self, *, name: str, question: str, status: str, text: str,
        media: list[Path], missing: list[str], mode: str = "read", qid: str = "",
        peer_id: str = "", done: list[dict[str, Any]] | None = None, working: bool = False,
        undo: dict[str, int] | None = None,
    ) -> None:
        """La risposta nella chat personale: una scheda, la stessa dal vivo e riletta."""
        from jenny.bus.events import (
            OUTBOUND_META_AGENT_UI,
            WEBSOCKET_CHANNEL,
            WEBUI_DEFAULT_CHAT_ID,
            OutboundMessage,
        )
        from jenny.runtime.delivery import RECORD_TAINT_META
        from jenny.session.turn_visibility import mark_silent_turn

        if self._deliver is None:
            logger.warning("peers: an answer from {} arrived with nowhere to show it", name)
            return
        card: dict[str, Any] = {
            "peer": name,
            "status": status,
            "question": question[:300],
            "text": text,
            "missing": missing,
            "at": now_ms(),
        }
        if mode == "act":
            # Una richiesta di fare: l'elenco di cosa ha toccato (dal codice
            # dell'altra), se ci sta ancora lavorando, e chi e quale per il
            # tasto «Chiedi di annullare».
            card.update({
                "mode": "act", "qid": qid, "peerId": peer_id,
                "done": list(done or []), "working": working,
            })
            if undo is not None:
                # L'esito di un «Chiedi di annullare»: quanti file e promemoria.
                card["undo"] = {k: int(v) for k, v in undo.items()}
        # Il testo per chi non disegna la scheda (Telegram, la tendina, la
        # cronologia del modello): chi parla e cosa, senza parole di una lingua.
        if status == "ok":
            fallback = f"{name}: «{text}»" if text else f"{name}: ✓"
        else:
            fallback = f"{name}: [{status}] «{question[:120]}»"
        metadata: dict[str, Any] = {
            OUTBOUND_META_AGENT_UI: {"peer_answer": card},
            "_record_channel_delivery": True,
            "_proactive_fanout": True,
        }
        if text:
            # Il testo l'ha scritto l'altra Jenny, che può essere stata guidata
            # da quel che ha letto: nella cronologia di questa la riga è non
            # fidata come un ``web_fetch``, e finché resta in vista i lavori
            # che Jenny avvia nascono contaminati.
            metadata[RECORD_TAINT_META] = [f"peer:{name}"]
        mark_silent_turn(metadata)
        await self._deliver(OutboundMessage(
            channel=WEBSOCKET_CHANNEL,
            chat_id=WEBUI_DEFAULT_CHAT_ID,
            content=fallback,
            media=[str(p) for p in media],
            metadata=metadata,
        ))

    async def _expire_loop(self) -> None:
        # Al primo giro, lo stato che il cancello delle richieste legge.
        try:
            await self.refresh()
        except Exception:
            logger.opt(exception=True).warning("peers: first refresh failed")
        while True:
            try:
                await self.expire_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.opt(exception=True).warning("peers: expiring questions failed")
            await asyncio.sleep(EXPIRE_EVERY_S)

    async def expire_due(self, *, now: int | None = None) -> int:
        """Le domande rimaste senza risposta oltre ``askTtlHours``: lo si dice all'utente."""
        cfg = await self._link._config()
        cutoff = (now_ms() if now is None else now) - cfg.ask_ttl_hours * 3600 * 1000
        expired = 0
        for q in await asyncio.to_thread(self.store.open_questions_before, cutoff):
            if q["outbox_id"]:
                await asyncio.to_thread(self.store.remove, q["outbox_id"])
            await asyncio.to_thread(
                self.store.set_question, q["peer"], "out", q["qid"], state="expired", answer=""
            )
            peer = await self._link._peer(q["peer"])
            await self._deliver_card(
                name=peer.name if peer is not None else q["peer"][:8],
                question=q["text"], status="expired", text="", media=[], missing=[],
            )
            expired += 1
        return expired

    # -- Chi risponde -------------------------------------------------------------------

    async def _on_ask(self, peer_id: str, body: dict[str, Any]) -> None:
        qid = body.get("qid")
        text = text_field(body, "text", limit=MAX_QUESTION_CHARS)
        if not is_id(qid) or not text:
            raise ValueError("bad question")
        mode = "act" if body.get("mode") == "act" else "read"
        peer = await self._link._peer(peer_id)
        if peer is None:
            return
        fresh = await asyncio.to_thread(
            self.store.add_question, peer_id, "in", qid, text, state="answering", mode=mode
        )
        if not fresh:
            return
        await asyncio.to_thread(
            self.store.log, peer_id, "in", KIND_ASK,
            {"qid": qid, "text": text[:300], "mode": mode},
        )
        # In disparte: il messaggio ha già il suo ``ack``, la risposta è un altro.
        self._spawn(self._answer(peer, qid, text, mode))

    async def _answer(self, peer: Any, qid: str, text: str, mode: str = "read") -> None:
        cfg = await self._link._config()
        if mode == "act":
            asked = await asyncio.to_thread(
                self.store.questions_since, peer.id, "in", now_ms() - 3600 * 1000
            )
            if self.acts is not None:
                await self.acts.answer(peer, qid, text, too_many=asked > cfg.max_asks_per_hour)
                return
            await asyncio.to_thread(
                self.store.set_question, peer.id, "in", qid, state="refused", answer=""
            )
            await self._link.send(peer.id, KIND_ANSWER, {
                "qid": qid, "status": "refused", "text": "", "files": [], "mode": "act",
            })
            return
        status, reply, infos = "ok", "", []
        try:
            asked_last_hour = await asyncio.to_thread(
                self.store.questions_since, peer.id, "in", now_ms() - 3600 * 1000
            )
            agent = self._get_agent()
            if not peer.can_ask:
                status = "refused"
            elif asked_last_hour > cfg.max_asks_per_hour:
                status = "too_many"
            elif agent is None:
                status = "unavailable"
            else:
                reply, attachments = await asyncio.wait_for(
                    self._run_turn(agent, peer, text), timeout=TURN_TIMEOUT_S
                )
                for path in attachments[:MAX_FILES] if peer.send_files else []:
                    try:
                        info = await self._link.send_file(peer.id, path)
                    except PeerError as exc:
                        logger.info("peers: {} not sent ({})", path.name, exc.code)
                        continue
                    infos.append({"fid": info["fid"], "name": info["name"], "size": info["size"]})
        except asyncio.TimeoutError:
            status = "failed"
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.opt(exception=True).warning("peers: answering {} failed", peer.name)
            status = "failed"
        reply = reply[:MAX_ANSWER_CHARS]
        await asyncio.to_thread(
            self.store.set_question, peer.id, "in", qid,
            state="answered" if status == "ok" else status, answer=reply,
        )
        await self._link.send(
            peer.id, KIND_ANSWER, {"qid": qid, "status": status, "text": reply, "files": infos}
        )
        await asyncio.to_thread(
            self.store.log, peer.id, "out", KIND_ANSWER,
            {"qid": qid, "status": status, "files": len(infos)},
        )

    async def _run_turn(self, agent: Any, peer: Any, text: str) -> tuple[str, list[Path]]:
        """Il turno di risposta: sola lettura, strumenti chiusi, contaminato."""
        from jenny.bus.events import WEBSOCKET_CHANNEL
        from jenny.security.workspace_access import WORKSPACE_READONLY_METADATA_KEY
        from jenny.session.keys import PEER_SESSION_PREFIX
        from jenny.session.turn_visibility import TurnVisibility, silent_progress
        from jenny.utils.prompt_templates import render_template

        turn = PeerTurn(peer_id=peer.id, peer_name=peer.name, allow_files=bool(peer.send_files))
        prompt = render_template(
            "agent/peer_request.md",
            peer_name=peer.name,
            question=_fence(text),
            act=False,
            confirm=False,
            allow_files=turn.allow_files,
            max_files=MAX_FILES,
        )
        session_key = f"{PEER_SESSION_PREFIX}{peer.id}"
        token = PEER_TURN.set(turn)
        try:
            outcome = await agent.process_direct_outcome(
                prompt,
                session_key=session_key,
                channel=WEBSOCKET_CHANNEL,
                chat_id="default",
                on_progress=silent_progress,
                tools=answer_tools(agent.tools),
                visibility=TurnVisibility.SILENT,
                metadata={
                    WORKSPACE_READONLY_METADATA_KEY: True,
                    "_taint": [f"peer:{peer.name}"],
                },
            )
        finally:
            PEER_TURN.reset(token)
        try:
            session = agent.sessions.get_or_create(session_key)
            session.retain_recent_legal_suffix(HISTORY_KEEP)
            agent.sessions.save(session)
        except Exception:
            logger.opt(exception=True).debug("peers: trimming the peer session failed")
        return (outcome.final_text or "").strip(), list(turn.attachments)


__all__ = [
    "ANSWER_TOOLS",
    "MODES",
    "KIND_ANSWER",
    "KIND_ASK",
    "PEER_TURN",
    "PeerQuestions",
    "PeerTurn",
    "answer_tools",
]
