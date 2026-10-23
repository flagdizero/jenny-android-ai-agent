/** Shared Application State — global reactive state. */

/* Il tema salvato, letto **al caricamento del modulo**, prima di tutto il
   resto: un `localStorage` che solleva (dati del sito bloccati, anteprima)
   qui si portava via l'intero grafo degli import, cioe' la pagina.
   La stessa regola di `readStorage` in `utils.js`, scritta
   qui perche' questo modulo non importa niente, e i banchi lo sanno. */
function savedTheme() {
  try {
    return globalThis.localStorage.getItem('tc-theme');
  } catch (_) {
    return null;
  }
}

export const AppState = {
  // Current view/mode
  currentMode: 'chat',

  // Theme (the boot script in index.html migrates legacy values first)
  theme: savedTheme() || 'synthwave',

  // Se il prossimo messaggio parte in sola lettura. Lo scrive soltanto
  // `write-switch.js`; lo leggono il placeholder del composer e `ws-manager`,
  // che lo mette nell'envelope di ogni invio.
  readonlyTurn: false,

  // Il tasto Piano nel composer di un quaderno (`home-plan.js`): se il prossimo
  // messaggio chiede un piano, e se rivede quello con questo id. A differenza
  // di `readonlyTurn`, `ws-manager` non li legge: valgono solo per il messaggio
  // del composer, che li passa esplicito (`HomePlan.composerExtra`), e il tasto
  // si spegne appena quel messaggio parte.
  planTurn: false,
  planRevise: null,

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
function onOtherComposeMenu(id, close) {
  AppState.on('composeMenu', (who) => {
    if (who !== id) close();
  });
}

/* Le tendine agganciate, per chi deve sapere se ce n'è una aperta: il tasto
   Indietro del guscio (`_overlayLayers` in `mobile-app.js`). */
const composeMenus = new Set();

/** Vero se una tendina del composer è aperta. */
export function composeMenuOpen() {
  for (const chip of composeMenus) if (chip._open) return true;
  return false;
}

/** Chiude le tendine aperte; vero se ne ha chiusa almeno una. */
export function closeComposeMenus() {
  let closed = false;
  for (const chip of composeMenus) {
    if (!chip._open) continue;
    chip.close();
    closed = true;
  }
  return closed;
}

/** Aggancia una tendina della riga del composer: il chip la apre e la chiude,
 *  un tocco fuori la chiude, e l'apertura di un'altra la chiude.
 *
 *  *chip* porta `el`, `menu`, `_open`, `toggle()` e `close()`. Il
 *  `stopPropagation()` sul click del chip è necessario — senza, il click che
 *  apre arriverebbe a `document` e la richiuderebbe nello stesso gesto — ed è
 *  anche il motivo per cui l'altro chip non vede mai quel click: per questo c'è
 *  `onOtherComposeMenu`. I listener su `document` sono chiusure anonime e non
 *  si smontano: chi chiama si tiene il suo latch di `init`.
 *
 *  **Escape e Indietro non sono qui**. C'era un
 *  `keydown` suo che chiudeva la tendina su Escape; ma Escape è anche la
 *  scorciatoia del tasto Indietro del guscio, e la stessa pressione chiudeva la
 *  tendina **e** tornava alla schermata di prima. Il tasto Indietro di Android,
 *  al contrario, la scavalcava. Ora la tendina è un livello della catena di
 *  Indietro (`composeMenuOpen`/`closeComposeMenus`): una pressione, un passo.
 */
export function armComposeMenu(chip, id) {
  composeMenus.add(chip);
  chip.el.addEventListener('click', (e) => {
    e.stopPropagation();
    chip.toggle();
  });
  chip.menu.addEventListener('click', (e) => e.stopPropagation());
  document.addEventListener('click', () => chip.close());
  onOtherComposeMenu(id, () => chip.close());
}
