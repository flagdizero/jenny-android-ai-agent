"""La bacheca in casa, con l'``index.html`` vero e i moduli veri (jsdom).

Si guarda il giro che conta: un quaderno si apre sulla bacheca, le schede
portano il loro segnale, il tocco su una polaroid apre il cassetto, una
correzione parte come ``board.fix`` e non scrive niente sul cassetto, e
«indietro» torna da una pagina al cassetto e dal cassetto alla bacheca.
"""

from __future__ import annotations

import json

from support import theme_tokens
from support.home_dom import UI, requires_jsdom, run_home
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
         "when": {"kind": "date", "date": "2026-10-10", "precision": "day"}},
    ],
    "postits": [{"card": "monstera", "pos": {"x": 62, "y": 35, "by": "jenny"}}],
    "postitRows": 3,
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
    "reminders": [{"id": "r-travaso", "card": "acero", "text": "Travaso nel vaso 20",
                   "when": {"kind": "date", "date": "2027-03-01", "precision": "month"}}],
    "userSections": [],
    "connections": [{"to": "monstera", "title": "Monstera", "label": "vasi dello stesso ordine",
                     "by": "jenny"}],
    "corrections": [],
    "skeleton": ["Vaso e travaso", "Acqua"],
}

_SETUP = """
import assert from 'node:assert/strict';
import { boot, tick, routes, sent, rpcAnswers, hooks, $ } from './boot.mjs';
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
// Uno schermo touch, come il telefono: d3-zoom ascolta le dita solo dove ci sono.
Object.defineProperty(navigator, 'maxTouchPoints', { value: 2, configurable: true });
const app = await boot();
// Un quaderno si apre sulla sua bacheca (05/10/2026).
await app.switchConversation('project:orto');
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
      // Le attese: il post-it della monstera, con la sua riga.
      assert.equal(document.querySelectorAll('.home-board-postit').length, 1);
      assert.equal(document.querySelectorAll('.home-board-postit-row').length, 1);
      // La busta col numero, e «dall'ultima volta»: una riga piu' la busta.
      assert.equal(document.querySelector('.home-board-envelope-count').textContent, '1');
      assert.equal($('home-board-since').hidden, false);
      assert.match($('home-board-since').textContent, /2/);
      // Aprire la bacheca e' «l'ultima volta».
      assert.equal(rpcs('board.seen').length, 1);
      // I comandi della bacheca in testa: la lente e l'elenco.
      assert.equal($('home-board-search-open').hidden, false);
      // L'interruttore Chat | Bacheca non c'e' piu': la chat la porta Jenny.
      assert.equal($('home-view-switch'), null);
      // I fili si disegnano dopo le schede: niente z-index, l'ordine nel DOM.
      const wall = $('home-board-wall');
      assert.ok([...wall.children].indexOf($('home-board-threads'))
        > [...wall.children].indexOf(polaroids[1]));
      // Il muro: le posizioni in percento dello schermo. Qui jsdom non ha
      // misure, quindi niente inquadratura: l'origine a grandezza naturale.
      assert.equal(polaroids[0].style.left, '5cqw');
      assert.equal(polaroids[0].style.top, '6cqh');
      const B = app.board;
      assert.deepEqual({ ...B.view }, { x: 0, y: 0, k: 1 });
      B._zoom.transform(B._sel, window.d3.zoomIdentity.translate(-120, 40).scale(1.5));
      assert.equal(wall.style.transform, 'translate(-120px, 40px) scale(1.5)');
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
      // La sezione e' chiusa a fisarmonica: «Correggi» sta dentro.
      assert.equal(document.querySelector('.home-drawer-link.is-fix'), null);
      document.querySelector('.home-drawer-fold').click();
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


_READER = {
    "slug": "acerello", "name": "Acerello", "label": "Umidità",
    "unit": "%", "field": "moisture", "type": "number", "verbs": [], "params": {},
    "value": 38, "at": "2026-10-05T12:00:00+00:00", "trend": [40, 39, 38],
    "dataState": "ok", "extras": [{"field": "online", "label": "Sensore", "unit": "",
                                   "type": "bool", "value": True}],
    "source": "hub WaterBot", "every": 30, "net": [], "uses": ["waterbot-imh"],
    "codeStatus": "ok", "lastRunAt": "", "nextRunAt": "", "lastError": "", "failures": 0,
    "view": "value", "hasScreen": False,
}


def _reader_section(view: dict) -> dict:
    return {"id": f"s-{view['slug']}", "kind": "app", "app": view["slug"], "params": {},
            "view": view}


@requires_jsdom
def test_the_auto_sign_is_drawn_and_is_not_a_signal() -> None:
    board = json.loads(json.dumps(BOARD))
    board["cards"][0]["auto"] = True
    head = _SETUP.replace("__BOARD__", json.dumps(board)).replace("__CARD__", json.dumps(CARD))
    run_home(head + """
      // Il segno ⟳ sulla polaroid: qualcosa gira da solo, ma non e' un segnale rosso.
      const polaroid = document.querySelector('.home-board-polaroid[data-id="acero"]');
      assert.ok(polaroid.querySelector('.home-board-auto'));
      assert.ok(!polaroid.querySelector('.home-board-sig-corner'));
    """)


@requires_jsdom
def test_a_broken_reader_keeps_its_last_value_in_grey() -> None:
    card = json.loads(json.dumps(CARD))
    card["userSections"] = [_reader_section({**_READER, "state": "broken",
                                             "lastError": "Traceback...\nKeyError: 'soil'"})]
    head = _SETUP.replace("__BOARD__", json.dumps(BOARD)).replace("__CARD__", json.dumps(card))
    run_home(head + """
      await app.openCard('acero', 'Acero');
      await tick(40);
      const drawer = $('home-board-drawer');
      assert.ok(drawer.querySelector('.home-drawer-app.is-broken'));
      assert.match(drawer.textContent, /38%/);
      assert.match(drawer.textContent, /waterbot-imh/);
    """)


@requires_jsdom
def test_the_add_menu_offers_a_micro_app_and_still_links_an_app() -> None:
    _run("""
      await app.openCard('acero', 'Acero');
      await tick(40);
      document.querySelector('.home-drawer-fab').click();
      await tick(10);
      const rows = [...document.querySelectorAll('.home-drawer-dial-row')];
      // Dal 06/10/2026 la voce si chiama «App»: una micro app e' un'app.
      const micro = rows.find((r) => r.querySelector('.home-drawer-dial-name')?.textContent === 'App');
      assert.ok(micro, 'niente voce App');
      assert.ok(!rows.some((r) => /^Mini app/.test(r.querySelector('.home-drawer-dial-name')?.textContent || '')));
      micro.click();
      await tick(10);
      const input = document.querySelector('.home-board-form .home-board-input');
      input.value = "l'umidità di Acerello";
      input.closest('form').dispatchEvent(new Event('submit', { cancelable: true }));
      await tick(30);
      assert.equal(app.chatOver, true);
      assert.match(document.getElementById('home-input').value,
        /an app that .*umidità di Acerello, to put on the board next to Acero/);
    """)


@requires_jsdom
def test_an_app_from_a_drawer_becomes_a_card_next_to_it() -> None:
    _run(_CARDS + """
      routes['/api/webui/apps'] = { apps: [
        { slug: 'etf', name: 'Portafoglio', broken: false, state: 'live', section: { params: {} } },
      ] };
      rpcAnswers['board.card.add'] = () => ({ ok: true, card: { id: 'portafoglio' } });
      await app.openCard('acero', 'Acero');
      await tick(40);
      document.querySelector('.home-drawer-fab').click();
      await tick(10);
      [...document.querySelectorAll('.home-drawer-dial-row')]
        .find((r) => r.querySelector('.home-drawer-dial-name')?.textContent === 'App').click();
      await tick(10);
      [...document.querySelectorAll('.home-board-menu-row')]
        .find((r) => /next to it/.test(r.textContent)).click();
      await tick(40);
      [...document.querySelectorAll('.home-board-menu-row')]
        .find((r) => /Portafoglio/.test(r.textContent)).click();
      await tick(60);
      const call = rpcs('board.card.add').at(-1);
      assert.deepEqual(call.params.app, { slug: 'etf', params: {} });
      assert.equal(call.params.link, 'acero');
      assert.equal(rpcs('board.section.add').length, 0, 'mai una sezione nel cassetto');
      assert.equal(app.view, 'board', 'si torna alla bacheca a vederla');
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
    base = {**_READER, "state": "ok", "extras": [], "uses": [], "net": []}
    sections = [
        {**base, "slug": "meteo", "name": "Meteo", "view": "chips", "extras": [
            {"field": "temp", "label": "Temperatura", "unit": "°", "type": "number", "value": 18},
            {"field": "pioggia", "label": "Pioggia", "unit": "%", "type": "number", "value": 70},
        ]},
        {**base, "slug": "parole", "name": "Romanzo", "view": "progress", "label": "Parole",
         "unit": "parole", "value": 41200, "min": 0, "max": 80000},
        {**base, "slug": "annunci", "name": "Annunci", "view": "list", "type": "rows",
         "value": 2, "columns": [{"field": "titolo", "label": "Titolo", "type": "text", "unit": ""},
                                 {"field": "prezzo", "label": "Prezzo", "type": "number",
                                  "unit": " €"}],
         "rows": [{"titolo": "Bilocale via Roma", "prezzo": 180000},
                  {"titolo": "Trilocale Pigneto", "prezzo": 260000}]},
        {**base, "slug": "etf", "name": "ETF", "view": "table", "type": "rows", "value": 1,
         "columns": [{"field": "nome", "label": "Nome", "type": "text", "unit": ""},
                     {"field": "prezzo", "label": "Prezzo", "type": "number", "unit": ""}],
         "rows": [{"nome": "MSCI World", "prezzo": 104.2}]},
    ]
    board = json.loads(json.dumps(BOARD))
    card = json.loads(json.dumps(CARD))
    card["userSections"] = [_reader_section(v) for v in sections]
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


