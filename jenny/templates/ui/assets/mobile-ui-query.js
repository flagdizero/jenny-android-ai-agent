/** UI Query Responder — risponde alle ui_query del server (tool ui_view).
 *
 * Modello pull: Jenny non riceve mai il contesto dello schermo, lo chiede quando
 * le serve. Il server manda un evento `ui_query` sul WebSocket della chat; qui lo
 * raccogliamo, descriviamo la vista attiva come HTML potato (e, se aperta, l'HTML
 * della Jenny app via SDK) e rispondiamo con un frame `ui_result`.
 *
 * L'HTML della vista nativa si legge direttamente dal DOM (`elementoVista(modo)`:
 * cervello, mani e memoria sono tutti e tre `#view-settings`).
 * L'HTML dell'app NO: l'iframe è sandboxato con origin opaca, illeggibile dal
 * parent — è l'app stessa a spedirlo fuori tramite l'SDK (jenny:ui-query).
 */

import { AppState } from './shared/state.js';
import { wsManager } from './shared/ws-manager.js';
import { elementoVista } from './mobile-settings.js';

// Cap per blocco HTML (il backend rifiuta comunque payload oltre 256 KB).
const HTML_CAP = 48 * 1024;
// Elementi rimossi dalla potatura: rumore inutile al modello o troppo pesanti.
const STRIP_SELECTOR = 'script, style, link, svg, template, noscript';

export class UiQueryResponder {
  constructor() {
    // Stream non filtrato per `chat_id` di proposito: una `ui_query` è una
    // richiesta mirata a *questa* connessione, correlata da `correlation_id`, e
    // la risposta descrive lo schermo — non il thread di una conversazione.
    wsManager.addEventListener('chat:message', (e) => {
      if (e.detail?.event === 'ui_query') this._respond(e.detail);
    });
  }

  async _respond(msg) {
    const id = msg.correlation_id;
    if (!id) return;
    try {
      const payload = await this._collect();
      wsManager.sendUiResult(id, payload);
    } catch (err) {
      console.error('ui_query collect failed:', err);
      wsManager.sendUiResult(id, null, 'collect_failed');
    }
  }

  /* Pota una stringa HTML: rimuove script/style/svg ecc. e commenti, tronca gli
     attributi lunghi (src/href, data-URI base64), comprime lo spazio, cappa. */
  _pruneHtml(htmlString) {
    if (!htmlString) return '';
    let out;
    try {
      const doc = new DOMParser().parseFromString(htmlString, 'text/html');
      doc.querySelectorAll(STRIP_SELECTOR).forEach((el) => el.remove());
      // Rimuovi i nodi commento.
      const walker = doc.createTreeWalker(doc, NodeFilter.SHOW_COMMENT);
      const comments = [];
      while (walker.nextNode()) comments.push(walker.currentNode);
      comments.forEach((c) => c.remove());
      // Tronca attributi pesanti (immagini inline, URL lunghi).
      doc.querySelectorAll('[src], [href]').forEach((el) => {
        for (const attr of ['src', 'href']) {
          const v = el.getAttribute(attr);
          if (v && (v.length > 128 || v.startsWith('data:'))) {
            el.setAttribute(attr, '[stripped]');
          }
        }
      });
      out = doc.body ? doc.body.innerHTML : doc.documentElement.outerHTML;
    } catch {
      out = htmlString;
    }
    out = out.replace(/[ \t]+/g, ' ').replace(/\n\s*\n\s*\n+/g, '\n\n').trim();
    if (out.length > HTML_CAP) out = out.slice(0, HTML_CAP) + '\n<!--[truncated]-->';
    return out;
  }

  async _collect() {
    const view = AppState.currentMode || 'unknown';
    const drawer = window.mobileApp?.drawer?.activeDrawer || null;
    /* Dalla tabella, non dall'id costruito: `view-cervello` non esiste, e
       per i tre cassetti Jenny riceveva un HTML vuoto. */
    const container = elementoVista(view);
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
