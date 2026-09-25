/** La casa — «Jenny»: com'e' fatta.
 *
 *  Quanto e' grande, se si vede, se sta sopra le altre app. Tre cose che
 *  esistono gia' tutte: la taglia e la visibilita' vivono in
 *  `shared/mascot.js` (localStorage, le stesse dell'officina — e' la stessa
 *  persona nello stesso telefono), la finestra flottante vive nel config,
 *  perche' a montarla e' il `GatewayService` all'avvio e un service non ha una
 *  WebView da cui leggere il `localStorage`.
 *
 *  **`enabled` e `active` non sono la stessa cosa**, ed e' l'unica cosa
 *  delicata di questa stanza. `enabled` e' quel che hai chiesto; `active` e'
 *  quel che Android ha concesso — la finestra vuole `SYSTEM_ALERT_WINDOW`, che
 *  si da' da una schermata di sistema. A permesso negato la config resta
 *  accesa e il payload lo dice: l'interruttore **non** rimbalza su spento, si
 *  accende e spiega. Un interruttore che torna indietro da solo, senza dire
 *  perche', e' peggio di un interruttore che manca.
 *
 *  Il nome della stanza e' «Jenny» e non «Mascotte» di proposito: qui dentro
 *  c'e' anche chi e', e `SOUL.md` decide come parla dappertutto — in chat, nel
 *  fumetto, nella tendina, su Telegram. Sotto un'etichetta che dice «mascotte»
 *  quel testo sembrerebbe una preferenza di disegno.
 */

import { api } from './shared/api-client.js';
import { i18n } from './shared/i18n.js';
import { rpc } from './shared/rpc-client.js';
import { showToast } from './shared/utils.js';
import { MASCOT_SIZES, mascotSize, mascotVisible, setMascotSize, setMascotVisible }
  from './shared/mascot.js';

/* Dove stanno le parole dell'utente. La stessa costante di
   `jenny/agent/soul_rules.py`, e l'unico posto da cui la casa le legge: la
   scrittura passa da un comando, perche' salvarle vuol dire anche rifare la
   copia dentro `SOUL.md`. */
export const RULES_PATH = '.jenny/soul_rules.md';

/* Le tre taglie, nell'ordine in cui crescono, e le parole che l'officina usa
   gia' per chiamarle. */
export const SIZES = ['sm', 'md', 'lg'];
export const SIZE_KEYS = {
  sm: 'settings.mascotSizeSmall',
  md: 'settings.mascotSizeMedium',
  lg: 'settings.mascotSizeLarge',
};

/** La riga che si legge senza entrare: «piccola · flottante».
 *
 *  Nascosta vince su tutto il resto — dire «media» di una mascotte che non si
 *  vede e' vero e inutile. La finestra flottante invece si aggiunge anche a
 *  quella: sono due posti diversi, e lei puo' stare sopra le altre app mentre
 *  dentro la casa non c'e'.
 */
export function jennyValue({ visible, size, floating }) {
  const parts = [];
  parts.push(visible ? i18n.t(SIZE_KEYS[size] || SIZE_KEYS.sm) : i18n.t('home.jenny.hidden'));
  if (floating) parts.push(i18n.t('home.jenny.floatingShort'));
  return parts.join(' · ').toLowerCase();
}

export class HomeJenny {
  /** @param onChange  la riga di «Tu e Jenny» si riscrive da se'.
   *  @param onFloating  com'e' finita la finestra flottante dopo un tocco: la
   *    casa ne tiene una copia (v. `HomeApp._keepFloating`).
   *  @param onName  il nome appena salvato: la casa lo scrive nella fila e nei
   *    Quaderni, e nella sua copia delle impostazioni (v. `HomeApp._keepName`). */
  constructor({ onChange, onFloating, onName } = {}) {
    this.el = document.getElementById('home-jenny-room');
    this.visibleBtn = document.getElementById('home-jenny-visible');
    this.visibleLabel = document.getElementById('home-jenny-visible-label');
    this.sizeEl = document.getElementById('home-jenny-size');
    this.sizeLabel = document.getElementById('home-jenny-size-label');
    this.floatingRow = document.getElementById('home-jenny-floating-row');
    this.floatingBtn = document.getElementById('home-jenny-floating');
    this.floatingLabel = document.getElementById('home-jenny-floating-label');
    this.floatingNote = document.getElementById('home-jenny-floating-note');
    this.nameEl = document.getElementById('home-name');
    this.nameLabel = document.getElementById('home-name-label');
    this.nameNote = document.getElementById('home-name-note');
    this.nameSave = document.getElementById('home-name-save');
    this.rulesEl = document.getElementById('home-rules');
    this.rulesLabel = document.getElementById('home-rules-label');
    this.rulesNote = document.getElementById('home-rules-note');
    this.rulesSave = document.getElementById('home-rules-save');

    this._onChange = onChange;
    this._onFloating = onFloating;
    this._onName = onName;
    /* Quel che il server dice della finestra: `null` finche' non l'ha detto. */
    this.floating = null;
    this._painted = false;

    this.visibleBtn?.addEventListener('click', () => this.toggleVisible());
    this.floatingBtn?.addEventListener('click', () => this.toggleFloating());
    this.sizeEl?.addEventListener('click', (e) => {
      const card = e.target.closest('[data-size]');
      if (card) this.pickSize(card.dataset.size);
    });
    /* Come si chiama secondo il server. `null` finche' non l'ha detto. */
    this._savedName = null;
    this.nameEl?.addEventListener('input', () => this._markName());
    this.nameSave?.addEventListener('click', () => this.saveName());

    /* Quel che c'e' su disco, per sapere se c'e' qualcosa da salvare. `null`
       finche' non si e' letto: diverso da «letto, ed era vuoto». */
    this._rulesOnDisk = null;
    this.rulesEl?.addEventListener('input', () => this._markRules());
    this.rulesSave?.addEventListener('click', () => this.saveRules());
  }

