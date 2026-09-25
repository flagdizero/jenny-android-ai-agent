"""«Chi risponde»: le marche configurate, la chiave, e i modelli.

Tre cose si misurano qui, e nessuna si vedrebbe aprendo la stanza una volta.

**Toccare una mattonella non cambia chi risponde.** Mostra i suoi modelli. Il
cambio e' il tocco su un modello, e salva `model` e `default_provider`
**insieme**: un id di OpenAI attivato con Anthropic come provider e' una config
che non risponde, e fra due chiamate separate esisterebbe davvero.

**Il modello in uso si vede anche quando il provider non lo elenca** — un id
battuto a mano in officina, o un elenco che non e' arrivato. Una stanza che
non risponde alla domanda che ha in testa e' peggio di una riga vuota.

**La chiave vera non torna mai al client.** Il campo parte vuoto, il
suggerimento offuscato arriva dal server, e salvare la stringa vuota non fa
niente: sarebbe il modo piu' silenzioso di cancellare la chiave buona.

I membri si ritagliano dal sorgente e girano in node su un DOM finto.
`shared/provider-brand.js` invece si importa **vero**: la tabella delle marche
e degli alias e' proprio cio' che qui non va ricopiato, o il banco misurerebbe
la propria copia.
"""

from __future__ import annotations

import json
from pathlib import Path

from support.js_harness import function, member, requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
MODEL_JS = ASSETS / "home-model.js"
BRAND_JS = ASSETS / "shared" / "provider-brand.js"
I18N_JS = ASSETS / "shared" / "i18n.js"
I18N_DIR = ASSETS / "i18n"


pytestmark = requires_node


