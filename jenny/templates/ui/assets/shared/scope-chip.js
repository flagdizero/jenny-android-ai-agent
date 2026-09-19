/** Scope chip — dice a quale scope va il prossimo messaggio, ed è il modo di cambiarlo.
 *
 * Sta ancorato sopra il composer perché quello è l'unico punto su cui l'occhio è
 * già posato prima di scrivere: la riga d'identità della chat (`.chat-identity`)
 * è il parente più prossimo ma è il primo elemento dell'area di scroll, quindi
 * scorre via. Un messaggio mandato allo scope sbagliato non si ritira, ed è il
 * solo guasto irrecuperabile del disegno delle sessioni-progetto.
 *
 * Scegliere uno scope cambia **davvero** conversazione: la chiave della
 * sessione diventa `project:<nome>`, e da quella il gateway ricava sia la
 * cartella su cui l'agente lavora sia il thread da disegnare. Il chip non manda
 * mai un percorso: manda un nome, e la cartella la deduce il server — così la
 * sessione e la sua cartella non possono divergere.
 *
 * Chi possiede la chat passa :attr:`onSwitch`, che viene chiamata dopo il
 * cambio: ricaricare quel che è a schermo non è mestiere di questo modulo.
 */

import { i18n } from './i18n.js';
import { AppState, claimComposeMenu, onOtherComposeMenu } from './state.js';
import { api } from './api-client.js';
import { showToast } from './utils.js';
import { deleteProjectFlow } from './project-delete.js';
import { PROJECT_WORDS, createProjectFlow } from './project-create.js';
import {
  ConversationList, UNOPENABLE_HINT_KEYS, ago, isOpenableProjectName, projectKey,
} from './conversation-list.js';

/** Quanto del nome entra nel placeholder prima dei puntini.
 *
 *  Il chip tronca da sé, in CSS (`max-width: 15ch` sul crumb); il placeholder di
 *  un `<textarea>` no — sfora la sua scatola e viene tagliato a metà parola,
 *  senza niente che dica che è tagliato. Un nome arriva a 64 caratteri
 *  (`is_valid_project_name`), quindi succede davvero: visto sul telefono il
 *  22/08.
 */
const NAME_IN_PLACEHOLDER = 22;

/** Il nome accorciato per il placeholder, **senza** aggiungere i puntini.
 *
 *  I puntini ce li ha già la stringa localizzata («Chiedi qualcosa su {name}…»),
 *  e aggiungerne altri dava `zz-bordi-lunghissimo-……` — visto sul telefono
 *  subito dopo il primo tentativo. Quindi qui si taglia e si toglie il
 *  separatore finale, così i puntini del template attaccano a una parola e non
 *  a un trattino: `zz-bordi-lunghissimo...`
 */
function _short(name) {
  const text = String(name || '');
  if (text.length <= NAME_IN_PLACEHOLDER) return text;
  return text.slice(0, NAME_IN_PLACEHOLDER).replace(/[-._]+$/, '');
}

export class ScopeChip {
  constructor() {
    this.el = document.getElementById('scope-chip');
    this.menu = document.getElementById('scope-menu');
    // Chat e onboarding condividono l'index: se il blocco non c'è, il modulo
    // non fa nulla invece di sollevare al primo getElementById nullo.
    this.enabled = Boolean(this.el && this.menu);
    // Scope corrente: ``null`` come nome significa sessione personale.
    this.scope = { kind: 'personal', name: null };
    /* Quali conversazioni esistono, in che ordine, e da quanto: **non è roba
       del chip**. Le stesse cinque regole servono al pannello «con chi parli»
       della casa, e una seconda copia sarebbe una seconda verità su cosa c'è
       nel workspace. Qui resta il disegno; i dati stanno in `conversation-list`
       e i quattro campi di prima sono diventati altrettanti accessori, così il
       resto di questo file è rimasto com'era. */
    this._list = new ConversationList(() => api.listProjects());
    this._open = false;
    // Latch di `init`, come `sessionManager._initialized` e
    // `ChatController._wsListenersBound`. I due listener su `document` non sono
    // rimovibili (sono chiusure anonime), quindi una seconda `init` li
    // registrerebbe una seconda volta: ogni click fuori chiuderebbe la tendina
    // due volte e ogni Escape pure. Oggi `_initSessions` chiama una volta sola,
    // ma i due fratelli si difendono e questo no.
    this._initialized = false;
    // Chiamata dopo un cambio di scope, con la nuova chiave di sessione. La
    // monta chi possiede la chat; senza, il chip resta presentazione.
    this.onSwitch = null;
  }

