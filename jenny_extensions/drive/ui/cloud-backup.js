/** La copia su Google Drive: i flussi che la casa e l'onboarding condividono.
 *
 *  Il gateway fa il lavoro (`/api/backup/cloud*`, `jenny_extensions/drive/`);
 *  il nativo fa una cosa sola, quella che vuole una schermata di sistema:
 *    connectDrive() → consenso Google → window.jennyCloud.onDriveConnected(stato)
 *  (`DriveExtension.kt`, raggiunto dal comando generico `extensionCall`).
 *
 *  Questo file e' dell'estensione Drive e la build senza estensioni non lo
 *  contiene: importa i moduli del core per percorso relativo, che il browser
 *  risolve sugli stessi URL della shell (`/html-mobile/assets/shared/…`), quindi
 *  sulle stesse istanze — lo stesso `api` col suo segreto, lo stesso `i18n`.
 *
 *  La copia si apre con una **password che sceglie l'utente**: la chiede una
 *  volta all'accensione e poi solo su un telefono nuovo. Le domande le fa chi
 *  chiama, passando `askPassword` — un dialogo (`cloud-backup-dialogs.js`) che
 *  riceve `submit(password)` e resta aperto finché la risposta non va bene:
 *  qui ci sono solo i passi, che si provano senza DOM.
 *
 *  Le parole sono la parte che conta. L'utente non legge mai «token», «scope»,
 *  «repository» o «chiave master»: legge «la copia», «il permesso di Google»,
 *  «la password», e un errore è sempre una frase che dice cosa fare.
 */

import { api } from '../../shared/api-client.js';
import { i18n } from '../../shared/i18n.js';
import { showRestartDialog, NATIVE_ANSWER_GRACE_MS } from '../../shared/backup-flow.js';

/** Il nome dell'estensione, lato gateway e lato nativo. */
const EXTENSION = 'drive';

/* ── Il client di `/api/backup/cloud*` ─────────────────────────────────── */

/** `{available, provider, has_key, running, last_success_ms, last_error, …}`. */
export async function cloudStatus() {
  const res = await api._fetch('/api/backup/cloud');
  if (!res.ok) throw new Error(`Cloud status failed: ${res.status}`);
  return res.json();
}

/** Un'azione senza argomenti: `enable` (senza password), `disable`, `run`,
 *  `verify`, `remote`. Torna sempre `{ok, error?, …}`. */
export async function cloudAction(name) {
  const res = await api._fetch(`/api/backup/cloud/${name}`);
  if (!res.ok) throw new Error(`Cloud ${name} failed: ${res.status}`);
  return res.json();
}

/** Le tre azioni con la password: `enable` (accendere con una chiave nuova),
 *  `password` (sceglierla o cambiarla), `unlock` (il telefono nuovo). La
 *  password viaggia nell'header come la passphrase del file, mai nell'URL. */
export function cloudWithPassword(name, password) {
  return api._backupPost(`/api/backup/cloud/${name}`, { password });
}

export function cloudRestore(snapshotId) {
  return api._backupPost('/api/backup/cloud/restore', { snapshot_id: snapshotId });
}

const CALLBACKS = ['onDriveConnected'];
const _waiting = {};

window.jennyCloud = Object.fromEntries(CALLBACKS.map((name) => [name, (value) => {
  const cb = _waiting[name];
  _waiting[name] = null;
  if (cb) cb(String(value));
}]));

/** Tre giorni senza una copia riuscita: la riga lo dice in giallo (e Jenny
 *  in chat, dal cron). Stessa soglia di `ALERT_AFTER_MS` nel gateway. */
export const CLOUD_LATE_MS = 72 * 3600 * 1000;

/** La password più corta che si accetta: `PASSWORD_MIN_LEN` in `cloud.py`. Il
 *  gateway la ricontrolla; qui serve a dirlo prima di mandarla. */
export const CLOUD_PASSWORD_MIN = 8;

/** Siamo nel guscio Android? Questo file arriva solo nell'APK `play`, che ha
 *  sempre anche la parte nativa del Drive (`DriveExtension.kt`): basta sapere
 *  che il ponte c'e'. Fuori (un browser via Tailscale, il banco) no. */
export function cloudNativeAvailable() {
  return typeof window.JennyNative?.extensionCall === 'function';
}

