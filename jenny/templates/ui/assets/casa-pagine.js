/** La casa — la pista delle pagine.
 *
 *  La casa e' un launcher: di lato alla chat ci sono le pagine che l'utente ha
 *  aggiunto (tavola `Pagine`). **La chat e' la pagina 0**: c'e' sempre, non si
 *  sposta e non si toglie — per questo non sta nell'elenco salvato, e per
 *  questo qui dentro l'indice 0 non e' una `schermata`.
 *
 *  **Che cosa muove.** Una pista in fila orizzontale, un pannello per pagina,
 *  e un `translateX`. Non e' la stessa risposta visiva del carosello
 *  dell'officina — li' si trascina la vista corrente con una sbirciata
 *  smorzata e la vicina non si disegna mai — e non per capriccio: la tavola
 *  mostra la pagina di fianco che **entra davvero**, affiancata, non che
 *  sbircia. Quel che i due gusci condividono e' il *riconoscimento* del gesto
 *  (`shared/gesto-orizzontale.js`), non la risposta.
 *
 *  **Niente `z-index`.** Quello di Jenny e' l'unico del foglio della casa e
 *  deve restarlo: lei sta sopra la chat, sopra un'app, sopra tutto.
 */

import { api } from './shared/api-client.js';
import { i18n } from './shared/i18n.js';
import { osservaGestoOrizzontale } from './shared/gesto-orizzontale.js';
import { cornicePerApp } from './shared/apps-actions.js';

/** Quanto la pista si lascia tirare oltre il capo, in frazione di schermo.
 *  Serve a dire «di la' non c'e' niente» col dito invece che con un blocco
 *  secco, che sembra un difetto. */
const OLTRE_IL_CAPO = 0.06;

/** Quanto in su deve andare il dito sulla striscia perche' sia «tira su».
 *
 *  Si misura fra `touchstart` e `touchend` e non si segue il dito: il foglio
 *  del cassetto non e' trascinabile — si apre e basta — quindi seguirlo
 *  prometterebbe un movimento che poi non c'e'. E cosi' il riconoscimento di
 *  un trascinamento resta **tutto** in `shared/gesto-orizzontale.js`, che e'
 *  l'invariante che tiene i due gusci allineati.
 */
const TIRA_SU = 32;

/** Le stanze che si possono appendere a una pagina.
 *
 *  **Non tutte.** `pages` — i quaderni — non c'e', e non e' una dimenticanza:
 *  `openPages()` legge il quaderno **dalla conversazione corrente**
 *  (`projectNameOf(sessionManager.currentKey)`), quindi «i quaderni» non sono
 *  un posto fisso ma le pagine di quello con cui stai parlando. Appenderla a
 *  una pagina vorrebbe dire una pagina che mostra cose diverse a seconda di
 *  dov'eri prima. La tavola `PagineGestione` la disegna come esempio, ma il
 *  prodotto non ha quella stanza: chi vuole un quaderno a portata di pollice
 *  ci mette **la sua conversazione**, che e' la stessa cosa detta bene.
 *
 *  L'etichetta e' quella che la stanza usa gia' di suo: due copie dello stesso
 *  nome divergono, e la seconda si scopre quando qualcuno rinomina la prima.
 */
const STANZE = ['tu', 'jenny', 'model', 'updates', 'backup'];

/** Un'icona per specie. Non quella dell'app: per averla servirebbe l'elenco
 *  caricato, e un foglio che aspetta la rete per disegnare una riga e' un
 *  foglio che a volte non si apre. */
const ICONE = { app: 'ti-apps', stanza: 'ti-home', conversazione: 'ti-message-circle' };

export class CasaPagine {
  /** @param app  il guscio, per le guardie che solo lui conosce. */
  constructor(app) {
    this.app = app;
    this.pista = document.getElementById('casa-pista');
    /** Le pagine aggiunte, come le ha salvate il server. La chat non c'e'. */
    this.schermate = [];
    /** 0 = la chat. 1..n = `this.schermate[i - 1]`. */
    this.indice = 0;
    this._tetto = 8;
    this._staccaGesto = null;
    this.striscia = document.getElementById('casa-pallini');
    this.foglio = document.getElementById('casa-pagine-dialog');
    this.elenco = document.getElementById('casa-foglio-elenco');

    if (this.pista) this._armaGesto();
    if (this.striscia) this._armaStriscia();
    if (this.striscia && this.foglio) this._armaFoglio();
    this._pallini();
    i18n.onLocaleChange(() => this._pallini());
  }