  init() {
    if (!this.enabled || this._initialized) return;
    this._initialized = true;
    this.el.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggle();
    });
    this.menu.addEventListener('click', (e) => e.stopPropagation());
    // Chiusura: tap fuori ed Escape, come gli sheet dell'app.
    document.addEventListener('click', () => this.close());
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') this.close();
    });
    onOtherComposeMenu('scope', () => this.close());
    i18n.onLocaleChange(() => {
      this.render();
      if (this._open) this._renderMenu();
    });
    // Il placeholder dipende da due cose con due proprietari: lo scope (questo
    // modulo) e il modo di scrittura (`write-switch.js`). Iscriversi invece di
    // farsi chiamare rende l'ordine dei due `syncFromSession` irrilevante —
    // altrimenti chi sincronizza per secondo lascia il testo del primo.
    AppState.on('readonlyTurn', () => this.syncPlaceholder());
    this.render();
  }

  // ── Stato ──────────────────────────────────────────────────────────────

  /* I quattro campi che erano qui, ora letti da `conversation-list`. Restano
     con i nomi di prima perché sono letti in una dozzina di punti di questo
     file, e cambiarli avrebbe reso il passaggio a un modulo condiviso
     indistinguibile da una riscrittura della tendina. In sola lettura: la cache
     la scrive `load()`, e si butta con `invalidate()`. */
  get _projects() {
    return this._list.projects;
  }

  get _unopenable() {
    return this._list.unopenable;
  }

  get _loadFailed() {
    return this._list.loadFailed;
  }

  get _dir() {
    return this._list.dir;
  }

  /** Scope attivo secondo il backend. ``null``/radice ⇒ sessione personale. */
  syncFromSession(workspaceScope) {
    if (!this.enabled) return;
    const path = workspaceScope?.project_path || '';
    // Se il backend non ha ancora risposto il nome e' il default: sbagliarlo
    // vorrebbe dire mostrare "personale" per un attimo, non mandare un
    // messaggio nel posto sbagliato — lo scope mostrato viene comunque
    // rirenderizzato quando l'elenco arriva.
    const marker = `/${this._dir}/`;
    const idx = path.indexOf(marker);
    if (idx === -1) {
      this.scope = { kind: 'personal', name: null };
    } else {
      const rest = path.slice(idx + marker.length).replace(/\/+$/, '');
      // Solo il primo segmento: uno scope più profondo di così non è un
      // progetto, e mostrarlo come tale mentirebbe sul confine.
      this.scope = { kind: 'project', name: rest.split('/')[0] || null };
      if (!this.scope.name) this.scope = { kind: 'personal', name: null };
    }
    this._publishPin();
    this.render();
  }

  /** Pubblica su ``AppState`` la wiki a cui le viste sono agganciate.
   *
   *  Le viste wiki e grafo hanno bisogno della stessa risposta che il chip ha
   *  gia' — *in quale progetto siamo* — e questo e' l'unico punto in cui
   *  cambia. Passa da ``AppState`` e non da un import diretto del chip perche'
   *  ``set`` avvisa chi ascolta: cambiare progetto mentre una vista e' aperta
   *  la deve riagganciare, e senza notifica resterebbe sul progetto di prima.
   *
   *  ``null`` = sessione personale, cioe' nessun aggancio: le viste tornano a
   *  mostrare tutte le wiki, che e' la Home di sempre.
   */
  _publishPin() {
    const pinned = this.scope.kind === 'project' ? this.scope.name : null;
    if (AppState.pinnedWiki === pinned) return;
    AppState.set('pinnedWiki', pinned);
  }

  /** Nome mostrato per la sessione personale (non è un nome di cartella). */
  get personalLabel() {
    return i18n.t('scope.personal');
  }

  /** Segmenti del percorso mostrati nel chip. */
  get pathSegments() {
    return this.scope.kind === 'project'
      ? [this._dir, this.scope.name]
      : [this.personalLabel];
  }

  // ── Chip ───────────────────────────────────────────────────────────────

  render() {
    if (!this.enabled) return;
    const isProject = this.scope.kind === 'project';
    this.el.dataset.scope = this.scope.kind;
    this.el.setAttribute('aria-label', i18n.t('scope.change'));

    // I due figli si guardano prima di scriverci, come fa
    // `WriteSwitch.render()`: `this.el` c'è (`enabled` lo ha verificato) ma il
    // suo interno viene dall'index, e un `null` qui porterebbe giù tutto il
    // disegno del chip — nome dello scope compreso, che è la sola cosa che dice
    // dove sta andando il prossimo messaggio.
    const mark = this.el.querySelector('.scope-chip-mark');
    // La sessione personale porta il fiore, un progetto la cartella: due
    // stati che devono distinguersi anche prima di leggere il nome.
    if (mark) {
      mark.className = 'scope-chip-mark' + (isProject ? ' ti ti-folder' : '');
      mark.textContent = isProject ? '' : '✿';
    }

    const pathEl = this.el.querySelector('.scope-chip-path');
    if (pathEl) {
      pathEl.innerHTML = '';
      this.pathSegments.forEach((part, i) => {
        if (i) {
          const sep = document.createElement('span');
          sep.className = 'scope-chip-sep';
          sep.textContent = '›';   // lo stesso separatore del breadcrumb
          pathEl.appendChild(sep);
        }
        const crumb = document.createElement('span');
        crumb.className = 'scope-chip-crumb';
        crumb.textContent = part;
        pathEl.appendChild(crumb);
      });
    }

    this.syncPlaceholder();
  }

  /** Il placeholder nomina lo scope: è il testo su cui si sta per scrivere.
   *
   *  Nomina anche la **sola lettura**, e non è un dettaglio: quel testo è
   *  l'ultima cosa che l'occhio attraversa prima di premere invio, e un
   *  messaggio partito credendo di poter scrivere (o di non poterlo) non si
   *  ritira. Il chip dice *dove* va, il placeholder ripete *come* parte.
   *
   *  Pubblico perché lo richiama chi possiede il composer quando cambia il modo
   *  di scrittura: quel cambio non passa da qui.
   */
  syncPlaceholder() {
    const input = document.getElementById('chat-input');
    if (!input) return;
    const project = this.scope.kind === 'project';
    const readonly = AppState.readonlyTurn === true;
    if (project) {
      input.placeholder = i18n.t(
        readonly ? 'write.askAboutReadonly' : 'scope.askAbout',
        { name: _short(this.scope.name) },
      );
      return;
    }
    input.placeholder = i18n.t(readonly ? 'write.askReadonly' : 'chat.placeholder');
  }

  // ── Tendina ────────────────────────────────────────────────────────────

  toggle() {
    this._open ? this.close() : this.open();
  }

  async open() {
    if (!this.enabled) return;
    // Una sola tendina per volta: chi si apre lo dichiara, l'altra si chiude.
    // Non basta il listener su `document` — il `stopPropagation` del chip
    // impedisce al click di arrivarci (v. `state.js::composeMenu`).
    claimComposeMenu('scope');
    this._open = true;
    this.menu.classList.add('open');
    this.el.setAttribute('aria-expanded', 'true');
    this._renderMenu();                 // subito, con quel che c'è in cache
    await this._loadProjects();
    if (this._open) this._renderMenu();
  }

  close() {
    if (!this.enabled || !this._open) return;
    this._open = false;
    this.menu.classList.remove('open');
    this.el.setAttribute('aria-expanded', 'false');
  }

  /** Progetti = le wiki del workspace, lette dal backend.
   *
   *  L'ordine, la divisione fra apribili e non, e la regola per cui una lettura
   *  fallita **non** cancella l'elenco buono stanno in `conversation-list.js`:
   *  sono le stesse per la tendina e per il pannello della casa, e da quando
   *  sono due i posti che le usano non possono piu' stare in uno solo.
   *
   *  Il ridisegno resta qui, e resta legato alla riuscita: il nome della
   *  cartella puo' essere cambiato, e su un fallimento non e' cambiato niente.
   */
  async _loadProjects() {
    if (await this._list.load()) this.render();
  }

  _renderMenu() {
    this.menu.innerHTML = '';
    this.menu.appendChild(this._label(i18n.t('scope.personalSection')));
    this.menu.appendChild(this._item({
      name: this.personalLabel, flower: true,
      active: this.scope.kind === 'personal',
      onPick: () => this.select({ kind: 'personal', name: null }),
    }));

    this.menu.appendChild(this._sep());
    this.menu.appendChild(this._label(i18n.t('scope.projectsSection')));
    // Solo i progetti stanno nel riquadro che scorre: il resto della tendina
    // e' alto quanto e' alto, e non deve sparire quando l'elenco cresce.
    const list = document.createElement('div');
    list.className = 'scope-menu-scroll';
    this.menu.appendChild(list);
    let activeEl = null;
    // L'elenco fallito si dichiara, e si dichiara *sopra* quel che resta in
    // cache: le righe sotto possono essere vecchie, e questa nota e' l'unica
    // cosa che lo dice. Con la cache vuota prende il posto sia di "caricamento"
    // sia di "nessun progetto" — quest'ultima sarebbe una bugia.
    if (this._loadFailed) list.appendChild(this._note(i18n.t('scope.loadFailed'), true));
    if (this._projects === null) {
      if (!this._loadFailed) list.appendChild(this._note(i18n.t('scope.loading')));
    } else if (!this._projects.length) {
      if (!this._loadFailed) list.appendChild(this._note(i18n.t('scope.noProjects')));
    } else {
      for (const project of this._projects) {
        const active = this.scope.kind === 'project' && this.scope.name === project.name;
        const item = this._item({
          name: project.name, icon: 'ti-folder',
          when: this._ago(project.modified),
          active,
          onPick: () => this.select({ kind: 'project', name: project.name }),
        });
        if (active) activeEl = item;
        /* Un progetto si cancella da dove lo si è creato.
           Fino alla 0.9.x la sola strada era il file manager, dopo aver saputo
           che i progetti vivono in `wikis/`: chi ne apriva uno per sbaglio non
           trovava la via indietro (issue #11). Un tasto **visibile** e non una
           pressione lunga: un gesto che non si vede non è una risposta a «non
           trovavo come si fa», che è la domanda da cui nasce tutto questo. */
        list.appendChild(this._projectRow(item, project.name));
      }
    }
    /* Le cartelle che ci sono ma non si aprono, in fondo all'elenco.

       **Si mostrano.** Il server le manda in una lista sua (`unopenable`)
       invece di buttarle via, e la ragione sta su
       `wiki_routes.py::_collect_projects`: su un telefono non c'è un file
       manager con cui rinominare una cartella, la sola strada è chiederlo
       all'agente dalla chat personale, e per chiederlo bisogna sapere che
       quella cartella esiste. Sparire dall'elenco è indistinguibile
       dall'essere state cancellate. Finché queste righe non c'erano il lavoro
       del server finiva in niente: dal lato dell'utente quella cartella era
       semplicemente assente, cioè lo stato peggiore dei due.

       Stanno **sotto** i progetti veri, dopo un'etichetta loro: non sono una
       scelta fra cui scegliere, sono una cosa da sistemare. E la spiegazione è
       **una per motivo**, non una per riga: la regola è la stessa per tutte, e
       ripeterla su ogni riga la trasforma da spiegazione in rumore. */
    const blocked = this._unopenable || [];
    if (blocked.length) {
      list.appendChild(this._label(i18n.t('scope.unopenableSection')));
      for (const folder of blocked) {
        list.appendChild(this._item({
          name: folder.name, icon: 'ti-folder-off',
          when: this._ago(folder.modified),
          unopenable: true, reason: folder.reason,
        }));
      }
      // `Set` per non ripetere la stessa nota quando due cartelle sono ferme
      // sullo stesso motivo, che oggi è il caso normale (ce n'è uno solo).
      for (const reason of [...new Set(blocked.map(it => it.reason))]) {
        const key = UNOPENABLE_HINT_KEYS[reason] || 'scope.unopenableOther';
        // `rule` lo legge solo la frase del nome invalido: le altre lo ignorano,
        // e va passato sempre perché la regola vive in **una** stringa sola.
        list.appendChild(this._note(i18n.t(key, { rule: i18n.t('scope.invalidName') })));
      }
    }

    this.menu.appendChild(this._sep());
    const add = this._item({
      name: i18n.t('scope.newProject'), icon: 'ti-plus',
      onPick: () => this._createProject(),
    });
    add.classList.add('scope-menu-new');
    this.menu.appendChild(add);

    // Con l'elenco piu' lungo del riquadro il progetto in cui si sta puo'
    // essere sotto la piega: aprire la tendina senza vedere dove si e' toglie
    // alla tendina meta' del suo mestiere. `nearest` non muove niente quando
    // e' gia' visibile.
    activeEl?.scrollIntoView({ block: 'nearest' });
  }

  _label(text) {
    const el = document.createElement('div');
    el.className = 'scope-menu-label';
    el.textContent = text;
    return el;
  }

  _sep() {
    const el = document.createElement('div');
    el.className = 'scope-menu-sep';
    return el;
  }

  _note(text, isError = false) {
    const el = document.createElement('div');
    el.className = 'scope-menu-note' + (isError ? ' is-error' : '');
    el.textContent = text;
    return el;
  }

  /** Una riga della tendina. Con `unopenable` è una riga che **non si tocca**.
   *
   *  Quella variante non è un `<button>` e non monta nessun listener: una riga
   *  tappabile che poi rifiuta è peggio di una riga grigia che dice perché — il
   *  tocco è una promessa, e quella riga non ha niente da mantenere (aprirla
   *  vorrebbe dire mandare il messaggio dopo in una conversazione che non
   *  esiste, il solo guasto irrecuperabile di questo disegno). Per la stessa
   *  ragione non porta `role="menuitemradio"` né la spunta: non è una delle
   *  opzioni fra cui la tendina fa scegliere. E non essere un bottone la tiene
   *  fuori dall'ordine di tabulazione senza doverlo dichiarare.
   *
   *  Sta **qui** e non in un costruttore suo perché così la decisione «questa
   *  riga non si apre» vive nell'unico punto che costruisce righe: una riga
   *  nuova non può diventare tappabile per distrazione. `when` invece resta —
   *  riconoscere *quale* cartella è serve a chiederne il nome nuovo all'agente.
   */
  _item({ name, icon, flower = false, when = '', active = false,
          unopenable = false, reason = '', onPick }) {
    const btn = document.createElement(unopenable ? 'div' : 'button');
    btn.className = 'scope-menu-item' + (unopenable ? ' is-unopenable' : '');
    if (unopenable) {
      btn.setAttribute('aria-disabled', 'true');
      btn.dataset.reason = reason || '';
    } else {
      btn.type = 'button';
      btn.setAttribute('role', 'menuitemradio');
      btn.setAttribute('aria-checked', active ? 'true' : 'false');
      btn.dataset.active = active ? '1' : '0';
    }

    if (flower) {
      const f = document.createElement('span');
      f.className = 'scope-menu-flower';
      f.textContent = '✿';
      btn.appendChild(f);
    } else {
      const i = document.createElement('i');
      i.className = `ti ${icon}`;
      btn.appendChild(i);
    }

    const text = document.createElement('span');
    text.className = 'scope-menu-text';
    const nameEl = document.createElement('span');
    nameEl.className = 'scope-menu-name';
    nameEl.textContent = name;
    text.appendChild(nameEl);
    if (when) {
      const whenEl = document.createElement('span');
      whenEl.className = 'scope-menu-when';
      whenEl.textContent = when;
      text.appendChild(whenEl);
    }
    btn.appendChild(text);

    // Da qui in giù è quel che rende una riga una *scelta*: la spunta dello
    // stato attivo e il tocco. Una riga che non si apre non ha né l'una né
    // l'altro — e non averli è il fix, non un'omissione.
    if (unopenable) return btn;

    const check = document.createElement('i');
    check.className = 'ti ti-check scope-menu-check';
    btn.appendChild(check);

    btn.addEventListener('click', () => {
      this.close();
      onPick();
    });
    return btn;
  }

  // ── Azioni ─────────────────────────────────────────────────────────────

  /** La chiave di sessione di uno scope. */
  static keyFor(scope) {
    return scope.kind === 'project' && scope.name
      ? projectKey(scope.name)
      : null;   // null = la conversazione personale, che la conosce il chiamante
  }

  /** Cambia scope: il chip, il placeholder, l'aggancio delle viste, e la
   *  conversazione sotto.
   *
   *  L'aggancio si pubblica **qui**, non solo in `syncFromSession`. La risposta
   *  la sappiamo già — l'utente ha appena scelto — e passare per il backend la
   *  faceva arrivare alle viste wiki e grafo un giro di rete più tardi: fino a
   *  quel momento il chip diceva un progetto e le due viste ne mostravano un
   *  altro. Se poi il caricamento del thread fallisce `syncFromSession` non
   *  viene chiamato affatto, e l'aggancio sbagliato ci restava per sempre.
   *  Resta un solo scrittore di `pinnedWiki` (`_publishPin`), che è la regola
   *  che tiene le viste su una sola risposta.
   */
  select(scope) {
    const changed = scope.kind !== this.scope.kind || scope.name !== this.scope.name;
    this.scope = scope;
    this._publishPin();
    this.render();
    if (changed) this.onSwitch?.(ScopeChip.keyFor(scope), scope);
  }

  /** Lascia *name* se e' lo scope corrente, tornando alla conversazione
   *  personale. Da chiamare quando quel progetto smette di esistere.
   *
   *  Senza, dopo una cancellazione il chip continua a nominare un progetto che
   *  non c'e' piu' e la chat ne mostra la trascrizione: niente si rompe — il
   *  primo messaggio verrebbe rifiutato dal server, che la cartella la cerca —
   *  ma e' uno schermo che dice il falso, ed e' il tipo di falso che poi si
   *  scambia per il difetto che questa cancellazione e' venuta a chiudere.
   */
  /** Una riga progetto: la scelta, e accanto il modo di cancellarla.
   *
   *  Due `<button>` affiancati e non uno dentro l'altro — un bottone dentro un
   *  bottone non è markup valido, ed è la stessa ragione per cui l'explorer del
   *  workspace fa la cella con un `div`. Il cestino è spento (`--text-faint`) e
   *  separato dal nome: si vede, ma non è la cosa che l'occhio incontra per
   *  prima su una riga che serve soprattutto a *entrare* nel progetto.
   *
   *  La domanda di conferma non è qui: la fa `deleteProjectFlow`, che è la
   *  stessa dei due chiamanti e nomina quante conversazioni si porta via. Qui
   *  c'è solo il seguito che appartiene a questa tendina — uscire dallo scope se
   *  era quello aperto, e ridisegnare un elenco che nomina ancora un progetto
   *  che non c'è più. */
  _projectRow(item, name) {
    const row = document.createElement('div');
    row.className = 'scope-menu-row';
    row.appendChild(item);

    const del = document.createElement('button');
    del.className = 'scope-menu-del';
    del.type = 'button';
    del.setAttribute('aria-label', i18n.t('scope.deleteProject', { name }));
    const icon = document.createElement('i');
    icon.className = 'ti ti-trash';
    del.appendChild(icon);
    del.addEventListener('click', async (e) => {
      // Il click non deve arrivare alla riga sotto: entrare nel progetto un
      // istante prima di chiedere se cancellarlo cambierebbe conversazione
      // sotto la finestra di conferma.
      e.stopPropagation();
      this.close();
      if (!(await deleteProjectFlow(name))) return;
      if (!this.leaveIfSelected(name)) {
        // Non era lo scope aperto: nessun cambio di conversazione, ma l'elenco
        // in cache nomina ancora un progetto che non c'è più.
        this._list.invalidate();
        await this._loadProjects();
      }
      showToast(i18n.t('workspace.deletedProject', { name }), 'success');
    });
    row.appendChild(del);
    return row;
  }

  leaveIfSelected(name) {
    if (this.scope.kind !== 'project' || this.scope.name !== name) return false;
    this._list.invalidate();            // l'elenco su disco e' cambiato
    this.select({ kind: 'personal', name: null });
    return true;
  }

  /** Un progetto nuovo, e ci si entra.
   *
   *  Le due domande e le cinque regole stanno in `shared/project-create.js`: le
   *  stesse servono al pannello della casa, e la prima volta che una delle due
   *  copie venisse corretta l'altra comincerebbe a mentire. Qui resta quel che
   *  è del chip: chi sono i nomi già noti, e dove si va dopo.
   */
  async _createProject() {
    const clean = await createProjectFlow({
      words: PROJECT_WORDS,
      t: (key, vars) => i18n.t(key, vars),
      known: this._projects || [],
    });
    if (!clean) return;
    this._list.invalidate();            // forza la rilettura da disco
    /* E ci si entra. Qui c'era `this.open()`: la tendina si riapriva sopra il
       toast e lasciava l'utente nella conversazione personale, con un secondo
       tocco da fare sulla riga appena creata — cioè aver dato un nome e scritto
       la riga di scope non portava a *lavorarci*. Vale allo stesso modo sul
       progetto completato (l'albero era rimasto a metà e il server l'ha finito):
       ha un nome, una mappa e un registro, quindi è un posto in cui si entra.
       `select` fa il resto — chip, placeholder, aggancio delle viste e cambio
       di conversazione. */
    this.select({ kind: 'project', name: clean });
  }

  /** "2 ore fa" da un mtime unix in secondi.
   *
   *  Il calcolo e le cinque frasi stanno in `conversation-list.js`, perche' le
   *  stesse date le stampa anche il pannello della casa. Il traduttore glielo
   *  passa chi chiama: quel modulo non importa niente, apposta.
   */
  _ago(modified) {
    return ago(modified, (key, vars) => i18n.t(key, vars));
  }
}

export const scopeChip = new ScopeChip();