/** Chiama *method* sul nativo e aspetta *callback*. Come `_awaitNative` di
 *  backup-flow: con una schermata di sistema davanti la pagina è nascosta e si
 *  aspetta quanto serve; tornati visibili, se entro il margine non risponde
 *  nessuno, vale *fallback*. */
function _native(method, callback, fallback) {
  return new Promise((resolve) => {
    let timer = null;
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      if (_waiting[callback] === finish) _waiting[callback] = null;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
      resolve(value);
    };
    const arm = () => {
      clearTimeout(timer);
      timer = setTimeout(() => finish(fallback), NATIVE_ANSWER_GRACE_MS);
    };
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') clearTimeout(timer);
      else arm();
    };
    _waiting[callback] = finish;
    document.addEventListener('visibilitychange', onVisibility);
    arm();
    try {
      window.JennyNative.extensionCall(EXTENSION, method);
    } catch (err) {
      console.error(`${method} failed to start:`, err);
      finish(fallback);
    }
  });
}

let _busy = false;
async function _exclusive(fn) {
  if (_busy) return { ok: false, error: 'busy' };
  _busy = true;
  try { return await fn(); } finally { _busy = false; }
}

export function connectDrive() {
  return _native('connectDrive', 'onDriveConnected', 'denied');
}

/** La frase per un codice d'errore del gateway o del nativo. */
export function cloudErrorText(code) {
  const key = String(code || '').split(':')[0];
  const known = i18n.t(`cloudBackup.errors.${key}`);
  return known && known !== `cloudBackup.errors.${key}` ? known : i18n.t('cloudBackup.errors.generic');
}

/** Quel che rimanda la domanda della password invece di chiuderla: la si
 *  corregge e si riprova, senza perdere quel che si era scritto. */
export const PASSWORD_RETRY = ['wrong_password', 'weak_password'];

/** Accende la copia: permesso → (password, se la copia non ne ha una) →
 *  prima copia. *onStep(nome)* racconta a che punto si è: `connecting`,
 *  `first_copy`. *askPassword(submit)* apre la scelta della password e
 *  risolve con l'esito di `submit`, o `null` se si rinuncia.
 *  Risolve `{ok, error?, firstOk?, status?}`. */
export function turnOnDrive({ onStep, askPassword } = {}) {
  return _exclusive(async () => {
    onStep?.('connecting');
    const consent = await connectDrive();
    if (consent !== 'granted') return { ok: false, error: consent === 'unavailable' ? 'unavailable' : 'denied' };
    let enabled = await cloudAction('enable');
    /* Una chiave nuova nasce solo insieme alla sua password; una copia
       riaccesa che la password non ce l'ha (o l'ha persa) la riceve adesso. */
    const name = enabled.error === 'password_needed' ? 'enable'
      : enabled.ok && !enabled.password_set_ms ? 'password' : null;
    if (name) {
      const chosen = await askPassword?.((password) => cloudWithPassword(name, password));
      if (!chosen) return { ok: false, error: 'cancelled' };
      enabled = chosen;
    }
    if (!enabled.ok) return enabled;
    onStep?.('first_copy');
    const first = await cloudAction('run');
    return { ok: true, firstOk: !!first.ok, error: first.ok ? '' : first.error, status: first };
  });
}

/** Sceglie o cambia la password della copia. La vecchia non serve. */
export function choosePassword({ askPassword } = {}) {
  return _exclusive(async () => {
    const chosen = await askPassword?.((password) => cloudWithPassword('password', password));
    return chosen || { ok: false, error: 'cancelled' };
  });
}

/** Il permesso di Google tolto: lo si richiede, e se torna si fa subito una copia. */
export function giveConsentBack() {
  return _exclusive(async () => {
    const consent = await connectDrive();
    if (consent !== 'granted') return { ok: false, error: 'denied' };
    return cloudAction('run');
  });
}

/** Una copia adesso, col dito. */
export function copyNow() {
  return _exclusive(() => cloudAction('run'));
}

/** L'elenco delle copie, chiedendo la password se questo telefono non ha la
 *  chiave che le apre (telefono nuovo, o la copia l'ha rifatta un altro). */
