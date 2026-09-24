"""Un booleano in query si legge allo stesso modo su ogni rotta.

Quattro moduli tenevano la propria lista di valori veri. Tre accettavano
``"on"`` — la forma che manda un checkbox HTML — e strippavano gli spazi;
quello delle skill no, quindi ``?disabled=on`` non disabilitava niente e uno
spazio finale ribaltava il valore. Il valore di questi test non sono le righe
risparmiate: è che le quattro rotte smettano di rispondere in modo diverso alla
stessa domanda.
"""

from __future__ import annotations

import pytest

from jenny.channels.http_utils import parse_flag
from jenny.webui import settings_api, ssh_api, worker_settings

# Le forme che un client manda davvero: un checkbox HTML spedisce ``on``, il JS
# della SPA manda ``true``, e un valore incollato a mano può portarsi gli spazi.
TRUE_FORMS = ["1", "true", "TRUE", "True", "yes", "on", "ON", " on ", "true\n"]
FALSE_FORMS = ["0", "false", "no", "off", "", "  ", "maybe", "2", "onn"]


@pytest.mark.parametrize("raw", TRUE_FORMS)
def test_true_forms_are_true(raw: str) -> None:
    assert parse_flag(raw) is True


@pytest.mark.parametrize("raw", FALSE_FORMS)
def test_false_forms_are_false(raw: str) -> None:
    assert parse_flag(raw) is False


def test_missing_value_is_false() -> None:
    assert parse_flag(None) is False


@pytest.mark.parametrize("raw", TRUE_FORMS)
def test_every_route_agrees_on_true(raw: str) -> None:
    """I tre punti di lettura restano allineati fra loro e all'helper."""
    query = {"flag": [raw]}
    assert ssh_api._flag(query, "flag") is True
    assert worker_settings._flag(query, "flag") is True
    assert settings_api.parse_flag(raw) is True


@pytest.mark.parametrize("raw", FALSE_FORMS)
def test_every_route_agrees_on_false(raw: str) -> None:
    query = {"flag": [raw]}
    assert ssh_api._flag(query, "flag") is False
    assert worker_settings._flag(query, "flag") is False
    assert settings_api.parse_flag(raw) is False


def test_strict_parser_still_rejects_what_it_used_to() -> None:
    """``_parse_bool`` non è permissivo, e non deve diventarlo.

    Condivide le due tuple con l'helper, ma la sua semantica è un'altra: un
    valore che non è né vero né falso è un errore, non un ``False``. Vive in
    ``settings_api`` — ci si è spostato quando i nove applicatori scritti a mano
    lì sono passati per i generalizzatori che ``worker_settings`` aveva già.
    """
    assert settings_api._parse_bool("on", "enabled") is True
    assert settings_api._parse_bool(" off ", "enabled") is False
    with pytest.raises(settings_api.WebUISettingsError):
        settings_api._parse_bool("maybe", "enabled")
