"""Un tipo di agente non parte monco in silenzio.

Sul telefono un subagent ``sysadmin`` e partito senza i tool SSH, ha risposto in
due secondi "il tool non era disponibile" ed e stato registrato come
*completato con successo*. Nessun errore, nessun warning, nessuna riga di log:
la frase del modello sembrava una scusa inventata e invece era l'unica traccia
del difetto.

Il buco stava fra due controlli che non si parlavano: ``split_allow_by_scope``
valida l'allowlist contro i tool che esistono *come classe*, e ``ToolLoader``
poi salta quelli che la config ha spento.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from support.subagent_provider_fakes import script_provider

from jenny.agent.agent_types import AGENT_TYPES
from jenny.agent.subagent import (
    SubagentCapabilityError,
    SubagentManager,
    unavailable_by_scope,
    unavailable_tools,
)
from jenny.agent.tools.loader import ToolLoader
from jenny.bus.queue import MessageBus
from jenny.config.schema import ToolsConfig
from jenny.config.tool_schemas import SshConfig, SshHostConfig
from jenny.providers.base import LLMProvider, LLMResponse

SSH_TOOLS = {"ssh_hosts", "ssh_exec", "ssh_job", "ssh_transfer"}


def _manager(workspace: Path, ssh: SshConfig) -> SubagentManager:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test"
    # I casi positivi lasciano davvero partire il subagent: senza una
    # sceneggiatura il runner chiama un mock non awaitabile e il test fallisce
    # per un motivo che non c'entra niente con quello che sta verificando.
    script_provider(provider, [LLMResponse(content="ok", tool_calls=[])])
    return SubagentManager(
        provider=provider,
        workspace=workspace,
        bus=MessageBus(),
        model="test",
        max_tool_result_chars=16_000,
        tools_config=ToolsConfig(ssh=ssh),
    )


def _host() -> SshHostConfig:
    return SshHostConfig(alias="box", host="example.com", username="u")


# -- il rifiuto -------------------------------------------------------------


@pytest.mark.parametrize(
    ("ssh", "expected_reason"),
    [
        (SshConfig(enable=False, hosts=[_host()]), "SSH access is off"),
        (SshConfig(enable=True, hosts=[]), "no SSH host is registered"),
    ],
)
async def test_sysadmin_is_refused_when_ssh_is_unusable(tmp_path, ssh, expected_reason):
    """Rifiuto immediato, e con il nome dell'interruttore giusto."""
    sm = _manager(tmp_path, ssh)

    with pytest.raises(SubagentCapabilityError) as excinfo:
        await sm.spawn(task="check disk space", agent_type="sysadmin")

    error = excinfo.value
    assert error.agent_type == "sysadmin"
    assert set(error.tools) == SSH_TOOLS
    assert expected_reason in error.reason


async def test_the_two_switches_are_told_apart(tmp_path):
    """Si rimediano in due punti diversi: confonderli costa un giro a vuoto."""
    off = _manager(tmp_path, SshConfig(enable=False, hosts=[_host()]))
    no_host = _manager(tmp_path, SshConfig(enable=True, hosts=[]))

    with pytest.raises(SubagentCapabilityError) as a:
        await off.spawn(task="t", agent_type="sysadmin")
    with pytest.raises(SubagentCapabilityError) as b:
        await no_host.spawn(task="t", agent_type="sysadmin")

    assert a.value.reason != b.value.reason


async def test_sysadmin_spawns_normally_once_ssh_is_usable(tmp_path):
    sm = _manager(tmp_path, SshConfig(enable=True, hosts=[_host()]))

    result = await sm.spawn(task="check disk space", agent_type="sysadmin")

    assert "started" in result
    await sm.drain()


@pytest.mark.parametrize("name", ["writer", "coder", "analyst", "operator"])
async def test_other_types_are_untouched_by_ssh_being_off(tmp_path, name):
    """Il controllo guarda ``requires``, non una lista di casi speciali."""
    sm = _manager(tmp_path, SshConfig(enable=False, hosts=[]))

    assert "started" in await sm.spawn(task="t", agent_type=name)
    await sm.drain()


# -- cosa NON deve far scattare il rifiuto ----------------------------------


async def test_a_missing_runtime_is_not_a_refusal(tmp_path):
    """Il caso del ``researcher`` fuori da Android — e la regola che ci sta dietro.

    I tool web sono spenti in questo processo perche non c'e un contesto
    Android, non perche qualcuno li abbia disattivati. Rifiutare direbbe
    all'utente di accendere un interruttore che non risolverebbe niente: il
    rifiuto esiste per consegnare un'azione, e senza azione non ha ragione di
    esistere.
    """
    sm = _manager(tmp_path, SshConfig(enable=False, hosts=[]))

    assert "started" in await sm.spawn(task="t", agent_type="researcher")
    await sm.drain()


def test_partial_loss_is_not_a_refusal(tmp_path):
    """Finche ne resta uno, il tipo puo ancora fare qualcosa: parte."""
    sm = _manager(tmp_path, SshConfig(enable=True, hosts=[_host()]))
    ctx = sm._tool_context(tmp_path, sm._subagent_tools_config())
    loader = ToolLoader()

    partial = unavailable_tools(loader, {"ssh_exec", "web_fetch"}, ctx)

    assert partial == ("web_fetch",)
    assert set(partial) != set(AGENT_TYPES["sysadmin"].requires)


def test_a_type_without_an_allowlist_declares_nothing_to_lose(tmp_path):
    """``operator`` ha ``tools=None``: "tutto lo scope", nessuna promessa esplicita."""
    sm = _manager(tmp_path, SshConfig(enable=False, hosts=[]))
    ctx = sm._tool_context(tmp_path, sm._subagent_tools_config())

    unavailable = unavailable_by_scope(ToolLoader(), AGENT_TYPES["operator"], ctx)

    assert unavailable == {"subagent": ()}
    assert AGENT_TYPES["operator"].requires is None


# -- l'invariante fra ``tools`` e ``requires`` ------------------------------


@pytest.mark.parametrize("name", sorted(AGENT_TYPES))
def test_requires_is_always_a_subset_of_tools(name):
    """Non si puo pretendere cio che non si e nemmeno chiesto.

    Un ``requires`` fuori da ``tools`` sarebbe un tipo che rifiuta di partire
    per un tool che comunque non avrebbe mai potuto vedere.
    """
    agent_type = AGENT_TYPES[name]
    if agent_type.requires is None:
        return
    assert agent_type.tools is not None, "requires senza allowlist non ha senso"
    assert agent_type.requires <= agent_type.tools


# -- la frase che arriva al modello -----------------------------------------


async def test_the_spawn_tool_turns_the_refusal_into_an_actionable_sentence(tmp_path):
    """Il modello non deve vedere un traceback, ne ripiegare su un altro tipo."""
    from jenny.agent.tools.spawn import SpawnTool

    sm = _manager(tmp_path, SshConfig(enable=True, hosts=[]))
    tool = SpawnTool(sm)

    text = await tool.execute(task="check disk space", agent_type="sysadmin")

    assert "no SSH host is registered" in text
    assert "Settings > SSH" in text
    assert "do not retry with another agent type" in text
