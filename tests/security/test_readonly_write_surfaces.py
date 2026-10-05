"""L'inventario delle scritture, e il guardiano che lo tiene onesto.

Passo **4.1** del piano dei progetti, allargato al passo **T4.7**.

«Sola lettura» vuol dire «non cambia niente sul telefono» (deciso il 22/08), e
quella promessa è mantenuta da una **lista** — chi scrive, e da quale cancello.
Una lista così invecchia in silenzio: il tool aggiunto fra sei mesi è uno
scrittore che nessuno ha classificato, e l'interruttore continua a promettere
quel che non fa più. Questo file è il solo motivo per cui quella promessa resta
vera senza che qualcuno se la ricordi.

Le quattro domande, e sono diverse fra loro:

1. *Chi scrive è classificato?* Se un modulo tocca una scrittura senza chiedere
   allo scope, o è nella lista di quelli che chiedono per conto proprio, o è un
   buco.
2. *Chi dice di chiedere, chiede davvero — e serve a qualcosa?* Un nome in
   elenco non è una prova, e nemmeno la stringa del gate dentro il file lo è:
   T4.7 nasce da un gate reso **irraggiungibile** che lasciava verde la ricerca
   testuale. Da qui le sonde di comportamento in fondo.
3. *I mutatori di ``os`` sono tutti classificati?* Le tabelle di ``python_exec``
   mescolano mutatori, sonde ed enumeratori; l'insieme chiuso in sola lettura è
   dichiarato a parte. Un nome che non sta in nessuno dei due insiemi è un
   mutatore aggiunto e mai deciso.
4. *Le op delle mini-app sono partizionate?* Perché lì è ``query`` — la sola
   lettura — a dover restare aperta.

Non usa ``Tool.read_only``, e la ragione sta in
``WorkspaceScope.without_write_access``: quel flag significa "parallelizzabile",
il suo default è ``False`` per chiunque non l'abbia dichiarato, e riusarlo qui
sarebbe leggere una risposta scritta per un'altra domanda.

**Come il rilevatore guarda il codice** (T4.7). Tre detector in unione, perché
"chi scrive" non è una domanda sola:

* ``_WRITE_PRIMITIVES`` — la *famiglia* delle scritture dirette, quelle che si
  portano la destinazione da sole. Prima erano cinque nomi e la famiglia è molto
  più larga: ``open(..., 'w')``, ``shutil``, i mutatori di ``os``, ``rename``,
  ``unlink``, ``touch``.
* ``_INDIRECT_WRITES`` — le facciate di persistenza. ``long_task`` non tocca un
  path: scrive ``metadata[goal_state]`` e chiama ``sessions.save``. Nessuna
  primitiva lo vedeva, e per questo è stato invisibile a questo file fino a
  T4.6.
* ``_GATE_MENTIONS`` — chi nomina l'interruttore. Serve al caso opposto: un
  modulo il cui *vero* mestiere è scrivere ma che non contiene nessuna
  primitiva, perché la scrittura la fanno i builtin che lui **patcha**
  (``python_exec``). Nessuna scansione testuale può vedere quel modulo dalle
  primitive; può vederlo da qui.

E li applica al codice con **commenti e docstring cancellati**
(:func:`_strip_prose`). Non è igiene: senza, ``python_exec.py`` "risultava
scrittore" per sei occorrenze di ``open(..., 'w')`` che sono tutte prosa, e una
classificazione ottenuta così è una casella spuntata da un commento.
"""

from __future__ import annotations

import ast
import io
import json
import re
import tokenize
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from jenny.agent.tools.app_update import InstallUpdateTool
from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.cron import CronTool
from jenny.agent.tools.download import DownloadFileTool
from jenny.agent.tools.journal import JournalAppendTool
from jenny.agent.tools.long_task import LongTaskTool
from jenny.agent.tools.python_exec import PythonNamespace
from jenny.apps.manifest import STORAGE_OPS, AppAction
from jenny.apps.storage import _MUTATING_OPS, execute_storage_action
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    build_workspace_scope,
    enter_workspace_scope,
)
from jenny.security.workspace_policy import ReadOnlyTurnError
from jenny.session.manager import SessionManager

