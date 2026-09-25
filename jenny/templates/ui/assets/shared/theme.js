/** Theme registry — single source of truth for the 7 named themes.
 *
 * Each theme is a token block in mobile-style.css selected via
 * `data-theme="<id>"` on <html>. `scheme` drives everything that needs a
 * binary light/dark signal (syntax highlighting, app iframes).
 */

import { AppState } from './state.js';

// `desc`/`reply` are the mini-conversation preview copy shown on each theme
// card in the settings picker — the card *is* the preview, so the copy leans
// evocative, not technical.
export const THEMES = [
  { id: 'chanel',    label: 'Chanel',        scheme: 'dark',
    accent: '#f4f1ea', onAccent: '#141414',
    swatch: ['#141414', '#1e1e1e', '#c8a96a'],
    desc: "Bianco e nero couture, un filo d'oro solo dove conta.",
    reply: 'Il lusso non alza mai la voce.' },
  { id: 'synthwave', label: "Synthwave '84", scheme: 'dark',
    accent: '#f92aad', onAccent: '#ffffff',
    swatch: ['#241b2f', '#f92aad', '#03edf9'],
    desc: 'Neon rosa su notte viola, ogni luce lascia la scia.',
    reply: 'Massimo carattere — per chi non ha paura.' },
  { id: 'kyoto',     label: 'Jenny Kyoto',   scheme: 'dark',
    accent: '#b2543f', onAccent: '#f5efe4',
    swatch: ['#201d1a', '#2b2723', '#b2543f'],
    desc: 'Terra, ruggine e angoli smussati a mano, come ceramica riparata.',
    reply: 'La quiete non chiede attenzione.' },
  { id: 'sticker',   label: 'Jenny Sticker', scheme: 'dark',
    accent: '#a78bfa', onAccent: '#1c1523',
    swatch: ['#17131c', '#a78bfa', '#ffd23f'],
    desc: 'Ogni messaggio è un adesivo ritagliato: bordo bianco, ombra dura, rotazione imperfetta.',
    reply: 'La chat come il retro di un laptop.' },
  { id: 'comic',   label: 'Jenny Fumetto', scheme: 'light',
    accent: '#1b1820', onAccent: '#faf6ef',
    swatch: ['#faf6ef', '#1b1820', '#7c5cff'],
    desc: 'China su carta, retini e nuvolette: ogni risposta è una vignetta.',
    reply: 'La tua chat è una tavola da leggere.' },
  { id: 'y2k',       label: 'Jenny Y2K',     scheme: 'light',
    accent: '#f56ab5', onAccent: '#ffffff',
    swatch: ['#f7e0f8', '#f56ab5', '#b76bf0'],
    desc: 'Gradienti lucidi, gloss bubblegum, bordi bianchi.',
    reply: 'Il duemila come ce lo eravamo promesso.' },
  { id: 'stone',     label: 'Jenny Pietra',  scheme: 'light',
    accent: '#8c6f4e', onAccent: '#f4f1ec',
    swatch: ['#eae6df', '#37332c', '#8c6f4e'],
    desc: 'Travertino, bronzo e serif romani, luce di mezzogiorno.',
    reply: "Solida come un'idea scolpita bene." },
];

export const DEFAULT_THEME = 'chanel';

