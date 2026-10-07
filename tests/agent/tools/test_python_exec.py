"""Tests for python_exec tool defaults."""

import asyncio
from unittest.mock import Mock, patch

import pytest

from jenny.agent.tools.python_exec import (
    PythonExecInterrupted,
    PythonExecTool,
    PythonNamespace,
    _register_builtin_functions,
    run_python_async,
)
from jenny.config.paths import get_workspace_path
from jenny.config.tool_schemas import PythonExecConfig


def test_python_namespace_default_working_dir():
    ns = PythonNamespace()
    assert ns.working_dir == str(get_workspace_path())


def test_python_namespace_explicit_working_dir():
    ns = PythonNamespace(working_dir="/tmp/custom")
    assert ns.working_dir == "/tmp/custom"


def test_python_exec_tool_default_working_dir():
    tool = PythonExecTool()
    assert tool.working_dir == str(get_workspace_path())
    assert tool.namespace.working_dir == str(get_workspace_path())


def test_python_exec_tool_explicit_working_dir():
    tool = PythonExecTool(working_dir="/tmp/other")
    assert tool.working_dir == "/tmp/other"
    assert tool.namespace.working_dir == "/tmp/other"


# ---------------------------------------------------------------------------
# http_get — SSRF redirect-bounce protection
# ---------------------------------------------------------------------------


def _make_namespace() -> PythonNamespace:
    ns = PythonNamespace()
    _register_builtin_functions(ns, workspace=str(get_workspace_path()), restrict_to_workspace=False)
    return ns


def _fake_validate(allowed_ok: bool = True, blocked_substrings: tuple[str, ...] = ("127.0.0.1",)):
    def _validate(url: str, **kwargs):
        if any(sub in url for sub in blocked_substrings):
            return False, f"Blocked: {url} resolves to private/internal address"
        return (allowed_ok, "")

    return _validate


class TestHttpGet:
    def test_allowed_host_returns_body_normally(self):
        """A request to a non-blocked host still succeeds and returns the body."""
        ns = _make_namespace()
        fake_resp = Mock()
        fake_resp.has_redirect_location = False
        fake_resp.text = "hello world"
        fake_resp.raise_for_status = Mock()

        with patch("jenny.security.network.validate_url_target", side_effect=_fake_validate()):
            with patch("httpx.get", return_value=fake_resp) as mock_get:
                _, _, result = ns.call_function("http_get", args=["https://example.com/data"])

        assert result == "hello world"
        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        assert kwargs["follow_redirects"] is False

    def test_blocks_ssrf_on_initial_url(self):
        """The initial URL is still validated and blocked as before."""
        ns = _make_namespace()
        with patch("jenny.security.network.validate_url_target", side_effect=_fake_validate()):
            _, _, result = ns.call_function("http_get", args=["http://127.0.0.1:8080/admin"])
        assert result.startswith("Error: SSRF blocked")

    def test_does_not_follow_redirect_into_blocked_address(self):
        """A remote server that 3xx-redirects into a blocked address (the
        SSRF-bounce bug) must not have that redirect silently followed.
        Previously httpx's own follow_redirects=True would resolve/connect to
        the Location target with no re-validation at all."""
        ns = _make_namespace()
        fake_resp = Mock()
        fake_resp.has_redirect_location = True
        fake_resp.status_code = 302
        fake_resp.headers = {"location": "http://127.0.0.1:8765/internal-admin"}

        with patch("jenny.security.network.validate_url_target", side_effect=_fake_validate()):
            with patch("httpx.get", return_value=fake_resp) as mock_get:
                _, _, result = ns.call_function("http_get", args=["https://example.com/redirector"])

        # Only the original URL was ever fetched — the redirect target was
        # never requested.
        mock_get.assert_called_once()
        assert "redirect" in result.lower()
        assert "127.0.0.1:8765" in result

    def test_non_redirect_response_still_raises_for_http_errors(self):
        """raise_for_status behavior for real 4xx/5xx errors is unchanged."""
        import httpx

        ns = _make_namespace()
        fake_resp = Mock()
        fake_resp.has_redirect_location = False
        fake_resp.raise_for_status = Mock(side_effect=httpx.HTTPStatusError(
            "500 error", request=Mock(), response=Mock(status_code=500)
        ))

        with patch("jenny.security.network.validate_url_target", side_effect=_fake_validate()):
            with patch("httpx.get", return_value=fake_resp):
                stdout, stderr, result = ns.call_function(
                    "http_get", args=["https://example.com/broken"]
                )

        assert result is None
        assert "500 error" in stderr


