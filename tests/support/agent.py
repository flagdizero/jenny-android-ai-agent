"""Un ``AgentLoop`` vero e un provider finto, per i test dell'agente.

Stavano in ``tests/agent/conftest.py``, e da lì tre file li importavano come
``tests.agent.conftest``: funziona solo con la radice del repo nel path, e
carica la conftest una seconda volta sotto un altro nome. Intanto una ventina di
file ne aveva una copia sua, quasi sempre con un ``MagicMock()`` nudo al posto
del provider e ``AgentLoop`` costruito coi suoi default. Qui le due cose:
``conftest.py`` le reimporta (la fixture ``loop_factory`` resta lì), e le copie
chiedono quel che le distingueva con un'opzione.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from jenny.agent.loop import AgentLoop
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMProvider

# Quel che ``patch_deps=True`` sostituisce: un workspace senza file veri non
# regge il costruttore di queste tre.
DEFAULT_PATCHES = (
    "jenny.agent.loop.ContextBuilder",
    "jenny.agent.loop.SessionManager",
    "jenny.agent.loop.SubagentManager",
)


def make_provider(
    default_model: str = "test-model",
    *,
    max_tokens: int = 4096,
    spec: bool = True,
    bare: bool = False,
) -> MagicMock:
    """Un provider finto.

    Di norma limitato allo ``spec`` di ``LLMProvider``, con ``generation``.
    Con *bare* è il ``MagicMock()`` nudo che quasi tutte le copie locali usavano:
    solo ``get_default_model``, e ogni altro attributo nasce a richiesta.
    """
    if bare:
        provider = MagicMock()
        provider.get_default_model.return_value = default_model
        return provider
    provider = MagicMock(spec=LLMProvider) if spec else MagicMock()
    provider.get_default_model.return_value = default_model
    provider.generation = SimpleNamespace(
        max_tokens=max_tokens,
        temperature=0.1,
        reasoning_effort=None,
    )
    if not spec:
        # Optional provider hook (see estimate_prompt_tokens_chain); the
        # LLMProvider ABC does not define it, so spec-limited mocks omit it
        # and the runtime falls back to the character heuristic.
        provider.estimate_prompt_tokens.return_value = (10_000, "test")
    return provider


def make_loop(
    tmp_path: Path,
    *,
    model: str | None = "test-model",
    context_window_tokens: int | None = 128_000,
    session_ttl_minutes: int = 0,
    max_messages: int = 120,
    tools_config=None,
    model_presets: dict | None = None,
    initial_model_preset: str | None = None,
    hooks: list | None = None,
    provider: MagicMock | None = None,
    bare: bool = False,
    patch_deps: bool = False,
    patches: tuple[str, ...] | None = None,
    **extra,
) -> AgentLoop:
    """Un ``AgentLoop`` vero per i test.

    *model* e *context_window_tokens* a ``None`` non si passano: vale il default
    del costruttore (il modello del provider, la finestra di default). *bare*
    usa ``make_provider(bare=True)`` se *provider* non è dato. *patch_deps*
    sostituisce :data:`DEFAULT_PATCHES` durante la costruzione, *patches* un
    elenco scelto dal test; un ``SubagentManager`` sostituito risponde a
    ``cancel_by_session`` con zero. *extra* va al costruttore com'è.
    """
    bus = MessageBus()
    if provider is None:
        provider = make_provider(default_model=model or "test-model", bare=bare)

    kwargs = dict(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        session_ttl_minutes=session_ttl_minutes,
        max_messages=max_messages,
        **extra,
    )
    if model is not None:
        kwargs["model"] = model
    if context_window_tokens is not None:
        kwargs["context_window_tokens"] = context_window_tokens
    if tools_config is not None:
        kwargs["tools_config"] = tools_config
    if model_presets is not None:
        kwargs["model_presets_config"] = model_presets
    if initial_model_preset is not None:
        kwargs["initial_model_preset"] = initial_model_preset
    if hooks is not None:
        kwargs["hooks"] = hooks

    targets = patches if patches is not None else (DEFAULT_PATCHES if patch_deps else ())
    with ExitStack() as stack:
        for target in targets:
            mocked = stack.enter_context(patch(target))
            if target.endswith(".SubagentManager"):
                mocked.return_value.cancel_by_session = AsyncMock(return_value=0)
        return AgentLoop(**kwargs)