# La tela del muro con le misure del quadrato, che jsdom non ha: 400 x 400, e il
# muro rifatto su quelle. Gli oggetti di ``BOARD`` stanno nello schermo
# dell'origine, quindi il muro va da -400 a 800 per lato (uno schermo d'aria).
_WALL = """
const B = app.board;
const C = $('home-board-canvas');
for (const [k, v] of Object.entries({ clientWidth: 400, clientHeight: 400 })) {
  Object.defineProperty(C, k, { value: v, configurable: true });
}
C.getBoundingClientRect = () => ({ left: 0, top: 0, right: 400, bottom: 400,
  width: 400, height: 400, x: 0, y: 0 });
B.render();
const at = () => ({ x: B.view.x, y: B.view.y, k: B.view.k });
const mouse = (target, type, x, y, extra = {}) => target.dispatchEvent(new window.MouseEvent(type, {
  bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, ...extra }));
const finger = (id, x, y) => ({ identifier: id, clientX: x, clientY: y });
const touch = (target, type, touches, changed) => {
  const e = new window.Event(type, { bubbles: true, cancelable: true });
  Object.assign(e, { touches, changedTouches: changed });
  target.dispatchEvent(e);
};
const pointer = (target, type, x, y) => {
  const e = new window.Event(type, { bubbles: true });
  Object.assign(e, { clientX: x, clientY: y, button: 0, pointerId: 1, isPrimary: true });
  target.dispatchEvent(e);
};
const card = (id) => document.querySelector(`.home-board-polaroid[data-id="${id}"]`);
"""


def _run_wall(script: str) -> None:
    _run(_WALL + script)


@requires_jsdom
def test_the_background_moves_the_wall_up_to_its_edge() -> None:
    _run_wall("""
      mouse(C, 'mousedown', 100, 100);
      mouse(window, 'mousemove', 150, 80);
      mouse(window, 'mouseup', 150, 80);
      assert.deepEqual(at(), { x: 50, y: -20, k: 1 });
      // Oltre lo schermo d'aria il muro non va: a sinistra si ferma a +400.
      mouse(C, 'mousedown', 0, 0);
      mouse(window, 'mousemove', 2000, 0);
      mouse(window, 'mouseup', 2000, 0);
      assert.equal(at().x, 400);
      // Il mouse su una scheda e' della scheda: il muro non si muove.
      const before = at();
      mouse(card('acero'), 'mousedown', 30, 40);
      mouse(window, 'mousemove', 90, 90);
      mouse(window, 'mouseup', 90, 90);
      assert.deepEqual(at(), before);
    """)


@requires_jsdom
def test_a_double_tap_on_the_background_frames_everything() -> None:
    _run_wall(_SIZED + """
      B._zoom.transform(B._sel, window.d3.zoomIdentity.translate(-200, 100).scale(1.5));
      mouse(card('acero'), 'dblclick', 30, 40);
      assert.deepEqual(at(), { x: -200, y: 100, k: 1.5 });
      mouse(C, 'dblclick', 200, 200);
      assert.deepEqual(at(), FITTED);
    """)


@requires_jsdom
def test_the_wheel_moves_the_wall_and_ctrl_wheel_zooms_it() -> None:
    _run_wall("""
      const wheel = (extra) => C.dispatchEvent(new window.WheelEvent('wheel', {
        bubbles: true, cancelable: true, clientX: 200, clientY: 200, ...extra }));
      wheel({ deltaX: 30, deltaY: 10 });
      assert.deepEqual(at(), { x: -30, y: -10, k: 1 });
      // Ctrl+rotella (e la pinza del trackpad) ingrandisce, fino a 2,2 e non oltre.
      for (let i = 0; i < 5; i++) wheel({ deltaY: -100, ctrlKey: true });
      assert.equal(at().k, 2.2);
      for (let i = 0; i < 20; i++) wheel({ deltaY: 100, ctrlKey: true });
      assert.equal(at().k, 0.3);
    """)


@requires_jsdom
def test_a_second_finger_turns_a_card_drag_into_a_pinch() -> None:
    _run_wall("""
      const el = card('acero');
      pointer(el, 'pointerdown', 30, 40);
      pointer(el, 'pointermove', 60, 70);
      assert.ok(el.classList.contains('is-dragging'));
      // Il secondo dito sul fondo: d3 prende tutte e due le dita, la scheda torna dov'era.
      const one = finger(1, 60, 70);
      touch(C, 'touchstart', [one, finger(2, 200, 200)], [finger(2, 200, 200)]);
      assert.ok(!el.classList.contains('is-dragging'));
      assert.equal(el.style.left, '5cqw');
      assert.equal(el.style.top, '6cqh');
      // Le dita si allargano: il muro si ingrandisce, e la scheda non segue piu' il dito.
      touch(C, 'touchmove', [one, finger(2, 300, 300)], [finger(2, 300, 300)]);
      assert.ok(at().k > 1.4, `k=${at().k}`);
      pointer(el, 'pointermove', 90, 100);
      assert.equal(el.style.left, '5cqw');
      touch(C, 'touchend', [], [one, finger(2, 300, 300)]);
      pointer(el, 'pointerup', 90, 100);
      // Nessuno spostamento al server, e il tocco non apre il cassetto.
      el.click();
      await tick(20);
      assert.equal(rpcs('board.move').length, 0);
      assert.equal(app.view, 'board');
    """)


@requires_jsdom
def test_a_second_finger_on_a_card_is_a_pinch_not_a_drag() -> None:
    _run_wall("""
      // Il primo dito sul fondo sposta il muro; il secondo cade su una scheda.
      const one = finger(1, 300, 300);
      touch(C, 'touchstart', [one], [one]);
      const el = card('monstera');
      const second = new window.Event('pointerdown', { bubbles: true });
      Object.assign(second, { clientX: 200, clientY: 30, button: 0, pointerId: 2, isPrimary: false });
      el.dispatchEvent(second);
      touch(el, 'touchstart', [one, finger(2, 200, 30)], [finger(2, 200, 30)]);
      // Non la trascina: le due dita sono una pinza.
      pointer(el, 'pointermove', 150, 10);
      assert.ok(!el.classList.contains('is-dragging'));
      touch(el, 'touchmove', [one, finger(2, 100, 10)], [finger(2, 100, 10)]);
      // L'ingrandimento e' quanto si sono allargate le dita.
      const grew = Math.hypot(200, 290) / Math.hypot(100, 270);
      assert.ok(Math.abs(at().k - grew) < 1e-9, `k=${at().k}`);
      touch(C, 'touchend', [], [one, finger(2, 100, 10)]);
      assert.equal(rpcs('board.move').length, 0);
    """)


@requires_jsdom
def test_after_a_pinch_the_next_tap_on_the_card_opens_its_drawer() -> None:
    _run_wall("""
      const el = card('acero');
      pointer(el, 'pointerdown', 30, 40);
      const one = finger(1, 30, 40);
      touch(C, 'touchstart', [one, finger(2, 200, 200)], [finger(2, 200, 200)]);
      touch(C, 'touchend', [], [one, finger(2, 200, 200)]);
      pointer(el, 'pointerup', 30, 40);
      // La pinza non ha fatto partire nessun click: il tocco dopo e' un tocco.
      pointer(el, 'pointerdown', 30, 40);
      pointer(el, 'pointerup', 30, 40);
      el.click();
      await tick(30);
      assert.equal(app.view, 'drawer');
    """)


@requires_jsdom
def test_a_redraw_under_the_finger_lets_go_of_the_card() -> None:
    _run_wall("""
      pointer(card('acero'), 'pointerdown', 30, 40);
      pointer(card('acero'), 'pointermove', 60, 70);
      // La bacheca si ridisegna (un aggiornamento dal server) col dito ancora giu':
      // quella scheda non c'e' piu', e la prossima si trascina.
      B.render();
      assert.equal(B._itemDrag, null);
      const el = card('monstera');
      pointer(el, 'pointerdown', 200, 30);
      pointer(el, 'pointermove', 230, 60);
      assert.ok(el.classList.contains('is-dragging'));
    """)


@requires_jsdom
def test_a_card_carried_to_the_edge_scrolls_the_wall_and_stays_in_hand() -> None:
    _run_wall("""
      const el = card('acero');
      pointer(el, 'pointerdown', 30, 40);
      pointer(el, 'pointermove', 200, 200);
      assert.equal(at().x, 0);
      // Vicino al bordo destro il muro scorre sotto la scheda, che resta in mano.
      pointer(el, 'pointermove', 390, 200);
      pointer(el, 'pointermove', 392, 200);
      assert.equal(at().x, -28);
      assert.ok(el.classList.contains('is-dragging'));
      pointer(el, 'pointerup', 392, 200);
      await tick(20);
      const move = rpcs('board.move').at(-1);
      assert.ok(move, 'nessuno spostamento al server');
      // Il dito e' andato di 362 px e il muro di 28: la scheda di 390 px, cioe' 97,5%.
      assert.equal(move.params.x, 5 + 97.5);
    """)


# Le schede con una misura, che jsdom non da': quella che avrebbero nel
# quadrato da 400 (polaroid 76 x 100), alla vista del momento. Il post-it qui e'
# piccolo, 56 x 60, quanto quello di prima: i conti restano quelli.
# Con ``BOARD`` l'ingombro va da (20, 16) a (304, 200): sta nella finestra a
# grandezza naturale, quindi l'inquadratura lo centra senza ingrandirlo.
_SIZED = """
const realRect = window.HTMLElement.prototype.getBoundingClientRect;
window.HTMLElement.prototype.getBoundingClientRect = function () {
  if (!this.matches('.home-board-polaroid, .home-board-postit')) return realRect.call(this);
  const v = B.view;
  const note = this.classList.contains('home-board-postit');
  const x = parseFloat(this.style.left) * 4, y = parseFloat(this.style.top) * 4;
  const w = note ? 56 : 76, h = note ? 60 : 100;
  return { left: v.x + x * v.k, top: v.y + y * v.k, right: v.x + (x + w) * v.k,
    bottom: v.y + (y + h) * v.k, width: w * v.k, height: h * v.k };
};
const FITTED = { x: 38, y: 92, k: 1 };
"""


# Una vista qualunque, diversa dall'inquadratura, dentro i bordi del muro.
_MOVED = """
const look = () => B._zoom.transform(B._sel, window.d3.zoomIdentity.translate(-150, 60).scale(1.2));
const LOOKED = { x: -150, y: 60, k: 1.2 };
"""


@requires_jsdom
def test_entering_a_board_frames_everything() -> None:
    _run_wall(_SIZED + _MOVED + """
      look();
      await B.load('altro');
      assert.deepEqual(at(), FITTED, 'un altro quaderno non si apre inquadrato');
      look();
      await B.load('altro', { fit: true });
      assert.deepEqual(at(), FITTED, 'rientrare da fuori torna dove lo si era lasciato');
      assert.equal($('home-board-wall').style.visibility, '');
    """)


@requires_jsdom
def test_opening_from_the_notebooks_frames_everything() -> None:
    _run_wall(_SIZED + _MOVED + """
      look();
      await app.openNotebook('orto');
      assert.equal(app.view, 'board');
      assert.deepEqual(at(), FITTED);
    """)


