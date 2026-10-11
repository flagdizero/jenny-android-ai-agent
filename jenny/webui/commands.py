"""Comandi della WebUI indipendenti dal trasporto (scritture con payload).

La superficie ``/api/`` del gateway è servita dall'hook di handshake di
``websockets``, che **non legge mai il body di una richiesta**
(``websockets.http11.Request`` non lo espone). È un trasporto di sola lettura:
query string e header, 8192 byte per riga (``MAX_LINE_LENGTH``) e solo
ISO-8859-1 — ``fetch`` rifiuta lato browser un header con un'emoji dentro. Chi
doveva spedire del contenuto se n'è accorto tre volte e ha inventato tre
dialetti dello stesso trucco (header grezzo, percent-encodato, base64); quello
grezzo, ``/api/workspace/write``, non poteva funzionare affatto: salvare
``SOUL.md`` — italiano, con emoji, oltre 8 KB — falliva sempre.

Qui vive la logica di quelle operazioni, senza sapere da dove arrivi la
chiamata: un dizionario di parametri già decodificati entra, un dizionario
JSON-serializzabile esce, e gli errori sono ``CommandError`` con un codice
chiuso. Il trasporto che le espone è l'RPC WebSocket
(:mod:`jenny.channels.ws_rpc`), l'unico canale verso la WebView che sappia
trasportare contenuto: framed, UTF-8, autenticato all'handshake.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.security.workspace_policy import is_path_within
from jenny.utils.wiki_paths import WIKI_INDEX_FILENAME, safe_wiki_page_path
from jenny.webui.workspace_files import os_error_text

# Tetto sul contenuto di una singola scrittura. Allineato al ``max_size`` di
# ``workspace_files.read_file``: ciò che l'editor non può aprire non deve
# nemmeno poter essere salvato, e il limite deve arrivare all'utente come un
# messaggio, non come un troncamento silenzioso del trasporto.
MAX_WRITE_BYTES = 1_000_000

# La riga di scope sta nel frontmatter dell'`AGENTS.md`, che e' YAML: una riga
# sola, e corta abbastanza da stare nel registro accanto al nome della wiki.
MAX_PROJECT_SEED_CHARS = 500


class CommandError(Exception):
    """Errore di un comando, con un codice che il trasporto sa tradurre.

    I codici sono un insieme chiuso — ``bad_request``, ``forbidden``,
    ``not_found``, ``too_large``, ``conflict``, ``name_taken``, ``unavailable``,
    ``internal`` — così un adapter può mapparli (a uno status HTTP, a un frame
    WS) senza indovinare dal testo del messaggio, e un client può dire nella
    propria lingua i rifiuti che si aspetta.

    ``name_taken``: il nome chiesto è già di qualcos'altro (il nome nuovo di un
    quaderno è già di una cartella o di una conversazione; la destinazione di
    ``workspace.rename``/``workspace.copy`` esiste già).

    ``conflict`` è l'unico che non parla della richiesta ma del *mondo*: la
    richiesta era buona, e il mondo si è mosso sotto — il file è cambiato da
    quando il client l'ha letto (``page.write``), oppure Jenny sta ancora
    scrivendo in quel quaderno (``project.rename``, ``project.delete``). Chi lo
    riceve non deve correggere quel che ha mandato: rilegge, o riprova quando
    lei ha finito.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CommandContext:
    """Dipendenze strette dei comandi, iniettate dal composition root.

    Stesso stile dei ``Callable`` che le famiglie di route ricevono in
    ``webui.ws_http``: getter risolti a call-time, così un test (o un cambio di
    workspace a runtime) non deve ricostruire nulla.
    """

    get_workspace_root: Callable[[], Path]
    # Sgombera dalla cache in memoria una sessione i cui file stanno per sparire
    # (``project.delete``). E' un campo **obbligatorio** e non un default a
    # no-op: un sito di costruzione che se lo dimenticasse lascerebbe la
    # sessione viva, e il primo salvataggio riscriverebbe il file appena tolto —
    # cioe' l'orfano, di nuovo. Meglio un TypeError all'avvio.
    invalidate_session: Callable[[str], None]
    # Le sessioni sotto cui qualcosa scrive adesso (``AgentLoop.busy_session_keys``):
    # un turno, un subagent lanciato da li', una passata del giardiniere,
    # l'autocompact. ``project.rename`` e ``project.delete`` le rifiutano:
    # sgomberare la cache non ferma chi la sessione ce l'ha gia' in mano, e a fine
    # lavoro scriverebbe sotto il nome vecchio — una chat senza cartella accanto a
    # quella spostata, o al posto di quella cancellata. Obbligatorio per la stessa
    # ragione di ``invalidate_session``.
    busy_session_keys: Callable[[], Collection[str]]
    # I due ganci che le rotte dei settings chiamano dopo un salvataggio
    # riuscito, per i comandi che ne hanno preso il posto:
    # ``settings.provider.update`` ricostruisce provider e modello,
    # ``telegram.save`` riavvia il canale. Facoltativi e non obbligatori come i
    # due qui sopra perche' i siti di costruzione dei test sono decine e nessuno
    # di loro salva un provider; il composition root li passa
    # (``gateway_services``), e se mancano il comando lo dice nel log invece di
    # tacere un provider nuovo che non entra in servizio fino al riavvio.
    on_settings_changed: Callable[[], None] | None = None
    on_telegram_changed: Callable[[], None] | None = None
    # Quel che serve a ``onboarding.save``: la sessione in cui scrivere il
    # saluto, e l'evento su cui l'agente differito aspetta per nascere. Senza
    # l'evento l'onboarding salva e l'agente non parte fino al riavvio.
    session_manager: Any | None = None
    onboarding_event: Any | None = None
    # Lo scheduler, per i cron del quaderno: togliere la sezione di un cron dal
    # cassetto toglie il job, e la pausa passa da li'. Facoltativo come i ganci
    # qui sopra; senza, quei comandi rispondono ``unavailable``.
    get_cron_service: Callable[[], Any | None] | None = None
    # Per ``project.greet``: il bus su cui il saluto va alla chat, e la coppia
    # ``(provider, modello)`` dell'agente, risolta a call-time perche' l'agente
    # nasce dopo il gateway (onboarding) e cambia provider a caldo. Facoltativi:
    # senza bus il saluto non si scrive, senza modello si scrive la frase fissa.
    bus: Any | None = None
    get_llm: Callable[[], tuple[Any, str] | None] | None = None


Command =Callable[[CommandContext, Mapping[str, Any]], Awaitable[dict[str, Any]]]


