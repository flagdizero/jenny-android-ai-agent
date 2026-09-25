// Pre-render bootstrap — must run synchronously before the body renders to
// avoid a flash of the wrong theme / locale (FOUC). Extracted from two inline
// <script> blocks in index.html so the SPA can be served with a
// script-src 'self' CSP (M1). Load WITHOUT defer, in <head>, so it still
// executes before the rest of the document (same timing as the old inline).
(function () {
  var THEMES = ['chanel', 'synthwave', 'kyoto', 'sticker', 'comic', 'y2k', 'pietra'];
  var MIGRATION = { dark: 'chanel', light: 'pietra', match: 'chanel' };
  var t = localStorage.getItem('tc-theme') || 'chanel';
  t = MIGRATION[t] || t;
  if (THEMES.indexOf(t) === -1) t = 'chanel';
  localStorage.setItem('tc-theme', t);
  document.documentElement.setAttribute('data-theme', t);

  // La lingua della pagina, con la regola di `i18n.detectLocale()`: quella del
  // telefono, italiano o inglese. Non piu' `localStorage.locale`, che solo il
  // selettore tolto dall'officina scriveva (v. shared/i18n.js).
  var nav = navigator.language || '';
  document.documentElement.lang = nav.indexOf('it') === 0 ? 'it' : 'en';

  // Mascotte: anti-flash come il tema. La verità a runtime resta in
  // shared/mascot.js (localStorage + evento 'mascotchange'); qui solo
  // l'attributo iniziale su <html> per evitare che lampeggi visibile prima
  // che mobile-jenny.js applichi la preferenza "non visibile" (il lato non
  // serve: la mascotte è creata da JS e posizionata prima del primo paint).
  var mascotVisible = localStorage.getItem('jenny-mascot-visible');
  if (mascotVisible === '0') {
    document.documentElement.setAttribute('data-mascot-hidden', '1');
  }
  // Taglia: stesso anti-flash. Il default CSS vale solo per 'sm', quindi senza
  // questo chi ha scelto un'altra taglia vedrebbe Jenny comparire piccola e
  // poi ridimensionarsi. Le misure sono duplicate da MASCOT_SIZES in
  // shared/mascot.js — qui non si possono importare moduli.
  var mascotSizes = { sm: '120px', md: '160px', lg: '210px' };
  var mascotSize = mascotSizes[localStorage.getItem('jenny-mascot-size')];
  if (mascotSize) {
    document.documentElement.style.setProperty('--jenny-size', mascotSize);
  }
})();