_HARNESS = """
import assert from 'node:assert/strict';

const { getProviderBrand } = await import('__BRAND_URL__');

const TRANSLATIONS = __TRANSLATIONS__;
const i18n = { locale: 'it', translations: TRANSLATIONS, __T__ };

function makeEl(tag) {
  const el = {
    tag,
    className: '',
    textContent: '',
    value: '',
    placeholder: '',
    hidden: false,
    dataset: {},
    attrs: {},
    style: {},
    children: [],
    listeners: {},
    setAttribute(k, v) { el.attrs[k] = v; },
    addEventListener(type, fn) { (el.listeners[type] ||= []).push(fn); },
    appendChild(child) { el.children.push(child); return child; },
    replaceChildren(...nuovi) { el.children = nuovi; },
    focus() { el.messoAFuoco = true; },
    classList: {
      add(n) { if (!el.classList.contains(n)) el.className = (el.className + ' ' + n).trim(); },
      remove(n) {
        el.className = String(el.className).split(' ').filter((x) => x && x !== n).join(' ');
      },
      contains(n) { return String(el.className).split(' ').includes(n); },
      toggle(n, on) { if (on) el.classList.add(n); else el.classList.remove(n); },
    },
  };
  return el;
}

const nodi = {};
const document = {
  createElement: (tag) => makeEl(tag),
  getElementById: (id) => (nodi[id] ||= makeEl('div')),
};

/* Cosa risponde il server, deciso dal banco. `cataloghi` e' quel che ogni
   provider dichiara; `chiesti` conta le richieste, che e' meta' di cio' che
   si misura qui. */
let cataloghi = {};
const chiesti = [];
const salvataggi = [];
let salvataggioRotto = false;
let ultimoPayload = null;
const brindisi = [];

const api = {
  getProviderModels(provider) {
    chiesti.push(provider);
    const c = cataloghi[provider];
    if (c === undefined) return Promise.reject(new Error('niente elenco'));
    return Promise.resolve(c);
  },
  updateSettings(params) {
    salvataggi.push({ tipo: 'settings', ...params });
    if (salvataggioRotto) return Promise.reject(new Error('rifiutato'));
    return Promise.resolve(ultimoPayload);
  },
  updateProvider(params) {
    salvataggi.push({ tipo: 'provider', ...params });
    if (salvataggioRotto) return Promise.reject(new Error('rifiutato'));
    return Promise.resolve(ultimoPayload);
  },
};

function showToast(msg, tipo) { brindisi.push([msg, tipo]); }

__SHORT_BRAND__
__TILE_NAMES__
__MODEL_VALUE__

class HomeModel {
  __CTOR__
  __OPEN__
  __SET_SETTINGS__
  __VIEW_NAME__
  __VALUE__
  __APPLY_TRANSLATIONS__
  __PICK_PROVIDER__
  __PICK_MODEL__
  __TOGGLE_KEY_EDIT__
  __SAVE_KEY__
  __PROVIDER__
  __APPLY__
  __LOAD_MODELS__
  __PAINT__
  __PAINT_BRANDS__
  __PAINT_KEY__
  __PAINT_MODELS__
  __SAY_MODELS__
  __SAY_RESTART__
}

/* Un payload della forma di `/api/settings`, con dentro solo cio' che questa
   stanza legge. */
function settings(providers, attivo, modello, extra) {
  return {
    providers,
    default_provider: attivo,
    agent: { model: modello },
    ...(extra || {}),
  };
}

const passati = [];
async function room(dati, catalogo) {
  for (const k of Object.keys(nodi)) delete nodi[k];
  cataloghi = catalogo || {};
  chiesti.length = 0;
  salvataggi.length = 0;
  salvataggioRotto = false;
  brindisi.length = 0;
  passati.length = 0;
  ultimoPayload = null;
  /* Quel che nel markup nasce `hidden`. Il banco parte da li' o misurerebbe
     una stanza che non esiste: che quei quattro nodi lo siano davvero lo
     tiene `test_casa_tu_contract.py`. */
  for (const id of ['casa-key-row', 'casa-key-edit', 'casa-models-note', 'casa-model-restart']) {
    document.getElementById(id).hidden = true;
  }
  const stanzaModelli = new HomeModel({ onSettings: (d) => passati.push(d) });
  stanzaModelli.setSettings(dati);
  stanzaModelli.open();
  await new Promise((r) => setImmediate(r));
  return stanzaModelli;
}

/* Le righe a schermo, come le legge chi guarda. */
function modelliAVideo() {
  return nodi['casa-models'].children.map((r) => r.dataset.model);
}
function acceso() {
  const on = nodi['casa-models'].children.filter((r) => r.classList.contains.call(null, 'is-on')
    || String(r.className).split(' ').includes('is-on'));
  return on.map((r) => r.dataset.model);
}
function mattonelle() {
  return nodi['casa-providers'].children.map((t) => ({
    provider: t.dataset.provider,
    nome: t.children[1].textContent,
    attiva: String(t.className).split(' ').includes('is-on'),
    guardata: String(t.className).split(' ').includes('is-viewing'),
  }));
}
"""


def _harness() -> str:
    src = MODEL_JS.read_text(encoding="utf-8")
    it = json.loads((I18N_DIR / "it.json").read_text(encoding="utf-8"))
    return (
        _HARNESS.replace("__TRANSLATIONS__", json.dumps({"it": it}, ensure_ascii=False))
        .replace("__T__", member(I18N_JS.read_text(encoding="utf-8"), "t"))
        .replace("__BRAND_URL__", BRAND_JS.as_uri())
        .replace("__SHORT_BRAND__", function(src, "shortBrand"))
        .replace("__TILE_NAMES__", function(src, "tileNames"))
        .replace("__MODEL_VALUE__", function(src, "modelValue"))
        .replace("__CTOR__", member(src, "constructor"))
        .replace("__OPEN__", member(src, "open"))
        .replace("__SET_SETTINGS__", member(src, "setSettings"))
        .replace("__VIEW_NAME__", member(src, "viewName"))
        .replace("__VALUE__", member(src, "value"))
        .replace("__APPLY_TRANSLATIONS__", member(src, "applyTranslations"))
        .replace("__PICK_PROVIDER__", member(src, "pickProvider"))
        .replace("__PICK_MODEL__", member(src, "pickModel"))
        .replace("__TOGGLE_KEY_EDIT__", member(src, "toggleKeyEdit"))
        .replace("__SAVE_KEY__", member(src, "saveKey"))
        .replace("__PROVIDER__", member(src, "_provider"))
        .replace("__APPLY__", member(src, "_apply"))
        .replace("__LOAD_MODELS__", member(src, "_loadModels"))
        .replace("__PAINT__", member(src, "_paint"))
        .replace("__PAINT_BRANDS__", member(src, "_paintBrands"))
        .replace("__PAINT_KEY__", member(src, "_paintKey"))
        .replace("__PAINT_MODELS__", member(src, "_paintModels"))
        .replace("__SAY_MODELS__", member(src, "_sayModels"))
        .replace("__SAY_RESTART__", member(src, "_sayRestart"))
    )


