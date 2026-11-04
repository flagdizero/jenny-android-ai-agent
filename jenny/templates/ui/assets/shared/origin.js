/** Da dove e' entrato un messaggio che non hai scritto qui dentro.
 *
 *  La chat e' il registro completo di tutte le superfici — l'app, Telegram, la
 *  tendina delle notifiche, il fumetto della mascotte, un altro dispositivo del
 *  tailnet — e la provenienza va detta, altrimenti un messaggio scritto dal
 *  blocco schermo sembra comparso dal nulla.
 *
 *  Nomi e non identificatori: fino al 02/11/2026 l'officina scriveva il canale
 *  con l'iniziale maiuscola («Floating», «Notification»), cioe' il nome che ha
 *  nel codice, e in inglese anche col telefono in italiano. Adesso casa e
 *  officina dicono la stessa cosa.
 */

import { i18n } from './i18n.js';
import { escapeHtml } from './utils.js';

const ORIGINS = {
  telegram: { icon: 'ti-brand-telegram', key: 'home.origin.telegram' },
  notification: { icon: 'ti-bell', key: 'home.origin.notification' },
  floating: { icon: 'ti-message-circle', key: 'home.origin.floating' },
  /* Scritto da un altro dispositivo del tailnet (il Mac, un secondo
     telefono). Col nome che gli da' MagicDNS, quando c'e': «dal mac». */
  remote: { icon: 'ti-device-laptop', key: 'home.origin.remote', named: 'home.origin.remoteNamed' },
};

/** Il markup dell'etichetta (icona e nome), o `''` per un messaggio scritto
 *  qui (`websocket`, o nessuna provenienza). Un canale sconosciuto si dice col
 *  suo nome: meglio di niente. */
export function originBadgeHtml(origin, device = null) {
  if (!origin || origin === 'websocket') return '';
  const known = ORIGINS[origin];
  const icon = known ? known.icon : 'ti-arrows-exchange';
  const label = !known ? origin
    : (device && known.named) ? i18n.t(known.named, { device }) : i18n.t(known.key);
  return `<i class="ti ${icon}" aria-hidden="true"></i>${escapeHtml(label)}`;
}

/** L'etichetta di provenienza come nodo (`<div class=cls>`), o `null` per un
 *  messaggio scritto qui. */
export function originBadge(origin, device = null, cls = '') {
  const html = originBadgeHtml(origin, device);
  if (!html) return null;
  const badge = document.createElement('div');
  badge.className = cls;
  badge.innerHTML = html;
  return badge;
}
