"""Wakelock e risvegli programmati (anti-doze), solo Android.

Unico strato Python che parla col ``PowerBridge`` Kotlin. Stesso pattern di
``jenny/runtime/location.py``: la classe nativa è risolta via Chaquopy
(``jclass``), istanziata una volta e cachata; fuori da Android (desktop, CI)
ogni funzione è un no-op che ritorna il default sicuro, senza sollevare.

Perché serve: un foreground service tiene vivo il *processo*, non la CPU. A
schermo spento il device sospende e i timer asyncio non scattano — il loop
riparte minuti (o ore) dopo, i cron slittano e da fuori sembra che Jenny si sia
piantata. Solo un ``PARTIAL_WAKE_LOCK`` impedisce la sospensione della CPU.

Modalità (``config.power.keep_awake``):

* ``"turns"`` — il lock per-turno di questo modulo è attivo: si acquisisce
  attorno al lavoro vero e si rilascia subito dopo.
* ``"always"`` — il lock a livello di servizio copre già tutto: il lock
  per-turno viene saltato, sarebbe un doppione.
* ``"off"`` — nessun lock (comportamento pre-0.6.6).

Il refcount per-tag esiste perché i turni si annidano (agente → tool → ssh):
senza, l'uscita del blocco interno rilascerebbe il lock mentre quello esterno
sta ancora lavorando. Acquisizione alla prima entrata, rilascio all'ultima
uscita.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from loguru import logger

from jenny.runtime.chaquopy_bridge import BridgeCache
from jenny.runtime.context import get_android_context

_BRIDGE = BridgeCache("com.flagdizero.jenny.PowerBridge")

# Serializza refcount e chiamate acquire/release: due turni che partono insieme
# sullo stesso tag non devono entrambi vedere "contatore a zero" e acquisire.
_STATE_LOCK = asyncio.Lock()

# tag -> quante ``keep_awake()`` annidate lo stanno tenendo.
_REFCOUNTS: dict[str, int] = {}
# Tag per cui il bridge ha confermato l'acquisizione (sottoinsieme di _REFCOUNTS).
_HELD: set[str] = set()

_DEFAULT_TIMEOUT_S = 300.0
# Tetto duro sul timeout passato al bridge. Un wakelock mai rilasciato è
# l'esito peggiore possibile — batteria a zero senza spiegazione — quindi ogni
# acquire porta con sé una scadenza: se Python muore a metà turno (crash del
# loop, processo ucciso), è l'OS a rilasciare il lock allo scadere del timeout.
# Nessun percorso di questo modulo può produrre un lock eterno.
_MAX_TIMEOUT_S = 3600.0

# Le chiamate JNI sono rapide ma restano I/O verso un altro runtime: girano in
# thread e con un timeout, così un bridge bloccato non blocca l'event loop.
_CALL_TIMEOUT_S = 10.0

# Request code della sveglia del cron. Deve stare SOTTO 9000: da 9000 in su i
# request code sono riservati a Kotlin (9001 = auto-recovery del service, 9002 =
# watchdog, 9003 = sveglia-sveglia di ``AlarmClockFallback``) e riusarne uno
# significherebbe che un ``cancel_wake`` del cron smonta in silenzio una delle
# reti di sicurezza che devono girare anche quando Python non c'è più.
WAKE_REQUEST_CODE_CRON = 8001

# Loop ed evento su cui recapitare i tick di sveglia che arrivano da Kotlin.
# Sono globali di modulo, non stato di un oggetto, perché il chiamante è
# ``GatewayService.deliverWakeTick`` via Chaquopy: entra da un thread di lavoro
# JNI e ha in mano solo il nome del modulo, nessun riferimento a un'istanza.
_WAKE_LOOP: asyncio.AbstractEventLoop | None = None
_WAKE_EVENT: asyncio.Event | None = None


def reset_power_state() -> None:
    """Azzera bridge cachato, refcount e tag tenuti a un nuovo start del gateway.

    Simmetrico a ``location.reset_location_state`` — chiamato da
    ``android_entry.run_gateway`` prima del nuovo event loop (una
    ``asyncio.Lock`` si lega al loop su cui è awaitata la prima volta).

    Non prova a rilasciare i lock ereditati: il bridge del loop morto non è più
    utilizzabile e il timeout passato all'acquire ha già garantito che l'OS li
    rilasci da solo. Qui si azzera solo la contabilità, così un gateway fresco
    non parte credendo di tenere un lock che non tiene.
    """
    global _STATE_LOCK, _WAKE_LOOP, _WAKE_EVENT
    _BRIDGE.reset()
    _STATE_LOCK = asyncio.Lock()
    _REFCOUNTS.clear()
    _HELD.clear()
    # Loop ed evento del giro precedente sono legati a un loop morto: un
    # ``call_soon_threadsafe`` su quello solleverebbe (o peggio, finirebbe in
    # una coda che nessuno drena). Chi vuole i tick li ri-aggancia con
    # ``bind_wake_loop`` a loop nuovo avviato.
    _WAKE_LOOP = None
    _WAKE_EVENT = None


def _resolve_bridge_class() -> Any:
    """Risolve la classe Kotlin ``PowerBridge`` via Chaquopy.

    Resta una funzione di modulo, e non una chiamata diretta a
    ``_BRIDGE.resolve_class``: è il seam su cui i test montano il finto
    bridge fuori dal telefono.
    """
    return _BRIDGE.resolve_class()


async def _get_bridge(context: Any) -> Any:
    """Costruisce o ritorna l'istanza cachata di ``PowerBridge`` (thread-safe)."""
    return await _BRIDGE.get(context, resolve=_resolve_bridge_class)


