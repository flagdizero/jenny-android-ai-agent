"""Una sola risposta a «dove posso scrivere» — passo **T4.4**.

Il confine di scrittura era ricalcolato in sei posti: i tool file
(``filesystem._resolve_write``), i builtin di ``python_exec``
(``_write_root``), i wrapper di ``os``/``io`` dello stesso modulo
(``_project_write_boundary`` / ``_mutation_boundary``), ``download`` e
``journal``. Nessuno sbagliato da solo; l'insieme sì — «che cosa può cambiare
questo turno» è UNA domanda, e sei risposte non possono che divergere. Ci erano
già divergiti: con uno scope su ``wikis/patreon``, ``open('<ws>/SOUL.md', 'w')``
veniva rifiutata e ``os.remove('<ws>/SOUL.md')`` passava.

Questo file è il guardiano di quell'unificazione, ed è fatto di due metà che
provano cose diverse:

1. **La metà estensionale.** Ogni superficie che scrive risolve *la stessa*
   radice, e la si misura provando a scrivere davvero in quattro posti — la
   cartella del progetto, quella di un altro progetto, la radice del workspace e
   una cartella fuori — invece di leggere un attributo. Un confine si prova con
   una scrittura rifiutata, non con un ``==`` su un ``Path``.

2. **La metà strutturale.** Chi, sotto ``agent/tools/``, chiede la radice
   scrivibile deve avere una sonda qui. Il controllo passa dall'AST e non da una
   ``grep``: un modulo nuovo che chiama ``write_root()`` fa fallire questo file
   **per costruzione**, prima che qualcuno si ricordi di aggiungerlo. Non è
   ancora "una sonda scritta da sola" — quella la deve scrivere una persona — ma
   il *rilevamento* non dipende dalla memoria di nessuno.

Le sonde di ``python_exec`` passano dal tool VERO e non dal namespace: il
confine del progetto arriva da un ContextVar, e in produzione il codice del
modello gira su un worker raggiunto con ``run_in_executor``. Una prova sincrona
vedrebbe un cancello che in produzione poteva non esserci (v.
``tests/agent/tools/test_project_write_boundary.py``).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
import pytest

from jenny.agent.tools import download as download_mod
from jenny.agent.tools.download import DownloadFileTool
from jenny.agent.tools.filesystem import WriteFileTool
from jenny.agent.tools.journal import JournalAppendTool
from jenny.agent.tools.python_exec import PythonExecTool
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.config.tool_schemas import PythonExecConfig
from jenny.security.workspace_access import (
    bind_workspace_scope,
    reset_workspace_scope,
    validate_workspace_scope_payload,
)

_REFUSED = ("WorkspaceBoundaryError", "outside allowed directory")
_DAY = date(2026, 8, 23)


# ── l'ambiente: un progetto legato, e tre posti dove non si deve scrivere ────


@dataclass
class Env:
    tmp: Path
    ws: Path
    project: Path
    other: Path
    outside: Path

    @property
    def candidates(self) -> dict[str, Path]:
        """Le quattro cartelle su cui si misura una radice.

        Una sola deve accettare: la cartella del progetto legato. Le altre tre
        sono i tre modi in cui un confine sbagliato si manifesta — la wiki di un
        altro progetto, la radice personale (dove stanno ``SOUL.md`` e
        ``USER.md``) e il fuori.
        """
        return {
            "progetto": self.project,
            "altro progetto": self.other,
            "radice del workspace": self.ws,
            "fuori dal workspace": self.outside,
        }


@pytest.fixture
def env(tmp_path: Path):
    ws = tmp_path / "workspace"
    project = ws / "wikis" / "patreon"
    other = ws / "wikis" / "etf"
    outside = tmp_path / "outside"
    for d in (project / "wiki", project / "raw" / "journal", other / "wiki", outside):
        d.mkdir(parents=True)
    (ws / "SOUL.md").write_text("chi sono", encoding="utf-8")

    scope = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "restricted"},
        default_workspace=ws,
        default_restrict_to_workspace=True,
    )
    token = bind_workspace_scope(scope)
    try:
        yield Env(tmp=tmp_path, ws=ws, project=project, other=other, outside=outside)
    finally:
        reset_workspace_scope(token)


@pytest.fixture(autouse=True)
def _allow_all_urls(monkeypatch):
    """La sonda di ``download`` usa un transport finto: salta il check SSRF."""
    monkeypatch.setattr(download_mod, "validate_url_target", lambda url: (True, None))


async def _only_accepted(env: Env, accepts: Callable[[Path], Awaitable[bool]]) -> Path:
    """La radice di una superficie, misurata: l'unica candidata che accetta."""
    accepted = [name for name, d in env.candidates.items() if await accepts(d)]
    assert len(accepted) == 1, (
        f"una superficie di scrittura deve accettare esattamente una delle quattro "
        f"candidate; ha accettato {accepted}"
    )
    return env.candidates[accepted[0]]


# ── le sonde, una per superficie ────────────────────────────────────────────