@requires_jsdom
def test_back_from_a_drawer_finds_the_board_where_it_was() -> None:
    _run_wall(_SIZED + _MOVED + """
      look();
      card('acero').click();
      await tick(40);
      assert.equal(app.view, 'drawer');
      app.handleHardwareBack();
      await tick(40);
      assert.equal(app.view, 'board');
      // La bacheca si e' ricaricata dal server, e non ha rifatto l'inquadratura.
      assert.equal(document.querySelectorAll('.home-board-polaroid').length, 2);
      assert.deepEqual(at(), LOOKED);
    """)


@requires_jsdom
def test_back_from_a_page_of_the_search_finds_the_board_where_it_was() -> None:
    _run_wall(_SIZED + _MOVED + """
      look();
      await app.openPage('travaso-acero.md', 'travaso acero');
      await tick(20);
      assert.equal(app.view, 'reader');
      app.handleHardwareBack();
      await tick(40);
      assert.equal(app.view, 'board');
      assert.deepEqual(at(), LOOKED);
    """)


@requires_jsdom
def test_the_path_root_from_a_drawer_finds_the_board_where_it_was() -> None:
    _run_wall(_SIZED + _MOVED + """
      look();
      card('acero').click();
      await tick(40);
      app.goToPathRoot();
      await tick(40);
      assert.equal(app.view, 'board');
      assert.deepEqual(at(), LOOKED);
    """)


@requires_jsdom
def test_a_big_board_is_framed_smaller_and_centred() -> None:
    board = json.loads(json.dumps(BOARD))
    board["cards"][1]["pos"]["x"] = 150  # la monstera da 600 a 676 px
    _run_wall(_SIZED + """
      routes['/api/board'] = __BIG__;
      await B.load('altro');
      // 656 px di muro in 368 di finestra: 0,56, e il centro dell'ingombro
      // (348, 108) al centro della finestra.
      const k = 368 / 656;
      assert.ok(Math.abs(at().k - k) < 1e-9, `ingrandimento ${at().k}`);
      assert.ok(Math.abs(at().x + 348 * k - 200) < 1e-9);
      assert.ok(Math.abs(at().y + 108 * k - 200) < 1e-9);
    """.replace("__BIG__", json.dumps(board)))


@requires_jsdom
def test_the_same_board_is_framed_before_the_server_answers() -> None:
    """Rientrando da fuori nello stesso quaderno il muro e' gia' a schermo: si
    inquadra subito, non quando arriva la risposta, che lo farebbe saltare."""
    _run_wall(_SIZED + """
      B._zoom.transform(B._sel, window.d3.zoomIdentity.translate(-150, 60).scale(1.2));
      let release;
      const held = new Promise((r) => { release = r; });
      hooks.fetch = async (u) => { if (u.pathname === '/api/board') await held; };
      const loading = B.load(B.notebook, { fit: true });
      assert.deepEqual(at(), FITTED, 'il muro si vede dove lo si era lasciato');
      release();
      await loading;
      assert.deepEqual(at(), FITTED);
    """)


@requires_jsdom
def test_a_finger_on_the_wall_while_it_loads_is_not_overruled() -> None:
    _run_wall(_SIZED + """
      let release;
      const held = new Promise((r) => { release = r; });
      hooks.fetch = async (u) => { if (u.pathname === '/api/board') await held; };
      const loading = B.load(B.notebook, { fit: true });
      mouse(C, 'mousedown', 100, 100);
      mouse(window, 'mousemove', 150, 80);
      mouse(window, 'mouseup', 150, 80);
      const moved = at();
      assert.notDeepEqual(moved, FITTED);
      release();
      await loading;
      assert.deepEqual(at(), moved, 'la risposta ha rifatto l\u2019inquadratura sotto il dito');
    """)


@requires_jsdom
def test_another_notebook_does_not_show_the_old_wall_while_it_loads() -> None:
    _run("""
      assert.equal(document.querySelectorAll('.home-board-polaroid').length, 2);
      // La bacheca dell'altro quaderno tarda: finche' non arriva, il muro
      // dell'orto non deve restare a schermo come se fosse il suo.
      let release;
      const held = new Promise((r) => { release = r; });
      hooks.fetch = async (u) => {
        if (u.pathname === '/api/board' && u.searchParams.get('wiki') === 'altro') await held;
      };
      const loading = app.board.load('altro');
      await tick(5);
      assert.equal(document.querySelectorAll('.home-board-polaroid').length, 0);
      assert.equal(document.querySelectorAll('.home-board-postit').length, 0);
      assert.equal(document.querySelector('.home-board-envelope'), null);
      assert.equal($('home-board-since').hidden, true);
      assert.equal($('home-board-empty').hidden, true);
      release();
      await loading;
      assert.equal(document.querySelectorAll('.home-board-polaroid').length, 2);
      // Ricaricare lo **stesso** quaderno non lo spegne: resta finche' arriva.
      hooks.fetch = async (u) => { if (u.pathname === '/api/board') await held; };
      const again = app.board.load('altro');
      await tick(5);
      assert.equal(document.querySelectorAll('.home-board-polaroid').length, 2);
      await again;
    """)


# ── Le schede dell'utente: +, pressione lunga, barra, Togli con Annulla ──────

_CARDS = """
rpcAnswers['board.card.add'] = (p) => ({ ok: true, card: { id: 'fico', title: p.title } });
for (const m of ['board.card.rename', 'board.card.remove', 'board.card.restore']) {
  rpcAnswers[m] = () => ({ ok: true });
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const press = (el, x, y, type = 'pointerdown') => {
  const e = new window.Event(type, { bubbles: true });
  Object.assign(e, { clientX: x, clientY: y, button: 0, pointerId: 1, isPrimary: true });
  el.dispatchEvent(e);
};
const sheetButton = (text) => [...$('home-board-sheet-body').querySelectorAll('button')]
  .find((b) => b.textContent === text);
"""


@requires_jsdom
def test_the_plus_makes_a_card_in_the_middle_of_the_view() -> None:
    _run(_CARDS + """
      const plus = $('home-board-add');
      assert.equal(plus.hidden, false);
      assert.equal(plus.getAttribute('aria-label'), 'New card');
      plus.click();
      await tick(10);
      assert.ok($('home-board-sheet').open);
      const input = $('home-board-sheet-body').querySelector('input');
      const create = sheetButton('Create');
      // Senza nome non si crea niente.
      assert.equal(create.disabled, true);
      input.value = 'Fico';
      input.dispatchEvent(new window.Event('input'));
      create.click();
      await tick(40);
      const call = rpcs('board.card.add').at(-1);
      assert.equal(call.params.title, 'Fico');
      assert.deepEqual(call.params.near, app.board.viewCenter());
      assert.equal($('home-board-sheet').open, false);
      // Fuori dalla bacheca il + non c'e'.
      await app.openCard('acero', 'Acero');
      await tick(40);
      assert.equal($('home-board-add').hidden, true);
    """)


@requires_jsdom
def test_holding_the_empty_wall_makes_a_card_under_the_finger() -> None:
    _run(_CARDS + """
      const canvas = $('home-board-canvas');
      canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 400,
        right: 400, bottom: 400 });
      // Un dito che si muove e' il muro che scorre, non una scheda nuova.
      press(canvas, 200, 100);
      press(canvas, 240, 100, 'pointermove');
      await wait(520);
      assert.equal($('home-board-sheet').open, false);
      press(canvas, 200, 100, 'pointerup');
      // Fermo per mezzo secondo: il foglio, e la scheda nasce li'.
      press(canvas, 200, 100);
      await wait(520);
      assert.ok($('home-board-sheet').open, 'la pressione lunga sul muro non apre niente');
      const input = $('home-board-sheet-body').querySelector('input');
      input.value = 'Fico';
      input.dispatchEvent(new window.Event('input'));
      sheetButton('Create').click();
      await tick(40);
      assert.deepEqual(rpcs('board.card.add').at(-1).params.near, { x: 50, y: 25 });
    """)


@requires_jsdom
def test_holding_a_card_raises_its_bar_and_back_closes_it() -> None:
    _run(_CARDS + """
      // Una tela con le misure, o i fili non si disegnano e niente si solleva.
      const rect = () => ({ left: 0, top: 0, width: 400, height: 400, right: 400, bottom: 400 });
      $('home-board-canvas').getBoundingClientRect = rect;
      $('home-board-wall').getBoundingClientRect = rect;
      const el = document.querySelector('.home-board-polaroid[data-id="acero"]');
      press(el, 10, 10);
      await wait(500);
      press(el, 10, 10, 'pointerup');
      const bar = document.querySelector('.home-board-cardbar');
      assert.ok(bar && !bar.hidden, 'la barra della scheda non sale');
      assert.equal(bar.parentElement.id, 'home-board-canvas');
      assert.ok(el.classList.contains('is-lifted'));
      assert.deepEqual([...bar.querySelectorAll('button')].map((b) => b.textContent),
        ['Rename', 'Remove from the board', '']);
      // Rinomina: il foglio col nome di adesso.
      bar.querySelector('button').click();
      await tick(10);
      const input = $('home-board-sheet-body').querySelector('input');
      assert.equal(input.value, 'Acero');
      input.value = 'Acerello';
      input.dispatchEvent(new window.Event('input'));
      sheetButton('Save').click();
      await tick(40);
      assert.deepEqual(rpcs('board.card.rename').at(-1).params, { wiki: 'orto', id: 'acero',
        title: 'Acerello' });
      // Indietro chiude la barra prima di lasciare la bacheca.
      assert.equal(app.board.focus, 'acero');
      app.handleHardwareBack();
      assert.equal(app.board.focus, null);
      assert.equal(bar.hidden, true);
      assert.equal(app.view, 'board');
    """)


@requires_jsdom
def test_remove_from_the_board_has_an_undo_instead_of_a_question() -> None:
    _run(_CARDS + """
      await app.board.removeCard('acero', 'Acero');
      await tick(20);
      assert.equal(rpcs('board.card.remove').at(-1).params.id, 'acero');
      assert.equal(document.querySelector('dialog[open]'), null, 'nessuna domanda prima');
      const toast = document.querySelector('.mobile-toast.has-action');
      assert.ok(toast, 'manca il toast con Annulla');
      assert.match(toast.textContent, /Acero/);
      toast.querySelector('.mobile-toast-action').click();
      await tick(40);
      assert.equal(rpcs('board.card.restore').at(-1).params.id, 'acero');
      // Un secondo tocco non ripristina due volte.
      toast.querySelector('.mobile-toast-action').click();
      await tick(10);
      assert.equal(rpcs('board.card.restore').length, 1);
    """)


