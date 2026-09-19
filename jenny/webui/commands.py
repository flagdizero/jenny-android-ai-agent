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
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

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
    ``not_found``, ``too_large``, ``unavailable``, ``internal`` — così un
    adapter può mapparli (a uno status HTTP, a un frame WS) senza indovinare
    dal testo del messaggio.
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


async def audit_resolve(ctx: CommandContext, params: Mapping[str, Any]) -> dict[str, Any]:
    """Chiude un item di audit con una nota di risoluzione (testo libero)."""
    from jenny.webui.wiki import discover_wikis, resolve_audit

    audit_id = _require_str(params, "audit_id")
    wiki_name = _require_str(params, "wiki")
    resolution = params.get("resolution")
    if resolution is not None and not isinstance(resolution, str):
        raise CommandError("bad_request", "resolution must be a string")

    _require_wiki_enabled()

    wikis = discover_wikis(_wikis_dir(ctx))
    if wiki_name not in wikis:
        raise CommandError("not_found", "wiki not found")
    wiki_root = wikis[wiki_name].parent
    try:
        # Anche qui il lavoro è su disco (lettura, riscrittura, move): fuori dal loop.
        return await asyncio.to_thread(resolve_audit, wiki_root, audit_id, resolution)
    except FileNotFoundError as exc:
        raise CommandError("not_found", str(exc)) from exc
    except ValueError as exc:
        raise CommandError("bad_request", str(exc)) from exc



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
    from jenny.session.keys import is_valid_project_name
    from jenny.webui.project_delete import ProjectDeleteError, delete_project

    name = _require_str(params, "name").strip()
    if not is_valid_project_name(name):
        raise CommandError("bad_request", "invalid project name")

    _require_wiki_enabled()

    try:
        return await asyncio.to_thread(
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


COMMANDS: dict[str, Command] = {
    "workspace.write": workspace_write,
    "soul.rules.write": soul_rules_write,
    "audit.resolve": audit_resolve,
    "project.create": project_create,
    "project.delete": project_delete,
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
