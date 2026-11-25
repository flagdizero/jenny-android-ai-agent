"""«Rete di casa» in officina: un indirizzo privato alla volta, col suo nome.

L'elenco ``security.ssrfWhitelist`` vale per tutti gli strumenti insieme: qui
si accetta solo un indirizzo IP di un altro dispositivo di casa — mai un nome,
un intervallo, il loopback o un indirizzo pubblico — e la scrittura passa da
``store.mutate`` e riallinea il blocco del processo.
"""

from __future__ import annotations

import json

import pytest

from jenny.security import network
from jenny.webui import home_network as hn
from jenny.webui.commands import CommandError, dispatch_command


@pytest.mark.parametrize("raw,entry", [
    ("192.168.1.20", "192.168.1.20/32"),
    (" 10.0.0.7 ", "10.0.0.7/32"),
    ("172.20.1.1", "172.20.1.1/32"),
    ("100.101.102.103", "100.101.102.103/32"),
    ("fd12:3456::1", "fd12:3456::1/128"),
])
def test_one_home_address_is_accepted(raw, entry) -> None:
    assert hn.parse_home_address(raw) == entry


@pytest.mark.parametrize("raw,code", [
    ("nas.local", "not_an_address"),
    ("192.168.1.0/24", "not_an_address"),
    ("", "not_an_address"),
    ("127.0.0.1", "not_home"),
    ("0.0.0.0", "not_home"),
    ("169.254.1.1", "not_home"),
    ("8.8.8.8", "not_home"),
    ("::1", "not_home"),
])
def test_names_ranges_loopback_and_public_addresses_are_refused(raw, code) -> None:
    with pytest.raises(hn.HomeNetworkError) as err:
        hn.parse_home_address(raw)
    assert err.value.code == code


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    from jenny.config import store
    from jenny.config.loader import save_config
    from jenny.config.schema import Config
    from jenny.runtime.context import get_runtime_context

    path = tmp_path / "config.json"
    config = Config()
    config.security.ssrf_whitelist = ["100.64.0.0/10"]
    save_config(config, path)
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    store.reset_config_store_state()
    monkeypatch.setattr("jenny.channels.ws_remote.on_remote_listener", lambda: False)
    yield path
    network.configure_ssrf_whitelist([])


async def test_add_and_remove_write_the_config_and_the_live_policy(config_file) -> None:
    out = await dispatch_command(None, "network.add", {"address": "192.168.1.20", "name": "  NAS  "})
    entries = {e["entry"]: e for e in out["home_network"]["entries"]}
    assert entries["192.168.1.20/32"] == {"entry": "192.168.1.20/32", "address": "192.168.1.20",
                                          "name": "NAS", "single": True}
    assert entries["100.64.0.0/10"]["single"] is False, "un intervallo scritto a mano si vede"
    on_disk = json.loads(config_file.read_text(encoding="utf-8"))["security"]
    assert on_disk["ssrfWhitelist"] == ["100.64.0.0/10", "192.168.1.20/32"]
    assert network.is_blocked_ip("192.168.1.20") is False

    await dispatch_command(None, "network.remove", {"entry": "100.64.0.0/10"})
    out = await dispatch_command(None, "network.remove", {"entry": "192.168.1.20/32"})
    assert out["home_network"]["entries"] == []
    assert network.is_blocked_ip("192.168.1.20") is True


async def test_a_refused_address_writes_nothing(config_file) -> None:
    before = config_file.read_text(encoding="utf-8")
    with pytest.raises(CommandError) as err:
        await dispatch_command(None, "network.add", {"address": "127.0.0.1"})
    assert err.value.code == "bad_request" and str(err.value) == "not_home"
    assert config_file.read_text(encoding="utf-8") == before


async def test_not_from_a_remote_connection(config_file, monkeypatch) -> None:
    monkeypatch.setattr("jenny.channels.ws_remote.on_remote_listener", lambda: True)
    with pytest.raises(CommandError) as err:
        await dispatch_command(None, "network.add", {"address": "192.168.1.20"})
    assert err.value.code == "phone_only"
