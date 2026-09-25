"""Integrità a runtime della SPA servita.

La directory servita ``workspace/ui`` è scrivibile dai tool dell'agente e viene
riallineata al package solo dalla sync all'avvio del gateway. Un reload "a
caldo" della WebView (senza restart del processo, quindi senza sync)
riservirebbe altrimenti una copia manomessa. Questi test verificano che il
contenuto attivo (HTML/JS/CSS del manifest) sia sempre servito dai byte
canonici del package, mai dalla copia su disco.
"""

from __future__ import annotations

from pathlib import Path

from support.gateway_http import make_handler

from jenny.utils.android_assets import read_asset
from jenny.webui.ws_http import GatewayHTTPHandler

_AUTH_SECRET = "test-secret"


def _make_handler(tmp_path: Path) -> GatewayHTTPHandler:
    handler = make_handler(tmp_path / "skills")
    # Punta la dir servita a un mirror di test scrivibile.
    ui_dir = (tmp_path / "ui").resolve()
    ui_dir.mkdir(parents=True, exist_ok=True)
    handler.static_dist_path = ui_dir
    return handler


def test_tampered_index_html_is_not_served(tmp_path):
    handler = _make_handler(tmp_path)
    marker = b"<script>window.__pwned=1</script>"
    (handler.static_dist_path / "index.html").write_bytes(
        b"<!DOCTYPE html><html><head>" + marker + b"</head></html>"
    )

    resp = handler._serve_static("/html-mobile/index.html")

    assert resp is not None
    assert marker not in resp.body, "la copia manomessa su disco è stata servita"
    assert resp.body == read_asset("jenny.templates.ui", "index.html")


def test_tampered_first_party_js_is_not_served(tmp_path):
    handler = _make_handler(tmp_path)
    rel = "assets/shared/api-client.js"
    disk = handler.static_dist_path / rel
    disk.parent.mkdir(parents=True, exist_ok=True)
    disk.write_bytes(b"export const api = {}; window.__pwned = 1;")

    resp = handler._serve_static(f"/html-mobile/{rel}")

    assert resp is not None
    assert b"__pwned" not in resp.body
    assert resp.body == read_asset("jenny.templates.ui", rel)


def test_spa_fallback_serves_canonical_index_even_without_disk_copy(tmp_path):
    # Nessun index.html su disco: un percorso sconosciuto deve comunque servire
    # la shell canonica, non 404.
    handler = _make_handler(tmp_path)

    resp = handler._serve_static("/html-mobile/some/unknown/route")

    assert resp is not None
    assert resp.body == read_asset("jenny.templates.ui", "index.html")


def test_non_manifest_orphan_file_is_served_from_disk(tmp_path):
    # File estraneo (fuori manifest): servito dal disco così com'è. È innocuo
    # perché l'index.html canonico non lo referenzia mai, quindi non viene
    # caricato dalla pagina; il test documenta il confine del fallback.
    handler = _make_handler(tmp_path)
    (handler.static_dist_path / "orphan.js").write_bytes(b"console.log('orphan')")

    resp = handler._serve_static("/html-mobile/orphan.js")

    assert resp is not None
    assert resp.body == b"console.log('orphan')"


def test_font_asset_is_served_from_disk(tmp_path):
    # I tipi non attivi (font/immagini) non eseguono codice: restano serviti
    # dal disco anche se nel manifest. Il file lo scrive il test, quindi il
    # nome serve solo a essere di un tipo non attivo — ma è comunque uno che
    # esiste davvero, per non descrivere un asset che non spediamo.
    handler = _make_handler(tmp_path)
    image = "assets/jenny-idle.webp"
    disk = handler.static_dist_path / image
    disk.parent.mkdir(parents=True, exist_ok=True)
    disk.write_bytes(b"RIFF\x00\x00\x00\x00WEBP-test-bytes")

    resp = handler._serve_static(f"/html-mobile/{image}")

    assert resp is not None
    assert resp.body == b"RIFF\x00\x00\x00\x00WEBP-test-bytes"
