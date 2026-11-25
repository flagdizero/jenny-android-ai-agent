"""Fixture condivise dai test di ``tests/webui``."""

import pytest
from support import config_file

# La ``config.json`` salvata coi default (``support/config_file.py``), registrata qui.
config_path = config_file.config_path


@pytest.fixture()
def onboarding_probe_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """La domanda di prova del primo avvio risponde sempre.

    ``save_onboarding`` fa una domanda vera al provider prima di salvare: un test
    che prova il salvataggio, e non la prova, non deve uscire in rete con una
    chiave finta (risponderebbe 401, o niente senza rete). Chi prova la domanda
    stessa finge invece ``make_provider`` (``test_settings_api.py``).
    """

    async def _answers(provider, model):
        return None

    monkeypatch.setattr("jenny.webui.settings_api._probe_provider", _answers)