def _run_js(script: str) -> None:
    run_js(_harness() + "\n" + script)


# ── I nomi sulle mattonelle ─────────────────────────────────────────────────


def test_a_tile_shows_the_brand_name() -> None:
    """`opencode_go` si legge «OpenCode»: e' il nome di marca, e la tabella che
    lo sa e' una sola."""
    _run_js("""
      const nomi = tileNames([{ name: 'opencode_go' }, { name: 'anthropic' }]);
      assert.equal(nomi[0], getProviderBrand('opencode_go').label);
      assert.equal(nomi[1], shortBrand(getProviderBrand('anthropic').label));
    """)


def test_two_providers_of_the_same_brand_keep_their_own_names() -> None:
    """`opencode_go` e `opencode_zen` cadono **sulla stessa marca** (v. gli
    alias in `shared/provider-brand.js`).

    Due mattonelle identiche sono peggio di due nomi tecnici: una delle due e'
    accesa, e non si sa quale. In quel caso vincono i nomi configurati, che
    sono unici per costruzione.
    """
    _run_js("""
      const doppie = [{ name: 'opencode_go' }, { name: 'opencode_zen' }];
      assert.equal(getProviderBrand('opencode_go').label,
                   getProviderBrand('opencode_zen').label,
                   'gli alias sono cambiati: il banco non misura piu' + ' la collisione');
      assert.deepEqual(tileNames(doppie), ['opencode_go', 'opencode_zen']);

      /* Una sola delle due, e torna a essere la marca. */
      assert.deepEqual(tileNames([{ name: 'opencode_go' }]),
                       [getProviderBrand('opencode_go').label]);
    """)


def test_the_row_says_it_is_not_set_up_when_no_provider_answers() -> None:
    """`default_provider` puo' nominare un provider che non c'e' piu': la riga
    non deve scrivere quel nome come se rispondesse."""
    _run_js("""
      assert.equal(modelValue({ providers: [], active: null }), i18n.t('casa.model.none'));
      assert.equal(modelValue({ providers: [{ name: 'groq' }], active: 'sparito' }),
                   i18n.t('casa.model.none'));
      assert.equal(modelValue({ providers: [{ name: 'groq' }], active: 'groq' }),
                   getProviderBrand('groq').label);
    """)


# ── Guardare non e' scegliere ───────────────────────────────────────────────


def test_tapping_a_tile_does_not_change_who_answers() -> None:
    """Si guarda un elenco **prima** di sceglierlo. Se toccare la mattonella
    cambiasse anche chi risponde, lo cambierebbe prima di scegliere — e con un
    modello che appartiene all'altra marca."""
    _run_js("""
      const dati = settings(
        [{ name: 'groq' }, { name: 'anthropic' }], 'groq', 'llama-3.3-70b');
      const s = await room(dati, { groq: { status: 'available', models: [{ id: 'llama-3.3-70b' }] },
                                     anthropic: { status: 'available', models: [{ id: 'claude-x' }] } });
      s.pickProvider('anthropic');
      await new Promise((r) => setImmediate(r));

      assert.deepEqual(salvataggi, [], 'guardare un elenco ha salvato qualcosa');
      assert.equal(s.data.default_provider, 'groq', 'chi risponde e cambiato da solo');
      const tiles = mattonelle();
      assert.deepEqual(tiles.map((t) => t.attiva), [true, false], 'l acceso ha seguito lo sguardo');
      assert.deepEqual(tiles.map((t) => t.guardata), [false, true], 'lo sguardo non si e mosso');
      assert.deepEqual(modelliAVideo(), ['claude-x']);
    """)


