/** La scheda della risposta di un'altra Jenny (`agent_ui.peer_answer`).
 *
 * Arriva come un messaggio a sé nella chat personale (`jenny/peers/questions.py`)
 * e si disegna qui, dal codice: chi ha risposto e quando, la domanda, la
 * risposta citata. Il testo dell'altra Jenny non passa dal markdown: è un dato
 * arrivato da un altro telefono, e un link o un'immagine lì dentro resta testo.
 * Gli allegati veri li disegna chi chiama, come per ogni messaggio.
 */

import { el, icon } from './dom.js';
import { i18n } from './i18n.js';

const STATUSES = new Set(['ok', 'refused', 'too_many', 'unavailable', 'failed', 'expired']);

function timeOf(ms) {
  const n = Number(ms);
  if (!(n > 0)) return '';
  return new Date(n).toLocaleTimeString(i18n.locale, { hour: '2-digit', minute: '2-digit' });
}

/** Il nodo della scheda, o `null` se *card* non è una risposta di un'altra Jenny. */
export function peerCardNode(card) {
  if (!card || typeof card !== 'object' || typeof card.peer !== 'string') return null;
  const status = STATUSES.has(card.status) ? card.status : 'failed';
  const node = el('div', `home-peer-card is-${status}`);

  const head = el('div', 'home-peer-head');
  head.appendChild(icon(status === 'ok' ? 'ti-arrows-exchange' : 'ti-alert-circle'));
  const time = timeOf(card.at);
  head.appendChild(el('span', 'home-peer-who', time
    ? i18n.t('home.peer.fromAt', { name: card.peer, time })
    : i18n.t('home.peer.from', { name: card.peer })));
  node.appendChild(head);

  if (typeof card.question === 'string' && card.question) {
    node.appendChild(el('div', 'home-peer-question', `«${card.question}»`));
  }
  if (status === 'ok') {
    const text = typeof card.text === 'string' ? card.text : '';
    node.appendChild(el('div', 'home-peer-quote', text || i18n.t('home.peer.empty')));
  } else {
    node.appendChild(el('div', 'home-peer-status', i18n.t(`home.peer.status.${status}`, { name: card.peer })));
  }
  const missing = Array.isArray(card.missing) ? card.missing.filter((m) => typeof m === 'string') : [];
  if (missing.length) {
    node.appendChild(el('div', 'home-peer-missing', i18n.t('home.peer.missing', { files: missing.join(', ') })));
  }
  return node;
}
