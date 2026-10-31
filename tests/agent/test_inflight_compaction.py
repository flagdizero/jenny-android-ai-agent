"""La compattazione a meta' turno: un compito lungo non sfonda la finestra.

Prima di questa, dentro un turno c'erano solo microcompact, tetto per risultato e
snip, e lo snip non taglia dentro il turno in corso: con un tool che il
microcompact non svuotava (allora ``ssh_exec``) un turno da 40 passi moriva
all'undicesimo con l'errore di contesto del provider (misurato il 25/10/2026).
Qui il tool e' uno che il microcompact non conosce, perche' la prova sia sulla
compattazione e non sul microcompact.
"""

from __future__ import annotations

from typing import Any

from support.runner import make_spec

from jenny.agent.inflight_compaction import (
    VERBATIM_USER_MAX_CHARS,
    InFlightCheckpoint,
    apply_checkpoint,
    collect_user_messages,
    deterministic_summary,
    find_request_index,
    format_range,
    plan_cut,
)
from jenny.agent.runner import AgentRunner
from jenny.agent.tools.base import Tool
from jenny.agent.tools.registry import ToolRegistry
from jenny.providers.base import LLMResponse, ToolCallRequest
from jenny.session.history_meta import (
    GOAL_CONTINUE_EVENT,
    INJECTED_EVENT_META,
    MID_TURN_META,
    SUBAGENT_RESULT_EVENT,
)
from jenny.utils.helpers import estimate_prompt_tokens_chain

WINDOW = 12_000
MAX_OUT = 1_000
CHECKPOINT_ASK = "Write a checkpoint that will REPLACE"
SUMMARY = "## Request\nDo the steps.\n## User instructions\nNone\n## Done so far\nsteps 1-N"


class _Report(Tool):
    """Un tool che il microcompact non svuota, con un output da ~1.000 token."""

    name = "long_report"
    description = "run a command"
    parameters = {"type": "object", "properties": {"cmd": {"type": "string"}}}
    read_only = True

    async def execute(self, cmd: str = "", **kwargs: Any) -> str:
        return f"output of {cmd}: " + ("lorem ipsum dolor " * 220)


class _Provider:
    """Rifiuta le richieste oltre la finestra, come un provider vero.

    *summary* e' cio' che risponde a una richiesta di checkpoint; una funzione
    puo' rispondere un ``LLMResponse`` qualunque (un tool call, un errore).
    """

    def __init__(self, steps: int, *, summary: Any = SUMMARY, inject_at: int | None = None):
        self.steps = steps
        self.summary = summary
        self.step = 0
        self.requests: list[list[dict[str, Any]]] = []
        # Tutte le richieste in ordine, coi checkpoint: ("step"|"checkpoint", messaggi).
        self.log: list[tuple[str, list[dict[str, Any]]]] = []
        self.checkpoint_requests: list[list[dict[str, Any]]] = []
        self.overflows = 0
        self.checkpoint_prompt_tokens = 0

        class _Generation:
            max_tokens = MAX_OUT

        self.generation = _Generation()

    def get_default_model(self) -> str:
        return "m"

    async def chat_with_retry(self, *, messages, tools=None, **kwargs):
        estimate, _ = estimate_prompt_tokens_chain(None, "m", messages, tools)
        if estimate + MAX_OUT > WINDOW:
            self.overflows += 1
            return LLMResponse(
                content=f"Error: maximum context length is {WINDOW} tokens",
                finish_reason="error", tool_calls=[], usage={},
            )
        last = messages[-1].get("content")
        # L'usage riporta la stima stessa: un numero diverso sposterebbe la
        # calibrazione dei token (``token_calibration``) e con lei ogni soglia.
        usage = {"prompt_tokens": estimate, "completion_tokens": 10}
        if isinstance(last, str) and CHECKPOINT_ASK in last:
            self.checkpoint_requests.append(list(messages))
            self.log.append(("checkpoint", list(messages)))
            if callable(self.summary):
                return self.summary()
            self.checkpoint_prompt_tokens += estimate
            return LLMResponse(content=self.summary, tool_calls=[], usage=usage)
        self.requests.append(list(messages))
        self.log.append(("step", list(messages)))
        self.step += 1
        if self.step <= self.steps:
            return LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(
                    id=f"c{self.step}", name="long_report", arguments={"cmd": f"step{self.step}"},
                )],
                usage=usage,
            )
        return LLMResponse(content="done", tool_calls=[], usage=usage)

    chat_stream_with_retry = None


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_Report())
    return registry


