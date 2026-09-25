"""La sessione di navigazione filtra anche le connessioni che non sono HTTP.

``shouldInterceptRequest`` vede le richieste HTTP della pagina, e il suo
commento diceva «ogni richiesta». WebSocket, WebTransport e WebRTC Chromium le
apre fuori da quel percorso: una pagina visitata dall'agente poteva parlare con
``ws://192.168.1.1``. E le richieste di un service worker passano dal client del
profilo, non da quello della WebView.

Il rimedio è parziale e il test fissa anche quel che promette: la guardia lato
pagina (``res/raw/browser_network_guard.js``) si esegue qui davvero, sotto node,
contro un ``window`` finto; l'aggancio nel Kotlin si legge dal sorgente.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import requires_node, run_js
from support.kotlin_source import ANDROID_SRC, function_body, read_code

ROOT = Path(__file__).resolve().parents[2]
GUARD_JS = ROOT / "android" / "app" / "src" / "main" / "res" / "raw" / "browser_network_guard.js"


def _bridge() -> str:
    return read_code("JennyBrowserBridge")


# ── Kotlin: la guardia è agganciata ─────────────────────────────────────────


def test_the_session_webview_gets_the_guard_before_any_page() -> None:
    ensure = function_body(_bridge(), "ensureWebViewOnMain")
    assert "installNetworkGuardOnMain(wv)" in ensure
    assert ensure.index("installNetworkGuardOnMain(wv)") < ensure.index("webView = wv")
    install = function_body(_bridge(), "installNetworkGuardOnMain")
    assert "WebViewFeature.DOCUMENT_START_SCRIPT" in install
    assert "WebViewCompat.addDocumentStartJavaScript(wv, networkGuardJs, setOf(" in install
    # code_only svuota le stringhe: il nome si controlla sul sorgente vero, sotto.
    assert re.search(r'addJavascriptInterface\(NetworkGuard\(\), "\s*"\)', install)
    raw = (ANDROID_SRC / "JennyBrowserBridge.kt").read_text(encoding="utf-8")
    assert 'addJavascriptInterface(NetworkGuard(), "JennyBrowserGuard")' in raw
    assert 'setOf("*")' in raw
    assert "R.raw.browser_network_guard" in raw


def test_the_guard_asks_the_same_verdict_as_http() -> None:
    """Un secondo elenco di reti in JS divergerebbe dal primo alla prima modifica."""
    blocked = function_body(_bridge(), "blocked")
    assert "isBlockedHost(" in blocked
    intercept = function_body(_bridge(), "blockedResponseFor")
    assert "isBlockedHost(host)" in intercept
    code = _bridge()
    assert "@JavascriptInterface\n        fun blocked(host: String): Boolean" in code


def test_service_worker_requests_go_through_the_same_filter() -> None:
    ensure = function_body(_bridge(), "ensureWebViewOnMain")
    # Solo sul profilo separato: quello di default è della SPA e di web_fetch.
    assert ensure.index("profile = p") < ensure.index("guardServiceWorkersOnMain(p)")
    sw = function_body(_bridge(), "guardServiceWorkersOnMain")
    assert "p.serviceWorkerController.setServiceWorkerClient(" in sw
    assert "blockedResponseFor(request.url" in sw


def test_the_comment_no_longer_claims_every_request() -> None:
    raw = (ANDROID_SRC / "JennyBrowserBridge.kt").read_text(encoding="utf-8")
    assert "l'unico\n        // punto che vede *ogni* richiesta" not in raw
    assert "ed è l'unico" not in raw


# ── La guardia, eseguita ─────────────────────────────────────────────────────


def _run_guard(verdicts: str, script: str, *, with_bridge: bool = True) -> str:
    bridge = (
        f"const VERDICTS = {verdicts};\n"
        "const asked = [];\n"
        "window.JennyBrowserGuard = { blocked(host) { asked.push(host); return !!VERDICTS[host]; } };\n"
        if with_bridge else "const asked = [];\n"
    )
    source = (
        "import assert from 'node:assert/strict';\n"
        "const opened = [];\n"
        "class FakeWS { constructor(url, protocols) { opened.push(String(url)); this.url = url; } }\n"
        "FakeWS.CONNECTING = 0; FakeWS.OPEN = 1; FakeWS.CLOSING = 2; FakeWS.CLOSED = 3;\n"
        "class FakeWT { constructor(url) { opened.push('wt:' + url); } }\n"
        "class FakePC { constructor() { opened.push('pc'); } }\n"
        "const window = { WebSocket: FakeWS, WebTransport: FakeWT, RTCPeerConnection: FakePC,\n"
        "                 webkitRTCPeerConnection: FakePC };\n"
        "const location = { href: 'https://example.com/page' };\n"
        + bridge
        + GUARD_JS.read_text(encoding="utf-8") + "\n" + script
    )
    return run_js(source)


@requires_node
def test_a_websocket_to_the_lan_is_refused_and_a_public_one_opens() -> None:
    out = _run_guard(
        "{ '192.168.1.1': true, 'router.lan': true, '::1': true }",
        """
