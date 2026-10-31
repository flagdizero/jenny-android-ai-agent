/** La casa — la conversazione.
 *
 *  Un filo solo, dall'alto verso il basso: cosa hai detto tu, cosa ha risposto
 *  Jenny. Quello che l'officina disegna e qui non esiste — pensieri, chiamate di
 *  strumento, subagent, token — non e' nascosto dietro un pannello: non viene
 *  proprio letto. I frame arrivano lo stesso, sullo stesso websocket, e restano
 *  lettera morta.
 *
 *  **Una sola cosa e' rientrata, il 21/09/2026: la coda della risposta.** Qui
 *  c'era scritto che nemmeno i tempi si leggevano, e il risultato a schermo era
 *  che quattro risposte di fila sembravano un messaggio solo — niente le
 *  separava, perche' in casa non c'e' ne' bolla ne' avatar, solo paragrafi. La
 *  riga in coda (Copia e i secondi, v. `_tailOf`) e' il confine: dice dove una
 *  risposta finisce, e lo dice con due cose che servono invece che con una
 *  linea che non serve a niente.
 *
 *  **E il 02/10/2026 sono rientrati i subagent, ma solo per dire che ci sono.**
 *  Un turno non li aspetta piu': Jenny risponde «ci sto lavorando» e il turno
 *  finisce, mentre il lavoro va avanti per minuti. Senza un segno la
 *  conversazione sembrava ferma. Il segno era un chip fuori dal filo; dal
 *  05/10/2026 e' una bolla di lavoro **dentro** il filo, nel suo posto vivo in
 *  fondo (`this.live`, `home-work.js`), insieme a quelle del giardiniere e di
 *  Dream. Dice chi lavora, cosa fa e da quanto; il resto — strumenti, Ferma —
 *  resta in officina, e qui non si legge.
 *
 *  **Le regole del filo non sono state inventate qui.** Sono quelle che
 *  `mobile-chat.js` ha imparato sbagliando, e che valgono identiche in casa
 *  perche' descrivono il protocollo, non il disegno:
 *
 *  1. `stream_end` puo' arrivare **senza** testo — il server lo omette quando
 *     l'ultimo delta e' vuoto, cioe' quasi sempre. Il buffer locale e' la stessa
 *     cosa e fa da riserva.
 *  2. Un frame `message` porta un testo **gia' completo**: apre un blocco suo e
 *     lo chiude subito. Riusare il blocco dei delta significa farselo
 *     sovrascrivere dal delta successivo, e il testo consegnato sparisce.
 *  3. Un turno puo' alternare testo e strumenti piu' volte: piu' segmenti di
 *     stream, stesso `turn_id`. Il blocco si chiude a ogni `stream_end`, o i
 *     segmenti si incollano fra loro.
 *  4. Un messaggio senza `turn_id` non entra **mai** nel turno precedente:
 *     `undefined !== undefined` e' falso, e quattro avvisi distinti diventano
 *     una bolla sola.
 */

import { api } from './shared/api-client.js';
import { copyToClipboard, escapeHtml, showToast } from './shared/utils.js';
import { i18n } from './shared/i18n.js';
import { openImageLightbox } from './shared/image-lightbox.js';
import { sessionManager } from './shared/session-manager.js';
import { HistoryPager } from './shared/history-pager.js';
import { renderRich } from './shared/rich-content.js';
import { renderMarkdown } from './shared/markdown.js';
import { describeWireError } from './shared/wire-error.js';
import { contentLinkOf, openContentLink } from './shared/content-link.js';
import { chatTime, chatTimeIso } from './shared/chat-time.js';

/* Da dove e' entrato un messaggio che non hai scritto qui dentro. La chat e' il
   registro completo di tutte le superfici — l'app, Telegram, la tendina delle
   notifiche, il fumetto della mascotte — e la provenienza va detta, altrimenti
   un messaggio scritto dal blocco schermo sembra comparso dal nulla.

   Nomi e non identificatori: in officina l'etichetta e' il canale con
   l'iniziale maiuscola ("Floating"), che e' il nome che ha nel codice. Qui e'
   il nome che ha per chi lo legge. */
const ORIGINS = {
  telegram: { icon: 'ti-brand-telegram', key: 'home.origin.telegram' },
  notification: { icon: 'ti-bell', key: 'home.origin.notification' },
  floating: { icon: 'ti-message-circle', key: 'home.origin.floating' },
  /* Scritto da un altro dispositivo del tailnet (il Mac, un secondo
     telefono). Col nome che gli da' MagicDNS, quando c'e': «dal mac». */
  remote: { icon: 'ti-device-laptop', key: 'home.origin.remote', named: 'home.origin.remoteNamed' },
};

/* Quanto lontano dal fondo si puo' essere e continuare a essere "in fondo".
   Sotto questa soglia il filo insegue i messaggi nuovi; sopra, no — chi sta
   rileggendo qualcosa piu' su non va strappato via da una risposta che arriva. */
const STICK_PX = 24;

/* Quante ancore chiedere per pagina. L'officina ne chiede 160 all'apertura; la
   casa molte meno, e la ragione e' il costo, non il contenuto: il budget conta
   gli eventi `user` / `stream_end` / `message`, non le righe degli strumenti,
   quindi 50 sono ~50 messaggi visibili, cioe' venticinque scambi — quattro o
   cinque schermate. Quel che cala e' il numero di turni selezionati, e con loro
   i record grezzi che il gateway rigioca a ogni apertura sulla CPU del telefono. */
const HISTORY_PAGE_SIZE = 50;

/* I nodi che fanno il filo: quel che una rilettura butta e ridisegna. Le
   righe di rifiuto (`.home-note`) comprese: sono un fatto della conversazione
   in cui sono nate, e senza restavano nel filo di quella dopo. La
   storia non le porta, quindi una rilettura della stessa le perde — ed e' il
   prezzo giusto: quel rifiuto l'hai gia' letto, e il messaggio e' tornato nel
   campo. */
/* I nodi del filo sono i suoi **figli**: le bolle di lavoro in corso stanno nel
   posto vivo (`.home-live`, v. il costruttore) e hanno la loro vita — una
   rilettura della storia non le tocca. Una bolla finita scende nel filo e da
   li' e' un messaggio come gli altri. */
const THREAD_NODES = ':scope > .home-msg, :scope > .home-msg-work, :scope > .home-boundary, :scope > .home-note';

/** Quel che un turno ha messo nei cassetti: le chiamate a `journal_append` con un
 *  quaderno, riuscite. Gli eventi arrivano due volte (inizio e fine) e la storia
 *  li fonde per `call_id`: conta la fine, e solo se il tool ha scritto davvero —
 *  un rifiuto («No notebook named…») non e' un'annotazione.
 *  @param {any[]} events  `tool_events` dal vivo o `toolEvents` della storia */
