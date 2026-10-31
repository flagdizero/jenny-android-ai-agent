/* La lingua e' quella del telefono, e non si cambia da dentro l'app: il
   selettore dell'officina e' uscito col «Trim 2/5» (3d57980) e la casa non ne
   ha uno per scelta (casa-tu-e-jenny-checklist). Fino al 24/09/2026 qui si
   leggeva ancora `localStorage.locale`, che solo quel selettore scriveva: chi
   l'aveva usato restava fermo sulla lingua scelta allora, senza piu' un modo
   di cambiarla. Se tornera' una scelta per-app sara' `android:localeConfig`,
   che ricrea l'Activity: un cambio a caldo non serve. */
export class I18n {
  constructor() {
    this.locale = this.detectLocale();
    this.translations = {};
    /* Le traduzioni delle estensioni (`shared/extensions.js`): `{locale: url}`
       per estensione, fuse sopra quelle del core a ogni `load`. */
    this._sources = [];
  }

  /** Aggiunge le traduzioni di un'estensione. Va chiamata prima di `load`. */
  addSource(urlsByLocale) {
    if (urlsByLocale && typeof urlsByLocale === 'object') this._sources.push(urlsByLocale);
  }

  detectLocale() {
    const nav = navigator.language;
    const supported = ['it', 'en'];
    return supported.find(s => s === nav) || supported.find(s => nav.startsWith(s)) || 'en';
  }

  async load(locale) {
    try {
      const res = await fetch(`/assets/i18n/${locale}.json`);
      if (!res.ok) {
        // Risposta non valida: non sovrascrivere translations[locale] con dati
        // errati.
        console.warn(`Failed to load locale ${locale}: HTTP ${res.status}`);
        return;
      }
      this.translations[locale] = await res.json();
    } catch (e) {
      console.warn(`Failed to load locale ${locale}:`, e);
      return;
    }
    for (const urls of this._sources) {
      const url = urls[locale];
      if (!url) continue;
      try {
        const res = await fetch(url);
        if (res.ok) _deepMerge(this.translations[locale], await res.json());
        else console.warn(`Failed to load extension locale ${url}: HTTP ${res.status}`);
      } catch (e) {
        console.warn(`Failed to load extension locale ${url}:`, e);
      }
    }
  }

  t(key, params) {
    const keys = key.split('.');
    let value = this.translations[this.locale];
    for (const k of keys) {
      if (!value || typeof value !== 'object') return key;
      value = value[k];
    }
    if (typeof value !== 'string') return key;
    if (params) {
      return value.replace(/\{(\w+)\}/g, (_, name) => params[name] ?? `{${name}}`);
    }
    return value;
  }
}

/** Fonde *extra* dentro *base*: le foglie di *extra* vincono, i rami si uniscono. */
function _deepMerge(base, extra) {
  for (const [key, value] of Object.entries(extra || {})) {
    if (value && typeof value === 'object' && !Array.isArray(value)
        && base[key] && typeof base[key] === 'object' && !Array.isArray(base[key])) {
      _deepMerge(base[key], value);
    } else {
      base[key] = value;
    }
  }
  return base;
}

export const i18n = new I18n();
