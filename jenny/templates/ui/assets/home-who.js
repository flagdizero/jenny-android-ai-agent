/** «Con chi parli» — la pagina Quaderni.
 *
 *  Dice con quale conversazione stai parlando, e quali quaderni ci sono. Un
 *  quaderno e' un progetto, che e' una wiki: l'elenco, l'ordine e le sue regole
 *  stanno in `shared/conversation-list.js`, che e' lo stesso modulo su cui si
 *  appoggia la tendina dell'officina. Qui c'e' solo il disegno.
 *
 *  **Dal 05/10/2026 e' una vetrina**: ogni quaderno e' una copertina sulla sua
 *  mensola, con la tela del suo nome (`shared/spines.js`), l'iniziale grande nel
 *  carattere del tema e il nome su due righe al massimo. In alto la ricerca,
 *  poi «In uso», poi l'archivio — i quaderni fermi da piu' di un mese — e in
 *  fondo le cartelle che non si aprono. Scelto dall'utente
 *  fra cinque varianti di scaffale, tutte costruite sui token del tema: questa
 *  perche' i nomi si leggono senza toccare niente.
 *
 *  **Dal 06/10/2026 la mensola «In uso» e' un bento**, scelto dall'utente fra
 *  dieci varianti. La grandezza dice le pagine: il quaderno che ne ha di piu'
 *  e' la tessera grande, due per due, in alto a sinistra; i tre che lo
 *  seguono sono tessere alte o larghe; gli altri, in ordine di data, sono
 *  tessere semplici, mai sotto i 104 px. Per un giro la grande era il piu'
 *  recente, con «Riprendi»: l'utente si aspettava il quaderno piu' lungo, e
 *  «Riprendi» non serviva — la tessera intera gia' apre. La griglia chiude
 *  sempre un rettangolo pieno quando si puo' (`packBento`), anche in A–Z, nei
 *  risultati e in archivio, dove pero' non c'e' la grande: li' conta la fila.
 *
 *  **La conversazione personale non c'e'.** Fino al 05/10/2026 era la prima
 *  riga, poi la prima copertina; ma e' gia' la voce «Jenny» della fila in
 *  alto, a un tocco da qualunque pagina, e qui era un doppione (deciso
 *  dall'utente). La pagina e' dei quaderni, e non segna in quale sei: la
 *  spunta che lo faceva e' stata tolta il 06/10/2026 (lo sa solo TalkBack).
 *
 *  **Un tocco cambia conversazione.** Per
 *  un giro non e' stato cosi' — le righe erano inerti e lo dichiaravano
 *  — e quel giro e' finito: le righe sono diventate bottoni e il resto
 *  di questo file e' rimasto com'era, che era la previsione.
 *
 *  Le righe delle cartelle **non apribili** restano inerti, e non e' una
 *  dimenticanza: il gateway rifiuterebbe quella chiave (il nome non passa
 *  `is_valid_project_name`), quindi un bottone li' sarebbe una promessa che il
 *  server e' gia' pronto a smentire.
 *
 *  Il pannello non conosce le chiavi di sessione: dice *quale nome* e' stato
 *  toccato, e chi lo ospita ne fa una conversazione. Cosi' la forma della
 *  chiave resta in un posto solo (`shared/conversation-list.js`).
 *
 *  **E' una pagina, non piu' una tendina.** Fino al 23/09/2026 si apriva dal
 *  titolo in un `<dialog>`; da allora e' la pagina Quaderni della pista:
 *  chi la ospita le da' il contenitore e la
 *  ridisegna quando ci arrivi (`show`). Non c'e' niente da aprire ne' da
 *  chiudere, e toccare una riga lascia al guscio di portarti alla chat.
 *
 *  **La tastiera fisica.** Una lettera premuta sulla pagina va nella ricerca
 *  (type-ahead, le stesse guardie del cassetto); le frecce passano da una
 *  copertina all'altra, ⏎ apre e ⇧⏎ apre la scheda. Chi sceglie con le frecce
 *  non vede il nome tagliato: la targhetta in fondo lo dice intero.
 */

import { i18n } from './shared/i18n.js';
import { api } from './shared/api-client.js';
import { ConversationList, UNOPENABLE_HINT_KEYS, ago } from './shared/conversation-list.js';
import { setupLongPress } from './shared/longpress.js';
import { isTypeAheadKey } from './shared/type-ahead.js';
import { initialOf, paintSpine } from './shared/spines.js';

/** Dopo quanti giorni senza modifiche un quaderno scende in archivio. Solo un
 *  raggruppamento a schermo: sul disco non si sposta niente. */
export const ARCHIVE_AFTER_DAYS = 30;

