"""«Con chi parli»: cosa scrive la tendina della casa, e cosa non promette.

Il pannello dice due cose: con quale conversazione stai parlando, e quali
quaderni ci sono. Le regole dell'elenco — l'ordine, la divisione fra apribili e
non, la cache che sopravvive a una lettura fallita — stanno in
`conversation-list.js` e hanno i loro test dalla parte dell'officina; qui si
misura **quel che finisce a schermo**, che e' l'altra meta' e non si deduce
dalla prima: un elenco giusto disegnato nell'ordine sbagliato resta sbagliato.

E si misura cosa fa un tocco: apre quella conversazione, e la spunta segue
quella in cui sei. Per un giro non e' stato cosi' — le righe erano inerti e i
banchi garantivano l'inerzia (`.agent/casa-who-plan.md`, D1) — e quei due
banchi sono stati **rovesciati**, non cancellati: chi li vede passare oggi deve
sapere che promettono il contrario di ieri. Le righe delle cartelle non
apribili sono rimaste inerti, e quella proprieta' ha un banco suo.

I membri si estraggono dal sorgente e si eseguono in node, come gli altri
banchi della casa; `conversation-list.js` invece si importa **vero**, perche'
non importa niente a sua volta. La `t()` e' quella di `i18n.js` sulle
traduzioni vere: quel che si legge nelle asserzioni e' la frase italiana che
legge l'utente.
"""

from __future__ import annotations

import json
from pathlib import Path

from support.js_harness import function, locale, member, requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
WHO_JS = ASSETS / "casa-who.js"
LIST_JS = ASSETS / "shared" / "conversation-list.js"
I18N_JS = ASSETS / "shared" / "i18n.js"


pytestmark = requires_node


def _who() -> str:
    return WHO_JS.read_text(encoding="utf-8")


