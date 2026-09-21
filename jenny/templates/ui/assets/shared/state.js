/** Shared Application State — global reactive state. */

export const AppState = {
  // Current view/mode
  currentMode: 'chat',

  // Theme (the boot script in index.html migrates legacy values first)
  theme: localStorage.getItem('tc-theme') || 'chanel',

  // Se il prossimo messaggio parte in sola lettura. Lo scrive soltanto
  // `write-switch.js`; lo leggono il placeholder del composer e `ws-manager`,
  // che lo mette nell'envelope di ogni invio.
  readonlyTurn: false,

  // Quale tendina della riga sopra il composer è aperta: `'scope'`,
  // `'commands'`, o `null`. Ce n'è **una sola**, e questo campo è il modo in cui
  // se ne accorgono a vicenda.
  //
  // Un canale condiviso serve perché la strada ovvia non funziona: ogni chip fa
  // `stopPropagation()` sul proprio click — deve, o il listener su `document`
  // che chiude la tendina quando si tocca altrove la richiuderebbe nello stesso
  // gesto che l'ha aperta — e quel `stopPropagation` impedisce all'altro chip di
  // vedere il click. Misurato sul telefono il 28/08: aprendo i comandi con lo
  // scope già aperto restavano aperti tutti e due, uno sopra l'altro.
  composeMenu: null,

  // State change listeners
  _listeners: new Map(),

  on(key, callback) {
    if (!this._listeners.has(key)) this._listeners.set(key, []);
    this._listeners.get(key).push(callback);
  },

  set(key, value) {
    const oldValue = this[key];
    this[key] = value;
    if (this._listeners.has(key)) {
      this._listeners.get(key).forEach(cb => cb(value, oldValue));
    }
  }
};

/** Dichiara che la tendina *id* si è aperta. Le altre si chiudono. */
export function claimComposeMenu(id) {
  AppState.set('composeMenu', id);
}

/** Chiude *close()* quando si apre una tendina che non è *id*.
 *
 *  **Solo l'apertura pubblica.** Se anche la chiusura scrivesse il campo, il
 *  `close()` che questo gancio provoca ne scriverebbe un altro, e due tendine si
 *  richiamerebbero a vicenda: chi arriva dopo chiude chi è appena stato aperto.
 *  Un campo che dice "chi è aperto" non ha bisogno di sapere chi si è chiuso —
 *  lo si scopre alla prossima apertura.
 */
export function onOtherComposeMenu(id, close) {
  AppState.on('composeMenu', (who) => {
    if (who !== id) close();
  });
}