def test_picking_a_model_saves_the_provider_with_it() -> None:
    """Modello e provider in **una** chiamata: fra due, per un istante, la
    config avrebbe un modello che il provider attivo non conosce."""
    _run_js("""
      const dati = settings(
        [{ name: 'groq' }, { name: 'anthropic' }], 'groq', 'llama-3.3-70b');
      const s = await room(dati, { groq: { status: 'available', models: [{ id: 'llama-3.3-70b' }] },
                                     anthropic: { status: 'available', models: [{ id: 'claude-x' }] } });
      s.pickProvider('anthropic');
      await new Promise((r) => setImmediate(r));
      ultimoPayload = settings(dati.providers, 'anthropic', 'claude-x');
      await s.pickModel('claude-x');

      assert.equal(salvataggi.length, 1, 'due chiamate invece di una: ' + JSON.stringify(salvataggi));
      assert.deepEqual(salvataggi[0],
        { tipo: 'settings', model: 'claude-x', default_provider: 'anthropic' });
      assert.equal(s.value(), shortBrand(getProviderBrand('anthropic').label));
      assert.equal(passati.length, 1, 'il guscio non ha ricevuto il payload fresco');
    """)


def test_a_model_that_did_not_save_leaves_the_room_as_it_was() -> None:
    """Il fallimento si dice, e non si finge: la riga resta su chi risponde
    davvero."""
    _run_js("""
      const dati = settings([{ name: 'groq' }], 'groq', 'llama-3.3-70b');
      const s = await room(dati, { groq: { status: 'available', models: [{ id: 'altro' }] } });
      salvataggioRotto = true;
      await s.pickModel('altro');

      assert.equal(s.data.agent.model, 'llama-3.3-70b', 'la stanza si e creduta salvata');
      assert.equal(brindisi.length, 1);
      assert.equal(brindisi[0][1], 'error');
      assert.deepEqual(acceso(), ['llama-3.3-70b'], 'il segno di spunta si e mosso lo stesso');
    """)


# ── L'elenco dei modelli ────────────────────────────────────────────────────


def test_the_model_in_use_shows_even_when_the_provider_does_not_list_it() -> None:
    """Un id battuto a mano in officina, o un elenco che non e' arrivato: in
    tutti e due i casi la stanza deve dire **chi risponde adesso**."""
    _run_js("""
      const dati = settings([{ name: 'groq' }], 'groq', 'un-id-a-mano');
      const s = await room(dati, { groq: { status: 'available', models: [{ id: 'llama-3.3-70b' }] } });
      assert.deepEqual(modelliAVideo(), ['un-id-a-mano', 'llama-3.3-70b'],
        'il modello in uso non e in cima');
      assert.deepEqual(acceso(), ['un-id-a-mano']);

      /* E anche quando l'elenco non arriva affatto. */
      const vuota = await room(dati, {});
      assert.deepEqual(modelliAVideo(), ['un-id-a-mano']);
    """)


