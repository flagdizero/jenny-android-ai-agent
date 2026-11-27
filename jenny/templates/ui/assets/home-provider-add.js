/** La casa — «Aggiungi chi risponde»: un provider nuovo, senza l'officina.
 *
 *  Un foglio dal basso aperto dalla riga in fondo alla scheda delle marche
 *  (`home-model.js`), in tre passi:
 *
 *  1. **Come parla** — compatibile OpenAI o compatibile Anthropic. Sono i due
 *     formati che il server conosce; un elenco di marche con l'indirizzo dentro
 *     invecchierebbe da solo, e il primo avvio chiede gia' cosi'.
 *  2. **Dove e con che chiave** — indirizzo (vuoto = quello del formato), nome
 *     (si scrive da solo dall'indirizzo finche' non lo tocchi) e chiave. Un
 *     nome gia' usato ferma il passo: il server lo rifiuterebbe comunque
 *     (`name_taken`), e l'elenco dei modelli di un nome gia' usato sarebbe
 *     quello del provider salvato, non di quello che stai scrivendo
 *     (`provider_models_payload`).
 *  3. **Quale modello** — l'elenco del servizio, o il campo per scriverlo
 *     quando l'elenco non arriva. «Prova e salva» manda tutto a
 *     `settings.provider.add`, che fa la domanda di prova del primo avvio e
 *     salva solo se risponde. Chi risponde lo cambia poi `home-model.js`, con
 *     lo stesso `switchModel` del tocco su un modello.
 *
 *  Restano in officina la modifica di un provider, il file CA privato e
 *  l'eliminazione.
 */

import { i18n } from './shared/i18n.js';
import { rpc } from './shared/rpc-client.js';
import { button, el, icon } from './shared/dom.js';
import { closeActionSheet, openActionSheet } from './shared/sheet.js';
import { DEFAULT_API_BASE, normalizeApiBase } from './shared/api-base.js';
import { getProviderBrand } from './shared/provider-brand.js';
import { CATALOG_NOTE_KEYS, PROBE_KINDS, PROBE_SAVE_ANYWAY } from './shared/model-catalog.js';

const FORMATS = ['openai_compat', 'anthropic'];
/** Sopra quanti modelli l'elenco porta la ricerca. */
const SEARCH_FROM = 8;

/** Il nome che un indirizzo suggerisce, libero fra *taken*.
 *
 *  Il nome di secondo livello dell'host — `api.deepseek.com` → `deepseek`,
 *  `openrouter.ai` → `openrouter` — perche' e' quello che `getProviderBrand`
 *  riconosce e colora; un indirizzo numerico resta com'e'. Vuoto vuol dire
 *  l'indirizzo predefinito del formato, e il nome e' quello della sua marca.
 *  Se e' gia' preso si aggiunge `_2`, `_3`: il nome e' la chiave con cui il
 *  server ritrova un provider, e due uguali non possono esistere. */
export function nameFromBase(base, format, taken = []) {
  let stem = '';
  const text = String(base || '').trim();
  if (!text) {
    stem = format === 'anthropic' ? 'anthropic' : 'openai';
  } else {
    let host = '';
    try {
      host = new URL(text.includes('://') ? text : `https://${text}`).hostname;
    } catch {
      host = '';
    }
    if (/^[\d.]+$/.test(host) || host.includes(':')) {
      stem = host;
    } else {
      const labels = host.split('.').filter(Boolean);
      stem = labels.length >= 2 ? labels[labels.length - 2] : (labels[0] || '');
    }
  }
  stem = stem.toLowerCase();
  if (!stem) return '';
  const used = new Set(taken);
  if (!used.has(stem)) return stem;
  for (let n = 2; ; n++) {
    if (!used.has(`${stem}_${n}`)) return `${stem}_${n}`;
  }
}

/** Cosa ferma il passo 2, o `null`: il primo campo che non va, dall'alto.
 *  `base` (indirizzo non valido), `name` (vuoto), `nameTaken`, `key` (vuota:
 *  la fabbrica dei provider non ne costruisce uno senza chiave). */
export function stepTwoBlock({ base, name, key }, taken = []) {
  if (normalizeApiBase(base).error) return 'base';
  const n = String(name || '').trim();
  if (!n) return 'name';
  if (taken.includes(n)) return 'nameTaken';
  if (!String(key || '').trim()) return 'key';
  return null;
}

/** Il foglio. *getProviders* da' i nomi gia' usati; *onAdded* riceve il
 *  payload del server (le impostazioni intere) e `{ name, model, useNow }`. */
