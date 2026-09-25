/** Dove puo' portare un link scritto dentro un contenuto — una risposta di
 *  Jenny, una pagina del quaderno — e dove invece non deve.
 *
 *  **La regola e' una sola ed e' dei due gusci.** Stava in `mobile-chat.js`
 *  (`_handleContentLink`) e l'officina era l'unica a rispettarla: la chat della
 *  casa scriveva il markdown in `innerHTML` senza guardare i link, e il lettore
 *  delle pagine mandava fuori ogni `http(s)://` senza guardare l'origine.
 *
 *  Il perche' e' nel guscio nativo. `MainActivity.isShellDocument` lascia
 *  dentro la WebView le navigazioni di main frame verso i documenti-guscio
 *  (`/html-mobile/`, `index.html`, `workshop.html`, con qualunque query): un
 *  `[x](workshop.html)` o un `[x](?mode=chat)` ricaricano quindi la SPA
 *  **senza** il fragment `#bs=`, che si legge una volta sola all'avvio —
 *  `/webui/bootstrap` risponde 401, API e websocket restano morti finche' l'app
 *  non viene uccisa. Su un telefono in cui questa app e' il launcher.
 *
 *  Tre esiti, e in nessuno il main frame naviga:
 *  - ancora interna (`#id`) → `{kind:'hash', id}`: la pagina scorre, non cambia;
 *  - http/https verso un'**altra** origine, `mailto:`, `tel:` →
 *    `{kind:'external', href}`: si apre fuori dalla WebView;
 *  - tutto il resto — relativi (risolvono sull'origine del gateway), stessa
 *    origine, schemi non navigabili, href vuoti o illeggibili → `null`: il link
 *    e' inerte, e chi chiama lo deve **dire** (`common.linkNotOpenable`).
 *
 *  Nessuna dipendenza, di proposito: e' il pezzo che i banchi node importano
 *  vero, e un import in piu' qui sarebbe un banco in meno.
 */

/**
 * @param {string} href  l'attributo com'e' scritto, non `a.href` gia' risolto
 * @param {{href: string, origin: string}} [here]  la pagina corrente
 * @returns {{kind:'hash', id:string} | {kind:'external', href:string} | null}
 */
export function contentLinkTarget(href, here = globalThis.location) {
  const raw = String(href || '');
  if (!raw) return null;
  if (raw.startsWith('#')) return { kind: 'hash', id: raw.slice(1) };
  let url = null;
  try {
    url = here?.href ? new URL(raw, here.href) : new URL(raw);
  } catch (_) {
    return null;
  }
  const scheme = url.protocol;
  const isWeb = scheme === 'http:' || scheme === 'https:';
  /* Senza un'origine con cui confrontarsi non si sa se il link porta fuori o
     al gateway: nel dubbio resta inerte. */
  const origin = here?.origin;
  if (isWeb && origin && url.origin !== origin) return { kind: 'external', href: url.href };
  if (scheme === 'mailto:' || scheme === 'tel:') return { kind: 'external', href: url.href };
  return null;
}

/** Apre un URL fuori dalla WebView. Falso se non ci e' riuscito.
 *
 *  `window.open` qui non apre una finestra: la WebView non supporta le finestre
 *  multiple, quindi la richiesta ricade su `shouldOverrideUrlLoading`, che per
 *  un'origine non-gateway apre una Chrome Custom Tab
 *  (`MainActivity#openExternalUrl`) e lascia la SPA dov'e'. Si chiama **solo**
 *  con un `href` che `contentLinkTarget` ha dato per esterno: con uno della
 *  stessa origine, questa riga sarebbe la navigazione che si vuole evitare.
 *  Il bridge `JennyNative` oggi non espone un metodo per gli URL esterni: se ne
 *  verra' aggiunto uno, va provato qui per primo.
 */
export function openOutsideWebView(href) {
  try {
    window.open(href, '_blank', 'noopener');
    return true;
  } catch (err) {
    console.warn('Could not open external link:', err);
    return false;
  }
}
