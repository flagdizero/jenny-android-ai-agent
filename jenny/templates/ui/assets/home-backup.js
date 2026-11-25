/** La casa — «Backup».
 *
 *  Disegno: la versione D della tela del 15/10/2026. La pagina e' fatta di
 *  righe che dicono cosa fanno senza istruzioni:
 *
 *  - **Copia su un file**: il file cifrato con una password scelta
 *    dall'utente (`shared/backup-flow.js`, lo stesso giro di officina e
 *    onboarding). La sua data sta nella riga: «ultimo file: ieri alle 23:10».
 *    Quella data non aveva nessuna fonte; adesso il client la dice
 *    (`/api/backup/exported`, scritto dal funnel di `store.mutate`), e la dice
 *    **dopo** la schermata di sistema: fra le due cose c'e' un annullabile, e
 *    scrivere «adesso» su un file mai salvato sarebbe la bugia peggiore della
 *    pagina.
 *  - **Ripristina da un file**, che non torna: riavvia.
 *  - **La storia locale**, che si sfoglia e si ripristina qui. Somiglia
 *    abbastanza da essere scambiata per un backup, e non lo e': vive sullo
 *    stesso telefono, quindi di un telefono perso non salva niente. La riga lo
 *    dice. Accenderla, spegnerla e farne uno a mano restano in officina.
 *
 *  **Le estensioni.** Una build con un'estensione di backup (`jenny/extensions.py`)
 *  la aggancia con `attachExtension`: disegna una sua scheda sopra
 *  (`#home-backup-ext`), aggiunge le sue righe in fondo e puo' dire la riga di
 *  «Tu e Jenny». Allora le righe del core scendono sotto «Altre opzioni»,
 *  chiuse. Senza estensioni sono aperte, e il bottone «Altre opzioni» non c'e':
 *  non c'e' niente sopra di cui essere «altre».
 */

import { i18n } from './shared/i18n.js';
import { ago, dayOffset, nearDay, whenText } from './shared/when.js';
import { api } from './shared/api-client.js';
import { escapeHtml } from './shared/utils.js';
import { confirmDialog } from './shared/dialog.js';
import { iconHtml } from './shared/dom.js';
import {
  runExportFlow, runImportFlow, runSnapshotRestore, backupNativeAvailable, snapshotDetail,
} from './shared/backup-flow.js';

/* Quanti snapshot si vedono aprendo la storia: di solito si cerca «prima di
   stamattina», non tre settimane fa. Gli altri dietro «Mostra gli altri». */
const HISTORY_FIRST = 8;

/** Quando e' stato fatto uno snapshot: «oggi alle 9:40», «martedì alle 9:40»;
 *  piu' vecchio di una settimana, giorno e ora («3 ott, 9:40») — due snapshot
 *  dello stesso giorno devono restare distinguibili. */
