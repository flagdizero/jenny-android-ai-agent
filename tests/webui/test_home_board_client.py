"""La bacheca in casa, con l'``index.html`` vero e i moduli veri (jsdom).

Si guarda il giro che conta: un quaderno si apre sulla bacheca, le schede
portano il loro segnale, il tocco su una polaroid apre il cassetto, una
correzione parte come ``board.fix`` e non scrive niente sul cassetto, e
«indietro» torna da una pagina al cassetto e dal cassetto alla bacheca.
"""

from __future__ import annotations

import json

from support.home_dom import requires_jsdom, run_home
from support.js_harness import ASSETS, function, requires_node, run_js

pytestmark = requires_node

BOARD = {
    "exists": True,
    "skeleton": ["Vaso e travaso", "Acqua"],
    "cards": [
        {"id": "acero", "title": "Acero", "glyph": "leaf", "pos": {"x": 5, "y": 6, "by": "jenny"},
         "pinned": False, "pages": 3, "signal": "new"},
        {"id": "monstera", "title": "Monstera", "glyph": "", "pos": {"x": 47, "y": 4, "by": "user"},
         "pinned": False, "pages": 1, "signal": "soon"},
    ],
    "notes": [
        {"id": "r-concime", "card": "monstera", "text": "Concime",
         "when": {"kind": "date", "date": "2026-10-10", "precision": "day"},
         "pos": {"x": 62, "y": 35, "by": "jenny"}},
    ],
    "connections": [{"a": "acero", "b": "monstera", "label": "vasi dello stesso ordine",
                     "by": "jenny"}],
    "suggestions": [{"id": "sg-ginkgo", "kind": "add_card", "subject": "Ginkgo",
                     "text": "Gli faccio un posto?"}],
    "since": [{"at": "2026-10-04T04:12:00+02:00", "kind": "sections_regenerated",
               "title": "Acero", "count": 2}],
    "offboard": 4,
    "pages": 8,
    "lastSeen": "",
    "pageOwners": {"travaso-acero.md": "acero"},
    "full": False,
}

CARD = {
    "id": "acero", "title": "Acero", "glyph": "leaf",
    "pages": [{"path": "travaso-acero.md", "title": "travaso acero"}],
    "generated": "2026-10-04T04:12:00+02:00",
    "brief": "In vaso 14, sano.",
    "sections": [{
        "title": "Vaso e travaso", "text": "Vaso 20 comprato il 04/09.", "fresh": True,
        "sources": [{"path": "travaso-acero.md", "title": "travaso acero", "exists": True}],
    }],
    "next": {"id": "r-travaso", "card": "acero", "text": "Travaso nel vaso 20",
             "when": {"kind": "date", "date": "2027-03-01", "precision": "month"}},
    "reminders": [],
    "userSections": [],
    "connections": [{"to": "monstera", "title": "Monstera", "label": "vasi dello stesso ordine",
                     "by": "jenny"}],
    "corrections": [],
    "skeleton": ["Vaso e travaso", "Acqua"],
}

_SETUP = """
import assert from 'node:assert/strict';
import { boot, tick, routes, sent, rpcAnswers, $ } from './boot.mjs';
routes['/api/board'] = __BOARD__;
routes['/api/board/card'] = __CARD__;
routes['/api/page'] = { wiki: 'orto', page: 'travaso-acero.md', title: 'travaso acero',
  html: '<p>Vedi <a class="wikilink" href="/?wiki=orto&page=vasi.md">vasi</a>, la '
    + '<a class="wikilink" href="/?wiki=orto&page=dormienza.md">dormienza</a> e la '
    + '<a class="wikilink" href="/?wiki=orto&page=monstera.md">monstera</a>.</p>',
  raw: 'x', board: { card: 'acero', cardTitle: 'Acero', links: {
    'vasi.md': { card: 'acero', title: 'Acero' },
    'dormienza.md': null,
    'monstera.md': { card: 'monstera', title: 'Monstera' },
  } } };
for (const m of ['board.seen', 'board.move', 'board.fix', 'board.suggestion.respond']) {
  rpcAnswers[m] = () => ({ ok: true });
}
const app = await boot();
await app.switchConversation('project:orto');
await tick(30);
await app.openBoard();
await tick(30);
const rpcs = (method) => sent.filter((m) => m.type === 'rpc' && m.method === method);
"""