ROOT = Path(__file__).resolve().parents[2] / "jenny"
TOOLS_DIR = ROOT / "agent" / "tools"

# Oltre ai tool, i moduli che scrivono e che vale la pena **dichiarare** fuori
# scopo invece di lasciarlo implicito: il silenzio su di loro somiglia troppo a
# una dimenticanza. ``session/goal_state.py`` è qui di proposito e non compare in
# nessuno dei due elenchi: scansionato, non scrive niente — serializza un blob
# che salva ``long_task``. È l'unico modo di registrare "l'ho guardato".
_EXTRA_SCANNED = (
    "apps/storage.py",
    "session/manager.py",
    "session/goal_state.py",
    "webui/transcript_store.py",
    "agent/subagent_records.py",
    "config/store.py",
)

# Le scritture **dirette**, quelle che si portano la destinazione da sole. Una
# famiglia, non cinque nomi: fino a T4.7 mancavano ``open(..., 'w')``, tutto
# ``shutil``, i mutatori di ``os``, ``rename``, ``unlink``, ``touch``,
# ``writelines`` — cioè la maggior parte dei modi in cui questo codice scrive.
#
# Restano fuori, e sono i punti ciechi da conoscere:
# * ``fh.write(...)`` su un handle già aperto — indistinguibile da stdout, da un
#   socket, da uno stream in memoria. Lo copre l'``open(..., 'w')`` che lo apre.
# * ``open(path, mode)`` con la modalità in una variabile.
# * ``Path.replace`` — ``.replace(`` è la sostituzione di stringa più comune del
#   codebase. ``os.replace`` sì, quello è qui sotto.
_WRITE_PRIMITIVES = re.compile(
    r"""
      \batomic_write\b
    | \.write_text\(
    | \.write_bytes\(
    | \.writelines\(
    | \.mkdir\(
    | \bensure_dir\(
    | \bmakedirs\(
    | \.rename\(
    | \.unlink\(
    | \.touch\(
    | \bopen\([^)\n]*['"][rwaxbt+]*[wax+][rwaxbt+]*['"]
    | \bshutil\.(?:copy\w*|move|rmtree)\(
    | \bos\.(?:replace|rename|renames|remove|unlink|rmdir|removedirs|mkdir
             |symlink|link|truncate|chmod|chown|utime)\(
    """,
    re.VERBOSE,
)

# Le facciate: la scrittura la fa qualcun altro, la **decisione** è qui.
# ``long_task`` è il caso che ha insegnato la categoria — nessuna primitiva, e
# uno stato che sopravvive al riavvio. Non è un insieme chiuso: è l'elenco delle
# facciate che conosciamo, e va allungato quando ne nasce una.
_INDIRECT_WRITES = re.compile(r"sessions\.save\(|\bsave_config\(|\bconfig_store\.mutate\(")

# Chi nomina l'interruttore. Un modulo che parla di sola lettura sta o
# implementando un cancello o passandoci: in entrambi i casi appartiene
# all'inventario, e se non c'è è l'inventario a essere vecchio.
_GATE_MENTIONS = re.compile(
    r"current_turn_is_readonly|ReadOnlyTurnError|READONLY_TOOL_REFUSAL"
)

_DETECTORS = {
    "primitiva": _WRITE_PRIMITIVES,
    "facciata": _INDIRECT_WRITES,
    "gate": _GATE_MENTIONS,
}

