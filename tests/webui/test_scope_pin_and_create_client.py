"""Il chip non aspetta il backend, e un progetto appena creato si apre.

Due difetti dello stesso modulo, entrambi sul «dopo».

**(a) la risposta arrivava un giro di rete in ritardo.** `select()` cambiava il
chip e chiamava `onSwitch`, ma il resto si raggiungeva solo da
`syncFromSession`, cioè dalla risposta del backend al caricamento del thread.
Se quel caricamento **falliva** — il caso normale su un telefono, e proprio
quello per cui `loadInitialHistory` ha una riga d'errore — `syncFromSession`
non arrivava affatto. La risposta la sapeva già `select`: l'utente l'aveva
appena toccata.

**Di (a) e' rimasto il patto, non il meccanismo.** Quel giro pubblicava anche
`AppState.pinnedWiki`, l'aggancio che diceva alle viste wiki e grafo
dell'officina quale progetto mostrare. Quelle viste sono uscite il 21/09/2026 —
elenco, mappa e lettore vivono in casa, dove il quaderno aperto **e'** la
conversazione aperta — e il 21/09/2026 se n'e' andata anche la pubblicazione,
che da allora era scritta e mai letta. Quel che i banchi qui sotto misurano e'
percio' lo scope del chip, che e' la cosa vera: quando cambia, chi lo decide, e
che il backend resti l'ultima parola.

**(b) creare un progetto non ci portava dentro.** Dopo il nome e la riga di scope
il chip faceva `this.open()`: la tendina si riapriva sopra il toast, l'utente
restava nella conversazione personale e gli toccava un secondo tocco sulla riga
appena comparsa. Il seguito ovvio di aver nominato un progetto e detto di cosa si
occupa è *lavorarci*.

E il rovescio di (b): un **rifiuto** non porta dentro niente. Il server ne ha due
— «ce l'hai già» e «c'è una cartella di mezzo che non è un progetto» — e nel
secondo caso entrare vorrebbe dire aprire una conversazione su una cartella che
non è una wiki, con il chip che nomina un progetto inesistente. In mezzo c'è il
caso per cui lo scaffolder è a top-up: l'albero rimasto a metà, che il server
**completa** invece di rifiutare — e in quel caso si entra, perché il progetto
adesso c'è.

E come si dice, quel rifiuto. `err.message` è il testo di un `CommandError`,
quindi **inglese**: interpolato nel toast localizzato dava «Creazione fallita:
project already exists: patreon». A schermo va la chiave che corrisponde al
*codice* — la sola parte della risposta pensata per un programma — e il messaggio
va in console. Il complemento è la validazione: la regex del client era più larga
di `keys.py::_PROJECT_NAME_RE` in tre modi (primo carattere, `..`, i 64
caratteri), quindi `.hidden` attraversava due dialoghi per farsi rifiutare in
inglese. Adesso è la stessa — *la stessa*, non più stretta: un nome che il server
accetta deve arrivarci, altrimenti il primo a rompersi è il recupero di un
albero rimasto a metà, che il server completa invece di rifiutare.

I metodi si estraggono dal sorgente e si eseguono in node, come in
``test_chat_switch_race_client.py`` e ``test_scope_chip_load_failure_client.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import function, member, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHIP_JS = ASSETS / "shared" / "scope-chip.js"
# Le due domande e le cinque regole non stanno più nel chip: stanno qui,
# perché le usa anche il pannello della casa.
CREATE_JS = ASSETS / "shared" / "project-create.js"
LIST_JS = ASSETS / "shared" / "conversation-list.js"


pytestmark = requires_node


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _member(source: str, name: str) -> str:
    return member(source, name, prefixes=("async ", "get ", "static "))


def _const(source: str, name: str) -> str:
    """La riga di una costante di modulo, presa dal sorgente e non riscritta.

    `VALID_NAME` decide quali nomi arrivano al server: una copia a mano nel test
    smetterebbe di misurare la regola vera al primo cambio.
    """
    m = re.search(rf"(?m)^const {re.escape(name)} = .*;$", source)
    assert m, f"const {name} non trovata"
    return m.group(0)


def _const_block(source: str, name: str) -> str:
    """Come :func:`_const`, per una costante su più righe (la mappa dei codici)."""
    m = re.search(rf"(?ms)^export const {re.escape(name)} = \{{.*?^\}};$", source)
    assert m, f"const {name} non trovata"
    return m.group(0)


_HARNESS = """
import assert from 'node:assert/strict';