def test_the_current_model_is_only_ticked_under_its_own_provider() -> None:
    """Lo stesso id puo' comparire nell'elenco di due marche — `gpt-4o` su
    OpenAI e su un gateway che lo rivende. Spuntarlo sotto quella che **non**
    risponde direbbe che e' attivo mentre non lo e'."""
    _run_js("""
      const dati = settings(
        [{ name: 'openai' }, { name: 'openrouter' }], 'openai', 'gpt-4o');
      const s = await room(dati, {
        openai: { status: 'available', models: [{ id: 'gpt-4o' }] },
        openrouter: { status: 'available', models: [{ id: 'gpt-4o' }] },
      });
      assert.deepEqual(acceso(), ['gpt-4o']);

      s.pickProvider('openrouter');
      await new Promise((r) => setImmediate(r));
      assert.deepEqual(modelliAVideo(), ['gpt-4o']);
      assert.deepEqual(acceso(), [], 'spuntato sotto la marca che non risponde');
    """)


def test_the_catalogue_is_asked_once_per_provider() -> None:
    """L'elenco lo va a chiedere al provider vero, e passa dalla rete: un
    ridisegno non e' una ragione per rifarlo."""
    _run_js("""
      const dati = settings([{ name: 'groq' }, { name: 'anthropic' }], 'groq', 'm');
      const s = await room(dati, { groq: { status: 'available', models: [] },
                                     anthropic: { status: 'available', models: [] } });
      s.pickProvider('anthropic');
      await new Promise((r) => setImmediate(r));
      s.pickProvider('groq');
      await new Promise((r) => setImmediate(r));
      s.pickProvider('anthropic');
      await new Promise((r) => setImmediate(r));
      assert.deepEqual(chiesti, ['groq', 'anthropic'], 'richieste ripetute: ' + chiesti.join(','));
    """)


def test_a_catalogue_that_arrives_late_does_not_paint_over_the_one_you_read() -> None:
    """Il tempo fra la richiesta e la risposta e' tempo in cui puoi aver
    cambiato marca.

    Cio' che a schermo ci va lo decide **il provider guardato**, non l'ultimo
    catalogo arrivato: e' l'unica forma che regge, perche' le risposte
    tornano nell'ordine della rete e non in quello dei tocchi.
    """
    _run_js("""
      let sbloccaGroq;
      const attesa = new Promise((r) => { sbloccaGroq = r; });
      const dati = settings([{ name: 'groq' }, { name: 'anthropic' }], 'groq', 'm');
      cataloghi = {};
      for (const k of Object.keys(nodi)) delete nodi[k];
      chiesti.length = 0;
      const s = new HomeModel({});
      api.getProviderModels = (p) => {
        chiesti.push(p);
        if (p === 'groq') return attesa.then(() => ({ status: 'available', models: [{ id: 'tardi' }] }));
        return Promise.resolve({ status: 'available', models: [{ id: 'subito' }] });
      };
      s.setSettings(dati);
      s.open();
      s.pickProvider('anthropic');
      await new Promise((r) => setImmediate(r));
      assert.deepEqual(modelliAVideo(), ['subito']);

      sbloccaGroq();
      await new Promise((r) => setImmediate(r));
      await new Promise((r) => setImmediate(r));
      assert.deepEqual(modelliAVideo(), ['subito'],
        'il catalogo di groq ha scavalcato quello che stavi leggendo');
    """)


def test_an_empty_list_says_why() -> None:
    """I quattro stati del server hanno una frase ciascuno, nella lingua del
    telefono: `message` e' diagnostica in inglese."""
    _run_js("""
      const dati = settings([{ name: 'groq' }], 'groq', '');
      await room(dati, { groq: { status: 'not_configured', models: [], message: 'Configure this provider.' } });
      assert.equal(nodi['casa-models-note'].textContent, i18n.t('casa.model.needsKey'));
      assert.equal(nodi['casa-models-note'].hidden, false);

      await room(dati, { groq: { status: 'missing_api_base', models: [] } });
      assert.equal(nodi['casa-models-note'].textContent, i18n.t('casa.model.needsBase'));

      await room(dati, { groq: { status: 'available', models: [{ id: 'x' }] } });
      assert.equal(nodi['casa-models-note'].hidden, true, 'una nota sopra un elenco che c e');
    """)


