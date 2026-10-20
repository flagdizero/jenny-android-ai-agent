/** La casa — i promemoria di tutto il quaderno, nel foglio della bacheca.
 *
 *  Il post-it giallo libero (e il «+» della bacheca) aprono qui: l'elenco delle
 *  attese che non sono di una scheda, ognuna con «Fatto» e «Togli», e il modulo
 *  per aggiungerne una. Il cassetto di una scheda ha il suo calendario di cura,
 *  più ricco; questo è il suo fratello piccolo per quello che non ha una scheda.
 *
 *  Il modulo è lo stesso del cassetto (cosa, quando, «solo il mese»): chi lo usa
 *  dice cosa fare col risultato.
 */

import { i18n } from './shared/i18n.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(className, text, onClick) {
  const b = el('button', className, text);
  b.type = 'button';
  b.addEventListener('click', onClick);
  return b;
}

/** Le attese, dalla più vicina: quando, cosa, «Fatto» e «Togli».
 *
 *  @param rems  le attese (già in ordine, come le manda il gateway).
 *  @param opts  `{ when(rem), onDone(rem), onDrop(rem) }`: `when` le scrive la
 *               data («2 nov», «marzo»), i due gesti li fa chi mostra l'elenco. */
export function agendaList(rems, { when, onDone, onDrop }) {
  const list = el('div', 'home-board-agenda');
  if (!rems.length) {
    list.appendChild(el('p', 'home-board-muted', i18n.t('board.agenda.empty')));
    return list;
  }
  for (const rem of rems) {
    const row = el('div', 'home-board-agenda-row');
    row.dataset.reminder = rem.id;
    row.append(el('span', 'home-board-agenda-when', when(rem)), el('span', 'home-board-agenda-text', rem.text));
    const tools = el('span', 'home-board-agenda-tools');
    tools.append(
      button('home-drawer-link', i18n.t('board.reminder.done'), () => onDone(rem)),
      button('home-drawer-link', i18n.t('board.reminder.drop'), () => onDrop(rem)),
    );
    row.appendChild(tools);
    list.appendChild(row);
  }
  return list;
}

/** Il modulo di un'attesa nuova: cosa, quando, «solo il mese».
 *
 *  @param opts `{ onSave({ text, when }), onCancel() }`: `when` è già nella forma
 *              del gateway (`{kind: 'date', date, precision}`). */
export function reminderForm({ onSave, onCancel }) {
  const form = el('form', 'home-board-form');
  const what = el('input', 'home-board-input');
  what.placeholder = i18n.t('board.section.reminderWhat');
  what.setAttribute('aria-label', what.placeholder);
  const when = el('input', 'home-board-input');
  when.type = 'date';
  when.setAttribute('aria-label', i18n.t('board.section.reminderWhen'));
  const month = el('label', 'home-board-check');
  const monthBox = el('input');
  monthBox.type = 'checkbox';
  month.append(monthBox, document.createTextNode(i18n.t('board.section.reminderMonth')));
  const actions = el('div', 'home-board-actions');
  const save = el('button', 'home-board-btn is-primary', i18n.t('board.section.save'));
  save.type = 'submit';
  actions.append(button('home-board-btn', i18n.t('common.cancel'), () => onCancel()), save);
  form.append(what, when, month, actions);
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!what.value.trim() || !when.value) return;
    onSave({
      text: what.value,
      when: { kind: 'date', date: when.value, precision: monthBox.checked ? 'month' : 'day' },
    });
  });
  return form;
}
