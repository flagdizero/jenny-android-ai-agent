"""Canale della mascotte flottante: la risposta nella finestra sopra di lei.

Quarto canale utente, gemello di ``channels/notification.py`` — stessa forma,
stesse ragioni, un solo destinatario diverso: là un alert di sistema, qui la
finestra overlay.

**Perché è un canale e non una funzione di consegna.** Per la stessa ragione
scritta per esteso nella tendina, e vale la pena ripeterla perché è l'intero
motivo per cui questo file è corto: essere un canale gli fa ereditare, senza
scrivere una riga,

* la conversazione — ``session_key_for_channel`` manda ogni canale che non sia
  la WebUI con un ``chat_id`` di progetto su ``unified:default``, quindi quello
  che si chiede al fumetto è *la stessa chat dell'app*, con la stessa memoria;
* l'eco del messaggio dell'utente in chat — ``WebuiTurnCoordinator`` ha già il
  ramo "turno partito da un altro canale utente";
* la risposta nel transcript — ``WebSocketDispatcher._mirror_final_to_webui_view``;
* e **nessuna doppia notifica**: quel mirror marca la copia con
  ``origin_channel``, e ``ws_sender`` non squilla sui messaggi con ``origin``.
  Senza, chi scrive nel fumetto si vedrebbe arrivare anche l'alert della
  tendina con le stesse parole.

**La cronologia ha un posto solo, e non è questo.** Dal 18/09/2026 la finestra
tiene una conversazione corta — gli ultimi quattro scambi, finché resta aperta —
ma il canale non ne sa niente, e non è una semplificazione: accumula Kotlin, che
è anche l'unico a sapere quando la finestra si chiude, cioè quando quella
conversazione finisce. Qui ogni ``send`` consegna una riga e la dimentica; due
contabilità della stessa lista divergerebbero al primo timeout. La conversazione
*completa* resta comunque quella dell'app, e dalla finestra ci si arriva con il
tasto in cima alla lista.

**E non fa comparire niente da sé.** ``show_reply`` ritorna ``False`` se la
finestra è chiusa, e il canale tira dritto: chi l'ha chiusa l'ha chiusa apposta,
e far saltare su un pannello sopra l'app che sta usando è esattamente ciò che una
mascotte non deve fare. La risposta non si perde — è nella stessa sessione
``unified`` di cui questa finestra è una vista.

Come la tendina, il canale **non** entra fra gli ``extra_targets`` del
``ChannelDeliverer``: là stanno i destinatari del fan-out proattivo, e
mettercelo significherebbe che ogni promemoria del cron va a stampare un
fumetto sopra l'app che l'utente sta usando.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from jenny.bus.events import COORDINATION_FLAGS, FLOATING_CHANNEL, OutboundMessage
from jenny.channels.non_streaming import NonStreamingChannelMixin
from jenny.runtime.floating import show_reply


class FloatingChannel(NonStreamingChannelMixin):
    """Consegna la risposta al fumetto della mascotte flottante."""

    name = FLOATING_CHANNEL
    # Un fumetto sopra una testa non è un posto per spinner e tool hint: l'unica
    # cosa che ha senso disegnarci è il messaggio finale. Mentre aspetta, lo
    # stato lo dice la faccia — che decide Kotlin, senza passare di qua.
    send_progress = False
    send_tool_hints = False
    show_reasoning = False
    # Un solo tentativo. ``show_reply`` non solleva mai — cattura tutto e
    # ritorna un booleano — quindi il retry del dispatcher non scatterebbe per
    # un fallimento di consegna, ma solo per un errore *qui*; e ripetere
    # vorrebbe dire ridisegnare lo stesso fumetto.
    send_max_retries = 1

    async def start(self) -> None:
        """Nessuna connessione da aprire: la finestra la monta Kotlin."""
        return None

    async def stop(self) -> None:
        """Nessuna connessione da chiudere.

        La finestra non si smonta qui, e non è una dimenticanza: vive nel
        processo del ``GatewayService`` e il suo ciclo di vita è quello del
        service, non quello del canale. Un gateway che riparte nello stesso
        processo non deve far sparire e ricomparire la mascotte.
        """
        return None

    async def send(
        self,
        msg: OutboundMessage,
        *,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        """Aggiunge il messaggio finale alla conversazione. Ritorna sempre ``[]``.

        Nessun fan-out parziale da tracciare (la finestra è una sola) e nessuna
        persistenza: la riga del transcript la scrive il mirror sulla vista
        WebUI, che è la vista canonica della conversazione. Per lo stesso motivo
        *skip_persist* non ha niente da saltare.

        Gli eventi di coordinamento — ``_turn_end`` e i suoi fratelli — arrivano
        fin qui perché non portano nessuno dei flag di streaming su cui il
        dispatcher smista prima, e vanno scartati: sono marcatori della vista
        WebUI, e disegnarli riempirebbe il fumetto di vuoto a ogni fine turno.
        """
        meta = msg.metadata or {}
        if any(meta.get(flag) for flag in COORDINATION_FLAGS):
            return []
        content = (msg.content or "").strip()
        if not content:
            return []
        if msg.media:
            # Una bolla è testo. Gli allegati restano nella conversazione — la
            # WebUI li mostra — e qui si annota che non sono passati di qua,
            # invece di far finta che il messaggio fosse completo.
            logger.info(
                "Floating channel: {} media attachment(s) not drawn in the bubble",
                len(msg.media),
            )
        shown = await show_reply(content)
        if not shown:
            # Esito normale e non un errore: mascotte spenta, permesso
            # mancante, app in primo piano (dove la risposta è già a schermo in
            # chat), o **finestra chiusa** — quest'ultimo è il caso volutamente
            # silenzioso: la finestra non si riapre da sé.
            logger.info(
                "Floating channel: reply not drawn (disabled, hidden, closed or no bridge)"
            )
        return []

