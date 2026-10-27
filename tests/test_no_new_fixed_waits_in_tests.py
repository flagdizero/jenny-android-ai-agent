"""Un test non aspetta un tempo fisso: aspetta la cosa.

``await asyncio.sleep(0.3)`` «perché il server sia su» costa 0,3 s sempre, ed è
poco su una macchina carica, dove il test fallisce a caso. Il 26/10/2026 ce
n'erano 61 così solo nei test WebSocket, e insieme a una manciata di grace vere
facevano quasi un minuto della suite. Si aspetta la condizione:
``support.aio.wait_until``, ``port_alloc.wait_listening``, un ``Event``.

Qui si contano, file per file, le ``asyncio.sleep(x)`` con ``x`` letterale fra
0,2 e 30 secondi. Sotto 0,2 è un giro di loop o un polling; da 30 in su è un
lavoro finto che il test cancella (``sleep(60)``, ``sleep(3600)``) e non costa
niente. In mezzo c'è il tempo perso, e il tetto è quello che c'era: un numero
si può solo abbassare. Un'attesa che serve davvero (un timeout vero da far
scadere, una grace da misurare) entra alzando il tetto del suo file in questo
stesso commit, e il commit dice perché.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent

_LOW, _HIGH = 0.2, 30.0

# Il tetto di ogni file: quante attese fisse ha oggi. Si abbassa, non si alza.
_BUDGET = {
    "agent/test_plan_single_wake.py": 3,
    "agent/test_session_routing.py": 3,
    "agent/test_tool_result_hook.py": 1,
    "agent/tools/test_browser.py": 2,
    "agent/tools/test_exec_session.py": 1,
    "agent/tools/test_python_exec_base_per_call.py": 1,
    "agent/tools/test_python_exec_interrupt.py": 1,
    "agent/tools/test_ssh_backend_dev.py": 1,
    "apps/test_proxy.py": 1,
    "apps/test_proxy_keepalive.py": 1,
    "channels/test_remote_media_does_not_stall_dispatch.py": 1,
    "channels/test_websocket_channel.py": 1,
    "channels/test_websocket_http_open_timeout.py": 1,
    "channels/test_websocket_integration.py": 3,
    "channels/test_websocket_subagent_activity.py": 1,
    "channels/test_ws_remote.py": 4,
    "cron/test_cron_pause.py": 1,
    "cron/test_cron_service.py": 3,
    "cron/test_cron_timer_pass.py": 1,
    "providers/test_read_timeout_covers_first_output.py": 1,
    "snapshot/test_snapshot_cancelled_mid_scan.py": 1,
    "snapshot/test_snapshot_service.py": 3,
    "webui/test_phone_only_remote.py": 1,
    "webui/test_project_greet.py": 1,
}


def _fixed_waits(tree: ast.AST) -> list[int]:
    """Le righe delle ``asyncio.sleep(<numero>)`` nell'intervallo che costa."""
    lines = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "sleep"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "asyncio"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            continue
        value = node.args[0].value
        if isinstance(value, (int, float)) and _LOW <= value < _HIGH:
            lines.append(node.lineno)
    return lines


def _count() -> dict[str, list[int]]:
    found = {}
    for path in sorted(TESTS.rglob("*.py")):
        lines = _fixed_waits(ast.parse(path.read_text(encoding="utf-8")))
        if lines:
            found[path.relative_to(TESTS).as_posix()] = lines
    return found


def test_no_file_waits_longer_than_it_did() -> None:
    over = {
        name: lines for name, lines in _count().items() if len(lines) > _BUDGET.get(name, 0)
    }
    assert not over, (
        "attese fisse nuove (asyncio.sleep fra 0,2 e 30 s): aspetta la condizione, "
        "non il tempo (support.aio.wait_until, port_alloc.wait_listening):\n"
        + "\n".join(f"  {name}: righe {lines}" for name, lines in over.items())
    )


def test_the_budget_follows_the_files_down() -> None:
    """Un tetto più alto del necessario lascia rientrare un'attesa senza che
    nessuno lo veda: quando un file ne toglie una, il suo numero scende."""
    found = _count()
    loose = {
        name: (cap, len(found.get(name, [])))
        for name, cap in _BUDGET.items()
        if len(found.get(name, [])) < cap
    }
    assert not loose, "abbassa il tetto in _BUDGET:\n" + "\n".join(
        f"  {name}: tetto {cap}, attese {now}" for name, (cap, now) in loose.items()
    )


def test_the_guard_sees_a_fixed_wait() -> None:
    tree = ast.parse(
        "async def t():\n"
        "    await asyncio.sleep(0.3)\n"   # contata
        "    await asyncio.sleep(0.05)\n"  # un giro di polling
        "    await asyncio.sleep(3600)\n"  # lavoro finto, cancellato
        "    await asyncio.sleep(delay)\n"  # non letterale
    )
    assert _fixed_waits(tree) == [2]
