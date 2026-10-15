/** La casa — «Aggiornamenti».
 *
 *  La seconda vista di `shared/update-flow.js`, non una seconda copia: le
 *  fasi, il polling e i due casi che ingannano — la connessione che cade
 *  *perche'* l'app si sta riavviando, e il rifiuto che non deve sporcare la
 *  fase — stanno li', misurati da `test_update_flow_client.py`. Qui c'e' solo
 *  come si legge in casa: righe, non schede da officina.
 *
 *  **Il pallino dice il meccanismo, non la versione.** Verde non vuol dire
 *  «sei aggiornata», vuol dire «il controllo funziona»: un manifest
 *  irraggiungibile da un mese mostrerebbe altrimenti la stessa schermata di
 *  chi e' aggiornato davvero, e su un telefono che nessuno guarda quella
 *  differenza non la scopre piu' nessuno.
 */

import { api } from './shared/api-client.js';
import { i18n } from './shared/i18n.js';
import { showToast } from './shared/utils.js';
import { UpdateFlow, checkLines, phaseKey } from './shared/update-flow.js';

/** La riga che si legge senza entrare: «0.11.0 · aggiornata», oppure la
 *  versione che aspetta. Una versione che non si sa non si finge. */
export function updatesValue(version) {
  const v = version || {};
  if (!v.current) return '';
  if (v.update_available && v.latest) {
    return i18n.t('home.updates.waiting', { version: v.current, latest: v.latest });
  }
  /* «Aggiornata» solo dopo un controllo riuscito: su un'installazione nuova
     accanto a «il primo controllo non e' ancora partito» era una frase che
     nessuno aveva verificato (collaudo del 27/09/2026). */
  if (!hasChecked(v)) return i18n.t('home.updates.currentUnchecked', { version: v.current });
  return i18n.t('home.updates.current', { version: v.current });
}

/** C'e' stato almeno un controllo riuscito? Senza, «e' l'ultima» non lo sa
 *  nessuno. */
function hasChecked(version) {
  return (Number(version?.last_success) || 0) > 0;
}

/** Di che colore sta il meccanismo: `ok`, `warn`, `new`.
 *
 *  `warn` vince su `new`: se il controllo non arriva piu' al server, quel che
 *  sai di una versione nuova e' vecchio quanto l'ultimo esito positivo, e
 *  annunciarla come una novita' fresca sarebbe la cosa sbagliata da dire.
 */
export function updatesMood(version) {
  if (checkLines(version).some((row) => row.warn)) return 'warn';
  if (version?.update_available) return 'new';
  /* Verde vuol dire «il controllo funziona»: prima del primo esito positivo
     non lo si sa, e il pallino resta neutro. */
  return hasChecked(version) ? 'ok' : 'idle';
}

export class HomeUpdates {
  /** @param onVersion  un controllo ha portato una versione fresca: la riga di
   *                    «Tu e Jenny» e la cache del guscio si riscrivono. */
  constructor({ onVersion } = {}) {
    this.el = document.getElementById('home-updates-room');
    this.dot = document.getElementById('home-update-dot');
    this.headline = document.getElementById('home-update-headline');
    this.summary = document.getElementById('home-update-summary');
    this.notes = document.getElementById('home-update-notes');
    this.installBtn = document.getElementById('home-update-install');
    this.progress = document.getElementById('home-update-progress');
    this.note = document.getElementById('home-update-note');
    this.phase = document.getElementById('home-update-phase');
    this.detail = document.getElementById('home-update-detail');
    this.bar = document.getElementById('home-update-bar');
    this.track = document.getElementById('home-update-track');
    this.lines = document.getElementById('home-update-lines');
    this.checkBtn = document.getElementById('home-update-check');

    this._onVersion = onVersion;
    this.version = null;
    /* La generazione della stanza: sale uscendo. Ogni continuazione del flusso
       la cattura prima del primo `await` ed esce se e' cambiata. */
    this._gen = 0;

    this.flow = new UpdateFlow({
      generation: () => this._gen,
      onToast: (text, type) => showToast(text, type),
      onVersion: (v) => {
        if (v) this.version = v;
        this._onVersion?.(v);
      },
      onChange: () => this._paint(),
    });

    this.installBtn?.addEventListener('click', () => this.flow.start());
    this.checkBtn?.addEventListener('click', () => this.check());

    /* Da un altro dispositivo l'aggiornamento si vede ma non si installa: lo
       scarica e lo installa il telefono, e da fuori potrebbe aprire la sua
       conferma su uno schermo che nessuno guarda, o riavviare Jenny mentre le
       parli. Il server lo rifiuta comunque (`phone_only`). */
    this._remote = false;
    api.isRemote().then((remote) => {
      this._remote = remote;
      if (remote) this._paint();
    });
  }

