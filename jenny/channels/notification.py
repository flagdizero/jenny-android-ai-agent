"""Canale della tendina: la risposta a un messaggio scritto da una notifica.

Terzo canale utente dopo WebSocket e Telegram, e il più piccolo possibile — non
ha una connessione da aprire, un polling da tenere o un destinatario da
accoppiare: la sua consegna è un alert di sistema, cioè una chiamata sola a
``runtime.notifier.post_alert``.

**Perché è un canale e non una funzione di consegna.** Perché essere un canale è
ciò che gli fa ereditare, senza scrivere una riga:

* la conversazione — ``session_key_for_channel`` manda ogni canale che non sia
  la WebUI con un ``chat_id`` di progetto su ``unified:default``;
* l'eco del messaggio dell'utente in chat — ``WebuiTurnCoordinator`` ha già il
  ramo "turno partito da un altro canale utente";
* la risposta nel transcript — ``WebSocketDispatcher._mirror_final_to_webui_view``;
* e soprattutto **nessuna doppia notifica**: quel mirror marca la copia con
  ``origin_channel``, e ``ws_sender`` non squilla sui messaggi con ``origin``
  («chi scrive sta già guardando»). L'unico a postare l'alert è questo canale.
  Una consegna scritta a lato, fuori dal giro dei canali, dovrebbe rifare quel
  ragionamento a mano — ed è esattamente così che si arriva al doppio squillo.

Il canale **non** entra fra gli ``extra_targets`` del ``ChannelDeliverer``: là
stanno i destinatari del fan-out proattivo, e mettercelo significherebbe che
ogni promemoria del cron squilla due volte, una dal percorso WebUI e una da qui.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from jenny.bus.events import COORDINATION_FLAGS, NOTIFICATION_CHANNEL, OutboundMessage
from jenny.channels.non_streaming import NonStreamingChannelMixin
from jenny.runtime.native_input import NATIVE_THREAD_KEY
from jenny.runtime.notifier import post_alert

# Tag di ripiego per le risposte dell'agente: il filo della conversazione quando
# non se ne conosce uno d'origine.
#
# **Il caso normale è un altro**, ed è il punto di questa meccanica: la domanda
# arriva da una notifica precisa e ne porta il tag (``NATIVE_THREAD_KEY``), così
# la risposta si posta **su quella** e il discorso resta nella scheda in cui è
# cominciato. Rispondere al promemoria delle 8 e vedersi aprire una seconda
# scheda altrove era il difetto, non lo stile.
#
# Questo ripiego copre l'unico caso in cui il tag non c'è: una notifica postata
# da una versione precedente, il cui ``PendingIntent`` non porta l'extra.
REPLY_THREAD_TAG = "chat"


def _thread_of(metadata: dict[str, Any]) -> str:
    """Il tag su cui postare la risposta: quello da cui è arrivata la domanda.

    Il valore ha attraversato tutto il turno dentro i metadata — ce lo porta
    ``_assemble_outbound``, che copia i metadata dell'inbound sull'outbound — e
    qui si chiude il cerchio. Un valore non-stringa o vuoto è trattato come
    assente: arriva da Kotlin, e un canale non si fida di ciò che gli entra
    dall'esterno del processo.
    """
    raw = metadata.get(NATIVE_THREAD_KEY)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return REPLY_THREAD_TAG


class NotificationChannel(NonStreamingChannelMixin):
    """Consegna la risposta come alert di sistema Android."""

    name = NOTIFICATION_CHANNEL
    # Niente spinner né tool hint in una tendina: l'unica cosa che ha senso
    # postare è il messaggio finale.
    send_progress = False
    send_tool_hints = False
    show_reasoning = False
    # Un solo tentativo. ``post_alert`` non solleva mai — cattura tutto e
    # ritorna un booleano — quindi il retry del dispatcher non scatterebbe per
    # un fallimento di consegna; scatterebbe solo per un errore *qui*, e
    # ripetere vorrebbe dire postare due volte lo stesso alert.
    send_max_retries = 1

    async def start(self) -> None:
        """Nessuna connessione da aprire: la tendina c'è già."""
        return None

    async def stop(self) -> None:
        """Nessuna connessione da chiudere. Gli alert già postati restano.

        Non si cancellano allo spegnimento del gateway di proposito: un avviso
        che l'utente non ha ancora letto non deve sparire perché il servizio si
        è riavviato. A ripulirli c'è ``NotifierBridge.clearAlerts`` quando la
        chat arriva a schermo, che è il momento in cui sono davvero stati letti.
        """
        return None

    async def send(
        self,
        msg: OutboundMessage,
        *,
        only_conns: list[Any] | None = None,
        skip_persist: bool = False,
    ) -> list[Any]:
        """Posta il messaggio finale come alert. Ritorna sempre ``[]``.

        Nessun fan-out parziale da tracciare (la tendina è una sola) e nessuna
        persistenza: la riga del transcript la scrive il mirror sulla vista
        WebUI, che è la vista canonica della conversazione. Per lo stesso motivo
        *skip_persist* non ha niente da saltare.

        Gli eventi di coordinamento — ``_turn_end`` e i suoi fratelli — arrivano
        fin qui perché non portano nessuno dei flag di streaming su cui il
        dispatcher smista prima, e vanno scartati: sono marcatori della vista
        WebUI, e postarli farebbe squillare una notifica vuota a ogni fine turno.
        """
        meta = msg.metadata or {}
        if any(meta.get(flag) for flag in COORDINATION_FLAGS):
            return []
        content = (msg.content or "").strip()
        if not content:
            return []
        if msg.media:
            # Un alert è testo. Gli allegati restano nella conversazione — la
            # WebUI li mostra — e qui si annota che non sono passati di qua,
            # invece di far finta che il messaggio fosse completo.
            logger.info(
                "Notification channel: {} media attachment(s) not sent to the shade",
                len(msg.media),
            )
        posted = await post_alert(content, meta, thread=_thread_of(meta))
        if not posted:
            # Esito normale e non un errore: l'app in primo piano sopprime
            # l'alert perché la risposta è già a schermo in chat.
            logger.info("Notification channel: alert not posted (foreground or no bridge)")
        return []

