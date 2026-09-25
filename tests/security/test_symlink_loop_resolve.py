"""Un loop di symlink non fa cadere i controlli di contenimento.

Su Python 3.11 — quello del telefono — ``Path.resolve(strict=False)`` davanti a un
loop di symlink solleva ``RuntimeError``; dal 3.13 non piu'. I punti che
risolvevano un percorso **fuori** da ogni ``try`` (o con un ``except`` che non
nominava ``RuntimeError``) e poi chiedevano ``is_path_within(...,
path_resolved=True)`` trasformavano il loop in un'eccezione: un 500 da una rotta,
un crollo da un gancio di scrittura. Qui ``Path.resolve`` si fa sollevare a mano
sui file il cui nome comincia per ``ciclo``, cosi' il banco vale anche su 3.14.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import pathlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from websockets.http11 import Headers
from websockets.http11 import Request as WsRequest

from jenny.agent import wiki_provenance
from jenny.webui import media_api
from jenny.webui.apps_routes import AppsRoutes
from jenny.webui.wiki import create_audit
from jenny.webui.wiki_routes import WikiRoutes

_resolve_vero = pathlib.Path.resolve


@pytest.fixture(autouse=True)
def loop_di_symlink(monkeypatch: pytest.MonkeyPatch) -> None:
    def _resolve(self, strict: bool = False):
        if self.name.startswith("ciclo"):
            raise RuntimeError(f"Symlink loop from {str(self)!r}")
        return _resolve_vero(self, strict=strict)

    monkeypatch.setattr(pathlib.Path, "resolve", _resolve)


def _progetto(tmp_path: Path) -> tuple[Path, Path]:
    root = (tmp_path / "wikis" / "progetto").resolve()
    pages = root / "wiki"
    pages.mkdir(parents=True)
    return root, pages


def test_a_journal_source_through_a_loop_is_unresolved(tmp_path: Path) -> None:
    root, _ = _progetto(tmp_path)
    esito = wiki_provenance._journal_line_provenance(root, "raw/ciclo.md#13:55")
    assert esito == wiki_provenance._UNRESOLVED


def test_a_document_source_through_a_loop_is_not_a_document(tmp_path: Path) -> None:
    root, _ = _progetto(tmp_path)
    assert wiki_provenance._names_a_document(root, "raw/ciclo.md") is False


def test_the_provenance_guards_let_a_loop_through_to_the_write(tmp_path: Path) -> None:
    """I ganci non decidono niente su un percorso che non si risolve: la
    scrittura prosegue e il filesystem dira' il suo."""
    root, pages = _progetto(tmp_path)
    assert wiki_provenance._provenance_guard(root, pages)(pages / "ciclo.md", "x") is None
    assert wiki_provenance.wiki_page_provenance_guard()(pages / "ciclo.md", "x") is None


def test_an_audit_on_a_loop_is_not_found(tmp_path: Path) -> None:
    root, _ = _progetto(tmp_path)
    with pytest.raises(FileNotFoundError):
        create_audit(root, "ciclo.md", "", 0, 0, "nota", "u")


async def test_the_audit_route_answers_403_to_a_loop(tmp_path: Path) -> None:
    root, pages = _progetto(tmp_path)
    (pages / "index.md").write_text("# indice\n", encoding="utf-8")
    routes = WikiRoutes(
        check_api_token=lambda r: True,
        get_workspace_root=lambda: tmp_path,
        json_safe=lambda v: v,
    )
    routes._check_wiki_enabled = lambda: None  # type: ignore[method-assign]
    routes._get_wikis_dir = lambda: root.parent  # type: ignore[method-assign]
    req = WsRequest(path="/api/audit/create?wiki=progetto&target=ciclo.md", headers=Headers())
    risposta = await routes.dispatch(req, "/api/audit/create")
    assert risposta is not None and risposta.status_code == 403


def test_a_static_file_through_a_loop_is_403(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from jenny.webui.ws_http import GatewayHTTPHandler

    handler = GatewayHTTPHandler(
        config=SimpleNamespace(
            workspace=SimpleNamespace(enabled=True),
            wiki=SimpleNamespace(enabled=True, wikis_dir="wikis"),
            token_issue_secret="test-secret",
            verbose=False,
        ),
        session_manager=None,
        runtime_model_name=lambda: "test-model",
        bus=MagicMock(),
        media=MagicMock(),
        workspaces=MagicMock(),
        skills_workspace_path=tmp_path / "skills",
    )
    handler.static_dist_path = (tmp_path / "ui").resolve()
    (handler.static_dist_path / "assets").mkdir(parents=True)
    risposta = handler._serve_static("/html-mobile/assets/ciclo.js")
    assert risposta is not None and risposta.status_code == 403


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def test_signed_media_through_a_loop_is_404_and_is_not_signed(tmp_path: Path) -> None:
    segreto = b"segreto"
    cartella = tmp_path / "media"
    cartella.mkdir()
    payload = _b64(b"ciclo.bin")
    mac = hmac.new(segreto, payload.encode("ascii"), hashlib.sha256).digest()[:16]

    risposta = media_api.serve_signed_media(
        _b64(mac), payload, secret=segreto, media_dir=lambda _c: cartella
    )
    assert risposta.status_code == 404
    firmato = media_api.sign_media_path(
        cartella / "ciclo.bin", secret=segreto, media_dir=lambda _c: cartella
    )
    assert firmato is None


def test_an_app_static_file_through_a_loop_is_403(tmp_path: Path) -> None:
    (tmp_path / "apps" / "orto" / "app").mkdir(parents=True)
    routes = AppsRoutes(
        check_api_token=lambda r: True,
        get_workspace_root=lambda: tmp_path,
        log=SimpleNamespace(warning=lambda *a, **k: None),
    )
    routes._check_apps_enabled = lambda: None  # type: ignore[method-assign]
    req = WsRequest(path="/apps/orto/ciclo.js", headers=Headers())
    risposta = routes._static(req, "/apps/orto/ciclo.js")
    assert risposta.status_code == 403