# ---------------------------------------------------------------------------
# get_env / list_env — must share the same allowlist, no secret leakage
# ---------------------------------------------------------------------------


class TestGetEnvListEnv:
    def test_get_env_returns_allowed_keys(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        ns = _make_namespace()
        _, _, result = ns.call_function("get_env", args=["PATH"])
        assert result == "/usr/bin"

    def test_get_env_blocks_non_allowlisted_key(self, monkeypatch):
        monkeypatch.setenv("SOME_SECRET_VAR", "top-secret-value")
        ns = _make_namespace()
        _, _, result = ns.call_function("get_env", args=["SOME_SECRET_VAR"])
        assert result is None
        assert result != "top-secret-value"

    def test_list_env_unchanged_behavior(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.setenv("PYTHONPATH", "/opt/x")
        monkeypatch.setenv("SOME_SECRET_VAR", "top-secret-value")
        ns = _make_namespace()
        _, _, result = ns.call_function("list_env")
        assert result == {
            "PATH": "/usr/bin",
            "LANG": "en_US.UTF-8",
            "PYTHONPATH": "/opt/x",
        }
        assert "SOME_SECRET_VAR" not in result


# ---------------------------------------------------------------------------
# Intervento 1 — workspace containment for open()/io.open/pathlib + no raw httpx
# ---------------------------------------------------------------------------


def _restricted_namespace(workspace: str) -> PythonNamespace:
    """A namespace configured exactly like production under restriction:
    default allow/block module lists + restrict_to_workspace=True + the
    registered builtin helpers."""
    cfg = PythonExecConfig()
    ns = PythonNamespace(
        working_dir=workspace,
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=True,
        workspace=workspace,
    )
    _register_builtin_functions(ns, workspace=workspace, restrict_to_workspace=True)
    return ns


class TestWorkspaceOpenContainment:
    def test_builtin_open_outside_workspace_is_blocked(self, tmp_path):
        outside = tmp_path.parent / "escape_open.txt"
        ns = _restricted_namespace(str(tmp_path))
        _, stderr, _ = ns.execute(f"open({str(outside)!r}, 'w').write('x')")
        assert "outside allowed directory" in stderr
        assert not outside.exists()

    def test_builtin_open_inside_workspace_works(self, tmp_path):
        inside = tmp_path / "inside.txt"
        ns = _restricted_namespace(str(tmp_path))
        _, stderr, _ = ns.execute(
            f"f = open({str(inside)!r}, 'w'); f.write('hello'); f.close()"
        )
        assert stderr == ""
        assert inside.read_text() == "hello"

    def test_builtin_open_accepts_a_file_descriptor(self, tmp_path):
        """Un fd passa invariato: non è (e non può essere) un confine.

        Il rifiuto che stava qui prima leggeva come un controllo di sicurezza e
        non lo era: `os.read`/`os.dup`/`os.pipe` non prendono percorsi, non sono
        wrappati e rileggono comunque qualunque descrittore. Costava un oggetto
        file al codice legittimo — `os.fdopen(os.open('data.txt'))`, con
        entrambi i capi DENTRO il workspace, falliva — e a un attaccante una
        riga. Quello che NON si può fare è togliere il ramo e basta: `str(5)`
        aprirebbe un file di nome "5", ed è ciò che questo test verifica.
        """
        (tmp_path / "data.txt").write_text("payload")
        ns = _restricted_namespace(str(tmp_path))
        stdout, stderr, _ = ns.execute(
            "import os\n"
            f"fd = os.open({str(tmp_path / 'data.txt')!r}, os.O_RDONLY)\n"
            "print(os.fdopen(fd).read())\n"
        )
        assert stderr == ""
        assert stdout.strip() == "payload"
        assert not (tmp_path / "5").exists()

    def test_io_open_outside_workspace_is_blocked(self, tmp_path):
        outside = tmp_path.parent / "escape_io.txt"
        ns = _restricted_namespace(str(tmp_path))
        _, stderr, _ = ns.execute(
            f"import io; io.open({str(outside)!r}, 'w').write('x')"
        )
        assert "outside allowed directory" in stderr
        assert not outside.exists()

    def test_pathlib_write_text_outside_workspace_is_blocked(self, tmp_path):
        """OPTION A: pathlib routes through io.open on this Python build, so
        Path.write_text is contained by the io.open patch. This test fixes
        that expected behavior; if a future runtime changes the pathlib I/O
        dispatch, this test is the tripwire."""
        outside = tmp_path.parent / "escape_pathlib.txt"
        ns = _restricted_namespace(str(tmp_path))
        _, stderr, _ = ns.execute(
            f"import pathlib; pathlib.Path({str(outside)!r}).write_text('x')"
        )
        assert "outside allowed directory" in stderr
        assert not outside.exists()

    def test_pathlib_write_text_inside_workspace_works(self, tmp_path):
        inside = tmp_path / "inside_pathlib.txt"
        ns = _restricted_namespace(str(tmp_path))
        _, stderr, _ = ns.execute(
            f"import pathlib; pathlib.Path({str(inside)!r}).write_text('ok')"
        )
        assert stderr == ""
        assert inside.read_text() == "ok"

    def test_unrestricted_open_is_unchanged(self, tmp_path):
        """With restrict_to_workspace=False the raw builtin open is used, so a
        path outside the workspace is writable (behavior unchanged)."""
        outside = tmp_path.parent / "unrestricted_open.txt"
        ns = PythonNamespace(
            working_dir=str(tmp_path),
            restrict_to_workspace=False,
            workspace=str(tmp_path),
        )
        _, stderr, _ = ns.execute(
            f"f = open({str(outside)!r}, 'w'); f.write('x'); f.close()"
        )
        assert stderr == ""
        assert outside.read_text() == "x"
        outside.unlink()


class TestHttpxNotAllowlisted:
    def test_import_httpx_blocked_by_default_allowlist(self, tmp_path):
        ns = _restricted_namespace(str(tmp_path))
        _, stderr, _ = ns.execute("import httpx")
        assert "httpx" in stderr
        assert "not in the allowed modules" in stderr

    def test_httpx_absent_from_default_config(self):
        assert "httpx" not in PythonExecConfig().allowed_modules

    def test_http_get_to_metadata_ip_is_blocked(self, tmp_path):
        """Non-regression: the SSRF guard still blocks the cloud metadata IP
        via the http_get helper (no httpx import needed by guarded code)."""
        ns = _restricted_namespace(str(tmp_path))
        _, _, result = ns.call_function(
            "http_get", args=["http://169.254.169.254/latest/meta-data/"]
        )
        assert result.startswith("Error: SSRF blocked")


class TestUrllibNotAllowlisted:
    """urllib is a raw URL client: `urlopen` bypasses validate_url_target
    (SSRF via http://, LFI via file://). It must not be in the default
    allowlist, same rationale as httpx."""

    def test_import_urllib_blocked_by_default_allowlist(self, tmp_path):
        ns = _restricted_namespace(str(tmp_path))
        _, stderr, _ = ns.execute("from urllib.request import urlopen")
        assert "urllib" in stderr
        assert "not in the allowed modules" in stderr

    def test_urllib_absent_from_default_config(self):
        assert "urllib" not in PythonExecConfig().allowed_modules


class TestWorkspaceExecutable:
    """Il workspace è eseguibile: un modulo che si risolve in un file dentro
    il workspace è importabile anche se non è nell'allowlist esplicita — è
    codice fidato quanto ciò che l'agente scriverebbe inline. Ma i moduli
    bloccati restano bloccati, e ciò che è fuori dal workspace resta soggetto
    all'allowlist."""

    def test_workspace_module_importable_outside_allowlist(self, tmp_path, monkeypatch):
        scripts = tmp_path / "skills" / "demo" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "mytool.py").write_text("VALUE = 42\n")
        monkeypatch.syspath_prepend(str(scripts))
        ns = _restricted_namespace(str(tmp_path))
        assert "mytool" not in ns.allowed_modules
        stdout, stderr, _ = ns.execute("import mytool; print(mytool.VALUE)")
        assert stderr == ""
        assert stdout.strip() == "42"

    def test_blocked_module_stays_blocked_even_if_shadowed(self, tmp_path, monkeypatch):
        """Un file `subprocess.py` nel workspace NON deve poter sbloccare il
        nome bloccato — la precedenza del blocklist viene prima."""
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "subprocess.py").write_text("HACKED = True\n")
        monkeypatch.syspath_prepend(str(scripts))
        ns = _restricted_namespace(str(tmp_path))
        assert "subprocess" in ns.blocked_modules
        _, stderr, _ = ns.execute("import subprocess")
        assert "blocked in python_exec" in stderr

    def test_non_workspace_module_still_needs_allowlist(self, tmp_path):
        """Un modulo fuori allowlist e non nel workspace resta negato."""
        ns = _restricted_namespace(str(tmp_path))
        assert "urllib" not in ns.allowed_modules
        _, stderr, _ = ns.execute("import urllib.request")
        assert "not in the allowed modules" in stderr


