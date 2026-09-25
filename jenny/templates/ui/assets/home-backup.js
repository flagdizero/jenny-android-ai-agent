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
 *  e' quello che la parola «backup» promette; il primo si dice dentro, in una
 *  frase.
 */

import { i18n } from './shared/i18n.js';
import { whenText } from './shared/when.js';
import { runExportFlow, runImportFlow, backupNativeAvailable } from './shared/backup-flow.js';

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
    this.snapNote = document.getElementById('home-backup-snapshots');

    this._onExported = onExported;
    this.backup = null;

    this.exportBtn?.addEventListener('click', () => this.runExport());
    this.importBtn?.addEventListener('click', () => this.runImport());
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

    if (this.snapNote) {
      /* La storia locale e' un'altra cosa, e somiglia abbastanza da essere
         scambiata per questa: si dice cos'e' e cosa **non** e'. */
      this.snapNote.textContent = i18n.t(
        this.backup?.snapshots_enabled ? 'home.backup.snapshots' : 'home.backup.snapshotsOff',
      );
    }
  }
}