/** Ponte dei token verso le mini-app (iframe a origine opaca).
 *
 * Le custom property non attraversano il confine dell'iframe, così
 * `jenny-kit.css` porta una copia dei token della SPA — ma statica: una palette
 * dark e una light, non i 7 temi. Il risultato era che l'unico colore a seguire
 * il tema era `--accent` (l'unico che l'SDK riscriveva): tutto il resto restava
 * l'indaco di riserva del kit, visibile sul velo di sfondo del `<body>` e sul
 * pressed dei bottoni primari.
 *
 * Questa mappa dice quale token della SPA alimenta ogni token del kit, e
 * `themeTokens()` ne legge i valori *risolti* dal tema attivo: il kit non può
 * più andare fuori sincrono con `mobile-style.css`, e un tema nuovo arriva
 * nelle app senza toccare niente.
 *
 * Fuori dalla mappa, di proposito:
 * - `--accent` / `--on-accent` viaggiano già sul loro canale (`accent=` /
 *   `onAccent=`), da cui l'SDK deriva `--accent-rgb`, `--accent-subtle` e
 *   `--bg-pattern`;
 * - `--green` / `--success-bg`: l'`--ok` della SPA vale avorio su chanel,
 *   giallo su sticker (lo stesso identico del suo `--warning`) e quasi nero su
 *   fumetto. Nella SPA il significato lo porta l'icona accanto; in un'app
 *   `.badge-ok` è una pillola isolata dove il colore *è* il messaggio, e su 3
 *   temi su 7 smetterebbe di leggersi come "ok". Resta il verde del kit;
 * - `--radius*`, `--font-*`: strutturali, non dipendono dal tema.
 */
export const APP_TOKEN_MAP = {
  '--bg-solid':     '--bg',
  '--bg':           '--surface',
  '--bg2':          '--surface',
  '--bg3':          '--surface-2',
  '--border':       '--border',
  '--border2':      '--border-strong',
  '--glass-border': '--border-strong',
  '--glass':        '--overlay',
  '--glass-strong': '--overlay-strong',
  '--hover-bg':     '--hover',
  '--text':         '--text',
  '--text2':        '--text-muted',
  '--text3':        '--text-faint',
  '--heading':      '--heading',
  '--accent-hover': '--accent-hover',
  '--error':        '--error',
  '--warning':      '--warning',
};

/** Palette del tema attivo per un'app, come `name:value;...` (senza `--`).
 *
 *  Legge i valori *calcolati* invece dei letterali del registro: è la stessa
 *  lettura che fa `syncNativeBars` per le barre di sistema, e vale anche per i
 *  token che il registro non conosce. Viaggia nella query string dell'iframe
 *  (il primo paint non può aspettare un postMessage) e nel messaggio
 *  `jenny:theme` a ogni cambio tema. */
export function themeTokens() {
  const cs = getComputedStyle(document.documentElement);
  const out = [];
  for (const [kit, spa] of Object.entries(APP_TOKEN_MAP)) {
    const value = cs.getPropertyValue(spa).trim();
    if (value) out.push(kit.slice(2) + ':' + value);
  }
  return out.join(';');
}

/** Legacy 'tc-theme' values from the old dark/light/match switcher. */
/* I nomi di prima: `dark`/`light`/`match` erano i modi, `fumetto` e `pietra` gli id
   italiani dei due temi fino al 25/09/2026. Un `tc-theme` salvato con uno di
   questi si legge col nome di adesso (e lo riscrive `setTheme`). */
export const MIGRATION = { dark: 'chanel', light: 'stone', match: 'chanel', fumetto: 'comic', pietra: 'stone' };

export function getTheme(id) {
  return THEMES.find(t => t.id === id) || null;
}

export function currentTheme() {
  return getTheme(document.documentElement.getAttribute('data-theme')) ||
    getTheme(DEFAULT_THEME);
}

/** Allinea le barre di sistema Android al tema: colore = `--bg` calcolato (la
 *  fonte di verità resta il CSS), icone = schema. Senza questo la status bar
 *  resta del colore fisso del tema Android e stona con 6 temi su 7. No-op fuori
 *  dalla WebView, o su un APK più vecchio del metodo. */
function syncNativeBars(scheme) {
  const native = window.JennyNative;
  if (!native || typeof native.setThemeBars !== 'function') return;
  const bg = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
  if (!bg) return;
  try {
    native.setThemeBars(bg, scheme);
  } catch (e) {
    /* bridge non disponibile: le barre restano quelle di themes.xml */
  }
}

