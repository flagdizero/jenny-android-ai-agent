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
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.security.workspace_policy import is_path_within
from jenny.utils.wiki_paths import safe_wiki_page_path

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

    ``name_taken``: il nome chiesto è già di qualcos'altro (oggi: il nome nuovo
    di un quaderno è già di una cartella o di una conversazione).

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


Command = Callable[[CommandContext, Mapping[str, Any]], Awaitable[dict[str, Any]]]


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


def _require_wiki_enabled() -> None:
    """``config.wiki.enabled``, con lo stesso fail-open storico della route.

    A differenza del workspace, una config illeggibile qui non blocca: la route
    HTTP si comportava così (``except Exception: pass``) e la wiki non è un gate
    di sicurezza sul filesystem, è una feature che può essere spenta.
    """
    from jenny.config.loader import load_config

    try:
        enabled = bool(load_config().wiki.enabled)
    except Exception:
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


async def workspace_write(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Scrive un file di testo del workspace (salvataggio dall'editor WebUI)."""
    from jenny.webui.workspace_files import validate_path, write_file

    rel_path = _require_str(params, "path")
    content = params.get("content", "")
    if not isinstance(content, str):
        raise CommandError("bad_request", "content must be a string")
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
        # Fuori dall'event loop: ``write_file`` fa un atomic_write con fsync, e
        # fino a 1 MB di disco su una CPU Android sono centinaia di ms in cui il
        # gateway non risponderebbe a nessun altro (stessa ragione per cui il
        # decode dei media sta in un thread, v. ``_save_envelope_media``).
        await asyncio.to_thread(write_file, full_path, content)
    except ValueError as exc:
        raise CommandError("bad_request", str(exc)) from exc
    except FileNotFoundError as exc:
        raise CommandError("not_found", "path not found") from exc
    except PermissionError as exc:
        raise CommandError("forbidden", "permission denied") from exc
    except OSError as exc:
        raise CommandError("bad_request", str(exc)) from exc
    return {"path": rel_path, "bytes": size}


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
        raise CommandError("bad_request", str(exc)) from exc
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
        raise CommandError("bad_request", str(exc)) from exc
    return {"wiki": wiki_name, "page": page_path, "bytes": size}


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
        raise CommandError("bad_request", str(exc)) from exc


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
    from jenny.webui.project_delete import ProjectDeleteError, delete_project

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
        raise CommandError("bad_request", str(exc)) from exc
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
        raise CommandError("bad_request", str(exc)) from exc

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
    scriveva ``config.json`` col JSON nell'indirizzo — contro la regola di
    ``.agent/design.md``, per cui ``/api/`` e' per letture e parametri corti.
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


COMMANDS: dict[str, Command] = {
    "workspace.write": workspace_write,
    "soul.rules.write": soul_rules_write,
    "page.write": page_write,
    "project.create": project_create,
    "project.delete": project_delete,
    "project.rename": project_rename,
    "home.pages.set": home_pages_set,
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
    if handler is None:
        raise CommandError("bad_request", f"unknown method: {method}")
    try:
        return await handler(ctx, params)
    except CommandError:
        raise
    except Exception as exc:
        logger.exception("command {} failed", method)
        raise CommandError("internal", "command failed") from exc
