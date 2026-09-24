"""L'output troppo lungo si tronca tenendo testa e coda, uguale per exec e sessioni.

``python_exec`` e le sue sessioni avevano ognuno la propria copia del taglio:
metà limite in testa, metà in coda, e in mezzo quanti caratteri sono spariti.
Nessun test lo guardava. Qui si fissa il formato esatto su entrambi, ricavato
dall'output pieno della stessa esecuzione.
"""

from __future__ import annotations

from pathlib import Path

from jenny.agent.tools.exec_session import _PythonSession
from jenny.agent.tools.python_exec import PythonNamespace, run_python_async

CODE = "print('A' * 60 + 'B' * 60)"


def _expected(full: str, limit: int) -> str:
    half = limit // 2
    return full[:half] + f"\n\n... ({len(full) - limit:,} chars truncated) ...\n\n" + full[-half:]


async def _run(tmp_path: Path, limit: int) -> str:
    ns = PythonNamespace(working_dir=str(tmp_path), workspace=str(tmp_path))
    return await run_python_async(
        code=CODE, function=None, args=None, kwargs=None,
        namespace=ns, timeout=10, max_output_chars=limit,
    )


async def test_python_exec_keeps_head_and_tail(tmp_path: Path) -> None:
    full = await _run(tmp_path, 100_000)
    assert len(full) > 100
    assert await _run(tmp_path, 40) == _expected(full, 40)


async def test_short_output_is_untouched(tmp_path: Path) -> None:
    full = await _run(tmp_path, 100_000)
    assert await _run(tmp_path, len(full)) == full


def _session_output(tmp_path: Path, limit: int):
    ns = PythonNamespace(working_dir=str(tmp_path), workspace=str(tmp_path))
    session = _PythonSession(
        session_id="s1", code=CODE, function=None, args=None, kwargs=None,
        namespace=ns, timeout=10,
    )
    session._thread.join(timeout=10)
    return session.poll(0, limit)


def test_a_session_keeps_head_and_tail_and_counts_the_cut(tmp_path: Path) -> None:
    full = _session_output(tmp_path, 100_000).output
    poll = _session_output(tmp_path, 40)
    assert poll.output == _expected(full, 40)
    assert poll.truncated_chars == len(full) - 40


def test_a_session_under_the_limit_reports_no_cut(tmp_path: Path) -> None:
    poll = _session_output(tmp_path, 100_000)
    assert poll.truncated_chars == 0
    assert "chars truncated" not in poll.output
