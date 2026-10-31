/** La copia su Google Drive nella pagina «Backup» della casa.
 *
 *  Disegno: la versione D della tela del 15/10/2026 («la frase della A, i
 *  pallini della B»). La scheda deve rispondere senza istruzioni a tre domande
 *  — *cosa fa*, *se va*, *da quanto non va* — e quando serve un gesto il bottone
 *  grande e' quel gesto. Uno stato alla volta, deciso da `cloudView()`
 *  (`cloud-backup.js`) con le sue regole di priorita': qui si disegna e basta.
 *  Mentre una copia gira si rilegge lo stato ogni 1,5 s, solo a stanza aperta:
 *  la barra avanza da sola.
 *
 *  La pagina e' del core (`home-backup.js`): questa estensione ci si aggancia
 *  con `attachExtension`, disegna la scheda sopra e le sue due righe (la
 *  password, lo spegnimento) in fondo alle «Altre opzioni», e dice la riga di
 *  «Tu e Jenny».
 */

import { i18n } from '../../shared/i18n.js';
import { whenText } from '../../shared/when.js';
import { escapeHtml, showToast } from '../../shared/utils.js';
import { confirmDialog } from '../../shared/dialog.js';
import { rowHtml } from '../../home-backup.js';
import {
  choosePassword, cloudAction, cloudErrorText, cloudNativeAvailable, cloudStatus, cloudView,
  copyNow, dayText, findMyCopy, giveConsentBack, nightStrip, progressSizes, restoreCopy, sizeText,
  turnOnDrive, unlockHere,
} from './cloud-backup.js';
import { CLOUD_PASSWORD_DIALOG_ID, askCloudPassword, pickCloudCopy } from './cloud-backup-dialogs.js';

const POLL_MS = 1500;

const icon = (name) => `<i class="ti ti-${name}" aria-hidden="true"></i>`;

/** La riga di «Tu e Jenny»: lo stato della copia in tre parole, o — copia
 *  spenta — quando hai esportato l'ultimo file. */
export function backupValue(backup, cloud = null, now = Date.now()) {
  const view = cloud ? cloudView(cloud, now) : { kind: 'unavailable' };
  switch (view.kind) {
    case 'safe': return i18n.t('cloudBackup.row.safe', { when: dayText(view.last, now) });
    case 'late': return i18n.t('cloudBackup.row.late', { days: view.days });
    case 'blocked':
      return i18n.t(view.reason === 'consent_needed' ? 'cloudBackup.row.consent' : 'cloudBackup.row.key');
    case 'nopass': return i18n.t('cloudBackup.row.nopass');
    case 'damaged': return i18n.t('cloudBackup.row.damaged');
    case 'first': return i18n.t('cloudBackup.row.first');
    case 'running': return i18n.t('cloudBackup.row.running');
    default: {
      const when = Number(backup?.last_export_at) || 0;
      return when ? whenText(when * 1000) : i18n.t('cloudBackup.row.onlyHere');
    }
  }
}

/** Il gancio sulla pagina del core: lo stato della copia e i suoi gesti. */
export class DriveBackupCard {
  /** @param page  l'`HomeBackup` del core (`home-backup.js`). */
  constructor(page) {
    this.page = page;
    /* Lo stato della copia (`/api/backup/cloud`), `null` finche' non arriva.
       `_step` e' il passo di un'accensione in corso, che non e' ancora uno
       stato del gateway; `_existing` dice che il Drive ha la copia di un altro
       telefono (si offre di ritrovarla invece di accendere). */
    this.cloud = null;
    this._step = null;
    this._existing = false;
    this._pollTimer = null;
    this._busy = false;
    /* La scheda e le righe si ridisegnano da capo a ogni stato: i loro
       bottoni si ascoltano dai contenitori, con `data-action`. */
    page.extCard?.addEventListener('click', (e) => this._onAction(e));
    page.extRows?.addEventListener('click', (e) => this._onAction(e));
  }

  /* ── I ganci della pagina ───────────────────────────────────────────── */

  open() {
    this.refreshCloud();
  }

  value(backup, now) {
    return backupValue(backup, this.cloud, now);
  }

  /** Aperte da sole quando la copia su Drive qui non si puo' fare: allora la
   *  copia su file e' la strada, e non deve stare dietro un tocco in piu'. */
  moreOpenByDefault() {
    return !!this.cloud && (!this.cloud.available || !cloudNativeAvailable());
  }