  /** Quante caselle ha la pista, chat compresa. */
  get quante() {
    return this.schermate.length + 1;
  }

  /** Se il tetto e' pieno non si puo' aggiungere: lo chiede il foglio. */
  get pienoZeppo() {
    return this.schermate.length >= this._tetto;
  }

  /** Legge l'elenco dal server e disegna. Non alza: una casa che non apre
   *  perche' non ha saputo leggere le sue pagine e' peggio di una casa con la
   *  sola chat. */
  async carica() {
    try {
      const dati = await api.getSchermate();
      this.schermate = Array.isArray(dati?.schermate) ? dati.schermate : [];
      if (Number.isFinite(dati?.max)) this._tetto = dati.max;
    } catch {
      this.schermate = [];
    }
    this._disegna();
    this.vaiA(0, { animato: false });
  }

  /** Salva l'elenco intero — aggiungere, togliere e spostare sono la stessa
   *  scrittura, e mandarlo tutto toglie di mezzo il caso in cui due
   *  scritture si incrociano lasciando un ordine che nessuno ha chiesto. */
  async salva(schermate) {
    const salvate = await api.setSchermate(schermate);
    this.schermate = salvate;
    this._disegna();
    if (this.indice > this.schermate.length) this.vaiA(this.schermate.length);
    else this.vaiA(this.indice, { animato: false });
    return salvate;
  }

  /** Va alla casella `i`, se esiste. */
  vaiA(i, { animato = true } = {}) {
    if (!this.pista) return;
    const bersaglio = Math.max(0, Math.min(i, this.quante - 1));
    this.indice = bersaglio;
    this.pista.style.transition = animato
      ? 'transform .22s cubic-bezier(.22,.61,.36,1)'
      : 'none';
    this.pista.style.transform = `translateX(${-bersaglio * 100}%)`;
    this._pallini();
    this._accendiSolo(bersaglio);
    this.app?.onPaginaCambiata?.(bersaglio, this.schermate[bersaglio - 1] || null);
  }

  /* ── Sotto ──────────────────────────────────────────────────────────── */

  /** Un pannello per schermata, accanto a quello della chat.
   *
   *  Il pannello della chat non si tocca mai: e' nell'HTML, ci vivono il filo
   *  e il composer, e ridisegnarlo vorrebbe dire buttare via la conversazione
   *  a ogni salvataggio.
   */
  _disegna() {
    if (!this.pista) return;
    for (const vecchio of this.pista.querySelectorAll('.casa-pagina[data-id]')) {
      vecchio.remove();
    }
    for (const s of this.schermate) {
      const pannello = document.createElement('div');
      pannello.className = 'casa-pagina';
      pannello.dataset.id = s.id;
      pannello.dataset.kind = s.kind;
      this.pista.appendChild(pannello);
    }
    this._pallini();
  }

