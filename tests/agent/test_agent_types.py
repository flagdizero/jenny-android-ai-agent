"""Tests per il registry dei tipi di agente (prompt + allowlist + default)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.agent_types import (
    AGENT_TYPE_NAMES,
    AGENT_TYPES,
    DEFAULT_AGENT_TYPE,
    UnknownAgentTypeError,
    coerce_agent_type,
    get_agent_type,
    validate_agent_type,
)
from jenny.agent.subagent_records import SubagentRecord, SubagentSpec
from jenny.agent.tools.context import ToolContext
from jenny.agent.tools.file_state import FileStates
from jenny.agent.tools.loader import ToolLoader, ToolLoadError
from jenny.agent.tools.registry import ToolRegistry
from jenny.config.schema import ToolsConfig

# Insieme atteso per tipo: e la definizione del contratto, quindi va scritto per
# esteso invece di essere derivato dal codice sotto test.
EXPECTED_TOOLS = {
    "researcher": {
        "web_search", "web_fetch",
        "browser_open", "browser_snapshot", "browser_do", "browser_read", "browser_close",
        "read_file", "list_dir", "write_file",
    },
    "writer": {"read_file", "list_dir", "write_file", "apply_patch"},
    "coder": {
        "read_file", "write_file", "edit_file", "list_dir", "apply_patch",
        "python_exec", "list_exec_sessions", "write_stdin", "get_recent_logs",
        "find_files", "grep",
    },
    "analyst": {"python_exec", "read_file", "list_dir", "write_file"},
    "sysadmin": {
        "ssh_hosts", "ssh_exec", "ssh_job", "ssh_transfer",
        "read_file", "list_dir", "write_file",
    },
    # Scrive e prova le app: il tool ``app`` (faccia del costruttore, scope
    # ``apps``) e la sola lettura. Niente web, niente esecuzione.
    "app-builder": {"app", "read_file", "list_dir", "grep"},
    "operator": None,  # tutto lo scope subagent
}

_CODE_EXECUTION = {"python_exec", "write_stdin", "list_exec_sessions"}
_NETWORK = {"web_search", "web_fetch", "download_file"}
_SSH = {"ssh_hosts", "ssh_exec", "ssh_job", "ssh_transfer"}


def _tools_config(*, ssh: bool = False) -> ToolsConfig:
    """``ToolsConfig`` per il ctx dei tool, con SSH acceso su richiesta.

    ``enabled()`` dei tool SSH vuole il toggle *e* almeno un host: sono due gate
    d'ambiente, indipendenti dal cablaggio scope/allowlist verificato qui, e
    senza soddisfarli i quattro tool non entrerebbero in nessun registry.
    """
    from jenny.config.tool_schemas import SshHostConfig

    cfg = ToolsConfig()
    if ssh:
        cfg.ssh.enable = True
        cfg.ssh.hosts = [SshHostConfig(alias="prod", host="example.com", username="u")]
        # Il gate dei tool SSH cerca la config sotto ``ctx.config.tools.ssh``,
        # mentre il ctx del runtime porta una ToolsConfig (quindi ``.ssh``).
        # Il ctx di questo test soddisfa entrambe le forme: il test verifica il
        # cablaggio, e non deve dipendere da quale delle due sia quella giusta.
        cfg.tools = cfg
    return cfg


def _ctx(tmp_path: Path, *, ssh: bool = False) -> ToolContext:
    return ToolContext(
        config=_tools_config(ssh=ssh),
        workspace=str(tmp_path),
        file_states=FileStates(),
        # Contesto Android finto: senza, i tool web sono disabilitati
        # dall'ambiente e il set del researcher non sarebbe verificabile.
        android_context=object(),
    )


def _load(allow, tmp_path: Path) -> set[str]:
    """Carica lo scope subagent con l'allowlist data e ritorna i nomi."""
    return set(ToolLoader().load(_ctx(tmp_path), ToolRegistry(), scope="subagent", allow=allow))


def _load_type(name: str, tmp_path: Path) -> set[str]:
    """Carica un tipo su TUTTI i suoi scope, come fa ``_build_tools``."""
    from jenny.agent.subagent import split_allow_by_scope

    agent_type = AGENT_TYPES[name]
    loader = ToolLoader()
    registry = ToolRegistry()
    ctx = _ctx(tmp_path, ssh="remote" in agent_type.scopes)
    loaded: set[str] = set()
    for scope, allow in split_allow_by_scope(loader, agent_type).items():
        loaded |= set(loader.load(ctx, registry, scope=scope, allow=allow))
    return loaded


