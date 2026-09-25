"""Sola lettura: cosa si chiude, e soprattutto cosa **non** si chiude.

Passo **4.2** di ``roadmap/progetti-passi.md``.

I cancelli sono otto, non tre. Tre sono quelli che il confine di scrittura del
passo 1.3 aveva già sdoppiato — i tool file, i builtin di ``python_exec``,
l'``open()`` grezza — e cinque sono nuovi: i mutatori di ``os``, ``os.open`` con
i soli flag di scrittura, ``symlink``, ``rmtree``, ``wiki_scaffold``. Più i tool
che si portano la destinazione da sé, che stanno in
``tests/security/test_readonly_write_surfaces.py``.

**La metà che conta di questo file è la seconda.** Chiudere si sbaglia in due
versi, e il verso pericoloso non è quello che sembra: una scrittura che passa è
un guasto, ma una *lettura* che si chiude rende l'interruttore inutilizzabile —
e un interruttore che nessuno accende non protegge niente. Le sonde
(``stat``, ``access``), gli enumeratori (``listdir``, ``walk``), ``read_file`` e
``open()`` in lettura devono continuare a funzionare identici.
"""

from __future__ import annotations

import dataclasses
import threading
from pathlib import Path
from typing import Any

import pytest

from jenny.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
from jenny.agent.tools.python_exec import PythonExecTool, PythonNamespace
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.config.tool_schemas import PythonExecConfig
from jenny.security.workspace_access import (
    build_workspace_scope,
    enter_workspace_scope,
)
from jenny.security.workspace_policy import ReadOnlyTurnError

_REFUSED = "read-only"


class _Recorder:
    def __init__(self) -> None:
        self.functions: dict[str, Any] = {}

    def register_function(self, name: str, func: Any) -> None:
        self.functions[name] = func


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """Un workspace con un file da leggere e uno da modificare."""
    (tmp_path / "leggibile.txt").write_text("ciao\n", encoding="utf-8")
    (tmp_path / "modificabile.txt").write_text("prima\n", encoding="utf-8")
    (tmp_path / "cartella").mkdir()
    return tmp_path


@pytest.fixture
def readonly(ws: Path):
    with enter_workspace_scope(build_workspace_scope(ws, "restricted").without_write_access()):
        yield ws


@pytest.fixture
def writable(ws: Path):
    with enter_workspace_scope(build_workspace_scope(ws, "restricted")):
        yield ws


def _builtins(ws: Path) -> dict[str, Any]:
    rec = _Recorder()
    _register_builtin_functions(rec, workspace=str(ws), restrict_to_workspace=True)  # type: ignore[arg-type]
    return rec.functions


def _namespace(ws: Path) -> PythonNamespace:
    cfg = PythonExecConfig()
    return PythonNamespace(
        working_dir=str(ws),
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=True,
        workspace=str(ws),
    )


# ── Cancello 1: i tool file ──────────────────────────────────────────────


async def test_write_file_is_refused(readonly: Path) -> None:
    result = await WriteFileTool(str(readonly)).execute(path="nuovo.txt", content="x")
    assert _REFUSED in result
    assert not (readonly / "nuovo.txt").exists(), "rifiutato ma scritto è il caso peggiore"


async def test_edit_file_is_refused_and_leaves_the_file_alone(readonly: Path) -> None:
    result = await EditFileTool(str(readonly)).execute(
        path="modificabile.txt", old_text="prima", new_text="dopo"
    )
    assert _REFUSED in result
    assert (readonly / "modificabile.txt").read_text(encoding="utf-8") == "prima\n"


async def test_read_file_still_works(readonly: Path) -> None:
    """La metà che conta: leggere è l'unica cosa che questa modalità permette."""
    result = await ReadFileTool(str(readonly)).execute(path="leggibile.txt")
    assert "ciao" in result


