/** «Con chi parli» — la pagina Quaderni.
 *
 *  Dice con quale conversazione stai parlando, e quali quaderni ci sono. Un
 *  quaderno e' un progetto, che e' una wiki: l'elenco, l'ordine e le sue regole
 *  stanno in `shared/conversation-list.js`, che e' lo stesso modulo su cui si
 *  appoggia la tendina dell'officina. Qui c'e' solo il disegno.
 *
 *  **Un tocco cambia conversazione**, e la spunta e' su quella in cui sei. Per
 *  un giro non e' stato cosi' — le righe erano inerti e lo dichiaravano (v.
 *  `.agent/casa-who-plan.md`, D1) — e quel giro e' finito con
 *  `.agent/casa-notebook-plan.md`: le righe sono diventate bottoni e il resto
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
 *  titolo in un `<dialog>`; da allora e' la pagina Quaderni della pista (v.
 *  `.agent/pagine-in-alto-plan.md`): chi la ospita le da' il contenitore e la
 *  ridisegna quando ci arrivi (`mostra`). Non c'e' niente da aprire ne' da
 *  chiudere, e toccare una riga lascia al guscio di portarti alla chat.
 */

import { i18n } from './shared/i18n.js';
import { api } from './shared/api-client.js';
import { ConversationList, UNOPENABLE_HINT_KEYS, ago } from './shared/conversation-list.js';
import { setupLongPress } from './shared/longpress.js';

export class WhoPanel {
  /** @param contenitore dove si disegna: il pannello della pagina Quaderni.
   *  @param personalName funzione che da' il nome della conversazione
   *         personale. **Non** si legge dalla testa: la testa delle stanze
   *         porta il nome del quaderno aperto, e leggerlo di li' farebbe dire
   *         alla riga personale il nome di un quaderno.
   *  @param currentProject funzione che da' il nome del quaderno aperto, o
   *         `null` se sei nella conversazione personale. E' cio' che decide
   *         dove sta la spunta.
   *  @param onPick chiamata col nome del quaderno toccato — `null` per la
   *         conversazione personale.
   *  @param onHold chiamata col nome del quaderno **tenuto premuto**: apre la
   *         sua scheda (Apri · Metti come pagina · Rinomina · Elimina), come la
   *         pressione lunga su un'app nel cassetto. Una cosa si appende dal
   *         posto dove vive, e i quaderni vivono qui.
   */
  constructor(contenitore, { personalName, currentProject, onPick, onHold } = {}) {
    this._personalName = personalName;
    this._currentProject = currentProject || (() => null);
    this._onPick = onPick || null;
    this._onHold = onHold || null;
    this._list = new ConversationList(() => api.listProjects());
    this._body = contenitore || null;
  }

  /** I quaderni che il pannello conosce, per chi sta per crearne uno.
   *
   *  Puo' essere vecchio o non essere mai stato letto, ed e' previsto: chi lo
   *  usa lo usa per un *avviso*, non per un rifiuto (v. `project-create.js`).
   */
  get known() {
    return this._list.projects || [];
  }

