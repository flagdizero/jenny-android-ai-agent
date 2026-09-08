"""Il sidecar dell'umore, nelle parti che non parlano col provider.

Selezione dell'input dalla coda della sessione, costruzione della richiesta,
lettura della lettera, scelta del modello, e la chiamata con un provider finto.
Le regole sono quelle di ``.agent/mascot-mood-plan.md`` (D3–D7, D9).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from jenny.config.schema import Config
from jenny.cron.session_turns import CRON_HISTORY_META
from jenny.providers.base import LLMResponse
from jenny.session import mascot_mood as mm
from jenny.session.history_meta import INJECTED_EVENT_META, SUBAGENT_RESULT_EVENT
from jenny.session.manager import Session

LONG_REPLY = "Fatto: ho spostato la riunione alle 16 e avvisato tutti. Spero vada bene!"
assert len(LONG_REPLY) >= mm.MOOD_MIN_ASSISTANT_CHARS


def _session(*rows: tuple) -> Session:
    session = Session(key="unified:default")
    for role, content, *meta in rows:
        session.add_message(role, content, **(meta[0] if meta else {}))
    return session


# --- mood_inputs -------------------------------------------------------------------


def test_inputs_take_the_last_exchange_not_the_first():
    session = _session(
        ("user", "prima domanda"),
        ("assistant", "prima risposta abbastanza lunga da contare come tale, ok"),
        ("user", "seconda domanda"),
        ("assistant", LONG_REPLY),
    )
    inputs = mm.mood_inputs(session)
    assert inputs == mm.MoodInputs(user="seconda domanda", assistant=LONG_REPLY)


def test_inputs_none_when_the_last_row_is_the_user():
    """Dopo un errore l'ultima riga e' dell'utente: l'errore ha gia' la sua faccia."""
    session = _session(
        ("user", "domanda"),
        ("assistant", LONG_REPLY),
        ("user", "riprova"),
    )
    assert mm.mood_inputs(session) is None


def test_inputs_none_when_the_reply_is_too_short():
    session = _session(("user", "ok?"), ("assistant", "Ok."))
    assert mm.mood_inputs(session) is None


def test_inputs_none_without_messages_or_without_a_user_line():
    assert mm.mood_inputs(Session(key="k")) is None
    assert mm.mood_inputs(_session(("assistant", LONG_REPLY))) is None


def test_inputs_skip_command_and_synthetic_rows():
    """Un ``/model`` o un rientro di subagent in coda non sono "l'utente ha detto"."""
    session = _session(
        ("user", "com'e' andato il backup?"),
        ("assistant", LONG_REPLY),
        ("user", "/model deep", {"_command": True}),
        ("assistant", "Switched model preset to `deep`.", {"_command": True}),
        (
            "user",
            "Scheduled cron job triggered: 30s-test\n\nInternal reminder prompt",
            {CRON_HISTORY_META: True},
        ),
        (
            "user",
            "Subagent result: done",
            {INJECTED_EVENT_META: SUBAGENT_RESULT_EVENT},
        ),
    )
    inputs = mm.mood_inputs(session)
    assert inputs is not None
    assert inputs.user == "com'e' andato il backup?"
    assert inputs.assistant == LONG_REPLY


def test_inputs_skip_tool_rows_and_blank_content():
    session = _session(
        ("user", "domanda"),
        ("assistant", LONG_REPLY),
        ("tool", "risultato di uno strumento"),
        ("assistant", "   "),
    )
    session.messages.append({"role": "assistant", "content": None, "tool_calls": [{}]})
    inputs = mm.mood_inputs(session)
    assert inputs is not None
    assert inputs.assistant == LONG_REPLY


def test_inputs_strip_think_blocks():
    session = _session(
        ("user", "domanda"),
        ("assistant", "<think>ragiono a lungo</think>" + LONG_REPLY),
    )
    inputs = mm.mood_inputs(session)
    assert inputs is not None
    assert inputs.assistant == LONG_REPLY


def test_inputs_truncate_the_reply_from_the_head_and_the_question_from_the_tail():
    reply = "x" * 1000 + " purtroppo non ci sono riuscita."
    question = "perche' " * 100
    session = _session(("user", question), ("assistant", reply))
    inputs = mm.mood_inputs(session)
    assert inputs is not None
    assert len(inputs.assistant) == mm.MOOD_ASSISTANT_MAX_CHARS
    assert inputs.assistant.startswith("…")
    assert inputs.assistant.endswith("purtroppo non ci sono riuscita.")
    assert len(inputs.user) == mm.MOOD_USER_MAX_CHARS
    assert inputs.user.startswith("perche'")
    assert inputs.user.endswith("…")


# --- build_mood_request --------------------------------------------------------------


def test_request_is_first_person_with_the_bot_name_and_only_the_exchange():
    inputs = mm.MoodInputs(user="ciao", assistant=LONG_REPLY)
    messages = mm.build_mood_request(inputs, bot_name="Nina")
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"].startswith("You are Nina,")
    assert "ONE letter" in messages[0]["content"]
    for letter in "ABCD":
        assert f"{letter} = " in messages[0]["content"]
    assert "E = " not in messages[0]["content"]
    assert messages[1]["content"] == f"They wrote:\nciao\n\nYou replied:\n{LONG_REPLY}\n\nLetter:"
    # Sotto le sessanta parole: e' il vincolo di costo del piano (D6).
    assert len(messages[0]["content"].split()) < 60


