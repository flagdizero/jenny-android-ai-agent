export class I18n {
  constructor() {
    this.locale = localStorage.getItem('locale') || this.detectLocale();
    this.translations = {};
    this._listeners = [];
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
        // errati, altrimenti setLocale() creda che il caricamento sia riuscito.
        console.warn(`Failed to load locale ${locale}: HTTP ${res.status}`);
        return;
      }
      this.translations[locale] = await res.json();
    } catch (e) {
      console.warn(`Failed to load locale ${locale}:`, e);
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

  async setLocale(locale) {
    // Carica le traduzioni prima di cambiare lingua: evita che i listener
    // onLocaleChange girino su this.translations[locale] non ancora popolato
    // (altrimenti t() ritorna la chiave grezza, es. "nav.chat").
    if (!this.translations[locale]) {
      await this.load(locale);
    }
    if (!this.translations[locale]) {
      // Load fallito (es. rete): mantieni la lingua corrente invece di
      // passare a chiavi grezze.
      console.warn(`Locale ${locale} non disponibile: switch annullato`);
      return;
    }
    this.locale = locale;
    localStorage.setItem('locale', locale);
    document.documentElement.lang = locale;
    this._listeners.forEach(cb => cb(locale));
  }

  onLocaleChange(cb) {
    this._listeners.push(cb);
  }

}

export const i18n = new I18n();