export class WhoPanel {
  /** @param contenitore dove si disegna: il pannello della pagina Quaderni.
   *  @param currentProject funzione che da' il nome del quaderno aperto, o
   *         `null` se sei nella conversazione personale. Decide quale
   *         tessera porta `aria-current`.
   *  @param onPick chiamata col nome del quaderno toccato.
   *  @param onHold chiamata col nome del quaderno **tenuto premuto**: apre la
   *         sua scheda (Apri · Metti come pagina · Rinomina · Elimina), come la
   *         pressione lunga su un'app nel cassetto. Una cosa si appende dal
   *         posto dove vive, e i quaderni vivono qui.
   *  @param onCreate chiamata per un quaderno nuovo, col nome cercato quando
   *         la ricerca non ha trovato niente (`undefined` dal posto vuoto).
   *  @param search il campo di ricerca, fuori dall'elenco che scorre.
   *  @param searchClear la ✕ del campo.
   *  @param plate la targhetta, anche lei fuori dall'elenco.
   *  @param active la pagina e' a schermo e niente le sta sopra: decide se una
   *         lettera premuta e' per la ricerca.
   */
  constructor(container, {
    currentProject, onPick, onHold, onCreate,
    search = null, searchClear = null, plate = null, active = null, badge = null,
  } = {}) {
    this._currentProject = currentProject || (() => null);
    /* Il bollino di una copertina (il piano aperto, «Piano 2/8»): un nodo, o
       `null`. Lo disegna chi sa dei piani (`home-plan.js`). */
    this._badge = badge;
    this._onPick = onPick || null;
    this._onHold = onHold || null;
    this._onCreate = onCreate || null;
    this._list = new ConversationList(() => api.listProjects());
    this._body = container || null;
    this._search = search;
    this._searchClear = searchClear;
    this._plate = plate;
    this._active = active || (() => false);
    this._query = '';
    this._sort = 'recent';
    this._wire();
  }

  /* Gli ascoltatori che non si ridisegnano: campo, ✕, tasti. Il corpo si
     svuota a ogni `render`, quindi chi ascolta le copertine ascolta lui. */
  _wire() {
    this._search?.addEventListener('input', () => {
      this._query = this._search.value;
      this.render();
    });
    this._search?.addEventListener('keydown', (e) => this._onSearchKey(e));
    this._searchClear?.addEventListener('click', () => {
      if (this._search) this._search.value = '';
      this._query = '';
      this.render();
      this._search?.focus();
    });
    this._body?.addEventListener?.('keydown', (e) => this._onCoverKey(e));
    this._body?.addEventListener?.('focusin', (e) => this._showPlate(e.target));
    this._body?.addEventListener?.('focusout', () => this._showPlate(null));
    globalThis.document?.addEventListener?.('keydown', (e) => this._onTypeAhead(e));
    /* Il bento e' calcolato sulle colonne, e le colonne sono quelle del
       momento in cui si disegna: all'avvio, o mentre la pagina entra, la
       larghezza puo' non essere ancora quella vera. Visto sul Titan il
       06/10/2026: un rettangolo fatto per cinque colonne, mostrato su quattro,
       con piante piccola in fondo e una cella vuota. Quindi si guarda la
       larghezza del contenitore, e quando le colonne non sono piu' quelle del
       calcolo il rettangolo si rifa'. */
    const Observer = globalThis.ResizeObserver;
    if (Observer && this._body) new Observer(() => this._repackIfStale()).observe(this._body);
    else globalThis.addEventListener?.('resize', () => this._repackIfStale());
  }

  _repackIfStale() {
    const shelf = this._body?.querySelector?.('.home-who-shelf');
    const now = shelf ? columnsFor(shelf) : 0;
    if (now && now !== this._packedFor) this.render();
  }

  /** I quaderni che il pannello conosce, per chi sta per crearne uno.
   *
   *  Puo' essere vecchio o non essere mai stato letto, ed e' previsto: chi lo
   *  usa lo usa per un *avviso*, non per un rifiuto (v. `project-create.js`).
   */
  get known() {
    return this._list.projects || [];
  }

  /** L'elenco su disco e' cambiato: si rilegge alla prossima apertura. */
  invalidate() {
    this._list.invalidate();
  }

  /** L'elenco su disco e' cambiato **adesso**, sotto la scheda aperta — un
   *  quaderno cancellato o rinominato: si rilegge e si ridisegna subito, o la
   *  riga di quello che non c'e' piu' resterebbe li' a farsi toccare. */
  async refresh() {
    this._list.invalidate();
    await this._list.load();
    this.render();
  }

  /** La pagina Quaderni e' diventata quella che guardi: si ridisegna subito con
   *  quel che c'e' e poi con l'elenco riletto, perche' i quaderni cambiano
   *  anche mentre non la guardi — Jenny ne crea, una mano ne cancella. */
  async show() {
    this.render();
    this._list.invalidate();
    await this._list.load();
    this.render();
  }

