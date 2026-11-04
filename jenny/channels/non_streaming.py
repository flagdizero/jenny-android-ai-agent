"""Il contratto di streaming del dispatcher, per i canali che non fanno streaming.

Il dispatcher chiama ``send_delta``, ``send_reasoning_*``, ``send_file_edit_events``
e ``discard_stream_buffer`` su ogni canale. Il fumetto della mascotte, la
tendina delle notifiche e Telegram mostrano solo il messaggio finale: per loro
quei metodi esistono e non fanno niente. Erano copiati identici in tutte e tre.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from jenny.bus.events import OutboundMessage, is_coordination


class NonStreamingChannelMixin:
    async def send_delta(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def send_reasoning_delta(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def send_reasoning_end(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def send_file_edit_events(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def discard_stream_buffer(self, *args: Any, **kwargs: Any) -> None:
        return None


class FinalTextChannel(NonStreamingChannelMixin):
    """Un canale che consegna solo il testo finale, in un posto solo del telefono.

    La tendina delle notifiche e il fumetto della mascotte: niente connessione
    da aprire (``start``/``stop`` non fanno niente), niente spinner, tool hint
    o ragionamento, e un tentativo solo — la consegna non solleva mai e ritorna
    un booleano, quindi un retry del dispatcher scatterebbe solo per un errore
    qui e consegnerebbe due volte la stessa cosa. Le sottoclassi dicono dove
    va il testo (:meth:`_deliver`) e come chiamarlo nei log (:attr:`target`).
    Fino al 02/11/2026 i due canali erano lo stesso file scritto due volte.
    """

    send_progress = False
    send_tool_hints = False
    show_reasoning = False
    send_max_retries = 1
    #: Il posto in cui il testo arriva, per i log («the shade», «the bubble»).
    target = ""

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def _deliver(self, content: str, metadata: dict[str, Any]) -> bool:
        raise NotImplementedError

    async def send(
        self,
        msg: OutboundMessage,
        *,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        """Consegna il messaggio finale. Ritorna sempre ``[]``.

        Nessun fan-out parziale da tracciare (il posto è uno) e nessuna
        persistenza: la riga del transcript la scrive il mirror sulla vista
        WebUI, che è la vista canonica della conversazione; *skip_persist* non
        ha niente da saltare. Gli eventi di coordinamento — ``_turn_end`` e i
        suoi fratelli — arrivano fin qui perché non portano nessuno dei flag di
        streaming su cui il dispatcher smista prima, e vanno scartati: sono
        marcatori della vista WebUI, e consegnarli farebbe squillare o
        disegnare il vuoto a ogni fine turno.
        """
        meta = msg.metadata or {}
        if is_coordination(meta):
            return []
        content = (msg.content or "").strip()
        if not content:
            return []
        if msg.media:
            # Il posto è testo. Gli allegati restano nella conversazione — la
            # WebUI li mostra — e lo si annota, invece di far finta che il
            # messaggio fosse completo.
            logger.info(
                "{} channel: {} media attachment(s) not sent to {}",
                self.name, len(msg.media), self.target,
            )
        if not await self._deliver(content, meta):
            # Esito normale e non un errore (spento, app in primo piano, nessun
            # ponte): il perché lo dice la sottoclasse dove serve.
            logger.info("{} channel: nothing shown in {}", self.name, self.target)
        return []

