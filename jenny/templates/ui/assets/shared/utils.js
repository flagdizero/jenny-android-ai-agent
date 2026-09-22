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