/* `projectKey` serve a `keyFor`, che la chiama: la forma della chiave sta in
   `conversation-list.js` insieme all'elenco di cui e' l'indirizzo. */
const { ConversationList, projectKey } = await import('__LIST_URL__');

const i18n = {
  t: (key, vars) => 'i18n:' + key + (vars ? ':' + Object.values(vars).join(',') : ''),
};
__VALID_NAME__
__CREATE_ERROR_KEYS__
__PROJECT_WORDS__
__CREATE_FLOW__

/* `published` resta anche senza l'aggancio, e adesso misura una cosa diversa:
   scegliere un progetto **non scrive piu' niente di globale**. Era l'unico
   campo che il chip pubblicava; un `set` che ricompare qui e' un secondo posto
   in cui vive la risposta a «in che conversazione siamo». */
const AppState = {
  published: [],
  set(key, value) { AppState[key] = value; AppState.published.push([key, value]); },
};

// I due dialoghi, a risposte prenotate. `undefined` = annullato.
let answers = [];
const asked = [];
function promptDialog(text) { asked.push(text); return Promise.resolve(answers.shift()); }

/* L'avviso «questo nome c'e' gia'»: un conferma/annulla, non un rifiuto. La
   risposta si prenota, perche' entrambe le vie contano — proseguire e' il caso
   dell'albero rimasto a meta', che il server completa. */
let confirmAnswer = true;
const confirms = [];
function confirmDialog(text, okText) {
  confirms.push([text, okText]);
  return Promise.resolve(confirmAnswer);
}

const toasts = [];
function showToast(text, kind) { toasts.push([text, kind]); }

/* Il bivio a tre uscite della chat rimasta sotto un nome cancellato.
   `undefined` = chiuso senza scegliere, che non crea niente. */
let detailAnswer;
const details = [];
function detailDialog(spec) { details.push(spec); return Promise.resolve(detailAnswer); }
const escapeHtml = (s) => String(s);

/* La creazione lato server, a comando: `{...}` riuscita (progetto nuovo o
   albero completato, dal client si vedono uguali), `Error` rifiuto. */
let createOutcome = null;
const created = [];
const rpc = {
  createProject(name, seed) {
    created.push([name, seed]);
    return createOutcome instanceof Error
      ? Promise.reject(createOutcome)
      : Promise.resolve(createOutcome);
  },
};

class ScopeChip {
  constructor() {
    this.enabled = true;
    this.scope = { kind: 'personal', name: null };
    /* L'elenco vive in `conversation-list.js`, importato vero: qui si semina
       la cache come se una lettura fosse andata a buon fine, perche' il
       controllo sul nome gia' preso legge quella. */
    this._list = new ConversationList(() => Promise.resolve({}));
    this._list.projects = [{ name: 'vecchio', modified: 1 }];
    this._open = false;
    this.renders = 0;
    this.opened = 0;
    this.switched = [];
    this.onSwitch = (key, scope) => { this.switched.push([key, scope]); };
  }
  // Il disegno del chip non è oggetto di questi test: lo sono l'aggancio delle
  // viste e la conversazione sotto.
  render() { this.renders++; }
  open() { this.opened++; }
  __PROJECTS__
  __LOAD_FAILED__
  __DIR__
    __SYNC_FROM_SESSION__
  __KEY_FOR__
  __SELECT__
  __CREATE__
}

function makeChip() {
  AppState.published.length = 0;
  answers = [];
  asked.length = 0;
  toasts.length = 0;
  created.length = 0;
  confirms.length = 0;
  confirmAnswer = true;
  details.length = 0;
  detailAnswer = undefined;
  createOutcome = null;
  return new ScopeChip();
}

/* Il rifiuto **vero** del server: `ws-manager._settleRpc` mette il codice
   sull'Error e il messaggio inglese del `CommandError` come `.message`. Un
   Error senza codice non e' un rifiuto: e' il trasporto (gateway spento,
   nessuna risposta), e quei messaggi nascono gia' localizzati. */
