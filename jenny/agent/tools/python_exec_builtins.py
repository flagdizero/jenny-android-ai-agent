"""Funzioni builtin del sandbox ``python_exec`` (estratte da python_exec.py).

`_register_builtin_functions` registra sulla ``PythonNamespace`` gli helper messi
a disposizione dentro il sandbox (I/O file entro workspace, JSON, ecc.), con
enforcement del path. Nessun import runtime verso ``python_exec`` (il tipo
``PythonNamespace`` è solo per annotazione) → nessun ciclo.
"""

from __future__ import annotations

import io
import logging
import os
import time
import types
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jenny.agent.tools.python_exec import PythonNamespace

logger = logging.getLogger("jenny.agent.tools.python_exec")


_SAFE_ENV_KEYS = frozenset({"PATH", "LANG", "PYTHONPATH"})


def _compile_script(code: str, filename: str) -> types.CodeType:
    """Compila *code* SENZA ereditare i ``__future__`` di questo modulo.

    ``exec(code, ns)`` compila ereditando i flag ``__future__`` del frame
    chiamante, e questo file apre con ``from __future__ import annotations``:
    qualunque script caricato con un ``exec`` nudo da qui veniva quindi
    compilato in PEP 563, con le annotazioni ridotte a stringhe. Il sintomo
    misurato (stesso di ``PythonNamespace._compile``, che lo ha chiuso per il
    codice dell'agente): un ``@dataclass`` nello script entra nel ramo
    ``isinstance(type, str)`` di ``dataclasses._process_class``, che risale a
    ``sys.modules.get(cls.__module__).__dict__`` — e un modulo costruito a mano
    non sta in ``sys.modules``, quindi muore con
    "AttributeError: 'NoneType' object has no attribute '__dict__'".

    ``dont_inherit=True`` restituisce allo script la semantica standard
    dell'interprete: se vuole PEP 563 se lo dichiara da sé, come farebbe se
    fosse importato normalmente.
    """
    return compile(code, filename, "exec", dont_inherit=True)


# Da dove vengono gli script della skill wiki: il pacchetto, mai il workspace
# (vedi `_load_wiki_script`). L'ordine di `_WIKI_SCRIPT_SIBLINGS` è quello delle
# dipendenze: ognuno importa per nome solo quelli che lo precedono.
_WIKI_SCRIPTS_PACKAGE = "jenny.skills"
_WIKI_SCRIPTS_REL = "llm-wiki/scripts"
_WIKI_SCRIPT_SIBLINGS = ("reindex_wikis.py", "lint_wiki.py")


def _read_packaged_wiki_script(name: str) -> bytes | None:
    """I byte impacchettati dello script *name* (``None`` se non ci sono)."""
    from jenny.utils.android_assets import read_asset

    return read_asset(_WIKI_SCRIPTS_PACKAGE, f"{_WIKI_SCRIPTS_REL}/{name}")


def _packaged_wiki_scripts_dir() -> Path:
    import jenny.skills

    return Path(jenny.skills.__file__).resolve().parent / _WIKI_SCRIPTS_REL