def test_seven_types_exist() -> None:
    assert AGENT_TYPE_NAMES == (
        "researcher", "writer", "coder", "analyst", "sysadmin", "app-builder", "operator",
    )
    assert DEFAULT_AGENT_TYPE == "operator"


@pytest.mark.parametrize(
    "name", ["researcher", "writer", "coder", "analyst", "sysadmin", "app-builder", "operator"]
)
def test_declared_allowlist_matches_contract(name: str) -> None:
    expected = EXPECTED_TOOLS[name]
    declared = AGENT_TYPES[name].tools
    if expected is None:
        assert declared is None, "operator deve vedere tutto lo scope subagent"
    else:
        assert declared == frozenset(expected)


@pytest.mark.parametrize("name", ["researcher", "writer", "coder", "analyst", "sysadmin"])
def test_type_registry_contains_exactly_its_tools(name: str, tmp_path: Path) -> None:
    assert _load_type(name, tmp_path) == EXPECTED_TOOLS[name]


def test_operator_gets_the_whole_subagent_scope(tmp_path: Path) -> None:
    assert _load(None, tmp_path) == _load(AGENT_TYPES["operator"].tools, tmp_path)


def test_allow_none_is_todays_behaviour(tmp_path: Path) -> None:
    registry = ToolRegistry()
    ctx = ToolContext(
        config=ToolsConfig(),
        workspace=str(tmp_path),
        file_states=FileStates(),
        android_context=object(),
    )
    without_kwarg = set(ToolLoader().load(ctx, registry, scope="subagent"))
    assert without_kwarg == _load(None, tmp_path)


def test_researcher_cannot_execute_code(tmp_path: Path) -> None:
    """Confine di sicurezza: chi legge il web non esegue codice."""
    loaded = _load(AGENT_TYPES["researcher"].tools, tmp_path)
    assert not (loaded & _CODE_EXECUTION)
    assert "apply_patch" not in loaded
    assert "edit_file" not in loaded


@pytest.mark.parametrize("name", ["writer", "analyst", "coder", "sysadmin"])
def test_non_researcher_types_have_no_network(name: str, tmp_path: Path) -> None:
    """Confine simmetrico: chi esegue/scrive non va a prendersi la fonte."""
    assert not (_load_type(name, tmp_path) & _NETWORK)


# -- SSH: il confine del tipo ``sysadmin`` -------------------------------------


def test_sysadmin_has_the_four_ssh_tools_and_nothing_that_executes_or_browses(
    tmp_path: Path,
) -> None:
    """La catena corta: da "pagina ostile" a "shell su un server" non c'e ponte."""
    loaded = _load_type("sysadmin", tmp_path)
    assert _SSH <= loaded
    assert not (loaded & _NETWORK)
    assert not (loaded & _CODE_EXECUTION)
    assert "apply_patch" not in loaded
    assert "edit_file" not in loaded


def test_operator_does_not_inherit_the_ssh_tools(tmp_path: Path) -> None:
    """``operator`` e ``tools=None``: tutto cio che entra nello scope subagent
    lo eredita in automatico. I tool SSH devono restarne fuori."""
    assert AGENT_TYPES["operator"].tools is None
    assert AGENT_TYPES["operator"].scopes == ("subagent",)
    assert not (_load_type("operator", tmp_path) & _SSH)


@pytest.mark.parametrize("scope", ["core", "orchestrator", "subagent"])
def test_ssh_tools_are_in_no_pre_existing_scope(scope: str, tmp_path: Path) -> None:
    loaded = set(ToolLoader().load(
        _ctx(tmp_path, ssh=True), ToolRegistry(), scope=scope, allow=None
    ))
    assert not (loaded & _SSH), scope


def test_only_sysadmin_reaches_the_remote_scope() -> None:
    extra = {"sysadmin": ("subagent", "remote"), "app-builder": ("subagent", "apps")}
    for name, agent_type in AGENT_TYPES.items():
        assert agent_type.scopes == extra.get(name, ("subagent",)), name