  paintCard(el, now) {
    /* Fuori dal guscio Android (un browser, il banco) il consenso e il gestore
       password non esistono: niente scheda che prometta un gesto che non c'e'. */
    const show = !!this.cloud && cloudNativeAvailable();
    if (show) el.innerHTML = this._cardHtml(this._view(now), now);
    this._schedulePoll();
    return show;
  }

  paintRows(el) {
    const view = this._view();
    /* «Spegni» c'e' solo con la copia accesa; la password vuole anche la
       chiave di qui (senza, il gesto e' scrivere quella che c'e', e lo fa la
       scheda). */
    const on = cloudNativeAvailable()
      && ['safe', 'late', 'blocked', 'first', 'damaged', 'nopass'].includes(view.kind);
    const row = (id, action, iconName, label, sub) => `<button class="home-backup-row is-flat" id="${id}" type="button" data-action="${action}">${rowHtml(iconName, label, sub)}</button>`;
    el.innerHTML = (on && this.cloud?.has_key
      ? row('home-cloud-password-row', 'setPassword', 'password',
        i18n.t('cloudBackup.more.password'), i18n.t('cloudBackup.more.passwordSub'))
      : '')
      + (on
        ? row('home-cloud-off', 'turnOff', 'cloud-off',
          i18n.t('cloudBackup.more.off'), i18n.t('cloudBackup.more.offSub'))
        : '');
  }

  /* ── Lo stato ───────────────────────────────────────────────────────── */

  /** Rilegge lo stato della copia; mentre gira, si riprogramma da solo. */
  async refreshCloud() {
    try {
      this.cloud = await cloudStatus();
    } catch {
      return;
    }
    this.page.refresh();
  }

  _schedulePoll() {
    clearTimeout(this._pollTimer);
    this._pollTimer = null;
    const running = this.cloud?.running || this._busy;
    /* Solo a stanza visibile: a pagina chiusa la copia va avanti lo stesso, e
       nessuno guarda la barra. */
    const visible = !!this.page.el && this.page.el.offsetParent !== null;
    if (running && visible) this._pollTimer = setTimeout(() => this.refreshCloud(), POLL_MS);
  }

  /* ── I gesti ─────────────────────────────────────────────────────────── */

  async _onAction(event) {
    const target = event.target.closest('[data-action]');
    if (!target || target.disabled || this._busy) return;
    const action = target.dataset.action;
    if (action === 'turnOff') {
      await this._turnOff();
      return;
    }
    this._busy = true;
    target.disabled = true;
    this._schedulePoll();
    try {
      if (action === 'turnOn') await this._turnOn();
      else if (action === 'copyNow') this._report(await copyNow());
      else if (action === 'giveBack') this._report(await giveConsentBack());
      else if (action === 'unlock') this._report(await unlockHere({ askPassword: askCloudPassword('type') }));
      else if (action === 'findCopy') await this._findCopy();
      else if (action === 'setPassword') await this._setPassword();
    } catch (err) {
      console.error('Cloud backup action failed:', err);
      showToast(cloudErrorText('generic'), 'error');
    } finally {
      this._busy = false;
      this._step = null;
      target.disabled = false;
      await this.refreshCloud();
    }
  }

  /** L'esito di un gesto: una copia riuscita si dice; un errore che la scheda
   *  spiega da se' (permesso, chiave) non si ripete in un toast. */
  _report(result) {
    if (!result || result.error === 'cancelled' || result.error === 'busy') return;
    if (result.ok) {
      showToast(i18n.t('cloudBackup.copied'));
      return;
    }
    if (!['consent_needed', 'missing_key', 'wrong_key'].includes(result.error)) {
      showToast(cloudErrorText(result.error), 'error');
    }
  }

  async _turnOn() {
    const result = await turnOnDrive({
      onStep: (step) => { this._step = step; this.page.refresh(); },
      askPassword: askCloudPassword('choose'),
    });
    if (!result.ok) {
      if (result.error === 'existing_backup') this._existing = true;
      else showToast(cloudErrorText(result.error), 'error');
      return;
    }
    this._existing = false;
    this._report({ ok: result.firstOk, error: result.error });
  }