@requires_jsdom
def test_the_drawer_offers_rename_and_remove_at_the_bottom() -> None:
    _run(_CARDS + """
      await app.openCard('acero', 'Acero');
      await tick(40);
      const own = document.querySelector('.home-drawer-own');
      assert.ok(own);
      const [rename, remove] = own.querySelectorAll('button');
      assert.equal(rename.textContent, 'Rename');
      remove.click();
      await tick(60);
      assert.equal(app.view, 'board');
      assert.equal(rpcs('board.card.remove').at(-1).params.id, 'acero');
    """)


# ── Le app sulla bacheca: la polaroid che e' un'app, il suo valore, il tocco ─

_PF_SLIP = {
    "size": "M", "name": "Portafoglio", "icon": "ti-chart-pie", "hasScreen": True,
    "state": "stale", "at": "2026-10-01T12:00:00+00:00", "meta": None, "recordId": "t",
    "blocks": [
        {"kind": "value", "rows": 2, "value": 12430, "unit": "€", "label": "",
         "delta": 14.9, "deltaUnit": "%", "trend": [11980, 12150, 12430]},
        {"kind": "bars", "rows": 2, "unit": "€", "more": 1, "items": [
            {"label": "VWCE", "value": 5827, "weight": 46.9, "target": 50},
            {"label": "SWDA", "value": 4074, "weight": 32.8, "target": 30}]}],
}
_WB_SLIP = {
    "size": "S", "name": "WaterBot", "icon": "ti-droplet", "hasScreen": False,
    "state": "ok", "at": "2026-10-06T12:00:00+00:00", "meta": None, "recordId": "1",
    "blocks": [{"kind": "value", "rows": 2, "value": 38, "unit": "%", "label": "Moisture",
                "trend": [41, 40, 38]}],
}
_SPESA_SLIP = {
    "size": "M", "name": "Spesa", "icon": "ti-shopping-cart", "hasScreen": True, "state": "ok",
    "at": "2026-10-06T09:00:00+00:00", "meta": {"value": 3, "label": "da comprare"},
    "recordId": "i1",
    "blocks": [
        {"kind": "checklist", "rows": 3, "more": 0, "toggle": "set_done", "doneField": "done",
         "left": 3, "items": [{"id": "i1", "text": "Latte", "done": False},
                              {"id": "i2", "text": "Uova", "done": False},
                              {"id": "i3", "text": "Caffè", "done": True}]},
        {"kind": "buttons", "rows": 1, "actions": [
            {"action": "clear", "label": "Svuota", "params": {}}]}],
}

_COVER_BOARD = {
    **BOARD,
    "cards": [
        BOARD["cards"][0],
        {"id": "portafoglio", "title": "Portafoglio", "glyph": "", "pages": 0, "signal": None,
         "pos": {"x": 30, "y": 40, "by": "user"}, "pinned": False, "box": [40, 39],
         "app": {"slug": "etf", "params": {}, "hasScreen": True, "state": "live",
                 "icon": "ti-chart-line"}, "slip": _PF_SLIP},
        {"id": "waterbot", "title": "WaterBot", "glyph": "", "pages": 0, "signal": None,
         "pos": {"x": 60, "y": 40, "by": "user"}, "pinned": False, "box": [40, 29],
         "app": {"slug": "waterbot", "params": {"sensor": "acerello"}, "hasScreen": False,
                 "state": "live", "icon": "ti-droplet"}, "slip": _WB_SLIP},
        {"id": "orto-app", "title": "Orto", "glyph": "", "pages": 0, "signal": None,
         "pos": {"x": 60, "y": 70, "by": "user"}, "pinned": False,
         "app": {"slug": "orto", "params": {}, "hasScreen": True, "state": "live",
                 "icon": "ti-plant"}},
        {"id": "spesa", "title": "Spesa", "glyph": "", "pages": 0, "signal": None,
         "pos": {"x": 10, "y": 80, "by": "user"}, "pinned": False, "box": [40, 39],
         "app": {"slug": "spesa", "params": {}, "hasScreen": True, "state": "live",
                 "icon": "ti-shopping-cart"}, "slip": _SPESA_SLIP},
    ],
}


def _run_covers(script: str) -> None:
    head = (
        _SETUP.replace("__BOARD__", json.dumps(_COVER_BOARD))
        .replace("__CARD__", json.dumps(CARD))
    )
    run_home(head + _CARDS + script)


@requires_jsdom
def test_an_app_with_data_is_a_strip_and_a_thing_keeps_its_photo() -> None:
    _run_covers("""
      const card = (id) => document.querySelector(`.home-board-card[data-id="${id}"]`);
      // La scheda-soggetto tiene la sua foto: la foglia, nessuna striscia.
      assert.ok(card('acero').classList.contains('home-board-polaroid'));
      assert.ok(card('acero').querySelector('.home-board-photo i.ti-leaf'));
      // Un'app con dati: una striscia della sua taglia, coi suoi blocchi.
      const water = card('waterbot');
      assert.ok(water.classList.contains('home-board-slip') && water.classList.contains('is-S'));
      assert.equal(water.querySelector('.home-board-slip-name').textContent, 'WaterBot');
      assert.match(water.querySelector('.is-value .home-board-block-big').textContent, /^38%$/);
      assert.ok(water.querySelector('.is-value svg polyline'), "manca l'andamento");
      // Il dato in alto: l'app non lo dichiara, quindi l'ora della lettura.
      assert.match(water.querySelector('.home-board-slip-meta').textContent, /^at /);
      assert.match(water.getAttribute('aria-label'), /^WaterBot, at /);
      // Ferma: grigia, e l'eta' in alto; le barre col loro peso, e quante altre.
      const pf = card('portafoglio');
      assert.ok(pf.classList.contains('is-stale') && pf.classList.contains('is-M'));
      assert.match(pf.querySelector('.home-board-slip-meta').textContent, /^stopped · .* ago$/);
      assert.match(pf.querySelector('.home-board-block-delta').textContent, /^\\+14\\.9 %$/);
      const rows = [...pf.querySelectorAll('.is-bars .home-board-block-row')];
      assert.deepEqual(rows.map((r) => (r.children.length ? [...r.children].map((c) => c.textContent)
        .filter(Boolean).join(' ') : r.textContent)), ['VWCE 5,827 €', 'and 2 more']);
      // Un'app senza dati: una polaroid con la sua icona.
      assert.ok(card('orto-app').querySelector('.home-board-photo i.ti-plant'));
      // Il dato in alto dichiarato dall'app.
      assert.equal(card('spesa').querySelector('.home-board-slip-meta').textContent, '3 da comprare');
    """)


@requires_jsdom
def test_an_offline_sensor_has_a_red_pill_even_with_fresh_data() -> None:
    """Sul Titan (07/10/2026): «offline · at 01:58 PM» con la pillola verde,
    perche' il colore seguiva solo lo stato dei dati e l'offline cambiava la parola."""
    run_home("""
      import assert from 'node:assert/strict';
      import { boot } from './boot.mjs';
      await boot();
      const { renderBlock } = await import(__UI__ + '/assets/home-board-blocks.js');
      const pill = (b) => renderBlock({ kind: 'status', rows: 1, at: '', ...b })
        .querySelector('.home-board-block-pill');
      const off = pill({ state: 'ok', online: false });
      assert.ok(off.classList.contains('is-alarm'), off.className);
      assert.match(off.textContent, /^offline/);
      assert.ok(pill({ state: 'ok', online: true }).classList.contains('is-ok'));
      // Senza un campo online la pillola resta quella dei dati.
      assert.ok(pill({ state: 'ok' }).classList.contains('is-ok'));
      assert.ok(pill({ state: 'stale', online: true }).classList.contains('is-warn'));
    """.replace("__UI__", json.dumps(str(UI))))


@requires_jsdom
def test_the_drawer_of_an_app_card_opens_on_its_whole_strip() -> None:
    card = json.loads(json.dumps(CARD))
    card.update(id="spesa", title="Spesa")
    card["userSections"] = [{"id": "@card", "kind": "app", "app": "spesa", "params": {},
                             "own": True, "view": {"state": "ok", "name": "Spesa",
                                                   "hasScreen": False},
                             "slip": _SPESA_SLIP}]
    head = (_SETUP.replace("__BOARD__", json.dumps(_COVER_BOARD))
            .replace("__CARD__", json.dumps(card)))
    run_home(head + _CARDS + """
      const { calls } = await import('./boot.mjs');
      routes['/api/apps/spesa/actions/set_done'] = { ok: true };
      await app.openCard('spesa', 'Spesa');
      await tick(40);
      const drawer = $('home-board-drawer');
      const slip = drawer.querySelector('.home-drawer-slip');
      assert.ok(slip, 'manca la striscia nel cassetto');
      assert.deepEqual([...slip.querySelectorAll('.is-checklist .home-board-block-cell')]
        .map((c) => c.textContent), ['Latte', 'Uova', 'Caffè']);
      // L'app della scheda non si toglie dal suo cassetto, ne' si sposta.
      const own = slip.closest('.home-drawer-section');
      assert.ok(![...own.querySelectorAll('button')].some((b) => b.textContent === 'Remove'));
      assert.equal(own.querySelector('.is-move'), null);
      // Nel cassetto i controlli sono sempre svegli.
      slip.querySelector('.is-checklist input').click();
      await tick(30);
      assert.ok(calls.some((c) => c.startsWith('/api/apps/spesa/actions/set_done')));
    """)


@requires_jsdom
def test_strip_controls_work_only_up_close() -> None:
    _run_covers("""
      const { calls } = await import('./boot.mjs');
      routes['/api/apps/spesa/actions/set_done'] = { ok: true };
      routes['/api/apps/spesa/actions/clear'] = { ok: true };
      const opened = [];
      app.appsSource().awaitJennyApps = async () => [{ slug: 'spesa' }];
      app.appsActions().openApp = (slug) => opened.push(slug);
      const spesa = () => document.querySelector('.home-board-card[data-id="spesa"]');
      const box = () => spesa().querySelector('.is-checklist input');
      const actions = () => calls.filter((c) => c.startsWith('/api/apps/spesa/actions/'));
      // Da lontano: la spunta non agisce, e il tocco apre l'app.
      $('home-board-wall').classList.remove('is-near');
      box().click();
      await tick(20);
      assert.equal(actions().length, 0);
      assert.equal(box().checked, false);
      assert.deepEqual(opened, ['spesa']);
      // Da vicino: spunta Latte (l'azione dichiarata, coi parametri giusti), e non apre.
      $('home-board-wall').classList.add('is-near');
      box().click();
      await tick(20);
      const call = decodeURIComponent(actions().at(-1));
      assert.match(call, /set_done\\?params=/);
      assert.deepEqual(JSON.parse(call.split('params=')[1]), { id: 'i1', done: true });
      spesa().querySelector('.home-board-block-btn').click();
      await tick(20);
      assert.match(actions().at(-1), /\\/clear\\?params=%7B%7D$/);
      assert.deepEqual(opened, ['spesa']);
    """)