# ---------------------------------------------------------------------------
# B1 — il sandbox non deve lasciar sfuggire BaseException
# ---------------------------------------------------------------------------


class TestBaseExceptionContainment:
    """``SystemExit`` & co. sollevate dal codice dell'agente devono diventare
    un normale errore di tool.

    Non è pedanteria: ``asyncio.Task.__step`` ri-alza KeyboardInterrupt e
    SystemExit *fuori* dall'event loop, quindi nessun ``except`` a valle le
    vede — l'unico punto in cui il fix funziona è questo confine.
    """

    async def _run_code(self, tmp_path, code: str) -> str:
        ns = _restricted_namespace(str(tmp_path))
        return await run_python_async(
            code=code,
            function=None,
            args=None,
            kwargs=None,
            namespace=ns,
            timeout=10,
            max_output_chars=4000,
        )

    async def test_raise_system_exit_is_reported_not_fatal(self, tmp_path):
        result = await self._run_code(tmp_path, "raise SystemExit(2)")
        assert "SystemExit" in result
        # L'event loop deve essere ancora vivo e utilizzabile.
        await asyncio.sleep(0)
        assert asyncio.get_running_loop().is_running()

    async def test_bare_system_exit_at_module_level_is_reported(self, tmp_path):
        """Il caso osservato sul device: `raise SystemExit` come "fermati qui"."""
        result = await self._run_code(tmp_path, "print('before')\nraise SystemExit")
        assert "SystemExit" in result
        assert "before" in result

    async def test_sys_exit_is_reported_not_fatal(self, tmp_path):
        result = await self._run_code(tmp_path, "import sys\nsys.exit(3)")
        assert "SystemExit" in result
        await asyncio.sleep(0)
        assert asyncio.get_running_loop().is_running()

    async def test_keyboard_interrupt_is_reported_not_fatal(self, tmp_path):
        result = await self._run_code(tmp_path, "raise KeyboardInterrupt")
        assert "KeyboardInterrupt" in result
        await asyncio.sleep(0)
        assert asyncio.get_running_loop().is_running()

    async def test_generator_exit_is_reported_not_fatal(self, tmp_path):
        result = await self._run_code(tmp_path, "raise GeneratorExit")
        assert "GeneratorExit" in result

    async def test_plain_base_exception_is_reported_not_fatal(self, tmp_path):
        result = await self._run_code(tmp_path, "raise BaseException('kaboom')")
        assert "BaseException" in result and "kaboom" in result

    async def test_exit_and_quit_are_not_in_the_sandbox_builtins(self, tmp_path):
        """`exit()`/`quit()` non esistono nel namespace (blocked in
        ``_safe_builtins``): restano un NameError, non una SystemExit — e
        comunque non uccidono il gateway."""
        for snippet in ("exit()", "quit()"):
            result = await self._run_code(tmp_path, snippet)
            assert "NameError" in result
        await asyncio.sleep(0)
        assert asyncio.get_running_loop().is_running()

    async def test_gateway_survives_a_second_call_after_system_exit(self, tmp_path):
        """Dopo la SystemExit il tool deve restare pienamente usabile."""
        await self._run_code(tmp_path, "raise SystemExit")
        result = await self._run_code(tmp_path, "print(1 + 1)")
        assert "2" in result

    def test_call_function_contains_base_exception(self, tmp_path):
        ns = _restricted_namespace(str(tmp_path))

        def _boom():
            raise SystemExit(7)

        ns.register_function("_boom", _boom)
        _, stderr, _ = ns.call_function("_boom")
        assert "SystemExit" in stderr

    def test_python_exec_interrupted_still_propagates_from_execute(self, tmp_path):
        """Carve-out obbligatorio: ``_run`` conta su questa eccezione per
        distinguere timeout/stop da un errore del codice utente."""
        ns = _restricted_namespace(str(tmp_path))

        def _interrupt():
            raise PythonExecInterrupted()

        ns.register_function("_interrupt", _interrupt)
        with pytest.raises(PythonExecInterrupted):
            ns.execute("_interrupt()")
        with pytest.raises(PythonExecInterrupted):
            ns.call_function("_interrupt")

    def test_cancelled_error_still_propagates_from_execute(self, tmp_path):
        """Secondo carve-out: /stop e l'abbandono del turno ci si appoggiano."""
        ns = _restricted_namespace(str(tmp_path))

        def _cancel():
            raise asyncio.CancelledError()

        ns.register_function("_cancel", _cancel)
        with pytest.raises(asyncio.CancelledError):
            ns.execute("_cancel()")
        with pytest.raises(asyncio.CancelledError):
            ns.call_function("_cancel")


