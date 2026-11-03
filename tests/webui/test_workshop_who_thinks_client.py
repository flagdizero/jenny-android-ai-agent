"""«Chi pensa» in officina: le marche in gruppi, coi loro modelli dentro.

Fino al 27/09/2026 erano due gruppi: una scheda col modello in uso, che si
leggeva e basta, e l'elenco delle marche, da cui si passava per cambiare
modello. Adesso sono uno, con la forma dei dati: un modello esiste dentro la
sua marca, e toccarlo sceglie la coppia intera.

Qui si misurano le tre regole che lo tengono in piedi, sul DOM vero (jsdom) e
coi metodi veri di ``mobile-settings.js``:

- **l'ordine si fissa all'ingresso**: scegliere sposta il segno, non le righe;
- **un gruppo chiuso dice con cosa risponde**;
- **l'intestazione apre e chiude, il cursore gestisce**: due bersagli.

E la scrittura: modello e marca in una chiamata sola, come in casa.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from support.home_dom import requires_jsdom
from support.js_harness import ASSETS, member, run_module

SRC = (ASSETS / "mobile-settings.js").read_text(encoding="utf-8")
CATALOG_JS = (ASSETS / "shared" / "model-catalog.js").read_text(encoding="utf-8")
# Dentro il banco: senza ``import`` (``api``, ``i18n``, ``showToast`` sono i finti).
_CATALOG = re.sub(r"^import .*?;\n", "", CATALOG_JS, flags=re.M).replace("export ", "")

_REAL = (
    "_formatLabel",
    "_renderWhoThinks",
    "_renderProviderListHtml",
    "_brandSubline",
    "_isBrandOpen",
    "_brandColor",
    "_resetBrandVisit",
    "_brandCatalogKey",
    "_groupProvider",
    "_wireBrands",
    "_toggleBrand",
    "_loadBrandModels",
    "_brandRows",
    "_paintBrandGroup",
    "_pickBrandModel",
    "_setBrandsBusy",
    "_pickTypedModel",
    "_repaintBrands",
)


def _shown() -> int:
    m = re.search(r"const BRAND_MODELS_SHOWN = (\d+);", SRC)
    assert m, "BRAND_MODELS_SHOWN non si trova piu'"
    return int(m.group(1))


def _run(body: str) -> dict:
    methods = "\n".join(member(SRC, name) for name in _REAL)
    script = f"""
import {{ createRequire }} from 'node:module';
const require = createRequire(import.meta.url);
const {{ JSDOM }} = require('jsdom');
const dom = new JSDOM('<!doctype html><body><div id="content"></div></body>');
globalThis.document = dom.window.document;
const BRAND_MODELS_SHOWN = {_shown()};

const i18n = {{ t: (k, p) => (p ? k + ' ' + Object.values(p).join(',') : k) }};
const escapeHtml = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
const getProviderBrand = () => ({{ color: '#f2c230' }});
const toasts = [];
const showToast = (t, kind) => toasts.push([t, kind]);
console.warn = () => {{}};
// Il dialogo di conferma: registra la domanda e risponde `confirmAnswer`.
const confirms = [];
let confirmAnswer = true;
const confirmDialog = async (msg) => {{ confirms.push(msg); return confirmAnswer; }};

const drawer = {{ opened: [], open(id) {{ this.opened.push(id); }} }};
globalThis.window = {{ mobileApp: {{ drawer }} }};

