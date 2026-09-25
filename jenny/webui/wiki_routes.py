"""Adapter di route HTTP per le API Wiki + Audit della WebUI (estratto da
ws_http). Stesso pattern di ``WebUISettingsRouter``/``SkillsRoutes``.

Solo letture e parametri corti — questo trasporto non può trasportare contenuto
(v. la docstring di ``webui.commands``), e infatti la scrittura di una pagina sta
di là (``page.write``).

**Una segnalazione si apre e basta.** Leggerle e chiuderle non passa più di qui:
dal 22/09/2026 confermarne una porta l'utente nella chat del quaderno, e di lì in
poi sono Jenny e i suoi strumenti file a lavorarle, con lo script della skill
(``llm-wiki/scripts/audit_review.py``). Le rotte che le elencavano e il comando
che le chiudeva erano rimasti senza un cliente, e se ne sono andati con lo stesso
giro.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

if TYPE_CHECKING:
    from jenny.webui.wiki_search import WikiSearchService

from loguru import logger

from jenny.channels.http_utils import (
    http_error,
    http_json_response,
    parse_query,
    query_first,
)
from jenny.security.workspace_policy import is_path_within
from jenny.utils.wiki_paths import WIKI_INDEX_FILENAME, safe_wiki_page_path

# Chiavi frontmatter esposte al client in ``/api/page``. Tutto il resto (URL
# sorgente, provenance, slug/flag interni) resta lato server: il frontmatter
# integrale non è usato dalla UI e ne inoltrerebbe chiavi arbitrarie inserite
# dall'autore.
_FRONTMATTER_ALLOWLIST = frozenset(
    {"title", "type", "entity_type", "tags", "created", "updated"}
)


# Tetto di una pagina servita da ``/api/page``, in byte. **Non è il tetto
# dell'iniettore** (6.000 caratteri, ``PAGE_MAX_CHARS``): quello dice cosa entra
# in un prompt, questo dice cosa il telefono riesce a rendere. La risposta porta
# il markdown grezzo *e* l'HTML, quindi un file da N byte costa più di 2N in
# JSON su un canale WebSocket, e la lettura sta sul loop dell'evento. Un mega di
# markdown è tre ordini di grandezza sopra la pagina più grande delle otto wiki
# vere (16.385 caratteri, misurati il 23/08): chi lo supera non è una pagina
# lunga, è un file finito lì per sbaglio.
#
# **Rifiuta invece di troncare**, e la ragione non è la prudenza: il client usa
# il ``raw`` per calcolare gli offset di un audit, e ``/api/audit/create`` rilegge
# il file **intero** per ancorarlo. Un ``raw`` tagliato darebbe ancore giuste per
# un testo che il server non ha, cioè un commento attaccato al punto sbagliato —
# un guasto silenzioso al posto di un 413 che si legge.
_PAGE_MAX_BYTES = 1_048_576


def _filter_frontmatter(fm: Any) -> dict[str, Any] | None:
    """Restringe il frontmatter alle sole chiavi presentazionali consentite."""
    if not isinstance(fm, dict):
        return fm
    return {k: v for k, v in fm.items() if k in _FRONTMATTER_ALLOWLIST}


def _collect_projects(wikis_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Le wiki divise in due: quelle apribili come chat, e quelle no.

    ``[{name, modified, pages}]`` per ognuna, nell'ordine in cui le da' la
    discovery. ``modified`` e' l'mtime della radice della wiki, che oggi e' il
    solo segnale di attivita' disponibile: la conversazione di un progetto non
    esiste ancora (item 3). Quando esistera', l'ultima attivita' dovra' venire
    da lei, non dal filesystem — un `lint` non e' attivita' dell'utente.

    ``pages`` e' il numero di pagine della wiki, ed e' qui e non su una route
    sua perche' questa e' la sola chiamata che l'elenco fa gia'. Costa una
    ``rglob`` per quaderno: misurato sul Titan 2, contare ricorsivamente i
    ``.md`` di quattordici quaderni (464 file) sta in **20 ms** di flash.
    L'alternativa sarebbe ``/api/graph``, che la stessa cifra la mette nel
    ``degree`` di un nodo — cioe' dentro una risposta da ~110 kB (grafo +
    indice full-text) che non si puo' chiedere per scrivere "34".

    **Conta con la regola di chi le elenca**, ``is_wiki_page_rel``, e non tutti
    i ``.md``: ``summaries/`` non e' fatto di pagine di contenuto e resta fuori
    da grafo, albero e ricerca. Con una ``rglob`` nuda la pastiglia
    dell'intestazione diceva «7 pagine» e la stanza dietro ne elencava sei —
    visto al primo giro sul banco, su un quaderno con un riassunto dentro. Un
    numero su una porta deve contare quel che c'e' dall'altra parte.

    **La divisione e' il punto.** Il nome di una cartella e' anche il nome di una
    sessione (``project:<nome>``), e i due lati non facevano la stessa domanda:
    questo elenco dava qualunque cartella, mentre
    ``channels/websocket.py::_envelope_chat_id`` accetta solo cio' che passa
    ``is_valid_project_name``. Una wiki chiamata ``Ricerca ETF`` compariva nel
    chip e, aprendola, ne apriva un'altra. Un nome che il server elenca deve
    essere un nome che il server accetta.

    Le altre non vengono **buttate**: viaggiano in ``unopenable``. Su un telefono
    l'utente non ha un file manager per rinominare la cartella — la sola strada
    e' chiederlo all'agente dalla chat personale, e per chiederlo deve sapere che
    quella cartella esiste. Sparire dall'elenco sarebbe indistinguibile
    dall'essere stata cancellata, cioe' il tipo di silenzio piu' costoso che
    questo codice possa produrre. Stanno in una lista **separata** e non nella
    stessa con un flag perche' il chip mappa ogni voce di ``projects`` in una
    riga tappabile: un flag in piu' in quelle voci lo ignorerebbe, e
    continuerebbe a offrire quel che non si apre.
    """
    from jenny.session.keys import is_valid_project_name
    from jenny.utils.wiki_paths import discover_wiki_roots, is_wiki_page_rel

    projects: list[dict[str, Any]] = []
    unopenable: list[dict[str, Any]] = []
    for name, root in discover_wiki_roots(wikis_dir).items():
        try:
            modified = int(root.stat().st_mtime)
        except OSError:
            modified = 0
        try:
            pages_dir = root / "wiki"
            pages = sum(
                1 for f in pages_dir.rglob("*.md")
                if is_wiki_page_rel(f.relative_to(pages_dir))
            )
        except OSError:
            # Una cartella sparita fra la discovery e il conteggio: zero e'
            # la risposta onesta, e non deve far cadere l'intero elenco.
            pages = 0
        entry = {"name": name, "modified": modified, "pages": pages}
        if is_valid_project_name(name):
            projects.append(entry)
        else:
            # ``reason`` perche' il motivo lo deve scegliere chi disegna la riga,
            # non indovinare dal nome: oggi ce n'e' uno solo, ma la forma non
            # deve cambiare quando ne nasce un secondo.
            unopenable.append({**entry, "reason": "invalid_name"})
    return projects, unopenable