  /** Sceglie la password (copia che non ne ha una) o la cambia. Con lo stato
   *  di adesso: «senza password» si sceglie, altrimenti si cambia. */
  async _setPassword() {
    const mode = this.cloud?.password_set_ms ? 'change' : 'choose';
    const result = await choosePassword({ askPassword: askCloudPassword(mode) });
    if (result.ok) showToast(i18n.t(`cloudBackup.password.${mode}.done`));
    else if (result.error !== 'cancelled' && result.error !== 'busy') {
      showToast(cloudErrorText(result.error), 'error');
    }
  }

  /** Su questo Drive c'e' la copia di un altro telefono: la si ritrova e ci si torna. */
  async _findCopy() {
    const found = await findMyCopy({ askPassword: askCloudPassword('type') });
    if (!found.ok) {
      if (found.error !== 'cancelled') showToast(cloudErrorText(found.error), 'error');
      return;
    }
    const chosen = await pickCloudCopy(found.snapshots || []);
    if (!chosen) return;
    const staged = await restoreCopy(chosen);
    if (!staged.ok) showToast(cloudErrorText(staged.error), 'error');
  }

  async _turnOff() {
    const yes = await confirmDialog(
      i18n.t('cloudBackup.turnOffConfirm'), i18n.t('cloudBackup.turnOff'),
    );
    if (!yes) return;
    try {
      await cloudAction('disable');
    } catch (err) {
      console.error('Cloud backup disable failed:', err);
    }
    await this.refreshCloud();
  }

  /* ── Il disegno ──────────────────────────────────────────────────────── */

  /** La vista da disegnare: quella del gateway, salvo un'accensione in corso
   *  (che il gateway non sa ancora) o un Drive con la copia di un altro telefono. */
  _view(now = Date.now()) {
    if (this._step && this._step !== 'first_copy') return { kind: 'step', tone: 'busy', step: this._step };
    const view = cloudView(this.cloud, now);
    if (view.kind === 'off' && this._existing) return { ...view, existing: true };
    return view;
  }