_HARNESS = """
import assert from 'node:assert/strict';

const { ConversationList, UNOPENABLE_HINT_KEYS, ago } = await import('__LIST_URL__');

/* La `t()` vera sulle traduzioni vere: le note si leggono come le legge
   l'utente, regola dei nomi interpolata dentro. */
const TRANSLATIONS = __TRANSLATIONS__;
const i18n = {
  locale: 'it',
  translations: TRANSLATIONS,
  __T__
};

function makeEl(tag) {
  const el = {
    tag,
    className: '',
    textContent: '',
    style: {},
    attrs: {},
    dataset: {},
    listeners: [],
    children: [],
    setAttribute(k, v) { el.attrs[k] = v; },
    addEventListener(type, fn) { el.listeners.push({ type, fn }); },
    appendChild(child) { el.children.push(child); return child; },
    classList: {
      add(...names) {
        const have = String(el.className).split(' ').filter(Boolean);
        el.className = [...have, ...names.filter((n) => !have.includes(n))].join(' ');
      },
      contains(name) { return String(el.className).split(' ').includes(name); },
    },
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return ''; },
    set(v) { if (!v) el.children.length = 0; },
  });
  return el;
}
const document = { createElement: (tag) => makeEl(tag) };

// La rete a mano: l'elenco risolve o fallisce a comando.
let nextPayload = null;
const api = {
  listProjects() {
    if (nextPayload === 'fail') return Promise.reject(new Error('500'));
    return Promise.resolve(nextPayload);
  },
};

__DOT_COLOR__

/* La pressione lunga finta: quella vera sta in `shared/longpress.js` e si
   prova li'. Qui conta **quali righe** la armano, e che il tocco che la segue
   non cambi conversazione — per questo mette lo stesso segno di quella vera. */
const premute = [];
function setupLongPress(el, cb) { premute.push({ el, cb }); }
function tieni(riga) {
  const p = premute.find((x) => x.el === riga);
  assert.ok(p, 'quella riga non si puo tenere premuta');
  riga.dataset.longpress = 'true';
  p.cb();
}

class Panel {
  constructor() {
    this._personalName = () => 'Jenny';
    this._currentProject = () => null;
    /* Cosa e' successo e in che ordine. Dal 23/09/2026 e' una pagina: non c'e'
       niente da chiudere prima di cambiare conversazione. */
    this.storia = [];
    this._onPick = (name) => this.storia.push('scelto:' + name);
    this._onHold = (name) => this.storia.push('tenuto:' + name);
    this._list = new ConversationList(() => api.listProjects());
    this._body = makeEl('div');
  }
  __RENDER__
  __LABEL__
  __NOTE__
  __PERSONAL_ROW__
  __ROW__
  __COMMAND__
  __MAYBE_CHECK__
  __PICK__
  __PAGES_OF__
}

/* Tutti i nodi, in ordine di disegno. */
function walk(el, out = []) {
  for (const c of el.children) { out.push(c); walk(c, out); }
  return out;
}

function righeDi(panel) {
  return walk(panel._body).filter((n) => {
    const cls = String(n.className);
    return cls.split(' ')[0] === 'casa-who-row' && !cls.includes('casa-who-new');
  });
}

/* Il comando in fondo, tenuto da parte: non è una conversazione. */
function nuovoDi(panel) {
  return walk(panel._body).find((n) => String(n.className).includes('casa-who-new'));
}

function tocca(riga) {
  const click = riga.listeners.find((l) => l.type === 'click');
  assert.ok(click, 'quella riga non risponde a un tocco');
  click.fn();
}

/* Quel che si legge nel pannello, dall'alto in basso: ogni nodo con il suo
   ruolo, così l'asserzione parla di ciò che vede l'utente. */
function readout(panel) {
  const out = [];
  const walk = (el) => {
    for (const child of el.children) {
      const cls = String(child.className);
      if (cls.startsWith('casa-who-label')) out.push('etichetta: ' + child.textContent);
      else if (cls.startsWith('casa-who-note')) {
        out.push((cls.includes('is-error') ? 'guasto: ' : 'nota: ') + child.textContent);
      } else if (cls.split(' ')[0] === 'casa-who-row') {
        const parts = child.children.map((c) => c.textContent).filter(Boolean);
        const mark = cls.includes('casa-who-new') ? '+'
          : (cls.includes('is-personal') ? 'io' : (cls.includes('is-blocked') ? 'x' : '-'));
        out.push(mark + ' ' + parts.join(' · '));
      } else walk(child);
    }
  };
  walk(panel._body);
  return out;
}

const ELENCO = {
  dir: 'wikis',
  projects: [
    { name: 'piante', modified: 100 },
    { name: 'memory', modified: 300 },
    { name: 'etf', modified: 200 },
  ],
  unopenable: [],
};

async function open(payload) {
  const panel = new Panel();
  nextPayload = payload;
  await panel._list.load();
  panel.render();
  return panel;
}
"""


def _harness() -> str:
    src = _who()
    return (
        _HARNESS.replace("__LIST_URL__", LIST_JS.as_uri())
        .replace("__TRANSLATIONS__", json.dumps({"it": locale("it")}, ensure_ascii=False))
        .replace("__T__", member(I18N_JS.read_text(encoding="utf-8"), "t"))
        .replace("__DOT_COLOR__", function(src, "dotColor"))
        .replace("__RENDER__", member(src, "render"))
        .replace("__LABEL__", member(src, "_label"))
        .replace("__NOTE__", member(src, "_note"))
        .replace("__PERSONAL_ROW__", member(src, "_personalRow"))
        .replace("__ROW__", member(src, "_row"))
        .replace("__COMMAND__", member(src, "_command"))
        .replace("__MAYBE_CHECK__", member(src, "_maybeCheck"))
        .replace("__PICK__", member(src, "_pick"))
        .replace("__PAGES_OF__", member(src, "pagesOf"))
    )