def _spec(initial: list[dict[str, Any]], **fields: Any):
    fields.setdefault("max_iterations", 80)
    return make_spec(
        initial_messages=initial,
        tools=_registry(),
        context_window_tokens=WINDOW,
        max_tokens=MAX_OUT,
        **fields,
    )


def _initial(request: str = "do 40 steps") -> list[dict[str, Any]]:
    return [{"role": "system", "content": "sys"}, {"role": "user", "content": request}]


def _round(i: int, size: int = 400) -> list[dict[str, Any]]:
    return [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "ssh_exec", "arguments": f'{{"cmd": "s{i}"}}'}},
        ]},
        {"role": "tool", "tool_call_id": f"c{i}", "name": "ssh_exec", "content": "x" * size},
    ]


# --- il caso misurato ---------------------------------------------------------


async def test_a_long_turn_past_the_window_completes():
    provider = _Provider(steps=40)
    result = await AgentRunner(provider).run(_spec(_initial()))

    assert provider.overflows == 0
    assert result.stop_reason == "completed"
    assert result.final_content == "done"
    assert len(provider.checkpoint_requests) >= 2


async def test_the_run_returns_every_step_uncompacted():
    """La vista compattata e' solo per il modello: il turno si salva intero."""
    provider = _Provider(steps=40)
    result = await AgentRunner(provider).run(_spec(_initial()))

    tool_results = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_results) == 40
    assert result.messages[1] == {"role": "user", "content": "do 40 steps"}


async def test_the_checkpoint_request_reuses_the_prefix_about_to_be_sent():
    """Il riassunto parte con la richiesta che stava per partire: e' in cache."""
    provider = _Provider(steps=40)
    await AgentRunner(provider).run(_spec(_initial()))

    at = next(i for i, (kind, _) in enumerate(provider.log) if kind == "checkpoint")
    previous, first = provider.log[at - 1][1], provider.log[at][1]
    assert first[: len(previous)] == previous
    assert CHECKPOINT_ASK in first[-1]["content"]


async def test_after_a_compaction_the_request_carries_the_checkpoint_and_a_short_tail():
    provider = _Provider(steps=40)
    await AgentRunner(provider).run(_spec(_initial()))

    after = provider.requests[-1]
    assert after[0] == {"role": "system", "content": "sys"}
    request = after[1]
    assert request["role"] == "user"
    assert request["content"].startswith("do 40 steps")
    assert "[Context checkpoint" in request["content"]
    assert SUMMARY in request["content"]
    assert after[2]["role"] == "assistant"
    assert len([m for m in after if m.get("role") == "tool"]) < 10


async def test_a_message_the_user_wrote_mid_task_survives_verbatim():
    provider = _Provider(steps=30)
    pending = [[{
        "role": "user", "content": "non toccare la cartella /etc, mi raccomando",
        MID_TURN_META: True,
    }]]

    async def injection_callback():
        return pending.pop() if pending and provider.step == 3 else []

    await AgentRunner(provider).run(_spec(_initial(), injection_callback=injection_callback))

    assert provider.checkpoint_requests, "the turn never compacted"
    last = provider.requests[-1]
    assert "non toccare la cartella /etc, mi raccomando" in last[1]["content"]


async def test_a_summary_that_calls_a_tool_falls_back_without_the_model():
    """Il modello chiama un tool invece di scrivere: dopo due giri a vuoto si fa senza."""
    def refuse():
        return LLMResponse(
            content="",
            tool_calls=[ToolCallRequest(id="x", name="long_report", arguments={"cmd": "ls"})],
            usage={},
        )

    provider = _Provider(steps=40, summary=refuse)
    result = await AgentRunner(provider).run(_spec(_initial()))

    assert provider.overflows == 0
    assert result.final_content == "done"
    # Due compattazioni ci provano col modello (cache e trascritto), le altre no.
    assert len(provider.checkpoint_requests) == 4
    assert "long_report(" in provider.requests[-1][1]["content"]


