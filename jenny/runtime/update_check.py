"""Controllo degli aggiornamenti in-app: manifest remoto, stato, gate di rollout.

Questo modulo è il *nucleo* del sistema di update: sa dire se esiste una versione
più nuova installabile su **questo** dispositivo e ricorda che cosa è già stato
annunciato all'utente. Non scarica e non installa nulla — quello è compito del
layer di installazione, che consuma :class:`UpdateInfo`.

Tre principi, tutti conseguenza di dove gira il codice:

* **Il manifest è dato non fidato.** Arriva dalla rete, quindi ogni campo viene
  validato per tipo e range prima di essere creduto, i campi sconosciuti sono
  ignorati, e un manifest che dichiara uno ``schema`` più alto di quello che
  questo client conosce viene scartato in blocco: un client vecchio non sa che
  cosa significhino i campi nuovi, e non proporre niente è l'unico esito onesto.
* **Nessun errore risale al cron.** Rete assente, DNS che non risolve, JSON
  malformato, disco pieno: tutto si traduce in ``None`` e una riga di log. Un
  job di sistema che solleva è un job che nel log sembra un guasto dell'app.
* **Lo stato non è config.** Vive in ``<workspace>/update_state.json`` con
  scrittura atomica, perché è stato di runtime e non una scelta dell'utente
  (``config.json`` si tocca solo via ``config/store.py::mutate``). Un file di
  stato corrotto non deve impedire nulla: si riparte da stato vuoto.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from loguru import logger

from jenny.runtime.context import get_android_context
from jenny.runtime.update_manifest import DEFAULT_MANIFEST_URL
from jenny.security.fetch import open_validated_stream, read_capped
from jenny.security.network import validate_url_target
from jenny.utils.path import atomic_write

if TYPE_CHECKING:
    from jenny.config.schema import Config

# ``DEFAULT_MANIFEST_URL`` è definito in ``runtime/update_manifest.py``, che non
# importa niente: così ``config/schema.py`` può usarlo come default di
# ``updates.manifest_url`` senza tirarsi dentro httpx all'avvio. Importandolo
# qui resta anche raggiungibile come ``update_check.DEFAULT_MANIFEST_URL``, dove
# lo cerca chi lavora sull'updater — una definizione sola, due nomi per dirla.

# Versione del formato del manifest che questo client sa leggere.
MANIFEST_SCHEMA = 1
# Versione del formato del file di stato locale.
STATE_SCHEMA = 1
STATE_FILENAME = "update_state.json"

_TIMEOUT_S = 20.0
# Il manifest è una manciata di campi: oltre questa soglia non è più il nostro
# manifest, e leggerlo tutto per scoprirlo sarebbe il vero costo.
_MAX_MANIFEST_BYTES = 64 * 1024
# Tetto di sanità sulla dimensione dichiarata dell'APK (l'installer ha il suo).
_MAX_APK_BYTES = 500 * 1024 * 1024
_MAX_URL_CHARS = 2048
_MAX_VERSION_NAME_CHARS = 64
_MAX_SUMMARY_CHARS = 400
_ROLLOUT_FULL = 100
_DEFAULT_LANGUAGE = "it"

# Fuori da Android non esiste un APK installato di cui leggere il ``versionCode``
# (test, CI, gateway avviato a mano su un desktop). Zero significa "più vecchio
# di qualunque release": il confronto resta definito e testabile, e la
# conseguenza pratica — un manifest valido risulta sempre più nuovo — non tocca
# la produzione, dove Android è l'unico target di runtime.
_HOST_FALLBACK_VERSION_CODE = 0

# Su Android, invece, lo zero sarebbe la risposta peggiore possibile. Il
# PackageManager può non rispondere (servizio non ancora pronto, package in
# aggiornamento, API mancante su una ROM), e degradare a zero renderebbe
# *qualunque* manifest "più nuovo dell'installato": sparirebbero insieme il
# filtro sulle versioni e il ri-controllo che l'installer fa prima di applicare
# (``update_install._run_install``), cioè l'unico guard contro il downgrade.
# Il tetto di un ``versionCode`` Android (int32) ha l'effetto opposto: nessun
# manifest può superarlo, quindi finché il PackageManager tace non si propone e
# non si installa niente. Fail-closed, e con una riga di log a WARNING perché
# questo caso non è normale.
_UNKNOWN_ANDROID_VERSION_CODE = 2**31 - 1

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class UpdateInfo:
    """Una release installabile su questo dispositivo, già validata e filtrata."""

    version_code: int
    version_name: str
    apk_url: str
    sha256: str
    size: int
    notes_url: str
    summary: str
    critical: bool


# --------------------------------------------------------------------------
# Versione installata
# --------------------------------------------------------------------------


def _android_version_code() -> int | None:
    """Legge il ``versionCode`` dell'APK installato dal PackageManager.

    Unico punto del modulo che tocca il runtime Android, e per questo tenuto
    minuscolo: nei test è l'unica cosa da sostituire.

    Distingue i due modi di non sapere, perché hanno esiti opposti: ``None``
    significa "non siamo su Android" e vale zero a valle, mentre un
    PackageManager che non risponde vale ``_UNKNOWN_ANDROID_VERSION_CODE``, che
    fa rifiutare ogni aggiornamento finché non torna a rispondere.
    """
    context = get_android_context()
    if context is None:
        return None
    try:
        info = context.getPackageManager().getPackageInfo(context.getPackageName(), 0)
        try:
            # getLongVersionCode() è API 28; il minSdk dell'app è 26, quindi il
            # campo deprecato resta il fallback su Oreo.
            return int(info.getLongVersionCode())
        except Exception:
            return int(info.versionCode)
    except Exception:
        # Le eccezioni Java arrivano qui come Exception generiche: catturarle
        # tutte è l'unico modo di non far fallire un job per una API mancante.
        logger.opt(exception=True).warning(
            "Could not read the installed versionCode on Android: refusing every "
            "update until the PackageManager answers again"
        )
        return _UNKNOWN_ANDROID_VERSION_CODE


def installed_version_code() -> int:
    """``versionCode`` dell'APK in esecuzione.

    Fuori da Android vale 0 (v. ``_HOST_FALLBACK_VERSION_CODE``). Su Android,
    se il PackageManager non risponde, vale un numero più alto di qualunque
    release pubblicabile: il chiamante non deve gestire un caso in più, e il
    caso in più si comporta da solo nel modo prudente.
    """
    code = _android_version_code()
    if code is not None and code >= 0:
        return code
    return _HOST_FALLBACK_VERSION_CODE


# --------------------------------------------------------------------------
# Stato su disco
# --------------------------------------------------------------------------


def _state_path() -> Path:
    from jenny.config.paths import get_workspace_path

    return get_workspace_path() / STATE_FILENAME


def _read_state() -> dict[str, Any]:
    """Stato persistito, o un dict vuoto se manca/è illeggibile/è di un'altra era."""
    try:
        raw = _state_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, RuntimeError, UnicodeDecodeError) as exc:
        logger.warning("Update state unreadable, starting empty: {}", exc)
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        logger.warning("Update state is not valid JSON, starting empty: {}", exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("Update state is not a JSON object, starting empty")
        return {}
    if data.get("schema") != STATE_SCHEMA:
        # Vale anche al contrario (stato scritto da una versione più nuova dopo
        # un downgrade): l'install_id si perde e il bucket di rollout cambia,
        # che è meno grave che interpretare male dei campi che non conosciamo.
        logger.warning(
            "Update state schema {!r} is not {}, starting empty",
            data.get("schema"), STATE_SCHEMA,
        )
        return {}
    return data


def _write_state(state: dict[str, Any]) -> None:
    """Persiste lo stato in modo atomico. Un fallimento non è mai fatale."""
    state["schema"] = STATE_SCHEMA
    try:
        atomic_write(_state_path(), json.dumps(state, ensure_ascii=False, indent=2))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("Could not persist the update state: {}", exc)


def install_id() -> str:
    """UUID4 stabile di questa installazione, generato una volta e persistito.

    Serve solo al gate di rollout: è un numero casuale locale, non un
    identificatore che lasci il dispositivo.
    """
    state = _read_state()
    value = state.get("install_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    new_id = str(uuid.uuid4())
    state["install_id"] = new_id
    _write_state(state)
    return new_id


def _state_int(key: str) -> int | None:
    """Intero letto dallo stato, ``None`` se assente o di un tipo non credibile.

    Un campo che una versione precedente non scriveva legge ``None`` senza
    bisogno di migrazioni, ed è lo stesso esito di un file manomesso.
    """
    value = _read_state().get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def last_check_ms() -> int | None:
    """Timestamp (ms) dell'ultimo controllo riuscito o fallito, ``None`` se mai."""
    return _state_int("last_check_ms")


def last_success_ms() -> int | None:
    """Timestamp (ms) dell'ultimo controllo **riuscito**, ``None`` se mai.

    Riuscito significa: manifest scaricato e validato. Serve perché
    :func:`last_check_ms` da solo non distingue "ho guardato e non c'è niente di
    nuovo" da "sono mesi che non riesco a guardare" — segna anche i tentativi
    falliti, per progetto. Su un telefono headless, dove nessuno legge il
    logcat, la distanza fra i due timestamp è l'unico segnale che l'updater è
    morto in silenzio.
    """
    return _state_int("last_success_ms")


def notified_version_code() -> int | None:
    """Ultimo ``version_code`` già annunciato in chat, ``None`` se nessuno.

    Non è nella lista minima dell'API, ma senza di essa il chiamante che deve
    decidere "l'ho già detto?" (il dispatcher del cron) dovrebbe leggere lo
    stato a mano.
    """
    return _state_int("notified_code")


def mark_notified(version_code: int) -> None:
    """Segna *version_code* come già annunciato: non si disturba due volte."""
    state = _read_state()
    state["notified_code"] = int(version_code)
    _write_state(state)


# --------------------------------------------------------------------------
# Validazione del manifest (dato non fidato)
# --------------------------------------------------------------------------


def _reject(reason: str) -> dict[str, Any] | None:
    """Scarta il manifest dicendolo: un update mai proposto è già abbastanza muto."""
    logger.warning("Update manifest rejected: {}", reason)
    return None


def _as_int(value: Any) -> int | None:
    """Intero stretto: ``bool`` è un ``int`` in Python ma non è un numero qui."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _as_text(value: Any, max_chars: int) -> str:
    """Stringa ripulita e troncata; stringa vuota se il valore non è testo."""
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_chars]


def validate_manifest(data: Any) -> dict[str, Any] | None:
    """Normalizza un manifest non fidato, o ``None`` se non è credibile.

    La severità è deliberatamente asimmetrica. I campi che governano una
    *decisione* o la *sicurezza* — ``schema``, ``version_code``, ``apk_url``,
    ``sha256``, ``size``, ``min_supported_code``, ``rollout``, ``critical`` —
    devono essere del tipo giusto o l'intero manifest viene scartato: su questi
    "indovinare" significa proporre l'APK sbagliato o saltare il gate. I campi
    di sola *presentazione* (``notes_url``, i sommari) degradano invece a vuoto:
    un sommario scritto male non è un motivo per non annunciare una fix di
    sicurezza.
    """
    if not isinstance(data, dict):
        return _reject("payload is not a JSON object")

    schema = _as_int(data.get("schema"))
    if schema is None or schema < 1:
        return _reject("missing or invalid 'schema'")
    if schema > MANIFEST_SCHEMA:
        return _reject(
            f"schema {schema} is newer than the supported {MANIFEST_SCHEMA}"
        )

    version_code = _as_int(data.get("version_code"))
    if version_code is None or version_code <= 0:
        return _reject("missing or invalid 'version_code'")

    version_name = _as_text(data.get("version_name"), _MAX_VERSION_NAME_CHARS)
    if not version_name:
        return _reject("missing or invalid 'version_name'")

    apk_url = _as_text(data.get("apk_url"), _MAX_URL_CHARS)
    if not apk_url.lower().startswith("https://"):
        return _reject("'apk_url' must be an https URL")

    sha256 = _as_text(data.get("sha256"), 64).lower()
    if not _SHA256_RE.match(sha256):
        return _reject("'sha256' must be 64 hex characters")

    size = _as_int(data.get("size"))
    if size is None or not 0 < size <= _MAX_APK_BYTES:
        return _reject("missing or out-of-range 'size'")

    min_supported_code = _as_int(data.get("min_supported_code", 0))
    if min_supported_code is None or min_supported_code < 0:
        return _reject("invalid 'min_supported_code'")

    rollout = _as_int(data.get("rollout", _ROLLOUT_FULL))
    if rollout is None:
        return _reject("invalid 'rollout'")
    if not 0 <= rollout <= _ROLLOUT_FULL:
        logger.warning("Update manifest: 'rollout' {} clamped into 0..100", rollout)
        rollout = min(max(rollout, 0), _ROLLOUT_FULL)

    critical = data.get("critical", False)
    if not isinstance(critical, bool):
        return _reject("invalid 'critical'")

    notes_url = _as_text(data.get("notes_url"), _MAX_URL_CHARS)
    if notes_url and not notes_url.lower().startswith("https://"):
        notes_url = ""

    # Solo i campi noti sopravvivono: quello che il manifest porta in più non
    # entra nello stato e non raggiunge nessun consumatore.
    return {
        "schema": schema,
        "version_code": version_code,
        "version_name": version_name,
        "apk_url": apk_url,
        "sha256": sha256,
        "size": size,
        "notes_url": notes_url,
        "summary_it": _as_text(data.get("summary_it"), _MAX_SUMMARY_CHARS),
        "summary_en": _as_text(data.get("summary_en"), _MAX_SUMMARY_CHARS),
        "min_supported_code": min_supported_code,
        "rollout": rollout,
        "critical": critical,
    }


# --------------------------------------------------------------------------
# Gate di rollout
# --------------------------------------------------------------------------


def _rollout_bucket(install: str, version_code: int) -> int:
    """Bucket 0..99 deterministico per (installazione, versione).

    Dipende **anche** dalla versione: con il solo ``install_id`` lo stesso
    dispositivo resterebbe per sempre in testa o per sempre in coda a ogni
    rollout graduale.
    """
    digest = hashlib.sha256(f"{install}:{version_code}".encode()).hexdigest()
    return int(digest[:8], 16) % 100


def _rollout_allows(
    install: str, version_code: int, *, rollout: int, critical: bool
) -> bool:
    """True se questa installazione è inclusa nell'ondata corrente.

    Lo zero viene prima di tutto, ``critical`` compreso. ``rollout: 0`` non è
    un'ondata vuota: è il freno d'emergenza documentato in
    ``docs/contribute/publish-a-release.md``, la manovra con cui si ripubblica
    il manifest per fermare una release rotta. Se ``critical`` lo scavalcasse,
    il freno mancherebbe esattamente sulle release per cui serve — la stessa
    pagina prescrive di riparare una build rotta pubblicando la fix con
    ``--critical``, e una fix pubblicata in fretta è anche quella che più
    facilmente va fermata a sua volta.

    Sopra lo zero ``critical`` salta invece lo scaglionamento: una fix di
    sicurezza non si consegna a ondate, e ogni dispositivo che può prenderla se
    la vede offrire subito.
    """
    if rollout <= 0:
        return False
    if critical or rollout >= _ROLLOUT_FULL:
        return True
    return _rollout_bucket(install, version_code) < rollout


# --------------------------------------------------------------------------
# Rete
# --------------------------------------------------------------------------


async def _read_manifest_bytes(client: httpx.AsyncClient, url: str) -> bytes:
    """GET con redirect seguiti a mano, validando ogni hop (SSRF) e il volume.

    Il ciclo è :func:`jenny.security.fetch.open_validated_stream`, lo stesso
    del download dell'APK e di ``download_file``: i redirect non si delegano a
    httpx perché ogni hop va rivalidato — ``/releases/latest/download/`` di
    GitHub è per definizione un redirect verso un altro host. Https a **ogni**
    salto, dal 24/09/2026: prima solo l'URL di partenza, e un redirect verso
    http consegnava in chiaro il manifest che dice quale APK installare.
    """
    async with open_validated_stream(
        client, url, validate=validate_url_target, https_only=True,
    ) as (response, _final_url):
        return await read_capped(
            response, _MAX_MANIFEST_BYTES, f"manifest exceeds {_MAX_MANIFEST_BYTES} bytes",
        )


async def _fetch_manifest(
    url: str, *, client: httpx.AsyncClient | None = None
) -> dict[str, Any] | None:
    """Scarica e valida il manifest. ``None`` per qualunque motivo, mai un raise."""
    if not url.lower().startswith("https://"):
        logger.warning("Update manifest URL must be https, got {!r}", url)
        return None

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=False)
    try:
        payload = await _read_manifest_bytes(client, url)
    except (httpx.HTTPError, ValueError, OSError) as exc:
        # Nessuna rete è la condizione normale su un telefono, non un guasto.
        logger.info("Update check: could not fetch the manifest ({})", exc)
        return None
    finally:
        if owns_client:
            await client.aclose()

    try:
        data = json.loads(payload)
    except ValueError as exc:
        return _reject(f"not valid JSON ({exc})")
    return validate_manifest(data)


# --------------------------------------------------------------------------
# API pubblica
# --------------------------------------------------------------------------


def _pick_summary(manifest: dict[str, Any], language: str | None) -> str:
    """Sommario nella lingua configurata, con fallback su inglese."""
    code = (language or _DEFAULT_LANGUAGE).strip().lower()[:2]
    localized = manifest.get(f"summary_{code}")
    if isinstance(localized, str) and localized:
        return localized
    fallback = manifest.get("summary_en")
    return fallback if isinstance(fallback, str) else ""


def _to_info(manifest: dict[str, Any], language: str | None) -> UpdateInfo:
    return UpdateInfo(
        version_code=manifest["version_code"],
        version_name=manifest["version_name"],
        apk_url=manifest["apk_url"],
        sha256=manifest["sha256"],
        size=manifest["size"],
        notes_url=manifest["notes_url"],
        summary=_pick_summary(manifest, language),
        critical=manifest["critical"],
    )


def _installable(
    manifest: dict[str, Any], *, install: str, language: str | None
) -> UpdateInfo | None:
    """Applica i gate (versione, soglia minima, rollout) a un manifest validato."""
    installed = installed_version_code()
    version_code = manifest["version_code"]
    if version_code <= installed:
        logger.debug(
            "Update check: manifest version {} is not newer than {}",
            version_code, installed,
        )
        return None

    min_supported = manifest["min_supported_code"]
    if min_supported and installed < min_supported:
        # Il manifest dichiara che da una build così vecchia non si aggiorna in
        # diretta: proporlo lo stesso significherebbe promettere un'installazione
        # che fallisce.
        logger.info(
            "Update check: {} requires versionCode >= {}, installed is {}",
            manifest["version_name"], min_supported, installed,
        )
        return None

    if not _rollout_allows(
        install,
        version_code,
        rollout=manifest["rollout"],
        critical=manifest["critical"],
    ):
        logger.info(
            "Update check: {} is at {}% rollout, this install is not in the wave",
            manifest["version_name"], manifest["rollout"],
        )
        return None

    return _to_info(manifest, language)


async def check_for_update(config: "Config") -> UpdateInfo | None:
    """Interroga il manifest remoto, persiste lo stato e ritorna l'update proponibile.

    Ritorna non-``None`` **solo** se il manifest è valido, la sua versione è più
    nuova di quella installata e il gate di rollout passa. Rete assente, manifest
    illeggibile o versione già installata danno ``None``: mai un'eccezione, perché
    il chiamante è un job di sistema.

    Non guarda ``config.updates.enabled``: quel flag decide se il job periodico
    viene registrato (``runtime/container.py``), non se un controllo esplicito
    può essere fatto.
    """
    try:
        return await _check_for_update(config)
    except Exception:
        logger.opt(exception=True).warning("Update check failed unexpectedly")
        return None


async def _check_for_update(config: "Config") -> UpdateInfo | None:
    url = (config.updates.manifest_url or "").strip() or DEFAULT_MANIFEST_URL
    language = config.agents.defaults.language

    manifest = await _fetch_manifest(url)

    # Lo stato si aggiorna anche quando il fetch fallisce: "quando ho guardato
    # l'ultima volta" è proprio l'informazione che serve dopo un fallimento.
    now_ms = int(time.time() * 1000)
    install = install_id()
    state = _read_state()
    state["install_id"] = install
    state["last_check_ms"] = now_ms
    state["language"] = language
    if manifest is not None:
        # ``last_success_ms`` invece si muove solo qui, con il manifest in mano
        # e già validato: è la differenza fra i due timestamp a dire che
        # l'updater sta provando senza riuscirci (v. :func:`last_success_ms`).
        state["last_success_ms"] = now_ms
        state["latest"] = manifest
    _write_state(state)

    if manifest is None:
        return None
    return _installable(manifest, install=install, language=language)


def cached_update(language: str | None = None) -> UpdateInfo | None:
    """Ultimo update proponibile secondo lo stato su disco. Sincrono, zero rete.

    Riapplica gli stessi gate di :func:`check_for_update` — versione installata,
    soglia minima, rollout — così un update già installato smette di comparire
    senza aspettare il controllo successivo. Il manifest memorizzato viene
    rivalidato: il file di stato è locale, ma è pur sempre un file.

    *language* è opzionale: senza argomento vale la lingua registrata durante
    l'ultimo controllo (e ``it`` se non c'è ancora stato un controllo), così i
    chiamanti che non hanno un ``Config`` sottomano non devono ricaricarlo.
    """
    state = _read_state()
    latest = state.get("latest")
    if not isinstance(latest, dict):
        return None
    manifest = validate_manifest(latest)
    if manifest is None:
        return None
    install = state.get("install_id")
    if not isinstance(install, str) or not install.strip():
        return None
    stored_language = state.get("language")
    resolved = language or (
        stored_language if isinstance(stored_language, str) else None
    )
    return _installable(manifest, install=install.strip(), language=resolved)