def _run(script: str) -> None:
    head = (
        _SETUP.replace("__BOARD__", json.dumps(BOARD))
        .replace("__CARD__", json.dumps(CARD))
    )
    run_home(head + script)


@requires_jsdom
def test_a_notebook_opens_on_its_board() -> None:
    _run("""
      assert.equal(app.view, 'board');
      const polaroids = [...document.querySelectorAll('.home-board-polaroid')];
      assert.deepEqual(polaroids.map((p) => p.dataset.id), ['acero', 'monstera']);
      // Un segnale per scheda: puntino blu sull'acero, angolo giallo sulla monstera.
      assert.ok(polaroids[0].querySelector('.home-board-sig-dot'));
      assert.ok(polaroids[1].querySelector('.home-board-sig-corner.is-soon'));
      // La puntina dice chi ha scelto il posto.
      assert.ok(polaroids[0].querySelector('.home-board-pin.is-jenny'));
      assert.ok(polaroids[1].querySelector('.home-board-pin.is-user'));
      assert.equal(document.querySelectorAll('.home-board-postit').length, 1);
      // La busta col numero, e «dall'ultima volta»: una riga piu' la busta.
      assert.equal(document.querySelector('.home-board-envelope-count').textContent, '1');
      assert.equal($('home-board-since').hidden, false);
      assert.match($('home-board-since').textContent, /2/);
      // Aprire la bacheca e' «l'ultima volta».
      assert.equal(rpcs('board.seen').length, 1);
      // I comandi della bacheca in testa: la lente e l'elenco.
      assert.equal($('home-board-search-open').hidden, false);
      assert.equal($('home-view-switch').hidden, false);
      // I fili si disegnano dopo le schede: niente z-index, l'ordine nel DOM.
      const wall = $('home-board-wall');
      assert.ok([...wall.children].indexOf($('home-board-threads'))
        > [...wall.children].indexOf(polaroids[1]));
      // Il muro: le posizioni in percento dello schermo, e lo schermo dell'origine
      // a grandezza naturale finche' non lo sposti o lo ingrandisci.
      assert.equal(polaroids[0].style.left, '5cqw');
      assert.equal(polaroids[0].style.top, '6cqh');
      assert.ok(wall.style.transform.endsWith("scale(1)"));
      app.board.view = { x: -120, y: 40, s: 1.5 };
      app.board._applyView();
      assert.equal(wall.style.transform, 'translate(-120px, 40px) scale(1.5)');
      app.board.resetView();
      assert.equal(wall.style.transform, 'translate(0px, 0px) scale(1)');
      // La busta resta nella finestra, ferma, mentre il muro scorre.
      assert.equal(document.querySelector('.home-board-envelope').parentElement.id, 'home-board-canvas');
    """)


@requires_jsdom
def test_a_tap_on_a_polaroid_opens_its_drawer_and_back_returns() -> None:
    _run("""
      document.querySelector('.home-board-polaroid[data-id="acero"]').click();
      await tick(40);
      assert.equal(app.view, 'drawer');
      assert.match($('home-board-drawer').textContent, /In vaso 14, sano\\./);
      assert.match($('home-board-drawer').textContent, /Travaso nel vaso 20/);
      assert.equal($('home-head-name').textContent, 'Acero');
      assert.equal(rpcs('board.seen').at(-1).params.card, 'acero');
      app.goBackOneRoom();
      await tick(30);
      assert.equal(app.view, 'board');
    """)


@requires_jsdom
def test_a_correction_is_a_request_not_a_write() -> None:
    _run("""
      await app.openCard('acero', 'Acero');
      await tick(40);
      const fix = [...document.querySelectorAll('.home-drawer-link.is-fix')][0];
      fix.click();
      const area = document.querySelector('.home-drawer-fix-area');
      assert.ok(area, 'niente campo di correzione');
      area.value = 'Il vaso 20 e\\u2019 del 05/09.';
      const send = [...document.querySelectorAll('.home-board-btn.is-primary')]
        .find((b) => b.closest('.home-drawer'));
      send.click();
      await tick(40);
      const call = rpcs('board.fix').at(-1);
      assert.equal(call.params.card, 'acero');
      assert.equal(call.params.section, 'Vaso e travaso');
      assert.equal(call.params.wiki, 'orto');
    """)


