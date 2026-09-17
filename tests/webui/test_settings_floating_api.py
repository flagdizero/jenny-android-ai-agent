"""Il controllo WebUI per la mascotte flottante (`floating.enabled`).

Il giro completo: lettura nel payload, scrittura persistita, valori fuori
intervallo rifiutati, e le due cose che distinguono questo interruttore dagli
altri — **si applica subito** (la finestra vive nel service e la config la
legge solo all'avvio del gateway) e **non mente sul permesso** (a
`SYSTEM_ALERT_WINDOW` negato la config resta accesa e la risposta lo dichiara).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config
from jenny.runtime.context import get_runtime_context
from jenny.webui import settings_api
from jenny.webui.settings_api import (
    WebUISettingsError,
    settings_payload,
    update_floating_settings,
)


@pytest.fixture
def config_path(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "config.json"
    save_config(Config(), path)
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    return path


@pytest.fixture
def applied(monkeypatch) -> list[bool]:
    """Sostituisce il push al bridge e registra che è avvenuto.

    Il bridge Kotlin non esiste fuori dal telefono: quello che si misura qui è
    che la route lo chiami, perché è l'unica cosa che rende l'interruttore vivo
    invece che buono dal prossimo riavvio.
    """
    calls: list[bool] = []

    async def fake_apply() -> bool:
        calls.append(True)
        return True

    monkeypatch.setattr("jenny.runtime.floating.apply_floating_config", fake_apply)
    return calls


# -- lettura -----------------------------------------------------------------


def test_payload_exposes_the_switch_and_its_defaults(config_path) -> None:
    floating = settings_payload()["floating"]

    assert floating["enabled"] is False
    assert floating["reply_hold_s"] == 20


def test_payload_says_the_mascot_is_unavailable_off_device(config_path) -> None:
    """Fuori da Android non c'è nessuna finestra da accendere, e la UI deve
    poter nascondere la voce invece di offrire un interruttore inerte."""
    assert settings_payload()["floating"]["available"] is False


def test_payload_reflects_what_is_written_in_the_config(config_path) -> None:
    config = Config()
    config.floating.enabled = True
    config.floating.reply_hold_s = 45
    save_config(config, config_path)

    floating = settings_payload()["floating"]
    assert floating["enabled"] is True
    assert floating["reply_hold_s"] == 45


# -- scrittura ---------------------------------------------------------------


async def test_the_switch_is_persisted(config_path, applied) -> None:
    payload = await update_floating_settings({"enabled": ["true"]})

    assert payload["floating"]["enabled"] is True
    assert load_config(config_path).floating.enabled is True


async def test_turning_it_off_is_persisted(config_path, applied) -> None:
    await update_floating_settings({"enabled": ["true"]})
    await update_floating_settings({"enabled": ["false"]})

    assert load_config(config_path).floating.enabled is False


async def test_the_hold_time_is_persisted(config_path, applied) -> None:
    await update_floating_settings({"reply_hold_s": ["45"]})

    assert load_config(config_path).floating.reply_hold_s == 45


async def test_a_request_without_fields_leaves_everything_alone(config_path, applied) -> None:
    await update_floating_settings({"enabled": ["true"], "reply_hold_s": ["30"]})
    payload = await update_floating_settings({})

    assert payload["floating"]["enabled"] is True
    assert payload["floating"]["reply_hold_s"] == 30


async def test_the_change_is_pushed_to_the_window_right_away(config_path, applied) -> None:
    """La ragione per cui questa route non si limita a scrivere il file.

    La finestra vive nel processo del service e la config la legge all'avvio
    del gateway: senza il push, spegnere la mascotte dalle impostazioni non
    farebbe nulla fino al riavvio dell'app.
    """
    await update_floating_settings({"enabled": ["true"]})

    assert applied == [True]


async def test_the_response_reports_what_android_actually_granted(
    config_path, monkeypatch
) -> None:
    """A permesso negato la config resta accesa e la risposta dice `active`
    falso: l'interruttore deve poter raccontare la differenza fra «l'ho voluta»
    e «Android non me la lascia aprire», invece di rimbalzare su off senza
    spiegare perché."""

    async def refused() -> bool:
        return False

    monkeypatch.setattr("jenny.runtime.floating.apply_floating_config", refused)

    payload = await update_floating_settings({"enabled": ["true"]})

    assert payload["floating"]["enabled"] is True
    assert payload["floating"]["active"] is False
    assert load_config(config_path).floating.enabled is True


async def test_the_settings_page_reports_the_live_permission_state(monkeypatch) -> None:
    """La riga sul permesso deve comparire a **ogni** apertura del pannello.

    Il permesso si concede e si revoca da una schermata di sistema, fuori da
    Jenny: se ``active`` arrivasse solo nella risposta all'interruttore, la riga
    che spiega perché la mascotte non si vede sparirebbe al primo ricaricamento
    — proprio mentre è ancora vera.
    """
    from jenny.webui import settings_routes as sr

    payload = {"floating": {"enabled": True, "available": True}}
    monkeypatch.setattr(sr, "settings_payload", lambda: payload)

    async def refused() -> bool:
        return False

    monkeypatch.setattr("jenny.runtime.floating.floating_active", refused)

    enriched = dict(payload)
    await sr._enrich_floating(enriched)
    assert enriched["floating"]["active"] is False

    # E il contrario: senza contesto Android non si chiede niente al bridge.
    off_device = {"floating": {"enabled": True, "available": False}}
    await sr._enrich_floating(off_device)
    assert "active" not in off_device["floating"]


# -- rifiuto -----------------------------------------------------------------


@pytest.mark.parametrize("bad", ["4", "121", "0", "-5"])
async def test_a_hold_time_out_of_range_is_rejected(config_path, applied, bad: str) -> None:
    """Il range nel messaggio viene dallo schema, non da una copia qui: v.
    ``test_settings_bounds_come_from_the_schema.py``."""
    with pytest.raises(WebUISettingsError, match="must be between 5–120"):
        await update_floating_settings({"reply_hold_s": [bad]})


@pytest.mark.parametrize("bad", ["", "presto", "20s"])
async def test_a_hold_time_that_is_not_a_number_is_rejected(
    config_path, applied, bad: str
) -> None:
    with pytest.raises(WebUISettingsError, match="must be a whole number"):
        await update_floating_settings({"reply_hold_s": [bad]})


async def test_the_camel_case_alias_is_accepted(config_path, applied) -> None:
    """La UI manda snake_case, ma il config parla camelCase: entrambi passano."""
    await update_floating_settings({"replyHoldS": ["45"]})

    assert load_config(config_path).floating.reply_hold_s == 45


async def test_a_rejected_value_leaves_the_config_untouched(config_path, applied) -> None:
    """``mutate`` non salva se la callback solleva: il file resta com'era, e non
    con metà della richiesta applicata."""
    await update_floating_settings({"enabled": ["true"], "reply_hold_s": ["30"]})
    with pytest.raises(WebUISettingsError):
        await update_floating_settings({"enabled": ["false"], "reply_hold_s": ["999"]})

    config = load_config(config_path)
    assert config.floating.enabled is True
    assert config.floating.reply_hold_s == 30


# -- funnel della config -----------------------------------------------------


async def test_the_write_goes_through_store_mutate(config_path, applied, monkeypatch) -> None:
    from jenny.config import store

    calls: list[str] = []
    real_mutate = store.mutate

    async def counting_mutate(apply, **kwargs):
        calls.append("mutate")
        return await real_mutate(apply, **kwargs)

    monkeypatch.setattr(store, "mutate", counting_mutate)
    await update_floating_settings({"enabled": ["true"]})

    assert calls == ["mutate"]


def test_the_bridge_push_is_not_inside_the_mutate_callback() -> None:
    """Il lock della config si tiene per tutta la callback: una chiamata al
    bridge Kotlin là dentro è I/O che può bloccarsi quanto il GIL resta preso,
    con il funnel della config chiuso nel frattempo."""
    source = Path(settings_api.__file__).read_text("utf-8")
    body = source.split("async def update_floating_settings")[1].split("\nasync def ")[0]
    apply_callback = body.split("def _apply(")[1].split("await store.mutate")[0]

    assert "apply_floating_config" not in apply_callback
    assert "apply_floating_config" in body