  /** Quante pagine ha il quaderno *name*, o `null` se non si sa.
   *
   *  Legge la stessa cache della pagina, e la riempie se e' vuota: la
   *  pastiglia delle pagine compare entrando in un quaderno, che puo' essere
   *  prima che la pagina Quaderni sia stata guardata anche una volta.
   *
   *  `null` e **non** zero quando la lettura non e' riuscita o la voce non c'e':
   *  «non lo so» e «e' vuoto» sono due cose diverse, e la seconda si scrive a
   *  schermo mentre la prima no.
   */
  async pagesOf(name) {
    if (!name) return null;
    if (this._list.projects === null) await this._list.load();
    const found = (this._list.projects || []).find((it) => it.name === name);
    return found && typeof found.pages === 'number' ? found.pages : null;
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
  async mostra() {
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
    const scorso = body.scrollTop;
    body.innerHTML = '';

    body.appendChild(this._label(i18n.t('home.who.title')));
    body.appendChild(this._personalRow());

    body.appendChild(this._label(i18n.t('home.who.notebooks'), true));
    /* L'elenco dei quaderni ha un contenitore suo: e' la parte che cresce, e
       il banco lo trova per nome. */
    const list = document.createElement('div');
    list.className = 'home-who-list';
    body.appendChild(list);

    /* I tre stati sono tre, e non due: *non lo so ancora*, *non ce n'e'*, e
       *non sono riuscita a leggerlo*. Confondere l'ultimo col secondo e' il
       difetto da cui nasce `conversation-list.js`, e la nota del guasto va
       **sopra** le righe perche' quelle possono essere vecchie e questa e'
       l'unica cosa che lo dice. */
    if (this._list.loadFailed) {
      list.appendChild(this._note(i18n.t('home.who.loadFailed'), true));
    }
    const projects = this._list.projects;
    if (projects === null) {
      if (!this._list.loadFailed) list.appendChild(this._note(i18n.t('scope.loading')));
    } else if (!projects.length) {
      if (!this._list.loadFailed) list.appendChild(this._note(i18n.t('home.who.none')));
    } else {
      for (const project of projects) list.appendChild(this._row(project));
    }

    /* Le cartelle che ci sono e non si aprono, in fondo. **Si mostrano**: su un
       telefono non c'e' un file manager con cui rinominarle, la sola strada e'
       chiederlo a Jenny — e questa e' proprio la chat da cui si chiede.
       Sparire dall'elenco sarebbe indistinguibile dall'essere state cancellate.
       Una spiegazione per motivo, non per riga. */
    const blocked = this._list.unopenable || [];
    if (blocked.length) {
      list.appendChild(this._label(i18n.t('scope.unopenableSection')));
      for (const folder of blocked) list.appendChild(this._row(folder, true));
      for (const reason of [...new Set(blocked.map((it) => it.reason))]) {
        const key = UNOPENABLE_HINT_KEYS[reason] || 'scope.unopenableOther';
        list.appendChild(this._note(i18n.t(key, { rule: i18n.t('scope.invalidName') })));
      }
    }

    /* «Nuovo quaderno» non sta qui: e' il tasto + tondo della pagina, fermo
       sopra l'elenco che scorre (index.html, `#home-notebooks-new`). Una riga
       in fondo all'elenco, con tanti quaderni, finiva sotto il bordo. */
    if (scorso) body.scrollTop = scorso;
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

  /* La casa: si torna sempre, e non ha un pallino perche' non e' un quaderno
     fra i quaderni. Ha il fiore, che e' il segno di Jenny e non un colore
     assegnato: lo stesso `✿` del dock e della riga d'identita' in officina,
     sulla stessa variabile di tema (`--flower`). */
  _personalRow() {
    const row = this._command(this._currentProject() === null, () => this._pick(null));
    row.classList.add('is-personal');

    const flower = document.createElement('span');
    flower.className = 'home-who-flower';
    flower.textContent = '✿';
    flower.setAttribute('aria-hidden', 'true');
    row.appendChild(flower);

    const name = document.createElement('span');
    name.className = 'home-who-row-name';
    name.textContent = this._personalName();
    row.appendChild(name);

    const kind = document.createElement('span');
    kind.className = 'home-who-row-meta';
    kind.textContent = i18n.t('home.who.personal');
    row.appendChild(kind);

    this._maybeCheck(row);
    return row;
  }

  _row(item, blocked = false) {
    /* Una cartella non apribile non e' un comando: il gateway rifiuta quella
       chiave (v. `_envelope_chat_id`), quindi qui sarebbe un bottone con un no
       gia' scritto dall'altra parte. */
    const row = blocked
      ? document.createElement('div')
      : this._command(item.name === this._currentProject(), () => this._pick(item.name));
    if (blocked) row.className = 'home-who-row is-blocked';
    /* La pressione lunga solo sui quaderni che si aprono: una cartella inerte
       non ha niente da offrire, e la riga personale non e' un quaderno — non
       si appende (ha gia' la sua pagina, la chat) e non si cancella. */
    if (!blocked && this._onHold) setupLongPress(row, () => this._onHold(item.name));

    const dot = document.createElement('span');
    dot.className = 'home-who-dot';
    if (!blocked) dot.style.background = dotColor(item.name);
    row.appendChild(dot);

    const name = document.createElement('span');
    name.className = 'home-who-row-name';
    name.textContent = item.name;
    row.appendChild(name);

    const when = document.createElement('span');
    when.className = 'home-who-row-meta';
    when.textContent = ago(item.modified, (key, vars) => i18n.t(key, vars));
    row.appendChild(when);

    if (!blocked) this._maybeCheck(row);
    return row;
  }

  /* Lo scheletro di una riga che si puo' toccare. `aria-current` e non solo la
     spunta: la spunta e' un'icona decorativa, e chi non la vede deve comunque
     sapere su quale riga si trova. */
  _command(current, onPick) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'home-who-row';
    if (current) {
      row.classList.add('is-current');
      row.setAttribute('aria-current', 'true');
    }
    /* Il tocco che segue una pressione lunga non e' un tocco: senza questa
       riga tenere premuto un quaderno aprirebbe la sua scheda **e** ci
       cambierebbe conversazione sotto. Stesso segno del cassetto. */
    row.addEventListener('click', () => {
      if (row.dataset.longpress) {
        delete row.dataset.longpress;
        return;
      }
      onPick();
    });
    return row;
  }

  _maybeCheck(row) {
    if (!row.classList.contains('is-current')) return;
    const check = document.createElement('i');
    check.className = 'ti ti-check home-who-check';
    check.setAttribute('aria-hidden', 'true');
    row.appendChild(check);
  }

  /* Il nome toccato va a chi ospita la pagina: lui cambia conversazione e
     porta alla chat. */
  _pick(name) {
    this._onPick?.(name);
  }
}

/** Un colore per il pallino, stabile nel nome.
 *
 *  **Non significa niente oltre l'identita'**: serve a riconoscere una riga in
 *  un elenco di nomi che si somigliano, non a dire uno stato. Per questo non
 *  esce dai token del tema — che vorrebbero dire qualcosa: un pallino
 *  `--error` su un quaderno sano si legge come un allarme — ma da una rampa di
 *  tinte tutte alla stessa saturazione e alla stessa chiarezza, che stanno
 *  sopra qualunque sfondo dei sette temi.
 */
export function dotColor(name) {
  let hash = 0;
  for (const ch of String(name || '')) hash = (hash * 31 + ch.codePointAt(0)) % 360;
  return `hsl(${hash}, 38%, 58%)`;
}