def _require_str(params: Mapping[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CommandError("bad_request", f"{key} required")
    return value


def _require_workspace_flag(attr: str, code: str, message: str) -> None:
    """Verifica un flag booleano di ``config.workspace``, fail-closed.

    Se ``load_config()`` solleva NON si prosegue verso il filesystem: un errore
    di configurazione non deve scavalcare in silenzio il gate di sicurezza
    (stessa regola di ``WorkspaceRoutes._require_workspace_flag``).
    """
    from jenny.config.loader import load_config

    try:
        allowed = bool(getattr(load_config().workspace, attr))
    except Exception:
        raise CommandError("unavailable", "workspace configuration unavailable") from None
    if not allowed:
        raise CommandError(code, message)


def _require_wiki_enabled(*, fail_closed: bool = False) -> None:
    """``config.wiki.enabled``, con lo stesso fail-open storico della route.

    A differenza del workspace, una config illeggibile qui non blocca: la route
    HTTP si comportava così (``except Exception: pass``) e la wiki non è un gate
    di sicurezza sul filesystem, è una feature che può essere spenta.

    *fail_closed* è per ``audit.create``, che viene da una route
    (``/api/audit/create``) il cui gate era già passato a fail-closed
    (``WikiRoutes._check_wiki_enabled``): spostarla sul WebSocket non deve
    riaprirlo.
    """
    from jenny.config.loader import load_config

    try:
        enabled = bool(load_config().wiki.enabled)
    except Exception:
        if fail_closed:
            logger.exception("wiki gate: could not read config; refusing")
            raise CommandError("unavailable", "wiki is unavailable") from None
        return
    if not enabled:
        raise CommandError("unavailable", "wiki is disabled")


def _skill_scripts_dir(ctx: CommandContext) -> Path:
    """Il checkout della skill `llm-wiki` nel workspace, dove sta lo scaffolder."""
    return ctx.get_workspace_root() / "skills" / "llm-wiki" / "scripts"


def _wikis_dir(ctx: CommandContext) -> Path:
    from jenny.config.loader import load_config

    try:
        subdir = load_config().wiki.wikis_dir
    except Exception:
        subdir = "wikis"
    return ctx.get_workspace_root() / subdir


def _is_live_config(path: Path) -> bool:
    """*path* (gia' risolto) e' il ``config.json`` che il gateway legge?"""
    from jenny.config.loader import get_config_path

    try:
        return path == get_config_path().resolve()
    except OSError:
        return False


async def _write_live_config(content: str, base: str) -> str:
    """Il salvataggio di ``config.json`` dall'editor, attraverso ``store.mutate``.

    ``workspace.write`` lo riscriveva a mano come un file qualunque:
    ``chmod 600`` perso — le chiavi API leggibili da chiunque
    abbia il permesso di leggere lo storage dell'app —, niente ``.bak``, niente
    lock, e la copia che l'editor aveva aperto minuti prima cancellava in
    silenzio quel che le Impostazioni avevano scritto nel frattempo. La regola
    di ``AGENTS.md`` e' che ``config.json`` si scrive solo da ``mutate``.

    Il contenuto si valida **prima** del lock, come vuole ``mutate`` (niente di
    lento la' dentro): JSON, un oggetto, e lo schema. Un file che il loader non
    sapesse leggere finirebbe in quarantena al prossimo avvio, e il gateway
    ripartirebbe coi default: si rifiuta qui, dicendolo. Dentro il lock ogni
    campo dello schema prende il valore scritto; le chiavi che questa versione
    non conosce restano quelle del file, come per ogni altro scrittore.

    **``base`` e' il testo che l'editor aveva aperto.** Il lock da solo non basta:
    l'editor rimanda il file intero, e ogni campo prenderebbe il valore di una
    copia vecchia di minuti — quel che le Impostazioni hanno scritto intanto
    sparirebbe. Dentro il lock si rilegge il file: se non e' piu' ``base``, la
    risposta e' ``conflict`` e non si scrive niente, come per ``page.write``.

    Restituisce il testo ora su disco: ``mutate`` lo riserializza a modo suo, e
    il salvataggio successivo deve confrontarsi con quello, non con quel che
    l'editor aveva mandato.
    """
    import json

    from jenny.config import store
    from jenny.config.loader import get_config_path
    from jenny.config.schema import Config

    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        raise CommandError(
            "bad_request",
            f"config.json is not valid JSON (line {exc.lineno}, column {exc.colno}): "
            "nothing was saved",
        ) from None
    if not isinstance(raw, dict):
        raise CommandError("bad_request", "config.json must be a JSON object: nothing was saved")
    try:
        written = Config.model_validate(raw)
    except (TypeError, ValueError) as exc:
        raise CommandError(
            "bad_request", f"config.json does not fit the settings ({exc}): nothing was saved"
        ) from None

    config_path = get_config_path()

    def _apply(config: Config) -> None:
        # Sotto il lock di ``mutate``: nessun altro scrittore della config puo'
        # passare fra questo confronto e la scrittura.
        if _lf(config_path.read_text(encoding="utf-8")) != _lf(base):
            raise CommandError("conflict", "config.json changed on disk: nothing was saved")
        for name in type(config).model_fields:
            setattr(config, name, getattr(written, name))

    await store.mutate(_apply)
    # Letto senza ``await`` in mezzo: il corpo di ``mutate`` e' sincrono, quindi
    # nessuno scrittore dell'event loop si infila fra la sua scrittura e questa.
    return config_path.read_text(encoding="utf-8")


def _clear_taint(root: Any, full_path: Any) -> None:
    from pathlib import Path

    from jenny.security.taint_ledger import ledger_for

    try:
        rel = Path(full_path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        return
    ledger_for(Path(root)).clear(rel)


async def workspace_write(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Scrive un file di testo del workspace (salvataggio dall'editor WebUI).

    ``base``, facoltativo, e' il testo da cui l'editor e' partito: se il file su
    disco non e' piu' quello la risposta e' ``conflict`` e non si scrive niente
    (lo stesso patto di ``page.write``). Per ``config.json`` e' obbligatorio, v.
    :func:`_write_live_config`. Chi scrive un file che possiede da solo (le
    puntine della mappa, la sua disposizione) non lo manda.
    """
    from jenny.webui.workspace_files import validate_path, write_file

    rel_path = _require_str(params, "path")
    content = params.get("content", "")
    if not isinstance(content, str):
        raise CommandError("bad_request", "content must be a string")
    base = params.get("base")
    if base is not None and not isinstance(base, str):
        raise CommandError("bad_request", "base must be a string")
    size = len(content.encode("utf-8"))
    if size > MAX_WRITE_BYTES:
        raise CommandError(
            "too_large",
            f"file too large to save ({size} > {MAX_WRITE_BYTES} bytes)",
        )

    _require_workspace_flag("enabled", "unavailable", "workspace is disabled")
    _require_workspace_flag("allow_write", "forbidden", "workspace writes are disabled")

    try:
        full_path = validate_path(ctx.get_workspace_root(), rel_path)
        if _is_live_config(full_path):
            if base is None:
                raise CommandError(
                    "bad_request",
                    "saving config.json needs base, the text the editor opened: "
                    "nothing was saved",
                )
            on_disk = await _write_live_config(content, base)
            return {"path": rel_path, "bytes": size, "content": on_disk}
        # Fuori dall'event loop: ``write_file`` fa un atomic_write con fsync, e
        # fino a 1 MB di disco su una CPU Android sono centinaia di ms in cui il
        # gateway non risponderebbe a nessun altro (stessa ragione per cui il
        # decode dei media sta in un thread, v. ``_save_envelope_media``).
        if base is None:
            await asyncio.to_thread(write_file, full_path, content)
        else:
            await asyncio.to_thread(_write_file_unchanged, full_path, content, base)
        # Il file ora è tuo per intero: se un lavoro non fidato l'aveva scritto,
        # chi lo legge da qui in poi non eredita più quel taint
        # (``security.taint_ledger``).
        _clear_taint(ctx.get_workspace_root(), full_path)
    except ValueError as exc:
        raise CommandError("bad_request", str(exc)) from exc
    except FileNotFoundError as exc:
        raise CommandError("not_found", "path not found") from exc
    except PermissionError as exc:
        raise CommandError("forbidden", "permission denied") from exc
    except OSError as exc:
        raise CommandError("bad_request", os_error_text(exc)) from exc
    return {"path": rel_path, "bytes": size}


# ---------------------------------------------------------------------------
# workspace.delete / workspace.rename / workspace.copy
# ---------------------------------------------------------------------------
#
# Fino al 26/09/2026 erano tre GET (``/api/workspace/delete``, ``rename``,
# ``copy``): scritture sul disco su una superficie che il gateway
# vuole di sola lettura, e che una qualunque ``<img src>`` con il token
# nell'indirizzo poteva far partire. Per questo stanno
# qui, accanto a ``project.delete`` e ``page.write``, sulla superficie
# autenticata all'handshake che la WebView usa per cio' che cambia il disco.


@contextmanager
def _fs_errors() -> Iterator[None]:
    """Traduce gli errori del filesystem nei codici di ``CommandError``.

    La stessa scala delle rotte del file manager (``WorkspaceRoutes.dispatch``):
    ``ValueError`` (il gate dei percorsi) → ``bad_request``, file che non c'e'
    → ``not_found``, permesso negato → ``forbidden``, il resto → ``bad_request``.
    """
    try:
        yield
    except ValueError as exc:
        raise CommandError("bad_request", str(exc)) from exc
    except FileNotFoundError as exc:
        raise CommandError("not_found", "path not found") from exc
    except FileExistsError as exc:
        # Rinomina e copia non sovrascrivono (``workspace_files``): il nome e'
        # gia' di qualcos'altro, che e' esattamente ``name_taken``.
        raise CommandError("name_taken", "the destination already exists") from exc
    except PermissionError as exc:
        raise CommandError("forbidden", "permission denied") from exc
    except OSError as exc:
        raise CommandError("bad_request", os_error_text(exc)) from exc


def _notebooks_dir(workspace_root: Path) -> Path:
    """La cartella dei quaderni (``wiki.wikis_dir``), **risolta**.

    Risolta perche' si confronta con percorsi che escono da ``validate_path``,
    che risolve: su Android ``/data/user/0/…`` e ``/data/data/…`` sono la stessa
    cartella per due nomi, e un confronto testuale fra le due forme direbbe di
    no a ogni domanda.
    """
    try:
        from jenny.config.loader import load_config

        subdir = load_config().wiki.wikis_dir or "wikis"
    except Exception:  # noqa: BLE001 — senza config si usa il nome di default
        subdir = "wikis"
    return (workspace_root / subdir).resolve()


def _delete_refusal(workspace_root: Path, target: Path) -> str | None:
    """Il motivo per cui *target* non si cancella dal file manager, o ``None``.

    Tre rifiuti, dal piu' largo. La radice del workspace:
    ``path=.`` — o qualunque percorso che ci si risolva — faceva la ``rmtree``
    di tutto, config e sessioni comprese. Una cartella che **contiene** dei
    quaderni — ``wikis/`` stessa o un suo antenato: il rifiuto dei progetti qui
    sotto guarda solo i figli diretti di ``wikis/``, e da un gradino piu' su si
    cancellavano tutti insieme, ognuno con la sua chat lasciata orfana. Poi il
    singolo progetto (:func:`_project_delete_refusal`).
    """
    if target == workspace_root.resolve():
        return "the workspace itself cannot be deleted from the file browser"
    notebooks = _notebooks_inside(workspace_root, target)
    if notebooks:
        return (
            f"this folder holds notebooks ({_names(notebooks)}): each has a conversation "
            "that lives outside this tree. Delete them one at a time from Notebooks, "
            "then the folder."
        )
    return _project_delete_refusal(workspace_root, target)


def _rename_refusal(workspace_root: Path, source: Path) -> str | None:
    """Il motivo per cui *source* non si rinomina dal file manager, o ``None``.

    Stessa geografia della cancellazione, per la stessa ragione:
    la chat di un quaderno sta fuori dal suo albero ed e'
    legata al **nome** della cartella. Spostare la cartella — o una che la
    contiene — lascia la chat sotto il nome vecchio: orfana, e pronta per il
    primo quaderno che lo riprende. Il rinomino di un quaderno e'
    ``project.rename``, che sposta anche la chat e le pagine in casa.
    """
    if source == workspace_root.resolve():
        return "the workspace itself cannot be renamed"
    notebooks = _notebooks_inside(workspace_root, source)
    if notebooks:
        return (
            f"this folder holds notebooks ({_names(notebooks)}): moving it would leave "
            "their conversations behind. Rename a notebook from Notebooks, which moves "
            "its conversation too."
        )
    name = _notebook_at(workspace_root, source)
    if name is not None:
        return (
            f"`{name}` is a notebook: its conversation is tied to this folder's name. "
            "Rename it from Notebooks, which moves the conversation with it."
        )
    return None


def _notebooks_inside(workspace_root: Path, target: Path) -> list[str]:
    """I quaderni che *target* contiene, se e' la cartella dei quaderni o un suo
    antenato; altrimenti nessuno."""
    from jenny.utils.wiki_paths import is_wiki_root

    wikis_dir = _notebooks_dir(workspace_root)
    if wikis_dir != target and target not in wikis_dir.parents:
        return []
    try:
        return sorted(child.name for child in wikis_dir.iterdir() if is_wiki_root(child))
    except OSError:
        return []


def _names(names: list[str]) -> str:
    return ", ".join(names[:5]) + (", …" if len(names) > 5 else "")


def _notebook_at(workspace_root: Path, target: Path) -> str | None:
    """Il nome del quaderno di cui *target* e' la radice o la ``wiki/``, o ``None``.

    Solo i figli diretti di ``wikis_dir``: ``is_wiki_root`` da solo direbbe di si'
    a qualunque cartella che contenga una ``wiki/``, e bloccherebbe operazioni
    legittime altrove nel workspace. E solo i nomi che possono essere il nome di
    una conversazione (``wikis/Ricerca ETNA`` no, v. ``_collect_projects``): una
    cartella che non ha una chat non ha niente da orfanare, e ``project.delete``/
    ``project.rename`` la rifiuterebbero proprio per quel nome — rifiutarla anche
    qui la renderebbe intoccabile da qualunque porta.
    """
    from jenny.session.keys import is_valid_project_name
    from jenny.utils.wiki_paths import is_wiki_root

    wikis_dir = _notebooks_dir(workspace_root)
    if target.parent == wikis_dir and is_wiki_root(target):
        name = target.name
    elif (
        target.name == "wiki"
        and target.parent.parent == wikis_dir
        and is_wiki_root(target.parent)
    ):
        name = target.parent.name
    else:
        return None
    return name if is_valid_project_name(name) else None


def _project_delete_refusal(workspace_root: Path, target: Path) -> str | None:
    """Il motivo per cui *target* non si cancella da qui, o ``None``.

    **La delete del file manager non deve poter cancellare un progetto**, e il
    perche' non e' che sia pericolosa: e' che e' *parziale*. Un progetto vive in
    due domini — l'albero sotto ``wikis/<nome>/`` e le quattro tracce della sua
    conversazione, che stanno altrove (v.
    ``session/project_rename.py::project_trace_paths``). Una ``rmtree`` raggiunge
    il primo e non sa del secondo, quindi libera il *nome* senza liberare la
    conversazione: il progetto successivo creato con quel nome se la riprende
    tutta. Riprodotto sul telefono il 24/08/2026.

    Il rifiuto **dice dove**, che e' la forma degli altri rifiuti di questo
    codice (``command/builtin.py::_gardener_no_target``, il rifiuto di
    ``journal_append`` fuori da un progetto): su un telefono un divieto che non
    indica la strada e' un vicolo cieco.

    Due bersagli, non uno. La radice del progetto e' quello ovvio; la sua
    ``wiki/`` e' lo stesso guasto per un'altra porta, perche' senza quella
    cartella ``is_wiki_root`` diventa falso e il progetto sparisce dal picker
    **con la chat ancora attaccata al nome** — cioe' di nuovo l'orfano.

    Quali cartelle sono un progetto lo dice :func:`_notebook_at`.

    Era in ``webui/workspace_routes.py`` finche' la cancellazione era una GET.
    """
    name = _notebook_at(workspace_root, target)
    if name is None:
        return None
    return (
        f"`{name}` is a project, not just a folder: its conversation lives outside "
        "this tree, and deleting the folder here would leave that behind under a name "
        "anything else could take. Deleting a project is its own operation and it "
        "removes both — the file browser uses it for you, so if you are seeing this "
        "the app is out of date or something else made the call."
    )


async def workspace_delete(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Cancella un file o una cartella del workspace (il «Elimina» del file manager).

    Un progetto no: v. :func:`_project_delete_refusal`, la strada e'
    ``project.delete``. La ``rmtree`` sta in un thread: una cartella grande su
    una CPU Android sono secondi in cui il gateway non risponderebbe a nessuno.
    """
    from jenny.webui.workspace_files import delete_path, validate_entry_path

    rel_path = _require_str(params, "path")
    _require_workspace_flag("enabled", "unavailable", "workspace is disabled")
    _require_workspace_flag("allow_delete", "forbidden", "workspace deletes are disabled")

    root = ctx.get_workspace_root()
    with _fs_errors():
        # La voce, non il suo bersaglio: un link si cancella come link.
        full_path = validate_entry_path(root, rel_path)
        refusal = _delete_refusal(root, full_path)
        if refusal:
            raise CommandError("forbidden", refusal)
        await asyncio.to_thread(delete_path, full_path)
    return {"success": True, "path": rel_path}


async def workspace_rename(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Rinomina (o sposta) un file o una cartella del workspace.

    Non un quaderno (:func:`_rename_refusal`, la strada e' ``project.rename``) e
    mai sopra qualcosa che c'e' gia': la destinazione occupata e' ``name_taken``.
    """
    from jenny.webui.workspace_files import rename_path, validate_entry_path

    old_rel = _require_str(params, "old_path")
    new_rel = _require_str(params, "new_path")
    _require_workspace_flag("enabled", "unavailable", "workspace is disabled")
    # Rinominare cambia il disco quanto scrivere: la rotta di prima non lo
    # chiedeva.
    _require_workspace_flag("allow_write", "forbidden", "workspace writes are disabled")

    root = ctx.get_workspace_root()
    with _fs_errors():
        # Le voci, non i bersagli: un link si rinomina come link.
        old_path = validate_entry_path(root, old_rel)
        new_path = validate_entry_path(root, new_rel)
        refusal = _rename_refusal(root, old_path)
        if refusal:
            raise CommandError("forbidden", refusal)
        await asyncio.to_thread(rename_path, old_path, new_path)
    return {"success": True, "old_path": old_rel, "new_path": new_rel}


async def workspace_copy(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Copia un file o una cartella del workspace (il «Duplica» del file manager).

    ``dest`` e' facoltativo: senza, la copia va **accanto all'originale** con un
    nome libero (``nota (copy).md``, poi ``nota (copy 2).md``…). La rotta di
    prima, senza ``dest``, copiava nella radice del workspace: un file della
    radice finiva su se stesso (``SameFileError``) e una cartella trovava sempre
    la destinazione occupata — il «Duplica» del file manager, che ``dest`` non lo
    manda, non aveva mai funzionato.
    """
    from jenny.webui.workspace_files import copy_path, free_copy_name, validate_path

    src_rel = _require_str(params, "path")
    dest_rel = params.get("dest")
    if dest_rel is not None and (not isinstance(dest_rel, str) or not dest_rel.strip()):
        raise CommandError("bad_request", "dest must be a non-empty string")
    _require_workspace_flag("enabled", "unavailable", "workspace is disabled")
    _require_workspace_flag("allow_write", "forbidden", "workspace writes are disabled")

    root = ctx.get_workspace_root()
    with _fs_errors():
        src_path = validate_path(root, src_rel)
        refusal = _copy_refusal(root, src_path)
        if refusal:
            raise CommandError("forbidden", refusal)
        if dest_rel is None:
            dest_path = await asyncio.to_thread(free_copy_name, src_path)
        else:
            dest_path = validate_path(root, dest_rel)
        # Una cartella copiata dentro se stessa: ``copytree`` ricopia a ogni
        # livello quel che ha appena scritto, fino al limite dei percorsi.
        if src_path.is_dir() and dest_path.is_relative_to(src_path):
            raise CommandError("bad_request", "cannot copy a folder into itself")
        await asyncio.to_thread(copy_path, src_path, dest_path)
    return {"success": True, "path": src_rel, "dest": _workspace_rel(root, dest_path)}


def _copy_refusal(workspace_root: Path, source: Path) -> str | None:
    """Il motivo per cui *source* non si duplica dal file manager, o ``None``.

    La radice del workspace per prima: senza ``dest`` la copia va accanto
    all'originale, e accanto alla radice vuol dire **fuori** dal confine — una
    ``copytree`` di tutto, config con le chiavi compresa, in una cartella che
    nessun gate del workspace vede piu'. Con un ``dest`` dentro sarebbe la
    copia di una cartella in se stessa.

    Poi una cartella che contiene quaderni, come per la cancellazione: ne
    copierebbe le pagine senza le conversazioni, che stanno fuori dall'albero.
    Un quaderno solo si duplica: la copia ha un nome nuovo e una chat vuota.
    """
    if source == workspace_root.resolve():
        return "the workspace itself cannot be copied from the file browser"
    notebooks = _notebooks_inside(workspace_root, source)
    if notebooks:
        return (
            f"this folder holds notebooks ({_names(notebooks)}): a copy would take their "
            "pages without their conversations, which live outside this tree."
        )
    return None


def _workspace_rel(root: Path, path: Path) -> str:
    """*path* relativo alla radice del workspace, come lo scrive il client."""
    try:
        return path.relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


# Tetto sulle regole che l'utente scrive a Jenny. Non e' un limite di
# trasporto — quello e' ``MAX_WRITE_BYTES``, mille volte piu' alto — ma una
# misura di cosa sia una regola: quel testo entra nel prompt di **ogni** turno,
# accanto a chi e' lei. Oltre qualche paragrafo non e' piu' una regola, e' un
# secondo SOUL.md scritto a mano.
MAX_SOUL_RULES_CHARS = 2000


async def soul_rules_write(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Salva le regole che l'utente ha dato a Jenny, e le proietta in ``SOUL.md``.

    Non e' ``workspace.write`` su un path qualunque, e la differenza e' tutta
    nella seconda meta': la verita' va in ``.jenny/soul_rules.md``, che il
    registro di scrittura di Dream non ammette, e dentro ``SOUL.md`` ne resta
    una copia proiettata — che e' dove il prompt la legge. Le due scritture
    devono restare una sola operazione, o la copia comincia a divergere dalla
    verita' (v. ``agent/soul_rules.py``).
    """
    from jenny.agent.soul_rules import save_rules

    content = params.get("content", "")
    if not isinstance(content, str):
        raise CommandError("bad_request", "content must be a string")
    if len(content) > MAX_SOUL_RULES_CHARS:
        raise CommandError(
            "too_large",
            f"rules too long ({len(content)} > {MAX_SOUL_RULES_CHARS} characters)",
        )

    _require_workspace_flag("enabled", "unavailable", "workspace is disabled")
    _require_workspace_flag("allow_write", "forbidden", "workspace writes are disabled")

    try:
        # Su disco, quindi fuori dal loop: stessa ragione di ``workspace.write``.
        saved = await asyncio.to_thread(save_rules, ctx.get_workspace_root(), content)
    except PermissionError as exc:
        raise CommandError("forbidden", "permission denied") from exc
    except OSError as exc:
        raise CommandError("bad_request", os_error_text(exc)) from exc
    return {"chars": len(saved)}


def _wiki_page_file(ctx: CommandContext, wiki_name: str, page_path: str) -> Path:
    """Il file di una pagina di quaderno, risolto e contenuto. Solo lettura di path.

    Specchio della risoluzione di ``wiki_routes._wiki_page``, e volutamente **più
    stretta in tre punti**, perché qui si scrive:

    - niente ripiego su ``resolve_wikilink``: si modifica la pagina che si stava
      leggendo, e il client rimanda il ``page`` che quella risposta gli ha dato;
    - niente correzione del suffisso: ``.md`` o è un errore, non una cosa da
      indovinare al posto di chi salva;
    - il file **deve esistere**. Creare una pagina nuova è un altro gesto, e da
      qui non passa.

    Il contenimento è sulla pages-dir ``wiki/`` e non sull'intera ``wikis/``:
    tiene fuori i fratelli ``raw/``, ``audit/``, ``log/``. E passa da
    ``resolve()``, che è il solo cancello che vede un link simbolico —
    ``safe_wiki_page_path`` guarda la stringa e un symlink non risale.
    """
    from jenny.webui.wiki import discover_wikis

    wikis = discover_wikis(_wikis_dir(ctx))
    if wiki_name not in wikis:
        raise CommandError("not_found", "wiki not found")
    pages_dir = wikis[wiki_name]

    rel = safe_wiki_page_path(page_path)
    if not rel or not rel.endswith(".md"):
        raise CommandError("bad_request", "invalid page path")

    full = pages_dir / rel
    if not is_path_within(full, pages_dir):
        raise CommandError("forbidden", "path escapes wiki root")
    if not full.is_file():
        raise CommandError("not_found", "page not found")
    return full


def _lf(text: str) -> str:
    """I fine riga come li vede chi legge con ``read_text`` (universal newlines)."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _write_file_unchanged(full: Path, content: str, base: str) -> None:
    """``write_file`` solo se *full* e' ancora *base*, confronto e scrittura nello
    stesso thread (v. :func:`_write_page_unchanged` per la finestra che resta).

    Il confronto ignora i fine riga: l'editor li porta tutti a ``\n``. Un file
    che non c'e' piu' e' cambiato anche lui.
    """
    from jenny.webui.workspace_files import write_file

    try:
        with open(full, encoding="utf-8", errors="replace", newline="") as f:
            raw = f.read()
    except FileNotFoundError:
        raise CommandError("conflict", "file changed on disk: nothing was saved") from None
    if _lf(raw) != _lf(base):
        raise CommandError("conflict", "file changed on disk: nothing was saved")
    write_file(full, content)


def _write_page_unchanged(full: Path, content: str, base: str) -> None:
    """Confronta e scrive **nello stesso thread**, per stringere la finestra.

    Lettura, confronto e scrittura sono tre passi, e fra il primo e il terzo
    Jenny potrebbe scrivere: qui non c'è un lock, c'è una finestra ridotta a
    quel che il disco impiega. Il caso che conta — l'editor aperto per minuti
    mentre lei lavora — lo chiude il confronto; questo chiude il resto per
    quanto si può senza un lock che due scrittori diversi (gateway e strumenti
    file dell'agente) non condividerebbero comunque.

    **I fine riga del file restano i suoi.** Il lettore ha ricevuto la pagina da
    ``read_text``, che porta tutto a ``\n``, quindi ``base`` e ``content``
    arrivano così: si confronta su quella forma, ma il file si legge grezzo
    (``newline=""``) per sapere com'era scritto, e se andava a CRLF ci torna.
    Prima un salvataggio convertiva in silenzio l'intero file a LF.
    """
    from jenny.webui.workspace_files import write_file

    with open(full, encoding="utf-8", newline="") as f:
        raw = f.read()
    if _lf(raw) != base:
        raise CommandError("conflict", "page changed on disk")
    content = _lf(content)
    crlf = raw.count("\r\n")
    if crlf and crlf >= raw.count("\n") - crlf:
        content = content.replace("\n", "\r\n")
    write_file(full, content)


async def page_write(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Salva una pagina di quaderno modificata a mano dal lettore.

    **Non è ``workspace.write`` su un path costruito dal client**, e non per
    stile: la cartella dei quaderni la decide la config (``wiki.wikis_dir``) e
    il client non la conosce — comporla di là vorrebbe dire indovinarla. Qui
    arriva il nome del quaderno, che è quel che il client davvero sa.

    **``base`` è la parte che morde.** Le stesse pagine le scrive anche Jenny,
    con gli strumenti file di sempre: fra il momento in cui l'editor si apre e
    quello in cui si salva, il file può essere cambiato sotto. ``base`` è il
    testo da cui si è partiti; se non combacia con quel che c'è su disco la
    risposta è ``conflict`` e **non si scrive niente**. Salvare a occhi chiusi
    qui vuol dire cancellare il lavoro di qualcun altro senza che nessuno se ne
    accorga — e nessuno dei due scrittori saprebbe di averlo fatto.

    I cancelli sono tre, e sono tre apposta: la wiki accesa (``wiki.enabled``)
    e le due del workspace — questo riscrive una pagina, cioè esattamente ciò che
    ``workspace.allow_write`` esiste per governare.
    """
    wiki_name = _require_str(params, "wiki")
    page_path = _require_str(params, "page")
    content = params.get("content")
    if not isinstance(content, str):
        raise CommandError("bad_request", "content must be a string")
    base = params.get("base")
    if not isinstance(base, str):
        raise CommandError("bad_request", "base must be a string")

    size = len(content.encode("utf-8"))
    if size > MAX_WRITE_BYTES:
        raise CommandError(
            "too_large",
            f"page too large to save ({size} > {MAX_WRITE_BYTES} bytes)",
        )

    _require_wiki_enabled()
    _require_workspace_flag("enabled", "unavailable", "workspace is disabled")
    _require_workspace_flag("allow_write", "forbidden", "workspace writes are disabled")

    full = _wiki_page_file(ctx, wiki_name, page_path)
    try:
        # Su disco, quindi fuori dal loop: stessa ragione di ``workspace.write``.
        await asyncio.to_thread(_write_page_unchanged, full, content, base)
    except PermissionError as exc:
        raise CommandError("forbidden", "permission denied") from exc
    except OSError as exc:
        raise CommandError("bad_request", os_error_text(exc)) from exc
    return {"wiki": wiki_name, "page": page_path, "bytes": size}


def _int_param(params: Mapping[str, Any], key: str) -> int:
    """Un offset intero; assente vale 0, come nella query della route di prima."""
    value = params.get(key, 0)
    # ``bool`` è un ``int`` per Python, e ``True`` come offset è un errore del client.
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommandError("bad_request", "invalid sel_start/sel_end")
    return value


async def audit_create(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Apre una segnalazione ancorata a un punto di una pagina di quaderno.

    Fino al 26/09/2026 era ``GET /api/audit/create?comment=…``: il commento è
    testo libero, e nell'indirizzo stava sotto il tetto di 8192 byte per riga
    di ``websockets`` — un commento lungo e accentato (ogni lettera accentata
    percent-encodata vale sei byte) falliva. È la regola del gateway:
    ``/api/`` è per letture e parametri corti, le note libere vanno qui.

    Stessa validazione e stessi esiti della route, tradotti nei codici di
    ``CommandError``: quaderno sconosciuto o assente ``bad_request`` (era 400
    «wiki required»), bersaglio fuori dalla pages-dir ``forbidden`` (403),
    pagina che non c'è ``not_found`` (404), selezione impossibile
    ``bad_request`` (400), wiki spenta o config illeggibile ``unavailable``
    (503). ``sel_start``/``sel_end`` sono offset nel **markdown sorgente**: il
    file lo rilegge il server, come faceva la route.
    """
    from jenny.webui.wiki import create_audit, discover_wikis

    wiki_name = params.get("wiki")
    target = params.get("target", "")
    comment = params.get("comment", "")
    author = params.get("author", "")
    for key, value in (("target", target), ("comment", comment), ("author", author)):
        if not isinstance(value, str):
            raise CommandError("bad_request", f"{key} must be a string")
    sel_start = _int_param(params, "sel_start")
    sel_end = _int_param(params, "sel_end")
    size = len(comment.encode("utf-8"))
    if size > MAX_WRITE_BYTES:
        raise CommandError("too_large", f"comment too large ({size} > {MAX_WRITE_BYTES} bytes)")

    _require_wiki_enabled(fail_closed=True)

    wikis = discover_wikis(_wikis_dir(ctx))
    if not isinstance(wiki_name, str) or not wiki_name or wiki_name not in wikis:
        raise CommandError("bad_request", "wiki required")
    pages_dir = wikis[wiki_name]

    # Il percorso grezzo: lo risolve ``is_path_within``, e un loop di symlink
    # (``RuntimeError`` su Python 3.11) e' un rifiuto, non un errore interno.
    raw_path = pages_dir / (target or WIKI_INDEX_FILENAME)
    if not is_path_within(raw_path, pages_dir):
        raise CommandError("forbidden", "path escapes wiki root")

    def _create() -> dict[str, Any]:
        raw_markdown = raw_path.read_text("utf-8") if raw_path.is_file() else ""
        return create_audit(
            wiki_root=pages_dir.parent,
            target=target,
            raw_markdown=raw_markdown,
            sel_start=sel_start,
            sel_end=sel_end,
            comment=comment,
            author=author or "anonymous",
        )

    try:
        # Su disco, quindi fuori dal loop: stessa ragione di ``workspace.write``.
        result = await asyncio.to_thread(_create)
    except FileNotFoundError as exc:
        raise CommandError("not_found", str(exc)) from exc
    except ValueError as exc:
        raise CommandError("bad_request", str(exc)) from exc
    result.pop("entry", None)
    return result


_CONVERSATION_CHOICES = frozenset({"refuse", "keep", "discard"})


def _conversation_choice(params: Mapping[str, Any]) -> str:
    """La scelta dell'utente su una conversazione rimasta, validata.

    Insieme chiuso e default ``refuse``: un valore sconosciuto non deve poter
    valere «vai avanti comunque» su un'operazione che puo' scartare una chat.
    """
    value = params.get("conversation")
    if value is None:
        return "refuse"
    if not isinstance(value, str) or value not in _CONVERSATION_CHOICES:
        raise CommandError("bad_request", "invalid conversation choice")
    return value


async def project_create(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Crea un progetto: una wiki nuova, completa e vuota, piu' la riga dell'utente.

    Sta qui e non su ``/api/`` perche' porta contenuto: la riga di scope e'
    testo libero dell'utente, in italiano e con le emoji che vuole, e la
    superficie ``/api/`` non sa trasportarlo (v. la docstring del modulo).
    """
    from jenny.session.keys import is_valid_project_name
    from jenny.webui.project_create import ProjectCreateError, create_project

    # La forma del nome la decide ``jenny.session.keys``, che e' anche chi la
    # applica a ogni ``chat_id`` in arrivo: un nome che qui passasse e li' no
    # creerebbe un progetto che non si puo' aprire.
    name = _require_str(params, "name").strip()
    if not is_valid_project_name(name):
        raise CommandError("bad_request", "invalid project name")

    # Una riga: gli a-capo vengono richiusi invece di far fallire il comando, che
    # su una tastiera mobile e' quel che l'utente si aspetta.
    seed = " ".join(_require_str(params, "seed").split())
    if not seed:
        raise CommandError("bad_request", "seed required")
    if len(seed) > MAX_PROJECT_SEED_CHARS:
        raise CommandError(
            "too_large",
            f"scope line too long ({len(seed)} > {MAX_PROJECT_SEED_CHARS} characters)",
        )

    _require_wiki_enabled()

    try:
        # Su disco: albero, template, registro. Fuori dall'event loop come le
        # altre scritture di questo modulo.
        return await asyncio.to_thread(
            create_project,
            wikis_dir=_wikis_dir(ctx),
            scripts_dir=_skill_scripts_dir(ctx),
            workspace=ctx.get_workspace_root(),
            name=name,
            seed=seed,
            # Cosa fare di una conversazione rimasta sotto questo nome. Il
            # default **chiede** invece di scegliere: la risposta torna come
            # ``status: conversation_exists`` e il client la trasforma in due
            # bottoni. V. la docstring di ``project_create``.
            conversation=_conversation_choice(params),
        )
    except ProjectCreateError as exc:
        raise CommandError("bad_request", str(exc)) from exc
    except OSError as exc:
        raise CommandError("bad_request", os_error_text(exc)) from exc


async def project_greet(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Il primo messaggio di Jenny in un quaderno appena creato.

    Separato da :func:`project_create` di proposito: la creazione scrive su
    disco ed e' istantanea, il saluto aspetta un modello. Lo chiede il client
    quando la chat del quaderno e' gia' aperta, cosi' lo vede arrivare. Le
    regole (solo su una chat vuota, la frase fissa se il modello manca) stanno
    in ``jenny/webui/project_greet.py``.
    """
    from jenny.config.loader import load_config
    from jenny.session.keys import is_valid_project_name
    from jenny.utils.wiki_paths import discover_wiki_roots, read_wiki_scope
    from jenny.webui.project_greet import greet_project

    name = _require_str(params, "name").strip()
    if not is_valid_project_name(name):
        raise CommandError("bad_request", "invalid project name")
    _require_wiki_enabled()
    if ctx.session_manager is None or ctx.bus is None:
        raise CommandError("unavailable", "greeting is unavailable")
    # La radice, non la pages-dir di ``discover_wikis``: lo scope sta
    # nell'``AGENTS.md`` accanto a ``wiki/``, non dentro.
    wiki_root = (await asyncio.to_thread(discover_wiki_roots, _wikis_dir(ctx))).get(name)
    if wiki_root is None:
        raise CommandError("not_found", "no such notebook")

    try:
        defaults = load_config().agents.defaults
        language, bot_name = defaults.language, defaults.bot_name
    except Exception:
        language, bot_name = "en", "Jenny"
    # La lingua dell'interfaccia, se il client la dice: e' quella in cui
    # l'utente sta leggendo adesso.
    lang = params.get("lang")
    if isinstance(lang, str) and lang in ("it", "en"):
        language = lang

    return await greet_project(
        name=name,
        wiki_root=wiki_root,
        workspace=ctx.get_workspace_root(),
        language=language,
        bot_name=bot_name,
        session_manager=ctx.session_manager,
        bus=ctx.bus,
        llm=ctx.get_llm() if ctx.get_llm is not None else None,
        busy_session_keys=ctx.busy_session_keys,
        read_scope=read_wiki_scope,
    )


async def project_delete(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Cancella un progetto: l'albero della wiki **e** la sua conversazione.

    L'inverso di :func:`project_create`, e sta qui accanto a lui di proposito: le
    due meta' del ciclo di vita di un progetto devono essere leggibili insieme.
    Fino al 24/08/2026 questa meta' non esisteva, e l'unico modo di cancellare un
    progetto era la ``delete`` generica del file manager — che toglie una
    cartella e non sa cosa sia un progetto, quindi lasciava la conversazione
    sotto un nome ormai libero.

    Non porta contenuto e potrebbe stare su ``/api/``; sta fra i comandi perche'
    e' **distruttiva**, e questa e' la superficie autenticata all'handshake che
    la WebView usa per le operazioni che cambiano il disco.
    """
    from jenny.session.keys import is_valid_project_name, project_session_key
    from jenny.webui.project_delete import (
        ProjectDeleteError,
        delete_project,
        remove_notebook_crons,
    )

    name = _require_str(params, "name").strip()
    if not is_valid_project_name(name):
        raise CommandError("bad_request", "invalid project name")

    _require_wiki_enabled()

    # Stessa guardia del rinomino, e per la stessa ragione: chi ha la sessione in
    # mano a fine lavoro la salverebbe di nuovo, e il quaderno appena cancellato
    # tornerebbe come una chat orfana. Sul loop, prima del thread.
    if project_session_key(name) in set(ctx.busy_session_keys()):
        raise CommandError(
            "conflict",
            "Jenny is still working in this notebook: delete it when she has finished",
        )

    try:
        outcome = await asyncio.to_thread(
            delete_project,
            wikis_dir=_wikis_dir(ctx),
            scripts_dir=_skill_scripts_dir(ctx),
            workspace=ctx.get_workspace_root(),
            name=name,
            invalidate_session=ctx.invalidate_session,
        )
    except ProjectDeleteError as exc:
        raise CommandError("bad_request", str(exc)) from exc
    except OSError as exc:
        raise CommandError("bad_request", os_error_text(exc)) from exc
    # I cron delle sue schede se ne vanno con lui: restando, fallirebbero a ogni
    # giro con «notebook gone». Sul loop e dopo la cancellazione riuscita, come
    # la pagina qui sotto (il perche' del loop sta in ``remove_notebook_crons``).
    get_cron = getattr(ctx, "get_cron_service", None)
    outcome["removed_crons"] = remove_notebook_crons(get_cron() if get_cron else None, name)
    # La sua pagina in casa, se ne aveva una: se ne va con lui. **Dopo** la
    # cancellazione, fuori dal thread — e se non ci riesce il quaderno resta
    # cancellato: la pagina verso il nulla la toglie l'utente.
    from jenny.webui.home_pages import detach_pages_quietly

    await detach_pages_quietly("conversation", project_session_key(name))
    return outcome


async def project_rename(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Rinomina un quaderno: la cartella, la sua chat, e le sue pagine in casa.

    Fra i comandi e non su ``/api/`` per la stessa ragione della cancellazione:
    cambia il disco, e questa e' la superficie autenticata all'handshake. Il
    lavoro sta in :mod:`jenny.webui.project_rename`, in un thread; qui c'e' il
    seguito che e' della casa — le pagine appese col nome vecchio — **dopo**, e
    fuori dal thread: se non riesce il quaderno resta rinominato, e la pagina
    verso il nome vecchio si disegna «non c'e' piu'».
    """
    from jenny.session.keys import is_valid_project_name, project_session_key
    from jenny.webui.project_rename import ProjectRenameError, rename_project

    name = _require_str(params, "name").strip()
    new_name = _require_str(params, "new_name").strip()
    if not is_valid_project_name(name):
        raise CommandError("bad_request", "invalid project name")
    if not is_valid_project_name(new_name):
        raise CommandError("bad_request", "invalid new name")

    _require_wiki_enabled()

    # Qui, sul loop, e non nel thread: quel che e' in volo lo sa il loop. Resta
    # una finestra fra questa domanda e il ``rename`` — millisecondi, contro i
    # secondi o i minuti di un turno, di un subagent o di una passata.
    in_flight = set(ctx.busy_session_keys())
    if project_session_key(name) in in_flight or project_session_key(new_name) in in_flight:
        raise CommandError(
            "conflict",
            "Jenny is still working in this notebook: rename it when she has finished",
        )

    try:
        outcome = await asyncio.to_thread(
            rename_project,
            wikis_dir=_wikis_dir(ctx),
            scripts_dir=_skill_scripts_dir(ctx),
            workspace=ctx.get_workspace_root(),
            name=name,
            new_name=new_name,
            invalidate_session=ctx.invalidate_session,
        )
    except ProjectRenameError as exc:
        raise CommandError(exc.code, str(exc)) from exc
    except OSError as exc:
        raise CommandError("bad_request", os_error_text(exc)) from exc

    from jenny.webui.home_pages import rename_pages_of

    try:
        await rename_pages_of(
            "conversation", project_session_key(name), project_session_key(new_name)
        )
    except Exception:  # noqa: BLE001 — il rinomino e' gia' riuscito
        logger.opt(exception=True).warning("Pages of renamed notebook {} not followed", name)
    return outcome


def _home_pages_from(params: Mapping[str, Any]) -> tuple[list[Any], list[str]]:
    """Le pagine e l'ordine richiesti, validati **tutti** prima di scrivere.

    Validare fuori da ``store.mutate``: li' dentro si tiene un lock per tutta la
    durata della callback, e un errore alzato dentro lo attraverserebbe come un
    ``internal`` invece di arrivare come ``bad_request``.
    """
    from jenny.config.schema import FIXED_PAGES, MAX_PAGES, HomePageConfig

    rows = params.get("pages")
    if not isinstance(rows, list):
        raise CommandError("bad_request", "pages must be a list")
    try:
        pages = [HomePageConfig(**row) for row in rows]
    except (TypeError, ValueError) as exc:
        # ``TypeError``: una riga che non e' un oggetto. ``ValueError`` (anche la
        # ``ValidationError`` dello schema): una specie o un riferimento storti.
        # Il messaggio viaggia nella risposta, perche' chi l'ha mandata sappia
        # quale riga era sbagliata.
        raise CommandError("bad_request", str(exc)) from exc
    if len(pages) > MAX_PAGES:
        raise CommandError("bad_request", f"too many pages (max {MAX_PAGES})")
    identifiers = [s.id for s in pages]
    if len(set(identifiers)) != len(identifiers):
        raise CommandError("bad_request", "duplicate page id")
    reserved = sorted(set(identifiers) & set(FIXED_PAGES))
    if reserved:
        raise CommandError("bad_request", f"reserved page id: {', '.join(reserved)}")
    order = params.get("order")
    expected = [*FIXED_PAGES, *identifiers]
    if (
        not isinstance(order, list)
        or not all(isinstance(v, str) for v in order)
        or sorted(order) != sorted(expected)
    ):
        raise CommandError(
            "bad_request", "order must list every fixed page and every page id, once each"
        )
    return pages, order


async def home_pages_set(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Salva le pagine della casa: l'elenco intero **e** l'ordine di tutte.

    Fino al 25/09/2026 era una GET (``/api/casa/schermate/set?v=<json>``, il nome di allora) che
    scriveva ``config.json`` col JSON nell'indirizzo — contro la regola
    del gateway, per cui ``/api/`` e' per letture e parametri corti.
    La lettura resta ``GET /api/home/pages``.

    Aggiungere, togliere e spostare sono la stessa scrittura: mandare l'elenco
    completo toglie di mezzo il caso in cui due scritture parziali si
    incrociano lasciando un ordine che nessuno ha chiesto. L'ordine che arriva
    deve essere **esattamente** le fisse piu' le schermate, ognuna una volta: la
    tolleranza di ``normalize_order`` e' per il file, non per chi scrive.
    """
    from jenny.config import store
    from jenny.config.schema import Config, normalize_order

    pages, order = _home_pages_from(params)
    order_after = normalize_order(order, [s.id for s in pages])
    after = [s.model_dump() for s in pages]

    def _apply(config: Config) -> bool:
        before = [s.model_dump() for s in config.home.pages]
        if before == after and config.home.order == order_after:
            return False
        config.home.pages = list(pages)
        config.home.order = list(order_after)
        return True

    await store.mutate(_apply)
    return {"ok": True, "pages": after, "order": list(order_after)}


# ---------------------------------------------------------------------------
# I segreti: chiave del provider, token Telegram, password SSH
# ---------------------------------------------------------------------------
#
# Viaggiavano nella query di una GET (``/api/settings/provider/update?api_key=``,
# ``provider-models``, ``/api/telegram/save?token=``,
# ``/api/settings/ssh/host/save?password=``): una query string sta nella riga
# di richiesta, e la riga di richiesta la vedono il log di accesso, i
# traceback con le variabili locali e chiunque logghi un URL.
# Il gateway non legge body HTTP, quindi la strada
# e' questa: un frame WebSocket, autenticato all'handshake. La logica resta
# dov'era (``settings_api``, ``telegram_api``, ``ssh_api``): qui si traduce solo
# il trasporto.

# ``WebUISettingsError.status`` → codice di ``CommandError``. Un 502 (Telegram o
# il provider irraggiungibili) e un 503 dicono entrambi «adesso non si puo'».
_SETTINGS_STATUS_CODES = {
    400: "bad_request",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "too_large",
    502: "unavailable",
    503: "unavailable",
}


def _settings_error(exc: Any) -> CommandError:
    return CommandError(_SETTINGS_STATUS_CODES.get(exc.status, "bad_request"), exc.message)


def _as_query(params: Mapping[str, Any], keys: Collection[str]) -> dict[str, list[str]]:
    """I parametri RPC nella forma ``QueryParams`` che le funzioni dei settings
    leggono (``{nome: [valore]}``). Solo *keys*; ``None`` diventa la stringa
    vuota, come la mandava il client HTTP (``_sshCall``); un valore che non sia
    una stringa, un numero o un booleano e' una richiesta sbagliata."""
    query: dict[str, list[str]] = {}
    for key in keys:
        if key not in params:
            continue
        value = params[key]
        if value is None:
            value = ""
        elif isinstance(value, bool):
            value = "1" if value else ""
        elif isinstance(value, (int, float)):
            value = str(value)
        elif not isinstance(value, str):
            raise CommandError("bad_request", f"{key} must be a string")
        query[key] = [value]
    return query


def _fire(hook: Callable[[], None] | None, name: str) -> None:
    """Chiama un gancio dopo un salvataggio riuscito; un suo errore resta nel log
    (come nelle rotte: il salvataggio e' gia' avvenuto)."""
    if hook is None:
        logger.warning("{} is not wired: the change applies on the next restart", name)
        return
    try:
        hook()
    except Exception:
        logger.exception("{} callback failed", name)


async def settings_provider_models(
    ctx: CommandContext, params: Mapping[str, Any]
) -> dict[str, Any]:
    """L'elenco dei modelli di un provider, anche non ancora salvato.

    Era ``GET /api/settings/provider-models?api_key=…``: l'onboarding e la
    scheda del provider chiedono i modelli con la chiave appena digitata,
    prima di salvarla. Sola lettura, e advisory: non tocca la config.
    """
    from jenny.webui.settings_api import WebUISettingsError, provider_models_payload

    query = _as_query(params, ("provider", "api_key", "api_base", "format"))
    try:
        payload = await asyncio.to_thread(provider_models_payload, query)
    except WebUISettingsError as exc:
        raise _settings_error(exc) from None
    # Diagnostica: il fetch modelli non solleva sugli esiti applicativi
    # (not_configured/error/unsupported), e senza questa riga una lista vuota
    # resterebbe invisibile nel log (era nella rotta).
    logger.info(
        "[provider-models] provider={!r} status={!r} count={} message={!r}",
        payload.get("provider"),
        payload.get("status"),
        payload.get("model_count"),
        payload.get("message"),
    )
    return payload


async def settings_provider_update(
    ctx: CommandContext, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Crea o aggiorna un provider — con la sua chiave API. Era
    ``GET /api/settings/provider/update?api_key=…``.

    Dopo il salvataggio ``on_settings_changed``, sempre, come faceva la rotta:
    il fingerprint del provider attivo decide da se' se c'e' qualcosa da
    ricostruire.
    """
    from jenny.webui.settings_api import WebUISettingsError, update_provider

    query = _as_query(
        params, ("name", "format", "api_key", "api_base", "ca_bundle", "ca_bundle_clear")
    )
    # Solo i campi valorizzati, come la rotta: ``ca_bundle_clear`` e' un segnale
    # a parte proprio perche' la stringa vuota qui vuol dire «non toccare».
    data: dict[str, str] = {"name": (query.get("name") or [""])[0]}
    for key in ("format", "api_key", "api_base", "ca_bundle", "ca_bundle_clear"):
        value = (query.get(key) or [""])[0]
        if value:
            data[key] = value
    try:
        payload = await update_provider(data)
    except WebUISettingsError as exc:
        raise _settings_error(exc) from None
    _fire(ctx.on_settings_changed, "on_settings_changed")
    return payload


async def telegram_save(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Salva il token del bot Telegram (lo valida con ``getMe``) e riavvia il
    canale. Era ``GET /api/telegram/save?token=…``."""
    from jenny.webui.settings_api import WebUISettingsError
    from jenny.webui.telegram_api import save_telegram_token

    token = params.get("token", "")
    if not isinstance(token, str):
        raise CommandError("bad_request", "token must be a string")
    try:
        payload = await save_telegram_token(token)
    except WebUISettingsError as exc:
        raise _settings_error(exc) from None
    _fire(ctx.on_telegram_changed, "on_telegram_changed")
    return payload


# I campi di un host SSH, come li legge ``ssh_api.save_ssh_host``.
_SSH_HOST_KEYS = (
    "alias", "host", "port", "username", "description", "job_log_dir", "auth", "password",
)


async def ssh_host_save(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Crea o aggiorna un host SSH — con la sua password, se ne ha una. Era
    ``GET /api/settings/ssh/host/save?password=…``. ``password`` assente vuol
    dire «tieni quella salvata», come prima."""
    from jenny.webui.settings_api import WebUISettingsError
    from jenny.webui.ssh_api import save_ssh_host

    try:
        return await save_ssh_host(_as_query(params, _SSH_HOST_KEYS))
    except WebUISettingsError as exc:
        raise _settings_error(exc) from None


# I campi dell'onboarding, come li legge ``settings_api.save_onboarding``.
_ONBOARDING_KEYS = (
    "provider_name", "format", "api_key", "api_base", "model", "bot_name", "bot_icon", "locale",
)


async def onboarding_save(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Il primo provider, il modello e il nome di Jenny, dal primo avvio. Era
    ``GET /api/onboarding/save?api_key=…``: la prima chiave dell'utente nella
    riga di richiesta, come le altre quattro qui sopra.

    Un campo assente vale la stringa vuota, come nella query di prima; i default
    (formato, nome, icona, lingua) li decide ``save_onboarding``. A salvataggio
    riuscito l'evento sveglia l'agente differito.
    """
    from jenny.webui import settings_api

    query = _as_query(params, _ONBOARDING_KEYS)
    data = {key: (query.get(key) or [""])[0] for key in _ONBOARDING_KEYS}
    # La chiave non si logga: solo la sua lunghezza, come faceva la rotta.
    logger.info(
        "[onboarding] received: provider_name={!r} format={!r} model={!r} api_key_len={} "
        "bot_name={!r}",
        data["provider_name"],
        data["format"],
        data["model"],
        len(data["api_key"]),
        data["bot_name"],
    )
    try:
        payload = await settings_api.save_onboarding(
            data,
            session_manager=ctx.session_manager,
            onboarding_event=ctx.onboarding_event,
        )
    except settings_api.WebUISettingsError as exc:
        logger.warning("[onboarding] settings error: {}", exc.message)
        raise _settings_error(exc) from None
    logger.info("[onboarding] success: chat_id={}", payload.get("chat_id"))
    return payload


COMMANDS: dict[str, Command] = {
    "workspace.write": workspace_write,
    "workspace.delete": workspace_delete,
    "workspace.rename": workspace_rename,
    "workspace.copy": workspace_copy,
    "soul.rules.write": soul_rules_write,
    "page.write": page_write,
    "audit.create": audit_create,
    "project.create": project_create,
    "project.greet": project_greet,
    "project.delete": project_delete,
    "project.rename": project_rename,
    "home.pages.set": home_pages_set,
    "settings.provider.models": settings_provider_models,
    "settings.provider.update": settings_provider_update,
    "telegram.save": telegram_save,
    "ssh.host.save": ssh_host_save,
    "onboarding.save": onboarding_save,
}


async def dispatch_command(
    ctx: CommandContext,
    method: str,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """Esegue ``method``. Solleva ``CommandError`` per ogni esito non riuscito.

    Le eccezioni inattese sono loggate e ripresentate come ``internal``: un
    traceback non deve mai raggiungere il client.
    """
    handler = COMMANDS.get(method)
    if handler is None and method.startswith("board."):
        # Le scritture della bacheca stanno in un modulo loro, che importa da
        # questo: caricate qui, al primo uso, per non chiudere un cerchio
        # d'import all'avvio.
        from jenny.webui.board_commands import BOARD_COMMANDS

        handler = BOARD_COMMANDS.get(method)
    if handler is None and method.startswith("apps."):
        # Il cestino delle app e le risposte alla prova: stesso motivo.
        from jenny.webui.apps_commands import APPS_COMMANDS

        handler = APPS_COMMANDS.get(method)
    if handler is None and method.startswith("staged."):
        # Le bozze dei subagent non fidati: solo l'interfaccia le installa.
        from jenny.webui.staged_commands import STAGED_COMMANDS

        handler = STAGED_COMMANDS.get(method)
    if handler is None:
        raise CommandError("bad_request", f"unknown method: {method}")
    try:
        return await handler(ctx, params)
    except CommandError:
        raise
    except Exception as exc:
        logger.exception("command {} failed", method)
        raise CommandError("internal", "command failed") from exc