# Chi chiede allo scope per conto proprio. Ognuno con la ragione per cui la
# destinazione non passa da ``resolve_allowed_path``, che è anche la ragione per
# cui il cancello non lo vede.
_ASKS_FOR_ITSELF = {
    "agent/tools/download.py": "destinazione fissa: <installazione>/downloads/",
    "apps/storage.py": "app_dir viene dalla radice dell'installazione, non dal turno",
    "agent/tools/cron.py": "scrive cron/jobs.json, che non è un file dell'utente",
    "agent/tools/app_update.py": "installa un APK: non è una scrittura, è una sostituzione",
    "agent/tools/journal.py": (
        "appende al diario del progetto: non passa dai tool file, ha il suo gate"
    ),
    "agent/tools/micro_app.py": (
        "scrive il codice di una micro app in board/micro/, che i tool file non "
        "possono scrivere: ha il suo gate"
    ),
    # T4.6/T4.7: nessuna primitiva di scrittura, e per questo è stato invisibile
    # a questo file. Scrive ``metadata[goal_state]`` e chiama ``sessions.save``:
    # stato che sopravvive al turno, alla conversazione e al riavvio, e che cambia
    # il comportamento futuro (wall timeout, chip del goal, iniezione «keep
    # working»). Stessa famiglia di un job cron, stesso rifiuto.
    "agent/tools/long_task.py": "registra un goal sostenuto via sessions.save, non un path",
    # Stava in ``_OUT_OF_SCOPE`` con «scrive su una
    # macchina remota», ma ``ssh_transfer direction=down`` scrive **sul
    # telefono**: un subagent nato in sola lettura riscriveva ``SOUL.md`` da un
    # file remoto. La destinazione passa da ``resolve_allowed_path`` con
    # ``for_write`` spento, quindi il cancello dei tool file non la vede.
    # ``ssh_exec``/``ssh_job``/``up`` scrivono sul remoto e restano aperti.
    "agent/tools/ssh.py": (
        "ssh_transfer down scrive nel workspace locale; exec, job e up scrivono sul "
        "remoto, altro asse, e restano aperti"
    ),
}

# Chi scrive ma **non deve** chiedere, con la ragione. Non è una lista di
# perdoni: è la parte dell'inventario che dice *perché* la sola lettura non li
# riguarda, e va riletta quando uno di questi cambia mestiere.
_OUT_OF_SCOPE = {
    "agent/tools/filesystem.py": "è il cancello (`_resolve_write` → `_commit_write`)",
    "agent/tools/python_exec_builtins.py": "è il cancello (`_write_path`)",
    # T4.7. Il suo vero surface di scrittura non è citabile: sono i builtin
    # patchati (`open`, `io`, `os`, `shutil.rmtree`) del codice ospite, che non
    # compaiono come chiamate qui dentro. Nessuna primitiva testuale può vederlo
    # — lo vede `_GATE_MENTIONS`, ed è la ragione per cui quel detector esiste.
    "agent/tools/python_exec.py": (
        "è il cancello (`_guard_readonly_op` sui mutatori di os, più il ramo "
        "`for_write` che rifiuta prima del confine di path)"
    ),
    "agent/tools/apply_patch.py": "passa da `_resolve_write` dei tool file",
    "agent/tools/memory_entries.py": (
        "non è registrato fra i tool (nessun TOOLS): ci scrive solo Dream, "
        "che è una sessione interna e non ha un messaggio da cui leggere il flag"
    ),
    "agent/tools/ssh_jobs.py": "stato locale di un job ssh, non un file dell'utente",
    "agent/tools/ssh_transport.py": "stato locale del trasporto",
    # ── T4.7: i quattro scrittori del gateway, fuori scopo *per costruzione* ──
    #
    # Tutti e quattro per la stessa ragione, ed è la ragione scritta nel gate di
    # ``python_exec`` (``_guard_readonly_op``, "per il CODICE HOST è una no-op"):
    # in sola lettura l'interruttore deve proteggere la conversazione, non
    # romperla. Se questi si chiudessero, il turno che promette «non cambio
    # niente» perderebbe i messaggi dell'utente.
    "session/manager.py": (
        "è il magazzino delle sessioni: ci scrive il gateway per persistere la "
        "conversazione, non un tool su richiesta del modello. Chiuderlo perderebbe "
        "il turno stesso"
    ),
    "webui/transcript_store.py": (
        "il transcript è il verbale del turno, non un suo effetto: un turno in sola "
        "lettura va comunque registrato, o l'interruttore cancellerebbe la prova di "
        "cosa è stato chiesto"
    ),
    "agent/subagent_records.py": (
        "contabilità di ciclo di vita dei subagent (`subagents/records/*.jsonl`), "
        "scritta dal runtime e non dal modello. Il lavoro delegato *è* già coperto: "
        "`spawn` copia lo scope del turno nella spec e il run lo rilega, quindi un "
        "subagent nato in sola lettura è in sola lettura"
    ),
    "config/store.py": (
        "è il funnel della configurazione, e nessun tool lo chiama: i chiamanti sono "
        "le rotte /api delle Impostazioni, i comandi slash e il restore. Quelle sono "
        "azioni dell'utente, non del modello — e l'interruttore stesso vive lì"
    ),
}


