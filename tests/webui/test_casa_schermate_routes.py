"""Le pagine della casa: l'elenco vive nel file di configurazione.

La casa e' un launcher e di lato alla chat ci sono le pagine che l'utente ha
aggiunto (tavola `Pagine`). **Perche' in `config.json` e non in
`localStorage`**: sono la schermata iniziale del telefono, perderle a un
ripristino sarebbe la sorpresa peggiore, e `localStorage` non entra nel backup
cifrato.

Il banco difende quattro cose che a sbagliarle non si nota subito:

1. la scrittura passa da **`store.mutate`** e non da `save_config` — la regola
   di `AGENTS.md`, che senza un banco nessuno vede rompersi;
2. il **cassetto** non e' una specie ammessa. La tavola lo esclude con un
   motivo («ce l'hai gia' tirando su»), e se il file potesse contenerlo la
   regola varrebbe solo finche' qualcuno non scrive a mano;
3. il **tetto** e gli **id doppi** sono rifiutati al confine, non dentro
   `mutate`: li' il lock e' preso per tutta la callback, e una `ValueError`
   alzata dentro diventa un 500 invece di un 400;
4. un **`ref` che non esiste piu'** (un'app disinstallata) si conserva
   com'e'. Cancellare una pagina dell'utente perche' il suo contenuto e'
   sparito non e' una decisione del server: la pagina si disegna «non c'e'
   piu'», e a toglierla decide lui.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from websockets.http11 import Headers
from websockets.http11 import Request as WsRequest

from jenny.config.schema import MAX_SCHERMATE
from jenny.webui.ws_http import GatewayHTTPHandler

_AUTH_SECRET = "test-secret"


def _richiesta(path: str, token: str | None = _AUTH_SECRET) -> WsRequest:
    if token is not None and "token=" not in path:
        sep = "&" if "?" in path else "?"
        path = f"{path}{sep}token={urllib.parse.quote(token)}"
    return WsRequest(path=path, headers=Headers())


def _set(schermate: list[dict]) -> str:
    v = urllib.parse.quote(json.dumps(schermate))
    return f"/api/casa/schermate/set?v={v}"


@pytest.fixture()
def env(tmp_path: Path, monkeypatch):
    """Un `config.json` vero su tmp_path, e l'handler HTTP completo."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    percorso = workspace / "config.json"

    from jenny.config import paths as paths_mod
    from jenny.config.loader import save_config
    from jenny.config.schema import Config
    from jenny.runtime.context import get_runtime_context

    save_config(Config(), percorso)
    monkeypatch.setattr(paths_mod, "get_workspace_path", lambda: workspace)
    monkeypatch.setattr(get_runtime_context(), "config_path", percorso)

    handler = GatewayHTTPHandler(
        config=SimpleNamespace(
            workspace=SimpleNamespace(enabled=True),
            wiki=SimpleNamespace(enabled=True, wikis_dir="wikis"),
            token_issue_secret=_AUTH_SECRET,
            verbose=False,
        ),
        session_manager=None,
        runtime_model_name=lambda: "test-model",
        bus=MagicMock(),
        media=MagicMock(),
        workspaces=MagicMock(),
        skills_workspace_path=workspace / "skills",
    )
    return SimpleNamespace(handler=handler, config_path=percorso, workspace=workspace)


