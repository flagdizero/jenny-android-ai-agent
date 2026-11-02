"""La scheda di un posto in chat (``shared/place-card.js``).

Jenny manda un posto scrivendo un link a Google Maps da solo sul suo paragrafo;
la chat lo disegna come cartolina, e un tocco apre Maps. Qui si misura il
modulo vero in node (riconoscimento, geometria delle tessere, «sei qui») e poi
la casa vera in jsdom: la risposta che arriva, la scheda che compare, il tocco
che va al ponte nativo e non al browser.

Cose che leggendo il sorgente non si dimostrano:

* un link **dentro una frase** resta un link: Jenny che cita un posto non lo
  sta mandando;
* il testo del link e' del modello e resta testo, anche se contiene HTML;
* coordinate fuori scala non fanno una mappa nel mezzo dell'oceano: niente
  coordinate, e la scheda resta senza mappa;
* le tessere che non arrivano lasciano la scheda intera, senza buchi.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.home_dom import requires_jsdom, run_home
from support.js_harness import requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
PLACE_CARD_JS = ASSETS / "shared" / "place-card.js"
_VENDOR = ASSETS / "vendor"

pytestmark = requires_node

_MODULE = (
    "import assert from 'node:assert/strict';\n"
    f"const M = await import('{PLACE_CARD_JS.as_uri()}');\n"
)

_LINK = "https://www.google.com/maps/search/?api=1&query=44.492400%2C11.347200"


def test_a_maps_link_gives_its_place() -> None:
    run_js(
        _MODULE
        + f"""
      const p = M.placeOf('{_LINK}', 'Piazza Santo Stefano — 40125 Bologna BO');
      assert.equal(p.lat, 44.4924);
      assert.equal(p.lng, 11.3472);
      assert.equal(p.name, 'Piazza Santo Stefano');
      assert.equal(p.address, '40125 Bologna BO');

      // Le forme che si trovano in giro.
      assert.equal(M.placeOf('https://www.google.com/maps?q=45.1,9.2', 'x').lat, 45.1);
      assert.equal(M.placeOf('https://maps.google.it/?ll=45.1,9.2', 'x').lng, 9.2);
      const place = M.placeOf('https://www.google.com/maps/place/Bar/@45.0,9.0,17z/data=!3m1!4b1!4m5!3m4!1s0x0:0x0!8m2!3d45.123!4d9.456', 'Bar');
      assert.deepEqual([place.lat, place.lng], [45.123, 9.456], 'il punto del posto vince sul centro della vista');

      // Un link corto non porta coordinate: scheda sì, mappa no.
      const short = M.placeOf('https://maps.app.goo.gl/AbCd123', 'Bar Roma');
      assert.equal(short.lat, null);
      assert.equal(short.name, 'Bar Roma');

      // Senza coordinate si cerca per nome: il nome e' quel che si cercava.
      const named = M.placeOf('https://www.google.com/maps/search/?api=1&query=Trattoria+da+Bruna+Bologna', 'https://www.google.com/maps/search/?api=1&query=Trattoria+da+Bruna+Bologna');
      assert.equal(named.lat, null);
      assert.equal(named.name, 'Trattoria da Bruna Bologna');

      // Il trattino corto sta dentro i nomi.
      assert.equal(M.placeOf('{_LINK}', 'Bar - Tabacchi').name, 'Bar - Tabacchi');
    """
    )


def test_not_every_link_is_a_place() -> None:
    run_js(
        _MODULE
        + """
      for (const href of [
        'https://example.org/maps?q=45,9',
        'https://www.google.com/search?q=45,9',
        'https://evil-google.com/maps?q=45,9',
        'https://google.com.evil.org/maps?q=45,9',
        'javascript:alert(1)',
        'geo:45,9',
        '/maps?q=45,9',
        '',
      ]) {
        assert.equal(M.placeOf(href, 'x'), null, href);
      }
      // Fuori scala: un posto senza coordinate, non un punto inventato.
      assert.equal(M.placeOf('https://www.google.com/maps?q=145,9', 'x').lat, null);
      assert.equal(M.placeOf('https://www.google.com/maps?q=45,190', 'x').lng, null);
    """
    )


def test_here_or_distance() -> None:
    run_js(
        _MODULE
        + """
      const place = { lat: 44.4924, lng: 11.3472 };
      const fix = (lat, lng, accuracyMeters = 12, ageSeconds = 20) => ({ lat, lng, accuracyMeters, ageSeconds });
      // A pochi metri, fix fresco: sei qui.
      assert.deepEqual(M.relation(place, fix(44.49241, 11.34721)), { here: true, accuracy: 12 });
      // Lo stesso punto ma un fix di un quarto d'ora fa: non si dice «sei qui».
      assert.equal(M.relation(place, fix(44.49241, 11.34721, 12, 900)).here, false);
      // Un fix di un'ora fa non dice nemmeno la distanza (sull'emulatore: «9.776 km»).
      assert.equal(M.relation(place, fix(37.42, -122.08, 5, 3600)), null);
      // Un chilometro e qualcosa.
      const far = M.relation(place, fix(44.4924, 11.3622));
      assert.equal(far.here, false);
      assert.ok(far.meters > 1100 && far.meters < 1300, far.meters);
      // Niente fix, o niente coordinate: niente da dire.
      assert.equal(M.relation(place, null), null);
      assert.equal(M.relation({ lat: null, lng: null }, fix(1, 1)), null);
    """
    )


def test_the_tiles_put_the_point_in_the_middle() -> None:
    run_js(
        _MODULE
        + """
      const p = M.worldPx(44.4924, 11.3472, 16);
      const tiles = M.tilesFor(p.x, p.y, 312, 138, 16);
      assert.ok(tiles.length >= 2 && tiles.length <= 6, tiles.length);
      // Ogni tessera sta dove la sua parte di mondo cade nel riquadro.
      for (const t of tiles) {
        assert.equal(t.x * 256 - (p.x - 156), t.left);
        assert.equal(t.y * 256 - (p.y - 69), t.top);
      }
      // Coprono il riquadro intero.
      const minL = Math.min(...tiles.map((t) => t.left));
      const minT = Math.min(...tiles.map((t) => t.top));
      assert.ok(minL <= 0 && minT <= 0);
      assert.ok(Math.max(...tiles.map((t) => t.left)) + 256 >= 312);
      assert.ok(Math.max(...tiles.map((t) => t.top)) + 256 >= 138);

      // Sul bordo del mondo: in orizzontale si ripete, in verticale non si chiede niente.
      const edge = M.tilesFor(10, 10, 312, 138, 2);
      assert.ok(edge.every((t) => t.x >= 0 && t.x < 4 && t.y >= 0 && t.y < 4));

      // Tre posti in una citta': lo zoom piu' alto che li tiene tutti.
      const pts = [{ lat: 44.4924, lng: 11.3472 }, { lat: 44.4957, lng: 11.333 }, { lat: 44.497, lng: 11.355 }];
      const view = M.fitView(pts, 330, 112);
      const px = pts.map((q) => M.worldPx(q.lat, q.lng, view.z));
      for (const q of px) {
        assert.ok(Math.abs(q.x - view.cx) <= 165 - 22 && Math.abs(q.y - view.cy) <= 56 - 22, JSON.stringify(view));
      }
      const tighter = M.fitView(pts, 330, 112, 22, view.z + 1);
      assert.equal(tighter.z, view.z, 'uno zoom piu\\u2019 alto li avrebbe tenuti tutti');
    """
    )


_HOME = f"""
import assert from 'node:assert/strict';
import {{ createRequire }} from 'node:module';
import {{ boot, tick, frame, routes, hooks, ok }} from './boot.mjs';
const require = createRequire(import.meta.url);
globalThis.DOMPurify = require({json.dumps(str(_VENDOR / "dompurify@3" / "purify.min.js"))})(window);
globalThis.marked = require({json.dumps(str(_VENDOR / "marked@15.0.7" / "marked.min.js"))});
window.DOMPurify = globalThis.DOMPurify;
window.marked = globalThis.marked;
const opened = [];
window.open = (href) => {{ opened.push(href); return {{}}; }};
const app = await boot();
const tap = (node) => {{
  const e = new window.MouseEvent('click', {{ bubbles: true, cancelable: true }});
  node.dispatchEvent(e);
  return e;
}};
let turn = 0;
const answer = async (text) => {{
  frame({{ event: 'stream_end', chat_id: 'default', turn_id: 't' + (++turn), text }});
  await tick(20);
  const blocks = document.querySelectorAll('#home-thread .home-block');
  return blocks[blocks.length - 1];
}};
const LINK = {json.dumps(_LINK)};
"""


@requires_jsdom
def test_a_sole_maps_link_becomes_a_card_and_a_quoted_one_does_not() -> None:
    run_home(
        _HOME
        + """