def _strip_prose(src: str) -> str:
    """Il sorgente con commenti e docstring sostituiti da spazi.

    Le posizioni restano intatte (si cancella in loco, non si ricompone), così
    un numero di riga in un messaggio d'errore resta quello del file vero.

    Serve perché i detector cercano *codice*: ``python_exec.py`` contiene sei
    ``open(..., 'w')`` e sono tutti prosa che spiega il cancello. Una
    classificazione ottenuta da un commento è una casella spuntata da un
    commento, e domani quel commento si riscrive.
    """
    lines = src.splitlines(keepends=True)
    spans: list[tuple[int, int, int, int]] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            spans.append((*tok.start, *tok.end))
    for node in ast.walk(ast.parse(src)):
        for child in ast.iter_child_nodes(node):
            # Docstring o stringa nuda: una `Expr` il cui valore è una stringa.
            if (
                isinstance(child, ast.Expr)
                and isinstance(child.value, ast.Constant)
                and isinstance(child.value.value, str)
                and child.end_lineno is not None
                and child.end_col_offset is not None
            ):
                spans.append(
                    (child.lineno, child.col_offset, child.end_lineno, child.end_col_offset)
                )
    for row_start, col_start, row_end, col_end in spans:
        for row in range(row_start, row_end + 1):
            line = lines[row - 1]
            body = line.rstrip("\n")
            start = col_start if row == row_start else 0
            end = col_end if row == row_end else len(body)
            lines[row - 1] = line[:start] + " " * max(0, end - start) + line[end:]
    return "".join(lines)


def _sources() -> dict[str, str]:
    """Nome relativo a ``jenny/`` → sorgente ripulito dalla prosa.

    Le chiavi sono path e non basename: da T4.7 l'inventario guarda anche fuori
    da ``agent/tools/``, e lì un ``store.py`` o un ``manager.py`` da soli non
    dicono di chi si parla.
    """
    paths = [*sorted(TOOLS_DIR.glob("*.py")), *(ROOT / extra for extra in _EXTRA_SCANNED)]
    return {
        p.relative_to(ROOT).as_posix(): _strip_prose(p.read_text(encoding="utf-8"))
        for p in paths
    }


def _signals(src: str) -> set[str]:
    """Quali detector hanno visto qualcosa. Il nome serve al messaggio d'errore."""
    return {label for label, pattern in _DETECTORS.items() if pattern.search(src)}


# ── 1. Chi scrive, e da dove chiede ──────────────────────────────────────


def test_every_direct_writer_is_classified() -> None:
    """Un modulo che scrive e non compare in nessuno dei due elenchi è un buco.

    È il test che il passo 4 esiste per non dover rifare: senza, la promessa
    «non cambia niente sul telefono» resta vera solo finché qualcuno se la
    ricorda a mano.
    """
    known = set(_ASKS_FOR_ITSELF) | set(_OUT_OF_SCOPE)
    unclassified = {
        name: sorted(signals)
        for name, src in _sources().items()
        if name not in known and (signals := _signals(src))
    }
    assert not unclassified, (
        "questi moduli toccano una scrittura e non sono classificati: "
        f"{unclassified}. Decidi se in sola lettura devono chiudersi (aggiungili a "
        "_ASKS_FOR_ITSELF, chiama current_turn_is_readonly e scrivi la sonda in "
        "_PROBES) o no (_OUT_OF_SCOPE, con la ragione)"
    )