@requires_jsdom
def test_a_page_from_a_drawer_goes_back_to_the_drawer_and_dresses_its_links() -> None:
    _run("""
      // La sanificazione e' di DOMPurify, che il banco non carica: qui passa
      // l'HTML com'e', che e' quel che serve a guardare i link.
      globalThis.DOMPurify = { sanitize: (h) => String(h), addHook() {}, removeHook() {},
        removeAllHooks() {}, isSupported: true };
      await app.openCard('acero', 'Acero');
      await tick(40);
      await app.openPage('travaso-acero.md', 'travaso acero', 'acero');
      await tick(40);
      assert.equal(app.view, 'reader');
      assert.equal($('home-path-root-name').textContent, 'Acero');
      const links = [...$('home-reader-body').querySelectorAll('a')];
      const [same, offboard, other] = links;
      assert.ok(!same.classList.contains('home-link-drawer'));
      assert.ok(offboard.classList.contains('home-link-offboard'));
      assert.ok(other.classList.contains('home-link-drawer'));
      assert.match(other.textContent, /Monstera/);
      // Un link a un altro cassetto apre quel cassetto, non la pagina.
      other.click();
      await tick(40);
      assert.equal(app.view, 'drawer');
      assert.equal(app.drawer.card, 'monstera');
    """)


@requires_jsdom
def test_dragging_a_card_tells_the_server_and_turns_its_pin_red() -> None:
    _run("""
      const el = document.querySelector('.home-board-polaroid[data-id="acero"]');
      const ev = (type, x, y) => {
        const e = new window.Event(type, { bubbles: true });
        Object.assign(e, { clientX: x, clientY: y, button: 0, pointerId: 1 });
        el.dispatchEvent(e);
      };
      ev('pointerdown', 10, 10);
      ev('pointermove', 60, 50);
      ev('pointerup', 60, 50);
      await tick(20);
      const move = rpcs('board.move').at(-1);
      assert.ok(move, 'nessuno spostamento al server');
      assert.equal(move.params.id, 'acero');
      assert.equal(move.params.kind, 'card');
      assert.ok(el.querySelector('.home-board-pin.is-user'));
    """)


@requires_jsdom
def test_the_envelope_answers_go_to_the_server() -> None:
    _run("""
      document.querySelector('.home-board-envelope').click();
      await tick(10);
      assert.ok($('home-board-sheet').open, 'la busta non si apre');
      const yes = [...$('home-board-sheet-body').querySelectorAll('.home-board-btn')].at(-1);
      yes.click();
      await tick(40);
      const call = rpcs('board.suggestion.respond').at(-1);
      assert.deepEqual([call.params.id, call.params.answer], ['sg-ginkgo', 'yes']);
    """)