def _corpo(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


async def _dispatch(env, path: str, token: str | None = _AUTH_SECRET):
    req = _richiesta(path, token=token)
    return await env.handler.casa_routes.dispatch(req, req.path.split("?")[0])


# ── Il confine ──────────────────────────────────────────────────────────────


async def test_unauthorized_without_token(env) -> None:
    risposta = await _dispatch(env, "/api/casa/schermate", token=None)
    assert risposta.status_code == 401


async def test_an_unknown_casa_path_is_not_ours(env) -> None:
    assert await _dispatch(env, "/api/casa/qualcosaltro") is None


# ── L'elenco ────────────────────────────────────────────────────────────────


async def test_a_fresh_install_has_no_pages(env) -> None:
    """Zero pagine, non una d'esempio: la chat da sola e' la casa."""
    corpo = _corpo(await _dispatch(env, "/api/casa/schermate"))
    assert corpo["schermate"] == []


async def test_the_cap_travels_with_the_list(env) -> None:
    """Il foglio deve sapere quando smettere di offrire la riga vuota.

    Il numero sta nello schema perche' e' il file a doverlo rispettare; arriva
    di qui perche' una seconda copia nel client divergerebbe, e la differenza
    si scoprirebbe solo quando un salvataggio viene rifiutato.
    """
    corpo = _corpo(await _dispatch(env, "/api/casa/schermate"))
    assert corpo["max"] == MAX_SCHERMATE
    # Due, non le tre della tavola. Il cassetto non c'e' perche' si tira su;
    # una conversazione perche' la chat in casa e' una sola e una pagina del
    # genere non avrebbe contenuto proprio (deciso il 22 settembre 2026).
    assert "drawer" not in corpo["specie"]
    assert "conversazione" not in corpo["specie"]
    assert set(corpo["specie"]) == {"app", "stanza"}


# ── La scrittura ────────────────────────────────────────────────────────────


async def test_saving_a_page_and_reading_it_back(env) -> None:
    pagine = [{"id": "p1", "kind": "app", "ref": "orto"}]
    assert _corpo(await _dispatch(env, _set(pagine)))["ok"] is True
    assert _corpo(await _dispatch(env, "/api/casa/schermate"))["schermate"] == pagine


async def test_the_write_lands_in_the_config_file(env) -> None:
    await _dispatch(env, _set([{"id": "p1", "kind": "stanza", "ref": "pages"}]))
    su_disco = json.loads(env.config_path.read_text(encoding="utf-8"))
    assert su_disco["casa"]["schermate"] == [
        {"id": "p1", "kind": "stanza", "ref": "pages"}
    ]


async def test_the_write_goes_through_the_funnel(env, monkeypatch) -> None:
    """`store.mutate`, non `save_config`.

    E' la regola di AGENTS.md: `save_config` riscrive il file intero da una
    copia che puo' essere gia' vecchia, e cancella in silenzio quel che un
    altro scrittore ha appena messo. Nessun test se ne accorgerebbe da solo,
    quindi se ne accorge questo.
    """
    from jenny.config import store as store_mod

    passaggi: list[str] = []
    vero = store_mod.mutate

    async def _spia(fn):
        passaggi.append("mutate")
        return await vero(fn)

    monkeypatch.setattr(store_mod, "mutate", _spia)
    await _dispatch(env, _set([{"id": "p1", "kind": "app", "ref": "orto"}]))
    assert passaggi == ["mutate"]


async def test_the_whole_list_replaces_the_old_one(env) -> None:
    """Aggiungere, togliere e spostare sono la stessa scrittura."""
    await _dispatch(env, _set([
        {"id": "a", "kind": "app", "ref": "orto"},
        {"id": "b", "kind": "app", "ref": "spesa"},
    ]))
    await _dispatch(env, _set([{"id": "b", "kind": "app", "ref": "spesa"}]))
    assert _corpo(await _dispatch(env, "/api/casa/schermate"))["schermate"] == [
        {"id": "b", "kind": "app", "ref": "spesa"}
    ]


# ── Quel che viene rifiutato, e con che numero ──────────────────────────────


async def test_the_app_drawer_is_not_a_kind(env) -> None:
    """La tavola lo esclude con un motivo; il file non puo' rimetterlo dentro."""
    risposta = await _dispatch(env, _set([{"id": "p1", "kind": "drawer", "ref": ""}]))
    assert risposta.status_code == 400
    # `http_error` risponde in testo semplice, non in JSON: il messaggio deve
    # nominare la specie rifiutata, o chi legge il 400 non sa cosa ha sbagliato.
    assert "drawer" in risposta.body.decode("utf-8")


async def test_over_the_cap_is_a_400_not_a_500(env) -> None:
    troppe = [
        {"id": f"p{i}", "kind": "app", "ref": "x"} for i in range(MAX_SCHERMATE + 1)
    ]
    assert (await _dispatch(env, _set(troppe))).status_code == 400


async def test_two_pages_with_the_same_id_are_refused(env) -> None:
    doppie = [
        {"id": "p1", "kind": "app", "ref": "orto"},
        {"id": "p1", "kind": "app", "ref": "spesa"},
    ]
    assert (await _dispatch(env, _set(doppie))).status_code == 400


async def test_junk_is_refused_before_it_reaches_the_file(env) -> None:
    for path in (
        "/api/casa/schermate/set",
        "/api/casa/schermate/set?v=nonjson",
        "/api/casa/schermate/set?v=" + urllib.parse.quote('{"non":"lista"}'),
    ):
        assert (await _dispatch(env, path)).status_code == 400
    assert _corpo(await _dispatch(env, "/api/casa/schermate"))["schermate"] == []


async def test_a_page_pointing_at_nothing_is_kept(env) -> None:
    """Un'app disinstallata non fa sparire la pagina.

    Il server non sa se `orto` esista ancora, e non deve: sapere che una app
    e' sparita e' lavoro del client, che disegna «non c'e' piu'». Cancellare
    una pagina dell'utente per conto proprio sarebbe una decisione presa dal
    codice al posto suo.
    """
    pagine = [{"id": "p1", "kind": "app", "ref": "app-che-non-esiste"}]
    await _dispatch(env, _set(pagine))
    assert _corpo(await _dispatch(env, "/api/casa/schermate"))["schermate"] == pagine
