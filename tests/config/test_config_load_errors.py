"""Cosa fa `load_config` quando il file non si legge.

Contratto cambiato in 0.3.2: prima sollevava, e su Android un `config.json`
troncato (scrittura non atomica interrotta dal sistema che uccide il processo)
impediva l'avvio del gateway — un'app che l'utente non può riparare, perché il
file sta in storage privato. Ora si parte sempre, dicendolo: prima si prova il
backup, poi si mette da parte il file rotto e si riparte dai default.
"""

import json

from jenny.config.loader import _backup_path, load_config, save_config
from jenny.config.schema import Config, ProviderConfig
from jenny.runtime.context import get_runtime_context


def _reset_recovery_flags() -> None:
    ctx = get_runtime_context()
    ctx.config_recovered_from = None
    ctx.config_quarantine_path = None


def test_load_config_missing_file_uses_defaults(tmp_path) -> None:
    config = load_config(tmp_path / "missing.json")

    assert config.agents.defaults.max_tokens == 32000
    assert config.agents.defaults.reasoning_effort == "medium"


def test_load_config_invalid_json_falls_back_to_defaults(tmp_path) -> None:
    _reset_recovery_flags()
    config_path = tmp_path / "config.json"
    config_path.write_text("{broken json", encoding="utf-8")

    config = load_config(config_path)

    assert config.agents.defaults.max_tokens == 32000
    ctx = get_runtime_context()
    assert ctx.config_recovered_from == "defaults"
    # Il file rotto non viene distrutto: serve per capire cosa è successo.
    assert ctx.config_quarantine_path is not None
    assert ctx.config_quarantine_path.exists()
    assert ctx.config_quarantine_path.read_text(encoding="utf-8") == "{broken json"


def test_load_config_invalid_schema_falls_back_to_defaults(tmp_path) -> None:
    _reset_recovery_flags()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"tools": {"python_exec": {"timeout": -1}}}),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.tools.python_exec.timeout > 0
    assert get_runtime_context().config_recovered_from == "defaults"


def test_load_config_prefers_the_backup_over_defaults(tmp_path) -> None:
    """Il caso che conta: le impostazioni dell'utente si recuperano, non si perdono."""
    _reset_recovery_flags()
    config_path = tmp_path / "config.json"
    good = Config()
    good.providers.providers = [
        ProviderConfig(name="deepseek", format="openai_compat", api_key="sk-keep-me")
    ]
    good.providers.default = "deepseek"
    save_config(good, config_path)
    # Un secondo salvataggio ruota il contenuto buono nel .bak, poi il file
    # vivo viene troncato come farebbe un kill a metà scrittura.
    save_config(good, config_path)
    assert _backup_path(config_path).exists()
    config_path.write_text('{"providers": {"provi', encoding="utf-8")

    config = load_config(config_path)

    assert [p.name for p in config.providers.providers] == ["deepseek"]
    assert config.providers.providers[0].api_key == "sk-keep-me"
    assert get_runtime_context().config_recovered_from == "backup"
    # Il backup viene promosso a file vivo: l'avvio successivo è normale.
    _reset_recovery_flags()
    again = load_config(config_path)
    assert [p.name for p in again.providers.providers] == ["deepseek"]
    assert get_runtime_context().config_recovered_from is None


def test_the_file_promoted_from_the_backup_is_private(tmp_path) -> None:
    """Il file rimesso al suo posto dal ``.bak`` porta gli stessi segreti.

    ``write_text`` qui sotto lascia il file rotto e il backup con i permessi di
    default (644 con l'umask comune): il file promosso deve uscire comunque 600.
    """
    import os
    import stat

    _reset_recovery_flags()
    old_umask = os.umask(0o022)
    try:
        config_path = tmp_path / "config.json"
        _backup_path(config_path).write_text(json.dumps({"agents": {}}), encoding="utf-8")
        config_path.write_text("{troncato", encoding="utf-8")

        load_config(config_path)
    finally:
        os.umask(old_umask)

    assert get_runtime_context().config_recovered_from == "backup"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    _reset_recovery_flags()
