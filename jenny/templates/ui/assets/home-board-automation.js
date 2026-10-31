/** La casa — un'automazione del quaderno (un cron), come la mostrano il cassetto
 *  di una scheda e il foglio dell'etichetta di tutto il quaderno.
 *
 *  Una riga sola per due posti: cosa fa, ogni quanto, il prossimo giro, l'ultimo
 *  esito, e i tre gesti (pausa, cambia, togli). Dal 20/10/2026 un cron non è più
 *  una sezione del cassetto: arriva dal job (`automations` nella vista), e chi lo
 *  mostra decide solo dove metterlo e come scrivere.
 */

import { i18n } from './shared/i18n.js';
import { describeSchedule } from './shared/cron-view.js';

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

/** Il ritmo di un cron a parole («ogni domenica alle 18:30»), dal suo `schedule`. */
export function rhythmLabel(job) {
  const tr = (key, vars) => i18n.t(key, vars);
  return describeSchedule(job?.schedule, { tr, locale: i18n.locale });
}

/** La riga di un'automazione.
 *
 *  @param job   l'istantanea del job (`cron_link.job_snapshot`), col suo `result`.
 *  @param opts  `{ stamp(iso), onPause(job), onChange(job), onRemove(job) }`:
 *               `stamp` scrive un istante («oggi alle 9:00»), i tre gesti li fa
 *               chi mostra la riga (il cassetto ricarica sé, il foglio la bacheca). */
export function automationRow(job, { stamp, onPause, onChange, onRemove }) {
  const row = el('div', 'home-drawer-section is-user is-automation');
  row.dataset.job = job.id;
  const h3 = el('h3', 'home-drawer-section-title', job.name);
  const body = el('div', 'home-drawer-section-body');
  if (job.message) body.appendChild(el('p', 'home-drawer-text', job.message));
  const result = job.result;
  if (result?.text) {
    body.appendChild(el('p', 'home-drawer-proof',
      i18n.t('board.cron.last', { when: stamp(result.textAt || result.at), text: result.text })));
  } else if (result?.at) {
    body.appendChild(el('p', 'home-drawer-hint', i18n.t('board.cron.quiet', { when: stamp(result.at) })));
  }
  /* Lavora Jenny, quindi costa: lo dice la firma, accanto a ogni quanto. */
  const sign = [rhythmLabel(job)];
  if (job.paused) sign.push(i18n.t('board.cron.paused'));
  else if (job.nextRunAtMs) {
    sign.push(i18n.t('board.cron.next', { when: stamp(new Date(job.nextRunAtMs).toISOString()) }));
  }
  sign.push(i18n.t('board.cron.costs'));
  body.appendChild(el('div', 'home-drawer-hint', sign.join(' · ')));
  const tools = el('div', 'home-drawer-row');
  tools.append(
    button('home-drawer-link', i18n.t(job.paused ? 'board.micro.resume' : 'board.micro.pause'),
      () => onPause(job)),
    button('home-drawer-link', i18n.t('board.micro.change'), () => onChange(job)),
    button('home-drawer-link', i18n.t('board.section.remove'), () => onRemove(job)),
  );
  body.appendChild(tools);
  row.append(h3, body);
  return row;
}
