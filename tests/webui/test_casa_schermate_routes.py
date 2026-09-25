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
    """Le schermate con l'ordine piu' semplice: le fisse, poi le pagine. Per i
    casi che provano le schermate e non l'ordine (quello ha i suoi, in fondo)."""
    ids = [r.get("id") for r in schermate if isinstance(r, dict)]
    ordine = ["app", "chat", "quaderni", "impostazioni", *ids]
    v = urllib.parse.quote(json.dumps({"schermate": schermate, "ordine": ordine}))
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
    # Il cassetto non c'e', e resta fuori: ce l'hai gia' accanto a dove scrivi.
    # La conversazione era fuori anche lei (22/09/2026: «la chat e' una sola, una
    # pagina del genere non avrebbe contenuto proprio») ed e' rientrata il
    # 23/09 in un'altra forma — non una seconda chat, una scorciatoia che cambia
    # quella che c'e'. V. `.agent/pagine-conversazione-plan.md`.
    # Le stanze sono uscite il 23/09/2026: posti dove si va, non dove si sta.
    assert "drawer" not in corpo["specie"]
    assert set(corpo["specie"]) == {"app", "conversazione"}


# ── La scrittura ────────────────────────────────────────────────────────────


async def test_saving_a_page_and_reading_it_back(env) -> None:
    pagine = [{"id": "p1", "kind": "app", "ref": "orto"}]
    assert _corpo(await _dispatch(env, _set(pagine)))["ok"] is True
    assert _corpo(await _dispatch(env, "/api/casa/schermate"))["schermate"] == pagine


