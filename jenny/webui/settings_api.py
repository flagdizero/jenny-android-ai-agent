"""Settings REST helpers for the WebUI HTTP surface.

The WebSocket channel owns transport/authentication. This module owns the
settings payload shape and the allowlisted config mutations exposed to WebUI.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Callable
from typing import Any

import httpx
from loguru import logger

from jenny import __version__
from jenny.agent.token_usage import token_usage_payload
from jenny.channels.http_utils import (
    FALSY_VALUES,
    TRUTHY_VALUES,
    QueryParams,
    parse_flag,
    query_first,
)
from jenny.config import store
from jenny.config.loader import get_config_path, load_config
from jenny.config.schema import KEEP_AWAKE_MODES, Config, FloatingConfig
from jenny.config.tool_schemas import AndroidWebSearchConfig
from jenny.providers.opencode import catalog_headers
from jenny.providers.tls import CaBundleError, build_ssl_context
from jenny.security.workspace_access import workspace_sandbox_status
from jenny.security.workspace_policy import _safe_expanduser
from jenny.session.keys import UNIFIED_SESSION_KEY
from jenny.utils.helpers import validate_timezone_name

# Fasi che il layer di installazione può dichiarare. Serve a non far arrivare
# alla UI una stringa che nessuna traduzione conosce (`i18n.t` stamperebbe la
# chiave grezza in mezzo alla pagina).
_UPDATE_PHASES = frozenset(
    {"idle", "downloading", "installing", "prompt", "error", "done"}
)
# Esiti di ``start_install``. "silent" = sessione committata senza interazione
# (il processo verrà ucciso e ripartirà), "prompt" = tocca all'utente
# confermare nell'installer di sistema.
_INSTALL_STATES = frozenset({"silent", "prompt", "error"})

_UPDATE_DEFAULTS: dict[str, Any] = {
    "current_code": None,
    "latest": None,
    "latest_code": None,
    "update_available": False,
    "critical": False,
    "notes_url": None,
    "summary": None,
    "last_check": None,
    "last_success": None,
}


def _last_success_ms(module: Any) -> int | None:
    """Ultimo controllo *riuscito*, letto in modo tollerante da *module*.

    ``last_success_ms`` è nato dopo ``last_check_ms``: manca su una build che
    non lo espone ancora e ritorna ``None`` su uno stato scritto prima che il
    campo esistesse. Entrambi i casi valgono "ignoto", e ignoto è ``None`` —
    la pagina resta disegnabile, come per tutto il resto di questo payload.

    Serve un accesso a parte proprio perché non può stare nell'``import`` degli
    altri tre: un ``ImportError`` lì dentro porterebbe via anche ``current_code``
    e ``last_check``, che invece ci sono.
    """
    fn = getattr(module, "last_success_ms", None)
    if not callable(fn):
        return None
    try:
        value = fn()
    except Exception:
        logger.opt(exception=True).debug("last_success_ms is unavailable")
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _version_payload() -> dict[str, Any]:
    """Versione installata e, se lo stato dell'updater ne conosce una, la nuova.

    Sincrono e senza rete: ``cached_update`` rilegge soltanto
    ``<workspace>/update_state.json``, scritto dal job periodico. Chiamare qui
    ``check_for_update`` significherebbe far pagare un giro di rete a ogni
    apertura delle impostazioni.

    ``last_check`` e ``last_success`` viaggiano insieme e sono due cose diverse:
    il primo è scritto a *ogni* tentativo, riuscito o no, il secondo solo quando
    il manifest è stato davvero letto. È la loro distanza a dire che il
    meccanismo è morto — con il solo ``last_check`` un manifest irraggiungibile
    da mesi resta indistinguibile da "sei aggiornato".

    Tutto è dentro un ``try``, e il degrado è verso i default: senza workspace
    (test, gateway avviato a mano), con lo stato illeggibile o con un updater
    che solleva, restano i campi nuovi a ``None``/``False`` e ``current``
    sempre presente. La pagina impostazioni non è il posto dove un updater rotto
    può togliere all'utente anche il resto.
    """
    payload: dict[str, Any] = {"current": __version__, **_UPDATE_DEFAULTS}
    try:
        from jenny.runtime import update_check

        payload["current_code"] = update_check.installed_version_code()
        payload["last_check"] = update_check.last_check_ms()
        payload["last_success"] = _last_success_ms(update_check)
        info = update_check.cached_update()
    except Exception:
        logger.opt(exception=True).debug("Update state unavailable for the settings payload")
        return payload

    if info is None:
        return payload

    payload.update({
        "latest": info.version_name,
        "latest_code": info.version_code,
        "update_available": True,
        "critical": bool(info.critical),
        # Il manifest degrada i campi di presentazione a stringa vuota; per la
        # UI "assente" è più utile di "vuoto ma presente".
        "notes_url": info.notes_url or None,
        "summary": info.summary or None,
    })
    return payload


def _load_update_install() -> Any:
    """Import tollerante del layer di installazione (``runtime/update_install``).

    Modulo separato e importato al bisogno: l'installazione tocca il
    PackageInstaller di Android, e la pagina impostazioni deve poter aprirsi su
    una build in cui quel layer non c'è. Chi lo chiama riceve un 503 parlante
    invece di un ImportError a caso nel dispatch.
    """
    try:
        from jenny.runtime import update_install
    except ImportError as exc:
        raise WebUISettingsError(
            "the in-app updater is not available in this build",
            status=503,
        ) from exc
    return update_install


def _clamp_progress(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return min(max(int(value), 0), 100)


def _as_detail(value: Any) -> str:
    return value.strip()[:400] if isinstance(value, str) else ""


def update_status_payload() -> dict[str, Any]:
    """Stato dell'installazione in corso, normalizzato per il polling della UI.

    La normalizzazione non è paranoia gratuita: la UI sceglie la stringa da
    mostrare *dalla* fase, quindi una fase che le traduzioni non conoscono
    finirebbe stampata com'è. Sconosciuta o mancante vale ``idle``, che è anche
    lo stato in cui la UI smette di seguire il processo.
    """
    module = _load_update_install()
    raw = module.install_status()
    data = raw if isinstance(raw, dict) else {}
    phase = data.get("phase")
    if phase not in _UPDATE_PHASES:
        phase = "idle"
    return {
        "phase": phase,
        "progress": _clamp_progress(data.get("progress")),
        "detail": _as_detail(data.get("detail")),
    }


async def start_update_install() -> dict[str, Any]:
    """Avvia il download+installazione dell'update in cache.

    Ritorna l'esito *applicativo* (``ok``/``state``/``detail``) con HTTP 200
    anche quando fallisce: "l'installer ha detto di no" è un'informazione da
    mostrare all'utente nello stesso riquadro in cui poi arriva il progresso,
    non un errore di trasporto. Restano 4xx/5xx per updater assente e guasti
    inattesi.
    """
    module = _load_update_install()
    result = await module.start_install()
    state = getattr(result, "state", None)
    if state not in _INSTALL_STATES:
        state = "error"
    return {
        "ok": bool(getattr(result, "ok", False)),
        "state": state,
        "detail": _as_detail(getattr(result, "detail", "")),
    }


# Un controllo alla volta. La rotta fa rete, e "l'utente pigia due volte" è il
# caso normale su un bottone che non dà riscontro immediato: senza questo, due
# richieste al manifest si sovrappongono e riscrivono lo stesso file di stato.
# Chi arriva secondo non si mette in coda — aspettare in silenzio un controllo
# che sta già girando non aggiunge niente: riceve ``busy`` e lo stato corrente.
_update_check_lock = asyncio.Lock()


def reset_update_check_state() -> None:
    """Rimpiazza il lock del controllo aggiornamenti prima di un nuovo loop.

    Simmetrico ai ``reset_*`` dei bridge; chiamato da
    ``android_entry.run_gateway``. Qui il lock resta preso attraverso una
    chiamata di rete: se il loop muore mentre il manifest è in volo, il lock
    può sopravvivere già acquisito, e la guardia ``locked()`` di
    :func:`run_update_check` risponderebbe ``busy`` per sempre — bottone
    "controlla aggiornamenti" morto fino al force-stop dell'app.
    """
    global _update_check_lock
    _update_check_lock = asyncio.Lock()


def _check_outcome(version: dict[str, Any]) -> str:
    """``"ok"`` solo se l'ultimo tentativo ha davvero raggiunto il manifest.

    Il confronto è fra i due timestamp che l'updater scrive: ``last_check`` a
    ogni tentativo, ``last_success`` solo quando il manifest è stato letto.
    Su una build che non espone ancora quel secondo segnale la domanda non ha
    risposta, e allora si tace: annunciare "non riuscito" senza saperlo sarebbe
    peggio del silenzio, perché è l'avviso stesso a perdere credito.
    """
    try:
        from jenny.runtime import update_check
    except ImportError:
        return "ok"
    if not callable(getattr(update_check, "last_success_ms", None)):
        return "ok"
    last_success = version.get("last_success")
    last_check = version.get("last_check")
    if not isinstance(last_success, int) or not isinstance(last_check, int):
        return "failed"
    return "ok" if last_success >= last_check else "failed"


async def run_update_check() -> dict[str, Any]:
    """Controllo aggiornamenti chiesto a mano, con il payload versione fresco.

    Fa **rete**, e per questo è una rotta a sé invece di un ramo di
    ``settings_payload()``: quel payload si costruisce a ogni apertura delle
    impostazioni, e pagarci un giro HTTP ogni volta sarebbe un costo nascosto.

    ``check_for_update`` ignora di proposito ``config.updates.enabled`` — quel
    flag decide se il job periodico viene registrato, non se un controllo
    esplicito può essere fatto — e non solleva mai: rete assente o manifest
    illeggibile tornano ``None``. Il fallimento va quindi dedotto dallo stato,
    ed è esattamente quello che fa ``_check_outcome``.

    La risposta porta sempre il payload versione aggiornato: la UI deve poter
    riflettere il nuovo stato senza ricaricarsi.
    """
    if _update_check_lock.locked():
        return {"status": "busy", "version": _version_payload()}
    async with _update_check_lock:
        try:
            from jenny.runtime.update_check import check_for_update
        except ImportError as exc:
            raise WebUISettingsError(
                "the updater is not available in this build",
                status=503,
            ) from exc
        await check_for_update(load_config())
        version = _version_payload()
    return {"status": _check_outcome(version), "version": version}


# Il tool android_web supporta solo Bing (android_web.py rifiuta ogni altro
# valore): il motore è quindi una costante, non un flag selezionabile.
_ANDROID_WEB_SEARCH_ENGINE = "bing"

WELCOME_TEMPLATES: dict[str, str] = {
    "it": "Ciao sono {bot_name} e da oggi vivo sul tuo smartphone, molto piacere!",
    "en": "Hi, I'm {bot_name} and from today I live on your smartphone. Nice to meet you!",
}

# Tupla e non `set`: la UI ne fa un menu', e l'ordine di un `set` non e'
# garantito fra due esecuzioni. Il controllo `in` costa uguale su due voci.
_CONTEXT_WINDOW_TOKEN_OPTIONS = (65_536, 262_144)
_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# Vocabolario accettato dal layer provider (``openai_compat_provider._build_kwargs``
# normalizza "minimum" in "minimal"). La select della WebUI ne espone un
# sottoinsieme; l'API accetta tutto ciò che il provider sa gestire.
_REASONING_EFFORT_VALUES = frozenset(
    {"none", "minimal", "minimum", "low", "medium", "high"}
)


class _Unset:
    """Sentinella per "campo assente dalla richiesta" dove ``None`` è un valore."""


_UNSET = _Unset()


class WebUISettingsError(ValueError):
    """User-facing settings validation failure."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _query_first_alias(query: QueryParams, *names: str) -> str | None:
    """Il primo valore fra più nomi accettati (snake_case e camelCase)."""
    for name in names:
        value = query_first(query, name)
        if value is not None:
            return value
    return None


