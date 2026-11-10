"""Persistenza dei parametri di generazione dalle impostazioni WebUI.

Regressione: la UI agganciava ``max_tokens`` / ``temperature`` /
``reasoning_effort`` all'auto-save, ma ``update_agent_settings`` non aveva un
ramo per nessuno dei tre. La richiesta arrivava, ``changed`` restava False,
niente veniva scritto — e il client mostrava "Saved!" perché la risposta è 200
comunque. Tre campi decorativi: il valore tornava al precedente al riavvio.
"""

from __future__ import annotations

import pytest

from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config
from jenny.webui.settings_api import WebUISettingsError, update_agent_settings

# --- max_tokens ----------------------------------------------------------------


async def test_max_tokens_is_persisted(config_path) -> None:
    payload = await update_agent_settings({"max_tokens": ["16384"]})

    assert payload["agent"]["max_tokens"] == 16384
    assert load_config(config_path).agents.defaults.max_tokens == 16384


async def test_max_tokens_accepts_camel_case_alias(config_path) -> None:
    await update_agent_settings({"maxTokens": ["4096"]})

    assert load_config(config_path).agents.defaults.max_tokens == 4096


async def test_max_tokens_rejects_non_integer(config_path) -> None:
    with pytest.raises(WebUISettingsError, match="max_tokens must be an integer"):
        await update_agent_settings({"max_tokens": ["lots"]})


async def test_max_tokens_rejects_zero(config_path) -> None:
    with pytest.raises(WebUISettingsError, match="max_tokens must be at least 1"):
        await update_agent_settings({"max_tokens": ["0"]})


async def test_a_rejected_value_leaves_the_config_untouched(config_path) -> None:
    """La validazione gira dentro ``store.mutate``, cioè dentro il lock.

    Il contratto di ``mutate`` è "se *apply* solleva, non viene scritto niente":
    un valore rifiutato non deve lasciare la config a metà.
    """
    await update_agent_settings({"max_tokens": ["16384"]})
    with pytest.raises(WebUISettingsError):
        await update_agent_settings({"max_tokens": ["nope"]})

    assert load_config(config_path).agents.defaults.max_tokens == 16384


# --- temperature ---------------------------------------------------------------


async def test_temperature_is_persisted(config_path) -> None:
    await update_agent_settings({"temperature": ["0.7"]})

    assert load_config(config_path).agents.defaults.temperature == 0.7


async def test_temperature_accepts_comma_decimal_separator(config_path) -> None:
    """Un ``input type=number`` su locale italiano può mandare la virgola."""
    await update_agent_settings({"temperature": ["0,4"]})

    assert load_config(config_path).agents.defaults.temperature == 0.4


async def test_temperature_rejects_out_of_range(config_path) -> None:
    with pytest.raises(WebUISettingsError, match="temperature must be between 0 and 2"):
        await update_agent_settings({"temperature": ["3"]})


async def test_temperature_zero_is_persisted(config_path) -> None:
    """0.0 è un valore legittimo, non un campo vuoto da ignorare."""
    save_config(Config(), config_path)
    await update_agent_settings({"temperature": ["0"]})

    assert load_config(config_path).agents.defaults.temperature == 0.0


# --- reasoning_effort ----------------------------------------------------------


async def test_reasoning_effort_is_persisted(config_path) -> None:
    await update_agent_settings({"reasoning_effort": ["medium"]})

    assert load_config(config_path).agents.defaults.reasoning_effort == "medium"


async def test_reasoning_effort_empty_clears_it(config_path) -> None:
    """L'opzione "—" della select significa "lascia decidere al provider"."""
    await update_agent_settings({"reasoning_effort": ["high"]})
    await update_agent_settings({"reasoning_effort": [""]})

    assert load_config(config_path).agents.defaults.reasoning_effort is None


async def test_reasoning_effort_survives_an_unrelated_update(config_path) -> None:
    """Campo assente != campo azzerato.

    Senza la sentinella, salvare un altro campo azzererebbe l'effort come
    effetto collaterale.
    """
    await update_agent_settings({"reasoning_effort": ["high"]})
    await update_agent_settings({"max_tokens": ["8192"]})

    assert load_config(config_path).agents.defaults.reasoning_effort == "high"


async def test_reasoning_effort_rejects_unknown_value(config_path) -> None:
    with pytest.raises(WebUISettingsError, match="reasoning_effort must be one of"):
        await update_agent_settings({"reasoning_effort": ["turbo"]})


async def test_reasoning_effort_accepts_provider_vocabulary(config_path) -> None:
    """L'API accetta tutto ciò che il provider sa normalizzare, non solo la select."""
    await update_agent_settings({"reasoning_effort": ["minimum"]})

    assert load_config(config_path).agents.defaults.reasoning_effort == "minimum"
