"""Migrazioni del config guidate da ``configVersion``.

Il punto di tutto il meccanismo: ``loader.py`` serializza il config *includendo
i default*, quindi un file scritto quando un default era X porta X per sempre e
alzare il default nello schema non raggiunge chi aggiorna. Il contatore di
versione e l'unica cosa che distingue "valore scelto" da "vecchio default
rimasto scritto".
"""

from __future__ import annotations

import json

import pytest

from jenny.config.loader import load_config
from jenny.config.schema import CURRENT_CONFIG_VERSION, AgentDefaults, Config

_NEW_CONCURRENCY = AgentDefaults.model_fields["max_concurrent_subagents"].default


def test_legacy_config_without_version_is_migrated() -> None:
    """Il caso reale: installazione che aggiorna, con il vecchio 1 nel file."""
    config = Config.model_validate({"agents": {"defaults": {"maxConcurrentSubagents": 1}}})
    assert config.agents.defaults.max_concurrent_subagents == _NEW_CONCURRENCY
    assert config.config_version == CURRENT_CONFIG_VERSION


def test_snake_case_key_is_migrated_too() -> None:
    """I config legacy usano entrambe le convenzioni."""
    config = Config.model_validate({"agents": {"defaults": {"max_concurrent_subagents": 1}}})
    assert config.agents.defaults.max_concurrent_subagents == _NEW_CONCURRENCY


def test_a_deliberate_one_survives_once_the_version_is_stamped() -> None:
    """Dopo la migrazione i valori nel file *sono* scelte, e restano.

    È la ragione d'essere del contatore: senza, ogni boot rispingerebbe a 3 un
    utente che ha deciso di volerne uno solo.
    """
    config = Config.model_validate({
        "configVersion": CURRENT_CONFIG_VERSION,
        "agents": {"defaults": {"maxConcurrentSubagents": 1}},
    })
    assert config.agents.defaults.max_concurrent_subagents == 1


def test_a_non_default_legacy_value_is_left_alone() -> None:
    """La migrazione e condizionata sul valore vecchio esatto, non sulla versione."""
    config = Config.model_validate({"agents": {"defaults": {"maxConcurrentSubagents": 2}}})
    assert config.agents.defaults.max_concurrent_subagents == 2


def test_a_fresh_config_is_already_current() -> None:
    config = Config.model_validate({})
    assert config.agents.defaults.max_concurrent_subagents == _NEW_CONCURRENCY
    assert config.config_version == CURRENT_CONFIG_VERSION


def test_a_garbage_version_degrades_to_zero_instead_of_raising() -> None:
    """Un config corrotto a mano non deve impedire il boot del gateway."""
    config = Config.model_validate({
        "configVersion": "not a number",
        "agents": {"defaults": {"maxConcurrentSubagents": 1}},
    })
    assert config.agents.defaults.max_concurrent_subagents == _NEW_CONCURRENCY
    assert config.config_version == CURRENT_CONFIG_VERSION