  /** Ridisegna il contenuto. Pubblico: lo richiama un cambio di lingua.
   *
   *  Lo scorrimento si rimette dov'era: il contenitore e' lui (`overflow-y:
   *  auto`), svuotarlo lo riporta in cima, e il ridisegno arriva anche mentre
   *  guardi — l'elenco riletto, un nome salvato, un quaderno rinominato. */
  render() {
    if (!this._body) return;
    const body = this._body;
    const last = body.scrollTop;
    const sortHadFocus = Boolean(body.querySelector?.('.home-who-sort:focus'));
    body.innerHTML = '';
    this._packedFor = 0;
    this._showPlate(null);
    this._syncSearch();

    /* I tre stati sono tre, e non due: *non lo so ancora*, *non ce n'e'*, e
       *non sono riuscita a leggerlo*. Confondere l'ultimo col secondo e' il
       difetto da cui nasce `conversation-list.js`, e la nota del guasto va
       **sopra** le copertine perche' quelle possono essere vecchie e questa e'
       l'unica cosa che lo dice. */
    const failed = this._list.loadFailed;
    if (failed) body.appendChild(this._note(i18n.t('home.who.loadFailed'), true));
    const projects = this._list.projects;
    if (projects === null) {
      if (!failed) {
        this._drawLoading(body);
        this._loadThenRender();
      }
    } else if (this._query.trim()) {
      this._drawResults(body, projects, failed);
    } else {
      this._drawShelves(body, projects, failed);
    }

    /* «Nuovo quaderno» non sta qui: e' il tasto + tondo della pagina, fermo
       sopra l'elenco che scorre (index.html, `#home-notebooks-new`). Una riga
       in fondo all'elenco, con tanti quaderni, finiva sotto il bordo. L'unica
       eccezione e' il posto vuoto sulla mensola quando non ce n'e' nessuno. */
    if (last) body.scrollTop = last;
    if (sortHadFocus) body.querySelector?.('.home-who-sort')?.focus();
  }

  /** Chi disegna «non lo so ancora» deve anche andarlo a leggere.
   *
   *  `invalidate()` svuota la cache senza rileggere, e un ridisegno arriva
   *  anche senza `show()`: il cambio di conversazione, il ResizeObserver
   *  quando la pagina torna visibile. Visto su jenny_square il 10/10/2026:
   *  creato un quaderno e tornati ai Quaderni, la pagina restava su
   *  «Caricamento…» per sempre. Una lettura sola alla volta; a lettura finita
   *  si ridisegna, e non si ricomincia — la cache e' piena, o `loadFailed`. */
  _loadThenRender() {
    if (this._loading) return;
    this._loading = this._list.load().finally(() => {
      this._loading = null;
      this.render();
    });
  }

  /* ── Le tre forme della pagina ─────────────────────────────────────────── */

  _drawLoading(body) {
    body.appendChild(this._note(i18n.t('scope.loading')));
    const shelf = this._shelf();
    shelf.setAttribute('aria-hidden', 'true');
    for (let i = 0; i < 3; i += 1) {
      const ghost = document.createElement('span');
      ghost.className = 'home-who-ghost';
      shelf.appendChild(ghost);
    }
    body.appendChild(shelf);
  }

  _drawShelves(body, projects, stale) {
    const cutoff = Date.now() / 1000 - ARCHIVE_AFTER_DAYS * 86400;
    const ordered = this._ordered(projects);
    const inUse = ordered.filter((it) => !it.modified || it.modified >= cutoff);
    const archive = ordered.filter((it) => it.modified && it.modified < cutoff);

    body.appendChild(this._head(i18n.t('home.who.inUse'), inUse.length,
                                { sortable: projects.length > 1 }));
    const shelf = body.appendChild(this._shelf(stale));
    const hero = this._sort !== 'az';
    const shown = hero ? bentoOrder(inUse) : inUse;
    const sizes = this._sizes(shelf, shown, { hero });
    for (const project of shown) shelf.appendChild(this._cover(project, { size: sizes.get(project.name) }));
    if (!projects.length) shelf.appendChild(this._slot());
    if (!projects.length && !stale) {
      body.appendChild(this._note(i18n.t('home.who.none')));
      body.appendChild(this._note(i18n.t('home.who.emptyHint')));
    }

    if (archive.length) {
      body.appendChild(this._head(i18n.t('home.who.archive'), archive.length,
                                  { sub: i18n.t('home.who.archiveHint') }));
      const old = body.appendChild(this._shelf(stale));
      old.classList.add('is-archive');
      const sizes = this._sizes(old, archive, { hero: false });
      for (const project of archive) old.appendChild(this._cover(project, { size: sizes.get(project.name) }));
    }

    this._drawBlocked(body);
  }

