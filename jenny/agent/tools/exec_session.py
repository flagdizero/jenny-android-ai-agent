"""Session support for long-running Python exec workflows."""

from __future__ import annotations

import atexit
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import current_request_session_key
from jenny.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from jenny.utils.helpers import truncate_head_tail

DEFAULT_YIELD_MS = 1000
MAX_YIELD_MS = 30_000
DEFAULT_WAIT_FOR_MS = 10_000
MAX_WAIT_FOR_MS = 120_000
DEFAULT_MAX_OUTPUT_CHARS = 10_000
MAX_OUTPUT_CHARS = 50_000


@dataclass(slots=True)
class _SessionPoll:
    output: str
    done: bool
    exit_code: int | None
    elapsed_s: float = 0.0
    timed_out: bool = False
    terminated: bool = False
    truncated_chars: int = 0


class _SessionStopped(BaseException):
    """Internal signal raised at a cooperative-cancellation checkpoint.

    Subclasses BaseException (not Exception) so a broad ``except Exception``
    in user code cannot accidentally swallow a requested stop, mirroring how
    CPython itself uses BaseException (e.g. KeyboardInterrupt) for cooperative
    interrupts.

    Non basta più da solo: ``PythonNamespace.execute``/``call_function`` sono
    diventate ``except BaseException`` (confine del sandbox contro il
    ``SystemExit`` che uccideva il gateway) e ingoiavano anche questa,
    rendendo morto l'``except _SessionStopped`` di ``_run`` qui sotto. Sono
    perciò elencate esplicitamente nelle loro tuple di carve-out — quella è
    l'invariante da tenere ferma, non la sola discendenza da BaseException.
    """


@dataclass(slots=True)
class ExecSessionInfo:
    session_id: str
    command: str
    cwd: str
    elapsed_s: float
    idle_s: float
    remaining_s: float
    returncode: int | None
    owner_session_key: str | None = None


# ---------------------------------------------------------------------------
# Python thread-based session
# ---------------------------------------------------------------------------

