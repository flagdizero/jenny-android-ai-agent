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
 *  Il modulo non sa quando la chat e' a schermo: lo chiede a chi lo crea
 *  (`attivo`), che conosce stanze, pagine e strati.
 */

import { isTypeAheadKey } from './shared/type-ahead.js';

/* Chi riceve gia' testo per conto suo: un tocco li' sposta il fuoco, ed e'
   giusto — e' un campo in cui hai scelto di scrivere. */
function _eCampo(el) {
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
export function tastieraFisica(win = globalThis.window) {
  const nativo = win?.JennyNative;
  if (typeof nativo?.hasHardwareKeyboard === 'function') {
    try { return Boolean(nativo.hasHardwareKeyboard()); } catch { return false; }
  }
  if (nativo) return false;  // guscio vecchio: nel dubbio, come prima
  return Boolean(win?.matchMedia?.('(any-pointer: fine)')?.matches);
}

export class FuocoComposer {
  /**
   * @param {object} opts
   * @param {HTMLTextAreaElement} opts.input il campo dove scrivi.
   * @param {Element[]} opts.superfici dove un tocco non deve rubargli il fuoco.
   * @param {() => boolean} opts.attivo il campo e' a schermo, e niente gli sta sopra.
   * @param {() => boolean} [opts.tastiera] c'e' una tastiera fisica.
   * @param {Document} [opts.doc]
   */
  constructor({ input, superfici, attivo, tastiera = tastieraFisica, doc = document }) {
    this.input = input;
    this.attivo = attivo;
    this.tastiera = tastiera;
    this.doc = doc;
    doc.addEventListener('keydown', (e) => this._onTasto(e));
    for (const el of superfici) el?.addEventListener('mousedown', (e) => this._onTocco(e));
    /* Tornando all'app da un'altra, il campo riprende il fuoco: sul Titan si
       torna per scrivere. */
    doc.addEventListener('visibilitychange', () => {
      if (doc.visibilityState === 'visible') this.rimetti();
    });
  }

  /** Il fuoco sul campo, se c'e' una tastiera fisica e il campo e' a schermo.
   *  Lo chiama chi riporta la chat davanti — una pagina, una stanza, Home. */
  rimetti() {
    if (!this.input || !this.tastiera() || !this.attivo()) return false;
    this._fuoco();
    return true;
  }

  /* `preventScroll` sempre: la chat puo' essere a meta' di una scivolata della
     pista, e mettere a fuoco un campo fuori vista fa scorrere il browser per
     raggiungerlo — la lezione di `test_chat_focus_no_scroll_contract.py`. */
  _fuoco() {
    if (this.doc.activeElement !== this.input) this.input.focus({ preventScroll: true });
  }

  _onTasto(e) {
    if (!this.input || !isTypeAheadKey(e, this.doc.activeElement)) return;
    if (!this.attivo()) return;
    /* `focus()` sincrono dentro il keydown: Chromium recapita il carattere
       all'elemento che ha il fuoco quando lo inserisce, cioe' al campo. */
    this._fuoco();
  }

  /* `mousedown` e non `pointerdown`: su un tocco il browser sposta il fuoco
     come azione di default del `mousedown` di compatibilita', ed e' li' che
     si puo' impedirlo. La pressione lunga non ne produce, quindi selezionare
     il testo di una bolla resta com'era. */
  _onTocco(e) {
    if (!this.input || _eCampo(e.target)) return;
    if (!this.tastiera() || !this.attivo()) return;
    e.preventDefault();
    this._fuoco();
  }
}