# ── Applicatori generici ─────────────────────────────────────────────────────
# Vivevano in ``worker_settings``, che li aveva generalizzati per i suoi tre
# lavoratori mentre qui sotto nove rami li riscrivevano a mano — e uno di quei
# nove aveva i bound del campo **copiati** invece di derivati, cioè un secondo
# posto da aggiornare a ogni cambio di schema. Stanno qui perché
# ``worker_settings`` importa già da questo modulo: il verso opposto sarebbe un
# ciclo.

def _bounds(model: type, attr: str) -> tuple[int | None, int | None]:
    """I bound che lo schema impone a *attr*, letti dallo schema stesso."""
    field = model.model_fields[attr]
    low = getattr(field, "ge", None)
    high = getattr(field, "le", None)
    return (int(low) if low is not None else None, int(high) if high is not None else None)


def _parse_bool(raw: str, field: str) -> bool:
    value = raw.strip().lower()
    if value in TRUTHY_VALUES:
        return True
    if value in FALSY_VALUES:
        return False
    raise WebUISettingsError(f"{field} must be a boolean")


def _parse_int(raw: str, field: str, model: type, attr: str) -> int:
    """Un intero dentro i bound dello schema, o un rifiuto che **nomina il range**.

    Il range nel messaggio non e' cortesia: e' l'unico modo in cui chi ha appena
    sforato scopre quale sia il valore ammesso, e la prosa che spiega *perche'*
    quel tetto esista sta nei file i18n accanto al campo.
    """
    try:
        value = int(raw.strip())
    except ValueError:
        raise WebUISettingsError(f"{field} must be a whole number") from None
    low, high = _bounds(model, attr)
    if (low is not None and value < low) or (high is not None and value > high):
        span = f"{low}" if high is None else f"{low}–{high}"
        raise WebUISettingsError(f"{field} must be between {span}")
    return value


