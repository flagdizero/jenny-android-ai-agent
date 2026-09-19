/** Quando e' successo, detto come lo direbbe una persona.
 *
 *  Serve a due posti che non hanno niente in comune fra loro — l'ultimo
 *  controllo degli aggiornamenti e l'ultimo backup esportato — e la domanda
 *  invece e' la stessa: «quand'e' stata l'ultima volta?». Stava dentro
 *  `update-flow.js`, ed e' uscita di li' quando il secondo posto ne ha avuto
 *  bisogno: una riga di backup che importa la macchina degli aggiornamenti
 *  per avere una data e' una dipendenza che non vuol dire niente.
 */

import { i18n } from './i18n.js';

/** «oggi alle 14:22», «3 giorni fa», «27 giu 2025».
 *
 *  Relativo finche' resta leggibile, datato dopo: a novanta giorni «90 giorni
 *  fa» non dice piu' niente, una data si'. «Ieri» ha un ramo suo perche' «1
 *  giorni fa» si legge male in tutte e due le lingue. Le date passano da
 *  `toLocaleDateString`, che le localizza da se'.
 */
export function whenText(ms) {
  const at = new Date(Number(ms) || 0);
  const dayMs = 86400000;
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  const time = at.toLocaleTimeString(i18n.locale, { hour: '2-digit', minute: '2-digit' });
  if (at.getTime() >= midnight.getTime()) return i18n.t('settings.update.whenToday', { time });
  if (at.getTime() >= midnight.getTime() - dayMs) {
    return i18n.t('settings.update.whenYesterday', { time });
  }
  /* Arrotondato per eccesso: il ramo «ieri» qui sopra garantisce gia' >= 2, e
     troncando, un controllo di tre giorni fa alle 23:00 diventerebbe «2». */
  const days = Math.ceil((midnight.getTime() - at.getTime()) / dayMs);
  if (days <= 30) return i18n.t('settings.update.whenDays', { days });
  return at.toLocaleDateString(i18n.locale, { day: 'numeric', month: 'short', year: 'numeric' });
}