def test_the_ones_that_ask_really_ask() -> None:
    """Un nome nell'elenco non è una prova: la chiamata deve esserci.

    Controllo **strutturale e debole** — la prova vera è la sonda in fondo. Vale
    comunque perché il messaggio d'errore che dà è preciso (manca la chiamata,
    manca il rifiuto condiviso) dove quello di una sonda dice solo "non ha
    rifiutato".

    Le **due forme** del rifiuto sono entrambe buone, e la regola sta in
    ``workspace_access.py`` accanto a ``READONLY_TOOL_REFUSAL``: un cancello di
    *percorso* solleva ``ReadOnlyTurnError``, perché lì l'errore deve
    assomigliare a un errore di filesystem; un *tool* torna la frase condivisa,
    perché lì deve assomigliare a una risposta. Accettarne una sola costringerebbe
    il prossimo cancello a travestirsi.
    """
    sources = _sources()
    for name in sorted(_ASKS_FOR_ITSELF):
        src = sources[name]
        assert "current_turn_is_readonly" in src, f"{name} è nell'elenco ma non chiede allo scope"
        assert "READONLY_TOOL_REFUSAL" in src or "ReadOnlyTurnError" in src, (
            f"{name} deve usare una delle due forme condivise del rifiuto: la frase "
            "READONLY_TOOL_REFUSAL (un tool risponde) o ReadOnlyTurnError (un cancello "
            "di percorso sbaglia come sbaglia il filesystem). Cinque parafrasi della "
            "stessa regola la fanno sembrare cinque regole"
        )


def test_no_stale_entries() -> None:
    """Una riga morta in un elenco è peggio di una mancante: sembra copertura."""
    sources = _sources()
    for name in sorted(set(_ASKS_FOR_ITSELF) | set(_OUT_OF_SCOPE)):
        assert name in sources, f"{name} è negli elenchi ma non è più fra i file scansionati"
        assert _signals(sources[name]), (
            f"{name} è classificato ma nessun detector lo vede più scrivere: o ha "
            "cambiato mestiere (togli la riga) o scrive in un modo che i detector non "
            "conoscono (allarga il detector, non l'elenco)"
        )


# ── 1b. E il cancello serve a qualcosa? (T4.7) ───────────────────────────
#
# Il difetto che ha motivato questa sezione: rendere **irraggiungibile** il gate
# di ``journal.py`` — spostandolo sotto un `return` — lasciava verde
# ``test_the_ones_that_ask_really_ask``, perché la stringa c'era ancora. Una
# ricerca testuale non può distinguere un cancello da una riga morta, e
# sostituirne una con un'altra (un check AST di raggiungibilità, per esempio) non
# cambia la categoria del problema: resterebbe una lettura del testo.
#
# Quindi la prova è **eseguire**. Una sonda per voce di ``_ASKS_FOR_ITSELF``, e
# due asserzioni per sonda: in sola lettura rifiuta, e con la scrittura accesa
# **non** rifiuta. La seconda non è simmetria: senza, una sonda che rifiuta per
# un'altra ragione (nessun progetto, parametri sbagliati, tool disabilitato)
# passerebbe per copertura, che è esattamente il difetto di partenza travestito.
#
# **Cosa questo ancora non prende**, detto qui perché un guardiano che promette
# più di quel che fa è peggio di uno che non promette:
#
# * una sonda per modulo, e un modulo ha più ingressi. ``cron`` ha add/list/
#   remove, ``python_exec`` ne ha decine: se domani si aggiunge una seconda
#   scrittura accanto a una già coperta, la sonda continua a passare.
# * niente sulle voci di ``_OUT_OF_SCOPE``: per costruzione non hanno un gate da
#   provare, e la loro correttezza è la *ragione* scritta accanto — che nessun
#   test può verificare.
# * l'ordine fra il gate e la scrittura solo se la scrittura è osservabile. Dove
#   la sonda può, guarda anche l'effetto (nessun file, nessun job, nessun goal);
#   dove non può, un gate che rifiuta *dopo* aver scritto passerebbe.

# La frase per intero e non un frammento: una sonda che cerca "read-only" passerebbe
# anche su un rifiuto scritto a mano, che è la parafrasi che
# ``READONLY_TOOL_REFUSAL`` esiste per non avere.
_MARK = READONLY_TOOL_REFUSAL
_RAISED = "ReadOnlyTurnError: "

