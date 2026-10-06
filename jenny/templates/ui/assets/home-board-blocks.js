/**
 * I blocchi di una striscia: la forma compatta di un'app sulla bacheca.
 *
 * Il gateway manda la striscia gia' calcolata (`jenny/apps/slip_view.py`): una
 * taglia (S/M/L/XL), un dato in alto, uno stato e pochi blocchi. Qui la si
 * disegna, sempre allo stesso modo per ogni app: e' la casa che disegna, non
 * l'app — niente HTML dell'agente, niente iframe.
 *
 * Ogni blocco occupa righe intere (`--slip-rows`); una lista che non ci sta
 * finisce con «e altre N» al posto dell'ultima riga, cosi' l'altezza della
 * striscia non cambia mai coi dati.
 *
 * I controlli (spunta, tasto, interruttore, − / +) chiamano `ctx.act({action,
 * params})`: chi disegna la bacheca decide come (l'azione dell'app, poi la
 * rilettura). Portano la classe `home-board-control`, che la bacheca accende
 * solo da vicino.
 */

import { i18n } from './shared/i18n.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function locale() {
  return i18n.locale || undefined;
}

/** Un numero nel formato di chi guarda; il testo resta testo. */
export function formatValue(value, { digits } = {}) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return i18n.t(value ? 'board.micro.yes' : 'board.micro.no');
  if (typeof value === 'number') {
    const max = digits ?? (Math.abs(value) >= 100 ? 0 : Math.abs(value) >= 10 ? 1 : 2);
    return new Intl.NumberFormat(locale(), { maximumFractionDigits: max }).format(value);
  }
  return String(value);
}

function withUnit(value, unit) {
  const v = formatValue(value);
  return unit && v !== '—' ? `${v} ${unit}` : v;
}

/** Un andamento come polyline: `width`×`height`, scala sui punti (e sulla soglia). */
export function sparkSvg(points, { width = 100, height = 30, band = null, cls = 'home-board-block-spark' } = {}) {
  const nums = (points || []).filter((p) => typeof p === 'number');
  if (nums.length < 2) return null;
  let lo = Math.min(...nums);
  let hi = Math.max(...nums);
  if (band && typeof band.value === 'number') {
    lo = Math.min(lo, band.value);
    hi = Math.max(hi, band.value);
  }
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.08;
  lo -= pad; hi += pad;
  const y = (v) => height - ((v - lo) / (hi - lo)) * height;
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', cls);
  if (band && typeof band.value === 'number') {
    const at = y(band.value);
    const rect = document.createElementNS(SVG_NS, 'rect');
    const below = band.op === '<' || band.op === '<=';
    rect.setAttribute('x', '0');
    rect.setAttribute('width', String(width));
    rect.setAttribute('y', String(below ? at : 0));
    rect.setAttribute('height', String(below ? height - at : at));
    rect.setAttribute('class', 'is-band');
    svg.appendChild(rect);
  }
  const line = document.createElementNS(SVG_NS, 'polyline');
  line.setAttribute('points', nums.map((v, i) => `${(i / (nums.length - 1)) * width},${y(v).toFixed(1)}`).join(' '));
  line.setAttribute('vector-effect', 'non-scaling-stroke');
  svg.appendChild(line);
  return svg;
}

function block(kind, rows) {
  const node = el('div', `home-board-block is-${kind}`);
  node.style.setProperty('--slip-rows', String(rows || 1));
  return node;
}

/* Sulla bacheca un controllo agisce solo da vicino (`is-near` sul muro, che il
   CSS rende anche non toccabile): da lontano il tocco passa alla striscia, che
   apre l'app. Fuori dalla bacheca (il cassetto) e' sempre attivo. */
function awake(node) {
  const wall = node.closest('.home-board-wall');
  return !wall || wall.classList.contains('is-near');
}

function control(node, ctx, request) {
  node.classList.add('home-board-control');
  node.addEventListener('click', (e) => {
    if (!awake(node)) {
      e.preventDefault();
      return;
    }
    e.stopPropagation();
    ctx.act?.(request());
  });
  node.addEventListener('pointerdown', (e) => {
    if (awake(node)) e.stopPropagation();
  });
  return node;
}

/** Le righe di una lista: al piu' *max*; se ne restano, l'ultima dice quante. */
function listRows(items, more) {
  if (!more) return { shown: items, more: 0 };
  return { shown: items.slice(0, Math.max(0, items.length - 1)), more: more + 1 };
}

function moreRow(more) {
  return el('div', 'home-board-block-row is-more', i18n.t('board.slip.more', { count: more }));
}

// ── i blocchi ───────────────────────────────────────────────────────────────

