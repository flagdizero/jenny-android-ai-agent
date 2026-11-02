"""Cancellare, rinominare e copiare dal file manager passa dal WebSocket.

``api.deleteWorkspace``/``renameWorkspace``/
``copyWorkspace`` mandavano una GET a ``/api/workspace/*`` che scriveva sul
disco. Ora sono i comandi RPC ``workspace.delete``/``rename``/``copy``, con la
stessa firma — ``mobile-workspace.js`` li chiama da ``rpc`` — e un errore
lanciato col messaggio del server.

Secondo pezzo: ``rpc-client.js`` apre il socket se nessuno l'ha ancora aperto.
Nell'officina lo apre il controller della chat, che nasce pigro: nell'onboarding
(dove ora passano le chiavi del provider) o sul file manager aperto per
primo, ogni comando rifiutava con «gateway offline».

In node sui file veri: ``api-client.js`` e ``rpc-client.js`` si importano davvero,
``ws-manager.js`` e' finto.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from support.js_harness import ASSETS, requires_node, run_module

pytestmark = requires_node

_FAKE_WS = """
export const requests = [];
export const wsManager = new (class extends EventTarget {
  constructor() { super(); this.chatWs = null; this.connects = 0; this.outcome = null; }
  connectChat() {
    this.connects += 1;
    if (this.chatWs) return;
    this.chatWs = { readyState: 0 };
    setTimeout(() => {
      this.chatWs.readyState = 1;
      this.dispatchEvent(new Event('chat:open'));
    }, 5);
  }
  request(method, params) {
    if (this.chatWs?.readyState !== 1) return Promise.reject(new Error('gateway offline'));
    requests.push([method, params]);
    return this.outcome(method, params);
  }
})();
"""

_ENTRY = """
import assert from 'node:assert/strict';
import { rpc } from './rpc-client.js';
import { wsManager, requests } from './ws-manager.js';

let fetched = 0;
globalThis.fetch = async () => { fetched += 1; throw new Error('niente HTTP'); };

wsManager.outcome = async (method, params) => ({ success: true, ...params });

// Socket mai aperto: il comando lo apre e aspetta, invece di rifiutare.
await rpc.deleteWorkspace('note/a.md');
assert.equal(wsManager.connects, 1);
await rpc.renameWorkspace('a.md', 'b.md');
await rpc.copyWorkspace('b.md');
await rpc.copyWorkspace('b.md', 'c.md');
assert.equal(wsManager.connects, 1, 'a socket aperto non si riconnette');
assert.deepEqual(requests, [
  ['workspace.delete', { path: 'note/a.md' }],
  ['workspace.rename', { old_path: 'a.md', new_path: 'b.md' }],
  ['workspace.copy', { path: 'b.md' }],
  ['workspace.copy', { path: 'b.md', dest: 'c.md' }],
]);
assert.equal(fetched, 0, 'le scritture del file manager non passano piu da /api/');

wsManager.outcome = async () => {
  const err = new Error('workspace deletes are disabled');
  err.code = 'forbidden';
  throw err;
};
await assert.rejects(
  rpc.deleteWorkspace('x'),
  (err) => err.code === 'forbidden' && err.message === 'workspace deletes are disabled',
);
console.log('ok');
"""


def test_a_taken_name_is_said_in_the_users_language() -> None:
    """Rinomina e copia non sovrascrivono piu': il rifiuto ``name_taken``
    arriva all'utente tradotto, gli altri col perche' del server."""
    import json

    from support.js_harness import function, locale, run_js

    source = (ASSETS / "mobile-workspace.js").read_text(encoding="utf-8")
    script = f"""
import assert from 'node:assert/strict';
const T = {json.dumps(locale("it"))};
const i18n = {{ t: (k) => k.split('.').reduce((o, p) => o?.[p], T) ?? k }};
{function(source, "workspaceErrorText")}
const taken = new Error('the destination already exists');
taken.code = 'name_taken';
assert.equal(workspaceErrorText(taken), T.workspace.nameTaken);
const other = new Error('permission denied');
other.code = 'forbidden';
assert.equal(workspaceErrorText(other), T.workspace.error + 'permission denied');
console.log('ok');
"""
    assert run_js(script).strip() == "ok"


def test_the_file_manager_writes_are_rpc_commands(tmp_path: Path) -> None:
    for name in ("api-client.js", "rpc-client.js"):
        shutil.copy(ASSETS / "shared" / name, tmp_path / name)
    (tmp_path / "ws-manager.js").write_text(_FAKE_WS, encoding="utf-8")
    (tmp_path / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    entry = tmp_path / "entry.js"
    entry.write_text(_ENTRY, encoding="utf-8")
    assert run_module(entry).strip() == "ok"