  /** Un pallino per casella, quello corrente allungato.
   *
   *  Bottoni veri e non `<span>`: chi i gesti non li fa — o non puo' farli —
   *  cambia pagina toccando, e chi legge lo schermo sente «pagina 2 di 3»
   *  invece di silenzio. `role="tab"` perche' la striscia e' una `tablist`:
   *  e' esattamente quel che e', un elenco di destinazioni di cui una e'
   *  accesa.
   */
  _pallini() {
    if (!this.striscia) return;
    this.striscia.textContent = '';
    for (let i = 0; i < this.quante; i += 1) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'casa-pallino';
      b.setAttribute('role', 'tab');
      b.setAttribute('aria-selected', String(i === this.indice));
      b.setAttribute(
        'aria-label',
        i === 0
          ? i18n.t('casa.pagine.chat')
          : i18n.t('casa.pagine.numero', { n: i, tot: this.quante - 1 }),
      );
      b.addEventListener('click', () => this.vaiA(i));
      this.striscia.appendChild(b);
    }
  }

  /** Il secondo gesto della striscia: **su** apre il cassetto.
   *
   *  Ha preso il posto del bottone che stava a sinistra del campo di
   *  scrittura. Il rischio e' dichiarato: si toglie un comando che si vedeva e
   *  lo si sostituisce con uno che non si vede — per questo la striscia c'e'
   *  sempre, anche con la sola chat, ed e' l'unica cosa che lo annuncia.
   */
  _armaStriscia() {
    let y0 = null;
    let x0 = 0;
    this.striscia.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) { y0 = null; return; }
      y0 = e.touches[0].clientY;
      x0 = e.touches[0].clientX;
    }, { passive: true });
    this.striscia.addEventListener('touchend', (e) => {
      if (y0 === null) return;
      const t = (e.changedTouches && e.changedTouches[0]) || null;
      const partenza = y0;
      y0 = null;
      if (!t) return;
      const su = partenza - t.clientY;
      /* Verticale davvero: sulla striscia passa anche il dito che sta
         cambiando pagina, e quello non deve aprire il cassetto. */
      if (su >= TIRA_SU && su > Math.abs(t.clientX - x0)) {
        this.app?.openLauncher?.();
      }
    }, { passive: true });
  }

  /** «Resta viva solo la pagina che guardi: le altre si spengono, o te le
   *  paghi in batteria» (tavola `PagineGestione`).
   *
   *  Non «la corrente piu' le due vicine»: tre `<iframe>` che girano insieme
   *  su un telefono sono tre app vive, e la regola della tavola e' piu'
   *  stretta **e** piu' semplice. Il prezzo e' che entrando in una pagina
   *  l'app riparte; il prezzo dell'altra scelta lo paga la batteria sempre.
   */
  _accendiSolo(indice) {
    if (!this.pista) return;
    const pannelli = this.pista.children.filter
      ? this.pista.children.filter((c) => c.dataset?.id)
      : [...this.pista.querySelectorAll('.casa-pagina[data-id]')];
    pannelli.forEach((pannello, i) => {
      const suo = i + 1 === indice;
      if (suo) this._riempi(pannello);
      else this._svuota(pannello);
    });
  }

  /** Il contenuto di una pagina, costruito adesso perche' adesso si guarda. */
  _riempi(pannello) {
    if (pannello.dataset.pieno === '1') return;
    const schermata = this.schermate.find((x) => x.id === pannello.dataset.id);
    if (!schermata) return;
    if (schermata.kind === 'app') {
      const cornice = cornicePerApp(schermata.ref);
      cornice.className = 'casa-pagina-app';
      pannello.appendChild(cornice);
    }
    pannello.dataset.pieno = '1';
  }

  _svuota(pannello) {
    if (pannello.dataset.pieno !== '1') return;
    pannello.textContent = '';
    pannello.dataset.pieno = '';
  }

  _armaGesto() {
    let larghezzaPista = 0;

    this._staccaGesto = osservaGestoOrizzontale(this.pista, {
      puoIniziare: () => {
        // Guardia: fuori dalla chat comandano le stanze, non le pagine.
        if (this.app?.view && this.app.view !== 'chat') return false;
        // Guardia: un cassetto aperto possiede il proprio gesto.
        if (this.app?.launcher?.isOpen?.()) return false;
        // Con la sola chat non c'e' nessun posto dove andare, e un elastico
        // che risponde a vuoto sembra un difetto invece che un confine.
        if (this.quante < 2) return false;
        larghezzaPista = this.pista.clientWidth || window.innerWidth;
        return true;
      },

      onOrizzontale: () => {
        this.pista.style.transition = 'none';
        this.pista.style.willChange = 'transform';
      },

      onTrascina: (dx) => {
        /* Ai due capi il dito tira, ma di meno: la pista non si richiude in
           cerchio come le linguette dell'officina. Li' le voci sono quattro e
           note; qui quante siano lo decide l'utente, e girando in tondo fra
           otto pagine non si sa piu' dove si e'. */
        let scostamento = dx;
        const alPrimo = this.indice === 0 && dx > 0;
        const allUltimo = this.indice === this.quante - 1 && dx < 0;
        if (alPrimo || allUltimo) {
          scostamento = dx * OLTRE_IL_CAPO * 2;
        }
        const base = -this.indice * larghezzaPista;
        this.pista.style.transform = `translateX(${(base + scostamento).toFixed(2)}px)`;
      },

      onFine: ({ verso, conferma }) => {
        this.pista.style.willChange = '';
        const passo = conferma ? (verso === 'prev' ? -1 : +1) : 0;
        this.vaiA(this.indice + passo);
      },

      onAnnulla: () => {
        this.pista.style.willChange = '';
        this.vaiA(this.indice);
      },
    });
  }
}

