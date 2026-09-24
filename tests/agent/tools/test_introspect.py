"""Test per GetSourceTool: introspezione del sorgente di jenny, sola lettura.

Copre: risoluzione di target validi (modulo/classe/funzione), rifiuto di
target fuori dal perimetro ``jenny.*`` (incluso traversal via path
separator), target inesistenti, troncamento oltre ``_MAX_SOURCE_CHARS``, il
fallback su sorgente estratta quando ``inspect.getsource`` fallisce, e il
contratto dello schema del tool.
"""

from __future__ import annotations

import inspect

import pytest

import jenny.agent.tools.diagnostics as diagnostics_module
from jenny.agent.tools import introspect
from jenny.agent.tools.introspect import GetSourceTool


def _tool() -> GetSourceTool:
    return GetSourceTool()


# ---------------------------------------------------------------------------
# Risoluzione di target validi
# ---------------------------------------------------------------------------


async def test_reads_source_of_a_module():
    result = await _tool().execute(target="jenny.agent.tools.diagnostics")

    assert result.startswith("# ")
    assert "diagnostics.py" in result.splitlines()[0]
    assert "class GetRecentLogsTool" in result


async def test_reads_source_of_a_class():
    result = await _tool().execute(target="jenny.agent.tools.introspect.GetSourceTool")

    assert "class GetSourceTool(Tool):" in result


async def test_reads_source_of_a_function():
    result = await _tool().execute(target="jenny.agent.tools.introspect._resolve_target")

    assert "def _resolve_target(target: str) -> Any:" in result


async def test_target_is_stripped_of_whitespace():
    result = await _tool().execute(target="  jenny.agent.tools.introspect._resolve_target  ")

    assert "def _resolve_target" in result


async def test_target_equal_to_bare_jenny_resolves_package():
    # "jenny" (senza punto) è l'unico caso speciale ammesso oltre a "jenny.*".
    result = await _tool().execute(target="jenny")

    assert result.startswith("# ")


# ---------------------------------------------------------------------------
# Rifiuto di path fuori perimetro / traversal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "os",
        "os.path",
        "builtins",
        "../etc/passwd",
        "jenny/../../etc/passwd",
        "jennywrong",  # prefisso stringa ma non un vero sotto-pacchetto jenny.*
        "",
    ],
)
async def test_rejects_targets_outside_jenny_package(target):
    result = await _tool().execute(target=target)

    assert result.startswith("Error:")
    assert "only exposes the jenny package" in result


# ---------------------------------------------------------------------------
# Target inesistenti
# ---------------------------------------------------------------------------


async def test_nonexistent_module_returns_error():
    result = await _tool().execute(target="jenny.agent.tools.does_not_exist_xyz")

    assert result.startswith("Error: cannot resolve")


async def test_nonexistent_attribute_on_real_module_returns_error():
    result = await _tool().execute(
        target="jenny.agent.tools.introspect.NoSuchClassHere"
    )

    assert result.startswith("Error: cannot resolve")


async def test_trailing_dot_target_returns_error():
    result = await _tool().execute(target="jenny.")

    assert result.startswith("Error: cannot resolve")


# ---------------------------------------------------------------------------
# Troncamento sorgente lunga
# ---------------------------------------------------------------------------


