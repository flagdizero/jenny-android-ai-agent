/** Quel che casa e officina fanno uguale a una risposta di Jenny.
 *
 *  Erano scritte due volte ciascuna: le due passate che un blocco di testo
 *  riceve dopo il markdown (formule, diagrammi, blocchi di codice, e le schede
 *  dei posti), in sette punti; il Copia col suo toast; i secondi del turno.
 */

import { i18n } from './i18n.js';
import { renderPlacesIn } from './places.js';
import { renderRich } from './rich-content.js';
import { copyToClipboard, showToast } from './utils.js';

/** Le passate dopo il markdown, nell'ordine: il contenuto ricco
 *  (`renderRich`, asincrono e senza attese: carica quel che serve da se') e i
 *  link `jenny://place` resi schede. */
export function renderReplyExtras(node) {
  renderRich(node);
  renderPlacesIn(node);
}

/** Copia *text* e lo dice: *okKey*, o il fallimento — `copyToClipboard`
 *  non riesce in ogni WebView, e un Copia muto sembrerebbe aver funzionato.
 *  Il Copia di una risposta, la chiave pubblica SSH, il link dell'accesso da
 *  altri dispositivi: il fallimento e' la stessa frase per tutti. */
export async function copyWithToast(text, { okKey = 'chat.copied', failKey = 'chat.copyFailed' } = {}) {
  if (!text) return;
  if (!(await copyToClipboard(text))) {
    showToast(i18n.t(failKey), 'error');
    return;
  }
  showToast(i18n.t(okKey), 'success');
}

/** Quanto ha messo il turno: «2.4s». */
export function latencyText(ms) {
  return (ms / 1000).toFixed(1) + 's';
}
