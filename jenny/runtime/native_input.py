"""Ingresso del testo dell'utente dalle superfici native di Android.

Due superfici, oggi: la **tendina** delle notifiche — la risposta rapida
(``RemoteInput``) che si scrive senza aprire l'app, rovescio di
``jenny/runtime/notifier.py`` — e la **mascotte flottante**, la finestra che sta
sopra le altre app e apre un campo al tocco. Entrambe scrivono testo dell'utente
da fuori dalla WebUI, ed entrambe entrano da qui.

**Il testo non prende una strada nuova.** Diventa un ``InboundMessage`` su un
canale utente come gli altri, e da lì in poi tutto il resto esiste già: la
risoluzione della session key manda ogni canale sulla conversazione unica
(:func:`jenny.session.keys.session_key_for_channel`), il coordinatore della
vista WebUI fa l'eco del messaggio in chat, e il dispatcher proietta la risposta
sul transcript. Quello che si scrive dalla tendina e la risposta che ne segue
finiscono quindi nella stessa conversazione di tutto il resto, e la memoria di
Dream copre anche quei turni.

**Come entra Kotlin.** Con la stessa grammatica di ``power.on_wake_tick``: da un
thread di lavoro JNI (mai dal main — l'attraversamento Chaquopy può restare
bloccato quanto il GIL resta preso da un turno in corso) chiama
``on_native_text(text, source)``. Da lì l'unica cosa lecita è
``loop.call_soon_threadsafe``: la pubblicazione è una coroutine e deve girare
sul loop del gateway, non sul thread chiamante. Toccare la coda direttamente da
quel thread sembrerebbe funzionare e perderebbe messaggi, perché non passerebbe
dal selector su cui il loop è bloccato.

**Una superficie, un canale.** La mappa ``_CHANNEL_BY_SOURCE`` è l'unico punto
in cui una sorgente nativa diventa un nome di canale, ed è chiusa: una sorgente
che non è là dentro viene rifiutata invece di fabbricare un canale che il
dispatcher non conosce. Il motivo è lo stesso per cui
``session_key_for_channel`` tiene un elenco chiuso di forme riconosciute — qui
il chiamante è codice nostro, ma la regola che vale è che un nome di canale non
si accetta da fuori.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.bus.events import FLOATING_CHANNEL, NOTIFICATION_CHANNEL, InboundMessage

if TYPE_CHECKING:
    from jenny.bus.queue import MessageBus

# Chiave dei metadata che dice da quale superficie è arrivato il testo. Il canale
# lo identifica già; la chiave esiste perché il canale dice *dove torna la
# risposta* e questa dice *da dove è entrata la domanda* — due domande diverse
# che finora hanno sempre la stessa risposta, ma non per forza.
NATIVE_SOURCE_KEY = "_native_source"

# Chiave dei metadata che porta il **filo** su cui è nata la domanda: il tag
# della notifica a cui l'utente ha risposto. Torna a valle in
# ``NotificationChannel.send``, che ci riporta sopra la risposta — così il
# discorso resta nella scheda in cui è cominciato invece di aprirne una seconda.
# Viaggia gratis: ``_assemble_outbound`` copia i metadata dell'inbound
# sull'outbound (``meta = dict(msg.metadata or {})``).
NATIVE_THREAD_KEY = "_native_thread"

# La sorgente "risposta dalla tendina". Stringa condivisa con Kotlin
# (``ReplyReceiver`` → ``GatewayService.deliverNativeText``): è un valore di
# protocollo, non un'etichetta.
SOURCE_NOTIFICATION = "notification"

# La sorgente "mascotte flottante": la finestra overlay sopra le altre app.
# Stringa condivisa con Kotlin (``FloatingOverlayController`` →
# ``GatewayService.deliverNativeText``), anche questa di protocollo.
SOURCE_FLOATING = "floating"

# Sorgente → canale di consegna della risposta. Elenco chiuso: v. il docstring.
#
# Le due sorgenti sono due superfici native diverse e vogliono due canali
# diversi **perché la risposta torna in due posti diversi**: la tendina la posta
# come alert, il fumetto la disegna nella propria finestra. Mandarle sullo
# stesso canale farebbe rispondere alla finestra sbagliata — chi ha scritto
# dall'overlay vedrebbe la risposta squillare in notifica, e viceversa.
_CHANNEL_BY_SOURCE: dict[str, str] = {
    SOURCE_NOTIFICATION: NOTIFICATION_CHANNEL,
    SOURCE_FLOATING: FLOATING_CHANNEL,
}

# ``chat_id`` dei messaggi che entrano da qui. La session key non lo guarda
# (tutto converge su ``unified:default``), quindi serve solo a leggersi nei log
# e a distinguere le impronte anti-duplicato del dispatcher.
#
# È lo stesso per tutte le superfici native, e va bene così: le impronte del
# dispatcher si distinguono già per canale, e un ``chat_id`` per superficie
# aprirebbe una seconda coordinata senza che nessuno la guardi.
NATIVE_CHAT_ID = "shade"

# Tetto duro sul testo accettato. Vale per tutte le superfici native, e la
# ragione è la stessa in tutte: è una domanda scritta col pollice in una casella
# di una riga, non un'interfaccia per saghe. Oltre il tetto è quasi sempre un
# incolla accidentale, e rifiutarlo è più onesto che troncarlo e rispondere a
# metà domanda.
MAX_TEXT_CHARS = 2000

# Loop e bus del gateway corrente. Globali di modulo e non stato di un oggetto,
# per lo stesso motivo di ``power._WAKE_LOOP``: il chiamante è Kotlin via
# Chaquopy da un thread JNI, con in mano solo il nome del modulo.
_LOOP: asyncio.AbstractEventLoop | None = None
_BUS: Any = None

# I task di pubblicazione vanno tenuti referenziati fino al completamento:
# asyncio tiene solo weakref e il GC può cancellarli a metà. Stessa rete di
# ``notifier._TASKS``.
_TASKS: set[asyncio.Task[Any]] = set()


def reset_native_input() -> None:
    """Slega loop e bus a un nuovo start del gateway.

    Simmetrico a ``power.reset_power_state`` e chiamato dallo stesso posto
    (``android_entry.run_gateway``, prima del nuovo event loop): i riferimenti
    del giro precedente puntano a un loop morto, e un ``call_soon_threadsafe``
    su quello solleverebbe — o peggio, accoderebbe per nessuno.
    """
    global _LOOP, _BUS
    _LOOP = None
    _BUS = None
    # I task rimasti appartengono al loop morto: nessuno li completerà e
    # tenerli referenziati terrebbe in vita quel loop. Si lasciano andare.
    _TASKS.clear()


def bind_native_input(bus: "MessageBus") -> bool:
    """Aggancia il loop corrente al bus e abilita l'ingresso nativo.

    Da chiamare **dal** loop del gateway (``GatewayContainer.run``, come i push
    di power): ``get_running_loop`` è l'unico momento in cui siamo certi di
    essere dentro quel loop — Kotlin entrerà da un thread JNI, dove non esiste.
    Ritorna ``True`` se l'ingresso è davvero aperto. Senza bus non lo è, e lo
    dice invece di lasciare un log che dichiara agganciato ciò che non lo è: in
    produzione il bus esiste sempre a questo punto (lo crea ``_build``), quindi
    un ``False`` qui è un guasto d'avvio da leggere, non un caso previsto.
    """
    global _LOOP, _BUS
    if bus is None:
        logger.warning("Native input NOT bound: no message bus")
        return False
    _LOOP = asyncio.get_running_loop()
    _BUS = bus
    logger.info("Native input bound to the gateway bus")
    return True


def _clean(text: str | None) -> str:
    """Testo normalizzato, o stringa vuota se non c'è niente da consegnare."""
    return (text or "").strip()