def _run_js(script: str) -> None:
    run_js(_harness() + "\n" + script)


# ── Cosa si legge ───────────────────────────────────────────────────────────


def test_the_panel_opens_on_the_conversation_you_are_in() -> None:
    """La prima riga e' dove sei, e la spunta e' sua."""
    _run_js("""
      const panel = await open(ELENCO);
      const righe = readout(panel);
      assert.equal(righe[0], 'etichetta: Con chi parli');
      assert.equal(righe[1], 'io ✿ · Jenny · personale');
    """)


def test_the_personal_row_keeps_its_own_name() -> None:
    """Il nome arriva dal guscio e non si legge dal titolo.

    Lo leggeva di li', ed era giusto finche' nel titolo c'era sempre lo stesso
    nome. Da quando il titolo porta il quaderno aperto, quella fonte farebbe
    dire «piante» alla riga della conversazione personale.
    """
    _run_js("""
      const panel = await open(ELENCO);
      panel._personalName = () => 'Qualcunaltra';
      panel.render();
      assert.ok(readout(panel)[1].includes('Qualcunaltra'));
    """)


def test_notebooks_come_down_from_the_most_recent() -> None:
    _run_js("""
      const panel = await open(ELENCO);
      const nomi = readout(panel).filter((r) => r.startsWith('- ')).map((r) => r.split(' ')[1]);
      assert.deepEqual(nomi, ['memory', 'etf', 'piante']);
    """)


def test_no_notebooks_is_said_with_words() -> None:
    _run_js("""
      const panel = await open({ dir: 'wikis', projects: [], unopenable: [] });
      assert.ok(readout(panel).includes('nota: Non hai ancora nessun quaderno.'),
                readout(panel).join(' | '));
    """)


# ── I tre stati, che sono tre ───────────────────────────────────────────────


def test_a_list_never_read_says_nothing_about_being_empty() -> None:
    """Aperto prima che la lettura torni: «sto leggendo», non «non ce n'e'»."""
    _run_js("""
      const panel = new Panel();
      panel.render();
      const righe = readout(panel).join(' | ');
      assert.ok(righe.includes('Caricamento'), righe);
      assert.ok(!righe.includes('Non hai ancora nessun quaderno'), righe);
    """)


def test_a_failed_read_keeps_the_notebooks_and_says_so_on_top() -> None:
    """Il difetto da cui nasce tutto: un guasto non deve cancellare l'elenco.

    E la nota va **sopra** le righe, perche' quelle possono essere vecchie e
    questa e' l'unica cosa che lo dice.
    """
    _run_js("""
      const panel = await open(ELENCO);
      nextPayload = 'fail';
      await panel._list.load();
      panel.render();
      const righe = readout(panel);
      const guasto = righe.findIndex((r) => r.startsWith('guasto: '));
      const primoQuaderno = righe.findIndex((r) => r.startsWith('- '));
      assert.ok(guasto !== -1, 'il guasto non si dichiara: ' + righe.join(' | '));
      assert.ok(guasto < primoQuaderno, 'la nota del guasto sta sotto le righe che spiega');
      assert.equal(righe.filter((r) => r.startsWith('- ')).length, 3,
                   'un guasto ha cancellato i quaderni');
    """)


def test_a_failure_is_never_told_as_an_empty_list() -> None:
    """Prima lettura fallita: non si sa niente, e non si dice il contrario."""
    _run_js("""
      const panel = await open('fail');
      const righe = readout(panel).join(' | ');
      assert.ok(righe.includes('Non sono riuscita a leggere'), righe);
      assert.ok(!righe.includes('Non hai ancora nessun quaderno'), righe);
      assert.ok(!righe.includes('Caricamento'), righe);
    """)


# ── Le cartelle che non si aprono ───────────────────────────────────────────


