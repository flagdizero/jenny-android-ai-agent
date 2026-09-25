/** La casa — «Chi risponde»: quale modello parla, e per conto di quale marca.
 *
 *  L'avevo tolta da questo giro con una ragione mia — «una chiave API si
 *  incolla in officina» — e quella ragione era paternalistica: in casa si
 *  sceglie chi risponde come si sceglie il tema. Piano:
 *  `.agent/casa-tu-e-jenny-resto-plan.md`, riga 2.
 *
 *  **Le mattonelle sono i provider gia' configurati**, non un catalogo di
 *  marche fra cui aggiungerne una nuova: scegliere una marca che non c'e'
 *  vuol dire conoscerne l'endpoint — per OpenCode Go sono tre campi — e quel
 *  meccanismo ha un piano suo (`provider-presets-plan.md`), proposto e non
 *  fatto. Qui si sceglie fra quelli che esistono.
 *
 *  **Toccare una mattonella non cambia chi risponde**, mostra i suoi modelli.
 *  Il cambio e' il tocco su un modello, e salva le due cose **insieme** —
 *  `model` e `default_provider` in una chiamata sola. E' il punto del
 *  redesign dell'officina (v. `_selectModel` in `mobile-settings.js`) e vale
 *  anche qui: un modello di OpenAI attivato con Anthropic come provider e'
 *  una config che non risponde, e per un attimo esisterebbe davvero.
 *
 *  **Le parole accanto ai modelli non ci sono**, e non e' una dimenticanza:
 *  «veloce, economico» non viene da nessuna API — i provider tornano id e
 *  basta. Scriverle noi vorrebbe dire una tabella di modelli che invecchia da
 *  sola. C'e' l'id, sotto la marca a cui appartiene.
 *
 *  **La chiave la incolli tu.** Il campo c'e' e il suggerimento offuscato
 *  arriva dal server (`api_key_hint`): la chiave vera non torna mai al
 *  client, quindi il campo parte vuoto e quel che non riscrivi resta com'e'.
 */

import { api } from './shared/api-client.js';
import { i18n } from './shared/i18n.js';
import { getProviderBrand } from './shared/provider-brand.js';
import { showToast } from './shared/utils.js';

/** Il nome di marca, per una pastiglia e per un titolo.
 *
 *  «Anthropic Compatible» e' il nome che l'officina usa nelle sue schede
 *  larghe, dove quella parola dice qualcosa: che il formato e' quello, non
 *  che la marca e' quella. In una striscia di pastiglie, e in «Modelli di
 *  …», e' la parola che non distingue niente — e occupa il posto di quella
 *  che distingue. Stessa ragione, e stessa forma, di `shortThemeName` in
 *  `home-you.js`: il nome intero resta dov'e' largo.
 */
export function shortBrand(label) {
  return String(label || '').replace(/\s+Compatible$/, '');
}

/** I nomi da scrivere sulle mattonelle, uno per provider configurato.
 *
 *  Di norma e' il nome di marca — «OpenCode» si legge meglio di
 *  `opencode_go`. Ma due provider configurati possono cadere sulla **stessa**
 *  marca (`opencode_go` e `opencode_zen` sono entrambi «OpenCode», v. gli
 *  alias in `shared/provider-brand.js`): due mattonelle identiche sono peggio
 *  di due nomi tecnici, perche' una delle due e' accesa e non si sa quale.
 *  In quel caso vincono i nomi configurati, che sono unici per costruzione.
 */
export function tileNames(providers) {
  const brands = (providers || []).map((p) => shortBrand(getProviderBrand(p.name).label));
  const howMany = {};
  for (const brand of brands) howMany[brand] = (howMany[brand] || 0) + 1;
  return (providers || []).map((p, i) => (howMany[brands[i]] > 1 ? p.name : brands[i]));
}

/** La riga che si legge senza entrare: la marca di chi risponde adesso. */
export function modelValue({ providers, active }) {
  const list = providers || [];
  const i = list.findIndex((p) => p.name === active);
  if (i < 0) return i18n.t('home.model.none');
  return tileNames(list)[i];
}

