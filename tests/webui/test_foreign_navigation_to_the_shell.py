"""La vista esterna di una Jenny App non si porta sull'origine del gateway.

La vista esterna e' un iframe ``allow-scripts allow-same-origin`` sull'origine
del proxy (``127.0.0.1:<porta effimera>``): all'apertura e' un'altra origine e
non vede la SPA. Ma i flag di sandbox stanno sull'iframe, non sul documento: se
la pagina remota porta l'iframe su ``http://127.0.0.1:18790/...``, quel
documento girerebbe con l'origine del gateway **e** con ``allow-same-origin`` —
``parent.document``, lo storage, il token.

Due chiusure, una per tipo di documento: la WebUI rifiuta le navigazioni che non
partono dal gateway o dal guscio nativo (Fetch Metadata), e una pagina di Jenny
App porta il proprio sandbox nella risposta (CSP ``sandbox``), cosi' resta opaca
in qualunque cornice.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from support.gateway_http import make_handler, make_request

from jenny.webui.ws_http import GatewayHTTPHandler


def _handler(tmp_path: Path) -> GatewayHTTPHandler:
    handler = make_handler(tmp_path / "skills")
    ui_dir = (tmp_path / "ui").resolve()
    ui_dir.mkdir(parents=True, exist_ok=True)
    (ui_dir / "index.html").write_text("<!DOCTYPE html><html></html>", encoding="utf-8")
    (ui_dir / "evil.html").write_text("<script>parent</script>", encoding="utf-8")
    handler.static_dist_path = ui_dir
    return handler


async def _get(handler: GatewayHTTPHandler, path: str, headers: list[tuple[str, str]]):
    request = make_request(path, token=None, headers=headers)
    return await handler._dispatch_resolved(MagicMock(), request, path)


def _nav(site: str, dest: str = "iframe") -> list[tuple[str, str]]:
    return [("Sec-Fetch-Mode", "navigate"), ("Sec-Fetch-Site", site), ("Sec-Fetch-Dest", dest)]


@pytest.mark.parametrize("path", ["/html-mobile/index.html", "/html-mobile/evil.html",
                                  "/html-mobile/qualunque"])
@pytest.mark.parametrize("site", ["same-site", "cross-site"])
async def test_a_navigation_from_another_origin_is_refused(tmp_path, path, site):
    """``same-site`` e' la vista esterna (altra porta di loopback), ``cross-site``
    una cornice a origine opaca. Nessuna delle due ha motivo di aprire la SPA."""
    response = await _get(_handler(tmp_path), path, _nav(site))
    assert response.status_code == 403


@pytest.mark.parametrize("site,dest", [("none", "document"), ("same-origin", "document"),
                                       ("same-origin", "iframe")])
async def test_the_shell_and_the_spa_still_navigate(tmp_path, site, dest):
    """``none`` e' il ``loadUrl`` del guscio nativo, ``same-origin`` la SPA che
    passa dalla casa all'officina."""
    response = await _get(_handler(tmp_path), "/html-mobile/index.html", _nav(site, dest))
    assert response.status_code == 200


async def test_subresources_from_an_app_frame_still_load(tmp_path):
    """Il kit e l'SDK li carica una cornice opaca: ``cross-site``, ma non una
    navigazione. Rifiutarli spegnerebbe ogni Jenny App."""
    headers = [("Sec-Fetch-Mode", "no-cors"), ("Sec-Fetch-Site", "cross-site"),
               ("Sec-Fetch-Dest", "script")]
    response = await _get(_handler(tmp_path), "/html-mobile/index.html", headers)
    assert response.status_code == 200


async def test_without_fetch_metadata_nothing_changes(tmp_path):
    response = await _get(_handler(tmp_path), "/html-mobile/index.html", [])
    assert response.status_code == 200


def test_an_app_page_carries_its_own_sandbox(tmp_path):
    """Opaca anche fuori dalla sua cornice: stesse restrizioni dell'iframe,
    ``allow-scripts`` e basta — senza ``allow-same-origin``."""
    handler = make_handler(tmp_path / "skills")
    workspace = tmp_path / "workspace"
    app = workspace / "apps" / "note"
    (app / "app").mkdir(parents=True)
    (app / "app.json").write_text(json.dumps({"name": "Note", "description": "x"}))
    (app / "app" / "index.html").write_text("<!DOCTYPE html>", encoding="utf-8")
    apps_on = MagicMock()
    apps_on.apps.enabled = True
    with patch.object(handler, "_get_workspace_root", return_value=workspace), \
         patch("jenny.config.loader.load_config", return_value=apps_on):
        response = handler.apps_routes._static(
            make_request("/apps/note/index.html"), "/apps/note/index.html"
        )
    assert response.status_code == 200
    csp = response.headers.get("Content-Security-Policy", "")
    assert csp.split() == ["sandbox", "allow-scripts"]