def _apply_int(
    query: QueryParams,
    target: Any,
    attr: str,
    model: type,
    *names: str,
) -> bool:
    """Scrive un intero su *target* se la query lo porta. ``True`` = cambiato."""
    raw = _query_first_alias(query, *names)
    if raw is None:
        return False
    value = _parse_int(raw, names[0], model, attr)
    if int(getattr(target, attr)) == value:
        return False
    setattr(target, attr, value)
    return True


def _apply_bool(query: QueryParams, target: Any, attr: str, *names: str) -> bool:
    raw = _query_first_alias(query, *names)
    if raw is None:
        return False
    value = _parse_bool(raw, names[0])
    if bool(getattr(target, attr)) == value:
        return False
    setattr(target, attr, value)
    return True


def _apply_str(
    query: QueryParams,
    target: Any,
    attr: str,
    *names: str,
    required: bool = True,
    validate: Callable[[str], str | None] | None = None,
) -> bool:
    """Scrive una stringa strippata su *target* se la query la porta.

    ``True`` = valore cambiato. *required* rifiuta la stringa vuota (che per
    quasi tutti questi campi significa «cancellalo», e non è ciò che l'utente
    intendeva); *validate* ritorna un messaggio d'errore, o ``None`` se il
    valore va bene, ed è come timezone e nome del provider fanno il loro
    controllo senza che questa funzione li conosca.
    """
    raw = _query_first_alias(query, *names)
    if raw is None:
        return False
    value = raw.strip()
    if required and not value:
        raise WebUISettingsError(f"{names[0]} is required")
    if validate is not None and (err := validate(value)):
        raise WebUISettingsError(err)
    if getattr(target, attr) == value:
        return False
    setattr(target, attr, value)
    return True


def _resolve_env_placeholders(value: str | None) -> str | None:
    if not value:
        return None
    missing = False

    def replace(match: re.Match[str]) -> str:
        nonlocal missing
        env_value = os.environ.get(match.group(1))
        if env_value is None:
            missing = True
            return ""
        return env_value

    resolved = _ENV_REF_RE.sub(replace, value).strip()
    if missing and not resolved:
        return None
    return resolved or None


def _model_id_from_row(row: Any) -> str | None:
    if isinstance(row, str):
        return row.strip() or None
    if not isinstance(row, dict):
        return None
    for key in ("id", "name", "model"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _model_context_window(row: Any) -> int | None:
    if not isinstance(row, dict):
        return None
    for key in (
        "context_window",
        "context_length",
        "max_context_length",
        "max_model_len",
        "max_input_tokens",
    ):
        value = row.get(key)
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, float) and value > 0:
            return int(value)
    return None


def _model_row_payload(row: Any) -> dict[str, Any] | None:
    model_id = _model_id_from_row(row)
    if not model_id:
        return None
    label: str | None = None
    owned_by: str | None = None
    if isinstance(row, dict):
        raw_label = row.get("display_name") or row.get("label") or row.get("name")
        if isinstance(raw_label, str) and raw_label.strip() and raw_label.strip() != model_id:
            label = raw_label.strip()
        raw_owner = row.get("owned_by") or row.get("owner") or row.get("organization")
        if isinstance(raw_owner, str) and raw_owner.strip():
            owned_by = raw_owner.strip()
    return {
        "id": model_id,
        "label": label,
        "owned_by": owned_by,
        "context_window": _model_context_window(row),
    }