export class HomeModel {
  /** @param onSettings  il payload fresco che torna da un salvataggio: e' la
   *                     stessa forma di `/api/settings`, quindi il guscio lo
   *                     ridistribuisce invece di richiederlo. */
  constructor({ onSettings } = {}) {
    this.el = document.getElementById('home-model-room');
    this.brandsEl = document.getElementById('home-providers');
    this.brandsLabel = document.getElementById('home-providers-label');
    this.brandsValue = document.getElementById('home-providers-value');
    this.keyRow = document.getElementById('home-key-row');
    this.keyLabel = document.getElementById('home-key-label');
    this.keyHint = document.getElementById('home-key-hint');
    this.keyBtn = document.getElementById('home-key-btn');
    this.keyEdit = document.getElementById('home-key-edit');
    this.keyInput = document.getElementById('home-key-input');
    this.keySave = document.getElementById('home-key-save');
    this.modelsLabel = document.getElementById('home-models-label');
    this.modelsEl = document.getElementById('home-models');
    this.modelsNote = document.getElementById('home-models-note');
    this.restartNote = document.getElementById('home-model-restart');

    this._onSettings = onSettings;
    /** Quel che il server dice: `null` finche' non l'ha detto. */
    this.data = null;
    /** Di quale provider si stanno guardando i modelli. Non e' quello attivo:
     *  si guarda un elenco prima di sceglierne uno. */
    this.viewing = null;
    /** Cataloghi gia' chiesti, per nome di provider. Una richiesta per
     *  provider e per apertura: l'elenco lo va a chiedere al provider vero,
     *  e passa dalla rete. */
    this._catalogs = new Map();

    this.brandsEl?.addEventListener('click', (e) => {
      const tile = e.target.closest('[data-provider]');
      if (tile) this.pickProvider(tile.dataset.provider);
    });
    this.modelsEl?.addEventListener('click', (e) => {
      const row = e.target.closest('[data-model]');
      if (row) this.pickModel(row.dataset.model);
    });
    this.keyBtn?.addEventListener('click', () => this.toggleKeyEdit());
    this.keySave?.addEventListener('click', () => this.saveKey());
  }

  /** La stanza si apre. I dati sono gia' arrivati: li porta chi la apre. */
  open() {
    this._paint();
    this._loadModels(this.viewing);
  }

  /** Quel che sa il server. La stessa forma di `/api/settings`. */
  setSettings(data) {
    this.data = data || null;
    const active = this.data?.default_provider || null;
    /* Il provider guardato segue quello attivo finche' non ne scegli un
       altro: dopo un salvataggio i due coincidono comunque, e ripiombare
       sull'attivo mentre stai guardando l'elenco di un'altra marca vorrebbe
       dire cambiare pagina sotto le mani. */
    if (!this.viewing || !this._provider(this.viewing)) this.viewing = active;
    this._paint();
  }

  /** Il nome della marca che stai **guardando**, per il titolo dell'elenco.
   *
   *  Non e' `value()`: quella e' chi risponde. Confonderle vuol dire una
   *  scheda che dice «Modelli di OpenCode» sopra i modelli di Anthropic —
   *  visto sul rig, e non e' un dettaglio: e' l'unica frase che dice di chi
   *  sono le righe che stai per toccare.
   */
  viewName() {
    const list = this.data?.providers || [];
    const i = list.findIndex((p) => p.name === this.viewing);
    return i < 0 ? '' : tileNames(list)[i];
  }

  /** Il valore per la riga di «Tu e Jenny»: la marca di chi risponde. */
  value() {
    return modelValue({
      providers: this.data?.providers || [],
      active: this.data?.default_provider || null,
    });
  }

  applyTranslations() {
    if (this.brandsLabel) this.brandsLabel.textContent = i18n.t('home.model.who');
    if (this.keyLabel) this.keyLabel.textContent = i18n.t('home.model.key');
    if (this.keySave) this.keySave.textContent = i18n.t('home.model.keySave');
    if (this.keyInput) this.keyInput.placeholder = i18n.t('home.model.keyPlaceholder');
    this._paint();
  }

  /** Guarda i modelli di un'altra marca. **Non** cambia chi risponde. */
  pickProvider(name) {
    if (!this._provider(name)) return;
    this.viewing = name;
    this._paint();
    this._loadModels(name);
  }

