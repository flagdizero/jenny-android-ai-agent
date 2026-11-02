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

from jenny.bus.events import FLOATING_CHANNEL
from jenny.channels.non_streaming import FinalTextChannel
from jenny.runtime.floating import show_reply


class FloatingChannel(FinalTextChannel):
    """Consegna la risposta al fumetto della mascotte flottante.

    Un fumetto sopra una testa non è un posto per spinner e tool hint: l'unica
    cosa che ha senso disegnarci è il messaggio finale, e mentre aspetta lo
    stato lo dice la faccia, che decide Kotlin senza passare di qua. La
    finestra non si smonta in ``stop``, e non è una dimenticanza: vive nel
    processo del ``GatewayService`` e il suo ciclo di vita è quello del
    service, non quello del canale — un gateway che riparte nello stesso
    processo non deve far sparire e ricomparire la mascotte.
    """

    name = FLOATING_CHANNEL
    target = "the bubble"

    async def _deliver(self, content: str, metadata: dict[str, Any]) -> bool:
        """Disegna il testo nel fumetto. ``False`` è un esito normale: mascotte
        spenta, permesso mancante, app in primo piano (dove la risposta è già a
        schermo in chat), o **finestra chiusa** — il caso volutamente
        silenzioso: la finestra non si riapre da sé."""
        return await show_reply(content)