let block = await answer('Eccola:\\n\\n[Piazza Santo Stefano — 40125 Bologna BO](' + LINK + ')');
const card = block.querySelector('a.place-card');
assert.ok(card, block.innerHTML);
assert.equal(card.querySelector('.place-name').textContent, 'Piazza Santo Stefano');
assert.equal(card.querySelector('.place-address').textContent, '40125 Bologna BO');
assert.equal(card.dataset.lat, '44.4924');
assert.equal(card.querySelector('.place-open').textContent.trim(), 'Open in Maps');
assert.equal(block.querySelectorAll('p').length, 1, 'il paragrafo del link e\\u2019 stato sostituito');

block = await answer('Ci vediamo in [piazza](' + LINK + ') alle otto.');
assert.equal(block.querySelector('.place-card'), null, 'un link dentro una frase resta un link');
assert.ok(block.querySelector('a[href]'));

// Il testo del link e' del modello: HTML compreso, resta testo.
block = await answer('[<img src=x onerror=alert(1)><b>Bar</b> — Via Roma 1](' + LINK + ')');
const name = block.querySelector('.place-name');
assert.ok(name, block.innerHTML);
assert.equal(name.children.length, 0);
assert.equal(block.querySelector('.place-card img, .place-card b'), null);
"""
    )


@requires_jsdom
def test_places_in_a_row_become_one_list() -> None:
    run_home(
        _HOME
        + """
