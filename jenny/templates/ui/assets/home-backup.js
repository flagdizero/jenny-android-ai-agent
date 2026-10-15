/** La casa — «Backup».
 *
 *  Il giro c'e' gia' tutto e non e' di qui: `shared/backup-flow.js` — cifratura
 *  con passphrase, i due picker SAF del guscio nativo, il riavvio — lo usano
 *  gia' l'officina e l'onboarding. Qui c'e' la terza vista, e **una cosa che
 *  non esisteva**: la data.
 *
 *  «Ultimo backup: ieri alle 23:10» non aveva nessuna fonte. Non c'era un
 *  `last_backup` in nessun file: il gateway prepara il container cifrato in
 *  staging, e se quel file finisca su disco lo decide il picker di sistema,
 *  che risponde solo al client. Adesso il client lo dice
 *  (`/api/backup/exported`, scritto dal funnel di `store.mutate`), e lo dice
 *  **dopo** la schermata di sistema: fra le due cose c'e' un annullabile, e
 *  scrivere «ultimo backup: adesso» su un file mai salvato sarebbe la bugia
 *  peggiore di questa pagina.
 *
 *  **Due cose che si somigliano e non sono la stessa.** La storia locale degli
 *  snapshot e' automatica e serve a rimettere a posto una cosa cancellata per
 *  sbaglio — ma vive sullo stesso telefono, quindi di un telefono perso non
 *  salva niente. Un backup esportato si'. La riga porta il **secondo**, perche'
 *  e' quello che la parola «backup» promette; il primo ha la sua scheda qui
 *  sotto, che lo dice e lo sfoglia: un tocco su uno snapshot, una conferma, e
 *  lo stesso ripristino dell'officina. Accenderla, spegnerla e farne uno a
 *  mano restano in officina.
 */

import { i18n } from './shared/i18n.js';
import { whenText } from './shared/when.js';
import { api } from './shared/api-client.js';
import { escapeHtml } from './shared/utils.js';
import { confirmDialog } from './shared/dialog.js';
import {
  runExportFlow, runImportFlow, runSnapshotRestore, backupNativeAvailable,
} from './shared/backup-flow.js';

/* Quanti snapshot si vedono aprendo la storia: di solito si cerca «prima di
   stamattina», non tre settimane fa. Gli altri dietro «Mostra gli altri». */
const HISTORY_FIRST = 8;
const DAY_MS = 86400000;

const icon = (name) => `<i class="ti ti-${name}" aria-hidden="true"></i>`;

/** Quando e' stato fatto uno snapshot: «oggi alle 9:40», «martedì alle 9:40»;
 *  piu' vecchio di una settimana, giorno e ora («3 ott, 9:40») — due snapshot
 *  dello stesso giorno devono restare distinguibili. */
