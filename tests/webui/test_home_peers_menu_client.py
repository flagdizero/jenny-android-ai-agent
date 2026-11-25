"""La stanza «Altre Jenny» è un menu: l'elenco, e una pagina per collegata.

Fino al 25/11/2026 ogni collegata era una scheda con dentro stato, «Parla»,
interruttore e livello, e file, cronologia e scollegamento erano un rimando
all'officina. Ora l'elenco è solo un elenco e ogni collegata ha la sua pagina.

Qui si prova quel che lega i pezzi, nella casa vera in jsdom: il tocco su una
riga apre la sua pagina e la testa dice il suo nome; Indietro fa un passo
dentro la stanza; il tocco sul testo di una riga muove il suo interruttore, e
una volta sola; scollegare chiede prima; il giro di controllo non cambia i
nodi sotto le dita. Le frasi e i colori li guarda chi la prova a schermo.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, sent, rpcAnswers, locales, $ } from './boot.mjs';

/* Il banco apre la casa in inglese: le frasi si prendono da li', non a mano. */
const P = locales.en.home.peers;
const named = (text, name) => text.replace('{name}', name);
const LIST = `${locales.en.home.strip.settings} > ${P.title}`;
const pageOf = (name) => `${P.title} > ${name}`;

const NOW = Date.now();
const state = {
  enabled: true, pairing: null, tailscale: { state: 'up' },
  peers: [
    { id: 'p1', name: 'Lavoro', kind: 'jenny', has_secret: true, queued: 0,
      last_seen_ms: NOW - 120000, last_try_ms: NOW - 120000, last_error: '',
      line: true, can_ask: true, act: 'confirm', send_files: false },
    { id: 'p2', name: 'Mare', kind: 'jenny', has_secret: true, queued: 2,
      last_seen_ms: NOW - 72000000, last_try_ms: NOW - 3600000, last_error: 'timeout',
      line: false, can_ask: true, act: 'off', send_files: false },
    { id: 'p3', name: 'Mac', kind: 'opencode', has_secret: true, queued: 0,
      last_seen_ms: NOW - 600000, last_try_ms: NOW - 600000, last_error: '',
      line: false, can_ask: false, act: 'off', send_files: false },
  ],
};
const status = () => JSON.parse(JSON.stringify(state));
const peer = (id) => state.peers.find((p) => p.id === id);
Object.assign(rpcAnswers, {
  'peer.status': status,
  'peer.permissions': ({ id, ...flags }) => { Object.assign(peer(id), flags); return status(); },
  'peer.ping': status,
  'peer.rename': ({ id, name }) => { peer(id).name = name; return status(); },
  'peer.remove': ({ id }) => { state.peers = state.peers.filter((p) => p.id !== id); return status(); },
  'peer.history': () => ({ ...status(), history: [
    { direction: 'out', kind: 'ask', at_ms: NOW - 60000, data: { text: 'è arrivata la mail?' } },
    { direction: 'out', kind: 'file_part', at_ms: NOW - 50000, data: {} },
    { direction: 'sys', kind: 'paired', at_ms: NOW - 900000, data: {} },
  ] }),
  'peer.acts': () => ({ ...status(), acts: [] }),
});

const body = () => $('home-peers-body');
const rpcs = (method) => sent.filter((m) => m.type === 'rpc' && m.method === method).map((m) => m.params);
const rows = () => [...body().querySelectorAll('.home-peers-peer')];
const byText = (text, root = body()) =>
  [...root.querySelectorAll('button, .home-row')].find((n) => n.textContent.includes(text));
const path = () => `${$('home-path-root-name').textContent} > ${$('home-head-name').textContent}`;

const open = async () => {
  const app = await boot();
  app.openPeers();
  await tick(30);
  return app;
};
const openPeer = async (name) => {
  const app = await open();
  rows().find((r) => r.textContent.includes(name)).click();
  await tick(10);
  return app;
};
"""


def test_the_list_is_one_row_per_linked_jenny_and_a_row_opens_her_page() -> None:
    run_home(_HEAD + """
const app = await open();
assert.deepEqual(rows().map((r) => r.dataset.peer), ['p1', 'p2', 'p3']);
// L'elenco non porta comandi: niente interruttori, e nessun rimando all'officina.
assert.equal(body().querySelectorAll('.home-switch').length, 0);
assert.equal(path(), LIST);

rows()[0].click();
await tick(10);
assert.equal(path(), pageOf('Lavoro'));
assert.ok(body().querySelector('.home-peers-hero'), 'manca la testa della pagina');
assert.equal(body().querySelectorAll('.home-switch').length, 2);

// Indietro fa un passo dentro la stanza, poi esce.
assert.equal($('home-back').getAttribute('aria-label'), P.back.list);
app.handleHardwareBack();
await tick(10);
assert.equal(path(), LIST);
assert.equal(rows().length, 3);
app.peersRoom.close();
""")


def test_the_root_of_the_path_goes_back_to_the_list() -> None:
    run_home(_HEAD + """
const app = await openPeer('Lavoro');
byText(P.rename).click();
await tick(10);
assert.equal($('home-back').getAttribute('aria-label'), named(P.back.peer, 'Lavoro'));
$('home-path-root').click();
await tick(10);
assert.equal(path(), LIST);
assert.equal(app.view, 'peers');
app.peersRoom.close();
""")


