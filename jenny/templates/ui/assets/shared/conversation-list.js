/** L'elenco delle conversazioni: quali ci sono, in che ordine, e da quanto.
 *
 *  Una conversazione e' la personale (`websocket:default`) oppure un
 *  **quaderno** — che e' un progetto, che e' una wiki: le elenca
 *  `/api/projects` (`wiki_routes.py::_collect_projects`), divise in due liste
 *  perche' non tutte le cartelle si possono aprire.
 *
 *  Qui c'e' solo *cosa esiste e con quali regole*. Le regole sono cinque, sono
 *  costate care, e valgono uguali per chiunque disegni quell'elenco: la tendina
 *  dell'officina (`scope-chip.js`) e il pannello «con chi parli» della casa.
 *  Disegnare non e' mestiere di questo modulo, e infatti non tocca il DOM.
 *
 *  **Non importa niente**: ne' la rete ne' le traduzioni. La fetch arriva dal
 *  costruttore, il traduttore da chi chiama `ago()`. E' la forma di
 *  `wire-error.js`, e la ragione e' la stessa: i due gusci lo montano ognuno
 *  col proprio, e un banco di prova puo' esercitare *questo* codice invece di
 *  ricostruirne una copia che gli somiglia.
 */

/** Cartella che ospita i quaderni, finche' il backend non dice la sua.
 *
 *  Un progetto **e' una wiki**: non esiste una `projects/` separata. Il nome
 *  vero arriva col payload (`config.wiki.wikis_dir` e' configurabile); questo
 *  e' il default della config, cioe' il valore mostrato nel frattempo.
 */
export const DEFAULT_PROJECTS_DIR = 'wikis';

/** Dal piu' recente, e a parita' per nome.
 *
 *  L'ordine alfabetico del backend mette in cima la wiki con la lettera piu'
 *  bassa, che non e' mai quella che si cerca. Il criterio e' lo stesso
 *  `modified` che ogni riga stampa accanto al nome, quindi l'elenco non puo'
 *  contraddire quel che mostra; a parita' (mtime uguale, o mancante e quindi 0)
 *  decide il nome, per non avere un ordine che cambia a ogni apertura.
 */
function byRecent(items, pick) {
  return (items || [])
    .map(pick)
    .sort((a, b) => (b.modified || 0) - (a.modified || 0) || a.name.localeCompare(b.name));
}

/** Perché una cartella non si apre, detto nella lingua dell'utente.
 *
 *  `reason` è la sola parte di una voce di `unopenable` pensata per essere letta
 *  da un programma (`wiki_routes.py::_collect_projects` lo dice sul posto: il
 *  motivo lo scelga chi disegna la riga, non si indovini dal nome). Oggi ce n'è
 *  uno solo; un motivo che questa mappa non conosce prende una frase che **non
 *  nomina nessuna regola**, perché raccontare la regola dei nomi di una cartella
 *  rifiutata per un altro motivo è peggio che non spiegare niente.
 *
 *  La regola dei nomi non si riscrive qui: la frase è quella che il dialogo di
 *  creazione mostra già (`scope.invalidName`), interpolata dentro la nota. Di
 *  copie a mano di quella regola ce ne sono già tre (`session/keys.py`, lo
 *  scaffolder della skill, e `VALID_NAME` in `scope-chip.js`) — una quarta, e
 *  in prosa, si desincronizzerebbe senza che nessun test se ne accorga.
 *
 *  Sta qui e non nel chip perché la stessa domanda se la pone chiunque disegni
 *  quelle righe: `reason` arriva dal server, e la sua traduzione è una sola.
 */
export const UNOPENABLE_HINT_KEYS = {
  invalid_name: 'scope.unopenableInvalidName',
};

