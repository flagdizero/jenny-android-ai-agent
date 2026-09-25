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

import { i18n } from './shared/i18n.js';
import { showToast } from './shared/utils.js';
import { UpdateFlow, checkLines, phaseKey } from './shared/update-flow.js';

/** La riga che si legge senza entrare: «0.11.0 · aggiornata», oppure la
 *  versione che aspetta. Una versione che non si sa non si finge. */
export function updatesValue(version) {
  const v = version || {};
  if (!v.current) return '';
  if (v.update_available && v.latest) {
    return i18n.t('casa.updates.waiting', { version: v.current, latest: v.latest });
  }
  return i18n.t('casa.updates.current', { version: v.current });
}

/** Di che colore sta il meccanismo: `ok`, `warn`, `new`.
 *
 *  `warn` vince su `new`: se il controllo non arriva piu' al server, quel che
 *  sai di una versione nuova e' vecchio quanto l'ultimo esito positivo, e
 *  annunciarla come una novita' fresca sarebbe la cosa sbagliata da dire.
 */
export function updatesMood(version) {
  if (checkLines(version).some((riga) => riga.warn)) return 'warn';
  return version?.update_available ? 'new' : 'ok';
}

export class HomeUpdates {
  /** @param onVersion  un controllo ha portato una versione fresca: la riga di
   *                    «Tu e Jenny» e la cache del guscio si riscrivono. */
  constructor({ onVersion } = {}) {
    this.el = document.getElementById('casa-updates-room');
    this.dot = document.getElementById('casa-update-dot');
    this.headline = document.getElementById('casa-update-headline');
    this.summary = document.getElementById('casa-update-summary');
    this.notes = document.getElementById('casa-update-notes');
    this.installBtn = document.getElementById('casa-update-install');
    this.progress = document.getElementById('casa-update-progress');
    this.note = document.getElementById('casa-update-note');
    this.phase = document.getElementById('casa-update-phase');
    this.detail = document.getElementById('casa-update-detail');
    this.bar = document.getElementById('casa-update-bar');
    this.track = document.getElementById('casa-update-track');
    this.lines = document.getElementById('casa-update-lines');
    this.checkBtn = document.getElementById('casa-update-check');

    this._onVersion = onVersion;
    this.version = null;
    /* La generazione della stanza: sale uscendo. Ogni continuazione del flusso
       la cattura prima del primo `await` ed esce se e' cambiata. */
    this._gen = 0;

    this.flow = new UpdateFlow({
      generation: () => this._gen,
      onToast: (testo, tipo) => showToast(testo, tipo),
      onVersion: (v) => {
        if (v) this.version = v;
        this._onVersion?.(v);
      },
      onChange: () => this._paint(),
    });

    this.installBtn?.addEventListener('click', () => this.flow.start());
    this.checkBtn?.addEventListener('click', () => this.check());
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
    if (this.dot) this.dot.className = `casa-update-dot is-${mood}`;
    if (this.headline) {
      /* Senza un numero non si dice «sei alla ultima»: si legge «Sei alla ,
         ed e' l'ultima», che e' una frase con un buco — visto sul rig, ed e'
         lo stato normale finche' `/api/settings` non ha risposto. Non sapere
         si dice, non si maschera. */
      let frase;
      if (v.update_available) {
        frase = i18n.t(
          v.critical ? 'settings.update.availableCritical' : 'settings.update.available',
          { version: v.latest || '' },
        );
      } else {
        frase = v.current
          ? i18n.t('casa.updates.upToDate', { version: v.current })
          : i18n.t('casa.updates.unknown');
      }
      this.headline.textContent = frase;
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
      this.installBtn.disabled = this.flow.busy;
      this.installBtn.textContent = i18n.t('settings.update.install');
      this.installBtn.classList.toggle('is-critical', !!v.critical);
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
    const chiave = phaseKey(u.phase);
    if (this.phase) {
      this.phase.textContent = chiave ? i18n.t(chiave) : '';
      this.phase.hidden = !chiave;
    }
    if (this.detail) {
      this.detail.textContent = u.detail || '';
      this.detail.hidden = !u.detail;
    }
    const correndo = (u.phase === 'downloading' || u.phase === 'installing') && u.progress > 0;
    if (this.track) this.track.hidden = !correndo;
    if (this.bar && correndo) {
      this.bar.style.width = `${Math.min(Math.max(u.progress, 0), 100)}%`;
    }
  }

  /* Il meccanismo che cerca gli aggiornamenti: c'e' sempre, anche — soprattutto
     — quando non c'e' niente da installare. */
  _paintLines() {
    if (this.lines) {
      this.lines.replaceChildren();
      for (const riga of checkLines(this.version)) {
        const el = document.createElement('div');
        el.className = riga.warn ? 'casa-update-line is-warn' : 'casa-update-line';
        el.textContent = i18n.t(riga.key, riga.params);
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