async def test_with_writing_on_the_same_call_lands(writable: Path) -> None:
    """Il cancello deve essere il *flag*, non un effetto collaterale del fixture."""
    await WriteFileTool(str(writable)).execute(path="nuovo.txt", content="x")
    assert (writable / "nuovo.txt").read_text(encoding="utf-8") == "x"


# ── Cancello 2: i builtin di python_exec ─────────────────────────────────


@pytest.mark.parametrize(
    ("fn", "args"),
    [
        ("write_file", ("b.txt", "x")),
        ("append_file", ("b.txt", "x")),
        ("write_json", ("b.json", {"a": 1})),
    ],
)
def test_the_builtin_writers_are_refused(readonly: Path, fn: str, args: tuple) -> None:
    env = _builtins(readonly)
    with pytest.raises(ReadOnlyTurnError):
        env[fn](*args)
    assert not (readonly / args[0]).exists()


def test_the_builtin_readers_are_untouched(readonly: Path) -> None:
    env = _builtins(readonly)
    assert "ciao" in env["read_file"]("leggibile.txt")


def test_wiki_lint_is_not_collateral_damage(readonly: Path) -> None:
    """``wiki_lint`` e ``wiki_scaffold`` condividono un helper: uno legge, l'altro crea.

    Chiudere l'helper condiviso avrebbe spento due letture per fermare una
    scrittura, ed è la ragione per cui il rifiuto sta in ``_write_path`` e in
    ``wiki_scaffold``, non in ``_enforce_path``.
    """
    env = _builtins(readonly)
    with pytest.raises(ReadOnlyTurnError):
        env["wiki_scaffold"](str(readonly / "nuova"), "Titolo")
    # `wiki_lint` non deve fallire *per la sola lettura*: qualunque altro esito
    # (anche un errore sulla wiki inesistente) va bene.
    try:
        env["wiki_lint"](str(readonly))
    except ReadOnlyTurnError:  # pragma: no cover - è il difetto che cerchiamo
        pytest.fail("wiki_lint è una lettura e non deve cadere sulla sola lettura")
    except Exception:
        pass


# ── Cancello 3: open() grezza e i patch di io/os ─────────────────────────


def test_raw_open_for_writing_is_refused(readonly: Path) -> None:
    ns = _namespace(readonly)
    with pytest.raises(ReadOnlyTurnError):
        ns._resolve_workspace_write(str(readonly / "c.txt"), for_write=True)


def test_raw_open_for_reading_is_not(readonly: Path) -> None:
    ns = _namespace(readonly)
    assert ns._resolve_workspace_write(str(readonly / "leggibile.txt")) is not None


# ── I mutatori di os, e le sonde che restano ─────────────────────────────
#
# Da qui in giù si passa da ``ns.execute``, cioè dal codice vero: i patch di
# ``os`` si montano dentro la finestra guardata, e provarli chiamando ``os``
# direttamente dal test proverebbe l'``os`` del gateway, non quello del sandbox.


def _run(ws: Path, code: str) -> tuple[str, str]:
    stdout, stderr, _ = _namespace(ws).execute(code)
    return stdout, stderr


@pytest.mark.parametrize(
    "code",
    [
        "import os; os.mkdir('nuova')",
        "import os; os.remove('leggibile.txt')",
        "import os; os.rename('leggibile.txt', 'altro.txt')",
        "import os; os.chmod('leggibile.txt', 0o600)",
        "import os; os.utime('leggibile.txt', None)",
        "import shutil; shutil.rmtree('cartella')",
        "import os; os.symlink('leggibile.txt', 'link.txt')",
        "import os; os.open('nuovo.txt', os.O_WRONLY | os.O_CREAT)",
        "open('nuovo.txt', 'w').write('x')",
        "from pathlib import Path; Path('nuovo.txt').write_text('x')",
    ],
    ids=["mkdir", "remove", "rename", "chmod", "utime", "rmtree", "symlink",
         "os.open-w", "open-w", "pathlib-write"],
)
def test_every_mutating_route_is_refused(readonly: Path, code: str) -> None:
    """Dieci strade diverse per la stessa cosa, e nessuna deve arrivare."""
    stdout, stderr = _run(readonly, code)
    # Sul nome dell'eccezione e non sul testo: un traceback che contenesse
    # "read-only" per un'altra ragione farebbe passare questo test a vuoto.
    assert "ReadOnlyTurnError" in stdout + stderr, f"passata: {code!r}"
    assert not (readonly / "nuova").exists()
    assert not (readonly / "nuovo.txt").exists()
    assert not (readonly / "link.txt").exists()
    assert not (readonly / "altro.txt").exists()
    assert (readonly / "leggibile.txt").exists()
    assert (readonly / "cartella").is_dir()


