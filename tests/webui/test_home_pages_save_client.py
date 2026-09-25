"""Salvare le pagine della casa passa dal WebSocket, non da una GET.

D4 della revisione profonda: ``api.savePages`` mandava l'elenco nell'indirizzo
di ``/api/casa/schermate/set`` (il nome di allora), che scriveva ``config.json``. Ora e' il comando
RPC ``home.pages.set``; il chiamante (``home-pages.js::save``) deve
ricevere lo stesso ``{ok, pages, order}`` e un errore lanciato se fallisce.

In node sui file veri: ``api-client.js`` e ``rpc-client.js`` si importano davvero,
``ws-manager.js`` e' finto e registra le richieste.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from support.js_harness import ASSETS, requires_node, run_module

pytestmark = requires_node

_FAKE_WS = """
export const requests = [];
export const wsManager = {
  outcome: null,
  request(method, params) {
    requests.push([method, params]);
    return this.outcome(method, params);
  },
};
"""

_ENTRY = """
import assert from 'node:assert/strict';
import { api } from './api-client.js';
import { wsManager, requests } from './ws-manager.js';

let fetched = 0;
globalThis.fetch = async () => { fetched += 1; throw new Error('niente HTTP'); };

const homePages = [{ id: 'p1', kind: 'app', ref: 'orto' }];
const order = ['p1', 'app', 'chat', 'notebooks', 'settings'];

wsManager.outcome = async (method, params) => ({ ok: true, ...params });
const saved = await api.savePages(homePages, order);
assert.deepEqual(saved, { ok: true, pages: homePages, order });
assert.deepEqual(requests, [['home.pages.set', { pages: homePages, order }]]);
assert.equal(fetched, 0, 'la scrittura non passa piu da /api/');

wsManager.outcome = async () => {
  const err = new Error('duplicate page id');
  err.code = 'bad_request';
  throw err;
};
await assert.rejects(api.savePages(homePages, order), (err) => err.code === 'bad_request');
console.log('ok');
"""


def test_saving_the_pages_is_an_rpc_command(tmp_path: Path) -> None:
    for name in ("api-client.js", "rpc-client.js"):
        shutil.copy(ASSETS / "shared" / name, tmp_path / name)
    (tmp_path / "ws-manager.js").write_text(_FAKE_WS, encoding="utf-8")
    (tmp_path / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    entry = tmp_path / "entry.js"
    entry.write_text(_ENTRY, encoding="utf-8")
    assert run_module(entry).strip() == "ok"