  /* La ricerca guarda il nome, in tutti i quaderni apribili archivio
     compreso: e' una ricerca, non un filtro della mensola che vedi. Le
     cartelle che non si aprono restano fuori — non c'e' niente da aprire. */
  _drawResults(body, projects, stale) {
    const q = this._query.trim().toLowerCase();
    const hits = this._ordered(projects).filter((it) => it.name.toLowerCase().includes(q));
    if (!hits.length) {
      body.appendChild(this._note(i18n.t('home.who.noResult', { query: this._query.trim() })));
      const create = document.createElement('button');
      create.type = 'button';
      create.className = 'home-who-create';
      create.textContent = i18n.t('home.who.create', { name: this._query.trim() });
      create.addEventListener('click', () => this._onCreate?.(this._query.trim()));
      body.appendChild(create);
      return;
    }
    body.appendChild(this._head(i18n.t('home.who.results'), hits.length));
    const cutoff = Date.now() / 1000 - ARCHIVE_AFTER_DAYS * 86400;
    const shelf = body.appendChild(this._shelf(stale));
    const sizes = this._sizes(shelf, hits, { hero: false });
    for (const project of hits) {
      shelf.appendChild(this._cover(project, {
        query: q, archived: project.modified && project.modified < cutoff, size: sizes.get(project.name),
      }));
    }
  }

  /** Le misure delle tessere di una griglia gia' nella pagina: un rettangolo
   *  pieno quando si puo' (`packBento`), altrimenti i desideri nudi.
   *
   *  **Le colonne le decide il JS e le impone alla griglia**, invece di
   *  leggerle dal foglio di stile: cosi' il calcolo e la griglia non possono
   *  piu' contare due numeri diversi. Visto sul Titan il 06/10/2026: un
   *  rettangolo fatto per tre o cinque colonne, mostrato su quattro, con un
   *  buco in fondo — letto in un momento in cui la larghezza non era quella
   *  vera, e mai rifatto. Ora una larghezza sbagliata da' al massimo tessere
   *  piu' larghe per un istante, mai un buco; e le colonne del calcolo restano
   *  scritte, cosi' una larghezza che le cambia ridisegna (v. `_wire`). */
  _sizes(shelf, list, { hero }) {
    const wishes = hero ? bentoSizes(list) : new Map();
    /* Una misura sola per tutto il disegno: «In uso» e l'archivio hanno la
       stessa larghezza, e se fra l'una e l'altra compare la barra di
       scorrimento la seconda misura direbbe un'altra cosa (sul Titan, cinque
       colonne sopra e quattro sotto). */
    const columns = this._packedFor || columnsFor(shelf);
    if (!columns) return wishes;
    this._packedFor = columns;
    shelf.style.gridTemplateColumns = `repeat(${columns}, minmax(0, 1fr))`;
    return packBento(list, columns, { hero }) || wishes;
  }

  /* Le cartelle che ci sono e non si aprono, in fondo. **Si mostrano**: su un
     telefono non c'e' un file manager con cui rinominarle, la sola strada e'
     chiederlo a Jenny — e questa e' proprio la chat da cui si chiede.
     Sparire dall'elenco sarebbe indistinguibile dall'essere state cancellate.
     Una spiegazione per motivo, non per riga. */
  _drawBlocked(body) {
    const blocked = this._list.unopenable || [];
    if (!blocked.length) return;
    body.appendChild(this._label(i18n.t('scope.unopenableSection'), true));
    for (const folder of blocked) body.appendChild(this._row(folder));
    for (const reason of [...new Set(blocked.map((it) => it.reason))]) {
      const key = UNOPENABLE_HINT_KEYS[reason] || 'scope.unopenableOther';
      body.appendChild(this._note(i18n.t(key, { rule: i18n.t('scope.invalidName') })));
    }
  }

  /** L'ordine della mensola: per data (quello di `conversation-list.js`) o
   *  per nome. Cambia l'ordine e non i colori: la tela e' del nome. */
  _ordered(projects) {
    if (this._sort !== 'az') return projects.slice();
    return projects.slice().sort((a, b) => a.name.localeCompare(b.name));
  }

  /* ── I pezzi ───────────────────────────────────────────────────────────── */

  _head(text, count, { sortable = false, sub = '' } = {}) {
    const head = document.createElement('div');
    head.className = 'home-who-head';
    head.appendChild(this._label(text));
    const n = document.createElement('span');
    n.className = 'home-who-count';
    n.textContent = String(count);
    head.appendChild(n);
    if (sub) {
      const s = document.createElement('span');
      s.className = 'home-who-sub';
      s.textContent = sub;
      head.appendChild(s);
    }
    if (sortable) {
      const sort = document.createElement('button');
      sort.type = 'button';
      sort.className = 'home-who-sort';
      sort.textContent = i18n.t(this._sort === 'az' ? 'home.who.sortAz' : 'home.who.sortRecent');
      sort.setAttribute('aria-label', i18n.t('home.who.sortLabel', { order: sort.textContent }));
      sort.addEventListener('click', () => {
        this._sort = this._sort === 'az' ? 'recent' : 'az';
        this.render();
      });
      head.appendChild(sort);
    }
    return head;
  }