@pytest.mark.parametrize("version", ["1e400", "2.5", '"3"', "true", "-1", "null", "[]"])
def test_a_version_that_is_not_a_plain_integer_costs_nothing_else(tmp_path, version) -> None:
    """``1e400`` (infinito per ``json``) faceva sollevare ``OverflowError`` fuori
    dal recupero — crash-loop del gateway; ``2.5``, ``"3"``, ``true`` o ``-1`` facevano
    rifiutare il file intero, e si ripartiva sui default di tutto."""
    path = tmp_path / "config.json"
    path.write_text(
        '{"configVersion": %s, "agents": {"defaults": {"maxTokens": 1234}}}' % version,
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.agents.defaults.max_tokens == 1234
    assert config.config_version == CURRENT_CONFIG_VERSION
    assert not list(tmp_path.glob("config.corrupt-*"))


@pytest.mark.parametrize("version", ['"3"', '" 3 "', "3.0", '"03"'])
def test_a_whole_number_in_another_form_is_that_version(tmp_path, version) -> None:
    """Un ``"3"`` (o ``3.0``) scritto a mano o da un altro strumento valeva 0: la
    migrazione v1 rigirava e riportava a 3 un ``maxConcurrentSubagents: 1``
    scelto dall'utente dopo lo stamp. E' la versione 3, scritta in un'altra forma."""
    path = tmp_path / "config.json"
    path.write_text(
        '{"configVersion": %s, "agents": {"defaults": {"maxConcurrentSubagents": 1}}}'
        % version,
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.agents.defaults.max_concurrent_subagents == 1
    assert config.config_version == CURRENT_CONFIG_VERSION


@pytest.mark.parametrize("version", ['"2.5"', "2.5", "true", '"-1"', '"tre"', '"%s"' % ("9" * 50)])
def test_a_version_that_is_no_whole_number_is_still_zero(version) -> None:
    config = Config.model_validate(json.loads(
        '{"configVersion": %s, "agents": {"defaults": {"maxConcurrentSubagents": 1}}}' % version
    ))
    assert config.agents.defaults.max_concurrent_subagents == _NEW_CONCURRENCY
    assert config.config_version == CURRENT_CONFIG_VERSION


async def test_persist_writes_a_string_version_back_as_an_integer(tmp_path) -> None:
    from jenny.config.store import persist_schema_migrations

    path = tmp_path / "config.json"
    path.write_text(
        '{"configVersion": "3", "agents": {"defaults": {"maxConcurrentSubagents": 1}}}',
        encoding="utf-8",
    )

    await persist_schema_migrations(config_path=path)

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["configVersion"] == CURRENT_CONFIG_VERSION
    assert written["agents"]["defaults"]["maxConcurrentSubagents"] == 1


def test_the_stamp_is_serialized_so_the_next_write_persists_it() -> None:
    """Nessuna scrittura dedicata: lo stamp viaggia col primo dump ordinario."""
    dumped = Config.model_validate({}).model_dump(mode="json", by_alias=True)
    assert dumped["configVersion"] == CURRENT_CONFIG_VERSION


def test_migration_runs_through_the_real_loader(tmp_path) -> None:
    """Il percorso che conta: file su disco -> ``load_config``."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"agents": {"defaults": {"maxConcurrentSubagents": 1}}}),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.agents.defaults.max_concurrent_subagents == _NEW_CONCURRENCY
    assert config.config_version == CURRENT_CONFIG_VERSION


@pytest.mark.parametrize("version", ["1e400", '"3"'])
async def test_persist_rewrites_a_version_that_is_not_an_integer(tmp_path, version) -> None:
    from jenny.config.store import persist_schema_migrations

    path = tmp_path / "config.json"
    path.write_text('{"configVersion": %s}' % version, encoding="utf-8")

    assert await persist_schema_migrations(config_path=path) is True
    assert json.loads(path.read_text(encoding="utf-8"))["configVersion"] == (
        CURRENT_CONFIG_VERSION
    )


async def test_persist_stamps_the_file_once(tmp_path) -> None:
    """Lo stamp va su disco una volta: senza, la migrazione rigira a ogni boot."""
    from jenny.config.store import persist_schema_migrations

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"agents": {"defaults": {"maxConcurrentSubagents": 1}}}),
        encoding="utf-8",
    )

    assert await persist_schema_migrations(config_path=path) is True
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["configVersion"] == CURRENT_CONFIG_VERSION
    assert written["agents"]["defaults"]["maxConcurrentSubagents"] == _NEW_CONCURRENCY

    # Seconda passata: niente da fare, e il file non viene toccato.
    before = path.read_bytes()
    assert await persist_schema_migrations(config_path=path) is False
    assert path.read_bytes() == before


async def test_persist_preserves_unknown_keys(tmp_path) -> None:
    """Passa da ``store.mutate``, quindi le chiavi che questa versione non
    conosce sopravvivono alla riscrittura."""
    from jenny.config.store import persist_schema_migrations

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({
            "agents": {"defaults": {"maxConcurrentSubagents": 1}},
            "somethingFromTheFuture": {"keep": "me"},
        }),
        encoding="utf-8",
    )
    await persist_schema_migrations(config_path=path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["somethingFromTheFuture"] == {"keep": "me"}


# ── v4: la finestra di contesto da 64K a 128K ────────────────────────────────

_NEW_WINDOW = AgentDefaults.model_fields["context_window_tokens"].default


def test_the_old_context_window_default_moves_to_128k() -> None:
    """Il caso reale del 05/10: il Titan aveva 65536 scritto nel file."""
    assert _NEW_WINDOW == 131072
    config = Config.model_validate({
        "configVersion": 3, "agents": {"defaults": {"contextWindowTokens": 65536}},
    })
    assert config.agents.defaults.context_window_tokens == _NEW_WINDOW
    assert config.config_version == CURRENT_CONFIG_VERSION


def test_a_context_window_the_user_picked_stays() -> None:
    legacy = Config.model_validate({
        "configVersion": 3, "agents": {"defaults": {"contextWindowTokens": 262144}},
    })
    assert legacy.agents.defaults.context_window_tokens == 262144
    stamped = Config.model_validate({
        "configVersion": CURRENT_CONFIG_VERSION,
        "agents": {"defaults": {"contextWindowTokens": 65536}},
    })
    assert stamped.agents.defaults.context_window_tokens == 65536


def test_a_preset_window_is_a_choice_and_is_not_migrated() -> None:
    config = Config.model_validate({
        "configVersion": 3,
        "modelPresets": {"piccolo": {"model": "m", "contextWindowTokens": 65536}},
    })
    assert config.model_presets["piccolo"].context_window_tokens == 65536