class WikiRoutes:
    """Route ``/api/{config,tree,graph,page}`` e ``/api/audit*``."""

    def __init__(
        self,
        *,
        check_api_token: Callable[[WsRequest], bool],
        get_workspace_root: Callable[[], Path],
        json_safe: Callable[[Any], Any],
        search_service: "WikiSearchService | None" = None,
    ) -> None:
        self._check_api_token = check_api_token
        self._get_workspace_root = get_workspace_root
        self._json_safe = json_safe
        # Collaboratore privato con stato: la cache grafo+indice vive quanto il
        # gateway. Iniettabile perché i test devono poterla azzerare o
        # sostituire senza passare dal composition root; costruito pigramente
        # perché ``wiki_search`` tira dentro ``wiki`` e quindi ``markdown``, e
        # questo ``__init__`` gira all'avvio del gateway — come tutti gli altri
        # import di questo modulo, sta fuori dal cammino di boot.
        self._search_service = search_service

    # -- helpers --

    def _get_search_service(self) -> "WikiSearchService":
        if self._search_service is None:
            from jenny.webui.wiki_search import WikiSearchService

            self._search_service = WikiSearchService()
        return self._search_service

    def _get_wikis_dir(self) -> Path:
        from jenny.config.loader import load_config

        try:
            wikis_subdir = load_config().wiki.wikis_dir
        except Exception:
            wikis_subdir = "wikis"
        return self._get_workspace_root() / wikis_subdir

    def _check_wiki_enabled(self) -> Response | None:
        """Verifica ``config.wiki.enabled``, fail-**closed**.

        Era ``except Exception: pass``, cioè un errore di config faceva passare
        la richiesta su tutte e otto le rotte che questo gate protegge. Terza
        istanza della stessa forma: ``apps_routes`` l'aveva identica,
        ``workspace_routes._require_workspace_flag`` invece risponde 503 nello
        stesso caso e spiega nella docstring perché deve. I due esiti restano
        distinguibili — illeggibile e spento non sono la stessa cosa.
        """
        from jenny.config.loader import load_config

        try:
            enabled = load_config().wiki.enabled
        except Exception:
            logger.exception("wiki gate: could not read config; refusing")
            return http_error(503, "wiki is unavailable")
        if not enabled:
            return http_error(503, "wiki is disabled")
        return None

    # -- dispatch --

    async def dispatch(self, request: WsRequest, path: str) -> Response | None:
        if path == "/api/projects":
            return await self._projects_list(request)
        if path == "/api/project/describe":
            return await self._project_describe(request)
        if path == "/api/graph":
            return await self._wiki_graph(request)
        if path == "/api/page":
            return await self._wiki_page(request)
        if path == "/api/audit/create":
            return await self._audit_create(request)
        return None

    # -- wiki handlers --

    async def _wiki_graph(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_wiki_enabled()
        if err:
            return err
        from jenny.webui.wiki import discover_wikis

        query = parse_query(request.path)
        wiki_name = query_first(query, "wiki") or ""
        # Il nome del quaderno è **obbligatorio**, come per ``/api/audit/create``.
        # C'era una vista senza nome — il grafo a stella di *tutte* le wiki — e
        # non c'è più: l'unica informazione che portava era quante pagine ha
        # ciascuna, e l'elenco dei quaderni la dà già con nomi, date e ricerca,
        # senza pagare una risposta da ~110 kB.
        if not wiki_name:
            return http_error(400, "wiki required")
        wikis_dir = self._get_wikis_dir()
        loop = asyncio.get_running_loop()

        # Grafo e indice full-text viaggiano nella *stessa* risposta perché le
        # postings dell'indice sono indici nell'array ``nodes`` qui sotto.
        # Servirli da due endpoint aprirebbe la finestra in cui la wiki cambia
        # fra le due chiamate: il client accenderebbe i nodi sbagliati.
        wikis = discover_wikis(wikis_dir)
        if wiki_name not in wikis:
            return http_error(404, "wiki not found")
        bundle = await loop.run_in_executor(
            None, self._get_search_service().bundle, wikis[wiki_name]
        )
        graph = bundle.graph
        search = bundle.search

        return http_json_response(
            {
                "nodes": [
                    {
                        "id": n.id,
                        "label": n.label,
                        "path": n.path,
                        "group": n.group,
                        "degree": n.degree,
                        "title": n.title,
                    }
                    for n in graph.nodes
                ],
                "edges": [{"source": e.source, "target": e.target} for e in graph.edges],
                "search": search,
            }
        )

    async def _wiki_page(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_wiki_enabled()
        if err:
            return err
        from jenny.webui.wiki import create_renderer, discover_wikis, resolve_wikilink

        query = parse_query(request.path)
        wiki_name = query_first(query, "wiki") or ""
        page_path = query_first(query, "page") or ""
        wikis_dir = self._get_wikis_dir()
        wikis = discover_wikis(wikis_dir)

        if not wiki_name:
            full = wikis_dir / "_index.md"
            current_wiki = None
            wiki_root_for_renderer = wikis_dir
            containment_root = wikis_dir
        else:
            if wiki_name not in wikis:
                return http_error(404, "wiki not found")
            wiki_dir = wikis[wiki_name]
            wiki_root = wiki_dir.parent
            rel = safe_wiki_page_path(page_path) if page_path else WIKI_INDEX_FILENAME
            if not rel:
                return http_error(400, "invalid page path")
            full = wiki_dir / rel
            if full.is_dir():
                full = full / WIKI_INDEX_FILENAME
            if full.suffix != ".md":
                full = full.with_suffix(".md")
            if not full.is_file():
                candidate = resolve_wikilink(wiki_root, rel)
                if candidate:
                    full = candidate
            current_wiki = wiki_name
            wiki_root_for_renderer = wiki_root
            # Contenimento nella pages-dir ``wiki/`` (non nell'intera wikis_dir):
            # impedisce di raggiungere i fratelli raw/ audit/ log/.
            containment_root = wiki_dir

        # Il contenimento **prima** dell'esistenza: nell'ordine inverso un 404
        # contro un 403 diceva a chi chiede se un file fuori dalla wiki c'e'.
        # Anche un errore di risoluzione (un loop di symlink) e' un 403: fino al
        # 24/09/2026 usciva come 500, perche' qui si catturava solo ValueError.
        if not is_path_within(full, containment_root):
            return http_error(403, "path escapes wiki root")

        if not full.is_file():
            return http_error(404, "file not found")

        try:
            size = full.stat().st_size
        except OSError:
            return http_error(404, "file not found")
        if size > _PAGE_MAX_BYTES:
            return http_error(
                413, f"page too large: {size} bytes (limit {_PAGE_MAX_BYTES})"
            )

        raw = full.read_text("utf-8")
        loop = asyncio.get_running_loop()
        renderer = create_renderer(
            wiki_root_for_renderer, current_wiki=current_wiki, wikis_map=wikis
        )
        rendered = await loop.run_in_executor(None, lambda: renderer(raw))

        if current_wiki:
            rel_page = str(full.relative_to(wikis[wiki_name])).replace(os.sep, "/")
        else:
            rel_page = "_index.md"

        return http_json_response(
            {
                "wiki": current_wiki,
                "page": rel_page,
                "path": str(full.relative_to(wikis_dir)).replace(os.sep, "/"),
                "title": rendered.title,
                "frontmatter": self._json_safe(_filter_frontmatter(rendered.frontmatter)),
                "html": rendered.html,
                "raw": rendered.raw_markdown,
            }
        )

    async def _projects_list(self, request: WsRequest) -> Response:
        """Elenco dei progetti per lo scope chip.

        **Un progetto e' una wiki**, quindi l'elenco e' `discover_wiki_roots` e
        non il contenuto di una cartella `projects/`: quella non esiste, e il
        chip la leggeva (v. `roadmap/project-sessions.md`, item 10). `dir` viaggia
        col payload perche' il chip mostra lo scope come un percorso e il nome
        della cartella e' configurabile (`config.wiki.wikis_dir`).

        `projects` sono le wiki che si possono **aprire**; `unopenable` quelle il
        cui nome di cartella non puo' essere il nome di una sessione. La ragione
        della divisione sta su `_collect_projects`.
        """
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_wiki_enabled()
        if err:
            return err

        wikis_dir = self._get_wikis_dir()
        loop = asyncio.get_running_loop()
        projects, unopenable = await loop.run_in_executor(
            None, _collect_projects, wikis_dir
        )
        return http_json_response(
            {"dir": wikis_dir.name, "projects": projects, "unopenable": unopenable}
        )

    async def _project_describe(self, request: WsRequest) -> Response:
        """Cosa porterebbe via la cancellazione di un progetto. Sole letture.

        Sta qui e non fra i comandi perche' e' una **lettura**: quella superficie
        esiste per le scritture che portano contenuto (v. la docstring di
        ``webui/commands.py``), e un nome di progetto sta in una query string.

        Serve alla conferma, che e' meta' del fix del 24/08: una cancellazione e'
        sicura quando la conferma dice il vero su cosa sparisce. La conferma
        vecchia diceva solo *Delete "viaggio"?* e taceva la conversazione,
        che era proprio la meta' che non spariva.
        """
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_wiki_enabled()
        if err:
            return err

        from jenny.session.keys import is_valid_project_name
        from jenny.webui.project_delete import describe_project

        name = (query_first(parse_query(request.path), "name") or "").strip()
        if not is_valid_project_name(name):
            return http_error(400, "invalid project name")

        wikis_dir = self._get_wikis_dir()
        workspace = self._get_workspace_root()
        loop = asyncio.get_running_loop()
        described = await loop.run_in_executor(
            None,
            lambda: describe_project(wikis_dir=wikis_dir, workspace=workspace, name=name),
        )
        if not described["exists"] and not described["orphan"]:
            return http_error(404, "project not found")
        return http_json_response(described)

    # -- audit handlers --

    async def _audit_create(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_wiki_enabled()
        if err:
            return err
        from jenny.webui.wiki import create_audit, discover_wikis

        query = parse_query(request.path)
        wikis = discover_wikis(self._get_wikis_dir())

        wiki_name = query_first(query, "wiki") or ""
        if not wiki_name or wiki_name not in wikis:
            return http_error(400, "wiki required")

        wiki_root = wikis[wiki_name].parent
        target = query_first(query, "target") or ""

        pages_dir = wikis[wiki_name]
        # Il percorso grezzo: lo risolve ``is_path_within``, e un loop di symlink
        # (``RuntimeError`` su Python 3.11, fuori da ogni ``try``) e' un 403, non un 500.
        raw_path = pages_dir / (target or WIKI_INDEX_FILENAME)
        if not is_path_within(raw_path, pages_dir):
            return http_error(403, "Forbidden")
        raw_markdown = ""
        if raw_path.is_file():
            raw_markdown = raw_path.read_text("utf-8")

        try:
            sel_start = int(query_first(query, "selStart") or 0)
            sel_end = int(query_first(query, "selEnd") or 0)
        except (ValueError, TypeError):
            return http_error(400, "invalid selStart/selEnd")

        try:
            result = create_audit(
                wiki_root=wiki_root,
                target=target,
                raw_markdown=raw_markdown,
                sel_start=sel_start,
                sel_end=sel_end,
                comment=query_first(query, "comment") or "",
                author=query_first(query, "author") or "anonymous",
            )
            result.pop("entry", None)
            return http_json_response(result)
        except FileNotFoundError as exc:
            return http_error(404, str(exc))
        except ValueError as exc:
            return http_error(400, str(exc))
