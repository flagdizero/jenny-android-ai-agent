/** L'ora sotto un messaggio della chat (06/10/2026).
 *
 *  Solo l'ora se il messaggio e' di oggi («14:32»), data e ora se e' di un
 *  giorno prima («5 ott, 18:04»), e anche l'anno se e' di un altro anno
 *  («5 ott 2025, 18:04»). Niente «ieri» e niente «3 giorni fa»: sotto un
 *  messaggio si legge quando, non quanto tempo fa — `whenText` e' l'altra
 *  domanda, e resta dov'e'.
 *
 *  Sempre a 24 ore, in tutte e due le lingue: «6:04 PM» sotto una bolla e'
 *  largo il doppio. «Oggi» e' deciso al momento del disegno, e non si
 *  ridecide a mezzanotte: una chat aperta a cavallo resta com'era fino alla
 *  rilettura (scelto con l'utente).
 */

import { i18n } from './i18n.js';

/** *ms* (epoch) come va scritto sotto il messaggio, o `''` se non e' un'ora.
 *  *now* e *locale* esistono per i test. */
export function chatTime(ms, { now = Date.now(), locale = i18n.locale } = {}) {
  const value = Number(ms);
  if (!Number.isFinite(value) || value <= 0) return '';
  const at = new Date(value);
  const today = new Date(now);
  const time = at.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  const sameDay = at.getFullYear() === today.getFullYear()
    && at.getMonth() === today.getMonth()
    && at.getDate() === today.getDate();
  if (sameDay) return time;
  const date = at.toLocaleDateString(locale, {
    day: 'numeric',
    month: 'short',
    ...(at.getFullYear() === today.getFullYear() ? {} : { year: 'numeric' }),
  });
  return `${date}, ${time}`;
}

/** Lo stesso, come attributo `datetime` di un `<time>`: chi legge con lo
 *  screen reader o copia dalla pagina ha la data intera. */
export function chatTimeIso(ms) {
  const value = Number(ms);
  return Number.isFinite(value) && value > 0 ? new Date(value).toISOString() : '';
}