@requires_jsdom
def test_a_tap_on_an_app_card_opens_the_app() -> None:
    _run_covers("""
      const opened = [];
      app.appsSource().awaitJennyApps = async () => [{ slug: 'etf' }, { slug: 'orto' }];
      app.appsActions().openApp = (slug) => opened.push(slug);
      const card = (id) => document.querySelector(`.home-board-card[data-id="${id}"]`);
      // Con lo schermo: si apre l'app, non il cassetto.
      card('portafoglio').click();
      await tick(20);
      assert.deepEqual(opened, ['etf']);
      assert.equal(app.view, 'board');
      // Ancora senza valore, ma con lo schermo: si apre l'app.
      card('orto-app').click();
      await tick(20);
      assert.deepEqual(opened, ['etf', 'orto']);
      // Senza schermo: il cassetto, col riassunto intero.
      card('waterbot').click();
      await tick(40);
      assert.deepEqual(opened, ['etf', 'orto']);
      assert.equal(app.view, 'drawer');
    """)


@requires_jsdom
def test_an_app_card_follows_its_data_without_reopening_jenny() -> None:
    """Sul telefono la polaroid restava al valore di prima finche' non si
    riapriva Jenny: la bacheca non ascoltava, e chiudere l'app non la rileggeva."""
    _run_covers("""
      const { calls, frame } = await import('./boot.mjs');
      const reads = () => calls.filter((c) => c.startsWith('/api/board?')).length;
      const before = reads();
      // Un avviso per un'app che non e' sulla bacheca: niente.
      frame({ event: 'app_data_changed', slug: 'altra' });
      await wait(400);
      assert.equal(reads(), before);
      // Per la sua: una rilettura sola, anche per una raffica.
      frame({ event: 'app_data_changed', slug: 'waterbot' });
      frame({ event: 'app_data_changed', slug: 'waterbot' });
      await wait(400);
      assert.equal(reads(), before + 1);
      // Chiudere l'app aperta rilegge la bacheca.
      const actions = app.appsActions();
      actions._openApp = { slug: 'etf', overlay: document.createElement('div'), iframe: null };
      actions.closeApp();
      await wait(400);
      assert.equal(reads(), before + 2);
    """)


@requires_jsdom
def test_a_board_changed_from_the_chat_redraws_itself() -> None:
    """L'editore lanciato dalla chat del quaderno cambia la bacheca che sta sotto:
    fino al 09/10/2026 restava quella di prima finche' non la si riapriva."""
    _run_covers("""
      const { calls, frame } = await import('./boot.mjs');
      const reads = () => calls.filter((c) => c.startsWith('/api/board?')).length;
      const before = reads();
      // Un altro quaderno: niente.
      frame({ event: 'board_changed', notebook: 'altro' });
      await wait(400);
      assert.equal(reads(), before);
      // Questo: una rilettura sola, anche per una raffica; le stesse schede non
      // spostano la vista.
      let fits = 0;
      const fit = app.board.zoomToFit.bind(app.board);
      app.board.zoomToFit = () => { fits += 1; return fit(); };
      frame({ event: 'board_changed', notebook: 'orto' });
      frame({ event: 'board_changed', notebook: 'orto' });
      await wait(400);
      assert.equal(reads(), before + 1);
      assert.equal(fits, 0);
      // Una scheda nata fuori da qui si inquadra: puo' stare fuori dalla vista.
      const board = structuredClone(routes['/api/board']);
      board.cards.push({ ...board.cards[0], id: 'nuova', title: 'Nuova',
                         pos: { x: -60, y: 3, by: 'user' } });
      routes['/api/board'] = board;
      frame({ event: 'board_changed', notebook: 'orto' });
      await wait(400);
      assert.equal(fits, 1);
      // Anche una scheda spostata: l'editore la mette in fila, magari fuori vista.
      const moved = structuredClone(board);
      moved.cards.at(-1).pos = { x: 80, y: 3, by: 'user' };
      routes['/api/board'] = moved;
      frame({ event: 'board_changed', notebook: 'orto' });
      await wait(400);
      assert.equal(fits, 2);
    """)


@requires_jsdom
def test_the_card_bar_has_no_cover_choice() -> None:
    _run_covers("""
      const rect = () => ({ left: 0, top: 0, width: 400, height: 400, right: 400, bottom: 400 });
      $('home-board-canvas').getBoundingClientRect = rect;
      $('home-board-wall').getBoundingClientRect = rect;
      const el = document.querySelector('.home-board-polaroid[data-id="acero"]');
      press(el, 10, 10);
      await wait(500);
      press(el, 10, 10, 'pointerup');
      const bar = document.querySelector('.home-board-cardbar');
      assert.deepEqual([...bar.querySelectorAll('button span')].map((b) => b.textContent),
        ['Rename', 'Remove from the board']);
    """)


@requires_jsdom
def test_a_new_card_can_be_an_app() -> None:
    _run_covers("""
      routes['/api/webui/apps'] = { apps: [
        { slug: 'etf', name: 'ETF', broken: false, state: 'live', section: { params: {} } },
        { slug: 'waterbot', name: 'WaterBot', broken: false, state: 'live',
          section: { params: { sensor: { type: 'string' } } } },
        { slug: 'calc', name: 'Calc', broken: false, state: 'live', section: null },
      ] };
      $('home-board-add').click();
      await tick(40);
      const select = $('home-board-sheet-body').querySelector('select');
      assert.ok(select, 'manca «Cosa ci metti?»');
      assert.deepEqual([...select.options].map((o) => o.value), ['', 'etf', 'waterbot']);
      select.value = 'waterbot';
      select.dispatchEvent(new window.Event('change'));
      const inputs = $('home-board-sheet-body').querySelectorAll('input');
      assert.equal(inputs.length, 2, 'il nome e il parametro');
      inputs[1].value = 'acerello';
      // Il nome si puo' lasciare vuoto: prende quello dell'app.
      sheetButton('Create').click();
      await tick(40);
      const call = rpcs('board.card.add').at(-1);
      assert.deepEqual(call.params.app, { slug: 'waterbot', params: { sensor: 'acerello' } });
      assert.equal(call.params.title, 'WaterBot');
    """)


# ── L'agenda delle attese, il calendario di cura, il «+» flottante ───────────


def _postit_board() -> dict:
    board = json.loads(json.dumps(BOARD))
    when = lambda d: {"kind": "date", "date": d, "precision": "day"}  # noqa: E731
    board["notes"] = [
        {"id": "r-1", "card": "monstera", "text": "Concime", "when": when("2099-01-10")},
        {"id": "r-2", "card": "acero", "text": "Poltiglia", "when": when("2099-02-01")},
        {"id": "r-3", "card": "monstera", "text": "Rinvaso", "when": when("2099-03-01")},
        {"id": "r-4", "card": "monstera", "text": "Margotta", "when": when("2099-04-01")},
        {"id": "r-5", "card": "monstera", "text": "Radici", "when": when("2099-08-20")},
    ]
    board["postits"] = [
        {"card": "acero", "pos": {"x": 20, "y": 50, "by": "jenny"}},
        {"card": "monstera", "pos": {"x": 62, "y": 35, "by": "jenny"}},
    ]
    return board


@requires_jsdom
def test_each_card_has_its_postit_with_three_rows_and_a_thread() -> None:
    _run_wall("""
      routes['/api/board'] = __POSTITS__;
      await B.load('altro');
      const postits = [...document.querySelectorAll('.home-board-postit')];
      assert.deepEqual(postits.map((p) => p.dataset.card), ['acero', 'monstera']);
      // La scheda con una sola attesa ha il suo post-it, non resta dietro alle altre.
      assert.equal(postits[0].querySelectorAll('.home-board-postit-row').length, 1);
      assert.match(postits[0].textContent, /Poltiglia/);
      // Quattro attese: tre righe, e «+1» per l'ultima.
      const rows = [...postits[1].querySelectorAll('.home-board-postit-text')].map((t) => t.textContent);
      assert.deepEqual(rows, ['Concime', 'Rinvaso', 'Margotta']);
      assert.equal(postits[1].querySelector('.home-board-postit-more').textContent, '+1 more');
      assert.ok(postits[0].querySelector('.home-board-pin.is-jenny'), 'il suo chiodo');
      // Un filo per post-it, piu' quello rosso fra le schede.
      assert.equal(document.querySelectorAll('#home-board-threads line.is-postit').length, 2);
      assert.equal(document.querySelectorAll('#home-board-threads line').length, 3);
      // Una scheda tenuta premuta accende anche il filo del suo post-it.
      B.setFocus('acero', { quiet: true });
      assert.equal(document.querySelectorAll('#home-board-threads line.is-postit.is-on').length, 1);
      assert.ok(postits[0].classList.contains('is-linked'));
      B.setFocus(null);
      // Il tocco apre il cassetto della sua scheda.
      postits[1].click();
      await tick(40);
      assert.equal(app.view, 'drawer');
      assert.equal(rpcs('board.seen').at(-1).params.card, 'monstera');
    """.replace("__POSTITS__", json.dumps(_postit_board())))


@requires_jsdom
def test_dragging_a_postit_tells_the_server() -> None:
    _run("""
      const el = document.querySelector('.home-board-postit');
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
      assert.equal(move.params.kind, 'postit');
      assert.equal(move.params.id, 'monstera');
      assert.ok(el.querySelector('.home-board-pin.is-user'));
      assert.equal(app.view, 'board', 'portarlo non apre niente');
    """)