def test_folders_that_do_not_open_are_shown_last_with_one_note_each_reason() -> None:
    """Si mostrano: sul telefono non c'e' un file manager, e questa e' proprio
    la chat da cui si chiede di rinominarle. Una spiegazione per motivo, non
    per riga."""
    _run_js("""
      const panel = await open({
        dir: 'wikis',
        projects: [{ name: 'piante', modified: 100 }],
        unopenable: [
          { name: 'Ricerca ETF', modified: 300, reason: 'invalid_name' },
          { name: 'università', modified: 200, reason: 'invalid_name' },
        ],
      });
      const righe = readout(panel);
      assert.deepEqual(righe.filter((r) => r.startsWith('x ')).map((r) => r.split(' · ')[0]),
                       ['x Ricerca ETF', 'x università']);
      assert.ok(righe.indexOf('etichetta: Non apribili') > righe.findIndex((r) => r.startsWith('- ')),
                'le non apribili vanno dopo i quaderni veri');
      assert.equal(righe.filter((r) => r.startsWith('nota: Queste cartelle')).length, 1,
                   'una nota per riga invece che una per motivo');
    """)


def test_an_unknown_reason_is_not_told_the_name_rule() -> None:
    """Raccontare la regola dei nomi per una cartella rifiutata per altro e'
    peggio che non spiegare niente."""
    _run_js("""
      const panel = await open({
        dir: 'wikis', projects: [],
        unopenable: [{ name: 'boh', modified: 1, reason: 'qualcosaltro' }],
      });
      const nota = readout(panel).find((r) => r.startsWith('nota: Queste cartelle'));
      assert.ok(nota, readout(panel).join(' | '));
      assert.ok(!nota.includes('64 caratteri'), nota);
    """)


# ── Cosa fa un tocco ────────────────────────────────────────────────────────


def test_a_notebook_row_is_a_command() -> None:
    """Per un giro non lo e' stato (D1), e il banco di allora garantiva
    l'inerzia: e' stato rovesciato di proposito, non cancellato."""
    _run_js("""
      const panel = await open(ELENCO);
      const aperte = righeDi(panel).filter((r) => !String(r.className).includes('is-blocked'));
      assert.equal(aperte.length, 4, 'la personale piu\\' i tre notebooks');
      for (const riga of aperte) {
        assert.equal(riga.tag, 'button', 'una riga ha smesso di essere un comando');
      }
    """)


def test_the_name_you_touch_is_the_name_that_comes_back() -> None:
    """Il pannello non sa cosa sia una chiave di sessione: dice il nome, e la
    conversazione la apre chi lo ospita."""
    _run_js("""
      const panel = await open(ELENCO);
      const etf = righeDi(panel).find((r) => r.children.some((c) => c.textContent === 'etf'));
      tocca(etf);
      assert.deepEqual(panel.storia, ['scelto:etf'],
                       'il pannello deve chiudersi prima di cambiare conversazione');
    """)


def test_the_personal_row_takes_you_home() -> None:
    """Senza, da dentro un quaderno il pannello saprebbe solo portarti altrove."""
    _run_js("""
      const panel = await open(ELENCO);
      panel._currentProject = () => 'etf';
      panel.render();
      const casa = righeDi(panel).find((r) => String(r.className).includes('is-personal'));
      tocca(casa);
      assert.deepEqual(panel.storia, ['scelto:null']);
    """)


def test_a_folder_that_does_not_open_is_not_a_command() -> None:
    """Il gateway rifiuta quella chiave (`_envelope_chat_id`): un bottone qui
    sarebbe una promessa gia' smentita dall'altra parte."""
    _run_js("""
      const panel = await open({
        dir: 'wikis', projects: [{ name: 'piante', modified: 1 }],
        unopenable: [{ name: 'Ricerca ETF', modified: 1, reason: 'invalid_name' }],
      });
      const bloccate = righeDi(panel).filter((r) => String(r.className).includes('is-blocked'));
      assert.equal(bloccate.length, 1);
      assert.notEqual(bloccate[0].tag, 'button');
      assert.deepEqual(bloccate[0].listeners, [], 'una cartella rotta risponde al tocco');
    """)


