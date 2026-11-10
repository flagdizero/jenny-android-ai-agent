/** I mattoni con cui la casa costruisce i suoi nodi a mano.
 *
 *  Erano copiati in testa a ogni modulo della casa — `el` in dieci, `button` in
 *  sei, l'icona in tre — e le copie avevano già cominciato a divergere: alcune
 *  lasciavano passare `null` come testo, altre no. Qui c'è una regola sola.
 *
 *  Il testo passa sempre da `textContent`, mai da `innerHTML`: titoli, note e
 *  nomi li scrivono l'utente e il modello.
 */

import { i18n } from './i18n.js';
import { showToast } from './utils.js';

/** Un elemento *tag* con le sue classi e, se c'è, il suo testo. `null` e
 *  `undefined` vogliono dire «nessun testo», come `''`. */
export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

/** Un `<button type="button">`: senza il tipo, dentro un `<form>` sarebbe un
 *  invio. */
export function button(className, text, onClick) {
  const b = el('button', className, text);
  b.type = 'button';
  if (onClick) b.addEventListener('click', onClick);
  return b;
}

/** Un'icona Tabler come nodo, nascosta ai lettori di schermo: chi la porta ha
 *  già il suo nome (un testo accanto, o un `aria-label`). *name* è la classe
 *  intera (`'ti-lock'`). */
export function icon(name, cls = '') {
  const i = el('i', `ti ${name}${cls ? ` ${cls}` : ''}`);
  i.setAttribute('aria-hidden', 'true');
  return i;
}

/** La stessa icona come markup, per chi costruisce le righe come stringhe.
 *  *name* è il nome senza prefisso (`'check'`). */
export function iconHtml(name) {
  return `<i class="ti ti-${name}" aria-hidden="true"></i>`;
}

/** La riga «c'è un piano in corso» sopra le pagine e la bacheca di un quaderno
 *  tenuto da un piano: visibile, col lucchetto e il testo, se *locked*;
 *  nascosta e vuota altrimenti. */
export function lockLine(line, locked) {
  line.hidden = !locked;
  line.replaceChildren();
  if (locked) line.append(icon('ti-lock'), el('span', null, i18n.t('home.plan.lock')));
}

/** Il toast di una scrittura rifiutata: «c'è un piano in corso» se è quello
 *  (`plan_running`: un piano tiene il quaderno), altrimenti *fallbackKey*.
 *  Stava scritto a mano in quattordici posti della bacheca e del lettore. */
export function writeFailedToast(err, fallbackKey) {
  showToast(i18n.t(err?.code === 'plan_running' ? 'home.plan.lock' : fallbackKey), 'error');
}
