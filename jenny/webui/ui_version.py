"""L'impronta della WebUI che questo gateway serve, e chi la serve.

Una pagina aperta prima di un aggiornamento continua col JS vecchio: quando il
gateway riparte il socket si ricollega da solo, ma la pagina resta quella di
prima. Il 21/10/2026 il browser del Mac, collegato da remoto al gateway del
Titan, mostrava la bacheca senza le etichette dei cron, mentre la WebView del
telefono, ripartita col processo, le aveva. La pagina confronta queste due voci
di ``/webui/bootstrap`` a ogni riconnessione (``shared/update-watch.js``) e si
ricarica quando servono file diversi da quelli con cui è partita.

- ``ui_version``: un hash dei file che la pagina esegue, letti dal pacchetto
  (gli stessi byte che ``ws_http._serve_static`` serve). **Il contenuto e non il
  numero di versione**: il 21/10 due APK diverse avevano lo stesso
  ``versionCode``. Dei file in ``assets/vendor/`` conta solo il nome, che porta
  la versione (``mermaid@10``): sono il grosso dei byte e non cambiano mai sotto
  lo stesso nome. Si calcola in un thread all'avvio, una volta per processo:
  sul telefono leggere 2,5 MB dal pacchetto non sta sul loop. Finché non è
  pronta vale ``""``.
- ``boot``: l'id di questo processo. Dice alla pagina che ha avuto ``""`` se il
  gateway a cui si è ricollegata è ancora lo stesso (allora l'impronta è quella
  dei suoi file) o un altro (e non sa dire se i file sono cambiati).
"""

from __future__ import annotations

import hashlib
import threading
import uuid

from loguru import logger

BOOT_ID = uuid.uuid4().hex[:12]

_ACTIVE_SUFFIXES = (".html", ".js", ".css", ".json")
_VENDOR_PREFIX = "assets/vendor/"

_lock = threading.Lock()
_started = False
_value = ""


def compute() -> str:
    """L'impronta, calcolata adesso. Sincrona e con I/O: va in un thread."""
    from jenny.utils.android_assets import read_asset, ui_sources

    digest = hashlib.blake2b(digest_size=12)
    for rel, (package, name) in sorted(ui_sources().items()):
        digest.update(rel.encode("utf-8") + b"\0")
        if rel.startswith(_VENDOR_PREFIX) or not rel.endswith(_ACTIVE_SUFFIXES):
            continue
        body = read_asset(package, name) or b""
        digest.update(len(body).to_bytes(8, "big") + body)
    return digest.hexdigest()


def _run() -> None:
    global _value
    try:
        value = compute()
    except Exception:  # noqa: BLE001 — senza impronta la pagina non si ricarica, niente di peggio
        logger.opt(exception=True).warning("WebUI fingerprint not computed")
        return
    _value = value
    logger.debug("WebUI fingerprint {}", value)


def warm() -> None:
    """Avvia il calcolo, una volta per processo, in un thread a parte."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_run, name="ui-version", daemon=True).start()


def current() -> str:
    """L'impronta, o ``""`` finché il calcolo non ha finito."""
    return _value