function value(b) {
  const node = block('value', b.rows);
  const big = el('span', 'home-board-block-big', formatValue(b.value));
  if (b.unit && b.value !== null && b.value !== undefined) big.appendChild(el('small', '', b.unit));
  node.appendChild(big);
  if (b.delta !== undefined && b.delta !== null) {
    const n = typeof b.delta === 'number' ? b.delta : null;
    const text = `${n !== null && n > 0 ? '+' : ''}${formatValue(b.delta)}${b.deltaUnit ? ` ${b.deltaUnit}` : ''}`;
    node.appendChild(el('span', `home-board-block-delta${n === null ? '' : n >= 0 ? ' is-up' : ' is-down'}`, text));
  }
  const spark = sparkSvg(b.trend, { width: 100, height: 30 });
  if (spark) node.appendChild(spark);
  if (b.label) node.setAttribute('aria-label', `${b.label}: ${withUnit(b.value, b.unit)}`);
  return node;
}

function numbers(b) {
  const node = block('numbers', b.rows);
  node.style.setProperty('--slip-cols', String(b.items?.length || 1));
  for (const it of b.items || []) {
    const cell = el('div', 'home-board-block-number');
    cell.append(el('span', 'home-board-block-label', it.label || ''), el('span', 'home-board-block-strong', withUnit(it.value, it.unit)));
    node.appendChild(cell);
  }
  return node;
}

function chart(b) {
  const node = block('chart', b.rows);
  const spark = sparkSvg(b.points, { width: 300, height: 60, band: b.band, cls: 'home-board-block-chart' });
  /* Meno di due punti: una linea tratteggiata, senza parole (lo stato le dice gia'). */
  node.appendChild(spark || el('div', 'home-board-block-flat'));
  return node;
}

function statusText(state, at, now) {
  if (state === 'alarm') return i18n.t('board.slip.alarm');
  if (state === 'empty') return i18n.t('board.slip.empty');
  if (state === 'broken' || state === 'quarantined') return i18n.t('board.slip.broken');
  if (state === 'stale' && at) return i18n.t('board.slip.stoppedFor', { age: ageText(at, now) });
  if (at) return i18n.t('board.slip.updatedAt', { time: timeText(at, now) });
  return '';
}

export function timeText(at, now = new Date()) {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return '';
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay
    ? d.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString(locale(), { day: 'numeric', month: 'short' });
}

export function ageText(at, now = new Date()) {
  const ms = now - new Date(at);
  if (!(ms >= 0)) return '';
  const h = Math.floor(ms / 3600000);
  if (h < 1) return i18n.t('board.cover.minutes', { count: Math.max(1, Math.floor(ms / 60000)) });
  if (h < 48) return i18n.t('board.cover.hours', { count: h });
  return i18n.t('board.cover.days', { count: Math.floor(h / 24) });
}

function status(b, ctx) {
  const node = block('status', b.rows);
  const kind = b.state === 'alarm' || b.state === 'broken' ? 'is-alarm'
    : b.state === 'stale' || b.state === 'empty' ? 'is-warn' : 'is-ok';
  let text = statusText(b.state, b.at, ctx.now);
  if (b.online !== undefined) text = `${i18n.t(b.online ? 'board.slip.online' : 'board.slip.offline')}${text ? ` · ${text}` : ''}`;
  node.appendChild(el('span', `home-board-block-pill ${kind}`, text || '—'));
  if (b.text !== undefined && b.text !== null) node.appendChild(el('span', 'home-board-block-label', formatValue(b.text)));
  return node;
}

function progress(b) {
  const node = block('progress', b.rows);
  const top = el('div', 'home-board-block-line');
  top.append(el('span', 'home-board-block-strong', withUnit(b.value, b.unit)));
  if (b.max) top.appendChild(el('span', 'home-board-block-label', i18n.t('board.slip.of', { max: withUnit(b.max, b.unit) })));
  if (b.pct !== null && b.pct !== undefined) top.appendChild(el('span', 'home-board-block-label is-end', `${formatValue(b.pct, { digits: 0 })} %`));
  const bar = el('div', 'home-board-block-bar');
  bar.setAttribute('role', 'progressbar');
  bar.setAttribute('aria-valuemin', '0');
  bar.setAttribute('aria-valuemax', '100');
  if (b.pct !== null && b.pct !== undefined) bar.setAttribute('aria-valuenow', String(b.pct));
  const fill = el('div', 'home-board-block-fill');
  fill.style.width = `${b.pct || 0}%`;
  bar.appendChild(fill);
  node.append(top, bar);
  return node;
}

