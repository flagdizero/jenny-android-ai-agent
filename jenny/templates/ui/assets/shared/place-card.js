/** La scheda di un posto dentro una risposta di Jenny.
 *
 *  **Nessun protocollo nuovo.** Jenny manda un posto scrivendo un link a Google
 *  Maps da solo sul suo paragrafo (`[nome — indirizzo](https://www.google.com/maps/search/?api=1&query=lat,lng)`);
 *  qui quel paragrafo diventa una cartolina. Dove la scheda non c'e' — Telegram,
 *  la notifica, la bolla della mascotte, le pagine del quaderno — resta un link
 *  che funziona lo stesso. Un link dentro una frase non si tocca: e' Jenny che
 *  cita un posto, non che lo manda.
 *
 *  **Le coordinate vengono dall'URL, il resto dal testo del link.** Il testo e'
 *  del modello: entra solo con `textContent`. Le coordinate sono numeri dentro i
 *  limiti o niente — e senza coordinate la scheda resta senza mappa (il disegno a
 *  curve di livello), ma il tocco porta lo stesso al posto, cercato per nome.
 *
 *  **Due cose arrivano dopo, e possono non arrivare:** dov'e' il telefono (per
 *  «sei qui» o «1,2 km da te») e le tessere della mappa. Le passa chi chiama
 *  (`deps`), cosi' questo modulo non importa niente ed e' quello vero che il
 *  banco node fa girare. Finche' non arrivano — o se non arrivano mai — la
 *  scheda e' gia' intera.
 */

/* Il lato di una tessera Web Mercator, e lo zoom della cartolina: le vie con i
   nomi, qualche isolato attorno. */
const TILE = 256;
const CARD_ZOOM = 16;
const MIN_ZOOM = 3;

/* L'area della mappa, in px CSS: deve combaciare con `.place-card-art` e
   `.place-list-map` in `mobile-style.css`. La cartolina e' larga al massimo 312
   e la lista 330; in una colonna piu' stretta il mosaico resta centrato e i bordi
   si tagliano in modo simmetrico, cosi' il punto resta in mezzo. */
const CARD_ART = { w: 312, h: 138 };
const LIST_ART = { w: 330, h: 104 };

/* «Sei qui»: il posto e' entro la precisione del fix (mai meno di 30 m, che e'
   l'ordine di un GPS buono in citta') e il fix ha meno di dieci minuti. */
const HERE_MIN_RADIUS_M = 30;
const HERE_MAX_AGE_S = 600;
/* Oltre mezz'ora un fix non dice piu' dove sei: niente distanza, piuttosto che
   «9.776 km da te» da una posizione di un'ora fa (visto sull'emulatore). */
const DISTANCE_MAX_AGE_S = 1800;

const COORD = /^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$/;
/* `/maps/place/…/data=…!3d<lat>!4d<lng>` e' il punto del posto; `@lat,lng,z`
   e' il centro della vista, che di solito ci sta sopra: si preferisce il primo. */
const DATA_POINT = /!3d(-?\d{1,3}\.\d+)!4d(-?\d{1,3}\.\d+)/;
const VIEW_POINT = /@(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)/;
/* Fra nome e indirizzo, il trattino lungo (o medio). Il trattino corto no:
   sta dentro i nomi («Bar - Tabacchi»). */
const NAME_SEP = /\s+[—–]\s+/;

const SVG_NS = 'http://www.w3.org/2000/svg';

