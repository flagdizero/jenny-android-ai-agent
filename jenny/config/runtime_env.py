"""Layer unico per i knob operativi da variabile d'ambiente (``JENNY_*``).

Questi non sono config utente (non stanno in ``config.json``): sono override
operativi di tuning (timeout, concorrenza) letti dall'ambiente. Prima erano
``os.environ.get(...)`` sparsi; qui sono centralizzati con nome/parsing/default
documentati in un solo posto (Fase 3.2 — "un solo layer env").

Precedenza: default del codice → override ``JENNY_*`` dell'ambiente.
"""

from __future__ import annotations

import os

from loguru import logger


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid {}={!r}; using {}", name, raw, default)
        return default


def _positive_float_env(name: str, default: float) -> float:
    """Come ``_float_env``, ma per i knob dove zero non vuol dire "disabilitato".

    La differenza è di significato, non di stile. Alcuni knob usano ``<= 0`` come
    "nessun limite" e lo interpretano nel chiamante (``llm_timeout_s``,
    ``tool_timeout_s``, ``max_concurrent_requests``): lì lo zero è una scelta.
    Per tutti gli altri viene passato tale e quale a chi lo consuma, che lo
    prende alla lettera — un ``asyncio.wait_for(timeout=0)`` scade sempre, un TTL
    a zero fa scadere tutto al primo giro — e allora è un valore malformato.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = _float_env(name, default)
    if value <= 0:
        logger.warning("Ignoring non-positive {}={!r}; using {}", name, raw, default)
        return default
    return value


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid {}={!r}; using {}", name, raw, default)
        return default


def max_concurrent_requests(default: int = 3) -> int:
    """Cap sui turni LLM concorrenti. ``<= 0`` = illimitato (deciso dal chiamante).

    Env: ``JENNY_MAX_CONCURRENT_REQUESTS`` (default 3).
    """
    return _int_env("JENNY_MAX_CONCURRENT_REQUESTS", default)


def llm_timeout_s(default: float = 300.0) -> float:
    """Timeout hard per singola richiesta LLM (evita starvation del lock di
    sessione se la rete si blocca). ``<= 0`` = disabilitato (deciso dal chiamante).

    Env: ``JENNY_LLM_TIMEOUT_S`` (default 300).
    """
    return _float_env("JENNY_LLM_TIMEOUT_S", default)


def llm_http_timeout_s(default: float) -> float:
    """Timeout della singola richiesta HTTP verso il provider del modello.

    Distinto da ``llm_timeout_s``, che limita il *turno*: questo è il budget del
    trasporto, e su uno stream si applica fra un chunk e il successivo. Il
    default arriva dal chiamante, che sa se l'endpoint è in loopback (vedi
    ``providers/endpoint_budget.py``).

    Env: ``JENNY_LLM_HTTP_TIMEOUT_S``, oppure il nome storico
    ``JENNY_OPENAI_COMPAT_TIMEOUT_S`` — che vale per entrambi i provider, e non
    solo per quello che ha nel nome.
    """
    for name in ("JENNY_LLM_HTTP_TIMEOUT_S", "JENNY_OPENAI_COMPAT_TIMEOUT_S"):
        if os.environ.get(name, "").strip():
            return _positive_float_env(name, default)
    return default


def ws_send_timeout_s(default: float = 12.0) -> float:
    """Timeout wall-clock per singolo ``connection.send()`` sul canale WebSocket.

    La libreria ``websockets`` applica backpressure: se il buffer TCP di un client
    è saturo (rete mobile lenta, app in background) ``send`` si blocca a tempo
    indeterminato. Il dispatcher outbound è seriale, quindi un client zombie
    stallerebbe la consegna a *tutti*. Allo scadere, quella connessione viene
    chiusa e scartata (mai riusata: un send cancellato lascia un frame parziale
    sul filo). Default generoso per non disconnettere client sani su reti lente.

    Un valore ``<= 0`` non disabilita il timeout: finirebbe in
    ``asyncio.wait_for``, che lo prende alla lettera e fa fallire *ogni* send —
    cioè scollega tutti i client invece di essere permissivo. Vedi
    ``_positive_float_env``.

    Env: ``JENNY_WS_SEND_TIMEOUT_S`` (default 12).
    """
    return _positive_float_env("JENNY_WS_SEND_TIMEOUT_S", default)


def tool_timeout_s(default: float = 300.0) -> float:
    """Timeout wall-clock per singola esecuzione di un tool.

    Rete di sicurezza contro i tool che non ritornano mai (il caso più comune di
    turno bloccato con UI ferma su "Agent running"). Allo scadere, l'esecuzione
    viene interrotta e l'errore è restituito al modello come normale tool-error,
    così il turno prosegue e si chiude. ``<= 0`` = disabilitato.

    Env: ``JENNY_TOOL_TIMEOUT_S`` (default 300).
    """
    return _float_env("JENNY_TOOL_TIMEOUT_S", default)


# ── Timeout di streaming dei provider ────────────────────────────────────────
# Vivevano in ``providers/base.py``, con un parser proprio che ri-implementava
# ``_positive_float_env``: due delle manopole operative più citate erano quindi
# invisibili a chi legge *questo* file per sapere quali knob esistono. Il
# ``maximum`` e l'override ``env_value`` sono venuti con loro — non sono stati
# appiattiti, perché servono: il tetto evita che un valore fuori scala trasformi
# una rete di sicurezza in un blocco, e l'override lascia a un valore di config
# la precedenza sull'ambiente.

STREAM_IDLE_TIMEOUT_ENV = "JENNY_STREAM_IDLE_TIMEOUT_S"
DEFAULT_STREAM_IDLE_TIMEOUT_S = 90.0
MAX_STREAM_IDLE_TIMEOUT_S = 3600.0

FIRST_OUTPUT_TIMEOUT_ENV = "JENNY_STREAM_FIRST_OUTPUT_TIMEOUT_S"
DEFAULT_FIRST_OUTPUT_TIMEOUT_S = 300.0
DEFAULT_LOCAL_FIRST_OUTPUT_TIMEOUT_S = 600.0


def _resolve_timeout_s(
    env_name: str,
    env_value: str | None,
    default: float,
    maximum: float,
) -> float:
    """Un timeout da testo (ambiente o config), ignorando i valori inutilizzabili."""
    raw = os.environ.get(env_name) if env_value is None else env_value
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid {}={!r}; using {}", env_name, raw, default)
        return default
    if value <= 0:
        logger.warning("Ignoring non-positive {}={!r}; using {}", env_name, raw, default)
        return default
    if value > maximum:
        logger.warning("Clamping {}={!r} to {}", env_name, raw, maximum)
        return maximum
    return value


def resolve_stream_idle_timeout_s(
    *,
    env_value: str | None = None,
    default: float = DEFAULT_STREAM_IDLE_TIMEOUT_S,
    maximum: float = MAX_STREAM_IDLE_TIMEOUT_S,
) -> float:
    """Silenzio massimo *fra* due chunk di uno stream già avviato.

    Env: ``JENNY_STREAM_IDLE_TIMEOUT_S`` (default 90, tetto 3600).
    """
    return _resolve_timeout_s(STREAM_IDLE_TIMEOUT_ENV, env_value, default, maximum)


def resolve_first_output_timeout_s(
    *,
    local: bool = False,
    env_value: str | None = None,
    maximum: float = MAX_STREAM_IDLE_TIMEOUT_S,
) -> float:
    """Quanto aspettare il *primo* output del modello prima di rinunciare.

    Tenuto separato dall'idle inter-chunk perché le due attese non sono la
    stessa cosa: prima del primo token il server sta macinando il prompt e il
    silenzio è previsto (su un llama.cpp on-device i soli schemi tool valgono
    minuti di prompt processing), mentre a stream avviato un buco lungo è un
    blocco vero. *local* alza il default per gli endpoint in loopback, gli unici
    che possono essere lenti così.

    Env: ``JENNY_STREAM_FIRST_OUTPUT_TIMEOUT_S`` (default 300, 600 in loopback).
    """
    default = DEFAULT_LOCAL_FIRST_OUTPUT_TIMEOUT_S if local else DEFAULT_FIRST_OUTPUT_TIMEOUT_S
    return _resolve_timeout_s(FIRST_OUTPUT_TIMEOUT_ENV, env_value, default, maximum)


# ── Sandbox del workspace ────────────────────────────────────────────────────
# Solo i *nomi*: la lettura resta in ``security/workspace_access.py``, che prende
# un ``environ`` iniettabile e ha una semantica sua (marker, provider, alias
# legacy) che non appartiene a un modulo di parsing. Ma i nomi stanno qui, perché
# è qui che si viene a sapere quali knob ``JENNY_*`` esistono — e uno dei tre è
# un knob di **sicurezza** con un alias storico, cioè esattamente il dettaglio
# che sopravvive solo se vive dove la gente lo cerca.

WORKSPACE_SANDBOX_PROVIDER_ENV = "JENNY_WORKSPACE_SANDBOX_PROVIDER"
WORKSPACE_SANDBOX_ENFORCED_ENV = "JENNY_WORKSPACE_SANDBOX_ENFORCED"
#: Nome storico di ``WORKSPACE_SANDBOX_ENFORCED_ENV``, ancora accettato.
WORKSPACE_SANDBOX_ENFORCED_LEGACY_ENV = "JENNY_SANDBOX_ENFORCED"

