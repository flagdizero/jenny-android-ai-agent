"""Il pannello «Da altri dispositivi» dell'officina (``mobile-remote-access.js``).

Il modulo vero gira in node con i vicini finti: un ``rpc`` che risponde lo stato
dato dal test, ``i18n`` che restituisce la chiave, un elemento che tiene
l'``innerHTML`` come stringa. Si guarda cosa il pannello mette a schermo nei
suoi stati, e soprattutto **dove finisce la chiave**: nel link e nel QR, mai
nell'indirizzo che si legge.
"""

from __future__ import annotations

import shutil
import tempfile
import textwrap
from pathlib import Path

from support.js_harness import ASSETS, requires_node, run_module

pytestmark = requires_node

_NEIGHBORS = {
    "api-client.js": "export const launched = []; "
    "export const api = { launchAndroidApp: async (p) => { launched.push(p); return { launched: true }; } };\n",
    "rpc-client.js": "export const calls = []; export const replies = {}; "
    "export const rpc = { remote: async (m, p) => { calls.push([m, p]); return replies[m]; } };\n",
    "i18n.js": "export const i18n = { locale: 'it', "
    "t: (k, p) => p ? `${k}${JSON.stringify(p)}` : k };\n",
    "utils.js": "export const toasts = []; export const clip = [];\n"
    "export function showToast(m) { toasts.push(m); }\n"
    "export async function copyToClipboard(t) { clip.push(t); return true; }\n"
    "export function escapeHtml(s) { return String(s).replace(/&/g, '&amp;')"
    ".replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;'); }\n",
    "dialog.js": "export const dialogs = []; export let answer = 'ok';\n"
    "export function setAnswer(a) { answer = a; }\n"
    "export async function detailDialog(o) { dialogs.push(o); return answer; }\n",
}

_PRELUDE = """
import assert from 'node:assert/strict';
globalThis.window = globalThis;
globalThis.setTimeout = () => 0;
globalThis.clearTimeout = () => {};
const { rpc, calls, replies } = await import('./shared/rpc-client.js');
const { clip, toasts } = await import('./shared/utils.js');
const { dialogs, setAnswer } = await import('./shared/dialog.js');
const { launched } = await import('./shared/api-client.js');
const mod = await import('./mobile-remote-access.js');
const { RemoteAccessPanel, remoteAddress, remoteLink, remoteSummary } = mod;

const el = () => ({
  innerHTML: '', isConnected: true, listeners: {},
  closest: () => ({ classList: { contains: () => true } }),
  querySelector() { return null; },
  querySelectorAll() { return []; },
});
const ON = {
  enabled: true, listening: true, address: '100.94.12.7', name: 'titan.tail1a2b3c.ts.net',
  port: 18790, error: null, key: 'k3y/+=', here: false,
  peers: [{ ip: '100.101.7.33', name: 'macbook', since: 1760000000 }],
};
async function panelWith(status) {
  replies.status = status;
  const body = el();
  const seen = [];
  const panel = new RemoteAccessPanel(body, { onStatus: (s) => seen.push(s) });
  await panel.refresh();
  return { panel, body, seen };
}
"""


def _run(body: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "shared").mkdir()
        shutil.copy(ASSETS / "mobile-remote-access.js", root / "mobile-remote-access.js")
        for name, text in _NEIGHBORS.items():
            (root / "shared" / name).write_text(text, encoding="utf-8")
        entry = root / "prova.mjs"
        entry.write_text(_PRELUDE + textwrap.dedent(body), encoding="utf-8")
        run_module(entry)


def test_the_key_goes_in_the_link_fragment_and_never_on_screen() -> None:
    _run(
        """
        assert.equal(remoteAddress(ON), 'http://titan.tail1a2b3c.ts.net:18790');
        assert.equal(remoteLink(ON), 'http://titan.tail1a2b3c.ts.net:18790/#bs=k3y%2F%2B%3D');
        // Senza nome MagicDNS l'IP; un IPv6 tra parentesi quadre.
        assert.equal(remoteAddress({ ...ON, name: null }), 'http://100.94.12.7:18790');
        assert.equal(remoteAddress({ ...ON, name: null, address: 'fd7a:115c:a1e0::1' }),
          'http://[fd7a:115c:a1e0::1]:18790');
        // Spento, il server non manda la chiave: niente link.
        assert.equal(remoteLink({ ...ON, key: null }), null);

        let { body } = await panelWith(ON);
        assert.ok(body.innerHTML.includes('http://titan.tail1a2b3c.ts.net:18790'));
        assert.ok(!body.innerHTML.includes('k3y'), 'la chiave e\\' finita a schermo');
        assert.ok(body.innerHTML.includes('settings.remote.copyLink'));
        assert.ok(body.innerHTML.includes('settings.remote.disconnectAll'));
        assert.ok(body.innerHTML.includes('macbook'));
        assert.ok(!body.innerHTML.includes('settings.remote.windows'));

        // Lo stesso dispositivo con tre finestre: una riga, e quante sono.
        ({ body } = await panelWith({ ...ON, peers: [{ ...ON.peers[0], windows: 3 }] }));
        assert.ok(body.innerHTML.includes('100.101.7.33 · settings.remote.windows{&quot;count&quot;:3}'));
        """
    )