@pytest.mark.parametrize(
    "code",
    [
        "import os; print(os.listdir('.'))",
        "import os; print(os.stat('leggibile.txt').st_size)",
        "import os; print(os.access('leggibile.txt', os.R_OK))",
        "import os; print(os.path.exists('leggibile.txt'))",
        "import os; print(sum(1 for _ in os.walk('.')))",
        "print(open('leggibile.txt').read())",
        "from pathlib import Path; print(Path('leggibile.txt').read_text())",
        # `O_RDONLY` è 0: senza la maschera sui flag si chiuderebbe anche questa.
        "import os; fd = os.open('leggibile.txt', os.O_RDONLY); print(os.read(fd, 4)); os.close(fd)",
    ],
    ids=["listdir", "stat", "access", "exists", "walk", "open-r", "pathlib-read", "os.open-r"],
)
def test_reading_is_untouched(readonly: Path, code: str) -> None:
    """La metà che conta.

    Chiudere si sbaglia in due versi, e quello pericoloso non è quello che
    sembra: una scrittura che passa è un guasto, ma una lettura che si chiude
    rende l'interruttore inutilizzabile — e un interruttore che nessuno accende
    non protegge niente.
    """
    stdout, stderr = _run(readonly, code)
    assert "ReadOnlyTurnError" not in stdout + stderr, f"chiusa per sbaglio: {code!r}"
    assert stdout.strip(), f"nessun output da {code!r}: {stderr}"


@pytest.mark.parametrize(
    "code",
    ["import os; os.mkdir('nuova')", "open('nuovo.txt', 'w').write('x')"],
    ids=["mkdir", "open-w"],
)
def test_with_writing_on_the_same_route_lands(writable: Path, code: str) -> None:
    """Controprova: i patch sono per processo, il flag è del *turno*."""
    stdout, stderr = _run(writable, code)
    assert "ReadOnlyTurnError" not in stdout + stderr
    assert (writable / "nuova").is_dir() or (writable / "nuovo.txt").exists()


# ── Dal tool VERO: il cancello sta sul turno, non sul confine ─────────────
#
# PERCHÉ QUESTA SEZIONE ESISTE, DUE VOLTE.
#
# 1. Le prove qui sopra chiamano `PythonNamespace.execute()` dal thread del
#    test. `current_turn_is_readonly()` legge un ContextVar, e in produzione il
#    codice del modello gira su un worker raggiunto con
#    `loop.run_in_executor`, che **non copia il contesto**: il flag tornava al
#    proprio default (`False`) e ogni scrittura passava. Un cancello provato in
#    modo sincrono non dice nulla su quello.
#
# 2. Con `restrict_to_workspace` SPENTO il confine di path non c'è, e ogni
#    wrapper prendeva il ramo di passthrough — prima di arrivare al rifiuto.
#    Ma la sola lettura non è una questione di *dove* si scrive: è del TURNO, e
#    deve valere identica nelle due modalità. Da qui la parametrizzazione.


@pytest.fixture(params=[True, False], ids=["restricted", "unrestricted"])
def restrict(request) -> bool:
    return request.param


