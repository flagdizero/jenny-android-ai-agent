/** Le estensioni: pezzi di Jenny che solo alcune build impacchettano.
 *
 *  Il gateway le elenca nella risposta di `/webui/bootstrap`
 *  (`jenny/extensions.py`): per ognuna un modulo d'ingresso, i fogli di stile
 *  e le traduzioni, serviti sotto `assets/ext/<nome>/`. Una build senza
 *  estensioni manda un elenco vuoto, e qui non succede niente.
 *
 *  Due tempi, perché le traduzioni devono esserci prima del primo disegno:
 *
 *  1. `prepareExtensions()` dopo `api.bootstrap()` e **prima** di `i18n.load`:
 *     registra le traduzioni e aggancia i fogli di stile.
 *  2. `mountExtensions(shell, ctx)` quando la shell ha costruito i suoi pezzi:
 *     importa ogni modulo e chiama `mount({shell, ...ctx})`. Il `ctx` porta i
 *     ganci della shell (la pagina Backup della casa, il primo passo
 *     dell'onboarding); un'estensione usa quelli che le servono.
 *
 *  Un'estensione rotta non ferma la shell: si logga e si va avanti.
 */

import { api } from './api-client.js';
import { i18n } from './i18n.js';

let _prepared = false;

export function prepareExtensions() {
  if (_prepared) return;
  _prepared = true;
  for (const ext of api.extensions()) {
    i18n.addSource(ext.i18n);
    for (const href of ext.css || []) {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = href;
      document.head.appendChild(link);
    }
  }
}

export async function mountExtensions(shell, ctx = {}) {
  for (const ext of api.extensions()) {
    try {
      const mod = await import(ext.entry);
      await mod.mount?.({ shell, ...ctx });
    } catch (err) {
      console.error(`Extension ${ext.name} failed to mount:`, err);
      api.clientLog('error', `extension.${ext.name}`, String(err && err.stack || err));
    }
  }
}