function serverError(code, message) {
  const err = new Error(message);
  err.code = code;
  return err;
}
"""


def _harness() -> str:
    src = _read(CHIP_JS)
    flow = _read(CREATE_JS)
    lst = _read(LIST_JS)
    return (
        _HARNESS.replace(
            "__VALID_NAME__",
            _const(lst, "VALID_NAME") + "\n" + function(lst, "isOpenableProjectName"),
        )
        .replace("__CREATE_ERROR_KEYS__", _const_block(flow, "CREATE_ERROR_KEYS"))
        .replace("__PROJECT_WORDS__", _const_block(flow, "PROJECT_WORDS"))
        .replace("__CREATE_FLOW__", function(flow, "createProjectFlow"))
        .replace("__LIST_URL__", LIST_JS.as_uri())
        .replace("__PROJECTS__", _member(src, "_projects"))
        .replace("__LOAD_FAILED__", _member(src, "_loadFailed"))
        .replace("__DIR__", _member(src, "_dir"))
        .replace("__SYNC_FROM_SESSION__", _member(src, "syncFromSession"))
        .replace("__KEY_FOR__", _member(src, "keyFor"))
        .replace("__SELECT__", _member(src, "select"))
        .replace("__CREATE__", _member(src, "_createProject"))
    )


def _run_js(script: str) -> None:
    source = _harness() + "\n" + script
    run_js(source)


# ── (a) L'aggancio segue il chip ────────────────────────────────────────────


def test_choosing_a_project_switches_the_conversation_at_once() -> None:
    """Nessuna attesa in mezzo: la risposta la sa già il tocco.

    E non si scrive niente di globale. Fino al 21/09/2026 qui partiva anche
    `AppState.set('pinnedWiki', …)`, per le viste wiki e grafo dell'officina;
    uscite quelle, il chip e' tornato a essere l'unico posto in cui vive la
    risposta a «in che conversazione siamo». Una seconda copia altrove e' il
    difetto che l'aggancio aveva gia' fatto pagare una volta.
    """
    _run_js("""
      const chip = makeChip();
      chip.select({ kind: 'project', name: 'bordi' });

      assert.equal(chip.scope.kind, 'project');
      assert.equal(chip.scope.name, 'bordi',
                   'il chip non nomina il progetto che l\u2019utente ha appena scelto');
      assert.deepEqual(chip.switched, [['project:bordi', { kind: 'project', name: 'bordi' }]]);
      assert.deepEqual(AppState.published, [], 'la risposta ha una seconda copia globale');
    """)


def test_the_chip_is_right_even_when_the_thread_never_loads() -> None:
    """Il caso che rendeva il difetto permanente.

    `syncFromSession` lo chiama solo il caricamento riuscito: se quello
    fallisce non viene chiamato affatto, e tutto cio' che aspettasse la
    risposta del backend resterebbe sul progetto di prima — per sempre. Quindi
    `select` deve valere **da solo**: e' l'unica cosa che gira di sicuro.
    """
    _run_js("""
      const chip = makeChip();
      chip.select({ kind: 'project', name: 'patreon' });
      assert.equal(chip.scope.name, 'patreon');

      // Il caricamento del thread del prossimo progetto fallirà: chi possiede la
      // chat solleva e nessun `syncFromSession` arriverà mai.
      chip.onSwitch = () => { throw new Error('rete'); };
      assert.throws(() => chip.select({ kind: 'project', name: 'bordi' }));

      assert.equal(chip.scope.name, 'bordi',
                   'il chip e\u2019 rimasto sul progetto di prima, e non lo dira\u2019 mai piu\u2019');
    """)


def test_going_back_to_the_personal_chat_leaves_no_project_behind() -> None:
    """La personale e' uno scope come gli altri, non «il progetto di prima
    spento»: il nome deve cadere, o il prossimo messaggio va nel posto
    sbagliato."""
    _run_js("""
      const chip = makeChip();
      chip.select({ kind: 'project', name: 'bordi' });
      chip.select({ kind: 'personal', name: null });
      assert.equal(chip.scope.kind, 'personal');
      assert.equal(chip.scope.name, null);
      assert.deepEqual(chip.switched.at(-1), [null, { kind: 'personal', name: null }]);
    """)


def test_the_backend_confirmation_changes_nothing() -> None:
    """Il thread arriva e conferma quel che il chip sapeva già: dev'essere un
    non-evento. Finché `select` anticipa, ogni conferma che *rifà* qualcosa e'
    lavoro doppio su una risposta che non e' cambiata — ed era la forma del
    difetto quando la conferma ripubblicava l'aggancio."""
    _run_js("""
      const chip = makeChip();
      chip.select({ kind: 'project', name: 'bordi' });
      chip.syncFromSession({ project_path: '/w/wikis/bordi/wiki' });
      assert.equal(chip.scope.name, 'bordi');
      assert.equal(chip.switched.length, 1, 'la conferma ha rifatto il cambio di conversazione');
      assert.deepEqual(AppState.published, []);
    """)


