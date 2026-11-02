"""``formDialog`` (``shared/dialog.js``): un modulo in una finestra, che non si
chiude a metà.

Erano cinque finestre costruite a mano. Quella dell'host SSH in officina non
aveva nessuna difesa: Indietro (o Esc) durante il salvataggio la smontava sotto
la richiesta, e un secondo tocco su Salva ne mandava una seconda. Qui gira la
funzione vera, sul DOM della casa (jsdom).
"""

from __future__ import annotations

import json

from support.home_dom import UI, requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = f"""
import assert from 'node:assert/strict';
import {{ boot, tick, $ }} from './boot.mjs';
await boot();
const {{ formDialog, dismissTopDialog }} = await import({json.dumps(str(UI))} + '/assets/shared/dialog.js');
const ok = (d) => d.querySelector('[data-form-ok]');
const cancel = (d) => d.querySelector('[data-form-cancel]');
"""


def test_a_save_in_flight_refuses_back_and_a_second_tap() -> None:
    run_home(_HEAD + """
let release;
let calls = 0;
const asked = formDialog({
  id: 'prova-dialog', title: 'Titolo', okText: 'Salva',
  bodyHtml: '<input type="password" id="pw" value="segreta">',
  onOk: () => { calls += 1; return new Promise((r) => { release = r; }); },
});
await tick(5);
const d = $('prova-dialog');
assert.equal(d.querySelector('.oc-dialog-title').textContent, 'Titolo');
ok(d).click();
await tick(5);
assert.equal(d.dataset.busy, '1');
assert.ok(ok(d).disabled && cancel(d).disabled, 'i bottoni restano premibili in volo');
ok(d).click();
cancel(d).click();
dismissTopDialog();
await tick(5);
assert.equal(calls, 1, 'un secondo tocco in volo ha salvato di nuovo');
assert.ok($('prova-dialog'), 'Indietro ha smontato la finestra durante il salvataggio');
const pw = d.querySelector('#pw');
release('fatto');
assert.equal(await asked, 'fatto');
assert.equal($('prova-dialog'), null);
assert.equal(pw.value, '', 'la password e\\u2019 rimasta nel DOM');
""")


def test_undefined_keeps_it_open_and_cancel_is_a_null() -> None:
    run_home(_HEAD + """
let n = 0;
const asked = formDialog({ id: 'prova-dialog', title: 'T', onOk: () => (++n < 2 ? undefined : 'ok') });
await tick(5);
const d = $('prova-dialog');
ok(d).click();
await tick(5);
assert.ok($('prova-dialog'), 'un campo da correggere ha chiuso la finestra');
assert.equal(d.dataset.busy, undefined);
cancel(d).click();
assert.equal(await asked, null);

const thrown = formDialog({ id: 'prova-dialog', title: 'T', onOk: () => { throw new Error('rete'); } });
await tick(5);
console.error = () => {};
ok($('prova-dialog')).click();
await tick(5);
assert.ok($('prova-dialog'), 'un errore ha chiuso la finestra');
assert.equal($('prova-dialog').dataset.busy, undefined, 'un errore ha lasciato la finestra occupata');
$('prova-dialog').close();
assert.equal(await thrown, null, 'una chiusura diretta lascia la Promise appesa');
""")


def test_without_a_cancel_text_back_does_not_close_it() -> None:
    run_home(_HEAD + """
formDialog({ id: 'prova-dialog', title: 'Riavvia', cancelText: null, onOk: () => undefined });
await tick(5);
assert.equal(cancel($('prova-dialog')), null);
assert.equal(dismissTopDialog(), true, 'la pressione non e\\u2019 stata consumata');
assert.ok($('prova-dialog').open, 'il riavvio si e\\u2019 chiuso');
""")
