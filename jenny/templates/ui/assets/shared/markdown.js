/** Il markdown di un messaggio, reso in HTML sanificato. Fallisce chiuso.
 *
 *  Era scritto due volte — la chat della casa e quella dell'officina — con la
 *  stessa regola di sicurezza: se il sanificatore (DOMPurify) non c'e', o se il
 *  parse fallisce, il testo esce con l'HTML neutralizzato, mai iniettato
 *  com'e'. Un testo del modello e' un input non fidato, e `innerHTML` non
 *  perdona.
 *
 *  **Le opzioni di base sono qui** (`MARKED_OPTIONS`), per tutte e due: una
 *  riga singola va a capo. Fino al 24/09/2026 le metteva solo l'officina, e in
 *  casa una lista scritta a righe singole si fondeva in un paragrafo — la
 *  stessa risposta letta in due modi (la finestra flottante, con Markwon, sta
 *  con l'officina). Quel che resta dell'officina — highlight.js e la riga
 *  «Copia» sui blocchi di codice — lo aggiunge il suo `initMarked`.
 */

import { escapeHtml } from './utils.js';

export const MARKED_OPTIONS = Object.freeze({ gfm: true, breaks: true });

let configured = false;

/* Una volta, alla prima resa con la libreria caricata. `setOptions` fonde con
   quel che c'e': un renderer messo prima (quello dell'officina) resta. */
function configureOnce() {
  if (configured) return;
  marked.setOptions({ ...MARKED_OPTIONS });
  configured = true;
}

export function renderMarkdown(text) {
  if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
    return escapeHtml(text);
  }
  try {
    // Dentro il ``try``: anche una configurazione che fallisce ripiega sul
    // testo neutralizzato, invece di lasciar salire l'errore a chi disegna.
    configureOnce();
    return DOMPurify.sanitize(marked.parse(text));
  } catch (e) {
    console.error('Markdown parse error:', e);
    return escapeHtml(text);
  }
}
