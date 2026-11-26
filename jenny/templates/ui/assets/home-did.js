/** La casa — «Ho fatto», sotto una risposta di Jenny.
 *
 *  Un turno che ha toccato qualcosa — un file, un promemoria, un messaggio
 *  mandato — lo dice in coda alla risposta, accanto all'ora: «Ho fatto: 1 file,
 *  1 promemoria». Toccato, si apre l'elenco (lo stesso della scheda di
 *  un'altra Jenny, `doneNode`), con segnato quel che non si annulla, e «Torna
 *  a prima di questo turno». Dopo, per qualche secondo, «Annulla».
 *
 *  Un turno che non ha toccato niente, o che ha solo cercato sul web, non ha
 *  la riga: la decide il server (`turn.summaries`, `webui/turn_commands.py`).
 *
 *  Le risposte sanno il loro turno (`data-turn`, il `turn_id` dei frame e del
 *  verbale): la chat avvisa quando ne mostra (`onTurnsShown`), dal vivo a turno
 *  finito e dopo ogni pagina di storia, e qui si chiedono i conti di tutte in
 *  una richiesta.
 *
 *  I conti gia' avuti si tengono (`_known`) e si **ridisegnano** a ogni
 *  avviso: il filo si rifa' da capo quando lo si ricarica o si cambia chat, e
 *  le righe disegnate prima non ci sono piu'. Si richiedono al server solo i
 *  turni mai chiesti, quello appena chiuso (`fresh`: mentre girava i conti
 *  erano parziali) e quelli con un aiutante ancora al lavoro.
 */

import { i18n } from './shared/i18n.js';
import { rpc } from './shared/rpc-client.js';
import { button, el, icon } from './shared/dom.js';
import { doneNode } from './shared/peer-card.js';
import { showToast, showUndo } from './shared/utils.js';

const t = (key, params) => i18n.t(`home.did.${key}`, params);
/* Quanti turni in una richiesta: il tetto di `turn.summaries`. */
const MAX_BATCH = 120;

/** «Ho fatto: 1 file, 1 promemoria»; con un aiutante al lavoro lo dice. */
export function didLabel(summary) {
  const parts = [];
  const files = Number(summary?.files) || 0;
  const jobs = Number(summary?.jobs) || 0;
  const others = Number(summary?.others) || 0;
  if (files) parts.push(t(files === 1 ? 'file' : 'files', { n: files }));
  if (jobs) parts.push(t(jobs === 1 ? 'job' : 'jobs', { n: jobs }));
  if (others) parts.push(t(others === 1 ? 'other' : 'others', { n: others }));
  if (summary?.state === 'undone') return t('undoneLabel');
  if (!parts.length) return summary?.working ? t('working') : '';
  const text = t('label', { what: parts.join(', ') });
  return summary?.working ? `${text} · ${t('stillWorking')}` : text;
}

export class HomeDid {
  /** @param thread  `#home-thread`, dove stanno le risposte. */
  constructor({ thread } = {}) {
    this.thread = thread || document.getElementById('home-thread');
    /* I conti avuti, per turno; `null` = il server ha detto «niente riga». */
    this._known = new Map();
    /* I turni con un aiutante ancora al lavoro: i loro conti cambieranno. */
    this._working = new Set();
    this.thread?.addEventListener('click', (e) => {
      const btn = e.target.closest?.('.home-did');
      if (btn) this._toggle(btn);
    });
  }

  /** La chat ha mostrato queste risposte: i conti, e la riga dove serve.
   *
   *  Quel che si sa gia' si ridisegna subito (i nodi possono essere nuovi);
   *  al server vanno i turni mai chiesti, tutti quelli di *turns* con `fresh`
   *  (un turno appena chiuso, o cambiato da questa stanza), e quelli con un
   *  aiutante al lavoro, a ogni giro: quando l'aiutante finisce il suo
   *  resoconto e' un turno nuovo, e la sua fine passa di qui. */
  async ask(turns, { fresh = false } = {}) {
    const shown = [...new Set((turns || []).filter(Boolean))];
    const wanted = new Set(this._working);
    for (const id of shown) {
      if (fresh || !this._known.has(id)) wanted.add(id);
      else this._show(id, this._known.get(id));
    }
    const list = [...wanted];
    for (let i = 0; i < list.length; i += MAX_BATCH) {
      const batch = list.slice(i, i + MAX_BATCH);
      let answer;
      try {
        answer = await rpc.turn('summaries', { turns: batch });
      } catch {
        return;   // niente in `_known`: al prossimo avviso si richiede
      }
      for (const turn of batch) this._learn(turn, answer?.turns?.[turn] || null);
    }
  }

