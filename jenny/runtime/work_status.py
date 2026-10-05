"""Lo stato di un lavoro lungo che qualcuno aspetta in una chat.

Una passata del giardiniere dura minuti, un ciclo di Dream lanciato a mano anche.
Fino al 05/10/2026 la chat diceva «Gardening etf-finance...» e poi niente fino
alla fine: quel giorno una passata ha lavorato sei minuti, con un errore 500 del
provider recuperato nel mezzo, e dalla home sembrava ferma. Questo modulo e' la
fonte della **bolla di lavoro** della home (``assets/home-work.js``): chi lavora,
in che fase, quante scritture, se il modello sta riprovando, com'e' finita.

**Una sola forma, due trasporti**, come lo snapshot dei subagent: il frame
``work_status`` sul WebSocket (``ws_sender.send_work_status``) e
``GET /api/work`` per chi si ricollega a meta' (``webui/work_routes.py``). Il
frame va solo a chi e' collegato a quella chat, e questo basta a realizzare la
regola «la bolla c'e' quando qualcuno aspetta in quella chat»: una passata
automatica su un quaderno che nessuno sta guardando non arriva a nessuno.

**Niente testo nel payload**, solo campi: la bolla la scrive il client, nella
lingua dell'interfaccia. Il resoconto in prosa resta il messaggio finale del
comando, che l'officina continua a leggere com'era.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Iterable
from typing import Any

from loguru import logger

from jenny.bus.events import OUTBOUND_META_WORK_STATUS, OutboundMessage

WEBSOCKET_CHANNEL = "websocket"

# Al massimo un aggiornamento ogni tanti secondi per lo stesso lavoro: una
# passata che scrive dieci file di fila non manda dieci frame. Le transizioni
# (inizio, fase, riprova, fine) partono subito.
PUBLISH_EVERY_S = 2.0

# Quanto resta nello snapshot un lavoro finito: abbastanza perche' chi riapre la
# home subito dopo veda com'e' andata, poco perche' lo snapshot resti piccolo.
LINGER_S = 60.0

KINDS = ("gardener", "dream")
STATES = ("running", "done", "failed")

_REGISTRY: dict[str, WorkReporter] = {}


def _retry_total() -> int:
    """Quanti tentativi fa il provider in modalita' standard (``1, 2, 4``)."""
    from jenny.providers.base import LLMProvider

    return len(LLMProvider._CHAT_RETRY_DELAYS)