# ---------------------------------------------------------------------------
# B12 — `class X: ...` deve funzionare dentro python_exec
# ---------------------------------------------------------------------------


class TestClassStatementSupport:
    """``_safe_builtins`` scartava ogni dunder, `__build_class__` compreso:
    l'opcode LOAD_BUILD_CLASS lo cerca nei builtins, quindi OGNI istruzione
    ``class`` moriva con "NameError: __build_class__ not found" — un dettaglio
    di implementazione su cui il modello non può correggersi."""

    async def _run(self, tmp_path, code: str) -> str:
        ns = _restricted_namespace(str(tmp_path))
        return await run_python_async(
            code=code,
            function=None,
            args=None,
            kwargs=None,
            namespace=ns,
            timeout=15,
            max_output_chars=8000,
        )

    async def test_plain_class_statement_works(self, tmp_path):
        result = await self._run(tmp_path, "class A:\n    x = 1\nprint(A().x)")
        assert "1" in result
        assert "__build_class__" not in result

    async def test_class_with_base_works(self, tmp_path):
        result = await self._run(tmp_path, "class B(dict):\n    pass\nprint(B(a=1))")
        assert "{'a': 1}" in result

    async def test_super_init_works(self, tmp_path):
        """`super()` a zero argomenti dipende dalla cella `__class__` creata
        dal compilatore dentro il corpo della classe."""
        code = (
            "class C:\n"
            "    def __init__(self):\n"
            "        self.v = 1\n"
            "class D(C):\n"
            "    def __init__(self):\n"
            "        super().__init__()\n"
            "        self.w = 2\n"
            "d = D()\n"
            "print(d.v, d.w)"
        )
        result = await self._run(tmp_path, code)
        assert "1 2" in result

    async def test_isinstance_against_sandbox_class(self, tmp_path):
        code = "class E:\n    pass\ne = E()\nprint(isinstance(e, E), type(e).__name__)"
        result = await self._run(tmp_path, code)
        assert "True E" in result

    async def test_exception_subclass_can_be_raised_and_caught(self, tmp_path):
        code = (
            "class MyErr(Exception):\n"
            "    pass\n"
            "try:\n"
            "    raise MyErr('boom')\n"
            "except MyErr as exc:\n"
            "    print('caught', exc)"
        )
        result = await self._run(tmp_path, code)
        assert "caught boom" in result

    async def test_metaclass_and_init_subclass_hooks_run(self, tmp_path):
        code = (
            "class M(type):\n"
            "    pass\n"
            "class H(metaclass=M):\n"
            "    def __init_subclass__(cls, **kw):\n"
            "        print('hook', cls.__name__)\n"
            "class I2(H):\n"
            "    pass\n"
            "print(type(H).__name__)"
        )
        result = await self._run(tmp_path, code)
        assert "hook I2" in result
        assert "M" in result

    async def test_dataclass_works(self, tmp_path):
        """`dataclasses` è nell'allowlist di default: `@dataclass` è la classe
        più probabile che un modello scriva."""
        code = (
            "from dataclasses import dataclass, field\n"
            "@dataclass\n"
            "class P:\n"
            "    x: int = 0\n"
            "    y: list = field(default_factory=list)\n"
            "print(P(1))"
        )
        result = await self._run(tmp_path, code)
        assert "P(x=1, y=[])" in result

    async def test_dataclass_classvar_is_not_a_field(self, tmp_path):
        """Regressione della compilazione: con le annotazioni stringificate
        (PEP 563 ereditato dall'host) `dataclasses` sbagliava a classificare
        `ClassVar` — o esplodeva prima ancora di arrivarci."""
        code = (
            "import dataclasses\n"
            "from dataclasses import dataclass\n"
            "from typing import ClassVar\n"
            "@dataclass\n"
            "class R:\n"
            "    k: ClassVar[int] = 5\n"
            "    x: int = 1\n"
            "print([f.name for f in dataclasses.fields(R)], R.k)"
        )
        result = await self._run(tmp_path, code)
        assert "['x'] 5" in result

    async def test_enum_subclass_works(self, tmp_path):
        code = "from enum import Enum\nclass Col(Enum):\n    R = 1\nprint(Col.R, Col.R.value)"
        result = await self._run(tmp_path, code)
        assert "Col.R 1" in result

    async def test_typing_namedtuple_subclass_works(self, tmp_path):
        code = "from typing import NamedTuple\nclass Q(NamedTuple):\n    a: int\nprint(Q(1))"
        result = await self._run(tmp_path, code)
        assert "Q(a=1)" in result

    async def test_class_survives_across_calls_in_the_same_namespace(self, tmp_path):
        """Il namespace persiste tra chiamate: una classe definita in una
        `python_exec` deve restare usabile nella successiva."""
        ns = _restricted_namespace(str(tmp_path))

        async def _call(code: str) -> str:
            return await run_python_async(
                code=code,
                function=None,
                args=None,
                kwargs=None,
                namespace=ns,
                timeout=15,
                max_output_chars=8000,
            )

        assert "defined" in await _call(
            "class Persisted:\n    def hi(self):\n        return 'hello'\nprint('defined')"
        )
        second = await _call("p = Persisted()\nprint(p.hi(), isinstance(p, Persisted))")
        assert "hello True" in second


