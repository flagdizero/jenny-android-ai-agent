/** Le risposte alle `ui_query` del server (tool `ui_view`): la parte che i due
 *  gusci hanno in comune.
 *
 *  Modello pull: Jenny non riceve mai il contesto dello schermo, lo chiede
 *  quando le serve. Il gateway lega ogni messaggio alla connessione che l'ha
 *  mandato (`metadata["conn_id"]` in `jenny/channels/websocket.py`) e il tool
 *  interroga **quella**: un guscio che non risponde fa aspettare Jenny sei
 *  secondi, e poi le fa dire che l'app e' in background. Fino al 26/09/2026
 *  rispondeva solo l'officina, e la casa — il guscio di default — no.
 *
 *  Qui c'e' il giro del filo (domanda → raccolta → `ui_result`), la potatura
 *  dell'HTML e la domanda a una cornice di Jenny App. **Cosa** descrivere lo
 *  decide ogni guscio nel suo `_collect`: `mobile-ui-query.js` e
 *  `home-ui-query.js`. La forma del risultato e' quella che legge
 *  `jenny/agent/tools/ui_view.py`: `{view, drawer, html, app?}`.
 */

import { wsManager } from './ws-manager.js';

// Cap per blocco HTML (il backend rifiuta comunque payload oltre 256 KB).
export const HTML_CAP = 48 * 1024;
// Elementi rimossi dalla potatura: rumore inutile al modello o troppo pesanti.
const STRIP_SELECTOR = 'script, style, link, svg, template, noscript';

/* Pota una stringa HTML: rimuove script/style/svg ecc. e commenti, tronca gli
   attributi lunghi (src/href, data-URI base64), comprime lo spazio, cappa. */
export function pruneHtml(htmlString) {
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

let frameSeq = 0;

/** Chiede il DOM a una cornice di Jenny App e ne aspetta la risposta.
 *
 *  L'iframe e' sandboxato con origine opaca, illeggibile da qui: e' l'app a
 *  spedirlo fuori tramite l'SDK (`jenny:ui-query` → `jenny:ui-result`, v.
 *  `apps/jenny-sdk.js`). Serve alla pagina di un'app appesa nella casa, che
 *  non e' la mini-app sopra tutto di `AppsActions.requestAppHtml`: quella
 *  ascolta solo la propria cornice.
 *
 *  Si accetta solo la risposta di **quella** finestra e con **quel** nonce;
 *  `null` se non arriva in tempo.
 */
export function requestFrameHtml(win, timeoutMs = 2000) {
  if (!win || typeof win.postMessage !== 'function') return Promise.resolve(null);
  const nonce = `ui-query-frame-${++frameSeq}`;
  return new Promise((resolve) => {
    let timer = null;
    const onMessage = (e) => {
      if (e.source !== win) return;
      const msg = e.data;
      if (!msg || msg.type !== 'jenny:ui-result' || msg.nonce !== nonce) return;
      done(String(msg.html || ''));
    };
    const done = (value) => {
      clearTimeout(timer);
      window.removeEventListener('message', onMessage);
      resolve(value);
    };
    timer = setTimeout(() => done(null), timeoutMs);
    window.addEventListener('message', onMessage);
    try {
      win.postMessage({ type: 'jenny:ui-query', nonce }, '*');
    } catch {
      done(null);
    }
  });
}

/** Il giro del filo. Ogni guscio lo estende con il suo `_collect()`. */
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

  _pruneHtml(htmlString) {
    return pruneHtml(htmlString);
  }

  /** `{view, drawer, html, app?}`: lo scrive il guscio. */
  async _collect() {
    throw new Error('_collect() is shell-specific');
  }
}
