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

from jenny.bus.events import NOTIFICATION_CHANNEL
from jenny.channels.non_streaming import FinalTextChannel
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


class NotificationChannel(FinalTextChannel):
    """Consegna la risposta come alert di sistema Android.

    Niente spinner né tool hint in una tendina: l'unica cosa che ha senso
    postare è il messaggio finale. Gli alert già postati restano allo
    spegnimento del gateway, di proposito: un avviso che l'utente non ha ancora
    letto non deve sparire perché il servizio si è riavviato. A ripulirli c'è
    ``NotifierBridge.clearAlerts`` quando la chat arriva a schermo, che è il
    momento in cui sono davvero stati letti.
    """

    name = NOTIFICATION_CHANNEL
    target = "the shade"

    async def _deliver(self, content: str, metadata: dict[str, Any]) -> bool:
        """Posta l'alert sul filo da cui è arrivata la domanda. ``False`` è un
        esito normale: l'app in primo piano sopprime l'alert perché la risposta
        è già a schermo in chat."""
        return await post_alert(content, metadata, thread=_thread_of(metadata))
