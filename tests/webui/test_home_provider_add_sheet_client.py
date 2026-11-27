"""«Aggiungi chi risponde», il foglio intero dentro la casa vera.

``test_home_provider_add_client.py`` prova le due funzioni pure. Qui si guida
il foglio com'e', nella casa vera (``support.home_dom``), con un finto gateway
che risponde a ``settings.provider.models`` e ``settings.provider.add``. Si
misura cio' che sta **fra** i passi:

- cosa arriva al server (i campi non ancora salvati, la prova, «Salva lo stesso»);
- dove si torna quando qualcosa non va: una chiave rifiutata gia' dall'elenco,
  un nome preso, una prova fallita;
- una risposta in ritardo che non deve dipingere un passo da cui sei uscito;
- l'attivazione, che passa dal salvataggio di sempre (``/api/settings/update``),
  e solo se l'hai chiesta.

I nomi e le chiavi sono inventati.
"""

from __future__ import annotations

from support.home_dom import requires_jsdom, run_home

pytestmark = requires_jsdom

_HEAD = """
import assert from 'node:assert/strict';
import { boot, tick, until, routes, hooks, ok, rpcAnswers, rpcSilent, sent, frame, locales, $ }
  from './boot.mjs';
const T = locales.en.home.model.add;
const P = locales.en.onboarding.probe;
const NEXT = locales.en.onboarding.next;

const base = {
  agent: { bot_name: 'Jenny', model: 'llama' }, default_provider: 'groq',
  providers: [{ name: 'groq', format: 'openai_compat', api_key_hint: 'gsk-...1234' }],
  version: {}, backup: {},
};
routes['/api/settings'] = base;
const MODELS = Array.from({ length: 12 }, (_, i) => ({ id: `m-${i + 1}` }));
const listed = [];
rpcAnswers['settings.provider.models'] = (p) => {
  listed.push(p);
  if (p.api_key === 'bad') return { status: 'rejected', models: [] };
  if (p.api_key === 'nolist') return { status: 'error', models: [] };
  if (p.provider === 'groq') return { status: 'available', models: [{ id: 'llama' }] };
  return { status: 'available', models: MODELS };
};
const added = [];
let addAnswer = (p) => ({ ok: true, ...base, providers: [...base.providers,
  { name: p.name, format: p.format, api_key_hint: 'sk-...xxxx' }] });
rpcAnswers['settings.provider.add'] = (p) => { added.push(p); return addAnswer(p); };
const updates = [];
hooks.fetch = async (u) => {
  if (u.pathname !== '/api/settings/update') return undefined;
  const q = Object.fromEntries(u.searchParams);
  updates.push(q);
  const after = JSON.parse(JSON.stringify(added.length ? addAnswer(added.at(-1)) : base));
  after.default_provider = q.default_provider;
  after.agent.model = q.model;
  return ok(after);
};

const sheet = () => $('home-provider-sheet');
const buttons = () => [...sheet().querySelectorAll('button')];
const press = (text) => {
  const b = buttons().find((x) => x.textContent.trim() === text);
  assert.ok(b, `nessun bottone «${text}»: ` + buttons().map((x) => x.textContent.trim()).join(' | '));
  assert.equal(b.disabled, false, `«${text}» e\\u2019 spento`);
  b.click();
};
const has = (text) => buttons().some((x) => x.textContent.trim() === text);
const disabled = (text) => buttons().find((x) => x.textContent.trim() === text)?.disabled;
const type = (id, value) => {
  const i = $(id);
  i.value = value;
  i.dispatchEvent(new window.Event('input', { bubbles: true }));
};
const title = () => sheet().querySelector('.home-plan-sheet-title')?.textContent;
const model = (id) => sheet().querySelector(`[data-model="${id}"]`);

const app = await boot();
await tick(30);
const data = await app._askSettings();
app._paintSettings(data);
app.openModel();
await tick(20);

/* Fino al passo 3 con un indirizzo e una chiave: l'elenco arriva. */
async function toModels({ baseUrl = 'https://api.deepseek.com/v1', key = 'sk-good' } = {}) {
  $('home-provider-add').click();
  await until(() => sheet().open, { msg: 'il foglio non si apre' });
  assert.equal(title(), T.title1);
  press(NEXT);
  assert.equal(title(), T.title2);
  type('home-padd-base', baseUrl);
  type('home-padd-key', key);
  press(NEXT);
}
"""


