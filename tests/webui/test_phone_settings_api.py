"""Le impostazioni di «Usa il telefono» (``jenny/webui/phone_api.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.config import store
from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config
from jenny.runtime.context import get_runtime_context
from jenny.webui import phone_api


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.json"
    config = Config()
    config.tools.phone.trusted_apps = ["com.whatsapp", "link:maps.example.org"]
    save_config(config, path)
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    store.reset_config_store_state()

    async def state() -> str:
        return "off"

    async def label(package: str) -> str:
        return {"com.whatsapp": "WhatsApp"}.get(package, package)

    monkeypatch.setattr(phone_api.runtime, "phone_state", state)
    monkeypatch.setattr(phone_api.runtime, "app_label", label)
    return path


async def test_the_section_says_mode_service_and_trusted_apps_by_name(config_file) -> None:
    section = await phone_api.phone_section(load_config(config_file))
    assert section == {
        "available": True, "mode": "confirm", "service": "off",
        "trusted": [{"target": "com.whatsapp", "label": "WhatsApp"},
                    {"target": "link:maps.example.org", "label": "maps.example.org"}],
    }


async def test_off_android_the_section_is_not_available(config_file, monkeypatch) -> None:
    async def nowhere() -> str:
        return "unavailable"

    monkeypatch.setattr(phone_api.runtime, "phone_state", nowhere)
    assert (await phone_api.phone_section(load_config(config_file)))["available"] is False


async def test_mode_changes_and_is_written(config_file) -> None:
    section = await phone_api.update_phone(mode="free")
    assert section["mode"] == "free"
    assert load_config(config_file).tools.phone.mode == "free"


async def test_forget_removes_one_trusted_app(config_file) -> None:
    section = await phone_api.update_phone(forget="com.whatsapp")
    assert [t["target"] for t in section["trusted"]] == ["link:maps.example.org"]
    assert load_config(config_file).tools.phone.trusted_apps == ["link:maps.example.org"]


async def test_the_command_refuses_an_unknown_mode_and_writes_nothing(config_file) -> None:
    from jenny.webui.commands import CommandError
    from jenny.webui.phone_commands import phone_update

    with pytest.raises(CommandError):
        await phone_update(None, {"mode": "yolo"})  # type: ignore[arg-type]
    assert load_config(config_file).tools.phone.mode == "confirm"
    out = await phone_update(None, {"mode": "off"})  # type: ignore[arg-type]
    assert out["ok"] is True and out["phone"]["mode"] == "off"


async def test_the_command_is_refused_from_a_remote_connection(config_file, monkeypatch) -> None:
    from jenny.webui.commands import CommandError
    from jenny.webui.phone_commands import phone_update

    monkeypatch.setattr("jenny.channels.ws_remote.on_remote_listener", lambda: True)
    with pytest.raises(CommandError):
        await phone_update(None, {"mode": "free"})  # type: ignore[arg-type]
    assert load_config(config_file).tools.phone.mode == "confirm"
