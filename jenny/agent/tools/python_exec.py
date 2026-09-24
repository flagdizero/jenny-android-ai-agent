"""Python execution tool — replaces shell exec."""

from __future__ import annotations

import asyncio
import atexit
import builtins
import contextvars
import importlib
import importlib.util
import io
import logging
import os  # solo per os.fsdecode / os.sep / os.path.* — helper puri, mai patchati
import shutil
import sys
import threading
import traceback
import types
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import current_request_session_key
from jenny.agent.tools.exec_session import (
    DEFAULT_EXEC_SESSION_MANAGER,
    DEFAULT_YIELD_MS,
    MAX_OUTPUT_CHARS,
    MAX_YIELD_MS,
    _SessionStopped,
    clamp_session_int,
    format_result_line,
    format_session_poll,
)
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.agent.tools.schema import (
    ArraySchema,
    IntegerSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from jenny.config.paths import get_workspace_path
from jenny.config.tool_schemas import PythonExecConfig  # re-export (def in config.tool_schemas)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import guardrail for python_exec  (NB: NOT a security sandbox)
# ---------------------------------------------------------------------------
#
# IMPORTANT / TRUST BOUNDARY: python_exec runs arbitrary Python IN-PROCESS on
# the single Chaquopy interpreter. With `os`/`sys`/`shutil`/`httpx` in
# `allowed_modules`, there is NO in-process sandbox that resists a motivated
# adversary. The REAL containment is:
#   * the Android app sandbox (the app's own uid/permissions),
#   * the workspace path policy (filesystem reads and writes — see
#     security.workspace_policy),
#   * the SSRF policy (outbound network — see security.network).
# The allow/block module lists below are a USABILITY GUARDRAIL (they stop the
# agent from accidentally reaching for e.g. `subprocess`), NOT a containment
# control. Deployments that don't trust the model should disable python_exec
# via config (`tools.python_exec.enable = false`).
#
# `_guarded_import` (installed as `__builtins__['__import__']` in the guarded
# code's own namespace) enforces the allow/block lists on the attacker's
# top-level `import` statements, and guarded code is handed a proxy `sys`
# whose `.modules` is filtered (see `_GUARDED_SYS`).
#
# We deliberately do NOT patch `builtins.__import__` / `importlib` PROCESS-WIDE
# anymore: that mutated global interpreter state for the entire gateway
# process, was an unwinnable arms-race (guarded code can reach modules via
# `sys.modules`/`os.sys` regardless), and provided no real containment given
# `os`/`sys` are allowed. It only added a global-state hazard. Removed.
#
# THE READ-ONLY TURN IS ON THIS SIDE OF THE BOUNDARY TOO, and that is the half
# the older notes left out. The accepted open door is a raw thread reached
# through an allowed module's internals — `asyncio.base_events.threading.Thread`,
# `asyncio.futures.concurrent.futures.ThreadPoolExecutor` (see
# `TestKnownRemainingDoors` and the comment above `_guard_state_snapshot`) — and
# it bypasses BOTH halves of the turn's policy, not just the workspace path
# boundary: `_guarded_exec_is_active()` is thread-local and reads False on a
# thread we never patched, so `_refuse_write_if_readonly` becomes a no-op there.
# Measured 23/08/2026 through `PythonExecTool.execute`: inside a read-only turn,
# with `restrict_to_workspace` on and off alike, a raw thread's `open(p, 'w')`
# writes. The asyncio hops carry both halves across
# (`_carry_turn_across_thread`); a raw thread carries neither.
# Consequence for anything that *describes* the switch — the prompt block in
# `templates/agent/readonly.md`, `.agent/security.md`: the read-only turn is an
# instruction backed by tool refusals, and it must not be written up as a
# boundary that holds against code that goes looking for a way round it.

_import_guard_state = threading.local()


# ---------------------------------------------------------------------------
# Cattura di stdout/stderr PER THREAD (niente più lock globale)
# ---------------------------------------------------------------------------
#
# `PythonNamespace.execute()`/`call_function()` devono catturare ciò che il
# codice guardato stampa. L'esecuzione vera avviene su thread separati: una
# `python_exec` one-shot gira su un worker del pool dedicato (vedi
# `_python_exec_executor`) e ogni exec session di lunga durata
# (`exec_session.py::_PythonSession`) ha un proprio thread di background che
# richiama queste stesse due funzioni.
#
# COM'ERA, E PERCHÉ NON VA PIÙ BENE. La cattura usava
# `contextlib.redirect_stdout`/`redirect_stderr`, che mutano il
# `sys.stdout`/`sys.stderr` GLOBALE per la durata della chiamata. Due
# esecuzioni concorrenti si rubavano quindi l'output a vicenda, e la cura era
# un lock di processo (`_stdout_redirect_lock`) tenuto per TUTTA la finestra
# guardata — fino a 600 s per un one-shot, illimitato per una exec session.
# Quel lock è la causa di R9: il secondo exec si parcheggiava su
# `lock.acquire()` dentro un worker dell'executor, e `PyThreadState_SetAsyncExc`
# (il nostro unico modo di interrompere, vedi `_interrupt_thread`) non può
# raggiungere un thread fermo in un lock a livello C — arriva solo a un confine
# di bytecode. La coroutine tornava un ordinato "timed out" al modello e il
# thread restava bloccato per sempre, un worker in meno ogni volta.
#
# COM'È ORA. `sys.stdout`/`sys.stderr` vengono avvolti UNA VOLTA in un proxy
# che instrada la scrittura al buffer registrato PER IL THREAD CORRENTE, e allo
# stream reale per tutti gli altri. Nessuna mutazione globale per chiamata,
# quindi nessun bisogno di serializzare: due esecuzioni concorrenti hanno
# ciascuna il proprio buffer e non si vedono. Il proxy segue la stessa
# disciplina di ogni altro patch di questo file — montato in modo idempotente,
# mai smontato, e completamente trasparente per chi non ha un buffer sul
# proprio thread (il codice host scrive sullo stream vero, bit per bit).
#
# Il caso che il vecchio commento indicava come il pericolo — un
# `redirect_stdout` annidato che al `__exit__` ripristina il valore sbagliato —
# qui non esiste: nessuno tocca `sys.stdout`.
_stream_capture_state = threading.local()
_stream_proxy_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Lock della mutazione di `sys.path`
# ---------------------------------------------------------------------------
#
# `_enter_guard`/`_exit_guard` mettono la base di risoluzione (`working_dir`) in
# testa a `sys.path` per la durata dell'exec. `sys.path` è una lista GLOBALE e
# due exec possono essere contemporaneamente dentro enter/exit: una rimozione
# per indice (`sys.path.pop(0)`) toglierebbe la voce dell'altro. Da qui le due
# misure, entrambe necessarie e ora le SOLE (il lock di processo che
# serializzava l'intera finestra guardata non c'è più — vedi sopra):
#
#   1. inserimento e rimozione passano da questo lock, tenuto per il tempo di
#      una `list.insert`/`del`, non per la durata dell'esecuzione;
#   2. la rimozione è PER IDENTITÀ, perché `_enter_guard`/`_exit_guard` restano
#      invocabili anche fuori da `execute()` (test, futuri chiamanti) e una
#      `sys.path` lasciata sporca è un bug raro e sgradevolissimo da
#      diagnosticare.
#
# Quel che resta scoperto, ed è il prezzo onesto della concorrenza
# riguadagnata: due exec con `working_dir` diversi vedono ciascuno la voce
# dell'altro in `sys.path` per la durata della sovrapposizione. Entrambe le
# voci sono validate dentro il rispettivo workspace (vedi `_resolve_exec_base`),
# quindi non è un buco nel confine — è codice dell'agente che può importare un
# modulo dell'agente.
_sys_path_lock = threading.Lock()


class _ThreadRoutedStream:
    """Stream che instrada la scrittura al buffer del thread corrente.

    Avvolge lo stream reale (``real``) e consulta ``_stream_capture_state``:
    se il thread corrente ha registrato un buffer per questo slot
    (``"stdout"``/``"stderr"``) ci scrive dentro, altrimenti passa allo stream
    reale. È il sostituto per-thread di ``contextlib.redirect_stdout``, e come
    quello non tocca nient'altro: ogni attributo che non sia scrittura viene
    delegato al bersaglio corrente.
    """

    def __init__(self, real: Any, slot: str) -> None:
        self._jenny_real_stream = real
        self._jenny_slot = slot

    @property
    def _jenny_target(self) -> Any:
        buffer = getattr(_stream_capture_state, self._jenny_slot, None)
        return self._jenny_real_stream if buffer is None else buffer

    def write(self, data: Any) -> Any:
        return self._jenny_target.write(data)

    def writelines(self, lines: Any) -> Any:
        return self._jenny_target.writelines(lines)

    def flush(self) -> Any:
        flush = getattr(self._jenny_target, "flush", None)
        return flush() if flush is not None else None

    def __getattr__(self, name: str) -> Any:
        # I `_jenny_*` sono attributi d'istanza: se la ricerca normale è
        # fallita su uno di quelli siamo prima di `__init__` e delegare
        # rientrerebbe qui all'infinito.
        if name.startswith("_jenny_"):
            raise AttributeError(name)
        return getattr(self._jenny_target, name)


def _install_stream_proxies() -> None:
    """Monta i proxy per-thread su ``sys.stdout``/``sys.stderr``.

    Idempotente: se lo slot è già un proxy non fa nulla. Se qualcun altro ha
    sostituito lo stream nel frattempo, il nuovo proxy avvolge quello nuovo —
    stessa disciplina dei patch su ``os``/``io``.
    """
    with _stream_proxy_lock:
        for slot in ("stdout", "stderr"):
            current = getattr(sys, slot, None)
            if current is None or isinstance(current, _ThreadRoutedStream):
                continue
            setattr(sys, slot, _ThreadRoutedStream(current, slot))


@contextmanager
def _capture_streams(stdout_buf: Any, stderr_buf: Any) -> Iterator[None]:
    """Dirotta stdout/stderr del SOLO thread corrente nei buffer dati.

    Il ripristino salva e rimette il valore precedente invece di azzerare: i
    worker del pool sono riciclati e una cattura annidata (una `call_function`
    che ne richiama un'altra) non deve lasciare il thread senza buffer.

    Fallback: se per qualche ragione il proxy non è montabile (uno slot a
    ``None``, come in un interprete senza console) si ricade sul vecchio
    ``redirect_stdout`` globale — comportamento storico, output attribuito male
    solo in caso di concorrenza, che è comunque meglio di output perso.
    """
    _install_stream_proxies()
    previous_out = getattr(_stream_capture_state, "stdout", None)
    previous_err = getattr(_stream_capture_state, "stderr", None)
    _stream_capture_state.stdout = stdout_buf
    _stream_capture_state.stderr = stderr_buf
    try:
        if isinstance(sys.stdout, _ThreadRoutedStream) and isinstance(
            sys.stderr, _ThreadRoutedStream
        ):
            yield
        else:
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                yield
    finally:
        _stream_capture_state.stdout = previous_out
        _stream_capture_state.stderr = previous_err


# ---------------------------------------------------------------------------
# Pool dedicato di python_exec
# ---------------------------------------------------------------------------
#
# `run_in_executor(None, ...)` è l'executor di DEFAULT dell'event loop:
# `min(32, cpu+4)` worker (≈12 sul device) condivisi con le ~50 chiamate
# `asyncio.to_thread` sparse per `jenny/` — snapshot, backup, crypto, notifier,
# location, ricerca web, Telegram, cron. Un thread di python_exec che si
# incastra in una chiamata C (una `sleep` lunga, una `socket.recv`, un lock)
# non è interrompibile da `PyThreadState_SetAsyncExc`, e finché occupa un
# worker di quel pool lo toglie a tutti gli altri: dopo una manciata di exec
# incastrati il gateway resta in piedi — porta aperta, watchdog contento,
# heartbeat fresco — ma non fa più nulla, e solo un force-stop lo recupera.
#
# Il pool qui sotto sposta il danno dentro il tool: N worker, e quando sono
# tutti occupati è python_exec a mettersi in coda e a scadere con il proprio
# timeout, non il notifier. Piccolo di proposito — le exec session hanno già
# un thread ciascuna (vedi `exec_session._PythonSession`), quindi qui passano
# solo le chiamate one-shot, che sono serializzate per turno da
# `PythonExecTool.exclusive`.
_PYTHON_EXEC_MAX_WORKERS = 4
_exec_pool: Any = None
_exec_pool_lock = threading.Lock()


def _python_exec_executor() -> Any:
    """Executor dedicato alle esecuzioni one-shot (creato pigramente)."""
    global _exec_pool
    if _exec_pool is None:
        with _exec_pool_lock:
            if _exec_pool is None:
                _exec_pool = ThreadPoolExecutor(
                    max_workers=_PYTHON_EXEC_MAX_WORKERS,
                    thread_name_prefix="python_exec",
                )
                atexit.register(_shutdown_python_exec_executor)
    return _exec_pool


def _shutdown_python_exec_executor() -> None:
    """Chiude il pool senza aspettare: un worker incastrato non si aspetta."""
    global _exec_pool
    with _exec_pool_lock:
        pool, _exec_pool = _exec_pool, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def _active_guard_rules() -> tuple[frozenset[str], frozenset[str]] | None:
    """Return (allowed, blocked) module sets for the guard active on this
    thread, or None if no python_exec code is currently executing here."""
    return getattr(_import_guard_state, "rules", None)


# ---------------------------------------------------------------------------
# Rientranza dei wrapper di path
# ---------------------------------------------------------------------------
#
# I patch di path stanno su moduli GLOBALI (`os`, `io`, `builtins`, `shutil`),
# quindi un solo set di wrapper serve thread host, exec guardati ristretti ed
# exec guardati NON ristretti, eventualmente con workspace diversi. Il confine
# da applicare non può perciò vivere nella closure del namespace che ha
# installato il wrapper (sarebbe quello dell'ultimo namespace passato di lì):
# vive qui, thread-local, ed è scritto da `_enter_guard`. Nessun confine sul
# thread ⇒ passthrough totale.
#
# `bypass` è la rientranza: i wrapper chiamano `resolve_allowed_path`, che usa
# `Path.resolve()` → `os.path.realpath` → `os.lstat`. Senza il flag il wrapper
# di `lstat` richiamerebbe la policy che richiama `lstat`, all'infinito.
#
# `base` è la BASE DI RISOLUZIONE, deliberatamente SEPARATA da `boundary`:
#   * base     = da cosa si misura un percorso RELATIVO (il `working_dir`);
#   * boundary = cosa non si può oltrepassare (sempre la radice del workspace).
# Tenerle in due slot distinti è ciò che rende `working_dir` utile senza
# riaprire il buco che B4 ha appena chiuso: la base può muoversi dentro il
# workspace, il confine no. Stesso ciclo di vita del boundary (scritto da
# `_enter_guard`, azzerato da `_exit_guard`), stesso significato di `None`:
# nessuna base ⇒ si ricade sul boundary, cioè il comportamento storico.
_path_guard_state = threading.local()


@contextmanager
def _path_guard_bypass() -> Iterator[None]:
    """Sospende l'INTERO confine di path sul thread corrente.

    Non è un elenco da tenere aggiornato, ed è importante non leggerlo come
    tale: il bypass azzera `boundary` e `base` per il thread, quindi OGNI
    wrapper di questo file che si apre con ``_active_path_boundary()`` prende il
    ramo di passthrough — la superficie ``os``, ``io.open``, ``io.open_code``,
    ``io.FileIO``, ``builtins.open``, ``shutil.rmtree``, e qualunque wrapper
    aggiunto in futuro con la stessa forma. Una carve-out scritta contro
    l'insieme di ieri è esattamente come è nato il buco delle callback di
    ``rmtree``: si ragiona sul gate, non sulla lista.
    Restano invece attivi i blocchi che si reggono su ``_active_guard_rules()``
    (stub di evasione di ``os``, filtro di ``sys.modules``) e l'``open`` che
    vive nei builtins del namespace guardato (``_workspace_builtin_open``, che
    non consulta il thread-local e confina sempre).

    CODICE UTENTE DENTRO UNA FINESTRA DI BYPASS. Ci arriva: il rendering di un
    traceback chiama lo ``__str__`` di un'eccezione dell'agente, un ``__del__``
    può girare a qualunque allocazione, un ``__repr__`` durante il logging. Non
    è chiuso, ed è una scelta: ogni finestra è breve e serve alla policy stessa
    o al formatting, e ciò che si guadagna passandoci — leggere fuori dal
    workspace via ``pathlib``/``io`` — richiede di piazzare di proposito un
    ``__del__`` o uno ``__str__`` ostile. Vale il commento TRUST BOUNDARY in
    testa al file: contro un avversario in-process il confine non è questo.
    """
    previous = getattr(_path_guard_state, "bypass", False)
    _path_guard_state.bypass = True
    try:
        yield
    finally:
        _path_guard_state.bypass = previous


# ---------------------------------------------------------------------------
# "C'è un exec guardato su questo thread?" — NON è "c'è un confine di path"
# ---------------------------------------------------------------------------
#
# I due gate sono diversi e confonderli è stato un buco vero. Ogni wrapper di
# path di questo file si apre su `_active_path_boundary()`, che è `None` in TRE
# casi: codice host, finestra di bypass, **e un exec guardato senza
# `restrict_to_workspace`**. Per il confine di path quel terzo caso è
# giustamente un passthrough — non c'è nessuna radice da far rispettare — ma la
# SOLA LETTURA non è una questione di dove: è una proprietà del TURNO, e con
# `restrict_to_workspace` spento ogni rifiuto di scrittura veniva saltato
# insieme al confine (misurato: `os.remove`, `shutil.rmtree`, `open(..., 'w')`
# tutti riusciti durante un turno in sola lettura).
#
# Da qui il secondo gate. Il segnale "sta girando codice del modello su questo
# thread" esiste già ed è `_import_guard_state.rules`: lo scrive `_enter_guard`
# in ENTRAMBE le modalità, lo azzera `_exit_guard`, e `_carry_guard_state` lo
# usa esattamente così (`snapshot[4] is None` ⇒ nessun guard, callable
# invariata). Non serve un terzo pezzo di stato.
#
# PERCHÉ IL BYPASS SPEGNE ANCHE QUESTO. Dentro una finestra di bypass girano il
# macchinario della policy, il rendering dei traceback e il `logging` — e un
# handler su file apre in scrittura attraverso il `builtins.open` patchato. Se
# il rifiuto valesse anche lì, un turno in sola lettura non riuscirebbe più a
# *loggare* il proprio rifiuto. Le mutazioni vere sono già state controllate
# all'ingresso del wrapper, prima del bypass.
def _guarded_exec_is_active() -> bool:
    """True se su QUESTO thread sta girando codice del modello, guardato.

    Vale in entrambe le modalità di ``restrict_to_workspace``, al contrario di
    ``_active_path_boundary()``. Vedi il commento qui sopra per il perché i due
    gate non sono lo stesso gate.
    """
    if getattr(_path_guard_state, "bypass", False):
        return False
    return getattr(_import_guard_state, "rules", None) is not None


def _active_path_boundary() -> str | None:
    """Radice di workspace da far rispettare sul thread corrente, o None.

    None significa "passa dritto alla funzione reale", e i casi sono tre:
    codice host (nessun exec guardato qui — è il gate che evita la regressione
    Chaquopy descritta su ``_patch_os_open``), exec guardato senza
    ``restrict_to_workspace``, e una finestra di ``_path_guard_bypass()``
    (risoluzione della policy, logging, rendering di un traceback, discesa di
    ``shutil.rmtree``).
    """
    if getattr(_path_guard_state, "bypass", False):
        return None
    return getattr(_path_guard_state, "boundary", None)


# Caratteri di modo che rendono un ``open`` una scrittura. ``+`` incluso: ``r+``
# apre in lettura *e* scrittura, e vale come scrittura. Un modo non-stringa (o
# assente) e' lettura: e' il default di ``open``.
_WRITE_MODE_CHARS = frozenset("wxa+")


def _is_write_mode(mode: Any) -> bool:
    """True se questo ``open`` puo' modificare il file.

    Serve perche' il confine non e' lo stesso nei due versi: dentro una
    sessione-progetto si legge in tutto il workspace e si scrive solo nella
    cartella del progetto (v. ``_resolve_workspace_write``).
    """
    return isinstance(mode, str) and any(ch in _WRITE_MODE_CHARS for ch in mode)


def _active_path_base() -> str | None:
    """Base di risoluzione dei percorsi relativi sul thread corrente, o None.

    È il ``working_dir`` dell'exec in corso, già validato contro il confine.
    ``None`` significa "misura dal confine", cioè il comportamento di sempre:
    chi legge questa funzione deve sempre avere un fallback su
    ``_active_path_boundary()``.
    """
    if getattr(_path_guard_state, "bypass", False):
        return None
    return getattr(_path_guard_state, "base", None)


# ---------------------------------------------------------------------------
# La cwd RIPORTATA è la base di risoluzione — non una seconda risposta
# ---------------------------------------------------------------------------
#
# Un sandbox che risolve `open("x.txt")` contro una directory e ne NOMINA
# un'altra quando gli si chiede dove si trova non è un guardrail, è una
# trappola: ci si cade scrivendo codice del tutto ragionevole. Misurato sul
# device un'ora dopo aver reso `working_dir` reale — il modello ha scritto da sé
#
#     sys.path.insert(0, os.path.join(os.getcwd(), "skills", "waterbot", "scripts"))
#     import wb_probe
#
# dopo aver passato `working_dir=<workspace>`. `os.getcwd()` rispondeva `/`
# (la cwd del processo su Android), quindi la `join` produceva
# `/skills/waterbot/scripts`, che non esiste, e l'import moriva. Terza
# incarnazione dello stesso difetto sullo stesso device.
#
# Da qui la regola, e vale la pena scriverla come regola e non come elenco di
# funzioni: LA BASE DI RISOLUZIONE E LA WORKING DIRECTORY RIPORTATA SONO LA
# STESSA COSA. `os.getcwd`/`os.getcwdb` riportano quindi
# `_active_path_base()` se c'è, altrimenti il confine, altrimenti (codice host,
# exec non ristretto) la cwd vera.
#
# Sotto ci finisce da sola tutta l'aritmetica di percorso della stdlib:
# `os.path.abspath`, `os.path.realpath`, `Path.resolve()`, `Path.absolute()` e
# `Path.cwd()` passano tutte da `os.getcwd()` (o da `os.getcwdb()` per gli
# argomenti `bytes`) — verificato sul 3.11 di Chaquopy, non dedotto dal
# sorgente locale. Chiude perciò anche l'incoerenza schedata a parte dal review
# di Round 2: `os.path.abspath("data.txt")` rispondeva `/data.txt` mentre
# `open("data.txt")` leggeva dalla base, quindi il modello poteva CALCOLARE un
# percorso con una chiamata e vederselo RIFIUTARE dalla successiva.
#
# PERCHÉ SOLO SOTTO `restrict_to_workspace`. Senza confine i wrapper di path
# passano dritti e un `open("x.txt")` finisce davvero nella cwd del processo:
# lì riportare `working_dir` sarebbe la stessa trappola al contrario. Il
# `working_dir` di un exec non ristretto muove `sys.path`, non la risoluzione,
# e la cwd riportata deve continuare a dire la verità su quest'ultima. È anche
# ciò che tiene d'accordo `python_exec_builtins._resolution_base()`, che per la
# stessa ragione vale `None` fuori restrizione.
#
# PERCHÉ NON SEGUE `_path_guard_bypass()`. Ogni altro wrapper di questo file si
# apre su `_active_path_boundary()`, che sotto bypass è `None`: quel bypass
# esiste perché il macchinario di risoluzione non rientri nei wrapper che sta
# servendo. `getcwd` non prende percorsi, non chiama la policy e non può
# rientrare in niente — la ragione del bypass non la tocca. Se invece la
# seguisse, la risposta cambierebbe IDENTITÀ a metà risoluzione: una
# `Path("x").resolve()` eseguita dentro una finestra di bypass (il macchinario
# ne apre una a ogni risoluzione, e `traceback`/`logging`/`rmtree` pure)
# risponderebbe con la cwd del processo mentre la stessa riga fuori risponde
# con la base. Sarebbe la trappola di prima, spostata di un livello e più
# difficile da vedere. Quindi si leggono i thread-local GREZZI: dentro una
# finestra guardata la risposta è una sola, dall'inizio alla fine. Non è un
# rilassamento del confine — `getcwd` non concede nulla, riporta una directory
# che il chiamante ha comunque già passato lui.
def _reported_working_directory() -> str | None:
    """Directory che il codice guardato deve vedere come cwd, o ``None``.

    ``None`` significa "nessuna finestra guardata ristretta su questo thread":
    il wrapper delega alla ``os.getcwd`` reale e il codice host non vede
    differenza. Vedi il commento qui sopra per la scelta di ignorare
    ``_path_guard_bypass()``.
    """
    boundary = getattr(_path_guard_state, "boundary", None)
    if boundary is None:
        return None
    return str(getattr(_path_guard_state, "base", None) or boundary)


# ---------------------------------------------------------------------------
# Percorsi dell'INTERPRETE, esenti dal confine
# ---------------------------------------------------------------------------
#
# Il confine è un confine sui dati dell'utente, non sull'installazione di Python
# che lo esegue. Su Chaquopy quella distinzione non è teorica: la stdlib e i
# pacchetti vivono dentro gli `.imy` degli asset e i moduli con payload nativo
# (`.so`) vengono ESTRATTI su disco al primo import, dentro
# `<filesDir>/chaquopy/AssetFinder/...`, cioè fuori dal workspace. Letto dal
# bytecode dell'importer nell'APK (`assets/chaquopy/bootstrap.imy`,
# `java/android/importer.pyc`), un import a freddo di un modulo con `.so` fa,
# nell'ordine:
#
#   AssetZipFile.extract_if_changed : os.path.exists → os.stat
#   AssetZipFile.extract            : os.makedirs, NamedTemporaryFile
#                                     (io.open/os.open), copyfileobj, os.replace
#   AssetZipFile.extract_if_changed : os.utime
#   load_needed → get_needed        : builtins.open(<.so>, 'rb')  ← ELF parsing
#
# Sette funzioni patchate su quattro righe di codice. `hashlib`, `uuid`, `csv`,
# `unicodedata` e `xml.etree.ElementTree` sono tutti nell'allowlist di default e
# tutti hanno un payload nativo: senza questa esenzione un `import hashlib`
# dentro un exec ristretto fallisce su un interprete freddo. (Il commento su
# `_patch_os_open` registra la stessa classe di regressione già successa una
# volta con `_elementtree` al primo `import markdown`.)
#
# Cosa NON è questa esenzione: un secondo confine da tenere allineato a mano.
# I prefissi non sono scritti a mano — si leggono dall'interprete stesso
# (l'attributo `extract_root` degli AssetFinder di Chaquopy) UNA VOLTA SOLA, e
# lo snapshot è preso al primo ingresso nel guard, cioè prima che qualunque
# codice guardato abbia girato: non è esprimibile dal codice dell'agente.
# Fuori da Chaquopy (CI, desktop) l'insieme è vuoto e nulla cambia.
#
# L'invariante che lo rende sicuro: un prefisso è accettato SOLO se è disgiunto
# dal confine. Una radice di estrazione che contenesse il workspace (o vi
# stesse dentro) spegnerebbe il confine invece di aggirarlo, quindi viene
# scartata. Quel che resta è il costo onesto della scelta: dentro un exec
# ristretto il codice guardato può leggere e scrivere la directory di
# estrazione del runtime. Vale il commento TRUST BOUNDARY in testa al file —
# codice arbitrario in-process ha già l'esecuzione arbitraria; qui si compra
# `import hashlib` che funziona.
_runtime_prefixes_cache: tuple[str, ...] | None = None


def _discover_runtime_path_prefixes() -> tuple[str, ...]:
    """Directory che l'interprete possiede, lette dall'interprete stesso.

    Su Chaquopy sono le ``extract_root`` degli ``AssetFinder`` registrati in
    ``sys.meta_path`` / ``sys.path_importer_cache``. Altrove non c'è nulla da
    trovare e il risultato è vuoto.
    """
    found: list[str] = []
    finders: list[Any] = list(sys.meta_path)
    finders.extend(sys.path_importer_cache.values())
    for finder in finders:
        root = getattr(finder, "extract_root", None)
        if not isinstance(root, str) or not root:
            continue
        absolute = os.path.abspath(root)
        if absolute in ("", os.sep):
            continue
        if absolute not in found:
            found.append(absolute)
    return tuple(found)


def _runtime_path_prefixes() -> tuple[str, ...]:
    """Snapshot memoizzato di ``_discover_runtime_path_prefixes``.

    Calcolato pigramente al primo ingresso nel guard e mai più: a quel punto
    Chaquopy ha già inizializzato i suoi finder (è la macchina di import che ha
    caricato questo stesso modulo) e nessun codice guardato ha ancora girato.
    """
    global _runtime_prefixes_cache
    if _runtime_prefixes_cache is None:
        _runtime_prefixes_cache = _discover_runtime_path_prefixes()
    return _runtime_prefixes_cache


def _reset_runtime_path_prefixes() -> None:
    """Scarta lo snapshot (solo per i test: in produzione si calcola una volta)."""
    global _runtime_prefixes_cache
    _runtime_prefixes_cache = None


def _effective_runtime_prefixes(boundary: str | None) -> tuple[str, ...]:
    """Prefissi del runtime utilizzabili con *boundary*, cioè quelli DISGIUNTI.

    Un prefisso che contiene il confine spegnerebbe il confine; uno che ci sta
    dentro è ridondante. Entrambi vengono scartati: l'esenzione può solo
    aggiungere un sottoalbero estraneo, mai allargare quello protetto.
    """
    if not boundary:
        return ()
    root = os.path.abspath(boundary)
    usable = []
    for prefix in _runtime_path_prefixes():
        if _is_within_prefix(root, prefix) or _is_within_prefix(prefix, root):
            continue
        usable.append(prefix)
    return tuple(usable)


def _is_within_prefix(path: str, prefix: str) -> bool:
    """True se *path* è *prefix* o un suo discendente (confronto testuale)."""
    return path == prefix or path.startswith(prefix.rstrip(os.sep) + os.sep)


# Le sole operazioni per cui vale anche l'ANTENATO di un prefisso, e serve un
# motivo preciso: `os.makedirs(d)` sonda `os.path.exists(dirname(d))` PRIMA di
# creare, e se la sonda dice "no" ricorre su tutto il ramo fino a `/`, mkdir
# dopo mkdir, fino a fallire. Con `d` uguale alla radice di estrazione — il caso
# normale, i `.so` stanno in cima all'archivio — quel `dirname` sta appena
# SOPRA il prefisso. Concedere `stat`/`lstat` sugli antenati fa dire "sì" alla
# sonda e ferma la ricorsione lì. È l'esenzione più stretta che risolve il
# problema: nessuna mutazione e nessuna enumerazione sugli antenati, solo la
# risposta a "questa directory esiste".
_RUNTIME_ANCESTOR_OPS = frozenset({"os.stat", "os.lstat"})


def _is_runtime_path(logical_path: str, *, allow_ancestors: bool = False) -> bool:
    """True se *logical_path* (assoluto) è dentro un prefisso del runtime.

    Con ``allow_ancestors`` vale anche il contrario — il percorso CONTIENE un
    prefisso — per le sole operazioni in ``_RUNTIME_ANCESTOR_OPS``.
    """
    prefixes = getattr(_path_guard_state, "runtime_prefixes", ())
    for prefix in prefixes:
        if _is_within_prefix(logical_path, prefix):
            return True
        if allow_ancestors and _is_within_prefix(prefix, logical_path):
            return True
    return False


# ---------------------------------------------------------------------------
# Registro dei rifiuti — perché un rifiuto non diventi una risposta sbagliata
# ---------------------------------------------------------------------------
#
# `WorkspaceBoundaryError` è una `PermissionError`, ed è esattamente ciò che
# ogni enumeratore della stdlib ingoia: `glob._iterdir` ha `except OSError:
# return`, `pathlib._Selector._select_from` ha `except PermissionError:
# return`. Quindi `glob.glob('/sdcard/Download/*')` non solleva: torna `[]`. Il
# danno non è un buco nel confine — il confine ha retto — è una RISPOSTA
# SBAGLIATA all'utente, che chiede cosa c'è in Downloads e si sente dire che la
# cartella è vuota. `os.listdir` sullo stesso percorso rifiuta invece a voce
# alta: due API vicine, comportamenti opposti.
#
# Cambiare la classe base non è la strada: `except OSError` la riprenderebbe
# comunque in `glob`, e mezzo `jenny/` (filesystem, apply_patch, search, le
# route WebUI) intercetta `PermissionError`/`OSError` di proposito. Quindi il
# rifiuto viene ANNOTATO qui e riportato in coda a stderr dall'esecuzione, dove
# il modello lo legge insieme all'output.
#
# Annota solo `_guarded_os_path`, l'imbuto di tutta la superficie di path di
# `os` — e quindi anche degli enumeratori e delle sonde (`scandir`, `listdir`,
# `stat`, `lstat`, `walk`), che sono i rifiuti che la stdlib ingoia. La famiglia
# `open` non è annotata perché il suo rifiuto risale sempre al codice utente e
# finisce nel traceback che il modello vede già.
_MAX_REPORTED_REFUSALS = 5


def _record_boundary_refusal(op: str, path: str) -> None:
    """Registra un rifiuto del confine per il riepilogo di fine esecuzione."""
    if getattr(_path_guard_state, "boundary", None) is None:
        return
    _path_guard_state.refusal_count = getattr(_path_guard_state, "refusal_count", 0) + 1
    seen: list[str] = getattr(_path_guard_state, "refusals", None) or []
    entry = f"{op} {path}"
    if entry not in seen and len(seen) < _MAX_REPORTED_REFUSALS:
        seen.append(entry)
    _path_guard_state.refusals = seen


def _boundary_refusal_note() -> str:
    """Riepilogo dei rifiuti dell'esecuzione appena conclusa, o stringa vuota."""
    count = getattr(_path_guard_state, "refusal_count", 0)
    if not count:
        return ""
    seen: list[str] = getattr(_path_guard_state, "refusals", None) or []
    listed = "; ".join(seen)
    if count > len(seen):
        listed += f"; ... ({count - len(seen)} more)"
    return (
        f"WORKSPACE BOUNDARY: {count} path operation(s) refused during this execution: "
        f"{listed}. Note that glob.glob(), Path.glob() and os.fwalk() swallow this "
        "refusal and return an empty result, so an empty listing above may mean "
        "'refused', not 'empty'. This is a hard policy boundary, not a transient "
        "failure; do not retry with alternative tools, and ask the user how to "
        "proceed if the resource is genuinely required."
    )


# ---------------------------------------------------------------------------
# Moduli caricati dal `working_dir` — ombra su `sys.modules`
# ---------------------------------------------------------------------------
#
# `working_dir` finisce in testa a `sys.path` per la durata dell'exec, ed è
# esattamente ciò che serve perché una skill possa `import` i propri script
# (`skill-creator` scaffolda `scripts/*.py`). La voce di `sys.path` viene tolta
# per identità all'uscita, ma `sys.modules` è GLOBALE e non veniva toccato:
# un `import csv` dentro un exec con un `csv.py` accanto alla skill lasciava
# `sys.modules['csv']` puntato a quel file PER SEMPRE, e da lì in poi ogni
# `import csv` del gateway — traceback, WebUI, cron — riceveva la skill.
# Riproducibile in tre righe; misurato.
#
# Salva solo il caso fortunato: un nome GIÀ importato non è ombreggiabile,
# perché `sys.modules` vince sul finder. I nomi a rischio sono quindi tutti
# quelli non ancora caricati — `csv`, `types`, `token`, `copy`, `secrets`,
# `statistics`, `platform`, `queue`, e ogni pacchetto di terze parti importato
# pigramente. Su Chaquopy sono quasi tutti in quello stato: l'import è caro e
# `jenny/` importa dentro le funzioni quasi ovunque.
#
# LA SCELTA: scaricare all'uscita del guard tutto ciò che è stato caricato
# DALLA VOCE DI QUESTO EXEC (identificato dal `__file__`/`__path__` sotto la
# base), non solo i nomi che ombreggiano la stdlib — la stessa ombra vale per i
# pacchetti installati e per `jenny` stesso, e distinguere richiederebbe una
# seconda risoluzione per nome. Il prezzo è che un modulo di skill viene
# ri-eseguito a ogni exec e perde lo stato a livello di modulo. Verificato su
# tutti gli script reali in `jenny/skills/*/scripts/`: quello stato è fatto di
# regex compilate e costanti (`SLUG_RE`, `WIKILINK_RE`, `SEVERITY_ORDER`,
# `SKILL_TEMPLATE`), microsecondi da ricostruire; nessuna cache, connessione o
# risorsa costosa. Ed è comunque la semantica che l'autore si aspetta — un
# `python script.py` riparte da zero ogni volta.
#
# Le due alternative scartate. RIFIUTARE la collisione all'ingresso (scandire
# `working_dir` e negare i nomi che ombreggiano) è più economico ma vieta a una
# skill di avere un file di quel nome anche quando non lo importa mai, e non
# copre il caso di un import fatto dall'HOST mentre la voce è in `sys.path`.
# Un FINDER su `sys.meta_path` attivo per il solo thread guardato sarebbe più
# pulito in teoria, ma `sys.meta_path` è globale quanto `sys.modules` e
# comporta molta più macchina per lo stesso risultato osservabile.
#
# Resta scoperto, e va detto: se durante la finestra guardata un thread HOST
# fa il suo primo `import <nome ombreggiato>`, prende l'ombra e se ne tiene il
# riferimento nei propri globali. Lo scarico rimette a posto `sys.modules`, non
# quel riferimento. È la ragione per cui la nota qui sotto dice al modello di
# rinominare il file invece di limitarsi a segnalare l'accaduto.


def _module_lives_under(module: Any, prefix: str) -> bool:
    """True se *module* è stato caricato da un file sotto *prefix*."""
    origin = getattr(module, "__file__", None)
    if isinstance(origin, str) and origin:
        return _is_within_prefix(os.path.abspath(origin), prefix)
    # Package namespace: nessun `__file__`, solo le directory di `__path__`.
    paths = getattr(module, "__path__", None)
    try:
        entries = list(paths) if paths else []
    except TypeError:
        return False
    return any(
        isinstance(entry, str) and _is_within_prefix(os.path.abspath(entry), prefix)
        for entry in entries
    )


def _shadowed_modules_note() -> str:
    """Avviso sui moduli del ``working_dir`` che ombreggiano nomi di sistema."""
    names: list[str] = getattr(_path_guard_state, "shadowed_modules", None) or []
    if not names:
        return ""
    return (
        f"WORKING_DIR SHADOWING: {', '.join(names)} — working_dir contains a .py file "
        "whose name is also a standard-library module, so the import inside this "
        "execution resolved to the local file. The module was unloaded afterwards so "
        "the rest of the gateway keeps the real one, but rename the file: while it "
        "sits on sys.path any first-time import of that name anywhere in this process "
        "gets your file instead."
    )


def _with_exec_notes(stderr: str) -> str:
    """Accoda a *stderr* le note di fine esecuzione (rifiuti, ombre)."""
    notes = [note for note in (_boundary_refusal_note(), _shadowed_modules_note()) if note]
    if not notes:
        return stderr
    if stderr and not stderr.endswith("\n"):
        stderr += "\n"
    return stderr + "\n".join(notes) + "\n"


# ---------------------------------------------------------------------------
# Il confine è THREAD-LOCAL, e `asyncio` sa cambiare thread
# ---------------------------------------------------------------------------
#
# Ogni wrapper di path passa da `_active_path_boundary()`, che è un
# `threading.local()` scritto da `_enter_guard`. Su un thread dove
# `_enter_guard` non è passato la funzione ritorna `None` e ogni wrapper prende
# il ramo di passthrough — come gli stub di evasione di `os` e il filtro di
# `sys.modules`, che si reggono su `_active_guard_rules()` allo stesso modo.
# È stata una scelta deliberata (chiude la perdita per cui un namespace NON
# ristretto ereditava il workspace di uno ristretto) e non va disfatta.
#
# Il buco è che `asyncio` sta nell'allowlist di default — ce l'hanno messo
# perché il modello possa scrivere codice async — e `asyncio.to_thread` /
# `loop.run_in_executor` spediscono una callable su un ALTRO thread. Lì non
# c'è confine, in nessuna delle due direzioni, e non si logga niente:
#
#     await asyncio.to_thread(pathlib.Path('/fuori/segreto').read_text)   # riesce
#
# Misurato, non dedotto. Attenzione a un dettaglio che inganna:
# `asyncio.to_thread(open(p).read)` NON evade — `open(p)` è valutata subito,
# sul thread guardato, e viene rifiutata lì. Serve che l'apertura avvenga DOPO
# il salto: un `Path(...).read_text` non ancora chiamato, o una lambda. Che è
# esattamente come si scrive normalmente.
#
# LA SCELTA: riportare il TURNO attraverso il salto. Le due funzioni sono
# patchate come tutto il resto in questo file — a livello di processo, in modo
# idempotente e GUARD-GATED: se il thread chiamante non ha un guard attivo la
# callable viene restituita così com'è e il codice host non paga né cambia. Se
# ce l'ha, la callable passa da `_carry_turn_across_thread`, che INSTALLA lo
# stato del guard sul worker per la sola durata della chiamata e RIPRISTINA lo
# stato precedente in `finally` — anche in caso di eccezione, perché il worker è
# di un pool riciclato e lasciarcelo sopra sarebbe esattamente la perdita che
# `_enter_guard` cerca di evitare — E fa girare la callable dentro una copia del
# `contextvars.Context` del turno.
#
# QUELLA SECONDA METÀ MANCAVA, e per un anno il confine "teneva" mentre le due
# politiche del TURNO non attraversavano (T4.13, misurato il 23/08): un
# `await loop.run_in_executor(None, lambda: open(p, 'w').write(...))` scritto dal
# modello scriveva durante un turno in sola lettura e scriveva fuori dal
# progetto. Leggere `_carry_turn_across_thread` prima di aggiungere un salto:
# portarne una sola è precisamente il difetto, non un dettaglio di stile.
#
# Le alternative scartate. TOGLIERE `asyncio` dall'allowlist chiude il buco in
# una riga ma toglie una capability che il modello usa. ACCETTARE E DOCUMENTARE
# non regge: la forma che evade è quella idiomatica, non quella contorta.
#
# `asyncio` È L'UNICA PORTA? No, ed è giusto scriverlo. `threading` e
# `concurrent.futures` non sono nell'allowlist e `import threading` viene
# rifiutato — ma un modulo consentito che li importa al proprio interno li
# espone come attributi, e `_patch_sys_backreferences` sostituisce solo i
# riferimenti a `sys`. Misurate, entrambe evadono:
#
#     asyncio.base_events.threading.Thread(target=...)          # thread grezzo
#     asyncio.futures.concurrent.futures.ThreadPoolExecutor()   # pool grezzo
#
# E su un thread grezzo mancano ENTRAMBI i segnali, non solo il confine sui
# percorsi: `_guarded_exec_is_active()` e' thread-local, quindi la' e' False e
# `_refuse_write_if_readonly` diventa un no-op; e un thread nuovo parte con un
# `contextvars.Context` vuoto, quindi anche `current_turn_is_readonly()` legge
# il default. Cioe' togliere il gate thread-local non chiuderebbe la porta —
# misurato il 23/08 (T4.16).
#
# Non sono coperte qui, e non per dimenticanza: coprirle vorrebbe dire patchare
# `threading.Thread` a livello di processo, cioè mettersi in mezzo a OGNI
# thread del gateway per contenere codice che il modello non scrive mai per
# sbaglio. Vale il commento TRUST BOUNDARY in testa al file: contro un
# avversario motivato in-process non c'è confine che tenga, e quello che si
# compra qui è che il codice NORMALE — `await asyncio.to_thread(...)` dentro
# uno snippet async — resti dentro il confine invece di scavalcarlo in
# silenzio.
#
# Limite noto: i rifiuti registrati sul worker finiscono nel suo thread-local e
# non nel riepilogo di fine esecuzione (vedi `_record_boundary_refusal`). Il
# rifiuto arriva comunque al codice come `WorkspaceBoundaryError`.


def _guard_state_snapshot() -> tuple[Any, Any, Any, bool, Any]:
    """Stato del guard sul thread corrente, in forma trasportabile."""
    return (
        getattr(_path_guard_state, "boundary", None),
        getattr(_path_guard_state, "base", None),
        getattr(_path_guard_state, "runtime_prefixes", ()),
        getattr(_path_guard_state, "bypass", False),
        getattr(_import_guard_state, "rules", None),
    )


def _apply_guard_state(state: tuple[Any, Any, Any, bool, Any]) -> None:
    """Installa *state* sul thread corrente. Solo store: non può fallire."""
    boundary, base, prefixes, bypass, rules = state
    _path_guard_state.boundary = boundary
    _path_guard_state.base = base
    _path_guard_state.runtime_prefixes = prefixes
    _path_guard_state.bypass = bypass
    _import_guard_state.rules = rules


def _capture_snapshot() -> tuple[Any, Any]:
    """Buffer di cattura di stdout/stderr sul thread corrente."""
    return (
        getattr(_stream_capture_state, "stdout", None),
        getattr(_stream_capture_state, "stderr", None),
    )


def _apply_capture(state: tuple[Any, Any]) -> None:
    """Installa i buffer di cattura sul thread corrente. Solo store."""
    _stream_capture_state.stdout, _stream_capture_state.stderr = state


def _carry_turn_across_thread(func: Any) -> Any:
    """**L'unico ponte fra un thread e l'altro in questo stack.** Porta DUE metà.

    Un turno di ``python_exec`` tiene la propria politica in due posti, e i due
    posti sono giusti così — v. il commento su ``_path_guard_state`` e la nota di
    T4.2/T4.3: **non vanno unificati**, questa funzione unifica il *ponte* e non
    la memoria.

    1. **Thread-local** (``_path_guard_state`` / ``_import_guard_state``): il
       confine di percorso, la base, i prefissi di runtime, le regole di import.
       Sono per-thread perché i wrapper sono di processo, entrati da thread
       arbitrari, e ``bypass`` è protezione di rientranza *del thread che
       esegue*. Si copiano e si reinstallano, ripristinando in ``finally`` quel
       che il worker aveva: il pool è riciclato.
    2. **ContextVar** (il ``contextvars.Context`` del turno): la sola lettura
       (``current_turn_is_readonly``) e lo scope di scrittura
       (``current_tool_workspace``). Sono per-turno perché molti turni
       concorrenti condividono un thread. ``loop.run_in_executor`` **non** copia
       il contesto, quindi sul worker tornavano al proprio default.

    **Portarne una sola è il difetto di T4.3, e si è ripresentato un salto più in
    fuori.** Misurato il 23/08 attraverso ``await PythonExecTool.execute()``: un
    ``await loop.run_in_executor(None, lambda: open(p, 'w').write(...))`` scritto
    dal modello portava il thread-local (quindi il confine di percorso valeva) e
    perdeva il ContextVar — cioè scriveva durante un turno in **sola lettura**, e
    scriveva su ``SOUL.md`` da dentro una sessione-progetto. ``asyncio.to_thread``
    non era bucata, ma solo perché la ``to_thread`` della stdlib si copia il
    contesto da sé: una copertura per caso, non per costruzione.

    Viaggiano anche i buffer di cattura, e non è un extra: finché il redirect
    era globale, ciò che il worker stampava finiva comunque nell'output
    dell'exec. Con la cattura per-thread andrebbe perso, e un
    ``await asyncio.to_thread(qualcosa_che_stampa)`` diventerebbe muto per il
    modello. Due thread che scrivono nello stesso ``StringIO`` è esattamente
    quel che succedeva prima.

    **Va chiamata sul thread che parte, non sul worker**: è là che le due metà
    esistono ancora. Il contesto copiato è un oggetto nuovo a ogni chiamata,
    quindi ``Context.run`` non può trovarlo già entrato — nemmeno quando il
    codice del modello, già dentro una copia, ne fa un altro salto.
    """
    # `bypass` non si trasporta mai: il worker parte con il confine acceso.
    snapshot = _guard_state_snapshot()
    carried = (snapshot[0], snapshot[1], snapshot[2], False, snapshot[4])
    capture = _capture_snapshot()
    context = contextvars.copy_context()

    def _carried(*args: Any, **kwargs: Any) -> Any:
        previous_guard = _guard_state_snapshot()
        previous_capture = _capture_snapshot()
        _apply_guard_state(carried)
        _apply_capture(capture)
        try:
            return context.run(func, *args, **kwargs)
        finally:
            _apply_guard_state(previous_guard)
            _apply_capture(previous_capture)

    return _carried


def _carry_guard_state(func: Any) -> Any:
    """Il ponte, con il **gate** che rende inerti i patch globali.

    ``asyncio.to_thread`` e ``BaseEventLoop.run_in_executor`` sono patchate a
    livello di processo: le attraversa anche il gateway, non solo il codice del
    modello. Senza questo gate ogni salto in executor dell'host si porterebbe
    dietro il contesto del turno — un cambiamento di semantica per tutta
    l'applicazione, chiesto da nessuno — e pagherebbe una copia per niente.

    Il gate è "su QUESTO thread sta girando codice del modello, guardato", cioè
    le regole di import: sono l'unica delle due metà che ``_enter_guard`` mette
    in **entrambe** le modalità di ``restrict_to_workspace``, quindi è il
    predicato che non si spegne quando il confine di percorso non c'è — la
    stessa scelta, e per la stessa ragione, di ``_guarded_exec_is_active``.

    I due ponti interni allo stack — ``run_python_async`` e
    ``_ContextBoundNamespace`` — chiamano invece ``_carry_turn_across_thread``
    diretta: partono dal thread dell'event loop, dove un guard non c'è per
    definizione (lo monta il worker), e sono proprio loro a dover portare il
    contesto.
    """
    if getattr(_import_guard_state, "rules", None) is None:
        return func
    return _carry_turn_across_thread(func)


def _patch_asyncio_thread_hops() -> None:
    """Fa attraversare il guard a ``asyncio.to_thread``/``run_in_executor``.

    Idempotente (la funzione reale è su ``_jenny_real_fn``) e guard-gated
    dentro ``_carry_guard_state``. Vedi il commento qui sopra.
    """
    real_to_thread = getattr(asyncio.to_thread, "_jenny_real_fn", asyncio.to_thread)

    async def _guarded_to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        return await real_to_thread(_carry_guard_state(func), *args, **kwargs)

    _guarded_to_thread._jenny_real_fn = real_to_thread  # type: ignore[attr-defined]
    asyncio.to_thread = _guarded_to_thread  # type: ignore[assignment]

    loop_cls = getattr(asyncio.base_events, "BaseEventLoop", None)
    if loop_cls is None:  # pragma: no cover - nessun runtime noto senza
        return
    real_run_in_executor = getattr(
        loop_cls.run_in_executor, "_jenny_real_fn", loop_cls.run_in_executor
    )

    def _guarded_run_in_executor(self: Any, executor: Any, func: Any, *args: Any) -> Any:
        return real_run_in_executor(self, executor, _carry_guard_state(func), *args)

    _guarded_run_in_executor._jenny_real_fn = real_run_in_executor  # type: ignore[attr-defined]
    loop_cls.run_in_executor = _guarded_run_in_executor  # type: ignore[method-assign]


def _refuse_write_if_readonly(op: str) -> None:
    """Chiude una mutazione quando il turno è in sola lettura.

    Funzione di modulo perché la usano tutti i wrapper globali di questo file —
    ``os.open`` (solo con flag di scrittura), i mutatori di ``os``,
    ``os.symlink``, ``shutil.rmtree``, ``io.open``, ``io.FileIO``,
    ``builtins.open``. Import locale: a livello di modulo ``workspace_access``
    chiuderebbe un ciclo.

    **IL GATE STA QUI DENTRO, ed è ``_guarded_exec_is_active()``.** Due
    conseguenze, entrambe volute:

    * i chiamanti la invocano PRIMA del proprio ``if boundary is None``. La sola
      lettura non è una questione di *dove* si scrive, quindi non può stare
      dietro al gate del confine di path — che è spento anche per un exec
      guardato senza ``restrict_to_workspace``;
    * per il CODICE HOST è una no-op. Sono wrapper montati sul modulo globale e
      mai smontati: senza questo gate, un turno in sola lettura vedrebbe
      rifiutata anche la scrittura con cui il gateway persiste la propria
      sessione o il proprio transcript — cioè l'interruttore romperebbe la
      conversazione che dovrebbe soltanto proteggere.
    """
    if not _guarded_exec_is_active():
        return
    from jenny.security.workspace_access import current_turn_is_readonly
    from jenny.security.workspace_policy import ReadOnlyTurnError

    if current_turn_is_readonly():
        raise ReadOnlyTurnError(f"refused {op}")


# Flag di ``os.open`` che significano "questa apertura può cambiare il file".
# ``O_RDONLY`` è 0, quindi non c'è un bit da testare per la lettura: si guarda
# l'insieme dei bit di scrittura, ed è per questo che l'espressione è una
# maschera e non un confronto.
_OS_OPEN_WRITE_FLAGS = (
    os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
)


def _real_builtins_open() -> Any:
    """L'``open`` vero, saltando il wrapper globale se è già montato.

    Da usare in ogni punto che deve aprire un file DOPO aver già validato il
    percorso: `builtins.open` è patchato a livello di processo (vedi
    ``_patch_builtins_open``) e richiamarlo rifarebbe la risoluzione — inutile
    nel caso migliore, e una ricorsione nel wrapper nel peggiore.
    """
    return getattr(builtins.open, "_jenny_real_open", builtins.open)


class _GuardedSysModules:
    """Filtered, read-only view over the real `sys.modules` mapping.

    Only restricts anything while a PythonNamespace guard is active on the
    current thread; outside of that window it passes straight through to
    the real mapping so normal host code is unaffected.
    """

    def _permitted(self, name: str) -> bool:
        rules = _active_guard_rules()
        if rules is None:
            return True
        allowed, blocked = rules
        base = name.split(".", 1)[0]
        if base in blocked:
            return False
        if allowed and base not in allowed:
            return False
        return True

    def __getitem__(self, name: str):
        if _active_guard_rules() is None:
            # Nessun exec guardato su questo thread: niente da filtrare e
            # soprattutto niente da sostituire. Restituire `_GUARDED_SYS` al
            # posto del `sys` vero qui significherebbe consegnare il proxy al
            # codice host — vedi la nota su `_GuardedSysModule.modules`.
            return sys.modules[name]
        if not self._permitted(name):
            raise KeyError(f"Module '{name}' is not accessible in python_exec")
        if name.split(".", 1)[0] == "sys":
            return _GUARDED_SYS
        return sys.modules[name]

    def copy(self) -> dict[str, Any]:
        """Vista filtrata come dizionario vero.

        Non è un extra: ``inspect._signature_from_builtin`` fa
        ``sys.modules.copy()`` su ogni builtin ispezionato (Python 3.11, la
        versione del device). Senza questo metodo l'accesso finisce in
        ``AttributeError`` invece che in un rifiuto — un guasto, non una
        restrizione.
        """
        return {name: self[name] for name in self}

    def get(self, name: str, default: Any = None):
        try:
            return self[name]
        except KeyError:
            return default

    def __contains__(self, name: str) -> bool:
        return self._permitted(name) and name in sys.modules

    def __iter__(self):
        return iter(k for k in list(sys.modules) if self._permitted(k))

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def keys(self):
        return list(self)

    def items(self):
        return [(k, self[k]) for k in self]

    def values(self):
        return [self[k] for k in self]


class _GuardedSysModule:
    """Proxy for the `sys` module handed to guarded python_exec code.

    Transparently delegates everything to the real `sys` module except
    `.modules`, which is replaced by `_GuardedSysModules` so guarded code
    cannot use a plain `sys.modules[...]` lookup to reach a module outside
    its allowlist (this requires no import call, so it isn't covered by the
    `_guarded_import` hook installed in the guarded namespace).
    """

    _modules = _GuardedSysModules()

    @property
    def modules(self) -> Any:
        """Il dizionario VERO fuori da un exec guardato, il proxy dentro.

        Guard-gated come ogni altro patch di questo file, e per una ragione
        che non è teorica: ``_patch_sys_backreferences`` sostituisce
        ``os.sys``, ``posixpath.sys``, ``pathlib.sys``, ``warnings.sys`` &c.
        in modo PERMANENTE e per l'intero processo. Se questo attributo fosse
        una costante di classe, dopo il primo ``python_exec`` ogni riga di
        codice host che arriva a ``sys`` da uno di quei moduli riceverebbe per
        sempre un proxy in luogo del dizionario — e il proxy non è un dict:
        niente ``copy``, niente assegnazione, niente ``pop``. Misurato: sulla
        3.11 del device questo trasformava 298 test in errori con una sola
        causa (``'_GuardedSysModules' object has no attribute 'copy'``, da
        ``inspect.signature``). Valutare la proprietà al momento dell'accesso
        la rende anche corretta per thread, cosa che una costante di classe
        non poteva essere.
        """
        if _active_guard_rules() is None:
            return sys.modules
        return type(self)._modules

    def __getattr__(self, name: str) -> Any:
        return getattr(sys, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(sys, name, value)


_GUARDED_SYS = _GuardedSysModule()


# ---------------------------------------------------------------------------
# Isolated namespace
# ---------------------------------------------------------------------------

class PythonNamespace:
    """Controlled Python namespace for exec tool."""

    def __init__(
        self,
        working_dir: str | None = None,
        allowed_modules: list[str] | None = None,
        blocked_modules: list[str] | None = None,
        restrict_to_workspace: bool = False,
        workspace: str | None = None,
    ):
        self.working_dir = working_dir or str(get_workspace_path())
        self.allowed_modules = set(allowed_modules or [])
        self.blocked_modules = set(blocked_modules or [])
        self.restrict_to_workspace = restrict_to_workspace
        self.workspace = workspace or self.working_dir
        # Base di risoluzione richiesta per le prossime esecuzioni, o None per
        # "radice del workspace" (default storico). Volutamente NON derivata da
        # `self.working_dir`: quello è un attributo di comodo (lo mostra
        # `list_exec_sessions`) che di default vale la workspace globale del
        # processo, e usarlo come base romperebbe ogni namespace costruito con
        # un `workspace` diverso. La base la decide il chiamante, per chiamata.
        self.exec_base: str | None = None
        self._ns: dict[str, Any] = {
            "__builtins__": self._safe_builtins(),
            "__name__": "__python_exec__",
            "__file__": "<python_exec>",
        }

    # Dunder dei builtins che il namespace guardato deve comunque avere.
    # Il filtro `name.startswith("_")` qui sotto è ereditato e cieco: toglie in
    # blocco tutti i dunder di `builtins` (`__build_class__`, `__debug__`,
    # `__doc__`, `__loader__`, `__name__`, `__package__`, `__spec__`,
    # `__import__`), e di quelli uno serve davvero.
    #
    #   * `__build_class__` — è l'opcode LOAD_BUILD_CLASS a cercarlo nei
    #     builtins: senza, OGNI `class X: ...` muore con
    #     "NameError: __build_class__ not found", un dettaglio di
    #     implementazione che il modello non può collegare a "qui non puoi
    #     definire classi" — quindi riprova, riformula e brucia turni.
    #     Non allarga il confine di fiducia: `type(name, bases, ns)` (builtin
    #     a tre argomenti, mai filtrato) costruisce già le stesse classi,
    #     metaclassi e `__init_subclass__` inclusi. Vedi il commento
    #     TRUST BOUNDARY in testa al file: questo non è un sandbox di
    #     sicurezza.
    #
    # Gli altri restano fuori di proposito: `__name__`/`__doc__` li fornisce
    # già il namespace globale (`__python_exec__`), e `__loader__`/`__spec__`/
    # `__package__` sono i metadati d'import del modulo `builtins` — inutili
    # al codice guardato e un manico in più verso la macchina di import.
    # `__debug__` è una costante risolta a compile-time, non serve nei builtins.
    # `__import__` NON va in questa lista: è reinstallato sotto come
    # `_guarded_import` e passare dal loop lo riporterebbe a quello vero.
    _ALLOWED_DUNDER_BUILTINS = frozenset({"__build_class__"})

    @staticmethod
    def _compile(code: str, mode: str) -> types.CodeType:
        """Compila il codice guardato SENZA ereditare i `__future__` dell'host.

        `eval(code, ns)` / `exec(code, ns)` compilano ereditando i flag
        `__future__` del frame chiamante, e questo modulo apre con
        `from __future__ import annotations`: senza `dont_inherit` ogni
        snippet dell'agente veniva quindi compilato in PEP 563: le annotazioni
        restano stringhe. Conseguenza concreta su CPython 3.11 (la versione
        Chaquopy del device), riscontrata subito dietro il fix di
        `__build_class__`: `@dataclass` su un campo annotato entra nel ramo
        `isinstance(type, str)` di `dataclasses._process_class`, che fa
        `sys.modules.get(cls.__module__).__dict__` — e `__python_exec__` non è
        un modulo reale, quindi esplode con
        "AttributeError: 'NoneType' object has no attribute '__dict__'".
        Illeggibile per il modello, e `dataclasses` è nell'allowlist di default.
        Con `dont_inherit=True` il codice guardato ha la semantica standard
        dell'interprete.

        Il codice guardato può ancora scrivere il proprio `from __future__
        import annotations` (`_guarded_import` lo concede di proposito, vedi
        lì), ma così facendo RIPRODUCE il crash qui sopra: la causa non è il
        flag ereditato, è che `__name__` vale `__python_exec__` e non sta in
        `sys.modules`, quindi qualunque annotazione diventata stringa rimanda
        `dataclasses` sulla stessa `sys.modules.get(...).__dict__`. Verificato
        su 3.11 e 3.14. Non è un motivo per negare la direttiva — serve alle
        forward reference — ma non va suggerita come alternativa innocua.

        Il filename resta `<string>` per non cambiare la forma dei traceback
        già mostrati al modello.
        """
        return compile(code, "<string>", mode, dont_inherit=True)

    def _safe_builtins(self) -> dict[str, Any]:
        """Return a restricted set of builtins."""
        safe = {}
        blocked = {"exec", "eval", "compile", "__import__", "breakpoint", "exit", "quit"}
        for name in dir(builtins):
            if name in blocked:
                continue
            if name.startswith("_") and name not in self._ALLOWED_DUNDER_BUILTINS:
                continue
            safe[name] = getattr(builtins, name)
        # Re-add exec and eval for controlled use
        safe["exec"] = builtins.exec
        safe["eval"] = builtins.eval
        safe["__import__"] = self._guarded_import
        if self.restrict_to_workspace:
            # `open()` is the most-used file-I/O channel and the raw builtin
            # bypasses the workspace policy entirely (the registered helpers in
            # python_exec_builtins go through resolve_allowed_path, but a plain
            # `open("/outside", "w")` did not). Under restriction we hand the
            # guarded namespace a wrapper that resolves the path against the
            # workspace boundary. Questo copre il nome `open` visto dal codice
            # guardato; le altre vie allo stesso syscall hanno ciascuna il
            # proprio patch globale (`_patch_io_open` per `io.open`/`open_code`
            # e quindi pathlib, `_patch_io_fileio`, `_patch_builtins_open` per
            # l'`open` che risolvono i moduli della stdlib).
            safe["open"] = self._workspace_builtin_open
        return safe

    def _project_write_boundary(self) -> str | None:
        """La cartella entro cui questo turno puo' scrivere, se c'e' un confine.

        Non la calcola: la chiede a ``ToolWorkspace.write_root()``, che dal passo
        T4.4 e' l'unica risposta a «dove posso scrivere» (v.
        ``WorkspaceScope.write_root``). Qui resta la conversione a stringa —
        i wrapper di questo file ragionano su percorsi testuali — e il
        ``None``, che vuol dire "nessun confine da imporre da parte dello scope:
        chiamante, tieniti quello che avevi".

        ``None`` in due casi: nessuno scope legato **e** questa istanza non
        ristretta, oppure uno scope in modalita' ``full``. Con uno scope
        ristretto e' la sua cartella; senza scope ma con questa istanza ristretta
        e' il workspace del costruttore, che e' anche il confine che il thread ha
        gia' — quindi il chiamante non cambia niente.

        IL SECONDO ARGOMENTO ERA ``True`` FISSO, ED ERA UNA COINCIDENZA.
        Conta solo quando NON c'e' uno scope legato (con uno scope vince il suo
        ``restrict_to_workspace``), e reggeva su un fatto di un altro modulo:
        ``WorkspaceScopeResolver.for_project`` costruisce sempre ``restricted``.
        Un fatto vero e non dichiarato qui, cioe' il modo in cui un confine di
        sicurezza si allarga in silenzio quando quell'altro modulo cambia. Ora si
        passa la restrizione di **questa** istanza, che e' la stessa che decide se
        i wrapper di percorso hanno un confine da applicare
        (``_path_guard_state.boundary``) — quindi i due non possono divergere.

        Letto a ogni chiamata e non memorizzato: lo scope e' un ContextVar del
        turno, e questa istanza di tool e' condivisa fra sessioni.
        """
        from jenny.security.workspace_access import current_tool_workspace

        access = current_tool_workspace(
            self.workspace, restrict_to_workspace=self.restrict_to_workspace
        )
        root = access.write_root()
        return str(root) if root is not None else None

    def _mutation_boundary(self, boundary: str) -> str:
        """**L'unica regola di questo file su dove una mutazione puo' atterrare.**

        Stessa asimmetria di ``_resolve_workspace_write``, che qui mancava: i
        wrapper di ``os`` validavano tutto — cancellazioni comprese — contro
        ``self.workspace``, cioè la radice con cui il tool è stato COSTRUITO,
        senza mai consultare lo scope del turno. Due confini di scrittura che
        non si parlavano dentro lo stesso file: con uno scope su
        ``wikis/patreon`` e il tool costruito sulla radice,
        ``open('<ws>/SOUL.md', 'w')`` veniva rifiutata e
        ``os.remove('<ws>/SOUL.md')`` passava.

        **Non allarga mai.** Se il confine del tool è già più stretto dello
        scope (un subagent costruito sulla cartella del progetto) vince il
        confine: prendere lo scope a occhi chiusi lo riporterebbe alla radice.
        Confronto puramente testuale, senza I/O — entrambe le radici sono
        assolute e già risolte da chi le ha prodotte.

        **T4.14: la chiama anche ``_resolve_workspace_write``**, che prima
        prendeva la radice dello scope *senza* la clausola di non-allargamento.
        Le due formule sono state misurate su una matrice di 40 righe (radice del
        tool × restrizione × scope × ``boundary``): 36 identiche, e le 4 che
        divergevano sono tutte la stessa forma — tool costruito su una cartella
        di progetto **mentre e' legato lo scope personale**, dove questa clamp
        tiene la cartella e l'altra tornava alla radice dell'installazione.
        Quella forma non la produce nessuna costruzione dell'albero (v.
        ``tests/agent/tools/test_python_exec_one_write_rule.py``), quindi il
        cambio e' inerte oggi e vale per la terza costruzione che arrivera' —
        che e' esattamente com'e' nato il difetto di T4.2.
        """
        root = self._project_write_boundary()
        if root is None:
            return boundary
        current = os.path.normpath(boundary)
        scoped = os.path.normpath(root)
        if current == scoped or current.startswith(scoped.rstrip(os.sep) + os.sep):
            return boundary
        return root

    def _resolve_workspace_write(
        self,
        file: Any,
        *,
        boundary: str | None = None,
        base: str | None = None,
        for_write: bool = False,
    ) -> Any:
        """Resolve *file* against the workspace boundary; fds passano invariati.

        Shared by the builtin ``open`` wrapper and the ``io.open`` patch.
        Raises ``WorkspaceBoundaryError`` (an ``OSError`` subclass) with a
        clear "outside allowed directory" message when the path escapes the
        workspace — never an obscure failure.

        SUI DESCRITTORI INTERI. Qui prima si rifiutavano, e la motivazione
        scritta ("un fd non ha percorso da validare") descriveva un controllo di
        sicurezza che non è mai stato tale. Misurato: `os.read`, `os.write`,
        `os.dup`, `os.pipe`, `os.fstat` non prendono percorsi, quindi non sono
        wrappati e non sono bloccati — un ciclo di `os.dup(k)`/`os.read(d, n)`
        rilegge già oggi il contenuto di file che il processo host tiene aperti
        FUORI dal workspace. Tutto ciò che si raggiunge con `os.fdopen(fd)` si
        raggiunge quindi con `os.read(fd)`: il rifiuto costava a un attaccante
        una riga in più, e al codice legittimo un oggetto file.
        E non è rendibile un controllo: la provenienza di un int non è
        verificabile (il kernel ricicla i numeri dopo `close`, e `os.dup` li
        rilava), e su Android non esiste un fd→path portabile. Il confine sta al
        momento dell'`open`, non sui descrittori che ne escono.
        Restano rifiutati, e per un motivo diverso: `dir_fd` (la risoluzione
        relativa a un descrittore non passa MAI dalla policy) e gli int in
        ``_normalize_path_arg``/``os.open``, dove un intero SIGNIFICA semantica
        relativa a un fd. Qui invece un int significa "questo stream", ed è
        l'unica cosa che `os.fdopen(os.open(...))`, `open(fd, closefd=False)`,
        `io.FileIO(fd)` e `os.pipe()` possono voler dire. Non si può nemmeno
        togliere il ramo e basta: `str(5)` aprirebbe un file di nome "5".

        ``boundary`` è la radice da far rispettare: i patch globali passano
        quella del thread (``_active_path_boundary``), il wrapper builtin —
        che vive solo nel namespace di questa istanza — usa il default.
        ``base`` è la directory contro cui si misura un percorso RELATIVO
        (``working_dir``); assente, si misura dal confine come sempre.

        Tutta la risoluzione gira sotto ``_path_guard_bypass``: la policy passa
        da ``os.lstat``/``os.stat``, che sotto guard sono patchati, e senza il
        bypass una singola ``Path(...).read_text()`` fuori dal workspace
        produceva una raffica di WARNING "refused os.lstat" prima del rifiuto
        vero. Ora che anche ``builtins.open`` è patchato il bypass non è più
        solo igiene sui log: è ciò che impedisce al wrapper di rientrare in sé
        stesso attraverso il proprio macchinario.
        """
        from jenny.security.workspace_access import current_turn_is_readonly
        from jenny.security.workspace_policy import (
            ReadOnlyTurnError,
            _resolve_logical_path,
            resolve_allowed_path,
        )

        root = boundary or self.workspace
        if for_write:
            # Sola lettura: il terzo cancello. Qui passano ``open(..., 'w')``,
            # i patch di ``io``/``os`` e ``io.FileIO`` — cioe' ogni scrittura che
            # non usa i builtin del namespace. Il controllo sta prima della
            # risoluzione del confine perche' non e' una questione di *dove*:
            # non c'e' un percorso che vada bene.
            if current_turn_is_readonly():
                raise ReadOnlyTurnError(f"refused write to {file}")
            # **Il confine di scrittura non e' quello di lettura.** Dentro una
            # sessione-progetto si legge in tutto il workspace — la skill, le
            # altre wiki se l'utente le chiede — e si scrive solo nella cartella
            # del progetto. E' quella asimmetria a tenere un progetto lontano dai
            # file di un altro, da ``USER.md`` e da ``SOUL.md``; il lato lettura
            # non proteggeva niente, perche' fuori dalla directory privata
            # dell'app non si arriva comunque (nessun permesso di storage).
            #
            # **La regola sta in ``_mutation_boundary`` e solo la'** (T4.14).
            # Qui c'era una seconda copia — ``root = self._project_write_boundary()``
            # senza la clausola di non-allargamento — cioe' di nuovo due confini
            # di scrittura nello stesso file, che e' come e' nato T4.2.
            root = self._mutation_boundary(root)
        if isinstance(file, int):
            # Vedi il docstring: un fd non è validabile e non è un confine.
            return file
        with _path_guard_bypass():
            if _is_runtime_path(str(_resolve_logical_path(str(file), base or root))):
                # Percorso dell'interprete (vedi `_runtime_path_prefixes`):
                # passa invariato, così Chaquopy può estrarre e leggere i `.so`.
                return str(file)
            return resolve_allowed_path(
                str(file),
                workspace=base or root,
                allowed_root=root,
            )

    @staticmethod
    def _open_target(resolved: Any) -> Any:
        """Argomento da passare all'``open`` reale.

        ``_resolve_workspace_write`` restituisce un ``Path`` per i percorsi e
        l'``int`` invariato per i descrittori: stringere un fd aprirebbe un file
        di nome "5".
        """
        return resolved if isinstance(resolved, int) else str(resolved)

    def _workspace_builtin_open(self, file: Any, mode: str = "r", *args: Any, **kwargs: Any):
        """Workspace-contained replacement for the builtin ``open``.

        Installed in the guarded namespace only when ``restrict_to_workspace``
        is True; otherwise the raw builtin is used (behavior unchanged).
        """
        resolved = self._resolve_workspace_write(
            file, base=_active_path_base(), for_write=_is_write_mode(mode)
        )
        return _real_builtins_open()(self._open_target(resolved), mode, *args, **kwargs)

    # ------------------------------------------------------------------
    # Funzioni `os` bloccate — due insiemi, due momenti di installazione
    # ------------------------------------------------------------------
    #
    # La SUPERFICIE DI EVASIONE (processi, exec, privilegi, nodi speciali) si
    # installa all'ingresso di ogni guard, come il confine di path: legarla
    # all'``import os`` esplicito del codice guardato non bloccava niente,
    # perché `import shutil; shutil.os.system(...)` arriva alla stessa funzione
    # senza mai nominare `os` (stessa dinamica per cui B4 ha spostato i patch
    # di path in ``_enter_guard``).
    _OS_BLOCKED_ESCAPE_FUNCTIONS = frozenset({
        "system", "popen", "popen2", "popen3", "popen4",
        "execv", "execve", "execvp", "execvpe",
        "execl", "execle", "execlp", "execlpe",
        "spawnl", "spawnle", "spawnv", "spawnve", "spawnvp", "spawnvpe",
        "posix_spawn", "posix_spawnp",
        "fork", "forkpty",
        "kill", "killpg",
        "setuid", "setgid", "seteuid", "setegid", "setreuid", "setregid",
        "setsid", "setpgrp", "setpgid",
        "chroot", "chown", "lchown",
        "mkfifo", "mknod",
        "openpty", "login_tty",
    })

    # Lo STATO GLOBALE DEL PROCESSO: `chdir`/`fchdir` non sono una questione di
    # confine, sono una mutazione dell'host. La cwd è UNA per il processo e la
    # condividono gateway, cron, notifier e ogni thread: un `os.chdir` dentro un
    # exec guardato la sposta per tutti, e non la rimette a posto nessuno.
    # `shutil.make_archive(root_dir=...)` la chiama, quindi il blocco ha un
    # costo reale — il messaggio dice cosa fare invece. Installate con le
    # evasioni (all'ingresso del guard) per la stessa ragione: `shutil.os.chdir`
    # arriva alla funzione senza mai nominare `os`.
    #
    # RESTA BLOCCATA ANCHE ORA CHE `getcwd` RIPORTA LA BASE, e la domanda è
    # legittima: con una cwd onesta si potrebbe far spostare a `chdir` la sola
    # base thread-local, senza toccare la cwd del processo. Tre ragioni per non
    # farlo, in ordine di peso:
    #   1. sarebbe una `chdir` che non fa `chdir` — muove ciò che vede il thread
    #      guardato e lascia il resto del processo dov'era. Chi la chiama si
    #      aspetta l'una o l'altra, e la differenza si vede solo quando qualcosa
    #      è già andato storto;
    #   2. `sys.path` non la seguirebbe. La voce ci entra UNA VOLTA all'ingresso
    #      del guard e ne esce per identità (vedi `_push_exec_sys_path`): dopo
    #      una `chdir` la cwd riportata e la testa di `sys.path` direbbero due
    #      directory diverse. Cioè esattamente la classe di incoerenza che
    #      questo fix sta togliendo, reintrodotta da un'altra porta;
    #   3. una base per chiamata, passata come argomento, si ragiona; una base
    #      mutabile durante l'esecuzione va ricostruita leggendo il codice.
    # Il messaggio indica `working_dir=`, che è la forma che funziona.
    _OS_BLOCKED_HOST_STATE_FUNCTIONS = frozenset({"chdir", "fchdir"})

    _OS_BLOCKED_HOST_STATE_MESSAGE = (
        "os.chdir/os.fchdir are not available in python_exec: the working "
        "directory is process-global and shared with the gateway. Pass "
        "working_dir= to python_exec instead — it is both the base that "
        "relative paths resolve against and the directory os.getcwd() reports "
        "for this execution — or use absolute paths."
    )

    # Le SONDE D'AMBIENTE restano invece legate all'``import os`` esplicito, e
    # NON perché siano pericolose — nessuna delle tre concede una capability —
    # ma perché la stdlib le chiama per conto proprio, a tempo di import:
    #
    #   * ``register_at_fork`` — misurato su CPython 3.11 (la versione Chaquopy
    #     del device): la chiamano ``random`` (random.py), ``threading`` e
    #     ``logging`` al primo import. Stubbarla all'ingresso del guard
    #     significherebbe che il primo ``import`` che tira dentro uno di quei
    #     moduli dentro un exec guardato fallisce con un OSError incomprensibile
    #     (e ``tempfile`` importa ``random``, quindi il raggio è largo).
    #   * ``device_encoding`` — misurata anch'essa su 3.11 e su 3.14: NON è più
    #     sul percorso di costruzione di ``TextIOWrapper`` (il C di ``_io`` usa
    #     ``_Py_GetLocaleEncodingObject``), quindi spostarla sarebbe innocuo.
    #     Resta qui solo per non separarla dalle altre due sonde: non è un
    #     confine, e tenerla dov'era costa zero.
    #   * ``get_terminal_size`` — ``shutil.get_terminal_size`` la chiama dentro
    #     un ``except OSError``, quindi lo stub verrebbe ingoiato in silenzio.
    #
    # Onestà su cosa questa separazione ottiene davvero: il primo ``import os``
    # esplicito di un qualunque exec guardato monta le sonde sul modulo GLOBALE
    # e non le smonta più (come ogni altro patch qui dentro), quindi la finestra
    # si chiude comunque, prima o poi. Il punto non è eliminare il rischio: è
    # non ALLARGARLO. Questo è esattamente il comportamento che c'era prima
    # dell'evasione 2, e spostare le sonde in ``_enter_guard`` lo peggiorerebbe
    # — le renderebbe attive dal primissimo exec di ogni boot, senza chiudere
    # nulla, perché nessuna delle tre è una capability.
    _OS_BLOCKED_PROBE_FUNCTIONS = frozenset({
        "device_encoding", "get_terminal_size", "register_at_fork",
    })

    _OS_BLOCKED_FUNCTIONS = (
        _OS_BLOCKED_ESCAPE_FUNCTIONS
        | _OS_BLOCKED_HOST_STATE_FUNCTIONS
        | _OS_BLOCKED_PROBE_FUNCTIONS
    )

    def _resolves_within_workspace(self, name: str) -> bool:
        """True se *name* è importabile da un file dentro il workspace.

        Un modulo che si risolve in un file locale del workspace non concede
        alcuna capability che il codice inline non abbia già (è codice scritto
        dall'utente o dall'agente, allo stesso livello di fiducia di ciò che
        finirebbe direttamente in ``python_exec``). Importarlo è quindi sicuro
        quanto eseguirlo inline, e va consentito anche se il nome non compare
        nell'allowlist esplicita — il workspace è eseguibile per definizione.

        Il controllo è fatto sul modulo top-level (``base``) via ``find_spec``,
        che localizza senza eseguire: risolvere un sottomodulo dotted invece
        importerebbe il package padre come effetto collaterale.
        """
        base = name.split(".")[0]
        try:
            spec = importlib.util.find_spec(base)
        except (ImportError, AttributeError, ValueError, TypeError):
            return False
        origin = getattr(spec, "origin", None) if spec is not None else None
        if not origin or origin in ("built-in", "frozen", "namespace"):
            return False
        from jenny.security.workspace_policy import resolve_allowed_path
        # Bypass obbligatorio: `resolve_allowed_path` fa `Path.resolve()`, che
        # in `realpath` chiama `os.lstat` su OGNI prefisso del percorso. Sotto
        # guard quei prefissi (`/`, `/var`, …) stanno fuori dal workspace, il
        # wrapper li rifiuta a WARNING e `realpath` ingoia l'OSError e tira
        # dritto: il risultato era corretto ma ogni import fuori allowlist
        # sputava una decina di rifiuti su percorsi che non c'entravano nulla.
        with _path_guard_bypass():
            try:
                resolve_allowed_path(
                    origin,
                    workspace=self.workspace,
                    allowed_root=self.workspace,
                )
            except Exception:
                return False
        return True

    def _guarded_import(self, name: str, *args, **kwargs):
        """Import hook that blocks dangerous modules and patches os."""
        base = name.split(".")[0]
        # I moduli bloccati vincono sempre, anche se un file omonimo esiste nel
        # workspace: un `subprocess.py` locale non deve poter sbloccare l'import.
        if base in self.blocked_modules:
            raise ImportError(f"Module '{name}' is blocked in python_exec")
        if base == "__future__":
            # Sempre concesso, e non per generosità: `from __future__ import
            # annotations` è una direttiva del COMPILATORE, ma CPython emette
            # comunque l'import a runtime. Ora che `_compile` non eredita più i
            # future flag dell'host, quella riga è l'unico modo che il modello
            # ha di chiedere le annotazioni pigre (forward reference senza
            # virgolette) — negargliela renderebbe la direttiva inutilizzabile.
            # Il modulo espone solo oggetti `_Feature`: nessuna capability.
            return importlib.import_module("__future__")
        if self.allowed_modules and base not in self.allowed_modules:
            # Fuori allowlist ma risolvibile in un file del workspace → concesso
            # (workspace eseguibile). Altrimenti resta negato.
            if not self._resolves_within_workspace(name):
                raise ImportError(f"Module '{name}' is not in the allowed modules list")
        try:
            mod = importlib.import_module(name)
        except ImportError:
            raise ImportError(f"Module '{name}' is not available on this platform (blocked or missing)")
        if base == "sys":
            # Never hand out the real `sys` module: it carries the
            # unfiltered `sys.modules` table, which would let guarded code
            # reach any module already loaded in this process (including
            # `importlib`/`builtins`) with a plain dict lookup — no import
            # call involved, so nothing else in this guard would catch it.
            return _GUARDED_SYS
        if base == "os":
            self._patch_os_module(mod)
        if base in ("io", "pathlib") and self.restrict_to_workspace:
            # Ridondante ma tenuto: `_enter_guard` monta già `_patch_io_open`
            # all'ingresso di ogni exec ristretto, perché legarlo all'import
            # esplicito non copriva chi arriva a `io.open` attraverso un altro
            # modulo. Il patch è idempotente, quindi rifarlo qui costa niente e
            # tiene in piedi i chiamanti che usano il namespace senza guard.
            self._patch_io_open()
        self._patch_sys_backreferences(mod)
        return mod

    def _patch_sys_backreferences(self, mod: Any, _seen: set[int] | None = None, _depth: int = 0) -> None:
        """Neutralize direct references to the real `sys` module held by an
        allowed module (e.g. `os.sys`, `pathlib.sys`, `collections._sys`) —
        an artifact of that module doing `import sys` internally.

        These are just as reachable as `sys` itself (`import os;
        os.sys.modules[...]`) and would otherwise bypass the `_GUARDED_SYS`
        substitution above. The replacement is a fully attribute-transparent
        proxy, so the host module's own internal use of its `sys` reference
        keeps working normally.
        """
        if _depth > 2:
            return
        if _seen is None:
            _seen = set()
        if id(mod) in _seen:
            return
        _seen.add(id(mod))
        try:
            attrs = list(vars(mod).items())
        except TypeError:
            return
        for attr, val in attrs:
            if val is sys:
                try:
                    setattr(mod, attr, _GUARDED_SYS)
                except Exception:
                    continue
            elif isinstance(val, types.ModuleType) and val is not mod:
                self._patch_sys_backreferences(val, _seen, _depth + 1)

    _OS_BLOCKED_DEFAULT_MESSAGE = "This function is not available on this platform"

    @staticmethod
    def _install_os_blocked(
        mod: Any, names: frozenset[str], message: str | None = None
    ) -> None:
        """Sostituisce le funzioni ``os`` in *names* con stub d'errore.

        Come per ``os.open``/``io.open``, ``mod`` è il modulo ``os`` GLOBALE
        (condiviso col gateway) e il patch non viene mai ripristinato: gli stub
        sono quindi GUARD-GATED — bloccano solo mentre un exec guardato è attivo
        sul thread corrente (``_active_guard_rules()`` non è None) e delegano
        alla funzione reale per il codice host. Idempotente: la funzione reale è
        memorizzata una volta su ``_jenny_real_fn``, così le installazioni
        ripetute (una per ingresso nel guard) non impilano wrapper.

        *message* permette a un insieme di dire perché è bloccato (lo usa
        ``_OS_BLOCKED_HOST_STATE_FUNCTIONS``): "not available on this platform"
        è vero per le evasioni, ma per ``os.chdir`` sarebbe una bugia inutile.
        """
        text = message or PythonNamespace._OS_BLOCKED_DEFAULT_MESSAGE
        for fn in names:
            if not hasattr(mod, fn):
                continue
            current = getattr(mod, fn)
            real = getattr(current, "_jenny_real_fn", current)

            def _blocked(*_a: Any, _real: Any = real, _text: str = text, **_kw: Any) -> Any:
                if _active_guard_rules() is None:
                    # Codice host (nessun exec guardato su questo thread): intatto.
                    return _real(*_a, **_kw)
                raise OSError(_text)

            _blocked._jenny_real_fn = real  # type: ignore[attr-defined]
            setattr(mod, fn, _blocked)

    def _patch_os_module(self, mod: Any) -> None:
        """Patch completo di ``os`` all'``import os`` esplicito del codice guardato.

        L'unica cosa che si monta SOLO qui sono le sonde d'ambiente (vedi
        ``_OS_BLOCKED_PROBE_FUNCTIONS``), che non possono stare in
        ``_enter_guard`` senza rompere gli import della stdlib. Tutto il resto —
        evasioni, stato globale del processo, e sotto restrizione l'intero
        confine di path — ``_enter_guard`` l'ha già montato: qui si rimonta, e
        va bene perché ogni patch di questo file è idempotente (la funzione
        reale è su ``_jenny_real_fn``). Serve ai chiamanti che usano il
        namespace senza passare da ``_enter_guard``.
        """
        self._install_os_blocked(mod, self._OS_BLOCKED_ESCAPE_FUNCTIONS)
        self._install_os_blocked(
            mod,
            self._OS_BLOCKED_HOST_STATE_FUNCTIONS,
            self._OS_BLOCKED_HOST_STATE_MESSAGE,
        )
        self._install_os_blocked(mod, self._OS_BLOCKED_PROBE_FUNCTIONS)

        if self.restrict_to_workspace:
            self._patch_os_workspace_boundary(mod)

    def _patch_os_workspace_boundary(self, mod: Any) -> None:
        """Installa TUTTO il confine di workspace sul modulo ``os`` globale.

        Separato da ``_patch_os_module`` perché ``_enter_guard`` deve poter
        installare il confine di path senza montare anche le sonde d'ambiente
        (``_OS_BLOCKED_PROBE_FUNCTIONS``), che restano legate all'``import os``
        esplicito del codice guardato per non rompere gli import della stdlib.

        ``_patch_os_getcwd`` viaggia con loro pur non essendo un confine: non
        rifiuta niente, fa dire alla cwd la stessa directory contro cui il
        confine risolve i percorsi relativi. Sta qui perché ha esattamente la
        stessa condizione di attivazione (una finestra guardata RISTRETTA: senza
        confine nulla viene deviato e non c'è nulla da riportare di diverso) e
        perché separarlo vorrebbe dire poter montare l'uno senza l'altro, che è
        la definizione del difetto.
        """
        self._patch_os_open(mod)
        self._patch_os_path_surface(mod)
        self._patch_os_getcwd(mod)

    def _patch_os_getcwd(self, mod: Any) -> None:
        """Fa riportare a ``os.getcwd``/``getcwdb`` la base di risoluzione.

        Vedi il commento su ``_reported_working_directory``: la base e la cwd
        riportata devono essere la stessa directory, altrimenti il modello
        calcola un percorso con ``os.path.join(os.getcwd(), ...)`` e se lo vede
        rifiutare da ``open``.

        Stessa disciplina di ogni altro patch di questo file: si tocca il modulo
        ``os`` GLOBALE (condiviso col gateway), la funzione reale è memorizzata
        una volta su ``_jenny_real_fn`` (idempotenza) e il wrapper è GUARD-GATED
        — fuori da una finestra guardata ristretta
        ``_reported_working_directory()`` è ``None`` e si delega alla funzione
        reale, bit per bit. Il gate non è una precauzione teorica: un patch
        ungated su questa superficie ha già rotto una volta il ``tempfile`` con
        cui Chaquopy estrae le ``.so`` native (vedi ``_patch_os_open``).
        """
        real_getcwd = self._real_os_fn(mod, "getcwd")
        if real_getcwd is not None:

            def _guarded_getcwd() -> str:
                reported = _reported_working_directory()
                return real_getcwd() if reported is None else reported

            self._install_os_wrapper(mod, "getcwd", _guarded_getcwd, real_getcwd)

        real_getcwdb = self._real_os_fn(mod, "getcwdb")
        if real_getcwdb is not None:

            def _guarded_getcwdb() -> bytes:
                # `os.path.abspath(b"rel")` passa da qui, non da `getcwd`.
                reported = _reported_working_directory()
                return real_getcwdb() if reported is None else os.fsencode(reported)

            self._install_os_wrapper(mod, "getcwdb", _guarded_getcwdb, real_getcwdb)

    def _patch_os_open(self, mod: Any) -> None:
        """Confina ``os.open`` dentro il workspace, ma solo per codice guardato.

        ``mod`` è il modulo ``os`` GLOBALE, condiviso col gateway. Come ogni
        altro wrapper di path di questo file, il gate è
        ``_active_path_boundary()``: si applica il confine solo quando il thread
        corrente ne ha uno, e altrimenti si passa dritti al vero ``os.open``.
        Tre casi cadono nel passthrough, ed è bene averli tutti in testa: codice
        host, exec guardato senza ``restrict_to_workspace``, **e una finestra di
        ``_path_guard_bypass()``** — sì, il bypass sospende anche questa
        funzione (vedi ``_path_guard_bypass``). Senza il gate il patch — mai
        ripristinato — resterebbe sul modulo globale e romperebbe l'``os.open``
        del gateway: p.es. la
        stdlib ``tempfile`` usata da Chaquopy per estrarre le ``.so`` native
        (``_elementtree`` al primo ``import markdown`` della tab Wiki) verrebbe
        rifiutata perché il file temporaneo è fuori dal workspace.
        Idempotente: il vero opener è memorizzato su ``_jenny_real_open``.
        """
        from jenny.security.workspace_policy import _resolve_logical_path, resolve_allowed_path

        real_open = getattr(mod.open, "_jenny_real_open", mod.open)

        def _workspace_open(path: str | bytes | int, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
            # In sola lettura si chiude solo l'apertura che PUÒ cambiare il
            # file: `os.open(p, os.O_RDONLY)` deve continuare a funzionare, o
            # l'interruttore impedirebbe anche di leggere.
            # PRIMA del gate del confine, non dopo: vedi
            # `_refuse_write_if_readonly`.
            if flags & _OS_OPEN_WRITE_FLAGS:
                _refuse_write_if_readonly("os.open for writing")
            boundary = _active_path_boundary()
            if boundary is None:
                # Codice host (nessun exec guardato su questo thread): intatto.
                return real_open(path, flags, mode, dir_fd=dir_fd)
            if isinstance(path, int):
                raise OSError("os.open with file descriptor is not allowed")
            if dir_fd is not None:
                raise OSError("os.open with dir_fd is not allowed")
            if flags & _OS_OPEN_WRITE_FLAGS:
                # Un'apertura in scrittura è una mutazione: confine dello scope.
                boundary = self._mutation_boundary(boundary)
            # La base si legge PRIMA del bypass: sotto bypass
            # `_active_path_base()` ritorna None per costruzione, e leggerla
            # dentro riporterebbe la base al confine, rendendo `working_dir`
            # muto proprio qui.
            anchor = _active_path_base() or boundary
            # Bypass obbligatorio, come in ogni altro punto di risoluzione
            # (vedi `_resolves_within_workspace`, `_guarded_os_path`,
            # `_resolve_exec_base`): `resolve_allowed_path` fa `Path.resolve()`
            # → `realpath` → `os.lstat` su OGNI antenato del percorso, e sotto
            # guard quegli antenati stanno fuori dal workspace. Il wrapper di
            # `lstat` li rifiuta a WARNING e `realpath` ingoia l'OSError e tira
            # dritto: una singola `os.open('a.txt')` LEGITTIMA sputava decine
            # di righe di rifiuto nell'output del tool (e ~1400 lstat), e il
            # modello concludeva di aver fallito una chiamata riuscita.
            with _path_guard_bypass():
                logical = str(_resolve_logical_path(str(path), anchor))
                if _is_runtime_path(logical):
                    # Percorso dell'interprete: vedi `_runtime_path_prefixes`.
                    # È questo ramo che permette a Chaquopy di creare il file
                    # temporaneo con cui estrae una `.so` al primo import.
                    return real_open(logical, flags, mode)
                resolved = resolve_allowed_path(
                    str(path),
                    workspace=anchor,
                    allowed_root=boundary,
                )
            return real_open(str(resolved), flags, mode)

        _workspace_open._jenny_real_open = real_open  # type: ignore[attr-defined]
        mod.open = _workspace_open

    # ------------------------------------------------------------------
    # Superficie path-taking di `os`
    # ------------------------------------------------------------------
    #
    # `os.open` da solo non è il confine: con `restrict_to_workspace` il codice
    # guardato non poteva LEGGERE fuori dal workspace ma poteva cancellare,
    # rinominare, troncare ed ENUMERARE tutto ciò che l'uid dell'app raggiunge
    # (`workspace/config.json`, `sessions/`, `jenny_src/`, lo snapshot store).
    # Le tabelle qui sotto sono la parte tabellare della chiusura di quel buco —
    # mutatori a uno e a due percorsi, enumeratori, sonde; l'enumerazione è
    # inclusa perché è esattamente ciò che permette di mappare la directory
    # privata. Il resto del confine NON sta in una tabella e va cercato altrove
    # prima di concludere che qualcosa è scoperto: `os.symlink`
    # (`_patch_os_symlink`, il target si ancora alla directory del link),
    # `shutil.rmtree` (`_patch_shutil_rmtree`, che i wrapper non possono coprire
    # perché lavora su descrittori) e tutta la famiglia `open`
    # (`_patch_io_open`, `_patch_io_fileio`, `_patch_builtins_open`).
    #
    # COSA RESTA APERTO, di proposito e non per dimenticanza: i descrittori
    # interi (subito qui sotto), `threading`/`concurrent.futures` raggiunti come
    # attributi di un modulo consentito (vedi il commento su
    # `_patch_asyncio_thread_hops`) e il codice utente che gira dentro una
    # finestra di `_path_guard_bypass()` (vedi lì). Sono tutte cose che vanno
    # scritte apposta: nessuna capita per sbaglio a un modello che lavora.
    #
    # Formato: (nome della funzione, nome del parametro path, valore quando è
    # omesso — None significa "nessun default, lascia sollevare il TypeError
    # nativo").

    _OS_SINGLE_PATH_FUNCTIONS: tuple[tuple[str, str, str | None], ...] = (
        # Mutatori a un percorso.
        ("remove", "path", None),
        ("unlink", "path", None),
        ("rmdir", "path", None),
        ("mkdir", "path", None),
        ("makedirs", "name", None),
        ("truncate", "path", None),
        ("chmod", "path", None),
        # `utime` era l'unico mutatore rimasto scoperto, ed è raggiungibile per
        # sbaglio: `pathlib.Path.touch()` la chiama PER PRIMA e ritorna se il
        # file esiste già, quindi `Path('/fuori/file').touch()` cambiava un
        # mtime senza incontrare nessun rifiuto (il caso "non esiste" cadeva
        # invece sull'`os.open` patchato). Stessa classe di `chmod`, che era
        # guardata: la differenza era una dimenticanza, non una scelta.
        ("utime", "path", None),
        # xattr: `set`/`remove` sono mutazioni, `get`/`list` sono informazione.
        # Guardate tutte e quattro perché `stat` — informazione anche lei — è
        # guardata: la coerenza qui costa quattro righe e nessuna chiamata
        # calda le attraversa.
        ("setxattr", "path", None),
        ("removexattr", "path", None),
        ("getxattr", "path", None),
        ("listxattr", "path", "."),
        # Enumeratori.
        ("listdir", "path", "."),
        ("scandir", "path", "."),
        ("walk", "top", None),
        # `fwalk` non esiste su tutti i platform (l'installazione la salta se
        # manca). Sotto guard resta comunque poco utile: la sua discesa usa
        # `os.open(..., dir_fd=...)` e `os.scandir(<fd>)`, che i wrapper
        # rifiutano, e lei ingoia l'errore via `onerror`. La includiamo perché
        # almeno la radice fuori dal workspace venga rifiutata a voce alta,
        # come per `walk`, invece di produrre un risultato vuoto ambiguo.
        ("fwalk", "top", "."),
        # Sonde a un percorso: non mutano niente, ma rispondono su un percorso
        # preciso, e `stat`/`lstat` erano già guardate. `access` è la sonda
        # "posso scriverci?", `readlink` rivela il bersaglio di un link,
        # `statvfs`/`pathconf` sono oracoli di esistenza (ENOENT fuori dal
        # confine). Lasciarle scoperte avrebbe reso il confine incoerente:
        # `os.stat` rifiutato e `os.access` che risponde la verità.
        ("stat", "path", None),
        ("lstat", "path", None),
        ("access", "path", None),
        ("readlink", "path", None),
        ("statvfs", "path", None),
        ("pathconf", "path", None),
    )
    #
    # DELIBERATAMENTE FUORI da queste tabelle, e non per dimenticanza:
    #   * la famiglia sui DESCRITTORI (`fstat`, `fchmod`, `ftruncate`, `read`,
    #     `write`, `dup`, `pipe`, …). Un fd non ha un percorso da validare, e la
    #     provenienza di un int non è verificabile: il confine sta al momento
    #     dell'`open` (vedi `_resolve_workspace_write`). Wrapparli sarebbe una
    #     recinzione senza cancello — `os.dup`+`os.read` rileggono comunque
    #     qualunque fd il processo host tenga aperto — e romperebbe `os.pipe`.
    #   * ciò che non prende percorsi (`environ`, `uname`, `urandom`): non c'è
    #     nulla su cui applicare un confine di path. `getcwd`/`getcwdb` sono
    #     nella stessa condizione — non prendono percorsi e non concedono
    #     nulla — ma hanno comunque un patch tutto loro (`_patch_os_getcwd`), e
    #     per un motivo diverso dal confine: RIPORTANO una directory, e se ne
    #     riportano una diversa da quella contro cui i percorsi relativi si
    #     risolvono il modello costruisce percorsi che poi gli vengono
    #     rifiutati. Vedi `_reported_working_directory`.

    # Mutatori a due percorsi: RISOLVERE ENTRAMBI GLI ESTREMI. Un rename con un
    # capo fuori dal workspace è un'evasione tanto quanto una scrittura diretta.
    _OS_TWO_PATH_FUNCTIONS: tuple[tuple[str, str, str], ...] = (
        ("rename", "src", "dst"),
        ("replace", "src", "dst"),
        ("link", "src", "dst"),
    )

    # Chi delle due tabelle qui sopra **cambia qualcosa**, e quindi è chiuso
    # quando il turno è in sola lettura. Non è una terza tabella: sono nomi che
    # devono esistere in una delle due, e `tests/agent/tools/
    # test_readonly_python_exec.py` lo verifica — un mutatore aggiunto là e non
    # qui è un buco, e un nome qui che là non c'è è una riga morta.
    #
    # Le sonde e gli enumeratori (`stat`, `access`, `listdir`, `walk`, i
    # `getxattr`/`listxattr`) restano APERTI di proposito: in sola lettura si
    # legge, e chiuderli renderebbe l'interruttore inutilizzabile invece che
    # sicuro. `symlink` ha un patch suo (`_patch_os_symlink`) e si chiude lì.
    _OS_MUTATING_FUNCTIONS: frozenset[str] = frozenset({
        "remove", "unlink", "rmdir", "mkdir", "makedirs", "truncate",
        "chmod", "utime", "setxattr", "removexattr",
        "rename", "replace", "link",
    })

    # Un descrittore di directory scavalca del tutto la risoluzione: con
    # `dir_fd` il percorso è relativo al descrittore, quindi `../../etc` da un
    # fd legittimo esce dal workspace senza passare da `resolve_allowed_path`.
    _OS_DIR_FD_KWARGS = ("dir_fd", "src_dir_fd", "dst_dir_fd")

    @staticmethod
    def _reject_fd_kwargs(op: str, kwargs: dict[str, Any]) -> None:
        """Rifiuta i parametri ``*dir_fd`` — vedi ``_OS_DIR_FD_KWARGS``."""
        for key in PythonNamespace._OS_DIR_FD_KWARGS:
            if kwargs.get(key) is not None:
                # Il bypass copre il logging: un handler su file aprirebbe
                # tramite il `builtins.open` patchato e rientrerebbe qui.
                with _path_guard_bypass():
                    logger.warning(
                        "python_exec: refused %s with %s= (a directory descriptor "
                        "bypasses workspace path resolution)",
                        op,
                        key,
                    )
                raise OSError(
                    f"{op} with {key} is not allowed under workspace restriction"
                )

    @staticmethod
    def _normalize_path_arg(path: Any, *, op: str) -> str:
        """Porta *path* a ``str``, rifiutando i descrittori interi.

        Un int è un fd già aperto: non ha un percorso da validare, quindi
        accettarlo riaprirebbe lo stesso buco del ``dir_fd``.
        """
        if isinstance(path, int):
            # Bypass sul logging: vedi `_reject_fd_kwargs`.
            with _path_guard_bypass():
                logger.warning(
                    "python_exec: refused %s on a raw file descriptor (%r)", op, path
                )
            raise OSError(
                f"{op} with a file descriptor is not allowed under workspace restriction"
            )
        if hasattr(path, "__fspath__"):
            path = path.__fspath__()
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        return str(path)

    @staticmethod
    def _guarded_os_path(
        path: Any, *, op: str, boundary: str, base: str | None = None
    ) -> str:
        """Valida *path* contro il confine e restituisce il percorso da usare.

        La VALIDAZIONE passa da ``resolve_allowed_path``, che dereferenzia i
        symlink: un link che punta fuori è un'evasione e viene rifiutato.
        Il valore RESTITUITO è invece il percorso assoluto *logico* (symlink
        non dereferenziati), perché ``remove``/``rename``/``lstat`` agiscono
        sul link stesso: passare il target risolto li farebbe operare su un
        altro file. Resta comunque assoluto, così un percorso relativo non può
        essere validato contro il workspace ed eseguito contro la cwd (che in
        python_exec è ``/``).

        ``base`` esplicita vince (la usa ``os.symlink`` per ancorare il target
        alla directory del link); altrimenti si usa la base dell'exec in corso
        (``working_dir``) e, in sua assenza, il confine stesso.

        È l'imbuto dove il rifiuto viene anche ANNOTATO (vedi
        ``_record_boundary_refusal``): da qui passano gli enumeratori e le sonde,
        cioè le chiamate i cui rifiuti la stdlib ingoia in silenzio.
        """
        from jenny.security.workspace_policy import (
            WorkspaceBoundaryError,
            _resolve_logical_path,
            resolve_allowed_path,
        )

        raw = PythonNamespace._normalize_path_arg(path, op=op)
        anchor = base or _active_path_base() or boundary
        # La policy stessa usa os.lstat/os.stat: senza bypass rientrerebbe qui.
        with _path_guard_bypass():
            logical = str(_resolve_logical_path(raw, anchor))
            if _is_runtime_path(logical, allow_ancestors=op in _RUNTIME_ANCESTOR_OPS):
                # Percorso dell'interprete: vedi `_runtime_path_prefixes`.
                return logical
            try:
                resolve_allowed_path(
                    raw,
                    workspace=anchor,
                    allowed_root=boundary,
                )
            except WorkspaceBoundaryError:
                logger.warning(
                    "python_exec: refused %s outside the workspace boundary %s: %s",
                    op,
                    boundary,
                    raw,
                )
                _record_boundary_refusal(op, raw)
                raise
            return logical

    def _patch_os_path_surface(self, mod: Any) -> None:
        """Confina nel workspace la superficie path-taking di ``os``.

        Stessa disciplina di ``_patch_os_open``, gate compreso: si patcha il
        modulo ``os`` GLOBALE, la funzione reale è memorizzata una volta su
        ``_jenny_real_fn`` (idempotenza) e ogni wrapper si apre su
        ``_active_path_boundary()`` — senza confine sul thread delega alla
        funzione reale, così il codice host su altri thread resta completamente
        inalterato (è il gate che evita la regressione Chaquopy descritta in
        ``_patch_os_open``, e che una finestra di ``_path_guard_bypass()``
        spegne anche qui).
        """
        mutating = self._OS_MUTATING_FUNCTIONS

        def _is_mutating(op: str) -> bool:
            return op.removeprefix("os.") in mutating

        def _refuse_os_if_readonly(op: str) -> None:
            """Chiude i soli mutatori quando il turno è in sola lettura.

            Dentro il wrapper e non prima: la tabella si installa una volta per
            processo, mentre la scrivibilità è del *turno*. Chiamata prima del
            gate del confine di path (vedi ``_refuse_write_if_readonly``), che
            per un exec senza ``restrict_to_workspace`` la saltava del tutto.
            """
            if _is_mutating(op):
                _refuse_write_if_readonly(op)

        for name, param, default in self._OS_SINGLE_PATH_FUNCTIONS:
            real = self._real_os_fn(mod, name)
            if real is None:
                continue

            def _single(
                *args: Any,
                _real: Any = real,
                _op: str = f"os.{name}",
                _param: str = param,
                _default: str | None = default,
                **kwargs: Any,
            ) -> Any:
                _refuse_os_if_readonly(_op)
                boundary = _active_path_boundary()
                if boundary is None:
                    return _real(*args, **kwargs)
                if _is_mutating(_op):
                    boundary = self._mutation_boundary(boundary)
                self._reject_fd_kwargs(_op, kwargs)
                if args:
                    raw, rest = args[0], args[1:]
                else:
                    raw, rest = kwargs.pop(_param, None), ()
                if raw is None:
                    if _default is None:
                        # Argomento mancante: lascia sollevare l'errore nativo.
                        return _real(*args, **kwargs)
                    raw = _default
                resolved = self._guarded_os_path(raw, op=_op, boundary=boundary)
                return _real(resolved, *rest, **kwargs)

            self._install_os_wrapper(mod, name, _single, real)

        for name, src_param, dst_param in self._OS_TWO_PATH_FUNCTIONS:
            real = self._real_os_fn(mod, name)
            if real is None:
                continue

            def _two(
                *args: Any,
                _real: Any = real,
                _op: str = f"os.{name}",
                _src: str = src_param,
                _dst: str = dst_param,
                **kwargs: Any,
            ) -> Any:
                _refuse_os_if_readonly(_op)
                boundary = _active_path_boundary()
                if boundary is None:
                    return _real(*args, **kwargs)
                # `rename`/`replace`/`link` mutano tutte: entrambi gli estremi
                # vanno validati contro il confine dello scope, non contro la
                # radice con cui il tool è stato costruito.
                if _is_mutating(_op):
                    boundary = self._mutation_boundary(boundary)
                self._reject_fd_kwargs(_op, kwargs)
                rest = list(args)
                raw_src = rest.pop(0) if rest else kwargs.pop(_src, None)
                raw_dst = rest.pop(0) if rest else kwargs.pop(_dst, None)
                if raw_src is None or raw_dst is None:
                    return _real(*args, **kwargs)
                src = self._guarded_os_path(raw_src, op=_op, boundary=boundary)
                dst = self._guarded_os_path(raw_dst, op=_op, boundary=boundary)
                return _real(src, dst, *rest, **kwargs)

            self._install_os_wrapper(mod, name, _two, real)

        self._patch_os_symlink(mod)

    def _patch_os_symlink(self, mod: Any) -> None:
        """``os.symlink`` a parte: il primo argomento è il TARGET del link.

        Un target relativo si legge rispetto alla directory del link, non
        rispetto al workspace root: validarlo con la base sbagliata
        rifiuterebbe link legittimi (o, peggio, ne accetterebbe di sbagliati).
        Quindi il link (``dst``) si valida normalmente e il target (``src``) si
        valida ancorato alla directory del link, ma si passa **invariato** alla
        funzione reale per non trasformare in assoluto un link volutamente
        relativo.
        """
        real = self._real_os_fn(mod, "symlink")
        if real is None:
            return

        def _symlink(*args: Any, **kwargs: Any) -> Any:
            _refuse_write_if_readonly("os.symlink")
            boundary = _active_path_boundary()
            if boundary is None:
                return real(*args, **kwargs)
            boundary = self._mutation_boundary(boundary)
            self._reject_fd_kwargs("os.symlink", kwargs)
            rest = list(args)
            raw_src = rest.pop(0) if rest else kwargs.pop("src", None)
            raw_dst = rest.pop(0) if rest else kwargs.pop("dst", None)
            if raw_src is None or raw_dst is None:
                return real(*args, **kwargs)
            dst = self._guarded_os_path(raw_dst, op="os.symlink", boundary=boundary)
            target = self._normalize_path_arg(raw_src, op="os.symlink")
            base = boundary if os.path.isabs(target) else os.path.dirname(dst)
            self._guarded_os_path(target, op="os.symlink", boundary=boundary, base=base)
            return real(raw_src, dst, *rest, **kwargs)

        self._install_os_wrapper(mod, "symlink", _symlink, real)

    @staticmethod
    def _reject_rmtree_callbacks(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        """Rifiuta ``onerror=``/``onexc=`` di ``shutil.rmtree``.

        Sono l'unico pezzo di codice ARBITRARIO che ``rmtree`` esegue, e lo
        esegue dentro il ``_path_guard_bypass`` che avvolge la rimozione: lì il
        confine del thread è sospeso PER INTERO (vedi ``_path_guard_bypass``) e
        la callback lavora senza. Non serve nemmeno un albero da cancellare: un
        percorso inesistente dentro il workspace passa la validazione,
        ``rmtree`` fallisce e chiama la callback.

        Posizionali inclusi: nella firma 3.11 (``path, ignore_errors=False,
        onerror=None, *, dir_fd=None``) ``onerror`` è il terzo parametro
        posizionale. ``onexc`` è keyword-only, da 3.12 in poi.
        """
        found = next(
            (name for name in ("onerror", "onexc") if kwargs.get(name) is not None),
            None,
        )
        if found is None and len(args) >= 2 and args[1] is not None:
            found = "onerror"
        if found is None:
            return
        # Il bypass copre il logging: vedi `_reject_fd_kwargs`.
        with _path_guard_bypass():
            logger.warning(
                "python_exec: refused shutil.rmtree with %s= (the callback would run "
                "with the workspace boundary suspended)",
                found,
            )
        raise OSError(
            f"shutil.rmtree with {found}= is not allowed under workspace restriction "
            "(the callback would run outside the workspace boundary). Use "
            "ignore_errors=True, or wrap the call in try/except OSError."
        )

    def _patch_shutil_rmtree(self) -> None:
        """Confina ``shutil.rmtree`` nel workspace (guard-gated).

        ``rmtree`` non è coperto dai wrapper qui sopra: su POSIX usa
        ``_rmtree_safe_fd``, che apre la radice una volta e poi lavora con
        ``dir_fd`` e descrittori interi — cioè proprio ciò che i wrapper
        rifiutano. Senza questo patch resterebbero insieme due comportamenti
        sbagliati: un ``rmtree`` FUORI dal workspace che riesce, e uno DENTRO
        che fallisce. Qui la radice è validata una volta sola e poi i wrapper
        vengono sospesi per la durata della rimozione.

        Cosa copre la sospensione, e cosa no. COPRE la DISCESA di ``rmtree``:
        l'algoritmo fd-based è progettato per non seguire i symlink (verifica
        ogni discesa con ``samestat``), quindi non può uscire dall'albero appena
        validato. NON copre il codice arbitrario che ``rmtree`` invoca per conto
        del chiamante — ``onerror``/``onexc`` girerebbero anch'esse dentro il
        bypass, cioè senza confine, e sono quindi rifiutate in ingresso (vedi
        ``_reject_rmtree_callbacks``). Il bypass è un'eccezione concessa a UN
        algoritmo di cui conosciamo il comportamento, non alla callback di
        chiunque.
        """
        real = getattr(shutil.rmtree, "_jenny_real_fn", shutil.rmtree)

        def _guarded_rmtree(path: Any = None, *args: Any, **kwargs: Any) -> Any:
            _refuse_write_if_readonly("shutil.rmtree")
            boundary = _active_path_boundary()
            if boundary is None:
                return real(path, *args, **kwargs)
            boundary = self._mutation_boundary(boundary)
            self._reject_fd_kwargs("shutil.rmtree", kwargs)
            self._reject_rmtree_callbacks(args, kwargs)
            target = self._guarded_os_path(path, op="shutil.rmtree", boundary=boundary)
            with _path_guard_bypass():
                return real(target, *args, **kwargs)

        _guarded_rmtree._jenny_real_fn = real  # type: ignore[attr-defined]
        shutil.rmtree = _guarded_rmtree

    @staticmethod
    def _real_os_fn(mod: Any, name: str) -> Any:
        """Funzione ``os`` originale, saltando un eventuale wrapper già montato."""
        current = getattr(mod, name, None)
        if current is None:
            return None
        return getattr(current, "_jenny_real_fn", current)

    @staticmethod
    def _install_os_wrapper(mod: Any, name: str, wrapper: Any, real: Any) -> None:
        """Monta *wrapper* su ``os.<name>`` ricordando la funzione reale."""
        wrapper._jenny_real_fn = real
        setattr(mod, name, wrapper)

    def _patch_io_open(self) -> None:
        """Route ``io.open`` through the workspace policy under restriction.

        OPTION A (chosen): on this Python build ``pathlib.Path.open`` /
        ``.read_text`` / ``.write_text`` all look ``open`` up as an attribute of
        the ``io`` module at call time, so patching ``io.open`` covers the
        entire pathlib file-I/O surface without touching the pathlib class —
        verified by the test suite (see test_pathlib_write_text_*). Questo, e
        non l'identità con ``builtins.open``, è ciò su cui poggia la scelta: da
        ``_patch_builtins_open`` in poi i due nomi sono due wrapper distinti,
        montati uno sopra l'``open`` reale ciascuno.

        ``io.open`` è un oggetto di processo, usato anche dal gateway. Il
        wrapper si apre perciò sul solito gate, ``_active_path_boundary()``:
        confine solo quando il thread corrente ne ha uno, passthrough per il
        codice host, per un exec non ristretto e dentro una finestra di
        ``_path_guard_bypass()``. È la stessa disciplina di ``_patch_os_open`` —
        tutti i wrapper di path di questo file si comportano così.

        ``io.open_code`` è confinata qui accanto. Apre per percorso esattamente
        come ``io.open``, ``io`` è nell'allowlist di produzione, e
        ``io.open_code(p).read()`` leggeva quindi qualunque cosa. Il docstring di
        ``_patch_builtins_open`` ragionava solo su ``_io.open_code`` come la usa
        la MACCHINA DI IMPORT e non vedeva che il codice guardato può chiamarla
        da sé. Chiuderla è gratis, verificato sul sorgente 3.11:
        ``_bootstrap_external`` scrive ``_io.open_code(...)``/``_io.FileIO(...)``,
        cioè attributi del modulo C ``_io``, non di ``io`` — riassegnare
        ``io.open_code`` non tocca l'import.
        """
        real_open = getattr(io.open, "_jenny_real_open", io.open)

        def _workspace_io_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
            # Sola lettura prima del gate: senza `restrict_to_workspace` il ramo
            # di passthrough qui sotto salta anche il rifiuto (vedi
            # `_refuse_write_if_readonly`).
            if _is_write_mode(mode):
                _refuse_write_if_readonly("io.open for writing")
            boundary = _active_path_boundary()
            if boundary is None:
                # Host code (no guarded exec active on this thread): untouched.
                return real_open(file, mode, *args, **kwargs)
            resolved = self._resolve_workspace_write(
                file,
                boundary=boundary,
                base=_active_path_base(),
                for_write=_is_write_mode(mode),
            )
            return real_open(self._open_target(resolved), mode, *args, **kwargs)

        _workspace_io_open._jenny_real_open = real_open  # type: ignore[attr-defined]
        io.open = _workspace_io_open

        real_open_code = getattr(io.open_code, "_jenny_real_open", io.open_code)

        def _workspace_io_open_code(path: Any):
            boundary = _active_path_boundary()
            if boundary is None:
                return real_open_code(path)
            resolved = self._resolve_workspace_write(
                path, boundary=boundary, base=_active_path_base()
            )
            # `open_code` accetta solo un percorso: un fd qui è un TypeError
            # nativo, e va lasciato tale.
            return real_open_code(str(resolved))

        _workspace_io_open_code._jenny_real_open = real_open_code  # type: ignore[attr-defined]
        io.open_code = _workspace_io_open_code  # type: ignore[assignment]

    def _patch_io_fileio(self) -> None:
        """Confina ``io.FileIO``, che apre per percorso senza passare da ``open``.

        ``io`` è nell'allowlist di produzione e ``FileIO`` è una CLASSE, non una
        funzione: ``_patch_io_open`` non la sfiorava, e il C che la costruisce
        non consulta né ``builtins.open`` né ``io.open``. Restava quindi una
        terza via, diretta e completa, per lo stesso confine chiuso altrove —
        ``io.FileIO('/fuori/config.json').read()`` in lettura e
        ``io.FileIO('/fuori/x', 'w')`` in scrittura. Misurata, non ipotizzata.

        Si sostituisce con una SOTTOCLASSE, non con una funzione, così che
        ``io.FileIO`` resti usabile come classe base e le istanze restino dei
        veri ``FileIO``. Limite noto e accettato: sotto guard,
        ``isinstance(<FileIO creato dal C>, io.FileIO)`` diventa False, perché
        il nome punta alla sottoclasse. Nessun modulo consentito fa quel
        controllo, e l'alternativa (una metaclasse con ``__instancecheck__``)
        costa più di quanto valga.

        Guard-gated e idempotente come tutto il resto: la classe reale è
        memorizzata su ``_jenny_real_fileio``.
        """
        namespace = self
        real_fileio = getattr(io.FileIO, "_jenny_real_fileio", io.FileIO)

        class _WorkspaceFileIO(real_fileio):  # type: ignore[misc, valid-type]
            def __init__(
                self,
                file: Any,
                mode: str = "r",
                closefd: bool = True,
                opener: Any = None,
            ) -> None:
                # Vedi `_workspace_io_open`: il rifiuto della sola lettura non
                # può stare dietro al gate del confine.
                if _is_write_mode(mode):
                    _refuse_write_if_readonly("io.FileIO for writing")
                boundary = _active_path_boundary()
                if boundary is not None:
                    # `_resolve_workspace_write` lascia passare i descrittori
                    # interi invariati (vedi lì il perché): `io.FileIO(fd)` è
                    # legittima e stringere l'int aprirebbe un file di nome "5".
                    file = namespace._open_target(
                        namespace._resolve_workspace_write(
                            file,
                            boundary=boundary,
                            base=_active_path_base(),
                            for_write=_is_write_mode(mode),
                        )
                    )
                super().__init__(file, mode, closefd=closefd, opener=opener)

        _WorkspaceFileIO._jenny_real_fileio = real_fileio  # type: ignore[attr-defined]
        io.FileIO = _WorkspaceFileIO  # type: ignore[misc]

    def _patch_builtins_open(self) -> None:
        """Confina nel workspace anche il ``builtins.open`` reale (guard-gated).

        È il patch più invasivo del file, e serve perché senza di lui il confine
        di lettura non esisteva affatto. Fino a qui l'``open`` confinato viveva
        in due posti che la stdlib non guarda mai:

        * ``__builtins__['open']`` del namespace guardato — lo vede solo il
          codice scritto dentro ``python_exec``;
        * l'attributo ``io.open`` — lo vede solo chi scrive ``io.open(...)``.

        Un modulo della stdlib risolve ``open`` come nome globale, e i globali
        di un modulo ricadono su ``builtins``: ``shutil.copyfile`` apriva quindi
        il ``builtins.open`` VERO. Misurato, non ipotizzato: con
        ``restrict_to_workspace=True``,
        ``shutil.copyfile('/fuori/secret.txt', '<ws>/stolen.txt')`` copiava
        dentro il contenuto di fuori (lo ``os.stat`` che ``_samefile`` fa prima
        veniva sì rifiutato, ma ``copyfile`` lo ingoia in un ``except OSError``).
        Stessa strada per ``xml.etree.ElementTree.parse`` e per qualunque altro
        modulo consentito che apra un file per nome.

        Il wrapper è GUARD-GATED come tutti gli altri: fuori da un exec guardato
        (``_active_path_boundary()`` è None) delega all'``open`` reale senza
        toccare nulla. Il gate qui è più che una precauzione — ``builtins.open``
        è attraversato da tutto l'interprete, incluso il bootstrap di Chaquopy —
        quindi il codice host su qualunque altro thread deve restare bit-per-bit
        inalterato.

        Sulla RICORSIONE: il wrapper apre soltanto dopo aver risolto, e la
        risoluzione gira dentro ``_path_guard_bypass`` (vedi
        ``_resolve_workspace_write``), quindi qualunque ``open`` fatto dal
        macchinario della policy — o da ``logging`` mentre riporta un rifiuto —
        ricade nel ramo di passthrough. Il ritorno usa l'``open`` reale, mai
        ``builtins.open``, che a quel punto è questo stesso wrapper.

        Restano fuori portata, per costruzione e non per dimenticanza, i moduli
        che catturano l'``open`` originale al proprio import
        (``from builtins import open as _builtin_open``, come fa ``tokenize``, e
        quindi ``linecache``) e la macchina di import, che legge i sorgenti con
        ``_io.open_code``. È esattamente ciò che tiene traceback, linecache e
        import fuori dai piedi durante un exec guardato.
        """
        real_open = _real_builtins_open()

        def _workspace_builtins_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
            # Vedi `_workspace_io_open`. Qui il gate di
            # `_refuse_write_if_readonly` pesa più che altrove: `builtins.open`
            # è attraversato da TUTTO l'interprete, e il rifiuto deve valere per
            # il codice del modello e per nessun altro.
            if _is_write_mode(mode):
                _refuse_write_if_readonly("open for writing")
            boundary = _active_path_boundary()
            if boundary is None:
                # Codice host (nessun exec guardato su questo thread): intatto.
                return real_open(file, mode, *args, **kwargs)
            resolved = self._resolve_workspace_write(
                file,
                boundary=boundary,
                base=_active_path_base(),
                for_write=_is_write_mode(mode),
            )
            return real_open(self._open_target(resolved), mode, *args, **kwargs)

        _workspace_builtins_open._jenny_real_open = real_open  # type: ignore[attr-defined]
        builtins.open = _workspace_builtins_open

    def _resolve_exec_base(self, working_dir: str | None) -> str | None:
        """Valida *working_dir* e restituisce la base di risoluzione assoluta.

        Il confine resta la radice del workspace: una base che ne esce è
        rifiutata con ``WorkspaceBoundaryError``, altrimenti si potrebbe
        spostare la risoluzione dei percorsi relativi fuori dal workspace e
        aggirare tutto il resto del confine. ``None``/vuoto ⇒ nessuna base,
        cioè il comportamento storico (si misura dalla radice).

        Stessa disciplina di ``_guarded_os_path``: si VALIDA col percorso
        risolto (i symlink vengono dereferenziati, un link che punta fuori è
        un'evasione) ma si RESTITUISCE quello logico. Restituire il realpath
        sposterebbe la base in uno spazio di nomi diverso da quello in cui è
        espresso il confine (su macOS ``/private/var`` vs ``/var``), e ogni
        percorso relativo risulterebbe poi "fuori dal workspace".
        """
        if not working_dir:
            return None
        from jenny.security.workspace_policy import (
            _resolve_logical_path,
            resolve_allowed_path,
        )

        # Bypass: la policy stessa passa da os.lstat, che sotto guard è
        # patchato (vedi `_guarded_os_path`).
        with _path_guard_bypass():
            resolve_allowed_path(
                str(working_dir),
                workspace=self.workspace,
                allowed_root=self.workspace,
            )
            return str(_resolve_logical_path(str(working_dir), self.workspace))

    @staticmethod
    def _push_exec_sys_path(base: str | None) -> None:
        """Mette *base* in testa a ``sys.path`` per la durata dell'exec.

        È ciò che rende possibile ``import <modulo accanto alla skill>``: la
        macchina di import non conosce il nostro confine, conosce solo
        ``sys.path``. La voce inserita viene ricordata sul thread-local e tolta
        da ``_pop_exec_sys_path`` PER IDENTITÀ (vedi ``_sys_path_lock``).
        """
        entry: str | None = None
        if base:
            entry = str(base)
        # Fotografia di `sys.modules` PRIMA che la voce esista: è ciò che
        # permette a `_unload_exec_modules` di distinguere "caricato da questo
        # exec" da "c'era già". Solo quando c'è una voce da inserire — senza
        # `working_dir` non c'è nulla da ombreggiare e non si paga niente.
        _path_guard_state.modules_before = frozenset(sys.modules) if entry else None
        # Registrare PRIMA di inserire, e sotto lo STESSO lock. Nell'ordine
        # inverso `PythonExecInterrupted` (iniettata a qualunque confine di
        # bytecode) poteva atterrare fra l'inserimento e la registrazione: la
        # voce restava in `sys.path` con nulla a ricordarla, `_pop_exec_sys_path`
        # leggeva None e usciva, e una directory scrivibile dall'agente restava
        # in `sys.path[0]` per tutta la vita del processo. Così il caso peggiore
        # è l'opposto — registrata e mai inserita — e la rimozione per identità
        # semplicemente non la trova.
        with _sys_path_lock:
            _path_guard_state.sys_path_entry = entry
            if entry is not None:
                sys.path.insert(0, entry)

    @staticmethod
    def _pop_exec_sys_path() -> None:
        """Toglie da ``sys.path`` la voce inserita all'ingresso del guard."""
        entry = getattr(_path_guard_state, "sys_path_entry", None)
        _path_guard_state.sys_path_entry = None
        # La voce serve ancora a `_unload_exec_modules`, che gira subito dopo.
        _path_guard_state.unload_entry = entry
        if entry is None:
            return
        with _sys_path_lock:
            for index, value in enumerate(sys.path):
                if value is entry:
                    del sys.path[index]
                    return
        # Il codice guardato può aver riscritto sys.path da sé: niente da
        # ripristinare, ma vale la traccia.
        logger.debug("python_exec: sys.path entry %r was already gone at exit", entry)

    @staticmethod
    def _unload_exec_modules() -> None:
        """Scarica da ``sys.modules`` ciò che questo exec ha caricato dalla base.

        Vedi il commento su ``_module_lives_under``: la voce di ``sys.path``
        viene tolta per identità, ma senza questo passo il modulo importato
        resterebbe registrato a livello di processo e ombreggerebbe per sempre
        un nome di sistema omonimo. I nomi che ombreggiano la stdlib vengono
        anche riportati al modello (vedi ``_shadowed_modules_note``).
        """
        entry = getattr(_path_guard_state, "unload_entry", None)
        before = getattr(_path_guard_state, "modules_before", None)
        _path_guard_state.unload_entry = None
        _path_guard_state.modules_before = None
        if entry is None or before is None:
            return
        prefix = os.path.abspath(entry)
        shadowed: list[str] = []
        stdlib = getattr(sys, "stdlib_module_names", frozenset())
        for name in list(sys.modules):
            if name in before:
                continue
            module = sys.modules.get(name)
            if module is None or not _module_lives_under(module, prefix):
                continue
            sys.modules.pop(name, None)
            top = name.partition(".")[0]
            if top in stdlib and top not in shadowed:
                shadowed.append(top)
        if shadowed:
            logger.warning(
                "python_exec: working_dir %s shadowed stdlib module(s) %s; unloaded",
                prefix,
                ", ".join(shadowed),
            )
        _path_guard_state.shadowed_modules = shadowed

    def _enter_guard(self, working_dir: str | None = None) -> None:
        """Activate the process-wide import guard for this thread.

        *working_dir* (o, se assente, ``self.exec_base``) diventa la base di
        risoluzione dei percorsi relativi e la testa di ``sys.path`` per la
        durata dell'exec. Valida PRIMA di toccare qualunque stato globale, così
        una base rifiutata non lascia niente da ripulire.
        """
        # Unica normalizzazione difensiva all'ingresso, e solo su `bypass`.
        # I worker del pool sono riciclati e nessuno azzera questi
        # thread-local fuori da `_exit_guard`: un `bypass` rimasto acceso (una
        # `PythonExecInterrupted` caduta nel `finally` del context manager)
        # spegnerebbe il confine per l'exec successivo che finisce su quel
        # worker. Azzerarlo è fail-CLOSED. `boundary`/`base`/`rules` NON sono
        # toccati qui di proposito: sono comunque riassegnati sotto, e
        # anticiparne l'azzeramento allargherebbe soltanto la finestra in cui
        # non c'è confine da applicare (fail-open) — l'opposto di ciò che serve.
        _path_guard_state.bypass = False
        # Registro dei rifiuti: azzerato all'INGRESSO, non all'uscita, perché
        # `execute()` lo legge dopo `_exit_guard` per metterlo in coda a stderr
        # (vedi `_boundary_refusal_note`). Sono dati inerti, non una capability:
        # lasciarli sopravvivere al teardown non riapre nulla.
        _path_guard_state.refusals = []
        _path_guard_state.refusal_count = 0
        # Stesso ciclo di vita del registro dei rifiuti: lo scrive
        # `_unload_exec_modules` durante il teardown e lo legge `execute()`
        # dopo, quindi va azzerato all'INGRESSO.
        _path_guard_state.shadowed_modules = []
        # I prefissi del runtime dipendono dal confine (devono restargli
        # disgiunti): si calcolano qui, una volta per exec, e il controllo sul
        # percorso diventa un confronto di stringhe. Vedi `_runtime_path_prefixes`.
        _path_guard_state.runtime_prefixes = (
            _effective_runtime_prefixes(self.workspace) if self.restrict_to_workspace else ()
        )
        # La cache della radice risolta vale per un exec: la radice non può
        # cambiare mentre l'exec gira, e ripartire puliti limita a una sola
        # esecuzione la vita di una voce stantia. Infallibile (una `dict.clear`).
        from jenny.security.workspace_policy import invalidate_root_cache

        invalidate_root_cache()
        base = self._resolve_exec_base(
            working_dir if working_dir is not None else self.exec_base
        )
        # Stesso ragionamento del confine di path qui sotto, applicato alla
        # superficie di evasione di `os`: legarla all'`import os` esplicito non
        # bloccava nulla, perché `import shutil; shutil.os.system("...")` arriva
        # alla stessa funzione senza nominare `os`. Vale anche senza
        # `restrict_to_workspace`: quel blocco riguarda i processi, non i path.
        self._install_os_blocked(sys.modules["os"], self._OS_BLOCKED_ESCAPE_FUNCTIONS)
        self._install_os_blocked(
            sys.modules["os"],
            self._OS_BLOCKED_HOST_STATE_FUNCTIONS,
            self._OS_BLOCKED_HOST_STATE_MESSAGE,
        )
        # Vale in entrambe le modalità: il salto di thread porta via anche le
        # `rules` (stub di evasione di `os`, filtro di `sys.modules`), non solo
        # il confine di path. Vedi `_patch_asyncio_thread_hops`.
        _patch_asyncio_thread_hops()
        # I wrapper di path servono in DUE casi, non in uno solo. Il confine di
        # workspace è il primo; il secondo è un turno in **sola lettura**, che
        # non è una questione di *dove* si scrive e vale quindi anche senza
        # `restrict_to_workspace`. Finché la condizione era il solo confine,
        # `restrict_to_workspace=False` non installava nessun wrapper e la sola
        # lettura non aveva dove essere applicata: `os.remove`, `shutil.rmtree`,
        # `open(..., 'w')` arrivavano tutti al filesystem (misurato).
        #
        # Il flag si legge QUI, dentro il worker, e ci arriva perché
        # `run_python_async` porta con sé il contesto del turno (vedi lì): è la
        # stessa lettura che faranno i wrapper, quindi non possono divergere.
        # Condizionale e non incondizionato: un'installazione che non serve a
        # nessuno lascerebbe `builtins.open` patchato a vita anche su un
        # deployment che non usa né il confine né l'interruttore.
        from jenny.security.workspace_access import current_turn_is_readonly

        if self.restrict_to_workspace or current_turn_is_readonly():
            # Il confine NON può dipendere da un `import os` del codice
            # guardato: `import shutil` / `glob` / `pathlib` arrivano alle
            # stesse funzioni attraverso il riferimento a `os` che quei moduli
            # tengono al loro interno, e prima di questo punto un semplice
            # `import shutil; shutil.rmtree("/qualsiasi/cosa")` girava del tutto
            # fuori dal confine. I patch sono guard-gated e idempotenti, quindi
            # installarli all'ingresso di ogni exec è sicuro e costa poco.
            self._patch_os_workspace_boundary(sys.modules["os"])
            self._patch_io_open()
            self._patch_io_fileio()
            self._patch_builtins_open()
            self._patch_shutil_rmtree()
        # I wrapper sono globali e condivisi: il confine da applicare è quello
        # del namespace attivo su QUESTO thread, non quello di chi ha
        # installato il patch (altrimenti un namespace non ristretto eseguito
        # dopo uno ristretto ne erediterebbe il workspace).
        _path_guard_state.boundary = self.workspace if self.restrict_to_workspace else None
        # La base vale anche senza `restrict_to_workspace`: lì i wrapper di path
        # passano dritti (nessun confine da applicare) ma `sys.path` no, e
        # `import <modulo della skill>` deve funzionare in entrambe le modalità.
        _path_guard_state.base = base
        _import_guard_state.rules = (frozenset(self.allowed_modules), frozenset(self.blocked_modules))
        # Per ultimo: se qualcosa sopra solleva, non c'è nulla da togliere.
        self._push_exec_sys_path(base)

    def _exit_guard(self) -> None:
        """Deactivate the process-wide import guard for this thread.

        L'ordine è deliberato. `PythonExecInterrupted` è iniettata in modo
        ASINCRONO a qualunque confine di bytecode — e il percorso di timeout la
        spara proprio mentre `execute()` sta per tornare, cioè qui dentro.
        `_pop_exec_sys_path` è la sola parte fallibile del teardown (prende un
        lock, scorre una lista, può loggare): se girava per prima e veniva
        interrotta, il worker tornava nel pool con `boundary` e `rules` ancora
        attivi, e chiunque ci finisse sopra dopo girava dentro il guard di
        qualcun altro. Da quando il pool è DEDICATO (vedi
        `_python_exec_executor`) la vittima non è più il notifier o lo snapshot
        ma l'exec successivo, che eredita in silenzio il workspace del
        precedente: meno vasto, altrettanto indiagnosticabile.
        Quindi: prima gli store infallibili, il pop in `finally`.

        `bypass` è incluso perché era l'unico pezzo di stato del guard che
        nessuno azzerava mai, e un `True` sopravvissuto disattiva in silenzio
        `restrict_to_workspace` per il resto della vita del processo. Azzerarlo
        è fail-closed e non può rompere nulla: nessun blocco di bypass chiama
        `_exit_guard`.

        `refusals`/`refusal_count`/`shadowed_modules` NON sono azzerati qui:
        `execute()` li legge dopo il teardown per comporre le note di fine
        esecuzione, e li riazzera `_enter_guard`. Sono dati inerti — nessun ramo
        del confine li consulta.

        `_unload_exec_modules` viene per ultimo, dopo il pop, e per la stessa
        ragione: è fallibile (scorre `sys.modules`) e non deve poter impedire
        allo stato del guard di essere smontato. Legge la voce che
        `_pop_exec_sys_path` gli lascia su `unload_entry`.
        """
        try:
            _path_guard_state.boundary = None
            _path_guard_state.base = None
            _path_guard_state.bypass = False
            _path_guard_state.runtime_prefixes = ()
            _import_guard_state.rules = None
        finally:
            try:
                self._pop_exec_sys_path()
            finally:
                self._unload_exec_modules()

    def execute(self, code: str, working_dir: str | None = None) -> tuple[str, str, Any]:
        """Execute code and return (stdout, stderr, result)."""
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        result = None

        # Nessun lock di processo: la cattura è per-thread (vedi
        # `_capture_streams`) e la sola mutazione globale rimasta — la voce in
        # `sys.path` — ha il suo lock breve dentro `_push_exec_sys_path`.
        # `_enter_guard` sta dentro il try perché `_exit_guard` deve girare
        # anche se l'ingresso fallisce a metà (p.es. `PythonExecInterrupted`
        # iniettata, o base fuori dal confine).
        try:
            self._enter_guard(working_dir)
            with _capture_streams(stdout_buf, stderr_buf):
                # Try eval first (for expressions)
                try:
                    result = eval(self._compile(code, "eval"), self._ns)
                except SyntaxError:
                    # Fall back to exec (for statements)
                    exec(self._compile(code, "exec"), self._ns)
        except (PythonExecInterrupted, _SessionStopped, asyncio.CancelledError):
            # Interrupt del sandbox (timeout / stop): non è un errore del
            # codice utente e deve risalire fino a _run / al chiamante.
            # `_SessionStopped` è nella lista per lo stesso motivo per cui
            # è una BaseException: è il segnale di cancellazione cooperativa
            # delle exec session, e l'`except _SessionStopped` di
            # `_PythonSession._run` è l'unico posto che sa tradurlo in
            # "Execution stopped" con exit code -1. Ingoiarla qui rendeva
            # quel ramo codice morto e trasformava ogni `/stop` in un
            # traceback grezzo chiuso con exit code 0, cioè successo.
            raise
        except BaseException:
            # Confine del sandbox: SystemExit, KeyboardInterrupt e ogni altra
            # BaseException sollevata dal codice utente si fermano QUI. Se
            # sfuggono, asyncio le rilancia fuori dall'event loop (Task.__step
            # ri-alza KeyboardInterrupt/SystemExit) e il gateway muore: nessun
            # except a valle può intercettarle. Diventano un normale errore di
            # tool, così il modello legge "SystemExit" e si corregge.
            # Il bypass è per il FORMATTING, non per il codice utente: linecache
            # apre i sorgenti dei frame (stdlib, jenny) per stampare le righe, e
            # senza bypass ogni eccezione produrrebbe una raffica di WARNING di
            # rifiuto su file che non c'entrano nulla con il codice guardato.
            with _path_guard_bypass():
                traceback.print_exc(file=stderr_buf)
        finally:
            self._exit_guard()

        return stdout_buf.getvalue(), _with_exec_notes(stderr_buf.getvalue()), result

    def call_function(
        self,
        name: str,
        args: list | None = None,
        kwargs: dict | None = None,
        working_dir: str | None = None,
    ) -> tuple[str, str, Any]:
        """Call a registered function by name."""
        func = self._ns.get(name)
        if func is None:
            return "", f"Function '{name}' not found in namespace", None
        if not callable(func):
            return "", f"'{name}' is not callable", None

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        result = None

        # Stessa disciplina di execute(): nessun lock di processo, cattura
        # per-thread, `_enter_guard` dentro il try perché `_exit_guard` deve
        # girare comunque.
        try:
            self._enter_guard(working_dir)
            with _capture_streams(stdout_buf, stderr_buf):
                result = func(*(args or []), **(kwargs or {}))
        except (PythonExecInterrupted, _SessionStopped, asyncio.CancelledError):
            # Vedi execute(): l'interrupt del sandbox (`_SessionStopped`
            # compresa) deve risalire.
            raise
        except BaseException:
            # Stesso confine di execute(): una BaseException del codice utente
            # non deve poter uscire dal sandbox e uccidere il gateway.
            # Bypass del path guard come in execute(): vedi lì il perché.
            with _path_guard_bypass():
                traceback.print_exc(file=stderr_buf)
        finally:
            self._exit_guard()

        return stdout_buf.getvalue(), _with_exec_notes(stderr_buf.getvalue()), result

    def register_function(self, name: str, func: Any) -> None:
        """Register a callable in the namespace."""
        self._ns[name] = func


# ---------------------------------------------------------------------------
# Async wrapper with timeout
# ---------------------------------------------------------------------------


class PythonExecInterrupted(BaseException):
    """Iniettata nel thread di esecuzione per interromperlo (timeout o /stop).

    BaseException di proposito: un ``except Exception`` nel codice utente non
    deve poterla ingoiare.
    """


def _interrupt_thread(ident: int | None) -> None:
    """Best-effort: alza :class:`PythonExecInterrupted` nel thread *ident*.

    Usa ``PyThreadState_SetAsyncExc`` (ctypes, supportato da Chaquopy):
    l'eccezione viene consegnata al prossimo confine di BYTECODE, quindi
    interrompe loop e codice Python puro ma NON un thread fermo dentro una
    chiamata C (una ``time.sleep`` lunga, una ``recv``, l'acquisizione di un
    lock). Quel thread resta bloccato a tempo indeterminato.

    QUANTO COSTA, ONESTAMENTE. La versione precedente di questo docstring
    diceva "zombie ma innocui": era vero quando la finestra guardata era
    interamente dentro ``execute()``, non lo era più da quando un lock di
    processo la avvolgeva — un thread incastrato lo teneva, e ogni exec
    successivo si parcheggiava dietro di lui, a sua volta non interrompibile.
    Quel lock non c'è più (vedi il commento sulla cattura per-thread in testa
    al file), quindi il danno residuo è di nuovo circoscritto e vale la pena
    dirlo per intero:

    * il thread occupa un worker del pool DEDICATO (vedi
      `_python_exec_executor`), non del default executor: notifier, snapshot,
      backup e cron non ne risentono più;
    * lo stato del guard su quel thread non viene mai smontato (``_exit_guard``
      non gira), quindi la sua voce di ``sys.path`` resta e il worker non è
      riutilizzabile in sicurezza — è perso finché vive il processo;
    * con tutti i worker persi, python_exec smette di funzionare (le chiamate
      vanno in coda e scadono con il loro timeout) mentre il resto del gateway
      continua a girare.

    Gli effetti del turno sono comunque scartati dall'epoch di turno (vedi
    jenny.agent.turn_epochs).
    """
    if ident is None:
        return
    try:
        import ctypes

        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(ident), ctypes.py_object(PythonExecInterrupted)
        )
        if res > 1:
            # Contratto CPython: >1 = stato corrotto, va annullato subito.
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(ident), None)
    except Exception:
        logger.debug("Could not interrupt python_exec thread %s", ident, exc_info=True)


class _ContextBoundNamespace:
    """Il namespace, portato oltre il thread grezzo della sessione.

    Il ramo ``yield_time_ms`` non passa dall'executor: ``ExecSessionManager``
    lancia un ``threading.Thread`` grezzo, che non è patchato e porta con sé
    **niente** — né ContextVar né thread-local — e da dentro ``exec_session``
    non c'è modo di rimediare, perché là il turno non c'è più. Il rimedio sta
    qui, sul thread dell'event loop dove le due metà esistono ancora: si
    costruisce il ponte una volta, alla nascita dell'involucro, e le due sole
    chiamate che ``_PythonSession`` fa lo attraversano.

    Vale la stessa ragione elencata su ``run_python_async``: senza questo,
    ``python_exec(code=..., yield_time_ms=...)`` era la strada che ignorava sia
    la sola lettura sia il confine di scrittura del progetto — misurata, non
    ipotizzata. Tutto il resto delega per attributo.

    **Il ponte è ``_carry_turn_across_thread``, lo stesso dei due salti in
    executor** (T4.13). Prima questa classe copiava il ``Context`` da sé e non
    toccava il thread-local: tre punti dello stack sapevano che un salto esiste
    e ognuno se ne ricordava una metà diversa. Ognuno dei due wrapper ha la
    **propria** copia del contesto, quindi non esiste il caso "già entrato".
    """

    def __init__(self, namespace: Any) -> None:
        self._namespace = namespace
        self._execute = _carry_turn_across_thread(namespace.execute)
        self._call_function = _carry_turn_across_thread(namespace.call_function)

    def execute(self, code: str, working_dir: str | None = None) -> tuple[str, str, Any]:
        return self._execute(code, working_dir)

    def call_function(
        self,
        function: str,
        args: list | None = None,
        kwargs: dict | None = None,
        working_dir: str | None = None,
    ) -> tuple[str, str, Any]:
        return self._call_function(function, args, kwargs, working_dir)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._namespace, name)


async def run_python_async(
    code: str | None,
    function: str | None,
    args: list | None,
    kwargs: dict | None,
    namespace: PythonNamespace,
    timeout: int | None,
    max_output_chars: int,
    working_dir: str | None = None,
) -> str:
    """Execute Python code/function in a thread with timeout.

    *working_dir* è la base di risoluzione per questa esecuzione (vedi
    ``PythonNamespace._enter_guard``): passata esplicitamente, non letta dal
    namespace, così due chiamate concorrenti sullo stesso tool non si
    sovrascrivono la base a vicenda.

    IL CONTESTO DEL TURNO VIAGGIA CON L'ESECUZIONE, e non è un dettaglio: è la
    differenza fra un cancello che tiene e un cancello che sembra tenere.
    ``loop.run_in_executor`` NON copia il ``contextvars.Context`` (al contrario
    di ``asyncio.to_thread``), quindi sul worker ogni ContextVar tornava al
    proprio default — e le due politiche del turno sono ContextVar:

    * ``current_turn_is_readonly()`` → ``False``: **ogni** scrittura passava
      durante un turno in sola lettura (``os.remove``, ``shutil.rmtree``,
      ``open(..., 'w')``, ``Path.write_text``, ``io.FileIO``, i builtin
      registrati). Misurato attraverso ``PythonExecTool.execute()``, non dedotto;
    * ``current_tool_workspace()`` → nessuno scope: il confine di scrittura di
      una sessione-progetto tornava alla radice del workspace, quindi da dentro
      un progetto si scriveva su ``SOUL.md`` e nella cartella di un altro.

    Non si vedeva nei test perché un test chiama ``PythonNamespace.execute()``
    dal proprio thread, dove il ContextVar c'è: il difetto vive esattamente nel
    salto che il test non fa. Da qui la regola — **ogni prova di questi cancelli
    passa da ``await PythonExecTool.execute()``**.

    Lo stato del guard di path resta invece thread-local e continua a essere
    montato dal worker (``_enter_guard``): vedi il commento su
    ``_path_guard_state`` per il perché non può stare in una closure.

    **Il salto lo fa ``_carry_turn_across_thread``, non questa funzione** (T4.13):
    la copia del contesto viveva qui, il trasporto del thread-local viveva in
    ``_carry_guard_state`` e il thread grezzo delle sessioni in un terzo posto —
    tre punti, due metà, e un salto nuovo che ne indovina una sola.
    """
    loop = asyncio.get_running_loop()
    ident_cell: list[int | None] = [None]
    done_cell = [False]

    def _run():
        ident_cell[0] = threading.get_ident()
        try:
            if function:
                return namespace.call_function(function, args, kwargs, working_dir)
            elif code:
                return namespace.execute(code, working_dir)
            else:
                return "", "Error: Provide 'code' or 'function'", None
        except PythonExecInterrupted:
            # Il chiamante ha già mollato l'await: esito consumato da nessuno,
            # ritorno pulito per non sporcare i log del future abbandonato.
            return "", "Error: execution interrupted", None
        except asyncio.CancelledError:
            # /stop e abbandono del turno passano di qui: deve propagare.
            raise
        except BaseException:
            # Difesa in profondità: il namespace già ferma le BaseException del
            # codice utente, ma nulla deve poter atterrare sul future e finire
            # rilanciato fuori dall'event loop (vedi PythonNamespace.execute).
            return "", traceback.format_exc(), None
        finally:
            done_cell[0] = True

    # Costruito QUI, sul thread dell'event loop, dove le due metà del turno
    # esistono ancora. Il contesto è copiato per questa singola esecuzione, e
    # non lo usa nessun altro.
    _run_carried = _carry_turn_across_thread(_run)

    def _interrupt_if_running() -> None:
        # Se il thread ha già finito, l'interrupt colpirebbe il worker del
        # pool sul lavoro successivo: fire solo se ancora dentro _run.
        if not done_cell[0]:
            _interrupt_thread(ident_cell[0])

    # Pool DEDICATO, mai il default executor: vedi `_python_exec_executor`.
    executor = _python_exec_executor()
    try:
        if timeout and timeout > 0:
            stdout, stderr, result = await asyncio.wait_for(
                loop.run_in_executor(executor, _run_carried),
                timeout=timeout,
            )
        else:
            stdout, stderr, result = await loop.run_in_executor(
                executor, _run_carried
            )
    except asyncio.TimeoutError:
        _interrupt_if_running()
        return f"Error: Python execution timed out after {timeout} seconds"
    except asyncio.CancelledError:
        # /stop (o abbandono del turno): prova a uccidere anche il thread.
        _interrupt_if_running()
        raise

    # Build output
    parts = []
    if stdout:
        parts.append(stdout.strip())
    if stderr:
        parts.append(f"STDERR:\n{stderr.strip()}")
    if result is not None:
        # Il `repr` gira col guard già smontato, e la riga la compone
        # `format_result_line`: vedi lì cosa questo lascia aperto e cosa no.
        parts.append(format_result_line(result))

    output = "\n".join(parts) if parts else "(no output)"

    # Truncate
    if len(output) > max_output_chars:
        half = max_output_chars // 2
        output = (
            output[:half]
            + f"\n\n... ({len(output) - max_output_chars:,} chars truncated) ...\n\n"
            + output[-half:]
        )

    return output


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

@tool_parameters(
    tool_parameters_schema(
        code=StringSchema(
            "Python code to execute. Can be a single expression or multiple statements.",
            nullable=True,
        ),
        function=StringSchema(
            "Name of a registered Python function to call. "
            "Use this for common operations like file I/O, HTTP, etc.",
            nullable=True,
        ),
        args=ArraySchema(
            StringSchema("Positional argument for the function call."),
            description="Positional arguments for the function call.",
            nullable=True,
        ),
        kwargs=ObjectSchema(
            description="Keyword arguments for the function call.",
            nullable=True,
        ),
        working_dir=StringSchema(
            "Optional directory this execution resolves against. Relative paths "
            "(e.g. open('data.txt')) are measured from it, os.getcwd() reports it, "
            "and it is placed at the head of sys.path so 'import <module>' finds .py "
            "files sitting in it — use it to import a skill's own scripts. Must be "
            "inside the workspace; it never widens the workspace boundary. Defaults "
            "to the workspace root.",
            nullable=True,
        ),
        timeout=IntegerSchema(
            description="Timeout in seconds (default 60, max 600).",
            minimum=1,
            maximum=600,
        ),
        max_output_chars=IntegerSchema(
            description="Maximum output characters to return (default 10000, max 50000).",
            minimum=1000,
            maximum=MAX_OUTPUT_CHARS,
            nullable=True,
        ),
        yield_time_ms=IntegerSchema(
            description=(
                "Optional milliseconds to wait before returning output. "
                "When set, a still-running execution returns a session_id that "
                "can be polled with write_stdin."
            ),
            minimum=0,
            maximum=MAX_YIELD_MS,
            nullable=True,
        ),
    )
)
class PythonExecTool(Tool):
    """Execute Python code or call registered functions."""

    _scopes = {"core", "subagent"}

    _MAX_TIMEOUT = 600
    _MAX_OUTPUT = 10_000

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        cfg = getattr(ctx.config, "python_exec", None)
        if cfg is None:
            return True
        return cfg.enable

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        cfg = getattr(ctx.config, "python_exec", None)
        if cfg is None:
            cfg = PythonExecConfig()
        # Accesso diretto (come filesystem.py): un attributo mancante deve
        # essere un errore rumoroso, non un silenzioso "unrestricted" (il
        # vecchio fallback getattr(..., False) apriva in caso di config anomala).
        restrict = ctx.config.restrict_to_workspace
        tool = cls(
            working_dir=str(ctx.workspace),
            timeout=cfg.timeout,
            max_output_chars=cfg.max_output_chars,
            allowed_modules=cfg.allowed_modules,
            blocked_modules=cfg.blocked_modules,
            restrict_to_workspace=restrict,
            workspace=str(ctx.workspace),
        )
        _register_builtin_functions(
            tool.namespace,
            workspace=str(ctx.workspace),
            restrict_to_workspace=restrict,
        )
        return tool

    def __init__(
        self,
        working_dir: str | None = None,
        timeout: int = 60,
        max_output_chars: int = 10_000,
        allowed_modules: list[str] | None = None,
        blocked_modules: list[str] | None = None,
        restrict_to_workspace: bool = False,
        workspace: str | None = None,
        session_manager: Any | None = None,
    ):
        self.working_dir = working_dir or str(get_workspace_path())
        self.timeout = timeout
        self.max_output_chars = max_output_chars
        self.namespace = PythonNamespace(
            working_dir=self.working_dir,
            allowed_modules=allowed_modules,
            blocked_modules=blocked_modules,
            restrict_to_workspace=restrict_to_workspace,
            workspace=workspace,
        )
        self._session_manager = session_manager or DEFAULT_EXEC_SESSION_MANAGER

    @property
    def name(self) -> str:
        return "python_exec"

    @property
    def description(self) -> str:
        return (
            "Execute Python code or call a registered function. "
            "Use code='...' for inline Python (expressions or statements). "
            "Use function='name' with args/kwargs to call registered functions. "
            "Prefer dedicated tools (read_file, grep, apply_patch, web_search, web_fetch) for file/search/web tasks. "
            "Use python_exec for tests, builds, calculations, data processing, "
            "and other logic. Output is truncated at 10000 chars."
        )

    @property
    def exclusive(self) -> bool:
        return True

    async def execute(
        self,
        code: str | None = None,
        function: str | None = None,
        args: list | None = None,
        kwargs: dict | None = None,
        working_dir: str | None = None,
        timeout: int | None = None,
        max_output_chars: int | None = None,
        yield_time_ms: int | None = None,
        **kwargs_extra: Any,
    ) -> str:
        if not code and not function:
            return "Error: Provide 'code' or 'function' parameter."

        # `working_dir` è la base di risoluzione della SOLA chiamata corrente:
        # validata qui per restituire al modello un errore leggibile invece di
        # un traceback, e riazzerata quando non è passata (senza il reset,
        # una chiamata con working_dir avvelenerebbe silenziosamente la
        # risoluzione dei percorsi di tutte le successive che non lo passano).
        try:
            resolved_working_dir = self.namespace._resolve_exec_base(working_dir)
        except OSError as exc:
            return f"Error: {exc}"
        # Il ramo con yield_time_ms esegue in un thread di sessione che chiama
        # il namespace senza argomenti: la base gli arriva da qui.
        self.namespace.exec_base = resolved_working_dir
        self.namespace.working_dir = resolved_working_dir or self.working_dir

        effective_timeout = self._resolve_timeout(timeout)
        effective_max = clamp_session_int(
            max_output_chars, self._MAX_OUTPUT, 1000, MAX_OUTPUT_CHARS,
        )

        if yield_time_ms is not None:
            return await self._execute_session(
                code=code,
                function=function,
                args=args,
                kwargs=kwargs,
                yield_time_ms=yield_time_ms,
                max_output_chars=effective_max,
                timeout=effective_timeout,
            )

        return await run_python_async(
            code=code,
            function=function,
            args=args,
            kwargs=kwargs,
            namespace=self.namespace,
            timeout=effective_timeout,
            max_output_chars=effective_max,
            working_dir=resolved_working_dir,
        )

    async def _execute_session(
        self,
        *,
        code: str | None,
        function: str | None,
        args: list | None,
        kwargs: dict | None,
        yield_time_ms: int,
        max_output_chars: int,
        timeout: int | None,
    ) -> str:
        try:
            session_id, poll = await self._session_manager.start_python(
                code=code,
                function=function,
                args=args,
                kwargs=kwargs,
                # Il turno non attraversa il thread grezzo della sessione:
                # l'involucro costruisce il ponte qui, sul thread dell'event
                # loop. Vedi `_ContextBoundNamespace`.
                namespace=_ContextBoundNamespace(self.namespace),
                timeout=timeout,
                yield_time_ms=clamp_session_int(yield_time_ms, DEFAULT_YIELD_MS, 0, MAX_YIELD_MS),
                owner_session_key=current_request_session_key(),
                max_output_chars=max_output_chars,
            )
            return format_session_poll(session_id, poll)
        except Exception as exc:
            return f"Error executing Python: {exc}"

    def _resolve_timeout(self, timeout: int | None) -> int | None:
        if timeout:
            return min(timeout, self._MAX_TIMEOUT)
        if self.timeout and self.timeout > 0:
            return self.timeout
        return None


# Registrazione esplicita dei tool di questo modulo (Fase 5.3): il
# ToolLoader legge questa lista invece della reflection dir(). Un nuovo
# tool va aggiunto qui esplicitamente.
TOOLS = [PythonExecTool]