def test_only_the_row_you_are_on_carries_the_check() -> None:
    """Una sola, e su quella giusta: la spunta dice dove sei, non cos'e' la
    riga personale."""
    _run_js("""
      const panel = await open(ELENCO);
      const spunte = (p) => walk(p._body).filter((n) => String(n.className).includes('ti-check'));
      const marcata = (p) => righeDi(p).find((r) => String(r.className).includes('is-current'));

      assert.equal(spunte(panel).length, 1);
      assert.ok(String(marcata(panel).className).includes('is-personal'));

      panel._currentProject = () => 'etf';
      panel.render();
      assert.equal(spunte(panel).length, 1, 'due spunte: una delle due mente');
      const riga = marcata(panel);
      assert.ok(riga.children.some((c) => c.textContent === 'etf'));
      assert.equal(riga.attrs['aria-current'], 'true',
                   'la spunta e\\' decorativa: chi non la vede deve saperlo lo stesso');
    """)


def test_a_notebook_that_is_gone_leaves_no_check_behind() -> None:
    """Sei dentro un quaderno che l'elenco non porta piu' (rinominato, o
    cancellato da un'altra superficie): meglio nessuna spunta che una spunta
    sulla casa, dove non sei."""
    _run_js("""
      const panel = await open(ELENCO);
      panel._currentProject = () => 'sparito';
      panel.render();
      const spunte = walk(panel._body).filter((n) => String(n.className).includes('ti-check'));
      assert.equal(spunte.length, 0, 'la spunta e\\' finita su una conversation che non e\\' tua');
    """)


def test_the_dot_is_an_identity_and_nothing_else() -> None:
    """Stabile nel nome, e fuori dai token del tema: un pallino `--error` su un
    quaderno sano si leggerebbe come un allarme."""
    _run_js("""
      assert.equal(dotColor('piante'), dotColor('piante'));
      assert.notEqual(dotColor('piante'), dotColor('memory'));
      assert.match(dotColor('piante'), /^hsl\\(\\d+, 38%, 58%\\)$/);
      assert.equal(dotColor(''), dotColor(undefined));
    """)


def test_a_blocked_row_has_no_colour_at_all() -> None:
    """Non e' una scelta fra cui scegliere: e' una cosa da sistemare."""
    _run_js("""
      const panel = await open({
        dir: 'wikis', projects: [{ name: 'piante', modified: 1 }],
        unopenable: [{ name: 'Ricerca ETF', modified: 1, reason: 'invalid_name' }],
      });
      const pallini = walk(panel._body).filter((n) => String(n.className) === 'casa-who-dot');
      assert.equal(pallini.length, 2);
      assert.ok(pallini[0].style.background, 'il quaderno ha perso il suo colore');
      assert.equal(pallini[1].style.background, undefined,
                   'una cartella che non si apre non deve avere un colore suo');
    """)

# ── Il fiore, e il comando in fondo ─────────────────────────────────────────


def test_the_house_row_carries_jennys_flower() -> None:
    """Non è un colore assegnato a un nome: è il segno che Jenny ha già nel dock
    e nella riga d'identità dell'officina. Per questo la riga personale non ha
    un pallino — non è un quaderno fra i quaderni."""
    _run_js("""
      const panel = await open(ELENCO);
      const casa = righeDi(panel).find((r) => String(r.className).includes('is-personal'));
      const fiore = casa.children.find((c) => String(c.className).includes('casa-who-flower'));
      assert.ok(fiore, 'la riga della casa ha perso il fiore');
      assert.equal(fiore.textContent, '✿');
      assert.ok(!casa.children.some((c) => String(c.className) === 'casa-who-dot'),
                'la casa si è presa anche un pallino da quaderno');
    """)


