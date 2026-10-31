/** Jenny companion — la mascotte che vive sul bordo di ogni vista dell'officina.
 *
 * A riposo sporge dal bordo destro, e ci torna anche quando la si lancia
 * altrove: il lato non si sceglie più (le sue chiavi sono fra quelle ritirate
 * in `shared/mascot.js`), e l'arrivo del volo lo decide `settle()` in
 * `shared/mascot-drag.js`.
 *
 * **È la porta della chat** (dal 05/10/2026, come in casa): fuori dalla
 * Console, toccata o richiamata con uno swipe, porta su la chat a tutto
 * schermo sopra la vista che stai guardando — Cervello, Mani, Memoria, un file
 * — e lei te la spiega (v. `MobileApp.openChatOver`). Ritoccata, la chat si
 * chiude. Nella Console saluta. Fino ad allora apriva la minichat, una domanda
 * a un turno in un fumetto.
 *
 * **Jenny in sé non è qui**: arte, stati, parlato, umore, lettura dei frame e
 * la porta stanno in `shared/jenny-mascot.js`, e sono gli stessi della casa.
 * Qui resta quel che l'officina ha di suo: dove sta la chat, e dove sta lei.
 */

import { AppState } from './shared/state.js';
import { i18n } from './shared/i18n.js';
import { sessionManager } from './shared/session-manager.js';
import { JennyMascot } from './shared/jenny-mascot.js';
import { UnseenReplies } from './shared/unseen-replies.js';

/* Il guscio: nasce dopo la mascotte (che sta nel suo costruttore), quindi si
   chiede a ogni domanda, non una volta sola. */
const app = () => window.mobileApp;

export class JennyCompanion extends JennyMascot {
  constructor() {
    super(document.getElementById('app'), {
      mode: AppState.currentMode || 'chat',
      chat: {
        isOpen: () => app()?._chatOver === true,
        onScreen: () => AppState.currentMode === 'chat' && app()?._chatOver !== true,
        open: () => app()?.openChatOver(),
        close: () => app()?.closeChatOver(),
      },
    });
    /* Il pallino: una risposta arrivata mentre la chat non era a schermo. */
    this.unseen = new UnseenReplies({
      onScreen: (id) => id === sessionManager.currentChatId
        && (AppState.currentMode === 'chat' || AppState.chatOver === true),
      wanted: () => sessionManager.currentChatId,
      paint: (on) => this.setBadge(on, i18n.t('jenny.replyWaiting')),
    });
    AppState.on('currentMode', () => this._place(true));
    AppState.on('chatOver', () => this._place(true));
  }

  _enterInitialState() {
    this._place(false);
  }

  /* Dove sta: fuori all'angolo quando la chat e' a schermo — la Console, o la
     chat aperta sopra una vista — al bordo altrove. */
  _place(moved) {
    if (moved && this._abortFlight) this._abortFlight();
    const shown = AppState.currentMode === 'chat' || AppState.chatOver === true;
    this.setChatOnScreen(shown);
    this.el.classList.toggle('out', shown);
    this.setExpanded(AppState.chatOver === true);
    this._syncArt();
    this._updateGestureExclusion();
    this.unseen?.refresh();
  }
}