routes['/api/location/last'] = { lat: 44.4924, lng: 11.3472, accuracyMeters: 12, ageSeconds: 30 };
let block = await answer('Tre aperte:\\n\\n- [Farmacia Centrale — Via Castiglione 9](https://www.google.com/maps?q=44.4910,11.3480)\\n- [Farmacia del Corso — Strada Maggiore 31](https://www.google.com/maps?q=44.4935,11.3530)\\n- [Farmacia San Vitale — Via San Vitale 72](https://www.google.com/maps?q=44.4950,11.3570)');
let list = block.querySelector('.place-list');
assert.ok(list, block.innerHTML);
assert.equal(list.querySelectorAll('a.place-row').length, 3);
assert.deepEqual([...list.querySelectorAll('.place-row-num')].map((n) => n.textContent), ['1', '2', '3']);
assert.equal(block.querySelector('ul'), null);
// La distanza sta a destra, fuori dall'indirizzo che i puntini tagliano.
await tick(20);
const first = list.querySelector('a.place-row');
assert.match(first.querySelector('.place-row-meta').textContent, /^\\d+ m$/);
assert.equal(first.querySelector('.place-address').textContent, 'Via Castiglione 9');

// Due paragrafi di fila fanno lo stesso; uno in mezzo a un testo no.
block = await answer('[A — via 1](https://www.google.com/maps?q=45,9)\\n\\n[B — via 2](https://www.google.com/maps?q=45.1,9.1)\\n\\nE poi:\\n\\n[C — via 3](https://www.google.com/maps?q=45.2,9.2)');
assert.equal(block.querySelectorAll('.place-list').length, 1);
assert.equal(block.querySelectorAll('.place-list a.place-row').length, 2);
assert.equal(block.querySelectorAll('a.place-card').length, 1);