def test_the_new_notebook_command_is_not_drawn_by_the_panel() -> None:
    """Dal 23/09/2026 e' il + tondo della pagina, fermo sopra l'elenco: una riga
    in fondo, con tanti quaderni, finiva sotto il bordo. Il pannello non la
    disegna piu' — ne' con l'elenco pieno, ne' vuoto, ne' rotto."""
    _run_js("""
      for (const dati of [ELENCO, { dir: 'wikis', projects: [], unopenable: [] }, 'fail']) {
        const panel = await open(dati);
        assert.equal(nuovoDi(panel), undefined, 'il pannello disegna ancora «Nuovo quaderno»');
      }
    """)


# ── Quante pagine ha un quaderno ────────────────────────────────────────────


def test_the_list_carries_the_page_count_it_is_given() -> None:
    """La pastiglia «N pagine» dell'intestazione legge di qui.

    Il conteggio lo manda `/api/projects` insieme al nome e alla data;
    `ConversationList` mappa la voce campo per campo, e un campo non nominato
    si perde in silenzio — la porta resterebbe senza numero per sempre, senza
    un errore da nessuna parte.
    """
    _run_js("""
      const list = new ConversationList(async () => ({
        dir: 'wikis',
        projects: [{ name: 'orto', modified: 20, pages: 34 }],
        unopenable: [],
      }));
      assert.equal(await list.load(), true);
      assert.equal(list.projects[0].pages, 34, 'il conteggio non arriva alla voce');
    """)


def test_a_gateway_that_does_not_send_the_count_says_it_does_not_know() -> None:
    """`null` e non zero: «non lo so» e «e' vuoto» sono due cose diverse, e
    solo la seconda si scrive a schermo."""
    _run_js("""
      const list = new ConversationList(async () => ({
        dir: 'wikis', projects: [{ name: 'orto', modified: 20 }], unopenable: [],
      }));
      await list.load();
      assert.equal(list.projects[0].pages, null);
    """)


def test_the_panel_answers_how_many_pages_and_reads_the_list_if_it_has_to() -> None:
    """La pastiglia compare entrando in un quaderno, che di solito e' prima
    che la tendina sia stata aperta anche una volta: se la cache e' vuota, la
    domanda la riempie invece di rispondere «non lo so» per sempre."""
    _run_js("""
      const panel = new Panel();
      let letture = 0;
      panel._list = new ConversationList(async () => {
        letture += 1;
        return { dir: 'wikis', projects: [{ name: 'orto', modified: 1, pages: 7 }],
                 unopenable: [] };
      });
      assert.equal(await panel.pagesOf('orto'), 7);
      assert.equal(letture, 1, 'la cache vuota non e’ stata riempita');
      // La seconda volta la cache c'e': non si rilegge.
      assert.equal(await panel.pagesOf('orto'), 7);
      assert.equal(letture, 1, 'riletto l’elenco per una risposta che sapeva gia’');
      // Un quaderno che l'elenco non conosce: «non lo so», non zero.
      assert.equal(await panel.pagesOf('sconosciuto'), null);
      assert.equal(await panel.pagesOf(''), null);
    """)


# ── La pressione lunga: la scheda del quaderno (23/09/2026) ─────────────────
#
# Una cosa si appende dal posto dove vive, e i quaderni vivono qui: tenerne
# premuto uno apre la sua scheda, come un'app nel cassetto.


def test_holding_a_notebook_asks_for_its_sheet() -> None:
    _run_js(
        "const panel = await open(ELENCO);\n"
        "const riga = righeDi(panel).find((r) => r.children.some((c) => c.textContent === 'etf'));\n"
        "tieni(riga);\n"
        "assert.deepEqual(panel.storia, ['tenuto:etf']);\n"
    )


