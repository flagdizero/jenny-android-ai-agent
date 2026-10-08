"""Create LLM providers from config."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jenny.config.schema import Config
from jenny.providers.base import GenerationSettings, LLMProvider
from jenny.providers.opencode import uses_opencode
from jenny.providers.tls import build_ssl_context

__all__ = ["make_provider", "provider_fingerprint"]


def _make_provider_core(config: Config) -> LLMProvider:
    """Create a plain LLM provider from the active provider in config."""
    try:
        p = config.get_active_provider()
    except ValueError:
        raise RuntimeError(
            "No provider configured. Add a provider in Settings or edit "
            "workspace/config.json to set providers.providers[0]."
        ) from None

    backend = p.format
    defaults = config.agents.defaults
    model = defaults.model

    if not p.api_key:
        raise RuntimeError(f"Provider '{p.name}': api_key is required.")

    # Un unico punto di costruzione per la fiducia TLS, prima del ramo sul
    # formato: cosi' un ``caBundle`` rotto e' un errore solo, con un messaggio
    # solo, e non due percorsi da tenere allineati. ``CaBundleError`` e' una
    # ``RuntimeError``, quindi risale come il caso della chiave mancante qui
    # sopra e i chiamanti non cambiano.
    ssl_context = build_ssl_context(p.ca_bundle, provider_name=p.name)

    if uses_opencode(p.api_base):
        # Go serve tre wire-format sulla stessa base e ogni modello ne accetta
        # uno: il formato scelto dall'utente diventa solo il primo tentativo
        # (v. ``providers/opencode_router.py``).
        from jenny.providers.opencode_router import EndpointMemory, OpenCodeRouter

        provider: LLMProvider = OpenCodeRouter(
            api_key=p.api_key,
            api_base=p.api_base or "",
            default_model=model,
            provider_format=backend,
            api_type=p.api_type,
            extra_headers=p.extra_headers,
            extra_body=p.extra_body,
            extra_query=p.extra_query,
            ssl_context=ssl_context,
            memory=EndpointMemory(_endpoint_memory_path()),
        )
    elif backend == "anthropic":
        from jenny.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(
            api_key=p.api_key,
            api_base=p.api_base,
            default_model=model,
            extra_headers=p.extra_headers,
            extra_body=p.extra_body,
            extra_query=p.extra_query,
            api_type=p.api_type,
            ssl_context=ssl_context,
        )
    else:  # "openai_compat"
        from jenny.providers.openai_compat_provider import OpenAICompatProvider

        provider = OpenAICompatProvider(
            api_key=p.api_key,
            api_base=p.api_base,
            default_model=model,
            extra_headers=p.extra_headers,
            extra_body=p.extra_body,
            api_type=p.api_type,
            extra_query=p.extra_query,
            ssl_context=ssl_context,
        )

    from jenny.utils.helpers import output_tokens_for_window

    provider.generation = GenerationSettings(
        temperature=defaults.temperature,
        max_tokens=output_tokens_for_window(defaults.max_tokens,
                                            defaults.context_window_tokens),
        reasoning_effort=defaults.reasoning_effort,
    )
    # Nome config del provider attivo: la WebUI lo usa per il branding
    # (evento runtime_model_updated e popover Info sessione).
    provider.provider_name = p.name
    return provider


def _endpoint_memory_path() -> Path | None:
    """Il file in cui il router di OpenCode ricorda l'endpoint di ogni modello.

    ``None`` senza workspace (test, onboarding): il router impara lo stesso,
    solo che lo dimentica alla ricostruzione successiva.
    """
    try:
        from jenny.config.paths import get_data_dir

        return get_data_dir() / "opencode_endpoints.json"
    except Exception:
        return None


def make_provider(config: Config) -> LLMProvider:
    """Create the LLM provider from config."""
    return _make_provider_core(config)


def provider_fingerprint(config: Config) -> str:
    """Tutto cio' che ``make_provider`` legge, in una forma confrontabile.

    Serve a chi tiene in mano un provider gia' costruito e deve decidere se il
    config e' cambiato abbastanza da ricostruirlo (``GatewayContainer``). Sta
    qui, accanto a ``_make_provider_core``, perche' le due funzioni devono
    dire la stessa cosa: una legge quei campi, l'altra li riassume. Separarle
    di modulo significherebbe cambiarne una sola.

    La voce del provider si riassume con ``model_dump()`` **intero** e non
    campo per campo, ed e' il punto di tutta la funzione. L'elenco a mano si
    dimentica il campo aggiunto dopo, e si e' dimenticato due volte: prima i
    parametri di generazione, poi ``caBundle`` (issue #12) — una CA salvata
    lasciava vivo il client di prima, che non se ne fidava, mentre la sonda del
    catalogo modelli — senza stato, ricostruita a ogni richiesta — funzionava.
    Da fuori sembrava che la CA arrivasse a un client e non all'altro.

    Il valore e' una stringa opaca: si confronta, non si legge. Contiene
    ``apiKey``, quindi non va loggato ne' esposto.
    """
    try:
        provider: dict[str, Any] | None = config.get_active_provider().model_dump()
    except ValueError:
        # Nessun provider attivo e' uno stato come un altro, non un'uscita
        # anticipata: il resto dell'impronta va calcolato lo stesso, altrimenti
        # durante l'onboarding un cambio di modello o di finestra di contesto
        # darebbe la stessa impronta di prima.
        provider = None
    defaults = config.agents.defaults
    payload = {
        "provider": provider,
        # Non sono del provider ma della sessione che gli sta attorno: il
        # modello e la finestra di contesto viaggiano insieme al provider in
        # ``_apply_provider_switch``, e ``generation`` la scrive il factory
        # sopra sull'oggetto appena costruito.
        "model": defaults.model,
        "context_window_tokens": defaults.context_window_tokens,
        "temperature": defaults.temperature,
        "max_tokens": defaults.max_tokens,
        "reasoning_effort": defaults.reasoning_effort,
    }
    return json.dumps(payload, sort_keys=True, default=str)
