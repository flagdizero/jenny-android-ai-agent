/** Quando e' successo, detto come lo direbbe una persona.
 *
 *  Serve a posti che non hanno niente in comune fra loro — l'ultimo controllo
 *  degli aggiornamenti, l'ultimo backup esportato, uno snapshot, un buco di
 *  batteria, il cassetto riscritto — e la domanda invece e' la stessa:
 *  «quand'e' stata l'ultima volta?». Stava dentro `update-flow.js`, ed e'
 *  uscita di li' quando il secondo posto ne ha avuto bisogno.
 *
 *  «oggi alle 14:22» e «ieri alle 14:22» erano scritti quattro volte, con
 *  quattro coppie di chiavi i18n identiche e due modi di contare i giorni:
 *  adesso sono `dayAndTime`, su `common.todayAt`/`common.yesterdayAt`, e il
 *  giorno si conta sul calendario (`dayOffset`).
 */

import { i18n } from './i18n.js';

/** Quanti giorni di calendario fa: 0 oggi, 1 ieri, negativo nel futuro.
 *
 *  Sul calendario del telefono e non a blocchi di 24 ore: le 23:50 di ieri
 *  sono «ieri» anche alle 00:10, e nel giorno di un cambio d'ora (23 o 25 ore)
 *  il conto non salta. */
export function dayOffset(at, now = Date.now()) {
  const day = (d) => {
    const c = new Date(d instanceof Date ? d.getTime() : Number(d) || 0);
    return new Date(c.getFullYear(), c.getMonth(), c.getDate()).getTime();
  };
  return Math.round((day(now) - day(at)) / 86400000);
}

/** «12 ott» (o «14 ottobre» con `month: 'long'`) da un ISO, dei millisecondi o
 *  una `Date`; `''` se non e' una data. Era scritto sette volte: le note e le
 *  foto della bacheca, il cestino delle app, il cassetto, i blocchi, la copia
 *  su Drive. */
export function shortDate(at, { month = 'short', locale = i18n.locale } = {}) {
  const when = at instanceof Date ? at : (at || at === 0 ? new Date(at) : null);
  if (!when || Number.isNaN(when.getTime())) return '';
  return when.toLocaleDateString(locale, { day: 'numeric', month });
}

/** «oggi alle 3:12», «ieri alle 3:12», «giovedì alle 3:12» entro la settimana;
 *  `''` piu' in la', dove ogni posto dice la data a modo suo. L'ora senza zero
 *  davanti, come la scrivono la copia su Drive e gli snapshot, che ne avevano
 *  una copia ciascuno con due chiavi uguali. */
export function nearDay(at, { now = Date.now(), locale = i18n.locale } = {}) {
  const when = at instanceof Date ? at : new Date(Number(at) || 0);
  const near = dayAndTime(when, { now, locale, hour: 'numeric' });
  if (near || dayOffset(when, now) >= 7) return near;
  return i18n.t('common.weekdayAt', {
    day: when.toLocaleDateString(locale, { weekday: 'long' }),
    time: when.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit' }),
  });
}

/** «2 ore fa» da un mtime unix in **secondi**; `''` se non si sa.
 *
 *  Le chiavi sono `scope.ago.*`, le stesse per i due gusci (la tendina
 *  dell'officina e il pannello «con chi parli» della casa). Un'ora e'
 *  singolare — da 60 a 119 minuti si leggeva «1 ore fa» — e i giorni si
 *  contano sul calendario, come `dayOffset`: a blocchi di 24 ore, 47 ore fa
 *  era «ieri» anche quando era l'altro ieri. */
export function ago(modified, now = Date.now()) {
  if (!modified) return '';
  const minutes = Math.floor((now / 1000 - modified) / 60);
  if (minutes < 2) return i18n.t('scope.ago.now');
  if (minutes < 60) return i18n.t('scope.ago.minutes', { n: String(minutes) });
  const hours = Math.floor(minutes / 60);
  if (hours === 1) return i18n.t('scope.ago.hour');
  if (hours < 24) return i18n.t('scope.ago.hours', { n: String(hours) });
  const days = Math.max(1, dayOffset(modified * 1000, now));
  return days === 1 ? i18n.t('scope.ago.yesterday') : i18n.t('scope.ago.days', { n: String(days) });
}

/** «oggi alle 14:22» o «ieri alle 14:22», o `''` per un giorno piu' lontano:
 *  li' ogni posto dice la data a modo suo (un giorno della settimana, «3 giorni
 *  fa», «3 ott»). Un momento nel futuro (un orologio indietro) conta come oggi.
 *  *hour* e' il formato dell'ora (`'2-digit'` o `'numeric'`). */
export function dayAndTime(at, { now = Date.now(), locale = i18n.locale, hour = '2-digit' } = {}) {
  const when = at instanceof Date ? at : new Date(Number(at) || 0);
  const days = dayOffset(when, now);
  if (days > 1) return '';
  const time = when.toLocaleTimeString(locale, { hour, minute: '2-digit' });
  return i18n.t(days === 1 ? 'common.yesterdayAt' : 'common.todayAt', { time });
}

/** «oggi alle 14:22», «3 giorni fa», «27 giu 2025».
 *
 *  Relativo finche' resta leggibile, datato dopo: a novanta giorni «90 giorni
 *  fa» non dice piu' niente, una data si'. «Ieri» ha un ramo suo perche' «1
 *  giorni fa» si legge male in tutte e due le lingue. Le date passano da
 *  `toLocaleDateString`, che le localizza da se'.
 */
export function whenText(ms) {
  const near = dayAndTime(ms);
  if (near) return near;
  const days = dayOffset(ms);
  if (days <= 30) return i18n.t('settings.update.whenDays', { days });
  const at = new Date(Number(ms) || 0);
  return at.toLocaleDateString(i18n.locale, { day: 'numeric', month: 'short', year: 'numeric' });
}