  open() {
    this._paintSizes();
    this._mark();
    /* Il «Salva» del nome parte nascosto anche nel markup, ma affidare a un
       attributo HTML l'unica garanzia che non compaia prima di sapere come si
       chiama vuol dire perderla al primo ritocco della pagina. */
    this._markName();
    this._loadRules();
  }

  /** Come si chiama, secondo il server.
   *
   *  Arriva col payload di «Tu e Jenny», come lo stato della finestra: una
   *  sola lettura per entrambi. Un campo che l'utente sta scrivendo non si
   *  sovrascrive — stesso patto delle regole qui sotto.
   *
   *  `null` e' «non lo so» — la lettura non e' riuscita — e non vuol dire
   *  «vuoto»: «Salva» resta nascosto, perche' non c'e' niente con cui
   *  confrontare quel che scrivi.
   */
  setName(name) {
    this._savedName = typeof name === 'string' ? name : null;
    if (this.nameEl && !this.nameEl.value && this._savedName !== null) {
      this.nameEl.value = this._savedName;
    }
    this._markName();
  }

  /** «Salva» c'e' solo quando c'e' qualcosa da salvare, e un nome vuoto non
   *  e' qualcosa: il server ripiegherebbe su «Jenny» senza dirlo. */
  _markName() {
    if (!this.nameSave || !this.nameEl) return;
    const written = this.nameEl.value.trim();
    this.nameSave.hidden = this._savedName === null
      || !written
      || written === this._savedName;
  }

  /** Salva il nome. Stessa chiamata con cui la casa salva il modello. */
  async saveName() {
    if (!this.nameEl) return;
    const name = this.nameEl.value.trim();
    if (!name) return;
    try {
      await api.updateSettings({ bot_name: name });
    } catch (err) {
      console.warn('casa.jenny: name not saved', err);
      showToast(i18n.t('home.jenny.nameFailed'), 'error');
      return;
    }
    this._savedName = name;
    this._markName();
    this._onName?.(name);
    showToast(i18n.t('home.jenny.rulesSaved'), 'success');
  }

  /** Quel che il server dice della finestra flottante. `null` = non si sa. */
  setFloating(section) {
    this.floating = section || null;
    this._mark();
  }

  applyTranslations() {
    if (this.visibleLabel) this.visibleLabel.textContent = i18n.t('settings.mascotVisible');
    if (this.sizeLabel) this.sizeLabel.textContent = i18n.t('settings.mascotSize');
    if (this.floatingLabel) this.floatingLabel.textContent = i18n.t('settings.floatingEnabled');
    if (this.nameLabel) this.nameLabel.textContent = i18n.t('home.jenny.name');
    if (this.nameNote) this.nameNote.textContent = i18n.t('home.jenny.nameHint');
    if (this.nameSave) this.nameSave.textContent = i18n.t('home.jenny.rulesSave');
    if (this.rulesLabel) this.rulesLabel.textContent = i18n.t('home.jenny.rules');
    if (this.rulesNote) this.rulesNote.textContent = i18n.t('home.jenny.rulesHint');
    if (this.rulesSave) this.rulesSave.textContent = i18n.t('home.jenny.rulesSave');
    if (this.rulesEl) this.rulesEl.placeholder = i18n.t('home.jenny.rulesPlaceholder');
    if (this._painted) {
      for (const btn of this.sizeEl.children) {
        btn.textContent = i18n.t(SIZE_KEYS[btn.dataset.size]);
      }
    }
    this._sayFloating();
  }

  /** Il valore da scrivere sulla riga di «Tu e Jenny». */
  value() {
    return jennyValue({
      visible: mascotVisible(),
      size: mascotSize(),
      floating: !!this.floating?.enabled,
    });
  }

  toggleVisible() {
    setMascotVisible(!mascotVisible());
    this._mark();
    this._onChange?.();
  }

  pickSize(size) {
    setMascotSize(size);
    this._mark();
    this._onChange?.();
  }

