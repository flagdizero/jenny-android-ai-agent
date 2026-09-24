"""Installazione dell'aggiornamento: download, verifica, commit della sessione.

Chiude l'anello che :mod:`jenny.runtime.update_check` lascia aperto. Quel modulo
sa dire *se* esiste una versione più nuova installabile su questo dispositivo;
questo prende l':class:`~jenny.runtime.update_check.UpdateInfo` che ne esce e lo
consegna al bridge Kotlin ``UpdateBridge``, che scarica l'APK verificandone
sha256 e dimensione e poi apre una sessione di ``PackageInstaller``.

Tre cose che questo modulo **non** fa, e che è importante non aspettarsi:

* **Non conferma che l'update sia installato.** Il bridge risponde ``"silent"``
  quando la sessione è stata committata *senza interazione*, non quando l'APK è
  in funzione: subito dopo il sistema uccide il processo, e l'unica prova vera è
  ripartire con un ``installed_version_code()`` più alto. Il riavvio non è
  compito nostro — lo fa ``BootReceiver`` su ``MY_PACKAGE_REPLACED``.
* **Non decide quando aggiornare.** La scelta è dell'utente: qui si esegue e
  basta. Il gate "questa versione è più nuova?" viene però ri-verificato subito
  prima di scaricare, perché fra la notifica in chat e il "sì, installala"
  possono passare ore e nel frattempo l'update può essere già stato applicato.
* **Non solleva.** Il bridge comunica i guasti con stringhe ``"error:<causa>"``
  e non lancia mai verso Python; qualunque altra eccezione (Chaquopy assente,
  Java che passa il confine, un valore inatteso) viene tradotta in un
  :class:`InstallResult` con ``state="error"``. Chi chiama è un tool dell'agente
  o una route HTTP: nessuno dei due deve vedere un traceback.

Quello che invece questo modulo **deve** fare è la validazione SSRF di
``apk_url``: l'URL arriva dal manifest, cioè da dato non fidato, e il bridge
Kotlin sa solo dire "è https". La catena di redirect viene perciò risolta qui,
hop per hop, con :func:`~jenny.security.network.validate_url_target` — v.
:func:`_resolve_apk_url`.

Fuori da Android — test, CI, gateway avviato a mano su un desktop — l'import
riesce comunque e :func:`start_install` risponde con un errore leggibile:
l'unico punto che tocca Chaquopy è :func:`_bridge`, tenuto minuscolo apposta
perché nei test sia l'unica cosa da sostituire.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from jenny.runtime import update_check
from jenny.runtime.context import get_android_context
from jenny.runtime.power import keep_awake
from jenny.runtime.update_check import UpdateInfo
from jenny.security.fetch import open_validated_stream
from jenny.security.network import validate_url_target

# Prefisso con cui il bridge Kotlin segnala un fallimento invece di lanciare.
_ERROR_PREFIX = "error:"

# Fasi osservabili da ``install_status``. "done" significa "sessione committata
# senza interazione", non "installato": vedi il docstring del modulo.
PHASE_IDLE = "idle"
PHASE_DOWNLOADING = "downloading"
PHASE_INSTALLING = "installing"
PHASE_PROMPT = "prompt"
PHASE_ERROR = "error"
PHASE_DONE = "done"

# La percentuale è deliberatamente grossolana: il bridge scarica in una sola
# chiamata bloccante e non riporta byte per byte, quindi ogni numero più fine
# sarebbe inventato. Servono a una barra che si muove tre volte, non a una stima.
_PROGRESS_IDLE = 0
_PROGRESS_DOWNLOADING = 10
_PROGRESS_INSTALLING = 70
_PROGRESS_COMPLETE = 100

_JAVA_CLASS = "com.flagdizero.jenny.UpdateBridge"

# Risoluzione della catena di redirect dell'APK: solo header, nessun byte di
# payload, quindi un timeout da "manciata di secondi" basta e avanza. Il tetto
# sugli hop è lo stesso di ``update_check`` e dello stesso ordine di quello del
# bridge: una catena più lunga non è più un CDN, è un giro strano.
_RESOLVE_TIMEOUT_S = 20.0

# Il download può durare parecchio (decine di MB su dati mobili) e il commit ha
# una finestra di 120s tutta sua: 30 minuti sono il tetto oltre il quale un
# aggiornamento è comunque da considerare perso. Stesso valore del lock di turno.
_INSTALL_WAKELOCK_TIMEOUT_S = 1800.0

_state: dict[str, Any] = {
    "phase": PHASE_IDLE,
    "progress": _PROGRESS_IDLE,
    "detail": "",
}

# Vero mentre un'installazione è in volo. È un flag e non un ``asyncio.Lock``
# per due motivi: il test-e-set qui sotto non ha ``await`` in mezzo, quindi su
# un event loop solo è già mutua esclusione; e un lock preso da un loop morto
# (il processo Android viene ucciso e ricostruito spesso) resterebbe chiuso per
# sempre, mentre questo modulo verrebbe reimportato pulito.
_active = False

_bridge_instance: Any = None


@dataclass(frozen=True)
class InstallResult:
    """Esito di una richiesta di installazione.

    ``state`` distingue i due modi in cui Android può accettare l'update —
    ``"silent"`` (committata senza chiedere niente) e ``"prompt"`` (l'installer
    di sistema è stato mostrato all'utente, che deve toccare Installa) — da
    ``"error"``, che copre sia i guasti sia i rifiuti (niente da installare,
    versione già presente, non siamo su Android).
    """

    ok: bool
    state: str  # "silent" | "prompt" | "error"
    detail: str


# --------------------------------------------------------------------------
# Stato osservabile
# --------------------------------------------------------------------------


def install_status() -> dict[str, Any]:
    """Fase corrente dell'installazione. Sincrona, senza rete, senza I/O.

    La chiama un percorso di lettura HTTP, quindi non deve costare nulla e non
    deve poter fallire. Lo stato è *sticky*: dopo un esito ``done``/``error``
    resta lì finché non parte un'altra installazione, così chi fa polling non
    perde il risultato per aver guardato un istante troppo tardi.

    **La fase è la storia completa.** Ogni esito di :func:`start_install` — i
    guasti in volo *e* i rifiuti che arrivano prima di toccare il bridge —
    finisce qui con la stessa motivazione che il chiamante riceve nell'
    :class:`InstallResult`. Non è ridondanza: i chiamanti sono due (il bottone
    della WebUI e il tool ``install_update``), la UI segue l'installazione in
    *polling* e chi non ha fatto la richiesta ha solo questa finestra. Se un
    rifiuto lasciasse la fase a ``idle``, il giro di polling successivo
    cancellerebbe il motivo appena mostrato e lascerebbe un riquadro vuoto — un
    pulsante che sembra non fare niente. Unica eccezione, e per lo stesso
    principio: un rifiuto che arriva *perché la fase è di qualcun altro* (un'
    installazione in volo, un commit già fatto) la lascia intatta.
    """
    return dict(_state)


def reset_install_state() -> None:
    """Riporta il modulo a "mai fatto niente" e scarta il bridge in cache.

    Simmetrica a ``notifier.reset_notifier_state``: serve a un gateway che
    riparte nello stesso processo (e ai test) per non ereditare né una fase
    vecchia né un'istanza Kotlin legata a un contesto ormai morto.
    """
    global _active, _bridge_instance
    _active = False
    _bridge_instance = None
    _set_phase(PHASE_IDLE, _PROGRESS_IDLE, "")


def _set_phase(phase: str, progress: int, detail: str) -> None:
    _state["phase"] = phase
    _state["progress"] = progress
    _state["detail"] = detail


def _fail(detail: str) -> InstallResult:
    """Registra il fallimento nello stato e lo restituisce al chiamante."""
    logger.error("Update install: {}", detail)
    # La progress resta quella raggiunta: dice a che punto si è rotto.
    _set_phase(PHASE_ERROR, int(_state["progress"]), detail)
    return InstallResult(False, "error", detail)


def _refuse(detail: str) -> InstallResult:
    """Rifiuta *prima* di toccare il bridge, rendendolo però osservabile.

    Non è un guasto — niente è stato avviato, e infatti il log è ``info`` e non
    ``error`` — ma è l'esito di questa richiesta, e la fase è il solo posto in
    cui un osservatore che non ha fatto la richiesta può leggerlo (v.
    :func:`install_status`). La progress torna a zero: non si è arrivati da
    nessuna parte.
    """
    logger.info("Update install refused: {}", detail)
    _set_phase(PHASE_ERROR, _PROGRESS_IDLE, detail)
    return InstallResult(False, "error", detail)


def _refuse_without_touching_phase(detail: str) -> InstallResult:
    """Rifiuta lasciando stare una fase che appartiene a un'altra richiesta.

    I due casi sono "ce n'è già una in volo" e "una è già stata committata": in
    entrambi la fase corrente (``downloading``/``installing``/``done``) è più
    vera di qualunque cosa questa richiesta possa scriverci sopra, e chi la sta
    osservando sta seguendo l'installazione giusta.
    """
    logger.info("Update install refused: {}", detail)
    return InstallResult(False, "error", detail)


# --------------------------------------------------------------------------
# Bridge Kotlin
# --------------------------------------------------------------------------


def _resolve_bridge_class() -> Any:
    """Resolve the Kotlin UpdateBridge class via Chaquopy."""
    from java import jclass  # only importable under the Chaquopy runtime

    return jclass(_JAVA_CLASS)


def _bridge() -> Any:
    """Istanza (in cache) di ``UpdateBridge``, o ``None`` fuori da Android.

    Unico punto del modulo che tocca Chaquopy. Non solleva: un ambiente senza
    runtime Android è la condizione normale nei test, non un guasto.
    """
    global _bridge_instance
    if _bridge_instance is not None:
        return _bridge_instance
    context = get_android_context()
    if context is None:
        return None
    try:
        _bridge_instance = _resolve_bridge_class()(context)
    except Exception:
        logger.opt(exception=True).error("Failed to construct UpdateBridge")
        return None
    return _bridge_instance


def _error_cause(value: str) -> str | None:
    """Causa di un ``"error:<causa>"`` del bridge, ``None`` se non è un errore."""
    if value.lower().startswith(_ERROR_PREFIX):
        return value[len(_ERROR_PREFIX) :].strip() or "unspecified"
    return None


# --------------------------------------------------------------------------
# Validazione SSRF del target di download
# --------------------------------------------------------------------------


async def _resolve_apk_url(url: str, *, client: httpx.AsyncClient | None = None) -> str:
    """Risolve la catena di redirect di *url* validando ogni hop, e ritorna l'ultimo.

    ``apk_url`` viene dal manifest, che questo progetto tratta come dato non
    fidato per scelta esplicita (v. ``update_check``). Il bridge Kotlin sa
    verificare una cosa sola — che lo schema sia https — e non ha modo di
    conoscere la policy SSRF, che vive in Python insieme alla whitelist
    configurabile. Senza questo passaggio un manifest ostile (o soltanto
    sbagliato) potrebbe far bussare il telefono a ``https://192.168.1.1/…`` o a
    un nodo Tailscale, e il messaggio d'errore che risale in chat direbbe a chi
    ha scritto il manifest se quell'indirizzo esiste: una sonda cieca con
    oracolo, che è la ragione per cui ``.agent/security.md`` impone
    ``validate_url_target`` su **ogni** richiesta uscente.

    I redirect si seguono a mano — stesso pattern di
    ``update_check._read_manifest_bytes`` e di ``agent/tools/download.py`` —
    perché ogni salto va rivalidato: l'URL pubblicato con la release è un
    ``/releases/latest/download/`` di GitHub, cioè per definizione un redirect
    verso un altro host (il CDN che serve l'asset). Delegarli a httpx
    validerebbe solo il primo.

    Al bridge si passa poi l'URL **finale**, non quello del manifest: è l'unico
    che sia stato validato fino in fondo. Il bridge continua a seguire redirect
    per conto suo, ma su un URL già terminale non dovrebbe trovarne, e la sua
    regola "solo https" resta come pavimento per il caso che ne compaia uno fra
    la nostra risoluzione e la sua GET.

    Solleva ``ValueError`` per un target rifiutato o una risposta inutilizzabile
    e le eccezioni di ``httpx`` per i guasti di rete: il chiamante le traduce in
    stato, come tutto il resto qui dentro.
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(
        timeout=_RESOLVE_TIMEOUT_S, follow_redirects=False
    )
    try:
        # ``validate_url_target`` accetta anche http: qui no, e non solo per
        # coerenza col bridge — un downgrade a metà catena toglierebbe l'unica
        # difesa contro un APK sostituito in transito che non sia l'hash (che
        # però nel manifest ce lo scrive la stessa fonte).
        async with open_validated_stream(
            client, url, validate=validate_url_target, https_only=True,
        ) as (_response, final_url):
            # Si esce dal ``with`` senza leggere il corpo: la connessione viene
            # chiusa e il download vero — decine di MB — resta tutto al bridge,
            # che lo scrive su disco calcolando l'hash in streaming. Qui
            # viaggiano solo gli header.
            return final_url
    finally:
        if owns_client:
            await client.aclose()


# --------------------------------------------------------------------------
# API pubblica
# --------------------------------------------------------------------------


async def start_install(info: UpdateInfo | None = None) -> InstallResult:
    """Scarica e installa *info*, o l'update in cache se *info* è ``None``.

    Una installazione alla volta: una seconda chiamata mentre la prima è in
    volo non avvia un secondo download, riporta la fase di quella in corso.

    Ritorna ``ok=True`` solo quando la sessione di installazione è stata
    accettata dal sistema (``"silent"``) o consegnata all'utente
    (``"prompt"``). In entrambi i casi l'app sta per essere sostituita: chi
    chiama non deve contare su altro codice dopo questo punto.

    **Il wakelock si prende qui**, non nei chiamanti, perché i chiamanti sono
    due e uno solo dei due è già coperto: il tool ``install_update`` gira dentro
    ``AgentLoop._dispatch`` sotto il lock di turno, ma il pulsante "Installa
    ora" delle impostazioni entra da una route HTTP e non ha niente sopra di sé.
    È anche la strada normale — l'utente apre la WebUI dal portatile via
    Tailscale mentre il telefono è in un cassetto a schermo spento — e senza
    lock la CPU si sospende in mezzo a un download di decine di MB, che il
    ``READ_TIMEOUT_MS`` del bridge finisce per far scadere. Metterlo qui invece
    che nei due chiamanti significa anche che non può essere dimenticato da un
    terzo punto d'ingresso che nascerà dopo.
    """
    global _active
    if _active:
        # Nessun ``await`` fra il test e l'assegnazione: è già atomico.
        return _refuse_without_touching_phase(
            f"An installation is already in progress (phase: {_state['phase']})."
        )
    if _state["phase"] == PHASE_DONE:
        return _refuse_without_touching_phase(
            "An update has already been committed on this run; the app is about "
            "to be replaced and restarted."
        )
    _active = True
    try:
        # Il tag è distinto da "turn" e da "cron" di proposito: chiamato dal
        # tool si annida sul lock di turno senza confondersi con lui, e in
        # ``dumpsys power`` si legge che è un aggiornamento a tenere accesa la
        # CPU. Il rilascio è nel ``finally`` di ``keep_awake``, quindi copre
        # anche i rami d'errore; e sul ramo silenzioso, dove il processo viene
        # ucciso senza arrivarci mai, il lock muore col processo — il
        # PowerManager rilascia i lock del binder morto — oltre ad avere già una
        # scadenza propria passata all'acquire. Nessun percorso lascia la CPU
        # accesa più a lungo di quanto la lasci oggi.
        async with keep_awake("update", timeout_s=_INSTALL_WAKELOCK_TIMEOUT_S):
            return await _run_install(info)
    except Exception as exc:
        # Include tutto ciò che può attraversare il confine Chaquopy: il
        # chiamante è un tool dell'agente, non deve mai vedere un traceback.
        logger.opt(exception=True).error("Update install failed unexpectedly")
        return _fail(f"unexpected error: {type(exc).__name__}: {exc}")
    finally:
        _active = False


async def _run_install(info: UpdateInfo | None) -> InstallResult:
    resolved = info if info is not None else update_check.cached_update()
    if resolved is None:
        return _refuse(
            "No update is available to install: nothing has been found by the "
            "last update check."
        )

    installed = update_check.installed_version_code()
    if resolved.version_code <= installed:
        # Fra l'annuncio in chat e il "sì" possono passare ore.
        return _refuse(
            f"{resolved.version_name} (versionCode {resolved.version_code}) is "
            f"not newer than the installed versionCode {installed}: it has "
            f"already been applied, or the manifest is stale."
        )

    bridge = _bridge()
    if bridge is None:
        return _refuse(
            "Self-update is only available inside the Android app (no Android "
            "runtime here)."
        )

    _set_phase(
        PHASE_DOWNLOADING,
        _PROGRESS_DOWNLOADING,
        f"Downloading {resolved.version_name} ({resolved.size} bytes)",
    )

    # Prima di consegnare un URL non fidato al bridge: v. ``_resolve_apk_url``.
    # Sta dentro la fase "downloading" e non fra i rifiuti perché la rete la
    # tocca davvero, e perché un fallimento qui deve lasciare la progress a 10 —
    # dice a chi guarda che non si è fermato prima di provarci.
    try:
        download_url = await _resolve_apk_url(resolved.apk_url)
    except (httpx.HTTPError, ValueError, OSError) as exc:
        return _fail(f"download failed: {exc}")

    # Metodo bloccante: mai sul loop. Niente ``wait_for`` attorno: annullare
    # l'attesa non ucciderebbe il thread, lascerebbe un download vivo che
    # scrive sullo stesso file su cui un ritentativo ripartirebbe. I timeout
    # veri (connect/read, e il commit) li applica il bridge.
    downloaded = str(
        await asyncio.to_thread(
            bridge.downloadApk,
            download_url,
            resolved.sha256,
            int(resolved.size),
        )
        or ""
    ).strip()

    cause = _error_cause(downloaded)
    if cause is not None:
        return _fail(f"download failed: {cause}")
    if not downloaded:
        return _fail("download failed: the bridge returned no APK path")

    _set_phase(
        PHASE_INSTALLING,
        _PROGRESS_INSTALLING,
        f"Installing {resolved.version_name}",
    )
    outcome = str(await asyncio.to_thread(bridge.installApk, downloaded) or "").strip()

    cause = _error_cause(outcome)
    if cause is not None:
        return _fail(f"install failed: {cause}")

    if outcome == "silent":
        detail = (
            f"{resolved.version_name} was committed without asking the user. "
            f"Android is replacing the app now: the process will be killed and "
            f"restarted, and the running conversation ends here."
        )
        _set_phase(PHASE_DONE, _PROGRESS_COMPLETE, detail)
        logger.info("Update install: {} committed silently", resolved.version_name)
        return InstallResult(True, "silent", detail)

    if outcome == "prompt":
        detail = (
            f"Android requires confirmation for {resolved.version_name}. The "
            f"system installer was shown (or posted as a notification if the "
            f"app was in the background): the update completes when the user "
            f"taps Install."
        )
        _set_phase(PHASE_PROMPT, _PROGRESS_COMPLETE, detail)
        logger.info("Update install: {} awaiting user confirmation", resolved.version_name)
        return InstallResult(True, "prompt", detail)

    return _fail(f"install returned an unexpected value: {outcome!r}")