async def _root_filesystem(env: Env) -> Path:
    tool = WriteFileTool(workspace=env.ws, allowed_dir=env.ws, restrict_to_workspace=True)

    async def accepts(d: Path) -> bool:
        out = await tool.execute(path=str(d / "probe-fs.txt"), content="x")
        return "Successfully wrote" in out

    return await _only_accepted(env, accepts)


async def _root_python_exec_builtins(env: Env) -> Path:
    recorder: dict[str, Any] = {}

    class _Rec:
        def register_function(self, name: str, func: Any) -> None:
            recorder[name] = func

    _register_builtin_functions(
        _Rec(),  # type: ignore[arg-type]
        workspace=str(env.ws),
        restrict_to_workspace=True,
    )

    async def accepts(d: Path) -> bool:
        try:
            recorder["write_file"](str(d / "probe-be.txt"), "x")
        except Exception:  # noqa: BLE001 — qualunque rifiuto è un rifiuto
            return False
        return True

    return await _only_accepted(env, accepts)


def _exec_tool(env: Env) -> PythonExecTool:
    """Il tool COSTRUITO SULLA RADICE, come fa ``AgentLoop``.

    È la forma in cui la restrizione al progetto può venire solo dallo scope del
    turno: un subagent riceve già la cartella del progetto e lì i due confini
    coincidono, quindi una divergenza non si vedrebbe.
    """
    cfg = PythonExecConfig()
    tool = PythonExecTool(
        working_dir=str(env.ws),
        timeout=30,
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=True,
        workspace=str(env.ws),
    )
    _register_builtin_functions(
        tool.namespace, workspace=str(env.ws), restrict_to_workspace=True
    )
    return tool


async def _root_python_exec_open(env: Env) -> Path:
    """``open(..., 'w')`` grezza: la superficie di ``_resolve_workspace_write``."""
    tool = _exec_tool(env)

    async def accepts(d: Path) -> bool:
        out = await tool.execute(code=f"open({str(d / 'probe-px.txt')!r}, 'w').write('x')")
        return not any(marker in out for marker in _REFUSED)

    return await _only_accepted(env, accepts)


async def _root_python_exec_os(env: Env) -> Path:
    """La superficie ``os``: il cancello di ``_mutation_boundary`` (T4.2)."""
    tool = _exec_tool(env)

    async def accepts(d: Path) -> bool:
        victim = d / "probe-os.txt"
        victim.write_text("x", encoding="utf-8")
        out = await tool.execute(code=f"import os; os.remove({str(victim)!r})")
        if victim.exists():
            victim.unlink()
            return False
        return not any(marker in out for marker in _REFUSED)

    return await _only_accepted(env, accepts)


async def _root_download(env: Env) -> Path:
    """Destinazione fissa: la radice è quella sotto cui compare ``downloads/``."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"probe", headers={"content-type": "application/pdf"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    tool = DownloadFileTool(workspace=env.ws, client=client)
    out = await tool.execute(url="https://x.example/probe.pdf")
    assert "Saved" in out, out

    created = [p.parent for p in env.tmp.rglob("downloads/probe*")]
    assert len(created) == 1, f"un solo `downloads/` atteso, trovati {created}"
    return created[0].parent


async def _root_journal(env: Env) -> Path:
    """Destinazione fissa: la radice è quella sotto cui compare ``raw/journal/``."""
    tool = JournalAppendTool(today=lambda: _DAY)
    out = await tool.execute(text="una riga")
    assert "Appended to" in out, out

    created = list(env.tmp.rglob("raw/journal/*.md"))
    assert len(created) == 1, f"una sola pagina di diario attesa, trovate {created}"
    # ``<root>/raw/journal/<AAAAMMGG>.md`` → tre livelli sopra il file.
    return created[0].parents[2]


# Nome della superficie → sonda. Il MODULO è la chiave del controllo
# strutturale qui sotto: una superficie nuova senza sonda fa fallire quello.
_SURFACES: dict[str, tuple[str, Callable[[Env], Awaitable[Path]]]] = {
    "filesystem (write_file/edit_file/apply_patch)": ("filesystem.py", _root_filesystem),
    "python_exec builtins (write_file/append_file)": (
        "python_exec_builtins.py",
        _root_python_exec_builtins,
    ),
    "python_exec open() grezza": ("python_exec.py", _root_python_exec_open),
    "python_exec superficie os": ("python_exec.py", _root_python_exec_os),
    "download_file": ("download.py", _root_download),
    "journal_append": ("journal.py", _root_journal),
}


# ── 1. la metà estensionale ─────────────────────────────────────────────────


@pytest.mark.parametrize("surface", sorted(_SURFACES), ids=sorted(_SURFACES))
async def test_every_write_surface_resolves_the_project_root(env: Env, surface: str) -> None:
    """Una superficie alla volta, così il fallimento dice QUALE ha divergito."""
    _module, probe = _SURFACES[surface]
    assert await probe(env) == env.project, (
        f"{surface} non scrive nella cartella del progetto legato: il confine di "
        "scrittura di questa superficie non è quello di `WorkspaceScope.write_root()`"
    )


async def test_all_write_surfaces_agree_on_one_root(env: Env) -> None:
    """Il test che il passo T4.4 esiste per non dover rifare.

    Separato da quello sopra di proposito: là si prova che ognuna è *corretta*,
    qui che sono *d'accordo*. Il secondo cade anche nel caso in cui un giorno la
    radice giusta cambi e solo cinque delle sei se ne accorgano.
    """
    roots = {name: await probe(env) for name, (_m, probe) in _SURFACES.items()}
    assert len(set(roots.values())) == 1, f"le superfici non concordano: {roots}"


# ── 2. la metà strutturale: nessuna settima implementazione ────────────────

_JENNY = Path(__file__).resolve().parents[2] / "jenny"
_TOOLS_DIR = _JENNY / "agent" / "tools"

# I moduli sotto ``agent/tools/`` che hanno una sonda qui sopra.
_PROBED_MODULES = {module for module, _probe in _SURFACES.values()}

# Chi legge ``project_path``/``allowed_root`` **senza** chiedersi dove si può
# scrivere. Non è una lista di perdoni: è la parte che dice *quale altra*
# domanda stanno facendo, e va riletta quando uno di questi cambia mestiere.
_NOT_A_WRITE_ROOT = {
    ("filesystem.py", "project_path"): (
        "base di risoluzione dei percorsi relativi e workspace da mostrare, non il confine"
    ),
    ("message.py", "project_path"): "risolve gli allegati in uscita: è una lettura",
    ("message.py", "allowed_root"): "idem — l'alias storico, non una seconda risposta",
    ("memory_recall.py", "project_path"): (
        "domanda opposta: non dove si scrive, ma **se** questo turno è dentro un "
        "progetto. recall_history legge solo la radice e tace altrove, e il "
        "confronto col workspace di radice è l'unico modo di accorgersene"
    ),
}


def _attribute_reads(path: Path) -> set[str]:
    """I nomi di attributo letti in *path*, dall'AST.

    Dall'AST e non da una ``grep``: i docstring di questi moduli citano
    ``WorkspaceScope.write_root()`` e ``project_path`` a ogni riga di commento, e
    una regex li conterebbe come codice.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in {"write_root", "allowed_root", "project_path"}
    }


