"""Chiave del provider, token Telegram e password SSH non passano da un URL.

``api.getProviderModels``/``updateProvider``/
``saveTelegramToken``/``saveSshHost`` mettevano il segreto nella query di una
GET. Ora sono comandi RPC, chiamati da ``rpc`` direttamente dal 02/11/2026 (``settings.provider.models``/``update``,
``telegram.save``, ``ssh.host.save``) con la stessa firma: qui si prova che
nessuna ``fetch`` parte, che i parametri arrivano com'erano (vuoti scartati per
il provider, ``null`` → ``''`` per l'SSH, ``password`` omessa se omessa) e che
un rifiuto porta il messaggio del server.

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
import { rpc } from './rpc-client.js';
import { wsManager, requests } from './ws-manager.js';

const fetched = [];
globalThis.fetch = async (url) => { fetched.push(String(url)); throw new Error('niente HTTP'); };

wsManager.outcome = async (method) => ({ method });

await rpc.providerModels({ provider: 'openai', apiKey: 'sk-segreta', apiBase: '', format: 'openai_compat' });
await rpc.updateProvider({
  name: 'p', format: 'openai_compat', api_key: 'sk-segreta', api_base: '',
  ca_bundle: null, ca_bundle_clear: '',
});
await rpc.saveTelegramToken('123:segreto');
await rpc.saveSshHost({ alias: 'nas', host: 'h', username: 'u', description: null,
  auth: 'password', password: 'segreta' });
await rpc.saveSshHost({ alias: 'nas', host: 'h', username: 'u', auth: 'password' });
await rpc.saveOnboarding({ provider: 'openai', format: 'openai_compat', api_key: 'sk-prima',
  model: 'gpt-x', bot_name: 'Jenny', locale: 'it' });

assert.deepEqual(requests, [
  ['settings.provider.models',
    { provider: 'openai', api_key: 'sk-segreta', api_base: '', format: 'openai_compat' }],
  ['settings.provider.update', { name: 'p', format: 'openai_compat', api_key: 'sk-segreta' }],
  ['telegram.save', { token: '123:segreto' }],
  ['ssh.host.save', { alias: 'nas', host: 'h', username: 'u', description: '',
    auth: 'password', password: 'segreta' }],
  ['ssh.host.save', { alias: 'nas', host: 'h', username: 'u', auth: 'password' }],
  ['onboarding.save', { provider_name: 'openai', format: 'openai_compat', api_key: 'sk-prima',
    api_base: '', model: 'gpt-x', bot_name: 'Jenny', bot_icon: '', locale: 'it' }],
]);
assert.deepEqual(fetched, [], 'nessun segreto in un URL');

wsManager.outcome = async () => {
  const err = new Error('telegram token rejected: Unauthorized');
  err.code = 'bad_request';
  throw err;
};
await assert.rejects(rpc.saveTelegramToken('x'), /telegram token rejected: Unauthorized/);
await assert.rejects(rpc.updateProvider({ name: 'p' }), (e) => e.code === 'bad_request');
console.log('ok');
"""


def test_the_secrets_travel_in_rpc_frames(tmp_path: Path) -> None:
    for name in ("api-client.js", "rpc-client.js"):
        shutil.copy(ASSETS / "shared" / name, tmp_path / name)
    (tmp_path / "ws-manager.js").write_text(_FAKE_WS, encoding="utf-8")
    (tmp_path / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    entry = tmp_path / "entry.js"
    entry.write_text(_ENTRY, encoding="utf-8")
    assert run_module(entry).strip() == "ok"