function barsBlock(b) {
  const node = block('bars', b.rows);
  const { shown, more } = listRows(b.items || [], b.more || 0);
  for (const r of shown) {
    const row = el('div', 'home-board-block-row');
    const bar = el('div', 'home-board-block-bar');
    const fill = el('div', 'home-board-block-fill');
    fill.style.width = `${Math.max(0, Math.min(100, r.weight || 0))}%`;
    bar.appendChild(fill);
    if (typeof r.target === 'number') {
      const mark = el('span', 'home-board-block-target');
      mark.style.left = `${Math.max(0, Math.min(100, r.target))}%`;
      bar.appendChild(mark);
    }
    row.append(el('span', 'home-board-block-strong is-key', formatValue(r.label)), bar,
      el('span', 'home-board-block-label is-end', withUnit(r.value, b.unit)));
    node.appendChild(row);
  }
  if (more) node.appendChild(moreRow(more));
  return node;
}

function table(b) {
  const node = block('table', b.rows);
  const { shown, more } = listRows(b.items || [], b.more || 0);
  for (const r of shown) {
    const row = el('div', 'home-board-block-row');
    (b.columns || []).forEach((c, i) => {
      const cell = el('span', i === 0 ? 'home-board-block-label' : `home-board-block-cell${i === b.columns.length - 1 ? ' is-end' : ''}`,
        withUnit(r[c.field], c.unit));
      row.appendChild(cell);
    });
    node.appendChild(row);
  }
  if (!shown.length && !more) node.appendChild(el('div', 'home-board-block-row is-more', i18n.t('board.micro.noRows')));
  if (more) node.appendChild(moreRow(more));
  return node;
}

function events(b) {
  const node = block('events', b.rows);
  const { shown, more } = listRows(b.items || [], b.more || 0);
  for (const r of shown) {
    const row = el('div', 'home-board-block-row');
    const d = new Date(`${r.date}T12:00:00`);
    const label = Number.isNaN(d.getTime()) ? r.date : d.toLocaleDateString(locale(), { day: 'numeric', month: 'short' });
    row.append(el('span', `home-board-block-strong is-key${r.days <= 7 ? ' is-alarm' : ''}`, label), el('span', 'home-board-block-cell', formatValue(r.text)));
    node.appendChild(row);
  }
  if (!shown.length && !more) node.appendChild(el('div', 'home-board-block-row is-more', i18n.t('board.slip.nothingAhead')));
  if (more) node.appendChild(moreRow(more));
  return node;
}

function checklist(b, ctx) {
  const node = block('checklist', b.rows);
  const { shown, more } = listRows(b.items || [], b.more || 0);
  for (const it of shown) {
    const row = el('label', `home-board-block-row is-check${it.done ? ' is-done' : ''}`);
    const box = el('input');
    box.type = 'checkbox';
    box.checked = !!it.done;
    if (b.toggle && it.id) {
      control(box, ctx, () => ({ action: b.toggle, params: { id: it.id, [b.doneField]: !it.done } }));
    } else {
      box.disabled = true;
    }
    row.append(box, el('span', 'home-board-block-cell', formatValue(it.text)));
    node.appendChild(row);
  }
  if (!shown.length && !more) node.appendChild(el('div', 'home-board-block-row is-more', i18n.t('board.slip.allDone')));
  if (more) node.appendChild(moreRow(more));
  return node;
}

function buttons(b, ctx) {
  const node = block('buttons', b.rows);
  (b.actions || []).forEach((a, i) => {
    const btn = el('button', `home-board-block-btn${i === 0 ? ' is-primary' : ''}`, a.label || a.action);
    btn.type = 'button';
    control(btn, ctx, () => ({ action: a.action, params: a.params || {} }));
    node.appendChild(btn);
  });
  return node;
}

function switches(b, ctx) {
  const node = block('switches', b.rows);
  for (const it of b.items || []) {
    const row = el('label', 'home-board-block-row is-switch');
    const box = el('input');
    box.type = 'checkbox';
    box.setAttribute('role', 'switch');
    box.checked = !!it.on;
    control(box, ctx, () => ({ action: it.action, params: { [it.field]: !it.on, ...(it.withId && b.id ? { id: b.id } : {}) } }));
    row.append(el('span', 'home-board-block-cell', it.label || it.field), box);
    node.appendChild(row);
  }
  return node;
}