export function snapshotWhen(ms, now = Date.now(), locale = i18n.locale) {
  const at = new Date(Number(ms) || 0);
  const day0 = (d) => { const c = new Date(d); c.setHours(0, 0, 0, 0); return c.getTime(); };
  const days = Math.round((day0(now) - day0(at)) / DAY_MS);
  if (days >= 7) {
    return at.toLocaleString(locale, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
  }
  const time = at.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit' });
  if (days <= 0) return i18n.t('home.backup.history.today', { time });
  if (days === 1) return i18n.t('home.backup.history.yesterday', { time });
  return i18n.t('home.backup.history.weekday', {
    day: at.toLocaleDateString(locale, { weekday: 'long' }), time,
  });
}

/** La riga che si legge senza entrare: quando l'hai esportato l'ultima volta.
 *
 *  Mai fatto si dice, non si maschera: «mai» e' l'informazione piu' utile che
 *  questa riga possa portare, ed e' anche l'unico momento in cui serve
 *  davvero leggerla.
 */
export function backupValue(backup) {
  const when = Number(backup?.last_export_at) || 0;
  return when ? whenText(when * 1000) : i18n.t('home.backup.never');
}

export class HomeBackup {
  /** @param onExported  un export riuscito: la riga di «Tu e Jenny» si
   *                     riscrive senza aspettare la prossima apertura. */
  constructor({ onExported } = {}) {
    this.el = document.getElementById('home-backup-room');
    this.whenEl = document.getElementById('home-backup-when');
    this.exportBtn = document.getElementById('home-backup-export');
    this.exportNote = document.getElementById('home-backup-export-note');
    this.importBtn = document.getElementById('home-backup-import');
    this.importCard = document.getElementById('home-backup-import-card');
    this.importNote = document.getElementById('home-backup-import-note');
    this.historyBtn = document.getElementById('home-backup-history');
    this.historyList = document.getElementById('home-backup-history-list');

    this._onExported = onExported;
    this.backup = null;
    /* La storia locale: `null` chiusa, altrimenti `{snapshots, all, error}`. */
    this._history = null;

    this.exportBtn?.addEventListener('click', () => this.runExport());
    this.importBtn?.addEventListener('click', () => this.runImport());
    this.historyBtn?.addEventListener('click', () => this._toggleHistory());
    this.historyList?.addEventListener('click', (e) => this._onHistoryClick(e));
  }

  /** Quel che il server dice: `{last_export_at, snapshots_enabled}`. */
  setBackup(backup) {
    this.backup = backup || null;
    this._paint();
  }

  open() {
    this._paint();
  }

  /** Il valore per la riga di «Tu e Jenny». */
  value() {
    return backupValue(this.backup);
  }

  applyTranslations() {
    this._paint();
  }

  /** Esporta. Il giro intero — passphrase, cifratura, picker — sta nel flusso
   *  condiviso; di qui si guarda solo l'esito. */
  async runExport() {
    if (!this.exportBtn || this.exportBtn.disabled) return;
    this.exportBtn.disabled = true;
    try {
      const done = await runExportFlow();
      if (!done) return;
      /* Il record l'ha appena scritto il flusso: qui si aggiorna quel che si
         legge, senza richiedere l'intero payload delle impostazioni per un
         campo solo. La data e' quella del client e non quella scritta dal
         server — differiscono di un giro di rete, e «oggi alle 17:49» e'
         identico in tutti e due i casi. */
      this.backup = { ...(this.backup || {}), last_export_at: Date.now() / 1000 };
      this._paint();
      this._onExported?.();
    } finally {
      this.exportBtn.disabled = false;
    }
  }

  /** Importa. Non torna: a cose fatte il flusso apre il dialogo del riavvio,
   *  e l'app riparte con dentro quel che c'era nel file. */
  async runImport() {
    if (!this.importBtn || this.importBtn.disabled) return;
    this.importBtn.disabled = true;
    try {
      await runImportFlow();
    } finally {
      this.importBtn.disabled = false;
    }
  }

  /* ── La storia locale ────────────────────────────────────────────────── */

  async _toggleHistory() {
    if (this._history) {
      this._history = null;
      this._paint();
      return;
    }
    this._history = { snapshots: null, all: false, error: false };
    this._paint();
    try {
      const data = await api.getSnapshotHistory(100);
      if (this._history) this._history.snapshots = data.snapshots || [];
    } catch (err) {
      console.error('Snapshot history failed:', err);
      if (this._history) this._history.error = true;
    }
    this._paint();
  }

  /** Un tocco su uno snapshot: conferma, poi lo stesso ripristino
   *  dell'officina — staging, e lo scambio al riavvio. Su «Mostra gli altri»
   *  l'elenco si allunga. */
  async _onHistoryClick(event) {
    const target = event.target.closest('[data-snapshot], [data-history-all]');
    if (!target) return;
    if (target.dataset.historyAll !== undefined) {
      this._history.all = true;
      this._paint();
      return;
    }
    const ok = await confirmDialog(
      i18n.t('backup.snapshotRestoreConfirm', { date: target.dataset.when }),
    );
    if (ok) await runSnapshotRestore(target.dataset.snapshot);
  }

  _historyHtml() {
    const h = this._history;
    if (!h) return '';
    const empty = (key) => `<div class="home-history-empty">${escapeHtml(i18n.t(key))}</div>`;
    if (h.error) return empty('backup.snapshotHistoryUnavailable');
    if (!h.snapshots) return empty('home.backup.history.loading');
    if (!h.snapshots.length) return empty('backup.snapshotHistoryEmpty');
    const shown = h.all ? h.snapshots : h.snapshots.slice(0, HISTORY_FIRST);
    const now = Date.now();
    const rows = shown.map((s) => {
      const when = snapshotWhen(s.created_at_ms, now);
      const key = `backup.trigger.${s.trigger}`;
      const trigger = i18n.t(key) === key ? s.trigger : i18n.t(key);
      const detail = `${trigger}${s.label ? ` · ${s.label}` : ''} · ${s.file_count} ${i18n.t('backup.files')}`;
      return `<button class="home-history-snap" type="button" data-snapshot="${escapeHtml(s.id)}" data-when="${escapeHtml(when)}">`
        + `<span>${escapeHtml(when)}<small>${escapeHtml(detail)}</small></span>`
        + `${icon('restore')}</button>`;
    }).join('');
    const more = !h.all && h.snapshots.length > HISTORY_FIRST
      ? `<button class="home-history-snap is-more" type="button" data-history-all>${escapeHtml(i18n.t('home.backup.history.showAll', { n: h.snapshots.length - HISTORY_FIRST }))}</button>`
      : '';
    return rows + more;
  }

  /* ── Sotto ──────────────────────────────────────────────────────────── */

  _paint() {
    if (this.whenEl) {
      const when = Number(this.backup?.last_export_at) || 0;
      this.whenEl.textContent = when
        ? i18n.t('home.backup.last', { when: whenText(when * 1000) })
        : i18n.t('home.backup.neverLong');
      this.whenEl.classList.toggle('is-warn', !when);
    }
    if (this.exportBtn) this.exportBtn.textContent = i18n.t('home.backup.export');
    if (this.importBtn) this.importBtn.textContent = i18n.t('home.backup.import');
    if (this.exportNote) this.exportNote.textContent = i18n.t('home.backup.exportHint');
    if (this.importNote) this.importNote.textContent = i18n.t('home.backup.importHint');

    /* Fuori dall'APK i due picker non esistono, e un bottone che non fa niente
       e' peggio di un bottone che manca. La spiegazione di **cosa sia** un
       backup pero' resta: e' vera comunque, e sostituirla col motivo lascia
       una pagina che non dice piu' di cosa parla. La scheda del ripristino
       invece sparisce tutta — una nota senza il suo bottone e' una frase che
       promette un gesto che non c'e' (visto sul rig). */
    const native = backupNativeAvailable();
    if (this.exportBtn) this.exportBtn.hidden = !native;
    if (this.importBtn) this.importBtn.hidden = !native;
    if (this.importCard) this.importCard.hidden = !native;
    if (!native && this.exportNote) {
      this.exportNote.textContent = `${i18n.t('home.backup.exportHint')} ${i18n.t('backup.androidOnly')}`;
    }

    if (this.historyBtn) {
      /* La storia locale e' un'altra cosa, e somiglia abbastanza da essere
         scambiata per questa: la riga dice cos'e' e cosa **non** e'. Spenta,
         gli snapshot gia' fatti restano, e si sfogliano lo stesso. */
      const on = this.backup?.snapshots_enabled !== false;
      const sub = i18n.t(on ? 'home.backup.history.sub' : 'home.backup.history.off');
      this.historyBtn.innerHTML = `${icon('history')}<span>${escapeHtml(i18n.t('home.backup.history.title'))}`
        + `<small>${escapeHtml(sub)}</small></span>`
        + `<i class="ti ti-${this._history ? 'chevron-up' : 'chevron-down'} home-history-go" aria-hidden="true"></i>`;
      this.historyBtn.setAttribute('aria-expanded', String(!!this._history));
    }
    if (this.historyList) {
      this.historyList.hidden = !this._history;
      this.historyList.innerHTML = this._historyHtml();
    }
  }
}