def test_the_tap_after_a_hold_does_not_switch_conversation() -> None:
    """Senza, tenere premuto un quaderno aprirebbe la scheda **e** cambierebbe
    conversazione sotto. E il tocco dopo quello torna a essere un tocco."""
    _run_js(
        "const panel = await open(ELENCO);\n"
        "const riga = righeDi(panel).find((r) => r.children.some((c) => c.textContent === 'etf'));\n"
        "tieni(riga);\n"
        "tocca(riga);\n"
        "assert.deepEqual(panel.storia, ['tenuto:etf'], 'il tocco dopo la pressione ha cambiato conversazione');\n"
        "tocca(riga);\n"
        "assert.deepEqual(panel.storia.slice(1), ['scelto:etf']);\n"
    )


def test_the_personal_row_cannot_be_held() -> None:
    """Non e' un quaderno: e' gia' la pagina 0, e non si cancella."""
    _run_js(
        "const panel = await open(ELENCO);\n"
        "const personale = righeDi(panel).find((r) => String(r.className).includes('is-personal'));\n"
        "assert.ok(!premute.some((p) => p.el === personale), 'la riga personale si puo tenere premuta');\n"
    )


def test_a_folder_that_does_not_open_cannot_be_held() -> None:
    _run_js(
        "const panel = await open({ ...ELENCO, unopenable: [{ name: 'Foto Mare', reason: 'invalid-name' }] });\n"
        "const inerte = walk(panel._body).find((n) => String(n.className).includes('is-blocked'));\n"
        "assert.ok(inerte, 'la cartella inerte non c e');\n"
        "assert.ok(!premute.some((p) => p.el === inerte), 'una cartella inerte si puo tenere premuta');\n"
    )


def test_every_openable_notebook_can_be_held() -> None:
    _run_js(
        "const panel = await open(ELENCO);\n"
        "const notebooks = righeDi(panel).filter((r) => !String(r.className).includes('is-personal'));\n"
        "assert.equal(notebooks.length, 3);\n"
        "assert.ok(notebooks.every((q) => premute.some((p) => p.el === q)));\n"
    )


def test_holding_a_row_does_not_select_its_text() -> None:
    """**Trovato sul telefono il 23/09/2026.** La selezione di Chromium scatta a
    ~500 ms, prima dei 600 della pressione lunga, e il suo `pointercancel` la
    spegne: sopra il nome compariva «Copy, Share, Select all», e la scheda non
    si apriva. Nessun banco in node lo vede — il DOM finto non seleziona niente
    — quindi lo tiene questo, sul foglio di stile."""
    css = (ASSETS / "casa-style.css").read_text(encoding="utf-8")
    riga = css.split("\n.casa-who-row {", 1)[1].split("}", 1)[0]
    for regola in ("user-select: none;", "-webkit-user-select: none;", "-webkit-touch-callout: none;"):
        assert regola in riga, f"la riga della tendina ha perso `{regola}`"


def test_a_redraw_keeps_where_you_had_scrolled() -> None:
    """La pagina Quaderni scorre dentro il proprio contenitore, e il ridisegno
    lo svuota: come nel DOM vero, svuotato torna in cima. Arrivava anche
    mentre la guardavi — l'elenco riletto, un rinomino — e ti riportava su."""
    _run_js("""
      const panel = await open(ELENCO);
      const body = panel._body;
      /* Il DOM vero: svuotato, il contenitore non ha piu' altezza e lo
         scorrimento torna a zero. */
      let scorso = 0;
      body.children = new Proxy([...body.children], {
        set(t, k, v) {
          if (k === 'length' && v === 0) scorso = 0;
          t[k] = v;
          return true;
        },
      });
      Object.defineProperty(body, 'scrollTop', {
        get() { return scorso; },
        set(v) { scorso = body.children.length ? v : 0; },
      });
      body.scrollTop = 240;
      panel.render();
      assert.equal(body.scrollTop, 240, 'il ridisegno ha riportato l\\u2019elenco in cima');
    """)