// I modelli: `catalogs[marca]` e' la risposta, o una funzione che la da'.
const catalogs = {{
  og: {{ status: 'available', models: ['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'm7', 'm8'].map((id) => ({{ id }})) }},
  sec: {{ status: 'available', models: [{{ id: 's1' }}, {{ id: 's2' }}] }},
}};
const api = {{
  asks: [],
  writes: [],
  providerModels: async (name) => {{
    api.asks.push(name);
    const c = catalogs[name];
    return typeof c === 'function' ? c() : c;
  }},
  // La rotta vera restituisce le impostazioni intere (`settings_payload`);
  // `serverSide` c'e' solo li', e dice se l'officina le ha usate.
  hold: null,
  updateSettings: async (patch) => {{
    api.writes.push(patch);
    if (api.hold) await api.hold;
    return {{
      ...s.data,
      default_provider: patch.default_provider,
      agent: {{ ...s.data.agent, model: patch.model }},
      serverSide: 'from-the-server',
    }};
  }},
}};

{_CATALOG}

class Settings {{
  constructor() {{
    this._gen = 0;
    this.contentEl = document.getElementById('content');
    this._modelCatalogs = new ModelCatalogs((name) => api.providerModels(name));
    this._resetBrandVisit();
    this.data = {{
      default_provider: 'og',
      agent: {{ model: 'm3' }},
      providers: [
        {{ name: 'og', format: 'openai_compat', api_base: 'https://og/v1', api_key_hint: 'oc_s...X5Vs' }},
        {{ name: 'sec', format: 'anthropic', api_base: '', api_key_hint: 'sk-a...9f2c' }},
      ],
    }};
  }}
  _stale(g) {{ return g !== this._gen; }}
  _restoreScrollTop() {{}}
  _openBrand(name) {{ this.openedBrand = name; }}
  _paint() {{
    this.contentEl.innerHTML = this._renderWhoThinks(this.data);
    this._wireBrands();
  }}
{methods}
}}

const s = new Settings();
const settle = () => new Promise((r) => setTimeout(r, 5));
const group = (name) => [...document.querySelectorAll('.brand-group')].find((g) => g.dataset.brand === name);
const rows = (name) => [...group(name).querySelectorAll('[data-brand-model]')].map((b) => b.dataset.brandModel);
const checked = (name) => [...group(name).querySelectorAll('[aria-checked="true"]')].map((b) => b.dataset.brandModel);
const isOpen = (name) => !group(name).querySelector('.brand-body').hidden;
const sub = (name) => group(name).querySelector('.brand-where').textContent.trim();
const count = (name) => group(name).querySelector('[data-brand-count]').textContent;
const out = {{}};

s._paint();
await settle();
{body}
console.log(JSON.stringify(out));
"""
    with tempfile.TemporaryDirectory() as tmp:
        entry = Path(tmp) / "prova.mjs"
        entry.write_text(script, encoding="utf-8")
        text = run_module(entry)
    return json.loads(text.strip().splitlines()[-1])


@requires_jsdom
def test_on_entry_only_the_answering_brand_is_open() -> None:
    out = _run(
        """
out.open = ['og', 'sec'].map(isOpen);
out.subSec = sub('sec');
out.countSec = count('sec');
out.countOg = count('og');
"""
    )
    assert out["open"] == [True, False], "all'ingresso deve essere aperta solo quella che risponde"
    # Chiuso, un gruppo che non risponde dice dove sta e quanti modelli ha.
    assert "https://og" not in out["subSec"] and "settings.defaultUrl" in out["subSec"]
    assert out["countSec"] == "settings.modelsCount 2"
    assert out["countOg"] == "", "un gruppo aperto non ripete il conto: i modelli li vedi"


@requires_jsdom
def test_a_closed_answering_group_says_what_it_answers_with() -> None:
    out = _run(
        """
group('og').querySelector('[data-brand-toggle]').click();
out.closed = isOpen('og');
out.sub = sub('og');
out.expanded = group('og').querySelector('[data-brand-toggle]').getAttribute('aria-expanded');
group('og').querySelector('[data-brand-toggle]').click();
out.subOpen = sub('og');
"""
    )
    assert out["closed"] is False and out["expanded"] == "false"
    assert out["sub"] == "settings.answersWith m3"
    assert "https://og/v1" in out["subOpen"], "riaperto, la seconda riga torna a dire dove sta"


@requires_jsdom
def test_the_model_in_use_is_on_top_and_the_list_is_cut() -> None:
    shown = _shown()
    out = _run(
        """
out.rows = rows('og');
out.checked = checked('og');
const more = group('og').querySelector('[data-brand-more]');
out.moreHidden = more.hidden;
out.moreText = more.textContent;
out.filterHidden = group('og').querySelector('.brand-filter').hidden;
more.click();
out.all = rows('og');
"""
    )
    assert out["rows"][0] == "m3", "quello che risponde non e' in cima"
    assert out["checked"] == ["m3"]
    assert len(out["rows"]) == shown
    assert out["moreHidden"] is False and out["moreText"] == "settings.brandShowAll 8"
    assert out["filterHidden"] is False, "otto modelli e nessun filtro"
    assert out["all"] == ["m3", "m1", "m2", "m4", "m5", "m6", "m7", "m8"]


@requires_jsdom
def test_picking_moves_the_mark_not_the_rows() -> None:
    """Regola 1: l'ordine si fissa all'ingresso. E la scrittura e' una."""
    out = _run(
        """
const before = rows('og');
group('og').querySelector('[data-brand-model="m5"]').click();
await settle();
out.writes = api.writes;
out.same = JSON.stringify(rows('og')) === JSON.stringify(before);
out.checked = checked('og');
out.brands = [...document.querySelectorAll('.brand-group')].map((g) => g.dataset.brand);
out.asks = api.asks;
"""
    )
    assert out["writes"] == [{"model": "m5", "default_provider": "og"}]
    assert out["same"], "scegliere ha riordinato le righe sotto il dito"
    assert out["checked"] == ["m5"]
    assert out["brands"] == ["og", "sec"]
    assert sorted(out["asks"]) == ["og", "sec"], "dopo la scelta gli elenchi si sono richiesti"


@requires_jsdom
def test_picking_in_another_brand_switches_the_pair_and_keeps_the_order() -> None:
    out = _run(
        """
group('sec').querySelector('[data-brand-toggle]').click();
group('sec').querySelector('[data-brand-model="s2"]').click();
await settle();
out.writes = api.writes;
out.brands = [...document.querySelectorAll('.brand-group')].map((g) => g.dataset.brand);
out.active = [...document.querySelectorAll('.brand-group.is-active')].map((g) => g.dataset.brand);
out.checkedOg = checked('og');
out.checkedSec = checked('sec');
out.rowsSec = rows('sec');
out.open = ['og', 'sec'].map(isOpen);
"""
    )
    assert out["writes"] == [{"model": "s2", "default_provider": "sec"}]
    assert out["brands"] == ["og", "sec"], "la marca che risponde e' saltata in cima"
    assert out["active"] == ["sec"]
    assert out["checkedOg"] == [], "il segno e' rimasto anche sulla marca di prima"
    assert out["checkedSec"] == ["s2"]
    assert out["rowsSec"] == ["s1", "s2"], "le righe della marca nuova si sono riordinate"
    assert out["open"] == [True, True], "una scelta ha aperto o chiuso gruppi"


@requires_jsdom
def test_the_header_toggles_and_the_slider_manages() -> None:
    """Regola 3: due bersagli. Aprire una marca non apre la gestione."""
    out = _run(
        """
group('sec').querySelector('[data-brand-toggle]').click();
out.afterToggle = drawer.opened.slice();
group('sec').querySelector('[data-brand-open]').click();
out.afterManage = drawer.opened.slice();
out.brand = s.openedBrand;
out.label = group('sec').querySelector('[data-brand-open]').getAttribute('aria-label');
"""
    )
    assert out["afterToggle"] == [], "l'intestazione ha aperto il pannello della marca"
    assert out["afterManage"] == ["brand"] and out["brand"] == "sec"
    assert out["label"] == "settings.manageBrand sec"


@requires_jsdom
def test_a_typed_model_id_answers_and_joins_the_top() -> None:
    out = _run(
        """
const g = group('og');
g.querySelector('[data-brand-custom]').click();
out.formShown = !g.querySelector('[data-brand-custom-form]').hidden;
g.querySelector('[data-brand-custom-input]').value = '  scritto-a-mano  ';
g.querySelector('[data-brand-custom-form]').dispatchEvent(new dom.window.Event('submit', { cancelable: true }));
await settle();
out.writes = api.writes;
out.confirms = confirms;
out.rows = rows('og');
out.checked = checked('og');
"""
    )
    assert out["formShown"]
    assert out["confirms"] == ["settings.customModelConfirm scritto-a-mano,og"]
    assert out["writes"] == [{"model": "scritto-a-mano", "default_provider": "og"}]
    assert out["rows"][:2] == ["scritto-a-mano", "m3"]
    assert out["checked"] == ["scritto-a-mano"]


@requires_jsdom
def test_the_filter_narrows_and_says_when_nothing_matches() -> None:
    out = _run(
        """
const f = group('og').querySelector('[data-brand-filter]');
f.value = 'm7';
f.dispatchEvent(new dom.window.Event('input'));
out.rows = rows('og');
f.value = 'zzz';
f.dispatchEvent(new dom.window.Event('input'));
out.none = rows('og');
const note = group('og').querySelector('[data-brand-note]');
out.note = note.hidden ? null : note.textContent;
"""
    )
    assert out["rows"] == ["m7"]
    assert out["none"] == [] and out["note"] == "settings.brandFilterEmpty"


@requires_jsdom
def test_a_failed_list_says_why_and_is_asked_again() -> None:
    out = _run(
        """
catalogs.sec = { status: 'error', models: [], message: 'TLS: unknown CA' };
s._modelCatalogs = new ModelCatalogs((name) => api.providerModels(name));
s._resetBrandVisit();
api.asks.length = 0;
s._paint();
await settle();
group('sec').querySelector('[data-brand-toggle]').click();
const note = group('sec').querySelector('[data-brand-note]');
out.note = note.hidden ? null : note.textContent;
out.count = count('sec');
catalogs.sec = { status: 'available', models: [{ id: 's1' }] };
s._paint();
await settle();
out.sameVisit = api.asks.filter((n) => n === 'sec').length;
s._resetBrandVisit();
s._paint();
await settle();
out.nextVisit = api.asks.filter((n) => n === 'sec').length;
group('sec').querySelector('[data-brand-toggle]').click();
out.rows = rows('sec');
"""
    )
    assert out["note"] == "settings.brandModelsFailed TLS: unknown CA"
    assert out["count"] == ""
    assert out["sameVisit"] == 1, "un ridisegno nella stessa visita ha richiesto di nuovo la marca rotta"
    assert out["nextVisit"] == 2, "alla visita dopo l'elenco che non era arrivato non si riprova"
    assert out["rows"] == ["s1"]


@requires_jsdom
def test_a_late_list_paints_the_group_that_is_there_now() -> None:
    out = _run(
        """
let release;
catalogs.sec = () => new Promise((r) => { release = () => r({ status: 'available', models: [{ id: 'late' }] }); });
s._modelCatalogs = new ModelCatalogs((name) => api.providerModels(name));
s._resetBrandVisit();
s._paint();
s._paint();
release();
await settle();
group('sec').querySelector('[data-brand-toggle]').click();
out.rows = rows('sec');
"""
    )
    assert out["rows"] == ["late"], "la risposta tardiva e' finita in un gruppo che non c'e' piu'"


@requires_jsdom
def test_a_new_visit_forgets_the_order_and_what_was_open() -> None:
    out = _run(
        """
group('og').querySelector('[data-brand-model="m5"]').click();
await settle();
group('sec').querySelector('[data-brand-toggle]').click();
s._resetBrandVisit();
s._paint();
out.rows = rows('og');
out.open = ['og', 'sec'].map(isOpen);
"""
    )
    assert out["rows"][0] == "m5", "all'ingresso nuovo quello che risponde non sale in cima"
    assert out["open"] == [True, False]


def test_the_group_strings_exist_in_both_languages() -> None:
    from support.js_harness import locale

    keys = (
        "whoThinksHint", "answersWith", "manageBrand", "modelsCount", "brandModelsOf",
        "brandFilter", "brandFilterLabel", "brandFilterEmpty", "brandShowAll", "customModel",
        "customModelPlaceholder", "customModelLabel", "customModelUse", "customModelConfirm",
        "brandModelsNeedBase", "brandModelsFailed", "brandModelSaved",
        "brandModelSavedRestart", "brandModelFailed",
    )
    for language in ("it", "en"):
        s = locale(language)["settings"]
        missing = [k for k in keys if not s.get(k)]
        assert not missing, f"{language}: {missing}"
        for k in ("answersWith", "manageBrand", "modelsCount", "brandFilter", "brandShowAll"):
            assert "{" in s[k], f"{language}: {k} ha perso il segnaposto"


@requires_jsdom
def test_a_typed_id_the_list_has_needs_no_question_and_a_no_saves_nothing() -> None:
    out = _run(
        """
const g = group('og');
const submit = (id) => {
  g.querySelector('[data-brand-custom-input]').value = id;
  g.querySelector('[data-brand-custom-form]').dispatchEvent(new dom.window.Event('submit', { cancelable: true }));
};
confirmAnswer = false;
submit('refuso');
await settle();
out.afterNo = { confirms: confirms.length, writes: api.writes.length };
submit('m7');
await settle();
out.afterListed = { confirms: confirms.length, writes: api.writes };
"""
    )
    assert out["afterNo"] == {"confirms": 1, "writes": 0}, "un «no» ha salvato lo stesso"
    assert out["afterListed"]["confirms"] == 1, "un id che l'elenco ha non va chiesto"
    assert out["afterListed"]["writes"] == [{"model": "m7", "default_provider": "og"}]


@requires_jsdom
def test_one_choice_at_a_time_and_the_active_row_is_a_no_op() -> None:
    out = _run(
        """
let release;
api.hold = new Promise((r) => { release = r; });
group('og').querySelector('[data-brand-model="m5"]').click();
await settle();
out.disabled = [...group('og').querySelectorAll('[data-brand-model]')].every((b) => b.disabled);
group('og').querySelector('[data-brand-model="m6"]').click();
// E una strada che non passa da un bottone spento (il campo dell'id a mano).
await s._pickBrandModel('og', 'm7');
await settle();
out.duringFlight = api.writes.length;
release();
api.hold = null;
await settle();
out.enabled = [...group('og').querySelectorAll('[data-brand-model]')].every((b) => !b.disabled);
group('og').querySelector('[data-brand-model="m5"]').click();
await settle();
out.afterActive = api.writes.length;
"""
    )
    assert out["disabled"], "durante il salvataggio le righe restano toccabili"
    assert out["duringFlight"] == 1, "un secondo tocco in volo ha fatto una seconda scrittura"
    assert out["enabled"], "dopo il salvataggio le righe restano spente"
    assert out["afterActive"] == 1, "toccare il modello gia' attivo ha riscritto la config"


@requires_jsdom
def test_the_page_keeps_what_the_server_returned() -> None:
    out = _run(
        """
group('og').querySelector('[data-brand-model="m5"]').click();
await settle();
out.serverSide = s.data.serverSide;
out.model = s.data.agent.model;
"""
    )
    assert out == {"serverSide": "from-the-server", "model": "m5"}, (
        "l'officina ha ricostruito le impostazioni a memoria invece di usare la risposta"
    )


@requires_jsdom
def test_the_confirmation_arrives_even_after_leaving() -> None:
    out = _run(
        """
let release;
api.hold = new Promise((r) => { release = r; });
group('og').querySelector('[data-brand-model="m5"]').click();
await settle();
s._gen++;
release();
await settle();
out.toasts = toasts;
"""
    )
    assert out["toasts"] == [["settings.brandModelSaved", "success"]], (
        "il cambio e' avvenuto ma chi e' uscito non ne ha saputo niente"
    )


@requires_jsdom
def test_a_brand_that_starts_answering_mid_visit_opens() -> None:
    """Una marca aggiunta con «Usala adesso» risponde subito: il suo gruppo si
    apre. Quello di prima resta com'era: chiuderlo sposterebbe la pagina."""
    out = _run(
        """
s.data = { ...s.data, default_provider: 'sec', agent: { model: 's1' } };
s._paint();
await settle();
out.open = ['og', 'sec'].map(isOpen);
"""
    )
    assert out["open"] == [True, True]


@requires_jsdom
def test_one_count_for_the_rows_you_can_pick() -> None:
    out = _run(
        """
s.data = { ...s.data, agent: { model: 'fuori-elenco' } };
s._paint();
await settle();
const f = group('og').querySelector('[data-brand-filter]').placeholder;
group('og').querySelector('[data-brand-toggle]').click();
out.filter = f;
out.closed = count('og');
"""
    )
    assert out["filter"] == "settings.brandFilter 9"
    assert out["closed"] == "settings.modelsCount 9", "chiuso e aperto dicono due numeri diversi"
