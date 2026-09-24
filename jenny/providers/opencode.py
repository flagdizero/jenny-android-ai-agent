"""Header di sessione per OpenCode Go, condivisi fra i provider (modulo "leaf").

OpenCode Go (``https://opencode.ai/zen/go/v1``) chiede ai client di terze parti
tre cose: mandare traffico da agente, identificarsi con uno user agent proprio
invece del nome della libreria HTTP, e mandare **un ID di sessione stabile per
conversazione** in ``x-opencode-session``, con cui il gateway ottimizza routing e
prompt caching. Senza quell'header il gateway risponde
``Request is missing x-opencode-session and cannot be routed efficiently``.

Il modulo sta qui e non fra gli helper OpenAI-compat perché i consumatori sono
tre e non uno: i due provider — Go espone la stessa base in tre wire-format,
``/chat/completions``, ``/responses`` e ``/messages`` — più la sonda del catalogo
modelli della WebUI. Stessa ragione per cui esiste ``endpoint_budget.py``.

**Il gate è il base URL, e si valuta a ogni richiesta.** Fuori da ``opencode.ai``
queste funzioni ritornano un dict vuoto, cioè le richieste verso ogni altro
provider restano esattamente quelle di prima. Per richiesta e non alla
costruzione perché la conversazione attiva non esiste ancora quando il provider
nasce, e perché la base può essere corretta a runtime
(``openai_compat_provider._retry_on_versioned_base``).

Leaf-level: solo stdlib + loguru.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from loguru import logger

from jenny import __version__

__all__ = [
    "SESSION_HEADER",
    "UNSUPPORTED_MESSAGE_KEYS",
    "catalog_headers",
    "conversation_scope",
    "current_conversation_id",
    "message_keys",
    "session_headers",
    "uses_opencode",
    "user_agent",
]

SESSION_HEADER = "x-opencode-session"
_OPENCODE_HOST = "opencode.ai"

# Chiavi di messaggio che ``/chat/completions`` di Go rifiuta invece di
# ignorare. ``name`` è opzionale nello schema OpenAI e Jenny lo mette sui
# risultati dei tool per leggibilità della cronologia; Go risponde
# ``HTTP 400 ... messages[N]: "name" is not supported by this endpoint`` e il
# turno muore alla **seconda** richiesta, cioè appena il modello usa un tool.
# La correlazione vera è ``tool_call_id``, quindi toglierlo dal filo non perde
# niente: la cronologia locale continua a portarlo.
UNSUPPORTED_MESSAGE_KEYS = frozenset({"name"})

# La conversazione in corso, non il suo contenuto: la scrive chi sta per
# chiamare il provider, la legge il provider al momento di firmare la richiesta.
# ContextVar e non attributo sull'istanza perché il provider è **uno solo** ed è
# condiviso: cron, Dream e heartbeat girano in task propri e possono
# sovrapporsi al turno dell'utente, e un attributo mutabile darebbe a una
# richiesta l'ID di un'altra conversazione. Ogni task porta la sua copia.
_current_session_key: ContextVar[str | None] = ContextVar(
    "opencode_session_key", default=None,
)

# Un percorso che chiama il provider senza aprire uno scope lo diciamo una volta
# sola: in un log di turno ripetuto sarebbe rumore, e quello che serve sapere è
# *che esiste*, non quante volte è passato.
_warned_missing_scope = False


def uses_opencode(api_base: str | None) -> bool:
    """Return True when the configured base URL points at OpenCode."""
    return bool(api_base and _OPENCODE_HOST in api_base.lower())


def user_agent() -> str:
    """Lo user agent con cui Jenny si presenta, come i docs di Go chiedono."""
    return f"jenny/{__version__}"


@contextmanager
def conversation_scope(session_key: str | None) -> Iterator[None]:
    """Dichiara a quale conversazione appartengono le richieste fatte qui dentro.

    Va aperto da **ogni** percorso che chiama il provider, non solo dal turno
    dell'utente: le chiamate ausiliarie fuori dal turno — come la compattazione
    — sono esattamente quelle che negli
    altri client restano scoperte, e il sintomo non è un errore ma un degrado
    silenzioso (cache mancata, e nei casi peggiori un 400 che fa ripiegare la
    richiesta su un altro modello).
    """
    token = _current_session_key.set(session_key)
    try:
        yield
    finally:
        _current_session_key.reset(token)


def current_conversation_id(*, fallback_id: str) -> str:
    """ID opaco della conversazione attiva, o *fallback_id* se non c'è scope.

    **Si manda l'hash e non la chiave di sessione.** Le chiavi nominano canale e
    chat (``telegram:123456``): in chiaro dentro un header sarebbero un dato
    personale regalato a un terzo, mentre a Go serve solo un valore opaco,
    stabile per conversazione e distinto fra conversazioni — cosa che l'hash è.

    Senza scope si ripiega su *fallback_id* invece di omettere l'header. Un
    header mancante è un fallimento documentato dal gateway; un header costante
    è soltanto prompt caching peggiore. Fra un guasto e un degrado si sceglie il
    degrado, e sono i test a garantire che i percorsi veri lo popolino davvero.
    """
    global _warned_missing_scope
    session_key = _current_session_key.get()
    if not session_key:
        if not _warned_missing_scope:
            _warned_missing_scope = True
            logger.debug(
                "OpenCode: provider called outside a conversation scope; "
                "falling back to a per-instance session id"
            )
        return fallback_id
    return hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:32]


def session_headers(api_base: str | None, *, fallback_id: str) -> dict[str, str]:
    """Header da aggiungere a una richiesta di chat verso OpenCode.

    Dict vuoto per ogni altro endpoint: è la garanzia che il supporto a Go non
    tocchi nessun provider già configurato.
    """
    if not uses_opencode(api_base):
        return {}
    return {
        SESSION_HEADER: current_conversation_id(fallback_id=fallback_id),
        "User-Agent": user_agent(),
    }


def catalog_headers(api_base: str | None) -> dict[str, str]:
    """Header per la sonda del catalogo modelli: solo identificazione.

    Niente ``x-opencode-session``: la lista dei modelli non è una conversazione,
    e inventarle un ID sporcherebbe il routing che quell'header serve a guidare.
    """
    if not uses_opencode(api_base):
        return {}
    return {"User-Agent": user_agent()}


def message_keys(api_base: str | None, allowed: frozenset[str]) -> frozenset[str]:
    """Le chiavi di messaggio ammesse sul filo, ristrette per OpenCode.

    Fuori da OpenCode ritorna *allowed* immutato — stesso gate, stessa garanzia
    di non toccare gli altri provider.
    """
    if not uses_opencode(api_base):
        return allowed
    return allowed - UNSUPPORTED_MESSAGE_KEYS
