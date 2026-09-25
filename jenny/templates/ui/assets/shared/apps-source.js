/** Le liste che riempiono il cassetto, senza una schermata addosso.
 *
 *  Stavano dentro `AppsController` — la scheda «App» dell'officina — e da li'
 *  sono uscite quando quella scheda e' stata cancellata (20/09/2026). Il
 *  cassetto pero' esisteva gia' e ci leggeva dentro: **la meta' dati era gia'
 *  scritta per girare senza schermo**, e il suo `render()` usciva prima di
 *  toccare il DOM con un commento che nominava proprio questo caso. Qui quella
 *  meta' e' diventata una cosa a se', e le due viste che ne restano — il
 *  cassetto in casa e quello in officina — ci si iscrivono e basta.
 *
 *  **Due liste, non quattro.** Erano skill, Jenny App, app Android e app
 *  nascoste. Le skill non entrano nel cassetto (non si lanciano: toccarne una
 *  apriva una scheda, e mescolarle alle app faceva un elenco di nature
 *  diverse), e «nascondi» e' stato tolto per intero con la schermata che lo
 *  ospitava. Restano le due che si aprono.
 *
 *  Quel che **non** e' cambiato passando di qui, perche' sono le tre cose che
 *  costano care e si erano gia' pagate:
 *
 *  * la guardia sulle risposte fuori ordine (`_seqAndroid`), senza la quale una
 *    fetch vecchia riscrive la lista con uno stato stantio e l'app appena
 *    disinstallata ricompare;
 *  * la distinzione fra «il ponte nativo non risponde» e «non ci sono app» —
 *    il primo torna 200 con una lista vuota e un `error` dentro, e senza
 *    guardare quel campo un PackageManager muto si legge come un telefono
 *    senza app;
 *  * l'annuncio delle disinstallazioni, con la sua guardia `!failed`: una lista
 *    vuota per guasto e' verissima a guardarla, e senza quel controllo
 *    annuncerebbe come disinstallate tutte le app del telefono in un colpo.
 */

import { api } from './api-client.js';
import { i18n } from './i18n.js';
import { showToast } from './utils.js';
import { wsManager } from './ws-manager.js';

export class AppsSource {
  constructor() {
    /** Le Jenny App, come le manda il gateway. */
    this.jennyApps = [];
    /** Le app Android lanciabili, come le manda il ponte nativo. */
    this.androidApps = [];

    this._jennyLoaded = false;
    this._androidLoaded = false;
    this._failed = { jenny: false, android: false };

    this._listeners = new Set();
    /** Chi vuole sapere che i dati di una Jenny App sono cambiati: riceve lo
     *  slug. Separati da `_listeners`, che parlano delle **liste**. */
    this._datiListeners = new Set();
    /* Ogni fetch delle app Android prende un numero: solo la piu' recente ha
       il diritto di scrivere. */
    this._seqAndroid = 0;
    this._seqJenny = 0;
    /* Le rimozioni gia' annunciate per la via del broadcast, per non dirle due
       volte quando la fetch successiva le riscopre. */
    this._annunciate = new Set();
    this._timerAndroid = null;
    this._rientroArmato = false;
    /* I due frame del gateway sulle Jenny App (v. `_onFrame`). L'ascolto sta
       qui e non in una vista perche' ogni cornice di app — la mini-app sopra
       tutto, la pagina app della casa — nasce dopo che la sorgente e' stata
       costruita: chi c'e' da avvisare, trova gia' qualcuno in ascolto. */
    wsManager.addEventListener('chat:message', (e) => this._onFrame(e.detail));
  }

  /* ── Chi guarda ─────────────────────────────────────────────────────────── */

  /** Iscrive un ascoltatore ai cambi; ritorna la funzione che lo disiscrive. */
  addChangeListener(fn) {
    this._listeners.add(fn);
    return () => this._listeners.delete(fn);
  }

  /* Un ascoltatore che esplode non deve portarsi via gli altri: chi guarda e'
     un consumatore, non un pezzo di questo flusso. */
  _emit() {
    for (const fn of this._listeners) {
      try {
        fn();
      } catch (err) {
        console.error('Apps change listener failed:', err);
      }
    }
  }

