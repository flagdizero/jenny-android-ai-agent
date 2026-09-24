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

export const i18n = new I18n();
