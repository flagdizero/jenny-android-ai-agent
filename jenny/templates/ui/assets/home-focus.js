/** La casa — il fuoco resta sul campo dove scrivi.
 *
 *  Sul Titan 2 la tastiera e' fisica: non c'e' una tastiera a schermo che
 *  compaia e scompaia col fuoco, e il fuoco e' l'unica cosa che decide dove
 *  finisce un tasto. Il browser lo toglie al campo a ogni tocco su qualunque
 *  altra cosa — il filo, una bolla, il tasto manda, un nome della fila — e da
 *  li' i tasti cadono nel vuoto senza che niente lo mostri. Visto sul telefono
 *  il 24/09/2026: tocco sul filo, poi «b», e la «b» non c'era.
 *
 *  Due difese, una per verso:
 *
 *  - **Type-ahead.** Un carattere premuto mentre il fuoco non e' in un campo
 *    rimette il fuoco sul campo, e il carattere ci finisce dentro. Vale su
 *    ogni telefono, perche' senza tastiera fisica un tasto nel vuoto non puo'
 *    esistere. Le guardie sono quelle dell'officina e del cassetto, alla
 *    lettera: `shared/type-ahead.js`.
 *  - **Il tocco non ruba il fuoco.** Con la tastiera fisica, un tocco sulla
 *    chat che non cade su un altro campo non sposta il fuoco — e se il campo
 *    l'aveva perso, glielo ridà. Il `click` parte lo stesso: il tasto manda
 *    manda, la graffetta allega, il link si apre. Solo con la tastiera
 *    fisica, perche' altrove il fuoco sul campo e' una tastiera a schermo
 *    alzata sopra meta' della conversazione che stai leggendo.
 *
 *  E una terza, per il cursore:
 *
 *  - **Il cursore resta dove l'avevi lasciato.** Il campo perde il fuoco a
 *    ogni pagina che lasci e lo riprende quando torni; e al ritorno il
 *    cursore finiva all'inizio della bozza, cosi' il tasto dopo ci scriveva in
 *    testa. Misurato il 10/10/2026 sull'emulatore: succede quando lo swipe di
 *    ritorno parte sopra un campo della pagina che lasci (la ricerca dei
 *    Quaderni), 3 volte su 3, e mai partendo da un'area senza campi. Non e'
 *    il nostro `focus()`: senza, Chromium da' il fuoco al campo da se', 4 ms
 *    dopo il rilascio, e il cursore va a 0 pochi ms dopo. Quindi il campo
 *    ricorda dov'era il cursore quando ha perso il fuoco, e quando lo
 *    riprende **senza un dito sopra** lo rimette li' — anche contro uno
 *    spostamento che arriva subito dopo (`CARET_GUARD_MS`). Un tocco sul
 *    campo, invece, mette il cursore dove tocchi: quello lo decidi tu.
 *
 *  Il modulo non sa quando la chat e' a schermo: lo chiede a chi lo crea
 *  (`active`), che conosce stanze, pagine e strati.
 */

import { isTypeAheadKey } from './shared/type-ahead.js';

/* Chi riceve gia' testo per conto suo: un tocco li' sposta il fuoco, ed e'
   giusto — e' un campo in cui hai scelto di scrivere. */
function _isField(el) {
  return Boolean(el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' ||
                        el.tagName === 'SELECT' || el.isContentEditable));
}

/** C'e' una tastiera fisica, adesso?
 *
 *  Lo sa il guscio nativo (`Configuration.keyboard`), e lo sa prima del primo
 *  tasto: indovinarlo dagli eventi vorrebbe dire sbagliare proprio sul primo
 *  tocco. Lo si chiede a ogni uso e non una volta sola, perche' una tastiera
 *  Bluetooth si attacca e si stacca. Fuori dal guscio — un browser sul Mac —
 *  un puntatore fine vuol dire un mouse, e accanto a un mouse c'e' una
 *  tastiera. */
export function physicalKeyboard(win = globalThis.window) {
  const native = win?.JennyNative;
  if (typeof native?.hasHardwareKeyboard === 'function') {
    try { return Boolean(native.hasHardwareKeyboard()); } catch { return false; }
  }
  if (native) return false;  // guscio vecchio: nel dubbio, come prima
  return Boolean(win?.matchMedia?.('(any-pointer: fine)')?.matches);
}

/** Per quanto, dopo un fuoco tornato senza un dito sul campo, il cursore si
 *  rimette dove l'avevi lasciato se qualcosa lo sposta. Lo spostamento
 *  misurato arriva 3-15 ms dopo il `focus`; un tasto vero cambia il testo, e
 *  da li' la guardia non vale piu'. */