  /* ── Lo stato ───────────────────────────────────────────────────────────── */

  /** Avvia le fetch che mancano. Idempotente: chiamarla a ogni apertura del
   *  cassetto non rifa' il lavoro gia' fatto, ma ritenta quello fallito. */
  ensureLoaded() {
    if (!this._jennyLoaded || this._failed.jenny) this.loadJennyApps();
    if (!this._androidLoaded || this._failed.android) this.loadAndroidApps();
  }

  /** Le Jenny App, **attese**.
   *
   *  `ensureLoaded()` non e' asincrona: avvia le due fetch e torna subito, e
   *  chi ci mette un `await` davanti aspetta `undefined` — cioe' niente. Sul
   *  telefono il 22/09/2026 questo faceva dire «non hai Jenny App» a un utente
   *  che ne aveva quattro: la lista arrivava un istante dopo che qualcuno
   *  aveva gia' deciso che era vuota.
   *
   *  Qui si aspetta la risposta vera. Un guasto non alza: torna la lista che
   *  c'e' — vuota — perche' chi chiede vuole sapere cosa mostrare, e
   *  distinguere «vuota» da «non letta» e' gia' il mestiere di
   *  `listsFailed()`.
   */
  attendiJennyApps() {
    this.ensureLoaded();
    if (this._jennyLoaded) return Promise.resolve(this.jennyApps);
    return new Promise((risolvi) => {
      const stacca = this.addChangeListener(() => {
        if (!this._jennyLoaded) return;
        stacca();
        risolvi(this.jennyApps);
      });
    });
  }

  /** Vero finche' una delle due risposte non e' tornata. Distingue «non c'e'
   *  niente» da «non e' ancora arrivato niente». */
  isLoadingLists() {
    return !(this._jennyLoaded && this._androidLoaded);
  }

  /** Almeno una lista non si e' potuta leggere. Terza risposta accanto a
   *  `isLoadingLists()` e a «l'elenco e' vuoto», e le tre non si sovrappongono: un
   *  guasto **non** lascia la UI in caricamento — la risposta e' arrivata, dice
   *  solo che e' andata male — e non e' nemmeno un elenco vuoto, perche'
   *  l'altra lista puo' esserci tutta. */
  listsFailed() {
    return this._failed.jenny || this._failed.android;
  }

  /** La lista delle **Jenny App** non si e' potuta leggere. Serve a chi deve
   *  dire «quest'app non c'e' piu'»: con la lista rotta la risposta e' «non lo
   *  so», e dire «sparita» a un'app che c'e' sarebbe peggio di tacere. */
  jennyListFailed() {
    return this._failed.jenny;
  }

  /** Riprova. `ensureLoaded()` fa gia' lo stesso a ogni apertura, quindi
   *  chiudere e riaprire basta; questo e' il pulsante per chi il foglio ce
   *  l'ha gia' aperto sotto gli occhi e non deve indovinare che riaprirlo
   *  ritenta. */
  retryFailedLists() {
    this.ensureLoaded();
  }

  /* ── Le due fetch ───────────────────────────────────────────────────────── */

  async loadJennyApps() {
    /* Da quando anche il gateway fa rileggere l'elenco (`apps_list_changed`)
       due letture possono accavallarsi: vince l'ultima partita, come per le
       app Android (`_seqAndroid`). */
    const token = ++this._seqJenny;
    let apps = null;
    try {
      const data = await api.getJennyApps();
      apps = data.apps || [];
    } catch {
      apps = null;
    }
    if (token !== this._seqJenny) return;
    if (apps) {
      this.jennyApps = apps;
      this._failed.jenny = false;
    } else if (!this._jennyLoaded || this._failed.jenny) {
      this.jennyApps = [];
      this._failed.jenny = true;
    }
    /* Altrimenti una rilettura e' fallita su un elenco che era buono: lo si
       tiene, invece di svuotare il cassetto e accenderne l'errore per un
       attimo di gateway occupato. */
    this._jennyLoaded = true;
    this._emit();
  }

