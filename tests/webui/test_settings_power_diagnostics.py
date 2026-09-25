"""Il pannello che rende visibile un gateway ucciso dal gestore energetico.

Quando Samsung, MIUI o PowerGenie chiudono Jenny in background il guasto non
lascia niente dietro di sé: nessun errore, nessuna notifica, solo promemoria
che smettono di arrivare e un utente che se ne accorge giorni dopo. Questo
endpoint è l'unico posto in cui quel silenzio diventa una risposta, quindi il
contratto del payload conta: la UI ci disegna sopra tre righe sì/no e la lista
dei buchi, e un campo rinominato la lascerebbe muta senza rompere niente.

La copy sta nei file i18n e la catena di Intent OEM in ``MainActivity.kt``:
entrambe sono asserzioni sul sorgente, nello stile di
``test_mascot_side_contract.py`` — la WebUI non ha un runner JS con DOM.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from support.gateway_http import make_request
from websockets.http11 import Request as WsRequest

from jenny.channels.http_utils import check_api_secret, http_error, http_json_response, parse_query
from jenny.config.loader import save_config
from jenny.config.paths import get_workspace_path, set_workspace_dir
from jenny.config.schema import Config
from jenny.runtime import gap_history, power
from jenny.runtime.context import get_runtime_context
from jenny.webui.settings_api import power_diagnostics_payload
from jenny.webui.settings_routes import WebUISettingsRouter

_SECRET = "s3cr3t-diagnostics"

ROOT = Path(__file__).resolve().parents[2]
_UI_ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
_MAIN_ACTIVITY = (
    ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "flagdizero" / "jenny"
    / "MainActivity.kt"
)


@pytest.fixture
def workspace(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Config e workspace isolati: qui si scrive uno storico dei buchi."""
    previous = get_workspace_path()
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    set_workspace_dir(str(ws))
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)
    yield ws
    set_workspace_dir(str(previous))