/** Un colore CSS in `#AARRGGBB`, la sola forma che `Color.parseColor` legge.
 *
 *  Non è una precauzione teorica: `getComputedStyle` restituisce le custom
 *  property **alla lettera**, e tre temi su sette scrivono i bordi come
 *  `rgba(244, 241, 234, 0.28)`. Passata così, quella stringa fa sollevare il
 *  parser di Android — cioè un bordo che resta del tema di prima, in silenzio.
 *  Qui il valore è già risolto, quindi la conversione si fa qui. */
export function argbHex(value) {
  const css = (value || '').trim();
  const hex = css.match(/^#([0-9a-f]{3,8})$/i);
  if (hex) {
    let h = hex[1];
    if (h.length === 3 || h.length === 4) h = h.split('').map(c => c + c).join('');
    if (h.length === 6) return '#ff' + h.toLowerCase();
    // `#rrggbbaa` è CSS, `#aarrggbb` è Android: l'alfa cambia di posto.
    if (h.length === 8) return ('#' + h.slice(6) + h.slice(0, 6)).toLowerCase();
    return '';
  }
  const fn = css.match(/^rgba?\(([^)]+)\)$/i);
  if (!fn) return '';
  const parts = fn[1].split(/[,/\s]+/).filter(Boolean);
  if (parts.length < 3) return '';
  const byte = n => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0');
  const alpha = parts.length > 3 ? parseFloat(parts[3]) * 255 : 255;
  if (parts.slice(0, 3).some(p => Number.isNaN(parseFloat(p)))) return '';
  return '#' + byte(alpha) + parts.slice(0, 3).map(p => byte(parseFloat(p))).join('');
}

/** I token che vestono la finestra flottante, nell'ordine del ponte. */
const FLOATING_TOKENS = [
  '--surface', '--border-strong', '--text', '--text-faint', '--accent', '--on-accent',
];

/** Veste la mascotte flottante con il tema attivo.
 *
 *  Là non c'è CSS — sono `View` in px — quindi i colori erano scritti nel
 *  Kotlin, ed erano quelli di `chanel` per tutti e sette i temi: con Synthwave
 *  la barra restava avorio sopra un'app rosa. Come per la taglia
 *  (`shared/mascot.js`), la fonte di verità resta qui e il guscio la riceve.
 *  No-op fuori dalla WebView, o su un APK più vecchio del metodo. */
function syncFloatingPalette() {
  const native = window.JennyNative;
  if (!native || typeof native.setFloatingPalette !== 'function') return;
  const cs = getComputedStyle(document.documentElement);
  const colors = FLOATING_TOKENS.map(t => argbHex(cs.getPropertyValue(t)));
  if (colors.some(c => !c)) return;
  try {
    native.setFloatingPalette(...colors);
  } catch (e) {
    /* ponte assente: la finestra resta con i colori che ha */
  }
}

/** Toggle the dark/light syntax stylesheets (highlight.js + CodeMirror). */
export function applySyntaxTheme(scheme) {
  for (const [id, s] of [
    ['hljs-theme-dark', 'dark'], ['hljs-theme-light', 'light'],
    ['cm-theme-dark', 'dark'], ['cm-theme-light', 'light'],
  ]) {
    const link = document.getElementById(id);
    if (link) link.media = s === scheme ? 'all' : 'not all';
  }
}

export function setTheme(id) {
  const theme = getTheme(MIGRATION[id] || id) || getTheme(DEFAULT_THEME);
  document.documentElement.setAttribute('data-theme', theme.id);
  localStorage.setItem('tc-theme', theme.id);
  AppState.theme = theme.id;
  applySyntaxTheme(theme.scheme);
  syncNativeBars(theme.scheme);
  syncFloatingPalette();
  window.dispatchEvent(new CustomEvent('themechange', { detail: theme }));
  return theme;
}

// Align the syntax stylesheets, the system bars and the floating mascot with
// the theme the boot script applied.
applySyntaxTheme(currentTheme().scheme);
syncNativeBars(currentTheme().scheme);
syncFloatingPalette();