_PURE = """
import assert from 'node:assert/strict';
const i18n = { locale: 'it', t: (k, p) => (k === 'board.since.kind.card_added' ? `${p.title} ok` : k) };
__TINT__
__WHEN__
__SINCE__
__CLASSIFY__
__SPARK__

// La tinta e' stabile per id, e cambia fra id diversi.
assert.deepEqual(tintFor('acero'), tintFor('acero'));
assert.notDeepEqual(tintFor('acero'), tintFor('monstera'));

// Un post-it scrive un mese come un mese, e una condizione per quel che misura.
assert.match(whenLabel({ kind: 'date', date: '2027-03-01', precision: 'month' }, 'it'), /2027/);
assert.doesNotMatch(whenLabel({ kind: 'date', date: '2026-10-10' }, 'it'), /2026/);
assert.equal(whenLabel({ kind: 'condition', field: 'umidita', op: '<', value: 30 }), 'umidita < 30');

// Una riga di «dall'ultima volta» di un tipo sconosciuto non esce come chiave.
assert.equal(sinceText({ kind: 'card_added', title: 'Ginkgo' }), 'Ginkgo ok');
assert.equal(sinceText({ kind: 'mai-visto' }), 'board.since.kind.other');

// I tre tipi di link.
const board = { card: 'acero', links: {
  'vasi.md': { card: 'acero', title: 'Acero' },
  'dormienza.md': null,
  'monstera.md': { card: 'monstera', title: 'Monstera' },
} };
assert.deepEqual(classifyBoardLink({ kind: 'page', path: 'vasi.md' }, board),
  { kind: 'page', path: 'vasi.md' });
assert.deepEqual(classifyBoardLink({ kind: 'page', path: 'dormienza.md' }, board),
  { kind: 'offboard', path: 'dormienza.md' });
assert.deepEqual(classifyBoardLink({ kind: 'page', path: 'monstera.md' }, board),
  { kind: 'drawer', card: 'monstera', title: 'Monstera' });
// Senza bacheca, o per un link che non conosce, il link resta un link.
assert.deepEqual(classifyBoardLink({ kind: 'page', path: 'x.md' }, null), { kind: 'page', path: 'x.md' });
assert.deepEqual(classifyBoardLink({ kind: 'page', path: 'x.md' }, board), { kind: 'page', path: 'x.md' });
assert.deepEqual(classifyBoardLink({ kind: 'external', href: 'https://x' }, board),
  { kind: 'external', href: 'https://x' });

// Un andamento ha bisogno di almeno due punti.
assert.equal(sparkline([3]), '');
assert.match(sparkline([1, 2, 3]), /<polyline points="/);
console.log('ok');
"""


def test_the_pure_helpers_of_the_board() -> None:
    board = (ASSETS / "home-board.js").read_text(encoding="utf-8")
    reader = (ASSETS / "home-reader.js").read_text(encoding="utf-8")
    drawer = (ASSETS / "home-board-drawer.js").read_text(encoding="utf-8")
    script = (
        _PURE.replace("__TINT__", function(board, "tintFor"))
        .replace("__WHEN__", function(board, "whenLabel"))
        .replace("__SINCE__", function(board, "sinceText"))
        .replace("__CLASSIFY__", function(reader, "classifyBoardLink"))
        .replace("__SPARK__", function(drawer, "sparkline"))
    )
    assert run_js(script).strip().endswith("ok")


_MICRO = {
    "slug": "m-acerello", "micro": "m-acerello", "name": "Acerello", "label": "Umidità",
    "unit": "%", "field": "moisture", "type": "number", "verbs": [], "params": {},
    "value": 38, "at": "2026-10-05T12:00:00+00:00", "trend": [40, 39, 38],
    "dataState": "ok", "extras": [{"field": "online", "label": "Sensore", "unit": "",
                                   "type": "bool", "value": True}],
    "source": "hub WaterBot", "every": 30, "net": [], "uses": ["waterbot-imh"],
    "codeStatus": "ok", "lastRunAt": "", "nextRunAt": "", "lastError": "", "failures": 0,
}


def _run_micro(state: str, script: str, *, error: str = "") -> None:
    board = json.loads(json.dumps(BOARD))
    board["cards"][0]["auto"] = True
    card = json.loads(json.dumps(CARD))
    view = {**_MICRO, "state": state, "lastError": error}
    card["userSections"] = [{"id": "s-micro", "kind": "micro", "micro": "m-acerello",
                             "pending": state == "pending", "view": view}]
    head = (
        _SETUP.replace("__BOARD__", json.dumps(board)).replace("__CARD__", json.dumps(card))
        + "for (const m of ['board.micro.keep', 'board.micro.discard', 'board.micro.run',"
          " 'board.micro.pause']) rpcAnswers[m] = () => ({ ok: true });\n"
    )
    run_home(head + script)