  _label(text, divided = false) {
    const el = document.createElement('div');
    el.className = 'home-who-label' + (divided ? ' is-divided' : '');
    el.textContent = text;
    return el;
  }

  _note(text, error = false) {
    const el = document.createElement('p');
    el.className = 'home-who-note' + (error ? ' is-error' : '');
    el.textContent = text;
    return el;
  }

  /* La mensola: una griglia, e il ripiano sotto ogni fila e' il suo sfondo
     (v. `.home-who-shelf`), cosi' le colonne si adattano allo schermo senza
     che il JS conti quante ne stanno. */
  _shelf(stale = false) {
    const el = document.createElement('div');
    el.className = 'home-who-shelf' + (stale ? ' is-stale' : '');
    return el;
  }

  /** Una tessera. *size* e' la sua misura nel bento (`hero`, `tall`, `wide`),
   *  assente per quella semplice; v. `bentoSizes`. */
  _cover(item, { query = '', archived = false, size = '' } = {}) {
    const cover = this._command(item.name === this._currentProject(), () => this._pick(item.name));
    cover.dataset.name = item.name;
    paintSpine(cover, item.name);
    if (size) cover.classList.add(`is-${size}`);
    if (archived) cover.classList.add('is-archived');
    if (this._onHold) {
      setupLongPress(cover, () => this._onHold(item.name));
    }

    const age = ago(item.modified, (key, vars) => i18n.t(key, vars));
    const pages = typeof item.pages === 'number' ? item.pages : null;
    /* Il nome intero lo legge TalkBack dall'etichetta: sulla copertina puo'
       essere tagliato a due righe, e l'iniziale e il numero corto sono segni. */
    cover.setAttribute('aria-label', [
      item.name,
      pages === null ? '' : pagesWord(pages),
      age,
      archived ? i18n.t('home.who.inArchive') : '',
    ].filter(Boolean).join(', '));

    /* L'iniziale grande, che esce dal bordo in basso a destra: e' un segno,
       e sta sotto le parole perche' viene prima di loro (la casa non usa
       `z-index`, v. `.home-who-cover-glyph`). */
    const glyph = document.createElement('span');
    glyph.className = 'home-who-cover-glyph';
    glyph.setAttribute('aria-hidden', 'true');
    glyph.textContent = initialOf(item.name);
    cover.appendChild(glyph);

    cover.appendChild(this._name(item.name, query));

    /* Sulla tessera semplice c'e' posto per una cosa sola prima che la riga
       arrivi sotto l'iniziale: le pagine, o la data se le pagine non si
       sanno. Le tessere grandi dicono tutte e due. */
    const when = document.createElement('span');
    when.className = 'home-who-cover-meta';
    const count = pages === null ? ''
      : (size === 'hero' ? pagesWord(pages) : i18n.t('home.who.pagesShort', { count: pages }));
    when.textContent = size ? [count, age].filter(Boolean).join(' · ') : (count || age);
    cover.appendChild(when);

    const badge = this._badge?.(item.name);
    if (badge) {
      cover.appendChild(badge);
      cover.setAttribute('aria-label', `${cover.getAttribute('aria-label')}, ${badge.textContent}`);
    }

    return cover;
  }

  /* Il nome, con la parte cercata segnata. A pezzi e non con `innerHTML`: il
     nome di una cartella e' testo che non scriviamo noi. */
  _name(name, query) {
    const el = document.createElement('span');
    el.className = 'home-who-cover-name';
    const at = query ? name.toLowerCase().indexOf(query) : -1;
    if (at < 0) {
      el.textContent = name;
      return el;
    }
    for (const [tag, text] of [['span', name.slice(0, at)],
                               ['mark', name.slice(at, at + query.length)],
                               ['span', name.slice(at + query.length)]]) {
      if (!text) continue;
      const part = document.createElement(tag);
      part.textContent = text;
      el.appendChild(part);
    }
    return el;
  }

  /* Il posto vuoto sulla mensola, quando non c'e' nessun quaderno: e' li' che
     un + serve di piu', e il + tondo sta comunque al suo posto. */
  _slot() {
    const slot = document.createElement('button');
    slot.type = 'button';
    slot.className = 'home-who-slot';
    const plus = document.createElement('i');
    plus.className = 'ti ti-plus';
    plus.setAttribute('aria-hidden', 'true');
    slot.appendChild(plus);
    const text = document.createElement('span');
    text.textContent = i18n.t('home.who.newNotebook');
    slot.appendChild(text);
    slot.addEventListener('click', () => this._onCreate?.());
    return slot;
  }