async function _listWithPassword(askPassword) {
  const remote = await cloudAction('remote');
  if (remote.ok || !['missing_key', 'wrong_key'].includes(remote.error)) return remote;
  const opened = await askPassword?.((password) => cloudWithPassword('unlock', password));
  if (!opened) return { ok: false, error: 'cancelled' };
  if (!opened.ok) return opened;
  return cloudAction('remote');
}

/** Telefono nuovo: permesso, password, elenco. Risolve `{ok, snapshots}` o
 *  `{ok: false, error}`. */
export function findMyCopy({ askPassword } = {}) {
  return _exclusive(async () => {
    const consent = await connectDrive();
    if (consent !== 'granted') return { ok: false, error: consent === 'unavailable' ? 'unavailable' : 'denied' };
    return _listWithPassword(askPassword);
  });
}

/** La copia ferma perché qui manca la chiave (stato «ferma»): la password la
 *  riapre, e la copia riprende. Non ripristina niente. */
export function unlockHere({ askPassword } = {}) {
  return _exclusive(async () => {
    const listed = await _listWithPassword(askPassword);
    if (!listed.ok) return listed;
    return cloudAction('run');
  });
}

/** Ripristina la copia *snapshotId* e chiede il riavvio. */
export function restoreCopy(snapshotId) {
  return _exclusive(async () => {
    const staged = await cloudRestore(snapshotId);
    if (!staged.ok) return staged;
    showRestartDialog();
    return staged;
  });
}

/* ── Lo stato in parole e in pallini ────────────────────────────────────
   Funzioni pure: lo stato del gateway e l'ora entrano, la descrizione esce.
   La pagina disegna e basta — e queste si provano senza DOM. */

/** Gli errori che Jenny non può risolvere da sola: la scheda diventa rossa e
 *  il bottone grande è il gesto che serve. */
export const CLOUD_BLOCKING = ['consent_needed', 'missing_key', 'wrong_key'];

const DAY_MS = 86400000;

