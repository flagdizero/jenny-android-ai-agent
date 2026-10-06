/** Shared Utilities — pure helper functions. */

export function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

export function getFileExtension(filename) {
  return filename.split('.').pop().toLowerCase();
}

/* Aggancia il toast dove sara VISIBILE, non semplicemente dove sta comodo.

   Un <dialog> aperto con showModal() vive nel "top layer": uno strato che sta
   sopra l'intero contesto di impilamento della pagina, indipendentemente dagli
   z-index. Un toast appeso al <body> quindi finisce SOTTO qualunque modale
   aperta, e alzargli lo z-index non serve a niente — non e una gara che si
   possa vincere con un numero piu grande.

   Le uniche due strade sono entrare nel top layer (Popover API) o entrare
   nella modale stessa. Si prova la prima, che non ha effetti collaterali; dove
   manca (WebView vecchia) si ripiega sulla seconda, che funziona ovunque ma
   lega la vita del toast a quella della modale che lo ospita. */
function _mountToast(toast) {
  if (typeof toast.showPopover === 'function') {
    // `manual`: niente chiusura automatica al click fuori o con Esc, che su un
    // toast sarebbe un modo di farlo sparire mentre lo si sta leggendo.
    toast.setAttribute('popover', 'manual');
    document.body.appendChild(toast);
    try {
      toast.showPopover();
      return;
    } catch (_) {
      toast.removeAttribute('popover');
    }
  }
  const openDialogs = document.querySelectorAll('dialog[open]');
  const host = openDialogs.length ? openDialogs[openDialogs.length - 1] : document.body;
  host.appendChild(toast);
}