class TestGuardedBuiltinsSurface:
    """I soli dunder esposti sono `__build_class__` (allowlist esplicita) e
    `__import__` (reinstallato come `_guarded_import`). Tutto il resto resta
    fuori, e questo test lo inchioda."""

    def test_only_build_class_and_import_are_exposed_as_dunders(self):
        ns = PythonNamespace()
        dunders = {k for k in ns._ns["__builtins__"] if k.startswith("_")}
        assert dunders == {"__build_class__", "__import__"}

    def test_import_dunder_is_still_the_guarded_one(self):
        """Il loop non deve poter reintrodurre il `__import__` reale."""
        import builtins as _real_builtins

        ns = PythonNamespace()
        installed = ns._ns["__builtins__"]["__import__"]
        assert installed is not _real_builtins.__import__
        assert installed.__func__ is PythonNamespace._guarded_import

    def test_build_class_grants_nothing_type_did_not(self, tmp_path):
        """`type(name, bases, ns)` costruiva già classi e metaclassi senza
        `__build_class__`: il confine non si sposta."""
        ns = _restricted_namespace(str(tmp_path))
        _out, err, _res = ns.execute(
            "X = type('X', (dict,), {'hi': lambda self: 'hi'})\nprint(X(a=1).hi())"
        )
        assert err == ""

    def test_blocked_builtins_stay_blocked(self):
        ns = PythonNamespace()
        safe = ns._ns["__builtins__"]
        for name in ("compile", "breakpoint", "exit", "quit"):
            assert name not in safe