@requires_jsdom
def test_the_drawer_shows_its_reminders_once_on_a_care_calendar() -> None:
    card = json.loads(json.dumps(CARD))
    day = lambda d: {"kind": "date", "date": d, "precision": "day"}  # noqa: E731
    card["reminders"] = [
        {"id": "r-old", "card": "acero", "text": "Old one", "when": day("2020-01-05")},
        {"id": "r-a", "card": "acero", "text": "Poltiglia", "when": day("2099-02-01")},
        {"id": "r-b", "card": "acero", "text": "Rinvaso", "when": day("2099-02-10")},
        {"id": "r-c", "card": "acero", "text": "Margotta",
         "when": {"kind": "date", "date": "2099-03-01", "precision": "month"}},
        {"id": "r-d", "card": "acero", "text": "Annaffia",
         "when": {"kind": "condition", "field": "moisture", "op": "<", "value": 30}},
    ]
    _run("""
      routes['/api/board/card'] = __CARD2__;
      for (const m of ['board.reminder.done', 'board.reminder.update']) rpcAnswers[m] = () => ({ ok: true });
      await app.openCard('acero', 'Acero');
      await tick(40);
      const cal = document.querySelector('.home-drawer-cal');
      // In ritardo, poi oggi, poi un gruppo per mese, poi quelle a condizione.
      const marks = [...cal.querySelectorAll('.home-drawer-cal-group, .home-drawer-cal-today')]
        .map((m) => m.textContent);
      assert.equal(marks[0], 'Overdue');
      assert.match(marks[1], /^Today · /);
      assert.deepEqual(marks.slice(2), ['February 2099', 'March 2099', 'When it happens']);
      // La prima e' la carta gialla, in ritardo; le altre righe, col giorno.
      const next = cal.querySelector('.home-drawer-cal-next');
      assert.ok(next.classList.contains('is-late'));
      assert.match(next.textContent, /Old one/);
      const days = [...cal.querySelectorAll('.home-drawer-cal-day')].map((d) => d.textContent);
      assert.deepEqual(days, ['1', '10', '—', 'moisture < 30']);
      // Una volta sola: niente riquadro a parte, niente sezioni «Promemoria».
      assert.equal(document.querySelectorAll('.home-drawer-cal-next').length, 1);
      assert.equal($('home-board-drawer').textContent.match(/Rinvaso/g).length, 1);
      next.querySelector('.home-drawer-cal-done').click();
      await tick(20);
      assert.equal(rpcs('board.reminder.done').at(-1).params.id, 'r-old');
      // ⋯ → Edit: il modulo gia' compilato, e la correzione va per id.
      const row = [...cal.querySelectorAll('.home-drawer-cal-row')].find((r) => /Rinvaso/.test(r.textContent));
      row.querySelector('.home-drawer-cal-more').click();
      const menu = [...$('home-board-sheet-body').querySelectorAll('.home-board-menu-row')];
      assert.deepEqual(menu.map((r) => r.textContent), ['Edit', 'Done', 'Remove']);
      menu[0].click();
      const inputs = $('home-board-sheet-body').querySelectorAll('.home-board-input');
      assert.equal(inputs[0].value, 'Rinvaso');
      assert.equal(inputs[1].value, '2099-02-10');
      inputs[0].value = 'Rinvaso nel vaso 45';
      inputs[0].closest('form').dispatchEvent(new Event('submit', { cancelable: true }));
      await tick(20);
      const update = rpcs('board.reminder.update').at(-1).params;
      assert.equal(update.id, 'r-b');
      assert.equal(update.text, 'Rinvaso nel vaso 45');
      assert.deepEqual(update.when, { kind: 'date', date: '2099-02-10', precision: 'day' });
    """.replace("__CARD2__", json.dumps(card)))


@requires_jsdom
def test_the_skeleton_sections_fold() -> None:
    _run("""
      await app.openCard('acero', 'Acero');
      await tick(40);
      const fold = document.querySelector('.home-drawer-fold');
      assert.equal(fold.getAttribute('aria-expanded'), 'false');
      assert.match(fold.textContent, /Vaso e travaso.*Vaso 20 comprato/);
      assert.equal(document.querySelector('.home-drawer-section-body .home-drawer-chip'), null);
      fold.click();
      const open = document.querySelector('.home-drawer-fold');
      assert.equal(open.getAttribute('aria-expanded'), 'true');
      assert.equal(document.activeElement, open, 'il fuoco resta sulla sezione');
      assert.ok(document.querySelector('.home-drawer-section-body .home-drawer-chip'));
    """)


@requires_jsdom
def test_the_floating_plus_opens_a_fan_and_back_closes_it() -> None:
    _run("""
      await app.openCard('acero', 'Acero');
      await tick(40);
      const fab = document.querySelector('.home-drawer-fab');
      const dial = document.querySelector('.home-drawer-dial');
      // Nel guscio, non nel cassetto che scorre; e prima della mascotte.
      assert.ok(fab.parentElement.classList.contains('home-shell'));
      assert.equal(document.querySelector('.home-drawer-add'), null);
      assert.equal(fab.getAttribute('aria-expanded'), 'false');
      fab.click();
      assert.equal(dial.hidden, false);
      assert.equal(fab.getAttribute('aria-expanded'), 'true');
      const names = [...dial.querySelectorAll('.home-drawer-dial-name')].map((n) => n.textContent);
      // Dal basso la prima e' il promemoria: e' l'ultima nel DOM, la piu' vicina al tondo.
      assert.equal(names.at(-1), 'Reminder');
      // Indietro chiude il ventaglio e si resta nel cassetto.
      app.handleHardwareBack();
      await tick(10);
      assert.equal(dial.hidden, true);
      assert.equal(app.view, 'drawer');
      // Un tocco sul velo lo chiude; una scelta apre il suo modulo.
      fab.click();
      dial.click();
      assert.equal(dial.hidden, true);
      fab.click();
      [...dial.querySelectorAll('.home-drawer-dial-row')].at(-1).click();
      assert.equal(dial.hidden, true);
      assert.ok($('home-board-sheet-body').querySelector('input[type="date"]'));
    """)


# ── Le note dell'utente: il mazzetto accanto al titolo, la lista nel foglio ──


def _notes_card() -> dict:
    card = json.loads(json.dumps(CARD))
    card["notes"] = [
        {"id": "s-note-2", "text": "Il vaso bianco in cantina.",
         "created": "2026-10-12T09:00:00+02:00", "status": "pending"},
        {"id": "s-note-1", "text": "Al vivaio dicono di potarlo subito dopo la fioritura e mai "
         "d'inverno, perché i rami a zig-zag li fa solo sui getti dell'anno; e di concimarlo a "
         "fine estate con poco azoto.",
         "created": "2026-10-03T09:00:00+02:00", "status": "integrated"},
    ]
    return card


_NOTES = """
routes['/api/board/card'] = __NOTES_CARD__;
for (const m of ['board.section.add', 'board.section.update', 'board.section.remove']) {
  rpcAnswers[m] = () => ({ ok: true });
}
// jsdom non misura: un testo lungo «sfora» le sue quattro righe.
Object.defineProperty(window.HTMLElement.prototype, 'scrollHeight', {
  configurable: true, get() { return this.textContent.length > 100 ? 200 : 40; } });
Object.defineProperty(window.HTMLElement.prototype, 'clientHeight', {
  configurable: true, get() { return 80; } });
await app.openCard('acero', 'Acero');
await tick(40);
const sheetBody = () => $('home-board-sheet-body');
"""


def _run_notes(script: str) -> None:
    _run(_NOTES.replace("__NOTES_CARD__", json.dumps(_notes_card())) + script)


@requires_jsdom
def test_a_drawer_without_notes_has_no_notes_block() -> None:
    _run("""
      await app.openCard('acero', 'Acero');
      await tick(40);
      assert.equal(document.querySelector('.home-notes'), null);
    """)


@requires_jsdom
def test_the_notes_are_a_deck_under_the_brief() -> None:
    _run_notes("""
      const block = document.querySelector('.home-drawer-pins .home-notes');
      assert.ok(block, 'il mazzetto sta nella fila sotto il brief');
      assert.equal(block.querySelector('.home-notes-title').textContent, 'Your notes · 2');
      // Le note non sono piu' sezioni fra le altre.
      assert.equal(document.querySelectorAll('.home-drawer-section.is-user').length, 0);
      const deck = block.querySelector('.home-notes-deck');
      const cards = [...deck.querySelectorAll('.home-notes-card')];
      assert.match(cards[0].className, /is-slot-0/, 'davanti la piu\\u2019 recente');
      assert.match(deck.getAttribute('aria-label'), /^Note 1 of 2: Il vaso bianco/);
      deck.click();
      // La carta davanti si sfila mentre l'altra avanza; un tocco adesso non conta.
      assert.match(cards[0].className, /is-out/);
      assert.match(cards[1].className, /is-slot-0/);
      deck.click();
      assert.match(cards[0].className, /is-out/);
      await tick(220);
      assert.match(cards[0].className, /is-slot-1/, 'rientrata in fondo al mazzo');
      assert.match(deck.getAttribute('aria-label'), /^Note 2 of 2: Al vivaio/);
      // L'ordine delle carte e' in linea, dentro il mazzetto.
      assert.ok(Number(cards[1].style.zIndex) > Number(cards[0].style.zIndex));
    """)


@requires_jsdom
def test_all_notes_open_in_the_sheet_long_ones_read_in_full_and_are_edited() -> None:
    _run_notes("""
      document.querySelector('.home-notes-all').click();
      await tick(20);
      assert.equal($('home-board-sheet-title').textContent, 'Your notes · 2');
      assert.ok(sheetBody().querySelector('.home-notes-new'));
      const tiles = [...sheetBody().querySelectorAll('.home-notes-tile')];
      assert.equal(tiles.length, 2);
      // «Leggi tutto» solo sulla nota che non sta nelle sue righe.
      assert.equal(tiles[0].querySelector('.home-notes-more').hidden, true);
      assert.equal(tiles[1].querySelector('.home-notes-more').hidden, false);
      // Il tocco la apre per esteso, con lo stato verso la wiki.
      tiles[1].click();
      const open = sheetBody().querySelector('.home-notes-open');
      assert.match(open.textContent, /getti dell'anno; e di concimarlo/);
      assert.match(open.textContent, /✓ in the wiki/);
      // Correggi → si scrive; Salva → la stessa sezione, testo nuovo.
      [...open.querySelectorAll('button')].find((b) => b.textContent === 'Edit').click();
      const area = sheetBody().querySelector('.home-notes-area');
      assert.match(area.value, /^Al vivaio/);
      area.value = 'Potarlo dopo la fioritura.';
      area.dispatchEvent(new Event('input'));
      [...sheetBody().querySelectorAll('button')].find((b) => b.textContent === 'Save').click();
      await tick(40);
      const call = rpcs('board.section.update').at(-1).params;
      assert.deepEqual([call.card, call.id, call.text], ['acero', 's-note-1', 'Potarlo dopo la fioritura.']);
      // Il cassetto si ricarica e la lista torna a riposo, nel foglio ancora aperto.
      assert.equal(sheetBody().querySelector('.home-notes-area'), null);
      assert.equal(sheetBody().querySelectorAll('.home-notes-tile').length, 2);
    """)