@requires_jsdom
def test_a_micro_app_on_trial_is_kept_from_the_drawer() -> None:
    _run_micro("pending", """
      // Il segno ⟳ sulla polaroid: qualcosa gira da solo, ma non e' un segnale rosso.
      const polaroid = document.querySelector('.home-board-polaroid[data-id="acero"]');
      assert.ok(polaroid.querySelector('.home-board-auto'));
      await app.openCard('acero', 'Acero');
      await tick(40);
      const drawer = $('home-board-drawer');
      assert.match(drawer.textContent, /38%/);
      assert.match(drawer.textContent, /waterbot-imh/);
      assert.ok(drawer.querySelector('.home-drawer-app.is-pending'));
      const keep = [...drawer.querySelectorAll('.home-board-btn.is-primary')]
        .find((b) => /Tienila|Keep/.test(b.textContent));
      keep.click();
      await tick(40);
      assert.equal(rpcs('board.micro.keep').at(-1).params.micro, 'm-acerello');
    """)


@requires_jsdom
def test_a_broken_micro_app_asks_jenny_to_fix_it() -> None:
    _run_micro("broken", """
      await app.openCard('acero', 'Acero');
      await tick(40);
      const drawer = $('home-board-drawer');
      assert.ok(drawer.querySelector('.home-drawer-alert'));
      assert.ok(drawer.querySelector('.home-drawer-app.is-broken'));
      const fix = [...drawer.querySelectorAll('.home-board-btn.is-primary')]
        .find((b) => /sistemare|fix/i.test(b.textContent));
      fix.click();
      await tick(40);
      assert.equal(app.view, 'chat');
      assert.match(document.getElementById('home-input').value, /KeyError: 'soil'/);
    """, error="Traceback...\nKeyError: 'soil'")


@requires_jsdom
def test_the_add_menu_offers_a_micro_app_and_still_links_an_app() -> None:
    _run("""
      await app.openCard('acero', 'Acero');
      await tick(40);
      document.querySelector('.home-drawer-add').click();
      await tick(10);
      const rows = [...document.querySelectorAll('.home-board-menu-row')];
      const micro = rows.find((r) => /Micro app/.test(r.textContent));
      assert.ok(micro, 'niente voce Micro app');
      assert.ok(!rows.some((r) => /^Mini app/.test(r.querySelector('.home-board-menu-name')?.textContent || '')));
      micro.click();
      await tick(10);
      const input = document.querySelector('.home-board-form .home-board-input');
      input.value = "l'umidità di Acerello";
      input.closest('form').dispatchEvent(new Event('submit', { cancelable: true }));
      await tick(30);
      assert.equal(app.view, 'chat');
      assert.match(document.getElementById('home-input').value, /micro app .*umidità di Acerello/);
    """)


@requires_jsdom
def test_a_notebook_cron_shows_in_its_drawer_and_pauses_there() -> None:
    board = json.loads(json.dumps(BOARD))
    card = json.loads(json.dumps(CARD))
    card["userSections"] = [{
        "id": "s-cron", "kind": "cron",
        "job": {"id": "ab12cd34", "name": "Meteo", "message": "guarda il meteo e dimmi se annaffiare",
                "mode": "monitor", "schedule": {"kind": "every", "every_ms": 86400000},
                "nextRunAtMs": 1791172800000, "lastRunAtMs": None, "lastStatus": None,
                "paused": False, "enabled": True},
        "result": {"at": "2026-10-05T09:00:00+02:00", "status": "ok",
                   "text": "Oggi piove: niente acqua.", "textAt": "2026-10-05T09:00:00+02:00"},
    }]
    head = (
        _SETUP.replace("__BOARD__", json.dumps(board)).replace("__CARD__", json.dumps(card))
        + "rpcAnswers['board.cron.pause'] = () => ({ ok: true });\n"
    )
    run_home(head + """
      await app.openCard('acero', 'Acero');
      await tick(40);
      const drawer = $('home-board-drawer');
      assert.match(drawer.textContent, /Meteo/);
      assert.match(drawer.textContent, /Oggi piove: niente acqua\\./);
      assert.match(drawer.textContent, /usa il modello|uses the model/);
      const pause = [...drawer.querySelectorAll('.home-drawer-link')]
        .find((b) => /^(Pausa|Pause)$/.test(b.textContent));
      pause.click();
      await tick(40);
      const call = rpcs('board.cron.pause').at(-1);
      assert.equal(call.params.job, 'ab12cd34');
      assert.equal(call.params.paused, true);
    """)