def test_the_whole_way_adds_the_provider_and_makes_it_answer() -> None:
    run_home(_HEAD + """
await toModels();
await until(() => model('m-3'), { msg: 'l\\u2019elenco non arriva' });
// L'elenco e' chiesto coi campi non salvati, e col nome preso dall'indirizzo.
assert.deepEqual(listed.at(-1), { provider: 'deepseek', api_key: 'sk-good',
  api_base: 'https://api.deepseek.com/v1', format: 'openai_compat' });
// Con piu' di otto modelli c'e' la ricerca, e filtra.
type('home-padd-search', '1');
assert.equal(model('m-2'), null);
assert.ok(model('m-11'));
type('home-padd-search', '');
assert.equal(disabled(T.save), true, 'senza un modello si puo\\u2019 gia\\u2019 provare');
model('m-3').click();
press(T.save);
await until(() => !sheet().open, { msg: 'il foglio resta aperto' });
await until(() => updates.length, { msg: 'nessuna attivazione' });

assert.deepEqual(added, [{ name: 'deepseek', format: 'openai_compat', api_key: 'sk-good',
  api_base: 'https://api.deepseek.com/v1', model: 'm-3', skip_probe: '' }]);
// L'attivazione e' il salvataggio di sempre: modello e provider insieme.
assert.deepEqual(updates, [{ model: 'm-3', default_provider: 'deepseek' }]);
assert.equal(app.modelRoom.viewing, 'deepseek');
assert.equal(app.modelRoom.data.default_provider, 'deepseek');
""")


def test_without_make_it_answer_nobody_changes() -> None:
    run_home(_HEAD + """
await toModels();
await until(() => model('m-3'));
model('m-3').click();
sheet().querySelector('.home-switch').click();
press(T.save);
await until(() => !sheet().open);
await tick(30);
assert.equal(added.length, 1);
assert.deepEqual(updates, [], 'chi risponde e\\u2019 cambiato');
assert.equal(app.modelRoom.viewing, 'deepseek', 'la scheda non guarda la marca nuova');
assert.equal(app.modelRoom.data.default_provider, 'groq');
""")


def test_a_name_in_use_stops_step_two() -> None:
    run_home(_HEAD + """
$('home-provider-add').click();
await until(() => sheet().open);
press(NEXT);
type('home-padd-base', 'https://api.groq.com/openai/v1');
// Il nome suggerito non e' mai uno gia' usato.
assert.equal($('home-padd-name').value, 'groq_2');
type('home-padd-name', 'groq');
type('home-padd-key', 'sk-good');
assert.equal(disabled(NEXT), true, 'un nome preso lascia andare avanti');
assert.ok($('home-padd-name').classList.contains('is-bad'));
assert.ok(sheet().textContent.includes(T.nameTaken));
// Toccato a mano, il nome non segue piu' l'indirizzo.
type('home-padd-name', 'mio');
type('home-padd-base', 'https://api.deepseek.com/v1');
assert.equal($('home-padd-name').value, 'mio');
assert.equal(disabled(NEXT), false);
// Senza chiave non si va avanti: la fabbrica non costruirebbe il provider.
type('home-padd-key', '');
assert.equal(disabled(NEXT), true);
""")


def test_a_key_turned_down_by_the_list_goes_back_to_the_key() -> None:
    run_home(_HEAD + """
await toModels({ key: 'bad' });
await until(() => title() === T.title2 && $('home-padd-key')?.classList.contains('is-bad'),
  { msg: 'non torna alla chiave' });
assert.ok(sheet().textContent.includes(locales.en.common.modelsKeyRejected));
assert.equal($('home-padd-key').value, 'bad', 'la chiave scritta e\\u2019 sparita');
type('home-padd-key', 'sk-good');
assert.equal($('home-padd-key').classList.contains('is-bad'), false);
press(NEXT);
await until(() => model('m-1'), { msg: 'con la chiave nuova l\\u2019elenco non arriva' });
""")