def _tool(ws: Path, restrict_to_workspace: bool) -> PythonExecTool:
    cfg = PythonExecConfig()
    tool = PythonExecTool(
        working_dir=str(ws),
        timeout=30,
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=restrict_to_workspace,
        workspace=str(ws),
    )
    _register_builtin_functions(
        tool.namespace, workspace=str(ws), restrict_to_workspace=restrict_to_workspace
    )
    return tool


@pytest.fixture
def readonly_scope(ws: Path, restrict: bool):
    """Turno in sola lettura, con `restrict_to_workspace` del turno allineato."""
    scope = build_workspace_scope(ws, "restricted").without_write_access()
    scope = dataclasses.replace(scope, restrict_to_workspace=restrict)
    with enter_workspace_scope(scope):
        yield ws


_MUTATIONS = [
    ("os.remove", "import os; os.remove({p!r})"),
    ("os.rename", "import os; os.rename({p!r}, {p!r} + '.x')"),
    ("os.mkdir", "import os; os.mkdir({p!r} + '.dir')"),
    ("os.chmod", "import os; os.chmod({p!r}, 0o600)"),
    ("os.utime", "import os; os.utime({p!r}, None)"),
    (
        "os.open-trunc",
        "import os; os.close(os.open({p!r}, os.O_WRONLY | os.O_TRUNC))",
    ),
    ("os.symlink", "import os; os.symlink({p!r}, {p!r} + '.link')"),
    ("shutil.rmtree", "import shutil; shutil.rmtree({p!r} + '.d')"),
    ("shutil.copy", "import shutil; shutil.copy({p!r}, {p!r} + '.copia')"),
    ("shutil.move", "import shutil; shutil.move({p!r}, {p!r} + '.mosso')"),
    ("open-w", "open({p!r}, 'w').write('x')"),
    ("pathlib-write", "from pathlib import Path; Path({p!r}).write_text('x')"),
    ("io.FileIO-w", "import io; io.FileIO({p!r}, 'w').close()"),
    ("builtin-write_file", "write_file({p!r}, 'x')"),
]


@pytest.mark.parametrize(
    ("_id", "code"), _MUTATIONS, ids=[i for i, _c in _MUTATIONS]
)
async def test_every_mutating_route_is_refused_on_the_real_path(
    readonly_scope: Path, restrict: bool, _id: str, code: str
) -> None:
    """Quattordici strade, due modalità, nessuna deve arrivare al filesystem."""
    target = readonly_scope / "modificabile.txt"
    (readonly_scope / (target.name + ".d")).mkdir()

    out = await _tool(readonly_scope, restrict).execute(code=code.format(p=str(target)))

    assert "ReadOnlyTurnError" in out, f"passata (restrict={restrict}): {code!r} -> {out!r}"
    # E il filesystem non si è mosso: un rifiuto detto a metà è il caso peggiore.
    assert target.read_text(encoding="utf-8") == "prima\n"
    assert (readonly_scope / (target.name + ".d")).is_dir()
    for suffix in (".x", ".dir", ".link", ".copia", ".mosso"):
        assert not Path(str(target) + suffix).exists(), suffix


_READS = [
    ("listdir", "import os; print(os.listdir({p!r}))"),
    ("stat", "import os; print(os.stat({p!r} + '/leggibile.txt').st_size)"),
    ("access", "import os; print(os.access({p!r}, os.R_OK))"),
    ("walk", "import os; print(sum(1 for _ in os.walk({p!r})))"),
    ("open-r", "print(open({p!r} + '/leggibile.txt').read())"),
    (
        "pathlib-read",
        "from pathlib import Path; print(Path({p!r} + '/leggibile.txt').read_text())",
    ),
    (
        "os.open-r",
        "import os; fd = os.open({p!r} + '/leggibile.txt', os.O_RDONLY);"
        " print(os.read(fd, 4)); os.close(fd)",
    ),
    ("builtin-read_file", "print(read_file({p!r} + '/leggibile.txt'))"),
]