async def _call(method: str, *args: Any) -> bool:
    """Invoca un metodo del bridge e ne ritorna l'esito booleano.

    Mai solleva: fuori da Android, senza bridge o su qualunque errore ritorna
    ``False``. Nessun chiamante deve dover proteggere una chiamata di power
    management — se fallisce, si degrada al comportamento senza wakelock.
    """
    context = get_android_context()
    if context is None:
        return False
    try:
        bridge = await _get_bridge(context)
        result = await asyncio.wait_for(
            asyncio.to_thread(getattr(bridge, method), *args),
            timeout=_CALL_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("PowerBridge.{} failed", method)
        return False
    return bool(result)


def _power_config() -> Any:
    """Sezione ``config.power`` letta da config **fresca**, o ``None``.

    Import differito (stesso idioma di ``ssh_transport.configured_hosts``): il
    modulo non deve tirarsi dietro il config a import time, e cambiare la
    modalità dalle impostazioni deve avere effetto senza riavviare il gateway.
    """
    try:
        from jenny.config.loader import load_config

        return getattr(load_config(), "power", None)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("Could not read power config")
        return None


def _keep_awake_mode() -> str:
    """Modalità corrente (``off``/``turns``/``always``). Mai solleva."""
    mode = getattr(_power_config(), "keep_awake", None)
    return mode if isinstance(mode, str) else "turns"


async def _acquire(tag: str, timeout_s: float) -> None:
    """Prende il refcount e, alla prima entrata, il lock sul bridge."""
    async with _STATE_LOCK:
        count = _REFCOUNTS.get(tag, 0) + 1
        _REFCOUNTS[tag] = count
        if count > 1:
            return  # già tenuto da un livello esterno
        timeout_ms = int(max(1.0, min(float(timeout_s), _MAX_TIMEOUT_S)) * 1000)
        if await _call("acquire", tag, timeout_ms):
            _HELD.add(tag)


async def _release(tag: str) -> None:
    """Molla il refcount e, all'ultima uscita, rilascia il lock sul bridge."""
    async with _STATE_LOCK:
        count = _REFCOUNTS.get(tag, 0) - 1
        if count > 0:
            _REFCOUNTS[tag] = count
            return
        _REFCOUNTS.pop(tag, None)
        if tag in _HELD:
            _HELD.discard(tag)
            await _call("release", tag)


@asynccontextmanager
async def keep_awake(
    tag: str,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> AsyncIterator[bool]:
    """Tiene la CPU sveglia per la durata del blocco. Valore yieldato: se il
    wakelock è effettivamente tenuto.

    No-op (ma sempre un context manager valido) fuori da Android e quando
    ``config.power.keep_awake`` non è ``"turns"``: con ``"always"`` il lock di
    servizio copre già tutto, con ``"off"`` non si prende alcun lock.

    Il rilascio è in ``finally``: qualunque cosa succeda dentro il blocco —
    eccezione, cancellazione del task — il refcount torna giù.
    """
    tracked = get_android_context() is not None and _keep_awake_mode() == "turns"
    if tracked:
        await _acquire(tag, timeout_s)
    try:
        yield tag in _HELD
    finally:
        if tracked:
            await _release(tag)


async def apply_service_lock() -> bool:
    """Allinea il wakelock di servizio a ``config.power``. Mai solleva.

    Chiamata una volta sola all'avvio del gateway (``GatewayContainer.run``).
    Con ``keep_awake == "always"`` accende un ``PARTIAL_WAKE_LOCK`` che copre
    tutta la vita del ``GatewayService``; in ogni altra modalità lo spegne, così
    un passaggio da ``always`` a ``turns`` fatto dalle impostazioni ha effetto
    al riavvio invece di lasciare in giro un lock che nessuno ricorda.

    È **Python** a leggere il config e a dirlo a Kotlin, non il contrario: il
    parsing di ``config.json`` deve esistere in un linguaggio solo, altrimenti
    la stessa impostazione finisce interpretata due volte e prima o poi le due
    letture divergono. Il rilascio invece è tutto di Kotlin, in due punti:
    ``GatewayService.onDestroy`` quando il thread del gateway è già morto, e
    l'uscita del thread del gateway stesso negli altri casi — un service
    distrutto con il thread ancora vivo non deve togliere il lock a chi lo
    vuole, perché a ri-acquisirlo c'è solo il prossimo ``run_gateway``.
    """
    cfg = _power_config()
    mode = getattr(cfg, "keep_awake", None)
    enabled = mode == "always"
    rotate_min = getattr(cfg, "wakelock_rotate_min", 0)
    if not isinstance(rotate_min, int) or rotate_min < 0:
        rotate_min = 0
    result = await _call("setServiceLock", enabled, rotate_min)
    if enabled:
        logger.info("Service wakelock requested (rotate={}min, ok={})", rotate_min, result)
    return result


async def apply_watchdog_config() -> bool:
    """Spinge ``config.power.watchdog_*`` al watchdog Kotlin. Mai solleva.

    Chiamata una volta all'avvio del gateway (``GatewayContainer.run``), subito
    dopo ``apply_service_lock`` e per lo stesso motivo: il config lo legge
    **solo** Python, Kotlin riceve valori già decisi (vedi il docstring di
    ``apply_service_lock``).

    Si chiama anche quando il watchdog è disabilitato, e non è uno spreco:
    ``setWatchdog(False, …)`` è l'unico modo di smontare una catena di sveglie
    armata da un avvio precedente, che altrimenti continuerebbe a girare per
    sempre — le sveglie vivono nell'AlarmManager di sistema, non nel nostro
    processo, e nessuno le disarma al cambio di impostazione.
    """
    cfg = _power_config()
    enabled = bool(getattr(cfg, "watchdog_enabled", True))
    interval_min = getattr(cfg, "watchdog_interval_min", 15)
    if not isinstance(interval_min, int) or interval_min <= 0:
        interval_min = 15
    result = await _call("setWatchdog", enabled, interval_min)
    logger.info(
        "Watchdog config pushed (enabled={}, interval={}min, ok={})",
        enabled,
        interval_min,
        result,
    )
    return result


async def apply_alarm_clock_config() -> bool:
    """Spinge ``config.power.alarm_clock_fallback`` a Kotlin. Mai solleva.

    Terza e ultima spinta della sequenza di avvio (``GatewayContainer.run``),
    accanto a ``apply_service_lock`` e ``apply_watchdog_config`` e per lo stesso
    motivo: ``config.json`` lo legge **solo** Python, Kotlin riceve valori già
    decisi.

    Il flag è l'unico di questa famiglia che esiste per un motivo estetico e
    non tecnico: una ``setAlarmClock`` in coda accende l'icona della sveglia
    nella barra di stato di parecchie ROM, ed è il prezzo dell'unica rete che
    nessun gestore energetico osa sopprimere (vedi ``AlarmClockFallback``).

    Come per il watchdog, si chiama anche a rete disattivata e non è uno
    spreco: la sveglia vive nell'AlarmManager di sistema, sopravvive alla morte
    del gateway e nessuno la disarma al cambio di impostazione. Solo un push
    esplicito di ``False`` la **cancella** — non riarmarla soltanto lascerebbe
    in piedi l'anello già in coda, e con lui l'icona che l'utente voleva far
    sparire, fino a otto ore dopo.
    """
    enabled = bool(getattr(_power_config(), "alarm_clock_fallback", True))
    result = await _call("setAlarmClockFallback", enabled)
    logger.info("Alarm clock fallback config pushed (enabled={}, ok={})", enabled, result)
    return result


def alarms_available() -> bool:
    """``True`` se ha senso programmare sveglie: cioè solo sotto Android.

    Sincrona di proposito: i chiamanti (``CronService._arm_timer``) sono codice
    sincrono che deve decidere *senza awaitare* se creare o no un task, e su
    desktop/CI la risposta è "no" — così là non nasce nemmeno il task, e il
    comportamento resta identico a quello pre-sveglie.
    """
    return get_android_context() is not None


def alarm_driven_cron_enabled() -> bool:
    """``True`` se il cron deve appoggiarsi alle sveglie di sistema.

    Due condizioni, in quest'ordine: essere su Android (fuori non esiste alcun
    AlarmManager da chiamare) e ``config.power.alarm_driven_cron``. Il
    controllo sul contesto viene per primo di proposito, così su desktop/CI non
    si legge nemmeno il config — il percorso resta esattamente quello di prima
    delle sveglie, senza task né letture in più.
    """
    if not alarms_available():
        return False
    return bool(getattr(_power_config(), "alarm_driven_cron", True))


def bind_wake_loop() -> asyncio.Event:
    """Aggancia il loop corrente ai tick di sveglia e ritorna l'evento da attendere.

    Da chiamare **dal** loop del gateway (``CronService.start``): il
    riferimento al loop si prende qui, con ``get_running_loop``, perché è
    l'unico momento in cui siamo certi di essere dentro quel loop. Kotlin
    entrerà da un thread JNI, dove ``get_running_loop`` non esiste e ``Event``
    non è toccabile in sicurezza.

    Ri-agganciare sostituisce l'evento precedente: un solo consumatore per
    volta, l'ultimo che si registra vince. Un evento orfano non fa danni, resta
    semplicemente senza nessuno che lo setti.
    """
    global _WAKE_LOOP, _WAKE_EVENT
    _WAKE_LOOP = asyncio.get_running_loop()
    _WAKE_EVENT = asyncio.Event()
    return _WAKE_EVENT


def on_wake_tick() -> bool:
    """Sveglia il gateway: chiamata da Kotlin, da un thread di lavoro.

    Punto d'ingresso di ``GatewayService.deliverWakeTick`` (Chaquopy
    ``getModule("jenny.runtime.power").callAttr("on_wake_tick")``). Gira sul
    thread JNI, **non** sul loop: l'unica cosa lecita da lì è
    ``loop.call_soon_threadsafe``, che accoda il ``set`` dell'evento dentro al
    loop e lo sveglia. Toccare l'``asyncio.Event`` direttamente da questo thread
    sembrerebbe funzionare e perderebbe i risvegli, perché il ``set`` non
    passerebbe dal selector su cui il loop è bloccato.

    ``False`` — mai un'eccezione verso Kotlin — quando non c'è nessun loop
    agganciato: il gateway sta ancora partendo (o è appena morto). Il tick va
    perso di proposito, perché la scadenza mancata la recupera
    ``CronService.start`` al primo giro di timer.
    """
    loop = _WAKE_LOOP
    event = _WAKE_EVENT
    if loop is None or event is None or loop.is_closed():
        return False
    try:
        loop.call_soon_threadsafe(event.set)
    except RuntimeError:
        # Loop chiuso fra il controllo e la chiamata: nessun destinatario.
        logger.opt(exception=True).debug("Wake tick could not reach the event loop")
        return False
    return True


async def is_battery_exempt() -> bool:
    """``True`` se l'app è esente dalle ottimizzazioni batteria."""
    return await _call("isBatteryExempt")


async def can_schedule_exact_alarms() -> bool:
    """``True`` se l'app può programmare alarm esatti (permesso Android 12+)."""
    return await _call("canScheduleExactAlarms")


# Tag del wakelock di servizio, lo stesso di ``PowerBridge.SERVICE_LOCK_TAG``.
# Duplicato di proposito: Python non ha modo di leggere una costante Kotlin
# senza costruire il bridge, e la stringa non cambia.
SERVICE_LOCK_TAG = "gateway"


async def is_wakelock_held() -> bool:
    """``True`` se un ``PARTIAL_WAKE_LOCK`` è tenuto adesso.

    Guarda entrambi i lock, perché sono presi da due parti diverse: quelli
    per-turno li conta questo modulo (``_HELD``), quello di servizio vive in
    Kotlin e va chiesto al bridge. Rispondere solo sul primo direbbe "no" a
    telefono in modalità ``always``, che è il momento in cui la risposta conta
    di più.
    """
    if _HELD:
        return True
    return await _call("isHeld", SERVICE_LOCK_TAG)


async def schedule_wake(at_ms: int, request_code: int) -> bool:
    """Programma un risveglio all'epoch ``at_ms``. ``False`` se non è stato possibile."""
    return await _call("scheduleWake", int(at_ms), int(request_code))


async def cancel_wake(request_code: int) -> bool:
    """Annulla il risveglio programmato con quel ``request_code``."""
    return await _call("cancelWake", int(request_code))