  /** Cambia modello — e provider, nella stessa chiamata. */
  async pickModel(model) {
    const provider = this.viewing;
    if (!model || !provider) return;
    try {
      const payload = await api.updateSettings({ model, default_provider: provider });
      this._apply(payload);
      showToast(i18n.t('home.model.saved'), 'success');
    } catch (err) {
      console.warn('home.model: model not changed', err);
      showToast(i18n.t('home.model.failed'), 'error');
    }
  }

  /** Apre o chiude il campo della chiave. */
  toggleKeyEdit() {
    if (!this.keyEdit) return;
    const open = !this.keyEdit.hidden;
    this.keyEdit.hidden = open;
    if (!open) this.keyInput?.focus();
    else if (this.keyInput) this.keyInput.value = '';
  }

  /** Salva la chiave che hai incollato. Vuota non salva niente: sarebbe il
   *  modo piu' silenzioso di cancellare la chiave buona. */
  async saveKey() {
    const provider = this.viewing;
    const key = (this.keyInput?.value || '').trim();
    if (!provider || !key) return;
    try {
      const payload = await api.updateProvider({ name: provider, api_key: key });
      if (this.keyInput) this.keyInput.value = '';
      if (this.keyEdit) this.keyEdit.hidden = true;
      this._apply(payload);
      /* Il catalogo si richiede: una chiave nuova puo' essere esattamente la
         ragione per cui l'elenco era vuoto. */
      this._catalogs.delete(provider);
      this._loadModels(provider);
      showToast(i18n.t('home.model.keySaved'), 'success');
    } catch (err) {
      console.warn('home.model: key not saved', err);
      showToast(err?.message || i18n.t('home.model.failed'), 'error');
    }
  }

  /* ── Sotto ──────────────────────────────────────────────────────────── */

  _provider(name) {
    return (this.data?.providers || []).find((p) => p.name === name) || null;
  }

  /* Il payload che torna da un salvataggio e' gia' quello di `/api/settings`:
     si usa qui e si passa al guscio, invece di chiederlo una seconda volta. */
  _apply(payload) {
    if (!payload) return;
    this.setSettings(payload);
    this._sayRestart(!!payload.requires_restart);
    this._onSettings?.(payload);
  }

  async _loadModels(provider) {
    if (!provider || !this.modelsEl) return;
    if (this._catalogs.has(provider)) {
      this._paintModels();
      return;
    }
    this._catalogs.set(provider, { status: 'loading', models: [] });
    this._paintModels();
    let outcome;
    try {
      const res = await api.getProviderModels(provider);
      outcome = {
        status: res?.status || 'available',
        models: (res?.models || []).map((m) => m.id || m),
        message: res?.message || '',
      };
    } catch (err) {
      console.warn('home.model: model list not read', err);
      outcome = { status: 'error', models: [], message: '' };
    }
    this._catalogs.set(provider, outcome);
    /* Il catalogo si tiene comunque; il ridisegno si salta se nel frattempo
       hai cambiato marca. Non e' quel che impedisce a una risposta in ritardo
       di scavalcare l'elenco che stai leggendo — quello lo fa `_paintModels`,
       che dipinge **il provider guardato** e non quello appena arrivato: e'
       solo un ridisegno che non serve a nessuno. */
    if (this.viewing === provider) this._paintModels();
  }

  _paint() {
    this._paintBrands();
    this._paintModels();
  }

