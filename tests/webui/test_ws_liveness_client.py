"""Il battito del WebSocket della WebUI aperta da un altro dispositivo.

Il 14/10/2026, su jenny_s22 con Tailscale fermato e riaperto, la pagina sul Mac
teneva un socket «OPEN» che il telefono non conosceva piu': la chiusura del
server non era mai arrivata, e il browser se ne accorge solo scrivendoci.
``shared/ws-manager.js`` manda quindi un ``ping`` applicativo — ogni 25 s e al
ritorno sulla scheda — e senza ``pong`` entro 10 s scarta il socket e si
ricollega. Solo da fuori: su loopback il caso non esiste, e un timer ogni 25 s
nella WebView del telefono sarebbe solo un risveglio in piu'.

Il modulo vero gira in node con un WebSocket finto e un orologio a mano.
"""

from __future__ import annotations

import shutil
import tempfile
import textwrap
from pathlib import Path

from support.js_harness import ASSETS, requires_node, run_module

pytestmark = requires_node

_NEIGHBORS = {
    "api-client.js": "export const api = { getSecret: () => 'k' };\n",
    "i18n.js": "export const i18n = { t: (k) => k };\n",
    "state.js": "export const AppState = {};\n",
}

_PRELUDE = """
import assert from 'node:assert/strict';
let now = 0;
const timers = new Map();
let seq = 0;
const add = (fn, ms, every) => { const id = ++seq; timers.set(id, { fn, at: now + ms, every }); return id; };
globalThis.setTimeout = (fn, ms) => add(fn, ms, 0);
globalThis.setInterval = (fn, ms) => add(fn, ms, ms);
globalThis.clearTimeout = globalThis.clearInterval = (id) => { timers.delete(id); };
const advance = (ms) => {
  const end = now + ms;
  for (;;) {
    const due = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
    if (!due) break;
    const [id, t] = due;
    now = t.at;
    if (t.every) t.at += t.every; else timers.delete(id);
    t.fn();
  }
  now = end;
};

const sockets = [];
class FakeWS {
  static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
  constructor(url) { this.url = url; this.readyState = 0; this.sent = []; sockets.push(this); }
  send(data) { this.sent.push(JSON.parse(data)); }
  close() { this.readyState = 3; this.closeCalled = true; }
  open() { this.readyState = 1; this.onopen?.(); }
  receive(obj) { this.onmessage?.({ data: JSON.stringify(obj) }); }
}
globalThis.WebSocket = FakeWS;
const docListeners = {};
globalThis.document = {
  visibilityState: 'visible',
  addEventListener(t, fn) { (docListeners[t] ||= []).push(fn); },
};
globalThis.window = {
  location: { protocol: 'http:', host: `${HOST}:18790`, hostname: HOST },
  addEventListener() {},
};
const { wsManager } = await import('./shared/ws-manager.js');
const pings = (ws) => ws.sent.filter((m) => m.type === 'ping').length;
const showTab = () => { for (const fn of docListeners.visibilitychange || []) fn(); };
"""


def _run(host: str, body: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "shared").mkdir()
        shutil.copy(ASSETS / "shared" / "ws-manager.js", root / "shared" / "ws-manager.js")
        for name, text in _NEIGHBORS.items():
            (root / "shared" / name).write_text(text, encoding="utf-8")
        entry = root / "prova.mjs"
        entry.write_text(
            f"const HOST = {host!r};\n" + _PRELUDE + textwrap.dedent(body), encoding="utf-8"
        )
        run_module(entry)


def test_from_another_device_a_silent_socket_is_dropped_and_reopened() -> None:
    _run(
        "titan.tail1a2b3c.ts.net",
        """
        wsManager.connectChat();
        const first = sockets[0];
        first.open();
        assert.equal(pings(first), 0);

        advance(25000);
        assert.equal(pings(first), 1, 'nessun ping dopo 25 s');

        // Un pong (o un frame qualunque) tiene vivo il socket.
        first.receive({ event: 'pong' });
        advance(25000);
        assert.equal(pings(first), 2);
        first.receive({ event: 'delta', text: 'x' });
        advance(10000);
        assert.equal(sockets.length, 1, 'ha scartato un socket che rispondeva');

        // Il tunnel e' giu': il ping parte e non torna niente.
        advance(15000);
        assert.equal(pings(first), 3);
        let closed = 0;
        wsManager.addEventListener('chat:close', () => { closed += 1; });
        advance(10000);
        assert.ok(first.closeCalled, 'il socket morto non e\\' stato chiuso');
        assert.equal(first.onmessage, null, 'il socket morto ascolta ancora');
        assert.equal(closed, 1);

        // E si riparte: un socket nuovo dopo il backoff.
        advance(3000);
        assert.equal(sockets.length, 2);
        sockets[1].open();
        advance(25000);
        assert.equal(pings(sockets[1]), 1);
        """,
    )


def test_returning_to_the_tab_probes_at_once_and_only_once() -> None:
    _run(
        "100.70.131.33",
        """
        wsManager.connectChat();
        sockets[0].open();
        showTab();
        assert.equal(pings(sockets[0]), 1, 'tornare sulla scheda non ha controllato il socket');
        showTab();
        assert.equal(pings(sockets[0]), 1, 'un secondo ping con il primo ancora in attesa');
        """,
    )


def test_on_loopback_there_is_no_heartbeat() -> None:
    _run(
        "127.0.0.1",
        """
        wsManager.connectChat();
        sockets[0].open();
        showTab();
        advance(120000);
        assert.equal(pings(sockets[0]), 0, 'la WebView del telefono manda ping');
        assert.equal(sockets.length, 1);
        """,
    )