# Firma di una sonda: riceve una directory pulita e il modo del turno, esegue
# **la** scrittura del modulo e torna l'esito come testo.
Probe = Callable[[Path, bool], Awaitable[str]]


@contextmanager
def _turn(root: Path, readonly: bool):
    """Un turno legato su *root*, in sola lettura o no."""
    scope = build_workspace_scope(root, "restricted")
    with enter_workspace_scope(scope.without_write_access() if readonly else scope):
        yield


def _context(tool: Any, session_key: str = "unified:default") -> None:
    tool.set_context(
        RequestContext(channel="websocket", chat_id="default", session_key=session_key)
    )


async def _probe_download(root: Path, readonly: bool) -> str:
    # Il client è una rete finta: se qualcuno arrivasse a usarla il test
    # esploderebbe invece di uscire in rete. Con la scrittura accesa la sonda
    # muore prima, sulla validazione dell'URL (host inesistente) — che è già
    # oltre il cancello, ed è quel che serve dimostrare.
    client = MagicMock()
    client.get = MagicMock(side_effect=RuntimeError("nessuna rete nei test"))
    with _turn(root, readonly):
        out = await DownloadFileTool(str(root), client=client).execute(
            url="https://example.invalid/a.jpg"
        )
    if readonly:
        assert not (root / "downloads").exists(), "in sola lettura non nasce nemmeno la cartella"
    return out


async def _probe_app_storage(root: Path, readonly: bool) -> str:
    action = AppAction(name="add", description="", kind="storage", collection="todos", op="append")
    with _turn(root, readonly):
        result = await execute_storage_action(root, action, {"text": "x"})
    return repr(result)


class _FakeCron:
    """Servizio cron minimo: registra invece di programmare."""

    def __init__(self) -> None:
        self.added: list[dict[str, Any]] = []

    def add_job(self, **kwargs: Any) -> Any:
        self.added.append(kwargs)
        return SimpleNamespace(id="j1", name="probe", next_run=None)

    def list_jobs(self) -> list[Any]:
        return []

    def get_job(self, _id: str) -> None:
        return None

    def remove_job(self, _id: str) -> str:
        return "removed"


async def _probe_cron(root: Path, readonly: bool) -> str:
    service = _FakeCron()
    tool = CronTool(service, default_timezone="UTC")
    # Chat personale e non progetto: dentro un progetto parlerebbe prima la
    # regola del progetto, e la sonda misurerebbe quel rifiuto invece di questo.
    _context(tool)
    with _turn(root, readonly):
        out = await tool.execute(action="add", message="x", at="2026-09-01T09:00:00")
    if readonly:
        assert service.added == [], "in sola lettura nessun job deve nascere"
    return out


async def _probe_app_update(root: Path, readonly: bool) -> str:
    # ``confirm=False`` di proposito: con la scrittura accesa deve fermarsi al
    # rifiuto della conferma e non partire per davvero a installare un APK.
    with _turn(root, readonly):
        out = await InstallUpdateTool().execute(confirm=False)
    # Questo tool risponde in JSON, e ``json.dumps`` di default scappa il trattino
    # lungo della frase in ``—``: il rifiuto va letto dal campo, non dal filo,
    # o la sonda cercherebbe una stringa che l'encoding ha cambiato.
    return str(json.loads(out).get("detail", out))


async def _probe_journal(root: Path, readonly: bool) -> str:
    project = root / "wikis" / "viaggio"
    (project / "wiki").mkdir(parents=True)
    page = project / "raw" / "journal" / "20260822.md"
    with _turn(project, readonly):
        out = await JournalAppendTool(today=lambda: date(2026, 8, 22)).execute(text="un fatto")
    if readonly:
        assert not page.exists(), "in sola lettura la pagina di diario non deve nascere"
    return out


