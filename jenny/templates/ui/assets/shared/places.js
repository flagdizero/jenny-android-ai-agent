/** Le schede dei posti nelle due chat: `shared/place-card.js` col resto
 *  dell'app attaccato — l'ultima posizione del telefono, le tessere della mappa,
 *  le traduzioni, il tocco che apre Maps.
 *
 *  Sta separato da `place-card.js` perche' quello non importa niente ed e' il
 *  modulo vero che gira nel banco node; qui c'e' tutto cio' che vuole il guscio.
 */

import { api } from './api-client.js';
import { i18n, tr } from './i18n.js';
import { openOutsideWebView } from './content-link.js';
import { placeTarget, renderPlaces } from './place-card.js';

/* L'ultima posizione, una lettura al minuto al massimo: una storia di cinquanta
   messaggi con tre posti non fa tre richieste, e un fix di un minuto fa per
   «1,2 km da te» va benissimo. Un «non c'e'» non si ricorda: appena aperta
   l'app la cache del gateway e' vuota, e il posto mandato subito dopo restava
   senza distanza per un minuto (visto su jenny_s22). */
const FIX_TTL_MS = 60_000;
let fixAt = 0;
let fixPromise = null;

function lastFix() {
  const now = Date.now();
  if (!fixPromise || now - fixAt > FIX_TTL_MS) {
    fixAt = now;
    const asked = api.getLastLocation().catch(() => null);
    fixPromise = asked;
    asked.then((fix) => { if (!fix && fixPromise === asked) fixPromise = null; });
  }
  return fixPromise;
}

/* Le tessere come `blob:` (la rotta vuole il segreto, che un `<img src>` non
   porterebbe), tenute finche' la pagina vive: le stesse quattro tornano a ogni
   rilettura della storia. Con un tetto, perche' un `blob:` e' memoria. */
const TILE_CACHE_MAX = 160;
const tiles = new Map();

function tileUrl(z, x, y) {
  const key = `${z}/${x}/${y}`;
  let url = tiles.get(key);
  if (url) {
    tiles.delete(key);
    tiles.set(key, url);
    return url;
  }
  url = api.getMapTileUrl(z, x, y).catch(() => {
    tiles.delete(key);
    return '';
  });
  tiles.set(key, url);
  while (tiles.size > TILE_CACHE_MAX) {
    const [oldKey, oldUrl] = tiles.entries().next().value;
    tiles.delete(oldKey);
    oldUrl.then((u) => { if (u) URL.revokeObjectURL?.(u); });
  }
  return url;
}

/* Nei temi scuri le tessere si tingono (`.place-tiles-dark`). Il tema lo dice
   il suo `color-scheme`, dichiarato su ogni `[data-theme]` di `mobile-style.css`:
   cosi' un tema nuovo non va aggiunto a nessun elenco. */
function dark() {
  try {
    return /dark/.test(getComputedStyle(document.documentElement).colorScheme || '');
  } catch (_) {
    return false;
  }
}

const deps = {
  t: tr,
  lastFix,
  tileUrl,
  dark,
  get locale() {
    return i18n.locale || undefined;
  },
};

/** Le schede dentro *block*, appena disegnato. Non aspetta niente: posizione e
 *  mappa arrivano dopo, e la scheda e' intera anche senza. */
export function renderPlacesIn(block) {
  renderPlaces(block, deps).catch((err) => console.warn('places: render failed', err));
}

/** Un tocco su una scheda: apre il posto in Maps. `true` se il tocco era suo.
 *
 *  Su Android passa dal ponte (`openMap`): l'app Google Maps, poi qualunque
 *  app di mappe, poi il browser. Fuori (banco, browser del Mac) apre il link. */
export function openPlaceFromClick(e) {
  const node = placeTarget(e.target);
  if (!node) return false;
  e.preventDefault();
  const { lat = '', lng = '', label = '', href = '' } = node.dataset;
  const native = window.JennyNative;
  if (native && typeof native.openMap === 'function') {
    native.openMap(lat, lng, label, href);
  } else {
    openOutsideWebView(href);
  }
  return true;
}