export class HomeProviderAdd {
  constructor({ getProviders, onAdded } = {}) {
    this.sheet = document.getElementById('home-provider-sheet');
    this._getProviders = getProviders || (() => []);
    this._onAdded = onAdded;
    this._reset();
  }

  _reset() {
    this.step = 1;
    this.format = 'openai_compat';
    this.base = '';
    this.name = '';
    /** Toccato a mano, il nome non segue piu' l'indirizzo. */
    this.nameTouched = false;
    this.key = '';
    /** Il server ha detto che il nome e' preso (fra il passo 2 e il salvataggio). */
    this.nameRefused = '';
    /** Il servizio ha rifiutato la chiave chiedendo l'elenco. */
    this.keyRejected = false;
    /** L'elenco: `{ status, models }`, `null` mentre arriva. */
    this.catalog = null;
    this.model = '';
    this.typed = '';
    this.search = '';
    this.useNow = true;
    this.busy = false;
    /** Il tipo dell'ultima prova fallita (`onboarding.probe.<tipo>`), o `null`. */
    this.failure = null;
    /** Una risposta in ritardo non dipinge un passo che non e' piu' il suo. */
    this._token = 0;
  }

  _taken() {
    return (this._getProviders() || []).map((p) => p.name);
  }

  open() {
    this._reset();
    this.name = nameFromBase('', this.format, this._taken());
    this._render();
  }

  close() {
    this._token++;
    closeActionSheet(this.sheet);
  }

  /* ── Disegno ────────────────────────────────────────────────────────── */

  _render() {
    if (!this.sheet) return;
    const body = this.step === 1 ? this._stepOne()
      : this.step === 2 ? this._stepTwo() : this._stepThree();
    openActionSheet(this.sheet, {
      children: [this._top(), ...body],
      closeLabel: i18n.t('common.close'),
      onClose: () => this.close(),
      cls: 'home-provider-sheet',
    });
  }

  _top() {
    const top = el('div', 'home-padd-top');
    if (this.step > 1) {
      const back = button('home-padd-back', null, () => this._goTo(this.step - 1));
      back.setAttribute('aria-label', i18n.t('header.back'));
      back.appendChild(icon('ti-arrow-left'));
      top.appendChild(back);
    }
    top.appendChild(el('h2', 'home-plan-sheet-title', i18n.t(`home.model.add.title${this.step}`)));
    const steps = el('span', 'home-padd-steps');
    steps.setAttribute('role', 'img');
    steps.setAttribute('aria-label', i18n.t('home.model.add.stepOf', { n: this.step, of: 3 }));
    for (let i = 1; i <= 3; i++) steps.appendChild(el('i', i === this.step ? 'is-on' : ''));
    top.appendChild(steps);
    return top;
  }

  _goTo(step) {
    this._token++;
    this.busy = false;
    this.step = step;
    this._render();
  }

  /* Passo 1 — come parla. */
  _stepOne() {
    const lead = el('p', 'home-padd-lead', i18n.t('home.model.add.lead1'));
    const cards = el('div', 'home-padd-formats');
    cards.setAttribute('role', 'radiogroup');
    for (const format of FORMATS) {
      const on = this.format === format;
      const card = button(`home-padd-format${on ? ' is-on' : ''}`, null, () => {
        if (this.format === format) return;
        this.format = format;
        /* Un indirizzo vuoto segue il formato: anche il nome suggerito. */
        if (!this.nameTouched) this.name = nameFromBase(this.base, format, this._taken());
        this.catalog = null;
        this._render();
      });
      card.setAttribute('role', 'radio');
      card.setAttribute('aria-checked', String(on));
      card.dataset.format = format;
      const glyph = el('span', 'home-padd-format-icon');
      glyph.appendChild(icon(format === 'anthropic' ? 'ti-message-chatbot' : 'ti-plug'));
      const text = el('span', 'home-padd-format-text');
      text.append(
        el('b', null, i18n.t(format === 'anthropic' ? 'onboarding.anthropicCompat' : 'settings.openaiCompat')),
        el('span', null, i18n.t(format === 'anthropic'
          ? 'onboarding.anthropicCompatHint' : 'onboarding.openaiCompatHint')),
      );
      card.append(glyph, text, el('span', 'home-padd-radio'));
      cards.appendChild(card);
    }
    const next = button('home-plan-btn is-primary home-padd-wide', i18n.t('onboarding.next'),
      () => this._goTo(2));
    return [lead, cards, this._actions(next)];
  }