def test_the_backend_still_wins_when_it_disagrees() -> None:
    """`select` è un'anticipazione, non una verità: chi decide resta il backend."""
    _run_js("""
      const chip = makeChip();
      chip.select({ kind: 'project', name: 'bordi' });
      // Il thread torna dalla personale (la chiave non era un progetto).
      chip.syncFromSession(null);
      assert.equal(chip.scope.kind, 'personal');
      assert.equal(chip.scope.name, null);
    """)


def test_reselecting_the_same_project_switches_nothing() -> None:
    """Un no-op non deve far ripartire il caricamento della conversazione."""
    _run_js("""
      const chip = makeChip();
      chip.select({ kind: 'project', name: 'bordi' });
      chip.select({ kind: 'project', name: 'bordi' });
      assert.equal(chip.switched.length, 1);
    """)


# ── (b) Creare un progetto vuol dire entrarci ───────────────────────────────


def test_creating_a_project_opens_it() -> None:
    """Nome, riga di scope, e ci si lavora: senza un secondo tocco."""
    _run_js("""
      const chip = makeChip();
      answers = ['bordi', 'i bordi delle stampe'];
      createOutcome = { name: 'bordi', created: ['AGENTS.md'], seeded: true };

      await chip._createProject();

      assert.deepEqual(created, [['bordi', 'i bordi delle stampe']]);
      assert.equal(chip.scope.kind, 'project');
      assert.equal(chip.scope.name, 'bordi', 'il chip è rimasto sulla personale');
      assert.deepEqual(chip.switched, [['project:bordi', { kind: 'project', name: 'bordi' }]],
                       'la conversazione non è cambiata: il messaggio andrebbe nella personale');
      assert.equal(chip.opened, 0, 'la tendina si riapre sopra il toast invece di lasciar lavorare');
      assert.equal(chip._projects, null, "l'elenco va riletto: il progetto nuovo non c'è");
      assert.deepEqual(toasts, [['i18n:scope.created:bordi', 'success']]);
    """)


def test_completing_a_half_built_project_opens_it_too() -> None:
    """L'albero rimasto a metà: il server lo finisce, e adesso è un posto in cui si entra.

    Dal lato client è una riuscita come le altre — `seeded: false` dice solo che
    la riga di scope c'era già e non è stata riscritta — e l'esito deve essere lo
    stesso: il progetto ha nome, mappa e registro.
    """
    _run_js("""
      const chip = makeChip();
      answers = ['bordi', 'la riga di stavolta'];
      createOutcome = { name: 'bordi', created: ['wiki/index.md'], seeded: false };

      await chip._createProject();

      assert.equal(chip.scope.name, 'bordi');
      assert.deepEqual(chip.switched, [['project:bordi', { kind: 'project', name: 'bordi' }]]);
      assert.equal(chip.opened, 0);
    """)


def test_a_refusal_leaves_you_exactly_where_you_were() -> None:
    """«C'è una cartella di mezzo»: entrarci vorrebbe dire nominare un progetto inesistente."""
    _run_js("""
      const chip = makeChip();
      answers = ['ostacolo', 'qualcosa'];
      createOutcome = serverError(
        'bad_request',
        'a folder named ostacolo is in the way: it exists but is not a project');

      await chip._createProject();

      assert.equal(chip.scope.kind, 'personal', 'il chip nomina un progetto che non esiste');
      assert.deepEqual(AppState.published, []);
      assert.deepEqual(chip.switched, [], 'il prossimo messaggio andrebbe in una cartella qualsiasi');
      assert.equal(toasts.length, 1);
      assert.equal(toasts[0][1], 'error');
      // Il nome nella frase è quello che l'utente ha scritto, non testo del server.
      assert.equal(toasts[0][0], 'i18n:scope.createRejected:ostacolo');
    """)