def test_request_falls_back_to_jenny_when_the_name_is_blank():
    inputs = mm.MoodInputs(user="ciao", assistant=LONG_REPLY)
    assert mm.build_mood_request(inputs, bot_name="  ")[0]["content"].startswith("You are Jenny,")


# --- parse_mood --------------------------------------------------------------------


def test_parse_reads_the_letter_in_any_dress():
    assert mm.parse_mood("A") == "happy"
    assert mm.parse_mood(" b") == "sad"
    assert mm.parse_mood("C.") == "angry"
    assert mm.parse_mood("(D)") == "neutral"
    assert mm.parse_mood("**B**") == "sad"


def test_the_retired_fifth_letter_is_neutral_and_not_a_mood():
    """L'alfabeto e' passato da cinque lettere a quattro: la E non vale piu' niente."""
    assert mm.parse_mood("E") == "neutral"
    assert "worried" not in mm.MOODS
    assert "surprised" not in mm.MOODS


def test_parse_is_neutral_for_words_blanks_and_strays():
    assert mm.parse_mood("Bene, sono felice") == "neutral"
    assert mm.parse_mood("") == "neutral"
    assert mm.parse_mood(None) == "neutral"
    assert mm.parse_mood("F") == "neutral"
    assert mm.parse_mood("Sono A") == "neutral"


def test_every_letter_maps_to_a_known_mood():
    assert set(mm._LETTER_TO_MOOD.values()) == set(mm.MOODS)


# --- resolve_mood_model --------------------------------------------------------------


def test_model_is_the_turn_model_without_a_preset():
    config = Config()
    assert mm.resolve_mood_model(config, "turn-model") == "turn-model"


def test_model_comes_from_the_preset_when_configured():
    config = Config.model_validate({
        "agents": {"defaults": {"mascotMoodModelPreset": "cheap"}},
        "modelPresets": {"cheap": {"model": "tiny-1", "provider": "other"}},
    })
    assert mm.resolve_mood_model(config, "turn-model") == "tiny-1"


def test_unknown_or_modelless_preset_falls_back_to_the_turn_model():
    unknown = Config.model_validate({"agents": {"defaults": {"mascotMoodModelPreset": "nope"}}})
    assert mm.resolve_mood_model(unknown, "turn-model") == "turn-model"
    modelless = Config.model_validate({
        "agents": {"defaults": {"mascotMoodModelPreset": "p"}},
        "modelPresets": {"p": {"temperature": 0.1}},
    })
    assert mm.resolve_mood_model(modelless, "turn-model") == "turn-model"


# --- classify_mood -------------------------------------------------------------------


def _provider(content: str | None, *, raises: bool = False, finish_reason: str = "stop"):
    provider = MagicMock()
    if raises:
        provider.chat_with_retry = AsyncMock(side_effect=RuntimeError("boom"))
    else:
        provider.chat_with_retry = AsyncMock(
            return_value=LLMResponse(
                content=content, finish_reason=finish_reason, usage={"total_tokens": 7}
            )
        )
    return provider


async def test_classify_calls_the_provider_small_and_returns_mood_and_response():
    provider = _provider("B")
    inputs = mm.MoodInputs(user="ciao", assistant=LONG_REPLY)

    mood, response = await mm.classify_mood(provider, "m", inputs, bot_name="Jenny")

    assert mood == "sad"
    assert response is not None and response.usage == {"total_tokens": 7}
    kwargs = provider.chat_with_retry.await_args.kwargs
    assert kwargs["model"] == "m"
    assert kwargs["max_tokens"] == mm.MOOD_MAX_TOKENS
    assert kwargs["temperature"] == mm.MOOD_TEMPERATURE
    assert kwargs["reasoning_effort"] == mm.MOOD_REASONING_EFFORT
    assert kwargs["tools"] is None
    messages = provider.chat_with_retry.await_args.args[0]
    assert messages == mm.build_mood_request(inputs, bot_name="Jenny")


async def test_classify_is_neutral_and_silent_on_provider_failure():
    inputs = mm.MoodInputs(user="ciao", assistant=LONG_REPLY)
    assert await mm.classify_mood(_provider(None, raises=True), "m", inputs, bot_name="J") == (
        "neutral",
        None,
    )
    mood, response = await mm.classify_mood(
        _provider("A", finish_reason="error"), "m", inputs, bot_name="J"
    )
    assert mood == "neutral"
    assert response is not None  # la risposta d'errore torna comunque, per la contabilita'


async def test_classify_is_neutral_when_the_budget_went_to_thinking():
    """Contenuto vuoto e ``finish_reason="length"``: il modello ha pensato e basta.

    E' il caso misurato sul telefono con DeepSeek V4 prima della mappa di
    thinking: neutro, ma con la risposta restituita per la contabilita'.
    """
    inputs = mm.MoodInputs(user="ciao", assistant=LONG_REPLY)
    mood, response = await mm.classify_mood(
        _provider("", finish_reason="length"), "thinker", inputs, bot_name="J"
    )
    assert mood == "neutral"
    assert response is not None
    # La seconda volta non deve rifare l'avviso, ma il verdetto e' lo stesso.
    mood, _ = await mm.classify_mood(
        _provider(None, finish_reason="length"), "thinker", inputs, bot_name="J"
    )
    assert mood == "neutral"
