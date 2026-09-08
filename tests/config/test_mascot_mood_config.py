"""I due campi dell'umore della mascotte su ``agents.defaults``: alias, default, round-trip."""

from __future__ import annotations

from jenny.config.schema import Config


def test_mascot_mood_defaults_on_without_a_preset():
    """Acceso, e senza preset: la domanda va al modello del turno."""
    defaults = Config().agents.defaults
    assert defaults.mascot_mood is True
    assert defaults.mascot_mood_model_preset is None


def test_mascot_mood_accepts_camel_and_snake_aliases():
    camel = Config.model_validate({
        "agents": {"defaults": {"mascotMood": True, "mascotMoodModelPreset": "cheap"}}
    })
    snake = Config.model_validate({
        "agents": {"defaults": {"mascot_mood": True, "mascot_mood_model_preset": "cheap"}}
    })
    for config in (camel, snake):
        assert config.agents.defaults.mascot_mood is True
        assert config.agents.defaults.mascot_mood_model_preset == "cheap"


def test_mascot_mood_serializes_in_camel_case():
    config = Config.model_validate({
        "agents": {"defaults": {"mascotMood": True, "mascotMoodModelPreset": "cheap"}}
    })
    dumped = config.model_dump(mode="json", by_alias=True)["agents"]["defaults"]
    assert dumped["mascotMood"] is True
    assert dumped["mascotMoodModelPreset"] == "cheap"
    assert "mascot_mood" not in dumped
