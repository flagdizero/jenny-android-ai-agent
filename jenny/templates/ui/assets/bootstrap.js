// Pre-render bootstrap — must run synchronously before the body renders to
// avoid a flash of the wrong theme / locale (FOUC). Extracted from two inline
// <script> blocks in index.html so the SPA can be served with a
// script-src 'self' CSP (M1). Load WITHOUT defer, in <head>, so it still
// executes before the rest of the document (same timing as the old inline).
(function () {
  // `localStorage` puo' sollevare (dati del sito bloccati, anteprima): qui
  // un errore fermerebbe l'intero script, e con lui il tema e la lingua
  // della pagina. Si legge null e non si scrive;
  // la stessa regola di `readStorage` in shared/utils.js.
  function read(key) {
    try { return localStorage.getItem(key); } catch (_) { return null; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, value); } catch (_) { /* storage non disponibile */ }
  }
  var THEMES = ['chanel', 'synthwave', 'kyoto', 'sticker', 'comic', 'y2k', 'stone'];
  // Come MIGRATION in shared/theme.js: i modi di una volta e gli id italiani
  // dei temi fino al 25/09/2026.
  var MIGRATION = { dark: 'chanel', light: 'stone', match: 'chanel', fumetto: 'comic', pietra: 'stone' };
  // Il default e' DEFAULT_THEME di shared/theme.js (qui non si importa: lo
  // script e' classico e deve girare prima di tutto il resto). Dal 27/09/2026
  // e' `synthwave`; chi aveva gia' un `tc-theme` salvato lo tiene.
  var t = read('tc-theme') || 'synthwave';
  t = MIGRATION[t] || t;
  if (THEMES.indexOf(t) === -1) t = 'synthwave';
  write('tc-theme', t);
  document.documentElement.setAttribute('data-theme', t);

  // La lingua della pagina, con la regola di `i18n.detectLocale()`: quella del
  // telefono, italiano o inglese. Non piu' `localStorage.locale`, che solo il
  // selettore tolto dall'officina scriveva (v. shared/i18n.js).
  var nav = navigator.language || '';
  document.documentElement.lang = nav.indexOf('it') === 0 ? 'it' : 'en';

  // Mascotte, taglia: anti-flash come il tema. Il default CSS vale solo per
  // 'sm', quindi senza questo chi ha scelto un'altra taglia vedrebbe Jenny
  // comparire piccola e poi ridimensionarsi. (Fino al 05/10/2026 qui si
  // leggeva anche «non visibile»: la preferenza non c'e' piu', Jenny e' la
  // porta della chat. La chiave si chiamava `jenny-mascotte-size` fino al
  // 25/09/2026: finche' shared/mascot.js non l'ha copiata, si legge anche
  // il nome vecchio.) Le misure sono duplicate da MASCOT_SIZES in
  // shared/mascot.js — qui non si possono importare moduli.
  var mascotSizes = { sm: '120px', md: '160px', lg: '210px' };
  var mascotSize = mascotSizes[read('jenny-mascot-size') || read('jenny-mascotte-size')];
  if (mascotSize) {
    document.documentElement.style.setProperty('--jenny-size', mascotSize);
  }
})();
