"""Le due cose che, sul telefono, rendevano inutilizzabile l'anti-doze.

La prima: `SCHEDULE_EXACT_ALARM` arriva **negato** a un'app che punta ad API 33
o più. Dichiararlo nel manifest non lo concede, e misurato su un'installazione
nuova ogni sveglia degradava a inesatta — cron +9m, watchdog +11m, controllo di
rete +1h; concesso a mano, tutte a finestra zero. Il pannello lo diceva già
("Sveglie precise: No") ma non offriva nessun modo di rimediare da dentro
l'app: qui si verifica che il bottone esista, che chiami il bridge e che si
faccia vedere solo quando il permesso manca davvero.

La seconda: le etichette della select "Tenere sveglia la CPU" portavano il
costo della scelta, e una `<option>` nativa non manda il testo a capo — la
parte tagliata era esattamente la clausola sul costo. Il costo ora vive fuori
dalla select, in una riga che segue la selezione.

Come ``test_settings_power_diagnostics.py``: asserzioni sul sorgente (JS, copy
i18n, Kotlin), perché la WebUI non ha un runner JS con DOM.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jenny.config.schema import KEEP_AWAKE_MODES

ROOT = Path(__file__).resolve().parents[2]
_UI_ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
_SETTINGS_JS = _UI_ASSETS / "mobile-settings.js"
_STYLE_CSS = _UI_ASSETS / "mobile-style.css"
_MAIN_ACTIVITY = (
    ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "flagdizero" / "jenny"
    / "MainActivity.kt"
)


def _js() -> str:
    return _SETTINGS_JS.read_text(encoding="utf-8")


def _kotlin() -> str:
    return _MAIN_ACTIVITY.read_text(encoding="utf-8")


def _battery_copy(locale: str) -> dict:
    return json.loads(
        (_UI_ASSETS / "i18n" / f"{locale}.json").read_text("utf-8")
    )["settings"]["battery"]


# -- richiesta del permesso: bridge nativo -----------------------------------


def test_the_bridge_can_open_the_exact_alarm_permission_screen() -> None:
    source = _kotlin()

    index = source.index("fun requestExactAlarmPermission()")
    # Senza l'annotazione il metodo, per il JS, semplicemente non esiste.
    assert "@JavascriptInterface" in source[index - 200 : index]
    body = source[index : index + 1200]
    assert "ACTION_REQUEST_SCHEDULE_EXACT_ALARM" in body
    # L'azione senza il proprio package apre l'elenco di tutte le app.
    assert 'Uri.parse("package:$packageName")' in body


def test_the_request_is_a_no_op_below_android_12() -> None:
    """Sotto API 31 il permesso non esiste e nemmeno la schermata: aprirla
    solleverebbe ActivityNotFoundException con il minSdk 26 dell'app."""
    source = _kotlin()
    body = source[source.index("fun requestExactAlarmPermission()") :][:1200]

    guard = body.index("Build.VERSION_CODES.S")
    assert "return false" in body[guard : guard + 60]
    # La guardia sta prima di qualunque tentativo di aprire la schermata.
    assert guard < body.index("startActivity")


def test_the_request_never_takes_the_webview_down() -> None:
    source = _kotlin()
    body = source[source.index("fun requestExactAlarmPermission()") :][:1200]

    launch = body.index("startActivity(intent)")
    assert "runOnUiThread" in body[:launch]
    assert body.rindex("try {", 0, launch) < launch
    assert "catch (e: Exception)" in body[launch:]


# -- richiesta del permesso: UI ----------------------------------------------


def _render_exact_alarm_fn() -> str:
    match = re.search(
        r"_renderExactAlarmRequest\(diag\) \{\n(.*?)\n  \}", _js(), re.S
    )
    assert match, "_renderExactAlarmRequest non trovata in mobile-settings.js"
    return match.group(1)


def test_the_button_is_offered_only_when_the_permission_is_missing() -> None:
    """A permesso concesso sarebbe l'ennesimo avviso che si impara a ignorare,
    e da un gateway che il campo non manda non si deduce che manchi."""
    body = _render_exact_alarm_fn()

    assert "diag.exact_alarms !== false" in body
    assert "return ''" in body


def test_the_button_degrades_on_an_older_bridge() -> None:
    """APK più vecchio della UI: nessun metodo da chiamare, nessun bottone."""
    body = _render_exact_alarm_fn()

    assert "typeof native.requestExactAlarmPermission !== 'function'" in body