  /** Accende o spegne la finestra flottante, e racconta cosa e' successo. */
  async toggleFloating() {
    if (!this.floating) return;
    const enabled = !this.floating.enabled;
    /* Ottimista e poi corretta dal server: il giro passa da `store.mutate` e
       da un ponte verso Kotlin, e un interruttore che aspetta mezzo secondo
       prima di muoversi sembra rotto. */
    this.floating = { ...this.floating, enabled };
    this._mark();
    this._onChange?.();
    try {
      const payload = await api.updateFloating({ enabled });
      if (payload?.floating) this.floating = payload.floating;
    } catch (err) {
      console.warn('casa.jenny: floating window not updated', err);
      this.floating = { ...this.floating, enabled: !enabled };
    }
    this._mark();
    this._onChange?.();
    this._onFloating?.(this.floating);
  }

  /* Quel che ha scritto, una volta per apertura della stanza. Un campo che
     l'utente sta scrivendo non si sovrascrive mai con quel che c'era: sarebbe
     lo stesso guasto della bozza della chat, un attimo piu' tardi. */
  async _loadRules() {
    if (this._rulesAsked || !this.rulesEl) return;
    this._rulesAsked = true;
    let text = '';
    try {
      const file = await api.readWorkspaceFile(RULES_PATH);
      text = (file?.content || '').trim();
    } catch (err) {
      /* 404 = non ne ha ancora scritte, ed e' lo stato normale del primo
         giorno. Qualunque altro errore lascia il campo vuoto e non lo dice:
         quel che c'e' su disco resta `null`, quindi «Salva» non compare e uno
         spazio battuto per sbaglio non puo' cancellare niente. */
      if (err?.status !== 404) {
        console.warn('casa.jenny: rules not read', err);
        return;
      }
    }
    this._rulesOnDisk = text;
    if (!this.rulesEl.value) this.rulesEl.value = text;
    this._markRules();
  }

  /** «Salva» c'e' solo quando c'e' qualcosa da salvare. */
  _markRules() {
    if (!this.rulesSave || !this.rulesEl) return;
    const changed = this._rulesOnDisk !== null
      && this.rulesEl.value.trim() !== this._rulesOnDisk;
    this.rulesSave.hidden = !changed;
  }

  /** Salva le regole. Il comando scrive la verita' **e** rifa' la copia. */
  async saveRules() {
    if (!this.rulesEl) return;
    const text = this.rulesEl.value.trim();
    try {
      await rpc.writeSoulRules(text);
    } catch (err) {
      console.warn('casa.jenny: rules not saved', err);
      showToast(i18n.t('home.jenny.rulesFailed'), 'error');
      return;
    }
    this._rulesOnDisk = text;
    this._markRules();
    showToast(i18n.t('home.jenny.rulesSaved'), 'success');
  }

  /* Le tre taglie si disegnano una volta: non cambiano mentre guardi. */
  _paintSizes() {
    if (this._painted || !this.sizeEl) return;
    for (const size of SIZES) {
      if (!(size in MASCOT_SIZES)) continue;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'home-seg-btn';
      btn.dataset.size = size;
      btn.setAttribute('role', 'radio');
      btn.textContent = i18n.t(SIZE_KEYS[size]);
      this.sizeEl.appendChild(btn);
    }
    this._painted = true;
  }

  /* Lo stato di tutti e tre i comandi, in un posto solo: si muovono insieme
     perche' descrivono la stessa persona. */
  _mark() {
    const visible = mascotVisible();
    this._switch(this.visibleBtn, visible);
    const size = mascotSize();
    if (this.sizeEl) {
      for (const btn of this.sizeEl.children) {
        const on = btn.dataset.size === size;
        btn.classList.toggle('is-on', on);
        btn.setAttribute('aria-checked', String(on));
      }
    }
    /* La riga della finestra non c'e' dove la finestra non puo' esistere:
       fuori da Android `available` e' falso, e un interruttore che non fa
       niente e' peggio di una riga che manca. */
    const available = !!this.floating?.available;
    if (this.floatingRow) this.floatingRow.hidden = !available;
    this._switch(this.floatingBtn, !!this.floating?.enabled);
    this._sayFloating();
  }

  _switch(el, on) {
    if (!el) return;
    el.classList.toggle('is-on', on);
    el.setAttribute('aria-checked', String(on));
  }

  /* Cosa c'e' da sapere della finestra: cos'e', oppure perche' non si apre. */
  _sayFloating() {
    if (!this.floatingNote) return;
    const available = !!this.floating?.available;
    const blocked = !!this.floating?.enabled && this.floating?.active === false;
    this.floatingNote.hidden = !available;
    this.floatingNote.classList.toggle('is-warn', blocked);
    if (!available) return;
    this.floatingNote.textContent = i18n.t(
      blocked ? 'settings.floatingBlocked' : 'settings.floatingHint',
    );
  }

}