  /** @param announceRemovals annuncia con un toast le app sparite dall'elenco
   *         rispetto alla lettura precedente. */
  async loadAndroidApps({ announceRemovals = false } = {}) {
    const token = ++this._seqAndroid;
    const prima = this.androidApps;
    let apps = null;
    let failed = false;
    try {
      const data = await api.getAndroidApps();
      apps = data.apps || [];
      /* Il ponte rotto risponde 200 con una lista vuota e questo campo: senza
         guardarlo, un PackageManager muto e un telefono senza app sono la
         stessa risposta. */
      failed = !!data.error;
    } catch {
      apps = null;
      failed = true;
    }
    /* Risposta vecchia: nel frattempo ne e' partita una piu' recente, e
       scrivere adesso rimetterebbe in lista l'app appena disinstallata. */
    if (token !== this._seqAndroid) return;

    this.androidApps = apps || [];
    this._failed.android = failed;
    this._androidLoaded = true;

    /* `!failed` accanto ad `apps`: una lista vuota **per guasto** arriva come
       `[]`, cioe' verissima a guardarla, e senza questa guardia annuncerebbe
       come disinstallate tutte le app del telefono in un colpo. */
    if (announceRemovals && apps && !failed) {
      const presenti = new Set(apps.map((a) => a.packageName));
      const sparite = prima.filter(
        (a) => !presenti.has(a.packageName) && !this._annunciate.has(a.packageName),
      );
      this._annunciate.clear();
      if (sparite.length) this._annuncia(sparite.map((a) => a.label));
    }
    this._emit();
  }

  /** Conferma visibile di una disinstallazione: il dialogo di sistema non ne
   *  da' nessuna. Un solo toast anche per piu' app — i toast si sovrappongono
   *  invece di impilarsi, quindi due insieme si coprirebbero. */
  _annuncia(etichette) {
    showToast(
      etichette.length === 1
        ? i18n.t('apps.uninstalled', { name: etichette[0] })
        : i18n.t('apps.uninstalledMany', { names: etichette.join(', ') }),
      'success',
    );
  }

  /* ── Quel che succede fuori dalla pagina ────────────────────────────────── */

  /** Il guscio nativo ha visto un pacchetto installato o rimosso. */
  onPackageChanged(kind, packageName) {
    if (kind === 'removed') {
      const app = this.androidApps.find((a) => a.packageName === packageName);
      // Pacchetto senza activity di launcher: mai stato in lista, niente da dire.
      if (app) {
        this.androidApps = this.androidApps.filter((a) => a.packageName !== packageName);
        this._emit();
        this._annunciate.add(packageName);
        this._annuncia([app.label]);
      }
    }
    clearTimeout(this._timerAndroid);
    /* `announceRemovals` anche qui: il broadcast puo' mancare per un'app — il
       filtro di visibilita' dei pacchetti non ne garantisce la consegna — e in
       quel caso la sua sparizione la vede solo il confronto con la lista
       precedente. */
    this._timerAndroid = setTimeout(
      () => this.loadAndroidApps({ announceRemovals: true }),
      500,
    );
  }

  /** Rilegge le app Android quando la pagina torna in primo piano.
   *
   *  Seconda linea di difesa dietro `onPackageChanged`: copre il caso in cui il
   *  broadcast di sistema non arrivi — per esempio una disinstallazione fatta
   *  da «Info app» — quindi annuncia anche lei le app sparite.
   */
  reloadOnReturn() {
    if (this._rientroArmato) return;
    this._rientroArmato = true;
    const quandoTorna = () => {
      if (document.visibilityState !== 'visible') return;
      document.removeEventListener('visibilitychange', quandoTorna);
      this._rientroArmato = false;
      this.loadAndroidApps({ announceRemovals: true });
    };
    document.addEventListener('visibilitychange', quandoTorna);
  }