async def test_the_summary_call_is_counted_in_the_turn_usage():
    provider = _Provider(steps=40)
    result = await AgentRunner(provider).run(_spec(_initial()))

    steps = sum(
        estimate_prompt_tokens_chain(None, "m", r, None)[0] for r in provider.requests
    )
    assert provider.checkpoint_prompt_tokens > 0
    assert result.usage["prompt_tokens"] >= steps + provider.checkpoint_prompt_tokens * 0.9


async def test_a_long_history_with_a_short_turn_is_left_to_the_snip():
    """La storia prima della richiesta la accorciano snip e Consolidator, come prima."""
    history: list[dict[str, Any]] = []
    for i in range(10):
        history += [
            {"role": "user", "content": f"q{i} " + "h" * 3_000},
            {"role": "assistant", "content": f"a{i} " + "h" * 3_000},
        ]
    provider = _Provider(steps=1)
    initial = [{"role": "system", "content": "sys"}, *history,
               {"role": "user", "content": "short task"}]
    result = await AgentRunner(provider).run(_spec(initial))

    assert result.final_content == "done"
    assert provider.checkpoint_requests == []


# --- i pezzi --------------------------------------------------------------------


def test_the_request_is_the_last_user_message_of_the_turn():
    initial = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "now"},
    ]
    assert find_request_index(initial) == 3
    assert find_request_index([{"role": "system", "content": "s"}]) is None


def test_the_cut_falls_on_an_assistant_and_keeps_what_fits():
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "r"}]
    for i in range(6):
        messages += _round(i)
    # Ogni giro ~130 token: una coda da 300 ne tiene due.
    cut = plan_cut(messages, 2, 300)
    assert messages[cut]["role"] == "assistant"
    assert len(messages) - cut == 4


def test_the_last_round_is_kept_even_when_it_alone_is_over_the_tail_budget():
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "r"}]
    for i in range(3):
        messages += _round(i, size=8_000)
    cut = plan_cut(messages, 2, 100)
    assert cut == len(messages) - 2


def test_nothing_to_cut_when_the_chain_is_one_round():
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "r"}, *_round(0)]
    assert plan_cut(messages, 2, 10) is None


def test_the_view_puts_the_checkpoint_after_the_request_and_drops_the_middle():
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "r"}]
    for i in range(4):
        messages += _round(i)
    checkpoint = InFlightCheckpoint(anchor=1, cut=6, summary="S")
    view = apply_checkpoint(messages, checkpoint)

    assert view[0] == messages[0]
    assert view[1]["content"].startswith("r\n\n[Context checkpoint")
    assert view[2:] == messages[6:]
    assert messages[1] == {"role": "user", "content": "r"}


def test_an_out_of_range_checkpoint_leaves_the_messages_alone():
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "r"}]
    assert apply_checkpoint(messages, InFlightCheckpoint(anchor=1, cut=9, summary="S")) is messages
    assert apply_checkpoint(messages, None) is messages


def test_a_request_with_images_keeps_them_and_gets_the_checkpoint_as_text():
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": [{"type": "text", "text": "r"}, image]},
        *_round(0), *_round(1),
    ]
    view = apply_checkpoint(messages, InFlightCheckpoint(anchor=1, cut=4, summary="S"))
    blocks = view[1]["content"]
    assert image in blocks
    assert any("[Context checkpoint" in b.get("text", "") for b in blocks)


def test_only_the_users_own_words_are_kept_verbatim():
    messages = [
        {"role": "user", "content": "keep this", MID_TURN_META: True},
        {"role": "user", "content": "go on", INJECTED_EVENT_META: GOAL_CONTINUE_EVENT},
        {"role": "user", "content": "result", INJECTED_EVENT_META: SUBAGENT_RESULT_EVENT},
        {"role": "assistant", "content": "a"},
    ]
    assert collect_user_messages(["earlier"], messages) == ["earlier", "keep this"]


