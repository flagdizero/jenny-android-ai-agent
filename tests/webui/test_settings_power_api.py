"""Il controllo WebUI per ``power.keepAwake`` (wakelock anti-doze).

Prima di questa route l'unico modo di cambiare il modo era editare
``config.json`` a mano: chi trovava i cron in ritardo di ore non aveva nessuna
manopola da girare. Qui si copre il giro completo — lettura nel payload,
scrittura persistita, valore inventato rifiutato — e il fatto che la scrittura
passi da ``store.mutate`` e non da ``save_config``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from support.gateway_http import make_request
from websockets.http11 import Request as WsRequest

from jenny.channels.http_utils import check_api_secret, http_error, http_json_response, parse_query
from jenny.config.loader import load_config, save_config
from jenny.config.schema import KEEP_AWAKE_MODES, Config
from jenny.runtime.context import get_runtime_context
from jenny.webui import settings_api
from jenny.webui.settings_api import (
    WebUISettingsError,
    settings_payload,
    update_power_settings,
)
from jenny.webui.settings_routes import WebUISettingsRouter

_SECRET = "s3cr3t-power"


@pytest.fixture
def config_path(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "config.json"
    save_config(Config(), path)
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    return path


# -- lettura -----------------------------------------------------------------


def test_payload_exposes_the_current_mode_and_the_allowed_ones(config_path) -> None:
    payload = settings_payload()

    assert payload["power"]["keep_awake"] == "turns"
    assert payload["power"]["modes"] == list(KEEP_AWAKE_MODES)


def test_payload_reflects_a_mode_written_in_the_config(config_path) -> None:
    config = Config()
    config.power.keep_awake = "always"
    save_config(config, config_path)

    assert settings_payload()["power"]["keep_awake"] == "always"


# -- scrittura ---------------------------------------------------------------


@pytest.mark.parametrize("mode", KEEP_AWAKE_MODES)
async def test_every_allowed_mode_is_persisted(config_path, mode: str) -> None:
    payload = await update_power_settings({"keep_awake": [mode]})

    assert payload["power"]["keep_awake"] == mode
    assert load_config(config_path).power.keep_awake == mode


async def test_camel_case_alias_is_accepted(config_path) -> None:
    """La UI manda snake_case, ma il config parla camelCase: entrambi passano."""
    await update_power_settings({"keepAwake": ["always"]})

    assert load_config(config_path).power.keep_awake == "always"


async def test_value_is_normalized_before_being_stored(config_path) -> None:
    await update_power_settings({"keep_awake": ["  ALWAYS  "]})

    assert load_config(config_path).power.keep_awake == "always"


async def test_a_changed_mode_reports_that_a_restart_is_needed(config_path) -> None:
    """Il lock di servizio si prende una volta all'avvio: prima del riavvio non
    cambia niente, e la risposta deve dirlo."""
    payload = await update_power_settings({"keep_awake": ["always"]})

    assert payload["requires_restart"] is True


async def test_the_same_mode_again_does_not_ask_for_a_restart(config_path) -> None:
    payload = await update_power_settings({"keep_awake": ["turns"]})

    assert payload["requires_restart"] is False


async def test_a_request_without_the_field_leaves_the_mode_alone(config_path) -> None:
    await update_power_settings({"keep_awake": ["always"]})
    payload = await update_power_settings({})

    assert payload["power"]["keep_awake"] == "always"
    assert load_config(config_path).power.keep_awake == "always"


# -- rifiuto -----------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "sometimes", "on", "1"])
async def test_an_unknown_mode_is_rejected(config_path, bad: str) -> None:
    with pytest.raises(WebUISettingsError, match="keep_awake must be one of"):
        await update_power_settings({"keep_awake": [bad]})


async def test_a_rejected_value_leaves_the_config_untouched(config_path) -> None:
    """Lo schema, in lettura, ricade in silenzio su "turns" quando il valore non
    è riconosciuto. Accettare qui un modo inventato scriverebbe quindi "turns"
    mostrando poi all'utente qualcosa che non ha scelto: meglio un 400."""
    await update_power_settings({"keep_awake": ["always"]})
    with pytest.raises(WebUISettingsError):
        await update_power_settings({"keep_awake": ["nope"]})

    assert load_config(config_path).power.keep_awake == "always"


