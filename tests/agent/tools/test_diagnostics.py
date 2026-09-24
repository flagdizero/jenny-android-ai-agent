"""Test per il tool diagnostics: ring buffer log in-memory + get_recent_logs.

Copre: ``install_log_buffer`` (idempotenza, cattura reale via loguru),
lettura/filtro/troncamento del buffer, degradazione pulita quando il buffer
non è ancora installato/popolato, e il contratto dello schema del tool.

Il buffer e il sink loguru sono stato globale di modulo: ogni test isola e
ripulisce esplicitamente per non inquinare gli altri test della suite.
"""

from __future__ import annotations

import pytest
from loguru import logger

from jenny.agent.tools import diagnostics
from jenny.agent.tools.diagnostics import GetRecentLogsTool, install_log_buffer


@pytest.fixture(autouse=True)
def _isolated_log_state():
    """Pulisce buffer e sink loguru prima e dopo ogni test di questo modulo."""
    diagnostics._LOG_BUFFER.clear()
    yield
    if diagnostics._SINK_ID is not None:
        try:
            logger.remove(diagnostics._SINK_ID)
        except ValueError:
            pass  # già rimosso dal test stesso
        diagnostics._SINK_ID = None
    diagnostics._LOG_BUFFER.clear()


def _tool() -> GetRecentLogsTool:
    return GetRecentLogsTool()


# ---------------------------------------------------------------------------
# install_log_buffer
# ---------------------------------------------------------------------------


async def test_install_log_buffer_captures_real_loguru_messages():
    install_log_buffer()

    logger.debug("marker-diagnostics-basic-token")

    result = await _tool().execute()

    assert "marker-diagnostics-basic-token" in result


def test_install_log_buffer_is_idempotent():
    install_log_buffer()
    first_id = diagnostics._SINK_ID

    install_log_buffer()

    assert diagnostics._SINK_ID == first_id


async def test_install_log_buffer_does_not_duplicate_lines_on_reinstall():
    install_log_buffer()
    install_log_buffer()

    logger.debug("marker-no-duplicate-token")

    result = await _tool().execute(module_filter="marker-no-duplicate-token")
    lines = result.splitlines()

    assert lines == [line for line in lines if "marker-no-duplicate-token" in line]
    assert len(lines) == 1


# ---------------------------------------------------------------------------
# get_recent_logs: degradazione senza buffer / vuoto
# ---------------------------------------------------------------------------


async def test_get_recent_logs_without_any_capture_yet():
    # Buffer vuoto (ripulito dalla fixture); nessun sink necessariamente installato.
    result = await _tool().execute()

    assert result == "No recent log lines captured yet."


async def test_get_recent_logs_filter_with_empty_buffer():
    result = await _tool().execute(module_filter="anything")

    assert result == "No recent log lines matching 'anything'."


# ---------------------------------------------------------------------------
# get_recent_logs: lettura/filtro/limiti (buffer popolato direttamente)
# ---------------------------------------------------------------------------


async def test_module_filter_matches_substring_case_insensitive():
    diagnostics._LOG_BUFFER.append("INFO android_web: fetch ok")
    diagnostics._LOG_BUFFER.append("INFO python_exec: run ok")

    result = await _tool().execute(module_filter="ANDROID_WEB")

    assert "android_web" in result
    assert "python_exec" not in result


async def test_module_filter_no_match_returns_specific_message():
    diagnostics._LOG_BUFFER.append("INFO android_web: fetch ok")

    result = await _tool().execute(module_filter="nonexistent_module")

    assert result == "No recent log lines matching 'nonexistent_module'."


async def test_lines_returned_in_chronological_order():
    for i in range(5):
        diagnostics._LOG_BUFFER.append(f"line-{i}")

    result = await _tool().execute()

    assert result.splitlines() == ["line-0", "line-1", "line-2", "line-3", "line-4"]


async def test_count_limits_to_most_recent_lines():
    for i in range(10):
        diagnostics._LOG_BUFFER.append(f"line-{i}")

    result = await _tool().execute(count=3)

    assert result.splitlines() == ["line-7", "line-8", "line-9"]


async def test_count_defaults_to_50():
    for i in range(60):
        diagnostics._LOG_BUFFER.append(f"line-{i}")

    result = await _tool().execute()
    lines = result.splitlines()

    assert len(lines) == 50
    assert lines[0] == "line-10"
    assert lines[-1] == "line-59"


async def test_count_zero_is_falsy_and_falls_back_to_default():
    # Comportamento reale (sospetto): `count or _DEFAULT_COUNT` tratta 0 come
    # "non specificato" perché 0 è falsy in Python, quindi count=0 non
    # restituisce zero righe ma il default (50), non il minimo (1).
    for i in range(5):
        diagnostics._LOG_BUFFER.append(f"line-{i}")

    result = await _tool().execute(count=0)

    assert result.splitlines() == ["line-0", "line-1", "line-2", "line-3", "line-4"]


async def test_count_clamped_to_minimum_of_one():
    for i in range(5):
        diagnostics._LOG_BUFFER.append(f"line-{i}")

    result = await _tool().execute(count=-1)

    assert result.splitlines() == ["line-4"]


async def test_count_clamped_to_maximum():
    for i in range(300):
        diagnostics._LOG_BUFFER.append(f"line-{i}")

    result = await _tool().execute(count=99999)
    lines = result.splitlines()

    assert len(lines) == 200
    assert lines[-1] == "line-299"


# ---------------------------------------------------------------------------
# Ring buffer: capacità massima ed eviction del più vecchio
# ---------------------------------------------------------------------------


def test_ring_buffer_evicts_oldest_beyond_max_size():
    for i in range(600):
        diagnostics._LOG_BUFFER.append(f"line-{i}")

    assert len(diagnostics._LOG_BUFFER) == 500
    assert diagnostics._LOG_BUFFER[0] == "line-100"
    assert diagnostics._LOG_BUFFER[-1] == "line-599"


# ---------------------------------------------------------------------------
# Contratto dello schema del tool
# ---------------------------------------------------------------------------


def test_tool_name_and_read_only():
    tool = _tool()

    assert tool.name == "get_recent_logs"
    assert tool.read_only is True


def test_tool_parameters_schema_contract():
    tool = _tool()
    params = tool.parameters

    assert params["type"] == "object"
    assert set(params["properties"]) == {"module_filter", "count"}
    assert params["properties"]["count"]["minimum"] == 1
    assert params["properties"]["count"]["maximum"] == 200
    # required=[] è omesso dallo schema JSON (nessuna chiave "required").
    assert "required" not in params


def test_get_recent_logs_appears_in_module_tools_registry():
    assert diagnostics.TOOLS == [GetRecentLogsTool]