def test_the_verbatim_messages_keep_the_newest_within_the_cap():
    many = [{"role": "user", "content": f"{i} " + "w" * 1_500} for i in range(20)]
    kept = collect_user_messages([], many)
    assert kept[-1].startswith("19 ")
    assert sum(len(t) for t in kept) <= VERBATIM_USER_MAX_CHARS + 2_000


def test_the_deterministic_checkpoint_lists_calls_and_results():
    messages = [*_round(0), *_round(1)]
    text = deterministic_summary(messages, previous_summary="before")
    assert text.startswith("before")
    assert 'ssh_exec({"cmd": "s0"}) → xxx' in text
    assert 'ssh_exec({"cmd": "s1"})' in text


# --- il turno dopo ----------------------------------------------------------------


def _visible(message: dict[str, Any]) -> dict[str, Any]:
    return {k: message.get(k) for k in ("role", "content", "tool_calls", "tool_call_id")}


async def test_the_next_turn_starts_from_the_compacted_view(tmp_path):
    """Il turno dopo un compito lungo riparte da dove il modello era rimasto.

    Prima del 25/10/2026 rileggeva la catena intera, che non stava nella
    finestra: lo snip buttava 26 messaggi su 27 e il modello non sapeva piu'
    cosa avesse letto. Ora la vista compattata e' salvata con le righe, e la sua
    richiesta comincia con l'ultima del turno prima: la cache del provider regge.
    """
    from support.agent import make_loop

    from jenny.providers.base import GenerationSettings
    from jenny.session.history_meta import CHECKPOINT_META, COMPACTED_META
    from jenny.session.keys import UNIFIED_SESSION_KEY

    provider = _Provider(steps=40)
    provider.generation = GenerationSettings(max_tokens=MAX_OUT)
    window = 40_000
    loop = make_loop(tmp_path, provider=provider, context_window_tokens=window)
    loop.tools.register(_Report())

    global WINDOW
    saved_window, WINDOW = WINDOW, window
    try:
        await loop.process_direct("do 40 steps", session_key=UNIFIED_SESSION_KEY)
        assert provider.checkpoint_requests, "the first turn never compacted"
        last_of_first = provider.requests[-1]
        await loop.process_direct("what was step 3?", session_key=UNIFIED_SESSION_KEY)
    finally:
        WINDOW = saved_window

    assert provider.overflows == 0
    rows = loop.sessions.get_or_create(UNIFIED_SESSION_KEY).messages
    assert any(r.get(COMPACTED_META) for r in rows)
    assert sum(1 for r in rows if r.get(CHECKPOINT_META)) == 1
    assert len([r for r in rows if r.get("role") == "tool"]) == 40

    first_of_second = provider.requests[-1]
    assert "[Context checkpoint" in str(first_of_second)
    tools_seen = [m for m in first_of_second if m.get("role") == "tool"]
    assert len(tools_seen) == len([m for m in last_of_first if m.get("role") == "tool"]) < 40
    # Dopo il system, la richiesta nuova comincia con l'ultima del turno prima.
    prefix = [_visible(m) for m in last_of_first[1:]]
    assert [_visible(m) for m in first_of_second[1:len(last_of_first)]] == prefix


def _long_file_round(i: int) -> list[dict[str, Any]]:
    body = "a" * 5_000 + f" CODICE: volpe-{i} " + "b" * 5_000
    return [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "read_file", "arguments": f'{{"path": "p{i}.txt"}}'}},
        ]},
        {"role": "tool", "tool_call_id": f"c{i}", "name": "read_file", "content": body},
    ]


def test_the_transcript_keeps_a_value_in_the_middle_of_a_long_result():
    """Visto sul telefono: un tetto fisso di 1.500 caratteri tagliava via il codice."""
    messages = [m for i in range(4) for m in _long_file_round(i)]
    text = format_range(messages, previous_summary=None, max_chars=100_000)
    assert all(f"CODICE: volpe-{i}" in text for i in range(4))


def test_a_tight_transcript_cuts_results_but_not_below_the_floor():
    messages = [m for i in range(4) for m in _long_file_round(i)]
    text = format_range(messages, previous_summary=None, max_chars=8_000, min_result_chars=1_500)
    assert "CODICE: volpe-3" not in text
    assert "a" * 1_500 in text