class WorkReporter:
    """Un lavoro in corso, e chi lo racconta alla chat che lo aspetta.

    Non decide niente del lavoro: lo osserva. Ogni metodo pubblico e' un fatto
    («e' entrato nella fase X», «ha scritto», «il modello riprova», «e' finito»)
    e nessuno solleva: una bolla che non si aggiorna e' un difetto minore di una
    passata che cade perche' la bolla non riusciva a pubblicare.
    """

    def __init__(
        self,
        bus: Any,
        *,
        kind: str,
        chat_id: str,
        phases: Iterable[str],
        project: str | None = None,
        manual: bool = True,
        channel: str = WEBSOCKET_CHANNEL,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown work kind: {kind!r}")
        self.bus = bus
        self.kind = kind
        self.chat_id = chat_id
        self.channel = channel
        self.project = project
        self.manual = manual
        self.phases = list(phases)
        self.id = f"{kind}-{uuid.uuid4().hex[:12]}"
        self._clock = clock
        self._monotonic = monotonic
        self.started_at = clock()
        self.updated_at = self.started_at
        self.ended_at: float | None = None
        self.state = "running"
        self.phase: str | None = None
        self.writes = 0
        self.retry: dict[str, int] | None = None
        self.outcome: dict[str, Any] | None = None
        self._last_publish: float | None = None
        self._trailing: asyncio.TimerHandle | None = None
        _REGISTRY[self.id] = self
        _prune(clock())

    # -- i fatti ------------------------------------------------------------

    def start(self) -> None:
        """Il lavoro e' partito: la bolla compare."""
        self._publish(force=True)

    def set_phases(self, phases: Iterable[str]) -> None:
        """Le fasi del lavoro, quando si sanno solo a lavoro iniziato.

        Dream sa se gli tocca la revisione solo dopo aver misurato i file: la
        bolla parte senza fasi e le riceve qui, prima della prima.
        """
        self.phases = list(phases)
        self._touch()
        self._publish(force=True)

    def enter(self, phase: str) -> None:
        """Il lavoro entra in *phase*; una fase non annunciata si aggiunge in coda."""
        if phase not in self.phases:
            self.phases.append(phase)
        self.phase = phase
        self.retry = None
        self._touch()
        self._publish(force=True)

    def wrote(self, count: int = 1) -> None:
        """Una scrittura (tentata) sul disco del lavoro."""
        self.writes += max(int(count), 0)
        self._touch()
        self._publish()

    def progress(self) -> None:
        """Il lavoro si e' mosso (uno strumento, una risposta): non e' fermo.

        Spegne anche un riprova in corso: se il modello ha risposto, l'attesa
        e' finita.
        """
        forced = self.retry is not None
        self.retry = None
        self._touch()
        self._publish(force=forced)

    def retry_notice(self, text: str) -> None:
        """Una riga d'attesa del provider: il modello non ha risposto e si riprova."""
        from jenny.providers import retry_notice

        parsed = retry_notice.parse(text)
        if parsed is None:
            return
        name, fields = parsed
        if name != "waiting":
            return
        try:
            attempt = int(fields.get("attempt") or 0)
        except ValueError:
            return
        self.retry = {"attempt": attempt, "of": _retry_total()}
        self._touch()
        self._publish(force=True)

    def finish(self, state: str, **outcome: Any) -> None:
        """Il lavoro e' finito (``done``) o non ci e' riuscito (``failed``)."""
        if state not in ("done", "failed"):
            raise ValueError(f"unknown final state: {state!r}")
        self.state = state
        self.retry = None
        self.outcome = {k: v for k, v in outcome.items() if v is not None}
        self.ended_at = self._clock()
        self._touch()
        self._publish(force=True)

    def forget(self) -> None:
        """Il lavoro non e' mai partito: via dal registro, senza dire niente.

        E' il caso di ``/dream`` quando un altro ciclo tiene gia' la presa: la
        bolla non era ancora comparsa, e non deve comparire.
        """
        self._cancel_trailing()
        _REGISTRY.pop(self.id, None)

    # -- callback pronti per ``process_direct`` -------------------------------

    async def on_progress(self, *_args: Any, **_kwargs: Any) -> None:
        """``on_progress`` per un turno interno: ogni chiamata vuol dire «si muove».

        La stessa firma di ``silent_progress``, che sostituisce: il contenuto non
        si guarda (e' roba interna), conta solo che sia arrivato.
        """
        self.progress()

    async def on_retry_wait(self, text: str) -> None:
        """``on_retry_wait`` per un turno interno."""
        self.retry_notice(text)

    def write_observer(self, _path: Any, _text: str) -> str | None:
        """Una guardia di scrittura che non rifiuta mai: conta e lascia passare.

        Si compone con le altre in ``gardener._compose_write_guards``: e' il
        solo punto che vede ogni scrittura nel momento in cui avviene, mentre i
        conteggi veri (``file_states.writes_ok``) esistono solo a turno finito.
        """
        self.wrote()
        return None

    # -- forma -----------------------------------------------------------------

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "manual": self.manual,
            "phases": list(self.phases),
            "phase": self.phase,
            "writes": self.writes,
            "retry": dict(self.retry) if self.retry else None,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }
        if self.project:
            body["project"] = self.project
        if self.ended_at is not None:
            body["ended_at"] = self.ended_at
        if self.outcome is not None:
            body["outcome"] = dict(self.outcome)
        return body

    # -- pubblicazione -----------------------------------------------------------

    def _touch(self) -> None:
        self.updated_at = self._clock()

    def _publish(self, *, force: bool = False) -> None:
        now = self._monotonic()
        if not force and self._last_publish is not None:
            wait = PUBLISH_EVERY_S - (now - self._last_publish)
            if wait > 0:
                self._schedule_trailing(wait)
                return
        self._cancel_trailing()
        self._last_publish = now
        try:
            self.bus.try_publish_outbound(OutboundMessage(
                channel=self.channel,
                chat_id=self.chat_id,
                content="",
                metadata={OUTBOUND_META_WORK_STATUS: self.payload()},
            ))
        except Exception as exc:  # noqa: BLE001 — la bolla non fa cadere il lavoro
            logger.debug("work status {} not published: {}", self.id, exc)

    def _schedule_trailing(self, wait: float) -> None:
        """L'ultimo aggiornamento trattenuto dal limite parte comunque, in ritardo.

        Senza, una scrittura seguita da minuti di silenzio resterebbe fuori
        dalla bolla fino al fatto successivo.
        """
        if self._trailing is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._trailing = loop.call_later(wait, self._flush_trailing)

    def _flush_trailing(self) -> None:
        self._trailing = None
        self._publish(force=True)

    def _cancel_trailing(self) -> None:
        if self._trailing is not None:
            self._trailing.cancel()
            self._trailing = None


def _prune(now: float) -> None:
    for work_id, reporter in list(_REGISTRY.items()):
        if reporter.ended_at is not None and now - reporter.ended_at > LINGER_S:
            del _REGISTRY[work_id]


def work_snapshot(chat_id: str, *, now: float | None = None) -> list[dict[str, Any]]:
    """I lavori di *chat_id*: quelli in corso e quelli finiti da poco, dal piu' vecchio."""
    _prune(time.time() if now is None else now)
    items = [r for r in _REGISTRY.values() if r.chat_id == chat_id]
    items.sort(key=lambda r: r.started_at)
    return [r.payload() for r in items]


def reset_registry() -> None:
    """Solo per i test: dimentica ogni lavoro."""
    for reporter in _REGISTRY.values():
        reporter._cancel_trailing()
    _REGISTRY.clear()