function counter(b, ctx) {
  const node = block('counter', b.rows);
  for (const it of b.items || []) {
    const cell = el('div', 'home-board-block-counter');
    const dec = el('button', 'home-board-block-round', '−');
    dec.type = 'button';
    dec.setAttribute('aria-label', i18n.t('board.slip.dec', { label: it.label || '' }));
    control(dec, ctx, () => ({ action: it.dec, params: {} }));
    const inc = el('button', 'home-board-block-round', '+');
    inc.type = 'button';
    inc.setAttribute('aria-label', i18n.t('board.slip.inc', { label: it.label || '' }));
    control(inc, ctx, () => ({ action: it.inc, params: {} }));
    const mid = el('div', 'home-board-block-counter-mid');
    mid.append(el('span', 'home-board-block-label', it.label || ''), el('span', 'home-board-block-big', formatValue(it.value)));
    cell.append(dec, mid, inc);
    node.appendChild(cell);
  }
  return node;
}

function weekdayLetters() {
  // Lunedi' 5 gennaio 2026: sette giorni di fila, nella lingua di chi guarda.
  return Array.from({ length: 7 }, (_, i) => new Date(2026, 0, 5 + i)
    .toLocaleDateString(locale(), { weekday: 'narrow' }));
}

function week(b) {
  const node = block('week', b.rows);
  const letters = weekdayLetters();
  (b.days || []).forEach((d, i) => {
    const cell = el('div', `home-board-block-day${d.done ? ' is-done' : ''}${i === b.today ? ' is-today' : ''}`);
    cell.append(el('span', 'home-board-block-label', letters[i]), el('span', 'home-board-block-dot', String(Number(d.date.slice(8, 10)))));
    node.appendChild(cell);
  });
  return node;
}

function month(b) {
  const node = block('month', b.rows);
  const title = new Date(b.year, (b.month || 1) - 1, 1).toLocaleDateString(locale(), { month: 'long', year: 'numeric' });
  node.appendChild(el('div', 'home-board-block-label is-title', title));
  const grid = el('div', 'home-board-block-month');
  for (const l of weekdayLetters()) grid.appendChild(el('span', 'home-board-block-label is-head', l));
  for (let i = 0; i < (b.firstWeekday || 0); i++) grid.appendChild(el('span'));
  const days = new Date(b.year, b.month, 0).getDate();
  const marked = new Set(b.days || []);
  const alarm = new Set(b.alarm || []);
  for (let d = 1; d <= days; d++) {
    const cls = ['home-board-block-mday'];
    if (marked.has(d)) cls.push('is-marked');
    if (alarm.has(d)) cls.push('is-alarm');
    if (d === b.today) cls.push('is-today');
    grid.appendChild(el('span', cls.join(' '), String(d)));
  }
  node.appendChild(grid);
  return node;
}

function note(b) {
  const node = block('note', b.rows);
  const p = el('p', 'home-board-block-note', b.text || '');
  p.style.setProperty('--slip-lines', String(b.lines || b.rows || 1));
  node.appendChild(p);
  return node;
}

/* Le foto arrivano dopo: `ctx.media(name)` le chiede al gateway (un `blob:`),
   e chi disegna le rilascia al giro dopo. Finche' non c'e', un riquadro vuoto. */
function photos(b, ctx) {
  const node = block('photos', b.rows);
  for (const name of b.names || []) {
    const frame = el('div', 'home-board-block-photo');
    const img = el('img');
    img.alt = '';
    img.decoding = 'async';
    frame.appendChild(img);
    ctx.media?.(name).then((url) => { if (url) img.src = url; }).catch(() => {});
    node.appendChild(frame);
  }
  if (!(b.names || []).length) node.appendChild(el('div', 'home-board-block-flat'));
  if (b.more) node.appendChild(el('span', 'home-board-block-label is-more-photos', i18n.t('board.slip.more', { count: b.more })));
  return node;
}

const RENDERERS = {
  value, numbers, chart, status, progress, bars: barsBlock, table, events, checklist, buttons,
  switches, counter, week, month, note, photos,
};

/** Un blocco della striscia. Uno sconosciuto (un gateway piu' nuovo) resta vuoto. */
export function renderBlock(b, ctx = {}) {
  if (b?.failed) return block('failed', b.rows);
  const fn = RENDERERS[b?.kind];
  return fn ? fn(b, { now: new Date(), ...ctx }) : block('unknown', b?.rows);
}

/** Il dato in alto: quello dichiarato dall'app, o lo stato (con riserva). */
export function metaText(slip, now = new Date()) {
  const m = slip?.meta;
  if (m && m.value !== undefined && m.value !== null) {
    return m.unit ? `${formatValue(m.value)} ${m.unit}${m.label ? ` ${m.label}` : ''}`
      : `${formatValue(m.value)}${m.label ? ` ${m.label}` : ''}`;
  }
  /* Una striscia vuota lo dice una volta, nel suo stato: in alto niente. */
  if (slip?.state === 'empty') return '';
  return statusText(slip?.state, slip?.at, now);
}
