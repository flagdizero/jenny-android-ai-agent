/** Le risposte che non hai visto: il pallino su Jenny.
 *
 *  Dal 05/10/2026 Jenny e' la porta della chat, e la chat sta chiusa finche'
 *  non la tocchi. Una risposta che arriva mentre guardi altro — una domanda
 *  lasciata a meta', un promemoria che scatta — non si vedrebbe: il pallino
 *  sulla sua testa dice «c'e' qualcosa per te, toccami».
 *
 *  Si accende per un turno che **ha parlato** (testo vero, non i nomi degli
 *  strumenti) e si e' chiuso mentre la sua chat non era a schermo, e solo per
 *  la conversazione che Jenny aprirebbe da dove sei: sopra le App la
 *  personale, sopra la bacheca quella del quaderno. Un `/stop` o un turno
 *  muto non accendono niente.
 *
 *  Vede solo le conversazioni di cui arrivano i frame: la personale sempre (il
 *  gateway non la stacca mai), un quaderno finche' e' quello della chat. Un
 *  quaderno lasciato a meta' turno non lo dice: la sua risposta la trovi
 *  riaprendolo, perche' la chat rilegge il filo.
 *
 *  Il modulo non sa che guscio lo usa: gli si dice cosa e' a schermo, quale
 *  conversazione conta da qui, e come dipingere.
 */

import { wsManager } from './ws-manager.js';
import { sessionManager } from './session-manager.js';

export class UnseenReplies {
  /** @param onScreen  `(chatId) => boolean`: quella chat e' a schermo adesso?
   *  @param wanted    `() => chatId`: la conversazione che Jenny aprirebbe da qui.
   *  @param paint     `(on) => void`: accende o spegne il pallino. */
  constructor({ onScreen, wanted, paint } = {}) {
    this._onScreen = onScreen || (() => true);
    this._wanted = wanted || (() => sessionManager.currentChatId);
    this._paint = paint || (() => {});
    /** Le chat il cui turno in corso ha gia' detto qualcosa. */
    this._spoke = new Set();
    /** Le chat con una risposta non vista. */
    this._unseen = new Set();
    this._lit = false;
    this._onFrame = (e) => this.ingest(e.detail);
    wsManager.addEventListener('chat:message', this._onFrame);
  }

  /** Un frame dal filo. Uno senza `chat_id` e' della conversazione aperta. */
  ingest(msg) {
    if (!msg || typeof msg !== 'object') return;
    const id = msg.chat_id || sessionManager.currentChatId;
    if (!id) return;
    const speaks = (msg.event === 'delta' && msg.text)
      || (msg.event === 'message' && msg.text && msg.kind !== 'tool_hint' && msg.kind !== 'progress');
    if (speaks) {
      this._spoke.add(id);
      return;
    }
    if (msg.event !== 'turn_end' && msg.event !== 'error') return;
    if (!this._spoke.delete(id)) return;
    if (!this._onScreen(id)) this._unseen.add(id);
    this.refresh();
  }

  /** Ridipinge: la chat che Jenny aprirebbe da qui ha una risposta non vista?
   *  Se e' a schermo, l'ha vista adesso. Da chiamare quando cambia il posto. */
  refresh() {
    const id = this._wanted();
    if (id && this._onScreen(id)) this._unseen.delete(id);
    const lit = Boolean(id) && this._unseen.has(id);
    if (lit === this._lit) return;
    this._lit = lit;
    this._paint(lit);
  }
}
