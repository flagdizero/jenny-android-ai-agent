"""Tests for SubagentManager."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jenny.agent.agent_types import AGENT_TYPES
from jenny.agent.subagent import SubagentManager
from jenny.agent.tools.filesystem import FileToolsConfig
from jenny.bus.queue import MessageBus
from jenny.config.paths import get_output_path
from jenny.config.schema import ToolsConfig
from jenny.config.tool_schemas import SshConfig, SshHostConfig
from jenny.providers.base import LLMProvider


@pytest.mark.asyncio
async def test_subagent_uses_tool_loader():
    """Verify subagent registers tools via ToolLoader, not hard-coded imports."""
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test"
    sm = SubagentManager(
        provider=provider,
        workspace=Path("/tmp"),
        bus=MessageBus(),
        model="test",
        max_tool_result_chars=16_000,
    )
    tools = sm._build_tools()
    assert tools.has("read_file")
    assert tools.has("write_file")
    assert not tools.has("message")
    assert not tools.has("spawn")


@pytest.mark.asyncio
async def test_subagent_build_tools_isolates_file_read_state(tmp_path):
    """Each spawned subagent needs a fresh file-state cache."""
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test"
    sm = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        model="test",
        max_tool_result_chars=16_000,
    )

    first_read = sm._build_tools().get("read_file")
    second_read = sm._build_tools().get("read_file")

    assert first_read is not second_read
    assert (await first_read.execute(path="note.txt")).startswith("1| hello")
    second_result = await second_read.execute(path="note.txt")
    assert second_result.startswith("1| hello")
    assert "File unchanged" not in second_result


def test_subagent_respects_file_tool_toggle(tmp_path):
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test"
    sm = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        model="test",
        max_tool_result_chars=16_000,
        tools_config=ToolsConfig(file=FileToolsConfig(enable=False)),
    )

    tools = sm._build_tools()

    file_tools = {
        "apply_patch",
        "edit_file",
        "find_files",
        "grep",
        "list_dir",
        "read_file",
        "write_file",
    }
    assert file_tools.isdisjoint(tools.tool_names)


def test_subagent_prompt_names_the_output_dir_and_forbids_the_root(tmp_path):
    """Guardiano della regola di destinazione nel prompt DEI SUBAGENTI.

    I subagenti non ricevono ``agent/tool_contract.md``: il loro prompt lo
    compone ``_build_subagent_prompt`` da ``agent/subagent_system.md``, un
    percorso separato da ``ContextBuilder.build_system_prompt`` del loop
    principale. Quindi la regola "scrivi sotto output/, mai nella radice" deve
    essere ribadita qui, e chi un domani rifà la composizione del prompt deve
    vedere fallire *questo* test invece di far sparire la regola in silenzio:
    senza, i subagenti tornano a depositare i file accanto ai documenti di
    bootstrap.
    """
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test"
    sm = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        model="test",
        max_tool_result_chars=16_000,
    )

    # Spazi normalizzati: il testo del template e giustificato a mano, e un
    # semplice ricapo delle righe non deve far fallire un test che difende il
    # *contenuto* della regola.
    prompt = " ".join(sm._build_subagent_prompt().split())

    # Percorso ASSOLUTO: un "output/" relativo lascerebbe il subagent a
    # indovinare rispetto a quale radice.
    assert str(get_output_path(tmp_path)) in prompt
    assert "Never create a new file in the workspace root" in prompt
    # I documenti di bootstrap restano modificabili: senza questa clausola un
    # subagent smetterebbe di aggiornare AGENTS.md e USER.md, che deve continuare a fare.
    assert "you may edit but never add to" in prompt


def _sysadmin_manager(workspace: Path, *, ssh_enabled: bool, hosts: bool) -> SubagentManager:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test"
    host_list = (
        [SshHostConfig(alias="box", host="example.com", username="u")] if hosts else []
    )
    return SubagentManager(
        provider=provider,
        workspace=workspace,
        bus=MessageBus(),
        model="test",
        max_tool_result_chars=16_000,
        tools_config=ToolsConfig(ssh=SshConfig(enable=ssh_enabled, hosts=host_list)),
    )


def test_sysadmin_gets_the_ssh_tools_through_the_real_build_path(tmp_path):
    """Regressione su due difetti indipendenti che si mascheravano a vicenda.

    Il test NON passa ``tools_config`` a ``_build_tools``: deve attraversare
    ``_subagent_tools_config()``, cioè il percorso vero dello spawn. Erano due:
    ``enabled()`` leggeva ``ctx.config.tools.ssh`` mentre ``ToolContext.config``
    è già una ``ToolsConfig``, e ``_subagent_tools_config()`` ricostruiva la
    config senza la sezione ``ssh`` (quindi default: spento, zero host). Con uno
    solo dei due corretto il registry restava senza SSH, e un ctx sintetico non
    lo avrebbe mostrato.
    """
    sm = _sysadmin_manager(tmp_path, ssh_enabled=True, hosts=True)

    names = sm._build_tools(agent_type=AGENT_TYPES["sysadmin"]).tool_names

    assert {"ssh_hosts", "ssh_exec", "ssh_job", "ssh_transfer"} <= set(names)


@pytest.mark.parametrize(("ssh_enabled", "hosts"), [(False, True), (True, False)])
def test_sysadmin_without_the_toggle_or_a_host_gets_no_ssh(tmp_path, ssh_enabled, hosts):
    """L'allowlist del tipo non è un permesso: il gate resta la config utente."""
    sm = _sysadmin_manager(tmp_path, ssh_enabled=ssh_enabled, hosts=hosts)

    names = sm._build_tools(agent_type=AGENT_TYPES["sysadmin"]).tool_names

    assert {"ssh_hosts", "ssh_exec", "ssh_job", "ssh_transfer"}.isdisjoint(names)