// Una lista con una voce che non e' un posto resta una lista.
block = await answer('- [A](https://www.google.com/maps?q=45,9)\\n- una cosa da ricordare');
assert.equal(block.querySelector('.place-list, .place-card'), null);
assert.ok(block.querySelector('ul'));
"""
    )


@requires_jsdom
def test_a_tap_goes_to_maps_not_to_the_browser() -> None:
    run_home(
        _HOME
        + """
const calls = [];
window.JennyNative = { openMap: (...a) => calls.push(a) };
const block = await answer('[Piazza Santo Stefano — 40125 Bologna BO](' + LINK + ')');
const e = tap(block.querySelector('.place-name'));
assert.equal(e.defaultPrevented, true);
assert.deepEqual(calls, [['44.4924', '11.3472', 'Piazza Santo Stefano, 40125 Bologna BO', LINK]]);
assert.deepEqual(opened, [], 'aperto anche nel browser');

// Fuori da Android il link si apre fuori dalla pagina.
delete window.JennyNative;
tap(block.querySelector('a.place-card'));
assert.deepEqual(opened, [LINK]);
"""
    )


@requires_jsdom
def test_here_distance_and_the_map_arrive_later() -> None:
    run_home(
        _HOME
        + """
routes['/api/location/last'] = { lat: 44.49241, lng: 11.34721, accuracyMeters: 12, ageSeconds: 30 };
const asked = [];
hooks.fetch = (u) => {
  const m = /^\\/api\\/map\\/tile\\/(\\d+)\\/(\\d+)\\/(\\d+)$/.exec(u.pathname);
  if (!m) return undefined;
  asked.push(m.slice(1).join('/'));
  return { ok: true, status: 200, blob: async () => new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' }) };
};
let block = await answer('[Piazza Santo Stefano — 40125 Bologna BO](' + LINK + ')');
await tick(30);
let card = block.querySelector('.place-card');
assert.ok(card.classList.contains('is-here'), card.className);
assert.equal(card.querySelector('.place-meta').textContent, 'You are here · ± 12 m');
assert.ok(card.classList.contains('has-map'), card.className);
const imgs = card.querySelectorAll('.place-tiles img');
assert.equal(imgs.length, asked.length);
assert.ok(asked.every((t) => t.startsWith('16/')), asked.join(' '));
assert.ok(card.querySelector('.place-attribution'));

// Un posto lontano: la distanza. E se le tessere non arrivano, niente mappa.
hooks.fetch = (u) => (u.pathname.startsWith('/api/map/tile/') ? { ok: false, status: 502 } : undefined);
block = await answer('[Trattoria da Bruna — Via del Pratello 48](https://www.google.com/maps?q=44.4957,11.3330)');
await tick(30);
card = block.querySelector('.place-card');
assert.equal(card.classList.contains('is-here'), false);
assert.match(card.querySelector('.place-meta').textContent, /^1\\.\\d km from you$/);
assert.equal(card.classList.contains('has-map'), false);
assert.equal(card.querySelector('.place-tiles'), null);
"""
    )


def test_both_chats_draw_places_and_route_their_taps() -> None:
    """La casa e' provata sopra in jsdom; l'officina ha lo stesso patto, qui
    misurato sul sorgente: ogni risposta finita passa da ``renderPlacesIn``, e
    il tocco su una scheda viene prima della regola dei link."""
    for name, renders in (("home-chat.js", 3), ("mobile-chat.js", 4)):
        src = (ASSETS / name).read_text(encoding="utf-8")
        assert len(re.findall(r"renderPlacesIn\(", src)) == renders, name
        place = src.index("openPlaceFromClick(e)")
        link = src.index("const link = contentLinkOf(e.target)")
        assert place < link, f"{name}: il tocco su una scheda deve venire prima dei link"
