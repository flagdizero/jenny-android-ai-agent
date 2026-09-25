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

/** Cosa il sanificatore toglie **oltre** ai suoi default.
 *
 *  La configurazione di base di DOMPurify lascia passare i controlli di un
 *  modulo — `<form>`, `<input>`, `<button>`, `<textarea>`, `<select>` — e la CSP
 *  della shell non ha `form-action`: un `<form action="https://…">` scritto nel
 *  testo di una risposta diventava un modulo vero, che mandava fuori quel che
 *  l'utente ci digitava dentro. Nessun contenuto legittimo di una risposta o di
 *  una pagina ha bisogno di un campo o di un bottone; i due che servivano li
 *  genera il client **dopo** la sanificazione o senza HTML: la casella di una
 *  lista di cose da fare (v. `configureOnce`) e il «Copia» dei blocchi di
 *  codice dell'officina (`restoreCopyButtons` in `mobile-chat.js`).
 *
 *  Vale anche per il lettore delle pagine del quaderno, che sanifica l'HTML del
 *  server con la stessa regola.
 */
export const SANITIZE_CONFIG = Object.freeze({
  FORBID_TAGS: ['form', 'input', 'button', 'textarea', 'select'],
});

let configured = false;

/* Una volta, alla prima resa con la libreria caricata. `setOptions` fonde con
   quel che c'e': un renderer messo prima (quello dell'officina) resta.

   La casella di una lista di cose da fare (`- [ ] latte`) marked la scrive come
   `<input type="checkbox" disabled>`, cioe' un tag che `SANITIZE_CONFIG` toglie:
   la si scrive come segno invece che come campo, cosi' la lista resta leggibile.
   `use` e non `setOptions`: aggiunge un metodo al renderer che c'e', senza
   rimpiazzarlo. */
function configureOnce() {
  if (configured) return;
  marked.setOptions({ ...MARKED_OPTIONS });
  if (typeof marked.use === 'function') {
    marked.use({ renderer: { checkbox: ({ checked }) => (checked ? '\u2611' : '\u2610') } });
  }
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
    return DOMPurify.sanitize(marked.parse(text), SANITIZE_CONFIG);
  } catch (e) {
    console.error('Markdown parse error:', e);
    return escapeHtml(text);
  }
}