export const CARET_GUARD_MS = 150;

/** Quanto dura «c'e' un dito sul campo»: il fuoco di un tocco arriva col
 *  `mousedown` di compatibilita', dopo il rilascio. */
const PLACING_MS = 1000;

export class ComposerFocus {
  /**
   * @param {object} opts
   * @param {HTMLTextAreaElement} opts.input il campo dove scrivi.
   * @param {Element[]} opts.superfici dove un tocco non deve rubargli il fuoco.
   * @param {() => boolean} opts.attivo il campo e' a schermo, e niente gli sta sopra.
   * @param {() => boolean} [opts.tastiera] c'e' una tastiera fisica.
   * @param {Document} [opts.doc]
   * @param {() => number} [opts.now] l'orologio, in ms.
   */
  constructor({
    input, surfaces, active, keyboard = physicalKeyboard, doc = document, now = () => Date.now(),
  }) {
    this.input = input;
    this.active = active;
    this.keyboard = keyboard;
    this.doc = doc;
    this.now = now;
    /** Il testo e il cursore all'ultima uscita dal campo. */
    this._caret = null;
    this._placingAt = -Infinity;
    this._guardUntil = 0;
    doc.addEventListener('keydown', (e) => this._onKey(e));
    for (const el of surfaces) el?.addEventListener('mousedown', (e) => this._onTap(e));
    /* Tornando all'app da un'altra, il campo riprende il fuoco: sul Titan si
       torna per scrivere. */
    doc.addEventListener('visibilitychange', () => {
      if (doc.visibilityState === 'visible') this.restore();
    });
    input?.addEventListener('focusout', () => this._keepCaret());
    input?.addEventListener('pointerdown', () => { this._placingAt = this.now(); });
    input?.addEventListener('focus', () => this._onFocus());
    input?.addEventListener('selectionchange', () => this._onSelection());
  }

  /** Il fuoco sul campo, se c'e' una tastiera fisica e il campo e' a schermo.
   *  Lo chiama chi riporta la chat davanti — una pagina, una stanza, Home. */
  restore() {
    if (!this.input || !this.keyboard() || !this.active()) return false;
    this._focus();
    return true;
  }

  /* `preventScroll` sempre: la chat puo' essere a meta' di una scivolata della
     pista, e mettere a fuoco un campo fuori vista fa scorrere il browser per
     raggiungerlo — la lezione di `test_chat_focus_no_scroll_contract.py`. */
  _focus() {
    if (this.doc.activeElement !== this.input) this.input.focus({ preventScroll: true });
  }

  _onKey(e) {
    if (!this.input || !isTypeAheadKey(e, this.doc.activeElement)) return;
    if (!this.active()) return;
    /* `focus()` sincrono dentro il keydown: Chromium recapita il carattere
       all'elemento che ha il fuoco quando lo inserisce, cioe' al campo. */
    this._focus();
  }

  /* `mousedown` e non `pointerdown`: su un tocco il browser sposta il fuoco
     come azione di default del `mousedown` di compatibilita', ed e' li' che
     si puo' impedirlo. La pressione lunga non ne produce, quindi selezionare
     il testo di una bolla resta com'era. */
  _onTap(e) {
    if (!this.input || _isField(e.target)) return;
    if (!this.keyboard() || !this.active()) return;
    e.preventDefault();
    this._focus();
  }

  _keepCaret() {
    const { value, selectionStart: start, selectionEnd: end } = this.input;
    this._caret = { value, start, end };
    this._guardUntil = 0;
  }

  /* Il cursore ricordato vale solo per lo stesso testo: una bozza cambiata
     mentre eri via (un invio, una frase pronta) ha il suo. */
  _kept() {
    const kept = this._caret;
    return kept && kept.value === this.input.value ? kept : null;
  }

  _putCaret({ start, end }) {
    if (this.input.selectionStart === start && this.input.selectionEnd === end) return;
    try { this.input.setSelectionRange(start, end); } catch { /* campo senza selezione */ }
  }

  /* Sincrono, dentro il `focus`: col type-ahead il carattere entra subito dopo,
     e deve entrare dove eri, non all'inizio. */
  _onFocus() {
    const placing = this.now() - this._placingAt < PLACING_MS;
    this._placingAt = -Infinity;
    const kept = placing ? null : this._kept();
    if (!kept) return;
    this._putCaret(kept);
    this._guardUntil = this.now() + CARET_GUARD_MS;
  }

  _onSelection() {
    if (this.now() > this._guardUntil) return;
    const kept = this._kept();
    if (!kept) {
      this._guardUntil = 0;
      return;
    }
    this._putCaret(kept);
  }
}
