"""I tentativi di aggirare il guard di ``python_exec`` visti sul device restano respinti.

Il 04/10/2026, sull'emulatore ``jenny_s22`` (build 1.0.0 debug da ``main``), un
subagent «analyst» a cui era stato chiesto uno script per un rapporto sulle
innaffiature ha sbattuto contro il guard (``argparse``, ``subprocess``,
``os.system``) e ha provato a girarci intorno con tre chiamate, riprodotte qui
alla lettera nella sostanza:

1. elencare ``sys.modules`` cercando i moduli del tool per arrivare alle funzioni
   registrate;
2. mettere un modulo finto in ``sys.modules`` e importarlo con ``exec``;
3. chiamare una funzione inesistente per sondare il dispatch.

Alla fine ha riferito di aver usato «solo stdlib (argparse, datetime, json, os,
sys)»: ``argparse`` non è nell'allowlist, quindi quel resoconto non può essere
vero, e il test lo fissa.

Ogni caso passa da ``await PythonExecTool.execute(...)`` — la strada di
produzione, che salta su un worker con ``run_in_executor`` — e di nuovo in
modalità sessione (``yield_time_ms``), che gira su un thread proprio. Un test che
chiama ``PythonNamespace.execute`` sul thread del test non prova niente su
quelle due strade; e una che non salta davvero di thread nemmeno, per questo
``_spy_threads`` verifica che il salto ci sia stato.

Non è una dichiarazione che il guard sia una sandbox: non lo è (v. il commento
TRUST BOUNDARY in testa a ``python_exec.py``). È il blocco di queste tre
chiamate precise, perché se una di loro un giorno passa lo si sappia subito.
"""

from __future__ import annotations

import threading

import pytest

from jenny.agent.tools.exec_session import ExecSessionManager
from jenny.agent.tools.python_exec import PythonExecTool, PythonNamespace
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.config.tool_schemas import PythonExecConfig


@pytest.fixture
def manager():
    m = ExecSessionManager()
    yield m
    m.shutdown()


@pytest.fixture
def tool(tmp_path, manager) -> PythonExecTool:
    """Il tool come lo costruisce ``create()``: liste di default, confine attivo."""
    cfg = PythonExecConfig()
    t = PythonExecTool(
        working_dir=str(tmp_path),
        timeout=30,
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=True,
        workspace=str(tmp_path),
        session_manager=manager,
    )
    _register_builtin_functions(t.namespace, workspace=str(tmp_path), restrict_to_workspace=True)
    return t


@pytest.fixture
def _spy_threads(monkeypatch):
    """Registra su quale thread gira davvero il codice guardato."""
    seen: list[int] = []
    real_execute = PythonNamespace.execute
    real_call = PythonNamespace.call_function

    def _execute(self, *a, **kw):
        seen.append(threading.get_ident())
        return real_execute(self, *a, **kw)

    def _call(self, *a, **kw):
        seen.append(threading.get_ident())
        return real_call(self, *a, **kw)

    monkeypatch.setattr(PythonNamespace, "execute", _execute)
    monkeypatch.setattr(PythonNamespace, "call_function", _call)
    return seen


# Il segnale che il codice è arrivato oltre il punto da bloccare. Si compone a
# runtime perché il traceback ripete il sorgente: un ``print('X')`` mai eseguito
# metterebbe comunque ``X`` nell'output.
_REACHED = "REACHED"
_PRINT_REACHED = "print('REA' + 'CHED')"


# Le due strade di produzione. La sessione con 5 s di yield restituisce l'esito
# completo di uno snippet breve, quindi le asserzioni sono le stesse.
_MODES = {
    "one-shot": {},
    "session": {"yield_time_ms": 5000},
}


async def _run(tool: PythonExecTool, seen: list[int], mode: str, **call) -> str:
    out = await tool.execute(**call, **_MODES[mode])
    assert seen, "il codice guardato non è mai arrivato a PythonNamespace"
    assert threading.get_ident() not in seen, "nessun salto di thread: il test non misura niente"
    return out


