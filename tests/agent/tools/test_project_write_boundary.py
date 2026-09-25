"""Dentro un progetto: si legge dove serve, si scrive solo nella sua cartella.

La prigione di una sessione-progetto è **sulla scrittura**. In lettura non serve
e costava caro: fuori dalla directory privata dell'app non si arriva comunque
(il permesso di storage non ce l'abbiamo, il confine vero lo mette Android),
mentre restringere le letture toglieva a Jenny la possibilità di leggere la
propria skill dentro un progetto — `SkillsLoader` le passa il percorso di
`SKILL.md` perché se lo legga da sé, e sotto scope stretto quel percorso veniva
poi negato. Il caricamento progressivo moriva dentro ogni progetto.

La scrittura invece è l'isolazione che conta: è ciò che tiene un progetto
lontano dai file di un altro, da `USER.md` e da `SOUL.md`.

I cancelli sono due e vanno provati tutti e due, perché hanno forme diverse: i
tool file (dove lettura e scrittura erano già funzioni separate) e `python_exec`
(dove c'era un cancello solo, e dove `open()` grezza è una terza superficie che
non passa dai builtin).

**La superficie `os` è un cancello a sé, e ci è arrivata mancandolo.** Dentro
`python_exec` c'erano due confini di scrittura che non si parlavano:
`_resolve_workspace_write` restringeva alla cartella del progetto, i wrapper di
`os` validavano contro la radice con cui il tool era stato costruito. Con uno
scope su `wikis/patreon`, `open('<ws>/SOUL.md', 'w')` veniva rifiutata e
`os.remove('<ws>/SOUL.md')` cancellava. Le prove stanno in
`TestSuperficieOsAsincrona`, che passa dal tool VERO — vedi lì perché non basta
chiamare il namespace.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from jenny.agent.tools.filesystem import ReadFileTool, WriteFileTool
from jenny.agent.tools.python_exec import PythonExecTool, PythonNamespace
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.config.tool_schemas import PythonExecConfig
from jenny.security.workspace_access import (
    bind_workspace_scope,
    reset_workspace_scope,
    validate_workspace_scope_payload,
)

_REFUSED = "outside allowed directory"


@pytest.fixture
def scoped(tmp_path: Path):
    """Un workspace con una skill, due wiki, e lo scope legato alla prima."""
    ws = tmp_path / "workspace"
    project = ws / "wikis" / "patreon"
    other = ws / "wikis" / "etf"
    skill = ws / "skills" / "llm-wiki"
    for d in (project, other, skill):
        d.mkdir(parents=True)
    (skill / "SKILL.md").write_text("come si tiene una wiki", encoding="utf-8")
    (other / "CLAUDE.md").write_text("l'altra wiki", encoding="utf-8")
    (ws / "SOUL.md").write_text("chi sono", encoding="utf-8")

    scope = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "restricted"},
        default_workspace=ws,
        default_restrict_to_workspace=True,
    )
    token = bind_workspace_scope(scope)
    try:
        yield ws, project, other, skill
    finally:
        reset_workspace_scope(token)


class _Recorder:
    def __init__(self) -> None:
        self.functions: dict[str, Any] = {}

    def register_function(self, name: str, func: Any) -> None:
        self.functions[name] = func


def _builtins(workspace: Path) -> dict[str, Any]:
    recorder = _Recorder()
    _register_builtin_functions(
        recorder,  # type: ignore[arg-type]
        workspace=str(workspace),
        restrict_to_workspace=True,
    )
    return recorder.functions


def _namespace(workspace: Path) -> PythonNamespace:
    cfg = PythonExecConfig()
    ns = PythonNamespace(
        working_dir=str(workspace),
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=True,
        workspace=str(workspace),
    )
    _register_builtin_functions(
        ns, workspace=str(workspace), restrict_to_workspace=True
    )
    return ns


# ── i tool file ──────────────────────────────────────────────────────────────


class TestToolFile:
    async def test_legge_la_propria_skill(self, scoped):
        """La regressione concreta per cui la regola esiste."""
        ws, _project, _other, skill = scoped
        tool = ReadFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)

        assert "come si tiene una wiki" in await tool.execute(path=str(skill / "SKILL.md"))

    async def test_legge_unaltra_wiki_se_gliela_si_chiede(self, scoped):
        ws, _project, other, _skill = scoped
        tool = ReadFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)

        assert "l'altra wiki" in await tool.execute(path=str(other / "CLAUDE.md"))

    async def test_scrive_nella_propria_cartella(self, scoped):
        ws, project, _other, _skill = scoped
        tool = WriteFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)

        result = await tool.execute(path=str(project / "wiki" / "nota.md"), content="ok")

        assert "Successfully wrote" in result
        assert (project / "wiki" / "nota.md").read_text(encoding="utf-8") == "ok"

    async def test_non_scrive_nella_wiki_di_un_altro_progetto(self, scoped):
        ws, _project, other, _skill = scoped
        tool = WriteFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)

        assert _REFUSED in await tool.execute(path=str(other / "rubato.md"), content="no")
        assert not (other / "rubato.md").exists()

    async def test_non_riscrive_chi_e_jenny(self, scoped):
        """`SOUL.md` e `USER.md` stanno fuori dalla cartella legata, ed è quello
        che li protegge: nessuna allowlist da mantenere."""
        ws, _project, _other, _skill = scoped
        tool = WriteFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)

        assert _REFUSED in await tool.execute(path=str(ws / "SOUL.md"), content="no")
        assert (ws / "SOUL.md").read_text(encoding="utf-8") == "chi sono"


# ── i builtin di python_exec ────────────────────────────────────────────────


class TestBuiltinPythonExec:
    def test_read_file_arriva_alla_skill(self, scoped):
        ws, _project, _other, skill = scoped

        assert "come si tiene una wiki" in _builtins(ws)["read_file"](str(skill / "SKILL.md"))

    def test_write_file_resta_nella_cartella_del_progetto(self, scoped):
        ws, project, _other, _skill = scoped

        _builtins(ws)["write_file"](str(project / "dentro.txt"), "ok")

        assert (project / "dentro.txt").read_text(encoding="utf-8") == "ok"

    @pytest.mark.parametrize("fn", ["write_file", "append_file"])
    def test_write_file_fuori_viene_rifiutato(self, scoped, fn):
        ws, _project, other, _skill = scoped

        with pytest.raises(Exception, match=_REFUSED):
            _builtins(ws)[fn](str(other / "rubato.txt"), "no")
        assert not (other / "rubato.txt").exists()

    def test_write_json_fuori_viene_rifiutato(self, scoped):
        ws, _project, other, _skill = scoped

        with pytest.raises(Exception, match=_REFUSED):
            _builtins(ws)["write_json"](str(other / "rubato.json"), {"a": 1})


# ── `open()` grezza dentro il sandbox ───────────────────────────────────────


class TestOpenGrezza:
    """La terza superficie: uno script che non usa i builtin.

    Se `open(..., 'w')` non fosse confinata, il confine sarebbe teatro — basta
    una riga di Python per aggirarlo.
    """

    def test_apre_in_lettura_fuori_dal_progetto(self, scoped):
        ws, _project, _other, skill = scoped

        stdout, stderr, _ = _namespace(ws).execute(
            f"print(open({str(skill / 'SKILL.md')!r}).read())", str(ws)
        )

        assert stderr == ""
        assert "come si tiene una wiki" in stdout

    def test_non_apre_in_scrittura_fuori_dal_progetto(self, scoped):
        ws, _project, other, _skill = scoped
        target = other / "rubato.txt"

        _stdout, stderr, _ = _namespace(ws).execute(
            f"open({str(target)!r}, 'w').write('no')", str(ws)
        )

        assert _REFUSED in stderr
        assert not target.exists()

    def test_apre_in_scrittura_dentro_il_progetto(self, scoped):
        ws, project, _other, _skill = scoped
        target = project / "dentro.txt"

        _stdout, stderr, _ = _namespace(ws).execute(
            f"open({str(target)!r}, 'w').write('ok')", str(ws)
        )

        assert stderr == ""
        assert target.read_text(encoding="utf-8") == "ok"


# ── senza scope non cambia niente ───────────────────────────────────────────


class TestSenzaProgetto:
    async def test_la_sessione_personale_scrive_in_tutto_il_workspace(self, tmp_path: Path):
        ws = tmp_path / "workspace"
        (ws / "wikis" / "patreon").mkdir(parents=True)
        tool = WriteFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)

        result = await tool.execute(path=str(ws / "wikis" / "patreon" / "x.md"), content="ok")

        assert "Successfully wrote" in result

    def test_e_i_builtin_pure(self, tmp_path: Path):
        ws = tmp_path / "workspace"
        (ws / "sotto").mkdir(parents=True)

        _builtins(ws)["write_file"](str(ws / "sotto" / "x.txt"), "ok")

        assert (ws / "sotto" / "x.txt").read_text(encoding="utf-8") == "ok"


# ── la superficie `os`, dal tool vero ───────────────────────────────────────
#
# PERCHÉ DA QUI SI PASSA DAL TOOL E NON DAL NAMESPACE. Le prove qui sopra
# chiamano `PythonNamespace.execute()` dal thread del test, e per il confine di
# path va bene: quel confine è thread-local e lo monta `_enter_guard` sul thread
# che esegue. Il confine di scrittura del PROGETTO no: arriva da un ContextVar
# (`current_tool_workspace`), e in produzione il codice del modello gira su un
# worker raggiunto con `loop.run_in_executor`, che **non copia il contesto**.
# Una prova sincrona vede quindi un cancello che in produzione non c'era. Da qui
# la regola: ogni prova di questo cancello passa da `await
# PythonExecTool.execute()`.

_BOUNDARY_ERROR = "WorkspaceBoundaryError"

# (id, codice, che tipo di bersaglio serve). `{p}` è il percorso da colpire,
# `{d}` una destinazione accanto a lui.
_MUTAZIONI: tuple[tuple[str, str, str], ...] = (
    ("os.remove", "import os; os.remove({p!r})", "file"),
    ("os.rename", "import os; os.rename({p!r}, {d!r})", "file"),
    (
        "os.open-trunc",
        "import os; os.close(os.open({p!r}, os.O_WRONLY | os.O_TRUNC))",
        "file",
    ),
    ("shutil.rmtree", "import shutil; shutil.rmtree({p!r})", "dir"),
    ("shutil.move", "import shutil; shutil.move({p!r}, {d!r})", "file"),
    ("pathlib-unlink", "from pathlib import Path; Path({p!r}).unlink()", "file"),
    ("pathlib-rename", "from pathlib import Path; Path({p!r}).rename({d!r})", "file"),
)
_MUTAZIONI_IDS = [nome for nome, _code, _kind in _MUTAZIONI]


def _tool(workspace: Path) -> PythonExecTool:
    """Il tool COSTRUITO SULLA RADICE dell'installazione.

    È la forma con `orchestrator_mode=False`: `AgentLoop` passa ai tool
    `ctx.workspace`, cioè la radice, e la restrizione al progetto può venire
    solo dallo scope del turno. Un subagent con scope invece riceve già la
    cartella del progetto come radice — lì i due confini coincidono e il difetto
    non si vede, che è la ragione per cui va provata questa forma.
    """
    cfg = PythonExecConfig()
    tool = PythonExecTool(
        working_dir=str(workspace),
        timeout=30,
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=True,
        workspace=str(workspace),
    )
    _register_builtin_functions(
        tool.namespace, workspace=str(workspace), restrict_to_workspace=True
    )
    return tool


class TestSuperficieOsAsincrona:
    @pytest.mark.parametrize(("_id", "code", "kind"), _MUTAZIONI, ids=_MUTAZIONI_IDS)
    async def test_fuori_dal_progetto_e_rifiutata(self, scoped, _id, code, kind):
        ws, _project, other, _skill = scoped
        if kind == "dir":
            target = other  # la wiki di un ALTRO progetto
            assert target.is_dir()
        else:
            target = ws / "SOUL.md"  # chi è Jenny, fuori da ogni progetto

        out = await _tool(ws).execute(
            code=code.format(p=str(target), d=str(target) + ".spostato")
        )

        assert _BOUNDARY_ERROR in out, f"passata: {code!r} -> {out!r}"
        assert target.exists(), "rifiutato ma cancellato è il caso peggiore"
        if kind == "file":
            assert target.read_text(encoding="utf-8") == "chi sono"
            assert not Path(str(target) + ".spostato").exists()

    @pytest.mark.parametrize(("_id", "code", "kind"), _MUTAZIONI, ids=_MUTAZIONI_IDS)
    async def test_dentro_il_progetto_passa(self, scoped, _id, code, kind):
        """La controprova: il cancello è lo SCOPE, non un divieto generale."""
        ws, project, _other, _skill = scoped
        if kind == "dir":
            target = project / "da-buttare"
            target.mkdir()
            (target / "x.txt").write_text("x", encoding="utf-8")
        else:
            target = project / "dentro.txt"
            target.write_text("contenuto", encoding="utf-8")

        out = await _tool(ws).execute(
            code=code.format(p=str(target), d=str(target) + ".spostato")
        )

        assert _BOUNDARY_ERROR not in out, f"chiusa per sbaglio: {code!r} -> {out!r}"
        assert "Traceback" not in out, f"caduta per altro: {code!r} -> {out!r}"
        # L'effetto è avvenuto davvero: il bersaglio è sparito (rimosso,
        # rinominato, spostato) oppure è stato troncato sul posto.
        spostato = Path(str(target) + ".spostato")
        assert not target.exists() or spostato.exists() or (
            kind == "file" and target.read_text(encoding="utf-8") == ""
        ), f"nessun effetto da {code!r}: {out!r}"

    async def test_la_lettura_fuori_dal_progetto_resta_aperta(self, scoped):
        """La metà che conta: il confine è asimmetrico e deve restarlo."""
        ws, _project, _other, skill = scoped

        out = await _tool(ws).execute(
            code=f"import os; print(os.stat({str(skill / 'SKILL.md')!r}).st_size)"
        )

        assert _BOUNDARY_ERROR not in out
        assert out.strip().isdigit(), out


class TestIlContestoDelTurnoArrivaAlWorker:
    """Il test che muore se il cancello torna a essere solo sincrono.

    Non prova un rifiuto: prova il *meccanismo*. Se qualcuno toglie la copia del
    contesto in `run_python_async`, i due assert qui sotto cadono con un
    messaggio che dice esattamente cosa si è rotto — mentre ogni prova che
    chiama il namespace dal proprio thread continuerebbe a passare.
    """

    async def test_lo_scope_del_progetto_e_visibile_sul_thread_di_esecuzione(
        self, scoped
    ):
        ws, project, _other, _skill = scoped
        visto: dict[str, Any] = {}

        def _register() -> None:
            from jenny.security.workspace_access import current_workspace_scope

            scope = current_workspace_scope()
            visto["thread"] = threading.get_ident()
            visto["project_path"] = None if scope is None else str(scope.project_path)

        tool = _tool(ws)
        tool.namespace.register_function("_register", _register)

        await tool.execute(code="_register()")

        assert visto["thread"] != threading.get_ident(), (
            "il codice ha girato sul thread del test: questa prova non dice più "
            "niente sulla produzione"
        )
        assert visto["project_path"] == str(project), (
            "lo scope del turno non arriva al thread che esegue: il confine di "
            "scrittura del progetto è tornato alla radice del workspace"
        )
