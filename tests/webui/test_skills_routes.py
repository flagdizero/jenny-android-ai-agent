"""Test delle route ``/api/webui/skills*`` (SkillsRoutes).

Segue il pattern di ``tests/webui/test_backup_routes.py``: costruisce un
``GatewayHTTPHandler`` reale con dipendenze mock/``SimpleNamespace`` e
dispatcha richieste ``websockets.http11.Request`` verso
``handler.skills_routes``. Come nell'handler reale, ``dispatch`` riceve il
path *senza* query string (la query viene invece letta da ``request.path``,
che la contiene per intero) — vedi ``_dispatch`` sotto.
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

from jenny.webui.ws_http import GatewayHTTPHandler

_AUTH_SECRET = "test-secret"


def _make_request(path_with_query: str, *, token: str | None = _AUTH_SECRET) -> WsRequest:
    if token is not None:
        sep = "&" if "?" in path_with_query else "?"
        path_with_query = f"{path_with_query}{sep}token={urllib.parse.quote(token)}"
    return WsRequest(path=path_with_query, headers=Headers())


def _dispatch(handler, path_with_query: str, *, token: str | None = _AUTH_SECRET):
    """Dispatcha come fa l'handler reale: path ripulito dalla query per il routing."""
    clean_path = path_with_query.split("?", 1)[0]
    request = _make_request(path_with_query, token=token)
    return handler.skills_routes.dispatch(request, clean_path)


def _update_path(name: str, **params: str) -> str:
    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    base = f"/api/webui/skills/{name}/update"
    return f"{base}?{query}" if query else base


def _write_skill(
    skills_dir: Path,
    name: str,
    *,
    description: str = "una descrizione",
    body: str = "corpo\n",
    extra_frontmatter: str = "",
) -> Path:
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    lines = ["---", f'description: "{description}"']
    if extra_frontmatter:
        lines.append(extra_frontmatter)
    lines.append("---")
    skill_file.write_text("\n".join(lines) + "\n" + body, encoding="utf-8")
    return skill_file


def _make_handler(workspace: Path, *, disabled_skills: set[str] | None = None) -> GatewayHTTPHandler:
    config = SimpleNamespace(
        workspace=SimpleNamespace(enabled=True),
        wiki=SimpleNamespace(enabled=True, wikis_dir="wikis"),
        token_issue_secret=_AUTH_SECRET,
        verbose=False,
    )
    return GatewayHTTPHandler(
        config=config,
        session_manager=None,
        runtime_model_name=lambda: "test-model",
        bus=MagicMock(),
        media=MagicMock(),
        workspaces=MagicMock(),
        # NB: SkillsLoader (jenny/agent/skills.py) fa workspace_path / "skills"
        # internamente: qui va passata la root del workspace, NON la cartella
        # skills già risolta (a differenza del valore fittizio usato in
        # test_backup_routes.py, dove questo parametro non viene mai usato).
        skills_workspace_path=workspace,
        disabled_skills=disabled_skills or set(),
    )


@pytest.fixture()
def env(tmp_path: Path, monkeypatch):
    """Workspace reale su tmp_path + handler HTTP completo (come test_backup_routes)."""
    workspace = tmp_path / "data" / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)

    from jenny.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "get_workspace_path", lambda: workspace)

    handler = _make_handler(workspace)
    return SimpleNamespace(handler=handler, workspace=workspace, skills_dir=skills_dir)