def test_no_list_means_typing_the_model() -> None:
    run_home(_HEAD + """
await toModels({ key: 'nolist' });
await until(() => $('home-padd-model'), { msg: 'nessun campo per il modello' });
assert.ok(sheet().textContent.includes(T.noList));
assert.equal(disabled(T.save), true);
type('home-padd-model', 'claude-x');
press(T.save);
await until(() => added.length);
assert.equal(added[0].model, 'claude-x');
""")


def test_a_turned_down_test_question_offers_no_save_anyway() -> None:
    run_home(_HEAD + """
addAnswer = () => ({ ok: false, probe: 'auth' });
await toModels();
await until(() => model('m-1'));
model('m-1').click();
press(T.save);
await until(() => sheet().querySelector('.home-padd-alert'), { msg: 'nessun avviso' });
assert.ok(sheet().textContent.includes(P.auth));
assert.equal(has(P.saveAnyway), false, 'una chiave rifiutata si puo\\u2019 salvare lo stesso');
assert.ok(sheet().open);
press(P.changeKey);
assert.equal(title(), T.title2);
assert.equal($('home-padd-key').value, 'sk-good');
""")


def test_an_unreachable_provider_can_be_saved_anyway() -> None:
    run_home(_HEAD + """
addAnswer = (p) => (p.skip_probe ? { ok: true, ...base,
  providers: [...base.providers, { name: p.name, format: p.format }] }
  : { ok: false, probe: 'connection' });
await toModels();
await until(() => model('m-1'));
model('m-1').click();
press(T.save);
await until(() => sheet().querySelector('.home-padd-alert'));
assert.ok(sheet().textContent.includes(P.connection));
assert.ok(has(T.changeBase));
press(P.saveAnyway);
await until(() => !sheet().open, { msg: 'Salva lo stesso non chiude' });
assert.deepEqual(added.map((a) => a.skip_probe), ['', '1']);
""")


def test_a_name_taken_meanwhile_goes_back_to_the_name() -> None:
    run_home(_HEAD + """
addAnswer = () => ({ ok: false, error: 'name_taken' });
await toModels();
await until(() => model('m-1'));
model('m-1').click();
press(T.save);
await until(() => title() === T.title2, { msg: 'non torna al nome' });
assert.ok($('home-padd-name').classList.contains('is-bad'));
assert.equal(disabled(NEXT), true);
type('home-padd-name', 'deepseek-due');
assert.equal(disabled(NEXT), false);
""")


def test_a_list_that_arrives_late_is_not_kept_for_another_key() -> None:
    """Torni indietro prima che l'elenco arrivi e cambi la chiave: l'elenco
    della chiave vecchia, arrivato dopo, non vale per la nuova. «Avanti» lo
    chiede di nuovo, invece di riusarlo — e di provare poi un modello che la
    chiave nuova magari non vede."""
    run_home(_HEAD + """
rpcSilent.add('settings.provider.models');
await toModels({ key: 'sk-vecchia' });
await until(() => sent.some((m) => m.method === 'settings.provider.models'));
const asked = () => sent.filter((m) => m.method === 'settings.provider.models');
const late = asked().at(-1);
sheet().querySelector('.home-padd-back').click();
assert.equal(title(), T.title2);
type('home-padd-key', 'sk-nuova');
frame({ event: 'rpc_result', id: late.id, ok: true, result: { status: 'available', models: MODELS } });
await tick(30);
assert.equal(title(), T.title2, 'l\\u2019elenco in ritardo ha cambiato passo');
assert.equal($('home-padd-key').value, 'sk-nuova', 'il ridisegno ha perso la chiave nuova');
press(NEXT);
await until(() => asked().length === 2, { msg: 'l\\u2019elenco della chiave vecchia e\\u2019 stato riusato' });
assert.equal(asked().at(-1).params.api_key, 'sk-nuova');
""")
