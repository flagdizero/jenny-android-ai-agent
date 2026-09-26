/** La casa — cosa mostra, quando Jenny lo chiede (tool `ui_view`).
 *
 *  Il gateway interroga la connessione **da cui e' partito il messaggio**:
 *  scrivendo dalla casa, e' la casa. Fino al 26/09/2026 qui non rispondeva
 *  nessuno, e ogni «cosa vedi?» finiva dopo sei secondi con Jenny convinta
 *  che l'app fosse in background.
 *
 *  Cosa si descrive, dal piu' specifico:
 *  - una **stanza** aperta sopra la pista (le pagine del quaderno, il lettore,
 *    le stanze delle impostazioni): `view` e' il suo nome, l'HTML e' il suo;
 *  - altrimenti la **pagina** della pista a schermo — la chat, un quaderno, il
 *    cassetto delle app, i Quaderni, le Impostazioni, un'app appesa: `view` e'
 *    il suo id, l'HTML il suo pannello. I Quaderni con un quaderno aperto
 *    dicono `notebook`: li' dentro c'e' la chat di quel quaderno.
 *  Il prefisso `home:` dice a Jenny in che guscio e' l'utente: le stesse
 *  parole («chat», «settings») nell'officina sono un'altra schermata.
 *
 *  Un'app, se c'e', arriva in `app` come in officina: la mini-app aperta sopra
 *  tutto (dal cassetto), oppure l'app della pagina appesa a schermo. Il DOM
 *  lo manda lei via SDK; il guscio lo pota.
 */

import { UiQueryResponder, requestFrameHtml } from './shared/ui-query.js';

/* Le stanze della casa e il loro elemento. Sono le chiavi di `BACK_TO` in
   `home-app.js`; una stanza nuova senza riga qui manda a Jenny un HTML vuoto,
   non un errore. */
const ROOM_ELEMENTS = {
  pages: 'home-notebook-pages',
  reader: 'home-reader',
  jenny: 'home-jenny-room',
  model: 'home-model-room',
  updates: 'home-updates-room',
  backup: 'home-backup-room',
};

/* Quanto aspettare il DOM di un'app: lo stesso margine dell'officina, dentro i
   sei secondi del tool. */
const APP_HTML_TIMEOUT_MS = 2000;

export class HomeUiQuery extends UiQueryResponder {
  /** @param app  il guscio della casa (`HomeApp`). */
  constructor(app) {
    super();
    this.app = app;
  }

  async _collect() {
    const app = this.app;
    const room = app?.view || 'chat';
    const pages = app?.homePages;
    const index = pages?.index;
    const entry = pages?.entry?.(index) || null;

    let name;
    let container;
    if (room !== 'chat') {
      name = room;
      container = document.getElementById(ROOM_ELEMENTS[room] || '');
    } else {
      name = entry?.id || 'chat';
      /* I Quaderni con un quaderno aperto sono una chat, non l'elenco: dirlo
         «notebooks» farebbe credere a Jenny di guardare la lista. */
      if (entry?.kind === 'notebooks' && pages?.notebooksConversation) name = 'notebook';
      container = pages?.panelOf?.(index) || null;
    }
    const payload = {
      view: `home:${name}`,
      drawer: null,
      html: this._pruneHtml(container ? container.outerHTML : ''),
    };

    const shown = await this._openApp(room, entry);
    if (shown) payload.app = shown;
    return payload;
  }

  /* L'app a schermo, se ce n'e' una. La mini-app sopra tutto vince: sta sopra
     la pista, quindi e' lei che l'utente sta guardando. */
  async _openApp(room, entry) {
    const app = this.app;
    const actions = app?._appActions;
    const open = actions?._openApp;
    let slug = null;
    let html = null;
    if (open) {
      slug = open.slug;
      try {
        html = await actions.requestAppHtml(APP_HTML_TIMEOUT_MS);
      } catch {
        html = null;
      }
    } else if (room === 'chat' && entry?.kind === 'app') {
      slug = entry.ref;
      html = await requestFrameHtml(app.homePages?._pageWindow?.(), APP_HTML_TIMEOUT_MS);
    }
    if (!slug) return null;
    return {
      slug,
      name: app?.appName?.(slug) || slug,
      responded: !!html,
      html: html ? this._pruneHtml(html) : '',
    };
  }
}
