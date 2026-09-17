"""Guardia: ogni primitiva asyncio in una globale di modulo deve avere un reset.

Il gateway riparte nello stesso processo (retry di ``run_gateway`` + restart
lato Kotlin), quindi apre più di un ``asyncio.run``. Una primitiva asyncio
tenuta in una globale di modulo sopravvive al loop che l'ha adottata, e da lì
in poi rifiuta ogni accodamento: la classe di bug che il blocco di reset in
``android_entry.run_gateway`` esiste per chiudere.

Il blocco è però una lista che si può dimenticare di allungare — è esattamente
così che ``config.store._LOCK`` è rimasto fuori. Questo test rende quella
dimenticanza rumorosa: chi aggiunge una primitiva la deve o resettare, o
iscrivere qui con la ragione per cui non serve.

Il perimetro copre anche le primitive create dentro ``__init__`` di una classe
di cui esiste un singleton di modulo — ``ssh_jobs._store``, la cui
``SshJobStore._lock`` è la ragione di ``reset_job_store``, e i quattro bridge
Chaquopy, che tengono il loro lock in un ``BridgeCache``. È la stessa classe di
bug, solo nascosta dentro un ``__init__``: un secondo ``asyncio.run`` non eredita
un lock perché sta in un attributo invece che in una globale.

Questo pezzo stava fuori, con scritto che riconoscerlo "richiederebbe inseguire i
tipi" e sarebbe un test che fallisce a caso. Non serve inseguire niente: gli
import qui sono espliciti, quindi dal nome della classe si arriva al file e da
lì al suo ``__init__`` per via meccanica. Misurato sull'albero di allora: un solo
risultato, ``ssh_jobs:_store._lock``, che era appunto l'unica cosa fissata a mano
— nessun falso positivo da spegnere.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "jenny"

# Le primitive di ``asyncio`` che si legano al loop, più i riferimenti a un loop
# vivo: tutte cose che un secondo ``asyncio.run`` non può ereditare.
_LOOP_BOUND_FACTORIES = {
    "Lock",
    "Event",
    "Condition",
    "Semaphore",
    "BoundedSemaphore",
    "Barrier",
    "Queue",
    "LifoQueue",
    "PriorityQueue",
    "Future",
    "get_event_loop",
    "get_running_loop",
    "new_event_loop",
}

# ``modulo:nome`` -> funzione di reset che la rimette a nuovo. Ogni voce viene
# verificata due volte: che esista ancora, e che il reset sia davvero chiamato
# da ``android_entry.run_gateway``.
ALLOWED: dict[str, str] = {
    "jenny/agent/tools/android_web.py:_BRIDGE_LOCK": "reset_android_web_state",
    "jenny/agent/tools/ssh_jobs.py:_store._lock": "reset_job_store",
    "jenny/agent/tools/browser.py:_BROWSER_LOCK": "reset_browser_state",
    "jenny/config/store.py:_LOCK": "reset_config_store_state",
    "jenny/runtime/floating.py:_BRIDGE.lock": "reset_floating_state",
    "jenny/runtime/location.py:_BRIDGE.lock": "reset_location_state",
    "jenny/runtime/notifier.py:_BRIDGE.lock": "reset_notifier_state",
    "jenny/runtime/power.py:_BRIDGE.lock": "reset_power_state",
    "jenny/runtime/power.py:_STATE_LOCK": "reset_power_state",
    "jenny/runtime/power.py:_WAKE_EVENT": "reset_power_state",
    "jenny/runtime/power.py:_WAKE_LOOP": "reset_power_state",
    "jenny/runtime/native_input.py:_LOOP": "reset_native_input",
    "jenny/webui/android_apps_api.py:_BRIDGE.lock": "reset_installed_apps_state",
    "jenny/webui/settings_api.py:_update_check_lock": "reset_update_check_state",
}


def _factory_name(node: ast.expr | None) -> str | None:
    """``asyncio.Lock()`` -> ``"Lock"``; qualunque altra cosa -> ``None``."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id == "asyncio" and func.attr in _LOOP_BOUND_FACTORIES:
            return func.attr
    return None


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [t.id for t in targets if isinstance(t, ast.Name)]


def _module_level_hits(tree: ast.Module, rel: str) -> list[str]:
    """Assegnamenti a livello di modulo, anche dentro ``if``/``try`` di modulo."""
    hits: list[str] = []

    def walk(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and _factory_name(node.value):
                hits.extend(f"{rel}:{name}" for name in _assigned_names(node))
            for attr in ("body", "orelse", "finalbody"):
                nested = getattr(node, attr, None)
                if isinstance(nested, list):
                    walk([s for s in nested if isinstance(s, ast.stmt)])

    walk(tree.body)
    return hits


def _cached_global_hits(tree: ast.Module, rel: str) -> list[str]:
    """Globali riempite pigramente dentro una funzione (il pattern cache).

    Le funzioni ``reset_*`` sono la cura, non la malattia: quello che assegnano
    non conta come nuova primitiva da censire.
    """
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("reset_"):
            continue
        declared: set[str] = {
            name
            for sub in ast.walk(node)
            if isinstance(sub, ast.Global)
            for name in sub.names
        }
        if not declared:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, (ast.Assign, ast.AnnAssign)) and _factory_name(sub.value):
                hits.extend(f"{rel}:{n}" for n in _assigned_names(sub) if n in declared)
    return hits