class TestGuardedCodeDoesNotInheritHostFutureFlags:
    """`python_exec.py` apre con `from __future__ import annotations` e
    `eval`/`exec` ereditano i flag `__future__` del chiamante: il codice
    dell'agente veniva compilato in PEP 563 senza saperlo."""

    async def _run(self, tmp_path, code: str) -> str:
        ns = _restricted_namespace(str(tmp_path))
        return await run_python_async(
            code=code,
            function=None,
            args=None,
            kwargs=None,
            namespace=ns,
            timeout=15,
            max_output_chars=8000,
        )

    async def test_annotations_are_not_stringified_by_default(self, tmp_path):
        code = "class T:\n    x: int = 0\nprint(T.__annotations__)"
        result = await self._run(tmp_path, code)
        assert "<class 'int'>" in result

    async def test_guarded_code_can_opt_into_pep563(self, tmp_path):
        """`from __future__ import annotations` è una direttiva del compilatore
        ma emette comunque un import a runtime: `_guarded_import` deve
        lasciarlo passare, altrimenti l'opt-in è inutilizzabile."""
        code = (
            "from __future__ import annotations\n"
            "class N:\n"
            "    nxt: N = None\n"
            "print(N.__annotations__)"
        )
        result = await self._run(tmp_path, code)
        assert "{'nxt': 'N'}" in result

    async def test_expression_result_still_returned(self, tmp_path):
        """La compilazione esplicita non deve rompere il ramo `eval`."""
        result = await self._run(tmp_path, "1 + 1")
        assert "2" in result

    async def test_syntax_error_still_reported(self, tmp_path):
        result = await self._run(tmp_path, "def (:")
        assert "SyntaxError" in result

    async def test_a_runtime_error_is_not_dressed_as_a_syntax_error(self, tmp_path):
        """Sul Titan, 07/10/2026: un HTTP 429 arrivava al modello dopo un
        «SyntaxError: invalid syntax» e un «During handling…», perche' l'exec
        delle istruzioni girava dentro l'``except`` del tentativo con eval."""
        code = "def fetch():\n    raise RuntimeError('HTTP 429')\nfetch()"
        result = await self._run(tmp_path, code)
        assert "RuntimeError: HTTP 429" in result
        assert "SyntaxError" not in result
        assert "During handling" not in result

    async def test_a_syntax_error_points_at_its_own_line(self, tmp_path):
        result = await self._run(tmp_path, "x = 1\ny = (\n")
        assert "SyntaxError" in result and "During handling" not in result

    async def test_an_expression_runs_once(self, tmp_path):
        code = "calls = []\ncalls.append(1)\nprint(len(calls))"
        assert "1" in await self._run(tmp_path, code)
        ns = _restricted_namespace(str(tmp_path))
        out = await run_python_async(code="print('side') or 7", function=None, args=None,
                                     kwargs=None, namespace=ns, timeout=15,
                                     max_output_chars=8000)
        assert out.count("side") == 1 and "7" in out
