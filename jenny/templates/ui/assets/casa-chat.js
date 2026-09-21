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
 *  riga in coda (Copia e i secondi, v. `_codaDi`) e' il confine: dice dove una
 *  risposta finisce, e lo dice con due cose che servono invece che con una
 *  linea che non serve a niente.
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

import { copyToClipboard, escapeHtml, showToast } from './shared/utils.js';
import { i18n } from './shared/i18n.js';
import { openImageLightbox } from './shared/image-lightbox.js';
import { sessionManager } from './shared/session-manager.js';
import { HistoryPager } from './shared/history-pager.js';
import { describeWireError } from './shared/wire-error.js';

/* Da dove e' entrato un messaggio che non hai scritto qui dentro. La chat e' il
   registro completo di tutte le superfici — l'app, Telegram, la tendina delle
   notifiche, il fumetto della mascotte — e la provenienza va detta, altrimenti
   un messaggio scritto dal blocco schermo sembra comparso dal nulla.

   Nomi e non identificatori: in officina l'etichetta e' il canale con
   l'iniziale maiuscola ("Floating"), che e' il nome che ha nel codice. Qui e'
   il nome che ha per chi lo legge. */
const ORIGINS = {
  telegram: { icon: 'ti-brand-telegram', key: 'casa.origin.telegram' },
  notification: { icon: 'ti-bell', key: 'casa.origin.notification' },
  floating: { icon: 'ti-message-circle', key: 'casa.origin.floating' },
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

function renderMarkdown(text) {
  /* Fallisce chiuso, non aperto: se il sanificatore non e' stato caricato si
     degrada a testo semplice invece di iniettare HTML non sanificato. */
  if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
    return escapeHtml(text);
  }
  try {
    return DOMPurify.sanitize(marked.parse(text));
  } catch (e) {
    console.error('Markdown parse error:', e);
    return escapeHtml(text);
  }
}

function mediaKind(entry) {
  if (entry.kind) return entry.kind;
  const name = (entry.name || entry.url || '').toLowerCase();
  if (/\.(png|jpe?g|gif|webp|bmp|svg)(\?|$)/.test(name)) return 'image';
  if (/\.(mp4|webm|mov|m4v)(\?|$)/.test(name)) return 'video';
  return 'file';
}

export class CasaChat {
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
    /* Il markdown com'e' arrivato, per bolla. Si copia il sorgente e non il
       reso: le recinzioni dei blocchi di codice sono esattamente cio' che
       serve quando una risposta si incolla altrove. `WeakMap` perche' la
       chiave e' il nodo, e una ricarica del filo li butta tutti. */
    this._sorgente = new WeakMap();
    /* I secondi dell'ultimo `turn_end`, in attesa che la bolla si chiuda. */
    this._secondi = null;
    /* L'ultimo invio, finché il gateway non ha dimostrato di averlo preso.
       `null` = non c'è niente da riprendere. */
    this._pendingSend = null;
    /* Il campo del messaggio è del guscio, non del filo: quando un messaggio
       torna indietro, il testo glielo ridà lui. */
    this.onSendRejected = null;

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

    /* Delegato, e non un ascoltatore per bolla: le bolle sono centinaia dopo
       tre pagine di storia. Come in officina, e per la stessa ragione — la CSP
       del guscio e' `script-src 'self'`, quindi niente `onclick` scritto nel
       markup. */
    this.el.addEventListener('click', (e) => {
      const btn = e.target.closest('.casa-copia');
      if (btn && this.el.contains(btn)) this._copia(btn.closest('.casa-msg'));
    });

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

