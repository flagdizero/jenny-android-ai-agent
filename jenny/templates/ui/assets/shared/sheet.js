/** Il foglio d'azioni della casa: una maniglia, la ✕, un titolo, delle righe.
 *
 *  Due fogli lo disegnavano uguale, ognuno col suo codice: quello del piano a
 *  lista (`home-plan.js`, `#home-plan-sheet`) e «Fermala» degli aiutanti
 *  (`home-work.js`, `#home-work-sheet`), che ne ha preso classi e forma. Qui il
 *  disegno, una volta; ognuno tiene il suo `<dialog>` e quel che ci mette
 *  dentro.
 *
 *  Il foglio e' un `<dialog>` aperto con `showModal()`: Indietro lo chiude come
 *  ogni altro (`dialog.js::dismissTopDialog`), e la ✕ c'e' perche' i fogli che
 *  si guardano e basta non hanno un Annulla, e velo e Indietro non si vedono.
 */

import { button, el, icon } from './dom.js';

/** Riempie *sheet* e lo apre (se non lo e' gia'); torna il foglio, o `null`.
 *
 *  - *children*: i nodi sotto la ✕, nell'ordine (i `null` si saltano);
 *  - *closeLabel*: il nome della ✕ per il lettore di schermo;
 *  - *onClose*: la ✕ toccata (di solito `closeActionSheet(sheet)`);
 *  - *cls*: se c'e', le classi del foglio diventano
 *    `oc-sheet home-plan-sheet <cls>` (un foglio che cambia faccia a seconda
 *    di cosa mostra); senza, restano quelle del markup;
 *  - *closeData*: attributi `data-*` della ✕ (`{ planAction: 'sheet-close' }`).
 */
export function openActionSheet(sheet, {
  children = [], closeLabel = '', onClose = null, cls = null, closeData = null,
} = {}) {
  if (!sheet) return null;
  if (cls !== null) sheet.className = `oc-sheet home-plan-sheet ${cls}`.trim();
  const body = sheet.querySelector('.home-plan-sheet-inner') || sheet;
  const grab = el('div', 'home-plan-sheet-grab');
  const close = button('home-plan-sheet-close', null,
    () => (onClose ? onClose() : closeActionSheet(sheet)));
  if (closeData) Object.assign(close.dataset, closeData);
  close.setAttribute('aria-label', closeLabel);
  close.appendChild(icon('ti-x'));
  body.replaceChildren(grab, close, ...children.filter(Boolean));
  if (!sheet.open) {
    if (typeof sheet.showModal === 'function') sheet.showModal();
    else sheet.setAttribute('open', '');
  }
  return sheet;
}

export function closeActionSheet(sheet) {
  if (!sheet?.open) return;
  if (typeof sheet.close === 'function') sheet.close();
  else sheet.removeAttribute('open');
}

/** Il titolo del foglio e, se c'e', la riga sotto. */
export function sheetHead(title, sub = '') {
  const nodes = [el('h2', 'home-plan-sheet-title', title)];
  if (sub) nodes.push(el('p', 'home-plan-sheet-sub', sub));
  return nodes;
}

/** Una riga-bottone: l'icona, l'etichetta e, se c'e', la riga piccola sotto.
 *  *data* sono i suoi `data-*` (`{ workAction: 'stop' }`). */
export function sheetRow(glyph, label, sub, cls, run, data = null) {
  const row = button(`home-plan-sheet-row ${cls || ''}`.trim());
  if (data) Object.assign(row.dataset, data);
  row.appendChild(icon(glyph));
  const text = el('span', 'home-plan-row-text');
  text.append(el('span', null, label));
  if (sub) text.appendChild(el('span', 'home-plan-row-sub', sub));
  row.appendChild(text);
  if (run) row.addEventListener('click', run);
  return row;
}