@pytest.mark.parametrize("mode", list(_MODES))
async def test_enumerating_sys_modules_shows_no_tool_or_host_module(tool, _spy_threads, mode):
    """Tentativo 1: la vista di ``sys.modules`` non contiene ``jenny.*``."""
    out = await _run(
        tool, _spy_threads, mode,
        code=(
            "import sys\n"
            "names = list(sys.modules)\n"
            "print('HOST', [n for n in names if n.split('.')[0] == 'jenny'])\n"
            "print('TOOLS', [n for n in names if 'python_exec' in n or 'tools' in n])\n"
        ),
    )
    assert "HOST []" in out, out
    # Ciò che resta sono moduli stdlib dell'allowlist (``itertools``,
    # ``functools``…), nessuno del tool.
    assert "python_exec" not in out.split("TOOLS", 1)[1].splitlines()[0], out


@pytest.mark.parametrize("mode", list(_MODES))
async def test_sys_modules_lookup_of_a_host_module_is_refused(tool, _spy_threads, mode):
    """Anche chiedendolo per nome, il modulo del tool non si raggiunge."""
    out = await _run(
        tool, _spy_threads, mode,
        code="import sys\nsys.modules['jenny.agent.tools.python_exec']\n" + _PRINT_REACHED,
    )
    assert "is not accessible in python_exec" in out, out
    assert _REACHED not in out, out


@pytest.mark.parametrize("mode", list(_MODES))
async def test_writing_into_sys_modules_is_refused(tool, _spy_threads, mode):
    """Tentativo 2, primo passo: la vista di ``sys.modules`` non accetta scritture."""
    out = await _run(
        tool, _spy_threads, mode,
        code="import sys\nsys.modules['argparse'] = object()\n" + _PRINT_REACHED,
    )
    assert _REACHED not in out, out
    assert "TypeError" in out, out


@pytest.mark.parametrize("mode", list(_MODES))
async def test_the_injection_as_logged_is_refused(tool, _spy_threads, mode):
    """Tentativo 2 come è stato scritto: si ferma già sull'import di ``types``."""
    out = await _run(
        tool, _spy_threads, mode,
        code=(
            "import sys, types\n"
            "fake = types.ModuleType('argparse')\n"
            "sys.modules['argparse'] = fake\n"
            "exec('import argparse')\n" + _PRINT_REACHED
        ),
    )
    assert _REACHED not in out, out
    assert "Module 'types' is not in the allowed modules list" in out, out


@pytest.mark.parametrize("mode", list(_MODES))
async def test_import_inside_exec_goes_through_the_guard(tool, _spy_threads, mode):
    """Tentativo 2, secondo passo: ``exec`` usa gli stessi builtins, quindi lo stesso guard."""
    out = await _run(
        tool, _spy_threads, mode,
        code="exec('import argparse')\n" + _PRINT_REACHED,
    )
    assert _REACHED not in out, out
    assert "Module 'argparse' is not in the allowed modules list" in out, out


@pytest.mark.parametrize("mode", list(_MODES))
async def test_a_nonexistent_function_is_a_plain_not_found(tool, _spy_threads, mode):
    """Tentativo 3: la sonda riceve un «non trovata», nessun dettaglio del dispatch."""
    out = await _run(tool, _spy_threads, mode, function="__nonexistent_probe__")
    assert "Function '__nonexistent_probe__' not found in namespace" in out, out
    assert "Traceback" not in out, out


@pytest.mark.parametrize("mode", list(_MODES))
async def test_argparse_is_not_importable(tool, _spy_threads, mode):
    """Il resoconto finale del subagent («ho usato argparse») non può essere vero."""
    out = await _run(tool, _spy_threads, mode, code="import argparse\n" + _PRINT_REACHED)
    assert _REACHED not in out, out
    assert "Module 'argparse' is not in the allowed modules list" in out, out


@pytest.mark.parametrize("mode", list(_MODES))
async def test_the_original_refusals_still_hold(tool, _spy_threads, mode):
    """Gli scontri che hanno fatto partire i tentativi: ``subprocess`` e ``os.system``."""
    out = await _run(
        tool, _spy_threads, mode,
        code="import sys\nprint(sys.modules['subprocess'])",
    )
    assert "Module 'subprocess' is not accessible in python_exec" in out, out

    out = await _run(
        tool, _spy_threads, mode,
        code="import os\nos.system('true')\n" + _PRINT_REACHED,
    )
    assert _REACHED not in out, out
    assert "OSError: This function is not available on this platform" in out, out