  /* Le mattonelle si ridisegnano a ogni passaggio: l'elenco dei provider
     cambia da sotto (una chiave salvata cambia `configured`), ed e' corto. */
  _paintBrands() {
    if (!this.brandsEl) return;
    const list = this.data?.providers || [];
    const names = tileNames(list);
    this.brandsEl.replaceChildren();
    for (const [i, p] of list.entries()) {
      const tile = document.createElement('button');
      tile.type = 'button';
      tile.className = 'home-brand';
      tile.dataset.provider = p.name;
      tile.setAttribute('role', 'radio');
      const active = p.name === this.data?.default_provider;
      /* Due cose diverse, e vanno dette diversamente: **acceso** e' chi
         risponde adesso, **guardato** e' l'elenco che stai leggendo. */
      tile.classList.toggle('is-on', active);
      tile.classList.toggle('is-viewing', p.name === this.viewing);
      tile.setAttribute('aria-checked', String(active));

      const dot = document.createElement('span');
      dot.className = 'home-brand-dot';
      dot.style.background = getProviderBrand(p.name).color;
      tile.appendChild(dot);

      const name = document.createElement('span');
      name.className = 'home-brand-name';
      name.textContent = names[i];
      tile.appendChild(name);

      /* Chi risponde porta il segno, e non solo un anello d'accento: nei temi
         chiari `--overlay` e `--overlay-strong` sono quasi lo stesso bianco, e
         a schermo non si distingueva quale elenco stessi leggendo (visto sul
         rig, tema Y2K). Il segno prende `currentColor`, quindi resta leggibile
         sia sulla pastiglia piena sia su quella vuota. */
      if (active) {
        const mark = document.createElement('i');
        mark.className = 'ti ti-check';
        mark.setAttribute('aria-hidden', 'true');
        tile.appendChild(mark);
      }
      this.brandsEl.appendChild(tile);
    }
    if (this.brandsValue) this.brandsValue.textContent = this.value();
    this._paintKey();
  }

  _paintKey() {
    if (!this.keyRow) return;
    const p = this._provider(this.viewing);
    this.keyRow.hidden = !p;
    if (!p) return;
    if (this.keyHint) {
      this.keyHint.textContent = p.api_key_hint || i18n.t('home.model.keyNone');
      this.keyHint.classList.toggle('is-faint', !p.api_key_hint);
    }
    if (this.keyBtn) {
      this.keyBtn.textContent = i18n.t(
        p.api_key_hint ? 'home.model.keyChange' : 'home.model.keyAdd',
      );
    }
  }

  _paintModels() {
    if (!this.modelsEl) return;
    const p = this._provider(this.viewing);
    if (this.modelsLabel) {
      this.modelsLabel.textContent = p
        ? i18n.t('home.model.models', { provider: this.viewName() })
        : i18n.t('home.model.none');
    }
    const catalog = this._catalogs.get(this.viewing) || { status: 'loading', models: [] };
    const corrente = this.data?.agent?.model || '';
    const active = this.viewing === this.data?.default_provider;
    /* Il modello in uso sta in cima **anche se il provider non lo elenca**:
       puo' essere un id battuto a mano in officina, o l'elenco puo' non
       essere arrivato. Non vederlo da nessuna parte vorrebbe dire una stanza
       che non risponde alla domanda che ha in testa. */
    const rows = [...catalog.models];
    if (active && corrente && !rows.includes(corrente)) rows.unshift(corrente);

    this.modelsEl.replaceChildren();
    for (const id of rows) {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'home-model';
      row.dataset.model = id;
      const on = active && id === corrente;
      row.classList.toggle('is-on', on);
      row.setAttribute('aria-checked', String(on));
      row.setAttribute('role', 'radio');

      const name = document.createElement('span');
      name.className = 'home-model-id';
      name.textContent = id;
      row.appendChild(name);

      const mark = document.createElement('i');
      mark.className = on ? 'ti ti-check' : 'ti';
      mark.setAttribute('aria-hidden', 'true');
      row.appendChild(mark);
      this.modelsEl.appendChild(row);
    }
    this._sayModels(catalog, rows.length);
  }

  /* Perche' l'elenco e' corto o vuoto. I quattro stati del server hanno una
     frase ciascuno: `message` e' diagnostica in inglese, e in casa la lingua
     e' quella del telefono. Se lo stato non si riconosce si mostra comunque
     quel che il server ha detto, che e' meglio del silenzio. */
  _sayModels(catalog, howMany) {
    if (!this.modelsNote) return;
    const keys = {
      loading: 'home.model.loading',
      not_configured: 'home.model.needsKey',
      missing_api_base: 'home.model.needsBase',
      error: 'home.model.listFailed',
    };
    const key = keys[catalog.status];
    const text = key ? i18n.t(key) : (howMany ? '' : catalog.message || '');
    this.modelsNote.textContent = text;
    this.modelsNote.hidden = !text;
  }

  _sayRestart(serve) {
    if (!this.restartNote) return;
    this.restartNote.hidden = !serve;
    if (serve) this.restartNote.textContent = i18n.t('home.model.restart');
  }
}