export function snapshotWhen(ms, now = Date.now(), locale = i18n.locale) {
  const at = new Date(Number(ms) || 0);
  if (dayOffset(at, now) >= 7) {
    return at.toLocaleString(locale, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
  }
  return nearDay(at, { now, locale });
}

/** Dopo quanti giorni un file di backup e' «vecchio». Il file e' l'unica
 *  difesa da un telefono perso dove non c'e' una copia automatica: la storia
 *  locale vive sullo stesso telefono. */
export const STALE_DAYS = 30;

/** Da quanti giorni l'ultimo file e' vecchio, oppure 0: fatto da meno di
 *  `STALE_DAYS`, o mai fatto. Mai fatto non si colora: il primo giorno
 *  sarebbe un rimprovero, e la riga dice gia' «mai». */
export function staleDays(backup, now = Date.now()) {
  const when = Number(backup?.last_export_at) || 0;
  if (!when) return 0;
  const days = dayOffset(when * 1000, now);
  return days > STALE_DAYS ? days : 0;
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

/** Una riga della pagina: icona, titolo, sottotitolo, e il segno a destra.
 *  Esportata perche' le righe di un'estensione siano uguali alle nostre. */
export function rowHtml(iconName, label, sub, go = 'chevron-right') {
  return `${iconHtml(iconName)}<span>${escapeHtml(label)}${sub ? `<small>${escapeHtml(sub)}</small>` : ''}</span>`
    + `<i class="ti ti-${go} home-backup-go" aria-hidden="true"></i>`;
}

export class HomeBackup {
  /** @param onExported  qualcosa e' cambiato (un file salvato, lo stato di
   *                     un'estensione): la riga di «Tu e Jenny» si riscrive
   *                     senza aspettare la prossima apertura. */
  constructor({ onExported } = {}) {
    this.el = document.getElementById('home-backup-room');
    this.extCard = document.getElementById('home-backup-ext');
    this.extRows = document.getElementById('home-backup-ext-rows');
    this.moreToggle = document.getElementById('home-backup-more-toggle');
    this.more = document.getElementById('home-backup-more');
    this.exportBtn = document.getElementById('home-backup-export');
    this.importBtn = document.getElementById('home-backup-import');
    this.historyBtn = document.getElementById('home-backup-history');
    this.staleCard = document.getElementById('home-backup-stale');
    this.historyList = document.getElementById('home-backup-history-list');

    this._onExported = onExported;
    this.backup = null;
    /* La storia locale: `null` chiusa, altrimenti `{snapshots, all, error}`. */
    this._history = null;
    /* «Altre opzioni» aperte o chiuse a mano; `null` = la scelta di default. */
    this._moreOpen = null;
    /* L'estensione agganciata (v. `attachExtension`), e se la sua scheda si vede. */
    this._ext = null;
    this._extShown = false;
    /* I salvataggi locali dopo l'ultimo file (v. `_countSince`), e per quale
       file sono stati contati. */
    this._since = null;
    this._sinceFor = null;

    this.moreToggle?.addEventListener('click', () => this._toggleMore());
    this.exportBtn?.addEventListener('click', () => this.runExport());
    this.importBtn?.addEventListener('click', () => this.runImport());
    this.historyBtn?.addEventListener('click', () => this._toggleHistory());
    this.historyList?.addEventListener('click', (e) => this._onHistoryClick(e));
    this.staleCard?.addEventListener('click', (e) => {
      if (e.target.closest('[data-action="export"]')) this.runExport();
    });
  }

  /** Aggancia un'estensione di backup. Tutti i metodi sono facoltativi:
   *
   *  - `paintCard(el, now)` disegna la scheda in `el` e dice se mostrarla;
   *  - `paintRows(el)` disegna le sue righe in fondo alle «Altre opzioni»;
   *  - `value(backup, now)` la riga di «Tu e Jenny», o `null` per la nostra;
   *  - `moreOpenByDefault()` se le «Altre opzioni» partono aperte;
   *  - `open()` la stanza si e' aperta (per rileggere uno stato);
   *  - `coversBackup(now)` se l'estensione fa gia' le copie da sola: allora
   *    un file vecchio non e' un problema, e l'avviso tace;
   *  - `warn(now)` se la riga di «Tu e Jenny» va colorata per conto suo.
   *
   *  Quando il suo stato cambia, l'estensione chiama `refresh()`. */
  attachExtension(ext) {
    this._ext = ext || null;
    this.refresh();
  }

  /** Ridisegna e riscrive la riga di «Tu e Jenny». */
  refresh() {
    this._paint();
    this._onExported?.();
  }

  /** Quel che il server dice: `{last_export_at, snapshots_enabled}`. */
  setBackup(backup) {
    this.backup = backup || null;
    this._paint();
  }

  open() {
    this._paint();
    this._ext?.open?.();
    this._countSince();
  }

  /** Da quanti giorni il file e' vecchio, se conta (v. `staleDays`). Con
   *  un'estensione che copia da sola, non conta. */
  stale(at) {
    const now = at || Date.now();
    if (this._ext?.coversBackup?.(now)) return 0;
    return staleDays(this.backup, now);
  }

  /** Il valore per la riga di «Tu e Jenny». Un file vecchio si dice in
   *  giorni, «47 giorni fa», non con una data: e' il numero che conta. */
  value() {
    const now = Date.now();
    if (this.stale(now)) return ago(Number(this.backup.last_export_at), now);
    return this._ext?.value?.(this.backup, now) || backupValue(this.backup);
  }

  /** Se la riga di «Tu e Jenny» va colorata: il file e' vecchio, o
   *  l'estensione ha un suo motivo. */
  warn() {
    const now = Date.now();
    return this.stale(now) > 0 || !!this._ext?.warn?.(now);
  }

  /* Quanti salvataggi della storia locale sono venuti dopo l'ultimo file:
     e' quel che un telefono perso si porterebbe via. Solo con il file
     vecchio, e una lettura fallita non dice niente invece di dire zero. */
  async _countSince() {
    const exported = Number(this.backup?.last_export_at) || 0;
    if (!this.stale() || this._sinceFor === exported) return;
    this._sinceFor = exported;
    this._since = null;
    try {
      const data = await api.getSnapshotHistory(200);
      const since = (data?.snapshots || [])
        .filter((s) => Number(s.created_at_ms) > exported * 1000).length;
      if (this._sinceFor === exported) this._since = since;
    } catch (err) {
      console.warn('home: snapshot count failed', err);
    }
    this._paint();
  }

  applyTranslations() {
    this._paint();
  }

  /** Esporta. Il giro intero — password, cifratura, picker — sta nel flusso
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
      this.refresh();
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
      const detail = snapshotDetail(s);
      return `<button class="home-history-snap" type="button" data-snapshot="${escapeHtml(s.id)}" data-when="${escapeHtml(when)}">`
        + `<span>${escapeHtml(when)}<small>${escapeHtml(detail)}</small></span>`
        + `${iconHtml('restore')}</button>`;
    }).join('');
    const more = !h.all && h.snapshots.length > HISTORY_FIRST
      ? `<button class="home-history-snap is-more" type="button" data-history-all>${escapeHtml(i18n.t('home.backup.history.showAll', { n: h.snapshots.length - HISTORY_FIRST }))}</button>`
      : '';
    return rows + more;
  }

  /* ── Il file vecchio ─────────────────────────────────────────────────── */

  /* In cima alla stanza: da quanti giorni, cosa e' cambiato da allora, e il
     gesto. Niente notifiche: il colore della riga e questa frase bastano. */
  _paintStale() {
    if (!this.staleCard) return;
    const days = this.stale();
    this.staleCard.hidden = !days;
    if (!days) {
      this.staleCard.innerHTML = '';
      return;
    }
    const sub = this._since
      ? i18n.t('home.backup.stale.since', { n: this._since })
      : i18n.t('home.backup.stale.sub');
    const button = backupNativeAvailable()
      ? `<button class="home-backup-stale-go" type="button" data-action="export">${iconHtml('file-download')}<span>${escapeHtml(i18n.t('home.backup.stale.go'))}</span></button>`
      : '';
    this.staleCard.innerHTML = `<div class="home-backup-stale-head">${iconHtml('alert-triangle')}`
      + `<strong>${escapeHtml(i18n.t('home.backup.stale.title', { days }))}</strong></div>`
      + `<p>${escapeHtml(sub)}</p>${button}`;
  }

  /* ── «Altre opzioni» ─────────────────────────────────────────────────── */

  _toggleMore() {
    this._moreOpen = !this._isMoreOpen();
    this._paint();
  }

  _isMoreOpen() {
    /* Senza una scheda sopra, le righe sono la pagina: sempre aperte. */
    if (!this._extShown) return true;
    if (this._moreOpen !== null) return this._moreOpen;
    return !!this._ext?.moreOpenByDefault?.();
  }

  /* ── Il disegno ──────────────────────────────────────────────────────── */

  _paint() {
    this._paintStale();
    if (this.extCard) {
      this._extShown = !!this._ext?.paintCard?.(this.extCard, Date.now());
      this.extCard.hidden = !this._extShown;
      if (!this._extShown) this.extCard.innerHTML = '';
    }
    const open = this._isMoreOpen();
    if (this.moreToggle) {
      this.moreToggle.hidden = !this._extShown;
      this.moreToggle.innerHTML = rowHtml('dots', i18n.t('home.backup.more.title'), '',
        open ? 'chevron-up' : 'chevron-down');
      this.moreToggle.setAttribute('aria-expanded', String(open));
    }
    if (this.more) this.more.hidden = !open;

    /* Fuori dall'APK i due picker non esistono, e una riga che non fa niente
       e' peggio di una riga che manca. */
    const native = backupNativeAvailable();
    const exported = Number(this.backup?.last_export_at) || 0;
    if (this.exportBtn) {
      const sub = i18n.t('home.backup.more.fileSub')
        + (exported ? ` ${i18n.t('home.backup.more.fileLast', { when: whenText(exported * 1000) })}` : '');
      this.exportBtn.innerHTML = rowHtml('file-download', i18n.t('home.backup.more.file'), sub);
      this.exportBtn.hidden = !native;
    }
    if (this.importBtn) {
      this.importBtn.innerHTML = rowHtml('file-upload', i18n.t('home.backup.more.restore'),
        i18n.t('home.backup.more.restoreSub'));
      this.importBtn.hidden = !native;
    }
    if (this.historyBtn) {
      /* La storia locale e' un'altra cosa, e somiglia abbastanza da essere
         scambiata per questa: la riga dice cos'e' e cosa **non** e'. Spenta,
         gli snapshot gia' fatti restano, e si sfogliano lo stesso. */
      const on = this.backup?.snapshots_enabled !== false;
      this.historyBtn.innerHTML = rowHtml('history', i18n.t('backup.snapshotHistory'),
        i18n.t(on ? 'home.backup.history.sub' : 'home.backup.history.off'),
        this._history ? 'chevron-up' : 'chevron-down');
      this.historyBtn.setAttribute('aria-expanded', String(!!this._history));
    }
    if (this.historyList) {
      this.historyList.hidden = !this._history;
      this.historyList.innerHTML = this._historyHtml();
    }
    if (this.extRows) {
      if (this._ext?.paintRows) this._ext.paintRows(this.extRows);
      else this.extRows.innerHTML = '';
    }
  }
}