def _register_builtin_functions(
    ns: PythonNamespace,
    workspace: str | None = None,
    restrict_to_workspace: bool = False,
) -> None:
    """Register commonly used Python functions in the namespace."""
    import json
    import re
    from pathlib import Path

    def _resolution_base() -> str | None:
        """Directory da cui si misura un percorso RELATIVO dentro il sandbox.

        Unico punto in cui questa domanda riceve risposta: la usano sia chi
        APRE i file (``_enforce_path``) sia chi RIPORTA un percorso al modello
        (``path_resolve``, ``path_base``). Tenerle allineate è tutto il punto —
        prima ``path_resolve("out.txt")`` rispondeva ``/out.txt`` (cwd del
        processo) mentre ``read_file("out.txt")`` leggeva dal workspace, quindi
        il modello poteva calcolare un percorso con un builtin e vederselo
        rifiutare da un altro.

        Sotto restrizione è il ``working_dir`` dell'exec in corso (B5) e, in sua
        assenza, la radice del workspace. Senza restrizione è ``None``, cioè "la
        cwd del processo": lì ``open()`` è il builtin nudo e misura da lì, e
        agganciare i soli builtin alla base creerebbe la discordanza che qui si
        sta togliendo.

        La stessa identica risposta la dà ora ``os.getcwd()`` dentro il sandbox
        (``python_exec._reported_working_directory``), e non per coincidenza: in
        entrambe le modalità le due funzioni sono d'accordo per costruzione —
        sotto restrizione ``_active_path_base() or workspace``, fuori la cwd del
        processo, che è ciò a cui la ``getcwd`` patchata delega quando non c'è
        confine. Cambiarne una senza l'altra rimette in piedi la trappola.
        """
        if not restrict_to_workspace:
            return None
        # Import locale: `python_exec` importa questo modulo, il contrario a
        # livello di modulo sarebbe un ciclo.
        from jenny.agent.tools.python_exec import _active_path_base

        return _active_path_base() or workspace

    def _refuse_if_readonly(detail: str) -> None:
        """Ferma una scrittura quando il turno e' in sola lettura.

        Import locale come gli altri di questo modulo: a livello di modulo
        sarebbe un ciclo.
        """
        from jenny.security.workspace_access import current_turn_is_readonly
        from jenny.security.workspace_policy import ReadOnlyTurnError

        if current_turn_is_readonly():
            raise ReadOnlyTurnError(detail)

    def _write_root() -> str | None:
        """La radice entro cui una SCRITTURA deve restare, adesso.

        Non la calcola: la chiede a ``ToolWorkspace.write_root()``, che e' l'unica
        risposta a «dove posso scrivere» (passo T4.4). Qui resta solo la
        conversione a stringa e il fallback sul workspace del costruttore, che
        vale quando non c'e' confine da far rispettare — questa funzione e'
        chiamata solo da ``_enforce_path`` sotto ``restrict_to_workspace``, quindi
        un confine ci vuole comunque.

        Con una sessione-progetto legata e' la cartella del progetto; senza, il
        workspace di sempre. La *lettura* non passa di qui: resta sul workspace,
        perche' la prigione di un progetto e' sulla scrittura (v.
        ``_enforce_path``).
        """
        from jenny.security.workspace_access import current_tool_workspace

        access = current_tool_workspace(
            workspace, restrict_to_workspace=restrict_to_workspace
        )
        root = access.write_root()
        return str(root) if root is not None else workspace

    def _enforce_path(path: str, *, for_write: bool = False) -> Path:
        """Resolve path and enforce the workspace boundary when restricted.

        Un percorso RELATIVO si misura dalla base di ``_resolution_base()``,
        cioè dalla stessa che usa ``open()`` dentro il sandbox. La base si
        sposta, il confine no — ma **il confine non e' lo stesso nei due versi**:

        - in lettura e' il workspace, sempre. Dentro un progetto Jenny deve poter
          leggere la propria skill e le altre wiki se gliele si chiede; e fuori
          dalla cartella privata dell'app non si arriva comunque, perche' il
          permesso di storage non ce l'abbiamo — il confine vero lo mette Android;
        - in scrittura e' la cartella del progetto, quando ce n'e' una legata.
          Questa e' l'isolazione che conta: e' cio' che tiene un progetto lontano
          dai file di un altro, da ``USER.md`` e da ``SOUL.md``.
        """
        from jenny.security.subagent_policy import check_read

        if not restrict_to_workspace:
            from jenny.security.workspace_policy import _safe_expanduser
            resolved = _safe_expanduser(path).resolve()
        else:
            from jenny.agent.tools.python_exec import _path_guard_bypass
            from jenny.security.workspace_policy import resolve_allowed_path

            allowed_root = _write_root() if for_write else workspace
            # La base va letta PRIMA del bypass, che la azzera. Il bypass copre
            # la sola risoluzione: `Path.resolve()` passa da `os.lstat` su ogni
            # prefisso del percorso e sotto guard quei prefissi sono fuori dal
            # workspace, quindi senza bypass ogni chiamata logga una raffica di
            # rifiuti spuri (stessa ragione di `_guarded_os_path`).
            base = _resolution_base()
            with _path_guard_bypass():
                resolved = resolve_allowed_path(path, workspace=base, allowed_root=allowed_root)
        if not for_write:
            # Dentro un subagent i file segreti non si leggono (``subagent_policy``).
            check_read(resolved)
        return resolved

    def _write_path(path: str) -> Path:
        """Percorso di una scrittura: confinato alla cartella del progetto."""
        # La sola lettura si controlla qui e **non** in ``_enforce_path``: quello
        # e' condiviso con ``_wiki_root``, da cui passano anche ``wiki_lint`` e
        # ``wiki_audit``, che leggono. Bloccare la' avrebbe spento due letture
        # per chiudere una scrittura.
        _refuse_if_readonly(f"refused write to {path}")
        resolved = _enforce_path(path, for_write=True)
        # La bacheca di un quaderno ha i suoi imbuti (``BoardWriteError``).
        from jenny.agent.tools.python_exec import _path_guard_bypass, _refuse_guarded_write

        with _path_guard_bypass():
            _refuse_guarded_write(resolved)
        return resolved

    # File I/O
    def read_file(path: str, encoding: str = "utf-8") -> str:
        """Read a file and return its content as string."""
        return _enforce_path(path).read_text(encoding=encoding)

    def write_file(path: str, content: str, encoding: str = "utf-8") -> None:
        """Write content to a file."""
        p = _write_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding=encoding)

    def append_file(path: str, content: str, encoding: str = "utf-8") -> None:
        """Append content to a file."""
        p = _write_path(path)
        with open(p, "a", encoding=encoding) as f:
            f.write(content)

    def list_dir(path: str = ".", pattern: str | None = None) -> list[str]:
        """List directory contents. Optional glob pattern."""
        d = _enforce_path(path)
        if pattern:
            return sorted(str(p) for p in d.glob(pattern))
        return sorted(str(p) for p in d.iterdir())

    def file_exists(path: str) -> bool:
        """Check if a file or directory exists."""
        return _enforce_path(path).exists()

    def read_json(path: str) -> Any:
        """Read and parse a JSON file."""
        return json.loads(_enforce_path(path).read_text())

    def write_json(path: str, data: Any, indent: int = 2) -> None:
        """Write data as JSON to a file."""
        p = _write_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=indent, ensure_ascii=False))

    # Search
    def find_files(directory: str = ".", pattern: str = "**/*") -> list[str]:
        """Find files matching a glob pattern."""
        return sorted(str(p) for p in _enforce_path(directory).glob(pattern))

    def grep_files(directory: str = ".", pattern: str = "", include: str | None = None) -> dict[str, list[str]]:
        """Search for regex pattern in files. Returns {file: [matching_lines]}."""
        results: dict[str, list[str]] = {}
        regex = re.compile(pattern)
        root = _enforce_path(directory)
        glob_pattern = include or "**/*"
        for path in root.glob(glob_pattern):
            if not path.is_file():
                continue
            try:
                text = path.read_text(errors="ignore")
                matches = [line for line in text.splitlines() if regex.search(line)]
                if matches:
                    results[str(path)] = matches
            except (OSError, UnicodeDecodeError):
                continue
        return results

    # HTTP
    def _url_host(url: str) -> str:
        from urllib.parse import urlparse

        try:
            return urlparse(url).hostname or ""
        except ValueError:
            return ""

    def http_get(url: str, headers: dict | None = None, timeout: int = 30) -> str:
        """Make an HTTP GET request and return the response body.

        Redirects are not followed. Only the initial URL is checked against
        the SSRF blocklist (``validate_url_target``); if httpx followed
        redirects itself it would resolve/connect to the ``Location`` target
        with zero re-validation, letting a remote server 3xx-bounce the
        request into loopback/RFC1918/link-local addresses (e.g. the
        gateway's own ``127.0.0.1:<port>``) and bypass the guard entirely.
        See ``jenny/apps/http.py`` for the same rationale/pattern. A
        redirect response is surfaced as a clear error instead of silently
        following it.
        """
        from jenny.security.network import validate_url_target

        ok, error = validate_url_target(url)
        if not ok:
            return f"Error: SSRF blocked: {error}"
        import httpx

        from jenny.security.subagent_policy import mark_untrusted

        # La risposta è testo che non viene dall'utente: il lavoro diventa non
        # fidato appena la si chiede (v. ``security.subagent_policy``).
        mark_untrusted(f"http_get:{_url_host(url)}")
        resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=False)
        if resp.has_redirect_location:
            location = resp.headers.get("location", "")
            return (
                f"Error: server responded with a redirect ({resp.status_code}) to "
                f"'{location}'. Redirects are not followed automatically; call "
                "http_get again with the target URL if you trust it."
            )
        resp.raise_for_status()
        return resp.text

    def http_post(
        url: str,
        data: Any = None,
        json_data: dict | None = None,
        headers: dict | None = None,
        timeout: int = 30,
    ) -> str:
        """Make an HTTP POST request and return the response body."""
        from jenny.security.network import validate_url_target

        ok, error = validate_url_target(url)
        if not ok:
            return f"Error: SSRF blocked: {error}"
        import httpx

        from jenny.security.subagent_policy import mark_untrusted

        mark_untrusted(f"http_post:{_url_host(url)}")
        resp = httpx.post(url, data=data, json=json_data, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.text

    # Data processing
    def json_parse(text: str) -> Any:
        """Parse a JSON string."""
        return json.loads(text)

    def json_dump(obj: Any, indent: int = 2) -> str:
        """Convert an object to JSON string."""
        return json.dumps(obj, indent=indent, ensure_ascii=False)

    def regex_match(pattern: str, text: str) -> list[str]:
        """Find all regex matches in text."""
        return re.findall(pattern, text)

    def regex_replace(pattern: str, replacement: str, text: str) -> str:
        """Replace regex matches in text."""
        return re.sub(pattern, replacement, text)

    # Path utilities
    #
    # `path_join`/`path_parent`/`path_name` restano ARITMETICA LESSICALE sulla
    # stringa: non toccano il filesystem e non pretendono di restituire un
    # percorso assoluto (`path_parent("out.txt")` è `"."`, come in pathlib). Chi
    # vuole una risposta assoluta la chiede a `path_resolve`, che è l'unico di
    # questa famiglia ad ancorarsi a una base.
    def path_join(*parts: str) -> str:
        """Join path components (purely lexical)."""
        return str(Path(*parts))

    def path_resolve(path: str) -> str:
        """Resolve a path to absolute, from the same base the sandbox reads from.

        Un relativo si misura da ``_resolution_base()``, cioè esattamente da
        dove lo misurano ``read_file``/``open`` in questa stessa esecuzione.

        Due scelte deliberate:

        * risoluzione LOGICA (``os.path.abspath``, symlink non dereferenziati)
          invece di ``Path.resolve()``. Quest'ultima passa da ``os.lstat`` su
          ogni prefisso: sotto guard i prefissi sono fuori dal confine e la sola
          richiesta di un percorso produceva una raffica di WARNING "refused",
          per giunta dentro lo stderr che il modello legge. Non serve nemmeno
          che il file esista, e non deve: si usa anche per costruire il percorso
          di un file da CREARE.
        * nessun controllo di confine. Questa è aritmetica su stringhe: può
          nominare un percorso fuori dal workspace, ma chi lo USA lo rifiuta
          come sempre. Il rifiuto appartiene all'operazione, non al calcolo.
        """
        from jenny.security.workspace_policy import _resolve_logical_path

        return str(_resolve_logical_path(path, _resolution_base()))

    def path_base() -> str:
        """Directory that relative paths are measured from in this execution.

        Nato quando ``os.getcwd()`` non era patchata e rispondeva ``/`` in ogni
        caso: era l'unico modo di sapere da dove si misura davvero. Ora
        ``os.getcwd()`` risponde la stessa cosa (vedi
        ``python_exec._reported_working_directory``) e questo builtin resta come
        il modo ESPLICITO di chiederlo — stessa risposta, nome che dice cosa
        vuol dire. Il ``or os.getcwd()`` non è un secondo parere: è il ramo
        senza restrizione, dove la base È la cwd del processo e la ``getcwd``
        patchata delega comunque a quella vera.
        """
        return str(_resolution_base() or os.getcwd())

    def path_parent(path: str) -> str:
        """Get parent directory (purely lexical)."""
        return str(Path(path).parent)

    def path_name(path: str) -> str:
        """Get file/directory name."""
        return Path(path).name

    # System
    def get_env(key: str) -> str | None:
        """Get an environment variable. Only a small allowlist (PATH, LANG,
        PYTHONPATH) is accessible; any other key returns None, same as if
        it were unset."""
        if key not in _SAFE_ENV_KEYS:
            return None
        return os.environ.get(key)

    def list_env() -> dict[str, str]:
        """List safe environment variables."""
        return {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}

    def platform_info() -> dict[str, str]:
        """Get platform information."""
        import platform
        return {
            "system": "Android",
            "release": platform.release(),
            "python": platform.python_version(),
            "machine": platform.machine(),
        }

    # Date/Time
    def now_iso() -> str:
        """Get current datetime in ISO format."""
        from datetime import datetime
        return datetime.now().isoformat()

    def timestamp() -> float:
        """Get current Unix timestamp."""
        return time.time()

    # Hashing
    def md5(text: str) -> str:
        """Compute MD5 hash of text."""
        import hashlib
        return hashlib.md5(text.encode()).hexdigest()

    def sha256(text: str) -> str:
        """Compute SHA-256 hash of text."""
        import hashlib
        return hashlib.sha256(text.encode()).hexdigest()

    # Encoding
    def base64_encode(text: str) -> str:
        """Base64 encode text."""
        import base64
        return base64.b64encode(text.encode()).decode()

    def base64_decode(encoded: str) -> str:
        """Base64 decode text."""
        import base64
        return base64.b64decode(encoded).decode()

    def url_encode(text: str) -> str:
        """URL encode text."""
        from urllib.parse import quote
        return quote(text)

    def url_decode(encoded: str) -> str:
        """URL decode text."""
        from urllib.parse import unquote
        return unquote(encoded)

    # ── LLM Wiki ──
    def _load_wiki_script(name: str):
        """Carica uno script della skill ``llm-wiki`` dalla copia **impacchettata**.

        Il sorgente viene da ``jenny/skills/llm-wiki/scripts`` (sul telefono
        l'asset dell'APK, via :func:`read_asset`): gli stessi byte che l'avvio
        estrae in ``<workspace>/skills``. **Mai dalla copia del workspace**, e
        la ragione è il bypass qui sotto: quella
        cartella il modello la scrive con ``write_file``, e caricarla dentro
        ``_path_guard_bypass()`` eseguiva il suo codice di primo livello senza
        confine di percorso. Il vecchio commento diceva che qui «non c'è niente
        da contenere» perché il percorso è fisso: il percorso sì, il contenuto
        no.

        **Il bypass resta, e ora copre solo codice nostro.** Leggere il
        pacchetto (una directory fuori dal workspace, o l'APK) è un'operazione
        dell'host che la guardia rifiuterebbe; e dentro un progetto la radice di
        lettura di un subagent è la cartella del progetto, quindi senza bypass
        ``wiki_lint``/``wiki_audit``/``wiki_scaffold`` erano irraggiungibili (il
        26/08, sul telefono) — con loro il «hard gate» della skill. Il codice di
        primo livello degli script definisce funzioni e costanti; quel che fanno
        dopo — leggere la wiki, scrivere il proprio stato — gira fuori dal
        bypass e resta guardato come prima.

        Gli script si importano fra loro per nome (``import reindex_wikis``,
        dopo un ``sys.path.insert`` della propria cartella): i fratelli di
        ``_WIKI_SCRIPT_SIBLINGS`` si caricano prima, dalla stessa copia, e stanno
        in ``sys.modules`` solo per la durata del caricamento, così l'``import``
        non cade sulla prima cartella di ``sys.path`` che ne ha uno.
        """
        import sys

        from jenny.agent.tools.python_exec import _path_guard_bypass

        with _path_guard_bypass():
            previous = {
                sib.removesuffix(".py"): sys.modules.get(sib.removesuffix(".py"))
                for sib in _WIKI_SCRIPT_SIBLINGS
            }
            try:
                loaded: dict[str, types.ModuleType] = {}
                for sib in _WIKI_SCRIPT_SIBLINGS:
                    if sib == name:
                        break
                    loaded[sib] = sys.modules[sib.removesuffix(".py")] = _exec_packaged(sib)
                return loaded.get(name) or _exec_packaged(name)
            finally:
                for mod_name, mod in previous.items():
                    if mod is None:
                        sys.modules.pop(mod_name, None)
                    else:
                        sys.modules[mod_name] = mod

    def _exec_packaged(name: str) -> types.ModuleType:
        """Esegue lo script impacchettato *name* in un modulo nuovo (sotto bypass)."""
        import sys

        rel = f"{_WIKI_SCRIPTS_REL}/{name}"
        source = _read_packaged_wiki_script(name)
        if source is None:
            raise FileNotFoundError(f"Packaged wiki script not found: {rel}")
        mod_name = name.removesuffix(".py")
        mod = types.ModuleType(mod_name)
        # `__file__` nella cartella del pacchetto: gli script ne ricavano la
        # propria directory (`sys.path.insert(0, dirname(abspath(__file__)))`).
        # Sul telefono quella cartella non ha i sorgenti, e non serve: i
        # fratelli sono già in `sys.modules`.
        script_dir = str(_packaged_wiki_scripts_dir())
        mod.__file__ = os.path.join(script_dir, name)
        logger.debug("Loading packaged wiki script %s", rel)
        # Un `@dataclass` risale a `sys.modules[cls.__module__]` mentre la
        # classe nasce: il modulo ci sta per la durata dell'exec.
        before = sys.modules.get(mod_name)
        sys.modules[mod_name] = mod
        try:
            # `_compile_script`, non `exec(code, ...)`: vedi lì il perché.
            exec(_compile_script(source.decode("utf-8"), mod.__file__), mod.__dict__)
        finally:
            if before is None:
                sys.modules.pop(mod_name, None)
            else:
                sys.modules[mod_name] = before
            # Lo script ha messo la propria cartella in testa a `sys.path`:
            # toglierla evita che la lista cresca a ogni chiamata.
            if script_dir in sys.path:
                sys.path.remove(script_dir)
        return mod

    def _wiki_root(root: str) -> str:
        """Porta *root* alla stessa base degli altri builtin, prima di passarlo.

        Gli script della wiki fanno ``Path(root)`` per conto proprio, e la loro
        idea di "relativo" è la cwd del processo (``/`` in ``python_exec``):
        ``wiki_lint("wikis/erbario")`` finiva quindi su ``/wikis/erbario`` mentre
        ``read_file("wikis/erbario/...")`` leggeva dal workspace. Passare per
        ``_enforce_path`` allinea la base e, sotto restrizione, fa fallire subito
        e con un messaggio chiaro una root fuori dal confine invece di lasciar
        morire lo script più a valle.
        """
        # Confine di **scrittura**: da qui passano `wiki_scaffold` (che crea file)
        # insieme a `wiki_lint`/`wiki_audit` (che leggono). Un solo helper per
        # tre operazioni, quindi vince la piu' restrittiva: lasciar scaffoldare
        # fuori dal progetto sarebbe una breccia, mentre non poter lintare la
        # wiki di un altro progetto e' una scomodita'.
        return str(_enforce_path(root, for_write=True))

    def wiki_scaffold(root: str, title: str) -> str:
        """Bootstrap a new LLM Wiki directory structure at root."""
        _refuse_if_readonly(f"refused wiki_scaffold in {root}")
        import contextlib

        mod = _load_wiki_script("scaffold.py")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod.scaffold(_wiki_root(root), title)
        return buf.getvalue()

    def wiki_lint(root: str) -> str:
        """Run health check on an LLM Wiki. Returns issues found.

        Cattura **solo stdout** — lo script scrive lì tutto, errori compresi — e
        quando stdout è vuoto lo dice col codice di uscita invece del vecchio
        "No output": quella stringa faceva passare per «nessun problema» un lint
        che non ha girato affatto.

        **Se il lint scoppia, il buffer parziale torna comunque**, con in coda una
        riga che dice che è parziale e perché. La ragione è tutta nella differenza
        fra i due esiti per chi chiama: lasciando salire l'eccezione, quel che il
        modello riceve da ``python_exec`` è un traceback e **zero** risultati —
        anche i quaranta già stampati prima del passo che è caduto. Non sono
        risultati dubbi: sono passi conclusi, ognuno col suo conteggio.
        Buttarli non rende nessuno più sicuro, rende solo il difetto più caro.

        E il verso opposto — «torna il parziale e taci» — è il difetto che T6.6 ha
        chiuso: un report senza la riga di riepilogo si legge come un report. Il
        modello non conta i passi che si aspettava, quindi l'*assenza* del
        riepilogo non è un segnale; un 🔴 che nomina l'eccezione lo è. Quindi
        tornano entrambe le cose, in quest'ordine.
        """
        import contextlib
        import traceback

        mod = _load_wiki_script("lint_wiki.py")
        # **Il confine si risolve fuori dal ``try``.** ``_wiki_root`` solleva
        # ``WorkspaceBoundaryError`` su una root fuori dal workspace, e quella
        # deve **salire**: è un rifiuto della policy, non un lint andato male, e
        # ridurla a una stringa nel report la renderebbe indistinguibile da una
        # wiki con dei problemi (v.
        # ``tests/agent/tools/test_python_exec_builtins_paths.py``).
        target = _wiki_root(root)
        buf = io.StringIO()
        code: int | None = None
        crash: str | None = None
        trace: str | None = None
        with contextlib.redirect_stdout(buf):
            try:
                code = mod.lint(target)
            except Exception as exc:  # noqa: BLE001 — vedi il docstring
                crash = f"{type(exc).__name__}: {exc}"
                # Lo stack si formatta qui, dentro l'``except``, e si scrive
                # **fuori** dal ``redirect_stdout``: un handler su stdout
                # finirebbe dentro il buffer che poi torna al modello.
                trace = traceback.format_exc()
        if crash is not None:
            # Il ritorno è per il modello; questo è per chi legge il telefono
            # dopo, con lo stack che la stringa non porta.
            logger.error("wiki_lint crashed on %s:\n%s", root, trace)
            return (
                f"{buf.getvalue()}\n"
                f"🔴 the lint crashed before it finished: {crash}\n"
                "   (everything above was already computed and stands. What comes\n"
                "    after the last check printed never ran, so this is NOT a clean\n"
                "    wiki and the count above is not the total.)"
            )
        return buf.getvalue() or f"(the lint printed nothing — it exited {code})"

    def wiki_audit(root: str, mode: str = "open") -> str:
        """List audit feedback grouped by target. Modes: open, resolved, all."""
        import contextlib

        mod = _load_wiki_script("audit_review.py")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod.main(_wiki_root(root), mode)
        return buf.getvalue()

    # Register all
    functions = {
        "read_file": read_file,
        "write_file": write_file,
        "append_file": append_file,
        "list_dir": list_dir,
        "file_exists": file_exists,
        "read_json": read_json,
        "write_json": write_json,
        "find_files": find_files,
        "grep_files": grep_files,
        "http_get": http_get,
        "http_post": http_post,
        "json_parse": json_parse,
        "json_dump": json_dump,
        "regex_match": regex_match,
        "regex_replace": regex_replace,
        "path_join": path_join,
        "path_resolve": path_resolve,
        "path_base": path_base,
        "path_parent": path_parent,
        "path_name": path_name,
        "get_env": get_env,
        "list_env": list_env,
        "platform_info": platform_info,
        "now_iso": now_iso,
        "timestamp": timestamp,
        "md5": md5,
        "sha256": sha256,
        "base64_encode": base64_encode,
        "base64_decode": base64_decode,
        "url_encode": url_encode,
        "url_decode": url_decode,
        "wiki_scaffold": wiki_scaffold,
        "wiki_lint": wiki_lint,
        "wiki_audit": wiki_audit,
    }
    for name, func in functions.items():
        ns.register_function(name, func)
