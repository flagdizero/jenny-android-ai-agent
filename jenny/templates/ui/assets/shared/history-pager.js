/** La pagina precedente della conversazione — per tutti e due i gusci.
 *
 *  Estratto da `mobile-chat.js` il 18/09/2026 **senza riscriverlo**: cursore,
 *  chiavistello, ancoraggio dello scorrimento e bottone di ripiego sono
 *  identici a com'erano, cambia solo da dove prendono i loro appigli. Il motivo
 *  e' lo stesso del volo pegman: le interfacce sono due, e una seconda copia di
 *  questa macchina a stati diverge in silenzio — un cursore sbagliato non si
 *  vede, ripaga la pagina che hai gia' a schermo e sembra che la conversazione
 *  si ripeta.
 *
 *  **Due modi di raggiungere il passato, e servono tutti e due.** Lo scorrimento
 *  infinito e' il gesto naturale, ma aspetta un evento `scroll` con
 *  `scrollTop === 0`, e un contenitore che non trabocca non ne emette nessuno:
 *  la pagina piu' vecchia esiste, il client sa che esiste (`hasMore`), e non c'e'
 *  gesto che possa chiederla. Li' entra il bottone, che compare **solo** in
 *  quello stato e sparisce da solo appena la chat cresce abbastanza.
 *
 *  Chi lo usa passa un *host*: dove si guarda, dove si ascolta, come si chiede
 *  una pagina, come si disegna. Il disegno non si condivide — l'officina
 *  antepone turni con pensieri e strumenti, la casa solo testo e allegati — e
 *  infatti non sta qui.
 */

/* Scarto sotto il quale il contenitore si considera "non traboccante". Non e'
   zero perche' un paio di px di arrotondamento fra `scrollHeight` e
   `clientHeight` farebbero credere scorribile una pagina che non lo e'. */
const OVERFLOW_SLOP = 4;

export const HISTORY_MORE_CLASS = 'chat-history-more';

export class HistoryPager {
  /**
   *  @param {object} host
   *  @param {() => Element} host.scroller   chi ha scrollTop/scrollHeight/clientHeight
   *  @param {EventTarget}   host.listenOn   chi emette `scroll` (in officina non
   *                                         coincide con lo scroller: lo scroller
   *                                         e' il documento, l'evento arriva a
   *                                         `window`)
   *  @param {() => Element} host.container  dove cercare un bottone gia' attaccato
   *  @param {number}        host.pageSize   quante ancore chiedere per pagina
   *  @param {() => object|null} host.begin  apre un caricamento: torna
   *                                         `{ fetch, stale }`, o null se non c'e'
   *                                         niente da caricare
   *  @param {(messages) => void} host.prepend  il disegno, in cima
   *  @param {(node) => void}     host.mount    dove va il bottone
   *  @param {() => string}       host.label    cosa c'è scritto sopra
   *
   *  `label` è un appiglio e non un `i18n.t` qui dentro di proposito: `i18n`
   *  costruisce la sua istanza al caricamento del modulo e legge `localStorage`,
   *  quindi importarlo renderebbe questo file impossibile da esercitare fuori da
   *  un browser — e questa è roba che si prova in node. I due gusci passano la
   *  stessa chiave, e un test lo pretende.
   */
  constructor(host) {
    this._host = host;
    this.cursor = null;
    this.loading = false;
    this.hasMore = true;
  }

  /* Lo stato di partenza di una conversazione: nessun cursore, e si presume che
     dietro ci sia altro finche' il server non dice il contrario. Si chiama al
     cambio di conversazione, dove lo schermo viene svuotato. */
  reset() {
    this.cursor = null;
    this.hasMore = true;
  }

  /* La prima pagina la chiede il guscio (insieme a bootstrap, scope, e tutto il
     resto del suo avvio): qui si registra solo dove si e' arrivati. */
  adopt(page) {
    this.cursor = page?.before_cursor || null;
    this.hasMore = page?.has_more_before !== false;
  }

  bindInfiniteScroll() {
    this._host.listenOn.addEventListener('scroll', () => {
      if (this._host.scroller().scrollTop === 0 && !this.loading && this.hasMore) {
        this.loadMore();
      }
    });
  }

  /* Il modo di raggiungere la pagina precedente **quando non si puo' scorrere**.

     Prima non si notava perche' la prima pagina e' lunga; da quando `/new` fa
     ripartire la chat dal separatore e' lo **stato normale subito dopo un
     reset** — tre righe a schermo e la conversazione di prima irraggiungibile,
     cioe' la stessa cancellazione apparente che questo disegno esiste per non
     fare.

     Un bottone e non un allungamento artificiale del contenuto: la riga dice
     cosa c'e' sopra, e sparisce da sola appena la chat cresce abbastanza da
     rendere di nuovo possibile il gesto. */
  ensureReach() {
    const existing = this._host.container().querySelector(`.${HISTORY_MORE_CLASS}`);
    const scroller = this._host.scroller();
    const canScroll = scroller.scrollHeight > scroller.clientHeight + OVERFLOW_SLOP;
    if (!this.hasMore || canScroll) {
      existing?.remove();
      return;
    }
    /* Riancorato in cima **anche quando c'e' gia'**, ed e' il difetto che i test
       non vedevano: la pagina chiesta dal tocco entra sopra di lui, e il bottone
       resta in mezzo — «mostra la conversazione precedente» con quella
       conversazione stampata sotto, che indica la direzione sbagliata. Succede
       quando la pagina caricata e' corta (due `/new` di fila: un separatore e
       basta), e allora la chat non trabocca ancora e il bottone non se ne va.
       `insertBefore` sposta un nodo gia' attaccato invece di duplicarlo, quindi
       il `disabled` del giro in corso resta suo. */
    this._host.mount(existing || this._createButton());
  }

  async loadMore() {
    if (this.loading || !this.hasMore) return;
    // Il guscio decide se c'e' una conversazione da paginare, e apre la guardia
    // contro il cambio di conversazione: una pagina vecchia arrivata dopo va
    // buttata, non incollata in cima al thread di un'altra chat.
    const ctx = this._host.begin();
    if (!ctx) return;
    // Senza cursore la thread API restituisce la pagina *piu' recente*, non
    // quella precedente: paginare indietro con before=null riporterebbe in cima
    // i messaggi gia' a schermo invece di quelli vecchi. Se il server dichiara
    // has_more_before senza darci un cursore, non c'e' nulla da paginare.
    if (!this.cursor) {
      this.hasMore = false;
      return;
    }
    this.loading = true;
    const scrollHeightBefore = this._host.scroller().scrollHeight;
    try {
      const thread = await ctx.fetch(this._host.pageSize, this.cursor);
      if (ctx.stale()) return;
      const messages = thread?.messages || [];
      this._host.prepend(messages);
      this.adopt(thread?.page);
      this.ensureReach();
      const scrollHeightAfter = this._host.scroller().scrollHeight;
      this._host.scroller().scrollTop = scrollHeightAfter - scrollHeightBefore;
    } catch (err) {
      console.error('Failed to load more history:', err);
    } finally {
      this.loading = false;
    }
  }

  _createButton() {
    const btn = document.createElement('button');
    btn.className = HISTORY_MORE_CLASS;
    btn.type = 'button';
    btn.textContent = this._host.label();
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      await this.loadMore();
      // `loadMore` richiama `ensureReach`, che toglie questo nodo quando non
      // serve piu'; se serve ancora (pagina corta) va riabilitato.
      btn.disabled = false;
    });
    return btn;
  }
}