# ── La chiave ───────────────────────────────────────────────────────────────


def test_the_key_field_starts_empty_and_the_hint_comes_from_the_server() -> None:
    """La chiave vera non torna mai al client: quel che si vede e' il
    suggerimento offuscato, e il campo parte vuoto — un salvataggio senza
    riscriverla persisterebbe la maschera."""
    _run_js("""
      const dati = settings([{ name: 'groq', api_key_hint: 'gsk_...4f2a' }], 'groq', 'm');
      const s = await room(dati, { groq: { status: 'available', models: [] } });
      assert.equal(nodi['casa-key-hint'].textContent, 'gsk_...4f2a');
      assert.equal(nodi['casa-key-input'].value, '', 'il campo e partito con dentro qualcosa');
      assert.equal(nodi['casa-key-btn'].textContent, i18n.t('casa.model.keyChange'));

      const senza = settings([{ name: 'groq', api_key_hint: '' }], 'groq', 'm');
      const s2 = await room(senza, { groq: { status: 'available', models: [] } });
      assert.equal(nodi['casa-key-hint'].textContent, i18n.t('casa.model.keyNone'));
      assert.equal(nodi['casa-key-btn'].textContent, i18n.t('casa.model.keyAdd'));
    """)


def test_saving_an_empty_key_does_nothing() -> None:
    """Sarebbe il modo piu' silenzioso di cancellare la chiave buona."""
    _run_js("""
      const dati = settings([{ name: 'groq', api_key_hint: 'gsk_...4f2a' }], 'groq', 'm');
      const s = await room(dati, { groq: { status: 'available', models: [] } });
      nodi['casa-key-input'].value = '   ';
      await s.saveKey();
      assert.deepEqual(salvataggi, [], 'una chiave vuota e arrivata al server');
    """)


def test_a_new_key_makes_the_catalogue_be_asked_again() -> None:
    """Una chiave appena messa puo' essere **esattamente** la ragione per cui
    l'elenco era vuoto."""
    _run_js("""
      const dati = settings([{ name: 'groq', api_key_hint: '' }], 'groq', '');
      const s = await room(dati, { groq: { status: 'not_configured', models: [] } });
      assert.deepEqual(chiesti, ['groq']);

      ultimoPayload = settings([{ name: 'groq', api_key_hint: 'gsk_...4f2a' }], 'groq', '');
      cataloghi = { groq: { status: 'available', models: [{ id: 'llama-3.3-70b' }] } };
      nodi['casa-key-input'].value = 'gsk_una_chiave_vera';
      await s.saveKey();
      await new Promise((r) => setImmediate(r));

      assert.deepEqual(salvataggi, [{ tipo: 'provider', name: 'groq', api_key: 'gsk_una_chiave_vera' }]);
      assert.deepEqual(chiesti, ['groq', 'groq'], 'l elenco non e stato richiesto');
      assert.deepEqual(modelliAVideo(), ['llama-3.3-70b']);
      assert.equal(nodi['casa-key-input'].value, '', 'la chiave e rimasta nel campo');
      assert.equal(nodi['casa-key-edit'].hidden, true);
    """)


def test_a_change_that_needs_a_restart_says_so() -> None:
    """Alcuni cambi valgono dal turno dopo, altri no. Il payload lo dice, e la
    stanza lo scrive invece di lasciarlo indovinare."""
    _run_js("""
      const dati = settings([{ name: 'groq' }], 'groq', 'm');
      const s = await room(dati, { groq: { status: 'available', models: [{ id: 'altro' }] } });
      assert.equal(nodi['casa-model-restart'].hidden, true);

      ultimoPayload = settings(dati.providers, 'groq', 'altro', { requires_restart: true });
      await s.pickModel('altro');
      assert.equal(nodi['casa-model-restart'].hidden, false);
      assert.equal(nodi['casa-model-restart'].textContent, i18n.t('casa.model.restart'));
    """)