/* ── Il foglio «Le pagine di casa» ──────────────────────────────────────── */

Object.assign(CasaPagine.prototype, {
  /** La terza via della striscia: **tieni premuto**.
   *
   *  La pressione la riconosce il modulo condiviso e non questo file, per un
   *  motivo solo: annullarla quando il dito si muove vuol dire guardare i
   *  movimenti, e i movimenti si guardano in un posto solo. Cosi' chi tira su
   *  il cassetto, o cambia pagina, non si ritrova il foglio in faccia.
   */
  _armaFoglio() {
    osservaGestoOrizzontale(this.striscia, {
      puoIniziare: () => true,
      onPressioneLunga: () => this.apriFoglio(),
    });
  },

  apriFoglio() {
    if (!this.foglio || this.foglio.open) return;
    this._disegnaFoglio();
    this.foglio.showModal();
  },

  chiudiFoglio() {
    if (this.foglio?.open) this.foglio.close();
  },

  /** Una riga per pagina piena, poi la prima libera con la scelta aperta. */
  _disegnaFoglio() {
    const t = (k, v) => i18n.t(k, v);
    document.getElementById('casa-foglio-titolo').textContent = t('casa.foglio.titolo');
    document.getElementById('casa-foglio-occhiello').textContent = t('casa.foglio.occhiello');
    document.getElementById('casa-foglio-nota-cassetto').textContent = t('casa.foglio.notaCassetto');
    document.getElementById('casa-foglio-nota-viva').textContent = t('casa.foglio.notaViva');

    this.elenco.textContent = '';
    this.schermate.forEach((s, i) => this.elenco.appendChild(this._riga(s, i + 1)));
    if (!this.pienoZeppo) this.elenco.appendChild(this._rigaLibera());
  },

  _riga(schermata, numero) {
    const riga = document.createElement('div');
    riga.className = 'casa-foglio-riga';
    riga.dataset.id = schermata.id;

    const icona = document.createElement('span');
    icona.className = 'casa-foglio-icona';
    const i = document.createElement('i');
    i.className = `ti ${ICONE[schermata.kind] || 'ti-square'}`;
    icona.appendChild(i);

    const testo = document.createElement('span');
    testo.className = 'casa-foglio-testo';
    const nome = document.createElement('span');
    nome.className = 'casa-foglio-nome';
    nome.textContent = this.nomeDi(schermata);
    const specie = document.createElement('span');
    specie.className = 'casa-foglio-specie';
    specie.textContent = i18n.t('casa.foglio.riga', {
      n: numero,
      specie: i18n.t(`casa.foglio.specie.${schermata.kind}`),
    });
    testo.append(nome, specie);

    const togli = document.createElement('button');
    togli.type = 'button';
    togli.className = 'casa-foglio-togli';
    togli.setAttribute('aria-label', i18n.t('casa.foglio.togli', { nome: this.nomeDi(schermata) }));
    const x = document.createElement('i');
    x.className = 'ti ti-x';
    togli.appendChild(x);
    /* Niente conferma: una pagina si rimette con tre tocchi, e una conferma
       per un gesto annullabile e' solo un tocco in piu' ogni volta. */
    togli.addEventListener('click', () => this._togli(schermata.id));

    riga.append(icona, testo, togli);
    return riga;
  },

  _rigaLibera() {
    const box = document.createElement('div');
    box.className = 'casa-foglio-libera';
    const domanda = document.createElement('div');
    domanda.className = 'casa-foglio-domanda';
    domanda.textContent = i18n.t('casa.foglio.vuota', { n: this.schermate.length + 1 });
    const scelte = document.createElement('div');
    scelte.className = 'casa-foglio-scelte';
    for (const kind of ['app', 'stanza', 'conversazione']) {
      scelte.appendChild(this._scelta(kind));
    }
    box.append(domanda, scelte);
    return box;
  },

  _scelta(kind) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'casa-foglio-scelta';
    b.dataset.kind = kind;
    const i = document.createElement('i');
    i.className = `ti ${ICONE[kind]}`;
    const testo = document.createElement('span');
    testo.textContent = i18n.t(`casa.foglio.scegli.${kind}`);
    b.append(i, testo);
    b.addEventListener('click', () => this._apriScelta(kind));
    return b;
  },

  /** Secondo passo: **quale**. Stesso foglio, non un secondo: due fogli
   *  impilati su un telefono lasciano metà schermo di velo e nessuno sa piu'
   *  quale «indietro» chiude cosa. */
  async _apriScelta(kind) {
    this.elenco.textContent = '';
    const indietro = document.createElement('button');
    indietro.type = 'button';
    indietro.className = 'casa-foglio-scelta';
    const freccia = document.createElement('i');
    freccia.className = 'ti ti-arrow-left';
    const et = document.createElement('span');
    et.textContent = i18n.t('casa.foglio.indietro');
    indietro.append(freccia, et);
    indietro.addEventListener('click', () => this._disegnaFoglio());
    this.elenco.appendChild(indietro);

    let voci = [];
    try {
      voci = await this._voci(kind);
    } catch {
      voci = [];
    }
    if (!voci.length) {
      const vuoto = document.createElement('p');
      vuoto.className = 'casa-foglio-nota';
      vuoto.textContent = i18n.t(`casa.foglio.niente.${kind}`);
      this.elenco.appendChild(vuoto);
      return;
    }
    for (const v of voci) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'casa-foglio-scelta';
      const i = document.createElement('i');
      i.className = `ti ${ICONE[kind]}`;
      const testo = document.createElement('span');
      testo.textContent = v.nome;
      b.append(i, testo);
      b.addEventListener('click', () => this._aggiungi(kind, v.ref));
      this.elenco.appendChild(b);
    }
  },

  /** Cosa si puo' scegliere, per specie. */
  async _voci(kind) {
    if (kind === 'stanza') {
      return STANZE.map((ref) => ({ ref, nome: i18n.t(`casa.${ref}.title`) }));
    }
    if (kind === 'app') {
      const fonte = this.app?.appsSource?.();
      await fonte?.ensureLoaded?.();
      /* Un'app rotta non si puo' appendere: `openApp` per quelle chiede
         conferma e propone la riparazione in chat, e una pagina fissa rotta e'
         un'altra cosa — resterebbe li' a non funzionare tutti i giorni.
         Un'app **esterna** apre un indirizzo che il guscio non controlla:
         incastonarla in una pagina fissa e' una decisione a se'. */
      return (fonte?.jennyApps || [])
        .filter((a) => !a.broken && a.view_kind !== 'external')
        .map((a) => ({ ref: a.slug, nome: a.name || a.slug }));
    }
    const progetti = await api.listProjects();
    return (progetti || []).map((pr) => ({ ref: pr.name, nome: pr.name }));
  },

  /** Come si chiama una pagina, per l'intestazione e per il foglio.
   *
   *  Una stanza porta **il nome che usa gia' di suo**: due copie dello stesso
   *  nome divergono, e la seconda si scopre quando qualcuno rinomina la prima.
   *  Un'app e una conversazione portano il loro riferimento, che e' gia' il
   *  nome che l'utente ha visto quando l'ha scelta.
   */
  nomeDi(schermata) {
    if (!schermata) return '';
    if (schermata.kind === 'stanza') return i18n.t(`casa.${schermata.ref}.title`);
    return schermata.ref;
  },

  async _aggiungi(kind, ref) {
    const id = `p${Date.now().toString(36)}`;
    await this.salva([...this.schermate, { id, kind, ref }]);
    this.chiudiFoglio();
    /* Si atterra sulla pagina appena fatta: chi l'ha aggiunta vuole vederla,
       e lasciarlo sulla chat gli farebbe credere che non sia successo niente. */
    this.vaiA(this.schermate.length);
  },

  async _togli(id) {
    await this.salva(this.schermate.filter((s) => s.id !== id));
    this._disegnaFoglio();
  },
});