def _json(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


# -- dispatch: routing -------------------------------------------------------


def test_dispatch_returns_none_for_unrelated_path(env) -> None:
    response = _dispatch(env.handler, "/api/webui/other")
    assert response is None


def test_dispatch_returns_none_for_partial_prefix_match(env) -> None:
    # "/api/webui/skills-extra" non deve far match con la route della lista.
    response = _dispatch(env.handler, "/api/webui/skills-extra")
    assert response is None


def test_dispatch_recognizes_update_and_delete_paths(env) -> None:
    _write_skill(env.skills_dir, "foo")
    update = _dispatch(env.handler, _update_path("foo", description="x"))
    assert update is not None and update.status_code == 200
    delete = _dispatch(env.handler, "/api/webui/skills/foo/delete")
    assert delete is not None and delete.status_code == 200


# -- autenticazione -----------------------------------------------------------


def test_list_requires_token(env) -> None:
    response = _dispatch(env.handler, "/api/webui/skills", token=None)
    assert response.status_code == 401


def test_update_requires_token(env) -> None:
    _write_skill(env.skills_dir, "foo")
    response = _dispatch(env.handler, _update_path("foo", description="x"), token=None)
    assert response.status_code == 401


def test_delete_requires_token(env) -> None:
    _write_skill(env.skills_dir, "foo")
    response = _dispatch(env.handler, "/api/webui/skills/foo/delete", token=None)
    assert response.status_code == 401


# -- lista skill --------------------------------------------------------------


def test_list_happy_path_returns_expected_json(env) -> None:
    _write_skill(env.skills_dir, "alpha", description="Prima skill")
    _write_skill(env.skills_dir, "beta", description="Seconda skill")

    response = _dispatch(env.handler, "/api/webui/skills")

    assert response.status_code == 200
    body = _json(response)
    names = [s["name"] for s in body["skills"]]
    assert names == ["alpha", "beta"]
    assert body["skills"][0]["description"] == "Prima skill"
    assert body["skills"][0]["source"] == "workspace"
    assert body["skills"][0]["available"] is True
    assert body["skills"][0]["disabled"] is False


def test_list_respects_disabled_skills_configured_on_handler(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    _write_skill(skills_dir, "foo")
    _write_skill(skills_dir, "bar")

    from jenny.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "get_workspace_path", lambda: workspace)

    handler = _make_handler(workspace, disabled_skills={"foo"})
    response = _dispatch(handler, "/api/webui/skills")
    names = [s["name"] for s in _json(response)["skills"]]
    assert names == ["bar"]


def test_list_unexpected_error_maps_to_500_generic(env, monkeypatch) -> None:
    # Come in BackupRoutes: l'eccezione non propaga fuori da dispatch ma
    # diventa un 500 con messaggio generico (dettagli solo nel log server).
    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("jenny.webui.skills_routes.webui_skills_payload", boom)

    response = _dispatch(env.handler, "/api/webui/skills")
    assert response.status_code == 500
    assert b"boom" not in response.body


# -- update ---------------------------------------------------------------


def test_update_happy_path(env) -> None:
    skill_file = _write_skill(env.skills_dir, "foo", description="vecchia")
    path = _update_path("foo", description="nuova", content="corpo nuovo", disabled="true")

    response = _dispatch(env.handler, path)

    assert response.status_code == 200
    body = _json(response)
    assert body["description"] == "nuova"
    assert body["disabled"] is True
    assert "corpo nuovo" in skill_file.read_text(encoding="utf-8")


@pytest.mark.parametrize("raw", ["on", "ON", " on ", "1", "yes", "TRUE"])
def test_update_accepts_every_truthy_form_for_disabled(env, raw: str) -> None:
    """``?disabled=on`` è la forma che manda un checkbox HTML, e non disabilitava.

    Questa rotta teneva la propria lista di valori veri — ``("true","1","yes")``
    senza ``.strip()`` — mentre le altre tre del repo accettavano anche ``on``.
    """
    _write_skill(env.skills_dir, "foo", description="vecchia")

    response = _dispatch(env.handler, _update_path("foo", disabled=raw))

    assert response.status_code == 200
    assert _json(response)["disabled"] is True


@pytest.mark.parametrize("raw", ["off", "false", "0", "no", "boh"])
def test_update_treats_other_forms_as_enabled(env, raw: str) -> None:
    _write_skill(env.skills_dir, "foo", description="vecchia")

    response = _dispatch(env.handler, _update_path("foo", disabled=raw))

    assert response.status_code == 200
    assert _json(response)["disabled"] is False


def test_update_rejects_name_with_encoded_slash(env) -> None:
    # Il path regex esclude "/" letterale, ma il nome viene decodificato con
    # unquote() *dopo* il match: uno slash percent-encoded (%2F) supera il
    # regex e viene poi correttamente rifiutato dal controllo esplicito.
    response = _dispatch(env.handler, "/api/webui/skills/a%2Fb/update?description=x")
    assert response.status_code == 400


def test_update_rejects_name_with_backslash(env) -> None:
    response = _dispatch(env.handler, "/api/webui/skills/a%5Cb/update?description=x")
    assert response.status_code == 400


def test_update_requires_at_least_one_field(env) -> None:
    _write_skill(env.skills_dir, "foo")
    response = _dispatch(env.handler, "/api/webui/skills/foo/update")
    assert response.status_code == 400


def test_update_missing_skill_maps_to_403(env) -> None:
    # SkillsLoader.update_skill considera "non workspace" (quindi builtin)
    # qualunque nome la cui SKILL.md non esiste, per cui una skill mai
    # creata solleva PermissionError -> 403, non 404 (il ramo FileNotFoundError
    # del route handler pare irraggiungibile con l'implementazione attuale
    # di SkillsLoader, dato che is_workspace_skill usa la stessa condizione
    # di esistenza già verificata subito dopo).
    response = _dispatch(env.handler, _update_path("never-created", description="x"))
    assert response.status_code == 403


def test_update_unexpected_error_maps_to_500_generic(env, monkeypatch) -> None:
    # Come in BackupRoutes: sul 500 il client riceve un messaggio generico,
    # il dettaglio dell'eccezione finisce solo nel log server.
    _write_skill(env.skills_dir, "foo")

    def boom(*_args, **_kwargs):
        raise RuntimeError("guasto interno inatteso")

    monkeypatch.setattr("jenny.webui.skills_routes.update_workspace_skill", boom)
    response = _dispatch(env.handler, _update_path("foo", description="x"))
    assert response.status_code == 500
    assert b"guasto interno inatteso" not in response.body


# -- delete -----------------------------------------------------------------


def test_delete_happy_path(env) -> None:
    _write_skill(env.skills_dir, "foo")
    response = _dispatch(env.handler, "/api/webui/skills/foo/delete")
    assert response.status_code == 200
    assert _json(response) == {"deleted": True}
    assert not (env.skills_dir / "foo").exists()


def test_delete_rejects_invalid_name(env) -> None:
    response = _dispatch(env.handler, "/api/webui/skills/a%2Fb/delete")
    assert response.status_code == 400


def test_delete_missing_skill_maps_to_403(env) -> None:
    response = _dispatch(env.handler, "/api/webui/skills/never-created/delete")
    assert response.status_code == 403


def test_delete_unexpected_error_maps_to_500(env, monkeypatch) -> None:
    _write_skill(env.skills_dir, "foo")

    def boom(*_args, **_kwargs):
        raise RuntimeError("guasto")

    monkeypatch.setattr("jenny.webui.skills_routes.delete_workspace_skill", boom)
    response = _dispatch(env.handler, "/api/webui/skills/foo/delete")
    assert response.status_code == 500


def test_update_of_a_bundled_skill_maps_to_403(env) -> None:
    """Spegnere una integrata varrebbe fino al riavvio: la rotta lo rifiuta."""
    skill_file = _write_skill(env.skills_dir, "cron")
    before = skill_file.read_bytes()

    response = _dispatch(env.handler, _update_path("cron", disabled="1"))

    assert response.status_code == 403
    assert skill_file.read_bytes() == before