def test_a_tap_on_the_row_moves_its_switch_once() -> None:
    run_home(_HEAD + """
const app = await openPeer('Lavoro');
// Sul testo della riga: su un telefono si mira alla riga, non al pallino.
byText(P.perm.files).querySelector('.home-row-label').click();
await tick(10);
assert.deepEqual(rpcs('peer.permissions'), [{ id: 'p1', send_files: true }]);

// Sull'interruttore: il tocco risale alla riga, e non deve contare due volte.
byText(P.perm.line).querySelector('.home-switch').click();
await tick(10);
assert.deepEqual(rpcs('peer.permissions').slice(1), [{ id: 'p1', line: false }]);
assert.equal(byText(P.perm.line).querySelector('.home-switch')
  .getAttribute('aria-checked'), 'false');
app.peersRoom.close();
""")


def test_a_computer_has_no_permissions_to_give() -> None:
    run_home(_HEAD + """
const app = await openPeer('Mac');
assert.equal(body().querySelectorAll('.home-switch').length, 0);
assert.equal(byText(P.ask), undefined);
assert.ok(byText(named(P.unlink, 'Mac')), 'un computer si scollega come le altre');
app.peersRoom.close();
""")


def test_one_that_does_not_answer_says_so_and_can_be_retried() -> None:
    run_home(_HEAD + """
const app = await openPeer('Mare');
const warn = body().querySelector('.home-peers-warn');
assert.ok(warn, 'manca l\\u2019avviso');
assert.ok(warn.textContent.includes('2'), 'non dice quanti aspettano');
warn.querySelector('button').click();
await tick(10);
assert.deepEqual(rpcs('peer.ping'), [{ id: 'p2' }]);
// «Parla» resta: quel che scrivi parte appena torna.
assert.equal(body().querySelector('.home-peers-talkbig').disabled, false);
app.peersRoom.close();
""")


def test_unlinking_asks_first_and_then_goes_back_to_the_list() -> None:
    run_home(_HEAD + """
const app = await openPeer('Lavoro');
byText(named(P.unlink, 'Lavoro')).click();
await tick(10);
const sheet = $('home-line-sheet');
assert.ok(sheet.open, 'il foglio non si e\\u2019 aperto');
assert.deepEqual(rpcs('peer.remove'), []);

byText(named(P.unlink, 'Lavoro'), sheet).click();
await tick(20);
assert.deepEqual(rpcs('peer.remove'), [{ id: 'p1' }]);
assert.equal(path(), LIST);
assert.deepEqual(rows().map((r) => r.dataset.peer), ['p2', 'p3']);
app.peersRoom.close();
""")


def test_the_history_shows_the_lines_it_has_words_for() -> None:
    run_home(_HEAD + """
const app = await openPeer('Lavoro');
byText(P.history).click();
await tick(30);
const lines = [...body().querySelectorAll('.home-peers-log .home-row-label')].map((n) => n.textContent);
// I pezzi di un file non hanno una frase: non compaiono.
assert.equal(lines.length, 2);
assert.ok(lines[0].includes('è arrivata la mail?'));
assert.equal($('home-back').getAttribute('aria-label'), named(P.back.peer, 'Lavoro'));
app.peersRoom.close();
""")


def test_the_poll_leaves_the_nodes_alone_when_nothing_changed() -> None:
    run_home(_HEAD + """
const app = await open();
const before = rows()[0];
await app.peersRoom.refresh();
await app.peersRoom.refresh();
// Un tocco vuole lo stesso nodo all'andata e al ritorno: cambiarlo ogni
// quattro secondi ne perdeva uno ogni tanto.
assert.equal(rows()[0], before);

// Quando lo stato cambia, il disegno segue.
peer('p1').name = 'Ufficio';
await app.peersRoom.refresh();
assert.ok(rows()[0].textContent.includes('Ufficio'));
app.peersRoom.close();
""")


def test_a_linked_jenny_that_disappears_closes_her_page() -> None:
    run_home(_HEAD + """
const app = await openPeer('Lavoro');
// L'ha scollegata l'altra, mentre la sua pagina era aperta.
state.peers = state.peers.filter((p) => p.id !== 'p1');
await app.peersRoom.refresh();
assert.equal(path(), LIST);
assert.deepEqual(rows().map((r) => r.dataset.peer), ['p2', 'p3']);
app.peersRoom.close();
""")


def test_with_no_linked_jenny_the_room_says_what_one_is_for() -> None:
    run_home(_HEAD + """
state.peers = [];
state.tailscale = { state: 'down' };
const app = await open();
assert.equal(rows().length, 0);
assert.equal(body().querySelectorAll('.home-peers-use').length, 3);
// Tailscale spento si vede subito: senza, collegare non riesce.
assert.ok(body().querySelector('.home-line-dot.is-off'), 'non dice che Tailscale e\\u2019 spento');
byText(P.add).click();
await tick(10);
assert.ok(body().querySelector('.home-peers-steps'), 'non e\\u2019 arrivato al collegamento');
app.peersRoom.close();
""")