function validPoint(lat, lng) {
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  if (Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
  return { lat, lng };
}

function isMapsUrl(url) {
  if (url.protocol !== 'https:' && url.protocol !== 'http:') return false;
  const host = url.hostname.toLowerCase();
  const path = url.pathname;
  if (host === 'maps.app.goo.gl') return true;
  if (host === 'goo.gl') return path === '/maps' || path.startsWith('/maps/');
  if (/^maps\.google\.[a-z]{2,3}(\.[a-z]{2})?$/.test(host)) return true;
  if (/^(www\.)?google\.[a-z]{2,3}(\.[a-z]{2})?$/.test(host)) {
    return path === '/maps' || path.startsWith('/maps/');
  }
  return false;
}

function pointOf(url) {
  for (const key of ['query', 'q', 'll', 'center', 'destination']) {
    const m = COORD.exec(url.searchParams.get(key) || '');
    if (m) return validPoint(Number(m[1]), Number(m[2]));
  }
  let path = url.pathname;
  try {
    path = decodeURIComponent(path);
  } catch (_) {
    /* Un percorso con un % rotto si legge com'e'. */
  }
  const m = DATA_POINT.exec(path) || VIEW_POINT.exec(path);
  return m ? validPoint(Number(m[1]), Number(m[2])) : null;
}

/** Il posto di un link, o `null` se il link non porta a Google Maps.
 *
 *  @param {string} href  l'attributo com'e' scritto
 *  @param {string} text  il testo del link
 *  @returns {{href:string, lat:number|null, lng:number|null, name:string, address:string} | null}
 */
export function placeOf(href, text = '') {
  let url;
  try {
    url = new URL(String(href || ''));
  } catch (_) {
    return null;
  }
  if (!isMapsUrl(url)) return null;
  const point = pointOf(url);
  const label = String(text || '').replace(/\s+/g, ' ').trim();
  let name = '';
  let address = '';
  /* Un indirizzo scritto nudo diventa un link il cui testo e' l'indirizzo
     stesso: li' il nome non c'e', e al suo posto va quel che si cercava. */
  if (label && label !== href && label !== url.href) {
    const [first, ...rest] = label.split(NAME_SEP);
    name = first.trim();
    address = rest.join(' — ').trim();
  } else {
    const query = url.searchParams.get('query') || url.searchParams.get('q') || '';
    if (query && !COORD.test(query)) name = query.trim();
  }
  return { href: url.href, lat: point?.lat ?? null, lng: point?.lng ?? null, name, address };
}

/** L'unico link di *el*, se *el* non contiene nient'altro; altrimenti `null`.
 *  Un grassetto attorno al link va bene (`**[x](url)**`), una parola accanto no. */
function soleLink(el) {
  const links = el.querySelectorAll('a[href]');
  if (links.length !== 1) return null;
  const a = links[0];
  if (el.textContent.trim() !== a.textContent.trim()) return null;
  for (const node of el.querySelectorAll('*')) {
    if (node === a || a.contains(node) || node.contains(a)) continue;
    if (node.tagName === 'BR') continue;
    return null;
  }
  return a;
}

function placeIn(el) {
  const a = soleLink(el);
  return a ? placeOf(a.getAttribute('href'), a.textContent) : null;
}

/* ── Geometria ── */

/** Distanza in metri fra due punti (haversine). */
function distanceM(a, b) {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLng = (b.lng - a.lng) * rad;
  const s = Math.sin(dLat / 2) ** 2
    + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * 6371008.8 * Math.asin(Math.min(1, Math.sqrt(s)));
}

/** Il punto in pixel del mondo Web Mercator a zoom *z*. */
export function worldPx(lat, lng, z) {
  const size = TILE * 2 ** z;
  const sin = Math.min(Math.max(Math.sin(lat * Math.PI / 180), -0.9999), 0.9999);
  return {
    x: ((lng + 180) / 360) * size,
    y: (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * size,
  };
}

/** Le tessere che coprono un riquadro *w*×*h* centrato in (*cx*, *cy*), con la
 *  posizione di ciascuna dentro il riquadro. In orizzontale il mondo si ripete;
 *  in verticale fuori dalla mappa non c'e' niente da chiedere. */
export function tilesFor(cx, cy, w, h, z) {
  const n = 2 ** z;
  const left = cx - w / 2;
  const top = cy - h / 2;
  const out = [];
  for (let ty = Math.floor(top / TILE); ty <= Math.floor((top + h - 1) / TILE); ty++) {
    if (ty < 0 || ty >= n) continue;
    for (let tx = Math.floor(left / TILE); tx <= Math.floor((left + w - 1) / TILE); tx++) {
      out.push({ z, x: ((tx % n) + n) % n, y: ty, left: tx * TILE - left, top: ty * TILE - top });
    }
  }
  return out;
}

/** Lo zoom piu' alto (fino a *maxZoom*) a cui tutti i *points* stanno nel
 *  riquadro, con *pad* di margine, e il centro del loro ingombro. */
export function fitView(points, w, h, pad = 22, maxZoom = CARD_ZOOM) {
  let view = null;
  for (let z = maxZoom; z >= MIN_ZOOM; z--) {
    const px = points.map((p) => worldPx(p.lat, p.lng, z));
    const xs = px.map((p) => p.x);
    const ys = px.map((p) => p.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    view = { z, cx: (minX + maxX) / 2, cy: (minY + maxY) / 2 };
    if (maxX - minX <= w - 2 * pad && maxY - minY <= h - 2 * pad) return view;
  }
  return view;
}

/* ── Testi ── */

function formatDistance(m, locale) {
  if (m < 1000) return `${Math.max(10, Math.round(m / 10) * 10)} m`;
  const km = new Intl.NumberFormat(locale || undefined, {
    maximumFractionDigits: m < 10000 ? 1 : 0,
  }).format(m / 1000);
  return `${km} km`;
}

/** Dove sta il posto rispetto al telefono: `{here:true, accuracy}` se ci sei
 *  sopra, `{here:false, meters}` altrimenti, `null` senza un fix recente o
 *  senza coordinate. */
export function relation(place, fix) {
  if (!fix || place.lat == null || place.lng == null) return null;
  if (!(Number(fix.ageSeconds) <= DISTANCE_MAX_AGE_S)) return null;
  const meters = distanceM({ lat: fix.lat, lng: fix.lng }, place);
  const radius = Math.max(Number(fix.accuracyMeters) || 0, HERE_MIN_RADIUS_M);
  const fresh = Number(fix.ageSeconds) <= HERE_MAX_AGE_S;
  if (fresh && meters <= radius) {
    return { here: true, accuracy: Math.round(Number(fix.accuracyMeters) || 0) };
  }
  return { here: false, meters };
}

const t = (deps, key, params) => (deps.t ? deps.t(key, params) : key);

/* ── Costruzione ── */

function el(doc, tag, cls, text) {
  const node = doc.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function icon(doc, name) {
  const i = el(doc, 'i', `ti ${name}`);
  i.setAttribute('aria-hidden', 'true');
  return i;
}

function svg(doc, tag, attrs) {
  const node = doc.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

/* La faccia senza mappa: curve di livello nel colore del tema. Non finge di
   essere una mappa — dice «un posto», non «questo posto». */
function topo(doc, w, h) {
  const root = svg(doc, 'svg', {
    class: 'place-topo', viewBox: `0 0 ${w} ${h}`,
    preserveAspectRatio: 'xMidYMid slice', 'aria-hidden': 'true',
  });
  for (let i = 1; i <= 7; i++) {
    root.appendChild(svg(doc, 'ellipse', {
      cx: w / 2 + i * 3, cy: h / 2 + 4 - i, rx: 26 + i * 24, ry: 14 + i * 11,
      transform: `rotate(${-8 + i * 2} ${w / 2} ${h / 2})`,
      style: `opacity:${(0.34 - i * 0.035).toFixed(3)}`,
    }));
  }
  return root;
}

function pin(doc) {
  const root = svg(doc, 'svg', { class: 'place-pin', viewBox: '0 0 30 38', 'aria-hidden': 'true' });
  root.appendChild(svg(doc, 'path', {
    d: 'M15 1C7.3 1 1 7.1 1 14.7 1 25 15 37 15 37s14-12 14-22.3C29 7.1 22.7 1 15 1z',
  }));
  root.appendChild(svg(doc, 'circle', { cx: 15, cy: 14.5, r: 5 }));
  return root;
}

function labelOf(place) {
  return [place.name, place.address].filter(Boolean).join(', ');
}

function dataOf(node, place) {
  node.href = place.href;
  node.dataset.href = place.href;
  if (place.lat != null) {
    node.dataset.lat = String(place.lat);
    node.dataset.lng = String(place.lng);
  }
  node.dataset.label = labelOf(place);
}

function buildCard(doc, place, deps) {
  const card = el(doc, 'a', 'place-card');
  dataOf(card, place);
  const art = el(doc, 'div', 'place-card-art');
  art.appendChild(topo(doc, CARD_ART.w, CARD_ART.h));
  art.appendChild(el(doc, 'span', 'place-mark-topo'));
  art.lastChild.appendChild(icon(doc, 'ti-map-pin'));
  art.appendChild(pin(doc));
  art.appendChild(el(doc, 'span', 'place-halo'));
  art.appendChild(el(doc, 'span', 'place-dot'));
  card.appendChild(art);

  const body = el(doc, 'div', 'place-card-body');
  const name = place.name || (place.lat != null
    ? `${place.lat.toFixed(5)}, ${place.lng.toFixed(5)}` : t(deps, 'place.unnamed'));
  body.appendChild(el(doc, 'div', 'place-name', name));
  if (place.address) body.appendChild(el(doc, 'div', 'place-address', place.address));
  card.appendChild(body);

  const foot = el(doc, 'div', 'place-card-foot');
  const open = el(doc, 'span', 'place-open');
  open.appendChild(icon(doc, 'ti-map-2'));
  open.appendChild(doc.createTextNode(t(deps, 'place.openInMaps')));
  foot.appendChild(open);
  foot.appendChild(el(doc, 'span', 'place-meta'));
  card.appendChild(foot);
  return card;
}

function buildList(doc, places, deps) {
  const list = el(doc, 'div', 'place-list');
  list.appendChild(el(doc, 'div', 'place-list-map'));
  places.forEach((place, i) => {
    const row = el(doc, 'a', 'place-row');
    dataOf(row, place);
    row.appendChild(el(doc, 'span', 'place-row-num', String(i + 1)));
    const text = el(doc, 'span', 'place-row-text');
    text.appendChild(el(doc, 'span', 'place-name', place.name || t(deps, 'place.unnamed')));
    text.appendChild(el(doc, 'span', 'place-address', place.address));
    row.appendChild(text);
    row.appendChild(el(doc, 'span', 'place-row-meta'));
    row.appendChild(icon(doc, 'ti-chevron-right place-row-go'));
    list.appendChild(row);
  });
  return list;
}

/* ── Quel che arriva dopo ── */

function mosaic(doc, view, size, deps) {
  const wrap = el(doc, 'div', 'place-tiles');
  wrap.style.width = `${size.w}px`;
  wrap.style.height = `${size.h}px`;
  wrap.style.marginLeft = `${-size.w / 2}px`;
  if (deps.dark?.()) wrap.classList.add('place-tiles-dark');
  const tiles = tilesFor(view.cx, view.cy, size.w, size.h, view.z);
  const loads = tiles.map(async (tile) => {
    const src = await deps.tileUrl(tile.z, tile.x, tile.y);
    if (!src) throw new Error('tile');
    const img = el(doc, 'img');
    img.alt = '';
    img.decoding = 'async';
    img.style.left = `${tile.left}px`;
    img.style.top = `${tile.top}px`;
    img.src = src;
    wrap.appendChild(img);
  });
  return { wrap, ready: Promise.all(loads) };
}

function attribution(doc) {
  return el(doc, 'span', 'place-attribution', '© OpenStreetMap');
}

async function enrichCard(card, place, deps) {
  const art = card.querySelector('.place-card-art');
  const meta = card.querySelector('.place-meta');
  const tasks = [];
  if (deps.lastFix) {
    tasks.push(Promise.resolve(deps.lastFix()).then((fix) => {
      const rel = relation(place, fix);
      if (!rel) return;
      if (rel.here) {
        card.classList.add('is-here');
        meta.textContent = rel.accuracy
          ? t(deps, 'place.hereWithin', { m: rel.accuracy }) : t(deps, 'place.youAreHere');
      } else {
        meta.textContent = t(deps, 'place.fromYou', { d: formatDistance(rel.meters, deps.locale) });
      }
    }).catch(() => {}));
  }
  if (deps.tileUrl && place.lat != null) {
    const p = worldPx(place.lat, place.lng, CARD_ZOOM);
    const { wrap, ready } = mosaic(card.ownerDocument, { z: CARD_ZOOM, cx: p.x, cy: p.y }, CARD_ART, deps);
    tasks.push(ready.then(() => {
      art.insertBefore(wrap, art.firstChild);
      art.appendChild(attribution(card.ownerDocument));
      card.classList.add('has-map');
    }).catch(() => {}));
  }
  await Promise.all(tasks);
}

async function enrichList(list, places, deps) {
  const rows = [...list.querySelectorAll('.place-row')];
  let fix = null;
  if (deps.lastFix) {
    fix = await Promise.resolve(deps.lastFix()).catch(() => null);
    rows.forEach((row, i) => {
      const rel = relation(places[i], fix);
      if (!rel) return;
      row.querySelector('.place-row-meta').textContent = rel.here
        ? t(deps, 'place.youAreHere') : formatDistance(rel.meters, deps.locale);
    });
  }
  const marked = places
    .map((p, i) => ({ ...p, n: i + 1 }))
    .filter((p) => p.lat != null);
  if (!deps.tileUrl || !marked.length) return;
  const doc = list.ownerDocument;
  const view = fitView(marked, LIST_ART.w, LIST_ART.h);
  const { wrap, ready } = mosaic(doc, view, LIST_ART, deps);
  const left = view.cx - LIST_ART.w / 2;
  const top = view.cy - LIST_ART.h / 2;
  const at = (lat, lng) => {
    const p = worldPx(lat, lng, view.z);
    return { x: p.x - left, y: p.y - top };
  };
  const inside = (q) => q.x >= 0 && q.x <= LIST_ART.w && q.y >= 0 && q.y <= LIST_ART.h;
  for (const p of marked) {
    const q = at(p.lat, p.lng);
    const num = el(doc, 'span', 'place-num', String(p.n));
    num.style.left = `${q.x}px`;
    num.style.top = `${q.y}px`;
    wrap.appendChild(num);
  }
  if (fix) {
    const q = at(fix.lat, fix.lng);
    if (inside(q)) {
      const dot = el(doc, 'span', 'place-dot');
      dot.style.left = `${q.x}px`;
      dot.style.top = `${q.y}px`;
      wrap.appendChild(dot);
    }
  }
  try {
    await ready;
  } catch (_) {
    return;
  }
  const box = list.querySelector('.place-list-map');
  box.appendChild(wrap);
  box.appendChild(attribution(doc));
  list.classList.add('has-map');
}

/* ── Il passaggio sul contenuto ── */

/** Sostituisce in *root* i link a Maps che stanno da soli con le schede.
 *
 *  Una lista le cui voci sono tutte posti diventa una scheda lista (una voce
 *  sola: una cartolina); due o piu' paragrafi-posto di fila, idem. Ritorna una
 *  Promise che si risolve quando posizione e tessere sono arrivate o hanno
 *  rinunciato (ai test serve; la chat non la aspetta).
 *
 *  @param {Element} root
 *  @param {{t?:Function, lastFix?:Function, tileUrl?:Function, dark?:Function, locale?:string}} deps
 */
export function renderPlaces(root, deps = {}) {
  if (!root?.querySelectorAll) return Promise.resolve();
  const doc = root.ownerDocument;
  const jobs = [];
  const make = (target, places) => {
    if (places.length === 1) {
      const card = buildCard(doc, places[0], deps);
      target.replaceWith(card);
      jobs.push(enrichCard(card, places[0], deps));
    } else {
      const list = buildList(doc, places, deps);
      target.replaceWith(list);
      jobs.push(enrichList(list, places, deps));
    }
  };

  for (const list of [...root.querySelectorAll('ul, ol')]) {
    if (!list.isConnected || !root.contains(list)) continue;
    const items = [...list.children].filter((c) => c.tagName === 'LI');
    if (!items.length || items.length !== list.children.length) continue;
    const places = items.map(placeIn);
    if (places.some((p) => !p)) continue;
    make(list, places);
  }

  /* I paragrafi, a gruppi di fratelli consecutivi. */
  const paras = [...root.querySelectorAll('p')].filter((p) => !p.closest('li'));
  let run = [];
  const flush = () => {
    if (!run.length) return;
    const places = run.map((r) => r.place);
    const first = run[0].p;
    for (const r of run.slice(1)) r.p.remove();
    make(first, places);
    run = [];
  };
  for (const p of paras) {
    const place = placeIn(p);
    if (!place) {
      flush();
      continue;
    }
    if (run.length && run[run.length - 1].p.nextElementSibling !== p) flush();
    run.push({ p, place });
  }
  flush();
  return Promise.all(jobs).then(() => undefined);
}

/** Il posto toccato, se il tocco e' su una scheda: l'elemento con i dati. */
export function placeTarget(target) {
  return target?.closest?.('a.place-card, a.place-row') || null;
}