  /* Una cartella non apribile non e' un comando: il gateway rifiuta quella
     chiave (v. `_envelope_chat_id`), quindi qui sarebbe un bottone con un no
     gia' scritto dall'altra parte. E non ha una tela: non e' una scelta fra
     cui scegliere, e' una cosa da sistemare. */
  _row(item) {
    const row = document.createElement('div');
    row.className = 'home-who-row is-blocked';

    const name = document.createElement('span');
    name.className = 'home-who-row-name';
    name.textContent = item.name;
    row.appendChild(name);

    const when = document.createElement('span');
    when.className = 'home-who-row-meta';
    when.textContent = ago(item.modified, (key, vars) => i18n.t(key, vars));
    row.appendChild(when);
    return row;
  }

  /* Lo scheletro di una tessera che si puo' toccare. Quella del quaderno in
     cui sei lo dice solo a chi legge con TalkBack (`aria-current`): a schermo
     c'era una spunta, tolta il 06/10/2026 — sulla pagina dei quaderni non
     diceva niente che servisse. */
  _command(current, onPick) {
    const cover = document.createElement('button');
    cover.type = 'button';
    cover.className = 'home-who-cover';
    if (current) cover.setAttribute('aria-current', 'true');
    /* Il tocco che segue una pressione lunga non e' un tocco: senza questa
       riga tenere premuto un quaderno aprirebbe la sua scheda **e** ci
       cambierebbe conversazione sotto. Stesso segno del cassetto. */
    cover.addEventListener('click', () => {
      if (cover.dataset.longpress) {
        delete cover.dataset.longpress;
        return;
      }
      onPick();
    });
    return cover;
  }

  /* Il nome toccato va a chi ospita la pagina: lui cambia conversazione e
     porta alla chat. */
  _pick(name) {
    this._onPick?.(name);
  }

  /* ── La tastiera ───────────────────────────────────────────────────────── */

  /* Anche le parole del campo: stanno nel markup, e la casa non ha una
     passata che le traduca (v. `_applyStaticTranslations` nel cassetto). Il
     ridisegno arriva a ogni cambio di lingua, quindi basta qui. */
  _syncSearch() {
    if (this._search) {
      this._search.placeholder = i18n.t('home.who.search');
      this._search.setAttribute('aria-label', i18n.t('home.who.search'));
    }
    if (this._searchClear) {
      this._searchClear.hidden = !this._query;
      this._searchClear.setAttribute('aria-label', i18n.t('home.who.clearSearch'));
    }
  }

  /* Una lettera premuta sulla pagina, fuori da un campo, e' l'inizio di una
     ricerca: il fuoco va al campo **prima** che il carattere arrivi, cosi' ci
     finisce dentro. Le guardie sono quelle del cassetto e dell'officina. */
  _onTypeAhead(e) {
    if (!this._search || !this._active()) return;
    if (!isTypeAheadKey(e, globalThis.document?.activeElement)) return;
    this._search.focus();
  }

  /* Dal campo: ↓ scende sulla prima copertina, ⏎ apre il primo risultato. */
  _onSearchKey(e) {
    const first = this._covers()[0];
    if (!first) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      first.focus();
    } else if (e.key === 'Enter' && this._query.trim()) {
      e.preventDefault();
      first.click();
    }
  }

  _covers() {
    return [...(this._body?.querySelectorAll?.('.home-who-cover') || [])];
  }

  /* Le frecce fra le tessere, verso quella che sta **a schermo** in quella
     direzione: nel bento le misure sono diverse e la griglia si compatta,
     quindi l'ordine del DOM non e' piu' quello che si vede. ←→ in fondo a una
     fila passano alla precedente o alla seguente nell'ordine; ↑ dalla prima
     fila torna al campo. Senza misure (un DOM senza layout) decide l'ordine,
     con le colonne lette dal foglio di stile. ⇧⏎ apre la scheda, come nel
     cassetto; ⏎ da solo e' il clic del bottone. */
  _onCoverKey(e) {
    const cover = e.target?.closest?.('.home-who-cover');
    if (!cover) return;
    if (e.key === 'Enter' && e.shiftKey) {
      e.preventDefault();
      this._onHold?.(cover.dataset.name);
      return;
    }
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return;
    e.preventDefault();
    const all = this._covers();
    const seen = nearestOnScreen(cover, all, e.key);
    if (seen) {
      seen.focus();
      return;
    }
    const sideways = e.key === 'ArrowLeft' || e.key === 'ArrowRight';
    if (seen === null && !sideways) {
      /* Sopra la prima fila c'e' il campo: ↑ ci torna, come ↓ ne scende. */
      if (e.key === 'ArrowUp') this._search?.focus();
      return;
    }
    const row = () => columnsOf(cover.parentElement);
    const delta = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -row(), ArrowDown: row() }[e.key];
    const at = all.indexOf(cover) + delta;
    if (at < 0 && e.key === 'ArrowUp') this._search?.focus();
    else all[Math.max(0, Math.min(all.length - 1, at))]?.focus();
  }

  /* La targhetta: il nome intero della copertina che ha il fuoco, quando ci
     si e' arrivati con la tastiera. Al dito non serve — il tocco apre — e
     comparire sotto un dito che sta gia' andando altrove sarebbe rumore. */
  _showPlate(target) {
    const plate = this._plate;
    if (!plate) return;
    const cover = target?.closest?.('.home-who-cover');
    if (!cover || !cover.matches?.(':focus-visible')) {
      plate.hidden = true;
      return;
    }
    plate.replaceChildren();
    paintSpine(plate, cover.dataset.name);
    const chip = document.createElement('span');
    chip.className = 'home-who-plate-chip';
    plate.appendChild(chip);
    const text = document.createElement('span');
    text.className = 'home-who-plate-text';
    const name = document.createElement('span');
    name.className = 'home-who-plate-name';
    name.textContent = cover.dataset.name;
    text.appendChild(name);
    const keys = document.createElement('span');
    keys.className = 'home-who-plate-keys';
    keys.textContent = i18n.t('home.who.plateKeys');
    text.appendChild(keys);
    plate.appendChild(text);
    plate.hidden = false;
  }
}

