"""Persistenza turno + checkpoint di crash-recovery per ``AgentLoop``.

`TurnPersistenceMixin` raccoglie i metodi che scrivono la storia di sessione e
gestiscono il runtime-checkpoint (materializzazione di un turno interrotto). Sono
mixati in ``AgentLoop`` verbatim: `self` risolve via MRO, comportamento identico,
zero churn ai call-site. Nessuna logica di concorrenza vive qui.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.agent.context import ContextBuilder
from jenny.agent.context_governor import (
    MID_TURN_LABEL,
    closed_reasoning_can_be_blank,
    settle_closed_reasoning,
)
from jenny.session.history_meta import (
    INJECTED_EVENT_META,
    MID_TURN_META,
    SENT_CONTENT_META,
    SUBAGENT_RESULT_EVENT,
    SUBAGENT_TAINT_META,
    is_synthetic_history_row,
)
from jenny.utils.helpers import image_placeholder_text
from jenny.utils.helpers import truncate_text as truncate_text_fn

if TYPE_CHECKING:
    from jenny.bus.events import InboundMessage
    from jenny.session.manager import Session, SessionManager


def sent_user_content(message: dict[str, Any]) -> str:
    """Il testo di una riga ``user`` come il runner lo manda al provider.

    Uguale a ``content``, tranne per un messaggio scritto mentre il turno
    lavorava: l'etichetta la mette il governatore a ogni richiesta
    (``context_governor.label_mid_turn_messages``), e il turno dopo la deve
    ritrovare.
    """
    content = message.get("content")
    text = content if isinstance(content, str) else ""
    if message.get(MID_TURN_META):
        return f"{MID_TURN_LABEL}\n{text}"
    return text


def record_sent_content(row: dict[str, Any], built: list[dict[str, Any]]) -> None:
    """Scrive su *row* il messaggio con cui ``build_messages`` chiude *built*.

    *row* e' la riga di storia che quel messaggio rappresenta, salvata prima
    del turno (la domanda dell'utente, un rientro di subagent): senza, il turno
    dopo la rimanderebbe senza Runtime Context e col ``[Message Time]`` davanti,
    e il provider ripartirebbe da li'. Un messaggio con immagini resta com'e'
    (il base64 non si salva): quel turno rinuncia alla cache, come prima.
    """
    if not built or built[-1].get("role") != "user":
        return
    content = built[-1].get("content")
    if isinstance(content, str):
        row[SENT_CONTENT_META] = content


class FollowupState(Enum):
    """Dove sta in storia il rientro di un subagent arrivato a turno chiuso."""

    #: Mai visto: il turno d'annuncio lo scrive e lo presenta al modello.
    NEW = "new"
    #: Gia' scritto da un turno d'annuncio che non ha risposto (interrotto).
    PENDING = "pending"
    #: Gia' scritto e gia' risposto: un secondo turno ripeterebbe la sintesi.
    DELIVERED = "delivered"
    #: Niente da dire: nessun turno.
    EMPTY = "empty"
    #: Scritto in storia senza turno: un fratello dello stesso passo del piano
    #: lavora ancora, e Jenny si sveglia una volta sola, con l'ultimo resoconto
    #: (``AgentLoop._hold_subagent_result``, ``PlanDriver.hold_helper_result``).
    #: Non lo restituisce :meth:`TurnPersistenceMixin._subagent_followup_state`,
    #: che dice dove sta la riga: e' l'esito che il loop decide dopo.
    HELD = "held"


class TurnPersistenceMixin:
    """Metodi di persistenza/checkpoint del turno (mixin di AgentLoop)."""

    if TYPE_CHECKING:
        # Contratto host↔mixin (solo per il type-checker; nessun effetto a
        # runtime). Attributi/costanti forniti da ``AgentLoop``.
        _PENDING_USER_TURN_KEY: str
        _RUNTIME_CHECKPOINT_KEY: str
        max_tool_result_chars: int
        sessions: SessionManager
        provider: Any
        model: str

    def _sanitize_persisted_blocks(
        self,
        content: list[dict[str, Any]],
        *,
        should_truncate_text: bool = False,
        drop_runtime: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip volatile multimodal payloads before writing session history."""
        filtered: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue

            if (
                drop_runtime
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                and block["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
            ):
                continue

            if block.get("type") == "image_url" and block.get("image_url", {}).get(
                "url", ""
            ).startswith("data:image/"):
                path = (block.get("_meta") or {}).get("path", "")
                filtered.append({"type": "text", "text": image_placeholder_text(path)})
                continue

            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text = block["text"]
                if should_truncate_text and len(text) > self.max_tool_result_chars:
                    text = truncate_text_fn(text, self.max_tool_result_chars)
                filtered.append({**block, "text": text})
                continue

            filtered.append(block)

        return filtered

    def _save_turn(
        self,
        session: Session,
        messages: list[dict],
        skip: int,
        *,
        turn_latency_ms: int | None = None,
    ) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime

        declared_tool_call_ids = {
            str(tc["id"])
            for m in session.messages
            if m.get("role") == "assistant"
            for tc in m.get("tool_calls") or []
            if isinstance(tc, dict) and tc.get("id")
        }
        last_assistant_idx: int | None = None
        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool":
                tool_call_id = entry.get("tool_call_id")
                if not tool_call_id or str(tool_call_id) not in declared_tool_call_ids:
                    # Undeclared tool results corrupt future provider requests.
                    logger.warning(
                        "Dropping orphaned tool result {} from session {} during persistence",
                        tool_call_id or "(missing id)",
                        session.key,
                    )
                    continue
                if isinstance(content, str) and len(content) > self.max_tool_result_chars:
                    entry["content"] = truncate_text_fn(content, self.max_tool_result_chars)
                elif isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, should_truncate_text=True)
                    if not filtered:
                        # Preserve the tool_call/result pair after block filtering.
                        filtered = [
                            {"type": "text", "text": "[tool result omitted during persistence]"}
                        ]
                    entry["content"] = filtered
            elif role == "user":
                if isinstance(content, str):
                    # Il testo com'e' partito, per la richiesta del turno dopo
                    # (``SENT_CONTENT_META``); ``content`` si ripulisce qui sotto.
                    entry[SENT_CONTENT_META] = sent_user_content(entry)
                if isinstance(content, str) and ContextBuilder._RUNTIME_CONTEXT_TAG in content:
                    # Strip the runtime-context block appended at the end.
                    tag_pos = content.find(ContextBuilder._RUNTIME_CONTEXT_TAG)
                    before = content[:tag_pos].rstrip("\n ")
                    if before:
                        entry["content"] = before
                    else:
                        continue
                if isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, drop_runtime=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
            if role == "assistant":
                last_assistant_idx = len(session.messages) - 1
                declared_tool_call_ids.update(
                    str(tc["id"])
                    for tc in entry.get("tool_calls") or []
                    if isinstance(tc, dict) and tc.get("id")
                )
        if turn_latency_ms is not None and last_assistant_idx is not None:
            session.messages[last_assistant_idx]["latency_ms"] = int(turn_latency_ms)
        session.updated_at = datetime.now()

    def _subagent_followup_state(self, session: Session, msg: InboundMessage) -> FollowupState:
        """A che punto e' in storia il rientro di subagent che ``msg`` porta.

        Il turno d'annuncio ne ha bisogno *prima* di costruire il prompt: un
        rientro nuovo si legge in storia dopo averla letta, uno gia' scritto da
        un turno interrotto e' gia' li', e uno a cui Jenny ha gia' risposto non
        deve far rispondere due volte.

        Un rientro in sospeso scritto da una versione precedente porta
        ``role: "assistant"``: qui torna ``user``, perche' in coda al prompt un
        assistant lo tolgono i provider (``enforce_role_alternation``) e il
        modello non lo vedrebbe — e' il difetto da cui viene questo metodo.
        """
        if not msg.content:
            return FollowupState.EMPTY
        task_id = msg.metadata.get("subagent_task_id") if isinstance(msg.metadata, dict) else None
        if not task_id:
            return FollowupState.NEW
        found = None
        for index in range(len(session.messages) - 1, -1, -1):
            row = session.messages[index]
            if (
                row.get(INJECTED_EVENT_META) == SUBAGENT_RESULT_EVENT
                and row.get("subagent_task_id") == task_id
            ):
                found = index
                break
        if found is None:
            return FollowupState.NEW
        answered = any(
            row.get("role") == "assistant" and not is_synthetic_history_row(row)
            for row in session.messages[found + 1:]
        )
        if answered:
            return FollowupState.DELIVERED
        session.messages[found]["role"] = "user"
        return FollowupState.PENDING

    def _settle_closed_reasoning(self, session: Session, built: list[dict[str, Any]]) -> None:
        """Marca il ragionamento chiuso da non rimandare, se il provider lo accetta vuoto.

        V. :func:`context_governor.settle_closed_reasoning`: lo si chiama dove
        il turno si costruisce, perche' e' li' che si sa se la cache e' gia'
        persa.
        """
        if closed_reasoning_can_be_blank(self.provider, self.model):
            settle_closed_reasoning(session.messages, built, session.metadata)

    def _subagent_followup_row(
        self, session: Session, msg: InboundMessage,
    ) -> dict[str, Any] | None:
        """La riga di storia del rientro che ``msg`` porta, la piu' recente."""
        meta = msg.metadata if isinstance(msg.metadata, dict) else {}
        task_id = meta.get("subagent_task_id")
        for row in reversed(session.messages):
            if row.get(INJECTED_EVENT_META) == SUBAGENT_RESULT_EVENT and (
                not task_id or row.get("subagent_task_id") == task_id
            ):
                return row
        return None

    def _persist_subagent_followup(self, session: Session, msg: InboundMessage) -> None:
        """Scrive in storia il rientro di un subagent arrivato a turno chiuso.

        Ruolo ``user``, come il rientro iniettato a meta' turno
        (``AgentLoop._drain_pending``): per il modello e' una notizia che arriva,
        non una cosa che ha detto lui. I metadati sono quelli che fanno
        riconoscere la riga come sintetica (``history_meta``) e che
        ``_subagent_followup_state`` usa per non scriverla due volte.
        """
        meta = msg.metadata if isinstance(msg.metadata, dict) else {}
        extra: dict[str, Any] = {}
        if taint := meta.get(SUBAGENT_TAINT_META):
            extra[SUBAGENT_TAINT_META] = list(taint)
        session.add_message(
            "user",
            msg.content,
            sender_id=msg.sender_id,
            injected_event=SUBAGENT_RESULT_EVENT,
            subagent_task_id=meta.get("subagent_task_id"),
            **extra,
        )

    def _set_runtime_checkpoint(self, session: Session, payload: dict[str, Any]) -> None:
        """Persist the latest in-flight turn state into session metadata.

        ``prior_messages`` (il turno prima dell'iterazione in corso, v.
        ``AgentRunner._emit_checkpoint``) si alleggerisce qui delle immagini in
        base64: il checkpoint si riscrive a ogni fase del turno, e una foto
        iniettata a metà turno lo gonfierebbe di megabyte a ogni riscrittura. Il
        segnaposto è lo stesso che la storia salvata porterebbe comunque.

        **``prior_messages`` non sta nei metadati ma nel diario del turno**
        (:meth:`_journal_prior_messages`). Il checkpoint si scrive due volte per
        iterazione e ogni scrittura riscrive il file di sessione intero: con il
        turno dentro i metadati, un turno di *n* iterazioni scriveva circa *n²*
        volte i risultati dei tool — misurato, 11 MB in più su 30 iterazioni con
        risultati da 8-16 kB, e il tetto è 200 iterazioni. Il diario riceve in
        append solo i messaggi nuovi, e il checkpoint ne porta il conto. Se il
        diario non si può scrivere, il turno torna in linea come prima.
        """
        prior = payload.get("prior_messages")
        if isinstance(prior, list):
            light: list[Any] = []
            for message in prior:
                if isinstance(message, dict) and isinstance(message.get("content"), list):
                    message = {
                        **message,
                        "content": self._sanitize_persisted_blocks(message["content"]),
                    }
                light.append(message)
            journal = self._journal_prior_messages(session.key, light)
            if journal is not None:
                payload = {k: v for k, v in payload.items() if k != "prior_messages"}
                payload["prior_journal"] = journal
            else:
                payload = {**payload, "prior_messages": light}
        session.metadata[self._RUNTIME_CHECKPOINT_KEY] = payload
        self.sessions.save(session)

    def _turn_journal_path(self, key: str) -> Path | None:
        """Il file del diario del turno di *key*, o ``None`` se non c'è dove scriverlo."""
        locate = getattr(getattr(self, "sessions", None), "turn_journal_path", None)
        path = locate(key) if callable(locate) else None
        return path if isinstance(path, Path) else None

    def _journal_prior_messages(
        self, key: str, prior: list[Any],
    ) -> dict[str, Any] | None:
        """Aggiunge al diario del turno i messaggi di *prior* che non ha ancora.

        Il diario è un JSONL accanto al file di sessione: una riga di testa con il
        ``stamp`` del turno, poi un messaggio per riga. ``prior`` cresce solo in
        coda (il runner aggiunge e non riscrive), quindi basta ricordare quanti
        messaggi sono già scritti, e di quale turno (``current_turn_id``). Il
        primo checkpoint di un turno — o uno che non torna col conto, o uno
        fuori da un turno legato — riscrive il diario da capo con un ``stamp``
        nuovo: un diario rimasto da un turno vecchio non si confonde col nuovo,
        perché il ripristino legge solo quello col ``stamp`` del checkpoint.

        Ritorna il riferimento da mettere nel checkpoint (``stamp`` e ``count``),
        o ``None`` se il diario non si è potuto scrivere.
        """
        import json
        import uuid

        path = self._turn_journal_path(key)
        if path is None:
            return None
        from jenny.agent.tools.context import current_turn_id

        turn_id = current_turn_id()
        journals: dict[str, tuple[str | None, str, int]] = self.__dict__.setdefault(
            "_checkpoint_journals", {},
        )
        state = journals.get(key)
        try:
            if state is None or turn_id is None or state[0] != turn_id or state[2] > len(prior):
                stamp = uuid.uuid4().hex
                lines = [json.dumps({"_type": "turn_journal", "stamp": stamp}) + "\n"]
                lines += [json.dumps(m, ensure_ascii=False) + "\n" for m in prior]
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("".join(lines))
            else:
                _, stamp, written = state
                fresh = prior[written:]
                if fresh:
                    with open(path, "a", encoding="utf-8") as fh:
                        fh.write("".join(
                            json.dumps(m, ensure_ascii=False) + "\n" for m in fresh
                        ))
        except (OSError, TypeError, ValueError) as exc:
            # UnicodeEncodeError è un ValueError: un surrogato isolato lo ripulisce
            # il salvataggio della sessione, che col turno in linea lo vede.
            journals.pop(key, None)
            logger.warning(
                "Turn journal for {} not written ({}); the checkpoint carries the turn inline",
                key, exc,
            )
            return None
        journals[key] = (turn_id, stamp, len(prior))
        return {"stamp": stamp, "count": len(prior)}

    def _read_turn_journal(self, key: str, ref: Any) -> list[dict[str, Any]]:
        """I messaggi del diario del turno a cui punta *ref*, o ``[]``."""
        import json

        if not isinstance(ref, dict):
            return []
        stamp, count = ref.get("stamp"), ref.get("count")
        path = self._turn_journal_path(key)
        if path is None or not isinstance(count, int) or count <= 0:
            return []
        messages: list[dict[str, Any]] = []
        try:
            with open(path, encoding="utf-8") as fh:
                head = json.loads(fh.readline() or "null")
                if not isinstance(head, dict) or head.get("stamp") != stamp:
                    raise ValueError("stamp mismatch")
                for line in fh:
                    if len(messages) >= count:
                        break
                    message = json.loads(line)
                    if isinstance(message, dict):
                        messages.append(message)
        except (OSError, ValueError) as exc:
            logger.warning(
                "Turn journal for {} unreadable ({}); restoring only the last iteration",
                key, exc,
            )
            return []
        return messages

    def _drop_turn_journal(self, key: str) -> None:
        """Dimentica il diario del turno di *key* e ne toglie il file."""
        self.__dict__.get("_checkpoint_journals", {}).pop(key, None)
        path = self._turn_journal_path(key)
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _mark_pending_user_turn(self, session: Session) -> None:
        session.metadata[self._PENDING_USER_TURN_KEY] = True

    def _clear_pending_user_turn(self, session: Session) -> None:
        session.metadata.pop(self._PENDING_USER_TURN_KEY, None)

    def _clear_runtime_checkpoint(self, session: Session) -> None:
        """Toglie il checkpoint dai metadati, e con lui il diario del turno.

        Il diario si cancella qui e non dopo il salvataggio che segue: tutti i
        chiamanti salvano subito dopo, senza ``await`` in mezzo, e il turno che
        il diario proteggeva è già nei messaggi della sessione.
        """
        if self._RUNTIME_CHECKPOINT_KEY in session.metadata:
            session.metadata.pop(self._RUNTIME_CHECKPOINT_KEY, None)
        self._drop_turn_journal(session.key)

    @staticmethod
    def _checkpoint_message_key(message: dict[str, Any]) -> tuple[Any, ...]:
        return (
            message.get("role"),
            message.get("content"),
            message.get("tool_call_id"),
            message.get("name"),
            message.get("tool_calls"),
            message.get("reasoning_content"),
            message.get("thinking_blocks"),
        )

    def _restore_runtime_checkpoint(self, session: Session) -> bool:
        """Materialize an unfinished turn into session history before a new request."""
        from datetime import datetime

        checkpoint = session.metadata.get(self._RUNTIME_CHECKPOINT_KEY)
        if not isinstance(checkpoint, dict):
            return False

        assistant_message = checkpoint.get("assistant_message")
        completed_tool_results = checkpoint.get("completed_tool_results") or []
        pending_tool_calls = checkpoint.get("pending_tool_calls") or []
        # Le iterazioni già chiuse del turno e i messaggi iniettati:
        # assenti in un checkpoint scritto da una versione
        # precedente, che si ripristina come prima.
        prior_messages = checkpoint.get("prior_messages") or self._read_turn_journal(
            session.key, checkpoint.get("prior_journal"),
        )

        restored_messages: list[dict[str, Any]] = [
            dict(message) for message in prior_messages if isinstance(message, dict)
        ]
        if isinstance(assistant_message, dict):
            restored = dict(assistant_message)
            restored.setdefault("timestamp", datetime.now().isoformat())
            restored_messages.append(restored)
        for message in completed_tool_results:
            if isinstance(message, dict):
                restored = dict(message)
                restored.setdefault("timestamp", datetime.now().isoformat())
                restored_messages.append(restored)
        for tool_call in pending_tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tool_id = tool_call.get("id")
            name = ((tool_call.get("function") or {}).get("name")) or "tool"
            restored_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": name,
                    "content": "Error: Task interrupted before this tool finished.",
                    "timestamp": datetime.now().isoformat(),
                }
            )

        overlap = 0
        max_overlap = min(len(session.messages), len(restored_messages))
        for size in range(max_overlap, 0, -1):
            existing = session.messages[-size:]
            restored = restored_messages[:size]
            if all(
                self._checkpoint_message_key(left) == self._checkpoint_message_key(right)
                for left, right in zip(existing, restored)
            ):
                overlap = size
                break
        # Dalla stessa porta di un turno finito: il runtime context tolto dai
        # messaggi utente iniettati, i risultati tool troncati, le immagini a
        # segnaposto, i risultati orfani scartati.
        self._save_turn(session, restored_messages, overlap)

        self._clear_pending_user_turn(session)
        self._clear_runtime_checkpoint(session)
        return True

    def _restore_pending_user_turn(self, session: Session) -> bool:
        """Close a turn that only persisted the user message before crashing."""
        from datetime import datetime

        if not session.metadata.get(self._PENDING_USER_TURN_KEY):
            return False

        if session.messages and session.messages[-1].get("role") == "user":
            session.messages.append(
                {
                    "role": "assistant",
                    "content": "Error: Task interrupted before a response was generated.",
                    "timestamp": datetime.now().isoformat(),
                }
            )
            session.updated_at = datetime.now()

        self._clear_pending_user_turn(session)
        return True

