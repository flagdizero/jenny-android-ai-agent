"""La sonda del catalogo modelli si identifica su OpenCode, e tace altrove.

Go chiede che il client si presenti con uno user agent proprio invece del nome
della libreria HTTP, e la lista modelli è una richiesta come le altre: parte da
Jenny e arriva a loro. Non porta invece ``x-opencode-session``, che vale per una
*conversazione* — un catalogo non lo è, e inventargli un ID sporcherebbe proprio
il routing che quell'header serve a guidare.

Il resto del file è la garanzia che conta: verso ogni altro provider gli header
della sonda restano quelli di prima.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jenny.config.loader import save_config
from jenny.config.schema import Config, ProviderConfig
from jenny.providers.opencode import SESSION_HEADER
from jenny.runtime.context import get_runtime_context
from jenny.webui.settings_api import provider_models_payload

GO_BASE = "https://opencode.ai/zen/go/v1"


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.json"
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    return path


def _probe(config_path: Path, *, api_base: str, fmt: str = "openai_compat") -> dict:
    """Esegue la sonda per un provider e ritorna gli header che ha mandato."""
    config = Config()
    config.providers.providers = [ProviderConfig(
        name="p", format=fmt, api_key="k", api_base=api_base,
    )]
    config.providers.default = "p"
    save_config(config, config_path)

    with patch("httpx.get") as get:
        get.return_value = MagicMock(
            json=MagicMock(return_value={"data": [{"id": "m"}]}),
            raise_for_status=MagicMock(),
        )
        payload = provider_models_payload({"provider": ["p"]})

    assert payload["status"] == "available"
    return get.call_args.kwargs["headers"]


class TestTowardOpenCode:

    def test_the_probe_identifies_itself(self, config_path: Path) -> None:
        headers = _probe(config_path, api_base=GO_BASE)
        assert headers["User-Agent"].startswith("jenny/")

    def test_does_not_invent_a_session(self, config_path: Path) -> None:
        headers = _probe(config_path, api_base=GO_BASE)
        assert SESSION_HEADER not in headers

    def test_also_applies_to_the_messages_format(self, config_path: Path) -> None:
        # Su Go la stessa base serve anche i modelli in formato Anthropic.
        headers = _probe(config_path, api_base=GO_BASE, fmt="anthropic")
        assert headers["User-Agent"].startswith("jenny/")
        assert headers["x-api-key"] == "k"

    def test_the_usual_headers_remain(self, config_path: Path) -> None:
        headers = _probe(config_path, api_base=GO_BASE)
        assert headers["Accept"] == "application/json"
        assert headers["Authorization"] == "Bearer k"


class TestTowardTheOthers:

    def test_openai_receives_the_previous_headers(self, config_path: Path) -> None:
        headers = _probe(config_path, api_base="https://api.openai.com/v1")
        assert headers == {"Accept": "application/json", "Authorization": "Bearer k"}

    def test_anthropic_receives_the_previous_headers(self, config_path: Path) -> None:
        headers = _probe(
            config_path, api_base="https://api.anthropic.com", fmt="anthropic",
        )
        assert headers == {
            "Accept": "application/json",
            "x-api-key": "k",
            "anthropic-version": "2023-06-01",
        }

    def test_openrouter_receives_the_previous_headers(self, config_path: Path) -> None:
        headers = _probe(config_path, api_base="https://openrouter.ai/api/v1")
        assert "User-Agent" not in headers