def test_every_module_that_asks_for_the_write_root_has_a_probe() -> None:
    """Un tool nuovo che chiama ``write_root()`` viene preso da questo test.

    È il rilevamento "per costruzione": non serve che qualcuno si ricordi di
    aggiungere una sonda, perché senza la sonda questo file è rosso.
    """
    asking = {
        p.name for p in sorted(_TOOLS_DIR.glob("*.py")) if "write_root" in _attribute_reads(p)
    }
    assert asking == _PROBED_MODULES, (
        "questi moduli chiedono la radice scrivibile e non hanno una sonda in "
        f"_SURFACES: {sorted(asking - _PROBED_MODULES)}; questi hanno una sonda ma non "
        f"la chiedono più: {sorted(_PROBED_MODULES - asking)}"
    )


def test_no_tool_computes_a_write_root_by_hand() -> None:
    """``project_path``/``allowed_root`` letti a mano: o è un'altra domanda, o è un buco."""
    unclassified = [
        (p.name, attr)
        for p in sorted(_TOOLS_DIR.glob("*.py"))
        for attr in sorted(_attribute_reads(p) & {"allowed_root", "project_path"})
        if (p.name, attr) not in _NOT_A_WRITE_ROOT
    ]
    assert not unclassified, (
        f"questi ricavano una radice per conto proprio: {unclassified}. Se serve il "
        "confine di scrittura, chiedilo a write_root(); se è un'altra domanda, "
        "dichiarala in _NOT_A_WRITE_ROOT con la ragione"
    )


def test_no_stale_exemptions() -> None:
    """Una riga morta in un elenco è peggio di una mancante: sembra copertura."""
    for (name, attr), reason in _NOT_A_WRITE_ROOT.items():
        path = _TOOLS_DIR / name
        assert path.exists(), f"{name} non esiste più: togli la riga da _NOT_A_WRITE_ROOT"
        assert attr in _attribute_reads(path), (
            f"{name} non legge più .{attr} ({reason}): togli la riga"
        )


def test_allowed_root_only_delegates() -> None:
    """L'alias storico non può tornare a essere un secondo calcolo.

    ``ToolWorkspace.allowed_root`` resta per il call site di lettura in
    ``message.py``. Se un giorno qualcuno ci rimette dentro la formula
    (``if restrict_to_workspace and project_path is not None: ...``) le sei
    risposte tornano a essere sette, e nessun test di comportamento lo vede —
    perché in quel momento il valore sarebbe ancora lo stesso.
    """
    source = (_JENNY / "security" / "workspace_access.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "ToolWorkspace"
    )
    fn = next(
        n
        for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "allowed_root"
    )
    body = [stmt for stmt in fn.body if not isinstance(stmt, ast.Expr)]
    assert len(body) == 1 and isinstance(body[0], ast.Return), (
        "allowed_root deve essere una sola `return`: qualunque logica qui dentro è "
        "una seconda risposta a «dove posso scrivere»"
    )
    call = body[0].value
    assert (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "write_root"
    ), "allowed_root deve delegare a write_root(), non ricalcolare la radice"