  /** Quel che il server dice della versione. */
  setVersion(version) {
    this.version = version || null;
    this._paint();
  }

  open() {
    /* Un'installazione avviata va avanti per conto suo anche mentre guardavi
       un'altra stanza: rientrando si riaggancia il polling, o il bottone
       resterebbe spento e lo stato fermo all'ultima cosa vista. */
    this.flow.resume();
    this._paint();
  }

  /** Si esce. Il polling si ferma: la guardia di generazione ferma gia' le
   *  continuazioni, ma un timer vivo terrebbe sveglia una stanza che non c'e'
   *  piu'. */
  close() {
    this._gen += 1;
    this.flow.stop();
  }

  /** Il valore per la riga di «Tu e Jenny». */
  value() {
    return updatesValue(this.version);
  }

  async check() {
    this._paint();
    await this.flow.check();
    this._paint();
  }

  applyTranslations() {
    this._paint();
  }

  /* ── Sotto ──────────────────────────────────────────────────────────── */

  _paint() {
    this._paintState();
    this._paintProgress();
    this._paintLines();
  }

  _paintState() {
    const v = this.version || {};
    const mood = updatesMood(v);
    if (this.dot) this.dot.className = `home-update-dot is-${mood}`;
    if (this.headline) {
      /* Senza un numero non si dice «sei alla ultima»: si legge «Sei alla ,
         ed e' l'ultima», che e' una frase con un buco — visto sul rig, ed e'
         lo stato normale finche' `/api/settings` non ha risposto. Non sapere
         si dice, non si maschera. */
      let phrase;
      if (v.update_available) {
        phrase = i18n.t(
          v.critical ? 'settings.update.availableCritical' : 'settings.update.available',
          { version: v.latest || '' },
        );
      } else {
        phrase = !v.current
          ? i18n.t('home.updates.unknown')
          : i18n.t(hasChecked(v) ? 'home.updates.upToDate' : 'home.updates.onVersion',
            { version: v.current });
      }
      this.headline.textContent = phrase;
    }
    if (this.summary) {
      this.summary.textContent = (v.update_available && v.summary) ? v.summary : '';
      this.summary.hidden = !this.summary.textContent;
    }
    if (this.notes) {
      /* Link normale: la WebView devia le navigazioni fuori dal gateway locale
         su una Custom Tab, aprirlo dentro la SPA la sostituirebbe senza
         ritorno. */
      const url = v.update_available ? (v.notes_url || '') : '';
      this.notes.href = url;
      this.notes.textContent = url ? i18n.t('settings.update.notes') : '';
      this.notes.hidden = !url;
    }
    if (this.installBtn) {
      this.installBtn.hidden = !v.update_available;
      this.installBtn.disabled = this.flow.busy || this._remote;
      this.installBtn.textContent = i18n.t(this._remote ? 'settings.update.installOnPhone' : 'settings.update.install');
      this.installBtn.classList.toggle('is-critical', !!v.critical && !this._remote);
    }
  }

  _paintProgress() {
    const u = this.flow.state;
    if (!this.progress) return;
    this.progress.hidden = !u;
    if (!u) return;
    /* La nota sopravvive ai cambi di fase: dice cosa aspettarsi — un riavvio
       in arrivo, una conferma di sistema da dare — mentre la fase dice solo a
       che punto e'. */
    if (this.note) {
      this.note.textContent = u.noteKey ? i18n.t(u.noteKey) : '';
      this.note.hidden = !u.noteKey;
    }
    const key = phaseKey(u.phase);
    if (this.phase) {
      this.phase.textContent = key ? i18n.t(key) : '';
      this.phase.hidden = !key;
    }
    if (this.detail) {
      this.detail.textContent = u.detail || '';
      this.detail.hidden = !u.detail;
    }
    const running = (u.phase === 'downloading' || u.phase === 'installing') && u.progress > 0;
    if (this.track) this.track.hidden = !running;
    if (this.bar && running) {
      this.bar.style.width = `${Math.min(Math.max(u.progress, 0), 100)}%`;
    }
  }

  /* Il meccanismo che cerca gli aggiornamenti: c'e' sempre, anche — soprattutto
     — quando non c'e' niente da installare. */
  _paintLines() {
    if (this.lines) {
      this.lines.replaceChildren();
      for (const row of checkLines(this.version)) {
        const el = document.createElement('div');
        el.className = row.warn ? 'home-update-line is-warn' : 'home-update-line';
        el.textContent = i18n.t(row.key, row.params);
        this.lines.appendChild(el);
      }
    }
    if (this.checkBtn) {
      this.checkBtn.disabled = this.flow.checking;
      this.checkBtn.textContent = i18n.t(
        this.flow.checking ? 'settings.update.checking' : 'settings.update.checkNow',
      );
    }
  }
}