@requires_jsdom
def test_a_new_note_and_a_removed_one() -> None:
    _run_notes("""
      document.querySelector('.home-notes-all').click();
      await tick(20);
      sheetBody().querySelector('.home-notes-new').click();
      const area = sheetBody().querySelector('.home-notes-area');
      assert.equal(document.activeElement, area);
      area.value = 'Sta meglio a est.';
      area.dispatchEvent(new Event('input'));
      [...sheetBody().querySelectorAll('button')].find((b) => b.textContent === 'Save').click();
      await tick(40);
      const add = rpcs('board.section.add').at(-1).params;
      assert.deepEqual([add.card, add.kind, add.text], ['acero', 'note', 'Sta meglio a est.']);
      // Togli chiede prima.
      sheetBody().querySelector('.home-notes-tile').click();
      [...sheetBody().querySelectorAll('.home-notes-open button')].find((b) => b.textContent === 'Edit').click();
      [...sheetBody().querySelectorAll('button')].find((b) => /Remove/.test(b.textContent)).click();
      await tick(10);
      assert.equal(rpcs('board.section.remove').length, 0);
      $('oc-confirm-ok').click();
      await tick(40);
      assert.equal(rpcs('board.section.remove').at(-1).params.id, 's-note-2');
    """)


@requires_jsdom
def test_the_floating_plus_writes_a_note_in_the_sheet() -> None:
    _run_notes("""
      document.querySelector('.home-drawer-fab').click();
      [...document.querySelectorAll('.home-drawer-dial-row')]
        .find((r) => r.querySelector('.home-drawer-dial-name').textContent === 'Note').click();
      await tick(20);
      assert.ok(sheetBody().querySelector('.home-notes-area'), 'il foglio e\\u2019 gia\\u2019 sulla nota nuova');
      assert.equal(document.querySelector('.home-drawer-dial').hidden, true);
    """)


def test_the_notes_lilac_reads_as_text_in_every_theme() -> None:
    """Il titoletto «Le tue note» e «Nuova nota» sono lilla sulla pagina, non
    sulla carta: nei temi chiari il lilla della carta si leggeva a 1,3:1, e lo
    sostituisce `--board-note-label`."""
    low = []
    for theme in theme_tokens.themes():
        t = theme_tokens.tokens(theme)
        ink = t.get("board-note-label") or t["board-note"]
        for ground in ("bg", "surface"):
            ratio = theme_tokens.contrast(ink, t[ground], t["bg"])
            if ratio < 4.5:
                low.append(f"{theme} su --{ground}: {ratio:.2f}:1")
    assert not low, f"il lilla delle note non si legge: {low}"


# ── Le foto: il mazzetto di polaroid, la galleria, una foto nuova con la nota ──


def _photos_card() -> dict:
    card = _notes_card()
    card["photos"] = [
        {"id": f"s-photo-{i}", "path": f"raw/photos/acero-{i}.jpg",
         "caption": "" if i == 2 else f"Foto numero {i}",
         "created": f"2026-10-{14 - i:02d}T10:00:00+02:00",
         "exists": True, "status": "none" if i == 2 else ("integrated" if i == 4 else "pending")}
        for i in range(5)
    ]
    return card


_PHOTOS = """
// Le immagini: un blob finto per ogni foto, e un url che dice di chi e'.
let blobs = 0;
URL.createObjectURL = () => `blob:${++blobs}`;
URL.revokeObjectURL = () => {};
hooks.fetch = (u) => (u.pathname === '/api/board/photo'
  ? { ok: true, status: 200, blob: async () => new Blob([u.searchParams.get('path')]) }
  : undefined);
"""


def _run_photos(script: str) -> None:
    _run(_PHOTOS + _NOTES.replace("__NOTES_CARD__", json.dumps(_photos_card())) + script)


@requires_jsdom
def test_the_photos_are_a_deck_beside_the_notes() -> None:
    _run_photos("""
      const pins = document.querySelector('.home-drawer-pins');
      // Le foto prima, le note dopo.
      assert.deepEqual([...pins.children].map((c) => c.className), ['home-photos', 'home-notes']);
      assert.equal(pins.querySelector('.home-photos-title').textContent, 'Your photos · 5');
      // Una foto non e' piu' una sezione del cassetto.
      assert.equal(document.querySelectorAll('.home-drawer-section.is-user').length, 0);
      const deck = pins.querySelector('.home-photos-deck');
      const cards = [...deck.querySelectorAll('.home-photos-card')];
      assert.equal(cards.length, 5);
      assert.match(cards[0].className, /is-slot-0/);
      assert.match(cards[0].textContent, /Foto numero 0/);
      // Si prendono solo le immagini che si vedono: le prime tre.
      await tick(20);
      const loaded = cards.map((c) => Boolean(c.querySelector('img').getAttribute('src')));
      assert.deepEqual(loaded, [true, true, true, false, false]);
      assert.match(deck.getAttribute('aria-label'), /^Photo 1 of 5: Foto numero 0/);
      deck.click();
      assert.match(cards[0].className, /is-out/);
      assert.match(cards[1].className, /is-slot-0/);
      await tick(220);
      assert.match(deck.getAttribute('aria-label'), /^Photo 2 of 5: Foto numero 1/);
      // La quarta arriva fra quelle che si vedono, e la sua immagine con lei.
      await tick(20);
      assert.ok(cards[3].querySelector('img').getAttribute('src'));
    """)


@requires_jsdom
def test_an_opened_photo_is_an_album_page_and_its_note_is_corrected_in_place() -> None:
    _run_photos("""
      document.querySelector('.home-photos .home-notes-all').click();
      await tick(20);
      assert.equal($('home-board-sheet-title').textContent, 'Your photos · 5');
      assert.ok(sheetBody().querySelector('.home-photos-new'));
      const tiles = [...sheetBody().querySelectorAll('.home-photos-tile')];
      assert.equal(tiles.length, 5);
      assert.equal(tiles[2].querySelector('.home-photos-tile-text').textContent, 'No note');
      // Aperta: la pagina d'album, al posto della griglia, col nome della scheda.
      tiles[4].click();
      assert.equal(sheetBody().querySelector('.home-photos-grid'), null);
      assert.equal($('home-board-sheet-title').textContent, 'Acero');
      const page = sheetBody().querySelector('.home-photos-page');
      assert.match(page.querySelector('.home-photos-pol-note').textContent, /Foto numero 4/);
      assert.match(page.querySelector('.home-photos-state').textContent, /in the wiki/);
      assert.equal(page.querySelector('.home-photos-pager-at').textContent, '5 of 5');
      assert.ok(page.querySelectorAll('.home-photos-step')[1].disabled, 'l\u2019ultima: niente avanti');
      // Le frecce: la precedente, senza nota e senza stato verso la wiki.
      page.querySelectorAll('.home-photos-step')[0].click();
      sheetBody().querySelectorAll('.home-photos-step')[0].click();
      const bare = sheetBody().querySelector('.home-photos-page');
      assert.equal(bare.querySelector('.home-photos-pager-at').textContent, '3 of 5');
      assert.equal(bare.querySelector('.home-photos-pol-note').textContent, 'No note');
      assert.equal(bare.querySelector('.home-photos-state'), null);
      // Correggi: la nota si scrive nella polaroid.
      [...bare.querySelectorAll('.home-photos-act')].find((b) => b.textContent === 'Edit the note').click();
      const area = sheetBody().querySelector('.home-photos-pol .home-photos-pol-area');
      assert.equal(document.activeElement, area);
      area.value = 'Le foglie in basso virano';
      area.dispatchEvent(new Event('input'));
      sheetBody().querySelector('.home-photos-save').click();
      await tick(40);
      const call = rpcs('board.section.update').at(-1).params;
      assert.deepEqual([call.card, call.id, call.text], ['acero', 's-photo-2', 'Le foglie in basso virano']);
      // Salvata, si torna alla sua pagina, a riposo.
      assert.equal(sheetBody().querySelector('.home-photos-pol-area'), null);
      assert.ok(sheetBody().querySelector('.home-photos-page'));
      // Tutte le foto: di nuovo la griglia.
      sheetBody().querySelector('.home-photos-back').click();
      assert.equal(sheetBody().querySelectorAll('.home-photos-tile').length, 5);
      // Togli chiede prima.
      sheetBody().querySelector('.home-photos-tile').click();
      [...sheetBody().querySelectorAll('.home-photos-act')].find((b) => b.textContent === 'Remove from the card').click();
      await tick(10);
      assert.equal(rpcs('board.section.remove').length, 0);
      $('oc-confirm-ok').click();
      await tick(40);
      assert.equal(rpcs('board.section.remove').at(-1).params.id, 's-photo-0');
    """)


@requires_jsdom
def test_a_new_photo_opens_on_its_note_and_goes_out_only_on_save() -> None:
    _run_photos("""
      // Il selettore di file: si prende l'input che il tocco apre, e gli si da' un file.
      let picker = null;
      HTMLInputElement.prototype.click = function () { if (this.type === 'file') picker = this; };
      const choose = async () => {
        Object.defineProperty(picker, 'files', { value: [new Blob(['jpg'])], configurable: true });
        picker.dispatchEvent(new Event('change'));
        await tick(20);
      };
      app.drawer.photos.shrink = async () => ({ mime: 'image/jpeg', data: 'AAAA' });
      document.querySelector('.home-drawer-fab').click();
      [...document.querySelectorAll('.home-drawer-dial-row')]
        .find((r) => r.querySelector('.home-drawer-dial-name').textContent === 'Photo').click();
      assert.equal(picker.accept, 'image/*');
      await choose();
      assert.equal($('home-board-sheet-title').textContent, 'New photo');
      const page = sheetBody().querySelector('.home-photos-page.is-editing');
      assert.match(page.querySelector('.home-photos-pol img').src, /^blob:/, 'l\u2019anteprima del file scelto');
      assert.equal(page.querySelector('.home-photos-day').textContent, 'Now');
      assert.equal(document.activeElement, page.querySelector('.home-photos-pol-area'));
      // Annulla: non parte niente.
      [...page.querySelectorAll('.home-photos-act')].find((b) => b.textContent === 'Cancel').click();
      assert.equal(rpcs('board.section.add').length, 0);
      assert.equal($('home-board-sheet-title').textContent, 'Your photos · 5');
      // Dal riquadro della galleria, questa volta con la nota.
      sheetBody().querySelector('.home-photos-new').click();
      await choose();
      const area = sheetBody().querySelector('.home-photos-pol-area');
      area.value = 'Il germoglio nuovo è rosso!';
      area.dispatchEvent(new Event('input'));
      sheetBody().querySelector('.home-photos-save').click();
      await tick(40);
      const add = rpcs('board.section.add').at(-1).params;
      assert.deepEqual([add.card, add.kind, add.mime, add.data, add.caption],
        ['acero', 'photo', 'image/jpeg', 'AAAA', 'Il germoglio nuovo è rosso!']);
      assert.equal(sheetBody().querySelector('.home-photos-pol-area'), null);
    """)