async def test_the_write_lands_in_the_config_file(env) -> None:
    await _dispatch(env, _set([{"id": "p1", "kind": "app", "ref": "orto"}]))
    su_disco = json.loads(env.config_path.read_text(encoding="utf-8"))
    assert su_disco["casa"]["schermate"] == [
        {"id": "p1", "kind": "app", "ref": "orto"}
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


async def test_a_room_is_no_longer_a_kind(env) -> None:
    """Uscita il 23/09/2026. Il file vecchio la perde in silenzio (v.
    `tests/config/test_casa_pages_config.py`); chi prova a scriverne una
    nuova se la vede rifiutare, con il nome della specie nel messaggio."""
    risposta = await _dispatch(env, _set([{"id": "p1", "kind": "stanza", "ref": "backup"}]))
    assert risposta.status_code == 400
    assert "stanza" in risposta.body.decode("utf-8")


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


# ── Le pagine conversazione (23/09/2026) ────────────────────────────────────
#
# Una scorciatoia che cambia la conversazione dell'unica chat, travestita da
# pagina (v. `.agent/pagine-conversazione-plan.md`). Punta a un quaderno, e
# solo a uno che il gateway aprirebbe.


async def test_a_notebook_can_be_a_page(env) -> None:
    pagine = [{"id": "p1", "kind": "conversazione", "ref": "project:piante"}]
    assert (await _dispatch(env, _set(pagine))).status_code == 200
    corpo = _corpo(await _dispatch(env, "/api/casa/schermate"))
    assert corpo["schermate"] == pagine
    assert "conversazione" in corpo["specie"]


async def test_a_conversation_page_must_point_at_a_notebook(env) -> None:
    """Non la personale, non un nome nudo, non un nome che il gateway rifiuta.

    La personale e' gia' la pagina 0, e l'utente ha chiesto «le chat
    quaderni». Un nome con `..` o vuoto e' un nome che `session/keys.py`
    rifiuterebbe al primo messaggio: meglio saperlo al salvataggio, con un 400
    che lo dice, che trovarsi una pagina che non risponde.
    """
    for ref in ("piante", "websocket:default", "project:", "project:..su", "project:a/b"):
        pagine = [{"id": "p1", "kind": "conversazione", "ref": ref}]
        risposta = await _dispatch(env, _set(pagine))
        assert risposta.status_code == 400, ref
    assert _corpo(await _dispatch(env, "/api/casa/schermate"))["schermate"] == []


async def test_the_notebook_rule_does_not_leak_onto_apps(env) -> None:
    """La regola vale per la sua specie: uno slug d'app non e' un quaderno."""
    pagine = [{"id": "p1", "kind": "app", "ref": "orto"}]
    assert (await _dispatch(env, _set(pagine))).status_code == 200


# ── Cancellare la cosa porta via la sua pagina (23/09/2026) ─────────────────
#
# Non contraddice il punto 4 della testata. Li' la cosa sparisce per altre
# strade e nessuno ha deciso niente sulla pagina; qui l'utente **cancella la
# cosa** dalla sua scheda, e la pagina e' della cosa.


def _pagine_su_disco(env) -> list[dict]:
    return json.loads(env.config_path.read_text(encoding="utf-8"))["casa"]["schermate"]


async def _con_pagine(env, pagine: list[dict]) -> None:
    assert _corpo(await _dispatch(env, _set(pagine)))["ok"] is True


async def test_deleting_an_app_takes_its_page_and_only_its_page(env) -> None:
    (env.workspace / "apps" / "orto").mkdir(parents=True)
    await _con_pagine(env, [
        {"id": "p1", "kind": "app", "ref": "orto"},
        {"id": "p2", "kind": "app", "ref": "lampo"},
        {"id": "p3", "kind": "conversazione", "ref": "project:orto"},
    ])
    req = _richiesta("/api/webui/apps/orto/delete")
    risposta = await env.handler.apps_routes.dispatch(req, req.path.split("?")[0])

    assert risposta.status_code == 200
    assert not (env.workspace / "apps" / "orto").exists()
    # Solo l'app: un quaderno che si chiama come lei e' un'altra cosa.
    assert [p["id"] for p in _pagine_su_disco(env)] == ["p2", "p3"]


async def test_a_failed_app_delete_leaves_the_pages_alone(env) -> None:
    """Un'app che non c'e' e' un 404, e le pagine non si toccano: la pagina
    se ne va **con** la cosa, non al posto suo."""
    await _con_pagine(env, [{"id": "p1", "kind": "app", "ref": "orto"}])
    req = _richiesta("/api/webui/apps/orto/delete")
    risposta = await env.handler.apps_routes.dispatch(req, req.path.split("?")[0])

    assert risposta.status_code == 404
    assert [p["id"] for p in _pagine_su_disco(env)] == ["p1"]


async def test_deleting_a_notebook_takes_its_page(env, monkeypatch) -> None:
    from jenny.webui import commands
    from jenny.webui import project_delete as modulo

    monkeypatch.setattr(modulo, "delete_project", lambda **kw: {"name": kw["name"]})
    await _con_pagine(env, [
        {"id": "p1", "kind": "conversazione", "ref": "project:piante"},
        {"id": "p2", "kind": "app", "ref": "piante"},
    ])
    ctx = SimpleNamespace(get_workspace_root=lambda: env.workspace, invalidate_session=lambda k: None,
                          get_cron_service=lambda: None)
    await commands.project_delete(ctx, {"name": "piante"})

    assert [p["id"] for p in _pagine_su_disco(env)] == ["p2"]


async def test_deleting_a_notebook_turns_its_cron_jobs_off(env, monkeypatch) -> None:
    """Deciso il 25/09/2026: spenti, non cancellati. Al primo scatto un job rimasto
    acceso ricreerebbe una chat sotto un nome ormai libero."""
    from jenny.webui import commands
    from jenny.webui import project_delete as modulo

    monkeypatch.setattr(modulo, "delete_project", lambda **kw: {"name": kw["name"]})
    spenti: list[str] = []
    cron = SimpleNamespace(disable_session_jobs=lambda key: spenti.append(key) or 1)
    ctx = SimpleNamespace(get_workspace_root=lambda: env.workspace, invalidate_session=lambda k: None,
                          get_cron_service=lambda: cron)
    await commands.project_delete(ctx, {"name": "piante"})

    assert spenti == ["project:piante"]


async def test_a_refused_notebook_delete_leaves_the_pages_alone(env, monkeypatch) -> None:
    from jenny.webui import commands
    from jenny.webui import project_delete as modulo
    from jenny.webui.commands import CommandError

    def _rifiuta(**kw):
        raise modulo.ProjectDeleteError("no project named piante")

    monkeypatch.setattr(modulo, "delete_project", _rifiuta)
    await _con_pagine(env, [{"id": "p1", "kind": "conversazione", "ref": "project:piante"}])
    ctx = SimpleNamespace(get_workspace_root=lambda: env.workspace, invalidate_session=lambda k: None,
                          get_cron_service=lambda: None)
    with pytest.raises(CommandError):
        await commands.project_delete(ctx, {"name": "piante"})

    assert [p["id"] for p in _pagine_su_disco(env)] == ["p1"]


async def test_nothing_to_take_means_no_write(env) -> None:
    """Se la cosa non aveva pagine il file non si riscrive: niente backup
    ruotato per un'operazione che in casa non ha cambiato niente."""
    from jenny.webui.casa_routes import stacca_pagine_di

    await _con_pagine(env, [{"id": "p1", "kind": "app", "ref": "lampo"}])
    prima = env.config_path.stat().st_mtime_ns
    assert await stacca_pagine_di("app", "orto") == 0
    assert env.config_path.stat().st_mtime_ns == prima


async def test_a_page_that_cannot_be_taken_does_not_undo_the_delete(env, monkeypatch) -> None:
    """La cancellazione e' gia' avvenuta e non si disfa: un guaio con la
    pagina non deve diventare un 500 su un'operazione riuscita."""
    from jenny.webui import casa_routes

    async def _rotto(kind, ref):
        raise RuntimeError("disco pieno")

    monkeypatch.setattr(casa_routes, "stacca_pagine_di", _rotto)
    (env.workspace / "apps" / "orto").mkdir(parents=True)
    req = _richiesta("/api/webui/apps/orto/delete")
    risposta = await env.handler.apps_routes.dispatch(req, req.path.split("?")[0])
    assert risposta.status_code == 200


async def test_a_delete_does_not_reach_across_kinds(env) -> None:
    """Oggi i riferimenti delle due specie non si toccano — uno slug d'app non
    ha i due punti, un quaderno e' `project:<nome>` — ma lo schema **non vieta**
    a una pagina app un `ref` a forma di quaderno. E' la specie, non la forma
    del riferimento, a dire di chi e' una pagina: senza, cancellare il quaderno
    toglierebbe anche quella. Trovato mutando: il controllo sulla specie
    sopravviveva a tutti gli altri banchi (23/09/2026)."""
    from jenny.webui.casa_routes import stacca_pagine_di

    await _con_pagine(env, [
        {"id": "p1", "kind": "conversazione", "ref": "project:piante"},
        {"id": "p2", "kind": "app", "ref": "project:piante"},
    ])
    assert await stacca_pagine_di("conversazione", "project:piante") == 1
    assert [p["id"] for p in _pagine_su_disco(env)] == ["p2"]


# ── L'ordine (23/09/2026) ───────────────────────────────────────────────────
#
# Dal 23/09 si spostano tutte le pagine, la chat e le tre fisse comprese
# (`.agent/pagine-in-alto-plan.md`). La rotta accetta `{schermate, ordine}`, e
# dal 24/09 solo quello; l'ordine che arriva dev'essere **esatto** — la
# tolleranza e' del file, non di chi scrive.

FISSE = ["app", "chat", "quaderni", "impostazioni"]


def _set_tutto(schermate: list[dict], ordine) -> str:
    v = urllib.parse.quote(json.dumps({"schermate": schermate, "ordine": ordine}))
    return f"/api/casa/schermate/set?v={v}"


async def test_a_fresh_install_reads_the_default_order(env) -> None:
    corpo = _corpo(await _dispatch(env, "/api/casa/schermate"))
    assert corpo["ordine"] == FISSE
    assert corpo["fisse"] == FISSE


async def test_the_order_is_saved_and_read_back(env) -> None:
    todo = {"id": "p1", "kind": "app", "ref": "todo"}
    ordine = ["p1", "app", "chat", "quaderni", "impostazioni"]
    corpo = _corpo(await _dispatch(env, _set_tutto([todo], ordine)))
    assert corpo["ordine"] == ordine
    assert _corpo(await _dispatch(env, "/api/casa/schermate"))["ordine"] == ordine
    su_disco = json.loads(env.config_path.read_text(encoding="utf-8"))
    assert su_disco["casa"]["ordine"] == ordine


async def test_moving_a_page_alone_is_a_write(env) -> None:
    """Stesse schermate, ordine nuovo: e' un cambiamento, e si scrive."""
    await _dispatch(env, _set_tutto([], FISSE))
    spostato = ["chat", "app", "quaderni", "impostazioni"]
    await _dispatch(env, _set_tutto([], spostato))
    assert _corpo(await _dispatch(env, "/api/casa/schermate"))["ordine"] == spostato


async def test_the_bare_list_is_refused(env) -> None:
    """L'elenco nudo di prima del 23/09 non ha piu' un mittente: e' un 400, e
    il file non cambia."""
    v = urllib.parse.quote(json.dumps([{"id": "p1", "kind": "app", "ref": "todo"}]))
    risposta = await _dispatch(env, f"/api/casa/schermate/set?v={v}")
    assert risposta.status_code == 400
    assert _corpo(await _dispatch(env, "/api/casa/schermate"))["schermate"] == []


async def test_an_object_without_an_order_is_refused(env) -> None:
    todo = {"id": "p1", "kind": "app", "ref": "todo"}
    v = urllib.parse.quote(json.dumps({"schermate": [todo]}))
    assert (await _dispatch(env, f"/api/casa/schermate/set?v={v}")).status_code == 400


@pytest.mark.parametrize(
    "ordine",
    [
        ["app", "chat", "quaderni"],  # manca una fissa
        ["app", "chat", "quaderni", "impostazioni", "chat"],  # doppione
        ["app", "chat", "quaderni", "impostazioni", "p9"],  # id che non c'e'
        ["app", "chat", "quaderni", "impostazioni"],  # manca la schermata
        "app,chat",
        ["app", "chat", "quaderni", 4],
    ],
    ids=["missing-fixed", "duplicate", "unknown", "missing-page", "not-a-list", "not-strings"],
)
async def test_a_crooked_order_is_a_400_and_nothing_is_written(env, ordine) -> None:
    todo = {"id": "p1", "kind": "app", "ref": "todo"}
    risposta = await _dispatch(env, _set_tutto([todo], ordine))
    assert risposta.status_code == 400
    assert _corpo(await _dispatch(env, "/api/casa/schermate"))["schermate"] == []


async def test_a_page_cannot_take_a_fixed_page_id(env) -> None:
    risposta = await _dispatch(env, _set([{"id": "chat", "kind": "app", "ref": "todo"}]))
    assert risposta.status_code == 400
    assert "chat" in risposta.body.decode("utf-8")


async def test_an_object_without_pages_is_refused(env) -> None:
    v = urllib.parse.quote(json.dumps({"ordine": FISSE}))
    assert (await _dispatch(env, f"/api/casa/schermate/set?v={v}")).status_code == 400


async def test_deleting_an_app_takes_its_id_out_of_the_order(env) -> None:
    from jenny.webui.casa_routes import stacca_pagine_di

    todo = {"id": "p1", "kind": "app", "ref": "todo"}
    await _dispatch(env, _set_tutto([todo], ["p1", "app", "chat", "quaderni", "impostazioni"]))
    assert await stacca_pagine_di("app", "todo") == 1
    su_disco = json.loads(env.config_path.read_text(encoding="utf-8"))
    assert su_disco["casa"]["ordine"] == FISSE
