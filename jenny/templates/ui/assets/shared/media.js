/** Gli allegati di un messaggio: di che tipo sono, e come si apre un file.
 *
 *  Casa e officina avevano ciascuna la sua regola per il tipo, e non erano la
 *  stessa: la casa guardava anche l'URL e contava `bmp` e `m4v`, l'officina
 *  solo il nome. Il tipo lo dice il server (`entry.kind`); quando manca — un
 *  messaggio registrato prima — vale la regola del server
 *  (`jenny/webui/transcript_markdown.py::media_kind_from_name`), sul nome o,
 *  senza nome, sul percorso dell'URL.
 */

import { i18n } from './i18n.js';
import { showToast } from './utils.js';

/* Le stesse estensioni di `INLINE_MARKDOWN_IMAGE_EXTS` / `_VIDEO_EXTS`. */
const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'svg']);
const VIDEO_EXTS = new Set(['mp4', 'mov', 'webm']);

/** `'image'`, `'video'` o `'file'`. */
export function mediaKind(entry) {
  if (entry?.kind) return entry.kind;
  const source = entry?.name || String(entry?.url || '').split(/[?#]/)[0];
  const ext = (String(source).match(/\.([a-z0-9]+)$/i) || [])[1]?.toLowerCase() || '';
  if (IMAGE_EXTS.has(ext)) return 'image';
  if (VIDEO_EXTS.has(ext)) return 'video';
  return 'file';
}

/** Apre un allegato che il filo non sa mostrare: sul telefono col ponte nativo
 *  (ACTION_VIEW, il visore di sistema), fuori dalla WebView l'URL firmato in
 *  una scheda nuova.
 *
 *  `window.open` non e' il ripiego del ponte che fallisce: sotto Android la
 *  WebView non ha un `DownloadListener` e blocca le schede nuove, quindi li'
 *  il tocco non produrrebbe niente — nessun visore, nessun errore. Con il
 *  ponte presente il fallimento e' un errore da dire. */
export async function openMediaFile(entry) {
  const bridge = window.JennyNative;
  if (bridge && typeof bridge.openFile === 'function') {
    try {
      // Asincrono: `openFile` sta sulla porta che solo la SPA raggiunge (v.
      // `native-bridge.js`) e risponde con una Promise.
      if (entry.path && await bridge.openFile(entry.path)) return;
    } catch (e) {
      console.warn('Native openFile failed:', e);
    }
    showToast(i18n.t('chat.couldNotOpen', { path: entry.name || entry.path || '' }), 'error');
    return;
  }
  if (entry.url) window.open(entry.url, '_blank');
}