export function showToast(message, type = 'info', duration = 3000) {
  const toast = document.createElement('div');
  toast.className = `mobile-toast ${type}`;
  toast.textContent = message;
  _mountToast(toast);

  setTimeout(() => {
    toast.style.animation = 'toastOut 0.3s ease forwards';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

/* Un toast con «Annulla»: al posto di una domanda di conferma prima di togliere
   qualcosa. Si fa, e per qualche secondo si puo' disfare — un tocco solo nel
   caso comune, uno in piu' nel caso raro. `onUndo` si chiama al massimo una
   volta; il toast se ne va da solo dopo `duration` ms, o subito dopo il tocco.
   Torna una funzione che lo chiude prima (per chi cambia stanza). */
export function showUndo(message, onUndo, { duration = 5000, label } = {}) {
  const toast = document.createElement('div');
  toast.className = 'mobile-toast info has-action';
  toast.setAttribute('role', 'status');
  const text = document.createElement('span');
  text.className = 'mobile-toast-text';
  text.textContent = message;
  const undo = document.createElement('button');
  undo.type = 'button';
  undo.className = 'mobile-toast-action';
  undo.textContent = label || 'Undo';
  let done = false;
  const close = () => {
    if (!toast.isConnected) return;
    toast.style.animation = 'toastOut 0.3s ease forwards';
    setTimeout(() => toast.remove(), 300);
  };
  undo.addEventListener('click', () => {
    if (done) return;
    done = true;
    close();
    onUndo?.();
  });
  toast.append(text, undo);
  _mountToast(toast);
  setTimeout(close, duration);
  return () => { done = true; close(); };
}

/* Carica su richiesta un vendor pesante che serve a una sola vista.
   Mermaid da solo pesa 3,2 MB e index.html lo caricava a ogni avvio anche a
   chi apriva solo la chat. La Promise è memoizzata (non il risultato), così
   due chiamate ravvicinate condividono lo stesso <script> invece di
   iniettarne due. Same-origin: passa sotto la CSP `script-src 'self'`. */
const _vendorLoads = new Map();

export function ensureVendor(src) {
  const cached = _vendorLoads.get(src);
  if (cached) return cached;
  const p = new Promise((resolve, reject) => {
    const el = document.createElement('script');
    el.src = src;
    el.onload = () => resolve();
    el.onerror = () => {
      // Non lasciare in cache un fallimento: un ritentativo (rete assente al
      // primo colpo, asset non ancora estratto) deve poter riprovare.
      _vendorLoads.delete(src);
      reject(new Error(`Failed to load ${src}`));
    };
    document.head.appendChild(el);
  });
  _vendorLoads.set(src, p);
  return p;
}

/** Come `ensureVendor`, ma per un foglio di stile.
 *
 *  Serve perche' una libreria puo' non essere fatta di solo codice: KaTeX
 *  disegna con i suoi font e la sua spaziatura, e senza il CSS le formule
 *  escono come lettere sparse — peggio del `$f(x)$` grezzo da cui si parte.
 *
 *  Stesso patto di `ensureVendor`: una promessa per URL, e il fallimento **non
 *  si ricorda**, cosi' un secondo tentativo puo' riuscire (rete assente al
 *  primo colpo, asset non ancora estratto dall'APK).
 */
export function ensureVendorStyle(href) {
  const cached = _vendorLoads.get(href);
  if (cached) return cached;
  const p = new Promise((resolve, reject) => {
    const el = document.createElement('link');
    el.rel = 'stylesheet';
    el.href = href;
    el.onload = () => resolve();
    el.onerror = () => {
      _vendorLoads.delete(href);
      reject(new Error(`Failed to load ${href}`));
    };
    document.head.appendChild(el);
  });
  _vendorLoads.set(href, p);
  return p;
}


/**
 * Copia *text* negli appunti. Ritorna true se ha funzionato.
 *
 * Il fallback non è prudenza generica: la Clipboard API può mancare in una
 * WebView, e la WebView Android è l'unico runtime che spediamo — quindi il
 * ramo `execCommand` è la strada normale su una fetta dei dispositivi, non
 * un caso limite. Esisteva già, in `mobile-settings.js`, per la chiave SSH;
 * il pulsante Copia dei blocchi di codice chiamava invece `writeText` nudo,
 * senza controllo e senza `.catch`, quindi lì non copiava niente **in
 * silenzio** e lasciava una promise rifiutata.
 */
export async function copyToClipboard(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
    const area = document.createElement('textarea');
    area.value = text;
    area.style.position = 'fixed';
    area.style.left = '-9999px';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok;
  } catch {
    return false;
  }
}

/** Vero se *reason* e' un fallimento della rete, e non un errore qualunque.
 *
 *  `fetch` rifiuta con un `TypeError` quando la richiesta non parte o non
 *  torna: «Failed to fetch» in Chromium (la WebView), «NetworkError when
 *  attempting to fetch resource» in Firefox, «Load failed» in Safari. Tutto il
 *  resto — un `TypeError` del codice, un `Error('Cron failed: 500')` — non e'
 *  la rete, e dire «errore di rete» a chi ha il Wi-Fi acceso lo manda a
 *  cercare il guasto nel posto sbagliato. */
export function isNetworkFailure(reason) {
  if (!reason || reason.name !== 'TypeError') return reason?.name === 'NetworkError';
  return /failed to fetch|networkerror|load failed|network request failed/i.test(
    String(reason.message || ''),
  );
}

/* ── `localStorage`, senza che un accesso negato butti giu' chi chiama ─────
   Nella WebView con i dati del sito bloccati, in un'anteprima o con la quota
   piena, `localStorage` **solleva** — gia' leggendo la proprieta', non solo
   scrivendo. Chiamato nudo dal caricamento di un modulo (`state.js` leggeva il
   tema cosi') l'errore si portava via l'intero grafo degli import, cioe' la
   pagina. Qui si legge `null` e si scrive
   niente: sono tutte preferenze, e senza si riparte dai default. */

export function readStorage(key) {
  try {
    return globalThis.localStorage.getItem(key);
  } catch (_) {
    return null;
  }
}

/** Vero se il valore e' stato scritto. */
export function writeStorage(key, value) {
  try {
    globalThis.localStorage.setItem(key, value);
    return true;
  } catch (_) {
    return false;
  }
}

export function removeStorage(key) {
  try {
    globalThis.localStorage.removeItem(key);
  } catch (_) {
    /* storage non disponibile */
  }
}
