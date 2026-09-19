/** L'aggiornamento dell'app: la macchina a stati, senza una vista addosso.
 *
 *  Stava dentro `mobile-settings.js` — circa duecento righe fra le fasi, il
 *  polling e i due casi che ingannano — e da li' e' uscita quando la casa ha
 *  avuto bisogno della stessa cosa. **Estratta e non ricopiata**: una seconda
 *  copia di una macchina a stati non sbaglia subito, sbaglia dopo, quando una
 *  delle due impara qualcosa che l'altra non sa.
 *
 *  Qui dentro non c'e' HTML. Le due viste disegnano in modo diverso — schede
 *  larghe in officina, righe in casa — e quel che hanno davvero in comune sono
 *  le fasi, il polling e le frasi. Il modulo torna **dati**: una chiave i18n e
 *  i suoi parametri; chi chiama decide che forma dargli.
 *
 *  **I due casi che ingannano**, ed erano gia' commentati dove stavano:
 *
 *  * *la connessione che cade perche' l'app si sta riavviando.* Nel percorso
 *    silenzioso il sistema uccide il processo mentre la risposta e' ancora in
 *    volo: chiamarlo errore sarebbe una bugia proprio nel caso normale. Solo
 *    se il polling non ha ancora visto muoversi niente e' un vero fallimento
 *    di partenza.
 *  * *il rifiuto «niente da installare»*, che non sporca la fase lato server:
 *    il motivo sta tutto nel `detail` della risposta, e un giro di polling lo
 *    cancellerebbe rileggendo una fase ancora «inattiva». Per questo il
 *    polling si ferma **prima** di scrivere lo stato.
 */

import { api } from './api-client.js';
import { i18n } from './i18n.js';

/** Oltre questo scarto fra l'ultimo tentativo e l'ultimo esito positivo, il
 *  meccanismo si annuncia guasto invece di tacere. */
export const STALE_MS = 7 * 86400000;

/* Ogni quanto si richiede lo stato, e quante volte al massimo: ~10 minuti.
   Senza il tetto, una fase che non si muove piu' lascerebbe un timer vivo per
   tutta la vita della pagina. */
export const POLL_MS = 1500;
export const POLL_MAX = 400;

/** La chiave della riga di fase. `idle` non ne ha una: prima di premere il
 *  bottone non c'e' niente da raccontare, e dopo un errore la fase torna a
 *  essere l'ultima cosa detta, non «inattivo». */
export function phaseKey(phase) {
  return {
    downloading: 'settings.update.phaseDownloading',
    installing: 'settings.update.phaseInstalling',
    prompt: 'settings.update.phasePrompt',
    error: 'settings.update.phaseError',
    done: 'settings.update.phaseDone',
  }[phase] || '';
}

/** «oggi alle 14:22», «3 giorni fa», «27 giu 2025».
 *
 *  Relativo finche' resta leggibile, datato dopo: a novanta giorni «90 giorni
 *  fa» non dice piu' niente, una data si'. «Ieri» ha un ramo suo perche' «1
 *  giorni fa» si legge male in tutte e due le lingue. Le date passano da
 *  `toLocaleDateString`, che le localizza da se'.
 */
export function whenText(ms) {
  const at = new Date(Number(ms) || 0);
  const dayMs = 86400000;
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  const time = at.toLocaleTimeString(i18n.locale, { hour: '2-digit', minute: '2-digit' });
  if (at.getTime() >= midnight.getTime()) return i18n.t('settings.update.whenToday', { time });
  if (at.getTime() >= midnight.getTime() - dayMs) {
    return i18n.t('settings.update.whenYesterday', { time });
  }
  /* Arrotondato per eccesso: il ramo «ieri» qui sopra garantisce gia' >= 2, e
     troncando, un controllo di tre giorni fa alle 23:00 diventerebbe «2». */
  const days = Math.ceil((midnight.getTime() - at.getTime()) / dayMs);
  if (days <= 30) return i18n.t('settings.update.whenDays', { days });
  return at.toLocaleDateString(i18n.locale, { day: 'numeric', month: 'short', year: 'numeric' });
}

