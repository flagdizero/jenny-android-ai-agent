"""Instradamento per modello sui tre endpoint di OpenCode Go.

OpenCode Go espone **la stessa base** in tre wire-format, e ogni modello ne
accetta uno solo (``https://opencode.ai/docs/go``): ``/chat/completions`` per
DeepSeek, GLM, Kimi…; ``/responses`` per GPT, Grok e Muse; ``/messages``
(formato Anthropic) per Claude, MiniMax e Qwen. Un provider configurato con un
formato solo riusciva quindi a parlare con un terzo dei modelli, e gli altri
rispondevano ``HTTP 400 {"error":{"type":"ModelProtocolUnsupported"}}``.

**Il protocollo non si chiede, si impara.** ``/models`` di Go non dice quale
endpoint vuole un modello (misurato l'08/10/2026: solo ``id``, ``object``,
``created``, ``owned_by``), e una tabella copiata dai docs invecchierebbe al
primo modello nuovo. Il rifiuto invece è esplicito e leggibile da macchina, e
arriva prima che il modello generi alcunché: niente è stato eseguito né
fatturato. Quindi:

1. si prova l'endpoint del formato configurato dall'utente;
2. su ``ModelProtocolUnsupported`` — e **solo** su quello: un 400 per altre
   ragioni (privacy dell'account, chiave, parametri) va all'utente intatto,
   perché cambiare endpoint non lo risolverebbe e ne nasconderebbe la causa —
   si riprova lo stesso turno sugli altri, nell'ordine;
3. l'endpoint che risponde si ricorda per quel modello, anche su disco: il
   tentativo a vuoto si paga una volta per modello, non a ogni turno.

Se nessun endpoint accetta il modello, l'utente vede il **primo** errore, non
l'ultimo: è quello dell'endpoint che aveva scelto.

Il router è un ``LLMProvider`` che delega a provider interni, uno per endpoint,
costruiti al primo uso. Retry, attese e ``generation`` restano del router (li
gestisce la base), gli interni fanno solo la richiesta. Fuori da OpenCode il
factory non lo costruisce: ogni altro provider resta esattamente quello di prima.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.providers.base import LLMProvider, LLMResponse

__all__ = [
    "ENDPOINTS",
    "EndpointMemory",
    "OpenCodeRouter",
    "is_protocol_mismatch",
]

# Nome dell'endpoint → (formato del provider interno, ``api_type`` OpenAI).
ENDPOINTS: dict[str, tuple[str, str]] = {
    "chat": ("openai_compat", "chat_completions"),
    "responses": ("openai_compat", "responses"),
    "messages": ("anthropic", "auto"),
}

# Solo per i log: il percorso che l'endpoint ha sul filo.
_PATHS = {"chat": "/chat/completions", "responses": "/responses", "messages": "/messages"}

_MISMATCH_TYPE = "modelprotocolunsupported"


def is_protocol_mismatch(response: LLMResponse) -> bool:
    """La risposta è il rifiuto di Go per un endpoint sbagliato, e solo quello?

    ``error_type`` arriva già normalizzato in minuscolo da ``_error_metadata``;
    il testo è il ripiego per un corpo che non si è lasciato leggere come JSON.
    """
    if response.finish_reason != "error":
        return False
    if response.error_type == _MISMATCH_TYPE:
        return True
    return _MISMATCH_TYPE in (response.content or "").lower()


def endpoint_order(provider_format: str, api_type: str) -> list[str]:
    """L'ordine in cui provare gli endpoint: prima quello che l'utente ha scelto."""
    if provider_format == "anthropic":
        first = "messages"
    elif api_type == "responses":
        first = "responses"
    else:
        first = "chat"
    return [first] + [name for name in ENDPOINTS if name != first]


class EndpointMemory:
    """Quale endpoint ha risposto per ciascun modello, su una base data.

    Su disco perché il provider si ricostruisce a ogni cambio di impostazioni —
    cambio di modello compreso — e il gateway si riavvia: in memoria soltanto,
    il tentativo a vuoto si ripagherebbe a ogni giro. Il file è un'ottimizzazione
    e non una fonte di verità: illeggibile o mancante vale vuoto, e un errore di
    scrittura si logga e basta, perché al peggio costa un tentativo in più.
    """

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._entries: dict[str, str] = self._load()

    @staticmethod
    def _key(api_base: str, model: str) -> str:
        return f"{api_base.rstrip('/').lower()}|{model}"

    def _load(self) -> dict[str, str]:
        if self._path is None or not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("OpenCode endpoint memory unreadable, starting empty: {}", exc)
            return {}
        entries = raw.get("endpoints") if isinstance(raw, dict) else None
        if not isinstance(entries, dict):
            return {}
        return {k: v for k, v in entries.items() if isinstance(k, str) and v in ENDPOINTS}

    def get(self, api_base: str, model: str) -> str | None:
        return self._entries.get(self._key(api_base, model))

    def set(self, api_base: str, model: str, endpoint: str) -> None:
        key = self._key(api_base, model)
        if self._entries.get(key) == endpoint:
            return
        self._entries[key] = endpoint
        self._save()

    def forget(self, api_base: str, model: str) -> None:
        if self._entries.pop(self._key(api_base, model), None) is not None:
            self._save()

    def _save(self) -> None:
        if self._path is None:
            return
        from jenny.utils.path import atomic_write

        payload = json.dumps({"version": 1, "endpoints": self._entries}, indent=1, sort_keys=True)
        try:
            atomic_write(self._path, payload + "\n")
        except Exception as exc:
            logger.warning("OpenCode endpoint memory not saved: {}", exc)


class OpenCodeRouter(LLMProvider):
    """Un provider OpenCode Go che sceglie l'endpoint giusto per ogni modello."""

    def __init__(
        self,
        *,
        api_key: str | None,
        api_base: str,
        default_model: str = "",
        provider_format: str = "openai_compat",
        api_type: str = "auto",
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        extra_query: dict[str, str] | None = None,
        ssl_context: Any = None,
        memory: EndpointMemory | None = None,
    ) -> None:
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self._order = endpoint_order(provider_format, api_type)
        self._extra_headers = extra_headers
        self._extra_body = extra_body
        self._extra_query = extra_query
        self._ssl_context = ssl_context
        self._memory = memory or EndpointMemory(None)
        self._inner: dict[str, LLMProvider] = {}

    # ------------------------------------------------------------------
    # Provider interni
    # ------------------------------------------------------------------

    def _provider_for(self, endpoint: str) -> LLMProvider:
        provider = self._inner.get(endpoint)
        if provider is not None:
            return provider
        provider_format, api_type = ENDPOINTS[endpoint]
        if provider_format == "anthropic":
            from jenny.providers.anthropic_provider import AnthropicProvider

            # ``api_type`` non significa niente per il formato Anthropic, e
            # passarlo farebbe loggare un avviso a ogni costruzione.
            provider = AnthropicProvider(
                api_key=self.api_key,
                api_base=self.api_base,
                default_model=self.default_model,
                extra_headers=self._extra_headers,
                extra_body=self._extra_body,
                extra_query=self._extra_query,
                ssl_context=self._ssl_context,
            )
        else:
            from jenny.providers.openai_compat_provider import OpenAICompatProvider

            provider = OpenAICompatProvider(
                api_key=self.api_key,
                api_base=self.api_base,
                default_model=self.default_model,
                extra_headers=self._extra_headers,
                extra_body=self._extra_body,
                api_type=api_type,
                extra_query=self._extra_query,
                ssl_context=self._ssl_context,
            )
        self._inner[endpoint] = provider
        return provider

    def _candidates(self, model: str) -> list[str]:
        learned = self._memory.get(self.api_base or "", model)
        if learned is None:
            return list(self._order)
        return [learned] + [name for name in self._order if name != learned]

    def endpoint_for(self, model: str | None = None) -> str:
        """L'endpoint che il prossimo turno proverà per primo con *model*."""
        return self._candidates(model or self.default_model)[0]

    # ------------------------------------------------------------------
    # Instradamento
    # ------------------------------------------------------------------

    async def _route(
        self,
        model: str,
        call: Callable[[LLMProvider], Awaitable[LLMResponse]],
        *,
        emitted: Callable[[], bool] = lambda: False,
    ) -> LLMResponse:
        base = self.api_base or ""
        learned = self._memory.get(base, model)
        first_error: LLMResponse | None = None
        for endpoint in self._candidates(model):
            response = await call(self._provider_for(endpoint))
            if not is_protocol_mismatch(response):
                # Si impara solo da un successo: un errore d'altro tipo (il
                # 400 di privacy di Muse è arrivato proprio su
                # ``/chat/completions``, che per Muse è l'endpoint sbagliato)
                # non dice niente sul protocollo.
                if response.finish_reason != "error":
                    if endpoint != learned:
                        logger.info(
                            "OpenCode: model {!r} answers on {} — remembered",
                            model, _PATHS[endpoint],
                        )
                    self._memory.set(base, model, endpoint)
                return response
            if first_error is None:
                first_error = response
            if endpoint == learned:
                # Il modello ha cambiato protocollo da quando l'abbiamo visto.
                self._memory.forget(base, model)
            if emitted():
                # Il rifiuto arriva prima di qualunque output; se qualcosa è
                # già uscito non è il caso che conosciamo, e ripetere il turno
                # duplicherebbe quel testo.
                return response
            logger.info(
                "OpenCode: model {!r} refused on {} (ModelProtocolUnsupported), "
                "trying the next endpoint",
                model, _PATHS[endpoint],
            )
        assert first_error is not None
        return first_error

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        effective = model or self.default_model
        return await self._route(
            effective,
            lambda provider: provider.chat(
                messages=messages, tools=tools, model=effective,
                max_tokens=max_tokens, temperature=temperature,
                reasoning_effort=reasoning_effort, tool_choice=tool_choice,
            ),
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        effective = model or self.default_model
        seen = False

        def _tracked(callback: Callable[[Any], Awaitable[None]] | None):
            if callback is None:
                return None

            async def wrapper(value: Any) -> None:
                nonlocal seen
                seen = True
                await callback(value)

            return wrapper

        content_cb = _tracked(on_content_delta)
        thinking_cb = _tracked(on_thinking_delta)
        tool_cb = _tracked(on_tool_call_delta)
        return await self._route(
            effective,
            lambda provider: provider.chat_stream(
                messages=messages, tools=tools, model=effective,
                max_tokens=max_tokens, temperature=temperature,
                reasoning_effort=reasoning_effort, tool_choice=tool_choice,
                on_content_delta=content_cb,
                on_thinking_delta=thinking_cb,
                on_tool_call_delta=tool_cb,
            ),
            emitted=lambda: seen,
        )

    # ------------------------------------------------------------------
    # Quello che il resto di Jenny legge dal provider
    # ------------------------------------------------------------------

    def closed_reasoning_can_be_blank(self, model: str | None) -> bool:
        """Delegato al ramo Chat Completions, l'unico che ne ha una regola."""
        inner = self._provider_for("chat")
        check = getattr(inner, "closed_reasoning_can_be_blank", None)
        return bool(check(model)) if callable(check) else False

    async def _close_http_client(self) -> None:
        for provider in list(self._inner.values()):
            await provider._close_http_client()

    def get_default_model(self) -> str:
        return self.default_model