async def _probe_micro_app(root: Path, readonly: bool) -> str:
    from unittest.mock import patch

    from jenny.agent.tools.context import bind_request_context, reset_request_context
    from jenny.agent.tools.micro_app import MicroAppTool
    from jenny.board.store import mutate_board

    project = root / "wikis" / "orto"
    (project / "wiki").mkdir(parents=True)
    mutate_board(project, lambda b: b["cards"].append(
        {"id": "pomodori", "title": "Pomodori", "pages": [], "pos": {"x": 1, "y": 1}}))
    token = bind_request_context(RequestContext(
        channel="websocket", chat_id="project:orto", session_key="project:orto"))
    try:
        with _turn(project, readonly), patch("jenny.config.paths.get_workspace_path",
                                            lambda: root):
            out = await MicroAppTool().execute(
                action="draft", card="pomodori", title="Conta",
                fields={"n": {"label": "Quanti", "type": "number"}},
                code="def run(ctx):\n    return {'n': 3}\n")
    finally:
        reset_request_context(token)
    if readonly:
        assert not (project / "board" / "micro").exists(), "in sola lettura niente codice"
    return out


async def _probe_long_task(root: Path, readonly: bool) -> str:
    from jenny.session.goal_state import GOAL_STATE_KEY

    sessions = SessionManager(root)
    tool = LongTaskTool(sessions=sessions)
    _context(tool)
    with _turn(root, readonly):
        out = await tool.execute(goal="Track the whole migration")
    if readonly:
        assert GOAL_STATE_KEY not in sessions.get_or_create("unified:default").metadata, (
            "in sola lettura nessun goal deve restare registrato"
        )
    return out


class _NoNetworkTransfer:
    """``ssh_transfer`` senza rete: la risoluzione dell'host fallisce subito.

    Con la scrittura accesa la sonda muore lì, cioè oltre il cancello, che è
    quel che serve dimostrare; nessuna config vera viene letta.
    """

    @staticmethod
    def build(root: Path) -> Any:
        from jenny.agent.tools.ssh import SshTransferTool
        from jenny.agent.tools.ssh_backends.base import SshError

        class _Tool(SshTransferTool):
            def _resolve(self, alias: str) -> Any:
                raise SshError("nessuna rete nei test")

        return _Tool(workspace=root)


async def _probe_ssh(root: Path, readonly: bool) -> str:
    (root / "SOUL.md").write_text("io\n", encoding="utf-8")
    with _turn(root, readonly):
        out = await _NoNetworkTransfer.build(root).execute(
            host="lab", direction="down", local_path="SOUL.md", remote_path="/tmp/x",
        )
    assert (root / "SOUL.md").read_text(encoding="utf-8") == "io\n"
    return out


_PROBES: dict[str, Probe] = {
    "agent/tools/download.py": _probe_download,
    "apps/storage.py": _probe_app_storage,
    "agent/tools/cron.py": _probe_cron,
    "agent/tools/app_update.py": _probe_app_update,
    "agent/tools/journal.py": _probe_journal,
    "agent/tools/micro_app.py": _probe_micro_app,
    "agent/tools/long_task.py": _probe_long_task,
    "agent/tools/ssh.py": _probe_ssh,
}


async def _outcome(probe: Probe, root: Path, readonly: bool) -> str:
    """L'esito della sonda come testo, qualunque forma abbia il rifiuto.

    Un tool *risponde* la frase condivisa, un cancello di percorso *solleva*
    ``ReadOnlyTurnError``, e lo storage delle mini-app avvolge la frase in
    ``StorageError``. Tre forme, una domanda.
    """
    try:
        return await probe(root, readonly)
    except ReadOnlyTurnError as exc:
        return f"{_RAISED}{exc}"
    except Exception as exc:  # noqa: BLE001 — l'esito è il messaggio
        return f"{type(exc).__name__}: {exc}"


def test_every_gate_has_a_probe() -> None:
    """Chi entra in ``_ASKS_FOR_ITSELF`` porta con sé il modo di provarlo."""
    missing = sorted(set(_ASKS_FOR_ITSELF) - set(_PROBES))
    assert not missing, (
        f"nessuna sonda per {missing}: una voce senza sonda è verificata solo dalla "
        "presenza di una stringa, cioè dal difetto che questa sezione esiste per chiudere"
    )
    stale = sorted(set(_PROBES) - set(_ASKS_FOR_ITSELF))
    assert not stale, f"sonde per moduli che non sono più nell'elenco: {stale}"


