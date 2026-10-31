"""Tests for proxy environment variable handling in OpenAICompatProvider."""

import httpx

from jenny.providers.openai_compat_provider import OpenAICompatProvider


class TestLocalEndpointProxyDisabled:
    """Local endpoints must bypass proxy to avoid routing LAN traffic through it.

    Con un proxy nell'ambiente, il client verso un indirizzo locale non ne monta
    nessuno: è il transport esplicito a spegnere i proxy d'ambiente di httpx.
    Controllare solo il tipo del transport non bastava — è lo stesso anche senza.
    """

    async def test_local_disables_proxy(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
        monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
        provider = OpenAICompatProvider(
            api_key="test", api_base="http://localhost:11434/v1", default_model="",
        )
        await provider._ensure_client()
        assert isinstance(provider._http_client._transport, httpx.AsyncHTTPTransport)
        assert not provider._http_client._mounts

    async def test_a_lan_ip_is_not_local_and_keeps_the_env_proxy(self, monkeypatch):
        """Locale è solo il loopback (``is_local_endpoint``: su Android la sandbox
        dell'app non raggiunge la LAN). Un IP di LAN è trattato come remoto, e il
        proxy d'ambiente resta. Il test di prima diceva il contrario, ma
        controllava solo il tipo del transport, e passava comunque."""
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
        monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
        provider = OpenAICompatProvider(
            api_key="test", api_base="http://192.168.1.50:1234/v1", default_model="",
        )
        assert provider._is_local is False
        await provider._ensure_client()
        assert provider._http_client._mounts


class TestCloudEndpointProxyEnabled:
    """Cloud endpoints must respect proxy env vars for corporate/VPN proxies."""

    async def test_cloud_respects_trust_env(self):
        provider = OpenAICompatProvider(
            api_key="test", api_base="https://api.openai.com/v1", default_model="",
        )
        await provider._ensure_client()
        client = provider._http_client
        assert client._trust_env is True

    async def test_cloud_mounts_the_env_proxy(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
        provider = OpenAICompatProvider(
            api_key="test", api_base="https://api.openai.com/v1", default_model="",
        )
        await provider._ensure_client()
        assert provider._http_client._mounts, "il proxy d'ambiente non è stato montato"
