"""``agents.defaults.mascotMood``: alias, default, round-trip.

L'altro campo che stava qui, ``mascotMoodModelPreset``, e' uscito il 24/09/2026
con la richiesta al modello che serviva: la sua uscita dal file e' provata in
``test_retired_config_keys.py``.
"""

from __future__ import annotations

from jenny.config.schema import Config


def test_mascot_mood_defaults_on():
    """Accese: leggere gli emoji non costa niente."""
    assert Config().agents.defaults.mascot_mood is True


def test_mascot_mood_accepts_camel_and_snake_aliases():
    camel = Config.model_validate({"agents": {"defaults": {"mascotMood": False}}})
    snake = Config.model_validate({"agents": {"defaults": {"mascot_mood": False}}})
    for config in (camel, snake):
        assert config.agents.defaults.mascot_mood is False


def test_mascot_mood_serializes_in_camel_case():
    config = Config.model_validate({"agents": {"defaults": {"mascotMood": True}}})
    dumped = config.model_dump(mode="json", by_alias=True)["agents"]["defaults"]
    assert dumped["mascotMood"] is True
    assert "mascot_mood" not in dumped
    assert "mascotMoodModelPreset" not in dumped