# --- Primitive tenute in un attributo di un singleton di modulo -------------
#
# I due passaggi sopra guardano il *valore assegnato*: cercano
# ``asyncio.Lock()`` alla destra di un ``=``. Un singleton di modulo lo nasconde
# di un livello — ``_store = SshJobStore()``, e il lock nasce nel suo
# ``__init__`` — quindi va risolto: dal nome della classe al file che la
# definisce, e da lì al suo ``__init__``. Niente inferenza di tipi: solo il nome
# e gli import, che qui sono tutti espliciti.

_TREES: dict[Path, ast.Module] = {}


def _tree(path: Path) -> ast.Module:
    if path not in _TREES:
        _TREES[path] = ast.parse(path.read_text(encoding="utf-8"))
    return _TREES[path]


def _class_named(tree: ast.Module, name: str) -> ast.ClassDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _locate_class(path: Path, name: str) -> ast.ClassDef | None:
    """La ``ClassDef`` di ``name``: definita qui, o importata da un modulo ``jenny``."""
    found = _class_named(_tree(path), name)
    if found is not None:
        return found
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if not node.module.startswith("jenny"):
            continue
        if name not in {alias.asname or alias.name for alias in node.names}:
            continue
        source = REPO / Path(*node.module.split(".")).with_suffix(".py")
        if source.is_file():
            return _class_named(_tree(source), name)
    return None


def _primitives_in_init(cls: ast.ClassDef) -> list[str]:
    """Gli attributi che l'``__init__`` riempie con una primitiva legata al loop."""
    out: list[str] = []
    for node in cls.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != "__init__":
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, (ast.Assign, ast.AnnAssign)) or not _factory_name(sub.value):
                continue
            targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            out.extend(
                t.attr
                for t in targets
                if isinstance(t, ast.Attribute)
                and isinstance(t.value, ast.Name)
                and t.value.id == "self"
            )
    return out


def _constructed_class(node: ast.expr | None) -> str | None:
    """``SshJobStore(...)`` -> ``"SshJobStore"``. La maiuscola è il filtro."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        return None
    name = node.func.id
    return name if name[:1].isupper() else None


def _singleton_attribute_hits(tree: ast.Module, path: Path, rel: str) -> list[str]:
    candidates: list[tuple[str, str]] = []

    def collect(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                cls_name = _constructed_class(node.value)
                if cls_name:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    candidates.extend(
                        (t.id, cls_name) for t in targets if isinstance(t, ast.Name)
                    )
            for attr in ("body", "orelse", "finalbody"):
                nested = getattr(node, attr, None)
                if isinstance(nested, list):
                    collect([s for s in nested if isinstance(s, ast.stmt)])

    collect(tree.body)

    # Lo stesso, per la globale riempita pigramente: ``global _store; _store = SshJobStore()``.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("reset_"):
            continue
        declared = {
            name
            for sub in ast.walk(node)
            if isinstance(sub, ast.Global)
            for name in sub.names
        }
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Assign):
                continue
            cls_name = _constructed_class(sub.value)
            if not cls_name:
                continue
            candidates.extend(
                (t.id, cls_name)
                for t in sub.targets
                if isinstance(t, ast.Name) and t.id in declared
            )

    hits: list[str] = []
    for var, cls_name in candidates:
        cls = _locate_class(path, cls_name)
        if cls is None:
            continue
        hits.extend(f"{rel}:{var}.{attr}" for attr in _primitives_in_init(cls))
    return hits


def _discover() -> set[str]:
    found: set[str] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        tree = _tree(path)
        found.update(_module_level_hits(tree, rel))
        found.update(_cached_global_hits(tree, rel))
        found.update(_singleton_attribute_hits(tree, path, rel))
    return found


def test_every_module_level_asyncio_primitive_is_accounted_for():
    found = _discover()
    allowed = set(ALLOWED)

    unlisted = sorted(found - allowed)
    assert not unlisted, (
        "Nuova primitiva asyncio in una globale di modulo: "
        f"{unlisted}. Un secondo ``asyncio.run`` non può ereditarla. "
        "Aggiungi una funzione ``reset_*``, chiamala dal blocco di reset in "
        "``android_entry.run_gateway``, e iscrivila in ALLOWED qui sopra."
    )

    stale = sorted(allowed - found)
    assert not stale, (
        f"Voci di ALLOWED che non esistono più nel sorgente: {stale}. "
        "Rimuovile (e togli il reset ormai inutile dall'entry point)."
    )


def _entry_point_calls() -> set[str]:
    tree = ast.parse((PACKAGE / "android_entry.py").read_text(encoding="utf-8"))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_every_allowlisted_reset_is_wired_into_the_entry_point():
    """Iscrivere il reset in ALLOWED senza chiamarlo lascerebbe il bug in piedi."""
    called = _entry_point_calls()

    missing = sorted({reset for reset in ALLOWED.values() if reset not in called})
    assert not missing, (
        f"Reset dichiarati in ALLOWED ma mai chiamati da run_gateway: {missing}"
    )


# ``test_the_ssh_job_store_singleton_is_reset_too`` stava qui, e fissava a mano
# l'unico caso che il censimento non vedeva: ``SshJobStore._lock``, che resta
# preso *durante* l'exec SSH — due poll concorrenti si accodano sul serio, e
# quello lega il lock al loop. Ora quel caso lo scopre
# ``_singleton_attribute_hits``, ed è iscritto in ``ALLOWED``: i due test qui
# sopra ne verificano già entrambe le metà (che esista ancora, e che
# ``reset_job_store`` sia chiamato da ``run_gateway``). Un test che ripete
# quello che un altro asserisce sembra proteggere qualcosa e non protegge nulla.
