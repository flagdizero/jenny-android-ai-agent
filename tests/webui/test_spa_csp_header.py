"""La CSP della shell SPA — nessun test la guardava, e si e' visto.

Questo header e' enforcing da luglio 2026 e non era asserito da nessuna parte
(l'unica CSP sotto test era quella della route media, che e' un'altra:
``default-src 'none'``). Il costo, misurato sul device il 01/09/2026: la vista
esterna di una Jenny App e' servita dal proxy su loopback su una porta effimera,
cioe' **un'altra origine**; senza ``frame-src`` la direttiva ricadeva su
``default-src 'self'`` e la shell bloccava il proprio iframe con
``net::ERR_BLOCKED_BY_CSP``. Un header di sicurezza non asserito e' un header
che cambia comportamento senza che niente lo dica.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support.gateway_http import make_handler

from jenny.webui.ws_http import GatewayHTTPHandler

_AUTH_SECRET = "test-secret"


def _make_handler(tmp_path: Path) -> GatewayHTTPHandler:
    handler = make_handler(tmp_path / "skills")
    ui_dir = (tmp_path / "ui").resolve()
    ui_dir.mkdir(parents=True, exist_ok=True)
    handler.static_dist_path = ui_dir
    return handler


def _csp(tmp_path: Path, document: str = "index.html") -> str:
    handler = _make_handler(tmp_path)
    # Il documento deve esistere su disco. Per un path sconosciuto `_serve_static`
    # ricade sulla shell e servirebbe *index.html*: un test su un altro guscio
    # passerebbe misurando index.html, cioe' il motivo sbagliato.
    (handler.static_dist_path / document).write_text(
        "<!DOCTYPE html><html></html>", encoding="utf-8"
    )
    resp = handler._serve_static(f"/html-mobile/{document}")
    assert resp is not None and resp.status_code == 200
    return resp.headers.get("content-security-policy", "")


def test_index_html_carries_a_csp(tmp_path):
    assert _csp(tmp_path), "la shell deve essere servita con una CSP"


@pytest.mark.parametrize("directive", [
    "default-src 'self'",
    "script-src 'self'",
    "connect-src 'self' ws: wss:",
    "object-src 'none'",
    "base-uri 'none'",
])
def test_the_directives_that_matter_are_intact(tmp_path, directive):
    """Il perimetro che regge contro un'iniezione nella SPA.

    ``frame-src`` sotto allarga cosa si puo' incorniciare; queste no, e allargare
    una di queste per far funzionare una vista sarebbe la scorciatoia sbagliata.
    """
    assert directive in _csp(tmp_path)


def test_frame_src_allows_the_loopback_view_proxy(tmp_path):
    """La porta e' effimera, quindi il jolly e' sulla porta e su nient'altro."""
    csp = _csp(tmp_path)
    assert "frame-src 'self' http://127.0.0.1:*" in csp


def test_frame_src_does_not_open_the_whole_web(tmp_path):
    """Il permesso deve restare loopback: non `*`, non http: nudo, non https:.

    Un `frame-src *` avrebbe fatto funzionare la vista esterna allo stesso modo,
    ed e' esattamente l'errore che questo test esiste per fermare.
    """
    csp = _csp(tmp_path)
    frame_src = next(
        (d.strip() for d in csp.split(";") if d.strip().startswith("frame-src")), ""
    )
    assert frame_src, "frame-src deve essere dichiarata, non lasciata a default-src"
    # Per TOKEN, non per sottostringa: `http:` e' contenuto in
    # `http://127.0.0.1:*`, e un controllo a sottostringa bocciava il valore
    # giusto (preso in pieno la prima volta che ho scritto questo test).
    sources = frame_src.split()[1:]
    for wide in ("*", "http:", "https:", "data:", "'unsafe-inline'"):
        assert wide not in sources, f"frame-src non deve concedere {wide!r}"
    for src in sources:
        assert src == "'self'" or src.startswith("http://127.0.0.1"), (
            f"frame-src concede un'origine non-loopback: {src!r}"
        )


def test_officina_html_carries_the_same_csp(tmp_path):
    """L'officina e' l'altro guscio, e vale la stessa policy.

    Dopo lo scambio dei nomi ``index.html`` e' la casa e l'officina ha il
    proprio: il documento che rischia di restare senza policy e' cambiato, il
    difetto no.

    La CSP era legata alla stringa ``index.html``, quindi un secondo documento
    nasceva senza policy e se la sarebbe presa addosso tutta insieme il giorno
    dello scambio dei nomi (v. `.agent/casa-plan.md`) — cioe' alla fine, quando
    una violazione costa di piu' e si spiega di meno. Il confronto ora e' su
    `_SHELL_DOCUMENTS`, e questo test e' cio' che impedisce di tornare indietro.
    """
    assert _csp(tmp_path, "workshop.html") == _csp(tmp_path, "index.html")


def test_only_shell_documents_get_the_csp(tmp_path):
    """E' una policy di pagina: gli asset non ne hanno bisogno.

    Metterla anche sugli asset non e' innocuo — `default-src 'self'` su un JS
    servito a un iframe a origine opaca romperebbe i suoi stessi fetch.
    """
    handler = _make_handler(tmp_path)
    # L'asset deve esistere su disco: per un path sconosciuto la shell fa da
    # fallback e servirebbe index.html — cioe' il test misurerebbe index.html
    # e passerebbe/fallirebbe per il motivo sbagliato (succede: verificato).
    assets = handler.static_dist_path / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "probe.css").write_text("body{}", encoding="utf-8")
    resp = handler._serve_static("/html-mobile/assets/probe.css")
    assert resp is not None and resp.status_code == 200
    assert b"<!doctype" not in resp.body[:64].lower(), "e' il fallback su index.html"
    assert "content-security-policy" not in {k.lower() for k in resp.headers.keys()}