  /* Passo 2 — dove e con che chiave. I campi si aggiornano sul posto mentre
     scrivi: ridisegnare il foglio a ogni tasto toglierebbe il fuoco al campo. */
  _stepTwo() {
    const nodes = [];
    nodes.push(this._chip(i18n.t(this.format === 'anthropic'
      ? 'onboarding.anthropicCompat' : 'settings.openaiCompat')));

    const baseInput = this._input('home-padd-base', this.base, {
      type: 'url', inputmode: 'url', placeholder: DEFAULT_API_BASE[this.format] || '',
    });
    const baseNote = el('p', 'home-padd-note');
    const nameDot = el('span', 'home-padd-dot');
    const nameInput = this._input('home-padd-name', this.name);
    const nameNote = el('p', 'home-padd-note');
    const keyInput = this._input('home-padd-key', this.key, {
      type: 'password', placeholder: i18n.t('home.model.keyPlaceholder'),
    });
    keyInput.dataset.lpignore = 'true';
    const keyNote = el('p', 'home-padd-note is-bad');
    const next = button('home-plan-btn is-primary home-padd-wide', i18n.t('onboarding.next'),
      () => this._toModels());

    const sync = () => {
      const taken = this._taken();
      const block = stepTwoBlock(this, taken);
      const refused = this.nameRefused && this.nameRefused === this.name.trim();
      next.disabled = Boolean(block) || refused;
      baseNote.classList.toggle('is-bad', block === 'base');
      /* Cosa vuol dire il campo vuoto si dice solo a campo vuoto. */
      baseNote.textContent = block === 'base'
        ? i18n.t('settings.baseUrlInvalid')
        : (this.base.trim() ? ''
          : i18n.t('home.model.add.baseDefault', { url: DEFAULT_API_BASE[this.format] || '' }));
      baseNote.hidden = !baseNote.textContent;
      nameDot.style.background = getProviderBrand(this.name.trim()).color;
      const nameBad = block === 'nameTaken' || refused;
      nameInput.classList.toggle('is-bad', nameBad);
      nameNote.classList.toggle('is-bad', nameBad);
      nameNote.textContent = nameBad ? i18n.t('home.model.add.nameTaken')
        : (this.nameTouched ? '' : i18n.t('home.model.add.nameHint'));
      nameNote.hidden = !nameNote.textContent;
      keyInput.classList.toggle('is-bad', this.keyRejected);
      keyNote.textContent = this.keyRejected ? i18n.t('common.modelsKeyRejected') : '';
      keyNote.hidden = !this.keyRejected;
    };
    baseInput.addEventListener('input', () => {
      this.base = baseInput.value;
      this.catalog = null;
      if (!this.nameTouched) {
        this.name = nameFromBase(this.base, this.format, this._taken());
        nameInput.value = this.name;
      }
      sync();
    });
    nameInput.addEventListener('input', () => {
      this.name = nameInput.value;
      this.nameTouched = true;
      this.catalog = null;
      sync();
    });
    keyInput.addEventListener('input', () => {
      this.key = keyInput.value;
      this.keyRejected = false;
      this.catalog = null;
      sync();
    });

    const nameRow = el('div', 'home-padd-field');
    nameRow.append(nameDot, nameInput);
    nodes.push(
      this._label('home.model.add.base', 'home-padd-base'), this._row(baseInput), baseNote,
      this._label('home.model.add.name', 'home-padd-name'), nameRow, nameNote,
      this._label('home.model.key', 'home-padd-key'), this._row(keyInput), keyNote,
      this._actions(next),
    );
    sync();
    return nodes;
  }

