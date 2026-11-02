"""Il cancello «wiki attiva»: uno solo, e chiude se la config non si legge.

Fino al 02/11/2026 ce n'erano due: la route HTTP chiudeva su una config
illeggibile, la RPC (salvo ``audit.create``) lasciava passare.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.webui import commands
from jenny.webui.wiki_routes import WikiRoutes


def _broken_config():
    raise ValueError("config.json is not JSON")


def _config(enabled: bool):
    return lambda: SimpleNamespace(wiki=SimpleNamespace(enabled=enabled))


def _routes() -> WikiRoutes:
    return WikiRoutes(check_api_token=lambda _r: True, get_workspace_root=lambda: Path("."),
                      json_safe=lambda v: v)


@pytest.mark.parametrize("load, message", [
    (_broken_config, "wiki is unavailable"),
    (_config(False), "wiki is disabled"),
])
def test_rpc_and_route_refuse_alike(monkeypatch, load, message) -> None:
    monkeypatch.setattr("jenny.config.loader.load_config", load)
    with pytest.raises(commands.CommandError) as err:
        commands._require_wiki_enabled()
    assert (err.value.code, err.value.message) == ("unavailable", message)
    refused = _routes()._check_wiki_enabled()
    assert refused is not None and refused.status_code == 503
    assert message.encode() in refused.body


def test_an_enabled_wiki_passes_both(monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.loader.load_config", _config(True))
    commands._require_wiki_enabled()
    assert _routes()._check_wiki_enabled() is None
