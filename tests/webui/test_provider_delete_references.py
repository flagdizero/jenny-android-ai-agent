"""Cancellare un provider deve chiudere anche i riferimenti che lo nominavano.

Un provider è indirizzato **per nome** da due posti — ``providers.default`` e il
campo ``provider`` di ogni preset di modello — e fino al 24/08/2026 la
cancellazione ne riparava uno solo. È la forma del difetto dei progetti trovato
lo stesso giorno: un nome torna
libero in un deposito e resta occupato in un altro.

Quel campo a runtime oggi non lo legge nessuno, quindi qui non si sta riparando
un guasto vivo: si sta disarmando una trappola prima che qualcuno cominci a
onorarlo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config, ModelPresetConfig, ProviderConfig
from jenny.runtime.context import get_runtime_context
from jenny.webui.settings_api import delete_provider


@pytest.fixture
def unsaved_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.json"
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    return path


def _config_with_two_providers() -> Config:
    config = Config()
    config.providers.providers = [
        ProviderConfig(name="alfa", format="anthropic", api_key="k1"),
        ProviderConfig(name="beta", format="anthropic", api_key="k2"),
    ]
    config.providers.default = "alfa"
    config.model_presets = {
        "veloce": ModelPresetConfig(provider="alfa", model="m-piccolo"),
        "lento": ModelPresetConfig(provider="beta", model="m-grande"),
        "senza": ModelPresetConfig(model="m-neutro"),
    }
    return config


async def test_a_preset_stops_naming_a_provider_that_is_gone(unsaved_config_path: Path) -> None:
    save_config(_config_with_two_providers(), unsaved_config_path)

    await delete_provider({"name": "alfa"})

    presets = load_config(unsaved_config_path).model_presets
    assert presets["veloce"].provider is None


async def test_the_presets_that_named_someone_else_are_left_alone(
    unsaved_config_path: Path,
) -> None:
    """Il controllo di quello sopra: una riparazione che azzera tutto passerebbe
    il primo test e sarebbe distruttiva."""
    save_config(_config_with_two_providers(), unsaved_config_path)

    await delete_provider({"name": "alfa"})

    presets = load_config(unsaved_config_path).model_presets
    assert presets["lento"].provider == "beta"
    assert presets["senza"].provider is None


async def test_the_preset_survives_it_is_only_the_pointer_that_goes(
    unsaved_config_path: Path,
) -> None:
    """Si azzera il campo, non si cancella il preset.

    Un preset senza provider resta valido — usa quello attivo — e buttare via la
    configurazione di qualcuno perché una sua riga è rimasta orfana sarebbe
    sproporzionato al guasto.
    """
    save_config(_config_with_two_providers(), unsaved_config_path)

    await delete_provider({"name": "alfa"})

    presets = load_config(unsaved_config_path).model_presets
    assert set(presets) == {"veloce", "lento", "senza"}
    assert presets["veloce"].model == "m-piccolo"


async def test_both_repairs_happen_in_the_same_write(unsaved_config_path: Path) -> None:
    """``providers.default`` e i preset si riparano nello stesso ``mutate``.

    Non è eleganza: un config con il provider tolto e i riferimenti ancora
    appesi non deve esistere in nessun istante, nemmeno fra due scritture.
    """
    save_config(_config_with_two_providers(), unsaved_config_path)

    await delete_provider({"name": "alfa"})

    config = load_config(unsaved_config_path)
    assert [p.name for p in config.providers.providers] == ["beta"]
    assert config.providers.default == "beta"
    assert config.model_presets["veloce"].provider is None


async def test_deleting_the_last_provider_leaves_no_dangling_pointer_anywhere(
    unsaved_config_path: Path,
) -> None:
    config = Config()
    config.providers.providers = [ProviderConfig(name="solo", format="anthropic", api_key="k")]
    config.providers.default = "solo"
    config.model_presets = {"unico": ModelPresetConfig(provider="solo", model="m")}
    save_config(config, unsaved_config_path)

    await delete_provider({"name": "solo"})

    after = load_config(unsaved_config_path)
    assert after.providers.providers == []
    assert after.providers.default is None
    assert after.model_presets["unico"].provider is None


async def test_a_config_without_presets_is_not_a_special_case(unsaved_config_path: Path) -> None:
    config = _config_with_two_providers()
    config.model_presets = {}
    save_config(config, unsaved_config_path)

    await delete_provider({"name": "alfa"})

    assert load_config(unsaved_config_path).model_presets == {}