def test_a_project_that_already_exists_is_a_refusal_as_well() -> None:
    """L'altro rifiuto: non si entra da qui, si sceglie dalla tendina."""
    _run_js("""
      const chip = makeChip();
      chip.select({ kind: 'project', name: 'patreon' });
      AppState.published.length = 0;
      answers = ['bordi', 'qualcosa'];
      createOutcome = serverError('bad_request', 'project already exists: bordi');

      await chip._createProject();

      assert.equal(chip.scope.name, 'patreon', 'un rifiuto ha spostato la conversazione');
      assert.deepEqual(AppState.published, []);
      assert.equal(chip.switched.length, 1, 'nessun cambio in più oltre a quello iniziale');
      assert.equal(toasts[0][0], 'i18n:scope.createRejected:bordi');
    """)


# ── T5.5 (a) Il rifiuto parla la lingua dell'utente ─────────────────────────


def test_no_server_english_reaches_the_toast() -> None:
    """Il difetto: «Creazione fallita: project already exists: patreon».

    `err.message` e' un `CommandError`, quindi inglese: interpolato nel toast
    localizzato dava mezza frase in una lingua che l'utente non ha scelto. A
    schermo va la chiave che corrisponde al **codice**, che e' la sola parte
    della risposta pensata per essere letta da un programma.
    """
    _run_js("""
      const cases = [
        ['bad_request', 'project already exists: bordi', 'i18n:scope.createRejected:bordi'],
        ['too_large', 'scope line too long (400 > 280 characters)',
         'i18n:scope.createSeedTooLong:bordi'],
        ['unavailable', 'wiki is disabled', 'i18n:scope.createWikiOff:bordi'],
        ['internal', 'command failed', 'i18n:scope.createInternal:bordi'],
        // Un codice che questa versione del client non conosce vale come un
        // guasto del gateway: una frase generica nella lingua giusta batte una
        // precisa in un'altra.
        ['forbidden', 'permission denied', 'i18n:scope.createInternal:bordi'],
      ];
      for (const [code, message, expected] of cases) {
        const chip = makeChip();
        answers = ['bordi', 'una riga'];
        createOutcome = serverError(code, message);
        await chip._createProject();
        assert.deepEqual(toasts, [[expected, 'error']], 'codice ' + code);
        assert.equal(toasts[0][0].includes(message), false,
                     'il testo inglese del server e\\' finito a schermo (' + code + ')');
        // E un rifiuto non porta dentro, qualunque sia il codice.
        assert.equal(chip.scope.kind, 'personal');
        assert.deepEqual(chip.switched, []);
      }
    """)


def test_a_transport_failure_still_says_what_happened() -> None:
    """Senza codice l'errore non viene dal server ma da `ws-manager.request`.

    «Gateway non raggiungibile» e «Nessuna risposta dal gateway» nascono da
    `i18n.t` la' dove vengono sollevate: sono le sole stringhe che possono
    passare cosi' come sono, e dirle e' piu' utile di un generico.
    """
    _run_js("""
      const chip = makeChip();
      answers = ['bordi', 'una riga'];
      createOutcome = new Error('i18n:common.gatewayOffline');   // già localizzata
      await chip._createProject();
      assert.deepEqual(toasts, [['i18n:scope.createFailed:i18n:common.gatewayOffline', 'error']]);
    """)


# ── T5.5 (b) La validazione è la stessa domanda del server ─────────────────


def test_the_names_the_server_refuses_never_leave_the_dialog() -> None:
    """Le tre larghezze di troppo: primo carattere, `..`, e i 64 caratteri.

    Ognuna di queste faceva attraversare **due** dialoghi per farsi rifiutare
    dal server in inglese. La regola e' quella di `_PROJECT_NAME_RE`.
    """
    _run_js("""
      const refused = [
        '.hidden',                       // punto iniziale: cartella nascosta
        '-x',                            // trattino iniziale
        '_x',                            // underscore iniziale
        'a..b',                          // `..` che la forma da sola non vede
        'con spazi',
        'sotto/cartella',
        'a'.repeat(65),                  // 64 e' il tetto del server
      ];
      for (const name of refused) {
        const chip = makeChip();
        answers = [name, 'una riga'];
        await chip._createProject();
        assert.deepEqual(created, [], 'il server lo rifiuterebbe: ' + name);
        assert.deepEqual(asked.length, 1, 'e non deve costare il secondo dialogo: ' + name);
        assert.deepEqual(toasts, [['i18n:scope.invalidName', 'error']], name);
      }
    """)