  /** Carica la conversazione e la disegna. Ritorna il numero di messaggi. */
  async load() {
    const key = sessionManager.currentKey;
    const { thread, stale } = await sessionManager.loadThread(key, HISTORY_PAGE_SIZE);
    if (stale) return 0;
    const messages = thread?.messages || [];
    for (const turn of this._buildTurns(messages)) {
      if (turn.boundary) this._appendBoundary();
      else if (turn.user) this._appendUser(turn.text, turn.origin, turn.media);
      else this._appendAssistant(turn.content, turn.media, false, turn.latencyMs);
    }
    this.pager.adopt(thread?.page);
    this.scrollToBottom();
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
    for (const turn of this._buildTurns(messages).reverse()) {
      if (turn.boundary) this._appendBoundary(true);
      else if (turn.user) this._appendUser(turn.text, turn.origin, turn.media, true);
      else this._appendAssistant(turn.content, turn.media, true, turn.latencyMs);
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
          media: Array.isArray(msg.media) ? msg.media : [],
        });
        continue;
      }
      const turnId = msg.turnId || msg.turn_id;
      // Regola 4: senza id non si accorpa. Mai.
      if (!current || !turnId || current.turnId !== turnId) {
        flush();
        current = { turnId, content: '', media: [], latencyMs: null };
      }
      if (Array.isArray(msg.media) && msg.media.length) current.media.push(...msg.media);
      /* I secondi sono l'unica cosa che l'officina teneva e la casa buttava e
         che adesso serve anche qui: sono meta' della riga che separa una
         risposta dalla successiva. */
      if (msg.latencyMs != null) current.latencyMs = msg.latencyMs;
      /* Una riga di traccia (`kind: 'trace'`, `role: 'tool'`) e' il resoconto di
         uno strumento, e in casa non e' niente: si butta, punto. L'officina la
         tiene come ripiego quando il turno non ha altro testo, ma quel ripiego
         qui sarebbe il difetto — un turno in cui Jenny ha solo lavorato senza
         dire niente deve restare muto, non mostrare `read_file: sensori.json`.
         E la condizione "solo se non c'e' altro testo" non si puo' nemmeno
         valutare qui: il testo vero arriva *dopo*, in un frame successivo dello
         stesso turno. */
      if (msg.kind === 'trace' || msg.role === 'tool') continue;
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

  _delta(text) {
    if (!text) return;
    this.buffer += text;
    this._ensureBlock().innerHTML = renderMarkdown(this.buffer);
    this._follow();
  }

  /* Regola 1: il testo di `stream_end` e' opzionale, il buffer e' la riserva.
     Regola 3: il blocco si chiude qui, o il segmento dopo gli si incolla. */
  _streamEnd(fullText) {
    const finalText = fullText || this.buffer;
    if (this.blockNode && finalText) {
      this.blockNode.innerHTML = renderMarkdown(finalText);
      this._registra(this.turnNode, finalText);
    }
    this.blockNode = null;
    this.buffer = '';
    this._follow();
  }

  /* Regola 2: testo gia' completo, blocco proprio, chiuso subito. */
  _message(msg) {
    if (msg.session_boundary) {
      /* Il contesto e' stato azzerato. La storia sul server e' cambiata sotto i
         piedi: si ricarica invece di indovinare. */
      this.reload();
      return;
    }
    // Un suggerimento di strumento e' esattamente cio' che la casa non mostra.
    if (msg.kind === 'tool_hint') return;
    if (msg.text) {
      if (this.buffer) this._streamEnd();
      const block = document.createElement('div');
      block.className = 'casa-block';
      block.innerHTML = renderMarkdown(msg.text);
      this._ensureTurn().appendChild(block);
      this._registra(this.turnNode, msg.text);
      // `blockNode` resta null: il delta dopo apre il proprio.
    }
    if (msg.media_urls?.length) this._appendMedia(this._ensureTurn(), msg.media_urls);
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
    const node = this._appendUser(text, null, media);
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
    node.className = 'casa-note';
    node.textContent = text;
    this._append(node);
    this.scrollToBottom();
  }

  /* Un messaggio entrato da un'altra superficie mentre la chat e' aperta. */
  _externalUser(msg) {
    const text = msg.text || '';
    const media = msg.media_urls || msg.media || [];
    if (!text && !media.length) return;
    this._resetTurn();
    this._appendUser(text, msg.origin_channel || msg.origin, media);
    this._follow();
  }

  _turnEnd(latencyMs) {
    this._secondi = latencyMs != null ? latencyMs : null;
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
    if (this.turnNode) {
      const chiusa = this.turnNode;
      this._codaDi(chiusa, this._secondi);
      /* La bolla e' cresciuta di una riga **dopo** essere stata misurata: il
         margine per scansare la mascotte va rifatto, e chi era in fondo deve
         restarci. Solo se la bolla e' ancora nel filo — `reload()` passa di
         qui con un nodo che sta per essere buttato. */
      if (chiusa.isConnected) {
        this.gap?.aggiorna();
        this._follow();
      }
    }
    this._secondi = null;
    this.turnNode = null;
    this.blockNode = null;
    this.buffer = '';
    this.turnId = null;
  }

  /** Ributta giu' la conversazione da capo.
   *
   *  E' anche il modo in cui si cambia quaderno: la chiave la sa il session
   *  manager, quindi qui non c'e' un parametro da passare — si svuota e si
   *  rilegge chi e' attuale adesso.
   */
  async reload() {
    this._resetTurn();
    this.el.querySelectorAll('.casa-msg, .casa-boundary').forEach((n) => n.remove());
    this._empty = true;
    /* La bolla in sospeso muore col DOM che la conteneva. Senza azzerarla, un
       rifiuto in arrivo — che e' l'unico frame che la lascia in vita — la
       toglierebbe da un nodo staccato e rimetterebbe quel testo nel campo di
       un'altra conversazione. */
    this._pendingSend = null;
    /* Il cursore appartiene alla conversazione che se ne sta andando. `adopt`
       lo riscrivera' a fetch riuscita — ma se la fetch fallisce lo schermo
       resta vuoto e il cursore resta quello dell'altra: una scorsa in su
       incollerebbe in cima la storia del quaderno sbagliato. Per questo
       `reset()` esiste, e il suo commento dice proprio «si chiama al cambio di
       conversazione». */
    this.pager.reset();
    await this.load();
    this.syncEmpty();
  }

  /* ── Disegno ── */

  _appendUser(text, origin, media, toTop = false) {
    const node = document.createElement('div');
    node.className = 'casa-msg casa-msg-user';
    const badge = this._originBadge(origin);
    if (badge) node.appendChild(badge);
    if (text) {
      const block = document.createElement('div');
      block.className = 'casa-block';
      // Testo dell'utente: mai markdown. E' quello che ha scritto, alla lettera.
      block.textContent = text;
      node.appendChild(block);
    }
    if (media?.length) this._appendMedia(node, media);
    return this._append(node, toTop);
  }

  _appendAssistant(content, media, toTop = false, latencyMs = null) {
    const node = document.createElement('div');
    node.className = 'casa-msg casa-msg-jenny';
    if (content) {
      const block = document.createElement('div');
      block.className = 'casa-block';
      block.innerHTML = renderMarkdown(content);
      node.appendChild(block);
      this._registra(node, content);
    }
    if (media?.length) this._appendMedia(node, media);
    /* Prima di `_append`: quello misura il nodo per scansare la mascotte, e
       misurarlo senza la sua ultima riga vorrebbe dire misurarlo corto. */
    this._codaDi(node, latencyMs);
    this._append(node, toTop);
  }

  /** La riga in coda a una risposta: il Copia e i secondi che ci ha messo.
   *
   *  **E' anche il confine fra una risposta e la successiva.** In casa non c'e'
   *  ne' bolla ne' avatar: due risposte di fila sono due gruppi di paragrafi,
   *  e a occhio diventano un messaggio solo. Serviva qualcosa che dicesse dove
   *  una finisce — e invece di una linea che non fa niente, ci sono le due
   *  cose che uno vorrebbe li'.
   *
   *  Una riga sola, icona e poi tempo, come in officina. I secondi possono
   *  mancare del tutto: una consegna proattiva non ha un turno dietro, quindi
   *  nessuno ha misurato niente, e in quel caso resta il solo Copia.
   */
  _codaDi(node, latencyMs) {
    if (!node || node.querySelector('.casa-coda')) return;
    /* Solo sulle risposte. Quel che hai scritto tu ha gia' la sua bolla col
       suo bordo: e' separato da se', e un Copia sotto le proprie parole non
       serve a nessuno. Oggi nessun chiamante ci passa una bolla utente — la
       guardia e' perche' la prossima non debba ricordarselo. */
    if (!String(node.className).includes('casa-msg-jenny')) return;
    // Un turno in cui Jenny ha solo lavorato non ha testo da copiare.
    if (!this._testoDi(node)) return;
    const riga = document.createElement('div');
    riga.className = 'casa-coda';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'casa-copia';
    btn.title = i18n.t('chat.copy');
    btn.setAttribute('aria-label', i18n.t('chat.copy'));
    btn.innerHTML = '<i class="ti ti-copy" aria-hidden="true"></i>';
    riga.appendChild(btn);
    if (latencyMs != null) {
      const s = document.createElement('span');
      s.className = 'casa-secondi';
      s.textContent = (latencyMs / 1000).toFixed(1) + 's';
      riga.appendChild(s);
    }
    node.appendChild(riga);
  }

  /* Il markdown di una bolla si accumula: un turno testo → strumento → testo
     apre piu' blocchi, e copiarne uno solo sarebbe copiare meta' risposta. */
  _registra(node, testo) {
    const pulito = String(testo || '').trim();
    if (!node || !pulito) return;
    const prima = this._sorgente.get(node);
    this._sorgente.set(node, prima ? `${prima}\n\n${pulito}` : pulito);
  }

  /* Il sorgente se c'e', altrimenti la rete di `innerText`: perde le
     recinzioni, ma non lascia mai un Copia che non copia niente. */
  _testoDi(node) {
    if (!node) return '';
    const registrato = this._sorgente.get(node);
    if (registrato) return registrato;
    return [...node.querySelectorAll('.casa-block')]
      .map((el) => (el.innerText || '').trim())
      .filter(Boolean)
      .join('\n\n');
  }

  async _copia(node) {
    const testo = this._testoDi(node);
    if (!testo) return;
    if (!(await copyToClipboard(testo))) {
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
    hr.className = 'casa-boundary';
    this._append(hr, toTop);
  }

  _originBadge(origin) {
    if (!origin || origin === 'websocket') return null;
    const known = ORIGINS[origin];
    const badge = document.createElement('div');
    badge.className = 'casa-origin';
    const icon = known ? known.icon : 'ti-arrows-exchange';
    const label = known ? i18n.t(known.key) : origin;
    badge.innerHTML = `<i class="ti ${icon}"></i>${escapeHtml(label)}`;
    return badge;
  }

  _appendMedia(node, entries) {
    const wrap = document.createElement('div');
    wrap.className = 'casa-media';
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
          closeLabel: i18n.t('casa.closeImage'),
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
        chip.className = 'casa-file';
        chip.href = entry.url;
        chip.textContent = entry.name || entry.url;
        wrap.appendChild(chip);
      }
    }
    if (wrap.childElementCount) node.appendChild(wrap);
  }

  /** La bolla dell'assistente del turno, creata al primo frame che la riempie. */
  _ensureTurn() {
    if (!this.turnNode) {
      this.turnNode = document.createElement('div');
      this.turnNode.className = 'casa-msg casa-msg-jenny';
      this._append(this.turnNode);
    }
    return this.turnNode;
  }

  /** Il blocco di testo del segmento di stream in corso. */
  _ensureBlock() {
    if (!this.blockNode) {
      this.blockNode = document.createElement('div');
      this.blockNode.className = 'casa-block';
      this._ensureTurn().appendChild(this.blockNode);
    }
    return this.blockNode;
  }

  _append(node, toTop = false) {
    if (toTop) this.el.insertBefore(node, this.el.firstChild);
    else this.el.appendChild(node);
    if (this._empty) {
      this._empty = false;
      this.syncEmpty();
    }
    /* Chi le finisce nell'angolo si scansa. Qui e non nel `_follow()`: quello
       scorre, e il margine va deciso **dopo** che il nodo e' nel filo e prima
       che l'occhio ci arrivi. */
    this.gap?.aggiorna();
    return node;
  }

  /** Mostra o nasconde lo stato vuoto secondo quel che c'e' nel filo. */
  syncEmpty() {
    const empty = document.getElementById('casa-empty');
    if (empty) empty.hidden = !this._empty;
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
    /* Il filo scorre e lei no, quindi quale messaggio le stia dietro cambia.
       Si ricalcola a scorrimento **fermo** e non qui dentro: il margine manda
       il testo a capo, e rifarlo a ogni fotogramma sposterebbe sotto le dita
       quel che si sta leggendo. */
    this.gap?.scorrendo();
  }

  _atBottom() {
    const gap = this.el.scrollHeight - this.el.scrollTop - this.el.clientHeight;
    return gap <= STICK_PX;
  }

  /** Segue il fondo, ma solo se ci si era. */
  _follow() {
    if (this._stick) this.scrollToBottom();
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
  }
}