@requires_jsdom
def test_the_drawer_draws_the_four_other_shapes() -> None:
    base = {**_MICRO, "state": "ok", "extras": [], "uses": [], "net": []}
    sections = [
        {**base, "micro": "m-meteo", "name": "Meteo", "view": "chips", "extras": [
            {"field": "temp", "label": "Temperatura", "unit": "°", "type": "number", "value": 18},
            {"field": "pioggia", "label": "Pioggia", "unit": "%", "type": "number", "value": 70},
        ]},
        {**base, "micro": "m-parole", "name": "Romanzo", "view": "progress", "label": "Parole",
         "unit": "parole", "value": 41200, "min": 0, "max": 80000},
        {**base, "micro": "m-annunci", "name": "Annunci", "view": "list", "type": "rows",
         "value": 2, "columns": [{"field": "titolo", "label": "Titolo", "type": "text", "unit": ""},
                                 {"field": "prezzo", "label": "Prezzo", "type": "number",
                                  "unit": " €"}],
         "rows": [{"titolo": "Bilocale via Roma", "prezzo": 180000},
                  {"titolo": "Trilocale Pigneto", "prezzo": 260000}]},
        {**base, "micro": "m-etf", "name": "ETF", "view": "table", "type": "rows", "value": 1,
         "columns": [{"field": "nome", "label": "Nome", "type": "text", "unit": ""},
                     {"field": "prezzo", "label": "Prezzo", "type": "number", "unit": ""}],
         "rows": [{"nome": "MSCI World", "prezzo": 104.2}]},
    ]
    board = json.loads(json.dumps(BOARD))
    card = json.loads(json.dumps(CARD))
    card["userSections"] = [{"id": f"s-{v['micro']}", "kind": "micro", "micro": v["micro"],
                             "pending": False, "view": v} for v in sections]
    head = _SETUP.replace("__BOARD__", json.dumps(board)).replace("__CARD__", json.dumps(card))
    run_home(head + """
      await app.openCard('acero', 'Acero');
      await tick(40);
      const drawer = $('home-board-drawer');
      const tiles = [...drawer.querySelectorAll('.home-drawer-tile')];
      assert.equal(tiles.length, 2, 'chips: una piastrella per valore');
      assert.equal(tiles[0].querySelector('.home-drawer-tile-label').textContent, 'Temperatura');
      assert.equal(tiles[0].querySelector('.home-drawer-tile-value').textContent, '18°');
      assert.equal(tiles[1].querySelector('.home-drawer-tile-value').textContent, '70%');
      const bar = drawer.querySelector('.home-drawer-progress-fill');
      assert.equal(bar.style.width, '52%');
      const rows = [...drawer.querySelectorAll('.home-drawer-micro-row-title')].map((r) => r.textContent);
      assert.deepEqual(rows, ['Bilocale via Roma', 'Trilocale Pigneto']);
      const cells = [...drawer.querySelectorAll('.home-drawer-micro-table td')].map((c) => c.textContent);
      assert.equal(cells[0], 'MSCI World');
      assert.ok(drawer.querySelector('.home-drawer-micro-table td.is-num'));
    """)


@requires_jsdom
def test_the_centre_of_the_view_goes_with_an_accepted_card() -> None:
    _run("""
      const B = app.board;
      const box = $('home-board-canvas').getBoundingClientRect();
      // jsdom non ha layout: senza misure il centro e' quello dello schermo dell'origine.
      assert.deepEqual(B.viewCenter(), box.width ? B.viewCenter() : { x: 50, y: 50 });
      document.querySelector('.home-board-envelope').click();
      await tick(20);
      const yes = [...document.querySelectorAll('.home-board-proposal .home-board-btn.is-primary')][0];
      yes.click();
      await tick(40);
      const call = rpcs('board.suggestion.respond').at(-1);
      assert.equal(call.params.answer, 'yes');
      assert.ok(call.params.near && typeof call.params.near.x === 'number');
    """)
