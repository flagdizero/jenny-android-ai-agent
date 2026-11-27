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
 *  L'elenco lo va a chiedere al provider vero (`rpc.providerModels`),
 *  e passa dalla rete: per questo si tiene.
 */

import { api } from './api-client.js';
import { i18n } from './i18n.js';
import { showToast } from './utils.js';

const LOADING = Object.freeze({ status: 'loading', models: [], message: '' });

/** Le frasi uguali nei due gusci e nel primo avvio; `missing_api_base` ed
 *  `error` dicono dove si ripara, che e' diverso in ognuno, e ognuno ha la sua.
 *  `not_configured` e' «manca la chiave», `rejected` «la chiave c'e' ma il
 *  provider la rifiuta» (401/403): erano lo stesso stato, e chi aveva appena
 *  scritto una chiave sbagliata leggeva «serve una chiave». */
export const CATALOG_NOTE_KEYS = Object.freeze({
  loading: 'common.modelsLoading',
  not_configured: 'common.modelsNeedKey',
  rejected: 'common.modelsKeyRejected',
});

/* I guasti della domanda di prova dopo cui la chiave può essere giusta: la rete,
   il tempo, il server, il limite di richieste, o un errore che non si sa dire
   (un 400 per un parametro che quel provider non accetta non dice niente della
   chiave, e senza «Salva lo stesso» il primo avvio non avrebbe un'uscita). Con
   la chiave rifiutata, il credito finito o il modello che non c'è, salvare
   vorrebbe dire una Jenny che non risponde. Vale per il primo avvio
   (`onboarding-wizard.js`) e per un provider aggiunto dalla casa
   (`home-provider-add.js`): i tipi sono quelli di `settings_api._probe_provider`. */
export const PROBE_SAVE_ANYWAY = new Set(['connection', 'timeout', 'server', 'rate_limit', 'generic']);
export const PROBE_KINDS = new Set([
  'auth', 'credit', 'model', 'config', ...PROBE_SAVE_ANYWAY,
]);

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

/* La scelta in volo, una per pagina: v. `switchModel`. */
let switching = false;

/** Cambia chi risponde: modello **e** marca, in una chiamata sola, per tutti e
 *  due i gusci (la casa da «Chi risponde», l'officina da «Chi pensa»).
 *
 *  - **Una scelta alla volta.** Mentre un salvataggio e' in volo un secondo
 *    tocco non scrive: due scritture di fila potevano lasciare il segno su un
 *    modello e far rispondere l'altro.
 *  - **Il toast dice quando vale.** `requires_restart` vuol dire «dal prossimo
 *    avvio»: la casa diceva «adesso risponde questo» lo stesso.
 *
 *  Il payload del server (le impostazioni intere), `{}` se la rotta non ne ha
 *  dato uno, o `null` se non e' cambiato niente: scelta in volo o fallita, e
 *  il fallimento l'ha gia' detto il toast. */
export async function switchModel(provider, model) {
  if (!provider || !model || switching) return null;
  switching = true;
  try {
    const payload = await api.updateSettings({ model, default_provider: provider });
    showToast(i18n.t(payload?.requires_restart
      ? 'settings.brandModelSavedRestart' : 'settings.brandModelSaved'), 'success');
    return payload || {};
  } catch (err) {
    console.warn('model not changed', err);
    showToast(i18n.t('settings.brandModelFailed'), 'error');
    return null;
  } finally {
    switching = false;
  }
}