const WS = window.WebSocket;
assert.throws(() => new WS('ws://192.168.1.1:8123/api/websocket'),
              (e) => e.name === 'SecurityError');
assert.throws(() => new WS('wss://router.lan/'), (e) => e.name === 'SecurityError');
assert.throws(() => new WS('ws://[::1]:9000/'), (e) => e.name === 'SecurityError');
assert.deepEqual(opened, [], 'nessuna connessione aperta verso la LAN');

const ok = new WS('wss://echo.example.org/socket', ['chat']);
assert.deepEqual(opened, ['wss://echo.example.org/socket']);
assert.ok(ok instanceof WS, 'instanceof continua a funzionare');
assert.equal(WS.OPEN, 1);
assert.deepEqual(asked, ['192.168.1.1', 'router.lan', '::1', 'echo.example.org'],
                 'lo stesso verdetto dell\\u2019HTTP, chiesto al nativo');
console.log('ok');
""",
    )
    assert "ok" in out


@requires_node
def test_the_original_constructor_is_not_handed_back() -> None:
    """`WebSocket.prototype.constructor` era la scorciatoia ovvia per aggirarla."""
    out = _run_guard(
        "{ '10.0.0.2': true }",
        """
const Proto = window.WebSocket.prototype.constructor;
assert.equal(Proto, window.WebSocket);
assert.throws(() => new Proto('ws://10.0.0.2/'), (e) => e.name === 'SecurityError');
// Riscrivere il metodo del ponte dopo l'avvio non cambia il verdetto.
window.JennyBrowserGuard.blocked = () => false;
assert.throws(() => new window.WebSocket('ws://10.0.0.2/'), (e) => e.name === 'SecurityError');
assert.throws(() => window.WebSocket('wss://example.org/'), TypeError);
console.log('ok');
""",
    )
    assert "ok" in out


@requires_node
def test_webrtc_is_off_and_webtransport_is_guarded() -> None:
    out = _run_guard(
        "{ '172.16.0.9': true }",
        """
assert.throws(() => new window.RTCPeerConnection({}), (e) => e.name === 'NotSupportedError');
assert.throws(() => new window.webkitRTCPeerConnection({}), (e) => e.name === 'NotSupportedError');
assert.throws(() => new window.WebTransport('https://172.16.0.9:4433/'),
              (e) => e.name === 'SecurityError');
new window.WebTransport('https://example.org:4433/');
assert.deepEqual(opened, ['wt:https://example.org:4433/']);
console.log('ok');
""",
    )
    assert "ok" in out


@requires_node
def test_without_the_native_verdict_it_refuses() -> None:
    """Nel dubbio si blocca, come `isBlockedHost` con un DNS che non risponde."""
    out = _run_guard(
        "{}",
        """
assert.throws(() => new window.WebSocket('wss://example.org/'), (e) => e.name === 'SecurityError');
console.log('ok');
""",
        with_bridge=False,
    )
    assert "ok" in out