def test_multi_scope_allowlist_is_split_per_scope() -> None:
    """L'allowlist di un tipo multi-scope si valida sull'unione, si passa a fette.

    ``ToolLoader.load`` valida l'``allow`` contro il solo scope che carica:
    passargli l'allowlist intera di ``sysadmin`` farebbe abortire lo startup
    perche ``ssh_exec`` nello scope ``subagent`` non esiste.
    """
    from jenny.agent.subagent import split_allow_by_scope

    by_scope = split_allow_by_scope(ToolLoader(), AGENT_TYPES["sysadmin"])
    assert by_scope["remote"] == frozenset(_SSH)
    assert by_scope["subagent"] == frozenset({"read_file", "list_dir", "write_file"})


def test_operator_keeps_allow_none_on_every_scope() -> None:
    from jenny.agent.subagent import split_allow_by_scope

    assert split_allow_by_scope(ToolLoader(), AGENT_TYPES["operator"]) == {"subagent": None}


def test_unknown_allow_entry_raises_at_load(tmp_path: Path) -> None:
    with pytest.raises(ToolLoadError) as exc:
        _load({"read_file", "reed_file"}, tmp_path)
    assert "reed_file" in str(exc.value)
    # L'errore deve elencare i tool noti, altrimenti il typo va indovinato.
    assert "read_file" in str(exc.value)


def test_a_name_missing_from_every_scope_is_still_fatal() -> None:
    """La guardia sui typo non si perde nello split: un nome che non esiste in
    NESSUNO degli scope del tipo resta un ``ToolLoadError`` che aborta il boot."""
    from dataclasses import replace

    from jenny.agent.subagent import split_allow_by_scope

    broken = replace(
        AGENT_TYPES["sysadmin"],
        tools=frozenset({"ssh_exec", "read_file", "ssh_exce"}),
    )
    with pytest.raises(ToolLoadError) as exc:
        split_allow_by_scope(ToolLoader(), broken)
    assert "ssh_exce" in str(exc.value)
    # I nomi noti dei DUE scope, altrimenti il typo va indovinato.
    assert "ssh_exec" in str(exc.value)
    assert "read_file" in str(exc.value)


def test_a_name_from_another_scope_is_not_a_typo(tmp_path: Path) -> None:
    """Il contrario della guardia: ``ssh_exec`` esiste, solo non in ``subagent``.
    Senza lo split questo sarebbe un boot abortito."""
    registry = ToolRegistry()
    loader = ToolLoader()
    ctx = _ctx(tmp_path, ssh=True)
    from jenny.agent.subagent import split_allow_by_scope

    for scope, allow in split_allow_by_scope(loader, AGENT_TYPES["sysadmin"]).items():
        loader.load(ctx, registry, scope=scope, allow=allow)  # non deve sollevare
    assert registry.has("ssh_exec")


def test_config_disabled_tool_is_not_a_typo(tmp_path: Path) -> None:
    """Un tool spento dall'ambiente non deve far abortire il caricamento.

    ``enabled()`` dice no per ragioni di config/dispositivo: se questo fosse
    fatale, spegnere i tool web in config renderebbe il researcher inavviabile.
    """
    registry = ToolRegistry()
    ctx = ToolContext(
        config=ToolsConfig(),
        workspace=str(tmp_path),
        file_states=FileStates(),
        android_context=None,  # tool web disabilitati
    )
    loaded = set(ToolLoader().load(
        ctx, registry, scope="subagent", allow=AGENT_TYPES["researcher"].tools
    ))
    assert "web_search" not in loaded
    assert "read_file" in loaded


# -- risoluzione / validazione -------------------------------------------------


def test_get_agent_type_defaults_to_operator() -> None:
    assert get_agent_type(None).name == "operator"


def test_unknown_type_error_names_valid_types() -> None:
    with pytest.raises(UnknownAgentTypeError) as exc:
        validate_agent_type("researchers")
    message = str(exc.value)
    for name in AGENT_TYPE_NAMES:
        assert name in message


def test_spec_rejects_unknown_agent_type() -> None:
    with pytest.raises(UnknownAgentTypeError):
        SubagentSpec(task="t", label="l", agent_type="mad_scientist")