export function journalNotes(events) {
  const out = [];
  const seen = new Set();
  for (const ev of Array.isArray(events) ? events : []) {
    if (!ev || ev.name !== 'journal_append' || ev.phase !== 'end') continue;
    const args = ev.arguments || {};
    const notebook = typeof args.notebook === 'string' ? args.notebook.trim() : '';
    if (!notebook || !String(ev.result || '').startsWith('Appended to')) continue;
    const key = ev.call_id || `${notebook}\u0000${args.text}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ notebook, text: String(args.text || '') });
  }
  return out;
}

/** I quaderni che il turno ha proposto di aprire («ci spostiamo?»): le chiamate a
 *  `suggest_notebook` riuscite. La scelta e' dell'utente, con un tocco: il tasto
 *  lo disegna `_moveOfferOf`.
 *  @param {any[]} events */
export function moveOffers(events) {
  const out = [];
  for (const ev of Array.isArray(events) ? events : []) {
    if (!ev || ev.name !== 'suggest_notebook' || ev.phase !== 'end') continue;
    const notebook = String(ev.arguments?.notebook || '').trim();
    if (notebook && String(ev.result || '').startsWith('The user now sees a button')
        && !out.includes(notebook)) out.push(notebook);
  }
  return out;
}

function mediaKind(entry) {
  if (entry.kind) return entry.kind;
  const name = (entry.name || entry.url || '').toLowerCase();
  if (/\.(png|jpe?g|gif|webp|bmp|svg)(\?|$)/.test(name)) return 'image';
  if (/\.(mp4|webm|mov|m4v)(\?|$)/.test(name)) return 'video';
  return 'file';
}

export class HomeChat {
  constructor(threadEl) {
    this.el = threadEl;
    /* La bolla dell'assistente del turno in corso, e il blocco di testo aperto
       dentro di essa. Due cose diverse: la bolla dura tutto il turno, il blocco
       dura un segmento di stream. */
    this.turnNode = null;
    this.blockNode = null;
    this.buffer = '';
    this.turnId = null;
    this._empty = true;
    /* Il fotogramma in cui il testo in arrivo verra' ridisegnato, o `null`.
       Un delta non rende da se': mette in coda un disegno, e i delta dello
       stesso fotogramma ne pagano uno solo (v. `_delta`). */
    this._frame = null;
    /* Vero mentre la storia entra a blocchi (`load`, `prependTurns`): la
       sagoma di Jenny si ricalcola una volta alla fine invece che a ogni
       messaggio (v. `_append`). */
    this._batching = false;
    /* **La storia arriva mentre il filo e' vivo.** Una lettura e' una fetch
       che dura, e intanto il socket porta frame: la risposta che Jenny sta
       scrivendo, un messaggio tuo. Quei nodi nascono *durante* la lettura e
       vanno *sotto* la storia che arriva dopo di loro — prima la storia si
       appendeva in fondo, e la bolla viva finiva sopra tutta la
       conversazione.

       `_shownKey` e' la conversazione che il filo mostra: una rilettura della
       stessa tiene quel che c'e' finche' la storia non e' pronta, una di
       un'altra lo toglie subito. `_reading` conta le letture in volo, `_live`
       sono i nodi nati mentre ce n'era una, `_anchor` il primo di loro: la
       storia entra prima di lui. */
    this._shownKey = null;
    this._reading = 0;
    /* **Una lettura sola disegna: l'ultima.** Due riletture della stessa
       conversazione insieme — un resync durante un cambio, un
       `session_boundary` durante un resync — disegnavano ognuna la sua
       storia, e il filo usciva doppio. Chi parte dopo sa di piu', e
       quella partita prima si scarta da se' quando torna. */
    this._readGen = 0;
    this._live = new WeakSet();
    this._anchor = null;
    /* Il markdown com'e' arrivato, per bolla. Si copia il sorgente e non il
       reso: le recinzioni dei blocchi di codice sono esattamente cio' che
       serve quando una risposta si incolla altrove. `WeakMap` perche' la
       chiave e' il nodo, e una ricarica del filo li butta tutti. */
    this._source = new WeakMap();
    /* Lo stesso markdown, per blocco: togliere un blocco di passaggio deve
       toglierlo anche da cio' che Copia copia. */
    this._blockSource = new WeakMap();
    /* **La frase fra un passo e l'altro.** Il testo che Jenny scrive prima di
       uno strumento («ok, la cerco e te la metto in un file») resta a schermo
       mentre lo strumento gira, e se ne va quando arriva il testo dopo — o a
       fine turno. E' la regola della rilettura: `transcript_replay.py`
       (`demote_interrupted_assistant`) fa di quel testo un pensiero, e la casa
       non lo mostra. Dal vivo restavano tutte, una sotto l'altra: sul s22 il
       28/10/2026 una ricerca ne lasciava quattro che dicevano la stessa cosa,
       e riaprendo l'app ne restava una. `_lastSegment` e' l'ultimo blocco
       chiuso da `_streamEnd`, che diventa di passaggio se lo segue un passo. */
    this._lastSegment = null;
    this._passing = [];
    /* L'allegato dietro ogni pastiglia di file: il percorso che il ponte
       nativo sa aprire non sta nell'URL firmato (v. `_openMediaFile`). */
    this._files = new WeakMap();
    /* I secondi dell'ultimo `turn_end`, in attesa che la bolla si chiuda. */
    this._seconds = null;
    /* Le annotazioni nei cassetti del turno in corso (`journalNotes`): lo
       strumento gira prima che la risposta abbia una bolla, e il segno si
       disegna quando la bolla si chiude. */
    this._pendingJournal = [];
    /* I quaderni proposti nel turno in corso, per il tasto «Apri». */
    this._pendingMoves = [];
    /* Chi apre un quaderno (il guscio, `home-app.js`): senza, il tasto non c'e'. */
    this.onOpenNotebook = null;
    /* L'ultimo invio, finché il gateway non ha dimostrato di averlo preso.
       `null` = non c'è niente da riprendere. */
    this._pendingSend = null;
    /* Il campo del messaggio è del guscio, non del filo: quando un messaggio
       torna indietro, il testo glielo ridà lui. */
    this.onSendRejected = null;
    /* Chi rilegge il filo dopo un `session_boundary` (v. `_message`). */
    this.onSessionBoundary = null;
    /* Chi sa dire se un testo con `work_id` racconta un lavoro che e' gia' una
       bolla (`WorkBubbles.claims`): quel testo non si disegna due volte. */
    this.claimsWork = null;
    /* Chi rimanda un messaggio dopo un errore del modello (il «Riprova» sotto
       la risposta che non e' arrivata): riceve il testo da rimandare. */
    this.onRetry = null;
    /* Il testo di ogni bolla dell'utente, e se aveva allegati: e' quel che
       «Riprova» rimanda. */
    this._userSent = new WeakMap();

    /* **Il posto vivo**, sempre sotto i messaggi e sopra la fascia: le bolle di chi sta
       lavorando adesso (`home-work.js`) e, per ultima, la riga di Jenny al
       lavoro (`home-activity.js`, un divisore e non una bolla). I messaggi entrano sopra di lui, quindi chi
       lavora resta sotto l'ultima cosa detta, come un messaggio in arrivo. */
    this.live = document.createElement('div');
    this.live.className = 'home-live';
    this.live.hidden = true;
    this.el.appendChild(this.live);

    /* **La fascia** (06/10/2026, `home-band.js`), dopo il posto vivo: l'ultimo
       figlio del filo in assoluto. Alta meta' di Jenny, ci scende la coda
       dell'ultima risposta (una regola `:has()` in home-style.css, niente JS),
       ed e' il posto per far vedere chi lavora. La sagoma sopra la fascia la
       rifa `this.band`, che le consegna il guscio. */
    this.bandEl = document.createElement('div');
    this.bandEl.className = 'home-band';
    this.el.appendChild(this.bandEl);

    /* **Uno `scroll` non e' sempre un gesto.** Quando una riga compare sotto il
       filo — gli allegati in attesa, la riga di lavoro — il contenitore si
       accorcia: `scrollTop` resta dov'e', la distanza dal fondo cresce, e il
       browser emette uno `scroll` che nessun dito ha causato. Riducendo tutto a
       `_stick = _atBottom()` quell'evento staccava l'aggancio da solo, e da li'
       in poi la chat smetteva di seguire i messaggi nuovi: bastava allegare una
       foto.

       I due casi si distinguono dalla direzione: **solo un dito porta
       `scrollTop` indietro.** Un accorciamento lo lascia fermo. */
    this._lastTop = 0;
    this._stick = true;
    this.el.addEventListener('scroll', () => this._onScroll());
    /* Dove eri rimasto a leggere, per conversazione: la distanza dal fondo
       (v. `restorePlace`). Niente se eri in fondo, che e' dove si torna. */
    this._marks = new Map();
    /* «↓» sopra il composer: compare quando sei risalito, col pallino se
       intanto sotto e' arrivato qualcosa. Sta dentro `#home-chat`, quindi
       sale nel velo con lei. E' il FAB dell'officina (`#chat-scroll-fab`). */
    this._newBelow = false;
    this.fab = document.createElement('button');
    this.fab.type = 'button';
    this.fab.className = 'home-scroll-fab';
    this.fab.innerHTML = '<i class="ti ti-chevron-down" aria-hidden="true"></i>'
      + '<span class="home-scroll-fab-dot" aria-hidden="true"></span>';
    this.fab.addEventListener('click', () => this.scrollToBottom());
    this.el.parentElement?.appendChild(this.fab);

    /* Delegato, e non un ascoltatore per bolla: le bolle sono centinaia dopo
       tre pagine di storia. Come in officina, e per la stessa ragione — la CSP
       del guscio e' `script-src 'self'`, quindi niente `onclick` scritto nel
       markup. */
    this.el.addEventListener('click', (e) => this._onClick(e));

    /* La pagina precedente: stessa macchina dell'officina
       (`shared/history-pager.js`), appigli diversi. Qui il filo e' il proprio
       contenitore di scorrimento — in officina lo scroller e' il documento e
       l'evento arriva a `window`, che sono due oggetti diversi; questa e' la
       sola asimmetria fra i due gusci. */
    this.pager = new HistoryPager({
      scroller: () => this.el,
      listenOn: this.el,
      container: () => this.el,
      pageSize: HISTORY_PAGE_SIZE,
      begin: () => this._beginHistoryPage(),
      prepend: (messages) => this.prependTurns(messages),
      // In cima a tutto: se sopra entra una pagina, `ensureReach` lo rimette
      // primo lui. Il primo figlio del filo porta il `margin-top:auto` che
      // appoggia al fondo una conversazione corta, e il bottone se lo prende
      // volentieri: finisce subito sopra il messaggio piu' vecchio.
      mount: (node) => this.el.insertBefore(node, this.el.firstChild),
      label: () => i18n.t('chat.loadPrevious'),
    });
    /* **Una volta sola, qui.** Stava in fondo a `load()`, e `bindInfiniteScroll`
       non ha guardia: un ascoltatore in piu' a ogni ricarica. Finche' una
       ricarica capitava solo dopo un `session_boundary` non si notava; da
       quando cambiare quaderno *e'* una ricarica, sarebbe uno in piu' per ogni
       cambio. L'officina lo lega una volta sola dal suo `setupInfiniteScroll`,
       per la stessa ragione. */
    this.pager.bindInfiniteScroll();
  }

  /* Un tocco nel filo. **I link vengono prima di tutto**, e sono tutti
     intercettati: il markdown di Jenny finisce in `innerHTML`, e un
     `[x](workshop.html)` lasciato navigare ricaricava la casa senza il `#bs=`
     — de-autenticata, API e websocket morti fino a che l'app non veniva
     uccisa. La regola e' quella dell'officina, in `shared/content-link.js`.
     `defaultPrevented` e' il segno di un `<a>` che ha gia' un padrone: oggi
     in casa non ce n'e', ma il primo che arrivera' non deve prendersi anche
     l'avviso del link inerte (e' successo in officina). */
  _onClick(e) {
    /* Una pastiglia di file e' un `<a>` anche lei, ma non e' un link del
       testo: non e' Jenny ad averla scritta, e la regola dei link la dava per
       inerte (una regressione di `a1b8b1e3`). Si apre col visore. */
    const file = e.target.closest('a.home-file');
    if (file && this.el.contains(file)) {
      e.preventDefault();
      this._openMediaFile(this._files.get(file) || { url: file.getAttribute('href') });
      return;
    }
    /* Non solo `a[href]`: un `<area href>` o un link SVG sono link anche loro,
       e un tocco lasciato passare navigava il frame principale. */
    const link = contentLinkOf(e.target);
    if (link && this.el.contains(link)) {
      if (!e.defaultPrevented) this._openLink(e, link);
      return;
    }
    const offer = e.target.closest('.home-move-offer');
    if (offer && this.el.contains(offer)) {
      this.onOpenNotebook?.(offer.dataset.notebook);
      return;
    }
    const mark = e.target.closest('.home-journal-mark');
    if (mark && this.el.contains(mark)) {
      this._toggleJournal(mark);
      return;
    }
    const btn = e.target.closest('.home-copy');
    if (btn && this.el.contains(btn)) this._copy(btn.closest('.home-msg'));
  }

  /* Tre esiti, e in nessuno la pagina naviga: l'ancora scorre il filo, un'altra
     origine si apre fuori dalla WebView, il resto lo dice. La regola e' in
     `shared/content-link.js` (ci stava anche per il fumetto della minichat,
     tolta il 05/10/2026). */
  _openLink(e, a) {
    openContentLink(e, a, this.el, (type) => showToast(i18n.t('common.linkNotOpenable'), type));
  }

  /* La guardia contro la pagina che arriva dopo un cambio di conversazione.
     Era gia' quella vera del session manager quando in casa la conversazione
     era una sola — «il giorno che le conversazioni diventassero due non sarebbe
     una bugia da scoprire». Quel giorno e' arrivato, e qui non c'e' stato
     niente da cambiare. */
  _beginHistoryPage() {
    const key = sessionManager.currentKey;
    if (!key) return null;
    let stale = false;
    return {
      fetch: async (limit, cursor) => {
        const res = await sessionManager.loadThread(key, limit, cursor);
        stale = !!res.stale;
        return res.thread;
      },
      stale: () => stale,
    };
  }

  /* ── Storia ── */

  /** Carica la conversazione e la disegna. Ritorna il numero di messaggi, o
   *  `null` se la risposta non e' piu' di questo filo: la conversazione e'
   *  cambiata nel frattempo, o una lettura partita dopo ne ha preso il posto.
   *  Anche un errore di una lettura scavalcata si scarta: decide l'ultima. */
  async load() {
    return this._read(false);
  }

  /* La lettura e il disegno, per `load` e `reload`. Con `fresh` quel che c'e'
     a schermo se ne va: subito se la conversazione e' un'altra — i suoi
     messaggi non sono di questa — e solo a storia arrivata se e' la stessa,
     cosi' una rilettura non lascia il filo vuoto per il tempo di un giro.
     Quel che e' nato dal vivo durante la lettura resta, sotto la storia. */
  async _read(fresh) {
    const gen = ++this._readGen;
    const key = sessionManager.currentKey;
    const switched = key !== this._shownKey;
    this._shownKey = key;
    if (fresh && switched) {
      /* Tutto, compreso quel che era nato dal vivo: era dell'altra. */
      this.el.querySelectorAll(THREAD_NODES).forEach((n) => n.remove());
      this._live = new WeakSet();
      this._empty = true;
      /* Il cursore appartiene alla conversazione che se ne sta andando.
         `adopt` lo riscrivera' a fetch riuscita — ma se la fetch fallisce lo
         schermo resta vuoto e il cursore resta quello dell'altra: una scorsa
         in su incollerebbe in cima la storia del quaderno sbagliato. Per
         questo `reset()` esiste, e il suo commento dice proprio «si chiama al
         cambio di conversazione». */
      this.pager.reset();
    }
    this._reading += 1;
    let res;
    try {
      res = await sessionManager.loadThread(key, HISTORY_PAGE_SIZE);
    } catch (err) {
      if (gen !== this._readGen) return null;
      throw err;
    } finally {
      this._reading -= 1;
    }
    if (gen !== this._readGen || res.stale) return null;
    const { thread } = res;
    this.rev = thread?.rev || null;
    /* Cosa se ne va si decide adesso, a storia arrivata, e non alla partenza:
       nel frattempo puo' essere entrata in cima una pagina piu' vecchia
       chiesta con lo scorrimento. Contata alla partenza restava a schermo, la
       storia recente le finiva sopra (Q3, A3, Q1, A1), e `adopt` qui sotto
       rimetteva il cursore che la stessa pagina l'avrebbe riaggiunta al tocco
       dopo. Il cursore che si adotta e' quello della storia appena letta,
       quindi tutto quel che non e' nato dal vivo va via: la pagina si
       richiede, ed entra al suo posto. */
    if (fresh) {
      this.el.querySelectorAll(THREAD_NODES).forEach((n) => {
        if (!this._live.has(n)) n.remove();
      });
    }
    const messages = thread?.messages || [];
    /* La storia va prima del primo nodo nato dal vivo. */
    this._anchor = [...this.el.querySelectorAll(THREAD_NODES)].find((n) => this._live.has(n)) || null;
    try {
      this._inBatch(() => {
        for (const turn of this._buildTurns(messages)) {
          if (turn.boundary) this._appendBoundary();
          else if (turn.user) this._appendUser(turn.text, turn.origin, turn.media, false, turn.at, turn.device);
          else this._appendAssistant(turn.content, turn.media, false, turn.latencyMs, turn.at, turn.error,
            turn.journal, turn.moves);
        }
      });
    } finally {
      this._anchor = null;
    }
    this.onThreadChange?.();
    if (!this._reading) this._live = new WeakSet();
    this._empty = !this.el.querySelector(THREAD_NODES);
    this.syncEmpty();
    this.pager.adopt(thread?.page);
    /* Dove eri rimasto, o in fondo (v. `restorePlace`): la prima parte e'
       sincrona, e `ensureReach` qui sotto misura gia' il filo al suo posto. */
    this.restorePlace();
    /* Dopo il disegno e dopo l'aggancio al fondo: `ensureReach` misura se il
       filo trabocca, e prima del disegno la risposta sarebbe sempre "no". */
    this.pager.ensureReach();
    return messages.length;
  }

  /** Una pagina piu' vecchia, in cima. Lo specchio del giro di `load`.
   *
   *  I turni si invertono e ognuno entra come primo figlio: inseriti a uno a uno
   *  in cima, l'ordine finale torna quello giusto. E' lo stesso giro che fa
   *  l'officina con `_renderThreadMessagesToTop`; a essere diverso e' solo cosa
   *  sopravvive a `_buildTurns`, cioe' il testo e gli allegati.
   */
  prependTurns(messages) {
    this._inBatch(() => {
      for (const turn of this._buildTurns(messages).reverse()) {
        if (turn.boundary) this._appendBoundary(true);
        else if (turn.user) this._appendUser(turn.text, turn.origin, turn.media, true, turn.at, turn.device);
        else this._appendAssistant(turn.content, turn.media, true, turn.latencyMs, turn.at, null,
          turn.journal, turn.moves);
      }
    });
    this.onThreadChange?.();
  }

  /** Molti messaggi in fila, e la sagoma di Jenny ricalcolata **una volta**,
   *  alla fine.
   *
   *  `band.refresh()` misura l'ultimo blocco, e ogni misura dopo una
   *  scrittura forza il layout: a ogni `_append` una pagina di N turni ne
   *  costava N. Il risultato di una misura sola in fondo e' lo stesso, perche'
   *  conta solo com'e' il fondo quando la pagina e' finita. */
  _inBatch(fill) {
    const outer = !this._batching;
    this._batching = true;
    try {
      fill();
    } finally {
      if (outer) {
        this._batching = false;
        this.band?.refresh();
      }
    }
  }

  /* I messaggi persistiti diventano turni. E' la versione di casa di
     `_buildTurns`: stessa spina dorsale, ma di un turno dell'assistente
     sopravvivono solo il testo e gli allegati. Pensieri, strumenti e modifiche
     ai file vengono letti e buttati qui, una volta sola, invece di essere
     filtrati in dieci posti piu' in la'. */
  _buildTurns(messages) {
    const turns = [];
    let current = null;
    const flush = () => { if (current) turns.push(current); current = null; };
    for (const msg of messages) {
      /* Di un lavoro lungo (`/gardener`, `/dream`) la storia tiene due testi:
         l'annuncio («Gardening…») e il resoconto. L'annuncio non si mostra mai:
         o il lavoro e' ancora in corso, e allora e' la bolla a dirlo, o e'
         finito, e allora c'e' il resoconto. */
      if (msg.workStart) continue;
      if (msg.session_boundary) {
        flush();
        turns.push({ boundary: true });
        continue;
      }
      const role = msg.role || (msg.kind === 'user' ? 'user' : 'assistant');
      if (role === 'user') {
        flush();
        turns.push({
          user: true,
          text: msg.text || msg.content || '',
          origin: msg.origin,
          device: msg.device || null,
          media: Array.isArray(msg.media) ? msg.media : [],
          at: msg.sentAt ?? null,
        });
        continue;
      }
      const turnId = msg.turnId || msg.turn_id;
      // Regola 4: senza id non si accorpa. Mai.
      if (!current || !turnId || current.turnId !== turnId) {
        flush();
        current = { turnId, content: '', media: [], latencyMs: null, at: null };
      }
      /* L'ora di una risposta e' quella del suo ultimo pezzo: quando il turno
         si e' chiuso, come dal vivo (v. `_resetTurn`). */
      if (msg.sentAt != null) current.at = msg.sentAt;
      if (Array.isArray(msg.media) && msg.media.length) current.media.push(...msg.media);
      /* I secondi sono l'unica cosa che l'officina teneva e la casa buttava e
         che adesso serve anche qui: sono meta' della riga che separa una
         risposta dalla successiva. */
      if (msg.latencyMs != null) current.latencyMs = msg.latencyMs;
      /* Il turno e' finito in un errore del modello (`error: {kind}`, dal
         gateway): la bolla avra' il suo «Riprova» anche dopo una ricarica. */
      if (msg.error) current.error = msg.error;
      /* Una riga di traccia (`kind: 'trace'`, `role: 'tool'`) e' il resoconto di
         uno strumento, e in casa non e' niente: si butta, punto. L'officina la
         tiene come ripiego quando il turno non ha altro testo, ma quel ripiego
         qui sarebbe il difetto — un turno in cui Jenny ha solo lavorato senza
         dire niente deve restare muto, non mostrare `read_file: sensori.json`.
         E la condizione "solo se non c'e' altro testo" non si puo' nemmeno
         valutare qui: il testo vero arriva *dopo*, in un frame successivo dello
         stesso turno. */
      if (msg.kind === 'trace' || msg.role === 'tool') {
        /* Tranne una cosa: quel che il turno ha messo in un cassetto, che diventa
           il segno sotto la risposta (v. `_journalMarkOf`). */
        const events = msg.toolEvents || msg.tool_events;
        const notes = journalNotes(events);
        if (notes.length) (current.journal ||= []).push(...notes);
        const moves = moveOffers(events);
        if (moves.length) (current.moves ||= []).push(...moves);
        continue;
      }
      const text = msg.text || msg.content || '';
      if (text) current.content += (current.content ? '\n\n' : '') + text;
    }
    flush();
    // Un turno senza niente da mostrare non e' una bolla vuota: non e' niente.
    return turns.filter((t) => t.boundary || t.user || t.content || t.media.length);
  }

  /* ── Frame dal vivo ── */

  /** Un frame del websocket. Tutto cio' che non e' qui sotto non riguarda la casa. */
  handleFrame(msg) {
    if (!this._belongsHere(msg)) return;
    /* Fin qui il filo e' completo: la revisione che `isStale` confronta. La
       portano solo `turn_end` e l'eco di un messaggio scritto altrove, cioe'
       i frame dopo i quali non resta niente in volo. */
    if (msg.rev && (msg.event === 'turn_end' || msg.event === 'user')) this.rev = msg.rev;
    /* La prova che l'ultimo invio è entrato: il gateway sta rispondendo di
       qualcosa che non è un rifiuto. Da qui in poi quella bolla non è più in
       sospeso, e un errore che arrivasse dopo è un errore di altro. */
    if (msg.event !== 'error') this._pendingSend = null;
    if (!this._crossesTurn(msg)) return;
    switch (msg.event) {
      case 'error': this._error(msg); break;
      case 'delta': this._delta(msg.text || ''); break;
      case 'stream_end': this._streamEnd(msg.text); break;
      case 'message': this._message(msg); break;
      case 'user': this._externalUser(msg); break;
      case 'turn_end': this._turnEnd(msg.latency_ms); break;
      default: break;
    }
  }

  _belongsHere(msg) {
    const chatId = msg.chat_id;
    return !chatId || chatId === sessionManager.currentChatId;
  }

  /* Regola 3 e 4: il confine di turno. Un `turn_end` di un altro turno non
     riguarda quello aperto e va ignorato; qualunque altro frame di un turno
     nuovo chiude quello in corso. */
  _crossesTurn(msg) {
    const TURN_SCOPED = ['delta', 'stream_end', 'message', 'turn_end'];
    if (!TURN_SCOPED.includes(msg.event)) return true;
    const turnId = msg.turn_id || msg.turnId || null;
    if (!turnId || turnId === this.turnId) return true;
    if (this.turnId === null) { this.turnId = turnId; return true; }
    if (msg.event === 'turn_end') return false;
    this._resetTurn();
    this.turnId = turnId;
    return true;
  }

  /* Formule e diagrammi **non** si disegnano qui (ne' in `_flushDelta`, che
     scrive quel che qui si accumula), ed e' l'unico dei quattro punti in cui
     si scrive markdown a restarne fuori: qui il testo sta ancora
     arrivando. Una formula a meta' (`$$E = mc`) non e' una formula, e un
     diagramma a meta' e' un errore di sintassi — mermaid pianterebbe a schermo
     il proprio messaggio in inglese, e lo rifarebbe a ogni pezzetto. Si disegna
     quando il testo e' finito, cioe' in `_streamEnd` qui sotto. */
  _delta(text) {
    if (!text) return;
    this.buffer += text;
    this._ensureBlock();
    this._scheduleRender();
  }

  /* **Un disegno per fotogramma, non uno per delta.** Ogni resa riparsa il
     markdown del buffer intero: farla a ogni pezzetto, che arriva ogni poche
     decine di millisecondi, e' lavoro quadratico nella lunghezza della
     risposta, sul thread che deve anche scorrere. Come l'officina
     (`_scheduleFlush`): i delta si accumulano, e il fotogramma dopo li
     disegna tutti insieme. La resa finale non aspetta — la fa `_streamEnd`. */
  _scheduleRender() {
    if (this._frame !== null) return;
    this._frame = requestAnimationFrame(() => this._flushDelta());
  }

  _flushDelta() {
    this._frame = null;
    if (!this.blockNode || !this.buffer) return;
    this.blockNode.innerHTML = renderMarkdown(this.buffer);
    this.band?.refresh();
    this._follow();
  }

  _cancelRender() {
    if (this._frame === null) return;
    cancelAnimationFrame(this._frame);
    this._frame = null;
  }

  /* Regola 1: il testo di `stream_end` e' opzionale, il buffer e' la riserva.
     Regola 3: il blocco si chiude qui, o il segmento dopo gli si incolla. */
  _streamEnd(fullText) {
    this._cancelRender();
    /* Un segmento che ha perso **tutti** i suoi delta — il bus li scarta
       sotto backpressure, e lo `stream_end` porta allora il testo intero
       — arriva qui senza un blocco aperto: il testo
       va disegnato lo stesso, o dal vivo la risposta non si vede. */
    if (!this.blockNode && fullText) this._ensureBlock();
    const finalText = fullText || this.buffer;
    if (this.blockNode && finalText) {
      this.blockNode.innerHTML = renderMarkdown(finalText);
      renderRich(this.blockNode);
      this._register(this.turnNode, finalText, this.blockNode);
      this._lastSegment = this.blockNode;
      this.band?.refresh();
    }
    this.blockNode = null;
    this.buffer = '';
    this._follow();
  }

  /* Regola 2: testo gia' completo, blocco proprio, chiuso subito. */
  _message(msg) {
    if (msg.session_boundary) {
      // Il punto di lettura era in una storia che non c'e' piu'.
      this._marks?.delete(sessionManager.currentKey);
      /* Il contesto e' stato azzerato. La storia sul server e' cambiata sotto i
         piedi: si ricarica invece di indovinare. La rilettura la fa chi sa
         dire che non e' arrivata (il guscio, `onSessionBoundary`); senza di
         lui un fallimento finisce nel log, e non resta un rifiuto di promessa
         che nessuno prende. */
      if (this.onSessionBoundary) this.onSessionBoundary();
      else this.reload().catch((err) => console.warn('home: thread reload after a boundary failed', err));
      return;
    }
    /* Prima di buttare i frame degli strumenti, quel che hanno messo nei
       cassetti: il segno sotto la risposta (v. `_journalMarkOf`). */
    const notes = journalNotes(msg.tool_events);
    if (notes.length) this._pendingJournal.push(...notes);
    const moves = moveOffers(msg.tool_events);
    if (moves.length) this._pendingMoves.push(...moves);
    /* Un passo: il testo scritto subito prima diventa di passaggio. Il testo
       di un frame `progress` lo e' da se' (la rilettura ne fa una riga di
       strumento, che la casa butta): si vede finche' non arriva il seguito. */
    const step = msg.kind === 'progress' || msg.kind === 'tool_hint';
    if (step && (msg.tool_events?.length || msg.text)) {
      if (this.buffer) this._streamEnd();
      this._markPassing(this._lastSegment);
    }
    // Un suggerimento di strumento e' esattamente cio' che la casa non mostra.
    if (msg.kind === 'tool_hint') return;
    // L'annuncio o il resoconto di un lavoro che e' gia' una bolla: la bolla
    // dice la stessa cosa, e meglio.
    if (msg.work_id && this.claimsWork?.(msg.work_id)) return;
    if (msg.text || msg.media_urls?.length) {
      if (this.buffer) this._streamEnd();
      /* Solo un testo che risponde prende il posto della frase di passaggio.
         Un file da solo (`message` col solo allegato) o un avviso d'errore (il
         turno finito vuoto) no: quella frase era il riassunto del file, o la
         risposta prima che il turno si perdesse, e resta. La rilettura fa lo
         stesso (`transcript_replay.py`, `restore_demoted`). */
      if (step || (msg.text && !msg.error)) this._dropPassing();
      else this._keepPassing();
    }
    if (msg.text) {
      const block = document.createElement('div');
      block.className = 'home-block';
      block.innerHTML = renderMarkdown(msg.text);
      renderRich(block);
      this._ensureTurn().appendChild(block);
      this._register(this.turnNode, msg.text, block);
      if (step) this._markPassing(block);
      // `blockNode` resta null: il delta dopo apre il proprio.
    }
    if (msg.media_urls?.length) this._appendMedia(this._ensureTurn(), msg.media_urls);
    if (msg.error && this.turnNode) this._offerRetry(this.turnNode);
    if (msg.text || msg.media_urls?.length) this.band?.refresh();
    this._follow();
  }

  /** Un messaggio appena partito da questa finestra.
   *
   *  Lo disegna il client, e non e' una scorciatoia: il gateway rimanda l'eco
   *  solo dei messaggi entrati da *altri* canali. Per il websocket
   *  `_handle_session_turn_started` esce subito, quindi se non lo disegnassimo
   *  qui la propria domanda comparirebbe solo dopo un ricaricamento.
   */
  appendOwn(text, media = []) {
    this._resetTurn();
    const node = this._appendUser(text, null, media, false, Date.now());
    this.scrollToBottom();
    /* Partito non vuol dire entrato: il gateway può ancora rifiutarlo (un
       allegato che non riesce ad aprire). Finché non arriva niente che dimostri
       il contrario questa bolla è "in sospeso", ed è così che un rifiuto sa
       *quale* togliere senza bisogno di un identificativo sul filo. */
    this._pendingSend = { node, text };
  }

  /* Un rifiuto del gateway. Le parole e la famiglia le decide il modulo
     condiviso con l'officina; qui si decide dove va a finire.

     La riga sta **nel filo** e non nella striscia dello stato: la striscia dice
     com'è il collegamento adesso e se ne va da sola, questo invece è un fatto
     della conversazione — quel messaggio non è entrato — e deve restare lì dove
     c'era la bolla, anche se scorri via e torni. */
  _error(msg) {
    const { text, blocksSend } = describeWireError(msg, (key) => i18n.t(key));
    if (blocksSend) {
      const returned = this._takeBackPendingSend();
      if (returned !== null && this.onSendRejected) this.onSendRejected(returned);
    }
    this._appendNote(text);
  }

  /** Un rifiuto deciso **qui**, dal telefono: un allegato che sfora i tetti.
   *
   *  Passa dalla stessa porta di un rifiuto del gateway perche' e' la stessa
   *  cosa detta un istante prima: le parole sono quelle, e il messaggio non e'
   *  partito, quindi non c'e' nessuna bolla da riprendere.
   */
  noteRefusal(reason) {
    this._error({ reason });
  }

  /* La bolla del messaggio rifiutato se ne va, e il suo testo torna a chi ce
     l'ha dato. Gli allegati no: l'allegato *è* la cosa rifiutata, e il server
     butta il lotto intero senza dire quale file fosse. */
  _takeBackPendingSend() {
    const pending = this._pendingSend;
    this._pendingSend = null;
    if (!pending?.node?.isConnected) return null;
    pending.node.remove();
    return pending.text || '';
  }

  /* Una riga sobria nel filo: nessuna icona, nessun pannello. In casa una cosa
     che non è andata si dice come si direbbe a voce. */
  _appendNote(text) {
    const node = document.createElement('div');
    node.className = 'home-note';
    node.textContent = text;
    this._append(node);
    /* Segue il fondo se ci si era; se stai rileggendo piu' su, il «↓» si
       accende invece di strapparti via (giro UX del 10/10/2026). */
    this._follow();
    return node;
  }

  /** Una riga detta dal guscio, come quelle di un rifiuto: la rilettura
   *  dopo la toglie. Torna il nodo. */
  showNote(text) {
    return this._appendNote(text);
  }

  /** Vero se nel filo non c'e' niente: ne' messaggi ne' righe. */
  get isBlank() {
    return !this.el.querySelector(THREAD_NODES);
  }

  /* Un messaggio entrato da un'altra superficie mentre la chat e' aperta. */
  _externalUser(msg) {
    const text = msg.text || '';
    const media = msg.media_urls || msg.media || [];
    if (!text && !media.length) return;
    this._resetTurn();
    this._appendUser(text, msg.origin_channel || msg.origin, media, false, Date.now(), msg.device);
    this._follow();
  }

  _turnEnd(latencyMs) {
    this._seconds = latencyMs != null ? latencyMs : null;
    this._resetTurn();
  }

  /** Chiude la bolla del turno in corso.
   *
   *  **La coda si posa qui e in nessun altro posto del percorso vivo**, e la
   *  ragione e' che i modi di finire un turno sono piu' d'uno: il `turn_end`
   *  del gateway, ma anche un frame di un turno nuovo che scavalca quello
   *  aperto (`_crossesTurn`) e un invio partito da qui (`appendOwn`). Con la
   *  coda attaccata al solo `turn_end`, una risposta seguita subito da
   *  un'altra restava senza — cioe' proprio il caso che si voleva separare.
   */
  _resetTurn() {
    /* Un turno che si chiude senza `stream_end` (lo scavalca un turno nuovo)
       non deve perdere i delta rimasti in coda per il fotogramma dopo: si
       disegnano adesso, prima di chiudere la bolla. */
    if (this._frame !== null) {
      this._cancelRender();
      this._flushDelta();
    }
    const dropped = this._dropPassing();
    this._lastSegment = null;
    /* Un turno di soli passi, senza niente dopo: come nella rilettura, non e'
       una bolla vuota con l'ora sotto. E' niente. */
    if (dropped && this.turnNode && !this.turnNode.children.length) {
      this.turnNode.remove();
      this.turnNode = null;
    }
    if (this.turnNode) {
      const closed = this.turnNode;
      /* Dal vivo l'ora e' quella dell'orologio di qui: il frame non ne porta
         una, e per una risposta appena chiusa dice la stessa cosa del
         transcript (che la rilettura usera'). */
      this._tailOf(closed, this._seconds, Date.now());
      this._journalMarkOf(closed, this._pendingJournal);
      this._moveOfferOf(closed, this._pendingMoves);
      /* La coda dell'ultima risposta scende nella fascia e non allunga niente;
         quella di una risposta con qualcosa sotto si', e chi era in fondo deve
         restarci. Solo se la bolla e' ancora nel filo — `reload()` passa di
         qui con un nodo che sta per essere buttato. */
      if (closed.isConnected) this._follow();
    }
    this._seconds = null;
    this._pendingJournal = [];
    this._pendingMoves = [];
    this.turnNode = null;
    this.blockNode = null;
    this.buffer = '';
    this.turnId = null;
  }

  /** Il filo a schermo e' rimasto indietro rispetto a quello vero?
   *
   *  Una richiesta piccola (l'ultima riga) e un confronto di revisione: la
   *  casa la fa tornando visibile, e rilegge solo se serve. Le chat scritte
   *  dal Mac arrivano gia' dal vivo; questo copre quel che il vivo puo'
   *  perdere. Senza revisione (gateway vecchio, lettura mai riuscita) o con
   *  una lettura in corso risponde «no». Un turno in corso lo esclude chi
   *  chiama (la casa sa se Jenny sta lavorando): a meta' turno il filo cresce
   *  e rileggerlo strapperebbe la risposta che sta arrivando. */
  async isStale() {
    if (!this.rev || this._reading) return false;
    const key = sessionManager.currentKey;
    const known = this.rev;
    let data;
    try {
      data = await api.fetchWebuiThread(key, { limit: 1 });
    } catch {
      return false;
    }
    if (key !== sessionManager.currentKey || known !== this.rev) return false;
    return Boolean(data?.rev) && data.rev !== known;
  }

  /** Ributta giu' la conversazione da capo.
   *
   *  E' anche il modo in cui si cambia quaderno: la chiave la sa il session
   *  manager, quindi qui non c'e' un parametro da passare — si svuota e si
   *  rilegge chi e' attuale adesso.
   */
  async reload() {
    this._resetTurn();
    /* La bolla in sospeso muore col DOM che la conteneva. Senza azzerarla, un
       rifiuto in arrivo — che e' l'unico frame che la lascia in vita — la
       toglierebbe da un nodo staccato e rimetterebbe quel testo nel campo di
       un'altra conversazione. */
    this._pendingSend = null;
    return this._read(true);
  }

  /* ── Disegno ── */

  _appendUser(text, origin, media, toTop = false, at = null, device = null) {
    const node = document.createElement('div');
    node.className = 'home-msg home-msg-user';
    const badge = this._originBadge(origin, device);
    if (badge) node.appendChild(badge);
    if (text) {
      const block = document.createElement('div');
      block.className = 'home-block';
      // Testo dell'utente: mai markdown. E' quello che ha scritto, alla lettera.
      block.textContent = text;
      node.appendChild(block);
    }
    if (media?.length) this._appendMedia(node, media);
    if (text) this._userSent.set(node, { text, media: Boolean(media?.length) });
    /* L'ora, sotto la bolla e fuori dalla sua pelle (che e' di `.home-block`):
       quel che hai scritto ha la sua, come le risposte. */
    const time = this._timeEl(at);
    if (time) node.appendChild(time);
    /* L'ora in millisecondi sul nodo: chi mette qualcosa al suo posto nel filo
       (la scheda di un piano finito, `HomePlan.place`) la legge da qui, anche
       dopo una rilettura della storia. */
    if (Number(at) > 0 && node.dataset) node.dataset.at = String(Number(at));
    return this._append(node, toTop);
  }

  _appendAssistant(content, media, toTop = false, latencyMs = null, at = null, error = null,
    journal = null, moves = null) {
    const node = document.createElement('div');
    node.className = 'home-msg home-msg-jenny';
    if (content) {
      const block = document.createElement('div');
      block.className = 'home-block';
      block.innerHTML = renderMarkdown(content);
      renderRich(block);
      node.appendChild(block);
      this._register(node, content);
    }
    if (media?.length) this._appendMedia(node, media);
    /* Prima di `_append`, che rifa la sagoma: il nodo va misurato intero. */
    this._tailOf(node, latencyMs, at);
    this._journalMarkOf(node, journal);
    this._moveOfferOf(node, moves);
    if (Number(at) > 0 && node.dataset) node.dataset.at = String(Number(at));
    this._append(node, toTop);
    /* Dopo: `_append` toglie i «Riprova» di prima, e la storia piu' vecchia
       (`toTop`) non ne ha — il presente e' in fondo. */
    if (error && !toTop) this._offerRetry(node);
  }

  /** «Riprova» sotto una risposta finita in un errore del modello.
   *
   *  Rimanda il messaggio dell'utente a cui rispondeva — prima andava
   *  riscritto a mano. Solo per l'ultima cosa detta: qualunque bolla nuova lo
   *  toglie (`_append`), perche' riprovare una domanda di tre messaggi fa
   *  vorrebbe dire rispondere fuori posto. Niente «Riprova» se quel messaggio
   *  aveva allegati: il testo da solo non sarebbe la stessa domanda. */
  _offerRetry(node) {
    if (!node || node.querySelector('.home-retry')) return;
    let prev = node.previousElementSibling;
    while (prev && !String(prev.className).includes('home-msg-user')) {
      if (String(prev.className).includes('home-msg-jenny')) return;
      prev = prev.previousElementSibling;
    }
    const sent = prev && this._userSent.get(prev);
    if (!sent || sent.media) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'home-retry';
    btn.innerHTML = '<i class="ti ti-refresh" aria-hidden="true"></i>';
    const label = document.createElement('span');
    label.textContent = i18n.t('home.retry');
    btn.appendChild(label);
    btn.addEventListener('click', () => {
      btn.remove();
      this.onRetry?.(sent.text);
    });
    node.appendChild(btn);
  }

  /** L'ora di un messaggio (v. `shared/chat-time.js`), o `null` se non ce
   *  n'e' una: una cronologia scritta prima del 06/10/2026 che la sessione non
   *  sa datare resta senza, invece di prendere un'ora inventata. */
  _timeEl(at) {
    const text = chatTime(at);
    if (!text) return null;
    const el = document.createElement('time');
    el.className = 'home-time';
    el.dateTime = chatTimeIso(at);
    el.textContent = text;
    return el;
  }

  /** La riga in coda a una risposta: il Copia, l'ora e i secondi che ci ha
   *  messo.
   *
   *  **E' anche il confine fra una risposta e la successiva.** In casa non c'e'
   *  ne' bolla ne' avatar: due risposte di fila sono due gruppi di paragrafi,
   *  e a occhio diventano un messaggio solo. Serviva qualcosa che dicesse dove
   *  una finisce — e invece di una linea che non fa niente, ci sono le due
   *  cose che uno vorrebbe li'.
   *
   *  Una riga sola, icona e poi tempo, come in officina: «⧉ 11:26 · 4.2s». I
   *  secondi possono mancare del tutto: una consegna proattiva non ha un
   *  turno dietro, quindi nessuno ha misurato niente, e resta «⧉ 11:40».
   */
  /** Il segno discreto dei cassetti: un'icona nella coda della risposta quando
   *  il turno ha annotato qualcosa in un quaderno. Niente frase «l'ho messo
   *  in…» nella risposta (scelta dell'utente, 12/10/2026): chi vuole sapere
   *  dove, tocca, e vede il quaderno e la riga. */
  _journalMarkOf(node, notes) {
    if (!node || !notes?.length || node.querySelector('.home-journal-mark')) return;
    let row = node.querySelector('.home-tail');
    if (!row) {
      row = document.createElement('div');
      row.className = 'home-tail';
      node.appendChild(row);
    }
    const names = [...new Set(notes.map((n) => n.notebook))];
    const label = i18n.t('home.journalMark.label', { notebook: names.join(', ') });
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'home-journal-mark';
    btn.title = label;
    btn.setAttribute('aria-label', label);
    btn.setAttribute('aria-expanded', 'false');
    btn.innerHTML = '<i class="ti ti-notebook" aria-hidden="true"></i>';
    row.insertBefore(btn, row.firstChild);
    const detail = document.createElement('div');
    detail.className = 'home-journal-detail';
    detail.hidden = true;
    for (const note of notes) {
      const line = document.createElement('div');
      line.className = 'home-journal-line';
      const where = document.createElement('span');
      where.className = 'home-journal-where';
      where.textContent = i18n.t('home.journalMark.where', { notebook: note.notebook });
      line.appendChild(where);
      line.appendChild(document.createTextNode(' ' + note.text));
      detail.appendChild(line);
    }
    node.appendChild(detail);
  }

  /** «Ci spostiamo?»: il tasto che apre il quaderno proposto. Resta anche
   *  dopo, nella storia: la proposta e' stata fatta, e toccarla piu' tardi apre
   *  lo stesso quaderno. */
  _moveOfferOf(node, notebooks) {
    if (!node || !notebooks?.length || !this.onOpenNotebook) return;
    if (node.querySelector('.home-move-offer')) return;
    for (const notebook of notebooks) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'home-move-offer';
      btn.dataset.notebook = notebook;
      btn.innerHTML = '<i class="ti ti-notebook" aria-hidden="true"></i> ';
      btn.appendChild(document.createTextNode(i18n.t('home.moveOffer.open', { notebook })));
      node.appendChild(btn);
    }
  }

  _toggleJournal(btn) {
    const detail = btn.closest('.home-msg')?.querySelector('.home-journal-detail');
    if (!detail) return;
    detail.hidden = !detail.hidden;
    btn.setAttribute('aria-expanded', String(!detail.hidden));
  }

  _tailOf(node, latencyMs, at = null) {
    if (!node || node.querySelector('.home-tail')) return;
    /* Solo sulle risposte. Quel che hai scritto tu ha gia' la sua bolla col
       suo bordo: e' separato da se', e un Copia sotto le proprie parole non
       serve a nessuno. Oggi nessun chiamante ci passa una bolla utente — la
       guardia e' perche' la prossima non debba ricordarselo. */
    if (!String(node.className).includes('home-msg-jenny')) return;
    /* Un turno in cui Jenny ha solo lavorato non ha testo da copiare; una
       risposta di sole immagini non ha Copia, ma la sua ora si'. */
    const hasText = Boolean(this._textOf(node));
    const time = this._timeEl(at);
    if (!hasText && !time) return;
    const row = document.createElement('div');
    row.className = 'home-tail';
    if (hasText) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'home-copy';
      btn.title = i18n.t('chat.copy');
      btn.setAttribute('aria-label', i18n.t('chat.copy'));
      btn.innerHTML = '<i class="ti ti-copy" aria-hidden="true"></i>';
      row.appendChild(btn);
    }
    if (time) row.appendChild(time);
    if (latencyMs != null) {
      if (time) {
        const sep = document.createElement('span');
        sep.className = 'home-tail-sep';
        sep.setAttribute('aria-hidden', 'true');
        sep.textContent = '·';
        row.appendChild(sep);
      }
      const s = document.createElement('span');
      s.className = 'home-seconds';
      s.textContent = (latencyMs / 1000).toFixed(1) + 's';
      row.appendChild(s);
    }
    node.appendChild(row);
  }

  /* Il markdown di una bolla si accumula: un turno testo → strumento → testo
     apre piu' blocchi, e copiarne uno solo sarebbe copiare meta' risposta. */
  _register(node, text, block = null) {
    const clean = String(text || '').trim();
    if (!node || !clean) return;
    const before = this._source.get(node);
    this._source.set(node, before ? `${before}\n\n${clean}` : clean);
    if (block) this._blockSource.set(block, clean);
  }

  /** Il blocco resta a schermo finche' non arriva qualcosa dopo (v. `_passing`). */
  _markPassing(block) {
    if (block && block.isConnected && !this._passing.includes(block)) this._passing.push(block);
    if (block === this._lastSegment) this._lastSegment = null;
  }

  /** Le frasi di passaggio restano: non sono piu' di passaggio. */
  _keepPassing() {
    this._passing = [];
  }

  /** Se ne vanno le frasi di passaggio, e con loro il loro pezzo di Copia.
   *  Dice se ce n'erano. */
  _dropPassing() {
    if (!this._passing.length) return false;
    const turns = new Set();
    for (const block of this._passing) {
      if (block.parentNode) turns.add(block.parentNode);
      block.remove();
    }
    this._passing = [];
    for (const turn of turns) {
      const kept = [...turn.querySelectorAll('.home-block')]
        .map((b) => this._blockSource.get(b))
        .filter(Boolean);
      if (kept.length) this._source.set(turn, kept.join('\n\n'));
      else this._source.delete(turn);
    }
    this.band?.refresh();
    return true;
  }

  /* Il sorgente se c'e', altrimenti la rete di `innerText`: perde le
     recinzioni, ma non lascia mai un Copia che non copia niente. */
  _textOf(node) {
    if (!node) return '';
    const registered = this._source.get(node);
    if (registered) return registered;
    return [...node.querySelectorAll('.home-block')]
      .map((el) => (el.innerText || '').trim())
      .filter(Boolean)
      .join('\n\n');
  }

  async _copy(node) {
    const text = this._textOf(node);
    if (!text) return;
    if (!(await copyToClipboard(text))) {
      showToast(i18n.t('chat.copyFailed'), 'error');
      return;
    }
    showToast(i18n.t('chat.copied'), 'success');
  }

  /* Il separatore di un azzeramento del contesto. Nessuna scritta: dire
     "confine di sessione" e' officina. Una riga sottile basta a spiegare perche'
     sopra e sotto non si parlano. */
  _appendBoundary(toTop = false) {
    const hr = document.createElement('div');
    hr.className = 'home-boundary';
    this._append(hr, toTop);
  }

  _originBadge(origin, device = null) {
    if (!origin || origin === 'websocket') return null;
    const known = ORIGINS[origin];
    const badge = document.createElement('div');
    badge.className = 'home-origin';
    const icon = known ? known.icon : 'ti-arrows-exchange';
    const label = !known ? origin
      : (device && known.named) ? i18n.t(known.named, { device }) : i18n.t(known.key);
    badge.innerHTML = `<i class="ti ${icon}" aria-hidden="true"></i>${escapeHtml(label)}`;
    return badge;
  }

  _appendMedia(node, entries) {
    const wrap = document.createElement('div');
    wrap.className = 'home-media';
    for (const raw of entries) {
      const entry = typeof raw === 'string' ? { url: raw } : raw;
      if (!entry.url) continue;
      const kind = mediaKind(entry);
      if (kind === 'image') {
        const img = document.createElement('img');
        img.src = entry.url;
        img.loading = 'lazy';
        img.alt = entry.name || '';
        /* Ingrandimento: la stessa lightbox dell'officina, col suo pinch-zoom.
           Lo zoom del viewport e' disabilitato in tutta l'app, quindi senza
           questa un'immagine si guarda solo alla misura della miniatura. */
        img.addEventListener('click', () => openImageLightbox(entry.url, {
          alt: entry.name || '',
          closeLabel: i18n.t('home.closeImage'),
        }));
        wrap.appendChild(img);
      } else if (kind === 'video') {
        const video = document.createElement('video');
        video.src = entry.url;
        video.controls = true;
        video.preload = 'metadata';
        wrap.appendChild(video);
      } else {
        const chip = document.createElement('a');
        chip.className = 'home-file';
        chip.href = entry.url;
        chip.textContent = entry.name || entry.url;
        this._files.set(chip, entry);
        wrap.appendChild(chip);
      }
    }
    if (wrap.childElementCount) node.appendChild(wrap);
  }

  /** Apre un allegato che il filo non sa mostrare: col ponte nativo, che lo
   *  passa al visore di sistema, come fa l'officina (`mobile-chat.js`,
   *  `_openMediaFile`).
   *
   *  Col ponte presente un fallimento si dice e basta: la WebView non ha un
   *  `DownloadListener` e blocca le schede nuove, quindi `window.open` li'
   *  non sarebbe un ripiego ma un tocco che non fa niente. Fuori dal guscio
   *  nativo (un browser) e' la strada giusta. */
  async _openMediaFile(entry) {
    const bridge = window.JennyNative;
    if (bridge && typeof bridge.openFile === 'function') {
      try {
        // Asincrono: `openFile` risponde con una Promise (v. `shared/native-bridge.js`).
        if (entry.path && await bridge.openFile(entry.path)) return;
      } catch (err) {
        console.warn('home: native openFile failed', err);
      }
      showToast(i18n.t('chat.couldNotOpen', { path: entry.name || entry.path || '' }), 'error');
      return;
    }
    if (entry.url) window.open(entry.url, '_blank');
  }

  /** La bolla dell'assistente del turno, creata al primo frame che la riempie. */
  _ensureTurn() {
    if (!this.turnNode) {
      this.turnNode = document.createElement('div');
      this.turnNode.className = 'home-msg home-msg-jenny';
      if (this.turnNode.dataset) this.turnNode.dataset.at = String(Date.now());
      this._append(this.turnNode);
    }
    return this.turnNode;
  }

  /** Il blocco di testo del segmento di stream in corso. */
  _ensureBlock() {
    if (!this.blockNode) {
      // Il seguito e' arrivato: la frase di passaggio lascia il posto.
      this._dropPassing();
      this.blockNode = document.createElement('div');
      this.blockNode.className = 'home-block';
      this._ensureTurn().appendChild(this.blockNode);
    }
    return this.blockNode;
  }

  _append(node, toTop = false) {
    /* Una cosa nuova in fondo: il «Riprova» di prima non e' piu' dell'ultima
       risposta (v. `_offerRetry`). */
    if (!toTop) for (const old of this.el.querySelectorAll('.home-retry')) old.remove();
    if (toTop) this.el.insertBefore(node, this.el.firstChild);
    else if (this._anchor?.parentNode === this.el) this.el.insertBefore(node, this._anchor);
    else if (this.live?.parentNode === this.el) this.el.insertBefore(node, this.live);
    else this.el.appendChild(node);
    /* Nato mentre una lettura era in volo: e' del presente, e la storia che
       arriva non lo butta (v. `_read`). La storia entra sempre in blocco
       (`_inBatch`), un frame vivo mai. */
    if (this._reading && !this._batching) this._live.add(node);
    if (this._empty) {
      this._empty = false;
      this.syncEmpty();
    }
    /* Il fondo del filo e' cambiato: la sagoma va rifatta **dopo** che il nodo
       e' nel filo e prima che l'occhio ci arrivi. Dentro un blocco di storia
       lo fa `_inBatch`, una volta sola alla fine. */
    if (!this._batching) this.band?.refresh();
    return node;
  }

  /** Mostra o nasconde lo stato vuoto secondo quel che c'e' nel filo. Una
   *  bolla di lavoro conta: una conversazione nuova in cui il giardiniere sta
   *  lavorando non e' vuota. */
  syncEmpty() {
    const empty = document.getElementById('home-empty');
    if (empty) empty.hidden = !this._empty || this.live?.hidden === false;
  }

  /** Il posto vivo e' cambiato: una bolla e' comparsa, cresciuta, sparita.
   *  Si nasconde da vuoto (un figlio vuoto del filo prenderebbe comunque il
   *  suo `gap`), e il fondo si riaggancia solo se ci si era. Acceso, separa
   *  l'ultima risposta dalla fascia: la sagoma va via o torna. */
  liveChanged() {
    this.live.hidden = ![...this.live.children].some((child) => !child.hidden);
    this.syncEmpty();
    this.band?.refresh();
    this._follow();
  }

  /** Una bolla di lavoro finita scende nel filo: da qui e' un messaggio come
   *  gli altri, e quel che arriva dopo le va sotto. */
  settle(node) {
    node.classList.add('home-msg-work');
    this._append(node);
    this.liveChanged();
  }

  /* ── Scorrimento ── */

  /* Un evento di scorrimento, e cosa farne. Metodo e non chiusura nel
     costruttore per una ragione precisa: un listener anonimo li' dentro non si
     puo' esercitare, e il primo banco che ci ho provato **passava a vuoto** —
     non agganciava niente, `_stick` restava vero, ed era proprio quello che
     asseriva. */
  _onScroll() {
    const top = this.el.scrollTop;
    if (this._atBottom()) this._stick = true;
    else if (top < this._lastTop) this._stick = false;
    this._lastTop = top;
    this._mark();
    this._paintFab();
    /* Niente altro, ed e' voluto: lo spazio per Jenny (la fascia e la sagoma,
       `home-band.js`) dipende da cosa c'e' nel filo, non da dove sta. Fino al
       06/10/2026 qui si ricalcolava chi le stava dietro, e il testo tornava a
       capo sotto il dito ogni volta che ti fermavi. */
  }

  _atBottom() {
    const gap = this.el.scrollHeight - this.el.scrollTop - this.el.clientHeight;
    return gap <= STICK_PX;
  }

  /** Vero se il filo sta seguendo il fondo: chi e' risalito a rileggere no. */
  get following() {
    return this._stick;
  }

  /** Segue il fondo, ma solo se ci si era. Se no, il «↓» si accende: sotto
   *  e' arrivato qualcosa che non stai guardando. */
  _follow() {
    if (this._stick) {
      this.scrollToBottom();
      return;
    }
    this._newBelow = true;
    this._paintFab();
  }

  /* Ricorda dove sei, per la conversazione a schermo. Non con il filo nascosto
     o staccato (alto zero): azzererebbe un ricordo buono, come avverte
     `_rememberScrollAnchor` dell'officina. */
  _mark() {
    const key = this._shownKey;
    if (!key || !this.el.clientHeight) return;
    if (this._atBottom()) this._marks.delete(key);
    else this._marks.set(key, this.el.scrollHeight - this.el.scrollTop);
  }

  /** Torna dove eri rimasto in questa conversazione — o in fondo, se eri li'
   *  o se non c'e' un ricordo.
   *
   *  Lo chiamano il trasloco (la chat che sale nel velo o ridiscende) e la
   *  lettura del filo. Fino al 10/10/2026 entrambi portavano sempre in fondo:
   *  chi stava rileggendo la storia, aperta e chiusa Jenny, ricominciava da
   *  capo. Se il punto sta oltre la storia ricaricata si chiedono fino a tre
   *  pagine in piu'; oltre, si resta in cima a quel che c'e'. */
  async restorePlace() {
    const key = sessionManager.currentKey;
    const fromBottom = this._marks.get(key);
    if (fromBottom == null || !this.el.clientHeight) {
      this.scrollToBottom();
      return;
    }
    for (let i = 0; i < 3 && this.el.scrollHeight < fromBottom && this.pager.hasMore; i += 1) {
      await this.pager.loadMore();
      if (sessionManager.currentKey !== key) return;
    }
    this.el.scrollTop = Math.max(0, this.el.scrollHeight - fromBottom);
    this._lastTop = this.el.scrollTop;
    this._stick = this._atBottom();
    this._paintFab();
  }

  /* Il «↓» si mostra solo da lontano: oltre un terzo di schermo dal fondo, e
     mai sotto la soglia di `STICK_PX`. Appena sopra il fondo quel che c'e'
     sotto si vede gia', e il tasto copriva la scheda del piano (prova 2,
     24/10/2026). Il filo invece smette di seguire appena oltre `STICK_PX`:
     le due soglie sono diverse di proposito. */
  _farFromBottom() {
    const height = this.el.clientHeight;
    if (!height) return false;
    const gap = this.el.scrollHeight - this.el.scrollTop - height;
    return gap > Math.max(STICK_PX, height / 3);
  }

  _paintFab() {
    if (!this.fab) return;
    const away = this._farFromBottom();
    /* «Qualcosa di nuovo sotto» si spegne solo arrivando in fondo: nella fascia
       fra `STICK_PX` e un terzo di schermo il tasto si nasconde, ma il nuovo
       non l'hai ancora visto, e risalendo il tasto deve ancora dirlo. */
    if (this._atBottom()) this._newBelow = false;
    this.fab.classList.toggle('visible', away);
    this.fab.classList.toggle('has-new', away && this._newBelow);
    this.fab.setAttribute('aria-label', i18n.t('chat.scrollToBottom'));
    this.fab.tabIndex = away ? 0 : -1;
  }

  /** Riaggancia il fondo **se ci si era**: chi sta rileggendo più su non si
   *  tocca. Serve a chi cambia l'altezza di quel che sta sotto il filo. */
  keepBottom() {
    this._follow();
  }

  scrollToBottom() {
    this.el.scrollTop = this.el.scrollHeight;
    // Anche il ricordo, o il primo gesto dopo sembrerebbe un ritorno indietro.
    this._lastTop = this.el.scrollTop;
    this._stick = true;
    if (this._shownKey) this._marks?.delete(this._shownKey);
    this._newBelow = false;
    this._paintFab();
  }
}