@pytest.mark.parametrize(("_id", "code"), _READS, ids=[i for i, _c in _READS])
async def test_reading_is_untouched_on_the_real_path(
    readonly_scope: Path, restrict: bool, _id: str, code: str
) -> None:
    """La metà che conta: un interruttore che chiude anche le letture non si usa."""
    out = await _tool(readonly_scope, restrict).execute(code=code.format(p=str(readonly_scope)))

    assert "ReadOnlyTurnError" not in out, f"chiusa per sbaglio: {code!r} -> {out!r}"
    assert "Traceback" not in out, f"caduta per altro: {code!r} -> {out!r}"
    assert out.strip() and out.strip() != "(no output)", f"nessun output da {code!r}: {out!r}"


async def test_a_writable_turn_still_writes_on_the_real_path(
    ws: Path, restrict: bool
) -> None:
    """Controprova: i wrapper sono di processo, il divieto è del *turno*."""
    scope = dataclasses.replace(
        build_workspace_scope(ws, "restricted"), restrict_to_workspace=restrict
    )
    with enter_workspace_scope(scope):
        out = await _tool(ws, restrict).execute(
            code=f"open({str(ws / 'nuovo.txt')!r}, 'w').write('x')"
        )

    assert "ReadOnlyTurnError" not in out, out
    assert (ws / "nuovo.txt").read_text(encoding="utf-8") == "x"


async def test_the_yield_time_session_route_is_refused(readonly_scope: Path) -> None:
    """Il ramo `yield_time_ms` non passa dall'executor ma da un thread grezzo.

    Porta con sé ancora meno del salto in executor: nessun ContextVar. Era la
    strada che ignorava l'interruttore anche con tutto il resto chiuso.
    """
    target = readonly_scope / "modificabile.txt"

    out = await _tool(readonly_scope, True).execute(
        code=f"open({str(target)!r}, 'w').write('x')", yield_time_ms=3000
    )

    assert "ReadOnlyTurnError" in out, out
    assert target.read_text(encoding="utf-8") == "prima\n"


async def test_host_code_keeps_writing_during_a_readonly_turn(readonly_scope: Path) -> None:
    """Il gate che rende innocuo il rifiuto anticipato.

    I wrapper stanno sul modulo globale e non vengono mai smontati: se il
    rifiuto valesse anche fuori da un exec guardato, un turno in sola lettura
    non riuscirebbe più a persistere la propria sessione né il proprio
    transcript. Questo test scrive dal thread del TEST — cioè come il gateway —
    mentre il turno in sola lettura è attivo.
    """
    # Prima si fa girare un exec guardato, così i wrapper sono montati.
    await _tool(readonly_scope, True).execute(code="print(1)")

    (readonly_scope / "dal-gateway.txt").write_text("ok", encoding="utf-8")
    import os as _os

    _os.remove(readonly_scope / "dal-gateway.txt")


class TestTheFlagReachesTheThreadThatRuns:
    """Il test che muore se il cancello torna a essere solo sincrono.

    Non prova un rifiuto: prova il *meccanismo*. Toccando la copia del contesto
    in `run_python_async` questi assert cadono dicendo cosa si è rotto, mentre
    ogni prova che chiama il namespace dal proprio thread continuerebbe a
    passare — che è esattamente come il difetto è arrivato in produzione.
    """

    async def test_current_turn_is_readonly_is_true_on_the_worker(
        self, readonly_scope: Path
    ) -> None:
        visto: dict[str, Any] = {}

        def _register() -> None:
            from jenny.security.workspace_access import current_turn_is_readonly

            visto["thread"] = threading.get_ident()
            visto["readonly"] = current_turn_is_readonly()

        tool = _tool(readonly_scope, True)
        tool.namespace.register_function("_register", _register)

        await tool.execute(code="_register()")

        assert visto["thread"] != threading.get_ident(), (
            "il codice ha girato sul thread del test: questa prova non dice più "
            "niente sulla produzione"
        )
        assert visto["readonly"] is True, (
            "il flag di sola lettura non attraversa il salto di thread: ogni "
            "cancello che lo legge è inerte in produzione"
        )