export class ConversationList {
  /** @param fetchProjects funzione senza argomenti che risolve col payload di
   *  `/api/projects`. Il modulo non sa da dove viene, e non deve saperlo. */
  constructor(fetchProjects) {
    this._fetch = fetchProjects;
    /** Nome vero della cartella dei quaderni, dal backend. */
    this.dir = DEFAULT_PROJECTS_DIR;
    /** I quaderni apribili. `null` = **non si sa ancora**, che non e' «non ce
     *  ne sono»: chi disegna deve poter distinguere i due casi. */
    this.projects = null;
    /** Le cartelle che ci sono e non si aprono, dallo stesso payload. `null`
     *  per la stessa ragione di `projects`. */
    this.unopenable = null;
    /** L'ultima lettura e' fallita: v. `load`. */
    this.loadFailed = false;
  }

  /** Butta la cache: la prossima `load` rilegge da disco.
   *
   *  Si usa dopo aver *cambiato* quel che c'e' su disco (creato o cancellato un
   *  quaderno), non dopo un guasto — quello ha la sua regola in `load`.
   */
  invalidate() {
    this.projects = null;
  }

  /** Rilegge l'elenco. Ritorna `true` se la lettura e' riuscita.
   *
   *  **Una lettura fallita non e' «nessun quaderno».** Qui c'era
   *  `projects = []`, e quello scriveva a schermo una frase che il client non
   *  sa: 401, 500, gateway ancora in piedi a meta' o telefono offline
   *  diventavano tutti "Nessun progetto ancora" — e buttavano via l'elenco
   *  buono letto un minuto prima. La risposta ovvia a quello schermo e' rifare
   *  il progetto, che e' il modo in cui nasce un doppione: due wiki con lo
   *  stesso scopo e la storia divisa fra le due, che nessuna delle due poi
   *  contiene. E un doppione non si ritira.
   *
   *  Quindi la cache non si tocca — quel che c'era resta, ed e' l'unica cosa
   *  vera che abbiamo — e chi disegna lo dichiara con una nota sua, distinta
   *  dall'elenco vuoto. Il `dir` neanche: un default sovrascritto sopra un
   *  valore letto dal backend farebbe sbagliare il confronto sul prossimo
   *  scope. `unopenable` neanche: buttarlo via rifarebbe sparire dallo schermo
   *  una cartella che c'e', che e' esattamente lo stato che quella lista esiste
   *  per evitare.
   */
  async load() {
    try {
      const data = await this._fetch();
      this.dir = data?.dir || DEFAULT_PROJECTS_DIR;
      this.projects = byRecent(data?.projects, (it) => ({
        name: it.name, modified: it.modified,
      }));
      // Stesso ordine delle righe apribili, e `reason` viaggia con la voce: la
      // riga la disegna chi sa cosa dire, e cosa dire dipende dal motivo.
      this.unopenable = byRecent(data?.unopenable, (it) => ({
        name: it.name, modified: it.modified, reason: it.reason,
      }));
      this.loadFailed = false;
      return true;
    } catch {
      this.loadFailed = true;
      return false;
    }
  }
}

/** "2 ore fa" da un mtime unix in secondi. Stringa vuota se non si sa.
 *
 *  `t` e' il traduttore di chi chiama — v. la nota in cima al file. Le chiavi
 *  sono `scope.ago.*`, e sono le stesse per tutti e due i gusci: una seconda
 *  copia di quelle cinque frasi sarebbe una seconda cosa da tenere allineata,
 *  per dire esattamente la stessa cosa.
 */
export function ago(modified, t) {
  if (!modified) return '';
  const minutes = Math.floor((Date.now() / 1000 - modified) / 60);
  if (minutes < 2) return t('scope.ago.now');
  if (minutes < 60) return t('scope.ago.minutes', { n: String(minutes) });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t('scope.ago.hours', { n: String(hours) });
  const days = Math.floor(hours / 24);
  if (days === 1) return t('scope.ago.yesterday');
  return t('scope.ago.days', { n: String(days) });
}
