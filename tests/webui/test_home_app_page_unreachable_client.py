"""Una pagina-app che non si apre lo dice, e offre di riprovare.

Se il gateway non dava il token dell'app, la pagina restava bianca e muta:
indistinguibile da un'app vuota (giro UX del 10/10/2026).
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom


def test_an_app_page_that_cannot_open_says_so_and_retries() -> None:
    run_home("""
import assert from 'node:assert/strict';
import { boot, tick, routes, hooks, failed, $ } from './boot.mjs';
routes['/api/home/pages'] = {
  pages: [{ id: 'p1', kind: 'app', ref: 'spesa' }],
  order: ['app', 'chat', 'p1', 'notebooks', 'settings'],
  fixed: ['app', 'chat', 'notebooks', 'settings'], max: 8,
};
let down = true;
hooks.fetch = async (u) => (down && u.pathname.endsWith('/spesa/token') ? failed(503) : undefined);
routes['/api/webui/apps/spesa/token'] = { token: 't' };
const app = await boot();
await tick(50);
app.homePages.goToId('p1');
await tick(400);
const panel = app.homePages.panelOf(app.homePages.indexOf('p1'));
const card = panel.querySelector('.home-page-gone');
assert.ok(card, 'la pagina e\\u2019 rimasta bianca e muta');
assert.equal(panel.querySelector('iframe'), null);
down = false;
card.querySelector('button').click();
await tick(100);
assert.equal(panel.querySelector('.home-page-gone'), null, 'l\\u2019avviso e\\u2019 rimasto dopo Riprova');
assert.ok(panel.querySelector('iframe'), 'Riprova non ha aperto l\\u2019app');
""")
