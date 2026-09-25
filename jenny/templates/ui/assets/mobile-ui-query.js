/** UI Query Responder — risponde alle ui_query del server (tool ui_view).
 *
 * Modello pull: Jenny non riceve mai il contesto dello schermo, lo chiede quando
 * le serve. Il server manda un evento `ui_query` sul WebSocket della chat; qui lo
 * raccogliamo, descriviamo la vista attiva come HTML potato (e, se aperta, l'HTML
 * della Jenny app via SDK) e rispondiamo con un frame `ui_result`.
 *
 * L'HTML della vista nativa si legge direttamente dal DOM (`viewElement(mode)`:
 * cervello, mani e memoria sono tutti e tre `#view-settings`).
 * L'HTML dell'app NO: l'iframe è sandboxato con origin opaca, illeggibile dal
 * parent — è l'app stessa a spedirlo fuori tramite l'SDK (jenny:ui-query).
 */

import { AppState } from './shared/state.js';
import { UiQueryResponder as ShellResponder } from './shared/ui-query.js';
import { viewElement } from './mobile-settings.js';

/* Il giro del filo, la potatura e il cap stanno in `shared/ui-query.js`, che la
   casa condivide dal 26/09/2026; qui resta cosa descrive l'officina. */
export class UiQueryResponder extends ShellResponder {
  async _collect() {
    const view = AppState.currentMode || 'unknown';
    const drawer = window.mobileApp?.drawer?.activeDrawer || null;
    /* Dalla tabella, non dall'id costruito: `view-cervello` non esiste, e
       per i tre cassetti Jenny riceveva un HTML vuoto. */
    const container = viewElement(view);
    const html = this._pruneHtml(container ? container.outerHTML : '');

    const payload = { view, drawer, html };

    // Jenny app aperta: chiedile il suo DOM via SDK (il parent non può leggerlo).
    /* La mini-app sta nel velo sopra qualunque vista, e il suo stato vive
       nelle azioni delle app: qui si leggeva `controllers.apps` con
       `view === 'apps'`, cioe' la scheda «App» che non esiste piu' — e l'app
       aperta non arrivava mai a Jenny. */
    const actions = window.mobileApp?._appsActions;
    const open = actions?._openApp;
    if (open) {
      const meta = window.mobileApp?._appsSource?.jennyApps?.find((a) => a.slug === open.slug);
      let appHtml = null;
      try {
        appHtml = await actions.requestAppHtml(2000);
      } catch {
        appHtml = null;
      }
      payload.app = {
        slug: open.slug,
        name: meta?.name || open.slug,
        responded: !!appHtml,
        html: appHtml ? this._pruneHtml(appHtml) : '',
      };
    }

    return payload;
  }
}
