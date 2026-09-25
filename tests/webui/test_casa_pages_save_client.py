"""Salvare le pagine della casa passa dal WebSocket, non da una GET.

D4 della revisione profonda: ``api.savePages`` mandava l'elenco nell'indirizzo
di ``/api/casa/schermate/set`` (il nome di allora), che scriveva ``config.json``. Ora e' il comando
RPC ``home.pages.set``; il chiamante (``casa-pagine.js::salva``) deve
ricevere lo stesso ``{ok, pages, order}`` e un errore lanciato se fallisce.

In node sui file veri: ``api-client.js`` e ``rpc-client.js`` si importano davvero,
``ws-manager.js`` e' finto e registra le richieste.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from support.js_harness import ASSETS, requires_node, run_module

pytestmark = requires_node

_WS_FINTO = """
export const richieste = [];
export const wsManager = {
  esito: null,
  request(method, params) {
    richieste.push([method, params]);
    return this.esito(method, params);
  },
};
"""

_ENTRY = """
import assert from 'node:assert/strict';
import { api } from './api-client.js';
import { wsManager, richieste } from './ws-manager.js';

let fetchate = 0;
globalThis.fetch = async () => { fetchate += 1; throw new Error('niente HTTP'); };

const pagine = [{ id: 'p1', kind: 'app', ref: 'orto' }];
const order = ['p1', 'app', 'chat', 'notebooks', 'settings'];

wsManager.esito = async (method, params) => ({ ok: true, ...params });
const salvate = await api.savePages(pagine, order);
assert.deepEqual(salvate, { ok: true, pages: pagine, order });
assert.deepEqual(richieste, [['home.pages.set', { pages: pagine, order }]]);
assert.equal(fetchate, 0, 'la scrittura non passa piu da /api/');

wsManager.esito = async () => {
  const err = new Error('duplicate page id');
  err.code = 'bad_request';
  throw err;
};
await assert.rejects(api.savePages(pagine, order), (err) => err.code === 'bad_request');
console.log('ok');
"""


def test_saving_the_pages_is_an_rpc_command(tmp_path: Path) -> None:
    for nome in ("api-client.js", "rpc-client.js"):
        shutil.copy(ASSETS / "shared" / nome, tmp_path / nome)
    (tmp_path / "ws-manager.js").write_text(_WS_FINTO, encoding="utf-8")
    (tmp_path / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    entry = tmp_path / "entry.js"
    entry.write_text(_ENTRY, encoding="utf-8")
    assert run_module(entry).strip() == "ok"