async def test_source_is_truncated_beyond_max_chars(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(introspect, "_MAX_SOURCE_CHARS", 40)

    result = await _tool().execute(target="jenny.agent.tools.diagnostics")

    assert "... [truncated]" in result
    # La parte di sorgente (dopo l'header "# path\n") non supera il limite.
    body = result.split("\n", 1)[1]
    assert len(body) <= 40 + len("\n... [truncated]")


# ---------------------------------------------------------------------------
# Fallback su sorgente estratta quando inspect.getsource fallisce
# ---------------------------------------------------------------------------


async def test_falls_back_to_extracted_source_when_getsource_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    """Simula una build pacchettizzata (.imy) senza .py accanto al bytecode.

    In dev ``get_package_source_root`` ritorna la vera directory del
    pacchetto ``jenny``, quindi il fallback legge il file reale da disco.
    """

    def boom(_obj):
        raise OSError("no source available (simulated packaged build)")

    monkeypatch.setattr(introspect.inspect, "getsource", boom)

    result = await _tool().execute(target="jenny.agent.tools.diagnostics")

    assert result.startswith("# ")
    assert result.splitlines()[0].endswith("diagnostics.py")
    assert "class GetRecentLogsTool" in result


async def test_fallback_reports_error_when_no_source_root_available(
    monkeypatch: pytest.MonkeyPatch,
):
    def boom(_obj):
        raise OSError("no source available (simulated packaged build)")

    monkeypatch.setattr(introspect.inspect, "getsource", boom)
    monkeypatch.setattr("jenny.utils.android_assets.get_package_source_root", lambda: None)

    result = await _tool().execute(target="jenny.agent.tools.diagnostics")

    assert result.startswith("Error: source not available")
    assert "packaged build" in result


def test_read_from_source_root_package_dir_layout(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Layout dev: root è la directory del pacchetto stesso (root/agent/tools/...)."""
    target_dir = tmp_path / "agent" / "tools"
    target_dir.mkdir(parents=True)
    probe = target_dir / "diagnostics.py"
    probe.write_text("# fake dev source\n", encoding="utf-8")

    monkeypatch.setattr("jenny.utils.android_assets.get_package_source_root", lambda: tmp_path)

    result = introspect._read_from_source_root(
        "jenny.agent.tools.diagnostics", diagnostics_module
    )

    assert result is not None
    path, source = result
    assert path == str(probe)
    assert source == "# fake dev source\n"


def test_read_from_source_root_extracted_assets_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """Layout asset estratti: root contiene una sotto-cartella jenny/ (APK)."""
    target_dir = tmp_path / "jenny" / "agent" / "tools"
    target_dir.mkdir(parents=True)
    probe = target_dir / "diagnostics.py"
    probe.write_text("# fake extracted source\n", encoding="utf-8")

    monkeypatch.setattr("jenny.utils.android_assets.get_package_source_root", lambda: tmp_path)

    result = introspect._read_from_source_root(
        "jenny.agent.tools.diagnostics", diagnostics_module
    )

    assert result is not None
    path, source = result
    assert path == str(probe)
    assert source == "# fake extracted source\n"


def test_read_from_source_root_returns_none_without_source_root(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("jenny.utils.android_assets.get_package_source_root", lambda: None)

    result = introspect._read_from_source_root(
        "jenny.agent.tools.diagnostics", diagnostics_module
    )

    assert result is None


def test_read_from_source_root_returns_none_when_files_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    # root esiste ma non contiene il file atteso in nessun layout candidato.
    monkeypatch.setattr("jenny.utils.android_assets.get_package_source_root", lambda: tmp_path)

    result = introspect._read_from_source_root(
        "jenny.agent.tools.diagnostics", diagnostics_module
    )

    assert result is None


# ---------------------------------------------------------------------------
# _resolve_target
# ---------------------------------------------------------------------------


def test_resolve_target_walks_attributes():
    obj = introspect._resolve_target("jenny.agent.tools.introspect.GetSourceTool")

    assert obj is GetSourceTool


def test_resolve_target_raises_import_error_for_unimportable_prefix():
    with pytest.raises(ImportError):
        introspect._resolve_target("totally.not.a.real.package")


# ---------------------------------------------------------------------------
# Contratto dello schema del tool
# ---------------------------------------------------------------------------


def test_tool_name_and_read_only():
    tool = _tool()

    assert tool.name == "get_source"
    assert tool.read_only is True


def test_tool_parameters_schema_contract():
    tool = _tool()
    params = tool.parameters

    assert params["type"] == "object"
    assert "target" in params["properties"]
    assert params["properties"]["target"]["type"] == "string"
    assert params["required"] == ["target"]


def test_tool_to_schema_contract():
    schema = _tool().to_schema()

    assert schema["type"] == "function"
    assert schema["function"]["name"] == "get_source"
    assert schema["function"]["parameters"]["required"] == ["target"]


def test_get_source_appears_in_module_tools_registry():
    assert introspect.TOOLS == [GetSourceTool]


def test_inspect_getsource_sanity_unaffected_outside_monkeypatch():
    # Verifica di controllo: fuori dai test col monkeypatch, inspect.getsource
    # funziona normalmente (nessuna fuga di stato tra i test precedenti).
    source = inspect.getsource(GetSourceTool)
    assert "class GetSourceTool" in source