@pytest.mark.parametrize("name", sorted(_PROBES))
async def test_the_gate_actually_refuses(name: str, tmp_path: Path) -> None:
    """Eseguita in sola lettura, la scrittura del modulo non passa.

    Un gate spostato sotto un ``return`` — irraggiungibile, stringa intatta —
    fallisce qui e non altrove.
    """
    out = await _outcome(_PROBES[name], tmp_path, True)
    assert _MARK in out or out.startswith(_RAISED), (
        f"{name} non si è rifiutato in sola lettura: {out!r}. La stringa del gate c'è "
        "(lo dice test_the_ones_that_ask_really_ask) ma la chiamata non chiude niente: "
        "cerca un return che le passa davanti"
    )


@pytest.mark.parametrize("name", sorted(_PROBES))
async def test_the_probe_is_not_refusing_for_another_reason(name: str, tmp_path: Path) -> None:
    """Con la scrittura accesa la stessa chiamata **non** deve rifiutare così.

    Senza questa, una sonda mal costruita (nessun progetto, tool disabilitato,
    parametri sbagliati) rifiuterebbe sempre e passerebbe per copertura — cioè lo
    stesso difetto di prima, spostato dal codice al test.
    """
    out = await _outcome(_PROBES[name], tmp_path, False)
    assert _MARK not in out and not out.startswith(_RAISED), (
        f"la sonda di {name} rifiuta anche con la scrittura accesa: {out!r}. Allora non "
        "sta misurando la sola lettura, e la sua conferma non vale niente"
    )


# ── 2. I mutatori di os ──────────────────────────────────────────────────

# Le voci delle tabelle che NON mutano: sonde ed enumeratori. Insieme a
# ``_OS_MUTATING_FUNCTIONS`` devono coprire le tabelle per intero, così un nome
# nuovo cade fuori da entrambi e questo file si rompe.
_OS_NON_MUTATING = frozenset({
    "getxattr", "listxattr",
    "listdir", "scandir", "walk", "fwalk",
    "stat", "lstat", "access", "readlink", "statvfs", "pathconf",
})


def _os_table_names() -> set[str]:
    return (
        {n for n, _, _ in PythonNamespace._OS_SINGLE_PATH_FUNCTIONS}
        | {n for n, _, _ in PythonNamespace._OS_TWO_PATH_FUNCTIONS}
    )


def test_every_os_entry_is_either_a_mutator_or_a_probe() -> None:
    """Aggiungere un mutatore alla tabella senza deciderlo rompe qui."""
    undecided = sorted(
        _os_table_names() - PythonNamespace._OS_MUTATING_FUNCTIONS - _OS_NON_MUTATING
    )
    assert not undecided, (
        f"voci di os non classificate: {undecided}. Se cambiano qualcosa vanno in "
        "_OS_MUTATING_FUNCTIONS (e si chiudono in sola lettura), altrimenti in "
        "_OS_NON_MUTATING qui sopra"
    )


def test_no_mutator_is_declared_that_the_tables_do_not_wrap() -> None:
    """Un nome dichiarato mutatore ma non wrappato è una riga che non chiude niente."""
    orphans = sorted(PythonNamespace._OS_MUTATING_FUNCTIONS - _os_table_names())
    assert not orphans, f"dichiarati mutatori ma non presenti nelle tabelle: {orphans}"


def test_the_two_sets_do_not_overlap() -> None:
    both = sorted(PythonNamespace._OS_MUTATING_FUNCTIONS & _OS_NON_MUTATING)
    assert not both, f"classificati due volte: {both}"


# ── 3. Le op delle mini-app ──────────────────────────────────────────────


def test_storage_ops_are_partitioned_and_query_stays_open() -> None:
    """In sola lettura una mini-app deve poter ancora *mostrare* i suoi dati."""
    assert _MUTATING_OPS | {"query"} == STORAGE_OPS, (
        "ogni op deve essere o mutante o la lettura: un'op nuova non classificata "
        f"passerebbe in sola lettura (mutanti={sorted(_MUTATING_OPS)}, tutte={sorted(STORAGE_OPS)})"
    )
    assert "query" not in _MUTATING_OPS