# -- funnel della config -----------------------------------------------------


def test_module_never_calls_save_config_directly() -> None:
    """``save_config`` riscrive il file intero: chi lo chiama fuori da
    ``store.mutate`` cancella in silenzio ciò che un altro scrittore ha appena
    salvato."""
    source = Path(settings_api.__file__).read_text("utf-8")
    assert "save_config" not in source


async def test_the_write_goes_through_store_mutate(config_path, monkeypatch) -> None:
    from jenny.config import store

    calls: list[str] = []
    real_mutate = store.mutate

    async def counting_mutate(apply, **kwargs):
        calls.append("mutate")
        return await real_mutate(apply, **kwargs)

    monkeypatch.setattr(store, "mutate", counting_mutate)

    await update_power_settings({"keep_awake": ["always"]})

    assert calls == ["mutate"]


# -- copy della UI -----------------------------------------------------------


_UI_ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"


@pytest.mark.parametrize("locale", ["it", "en"])
def test_every_mode_has_its_own_line_of_copy(locale: str) -> None:
    """Un'etichetta col solo nome ("Sempre") non dice che cosa si sta pagando.

    Ogni modo ha la sua riga, e accanto al controllo c'è l'avvertenza sul
    riavvio: senza, chi cambia impostazione crede di aver acceso qualcosa ora.
    """
    strings = json.loads((_UI_ASSETS / "i18n" / f"{locale}.json").read_text("utf-8"))
    battery = strings["settings"]["battery"]

    for mode in KEEP_AWAKE_MODES:
        assert battery["keepAwake"][mode].strip()
    for key in ("keepAwakeTitle", "keepAwakeHint", "keepAwakeRestart", "keepAwakeSaved"):
        assert battery[key].strip()


def test_the_control_lives_in_the_background_activity_section() -> None:
    """Stesso argomento, stessa sezione: l'esenzione dice ad Android di non
    strozzare Jenny, keepAwake se Jenny tiene sveglia la CPU da sé."""
    source = (_UI_ASSETS / "mobile-settings.js").read_text("utf-8")

    assert "_renderKeepAwake" in source
    assert "settings.battery.keepAwake" in source
    assert "settings.battery.keepAwakeRestart" in source
    # Niente etichette inline: la copy sta nei file i18n.
    # Da tendina a segmenti (v. `_renderKeepAwake`): l'id cambia, il fatto che
    # il comando esista e stia qui no.
    assert "keep-awake-seg" in source
    assert "settings.battery.keepAwakeShort." in source, (
        "i segmenti usano le parole corte: quelle lunghe non stanno in un terzo di riga"
    )


# -- strato route ------------------------------------------------------------


def _request(path: str, token: str | None = _SECRET) -> WsRequest:
    return make_request(path, token)


def _router() -> WebUISettingsRouter:
    return WebUISettingsRouter(
        bus=MagicMock(),
        logger=MagicMock(),
        check_api_token=lambda request: check_api_secret(request.headers, request.path, _SECRET),
        parse_query=parse_query,
        json_response=http_json_response,
        error_response=http_error,
    )


async def test_route_requires_auth(config_path) -> None:
    response = await _router().dispatch(
        _request("/api/settings/power/update?keep_awake=always", token=None),
        "/api/settings/power/update",
    )

    assert response.status_code == 401
    assert load_config(config_path).power.keep_awake == "turns"


async def test_route_persists_and_returns_the_settings_payload(config_path) -> None:
    response = await _router().dispatch(
        _request("/api/settings/power/update?keep_awake=always"),
        "/api/settings/power/update",
    )

    assert response.status_code == 200
    body = json.loads(response.body.decode("utf-8"))
    assert body["power"]["keep_awake"] == "always"
    assert body["requires_restart"] is True
    assert load_config(config_path).power.keep_awake == "always"


async def test_route_maps_an_unknown_mode_to_400(config_path) -> None:
    response = await _router().dispatch(
        _request("/api/settings/power/update?keep_awake=sometimes"),
        "/api/settings/power/update",
    )

    assert response.status_code == 400
    assert load_config(config_path).power.keep_awake == "turns"
