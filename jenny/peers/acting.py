"""Le richieste di *fare* fra due Jenny: chi le fa, cosa toccano, come si annullano.

Una domanda (``jenny.peers.questions``) guarda e risponde. Una richiesta di
fare — «di' a Lavoro di mettermi un promemoria», «salva il PDF nel quaderno
Clienti» — lavora: su questo telefono gira un turno con tutti gli strumenti, e
in modalità orchestratore (il default) il lavoro vero lo fanno gli aiutanti che
quel turno lancia.

Cosa la tiene in riga, sul telefono che la riceve:

- il **permesso** (``PeerConfig.act``): ``off`` la rifiuta senza turno;
  ``confirm`` e ``free`` la fanno, e il cancello (``jenny.peers.gate``) decide
  cosa può uscire dal telefono, chiedendo all'utente di qui con ``confirm``;
- il **registro dell'annulla**: le scritture del turno vanno nel registro
  ``peer-<qid>``, quelle degli aiutanti nei loro (``helper-<lineage>``), che la
  richiesta si annota; l'«Annulla» li rimette insieme (``undo_set``) e toglie i
  promemoria che la richiesta aveva creato;
- l'**elenco di cosa ha toccato**, scritto dal codice e non dal modello: file,
  promemoria, pagine aperte (queste non si annullano). Torna all'altra Jenny
  con la risposta, e resta nella cronologia di qui.

La risposta parte quando il turno finisce. Se ha lasciato aiutanti al lavoro
parte con ``working``, e un **aggiornamento** segue quando l'ultimo ha finito e
il turno che legge il suo risultato si chiude (:meth:`PeerActs.turn_ended`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from . import gate
from .confirm import Confirmations
from .link import PeerError, PeerLink
from .model import is_id, text_field

KIND_UNDO = "undo"
KIND_UNDONE = "undone"

#: Il tetto di un turno che fa: il lavoro, più qualche conferma da aspettare.
ACT_TURN_TIMEOUT_S = 2400.0
#: Quante voci al massimo nell'elenco di cosa ha toccato.
MAX_DONE_ITEMS = 40
#: La sessione delle richieste tiene una coda corta, come quella delle domande.
HISTORY_KEEP = 6

UNDONE_STATUSES = ("ok", "refused", "still_working", "not_found", "failed")


def act_session_key(peer_id: str) -> str:
    from jenny.session.keys import peer_act_session_key

    return peer_act_session_key(peer_id)


class PeerActs:
    """Le richieste di fare: quelle che arrivano qui, e l'annulla di quelle fatte da qui."""

    def __init__(
        self,
        link: PeerLink,
        install_root: Path,
        *,
        get_agent: Callable[[], Any],
        get_cron: Callable[[], Any] | None = None,
        notify_done: Callable[[str, str, str], None] | None = None,
        clear_done: Callable[[str], None] | None = None,
        confirmations: Confirmations | None = None,
    ) -> None:
        self._link = link
        self._root = Path(install_root)
        self._get_agent = get_agent
        self._get_cron = get_cron or (lambda: None)
        self._notify_done = notify_done
        self._clear_done = clear_done
        self.confirmations = confirmations or Confirmations()
        # Copia sincrona di nomi e livelli: il cancello li legge dai fili del
        # codice, dove non si può aspettare la config.
        self._peers: dict[str, tuple[str, str]] = {}
        # Cosa è uscito, per richiesta, mentre lavora: va nell'elenco alla fine.
        self._notes: dict[str, list[dict[str, Any]]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        # Chi disegna le schede dalla parte di chi chiede (``PeerQuestions``).
        self.deliver_card: Callable[..., Any] | None = None
        link.register(KIND_UNDO, self._on_undo)
        link.register(KIND_UNDONE, self._on_undone)

    @property
    def store(self) -> Any:
        return self._link.store

    # -- Lo stato che il cancello legge ------------------------------------------------

    async def refresh(self) -> None:
        cfg = await self._link._config()
        before = dict(self._peers)
        self._peers = {p.id: (p.name, p.act) for p in cfg.peers} if cfg.enabled else {}
        for peer_id, (_name, level) in before.items():
            now = self._peers.get(peer_id)
            if level != "off" and (now is None or now[1] == "off"):
                self.confirmations.deny_peer(peer_id)

    def level(self, peer_id: str) -> str:
        entry = self._peers.get(peer_id)
        return entry[1] if entry is not None else "off"

    def name(self, peer_id: str) -> str:
        entry = self._peers.get(peer_id)
        return entry[0] if entry is not None else "the other Jenny"

    async def confirm(self, peer_id: str, qid: str, action: str, target: str) -> bool:
        from jenny.runtime.user_text import say

        name = self.name(peer_id)
        key = {"open": "peer.what_open", "download": "peer.what_download"}.get(
            action, "peer.what_browser")
        what = say(key, target=target)
        ok = await self.confirmations.ask(peer=peer_id, peer_name=name, qid=qid, what=what)
        await asyncio.to_thread(
            self.store.log, peer_id, "sys", "confirm",
            {"qid": qid, "action": action, "target": target[:200], "allowed": ok},
        )
        return ok

    def note(self, peer_id: str, qid: str, item: dict[str, Any]) -> None:
        notes = self._notes.setdefault(qid, [])
        if item not in notes and len(notes) < MAX_DONE_ITEMS:
            notes.append(item)

    # -- Gli aiutanti di una richiesta ---------------------------------------------------

    def helper_started(self, act: dict[str, str], journal_key: str, lineage: str) -> None:
        """Un aiutante parte (o riparte) per la richiesta *act*: il suo registro è suo."""
        try:
            self.store.add_journal(act["peer"], "in", act["qid"], journal_key)
        except Exception:  # noqa: BLE001 — l'aiutante lavora comunque
            logger.opt(exception=True).warning("peers: helper journal not noted")

    def working(self, peer_id: str, qid: str) -> bool:
        """Se un aiutante della richiesta *qid* è vivo **adesso**.

        Si chiede al gestore degli aiutanti e non a un conto tenuto qui: il
        risultato di un aiutante che finisce mentre il turno della richiesta è
        ancora aperto entra in quel turno, e nessun turno a parte lo annuncia.
        """
        agent = self._get_agent()
        subagents = getattr(agent, "subagents", None)
        if subagents is None:
            return False
        try:
            tids = subagents.get_running_ids_by_session(act_session_key(peer_id))
            for tid in tids:
                spec = subagents._spec_for_task(tid)
                if spec is not None and (spec.peer_act or {}).get("qid") == qid:
                    return True
        except Exception:  # noqa: BLE001 — nel dubbio, finito
            logger.opt(exception=True).debug("peers: running helpers not readable")
        return False

    # -- Chi riceve: il turno ------------------------------------------------------------

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def answer(self, peer: Any, qid: str, text: str, *, too_many: bool) -> None:
        """La richiesta *qid* di *peer*: rifiutata, o fatta, e la risposta in coda."""
        await self.refresh()
        level = self.level(peer.id)
        agent = self._get_agent()
        status, reply = "ok", ""
        if level == "off":
            status = "refused"
        elif too_many:
            status = "too_many"
        elif agent is None:
            status = "unavailable"
        else:
            try:
                reply = await asyncio.wait_for(
                    self._run_turn(agent, peer, qid, text), timeout=ACT_TURN_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                status = "failed"
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.opt(exception=True).warning("peers: acting for {} failed", peer.name)
                status = "failed"
        await self._send_answer(peer.id, qid, status=status, text=reply, update=False)

    async def _run_turn(self, agent: Any, peer: Any, qid: str, text: str) -> str:
        from jenny.bus.events import WEBSOCKET_CHANNEL
        from jenny.session.turn_visibility import TurnVisibility, silent_progress
        from jenny.utils.prompt_templates import render_template

        from .questions import _fence

        prompt = render_template(
            "agent/peer_request.md",
            peer_name=peer.name,
            question=_fence(text),
            act=True,
            confirm=self.level(peer.id) == "confirm",
            allow_files=False,
            max_files=0,
        )
        session_key = act_session_key(peer.id)
        outcome = await agent.process_direct_outcome(
            prompt,
            session_key=session_key,
            channel=WEBSOCKET_CHANNEL,
            chat_id="default",
            on_progress=silent_progress,
            visibility=TurnVisibility.SILENT,
            metadata={
                "_taint": [f"peer:{peer.name}"],
                gate.PEER_ACT_META: {"peer": peer.id, "qid": qid},
            },
        )
        self._trim(agent, session_key)
        return (outcome.final_text or "").strip()

    @staticmethod
    def _trim(agent: Any, session_key: str) -> None:
        try:
            session = agent.sessions.get_or_create(session_key)
            session.retain_recent_legal_suffix(HISTORY_KEEP)
            agent.sessions.save(session)
        except Exception:
            logger.opt(exception=True).debug("peers: trimming the act session failed")

    async def turn_ended(self, metadata: Any, final_text: str) -> None:
        """Un turno della richiesta è finito: se era il rientro dell'ultimo aiutante, l'aggiornamento.

        Lo chiama il loop alla fine di ogni turno che porta la richiesta nei
        metadati. Conta solo il turno che legge il risultato di un aiutante: il
        primo turno risponde da sé (:meth:`answer`), e un promemoria nato dalla
        richiesta che scatta non è una risposta.
        """
        act = gate.act_of(metadata)
        if act is None or not isinstance(metadata, dict):
            return
        if metadata.get("injected_event") != "subagent_result":
            return
        if self.working(act["peer"], act["qid"]):
            return
        row = await asyncio.to_thread(self.store.question, act["peer"], "in", act["qid"])
        if row is None or row["state"] in ("undone",):
            return
        await self._send_answer(
            act["peer"], act["qid"], status="ok", text=(final_text or "").strip(), update=True
        )

    async def _send_answer(
        self, peer_id: str, qid: str, *, status: str, text: str, update: bool
    ) -> None:
        from .questions import KIND_ANSWER, MAX_ANSWER_CHARS

        text = text[:MAX_ANSWER_CHARS]
        done = await self.done_list(peer_id, qid)
        working = status == "ok" and self.working(peer_id, qid)
        state = ("working" if working else "answered") if status == "ok" else status
        await asyncio.to_thread(self.store.set_done, peer_id, "in", qid, done)
        await asyncio.to_thread(
            self.store.set_question, peer_id, "in", qid, state=state, answer=text
        )
        await self._link.send(peer_id, KIND_ANSWER, {
            "qid": qid, "status": status, "text": text, "files": [],
            "mode": "act", "done": done, "working": working, "update": update,
        })
        await asyncio.to_thread(
            self.store.log, peer_id, "out", KIND_ANSWER,
            {"qid": qid, "status": status, "mode": "act", "done": len(done), "working": working},
        )
        if status == "ok":
            row = await asyncio.to_thread(self.store.question, peer_id, "in", qid)
            self._post_done(peer_id, qid, row["text"] if row else "", working=working)
        if not working:
            self._notes.pop(qid, None)

    def _post_done(self, peer_id: str, qid: str, text: str, *, working: bool) -> None:
        if self._notify_done is None:
            return
        from jenny.runtime.user_text import say

        name = self.name(peer_id)
        short = " ".join(text.split())[:120]
        body = say("peer.working_body" if working else "peer.done_body", text=short)
        try:
            self._notify_done(qid, say("peer.done_title", peer=name), body)
        except Exception:  # noqa: BLE001 — la notifica è una cortesia
            logger.opt(exception=True).debug("peers: done notice failed")

    # -- Cosa ha toccato -------------------------------------------------------------------

    def _journal_keys(self, qid: str, row: dict[str, Any] | None) -> list[str]:
        from jenny.agent.write_journal import journal_key

        keys = [journal_key("peer", qid)]
        for key in (row or {}).get("journals") or []:
            if key not in keys:
                keys.append(key)
        return keys

    def _jobs_of(self, qid: str) -> list[Any]:
        cron = self._get_cron()
        if cron is None:
            return []
        try:
            jobs = cron.list_jobs(include_disabled=True)
        except Exception:  # noqa: BLE001
            logger.opt(exception=True).debug("peers: jobs not readable")
            return []
        out = []
        for job in jobs:
            act = gate.act_of(getattr(job.payload, "origin_metadata", None))
            if act is not None and act["qid"] == qid:
                out.append(job)
        return out

    async def done_list(self, peer_id: str, qid: str) -> list[dict[str, Any]]:
        """Cosa ha toccato la richiesta *qid*: dai registri, dai promemoria, dalle uscite."""
        row = await asyncio.to_thread(self.store.question, peer_id, "in", qid)
        return await asyncio.to_thread(self._done_list_sync, qid, row)

    def _done_list_sync(self, qid: str, row: dict[str, Any] | None) -> list[dict[str, Any]]:
        from jenny.agent.write_journal import journal_for
        from jenny.cron.types import job_display_name

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for key in self._journal_keys(qid, row):
            try:
                entries = journal_for(self._root, key).entries()
            except Exception:  # noqa: BLE001
                continue
            for entry in entries:
                path = str(entry.get("path") or "")
                if not path or path.endswith("/") or path in seen:
                    continue
                seen.add(path)
                items.append({"kind": "file", "label": path, "undoable": True})
        for job in self._jobs_of(qid):
            items.append({"kind": "job", "label": job_display_name(job)[:120], "undoable": True})
        for note in self._notes.get(qid, []):
            items.append(dict(note))
        if row is not None:
            for old in row.get("done") or []:
                if old.get("kind") == "egress" and old not in items:
                    items.append(old)
        return items[:MAX_DONE_ITEMS]

    # -- L'annulla ---------------------------------------------------------------------------

    async def undo(self, peer_id: str, qid: str, *, by: str = "user") -> dict[str, Any]:
        """Rimette com'era quello che la richiesta *qid* di *peer_id* ha fatto qui."""
        row = await asyncio.to_thread(self.store.question, peer_id, "in", qid)
        if row is None or row.get("mode") != "act":
            raise PeerError("nothing_to_undo")
        if self.working(peer_id, qid):
            raise PeerError("still_working")
        report = await asyncio.to_thread(self._undo_sync, qid, row)
        removed = 0
        cron = self._get_cron()
        for job in self._jobs_of(qid):
            try:
                if cron is not None and cron.remove_job(job.id) == "removed":
                    removed += 1
            except Exception:  # noqa: BLE001 — un promemoria che non si toglie resta detto
                logger.opt(exception=True).warning("peers: job {} not removed", job.id)
        result = {
            "restored": len(report.restored) if report is not None else 0,
            "kept": len(report.kept) if report is not None else 0,
            "jobs": removed,
        }
        await asyncio.to_thread(self.store.set_question, peer_id, "in", qid, state="undone")
        await asyncio.to_thread(
            self.store.log, peer_id, "sys", "undo", {"qid": qid, "by": by, **result}
        )
        if self._notify_done is not None:
            from jenny.runtime.user_text import say

            short = " ".join(str(row["text"]).split())[:120]
            try:
                self._notify_done(
                    qid, say("peer.done_title", peer=self.name(peer_id)),
                    say("peer.undone_body", text=short),
                )
            except Exception:  # noqa: BLE001
                logger.opt(exception=True).debug("peers: undone notice failed")
        await self._link.send(peer_id, KIND_UNDONE, {"qid": qid, "status": "ok", **result})
        return result

    def _undo_sync(self, qid: str, row: dict[str, Any]) -> Any:
        from jenny.agent.write_journal import journal_for, journal_key, undo_set

        journals = []
        for key in self._journal_keys(qid, row):
            journal = journal_for(self._root, key)
            if journal.entries():
                journals.append(journal)
        if not journals:
            return None
        into = journal_for(self._root, journal_key("undo-peer", qid))
        return undo_set(journals, by="user", into=into)

    def undo_from_notice(self, qid: str) -> None:
        """Il tasto «Annulla» della notifica: la richiesta *qid* arrivata qui."""
        self._spawn(self._undo_from_notice(qid))

    async def _undo_from_notice(self, qid: str) -> None:
        try:
            row = await asyncio.to_thread(self.store.find_question, "in", qid)
            if row is None:
                logger.info("peers: undo from the notice for an unknown request")
                return
            await self.undo(row["peer"], qid, by="user")
        except PeerError as exc:
            logger.info("peers: undo from the notice not done ({})", exc.code)
        except Exception:
            logger.opt(exception=True).warning("peers: undo from the notice failed")

    async def _on_undo(self, peer_id: str, body: dict[str, Any]) -> None:
        """L'altra Jenny chiede di annullare una sua richiesta: vale se il permesso c'è ancora."""
        qid = body.get("qid")
        if not is_id(qid):
            raise ValueError("bad undo")
        await self.refresh()
        if self.level(peer_id) == "off":
            await self._link.send(peer_id, KIND_UNDONE, {"qid": qid, "status": "refused"})
            return
        self._spawn(self._undo_for_peer(peer_id, qid))

    async def _undo_for_peer(self, peer_id: str, qid: str) -> None:
        try:
            await self.undo(peer_id, qid, by="peer")
        except PeerError as exc:
            status = exc.code if exc.code in UNDONE_STATUSES else "not_found"
            if exc.code == "nothing_to_undo":
                status = "not_found"
            await self._link.send(peer_id, KIND_UNDONE, {"qid": qid, "status": status})
        except Exception:
            logger.opt(exception=True).warning("peers: undo for {} failed", peer_id[:8])
            await self._link.send(peer_id, KIND_UNDONE, {"qid": qid, "status": "failed"})

    # -- Chi chiede: l'annulla richiesto e il suo esito -------------------------------------

    async def request_undo(self, peer_id: str, qid: str) -> None:
        """Chiede all'altra Jenny di annullare la richiesta *qid* fatta da qui."""
        row = await asyncio.to_thread(self.store.question, peer_id, "out", qid)
        if row is None or row.get("mode") != "act":
            raise PeerError("nothing_to_undo")
        await self._link.send(peer_id, KIND_UNDO, {"qid": qid})
        await asyncio.to_thread(self.store.log, peer_id, "out", KIND_UNDO, {"qid": qid})

    async def _on_undone(self, peer_id: str, body: dict[str, Any]) -> None:
        qid = body.get("qid")
        status = body.get("status")
        if not is_id(qid) or status not in UNDONE_STATUSES:
            raise ValueError("bad undone")
        row = await asyncio.to_thread(self.store.question, peer_id, "out", qid)
        if row is None:
            return
        if status == "ok":
            await asyncio.to_thread(self.store.set_question, peer_id, "out", qid, state="undone")
        await asyncio.to_thread(self.store.log, peer_id, "in", KIND_UNDONE,
                                {"qid": qid, "status": status})
        if self.deliver_card is None:
            return
        counts = {k: int(body.get(k) or 0) for k in ("restored", "kept", "jobs")
                  if isinstance(body.get(k), int)}
        peer = await self._link._peer(peer_id)
        await self.deliver_card(
            name=peer.name if peer is not None else peer_id[:8],
            question=row["text"], status="undone" if status == "ok" else f"undo_{status}",
            text="", media=[], missing=[], mode="act", qid=qid, peer_id=peer_id,
            undo=counts,
        )

    # -- Per il pannello ---------------------------------------------------------------------

    async def history(self, peer_id: str) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(self.store.recent_acts, peer_id)
        out = []
        for row in rows:
            out.append({
                "qid": row["qid"], "text": text_field(row, "text", limit=300),
                "state": row["state"], "at_ms": int(row["asked_ms"]),
                "done": row["done"], "working": self.working(peer_id, row["qid"]),
            })
        return out

    def pending(self, peer_id: str | None = None) -> list[dict[str, Any]]:
        return self.confirmations.pending(peer_id)


__all__ = [
    "ACT_TURN_TIMEOUT_S",
    "KIND_UNDO",
    "KIND_UNDONE",
    "PeerActs",
    "act_session_key",
]
