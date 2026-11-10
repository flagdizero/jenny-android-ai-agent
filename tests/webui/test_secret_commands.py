"""I segreti viaggiano sul WebSocket: chiave del provider, token Telegram, password SSH.

Erano nella query di una GET (``/api/settings/provider/update?api_key=``,
``provider-models``, ``/api/telegram/save?token=``,
``/api/settings/ssh/host/save?password=``): la riga di richiesta la vedono il
log di accesso, i traceback con le variabili locali e chiunque logghi un URL.
Il gateway non legge body HTTP, quindi sono comandi
dell'RPC WebSocket in ``webui/commands.py``. La logica e' rimasta in
``settings_api``/``telegram_api``/``ssh_api``: qui si prova il trasporto — i
parametri, i ganci dopo il salvataggio, la traduzione degli errori.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jenny.config.loader import load_config, save_config
from jenny.config.schema import ProviderConfig
from jenny.providers.factory import provider_fingerprint
from jenny.webui.commands import CommandContext, CommandError, dispatch_command
from jenny.webui.settings_api import WebUISettingsError


def _ctx(tmp_path: Path, **hooks) -> CommandContext:
    return CommandContext(
        get_workspace_root=lambda: tmp_path,
        invalidate_session=lambda _key: None,
        busy_session_keys=lambda: (),
        **hooks,
    )


async def _refused(ctx: CommandContext, method: str, params: dict) -> CommandError:
    with pytest.raises(CommandError) as exc:
        await dispatch_command(ctx, method, params)
    return exc.value


# ---------------------------------------------------------------------------
# settings.provider.update
# ---------------------------------------------------------------------------


async def test_provider_update_saves_the_key_and_fires_settings_changed(
    tmp_path: Path, config_path: Path
) -> None:
    on_changed = MagicMock()
    payload = await dispatch_command(
        _ctx(tmp_path, on_settings_changed=on_changed),
        "settings.provider.update",
        {"name": "my-provider", "api_key": "sk-test", "format": "openai_compat"},
    )
    assert "my-provider" in {p["name"] for p in payload["providers"]}
    saved = {p.name: p.api_key for p in load_config(config_path).providers.providers}
    assert saved["my-provider"] == "sk-test"
    on_changed.assert_called_once()


async def test_provider_update_requires_a_name(tmp_path: Path, config_path: Path) -> None:
    on_changed = MagicMock()
    err = await _refused(
        _ctx(tmp_path, on_settings_changed=on_changed),
        "settings.provider.update",
        {"format": "openai_compat"},
    )
    assert err.code == "bad_request"
    on_changed.assert_not_called()


async def test_provider_update_of_an_inactive_provider_changes_nothing_live(
    tmp_path: Path, config_path: Path
) -> None:
    """Il gancio parte sempre; decide l'impronta del provider attivo (era un
    test della rotta)."""
    config = load_config(config_path)
    config.providers.providers.append(
        ProviderConfig(name="attivo", format="openai_compat", api_key="sk-attivo")
    )
    config.providers.providers.append(
        ProviderConfig(name="dormiente", format="openai_compat", api_key="sk-1")
    )
    config.providers.default = "attivo"
    save_config(config, config_path)
    before = provider_fingerprint(load_config(config_path))

    on_changed = MagicMock()
    await dispatch_command(
        _ctx(tmp_path, on_settings_changed=on_changed),
        "settings.provider.update",
        {"name": "dormiente", "api_key": "sk-2"},
    )
    on_changed.assert_called_once()
    saved = load_config(config_path)
    assert {p.name: p.api_key for p in saved.providers.providers}["dormiente"] == "sk-2"
    assert provider_fingerprint(saved) == before


async def test_a_missing_hook_is_logged_not_fatal(
    tmp_path: Path, config_path: Path
) -> None:
    from loguru import logger

    seen: list[str] = []
    sink = logger.add(lambda m: seen.append(str(m)), level="WARNING")
    try:
        await dispatch_command(
            _ctx(tmp_path), "settings.provider.update", {"name": "p", "api_key": "sk"}
        )
    finally:
        logger.remove(sink)
    assert any("on_settings_changed is not wired" in line for line in seen)


async def test_an_unexpected_error_is_internal_and_mute(
    tmp_path: Path, config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_a, **_k):
        raise RuntimeError("kaboom: /Users/someone/workspace/config.json")

    monkeypatch.setattr("jenny.webui.settings_api.update_provider", boom)
    on_changed = MagicMock()
    err = await _refused(
        _ctx(tmp_path, on_settings_changed=on_changed),
        "settings.provider.update",
        {"name": "p", "api_key": "sk"},
    )
    assert err.code == "internal"
    assert "kaboom" not in err.message and "config.json" not in err.message
    on_changed.assert_not_called()


# ---------------------------------------------------------------------------
# settings.provider.models
# ---------------------------------------------------------------------------


async def test_provider_models_passes_the_typed_key(
    tmp_path: Path, config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict] = []

    def fake(query):
        seen.append(query)
        return {"provider": "x", "models": [], "model_count": 0, "status": "ok"}

    monkeypatch.setattr("jenny.webui.settings_api.provider_models_payload", fake)
    payload = await dispatch_command(
        _ctx(tmp_path),
        "settings.provider.models",
        {"provider": "x", "api_key": "sk-nuova", "api_base": "https://h/v1", "format": None},
    )
    assert payload["status"] == "ok"
    assert seen == [
        {"provider": ["x"], "api_key": ["sk-nuova"], "api_base": ["https://h/v1"], "format": [""]}
    ]


@pytest.mark.parametrize(("status", "code"), [(400, "bad_request"), (404, "not_found"), (502, "unavailable")])
async def test_a_settings_error_keeps_its_meaning(
    tmp_path: Path, config_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, code: str
) -> None:
    def boom(_query):
        raise WebUISettingsError("provider sconosciuto", status=status)

    monkeypatch.setattr("jenny.webui.settings_api.provider_models_payload", boom)
    err = await _refused(_ctx(tmp_path), "settings.provider.models", {"provider": "x"})
    assert err.code == code
    assert err.message == "provider sconosciuto"


async def test_a_non_string_parameter_is_a_bad_request(tmp_path: Path, config_path: Path) -> None:
    err = await _refused(
        _ctx(tmp_path), "settings.provider.models", {"provider": {"nested": True}}
    )
    assert err.code == "bad_request"


# ---------------------------------------------------------------------------
# telegram.save
# ---------------------------------------------------------------------------


async def test_telegram_save_fires_the_channel_hook(
    tmp_path: Path, config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens: list[str] = []

    async def fake(token):
        tokens.append(token)
        return {"enabled": True}

    monkeypatch.setattr("jenny.webui.telegram_api.save_telegram_token", fake)
    on_tg = MagicMock()
    payload = await dispatch_command(
        _ctx(tmp_path, on_telegram_changed=on_tg), "telegram.save", {"token": "123:abc"}
    )
    assert payload == {"enabled": True}
    assert tokens == ["123:abc"]
    on_tg.assert_called_once()


async def test_a_rejected_telegram_token_does_not_restart_the_channel(
    tmp_path: Path, config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(_token):
        raise WebUISettingsError("cannot reach Telegram: ConnectError", status=502)

    monkeypatch.setattr("jenny.webui.telegram_api.save_telegram_token", fake)
    on_tg = MagicMock()
    err = await _refused(
        _ctx(tmp_path, on_telegram_changed=on_tg), "telegram.save", {"token": "123:abc"}
    )
    assert err.code == "unavailable"
    on_tg.assert_not_called()


# ---------------------------------------------------------------------------
# ssh.host.save
# ---------------------------------------------------------------------------


@pytest.fixture()
def reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jenny.webui.ssh_api.validate_ssh_target", lambda _h: (True, None))


async def test_ssh_host_save_keeps_the_password(
    tmp_path: Path, config_path: Path, reachable
) -> None:
    payload = await dispatch_command(
        _ctx(tmp_path),
        "ssh.host.save",
        {
            "alias": "nas", "host": "example.com", "username": "u", "port": "2222",
            "auth": "password", "password": " con spazi ",
        },
    )
    assert payload["hosts"][0]["alias"] == "nas"
    host = load_config(config_path).tools.ssh.hosts[0]
    assert host.password == " con spazi "
    assert host.port == 2222


async def test_ssh_host_save_without_password_keeps_the_saved_one(
    tmp_path: Path, config_path: Path, reachable
) -> None:
    base = {"alias": "nas", "host": "example.com", "username": "u", "auth": "password"}
    await dispatch_command(_ctx(tmp_path), "ssh.host.save", {**base, "password": "prima"})
    await dispatch_command(_ctx(tmp_path), "ssh.host.save", {**base, "description": "d"})
    assert load_config(config_path).tools.ssh.hosts[0].password == "prima"


async def test_ssh_host_save_refuses_a_password_host_without_password(
    tmp_path: Path, config_path: Path, reachable
) -> None:
    err = await _refused(
        _ctx(tmp_path),
        "ssh.host.save",
        {"alias": "nas", "host": "example.com", "username": "u", "auth": "password"},
    )
    assert err.code == "bad_request"


# ---------------------------------------------------------------------------
# onboarding.save
# ---------------------------------------------------------------------------
#
# La prima chiave API dell'utente viaggiava nella query di
# ``GET /api/onboarding/save?api_key=…``: la stessa riga di richiesta delle
# altre quattro. E' un comando come loro.


_ONBOARDING = {
    "provider_name": "openai",
    "format": "openai_compat",
    "api_key": "sk-test-123",
    "api_base": "",
    "model": "gpt-x",
    "bot_name": "Jenny",
    "bot_icon": "",
    "locale": "it",
}


async def test_onboarding_save_writes_the_key_and_wakes_the_agent(
    tmp_path: Path, config_path: Path
) -> None:
    import asyncio

    from jenny.session.keys import UNIFIED_SESSION_KEY
    from jenny.session.manager import SessionManager

    event = asyncio.Event()
    sessions = SessionManager(tmp_path)
    payload = await dispatch_command(
        _ctx(tmp_path, session_manager=sessions, onboarding_event=event),
        "onboarding.save",
        dict(_ONBOARDING),
    )
    assert payload["chat_id"] == "default"
    assert event.is_set(), "l'agente differito aspetta questo evento per nascere"
    config = load_config(config_path)
    assert config.providers.default == "openai"
    assert config.providers.providers[0].api_key == "sk-test-123"
    assert config.agents.defaults.model == "gpt-x"
    greeting = sessions.get_or_create(UNIFIED_SESSION_KEY).messages[-1]
    assert greeting["content"] == payload["welcome_message"]


async def test_onboarding_save_refuses_a_missing_model(
    tmp_path: Path, config_path: Path
) -> None:
    import asyncio

    event = asyncio.Event()
    err = await _refused(
        _ctx(tmp_path, onboarding_event=event),
        "onboarding.save",
        {**_ONBOARDING, "model": ""},
    )
    assert err.code == "bad_request"
    assert "model" in err.message
    assert not event.is_set()
    assert load_config(config_path).providers.providers == []


async def test_onboarding_save_refuses_a_value_that_is_not_text(
    tmp_path: Path, config_path: Path
) -> None:
    err = await _refused(_ctx(tmp_path), "onboarding.save", {**_ONBOARDING, "model": ["x"]})
    assert err.code == "bad_request"


async def test_onboarding_save_hides_an_unexpected_error(
    tmp_path: Path, config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("jenny.webui.settings_api.save_onboarding", boom)
    err = await _refused(_ctx(tmp_path), "onboarding.save", dict(_ONBOARDING))
    assert err.code == "internal"
    assert "kaboom" not in err.message


def test_the_gateway_hands_the_onboarding_wiring_to_the_commands(tmp_path: Path) -> None:
    """Senza l'evento il comando salverebbe e l'agente non nascerebbe mai: il
    gateway resterebbe ad aspettare l'onboarding fino al riavvio."""
    import asyncio

    from jenny.channels.websocket import WebSocketConfig
    from jenny.webui.gateway_services import build_gateway_services

    event = asyncio.Event()
    sessions = MagicMock()
    services = build_gateway_services(
        config=WebSocketConfig.model_validate({}),
        bus=MagicMock(),
        session_manager=sessions,
        workspace_path=tmp_path,
        default_restrict_to_workspace=False,
        runtime_model_name=None,
        onboarding_event=event,
    )
    assert services.commands.onboarding_event is event
    assert services.commands.session_manager is sessions