  /* Passo 3 — quale modello. */
  _stepThree() {
    const nodes = [];
    const catalog = this.catalog;
    const count = catalog?.models?.length || 0;
    nodes.push(this._chip(count
      ? `${this.name.trim()} · ${i18n.t('home.model.add.count', { n: count })}`
      : `${this.name.trim()} · ${i18n.t(this.format === 'anthropic'
        ? 'onboarding.anthropicCompat' : 'settings.openaiCompat')}`));

    if (!catalog) {
      nodes.push(el('p', 'home-padd-note', i18n.t('common.modelsLoading')));
      return nodes;
    }

    if (count) {
      nodes.push(...this._modelList(catalog.models));
    } else {
      const warn = el('div', 'home-padd-alert is-soft');
      warn.appendChild(icon('ti-list-search'));
      const text = el('div');
      text.append(
        el('b', null, i18n.t('home.model.add.noList')),
        el('span', null, this._catalogNote(catalog.status)),
      );
      warn.appendChild(text);
      const typed = this._input('home-padd-model', this.typed, {
        placeholder: i18n.t('home.model.add.modelPlaceholder'),
      });
      typed.addEventListener('input', () => {
        this.typed = typed.value;
        this.model = typed.value.trim();
        this._syncSave();
      });
      nodes.push(warn, this._label('home.model.add.model', 'home-padd-model'), this._row(typed));
    }

    const sw = button(`home-switch${this.useNow ? ' is-on' : ''}`, null, () => {
      this.useNow = !this.useNow;
      sw.classList.toggle('is-on', this.useNow);
      sw.setAttribute('aria-checked', String(this.useNow));
    });
    sw.setAttribute('role', 'switch');
    sw.setAttribute('aria-checked', String(this.useNow));
    sw.setAttribute('aria-labelledby', 'home-padd-usenow');
    sw.appendChild(el('span', 'home-switch-knob'));
    const swText = el('span', 'home-padd-switch-text');
    const swLabel = el('span', null, i18n.t('home.model.add.useNow'));
    swLabel.id = 'home-padd-usenow';
    swText.append(swLabel, el('small', null, i18n.t('home.model.add.useNowHint')));
    const swRow = el('div', 'home-padd-switch');
    swRow.append(swText, sw);
    nodes.push(swRow);

    if (this.failure) {
      nodes.push(this._failureBox(this.failure));
    } else {
      this._save = button('home-plan-btn is-primary home-padd-wide',
        i18n.t('home.model.add.save'), () => this._submit());
      this._saveHint = el('p', 'home-padd-note');
      nodes.push(this._actions(this._save), this._saveHint);
      this._syncSave();
    }
    return nodes;
  }

  _modelList(models) {
    const nodes = [];
    const list = el('div', 'home-padd-models');
    list.setAttribute('role', 'radiogroup');
    const paint = () => {
      const q = this.search.trim().toLowerCase();
      const shown = q ? models.filter((m) => m.toLowerCase().includes(q)) : models;
      list.replaceChildren();
      for (const id of shown) {
        const on = id === this.model;
        const row = button(`home-padd-model${on ? ' is-on' : ''}`, null, () => {
          this.model = id;
          paint();
          this._syncSave();
        });
        row.setAttribute('role', 'radio');
        row.setAttribute('aria-checked', String(on));
        row.dataset.model = id;
        row.append(el('span', 'home-padd-model-id', id), el('span', 'home-padd-radio'));
        list.appendChild(row);
      }
      if (!shown.length) list.appendChild(el('p', 'home-padd-note', i18n.t('home.model.add.noMatch')));
    };
    if (models.length > SEARCH_FROM) {
      const search = this._input('home-padd-search', this.search, {
        type: 'search', placeholder: i18n.t('home.model.add.search'),
      });
      search.setAttribute('aria-label', i18n.t('home.model.add.search'));
      search.addEventListener('input', () => { this.search = search.value; paint(); });
      nodes.push(this._row(search));
    }
    paint();
    nodes.push(list);
    return nodes;
  }

  _syncSave() {
    if (!this._save) return;
    const model = this.model.trim();
    this._save.disabled = this.busy || !model;
    if (this._saveHint) {
      this._saveHint.textContent = this.busy ? i18n.t('onboarding.probe.trying')
        : (model ? i18n.t('home.model.add.saveHint', { model }) : '');
      this._saveHint.classList.toggle('is-busy', this.busy);
    }
  }

  /** L'avviso di una prova fallita, con i bottoni del primo avvio: dove si
   *  ripara, Riprova, e «Salva lo stesso» solo dove la chiave puo' essere
   *  giusta (`PROBE_SAVE_ANYWAY`). */
  _failureBox(kind) {
    const anyway = PROBE_SAVE_ANYWAY.has(kind);
    const box = el('div', `home-padd-alert${anyway ? ' is-soft' : ''}`);
    box.setAttribute('role', 'alert');
    box.appendChild(icon(kind === 'auth' ? 'ti-key-off'
      : kind === 'connection' ? 'ti-wifi-off' : 'ti-alert-triangle'));
    const text = el('div');
    text.appendChild(el('span', null, i18n.t(`onboarding.probe.${kind}`)));
    if (anyway) text.appendChild(el('span', null, i18n.t('onboarding.probe.mayBeRight')));
    const actions = el('div', 'home-padd-alert-actions');
    if (kind === 'model') {
      actions.appendChild(button('home-plan-btn is-small', i18n.t('onboarding.probe.changeModel'),
        () => { this.failure = null; this._render(); }));
    } else {
      actions.appendChild(button('home-plan-btn is-small', i18n.t(
        kind === 'auth' || kind === 'credit' ? 'onboarding.probe.changeKey' : 'home.model.add.changeBase',
      ), () => { this.failure = null; this._goTo(2); }));
    }
    actions.appendChild(button('home-plan-btn is-small', i18n.t('onboarding.probe.retry'),
      () => this._submit()));
    if (anyway) {
      actions.appendChild(button('home-plan-btn is-small is-primary',
        i18n.t('onboarding.probe.saveAnyway'), () => this._submit({ skipProbe: true })));
    }
    text.appendChild(actions);
    box.appendChild(text);
    return box;
  }

