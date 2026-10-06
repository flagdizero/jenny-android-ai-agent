"""La prova di un'app, il cestino e «Metti in un quaderno…», nella casa vera (jsdom).

Un'app in prova non si apre: il tocco apre la sua prova, in parole, con
Tienila / Scarta / Cambia. Un'app tenuta si mette in un quaderno con tre
domande al piu'. Il cestino c'e' in fondo alla pagina App solo quando non e'
vuoto.
"""

from __future__ import annotations

import json

from support.home_dom import requires_jsdom, run_home
from support.js_harness import requires_node

pytestmark = requires_node

APPS = {"apps": [
    {"slug": "waterbot", "name": "WaterBot", "broken": False, "icon": "ti-droplet",
     "state": "trial", "section": None,
     "trial": {"reaches": ["192.168.1.40"], "writes": [], "skills": [], "actions": [], "new": [],
               "broken": False, "error": "",
               "reading": {"value": 38, "unit": "%", "label": "Moisture", "source": "", "at": ""}}},
    {"slug": "etf", "name": "ETF", "broken": False, "icon": "ti-chart-line", "state": "live",
     "section": {"params": {}}},
]}

_SETUP = """
import assert from 'node:assert/strict';
import { boot, tick, routes, sent, rpcAnswers, $ } from './boot.mjs';
routes['/api/webui/apps'] = __APPS__;
routes['/api/projects'] = { dir: 'wikis', projects: [{ name: 'soldi', modified: '2026-10-06' }] };
routes['/api/board'] = { exists: true, cards: [{ id: 'conto', title: 'Conto', pos: { x: 1, y: 1 } }],
  notes: [], connections: [], suggestions: [], since: [], offboard: 0, pages: 0 };
for (const m of ['apps.keep', 'apps.discard', 'board.card.add', 'board.section.add']) {
  rpcAnswers[m] = () => ({ ok: true });
}
const app = await boot();
await tick(20);
const actions = app.appsActions();
await app.appsSource().awaitJennyApps();
const rpcs = (method) => sent.filter((m) => m.type === 'rpc' && m.method === method);
const buttons = () => [...$('jenny-app-sheet-actions').querySelectorAll('button')];
"""


def _run(script: str, apps: dict = APPS) -> None:
    run_home(_SETUP.replace("__APPS__", json.dumps(apps)) + script)


@requires_jsdom
def test_an_app_on_trial_opens_its_trial_card_in_plain_words() -> None:
    _run("""
      await actions.openApp('waterbot');
      await tick(10);
      assert.ok($('jenny-app-sheet').open, 'la prova non si apre');
      assert.equal(document.querySelector('.app-frame-overlay'), null, 'lo schermo non si apre');
      const text = $('jenny-app-sheet-actions').textContent;
      assert.match(text, /38 %/);
      assert.match(text, /192\\.168\\.1\\.40/);
      assert.match(text, /Writes nothing/);
      assert.deepEqual(buttons().map((b) => b.textContent), ['Keep it', 'Discard', 'Change']);
      buttons()[0].click();
      await tick(40);
      assert.deepEqual(rpcs('apps.keep').at(-1).params, { slug: 'waterbot' });
      assert.equal($('jenny-app-sheet').open, false);
    """)


@requires_jsdom
def test_a_kept_app_goes_into_a_notebook_as_its_own_card() -> None:
    _run("""
      await actions.showPlaceSheet('etf');
      await tick(20);
      assert.deepEqual(buttons().map((b) => b.textContent.trim()), ['soldi']);
      buttons()[0].click();
      await tick(30);
      // Mai dentro un'altra scheda: da sola, o accanto con un filo.
      assert.deepEqual(buttons().map((b) => b.textContent.trim()),
        ['On its own', 'Next to “Conto”, tied by a thread']);
      buttons()[0].click();
      await tick(30);
      let call = rpcs('board.card.add').at(-1);
      assert.equal(call.params.wiki, 'soldi');
      assert.deepEqual(call.params.app, { slug: 'etf', params: {} });
      assert.equal(call.params.link, undefined);
      await actions.showPlaceSheet('etf');
      await tick(20);
      buttons()[0].click();
      await tick(30);
      buttons()[1].click();
      await tick(30);
      call = rpcs('board.card.add').at(-1);
      assert.equal(call.params.link, 'conto');
      assert.equal(rpcs('board.section.add').length, 0);
    """)


@requires_jsdom
def test_the_trash_row_shows_only_when_something_is_there() -> None:
    _run("""
      rpcAnswers['apps.trash'] = () => ({ ok: true, trash: [
        { id: 'orto-20261006T101500Z', slug: 'orto', name: 'Orto', at: '2026-10-06T10:15:00+00:00',
          records: 4 } ] });
      rpcAnswers['apps.restore'] = () => ({ ok: true, slug: 'orto' });
      await app.appsTrash().refresh();
      const row = $('launcher-trash');
      assert.equal(row.hidden, false);
      assert.match(row.textContent, /1/);
      row.click();
      await tick(30);
      assert.ok($('apps-trash-sheet').open);
      assert.equal($('apps-trash-sheet-close').textContent, 'Close', 'il bottone resta in italiano');
      const [restore] = $('apps-trash-sheet-body').querySelectorAll('button');
      restore.click();
      await tick(30);
      assert.deepEqual(rpcs('apps.restore').at(-1).params, { id: 'orto-20261006T101500Z' });
    """)