/** Cosa dire del **meccanismo** che cerca gli aggiornamenti, non di un
 *  aggiornamento trovato: `[{ key, params, warn }]`.
 *
 *  Il confronto e' fra i due marcatempo dell'updater, non con l'ora corrente:
 *  `last_check` e' scritto a ogni tentativo, `last_success` solo quando il
 *  manifest e' stato letto davvero, e un telefono spento per una settimana li
 *  ha vecchi entrambi — nessun meccanismo rotto da segnalare.
 */
export function checkLines(version) {
  const v = version || {};
  const success = Number(v.last_success) || 0;
  const check = Number(v.last_check) || 0;
  if (!success) {
    /* Prima del primo tentativo in assoluto non c'e' nessun guasto: c'e'
       un'installazione appena fatta, e darle l'aria dell'allarme sarebbe la
       prima cosa falsa che Jenny dice. */
    if (!check) return [{ key: 'settings.update.neverChecked', params: {}, warn: false }];
    /* Tentativi si', esiti positivi no. Vale anche per uno stato scritto prima
       che `last_success` esistesse: la frase manda a premere «Controlla ora»
       invece di sentenziare, perche' un tocco distingue i due casi meglio di
       qualunque euristica. */
    return [{ key: 'settings.update.staleNever', params: {}, warn: true }];
  }
  const when = whenText(success);
  const righe = [{ key: 'settings.update.lastSuccess', params: { when }, warn: false }];
  if (check - success > STALE_MS) {
    righe.push({ key: 'settings.update.stale', params: { when }, warn: true });
  }
  return righe;
}

export class UpdateFlow {
  /** @param onChange   lo stato si e' mosso: ridisegna.
   *  @param onVersion  un controllo ha portato un payload versione fresco.
   *  @param onToast    (testo, tipo) — gia' tradotto.
   *  @param generation la generazione della vista, per sapere se e' ancora
   *                    quella che ha cominciato. Di suo: sempre la stessa. */
  constructor({ onChange, onVersion, onToast, generation } = {}) {
    this._onChange = onChange;
    this._onVersion = onVersion;
    this._onToast = onToast;
    this._generation = generation || (() => 0);

    /** `null` finche' non si avvia, poi {busy, noteKey, phase, progress,
     *  detail}. Vive qui e non nel DOM perche' in officina ogni salvataggio
     *  riscrive tutta la pagina, e un'installazione in corso non e' una cosa
     *  che possa sparire da sotto gli occhi. */
    this.state = null;
    /** Vero mentre un controllo manuale e' in volo. */
    this.checking = false;
    this._timer = null;
    this._polls = 0;
  }

  /** C'e' un'installazione in corso, e nessuno la sta piu' guardando. */
  get busy() {
    return !!this.state?.busy;
  }

  /** Vero se il polling e' spento: serve a chi rientra nella sezione. */
  get idleTimer() {
    return !this._timer;
  }

  _stale(gen) {
    return gen !== this._generation();
  }

  _changed() {
    this._onChange?.(this.state);
  }

