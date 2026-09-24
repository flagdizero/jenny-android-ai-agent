"""CA per provider: risoluzione, additivita', errori, e i due client che la usano.

Copre l'issue #12 — un server OpenAI-compatibile con certificato firmato da una
CA propria. Il punto piu' facile da rompere in silenzio e' l'ultimo test:
``verify`` passato al client viene **ignorato** quando c'e' un ``transport``
esplicito, e il ramo loopback ne ha uno.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from support.tls import CA_COMMON_NAME, write_test_ca

from jenny.config.paths import get_workspace_path
from jenny.config.schema import Config, ProviderConfig, ProvidersConfig
from jenny.providers.anthropic_provider import AnthropicProvider
from jenny.providers.factory import make_provider
from jenny.providers.openai_compat_provider import OpenAICompatProvider
from jenny.providers.tls import CaBundleError, build_ssl_context, resolve_ca_bundle


def _ca(tmp_path: Path) -> Path:
    return write_test_ca(tmp_path / "ca.pem")


class TestSchema:
    def test_default_is_none(self) -> None:
        assert ProviderConfig(name="p", format="openai_compat").ca_bundle is None

    def test_camel_case_alias_is_accepted(self) -> None:
        """``config.json`` porta camelCase: e' la forma che Jenny riscrive."""
        provider = ProviderConfig.model_validate(
            {"name": "p", "format": "openai_compat", "caBundle": "ca.pem"}
        )
        assert provider.ca_bundle == "ca.pem"


class TestResolution:
    def test_relative_path_hangs_off_the_workspace(self) -> None:
        resolved = resolve_ca_bundle("certs/ca.pem")
        assert resolved == get_workspace_path() / "certs" / "ca.pem"

    def test_absolute_path_is_left_alone(self, tmp_path: Path) -> None:
        assert resolve_ca_bundle(str(tmp_path / "ca.pem")) == tmp_path / "ca.pem"


class TestBuildSslContext:
    def test_no_bundle_means_no_context(self) -> None:
        """``None`` = "non toccare ``verify``", non "non verificare"."""
        assert build_ssl_context(None, provider_name="p") is None
        assert build_ssl_context("   ", provider_name="p") is None

    def test_trust_is_added_not_replaced(self, tmp_path: Path) -> None:
        context = build_ssl_context(str(_ca(tmp_path)), provider_name="p")
        assert context is not None
        subjects = [
            value
            for cert in context.get_ca_certs()
            for rdn in cert.get("subject", ())
            for key, value in rdn
            if key == "commonName"
        ]
        assert CA_COMMON_NAME in subjects
        # E il bundle di default e' ancora tutto li': la CA dell'utente si
        # somma, non prende il posto di certifi. Un contesto che contenesse
        # *solo* la CA di prova avrebbe una manciata di certificati, non cento.
        default_roots = len(httpx.create_ssl_context().get_ca_certs())
        assert len(context.get_ca_certs()) == default_roots + 1

    def test_missing_file_names_the_path_and_the_provider(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.pem"
        with pytest.raises(CaBundleError) as excinfo:
            build_ssl_context(str(missing), provider_name="my-server")
        message = str(excinfo.value)
        assert str(missing) in message
        assert "my-server" in message

    def test_file_that_is_not_a_certificate_is_refused(self, tmp_path: Path) -> None:
        junk = tmp_path / "junk.pem"
        junk.write_text("not a certificate\n")
        with pytest.raises(CaBundleError):
            build_ssl_context(str(junk), provider_name="p")

    def test_directory_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(CaBundleError):
            build_ssl_context(str(tmp_path), provider_name="p")


def _config(fmt: str, ca_bundle: str | None) -> Config:
    return Config(
        providers=ProvidersConfig(
            providers=[
                ProviderConfig(
                    name="p", format=fmt, api_key="k",
                    api_base="https://models.example.test/v1",
                    ca_bundle=ca_bundle,
                )
            ],
            default="p",
        )
    )


class TestFactory:
    @pytest.mark.parametrize("fmt", ["openai_compat", "anthropic"])
    def test_context_reaches_the_provider(self, fmt: str, tmp_path: Path) -> None:
        provider = make_provider(_config(fmt, str(_ca(tmp_path))))
        assert isinstance(provider, (OpenAICompatProvider, AnthropicProvider))
        assert provider._ssl_context is not None

    @pytest.mark.parametrize("fmt", ["openai_compat", "anthropic"])
    def test_without_a_bundle_nothing_is_built(self, fmt: str) -> None:
        assert make_provider(_config(fmt, None))._ssl_context is None

    def test_broken_bundle_stops_provider_construction(self, tmp_path: Path) -> None:
        """Non si parte fidandosi del bundle di default: sarebbe una bugia."""
        with pytest.raises(CaBundleError):
            make_provider(_config("openai_compat", str(tmp_path / "gone.pem")))


class TestClientsCarryTheContext:
    """Dove il contesto deve finire davvero: dentro httpx."""

    def test_anthropic_client_gets_verify(self, tmp_path: Path) -> None:
        context = build_ssl_context(str(_ca(tmp_path)), provider_name="p")
        with patch("httpx.AsyncClient", return_value=MagicMock()) as client:
            AnthropicProvider(api_key="k", ssl_context=context)
        assert client.call_args.kwargs["verify"] is context

    def test_anthropic_client_defaults_to_true(self) -> None:
        with patch("httpx.AsyncClient", return_value=MagicMock()) as client:
            AnthropicProvider(api_key="k")
        assert client.call_args.kwargs["verify"] is True

    def test_remote_openai_client_gets_verify(self, tmp_path: Path) -> None:
        context = build_ssl_context(str(_ca(tmp_path)), provider_name="p")
        with patch("httpx.AsyncClient", return_value=MagicMock()) as client:
            provider = OpenAICompatProvider(
                api_key="k", api_base="https://models.example.test/v1",
                ssl_context=context,
            )
            provider._build_http_client()
        assert client.call_args.kwargs["verify"] is context

    def test_loopback_transport_gets_verify(self, tmp_path: Path) -> None:
        """La prova che conta: sul ramo loopback ``verify`` va sul transport.

        ``AsyncClient._init_transport`` restituisce il transport ricevuto senza
        guardare ``verify``, quindi metterlo solo sul client lo farebbe sparire
        **in silenzio** — la CA dell'utente non varrebbe niente e nessun errore
        lo direbbe. E' esattamente il fallimento che l'issue chiede di evitare.
        """
        context = build_ssl_context(str(_ca(tmp_path)), provider_name="p")
        with patch("httpx.AsyncClient", return_value=MagicMock()), patch(
            "httpx.AsyncHTTPTransport", return_value=MagicMock()
        ) as transport:
            provider = OpenAICompatProvider(
                api_key="k", api_base="https://127.0.0.1:8443/v1", ssl_context=context,
            )
            assert provider._is_local
            provider._build_http_client()
        assert transport.call_args.kwargs["verify"] is context
