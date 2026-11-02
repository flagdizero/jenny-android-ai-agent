/** Gli elenchi dei modelli dei provider, chiesti una volta e tenuti.
 *
 *  Casa (`home-model.js`) e officina (`mobile-settings.js`, «Chi pensa») ne
 *  tenevano ciascuna una copia, con due regole diverse per riprovare: la casa
 *  richiedeva un elenco fallito a ogni tocco sulla marca, l'officina una volta
 *  per visita ma richiedeva anche «serve una chiave». La regola, una:
 *
 *  - un elenco si tiene **finche' il provider resta quello** — nome, formato,
 *    indirizzo e chiave sono la chiave della cache: cambiato uno, e' un altro
 *    elenco, e la risposta di prima («serve una chiave», «manca l'indirizzo»)
 *    non dice piu' niente;
 *  - un elenco che **non e' arrivato** (`error`, o la chiamata fallita) si
 *    riprova **alla visita dopo** (`newVisit`), non a ogni ridisegno: la rotta
 *    puo' essere lenta fino al suo timeout, e ogni tocco altrove la rifaceva;
 *  - una lettura in volo non ne fa partire una seconda.
 *
 *  L'elenco lo va a chiedere al provider vero (`/api/providers/<name>/models`),
 *  e passa dalla rete: per questo si tiene.
 */

const LOADING = Object.freeze({ status: 'loading', models: [], message: '' });

/** Le frasi uguali nei due gusci; `missing_api_base` ed `error` dicono dove si
 *  ripara, che e' diverso in casa e in officina, e ognuno ha la sua. */
export const CATALOG_NOTE_KEYS = Object.freeze({
  loading: 'common.modelsLoading',
  not_configured: 'common.modelsNeedKey',
});

export class ModelCatalogs {
  /** *fetchModels(name)*: la richiesta (`rpc.providerModels`). */
  constructor(fetchModels) {
    this._fetch = fetchModels;
    this._lists = new Map();
    this._tried = new Set();
  }

  static key(p) {
    return [p?.name, p?.format, p?.api_base, p?.api_key_hint].join('\n');
  }

  /** Quel che si sa dell'elenco di *p*: `{ status, models, message }`, con
   *  `status` quello del server (`available`, `not_configured`, ...),
   *  `loading` o `error`. Mai `null`: un elenco non ancora chiesto e' «sto
   *  chiedendo», che e' quel che succede subito dopo. */
  get(p) {
    return (p && this._lists.get(ModelCatalogs.key(p))) || LOADING;
  }

  /** Una visita nuova: gli elenchi non arrivati si possono richiedere. */
  newVisit() {
    this._tried.clear();
  }

  /** Dimentica l'elenco di *p* (una chiave appena salvata potrebbe essere la
   *  ragione per cui era vuoto), a meno che non sia in volo. */
  forget(p) {
    const key = ModelCatalogs.key(p);
    if (this._lists.get(key)?.status === 'loading') return;
    this._lists.delete(key);
    this._tried.delete(key);
  }

  /** Chiede l'elenco di *p* se la regola lo vuole. Vero se ha chiesto (e la
   *  risposta, buona o no, e' adesso in `get(p)`); falso se c'era gia'. */
  async load(p) {
    if (!p) return false;
    const key = ModelCatalogs.key(p);
    const known = this._lists.get(key);
    if (known && (known.status !== 'error' || this._tried.has(key))) return false;
    this._tried.add(key);
    this._lists.set(key, LOADING);
    let outcome;
    try {
      const res = await this._fetch(p.name);
      outcome = {
        status: res?.status || 'available',
        models: (res?.models || []).map((m) => m.id || m),
        message: res?.message || '',
      };
    } catch (err) {
      console.warn('model list not read', p.name, err);
      outcome = { status: 'error', models: [], message: '' };
    }
    this._lists.set(key, outcome);
    return true;
  }
}