  /** Controllo forzato.
   *
   *  Esiste perche' senza di esso non c'e' alcun modo di chiedere «sei ancora
   *  viva?»: si aspetta il cron, ventiquattr'ore, e se fallisce non lo dice
   *  nessuno. E' l'affordance che rende diagnosticabili le righe di
   *  `checkLines`, e `check_for_update` ignora `updates.enabled` proprio per
   *  poter essere chiamata cosi'. Il doppio tocco e' fermato due volte: qui
   *  dal flag, e lato server da un lock, perche' la rotta fa rete.
   */
  async check() {
    if (this.checking) return null;
    const gen = this._generation();
    this.checking = true;
    this._changed();

    let payload = null;
    try {
      /* GET e non POST: v. `start()` per il motivo. */
      const res = await api._fetch('/api/updates/check');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      payload = await res.json();
    } catch (_) {
      payload = null;
    }
    /* Il flag si azzera anche se nel frattempo si e' usciti dalla sezione: e'
       roba del flusso, non del DOM, e lasciarlo acceso bloccherebbe il bottone
       al rientro senza che nulla lo rimetta a posto. */
    this.checking = false;
    if (this._stale(gen)) return null;

    if (!payload) {
      this._changed();
      this._onToast?.(i18n.t('settings.update.checkFailed'), 'error');
      return null;
    }
    if (payload.status === 'busy') {
      this._changed();
      this._onToast?.(i18n.t('settings.update.checkBusy'));
      return payload;
    }
    /* La versione fresca esce subito: senza, una versione appena trovata
       comparirebbe solo alla prossima apertura — cioe' proprio dopo il gesto
       con cui l'utente l'ha chiesta. */
    this._onVersion?.(payload.version || null);
    this._changed();
    if (payload.status !== 'ok') {
      this._onToast?.(i18n.t('settings.update.checkFailed'), 'error');
      return payload;
    }
    const v = payload.version || {};
    this._onToast?.(v.update_available
      ? i18n.t('settings.update.available', { version: v.latest || '' })
      : i18n.t('settings.update.checkUpToDate'));
    return payload;
  }

  /** Avvia l'installazione. */
  async start() {
    const gen = this._generation();
    this.state = {
      busy: true, noteKey: 'settings.update.starting', phase: 'idle', progress: 0, detail: '',
    };
    this._polls = 0;
    this._changed();

    /* Il polling parte *prima* di aspettare la risposta, non dopo: la
       richiesta di installazione risponde solo a download+commit conclusi, e
       nel percorso silenzioso non risponde affatto — il sistema uccide il
       processo mentre la risposta e' ancora in volo. Lo stato vero arriva da
       qui. */
    this._schedulePoll(gen);

    let result;
    try {
      /* GET e non POST, come ogni altra scrittura di questa WebUI: il server
         HTTP e' quello di `websockets`, e `Request.parse` rifiuta qualunque
         metodo diverso da GET *prima* che la richiesta arrivi al router — un
         POST qui non fallirebbe con un 405, fallirebbe con la connessione
         chiusa. */
      const res = await api._fetch('/api/updates/install');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      result = await res.json();
    } catch (err) {
      if (this._stale(gen) || !this.state) return;
      /* Connessione caduta a installazione gia' avviata: e' il riavvio, non un
         guasto. Solo se il polling non ha ancora visto muoversi niente si
         tratta di un vero fallimento di partenza. */
      if (this.state.phase !== 'idle') {
        this.state = { ...this.state, noteKey: 'settings.update.restarting' };
        this._changed();
        return;
      }
      this._fail(err.message);
      return;
    }
    if (this._stale(gen) || !this.state) return;

    if (!result.ok || result.state === 'error') {
      this._fail(result.detail || '');
      return;
    }
    if (result.state === 'prompt') {
      this._settleAtPrompt(result.detail || '');
      return;
    }
    /* «silent» non vuol dire «finito»: la sessione e' committata, il sistema
       uccidera' questo processo e Jenny ripartira' da sola. La WebSocket
       cadra' — `ws-manager` riconnette da se' con backoff — e dirlo prima e'
       l'unico modo perche' quella caduta non sembri un guasto. */
    this.state = { ...this.state, noteKey: 'settings.update.restarting' };
    this._changed();
  }

  /** Ferma il polling. Da chiamare quando la vista se ne va: la guardia di
   *  generazione ferma gia' la continuazione, ma il timer va spento comunque
   *  per non tenere sveglia una schermata che non c'e' piu'. */
  stop() {
    clearTimeout(this._timer);
    this._timer = null;
  }