function _startOfDay(ms) {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/** Giorni di calendario fra due istanti (non 24 h: «domenica → giovedì» è 4). */
export function calendarDays(fromMs, toMs) {
  return Math.round((_startOfDay(toMs) - _startOfDay(fromMs)) / DAY_MS);
}

/** Il motivo da mostrare, fra quelli che la pagina sa spiegare. */
export function reasonKey(error) {
  const code = String(error || '').split(':')[0];
  if (!code) return 'waiting';
  if (code === 'wrong_key' || code === 'missing_key') return 'missing_key';
  if (code === 'consent_needed' || code === 'verify_mismatch') return code;
  return 'other';
}

/** Quale stato vale adesso, in ordine di priorità (il primo che vale vince):
 *  non disponibile → spenta → in corso → ferma → senza password → in ritardo →
 *  prima copia → copia rovinata → al sicuro.
 *  Un errore conta solo se è più recente dell'ultima copia riuscita: una notte
 *  andata male e poi rimediata non è più un problema. */
export function cloudView(status, now = Date.now()) {
  const s = status || {};
  if (!s.available) return { kind: 'unavailable', tone: 'off' };
  if (s.provider !== 'drive') return { kind: 'off', tone: 'off' };
  const last = Number(s.last_success_ms) || 0;
  const failedAt = Number(s.last_failure_ms) || 0;
  const error = failedAt > last ? String(s.last_error || '') : '';
  const code = error.split(':')[0];
  if (s.running) return { kind: 'running', tone: 'busy', first: !last, progress: s.progress || null };
  if (CLOUD_BLOCKING.includes(code)) {
    return {
      kind: 'blocked', tone: 'err', last, reason: reasonKey(code),
      action: code === 'consent_needed' ? 'giveBack' : 'unlock',
    };
  }
  /* La copia gira ma non ha una password (installazioni di prima, o `key.jbk`
     tolto da Drive): qui va tutto, ma un telefono nuovo non la riaprirebbe. Non
     è rossa — non si è perso niente — ed è prima del ritardo perché è la cosa
     che, se succede il guaio, conta. */
  if (s.has_key && !Number(s.password_set_ms)) {
    return { kind: 'nopass', tone: 'warn', last, action: 'setPassword' };
  }
  const since = last || Number(s.enabled_at_ms) || now;
  if (now - since >= CLOUD_LATE_MS) {
    return {
      kind: 'late', tone: 'warn', last, days: Math.max(3, calendarDays(since, now)),
      reason: reasonKey(code), action: 'copyNow',
      alerted: (Number(s.alerted_ms) || 0) > since,
    };
  }
  if (!last) return { kind: 'first', tone: 'soft', reason: code ? reasonKey(code) : '', action: 'copyNow' };
  /* Il controllo settimanale ha riscaricato la copia e non era a posto: non
     si dice «al sicuro». Un controllo solo *non finito* (rete) non conta —
     lo si rifà, e intanto la copia non risulta rotta. */
  if (code === 'verify_mismatch') {
    return { kind: 'damaged', tone: 'warn', last, reason: 'verify_mismatch', action: 'copyNow' };
  }
  return { kind: 'safe', tone: 'ok', last, action: 'copyNow' };
}

/** Le ultime 7 notti: i giorni di calendario del telefono, da 6 giorni fa a oggi.
 *  `ok` c'è stata almeno una copia · `miss` no · `today` oggi, ancora senza
 *  copia (la notte non è passata) · `before` la copia non era ancora accesa. */
export function nightStrip(status, now = Date.now(), locale = i18n.locale) {
  const copies = (status?.recent_copies_ms || []).map(Number).filter(Boolean);
  /* Da quando c'è la copia: l'accensione, o la copia più vecchia che si conosce
     se è più vecchia ancora (un telefono ripristinato non sa quando fu accesa). */
  const known = [Number(status?.enabled_at_ms) || 0, ...copies].filter(Boolean);
  const enabledDay = _startOfDay(known.length ? Math.min(...known) : now);
  const weekday = new Intl.DateTimeFormat(locale, { weekday: 'short' });
  const cells = [];
  for (let back = 6; back >= 0; back -= 1) {
    const day = new Date(now);
    day.setHours(0, 0, 0, 0);
    day.setDate(day.getDate() - back);
    const start = day.getTime();
    const next = new Date(start);
    next.setDate(next.getDate() + 1);
    const copied = copies.some((t) => t >= start && t < next.getTime());
    const today = back === 0;
    let state = 'miss';
    if (copied) state = 'ok';
    else if (start < enabledDay) state = 'before';
    else if (today) state = 'today';
    cells.push({
      state, today,
      label: today ? i18n.t('cloudBackup.strip.today') : weekday.format(day).replace('.', ''),
    });
  }
  return {
    cells,
    copies: cells.filter((c) => c.state === 'ok').length,
    missed: cells.filter((c) => c.state === 'miss').length,
  };
}

/** «oggi alle 3:12» · «ieri alle 3:12» · «domenica alle 3:12» · «3 ott». */
export function dayText(ms, now = Date.now(), locale = i18n.locale) {
  const at = new Date(Number(ms) || 0);
  const time = at.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit' });
  const days = calendarDays(at.getTime(), now);
  if (days <= 0) return i18n.t('cloudBackup.when.today', { time });
  if (days === 1) return i18n.t('cloudBackup.when.yesterday', { time });
  if (days < 7) {
    const day = at.toLocaleDateString(locale, { weekday: 'long' });
    return i18n.t('cloudBackup.when.weekday', { day, time });
  }
  return at.toLocaleDateString(locale, { day: 'numeric', month: 'short' });
}

/** «5,1 MB» e «8,4 MB» per la barra: tutti e due nell'unità del totale, così
 *  si leggono come «quanto manca» (e zero si scrive 0, non «1 kB»). */
export function progressSizes(done, total, locale = i18n.locale) {
  const t = Number(total) || 0;
  const [div, unit] = t >= 1e9 ? [1e9, 'GB'] : t >= 1e6 ? [1e6, 'MB'] : [1e3, 'kB'];
  const fmt = (v) => `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(v / div)} ${unit}`;
  return { done: fmt(Math.min(Number(done) || 0, t)), total: fmt(t) };
}

/** «8,4 MB», nel formato numerico della lingua. */
export function sizeText(bytes, locale = i18n.locale) {
  const n = Number(bytes) || 0;
  const fmt = (v, unit) => `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(v)} ${unit}`;
  if (n >= 1e9) return fmt(n / 1e9, 'GB');
  if (n >= 1e6) return fmt(n / 1e6, 'MB');
  return fmt(Math.max(1, Math.round(n / 1e3)), 'kB');
}