def test_every_name_the_server_accepts_gets_through() -> None:
    """Il controllo qui e' un avviso, non un secondo cancello.

    Un client piu' stretto del server e' una seconda verita' sulla forma dei
    nomi, e il primo a pagarla e' chi ha un nome legittimo che non passa piu'.
    """
    _run_js("""
      const accepted = [
        'patreon', 'a', 'A1', 'zz-bordi', 'con.punto', 'con_underscore',
        '9-inizia-con-cifra', 'a'.repeat(64),   // esattamente il tetto
      ];
      for (const name of accepted) {
        const chip = makeChip();
        answers = [name, 'una riga'];
        createOutcome = { name, created: ['AGENTS.md'], seeded: true };
        await chip._createProject();
        assert.deepEqual(created, [[name, 'una riga']], 'il client rifiuta ' + name);
        assert.equal(chip.scope.name, name);
      }
    """)


def test_a_name_already_in_the_list_is_flagged_before_the_second_prompt() -> None:
    """Dirlo dopo la riga di scope vuol dire farla scrivere per niente."""
    _run_js("""
      const chip = makeChip();          // in elenco c'è già `vecchio`
      answers = ['vecchio', 'una riga'];
      confirmAnswer = false;            // l'utente ci ripensa

      await chip._createProject();

      assert.equal(confirms.length, 1, "il nome duplicato non è stato segnalato");
      assert.deepEqual(confirms[0], ['i18n:scope.nameTaken:vecchio',
                                     'i18n:scope.nameTakenContinue']);
      assert.deepEqual(asked, ['i18n:scope.newProjectName'],
                       'la riga di scope è stata chiesta comunque');
      assert.deepEqual(created, []);
      assert.deepEqual(chip.switched, []);
    """)


def test_the_warning_can_be_walked_through_because_the_server_may_accept() -> None:
    """La ragione per cui e' un avviso: l'albero rimasto a meta'.

    `/api/projects` elenca ogni cartella che contiene `wiki/`, e lo scaffolder la
    crea per prima: un albero morto a meta' **e' in elenco**, e il server lo
    completa invece di rifiutarlo. Un rifiuto qui renderebbe irreparabile
    esattamente il caso in cui questo dialogo serve a riparare.
    """
    _run_js("""
      const chip = makeChip();
      answers = ['vecchio', 'la riga di stavolta'];
      confirmAnswer = true;
      createOutcome = { name: 'vecchio', created: ['wiki/index.md'], seeded: false };

      await chip._createProject();

      assert.deepEqual(created, [['vecchio', 'la riga di stavolta']],
                       'il client ha rifiutato un nome che il server completa');
      assert.equal(chip.scope.name, 'vecchio');
    """)


def test_an_unread_list_raises_no_warning_at_all() -> None:
    """Con la cache vuota (`null`) non si sa niente: non si avvisa di niente."""
    _run_js("""
      const chip = makeChip();
      chip._list.invalidate();          // nessuna lettura riuscita: `null`, non `[]`
      answers = ['vecchio', 'una riga'];
      createOutcome = { name: 'vecchio', created: [], seeded: true };
      await chip._createProject();
      assert.deepEqual(confirms, []);
      assert.deepEqual(created, [['vecchio', 'una riga']]);
    """)


def test_nothing_is_created_and_nothing_is_entered_without_both_answers() -> None:
    """Annullare la riga di scope annulla la creazione: nessuna chiamata, nessun cambio."""
    _run_js("""
      // Nome annullato.
      let chip = makeChip();
      answers = [undefined];
      await chip._createProject();
      assert.deepEqual(created, []);
      assert.deepEqual(chip.switched, []);

      // Riga di scope annullata: il nome da solo non crea niente.
      chip = makeChip();
      answers = ['bordi', '   '];
      await chip._createProject();
      assert.deepEqual(created, []);
      assert.deepEqual(chip.switched, []);
      assert.equal(chip.scope.kind, 'personal');
      assert.deepEqual(toasts, [['i18n:scope.seedRequired', 'info']]);

      // Nome non valido: nemmeno arriva al secondo dialogo.
      chip = makeChip();
      answers = ['../fuori', 'qualcosa'];
      await chip._createProject();
      assert.deepEqual(created, []);
      assert.deepEqual(chip.switched, []);
      assert.deepEqual(toasts, [['i18n:scope.invalidName', 'error']]);
    """)