  /** Riaggancia il polling rientrando con un'installazione gia' avviata: va
   *  avanti per conto suo, ma `stop()` aveva spento il timer. Senza questo il
   *  bottone resterebbe disabilitato e lo stato congelato all'ultima cosa
   *  vista. */
  resume() {
    if (this.busy && this.idleTimer) this._schedulePoll(this._generation());
  }

  /* `prompt` e' terminale: la palla e' passata ad Android e non torna indietro
     da sola. La fase resta su questo valore finche' l'utente non risponde,
     quindi continuare a interrogarla non porta niente di nuovo — porta solo
     dieci minuti di polling col bottone disabilitato, e un'uscita-e-rientro ne
     fa ripartire altri dieci.

     Non e' un caso di nicchia: su Android 14+, con l'update ownership, il ramo
     con conferma e' *la* strada normale (v. `UpdateBridge.kt`). Qui si ferma il
     polling, si sblocca il bottone e si dice che cosa manca — la conferma puo'
     essere una schermata aperta davanti agli occhi o, se l'app era in
     background, una notifica ancora in attesa. */
  _settleAtPrompt(detail) {
    this.stop();
    this.state = {
      ...(this.state || {}),
      busy: false,
      noteKey: 'settings.update.promptNote',
      phase: 'prompt',
      progress: 0,
      detail: detail || this.state?.detail || '',
    };
    this._changed();
  }

  /* Esito negativo definitivo: ferma il polling *prima* di scrivere lo stato,
     altrimenti il giro successivo sovrascrive il motivo con la fase del
     server, che dopo un rifiuto e' ancora «inattivo». */
  _fail(detail) {
    this.stop();
    this.state = { busy: false, noteKey: null, phase: 'error', progress: 0, detail };
    this._changed();
    this._onToast?.(i18n.t('settings.update.startFailed'), 'error');
  }

  _schedulePoll(gen) {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this._poll(gen), POLL_MS);
  }

  async _poll(gen) {
    /* Un timer che e' scattato non e' piu' in attesa. Senza questa riga
       `idleTimer` continuerebbe a dire di no per un id morto, e `resume()` —
       che e' fatto apposta per riagganciare un polling fermo — si rifiuterebbe
       di ripartire. Si vede solo se il giro si e' interrotto senza passare da
       `stop()`: la guardia di generazione fa esattamente quello. */
    this._timer = null;
    if (this._stale(gen)) return;
    let status;
    try {
      const res = await api._fetch('/api/updates/status');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      status = await res.json();
    } catch (_) {
      /* Un polling che fallisce non e' un'installazione fallita: durante
         l'installazione il gateway *sparisce*, ed e' il caso normale. Si
         riprova senza cambiare quello che l'utente legge. */
      if (!this._stale(gen) && this._polls++ < POLL_MAX) this._schedulePoll(gen);
      return;
    }
    if (this._stale(gen) || !this.state) return;

    this.state = {
      ...this.state,
      phase: status.phase || 'idle',
      progress: Number(status.progress) || 0,
      detail: status.detail || '',
    };
    /* Terminale quanto `error` e `done`, ma per l'altro motivo: non e' finita,
       e' ferma e aspetta una persona. */
    if (status.phase === 'prompt') {
      this._settleAtPrompt(status.detail || '');
      return;
    }
    if (status.phase === 'error' || status.phase === 'done') {
      this.stop();
      this.state = { ...this.state, busy: false };
      /* «done» lato server vuol dire «sessione committata», non «installato»:
         subito dopo Android sostituisce l'app e il processo muore. La nota sul
         riavvio e' quindi piu' vera adesso che mai — e la si rimette anche se
         la risposta della richiesta non e' mai arrivata. */
      if (status.phase === 'done') this.state.noteKey = 'settings.update.restarting';
      this._changed();
      return;
    }
    this._changed();
    if (this._polls++ < POLL_MAX) this._schedulePoll(gen);
  }
}
