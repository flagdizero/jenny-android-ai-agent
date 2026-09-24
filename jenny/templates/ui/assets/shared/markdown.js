/** Il markdown di un messaggio, reso in HTML sanificato. Fallisce chiuso.
 *
 *  Era scritto due volte — la chat della casa e quella dell'officina — con la
 *  stessa regola di sicurezza: se il sanificatore (DOMPurify) non c'e', o se il
 *  parse fallisce, il testo esce con l'HTML neutralizzato, mai iniettato
 *  com'e'. Un testo del modello e' un input non fidato, e `innerHTML` non
 *  perdona.
 *
 *  Le opzioni di `marked` (a capo, highlight.js, la riga «Copia» sui blocchi di
 *  codice) le mette chi chiama prima di disegnare: l'officina le ha
 *  (`initMarked` in mobile-chat.js), la casa no.
 */

import { escapeHtml } from './utils.js';

export function renderMarkdown(text) {
  if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
    return escapeHtml(text);
  }
  try {
    return DOMPurify.sanitize(marked.parse(text));
  } catch (e) {
    console.error('Markdown parse error:', e);
    return escapeHtml(text);
  }
}