/* «1 pagina» e non «1 pagine»: le traduzioni non conoscono il plurale, quindi
   il singolare ha la sua chiave. */
function pagesWord(count) {
  return i18n.t(count === 1 ? 'home.who.page' : 'home.who.pages', { count });
}

/** L'ordine del bento: in testa il quaderno con piu' pagine, che sara' la
 *  tessera grande in alto a sinistra, e dietro gli altri nell'ordine che
 *  avevano (per data). A pari pagine vince il piu' recente; se nessuno ha
 *  pagine, la testa resta il piu' recente. */
export function bentoOrder(inUse) {
  if (!inUse.length) return [];
  let top = 0;
  inUse.forEach((it, i) => {
    if ((it.pages || 0) > (inUse[top].pages || 0)) top = i;
  });
  return [inUse[top], ...inUse.slice(0, top), ...inUse.slice(top + 1)];
}

/** Le misure del bento per una fila gia' in ordine (`bentoOrder`): nome →
 *  `hero` | `tall` | `wide`. Il primo e' la tessera grande; fra gli altri, i
 *  due con piu' pagine sono alti e il terzo e' largo (a pari pagine vince il
 *  piu' recente). Un quaderno senza pagine, o di cui non si sanno, resta
 *  semplice: una tessera alta che dice «0 p» non ha niente da mostrare. */
export function bentoSizes(inUse) {
  const sizes = new Map();
  if (!inUse.length) return sizes;
  sizes.set(inUse[0].name, 'hero');
  const thick = inUse.slice(1)
    .filter((it) => typeof it.pages === 'number' && it.pages > 0)
    .sort((a, b) => b.pages - a.pages);
  for (const it of thick.slice(0, 2)) sizes.set(it.name, 'tall');
  if (thick[2]) sizes.set(thick[2].name, 'wide');
  return sizes;
}

/** Le misure che chiudono la griglia in un rettangolo pieno, senza buchi e
 *  con l'ultima fila completa: nome → `hero` | `tall` | `wide` (assente per la
 *  tessera semplice), o `null` se con questi quaderni e queste colonne non si
 *  puo' (uno solo su quattro colonne, per esempio).
 *
 *  Si riempie come fa la griglia con `dense`: ogni tessera, in ordine, nella
 *  prima cella libera. Cosi' le misure scelte qui sono anche le posizioni che
 *  il browser trovera'. Con *hero* il primo e' la tessera grande, in alto a
 *  sinistra; poi ognuno prova prima la misura che desidera (`bentoSizes`: alti
 *  i piu' lunghi) e, se non chiude, le altre. Si cerca prima il rettangolo
 *  piu' vicino a quello desiderato; senza *hero* il piu' piccolo, cioe' si
 *  allarga solo quanto serve a completare l'ultima fila. */
export function packBento(items, columns, { hero = true } = {}) {
  const n = items.length;
  if (!n || columns < 2) return null;
  const wishes = hero ? bentoSizes(items) : new Map();
  const first = hero ? 4 : 1;
  const least = first + (n - 1);
  const most = (hero ? 4 : 2) + 2 * (n - 1);
  const wanted = least + [...wishes.values()].filter((s) => s !== 'hero').length;
  const totals = [];
  for (let t = Math.ceil(least / columns) * columns; t <= most; t += columns) totals.push(t);
  totals.sort((a, b) => Math.abs(a - wanted) - Math.abs(b - wanted) || a - b);
  for (const total of totals) {
    const sizes = fillRectangle(items, columns, total / columns, wishes, hero);
    if (sizes) return sizes;
  }
  return null;
}