@pytest.mark.asyncio
async def test_spawn_rejects_unknown_agent_type(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from jenny.agent.subagent import SubagentManager
    from jenny.bus.queue import MessageBus

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider, workspace=tmp_path, bus=MessageBus(), max_tool_result_chars=16_000,
    )
    with pytest.raises(UnknownAgentTypeError):
        await mgr.spawn(task="t", agent_type="nope")
    assert not mgr._running_tasks


def test_record_replay_degrades_unknown_type_to_operator() -> None:
    """Un record su disco con un tipo scomparso resta rilanciabile."""
    raw = {
        "task_id": "abc12345",
        "lineage_id": "lin12345",
        "attempt": 1,
        "spec": {"task": "do it", "label": "l", "agent_type": "phrenologist"},
        "state": "failed",
    }
    record = SubagentRecord.from_dict(raw)
    assert record.spec.agent_type == DEFAULT_AGENT_TYPE


def test_coerce_agent_type_passes_known_names() -> None:
    assert coerce_agent_type("coder") == "coder"
    assert coerce_agent_type(None) == DEFAULT_AGENT_TYPE
    assert coerce_agent_type(17) == DEFAULT_AGENT_TYPE


# -- default per tipo ----------------------------------------------------------


def test_type_max_iterations_can_only_narrow_the_configured_cap(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from jenny.agent.subagent import SubagentManager
    from jenny.bus.queue import MessageBus

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider, workspace=tmp_path, bus=MessageBus(),
        max_tool_result_chars=16_000, max_iterations=25,
    )
    # coder chiede 120, la config concede 25: vince la config.
    assert mgr._type_max_iterations(AGENT_TYPES["coder"]) == 25
    # operator non ha default proprio: eredita.
    assert mgr._type_max_iterations(AGENT_TYPES["operator"]) == 25


@pytest.mark.asyncio
async def test_run_subagent_uses_type_defaults_and_prompt(tmp_path: Path) -> None:
    import time
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from jenny.agent.subagent import SubagentManager, SubagentStatus
    from jenny.bus.queue import MessageBus

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider, workspace=tmp_path, bus=MessageBus(),
        max_tool_result_chars=16_000,
    )
    mgr._announce_result = AsyncMock()
    seen: dict[str, object] = {}

    async def fake_run(spec):
        seen["temperature"] = spec.temperature
        seen["max_iterations"] = spec.max_iterations
        seen["tools"] = set(spec.tools.tool_names)
        seen["system"] = spec.initial_messages[0]["content"]
        return SimpleNamespace(
            stop_reason="done", final_content="ok", error=None, tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)
    spec = SubagentSpec(task="analyse", label="l", agent_type="analyst")
    status = SubagentStatus(
        task_id="s1", label="l", task_description="analyse", started_at=time.monotonic(),
    )
    await mgr._run_subagent("s1", spec, status)

    assert seen["temperature"] == AGENT_TYPES["analyst"].temperature
    assert seen["max_iterations"] == AGENT_TYPES["analyst"].max_iterations
    assert "web_search" not in seen["tools"]
    assert "Role: analyst" in str(seen["system"])


@pytest.mark.asyncio
async def test_explicit_temperature_beats_type_default(tmp_path: Path) -> None:
    import time
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from jenny.agent.subagent import SubagentManager, SubagentStatus
    from jenny.bus.queue import MessageBus

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider, workspace=tmp_path, bus=MessageBus(),
        max_tool_result_chars=16_000,
    )
    mgr._announce_result = AsyncMock()
    seen: dict[str, object] = {}

    async def fake_run(spec):
        seen["temperature"] = spec.temperature
        return SimpleNamespace(
            stop_reason="done", final_content="ok", error=None, tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)
    spec = SubagentSpec(task="write", label="l", agent_type="writer", temperature=0.0)
    status = SubagentStatus(
        task_id="s1", label="l", task_description="write", started_at=time.monotonic(),
    )
    await mgr._run_subagent("s1", spec, status)

    assert seen["temperature"] == 0.0


def test_every_type_has_a_prompt_template() -> None:
    from jenny.utils.prompt_templates import render_template

    for name, atype in AGENT_TYPES.items():
        rendered = render_template(atype.prompt_template, strip=True)
        assert f"Role: {name}" in rendered
