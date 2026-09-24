"""Recovery del troncamento a contenuto vuoto attraverso ``AgentRunner``.

Il caso reale: un reasoning model consuma l'intero ``max_tokens`` in
``reasoning_content`` e restituisce contenuto vuoto senza tool call. Prima veniva
trattato come "risposta vuota" e ritentato *identico* — tre chiamate contro lo
stesso tetto, ~3,5 minuti, poi un messaggio di fallback che non nominava la
causa. Questi test bloccano entrambe le regressioni: il retry deve differire, e
l'esito terminale deve dire la verità.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from support.runner import make_spec

from jenny.config.schema import AgentDefaults
from jenny.providers.base import LLMProvider, LLMResponse

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


def _truncated_blank(budget: int) -> LLMResponse:
    """Risposta che ha bruciato tutto il budget nel thinking."""
    return LLMResponse(
        content=None,
        tool_calls=[],
        finish_reason="length",
        reasoning_content="planning " * 200,
        usage={"prompt_tokens": 20000, "completion_tokens": budget},
    )


def _make_spec(**overrides):
    from jenny.agent.runner import AgentRunSpec

    tools = MagicMock()
    tools.get_definitions.return_value = []
    kwargs = dict(
        initial_messages=[{"role": "user", "content": "make an animated dice app"}],
        tools=tools,
        model="test-model",
        max_iterations=6,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        max_tokens=8192,
        context_window_tokens=65536,
    )
    kwargs.update(overrides)
    return AgentRunSpec(**kwargs)


@pytest.mark.asyncio
async def test_blank_truncation_retry_raises_the_output_budget():
    """Il retry deve alzare il tetto, non ripetere la stessa richiesta.

    Questa è la regressione centrale: una richiesta byte-identica contro lo
    stesso ``max_tokens`` non può che ri-troncare.
    """
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    budgets: list[int | None] = []

    async def chat_with_retry(*, messages, tools=None, **kwargs):
        budgets.append(kwargs.get("max_tokens"))
        if len(budgets) == 1:
            return _truncated_blank(8192)
        return LLMResponse(
            content="here is the app",
            tool_calls=[],
            usage={"prompt_tokens": 20000, "completion_tokens": 500},
        )

    provider.chat_with_retry = chat_with_retry
    result = await AgentRunner(provider).run(_make_spec())

    assert result.final_content == "here is the app"
    assert budgets[0] == 8192
    assert budgets[1] == 16384


@pytest.mark.asyncio
async def test_blank_truncation_does_not_mutate_the_conversation():
    """Nessun "continua da dove eri": non c'è nulla da continuare.

    Il modello non ha prodotto output visibile, e diverse API scartano il
    ``reasoning_content`` dei turni precedenti — quindi un prompt di
    continuazione sarebbe un'istruzione insoddisfacibile, oltre a sporcare la
    storia con un turno assistant vuoto.
    """
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    seen_messages: list[list[dict]] = []

    async def chat_with_retry(*, messages, tools=None, **kwargs):
        seen_messages.append([dict(m) for m in messages])
        if len(seen_messages) == 1:
            return _truncated_blank(8192)
        return LLMResponse(
            content="done",
            tool_calls=[],
            usage={"prompt_tokens": 20000, "completion_tokens": 10},
        )

    provider.chat_with_retry = chat_with_retry
    await AgentRunner(provider).run(_make_spec())

    assert seen_messages[1] == seen_messages[0]


@pytest.mark.asyncio
async def test_second_retry_also_lowers_reasoning_effort():
    """Effort abbassato solo dal secondo tentativo.

    Ordine deliberato: se il gateway rifiuta ``reasoning_effort``, un tentativo
    col solo budget alzato è già stato speso.
    """
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    calls: list[dict] = []

    async def chat_with_retry(*, messages, tools=None, **kwargs):
        calls.append(kwargs)
        budget = kwargs.get("max_tokens") or 8192
        if len(calls) <= 2:
            return _truncated_blank(budget)
        return LLMResponse(
            content="done",
            tool_calls=[],
            usage={"prompt_tokens": 20000, "completion_tokens": 10},
        )

    provider.chat_with_retry = chat_with_retry
    result = await AgentRunner(provider).run(_make_spec())

    assert result.final_content == "done"
    assert "reasoning_effort" not in calls[0]
    assert "reasoning_effort" not in calls[1]
    assert calls[2]["reasoning_effort"] == "low"
    assert calls[2]["max_tokens"] == 32768


@pytest.mark.asyncio
async def test_exhausted_retries_report_truncation_not_a_generic_failure():
    """Il messaggio terminale deve nominare il limite di token.

    Con il messaggio generico "non ho prodotto una risposta" la causa resta
    invisibile e si va a caccia del bug sbagliato.
    """
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    calls = {"n": 0}

    async def chat_with_retry(*, messages, tools=None, **kwargs):
        calls["n"] += 1
        return _truncated_blank(kwargs.get("max_tokens") or 8192)

    provider.chat_with_retry = chat_with_retry
    result = await AgentRunner(provider).run(_make_spec())

    assert result.stop_reason == "output_truncated"
    assert "output token limit" in (result.final_content or "")
    # 1 tentativo iniziale + 2 retry, e nessuna chiamata di finalizzazione: il
    # turno ha già fatto attendere l'utente, non si spende un'altra chiamata.
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_no_headroom_falls_back_to_lowering_the_effort():
    """Senza spazio nella finestra il budget non si alza: resta l'effort.

    Alzare il budget oltre lo spazio disponibile scambierebbe un troncamento con
    un errore di context length — un fallimento peggiore e meno leggibile. Ma lo
    stadio "solo budget" qui non esiste, quindi l'effort va usato subito invece
    di arrendersi con una leva ancora in mano.
    """
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    calls: list[dict] = []

    async def chat_with_retry(*, messages, tools=None, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return LLMResponse(
                content=None,
                tool_calls=[],
                finish_reason="length",
                reasoning_content="planning " * 200,
                # Il prompt riempie quasi tutta la finestra: niente spazio per crescere.
                usage={"prompt_tokens": 60000, "completion_tokens": 8192},
            )
        return LLMResponse(
            content="done",
            tool_calls=[],
            usage={"prompt_tokens": 60000, "completion_tokens": 10},
        )

    provider.chat_with_retry = chat_with_retry
    result = await AgentRunner(provider).run(_make_spec())

    assert result.final_content == "done"
    assert calls[1]["reasoning_effort"] == "low"
    # Il tetto resta quello di partenza: la finestra non permette di alzarlo.
    assert calls[1]["max_tokens"] == 8192


@pytest.mark.asyncio
async def test_no_headroom_and_effort_already_low_gives_up_immediately():
    """Nessuna leva disponibile: si smette invece di ripetere a vuoto."""
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    calls = {"n": 0}

    async def chat_with_retry(*, messages, tools=None, **kwargs):
        calls["n"] += 1
        return LLMResponse(
            content=None,
            tool_calls=[],
            finish_reason="length",
            reasoning_content="planning " * 200,
            usage={"prompt_tokens": 60000, "completion_tokens": 8192},
        )

    provider.chat_with_retry = chat_with_retry
    result = await AgentRunner(provider).run(_make_spec(reasoning_effort="minimal"))

    assert result.stop_reason == "output_truncated"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_truncation_detected_when_provider_mislabels_finish_reason():
    """Il rilevamento non dipende da ``finish_reason == "length"``.

    Jenny non logga ``finish_reason`` da nessuna parte, quindi non è verificabile
    a posteriori se un gateway lo riporti: il confronto con l'usage riportato è
    ciò che rende il fix indipendente da quell'etichetta.
    """
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    budgets: list[int | None] = []

    async def chat_with_retry(*, messages, tools=None, **kwargs):
        budgets.append(kwargs.get("max_tokens"))
        if len(budgets) == 1:
            return LLMResponse(
                content=None,
                tool_calls=[],
                finish_reason="stop",  # il provider etichetta male
                reasoning_content="planning " * 200,
                usage={"prompt_tokens": 20000, "completion_tokens": 8192},
            )
        return LLMResponse(
            content="done",
            tool_calls=[],
            usage={"prompt_tokens": 20000, "completion_tokens": 10},
        )

    provider.chat_with_retry = chat_with_retry
    result = await AgentRunner(provider).run(_make_spec())

    assert result.final_content == "done"
    assert budgets[1] == 16384


@pytest.mark.asyncio
async def test_raised_budget_survives_a_tool_phase():
    """Il budget alzato resta per il turno; il contatore si azzera.

    Rimetterlo al valore di partenza dopo i tool garantirebbe di ri-sbattere
    sullo stesso muro, sprecando un'altra chiamata per riscoprire una cosa già
    nota.
    """
    from jenny.agent.runner import AgentRunner
    from jenny.providers.base import ToolCallRequest

    registry = MagicMock()
    registry.get_definitions.return_value = []

    async def execute(name, arguments):
        return "ok"

    registry.execute = execute
    registry.get_tool.return_value = MagicMock(concurrency_safe=False)

    provider = MagicMock(spec=LLMProvider)
    budgets: list[int | None] = []

    async def chat_with_retry(*, messages, tools=None, **kwargs):
        budgets.append(kwargs.get("max_tokens"))
        if len(budgets) == 1:
            return _truncated_blank(8192)
        if len(budgets) == 2:
            return LLMResponse(
                content="calling",
                tool_calls=[ToolCallRequest(id="1", name="noop", arguments={})],
                finish_reason="tool_calls",
                usage={"prompt_tokens": 20000, "completion_tokens": 20},
            )
        return LLMResponse(
            content="done",
            tool_calls=[],
            usage={"prompt_tokens": 20000, "completion_tokens": 10},
        )

    provider.chat_with_retry = chat_with_retry
    spec = make_spec(
        initial_messages=[{"role": "user", "content": "go"}],
        tools=registry,
        max_iterations=6,
        max_tokens=8192,
        context_window_tokens=65536,
    )
    result = await AgentRunner(provider).run(spec)

    assert result.final_content == "done"
    assert budgets == [8192, 16384, 16384]


@pytest.mark.asyncio
async def test_genuinely_empty_response_keeps_the_old_retry_path():
    """Non-regressione: la risposta vuota non troncata non alza il budget."""
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    budgets: list[int | None] = []

    async def chat_with_retry(*, messages, tools=None, **kwargs):
        budgets.append(kwargs.get("max_tokens"))
        if len(budgets) == 1:
            return LLMResponse(
                content=None,
                tool_calls=[],
                finish_reason="stop",
                usage={"prompt_tokens": 20000, "completion_tokens": 1},
            )
        return LLMResponse(
            content="done",
            tool_calls=[],
            usage={"prompt_tokens": 20000, "completion_tokens": 10},
        )

    provider.chat_with_retry = chat_with_retry
    result = await AgentRunner(provider).run(_make_spec())

    assert result.final_content == "done"
    assert budgets == [8192, 8192]
