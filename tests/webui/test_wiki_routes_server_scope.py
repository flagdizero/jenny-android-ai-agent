"""Cosa garantisce il **server** su ``/api/{tree,graph,page}``, e cosa no.

**T4.11 (23/08).** ``test_project_views_contract.py`` inchioda con dieci
asserzioni sul sorgente il fatto che dentro un progetto le viste wiki e grafo
mostrino quel progetto e basta. Sono asserzioni sul *client*: leggono
``mobile-wiki.js`` e ``mobile-graph.js``. Il server, intanto, accetta qualunque
nome che ``discover_wikis`` conosca, quale che sia la conversazione aperta.

La domanda posta era se l'isolamento debba esistere anche qui. **No, e la
ragione è che l'aggancio non ha un modo di arrivare fin qui che non sia
peggiore del problema:**

- il pin vive in ``AppState`` e ha **un solo scrittore**, lo scope chip, che è
  anche l'unico a sapere in che conversazione siamo (lo pretende
  ``test_only_the_scope_chip_publishes_the_pin``). Queste route sono HTTP
  autenticate da un token: non c'è nessuna sessione dietro da cui dedurlo;
- fargli ricordare al server "il progetto aperto" sarebbe il **secondo
  scrittore** di quella risposta, e quel ricordo potrebbe divergere da quel che
  il chip mostra — è l'alternativa già scartata, con motivazione, in
  ``tests/agent/test_project_scope_binding.py``;
- passarlo come secondo parametro accanto a ``wiki=`` non aggiungerebbe niente:
  è il client a scriverlo, ed è lo stesso client che oggi decide il ``wiki=``.
  Un controllo che si autodichiara non è un controllo.

E non c'è una conseguenza di sicurezza: sono wiki dello stesso utente, sullo
stesso telefono, dietro lo stesso token, e il confine di **lettura** dell'agente
è già tutto il workspace di proposito (v. ``FileSystemTools._read_allowed_root``).
L'isolamento delle viste è una scelta di prodotto — «Claude Code non ti parla
degli altri tuoi repository» — non una barriera.

Quel che il server garantisce è il **contenimento**, ed è quello che questi test
tengono fermo: il nome deve essere una wiki esistente (niente risalite, niente
cartelle qualunque) e la pagina deve stare dentro la ``wiki/`` di quella wiki
(niente ``raw/``, ``audit/``, ``log/`` dei fratelli). Prima di oggi nessun test
guidava questi tre handler su un input ostile: la copertura c'era sulla ricerca
(``test_wiki_search.py``) e sul client, non qui.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import pytest
from support.gateway_http import make_handler
from websockets.http11 import Headers
from websockets.http11 import Request as WsRequest

_AUTH_SECRET = "test-secret"


@pytest.fixture
def handler(tmp_path: Path, monkeypatch):
    """GatewayHTTPHandler reale su un workspace di tmp_path (v. test_wiki_search)."""
    from jenny.config import paths as paths_mod

    workspace = tmp_path / "data" / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(paths_mod, "get_workspace_path", lambda: workspace)

    return make_handler(workspace)


def _wiki(workspace: Path, name: str, pages: dict[str, str]) -> Path:
    root = workspace / "wikis" / name
    for rel, content in pages.items():
        full = root / "wiki" / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    return root


async def _call(handler, route: str, **params: str):
    query = "&".join(f"{k}={urllib.parse.quote(v)}" for k, v in params.items())
    path = f"{route}?{query}&token={_AUTH_SECRET}" if query else f"{route}?token={_AUTH_SECRET}"
    return await handler.wiki_routes.dispatch(WsRequest(path=path, headers=Headers()), route)


@pytest.fixture
def due_progetti(handler) -> Path:
    workspace = handler._get_workspace_root()
    _wiki(workspace, "patreon", {"index.md": "# Patreon\n"})
    _wiki(workspace, "etf", {"index.md": "# ETF\n", "note/segreto.md": "# Segreto\n"})
    # Una wiki **fuori** da ``wikis/``: il bersaglio di una risalita che, senza il
    # controllo di appartenenza, sarebbe una cartella dalla forma giusta.
    (workspace / "fuori" / "wiki").mkdir(parents=True)
    (workspace / "fuori" / "wiki" / "index.md").write_text("# Fuori\n", encoding="utf-8")
    # E un fratello che non è pagine: è il vicino che il contenimento esclude.
    (workspace / "wikis" / "etf" / "raw").mkdir()
    (workspace / "wikis" / "etf" / "raw" / "appunti.md").write_text("# grezzo\n", encoding="utf-8")
    return workspace


# ── il contenimento: quel che il server garantisce ────────────────────────


@pytest.mark.parametrize("route", ["/api/graph", "/api/page"])
@pytest.mark.parametrize(
    "name",
    [
        "../fuori",
        "..",
        "../../fuori",
        "patreon/../../fuori",
        "nessuna-wiki",
        "fuori",
    ],
)
async def test_a_wiki_name_must_be_a_wiki_that_exists_under_wikis(
    handler, due_progetti, route: str, name: str
) -> None:
    """Il nome arriva da un client: solo i nomi che ``discover_wikis`` conosce.

    ``../fuori`` è il parametro che conta: là **c'è** una wiki vera, quindi un
    controllo che si limitasse alla forma della cartella la accetterebbe. Qui la
    regola è l'appartenenza all'elenco, non la forma, e l'elenco lo produce
    ``discover_wikis`` scandendo la sola ``wikis/``.
    """
    response = await _call(handler, route, wiki=name)
    assert response is not None
    assert response.status_code == 404, (route, name, response.status_code)


@pytest.mark.parametrize(
    "page",
    ["../raw/appunti.md", "../../patreon/wiki/index.md", "/etc/passwd", "note/../../raw/appunti.md"],
)
async def test_a_page_path_that_climbs_is_refused_before_any_read(
    handler, due_progetti, page: str
) -> None:
    """Il primo dei due cancelli: ``safe_wiki_page_path``, che guarda la *stringa*.

    **Lo stato è 400 e non "uno fra 400/403/404"**, ed è il punto di questo test.
    I due cancelli qui sono ridondanti — su questi stessi input il controllo di
    contenimento più sotto rifiuterebbe comunque, con 403 — e un'asserzione
    permissiva li avrebbe coperti a vicenda: togliendone uno il test restava
    verde. È esattamente il difetto che T4.12 ha trovato altrove, e lo si evita
    solo dicendo *quale* dei due deve rispondere.
    """
    response = await _call(handler, "/api/page", wiki="etf", page=page)
    assert response is not None
    assert response.status_code == 400, (page, response.status_code)


async def test_a_symlink_out_of_the_pages_dir_is_refused_by_containment(
    handler, due_progetti
) -> None:
    """Il secondo cancello, e l'input che solo lui vede.

    ``safe_wiki_page_path`` normalizza una stringa: un link simbolico dentro
    ``wiki/`` la supera senza obiezioni — il nome non risale — e finisce comunque
    fuori. È il controllo ``full.resolve().relative_to(containment_root)`` a
    fermarlo, e questo è il solo input che lo distingue dal primo cancello.
    """
    pages = due_progetti / "wikis" / "etf" / "wiki"
    (pages / "scorciatoia.md").symlink_to(due_progetti / "wikis" / "etf" / "raw" / "appunti.md")

    response = await _call(handler, "/api/page", wiki="etf", page="scorciatoia.md")

    assert response is not None
    assert response.status_code == 403, response.status_code


async def test_containment_answers_the_same_whether_the_file_exists_or_not(
    handler, due_progetti
) -> None:
    """Un link fuori dalla ``wiki/`` e' un 403 **anche** se punta al nulla.

    Nell'ordine di prima — esiste? poi contenuto? — un bersaglio mancante dava
    404 e uno presente 403: la risposta diceva a chi chiede se un file fuori
    dalla wiki c'e'.
    """
    pages = due_progetti / "wikis" / "etf" / "wiki"
    fuori = due_progetti / "wikis" / "etf" / "raw"
    (pages / "c-e.md").symlink_to(fuori / "appunti.md")
    (pages / "non-c-e.md").symlink_to(fuori / "mai-scritto.md")

    presente = await _call(handler, "/api/page", wiki="etf", page="c-e.md")
    assente = await _call(handler, "/api/page", wiki="etf", page="non-c-e.md")

    assert (presente.status_code, assente.status_code) == (403, 403)


# ── l'asimmetria, messa a verbale ─────────────────────────────────────────


@pytest.mark.parametrize("route", ["/api/graph"])
async def test_the_server_serves_any_of_the_users_own_wikis_by_design(
    handler, due_progetti, route: str
) -> None:
    """**L'asimmetria del passo 5, a verbale.** Il server non conosce l'aggancio.

    Non è una svista da chiudere: l'aggancio ha un solo scrittore, il chip, e
    queste route non hanno una sessione da cui dedurlo (v. la docstring del
    modulo). Se un giorno il server dovesse davvero scoprirlo, questo test è il
    posto in cui la decisione cambia — e va cambiata qui, non aggiunta accanto.
    """
    response = await _call(handler, route, wiki="etf")
    assert response is not None and response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload


async def test_a_wiki_outside_wikis_is_not_reachable_by_name(handler, due_progetti) -> None:
    """Quel che misurava la prova della Home, ora che la Home non c'e' piu'.

    Qui c'era ``/api/tree`` senza nome: elencava le wiki di ``wikis/`` e doveva
    **non** contenere la sorella ``fuori/``, che ha la forma giusta ma sta
    altrove. L'albero e' uscito il 22/09/2026 insieme alle altre rotte senza
    clienti, e la regola che difendeva vive comunque — l'appartenenza all'elenco
    di ``discover_wikis``, non la forma della cartella. Questo e' lo stesso fatto
    detto sull'unica rotta rimasta a chiedere un nome.
    """
    response = await _call(handler, "/api/graph", wiki="fuori")
    assert response is not None
    assert response.status_code == 404, response.status_code

    # …e le due vere si raggiungono entrambe.
    for nome in ("patreon", "etf"):
        ok = await _call(handler, "/api/graph", wiki=nome)
        assert ok is not None and ok.status_code == 200, nome


# ── il tetto di lettura ───────────────────────────────────────────────────


class TestUnaPaginaEnormeNonEUnaRisposta:
    """T9.4/G9. ``/api/page`` leggeva il file **senza tetto**, e lo fa sul loop
    dell'evento: la risposta porta il markdown grezzo *più* l'HTML reso, quindi
    un file finito lì per sbaglio — un dump, un log, un allegato — costava più
    del doppio di sé in JSON su un canale WebSocket, mentre il gateway era
    fermo.

    **Rifiuta invece di troncare**, e non per prudenza: il client calcola gli
    offset di un audit sul ``raw`` che riceve, e ``audit.resolve`` rilegge il
    file **intero** per ancorarlo. Un ``raw`` tagliato darebbe ancore giuste per
    un testo che il server non ha — un commento attaccato al punto sbagliato al
    posto di un errore che si legge.

    Il tetto vero è un mega (tre ordini di grandezza sopra la pagina più grande
    delle wiki reali); qui è abbassato perché il numero non è quel che va
    provato: il confine sì.
    """

    async def test_oltre_il_tetto_e_un_413_e_non_una_risposta_a_meta(
        self, handler, due_progetti, monkeypatch
    ) -> None:
        monkeypatch.setattr("jenny.webui.wiki_routes._PAGE_MAX_BYTES", 64)
        pages = due_progetti / "wikis" / "etf" / "wiki"
        (pages / "enorme.md").write_text("# Grossa\n" + "x" * 200, encoding="utf-8")

        response = await _call(handler, "/api/page", wiki="etf", page="enorme.md")

        assert response is not None
        assert response.status_code == 413, response.status_code

    async def test_sotto_il_tetto_la_pagina_arriva_intera(
        self, handler, due_progetti, monkeypatch
    ) -> None:
        """Il verso opposto, che è quel che rende il tetto un tetto e non un
        rifiuto: al confine esatto la pagina si serve, e il ``raw`` è tutto il
        file — l'ancoraggio degli audit ci conta.
        """
        monkeypatch.setattr("jenny.webui.wiki_routes._PAGE_MAX_BYTES", 64)
        body = "# Piccola\n" + "y" * 54
        assert len(body.encode("utf-8")) == 64
        pages = due_progetti / "wikis" / "etf" / "wiki"
        (pages / "piccola.md").write_text(body, encoding="utf-8")

        response = await _call(handler, "/api/page", wiki="etf", page="piccola.md")

        assert response is not None and response.status_code == 200
        assert json.loads(response.body.decode("utf-8"))["raw"] == body