@requires_jsdom
def test_a_drawer_without_photos_has_only_the_notes() -> None:
    _run_notes("""
      const pins = document.querySelector('.home-drawer-pins');
      assert.deepEqual([...pins.children].map((c) => c.className), ['home-notes']);
    """)


# ── Sulla bacheca: le note accorpate in un post-it lilla, le foto in una galleria ──


def _kept_board() -> dict:
    board = json.loads(json.dumps(BOARD))
    board["notePostits"] = [{
        "card": "acero", "pos": {"x": 5, "y": 50, "by": "jenny"}, "count": 2,
        "rows": [
            {"text": "Il vaso bianco in cantina.", "created": "2026-10-12T09:00:00+02:00"},
            {"text": "Al vivaio dicono di potarlo.", "created": "2026-10-03T09:00:00+02:00"},
        ],
    }]
    board["galleries"] = [{
        "card": "acero", "pos": {"x": 28, "y": 6, "by": "jenny"}, "count": 5,
        "photos": [{"id": f"s-photo-{i}", "path": f"raw/photos/acero-{i}.jpg",
                    "caption": f"Foto numero {i}"} for i in range(3)],
    }]
    return board


def _run_kept(script: str) -> None:
    _run(_PHOTOS + """
      // La tela con una misura, che jsdom non ha: senza, i fili non si disegnano.
      $('home-board-canvas').getBoundingClientRect = () => ({ left: 0, top: 0, right: 400,
        bottom: 400, width: 400, height: 400, x: 0, y: 0 });
      routes['/api/board'] = __KEPT__;
      routes['/api/board/card'] = __CARD3__;
      for (const m of ['board.section.add', 'board.section.update', 'board.section.remove']) {
        rpcAnswers[m] = () => ({ ok: true });
      }
      await app.board.load('orto');
      await tick(30);
      const sheetBody = () => $('home-board-sheet-body');
    """.replace("__KEPT__", json.dumps(_kept_board()))
       .replace("__CARD3__", json.dumps(_photos_card())) + script)


@requires_jsdom
def test_the_board_shows_a_lilac_note_and_a_gallery_by_their_card() -> None:
    _run_kept("""
      const lilac = document.querySelector('.home-board-postit.is-notes');
      assert.ok(lilac.classList.contains('is-many'), 'sotto spunta un secondo foglio');
      const rows = [...lilac.querySelectorAll('.home-board-postit-text')].map((t) => t.textContent);
      assert.deepEqual(rows, ['Il vaso bianco in cantina.', 'Al vivaio dicono di potarlo.']);
      assert.equal(lilac.dataset.card, 'acero');
      assert.match(lilac.getAttribute('aria-label'), /^Acero, your notes — /);
      const gallery = document.querySelector('.home-board-gallery');
      assert.equal(gallery.querySelector('.home-board-gallery-count').textContent, '5');
      const photos = [...gallery.querySelectorAll('.home-board-gallery-photo')];
      // L'ultima nel DOM e' quella davanti, con la sua nota.
      assert.equal(photos.at(-1).className, 'home-board-gallery-photo is-0');
      assert.equal(photos.at(-1).textContent, 'Foto numero 0');
      await tick(20);
      assert.ok(photos.every((p) => p.querySelector('img').getAttribute('src')));
      assert.equal(gallery.getAttribute('aria-label'), 'Acero, 5 photos. Tap to see them.');
      // Un filo per il giallo, il lilla e la galleria, piu' quello fra le schede.
      assert.equal(document.querySelectorAll('#home-board-threads line.is-postit').length, 3);
      // La scheda tenuta premuta accende anche i loro fili.
      app.board.setFocus('acero', { quiet: true });
      assert.ok(lilac.classList.contains('is-linked') && gallery.classList.contains('is-linked'));
      app.board.setFocus(null);
      // La scatola e' grande quanto le polaroid che porta: tre qui, una sotto.
      assert.ok(gallery.classList.contains('is-n3'));
      const one = JSON.parse(JSON.stringify(routes['/api/board']));
      one.galleries[0].photos = one.galleries[0].photos.slice(0, 1);
      one.galleries[0].count = 1;
      routes['/api/board'] = one;
      await app.board.load('orto');
      await tick(20);
      const single = document.querySelector('.home-board-gallery');
      assert.ok(single.classList.contains('is-n1'));
      assert.equal(single.querySelectorAll('.home-board-gallery-photo').length, 1);
      assert.equal(single.getAttribute('aria-label'), 'Acero, 1 photo. Tap to see it.');
    """)


@requires_jsdom
def test_the_lilac_note_and_the_gallery_open_their_sheet_on_the_board() -> None:
    _run_kept("""
      document.querySelector('.home-board-gallery').click();
      await tick(40);
      assert.equal(app.view, 'board', 'il cassetto non si apre');
      assert.equal($('home-board-sheet-title').textContent, 'Your photos · 5');
      assert.equal(sheetBody().querySelectorAll('.home-photos-tile').length, 5);
      // Una nota corretta da qui: la scrittura, poi il muro si ridisegna.
      sheetBody().querySelector('.home-photos-tile').click();
      [...sheetBody().querySelectorAll('.home-photos-act')].find((b) => b.textContent === 'Edit the note').click();
      const area = sheetBody().querySelector('.home-photos-pol-area');
      area.value = 'Rossa';
      area.dispatchEvent(new Event('input'));
      const { calls } = await import('./boot.mjs');
      const boards = calls.filter((c) => c.startsWith('/api/board?')).length;
      sheetBody().querySelector('.home-photos-save').click();
      await tick(60);
      assert.deepEqual(rpcs('board.section.update').at(-1).params.text, 'Rossa');
      assert.ok(calls.filter((c) => c.startsWith('/api/board?')).length > boards, 'il muro riletto');
      assert.equal(sheetBody().querySelector('.home-photos-pol-area'), null);
      // Il lilla apre le note.
      document.querySelector('.home-board-postit.is-notes').click();
      await tick(40);
      assert.equal($('home-board-sheet-title').textContent, 'Your notes · 2');
      assert.equal(app.view, 'board');
    """)


@requires_jsdom
def test_dragging_the_lilac_note_and_the_gallery_tells_the_server() -> None:
    _run_kept("""
      for (const [sel, kind] of [['.home-board-postit.is-notes', 'notes'], ['.home-board-gallery', 'photos']]) {
        const el = document.querySelector(sel);
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
        assert.deepEqual([move.params.kind, move.params.id], [kind, 'acero']);
        assert.equal(app.view, 'board', 'portarlo non apre niente');
      }
    """)


@requires_jsdom
def test_the_list_view_counts_the_notes_and_the_photos() -> None:
    _run_kept("""
      app.board.toggleList();
      const row = [...document.querySelectorAll('.home-board-list-row')]
        .find((r) => r.querySelector('.home-board-list-name')?.textContent === 'Acero');
      assert.equal(row.querySelector('.home-board-list-kept').textContent, '2 notes · 5 photos');
    """)


@requires_jsdom
def test_an_opened_photo_goes_full_screen_whole_and_back_closes_it_first() -> None:
    _run_photos("""
      let revoked = 0;
      URL.revokeObjectURL = () => { revoked += 1; };
      document.querySelector('.home-photos .home-notes-all').click();
      await tick(20);
      sheetBody().querySelectorAll('.home-photos-tile')[1].click();
      await tick(20);
      const shot = sheetBody().querySelector('button.home-photos-pol-img');
      assert.equal(shot.getAttribute('aria-label'), 'See the whole photo, full screen');
      shot.click();
      await tick(20);
      // Un dialog nel top layer, sopra il foglio, con la foto intera.
      const box = document.querySelector('dialog.home-photo-viewer');
      assert.ok(box && box.open, 'la foto a tutto schermo e\u2019 un dialog aperto');
      assert.ok(box.classList.contains('image-lightbox'), 'per la catena di Indietro');
      assert.equal(box.querySelector('.home-photo-viewer-count').textContent, 'Acero · 2 of 5');
      assert.equal(box.querySelector('.home-photo-viewer-note').textContent, 'Foto numero 1');
      assert.equal(box.querySelector('.home-photo-viewer-img').getAttribute('src'),
        sheetBody().querySelector('.home-photos-pol img').getAttribute('src'));
      assert.equal(box.querySelectorAll('.home-photo-viewer-dots i').length, 5);
      assert.ok(box.querySelectorAll('.home-photo-viewer-dots i')[1].classList.contains('is-on'));
      // Col dito verso sinistra, la prossima; e la pagina d'album sotto la segue.
      const stage = box.querySelector('.home-photo-viewer-stage');
      const touch = (type, x) => {
        const e = new window.Event(type, { bubbles: true, cancelable: true });
        const t = { screenX: x, screenY: 200, clientX: x, clientY: 200 };
        Object.assign(e, { touches: type === 'touchend' ? [] : [t], changedTouches: [t] });
        stage.querySelector('img').dispatchEvent(e);
      };
      touch('touchstart', 300);
      touch('touchmove', 250);
      touch('touchmove', 150);
      touch('touchend', 120);
      await tick(10);
      assert.equal(box.querySelector('.home-photo-viewer-count').textContent, 'Acero · 3 of 5');
      assert.equal(sheetBody().querySelector('.home-photos-pager-at').textContent, '3 of 5');
      // Indietro chiude prima la foto, e il foglio resta.
      app.handleHardwareBack();
      await tick(10);
      assert.equal(document.querySelector('.image-lightbox'), null);
      assert.ok($('home-board-sheet').open, 'il foglio e\u2019 ancora aperto');
      assert.equal(revoked, 0, 'i blob: della galleria non si revocano');
      app.handleHardwareBack();
      await tick(10);
      assert.ok(!$('home-board-sheet').open);
    """)


def test_the_album_polaroid_is_square_and_the_viewer_shows_the_whole_photo() -> None:
    """Aperta, la polaroid e' quadrata (ritagliata, come una polaroid); intera la
    foto e' a tutto schermo. Il 14/10/2026 una foto aperta ``contain`` su
    un'altezza fissa era una striscia fra due bande grigie (Titan)."""
    from support import css_levels

    css = (css_levels.ASSETS / "home-style.css").read_text(encoding="utf-8")
    rules = {sel.strip(): body for sel, body, _ in css_levels.rules(css)}
    assert "aspect-ratio: 1" in rules[".home-photos-pol-img"]
    assert "object-fit: cover" in rules[".home-photos-pol-img img"]
    assert "object-fit: contain" in rules[".home-photo-viewer-img"]
