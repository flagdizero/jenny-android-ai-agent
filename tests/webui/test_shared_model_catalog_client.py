"""`shared/model-catalog.js`: una regola per tenere e riprovare un elenco.

La casa richiedeva un elenco fallito a ogni tocco sulla marca; l'officina una
volta per visita, ma richiedeva a ogni visita anche «serve una chiave». Resta:
si tiene finche' il provider resta quello, e uno non arrivato si riprova alla
visita dopo.
"""

from __future__ import annotations

import re

from support.js_harness import ASSETS, requires_node, run_js

# Senza gli ``import``: quel che servono (``switchModel``) qui non si prova.
CATALOG_JS = re.sub(
    r"^import .*?;\n", "", (ASSETS / "shared" / "model-catalog.js").read_text(encoding="utf-8"),
    flags=re.M,
)

pytestmark = requires_node


def test_the_rule() -> None:
    run_js(
        "import assert from 'node:assert/strict';\n"
        + CATALOG_JS.replace("export ", "")
        + """
console.warn = () => {};
const answers = { og: { status: 'error', models: [] }, sec: { status: 'not_configured', models: [] } };
const asked = [];
const c = new ModelCatalogs(async (name) => { asked.push(name); return answers[name]; });
const og = { name: 'og', format: 'openai_compat', api_base: 'https://og', api_key_hint: '' };
const sec = { name: 'sec', format: 'anthropic', api_base: '', api_key_hint: '' };

assert.equal(c.get(og).status, 'loading', 'mai chiesto si legge come «sto chiedendo»');
const first = c.load(og);
assert.equal(c.get(og).status, 'loading');
assert.equal(await c.load(og), false, 'una lettura in volo non ne fa partire una seconda');
assert.equal(await first, true);
assert.equal(c.get(og).status, 'error');
assert.equal(await c.load(og), false, 'un errore non si richiede nella stessa visita');
c.newVisit();
answers.og = { status: 'available', models: [{ id: 'm1' }, 'm2'] };
assert.equal(await c.load(og), true, 'alla visita dopo si riprova');
assert.deepEqual(c.get(og).models, ['m1', 'm2']);

await c.load(sec);
c.newVisit();
assert.equal(await c.load(sec), false, '«serve una chiave» si tiene anche alla visita dopo');
const withKey = { ...sec, api_key_hint: 'sk-...9f2c' };
answers.sec = { status: 'available', models: ['s1'] };
assert.equal(await c.load(withKey), true, 'con la chiave e\\u2019 un altro elenco');
c.forget(withKey);
assert.equal(await c.load(withKey), true, 'dimenticato, si richiede');
assert.deepEqual(asked, ['og', 'og', 'sec', 'sec', 'sec']);

const failing = new ModelCatalogs(async () => { throw new Error('rete'); });
await failing.load(og);
assert.equal(failing.get(og).status, 'error');
"""
    )