def test_every_word_the_flow_names_exists_in_both_languages() -> None:
    """Una chiave che manca stampa se stessa: `scope.createRejected` a schermo.

    Il giro non conosce nessuna stringa: conosce dei *posti* (`words`), e chi lo
    chiama ci mette le proprie chiavi. Quindi si controlla il vocabolario intero
    dell'officina, non solo la mappa dei codici — compreso il ripiego per un
    codice sconosciuto, che è la sola voce raggiungibile senza essere nominata
    dalla mappa.
    """
    import json

    flow = _read(CREATE_JS)
    slots = set(re.findall(r"(?m)^\s*(\w+): '([^']+)'", _const_block(flow, "PROJECT_WORDS")))
    keys = {key for _, key in slots}
    assert len(keys) >= 15, f"il vocabolario si è accorciato: {sorted(keys)}"
    # Ogni posto che la mappa dei codici nomina deve esistere nel vocabolario.
    codes = set(re.findall(r"(?m)^\s*\w+: '([^']+)'", _const_block(flow, "CREATE_ERROR_KEYS")))
    names = {name for name, _ in slots}
    assert codes <= names, f"la mappa dei codici nomina posti che non esistono: {codes - names}"
    assert "internal" in names, "manca il ripiego per un codice che il client non conosce"

    for locale in ("it", "en"):
        data = json.loads((ASSETS / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))
        for key in sorted(keys):
            leaf = data
            for part in key.split("."):
                assert isinstance(leaf, dict) and part in leaf, f"{locale}: manca {key}"
                leaf = leaf[part]
            assert isinstance(leaf, str) and leaf.strip(), f"{locale}: {key} è vuota"
    # E il nome che l'utente ha scritto entra nelle due frasi che lo nominano.
    for locale in ("it", "en"):
        data = json.loads((ASSETS / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))
        for key in ("createRejected", "nameTaken"):
            assert "{name}" in data["scope"][key], f"{locale}: scope.{key} non nomina il progetto"


# ── Guardie sul testo del sorgente ─────────────────────────────────────────
#
# Deboli per costruzione: provano che una riga c'è, non che faccia effetto.


def test_the_pin_is_gone_from_the_product() -> None:
    """L'aggancio delle viste non esiste piu' in nessun file.

    Era il complemento di ``test_project_views_contract``, che teneva a **uno**
    il numero di scrittori di `pinnedWiki`: due scrittori sono due risposte a
    «in che progetto siamo», e divergono. Dal 21/09/2026 gli scrittori sono
    zero, perche' i lettori erano zero — le viste wiki e grafo dell'officina
    erano le uniche, e sono uscite. Quel banco e' sparito con loro; questo ne
    prende il posto e dice la cosa piu' forte: il campo non c'e'.

    Vale la pena di un banco perche' uno stato globale scritto e mai letto non
    si nota — nessun test diventa rosso, niente si rompe a schermo — e il modo
    in cui torna e' che qualcuno ne abbia di nuovo bisogno e lo ripubblichi
    invece di chiedere al chip, che e' l'unico a saperlo.
    """
    culprits = [
        path.name
        for path in sorted(ASSETS.rglob("*.js"))
        if "vendor" not in path.parts and "pinnedWiki" in _read(path)
    ]
    # `scope-chip.js` lo **nomina** nel commento di `select`, che racconta dove
    # era finito: il commento e' la ragione per cui questo file e' l'eccezione.
    assert culprits == ["scope-chip.js"], culprits
    src = _read(CHIP_JS)
    assert "_publishPin" not in _member(src, "select")
    assert "AppState.set('pinnedWiki'" not in src
    assert "pinnedWiki" not in _read(ASSETS / "shared" / "state.js"), (
        "il campo e' tornato dentro AppState"
    )


def test_the_creation_no_longer_reopens_the_menu() -> None:
    """Guardia debole: la riga che lasciava l'utente fuori dal suo progetto.

    Il metodo è diventato sottile — le domande le fa il giro condiviso — e quel
    che resta suo è dove si va dopo: dentro, e solo se qualcosa è stato creato.
    """
    body = _member(_read(CHIP_JS), "_createProject")
    code = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$|\s//.*$", "", code)
    assert "this.open()" not in code, (
        "la tendina si riapre sopra il toast e il progetto appena creato resta da aprire"
    )
    assert re.search(r"this\.select\(\{ kind: 'project', name: clean \}\)", code)
    # Niente creato, niente select: `createProjectFlow` torna `null` in tutte le
    # uscite che non hanno scritto su disco, e questa è la riga che ci crede.
    assert code.index("if (!clean) return;") < code.index("this.select("), (
        "un giro annullato o rifiutato porterebbe dentro un progetto che non c'è"
    )