  /* ── Le richieste ───────────────────────────────────────────────────── */

  /** Dal passo 2 al 3: l'elenco dei modelli, con nome, chiave, indirizzo e
   *  formato non ancora salvati — il nome e' nuovo, quindi il server usa
   *  proprio questi (`provider_models_payload`). */
  async _toModels() {
    if (stepTwoBlock(this, this._taken())) return;
    this.failure = null;
    if (this.catalog) {
      this._goTo(3);
      return;
    }
    this.step = 3;
    this.model = '';
    this.typed = '';
    this.search = '';
    const token = ++this._token;
    this._render();
    let catalog;
    try {
      const base = normalizeApiBase(this.base).value || DEFAULT_API_BASE[this.format] || '';
      const resp = await rpc.providerModels({
        provider: this.name.trim(), apiKey: this.key.trim(), apiBase: base, format: this.format,
      });
      const models = (resp?.models || []).map((m) => m.id || m).filter(Boolean);
      catalog = { status: models.length ? 'available' : (resp?.status || 'error'), models };
    } catch (err) {
      console.warn('home.provider-add: models not listed', err);
      catalog = { status: 'error', models: [] };
    }
    if (token !== this._token) return;
    if (catalog.status === 'rejected') {
      /* La chiave rifiutata gia' dall'elenco: si torna dove si corregge. */
      this.keyRejected = true;
      this.catalog = null;
      this._goTo(2);
      return;
    }
    this.catalog = catalog;
    this._render();
  }

  /** La frase per un elenco che non e' arrivato, nella lingua della casa. */
  _catalogNote(status) {
    const keys = {
      ...CATALOG_NOTE_KEYS,
      missing_api_base: 'onboarding.modelsNeedBase',
    };
    return i18n.t(keys[status] || 'home.model.add.noListText');
  }

  async _submit({ skipProbe = false } = {}) {
    const model = this.model.trim();
    if (!model || this.busy) return;
    this.busy = true;
    this.failure = null;
    const token = ++this._token;
    this._render();
    let result;
    try {
      result = await rpc.addProvider({
        name: this.name.trim(),
        format: this.format,
        api_key: this.key.trim(),
        api_base: normalizeApiBase(this.base).value || '',
        model,
        skip_probe: skipProbe,
      });
    } catch (err) {
      /* Un rifiuto del comando (indirizzo, formato): il motivo nel log, a
         schermo una frase della casa. */
      console.warn('home.provider-add: not added', err);
      result = { ok: false, probe: 'generic' };
    }
    if (token !== this._token) return;
    this.busy = false;
    if (result?.ok) {
      const added = { name: this.name.trim(), model, useNow: this.useNow };
      this.close();
      this._onAdded?.(result, added);
      return;
    }
    if (result?.error === 'name_taken') {
      this.nameRefused = this.name.trim();
      this._goTo(2);
      return;
    }
    this.failure = PROBE_KINDS.has(result?.probe) ? result.probe : 'generic';
    this._render();
  }

  /* ── Mattoni ────────────────────────────────────────────────────────── */

  _chip(text) {
    return el('span', 'home-padd-chip', text);
  }

  _label(key, forId) {
    const label = el('label', 'home-padd-label', i18n.t(key));
    label.htmlFor = forId;
    return label;
  }

  _input(id, value, attrs = {}) {
    const input = el('input', 'home-key-input');
    input.id = id;
    input.value = value || '';
    input.type = attrs.type || 'text';
    if (attrs.placeholder) input.placeholder = attrs.placeholder;
    if (attrs.inputmode) input.setAttribute('inputmode', attrs.inputmode);
    for (const [k, v] of Object.entries({
      autocomplete: 'off', autocorrect: 'off', autocapitalize: 'none', spellcheck: 'false',
    })) input.setAttribute(k, v);
    return input;
  }

  _row(input) {
    const row = el('div', 'home-padd-field');
    row.appendChild(input);
    return row;
  }

  _actions(...buttons) {
    const row = el('div', 'home-padd-actions');
    row.append(...buttons);
    return row;
  }
}