def test_the_button_sits_in_the_diagnostics_flow_and_calls_the_bridge() -> None:
    source = _js()

    # Disegnato subito sotto le tre righe sì/no, dove sta la riga che dice "no".
    assert "${this._renderExactAlarmRequest(diag)}" in source
    assert "btn-exact-alarms" in source
    assert "native.requestExactAlarmPermission()" in source
    # Schermata irraggiungibile: dirlo, non lasciare il tap senza conseguenze.
    assert "settings.battery.exactAlarmsFailed" in source


@pytest.mark.parametrize("locale", ["it", "en"])
def test_the_request_has_its_copy_in_both_locales(locale: str) -> None:
    battery = _battery_copy(locale)

    for key in ("exactAlarmsHint", "exactAlarmsRestart", "exactAlarmsButton",
                "exactAlarmsFailed"):
        assert battery[key].strip(), f"{key} vuota in {locale}.json"


@pytest.mark.parametrize("locale", ["it", "en"])
def test_the_copy_admits_the_grant_is_not_retroactive(locale: str) -> None:
    """Le sveglie già in coda restano inesatte fino al riavvio del gateway:
    una copy che promettesse effetto immediato manderebbe l'utente a
    controllare un cron che continua a slittare, credendo di aver risolto."""
    restart = _battery_copy(locale)["exactAlarmsRestart"].lower()

    assert "riavvio" in restart or "restart" in restart


def test_the_panel_prints_the_restart_caveat_next_to_the_button() -> None:
    body = _render_exact_alarm_fn()

    assert "settings.battery.exactAlarmsHint" in body
    assert "settings.battery.exactAlarmsRestart" in body


# -- costo della scelta keepAwake fuori dalla select -------------------------


@pytest.mark.parametrize("locale", ["it", "en"])
def test_every_keep_awake_mode_states_its_cost(locale: str) -> None:
    costs = _battery_copy(locale)["keepAwakeCost"]

    for mode in KEEP_AWAKE_MODES:
        assert costs[mode].strip(), f"keepAwakeCost.{mode} vuota in {locale}.json"


@pytest.mark.parametrize("locale", ["it", "en"])
def test_the_option_labels_are_short_enough_not_to_be_truncated(locale: str) -> None:
    """Una <option> nativa non va a capo: quello che non ci sta sparisce, e
    prima a sparire era proprio il costo. Il nome resta, il costo sta sotto."""
    battery = _battery_copy(locale)

    for mode in KEEP_AWAKE_MODES:
        label = battery["keepAwake"][mode]
        assert len(label) <= 40, f"keepAwake.{mode} troppo lunga in {locale}.json: {label}"
        # Il costo non deve essere rimasto anche dentro l'etichetta.
        assert label.strip() != battery["keepAwakeCost"][mode].strip()


def test_the_cost_line_follows_the_selection() -> None:
    source = _js()

    assert 'id="keep-awake-cost"' in source
    assert "settings.battery.keepAwakeCost." in source
    # Cambia al tocco del segmento, prima ancora che il salvataggio torni: è
    # l'informazione che l'utente sta valutando. Il comando è passato da
    # tendina a segmenti (la tavola lo vuole così, e il pezzo esisteva già):
    # cambia l'evento, non la regola.
    click = source.index("buttons.forEach(btn => btn.addEventListener('click'")
    assert "const show = (mode)" in source[:click], "`mostra` definita dopo l'uso"
    assert "show(mode)" in source[click : click + 400]


def test_an_unknown_mode_prints_nothing_instead_of_the_raw_key() -> None:
    """`power.modes` viene dal gateway: uno più nuovo del client può mandare un
    modo che qui non ha frase, e `i18n.t` in quel caso ritorna la chiave."""
    match = re.search(r"_keepAwakeCost\(mode\) \{\n(.*?)\n  \}", _js(), re.S)
    assert match, "_keepAwakeCost non trovata in mobile-settings.js"

    assert "text === key ? '' : text" in match.group(1)


def test_the_cost_line_can_wrap() -> None:
    """Il motivo per cui esiste: prendersi le righe che le servono."""
    css = _STYLE_CSS.read_text(encoding="utf-8")

    assert ".settings-choice-cost" in css
    block = css[css.index(".settings-choice-cost") :][:400]
    # Niente troncamento reintrodotto per estetica.
    assert "nowrap" not in block
    assert "text-overflow" not in block