class _PythonSession:
    """A Python code execution session running in a background thread."""

    def __init__(
        self,
        *,
        session_id: str,
        code: str | None,
        function: str | None,
        args: list | None,
        kwargs: dict | None,
        namespace: Any,
        timeout: int | None,
        owner_session_key: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.code = code
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.namespace = namespace
        self.owner_session_key = owner_session_key
        self.started_at = time.monotonic()
        self.deadline = time.monotonic() + timeout if timeout else float("inf")
        self.last_access = time.monotonic()
        self._output_chunks: list[str] = []
        self._lock = threading.Lock()
        self._done = False
        self._error: str | None = None
        self._timed_out = False
        self._terminated = False
        self._exit_code: int | None = None
        self._stop_event = threading.Event()

        # Build a human-readable description for listing
        if function:
            self._description = f"python_exec(function={function!r})"
        else:
            code_preview = (code or "").replace("\n", " ")
            if len(code_preview) > 100:
                code_preview = code_preview[:99] + "..."
            self._description = f"python_exec(code={code_preview!r})"

        # Start execution in background thread (non-daemon so Android/Chaquopy
        # does not kill the interpreter while user code is running).
        self._thread = threading.Thread(target=self._run, daemon=False)
        self._thread.start()

    def _make_checkpoint(self):
        """Build a trace function that raises _SessionStopped once stop() is
        called, checked between top-level statements/loop iterations of the
        directly-submitted code.

        Only the frame for the submitted code itself (compiled with the
        default "<string>" filename by eval/exec) gets line-level tracing;
        nested calls into library/user functions return None and are left
        untraced, so the overhead stays bounded to the submitted code rather
        than everything it calls.
        """
        stop_event = self._stop_event

        def _checkpoint(frame, event, arg):
            if stop_event.is_set():
                raise _SessionStopped()
            if event == "call" and frame.f_code.co_filename == "<string>":
                return _checkpoint
            return None

        return _checkpoint

    def _run(self):
        # Note: no redirect_stdout/redirect_stderr here. The actual stdout/
        # stderr capture happens inside self.namespace.execute()/
        # call_function() (python_exec.py), which route the stream PER THREAD
        # (`_ThreadRoutedStream`) so that genuinely concurrent executions —
        # this background thread vs. another session's thread, or a one-shot
        # python_exec call on the dedicated pool — each get their own buffer
        # without any process-wide serialisation. Redirecting again at this
        # layer would be redundant (these buffers are never read; the inner
        # call's returned strings are used instead) and would defeat the
        # per-thread routing by mutating sys.stdout globally again.
        try:
            if self._stop_event.is_set():
                raise _SessionStopped()
            sys.settrace(self._make_checkpoint())
            try:
                if self.function:
                    stdout, stderr, result = self.namespace.call_function(
                        self.function, self.args, self.kwargs,
                    )
                    if stdout:
                        self._output_chunks.append(stdout)
                    if stderr:
                        self._output_chunks.append(f"STDERR:\n{stderr}")
                    if result is not None:
                        self._output_chunks.append(format_result_line(result))
                elif self.code:
                    stdout, stderr, result = self.namespace.execute(self.code)
                    if stdout:
                        self._output_chunks.append(stdout)
                    if stderr:
                        self._output_chunks.append(f"STDERR:\n{stderr}")
                    if result is not None:
                        self._output_chunks.append(format_result_line(result))
                else:
                    self._output_chunks.append("Error: Provide 'code' or 'function'")
                    self._exit_code = 1
            finally:
                sys.settrace(None)
        except _SessionStopped:
            self._output_chunks.append("Execution stopped (session was terminated).")
            self._exit_code = -1
        # `BaseException` e non `Exception`, per il motivo opposto a quello che
        # vale in `PythonNamespace.execute`. Là la tupla di carve-out ri-alza
        # `asyncio.CancelledError` e `PythonExecInterrupted` perché a valle c'è
        # un consumatore (l'await di `run_python_async`) che sa cosa farne. Qui
        # siamo nel corpo di un thread che NESSUNO aspetta: qualunque cosa
        # sfugga muore stampando "Exception in thread" sullo stderr vero, e il
        # `finally` qui sotto riporta al modello output vuoto ed exit code 0 —
        # cioè successo. Un `raise asyncio.CancelledError()` (o `SystemExit`)
        # scritto dal codice utente diventava esattamente questo.
        # `_SessionStopped` resta sopra: è l'unico caso che non è un errore.
        except BaseException:
            tb = traceback.format_exc()
            self._output_chunks.append(f"STDERR:\n{tb}")
            self._exit_code = 1
        finally:
            with self._lock:
                self._done = True
                if self._exit_code is None:
                    self._exit_code = 0

    def poll(self, yield_time_ms: int, max_output_chars: int) -> _SessionPoll:
        self.last_access = time.monotonic()

        if yield_time_ms > 0 and not self._done:
            time.sleep(min(yield_time_ms, MAX_YIELD_MS) / 1000)

        if not self._done and time.monotonic() >= self.deadline:
            # Percorso deadline: segnala lo stop al thread e marca SOLO
            # _timed_out (non _terminated), così il timeout resta distinto
            # da una terminazione volontaria dell'utente.
            self._signal_stop()
            self._timed_out = True

        with self._lock:
            output = "".join(self._output_chunks)
            self._output_chunks.clear()

        output, truncated = truncate_head_tail(output, max_output_chars)

        return _SessionPoll(
            output=output,
            done=self._done,
            exit_code=self._exit_code,
            elapsed_s=max(0.0, time.monotonic() - self.started_at),
            timed_out=self._timed_out,
            terminated=self._terminated,
            truncated_chars=truncated,
        )

    def terminate(self) -> None:
        """Mark the session finished and signal the background thread to
        stop cooperatively at its next checkpoint (see stop())."""
        self.stop()

    def _signal_stop(self) -> None:
        """Segnala al thread di esecuzione di fermarsi appena possibile.

        Imposta l'evento di cancellazione cooperativa osservato dal checkpoint
        installato in _run() e marca la sessione come conclusa dal punto di
        vista del chiamante (il thread di background può impiegare un istante
        in più a uscire davvero, al suo prossimo checkpoint). Non tocca né
        _terminated né _timed_out: la scelta dello stato spetta al chiamante.
        """
        self._stop_event.set()
        self._done = True

    def stop(self) -> None:
        """Termina volontariamente la sessione (richiesta utente / shutdown).

        Segnala lo stop cooperativo e marca _terminated, distinto dal timeout
        di deadline gestito in poll().
        """
        self._signal_stop()
        self._terminated = True

    def join(self, timeout: float | None = None) -> None:
        """Wait for the execution thread to finish."""
        self._thread.join(timeout)


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------

class ExecSessionManager:
    def __init__(self, *, max_sessions: int = 8, idle_timeout: int = 1800) -> None:
        self.max_sessions = max_sessions
        self.idle_timeout = idle_timeout
        self._python_sessions: dict[str, _PythonSession] = {}

    # --- Python sessions ---

    async def start_python(
        self,
        *,
        code: str | None,
        function: str | None,
        args: list | None,
        kwargs: dict | None,
        namespace: Any,
        timeout: int | None,
        yield_time_ms: int,
        max_output_chars: int,
        owner_session_key: str | None = None,
    ) -> tuple[str, _SessionPoll]:
        self._cleanup_python()
        if len(self._python_sessions) >= self.max_sessions:
            raise RuntimeError(f"maximum exec sessions reached ({self.max_sessions})")

        session_id = uuid.uuid4().hex[:12]
        session = _PythonSession(
            session_id=session_id,
            code=code,
            function=function,
            args=args,
            kwargs=kwargs,
            namespace=namespace,
            timeout=timeout,
            owner_session_key=owner_session_key,
        )
        self._python_sessions[session_id] = session

        poll = session.poll(yield_time_ms, max_output_chars)
        if poll.done:
            self._python_sessions.pop(session_id, None)
        return session_id, poll

    def _cleanup_python(self) -> None:
        now = time.monotonic()
        stale = [
            sid for sid, s in self._python_sessions.items()
            if now - s.last_access > self.idle_timeout
        ]
        for sid in stale:
            session = self._python_sessions.pop(sid, None)
            if session is not None:
                # Signal the background thread to stop before dropping it
                # from tracking, so it doesn't leak as an orphaned,
                # unreachable-but-still-running thread.
                session.stop()

    def shutdown(self) -> None:
        """Terminate and join all running Python exec sessions."""
        sessions = list(self._python_sessions.values())
        for session in sessions:
            session.stop()
        for session in sessions:
            session.join(timeout=5)
        self._python_sessions.clear()

    async def poll_python(
        self,
        *,
        session_id: str,
        yield_time_ms: int,
        max_output_chars: int,
        terminate: bool = False,
        owner_session_key: str | None = None,
    ) -> _SessionPoll:
        self._cleanup_python()
        session = self._python_sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        if (
            owner_session_key
            and session.owner_session_key
            and session.owner_session_key != owner_session_key
        ):
            raise KeyError(session_id)

        if terminate:
            session.terminate()

        poll = session.poll(yield_time_ms, max_output_chars)
        if poll.done:
            self._python_sessions.pop(session_id, None)
        return poll

    def list_python(self, *, owner_session_key: str | None = None) -> list[ExecSessionInfo]:
        self._cleanup_python()
        now = time.monotonic()
        return [
            ExecSessionInfo(
                session_id=sid,
                command=s._description,
                cwd=s.namespace.working_dir if hasattr(s.namespace, 'working_dir') else "",
                elapsed_s=max(0.0, now - s.started_at),
                idle_s=max(0.0, now - s.last_access),
                remaining_s=max(0.0, s.deadline - now) if s.deadline != float("inf") else -1,
                returncode=s._exit_code,
                owner_session_key=s.owner_session_key,
            )
            for sid, s in sorted(self._python_sessions.items())
            if not owner_session_key
            or not s.owner_session_key
            or s.owner_session_key == owner_session_key
        ]




DEFAULT_EXEC_SESSION_MANAGER = ExecSessionManager()
atexit.register(DEFAULT_EXEC_SESSION_MANAGER.shutdown)


def format_result_line(result: Any) -> str:
    """Riga ``Result:`` per il valore di ritorno di un exec.

    Il ``repr()`` del risultato gira FUORI dalla finestra guardata: sia qui che
    in ``run_python_async`` l'oggetto arriva dopo che ``PythonNamespace.execute``
    ha già eseguito ``_exit_guard``. Un ``__repr__`` scritto dall'agente è quindi
    codice utente senza confine di workspace, e questo NON è chiuso: vale il
    commento TRUST BOUNDARY in testa a ``python_exec.py`` — leggere fuori dal
    workspace da dentro un ``__repr__`` richiede di scriverlo apposta, e chi lo
    scrive apposta ha già altre porte documentate.

    Quel che invece va chiuso, ed è il motivo per cui questa funzione esiste, è
    la ``BaseException``: senza il ``try`` qui sotto un ``__repr__`` che alza
    ``SystemExit`` scavalcherebbe il confine del sandbox che
    ``PythonNamespace.execute`` installa proprio contro quello — l'eccezione
    nasce dopo il ``finally``, atterra sul future e asyncio la rilancia fuori
    dall'event loop, cioè esattamente il crash che quel confine esiste per
    evitare.
    """
    try:
        return f"Result: {result!r}"
    except BaseException as exc:  # noqa: BLE001 - vedi il docstring
        return f"Result: <repr() raised {type(exc).__name__}: {type(result).__name__} object>"


def clamp_session_int(value: int | None, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    return min(max(value, minimum), maximum)


def format_session_poll(session_id: str, poll: _SessionPoll) -> str:
    parts = [poll.output] if poll.output else []
    if poll.truncated_chars:
        parts.append(f"(output truncated by {poll.truncated_chars:,} chars)")
    if poll.timed_out:
        parts.append("Error: Execution timed out; session was terminated.")
    if poll.terminated and not poll.timed_out:
        parts.append("Session terminated.")
    if poll.done:
        parts.append(f"Exit code: {poll.exit_code}")
    else:
        parts.append(f"Execution running. session_id: {session_id}")
    parts.append(f"Elapsed: {poll.elapsed_s:.1f}s")
    return "\n".join(parts) if parts else "(no output yet)"


class PythonExecGateMixin:
    """L'interruttore di ``python_exec``, per lui e per le sue sessioni.

    Senza la sezione in config si resta accesi (è il default storico); con la
    sezione vale ``enable``. Era copiato identico in tre classi. Qui e non in
    ``python_exec.py`` perché è quello a importare questo modulo.
    """

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        cfg = getattr(ctx.config, "python_exec", None)
        if cfg is None:
            return True
        return cfg.enable


class _ExecSessionTool(PythonExecGateMixin, Tool):
    """Base dei due tool che guardano le sessioni: stesso gestore, stesso scope."""

    _scopes = {"core", "subagent"}

    def __init__(
        self,
        *,
        manager: ExecSessionManager | None = None,
    ) -> None:
        self._manager = manager or DEFAULT_EXEC_SESSION_MANAGER

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool_parameters(
    tool_parameters_schema(
        session_id=StringSchema("Session id returned by python_exec when yield_time_ms is used."),
        terminate=BooleanSchema(
            description="Terminate the running session.",
            default=False,
        ),
        yield_time_ms=IntegerSchema(
            description="Milliseconds to wait before returning recent output (default 1000, max 30000).",
            minimum=0,
            maximum=MAX_YIELD_MS,
        ),
        wait_for=StringSchema(
            "Optional text to wait for in output before returning.",
            nullable=True,
        ),
        wait_timeout_ms=IntegerSchema(
            description="Maximum milliseconds to wait for wait_for text (default 10000, max 120000).",
            minimum=0,
            maximum=MAX_WAIT_FOR_MS,
            nullable=True,
        ),
        max_output_chars=IntegerSchema(
            description="Maximum output characters to return from this poll (default 10000, max 50000).",
            minimum=1000,
            maximum=MAX_OUTPUT_CHARS,
        ),
        required=["session_id"],
    )
)
class WriteStdinTool(_ExecSessionTool):
    """Poll, wait for output, or terminate a running Python exec session."""

    @property
    def exclusive(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "write_stdin"

    @property
    def description(self) -> str:
        return (
            "Poll, wait for output, or terminate a running Python exec session "
            "created by python_exec with yield_time_ms. Use terminate=true to "
            "stop the execution. Use wait_for with wait_timeout_ms to wait for "
            "specific output text. Do not use this to start new executions; "
            "start them with python_exec."
        )

    async def execute(
        self,
        session_id: str,
        terminate: bool = False,
        yield_time_ms: int | None = None,
        wait_for: str | None = None,
        wait_timeout_ms: int | None = None,
        max_output_chars: int | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            output_limit = clamp_session_int(
                max_output_chars,
                DEFAULT_MAX_OUTPUT_CHARS,
                1000,
                MAX_OUTPUT_CHARS,
            )
            if wait_for:
                return await self._wait_for_output(
                    session_id=session_id,
                    terminate=terminate,
                    wait_for=wait_for,
                    wait_timeout_ms=clamp_session_int(
                        wait_timeout_ms,
                        DEFAULT_WAIT_FOR_MS,
                        0,
                        MAX_WAIT_FOR_MS,
                    ),
                    max_output_chars=output_limit,
                )
            poll = await self._manager.poll_python(
                session_id=session_id,
                yield_time_ms=clamp_session_int(yield_time_ms, DEFAULT_YIELD_MS, 0, MAX_YIELD_MS),
                max_output_chars=output_limit,
                terminate=terminate,
                owner_session_key=current_request_session_key(),
            )
            return format_session_poll(session_id, poll)
        except KeyError:
            return f"Error: exec session not found: {session_id}"
        except Exception as exc:
            return f"Error polling exec session: {exc}"

    async def _wait_for_output(
        self,
        *,
        session_id: str,
        terminate: bool,
        wait_for: str,
        wait_timeout_ms: int,
        max_output_chars: int,
    ) -> str:
        deadline = time.monotonic() + (wait_timeout_ms / 1000)
        aggregate: list[str] = []
        poll: _SessionPoll | None = None

        while True:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            step_ms = min(500, remaining_ms)
            poll = await self._manager.poll_python(
                session_id=session_id,
                yield_time_ms=step_ms,
                max_output_chars=max_output_chars,
                terminate=terminate,
                owner_session_key=current_request_session_key(),
            )
            if poll.output:
                aggregate.append(poll.output)
                joined = "".join(aggregate)
                if wait_for in joined:
                    poll.output = joined
                    return format_session_poll(session_id, poll)
            if poll.done or remaining_ms <= 0:
                poll.output = "".join(aggregate)
                result = format_session_poll(session_id, poll)
                if wait_for not in poll.output:
                    result += f"\nWait target not observed: {wait_for!r}"
                return result


@tool_parameters(tool_parameters_schema())
class ListExecSessionsTool(_ExecSessionTool):
    """List active exec sessions."""

    @property
    def name(self) -> str:
        return "list_exec_sessions"

    @property
    def description(self) -> str:
        return (
            "List active long-running Python exec sessions, including session_id, "
            "elapsed time, idle time, remaining timeout, and description. "
            "Use this to recover a session_id after context shifts before "
            "polling or terminating with write_stdin."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        try:
            sessions = self._manager.list_python(
                owner_session_key=current_request_session_key(),
            )
            if not sessions:
                return "No active exec sessions."
            lines = []
            for info in sessions:
                command = " ".join(info.command.split())
                if len(command) > 120:
                    command = command[:119] + "..."
                status = "exited" if info.returncode is not None else "running"
                lines.append(
                    f"{info.session_id} | {status} | elapsed={info.elapsed_s:.1f}s "
                    f"| idle={info.idle_s:.1f}s | remaining={info.remaining_s:.1f}s "
                    f"| {command}"
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"Error listing exec sessions: {exc}"


# Registrazione esplicita dei tool di questo modulo (Fase 5.3): il
# ToolLoader legge questa lista invece della reflection dir(). Un nuovo
# tool va aggiunto qui esplicitamente.
TOOLS = [ListExecSessionsTool, WriteStdinTool]