  /** Il gateway dice che una Jenny App ha cambiato i suoi dati (una sua
   *  azione girata come tool) o che l'elenco delle app e' cambiato (un turno
   *  che ha scritto in `apps/`).
   *
   *  **Sono stati senza ascoltatore dal 21/09 al 24/09/2026.** Stavano nel
   *  costruttore della scheda «App» e se ne sono andati con lei (`98a0230`):
   *  il gateway ha continuato a mandarli, la mini-app aperta ha smesso di
   *  rileggersi da sola e il cassetto di accorgersi di un'app nuova, e nessun
   *  errore l'ha detto. Ora c'e' un banco che lo dice
   *  (`test_ws_events_have_listeners_contract.py`).
   *
   *  L'elenco si rilegge **solo se era gia' stato letto**: se nessuno l'ha mai
   *  chiesto non c'e' niente da tenere aggiornato, e la prima lettura avverra'
   *  comunque quando servira'. E si rilegge senza tornare «non caricato» —
   *  com'era prima — perche' quello riaccendeva il «caricamento» nel cassetto
   *  aperto per una risposta che arriva in un istante; se le righe non sono
   *  cambiate il cassetto non le ridisegna nemmeno (confronto di firma).
   */
  _onFrame(msg) {
    if (msg?.event === 'apps_list_changed') {
      if (this._jennyLoaded) this.loadJennyApps();
      return;
    }
    if (msg?.event === 'app_data_changed' && msg.slug) {
      for (const fn of this._datiListeners) {
        try {
          fn(msg.slug);
        } catch (err) {
          console.error('App data listener failed:', err);
        }
      }
    }
  }

  /** Iscrive chi ha una cornice di app da avvisare; ritorna la funzione che lo
   *  disiscrive. */
  onAppDataChanged(fn) {
    this._datiListeners.add(fn);
    return () => this._datiListeners.delete(fn);
  }

  /* ── Le righe del cassetto ──────────────────────────────────────────────── */

  /** Le due liste normalizzate in righe.
   *
   *  L'ordine e' alfabetico e stabile: il cassetto lo riordina per pertinenza,
   *  frequenza e recenza (`shared/launcher-rank.js`), ma questa e' la lista,
   *  non la classifica. Il `key` porta lo spazio di nomi (`android:<pkg>`,
   *  `jenny:<slug>`), cosi' due voci omonime non si confondono mai; `id` e' la
   *  stessa cosa senza prefisso, per chi deve poi avviarla.
   *
   *  **`description` e `problem` non sono decorazione.** Il gateway manda per
   *  ogni Jenny App una descrizione, e dice anche quando qualcosa non va
   *  (`broken`/`error`): qui arriva intero alle righe, che e' anche cio' su cui
   *  si cerca. Le app Android non hanno una descrizione e non gliene
   *  inventiamo una: al suo posto va il nome del pacchetto, che e' un dato
   *  vero, distingue due app omonime, e si cerca — «gmail» trova *Gmail* anche
   *  da `com.google.android.gm`.
   */
  launcherEntries() {
    const righe = [];
    for (const app of this.jennyApps) {
      const problem = app.broken ? (app.error || i18n.t('apps.invalidManifest')) : null;
      righe.push({
        key: `jenny:${app.slug}`, id: app.slug, kind: 'jenny',
        name: app.name || app.slug,
        glyph: app.broken ? 'ti-alert-triangle' : (app.icon || 'ti-apps'),
        icon: null,
        description: app.description || '',
        problem,
        hasServer: !!app.has_server,
        // Anche l'errore si cerca: «manifest» deve far emergere le app rotte
        // tutte insieme, che e' il modo in cui uno le ripara.
        searchText: [app.description, app.slug, problem].filter(Boolean).join(' '),
      });
    }
    if (this._androidLoaded) {
      for (const app of this.androidApps) {
        righe.push({
          key: `android:${app.packageName}`, id: app.packageName, kind: 'android',
          name: app.label,
          glyph: 'ti-apps', icon: app.icon || null,
          description: app.packageName,
          problem: null,
          system: !!app.system,
          searchText: app.packageName,
        });
      }
    }
    righe.sort((a, b) =>
      a.name.localeCompare(b.name, i18n.locale, { sensitivity: 'base' })
      || a.key.localeCompare(b.key));
    return righe;
  }
}