@pytest.fixture
def android(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un telefono che risponde: bridge presente, permessi concessi.

    ``power_diagnostics_payload`` importa da ``jenny.runtime.power`` dentro la
    funzione, quindi i nomi si risolvono sul modulo a ogni chiamata ed è là che
    vanno sostituiti.
    """

    async def _true() -> bool:
        return True

    monkeypatch.setattr(power, "alarms_available", lambda: True)
    monkeypatch.setattr(power, "is_battery_exempt", _true)
    monkeypatch.setattr(power, "can_schedule_exact_alarms", _true)
    monkeypatch.setattr(power, "is_wakelock_held", _true)


def _record_gap(ws: Path, *, minutes: int, ended_ms: int) -> None:
    gap_history.record_probe(
        ws, (ended_ms - minutes * 60_000, ended_ms), threshold_min=60
    )


# -- payload -----------------------------------------------------------------


async def test_payload_answers_the_three_questions_the_user_can_act_on(
    workspace, android
) -> None:
    payload = await power_diagnostics_payload()

    assert payload["android"] is True
    assert payload["battery_exempt"] is True
    assert payload["exact_alarms"] is True
    assert payload["wakelock_held"] is True


async def test_payload_says_it_is_not_android_when_there_is_no_bridge(workspace) -> None:
    """Fuori da Android i tre booleani non significano niente e la UI si nasconde."""
    payload = await power_diagnostics_payload()

    assert payload["android"] is False
    assert payload["gaps"] == []


async def test_payload_carries_the_threshold_the_gaps_were_measured_against(
    workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Senza la soglia, "nessuna interruzione" non dice sotto cosa si sta tacendo."""
    config = Config()
    config.power.gap_warning_min = 90
    save_config(config, get_runtime_context().config_path)

    assert (await power_diagnostics_payload())["gap_warning_min"] == 90


async def test_payload_carries_the_recorded_gaps_newest_first(workspace) -> None:
    for index in range(1, 8):
        _record_gap(workspace, minutes=60 + index, ended_ms=10_000_000_000 + index * 60_000)

    gaps = (await power_diagnostics_payload())["gaps"]

    # Cinque: il pannello ne mostra "gli ultimi", non tutto lo storico.
    assert len(gaps) == 5
    assert [g["duration_ms"] for g in gaps] == [
        (60 + index) * 60_000 for index in range(7, 2, -1)
    ]


async def test_every_gap_carries_the_three_fields_the_panel_prints(workspace) -> None:
    """La UI stampa una durata e l'ora d'inizio: entrambe vengono da qui."""
    _record_gap(workspace, minutes=252, ended_ms=10_000_000_000)

    gap = (await power_diagnostics_payload())["gaps"][0]

    assert set(gap) == {"start_ms", "end_ms", "duration_ms"}
    assert gap["duration_ms"] == gap["end_ms"] - gap["start_ms"]


async def test_an_unreadable_history_does_not_take_the_diagnostics_down(workspace) -> None:
    """Telemetria persa, pannello vivo: le altre tre righe restano informative."""
    path = gap_history.history_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ broken", encoding="utf-8")

    assert (await power_diagnostics_payload())["gaps"] == []


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


_PATH = "/api/settings/power/diagnostics"


async def test_route_requires_auth(workspace) -> None:
    response = await _router().dispatch(_request(_PATH, token=None), _PATH)

    assert response.status_code == 401


async def test_route_returns_the_diagnostics_payload(workspace, android) -> None:
    _record_gap(workspace, minutes=252, ended_ms=10_000_000_000)

    response = await _router().dispatch(_request(_PATH), _PATH)

    assert response.status_code == 200
    body = json.loads(response.body.decode("utf-8"))
    assert body["android"] is True
    assert body["battery_exempt"] is True
    assert len(body["gaps"]) == 1


async def test_route_maps_a_broken_bridge_to_500(
    workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un bridge che solleva non deve tornare come un pannello vuoto e falso."""

    async def _boom() -> dict:
        raise RuntimeError("bridge gone")

    monkeypatch.setattr(
        "jenny.webui.settings_routes.power_diagnostics_payload", _boom, raising=True
    )

    response = await _router().dispatch(_request(_PATH), _PATH)

    assert response.status_code == 500


# -- copy della UI -----------------------------------------------------------


_UI_KEYS = (
    "diagTitle",
    "diagExempt",
    "diagExactAlarms",
    "diagWakelock",
    "diagYes",
    "diagNo",
    "diagUnavailable",
    "gapsTitle",
    "gapsHint",
    "gapsEmpty",
    "gapDays",
    "gapHours",
    "gapMinutes",
    "gapToday",
    "gapYesterday",
    "gapOn",
    "oemHint",
    "oemButton",
    "oemLink",
    "oemUnknownBrand",
    "oemOpenFailed",
)


@pytest.mark.parametrize("locale", ["it", "en"])
def test_the_panel_has_its_copy_in_both_locales(locale: str) -> None:
    battery = json.loads(
        (_UI_ASSETS / "i18n" / f"{locale}.json").read_text("utf-8")
    )["settings"]["battery"]

    for key in _UI_KEYS:
        assert battery[key].strip(), f"{key} vuota in {locale}.json"


@pytest.mark.parametrize("locale", ["it", "en"])
def test_the_time_formats_keep_their_placeholders(locale: str) -> None:
    """Durata e ora sono interpolate: un placeholder perso stampa "{time}"."""
    battery = json.loads(
        (_UI_ASSETS / "i18n" / f"{locale}.json").read_text("utf-8")
    )["settings"]["battery"]

    assert "{days}" in battery["gapDays"] and "{hours}" in battery["gapDays"]
    assert "{hours}" in battery["gapHours"] and "{minutes}" in battery["gapHours"]
    assert "{minutes}" in battery["gapMinutes"]
    for key in ("gapToday", "gapYesterday", "gapOn"):
        assert "{time}" in battery[key]
    assert "{date}" in battery["gapOn"]
    assert "{minutes}" in battery["gapsHint"]
    assert "{brand}" in battery["oemLink"]


def test_the_panel_lives_in_the_background_activity_section() -> None:
    source = (_UI_ASSETS / "mobile-settings.js").read_text("utf-8")

    assert "settings-power-diagnostics" in source
    assert "getPowerDiagnostics" in source
    # Nessuna etichetta inline: tutta la copy passa da i18n.t. Le tre righe di
    # stato compongono la chiave (`settings.battery.${key}`), quindi là si
    # cerca il solo suffisso.
    dynamic = {"diagExempt", "diagExactAlarms", "diagWakelock"}
    for key in _UI_KEYS:
        needle = f"'{key}'" if key in dynamic else f"settings.battery.{key}"
        assert needle in source, f"{key} non usata dal pannello"


def test_the_panel_hides_itself_off_android() -> None:
    """Tre "no" su un browser desktop sarebbero tre bugie."""
    source = (_UI_ASSETS / "mobile-settings.js").read_text("utf-8")

    assert "diag.android" in source


def test_the_guidance_card_links_to_dontkillmyapp_for_this_phone() -> None:
    source = (_UI_ASSETS / "mobile-settings.js").read_text("utf-8")

    assert "dontkillmyapp.com" in source
    assert "deviceManufacturer" in source
    # Slug minuscolo e ASCII-safe: Build.MANUFACTURER è testo libero dell'OEM.
    assert "toLowerCase()" in source
    assert "encodeURIComponent" in source
    # Link esterno: la WebView lo devia su una Custom Tab, non sulla SPA.
    assert 'target="_blank"' in source


# -- catena di Intent OEM (MainActivity.kt) ----------------------------------


def _kotlin() -> str:
    return _MAIN_ACTIVITY.read_text(encoding="utf-8")


def _kotlin_fun(name: str) -> str:
    source = _kotlin()
    match = re.search(rf"fun {name}\([^)]*\)[^\n]*\{{\n(.*?)\n    \}}", source, re.S)
    assert match, f"{name}() non trovata in MainActivity.kt"
    return match.group(1)


def test_the_bridge_exposes_what_the_panel_needs() -> None:
    source = _kotlin()

    # Una lettura innocua sta sulla porta sincrona: l'annotazione è sulla riga
    # sopra, e senza il metodo non esiste per il JS.
    index = source.index("fun deviceManufacturer()")
    assert "@JavascriptInterface" in source[index - 120 : index]
    # Aprire una schermata di sistema no: sta sulla porta dei comandi, che solo
    # il frame principale della SPA raggiunge.
    assert '"openBatterySettings" -> openBatterySettings()' in source
    index = source.index("fun openBatterySettings()")
    assert "@JavascriptInterface" not in source[index - 120 : index]


def test_the_worst_offenders_all_have_their_own_screen() -> None:
    """I cinque gestori energetici che uccidono le app a prescindere."""
    candidates = _kotlin_fun("batterySettingsCandidates")

    for vendor in ("samsung", "xiaomi", "redmi", "poco", "huawei", "honor",
                   "oppo", "oneplus", "realme", "vivo"):
        assert f'"{vendor}"' in candidates, f"nessuna schermata per {vendor}"
    for package in ("com.samsung.android.lool", "com.miui.securitycenter",
                    "com.huawei.systemmanager", "com.coloros.safecenter",
                    "com.iqoo.secure"):
        assert package in candidates


def test_the_chain_always_ends_on_a_screen_that_exists() -> None:
    """Le Activity OEM sono API private: l'ultimo anello dev'essere di sistema."""
    candidates = _kotlin_fun("batterySettingsCandidates")

    assert "ACTION_APPLICATION_DETAILS_SETTINGS" in candidates
    assert candidates.rstrip().endswith("+ fallback")


def test_every_attempt_is_guarded() -> None:
    """Un component name morto solleva: un crash qui sarebbe peggio del guasto
    che stiamo segnalando."""
    body = _kotlin_fun("startFirstWorking")

    launch = body.index("startActivity(intent)")
    assert body.index("try {") < launch
    assert "catch (e: Exception)" in body[launch:]