def test_the_summary_row_follows_the_state() -> None:
    _run(
        """
        assert.equal(remoteSummary(null), 'settings.loading');
        assert.equal(remoteSummary({ ...ON, enabled: false }), 'settings.remote.summaryOff');
        assert.equal(remoteSummary({ ...ON, listening: false }), 'settings.remote.summaryWaiting');
        assert.equal(remoteSummary({ ...ON, peers: [] }), 'settings.remote.summaryOn');
        assert.equal(remoteSummary(ON), 'settings.remote.summaryOnNamed{"name":"macbook"}');
        assert.equal(remoteSummary({ ...ON, peers: [ON.peers[0], { ip: '100.1.1.1', since: 1 }] }),
          'settings.remote.summaryOnMany{"count":2}');

        const { seen } = await panelWith(ON);
        assert.equal(seen.length, 1, 'la riga in cassetto non ha saputo lo stato');
        """
    )


def test_off_waiting_and_remote_states() -> None:
    _run(
        """
        let { body } = await panelWith({ ...ON, enabled: false, key: null, peers: [] });
        assert.ok(body.innerHTML.includes('settings.remote.tsUp'));
        assert.ok(body.innerHTML.includes('settings.remote.offHint'));
        assert.ok(!body.innerHTML.includes('settings.remote.copyLink'));

        ({ body } = await panelWith({ ...ON, enabled: false, address: null, key: null }));
        assert.ok(body.innerHTML.includes('settings.remote.tsUnknown'));

        // Acceso ma Tailscale giu': l'avviso, e «Apri Tailscale» solo nel guscio.
        ({ body } = await panelWith({ ...ON, listening: false, address: null, peers: [] }));
        assert.ok(body.innerHTML.includes('settings.remote.tsDown'));
        assert.ok(!body.innerHTML.includes('data-remote="open-ts"'));
        globalThis.JennyNative = {};
        ({ body } = await panelWith({ ...ON, listening: false, address: null, peers: [] }));
        assert.ok(body.innerHTML.includes('data-remote="open-ts"'));
        delete globalThis.JennyNative;
        assert.ok(body.innerHTML.includes('settings.remote.waiting'));

        // Jenny lo sta riaccendendo: lo si dice, col tentativo.
        ({ body } = await panelWith({
          ...ON, listening: false, address: null, peers: [], tailscale: 'reviving', revive_attempts: 2,
        }));
        assert.ok(body.innerHTML.includes('settings.remote.reviving{&quot;count&quot;:2}'));
        assert.ok(body.innerHTML.includes('settings.remote.tsDown'));

        // Un errore di bind si dice, senza l'avviso di Tailscale.
        ({ body } = await panelWith({ ...ON, listening: false, error: 'Address already in use' }));
        assert.ok(body.innerHTML.includes('Address already in use'));
        assert.ok(!body.innerHTML.includes('settings.remote.tsDown'));

        // Aperto da fuori: l'avviso che spegnere taglia fuori questa finestra.
        ({ body } = await panelWith({ ...ON, here: true }));
        assert.ok(body.innerHTML.includes('settings.remote.remoteHere'));
        """
    )


def test_actions_copy_rotate_and_turning_off_from_outside() -> None:
    _run(
        """
        let { panel } = await panelWith(ON);
        await panel._action('copy');
        assert.deepEqual(clip, [remoteLink(ON)]);

        replies.rotate = { ...ON, key: 'new' };
        setAnswer('cancel');
        await panel._action('rotate');
        assert.ok(!calls.some(([m]) => m === 'rotate'), 'ha cambiato la chiave senza conferma');
        setAnswer('ok');
        await panel._action('rotate');
        assert.ok(calls.some(([m]) => m === 'rotate'));
        assert.equal(panel.status.key, 'new');
        assert.equal(dialogs.at(-1).bodyHtml.includes('disconnectBodyHere'), false);

        // Da fuori: spegnere chiede prima, e «annulla» rimette l'interruttore.
        ({ panel } = await panelWith({ ...ON, here: true }));
        const input = { checked: false };
        setAnswer('cancel');
        await panel._toggle(input);
        assert.equal(input.checked, true);
        assert.ok(!calls.some(([m]) => m === 'set'));
        replies.set = { ...ON, enabled: false, here: true };
        setAnswer('ok');
        input.checked = false;
        await panel._toggle(input);
        assert.deepEqual(calls.at(-1), ['set', { enabled: false }]);

        // Dal telefono si spegne senza domande.
        ({ panel } = await panelWith(ON));
        const before = dialogs.length;
        await panel._toggle({ checked: false });
        assert.equal(dialogs.length, before);

        await panel._action('open-ts');
        assert.deepEqual(launched, ['com.tailscale.ipn']);
        """
    )
