/** La scheda della risposta di un'altra Jenny (`agent_ui.peer_answer`).
 *
 * Arriva come un messaggio a sé nella chat personale (`jenny/peers/questions.py`)
 * e si disegna qui, dal codice: chi ha risposto e quando, la domanda, la
 * risposta citata. Il testo dell'altra Jenny non passa dal markdown: è un dato
 * arrivato da un altro telefono, e un link o un'immagine lì dentro resta testo.
 * Gli allegati veri li disegna chi chiama, come per ogni messaggio.
 *
 * Una richiesta di fare (`mode: "act"`, `jenny/peers/acting.py`) porta in più
 * l'elenco di cosa ha toccato sull'altro telefono — scritto dal codice di là,
 * non dal modello —, se ci sta ancora lavorando, e il tasto «Chiedi di
 * annullare». L'esito di quella richiesta torna come una scheda `undone`.
 */

import { button, el, icon } from './dom.js';
import { i18n } from './i18n.js';
import { rpc } from './rpc-client.js';
import { showToast } from './utils.js';

const STATUSES = new Set([
  'ok', 'refused', 'too_many', 'unavailable', 'failed', 'expired',
  'undone', 'undo_refused', 'undo_still_working', 'undo_not_found', 'undo_failed',
]);
const DONE_ICONS = { file: 'ti-file', job: 'ti-clock', egress: 'ti-world' };
const ID_RE = /^[0-9a-f]{32}$/;

function timeOf(ms) {
  const n = Number(ms);
  if (!(n > 0)) return '';
  return new Date(n).toLocaleTimeString(i18n.locale, { hour: '2-digit', minute: '2-digit' });
}

function headText(card, status, time) {
  const name = card.peer;
  if (card.mode === 'act' && status === 'ok') {
    const key = card.working ? 'home.peer.workingAt' : 'home.peer.didAt';
    return i18n.t(key, { name, time });
  }
  return time ? i18n.t('home.peer.fromAt', { name, time }) : i18n.t('home.peer.from', { name });
}

/** L'elenco di cosa ha toccato: file, promemoria, pagine aperte (queste restano). */
function doneNode(done) {
  const items = (Array.isArray(done) ? done : [])
    .filter((d) => d && typeof d.label === 'string' && DONE_ICONS[d.kind]);
  if (!items.length) return null;
  const list = el('ul', 'home-peer-done');
  for (const item of items) {
    const row = el('li', `home-peer-done-item is-${item.kind}`);
    row.appendChild(icon(DONE_ICONS[item.kind]));
    row.appendChild(el('span', 'home-peer-done-label', item.label));
    if (item.kind === 'egress') {
      const key = item.allowed === false ? 'home.peer.denied' : 'home.peer.notUndoable';
      row.appendChild(el('span', 'home-peer-done-note', i18n.t(key)));
    }
    list.appendChild(row);
  }
  return list;
}

function undoButton(card) {
  if (!ID_RE.test(card.qid || '') || !ID_RE.test(card.peerId || '')) return null;
  const undoable = (card.done || []).some((d) => d && d.undoable === true);
  if (!undoable) return null;
  const btn = button('home-peer-undo', null, async () => {
    btn.disabled = true;
    try {
      await rpc.peer('undo_remote', { id: card.peerId, qid: card.qid });
      showToast(i18n.t('home.peer.undoAsked', { name: card.peer }));
    } catch (e) {
      btn.disabled = false;
      showToast(i18n.t('home.peer.undoFailed'), 'error');
    }
  });
  btn.appendChild(icon('ti-arrow-back-up'));
  btn.appendChild(el('span', '', i18n.t('home.peer.askUndo')));
  return btn;
}

/** Il nodo della scheda, o `null` se *card* non è una risposta di un'altra Jenny. */
export function peerCardNode(card) {
  if (!card || typeof card !== 'object' || typeof card.peer !== 'string') return null;
  const status = STATUSES.has(card.status) ? card.status : 'failed';
  const act = card.mode === 'act';
  const node = el('div', `home-peer-card is-${status}${act ? ' is-act' : ''}`);

  const head = el('div', 'home-peer-head');
  const okish = status === 'ok' || status === 'undone';
  head.appendChild(icon(okish ? (act ? 'ti-checklist' : 'ti-arrows-exchange') : 'ti-alert-circle'));
  head.appendChild(el('span', 'home-peer-who', headText(card, status, timeOf(card.at))));
  node.appendChild(head);

  if (typeof card.question === 'string' && card.question) {
    node.appendChild(el('div', 'home-peer-question', `«${card.question}»`));
  }
  if (status === 'ok') {
    const text = typeof card.text === 'string' ? card.text : '';
    node.appendChild(el('div', 'home-peer-quote', text || i18n.t('home.peer.empty')));
  } else if (status === 'undone') {
    const u = card.undo || {};
    node.appendChild(el('div', 'home-peer-status', i18n.t('home.peer.status.undone', {
      name: card.peer, files: Number(u.restored) || 0, jobs: Number(u.jobs) || 0,
    })));
    if (Number(u.kept) > 0) {
      node.appendChild(el('div', 'home-peer-missing', i18n.t('home.peer.kept', { count: Number(u.kept) })));
    }
  } else {
    const key = act && status === 'refused' ? 'refused_act' : status;
    node.appendChild(el('div', 'home-peer-status', i18n.t(`home.peer.status.${key}`, { name: card.peer })));
  }
  if (act && status === 'ok') {
    const list = doneNode(card.done);
    if (list) node.appendChild(list);
    if (!card.working) {
      const btn = undoButton(card);
      if (btn) node.appendChild(btn);
    }
  }
  const missing = Array.isArray(card.missing) ? card.missing.filter((m) => typeof m === 'string') : [];
  if (missing.length) {
    node.appendChild(el('div', 'home-peer-missing', i18n.t('home.peer.missing', { files: missing.join(', ') })));
  }
  return node;
}