def _accept(text: str) -> bool:
    """``True`` se *text* è pubblicabile. Validazione sincrona, senza effetti."""
    return bool(text) and len(text) <= MAX_TEXT_CHARS


def _schedule(bus: Any, channel: str, text: str, source: str, thread: str | None) -> None:
    """Crea il task di pubblicazione. Gira **sul** loop, non sul thread JNI.

    Il task si crea qui e non nel chiamante per una ragione precisa: costruire
    la coroutine di là e poi non riuscire ad accodarla lascerebbe un oggetto
    coroutine mai awaitato — un ``RuntimeWarning`` a carico di chi non c'entra,
    e nel percorso d'errore, cioè quello che si legge peggio.
    """
    task = asyncio.get_running_loop().create_task(
        _publish(bus, channel, text, source, thread)
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _publish(bus: Any, channel: str, text: str, source: str, thread: str | None) -> None:
    """Costruisce l'``InboundMessage`` e lo mette sul bus.

    ``publish_inbound`` fa backpressure da solo (coda limitata): se il gateway è
    sommerso, questo task attende il proprio turno invece di scartare.
    """
    metadata: dict[str, Any] = {NATIVE_SOURCE_KEY: source}
    if thread:
        # Solo se c'è: una chiave a ``None`` nei metadata è una chiave che ogni
        # lettore a valle deve imparare a ignorare.
        metadata[NATIVE_THREAD_KEY] = thread
    msg = InboundMessage(
        channel=channel,
        sender_id="user",
        chat_id=NATIVE_CHAT_ID,
        content=text,
        metadata=metadata,
    )
    await bus.publish_inbound(msg)
    logger.info(
        "Native text published (source={}, thread={}, chars={})", source, thread, len(text)
    )


def on_native_text(
    text: str,
    source: str = SOURCE_NOTIFICATION,
    thread: str | None = None,
) -> bool:
    """Riceve il testo da una superficie nativa. Chiamata da Kotlin (thread JNI).

    **Mai solleva verso Kotlin** — lo stesso contratto di ``power.on_wake_tick``.
    Ritorna ``False`` quando il testo non può arrivare a destinazione: gateway
    non agganciato (ancora in avvio, loop morto, reset), sorgente sconosciuta,
    testo vuoto o oltre il tetto.

    ``True`` significa soltanto "accettato e in viaggio verso il bus": la
    risposta dell'agente arriverà dopo, sul suo tempo, e torna in superficie dal
    canale che corrisponde alla sorgente — un alert per la tendina
    (``NotificationChannel``), il fumetto per la mascotte (``FloatingChannel``).

    *thread* è il tag della notifica da cui è partita la domanda. Arriva da
    Kotlin e serve solo a tornare indietro: la risposta si posta su quel tag, e
    il discorso resta dove è cominciato. Assente o vuoto vuol dire "non lo so",
    e a valle si ricade sul filo di default. La mascotte non lo usa — la sua
    finestra è una sola e non ha fili da tenere distinti — e lo lascia a
    ``None``.

    A differenza di un tick di sveglia, un ``False`` qui **non** è un esito
    innocuo: il tick perso lo recupera il giro successivo del cron, le parole
    dell'utente no. È il chiamante Kotlin a doverci riprovare, e a dirlo se non
    ce la fa.
    """
    loop = _LOOP
    bus = _BUS
    if loop is None or bus is None or loop.is_closed():
        logger.info("Native text dropped: no gateway bound yet (source={})", source)
        return False
    channel = _CHANNEL_BY_SOURCE.get(source)
    if channel is None:
        logger.warning("Native text rejected: unknown source {!r}", source)
        return False
    clean = _clean(text)
    if not _accept(clean):
        logger.warning(
            "Native text rejected (source={}, chars={}, max={})",
            source, len(clean), MAX_TEXT_CHARS,
        )
        return False
    # Kotlin può passare ``None`` o una stringa vuota: entrambe vogliono dire
    # "nessun filo", e normalizzarle qui evita che ogni lettore a valle debba
    # distinguerle.
    thread_tag = thread.strip() if isinstance(thread, str) else None
    try:
        loop.call_soon_threadsafe(_schedule, bus, channel, clean, source, thread_tag or None)
    except RuntimeError:
        # Loop chiuso fra il controllo e la chiamata: nessun destinatario.
        logger.opt(exception=True).warning("Native text could not reach the event loop")
        return False
    return True