def _extract_model_rows(body: Any) -> list[dict[str, Any]]:
    raw_rows = body.get("data") if isinstance(body, dict) else body
    if not isinstance(raw_rows, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_row in raw_rows:
        row = _model_row_payload(raw_row)
        if row is None or row["id"] in seen:
            continue
        seen.add(row["id"])
        rows.append(row)
    return rows


def provider_models_payload(query: QueryParams) -> dict[str, Any]:
    """Fetch an OpenAI-compatible provider's model list for Settings.

    The result is advisory only: users can always type a custom model id. This
    helper deliberately avoids mutating config so probing model lists never
    changes runtime behavior.
    """
    provider_name = (query_first(query, "provider") or "").strip()
    if not provider_name:
        raise WebUISettingsError("provider is required")

    config = load_config()
    provider_config = None
    for p in config.providers.providers:
        if p.name == provider_name:
            provider_config = p
            break
    if provider_config is None:
        api_key = (query_first(query, "api_key") or "").strip()
        if not api_key:
            raise WebUISettingsError("unknown provider")
        provider_format = (query_first(query, "format") or "openai_compat").strip()
        api_base = (query_first(query, "api_base") or "").strip()
        from jenny.config.schema import ProviderConfig
        provider_config = ProviderConfig(
            name=provider_name,
            format=provider_format,
            api_key=api_key,
            api_base=api_base,
        )

    base_payload: dict[str, Any] = {
        "provider": provider_config.name,
        "label": provider_config.name,
        "models": [],
        "model_count": 0,
        "message": None,
        "fetched_at": time.time(),
    }

    api_base = _resolve_env_placeholders(provider_config.api_base)
    if provider_config.name == "openai" and not api_base:
        api_base = "https://api.openai.com/v1"
    if provider_config.format == "anthropic" and not api_base:
        api_base = "https://api.anthropic.com"
    if not api_base:
        return {
            **base_payload,
            "status": "missing_api_base",
            "message": "Configure an API base URL to load models.",
        }

    api_key = _resolve_env_placeholders(provider_config.api_key)
    override_key = (query_first(query, "api_key") or "").strip()
    if override_key:
        api_key = override_key
    if not api_key:
        return {
            **base_payload,
            "status": "not_configured",
            "message": "Configure this provider before loading models.",
        }

    # La sonda deve fidarsi di cio' di cui si fida il provider vero: senza
    # questo, chi configura la propria CA la vede funzionare in chat e continua a
    # trovare il catalogo modelli vuoto, con un errore TLS che sembra un secondo
    # guasto invece dello stesso.
    try:
        ssl_context = build_ssl_context(
            provider_config.ca_bundle, provider_name=provider_config.name
        )
    except CaBundleError as exc:
        return {**base_payload, "status": "error", "message": str(exc)}

    # Identificazione, non sessione: la lista dei modelli non è una
    # conversazione, quindi niente ``x-opencode-session`` (v.
    # ``providers/opencode.py``). Fuori da OpenCode il dict è vuoto e questi
    # header restano quelli di prima.
    headers = {"Accept": "application/json", **catalog_headers(api_base)}
    if provider_config.format == "anthropic":
        # Messages API: auth via x-api-key e /v1/models (la base non include /v1).
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        base = api_base.rstrip("/")
        models_url = base + ("/models" if base.endswith("/v1") else "/v1/models")
    else:
        headers["Authorization"] = f"Bearer {api_key}"
        models_url = f"{api_base.rstrip('/')}/models"

    # Deliberatamente **senza** ``validate_url_target``, e va scritto perché chi
    # verifica "ogni richiesta in uscita è controllata?" trova questa e non può
    # distinguere una decisione da una dimenticanza.
    #
    # La destinazione è ``providers.<nome>.apiBase``, cioè un valore che l'utente
    # ha messo in Impostazioni per parlare col *suo* provider — e su questo
    # dispositivo quel provider può legittimamente essere un llama.cpp in
    # loopback o un server di modelli in LAN, che è esattamente ciò che l'SSRF
    # blocca. La stessa deroga la prendono i due provider LLM quando chiamano
    # l'endpoint di chat: bloccare qui e non lì proteggerebbe da niente e
    # romperebbe i modelli locali.
    #
    # Cosa resta a delimitarla: la rotta è dietro il token (``/api/``), non
    # segue redirect, ha un timeout stretto, e la risposta viene solo letta per
    # estrarne una lista di nomi di modelli.
    try:
        response = httpx.get(
            models_url,
            headers=headers,
            timeout=10.0,
            follow_redirects=False,
            verify=ssl_context or True,
        )
        response.raise_for_status()
        rows = _extract_model_rows(response.json())
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {401, 403}:
            return {
                **base_payload,
                "status": "not_configured",
                "message": "The provider rejected the configured credential.",
            }
        return {
            **base_payload,
            "status": "error",
            "message": f"Model list request failed with HTTP {status}.",
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {
            **base_payload,
            "status": "error",
            "message": f"Could not load models: {exc}",
        }

    return {
        **base_payload,
        "status": "available",
        "models": rows,
        "model_count": len(rows),
    }


def _parse_context_window_tokens(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        raise WebUISettingsError("context_window_tokens must be an integer") from None
    if parsed not in _CONTEXT_WINDOW_TOKEN_OPTIONS:
        # Dalla tupla, non scritto a mano: una terza opzione non deve lasciare un
        # messaggio che ne nomina due.
        allowed = " or ".join(str(v) for v in _CONTEXT_WINDOW_TOKEN_OPTIONS)
        raise WebUISettingsError(f"context_window_tokens must be {allowed}")
    return parsed


def _parse_max_tokens(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value.strip())
    except ValueError:
        raise WebUISettingsError("max_tokens must be an integer") from None
    if parsed < 1:
        raise WebUISettingsError("max_tokens must be at least 1")
    return parsed


def _parse_temperature(value: str | None) -> float | None:
    if value is None:
        return None
    # Un ``input type=number`` su locale italiano può arrivare con la virgola
    # come separatore decimale; rifiutarlo trasformerebbe un campo legittimo in
    # un errore che l'utente non sa spiegare.
    normalized = value.strip().replace(",", ".")
    try:
        parsed = float(normalized)
    except ValueError:
        raise WebUISettingsError("temperature must be a number") from None
    if not 0.0 <= parsed <= 2.0:
        raise WebUISettingsError("temperature must be between 0 and 2")
    return parsed


def _parse_reasoning_effort(value: str | None) -> str | None | _Unset:
    """Valida l'effort, distinguendo "non inviato" da "azzerato".

    La select manda la stringa vuota per l'opzione "—", che significa "lascia
    decidere al provider" e va scritta come ``None``. ``_UNSET`` è il caso in
    cui il campo non è nella richiesta: senza questa distinzione un update di un
    altro campo azzererebbe l'effort come effetto collaterale.
    """
    if value is None:
        return _UNSET
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized not in _REASONING_EFFORT_VALUES:
        allowed = ", ".join(sorted(_REASONING_EFFORT_VALUES))
        raise WebUISettingsError(f"reasoning_effort must be one of: {allowed}")
    return normalized


def _parse_keep_awake(value: str | None) -> str | None:
    """Valida ``power.keepAwake`` prima che arrivi al config.

    Lo schema, in caricamento, ricade in silenzio su ``"turns"`` quando trova un
    valore che non conosce: giusto per un file scritto a mano, sbagliato qui.
    Una richiesta della WebUI con un modo inventato è un bug del client, e
    accettarla scriverebbe "turns" mostrando poi un valore che l'utente non ha
    scelto. Meglio un 400 esplicito.
    """
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized not in KEEP_AWAKE_MODES:
        allowed = ", ".join(KEEP_AWAKE_MODES)
        raise WebUISettingsError(f"keep_awake must be one of: {allowed}")
    return normalized


def _is_first_run(config: Any = None) -> bool:
    """Return True if no provider has been configured yet."""
    config_path = get_config_path()
    if not config_path.exists():
        return True
    try:
        if config is None:
            config = load_config()
        return len(config.providers.providers) == 0
    except Exception:
        return True


# Mostrato al posto di una chiave troppo corta per essere mascherata senza
# rivelarne una parte sproporzionata. Non è vuoto perché una chiave corta
# esiste: i nostri docs raccomandano il segnaposto "EMPTY" per i server locali
# (5 caratteri), e prima la UI la annunciava come "(no key)" — chi seguiva le
# istruzioni pensava di aver sbagliato qualcosa.
_SHORT_KEY_HINT = "••••"


def _mask_api_key(api_key: str | None) -> str:
    """Suggerimento offuscato mostrato dalla UI al posto della chiave vera.

    Stringa vuota solo se la chiave è assente: una chiave corta viene
    annunciata come presente, senza rivelarne il contenuto.
    """
    if not api_key:
        return ""
    if len(api_key) > 8:
        return api_key[:4] + "..." + api_key[-4:]
    return _SHORT_KEY_HINT


def _provider_list_simple(config: Any = None) -> list[dict[str, Any]]:
    """Return configured providers as a simple list for the UI."""
    if config is None:
        config = load_config()
    result: list[dict[str, Any]] = []
    for p in config.providers.providers:
        api_key_hint = _mask_api_key(p.api_key)
        result.append({
            "name": p.name,
            "format": p.format,
            "api_key_hint": api_key_hint,
            "api_base": p.api_base,
            "configured": bool(p.api_key or p.api_base),
            "api_type": p.api_type,
            "ca_bundle": p.ca_bundle,
        })
    return result


async def save_onboarding(
    data: dict[str, Any],
    *,
    session_manager: Any | None = None,
    onboarding_event: Any | None = None,
) -> dict[str, Any]:
    """Save onboarding configuration."""
    provider_name = (data.get("provider_name") or "").strip()
    provider_format = (data.get("format") or "openai_compat").strip()
    api_key = (data.get("api_key") or "").strip()
    api_base = (data.get("api_base") or "").strip()
    model = (data.get("model") or "").strip()
    bot_name = (data.get("bot_name") or "Jenny").strip()
    bot_icon = (data.get("bot_icon") or "✿").strip()

    if not provider_name:
        raise WebUISettingsError("provider_name is required")
    if not model:
        raise WebUISettingsError("model is required")

    def _apply(config: Config) -> None:
        # Set up the provider
        from jenny.config.schema import ProviderConfig

        config.providers.providers = [
            ProviderConfig(
                name=provider_name,
                format=provider_format,
                api_key=api_key or None,
                api_base=api_base or None,
            )
        ]
        config.providers.default = provider_name

        # Set the model and persona
        config.agents.defaults.model = model
        config.agents.defaults.bot_name = bot_name
        config.agents.defaults.bot_icon = bot_icon

        # Persist the user's language preference for future backend-localized messages.
        locale = (data.get("locale") or "it").strip().lower()
        config.agents.defaults.language = locale if locale in WELCOME_TEMPLATES else "it"

        # Fase 6.7: valida il provider PRIMA di persistere/segnalare. Se la config
        # non produce un provider valido, l'errore torna subito alla WebUI di
        # onboarding invece di fallire in modo asincrono e silenzioso nel
        # GatewayContainer (che reloada la stessa config e resterebbe senza agent).
        # Sollevare qui lascia il file intatto: ``store.mutate`` non salva.
        from jenny.providers.factory import make_provider

        try:
            make_provider(config)
        except (ValueError, RuntimeError) as exc:
            raise WebUISettingsError(f"Provider configuration is invalid: {exc}") from exc

    config = await store.mutate(_apply)

    # Il saluto di benvenuto finisce nell'unica sessione unificata,
    # la stessa che la chat rilegge all'attach.
    chat_id = "default"
    greeting_template = WELCOME_TEMPLATES.get(config.agents.defaults.language, WELCOME_TEMPLATES["it"])
    greeting = greeting_template.format(bot_name=bot_name)
    if session_manager:
        session = session_manager.get_or_create(UNIFIED_SESSION_KEY)
        session.add_message("assistant", greeting, chat_id=chat_id)
        session_manager.save(session)

    if onboarding_event:
        onboarding_event.set()

    return {
        "status": "ok",
        "chat_id": chat_id,
        "welcome_message": greeting,
    }


def _worker_settings_sections(config: Any) -> dict[str, Any]:
    """Le due sezioni dei lavoratori periodici, o niente se non si possono leggere.

    Import dentro la funzione perche' ``worker_settings`` importa da qui
    (``WebUISettingsError``, :func:`settings_payload`): a livello di modulo
    sarebbe un ciclo.

    Il ``try`` non e' timidezza. Le due letture aprono quattro file dell'utente
    (i tre di memoria piu' lo stato del review), e questa e' la schermata da cui
    si spengono i lavoratori: se non si aprisse per un ``SOUL.md`` illeggibile,
    il rimedio sarebbe fuori portata proprio quando serve. Le due funzioni sono
    gia' fail-soft al loro interno; questo e' il secondo giro di chiave, per
    tutto cio' che non e' la misura — uno schema che cambia, un campo rinominato.
    """
    try:
        from jenny.webui.worker_settings import (
            memory_settings_payload,
            worker_settings_payload,
        )

        return {
            "memory": memory_settings_payload(config),
            "workers": worker_settings_payload(config),
        }
    except Exception:
        logger.exception("could not build the worker settings sections")
        return {"memory": None, "workers": None}


def settings_payload(
    *,
    requires_restart: bool = False,
) -> dict[str, Any]:
    config = load_config()
    defaults = config.agents.defaults
    effective_preset = config.agents.defaults

    android_web_search = config.tools.android_web.search
    android_web_fetch = config.tools.android_web.fetch

    exec_config = config.tools.python_exec
    sandbox_status = workspace_sandbox_status(
        restrict_to_workspace=config.security.restrict_to_workspace,
        workspace=config.workspace_path,
    )

    payload = {
        "first_run": _is_first_run(config),
        "providers": _provider_list_simple(config),
        "default_provider": config.providers.default,
        "agent": {
            "model": effective_preset.model,
            "max_tokens": effective_preset.max_tokens,
            "context_window_tokens": effective_preset.context_window_tokens,
            # L'elenco accettato, non solo il valore scelto: senza, la UI
            # dovrebbe ricopiarlo e una modifica qui divergerebbe in
            # silenzio. Stessa forma di `power.modes`.
            "context_window_options": list(_CONTEXT_WINDOW_TOKEN_OPTIONS),
            "temperature": effective_preset.temperature,
            "reasoning_effort": effective_preset.reasoning_effort,
            "timezone": defaults.timezone,
            "bot_name": defaults.bot_name,
            "bot_icon": defaults.bot_icon,
            "tool_hint_max_length": defaults.tool_hint_max_length,
        },
        "web_search": {
            "search_engine": android_web_search.search_engine,
            "max_results": android_web_search.max_results,
            "timeout": android_web_search.timeout,
            "fetch_max_chars": android_web_fetch.max_chars,
        },
        "location": {
            "enabled": config.tools.location.enable,
        },
        # Il wakelock di servizio si prende una volta sola all'avvio: qui si
        # espone il valore scritto nel config, non lo stato del lock vivo. La UI
        # deve dire che il cambio vale dal prossimo riavvio.
        "power": {
            "keep_awake": config.power.keep_awake,
            "modes": list(KEEP_AWAKE_MODES),
        },
        # Mascotte flottante. ``available`` dice se la finestra può esistere su
        # questo dispositivo: fuori da Android no, e la UI nasconde la voce
        # invece di offrire un interruttore che non farebbe nulla.
        "floating": {
            "enabled": config.floating.enabled,
            "reply_hold_s": config.floating.reply_hold_s,
            "available": _android_context() is not None,
        },
        "runtime": {
            "config_path": str(_safe_expanduser(get_config_path())),
            "workspace_path": str(config.workspace_path),
            "gateway_host": config.gateway.host,
            "gateway_port": config.gateway.port,
            "heartbeat": {
                "enabled": config.gateway.heartbeat.enabled,
                "interval_s": config.gateway.heartbeat.interval_s,
                "keep_recent_messages": config.gateway.heartbeat.keep_recent_messages,
            },
        },
        # Le manopole dei tre lavoratori periodici. Import locale: quel modulo
        # importa ``WebUISettingsError`` e ``settings_payload`` da qui, quindi a
        # livello di modulo sarebbe un ciclo.
        **_worker_settings_sections(config),
        "usage": token_usage_payload(timezone_name=defaults.timezone),
        "advanced": {
            "workspace_sandbox": sandbox_status.as_dict(),
            "ssrf_whitelist_count": len(config.security.ssrf_whitelist),
            "exec_enabled": exec_config.enable,
        },
        # Quando l'ultimo backup cifrato è stato salvato davvero (0 = mai), e
        # se la storia locale degli snapshot è accesa. Sono due cose diverse e
        # la casa lo dice: la storia locale vive sullo stesso telefono, un
        # backup esportato no.
        "backup": {
            "last_export_at": config.snapshots.last_export_at,
            "snapshots_enabled": config.snapshots.enabled,
        },
        "requires_restart": requires_restart,
        "version": _version_payload(),
        "config_recovery": _config_recovery_payload(),
        "cron_recovery": _cron_recovery_payload(),
    }
    return payload


def _config_recovery_payload() -> dict[str, Any] | None:
    """Segnala alla UI che `config.json` è stato recuperato all'avvio.

    None nel caso normale. Quando c'è, la UI deve dirlo: ripartire con
    impostazioni diverse da quelle scelte dall'utente — o peggio, dai default —
    senza avvisare sarebbe la sorpresa peggiore, e con `restoredFrom` a
    "defaults" significa che va rimessa anche la chiave API.
    """
    from jenny.runtime.context import get_runtime_context

    ctx = get_runtime_context()
    if not ctx.config_recovered_from:
        return None
    quarantine = ctx.config_quarantine_path
    return {
        "restored_from": ctx.config_recovered_from,
        "broken_file": str(_safe_expanduser(quarantine)) if quarantine else None,
    }


def _cron_recovery_payload() -> dict[str, Any] | None:
    """Segnala alla UI che ``cron/jobs.json`` era illeggibile all'avvio.

    Separato da ``config_recovery`` perché la conseguenza è diversa e più
    concreta: con ``restoredFrom`` a "empty" i promemoria che l'utente aveva
    creato non ci sono più, e nessuna schermata glielo direbbe — se ne
    accorgerebbe solo quando non suonano. Il file rotto è conservato accanto,
    così il recupero a mano resta possibile.
    """
    from jenny.runtime.context import get_runtime_context

    ctx = get_runtime_context()
    if not ctx.cron_recovered_from:
        return None
    quarantine = ctx.cron_quarantine_path
    return {
        "restored_from": ctx.cron_recovered_from,
        "broken_file": str(_safe_expanduser(quarantine)) if quarantine else None,
    }


async def update_agent_settings(query: QueryParams) -> dict[str, Any]:
    """Aggiorna le impostazioni dell'agente dentro il funnel della config."""
    restart: dict[str, bool] = {}

    def _apply(config: Config) -> bool:
        changed, restart["required"] = _apply_agent_settings(config, query)
        return changed

    await store.mutate(_apply)
    return settings_payload(requires_restart=restart.get("required", False))


def _apply_agent_settings(config: Config, query: QueryParams) -> tuple[bool, bool]:
    """Applica le impostazioni agente a *config*; ritorna (modificato, richiede-riavvio).

    Riceve la config invece di leggerla: la lettura deve avvenire dentro il
    lock di ``store.mutate``, altrimenti si torna al lost update.
    """
    defaults = config.agents.defaults
    changed = False
    restart_required = False

    if _apply_str(query, defaults, "model", "model"):
        changed = True

    def _known_provider(name: str) -> str | None:
        known = any(p.name == name for p in config.providers.providers)
        return None if known else "unknown provider"

    if _apply_str(
        query, config.providers, "default", "default_provider",
        validate=_known_provider,
    ):
        changed = True

    context_window_tokens = _parse_context_window_tokens(
        _query_first_alias(query, "context_window_tokens", "contextWindowTokens")
    )
    if (
        context_window_tokens is not None
        and defaults.context_window_tokens != context_window_tokens
    ):
        defaults.context_window_tokens = context_window_tokens
        changed = True

    # I tre parametri di generazione. La WebUI li agganciava già all'auto-save
    # ma qui non c'era nessun ramo a raccoglierli: la richiesta arrivava,
    # ``changed`` restava False, e il client mostrava "Saved!" perché la risposta
    # è 200 comunque. Erano campi decorativi.
    max_tokens = _parse_max_tokens(_query_first_alias(query, "max_tokens", "maxTokens"))
    if max_tokens is not None and defaults.max_tokens != max_tokens:
        defaults.max_tokens = max_tokens
        changed = True

    temperature = _parse_temperature(query_first(query, "temperature"))
    if temperature is not None and defaults.temperature != temperature:
        defaults.temperature = temperature
        changed = True

    reasoning_effort = _parse_reasoning_effort(
        _query_first_alias(query, "reasoning_effort", "reasoningEffort")
    )
    if (
        not isinstance(reasoning_effort, _Unset)
        and defaults.reasoning_effort != reasoning_effort
    ):
        defaults.reasoning_effort = reasoning_effort
        changed = True

    # ``validate_timezone_name`` rifiuta i nomi IANA sconosciuti, e degrada ad
    # accettare se ``tzdata`` manca.
    if _apply_str(query, defaults, "timezone", "timezone", validate=validate_timezone_name):
        changed = True
        restart_required = True

    if _apply_str(query, defaults, "bot_name", "bot_name", "botName"):
        changed = True
        restart_required = True

    # ``required=False``: l'icona vuota è "nessuna icona", che è una scelta
    # legittima — a differenza del nome vuoto, che è un errore di battitura.
    if _apply_str(query, defaults, "bot_icon", "bot_icon", "botIcon", required=False):
        changed = True
        restart_required = True

    # I bound li deriva ``_apply_int`` dallo schema (``ge=20, le=500`` su
    # ``AgentDefaults.tool_hint_max_length``). Erano copiati qui a mano, cioè un
    # secondo posto da aggiornare a ogni cambio di schema — e il messaggio
    # d'errore, che nomina il range, sarebbe stato il primo a mentire.
    if _apply_int(
        query,
        defaults,
        "tool_hint_max_length",
        type(defaults),
        "tool_hint_max_length",
        "toolHintMaxLength",
    ):
        changed = True
        restart_required = True

    return changed, restart_required


async def update_provider(data: dict[str, Any]) -> dict[str, Any]:
    """Create or update a provider in the providers array."""
    name = (data.get("name") or "").strip()
    if not name:
        raise WebUISettingsError("name is required")

    fmt = data.get("format", "openai_compat")
    if fmt not in ("openai_compat", "anthropic"):
        raise WebUISettingsError(f"unknown format: {fmt}")

    api_key = (data.get("api_key") or "").strip() or None
    api_base = (data.get("api_base") or "").strip() or None
    ca_bundle = (data.get("ca_bundle") or "").strip() or None
    # Il campo vuoto non arriva fin qui: ``_postWithQuery`` scarta le stringhe
    # vuote, ed e' quello che fa funzionare "chiave vuota = tieni quella
    # salvata". Svuotare la CA vuole quindi un segnale suo, esplicito.
    clear_ca_bundle = parse_flag(data.get("ca_bundle_clear"))
    if ca_bundle is not None:
        # Validata **prima** di entrare in ``store.mutate``: leggere e parsare un
        # PEM e' I/O, e sotto quel lock l'I/O non ci va (v. AGENTS.md). Ed e' la
        # garanzia piu' forte che si possa dare qui — fallendo prima, il file di
        # configurazione non viene nemmeno aperto.
        #
        # Si valida solo cio' che arriva dalla richiesta, non cio' che era gia'
        # su disco: un PEM sparito dopo non deve rendere impossibile correggere
        # il *base URL* dello stesso provider. In pratica il dialog ripropone
        # sempre il percorso salvato, quindi un file diventato irraggiungibile
        # viene comunque intercettato al primo salvataggio — con il campo sotto
        # gli occhi di chi legge l'errore.
        try:
            build_ssl_context(ca_bundle, provider_name=name)
        except CaBundleError as exc:
            raise WebUISettingsError(str(exc)) from exc

    def _apply(config: Config) -> None:
        _upsert_provider(
            config, name, fmt, api_key, api_base,
            ca_bundle=ca_bundle, clear_ca_bundle=clear_ca_bundle,
        )

    await store.mutate(_apply)
    return settings_payload()


def _resolved_ca_bundle(
    stored: str | None, requested: str | None, clear: bool
) -> str | None:
    """Valore da persistere per ``caBundle``: tre casi, nessun I/O.

    Assente non significa vuoto — un client che non manda il campo non deve
    cancellare la CA — e per questo svuotarla ha un segnale suo. La validazione
    e' gia' avvenuta in ``update_provider``, fuori dal lock di ``store.mutate``.
    """
    if clear:
        return None
    return stored if requested is None else requested


def _upsert_provider(
    config: Config,
    name: str,
    fmt: str,
    api_key: str | None,
    api_base: str | None,
    *,
    ca_bundle: str | None = None,
    clear_ca_bundle: bool = False,
) -> None:
    """Inserisce o aggiorna il provider *name* dentro *config*."""
    providers = config.providers.providers

    for p in providers:
        if p.name == name:
            p.format = fmt
            # Chiave vuota = "tieni quella salvata". Rifiutiamo anche il
            # suggerimento offuscato (`sk-a...j8f9`): un client vecchio che
            # lo pre-compila nel campo lo rimanderebbe qui identico e
            # sovrascriverebbe la chiave vera con il segnaposto.
            if api_key and api_key != _mask_api_key(p.api_key):
                p.api_key = api_key
            p.api_base = api_base or p.api_base
            p.ca_bundle = _resolved_ca_bundle(p.ca_bundle, ca_bundle, clear_ca_bundle)
            break
    else:
        from jenny.config.schema import ProviderConfig

        providers.append(ProviderConfig(
            name=name,
            format=fmt,
            api_key=api_key,
            api_base=api_base,
            ca_bundle=_resolved_ca_bundle(None, ca_bundle, clear_ca_bundle),
        ))

    if not config.providers.default:
        config.providers.default = name


async def delete_provider(data: dict[str, Any]) -> dict[str, Any]:
    """Remove a provider from the array, e i riferimenti che restavano appesi.

    **Un provider e' indirizzato per nome da due posti**, non da uno:
    ``providers.default`` e il campo ``provider`` di ogni preset di modello
    (``config.model_presets``, ``schema.ModelPresetConfig``). Il default lo si
    riparava gia'; i preset no, e restavano a nominare qualcosa che non c'e'
    piu' — la stessa forma del difetto dei progetti del 24/08/2026, dove un nome
    tornava libero in un deposito e restava occupato in un altro.

    Oggi quel campo a runtime **non lo legge nessuno** (``_apply_preset`` cambia
    modello e parametri sul provider *attivo*, e i processi provider non si
    scambiano a caldo), quindi il riferimento appeso non fa danno *adesso*: si
    ripara perche' la trappola scatti mai, non perche' stia scattando.

    **Si azzera il campo, non si cancella il preset.** Un preset senza provider
    resta valido — usa quello attivo — mentre buttare via la configurazione di
    qualcuno perche' una sua riga e' rimasta orfana sarebbe sproporzionato al
    guasto.

    Entrambe le riparazioni stanno nello stesso ``_apply``, quindi nella stessa
    transazione del funnel di scrittura: un config con il provider tolto e i
    riferimenti ancora appesi non esiste in nessun istante.
    """
    name = (data.get("name") or "").strip()
    repointed: list[str] = []

    def _apply(config: Config) -> None:
        config.providers.providers = [
            p for p in config.providers.providers if p.name != name
        ]

        if config.providers.default == name:
            config.providers.default = (
                config.providers.providers[0].name
                if config.providers.providers
                else None
            )

        # ``repointed`` si ricostruisce da zero a ogni giro: ``mutate`` puo'
        # rieseguire il callback, e una lista che si accumula racconterebbe il
        # doppio del lavoro fatto.
        repointed.clear()
        for preset_name, preset in (config.model_presets or {}).items():
            if getattr(preset, "provider", None) == name:
                preset.provider = None
                repointed.append(preset_name)

    await store.mutate(_apply)
    if repointed:
        # Detto e non taciuto: un preset che cambia dove manda le richieste
        # senza che nessuno lo dica e' il genere di cosa che poi si cerca per
        # mezz'ora.
        logger.info(
            "Provider {} removed: {} presets no longer name it ({})",
            name, len(repointed), ", ".join(sorted(repointed)),
        )
    return settings_payload()


async def update_web_search_settings(query: QueryParams) -> dict[str, Any]:
    """Aggiorna le impostazioni di ricerca/fetch web dentro il funnel della config."""
    await store.mutate(lambda config: _apply_web_search_settings(config, query))
    return settings_payload()


def _apply_web_search_settings(config: Config, query: QueryParams) -> bool:
    """Applica le impostazioni web a *config*; ritorna True se qualcosa è cambiato."""
    search_config = config.tools.android_web.search
    fetch_config = config.tools.android_web.fetch
    changed = False

    def set_search_value(attr: str, value: object) -> None:
        nonlocal changed
        if getattr(search_config, attr) != value:
            setattr(search_config, attr, value)
            changed = True

    def set_fetch_value(attr: str, value: object) -> None:
        nonlocal changed
        if getattr(fetch_config, attr) != value:
            setattr(fetch_config, attr, value)
            changed = True

    search_engine = query_first(query, "search_engine")
    if search_engine is not None:
        search_engine = search_engine.strip().lower()
        if search_engine != _ANDROID_WEB_SEARCH_ENGINE:
            raise WebUISettingsError("unknown search engine")
        set_search_value("search_engine", search_engine)

    max_results = _query_first_alias(query, "max_results", "maxResults")
    if max_results is not None:
        set_search_value(
            "max_results",
            _parse_int(max_results, "max_results", AndroidWebSearchConfig, "max_results"),
        )

    timeout = query_first(query, "timeout")
    if timeout is not None:
        set_search_value(
            "timeout", _parse_int(timeout, "timeout", AndroidWebSearchConfig, "timeout")
        )

    fetch_max_chars = _query_first_alias(query, "fetch_max_chars", "fetchMaxChars")
    if fetch_max_chars is not None:
        try:
            parsed_fetch_max_chars = int(fetch_max_chars)
        except ValueError:
            raise WebUISettingsError("fetch_max_chars must be an integer") from None
        if parsed_fetch_max_chars < 1000 or parsed_fetch_max_chars > 200_000:
            raise WebUISettingsError("fetch_max_chars must be between 1000 and 200000")
        set_fetch_value("max_chars", parsed_fetch_max_chars)

    return changed


async def update_location_settings(query: QueryParams) -> dict[str, Any]:
    """Aggiorna il toggle posizione (``tools.location.enable``).

    Solo il toggle è esposto in UI: gli altri campi (TTL Telegram, timeout fix
    fresco) restano config-only. Il gate reale resta comunque il permesso
    runtime Android; questo toggle spegne l'iniezione anche a permesso concesso.
    """

    def _apply(config: Config) -> bool:
        loc = config.tools.location
        enabled = query_first(query, "enabled")
        if enabled is None:
            return False
        value = parse_flag(enabled)
        if loc.enable == value:
            return False
        loc.enable = value
        return True

    await store.mutate(_apply)
    return settings_payload()


def _android_context() -> Any:
    """Il contesto Android, o ``None`` fuori dal telefono.

    Import locale come gli altri accessi al runtime da questo modulo: tenerlo a
    livello di modulo legherebbe il payload delle impostazioni al runtime
    Android anche dove non esiste.
    """
    from jenny.runtime.context import get_android_context

    return get_android_context()


async def update_floating_settings(query: QueryParams) -> dict[str, Any]:
    """Accende o spegne la mascotte flottante, e la applica subito.

    Il push al bridge dopo la ``mutate`` non è un di più: la finestra vive nel
    processo del service e la config la legge solo all'avvio del gateway. Senza
    questa riga l'interruttore non farebbe niente fino al riavvio dell'app — e
    un interruttore che mente è peggio di un interruttore che manca.

    Il push sta **fuori** dal callback di ``mutate``: quel lock si tiene per
    tutta la durata della callback, e una chiamata al bridge Kotlin è I/O che
    può bloccarsi quanto il GIL resta preso.

    L'esito del push **non** cambia la risposta, e nemmeno questo è una svista:
    a permesso negato la config resta accesa e il payload lo dice. La UI ha
    bisogno di sapere che l'utente l'ha voluta accesa *e* che Android non la
    lascia aprire, altrimenti l'interruttore rimbalzerebbe su off senza
    spiegare perché.
    """

    def _apply(config: Config) -> bool:
        section = config.floating
        # I bound li dichiara lo schema (``FloatingConfig.reply_hold_s``) e
        # ``_apply_int`` li legge da lì: riscriverli qui vorrebbe dire che al
        # primo cambio di schema il messaggio d'errore — che nomina il range —
        # comincia a mentire.
        changed = _apply_bool(query, section, "enabled", "enabled")
        changed |= _apply_int(
            query, section, "reply_hold_s", FloatingConfig, "reply_hold_s", "replyHoldS"
        )
        return changed

    await store.mutate(_apply)
    from jenny.runtime.floating import apply_floating_config

    applied = await apply_floating_config()
    payload = settings_payload()
    # Ciò che Android ha concesso, non ciò che l'utente ha chiesto: è la
    # differenza che l'interruttore deve poter raccontare.
    payload["floating"]["active"] = applied
    return payload


async def update_power_settings(query: QueryParams) -> dict[str, Any]:
    """Aggiorna ``power.keepAwake`` (anti-doze) dentro il funnel della config.

    Solo ``keep_awake`` è esposto: rotazione del wakelock, watchdog e alarm
    restano config-only perché sono manopole di taratura, non una scelta che
    l'utente possa fare con cognizione.

    ``requires_restart`` è sempre True quando il valore cambia: il lock di
    servizio (``"always"``) viene preso una volta sola alla partenza del
    gateway, quindi il nuovo modo non entra in vigore prima del riavvio.
    """
    changed: dict[str, bool] = {}

    def _apply(config: Config) -> bool:
        keep_awake = _parse_keep_awake(_query_first_alias(query, "keep_awake", "keepAwake"))
        if keep_awake is None or config.power.keep_awake == keep_awake:
            changed["required"] = False
            return False
        config.power.keep_awake = keep_awake
        changed["required"] = True
        return True

    await store.mutate(_apply)
    return settings_payload(requires_restart=changed.get("required", False))


async def power_diagnostics_payload() -> dict[str, Any]:
    """Stato energetico osservabile: permessi, wakelock e buchi di attività.

    Endpoint a parte e non un pezzo di ``settings_payload`` per due motivi: qui
    si interroga il bridge Android (tre chiamate JNI, che il payload delle
    impostazioni non deve pagare a ogni apertura) e il pannello si aggiorna da
    solo al ritorno da un dialogo di sistema, quando il resto delle
    impostazioni non è cambiato.

    Fuori da Android ``android`` è ``False`` e i tre booleani non significano
    niente: la UI si limita a non mostrare il pannello.
    """
    from jenny.runtime.gap_history import recent_gaps
    from jenny.runtime.power import (
        alarms_available,
        can_schedule_exact_alarms,
        is_battery_exempt,
        is_wakelock_held,
    )

    config = load_config()
    return {
        "android": alarms_available(),
        "battery_exempt": await is_battery_exempt(),
        "exact_alarms": await can_schedule_exact_alarms(),
        "wakelock_held": await is_wakelock_held(),
        "gap_warning_min": config.power.gap_warning_min,
        "gaps": recent_gaps(config.workspace_path, limit=5),
    }