const SHAPES = { hero: [2, 2], tall: [1, 2], wide: [2, 1], '': [1, 1] };
const TRIES = { tall: ['tall', 'wide', ''], wide: ['wide', 'tall', ''], '': ['', 'wide', 'tall'] };

/* Una ricerca all'indietro, cella per cella: i quaderni sono decine, le misure
   tre, e i conti delle celle libere tagliano quasi tutto. Il tetto di passi e'
   una cintura: finito quello, «non si puo'», e la griglia resta com'era. */
function fillRectangle(items, columns, rows, wishes, hero) {
  const grid = new Array(columns * rows).fill(false);
  const sizes = new Map();
  let free = grid.length;
  let budget = 20000;
  const cells = (at, [w, h]) => {
    const r = Math.floor(at / columns);
    const c = at % columns;
    if (c + w > columns || r + h > rows) return null;
    const out = [];
    for (let y = 0; y < h; y += 1) {
      for (let x = 0; x < w; x += 1) {
        const k = (r + y) * columns + c + x;
        if (grid[k]) return null;
        out.push(k);
      }
    }
    return out;
  };
  const step = (i, from) => {
    if ((budget -= 1) < 0) return false;
    const at = grid.indexOf(false, from);
    if (i === items.length) return at === -1;
    if (at === -1) return false;
    const left = items.length - i;
    const bigFirst = hero && i === 0;
    if (free < left + (bigFirst ? 3 : 0) || free > 2 * left + (bigFirst ? 2 : 0)) return false;
    const tries = bigFirst ? ['hero'] : TRIES[(wishes.get(items[i].name) || '').replace('hero', '')];
    for (const size of tries) {
      const taken = cells(at, SHAPES[size]);
      if (!taken) continue;
      for (const k of taken) grid[k] = true;
      free -= taken.length;
      if (size) sizes.set(items[i].name, size);
      if (step(i + 1, at)) return true;
      for (const k of taken) grid[k] = false;
      free += taken.length;
      sizes.delete(items[i].name);
    }
    return false;
  };
  return step(0, 0) ? sizes : null;
}

/** La tessera piu' stretta e lo spazio fra due tessere: gli stessi numeri di
 *  `.home-who-shelf` (`minmax(112px, 1fr)`, `gap: 10px`), che il banco del
 *  contratto tiene allineati. */
export const TILE_MIN = 112;
export const TILE_GAP = 10;

/* Quante colonne ci stanno nella larghezza della griglia, con la regola di
   `auto-fill`: tessere mai sotto `TILE_MIN`. 0 se la griglia non ha ancora
   una larghezza (fuori dalla pagina, nascosta). */
function columnsFor(shelf) {
  const width = shelf.clientWidth || 0;
  if (!width) return 0;
  return Math.max(1, Math.floor((width + TILE_GAP) / (TILE_MIN + TILE_GAP)));
}

/* Quante colonne ha la mensola adesso: le decide il foglio di stile
   (`auto-fill`), quindi si leggono invece di supporle. */
function columnsOf(shelf) {
  const cols = globalThis.getComputedStyle?.(shelf)?.gridTemplateColumns || '';
  return Math.max(1, cols.split(' ').filter(Boolean).length);
}

/* La tessera piu' vicina a *cover* nella direzione di *key*, guardando i
   rettangoli a schermo: prima chi sta nella stessa fila (o colonna), poi il
   piu' vicino lungo la freccia. `null` se da quella parte non c'e' niente,
   `undefined` se le misure non ci sono e deve decidere l'ordine. */
function nearestOnScreen(cover, all, key) {
  const from = cover.getBoundingClientRect?.();
  if (!from || !(from.width || from.height)) return undefined;
  const across = key === 'ArrowLeft' || key === 'ArrowRight';
  const sign = key === 'ArrowLeft' || key === 'ArrowUp' ? -1 : 1;
  const gap = (a0, a1, b0, b1) => Math.max(0, a0 - b1, b0 - a1);
  let best = null;
  let bestScore = Infinity;
  for (const other of all) {
    if (other === cover) continue;
    const r = other.getBoundingClientRect();
    const ahead = across
      ? (sign > 0 ? r.left >= from.right - 1 : r.right <= from.left + 1)
      : (sign > 0 ? r.top >= from.bottom - 1 : r.bottom <= from.top + 1);
    if (!ahead) continue;
    const off = across ? gap(r.top, r.bottom, from.top, from.bottom)
      : gap(r.left, r.right, from.left, from.right);
    /* ←→ restano nella fila: in fondo decide l'ordine, che va a capo. */
    if (across && off > 0) continue;
    const along = across ? Math.abs(r.left - from.left) : Math.abs(r.top - from.top);
    const score = off * 10000 + along;
    if (score < bestScore) {
      best = other;
      bestScore = score;
    }
  }
  return best;
}