def test_the_list_is_titled_with_the_brand_you_are_reading() -> None:
    """Visto sul rig: la scheda diceva «Modelli di OpenCode» sopra i modelli
    di Anthropic, perche' il titolo leggeva chi *risponde* invece di chi stai
    *guardando*. E' l'unica frase che dice di chi sono le righe che stai per
    toccare, quindi sbagliarla e' peggio che non averla."""
    _run_js("""
      const dati = settings(
        [{ name: 'opencode_go' }, { name: 'anthropic' }], 'opencode_go', 'grok-code-fast-1');
      const s = await room(dati, { opencode_go: { status: 'available', models: [] },
                                     anthropic: { status: 'available', models: [] } });
      assert.equal(nodi['casa-models-label'].textContent,
        i18n.t('casa.model.models', { provider: getProviderBrand('opencode_go').label }));

      s.pickProvider('anthropic');
      await new Promise((r) => setImmediate(r));
      assert.equal(nodi['casa-models-label'].textContent,
        i18n.t('casa.model.models', { provider: shortBrand(getProviderBrand('anthropic').label) }),
        'il titolo nomina chi risponde invece di chi stai guardando');
      /* E la riga di «Tu e Jenny» continua a dire chi risponde: sono due
         domande diverse, e una sola risposta non puo' servirle entrambe. */
      assert.equal(s.value(), getProviderBrand('opencode_go').label);
    """)


def test_the_brand_that_answers_carries_a_mark_and_not_only_a_ring() -> None:
    """Nei temi chiari `--overlay` e `--overlay-strong` sono quasi lo stesso
    bianco: a schermo non si distingueva quale elenco stessi leggendo (visto
    sul rig, tema Y2K). Adesso la pastiglia guardata e' **piena** e quella che
    risponde porta un segno — e un anello d'accento da solo non basta, perche'
    dove l'accento e' il testo quell'anello e' il fondo della pastiglia."""
    _run_js("""
      const dati = settings(
        [{ name: 'opencode_go' }, { name: 'anthropic' }], 'opencode_go', 'm');
      const s = await room(dati, { opencode_go: { status: 'available', models: [] },
                                     anthropic: { status: 'available', models: [] } });
      const segni = () => nodi['casa-providers'].children.map(
        (t) => t.children.some((c) => String(c.className).includes('ti-check')));
      assert.deepEqual(segni(), [true, false], 'chi risponde non porta nessun segno');

      s.pickProvider('anthropic');
      await new Promise((r) => setImmediate(r));
      assert.deepEqual(segni(), [true, false], 'il segno ha seguito lo sguardo');
      assert.deepEqual(mattonelle().map((t) => t.guardata), [false, true]);
    """)


def test_the_word_that_does_not_distinguish_a_brand_falls() -> None:
    """«Anthropic Compatible» e' il nome delle schede larghe dell'officina,
    dove quella parola dice che il *formato* e' quello. Su una pastiglia, e in
    «Modelli di …», e' solo la parola che non distingue niente — visto sul
    rig, dove riempiva mezza striscia. Stessa forma di `shortThemeName`."""
    _run_js("""
      assert.equal(getProviderBrand('anthropic').label, 'Anthropic Compatible',
        'la tabella delle marche e cambiata: il banco non misura piu la coda');
      assert.equal(shortBrand('Anthropic Compatible'), 'Anthropic');
      /* Non tocca gli altri nomi, e una marca che contiene la parola in mezzo
         resta intera. */
      assert.equal(shortBrand('OpenCode'), 'OpenCode');
      assert.equal(shortBrand('Compatible Systems'), 'Compatible Systems');
      /* In coda, e solo in coda: una marca che si chiamasse cosi' resterebbe
         intera invece di perdere una parola dal mezzo. */
      assert.equal(shortBrand('Anthropic Compatible Systems'), 'Anthropic Compatible Systems');
      assert.deepEqual(tileNames([{ name: 'anthropic' }]), ['Anthropic']);
    """)