  _cardHtml(view, now) {
    const t = (key, params) => escapeHtml(i18n.t(key, params));
    const hero = (iconName, title, sub) => `
      <div class="home-cloud-hero">
        <div class="home-cloud-badge is-${view.tone}">${icon(iconName)}</div>
        <div><div class="home-cloud-title">${title}</div>${sub ? `<div class="home-cloud-sub">${sub}</div>` : ''}</div>
      </div>`;
    const action = (name) => `<button class="home-save home-cloud-action" type="button" data-action="${name}">${t(`cloudBackup.actions.${name}`)}</button>`;
    const note = (iconName, text) => `<div class="home-cloud-note">${icon(iconName)}<span>${text}</span></div>`;
    const box = (reason) => `<div class="home-cloud-box"><b>${t('cloudBackup.why')}</b> ${t(`cloudBackup.reasons.${reason}.why`)}<br><b>${t('cloudBackup.todo')}</b> ${t(`cloudBackup.reasons.${reason}.todo`)}</div>`;

    switch (view.kind) {
      case 'unavailable':
        return hero('cloud-off', t('cloudBackup.unavailable.title'), t('cloudBackup.unavailable.sub'));
      case 'off': {
        if (view.existing) {
          return hero('cloud-question', t('cloudBackup.off.title'), t('cloudBackup.off.sub'))
            + `<div class="home-cloud-box">${t('cloudBackup.errors.existing_backup')}</div>`
            + action('findCopy');
        }
        const icons = ['moon', 'lock', 'device-mobile'];
        const list = ['p1', 'p2', 'p3']
          .map((k, i) => `<li>${icon(icons[i])}<span>${t(`cloudBackup.off.${k}`)}</span></li>`).join('');
        return hero('cloud-off', t('cloudBackup.off.title'), t('cloudBackup.off.sub'))
          + `<ul class="home-cloud-list">${list}</ul>`
          + action('turnOn')
          + note('hand-finger', t('cloudBackup.off.note'));
      }
      case 'step':
        return hero('cloud-upload', t(`cloudBackup.steps.${view.step}`), '');
      case 'running': {
        const p = view.progress;
        const total = Number(p?.total_bytes) || 0;
        const done = Math.min(Number(p?.done_bytes) || 0, total);
        const pct = total ? Math.round((done / total) * 100) : 4;
        const sub = total
          ? t('cloudBackup.running.sub', progressSizes(done, total))
          : t('cloudBackup.running.subStart');
        return hero('cloud-upload', t(view.first ? 'cloudBackup.running.titleFirst' : 'cloudBackup.running.title'), sub)
          + `<div class="home-cloud-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="width:${pct}%"></i></div>`
          + note('door-exit', t('cloudBackup.running.note'));
      }
      case 'blocked':
        return hero(view.reason === 'consent_needed' ? 'cloud-lock' : 'lock-question', t('cloudBackup.blocked.title'),
          view.last ? t('cloudBackup.blocked.sub', { when: dayText(view.last, now) }) : t('cloudBackup.blocked.subNever'))
          + box(view.reason)
          + action(view.action)
          + note('heart', t('cloudBackup.blocked.calm'));
      case 'nopass':
        return hero('lock-open', t('cloudBackup.nopass.title'), t('cloudBackup.nopass.sub'))
          + `<div class="home-cloud-box">${t('cloudBackup.nopass.why')}</div>`
          + action('setPassword')
          + note('heart', t('cloudBackup.nopass.calm'));
      case 'late':
        return hero('cloud-exclamation', t('cloudBackup.late.title', { days: view.days }),
          view.last ? t('cloudBackup.late.sub', { when: dayText(view.last, now) }) : t('cloudBackup.late.subNever'))
          + this._stripHtml(now)
          + box(view.reason)
          + action('copyNow')
          + note('heart', t(view.alerted ? 'cloudBackup.late.calmAlerted' : 'cloudBackup.late.calm'));
      case 'damaged':
        return hero('cloud-exclamation', t('cloudBackup.damaged.title'),
          t('cloudBackup.damaged.sub', { when: dayText(view.last, now) }))
          + this._stripHtml(now)
          + box(view.reason)
          + action('copyNow')
          + note('heart', t('cloudBackup.late.calm'));
      case 'first':
        return hero('cloud', t('cloudBackup.first.title'),
          t(view.reason ? 'cloudBackup.first.subFailed' : 'cloudBackup.first.sub'))
          + (view.reason ? box(view.reason) : '')
          + action('copyNow')
          + note('message-circle', t('cloudBackup.promise'));
      default: { // safe
        const c = this.cloud || {};
        const inside = c.stored_bytes
          ? t('cloudBackup.facts.insideValue', { size: sizeText(c.stored_bytes) })
          : t('cloudBackup.facts.insideValueNoSize');
        /* Il controllo settimanale non ha una riga sua: quando va bene non e'
           un'informazione («al sicuro» lo dice gia'), quando va male cambia lo
           stato intero (`damaged`). */
        const fact = (iconName, label, value) => `<div class="home-cloud-fact">${icon(iconName)}<div><small>${label}</small>${value}</div></div>`;
        return hero('cloud-check', t('cloudBackup.safe.title'), t('cloudBackup.safe.sub', { when: dayText(view.last, now) }))
          + this._stripHtml(now)
          + '<div class="home-cloud-facts">'
          + fact('box', t('cloudBackup.facts.inside'), inside)
          + fact('folder', t('cloudBackup.facts.where'), t('cloudBackup.facts.whereValue'))
          + '</div>'
          + action('copyNow')
          + note('message-circle', t('cloudBackup.promise'));
      }
    }
  }

  _stripHtml(now) {
    const strip = nightStrip(this.cloud, now);
    const aria = escapeHtml(i18n.t('cloudBackup.strip.aria', { copies: strip.copies, missed: strip.missed }));
    const cells = strip.cells.map((c) => `
      <div class="home-cloud-day${c.today ? ' is-today' : ''}">
        <div class="home-cloud-dot is-${c.state}">${c.state === 'ok' ? icon('check') : ''}</div>${escapeHtml(c.label)}
      </div>`).join('');
    return `<div class="home-cloud-strip" role="img" aria-label="${aria}">${cells}</div>`
      + `<div class="home-cloud-caption">${escapeHtml(i18n.t('cloudBackup.strip.caption'))}</div>`;
  }
}

/** Il montaggio nella casa: la scheda sulla pagina Backup, il dialogo della
 *  password fra quelli che Indietro chiude, e lo stato letto subito — serve
 *  alla riga di «Tu e Jenny» anche a stanza chiusa. */
export function mountHome({ homeBackup, registerSharedDialog }) {
  if (!homeBackup) return;
  registerSharedDialog?.(CLOUD_PASSWORD_DIALOG_ID);
  const card = new DriveBackupCard(homeBackup);
  homeBackup.attachExtension(card);
  card.refreshCloud();
}