  /* I conti di *turn*, appena arrivati o cambiati qui: si tengono e si disegnano. */
  _learn(turn, summary) {
    this._known.set(turn, summary);
    if (summary?.working) this._working.add(turn);
    else this._working.delete(turn);
    this._show(turn, summary);
  }

  /* La riga di *turn* com'e' adesso: disegnata, o tolta se non ne ha piu'. */
  _show(turn, summary) {
    if (summary && didLabel(summary)) {
      this._paint(turn, summary);
      return;
    }
    this._node(turn)?.querySelector('.home-did')?.remove();
  }

  /** L'ultima risposta di *turn* nel filo (un turno lungo puo' averne piu'). */
  _node(turn) {
    const all = [...(this.thread?.querySelectorAll('.home-msg-jenny') || [])]
      .filter((n) => n.dataset?.turn === turn);
    return all.at(-1) || null;
  }

  _paint(turn, summary) {
    const node = this._node(turn);
    const label = didLabel(summary);
    if (!node || !label) return;
    let row = node.querySelector('.home-tail');
    if (!row) {
      row = el('div', 'home-tail');
      node.appendChild(row);
    }
    let btn = node.querySelector('.home-did');
    if (!btn) {
      btn = button('home-did', null);
      btn.dataset.turn = turn;
      btn.setAttribute('aria-expanded', 'false');
      row.insertBefore(btn, row.firstChild);
    }
    btn.replaceChildren(icon(summary.state === 'undone' ? 'ti-arrow-back-up' : 'ti-checks'),
      el('span', null, label));
  }

  async _toggle(btn) {
    const node = btn.closest('.home-msg');
    const open = node?.querySelector('.home-did-detail');
    if (open) {
      open.remove();
      btn.setAttribute('aria-expanded', 'false');
      return;
    }
    btn.setAttribute('aria-expanded', 'true');
    await this._open(btn.dataset.turn, node);
  }

  /** L'elenco intero, con l'anteprima del ritorno (il suo gettone). */
  async _open(turn, node) {
    if (!node) return;
    node.querySelector('.home-did-detail')?.remove();
    const detail = el('div', 'home-did-detail');
    detail.appendChild(el('div', 'home-card-note', t('loading')));
    node.appendChild(detail);
    let work;
    try {
      work = await rpc.turn('work', { turn });
    } catch {
      detail.replaceChildren(el('div', 'home-card-note', t('unavailable')));
      return;
    }
    const parts = [];
    const list = doneNode(work.items);
    if (list) parts.push(list);
    if (work.state === 'undone') {
      parts.push(el('p', 'home-did-note', t('undoneNote')));
      parts.push(button('home-did-act', t('redo'), () => this._redo(turn, node)));
    } else if (work.state === 'expired') {
      parts.push(el('p', 'home-did-note', t('expired')));
    } else if (work.running) {
      parts.push(el('p', 'home-did-note', t('runningNote')));
    } else if (work.working) {
      parts.push(el('p', 'home-did-note', t('workingNote')));
    } else if (work.expect_end) {
      parts.push(el('p', 'home-did-note', i18n.t('home.work.undoLimit')));
      const back = button('home-did-act', null, () => this._undo(turn, node, work.expect_end));
      back.append(icon('ti-arrow-back-up'), el('span', null, t('undo')));
      parts.push(back);
    }
    detail.replaceChildren(...parts);
  }

  async _undo(turn, node, expectEnd) {
    try {
      await rpc.turn('undo', { turn, expect_end: expectEnd });
    } catch (err) {
      const key = { conflict: 'conflict', busy: 'workingNote', running: 'runningNote' }[err?.code]
        || 'failed';
      showToast(t(key), 'error');
      await this._open(turn, node);
      return;
    }
    this._learn(turn, { ...(this._known.get(turn) || {}), state: 'undone', working: false });
    node.querySelector('.home-did-detail')?.remove();
    node.querySelector('.home-did')?.setAttribute('aria-expanded', 'false');
    showUndo(t('undoneLabel'), () => this._redo(turn, node), { label: i18n.t('apps.undo') });
  }

  async _redo(turn, node) {
    try {
      await rpc.turn('redo', { turn });
    } catch {
      showToast(t('failed'), 'error');
      return;
    }
    node.querySelector('.home-did-detail')?.remove();
    await this.ask([turn], { fresh: true });
  }
}
