"""Il campo CA passa da Impostazioni, e un percorso sbagliato non si salva.

L'errore serve *qui*: un ``caBundle`` che non si legge, scoperto al riavvio, si
presenta all'utente come "nessun provider configurato" — il gateway degrada a
onboarding — che non somiglia per niente alla causa. Al salvataggio invece il
campo e' sullo schermo di chi legge il messaggio.
"""

from __future__ import annotations

import json
import ssl
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from support.tls import write_test_ca

from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config, ProviderConfig
from jenny.runtime.context import get_runtime_context
from jenny.webui.settings_api import (
    WebUISettingsError,
    provider_models_payload,
    update_provider,
)


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.json"
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    return path


def _save(ca_bundle: str | None = None) -> Config:
    config = Config()
    config.providers.providers = [
        ProviderConfig(
            name="casa", format="openai_compat", api_key="k",
            api_base="https://models.example.test/v1", ca_bundle=ca_bundle,
        )
    ]
    config.providers.default = "casa"
    return config


async def test_a_good_bundle_is_stored_in_camel_case(
    config_path: Path, tmp_path: Path
) -> None:
    save_config(_save(), config_path)
    ca = write_test_ca(tmp_path / "ca.pem")

    await update_provider({"name": "casa", "ca_bundle": str(ca)})

    assert load_config(config_path).providers.providers[0].ca_bundle == str(ca)
    raw = json.loads(config_path.read_text("utf-8"))
    assert raw["providers"]["providers"][0]["caBundle"] == str(ca)


async def test_an_unreadable_bundle_is_refused_and_nothing_is_written(
    config_path: Path, tmp_path: Path
) -> None:
    save_config(_save(), config_path)
    before = config_path.read_text("utf-8")
    missing = tmp_path / "nope.pem"

    with pytest.raises(WebUISettingsError) as excinfo:
        await update_provider({"name": "casa", "ca_bundle": str(missing)})

    # Il messaggio deve nominare il file: e' l'unica cosa che l'utente puo'
    # andare a controllare.
    assert str(missing) in str(excinfo.value)
    assert config_path.read_text("utf-8") == before


async def test_the_payload_exposes_the_bundle_so_the_dialog_can_prefill(
    config_path: Path, tmp_path: Path
) -> None:
    ca = write_test_ca(tmp_path / "ca.pem")
    save_config(_save(str(ca)), config_path)

    payload = await update_provider({"name": "casa", "api_base": "https://x.test/v1"})

    assert payload["providers"][0]["ca_bundle"] == str(ca)


async def test_a_save_that_does_not_mention_the_bundle_keeps_it(
    config_path: Path, tmp_path: Path
) -> None:
    """Un client vecchio non manda il campo: non deve cancellare la CA."""
    ca = write_test_ca(tmp_path / "ca.pem")
    save_config(_save(str(ca)), config_path)

    await update_provider({"name": "casa", "api_base": "https://x.test/v1"})

    assert load_config(config_path).providers.providers[0].ca_bundle == str(ca)


async def test_the_clear_flag_empties_it(config_path: Path, tmp_path: Path) -> None:
    """La stringa vuota non arriva mai (``_postWithQuery`` la scarta): per
    svuotare serve un segnale esplicito, ed e' questo."""
    ca = write_test_ca(tmp_path / "ca.pem")
    save_config(_save(str(ca)), config_path)

    await update_provider({"name": "casa", "ca_bundle_clear": "1"})

    assert load_config(config_path).providers.providers[0].ca_bundle is None


async def test_a_new_provider_can_be_created_with_a_bundle(
    config_path: Path, tmp_path: Path
) -> None:
    save_config(_save(), config_path)
    ca = write_test_ca(tmp_path / "ca.pem")

    await update_provider({
        "name": "nuovo", "format": "openai_compat",
        "api_key": "k2", "ca_bundle": str(ca),
    })

    providers = {p.name: p for p in load_config(config_path).providers.providers}
    assert providers["nuovo"].ca_bundle == str(ca)


async def test_a_new_provider_with_a_broken_bundle_is_not_created(
    config_path: Path, tmp_path: Path
) -> None:
    save_config(_save(), config_path)

    with pytest.raises(WebUISettingsError):
        await update_provider({
            "name": "nuovo", "format": "openai_compat",
            "api_key": "k2", "ca_bundle": str(tmp_path / "nope.pem"),
        })

    names = [p.name for p in load_config(config_path).providers.providers]
    assert names == ["casa"]


class TestTheModelsProbeUsesTheSameTrust:
    """Il catalogo modelli apre un suo client: senza la CA resterebbe vuoto.

    E' il guasto che fa sembrare la funzione a meta' — la chat funziona, la
    lista modelli no, e l'errore TLS sembra un secondo problema invece dello
    stesso.
    """

    def test_the_probe_passes_the_context(
        self, config_path: Path, tmp_path: Path
    ) -> None:
        ca = write_test_ca(tmp_path / "ca.pem")
        save_config(_save(str(ca)), config_path)

        with patch("httpx.get") as get:
            get.return_value = MagicMock(
                json=MagicMock(return_value={"data": [{"id": "m"}]}),
                raise_for_status=MagicMock(),
            )
            payload = provider_models_payload({"provider": ["casa"]})

        assert payload["status"] == "available"
        assert isinstance(get.call_args.kwargs["verify"], ssl.SSLContext)

    def test_the_probe_explains_a_broken_bundle_instead_of_raising(
        self, config_path: Path, tmp_path: Path
    ) -> None:
        missing = tmp_path / "nope.pem"
        save_config(_save(str(missing)), config_path)

        payload = provider_models_payload({"provider": ["casa"]})

        assert payload["status"] == "error"
        assert str(missing) in payload["message"]

    def test_without_a_bundle_the_probe_keeps_the_httpx_default(
        self, config_path: Path
    ) -> None:
        save_config(_save(), config_path)

        with patch("httpx.get") as get:
            get.return_value = MagicMock(
                json=MagicMock(return_value={"data": []}),
                raise_for_status=MagicMock(),
            )
            provider_models_payload({"provider": ["casa"]})

        assert get.call_args.kwargs["verify"] is True
