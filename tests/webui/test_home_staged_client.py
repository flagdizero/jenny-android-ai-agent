"""La scheda delle bozze da approvare, nella casa vera.

Un subagent che ha letto qualcosa da fuori non scrive da solo i file che Jenny
carica senza nessuno davanti: la modifica resta in bozza e la casa mostra una
scheda con **Installa** / **Scarta**. Qui si fissa che la scheda compare
all'apertura e al frame ``staged_changed``, che niente si installa senza un
tocco, e che un conflitto lo dice invece di scrivere.
"""

from __future__ import annotations

import json
from pathlib import Path

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, until, frame, sent, rpcAnswers, rpcFailed, toasts, locales } from './boot.mjs';
const S = locales.en.home.staged;
const draft = (over = {}) => ({ id: 'd1', target: 'USER.md', sources: ['web_fetch:evil.example'],
  created_ms: 1, new_file: false, target_sha256: 'abc', diff: '--- a\\n+++ b\\n+- check x\\n',
  truncated: false, ...over });
let pending = [draft()];
rpcAnswers['staged.list'] = () => ({ ok: true, staged: pending });
const card = () => document.querySelector('#home-thread > .home-live > .home-staged');
const rpcs = (m) => sent.filter((x) => x.type === 'rpc' && x.method === m);
const text = (node) => node.textContent.replace(/\\s+/g, ' ').trim();
"""


def test_the_card_appears_and_nothing_installs_without_a_tap() -> None:
    run_home(_HEAD + """
await boot();
await until(card, { msg: 'la scheda compare all\\'apertura' });
assert.match(text(card()), /USER\\.md/);
assert.match(text(card()), /evil\\.example/);
assert.equal(rpcs('staged.install').length, 0, 'niente si installa da solo');
assert.equal(card().querySelector('.home-staged-diff'), null, 'la diff parte chiusa');
card().querySelector('.home-staged-toggle').click();
assert.match(card().querySelector('.home-staged-diff').textContent, /check x/);
""")


def test_install_sends_the_sha_it_showed_and_the_card_goes() -> None:
    run_home(_HEAD + """
await boot();
await until(card);
rpcAnswers['staged.install'] = (p) => { pending = []; return { ok: true }; };
const install = [...card().querySelectorAll('button')].find((b) => b.textContent === S.install);
install.click();
await until(() => card() === null && toasts().some((t) => t.includes('USER.md')));
const call = rpcs('staged.install')[0];
assert.deepEqual(call.params, { id: 'd1', target_sha256: 'abc' });
assert.equal(card(), null, 'senza bozze la scheda sparisce');
assert.ok(toasts().some((t) => t.includes('USER.md')));
""")


def test_a_conflict_says_so_and_keeps_the_card() -> None:
    run_home(_HEAD + """
await boot();
await until(card);
rpcAnswers['staged.install'] = () => rpcFailed('conflict', 'changed');
[...card().querySelectorAll('button')].find((b) => b.textContent === S.install).click();
await until(() => toasts().includes(S.conflict));
assert.ok(toasts().includes(S.conflict), toasts());
assert.ok(card(), 'la bozza resta da guardare');
""")


def test_discard_and_the_frame_refresh() -> None:
    run_home(_HEAD + """
pending = [];
await boot();
await tick(30);
assert.equal(card(), null);
pending = [draft({ id: 'd2', target: 'skills/x/SKILL.md', new_file: true, target_sha256: null })];
frame({ event: 'staged_changed', count: 1 });
await until(card);
assert.match(text(card()), /New file: skills\\/x\\/SKILL\\.md/);
rpcAnswers['staged.discard'] = () => { pending = []; return { ok: true }; };
[...card().querySelectorAll('button')].find((b) => b.textContent === S.discard).click();
await until(() => card() === null);
assert.deepEqual(rpcs('staged.discard')[0].params, { id: 'd2' });
assert.equal(card(), null);
""")


def test_long_sources_are_shortened_and_internal_files_hidden() -> None:
    run_home(_HEAD + """
pending = [draft({ sources: ['file:wikis/piante/.jenny/tool-results/call_abcdef0123456789.txt',
  'web_fetch:a.example', 'web_search:b', 'web_fetch:c.example', 'web_fetch:d.example', 'web_fetch:e.example'] })];
await boot();
await until(card);
const why = text(card().querySelector('.home-staged-why'));
assert.ok(!why.includes('tool-results'), 'i file interni non si nominano');
assert.match(why, /a\\.example, web_search:b, web_fetch:c\\.example and 2 more/);
pending = [draft({ id: 'd2', sources: ['file:.jenny/tool-results/x.txt'] })];
frame({ event: 'staged_changed', count: 1 });
await until(() => card() && text(card()).includes(S.toolResults));
assert.match(text(card().querySelector('.home-staged-why')), new RegExp(S.toolResults));
""")


def test_the_card_rows_wrap_long_paths() -> None:
    """Il foglio vero applicato alla scheda vera: lo stile calcolato della riga
    va a capo dentro le parole (un percorso lungo non esce dal bordo)."""
    css = (Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
           / "home-style.css").read_text(encoding="utf-8")
    run_home(_HEAD + f"""
const style = document.createElement('style');
style.textContent = {json.dumps(css)};
document.head.appendChild(style);
await boot();
await until(card);
const row = card().querySelector('.home-staged-row');
const cs = getComputedStyle(row);
assert.equal(cs.overflowWrap || cs.getPropertyValue('overflow-wrap'), 'anywhere');
assert.equal(cs.minWidth, '0px');
""")
