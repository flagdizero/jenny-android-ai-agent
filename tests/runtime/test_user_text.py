"""Le frasi per l'utente: due lingue con le stesse chiavi, e la lingua letta dal vivo."""

from __future__ import annotations

import string

import pytest

from jenny.config.loader import save_config
from jenny.config.schema import Config
from jenny.runtime import user_text
from jenny.runtime.context import get_runtime_context
from jenny.runtime.user_text import ERROR_KINDS, keys, languages, say


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_every_language_has_the_same_keys_and_the_same_fields() -> None:
    """Una chiave in una lingua sola sarebbe un ripiego silenzioso sull'inglese;
    un campo in una stesura sola un ``KeyError`` (o un ``{campo}`` letterale) in
    produzione, nella lingua che i test non guardano."""
    reference = keys("en")
    for lang in languages():
        assert keys(lang) == reference, lang
        for key in reference:
            assert _fields(say.__globals__["_TEXT"][lang][key]) == _fields(
                say.__globals__["_TEXT"]["en"][key]
            ), (lang, key)


def test_a_missing_key_raises() -> None:
    with pytest.raises(KeyError):
        say("no.such.key", "it")


def test_an_unknown_language_falls_back_to_english() -> None:
    assert say("stop.none", "fr") == say("stop.none", "en")
    assert say("stop.none", " IT ") == say("stop.none", "it") != say("stop.none", "en")


def test_fields_are_filled() -> None:
    assert say("stop.helpers_running", "it", count=3).startswith("3 aiutanti")
    assert "(7)" in say("turn.max_iterations", "en", max_iterations=7)


def test_the_error_kinds_are_the_websocket_contract() -> None:
    """Gli stessi tipi di ``error.kind`` nel payload WebSocket, che la WebUI legge."""
    assert ERROR_KINDS == {
        "connection", "timeout", "auth", "rate_limit", "server", "credit", "generic",
    }


def test_no_user_text_sends_a_phone_user_to_tokens_or_the_terminal() -> None:
    """Chi tiene un telefono non alza «max output tokens» e non legge logcat."""
    for lang in languages():
        for key in keys(lang):
            text = say(key, lang).lower()
            assert "token" not in text, (lang, key)
            assert "logcat" not in text, (lang, key)


def test_the_language_is_read_from_the_config_at_call_time(tmp_path, monkeypatch) -> None:
    """Nessuna copia in memoria: la config cambia, la frase dopo cambia lingua."""
    path = tmp_path / "config.json"
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    # La fixture della suite fissa l'inglese: qui serve la lettura vera.
    monkeypatch.setattr(user_text, "current_language", user_text._language_from_config)

    config = Config()
    config.agents.defaults.language = "it"
    save_config(config, path)
    assert say("stop.none") == say("stop.none", "it")

    config.agents.defaults.language = "en"
    save_config(config, path)
    assert say("stop.none") == say("stop.none", "en")


def test_the_bot_name_is_read_live_and_never_empty(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.json"
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    assert user_text.current_bot_name() == "Jenny"

    config = Config()
    config.agents.defaults.bot_name = "  Ada "
    save_config(config, path)
    assert user_text.current_bot_name() == "Ada"

    config.agents.defaults.bot_name = "   "
    save_config(config, path)
    assert user_text.current_bot_name() == "Jenny"
