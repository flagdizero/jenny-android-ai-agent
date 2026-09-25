"""La tendina dei comandi mostra quel che il backend dichiara, e solo quello.

I comandi c'erano da sempre e non li vedeva nessuno: `BUILTIN_COMMAND_SPECS` dava
a ognuno titolo, descrizione e icona, ma `as_dict()` non aveva un consumatore in
tutto il repo — nessun pulsante, nessuna palette, nessun autocomplete sullo `/`.
L'unico modo di scoprirli era indovinare `/help`, e la issue #11 è arrivata da
lì: «non ho trovato un modo di azzerare la chat», con `/new` a un tocco di
distanza.

Il rischio del rimedio è di rifare il difetto al contrario — un secondo elenco
nel client, che diverge dal primo senza che nessuno se ne accorga. Questi test lo
tengono chiuso da due lati: l'elenco arriva dal server, e la prosa che manca
ripiega sull'inglese della tabella invece di stampare una chiave grezza.

**Come sono fatti.** I membri si estraggono dal sorgente e si eseguono in
**node**, come in `test_scope_chip_unopenable_client.py`: niente è riscritto a
mano, nemmeno la `t()` di `i18n.js`.
"""

from __future__ import annotations

import json
from pathlib import Path

from support.js_harness import locale, member, requires_node, run_js

from jenny.command.specs import BUILTIN_COMMAND_SPECS

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CHIP_JS = ASSETS / "shared" / "commands-chip.js"
I18N_JS = ASSETS / "shared" / "i18n.js"


pytestmark = requires_node


def _chip() -> str:
    return CHIP_JS.read_text(encoding="utf-8")


def _specs_json() -> str:
    """La tabella vera, servita come la serve `/api/webui/commands`."""
    return json.dumps([spec.as_dict() for spec in BUILTIN_COMMAND_SPECS], ensure_ascii=False)


_HARNESS = """
import assert from 'node:assert/strict';

const TRANSLATIONS = __TRANSLATIONS__;
const i18n = {
  locale: 'it',
  translations: TRANSLATIONS,
  __T__
};

/* Un elemento è quel poco che la tendina tocca. `listeners` registra i tipi
   montati: una riga che non ascolta niente non è una scelta. */
function makeEl(tag) {
  const el = {
    tag,
    className: '',
    textContent: '',
    children: [],
    dataset: {},
    attrs: {},
    listeners: [],
    handlers: {},
    appendChild(child) { el.children.push(child); return child; },
    setAttribute(k, v) { el.attrs[k] = v; },
    addEventListener(type, fn) { el.listeners.push(type); el.handlers[type] = fn; },
    querySelector() { return makeEl('span'); },
  };
  el.classList = {
    add(c) { el.className = (el.className + ' ' + c).trim(); },
    remove() {},
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return ''; },
    set(v) { if (v === '') el.children.length = 0; },
  });
  return el;
}
const document = { createElement: (tag) => makeEl(tag) };
const console = { warn() {} };

// La conversazione aperta e il client HTTP: sono di altri due moduli, e qui
// sono quel poco che `_load` ne legge. Lo scope **non** compare più: il filtro
// per conversazione lo fa il server (`ws_http._handle_webui_commands`), e la
// tendina disegna quel che riceve.
const sessionManager = { currentKey: 'websocket:default' };
const requests = [];
const api = {
  async getCommands(key) {
    requests.push(key);
    return { commands: SPECS };
  },
};

function texts(el) {
  const out = [];
  for (const child of el.children) {
    if (child.textContent) out.push(child.textContent);
    out.push(...texts(child));
  }
  return out;
}
function byClass(el, cls) {
  const out = [];
  for (const child of el.children) {
    if (String(child.className).split(/\\s+/).includes(cls)) out.push(child);
    out.push(...byClass(child, cls));
  }
  return out;
}

const SPECS = __SPECS__;

class Chip {
  constructor() {
    this.menu = makeEl('div');
    this._commands = SPECS;
    this._loadFailed = false;
    this.picked = [];
    this.onPick = (spec) => this.picked.push(spec.command);
  }
  close() {}
  __LOAD__
  __RENDER_MENU__
  __ITEM__
  __NOTE__
  __TEXT__
}
"""


_ALIGN_HARNESS = """
import assert from 'node:assert/strict';

/* Le tre misure che `_alignToChip` legge, e l'unica che scrive. */
function makeChip({ chipLeft, menuWidth, rowWidth }) {
  const chip = {
    menu: { offsetWidth: menuWidth, offsetParent: { clientWidth: rowWidth }, style: {} },
    el: { offsetLeft: chipLeft },
    __ALIGN__,
  };
  return chip;
}
const leftOf = (chip) => parseInt(chip.menu.style.left, 10);
"""


def _align_harness() -> str:
    return _ALIGN_HARNESS.replace("__ALIGN__", member(_chip(), "_alignToChip"))


def _run_align(script: str) -> None:
    source = _align_harness() + "\n" + script
    run_js(source)


# ── 5. Il pannello sta sopra il suo chip ─────────────────────────────────────


def test_the_panel_starts_where_the_chip_starts() -> None:
    """Bordo sinistro con bordo sinistro, come la tendina dello scope.

    In CSS non si può dire: il contenitore posizionato è `.compose-scope`, larga
    tutta la riga, quindi `left: 0` è il bordo della *riga*. Misurato sul
    telefono il 28/08 con `right: 0`: chip 236..364 CSS px, pannello 359..700 —
    accanto, non sopra.
    """
    _run_align("""
      const chip = makeChip({ chipLeft: 236, menuWidth: 340, rowWidth: 690 });
      chip._alignToChip();
      assert.equal(leftOf(chip), 236);
    """)


def test_it_stays_inside_the_row_when_the_chip_is_too_far_right() -> None:
    """Su uno schermo stretto i due desideri sono incompatibili: vince restare a schermo.

    Un pannello agganciato al chip e uscito dal bordo destro perde la fine delle
    descrizioni, cioè il motivo per cui la tendina esiste.
    """
    _run_align("""
      const chip = makeChip({ chipLeft: 236, menuWidth: 340, rowWidth: 350 });
      chip._alignToChip();
      assert.equal(leftOf(chip), 10, 'il pannello deve appoggiarsi al margine destro');
    """)


def test_it_never_goes_negative() -> None:
    """Pannello più largo della riga: al bordo sinistro, non fuori dallo schermo."""
    _run_align("""
      const chip = makeChip({ chipLeft: 40, menuWidth: 400, rowWidth: 300 });
      chip._alignToChip();
      assert.equal(leftOf(chip), 0);
    """)


def test_it_does_nothing_without_a_positioned_row() -> None:
    """`offsetParent` è null su un elemento non ancora in pagina: non si scrive nulla."""
    _run_align("""
      const chip = makeChip({ chipLeft: 236, menuWidth: 340, rowWidth: 690 });
      chip.menu.offsetParent = null;
      chip._alignToChip();
      assert.equal(chip.menu.style.left, undefined);
    """)


def _harness() -> str:
    src = _chip()
    return (
        _HARNESS.replace("__TRANSLATIONS__", json.dumps({"it": locale("it")}))
        .replace("__T__", member(I18N_JS.read_text(encoding="utf-8"), "t"))
        .replace("__SPECS__", _specs_json())
        .replace("__LOAD__", member(src, "_load"))
        .replace("__RENDER_MENU__", member(src, "_renderMenu"))
        .replace("__ITEM__", member(src, "_item"))
        .replace("__NOTE__", member(src, "_note"))
        .replace("__TEXT__", member(src, "_text"))
    )


def _run_js(script: str) -> None:
    source = _harness() + "\n" + script
    run_js(source)


# ── 1. L'elenco è quello del backend ─────────────────────────────────────────


def test_every_command_the_backend_serves_gets_a_row() -> None:
    """Nessun filtro silenzioso: quel che il server serve si vede, tutto.

    Dal 31/08/2026 vale alla lettera: il filtro per conversazione è del server,
    quindi qui non c'è più niente da nascondere — e un filtro in questo file
    sarebbe la seconda copia della regola che il modulo esiste per non tenere.
    """
    expected = [s.command for s in BUILTIN_COMMAND_SPECS]
    _run_js(f"""
      const chip = new Chip();
      chip._renderMenu();
      const rows = byClass(chip.menu, 'commands-menu-item');
      const names = texts(chip.menu);
      const expected = {json.dumps(expected)};
      assert.equal(rows.length, expected.length,
                   `righe: ${{rows.length}}, attese: ${{expected.length}}`);
      for (const command of expected) {{
        assert.ok(names.some((t) => t.startsWith(command)),
                  `manca la riga di ${{command}}`);
      }}
    """)


def test_the_argument_is_written_next_to_the_command() -> None:
    """`/model [preset]`, non `/model`: la riga dice che vuole qualcosa."""
    _run_js("""
      const chip = new Chip();
      chip._renderMenu();
      const names = texts(chip.menu);
      assert.ok(names.includes('/model [preset]'),
                'la riga non mostra il segnaposto dell-argomento');
      assert.ok(names.includes('/new'),
                'un comando senza argomenti non deve crescere uno spazio in coda');
    """)


def test_every_row_carries_the_icon_the_backend_declared() -> None:
    """Il nome Tabler viaggia dal server alla classe, senza rimappature qui."""
    _run_js("""
      const chip = new Chip();
      chip._renderMenu();
      const rows = byClass(chip.menu, 'commands-menu-item');
      for (const row of rows) {
        const icon = row.children[0];
        assert.ok(String(icon.className).startsWith('ti ti-'),
                  `riga senza icona: ${icon.className}`);
        assert.equal(String(icon.className).includes('undefined'), false);
      }
    """)


# ── 2. Lo scope, che è del server ───────────────────────────────────────────


def test_the_chip_draws_what_it_is_served_and_filters_nothing() -> None:
    """Il filtro per conversazione è del server, non di qui.

    Prima queste due righe vivevano nel `_renderMenu`, e c'era un motivo per
    toglierle: **non c'è autocomplete sullo `/`**, quindi un filtro nel client
    nasconde una voce a chi guarda il menu e non dice niente a chi digita. La
    regola vera è il cancello del dispatch (`jenny/command/scope.py`); questa era
    cosmetica, e una copia della regola in più da tenere allineata.
    """
    _run_js("""
      const chip = new Chip();
      chip._commands = [
        { command: '/tidy', title: 'Tidy', icon: 'wand', arg_hint: '', scope: 'project' },
        { command: '/dream', title: 'Dream', icon: 'sparkles', arg_hint: '', scope: 'personal' },
      ];
      chip._renderMenu();
      const names = texts(chip.menu);
      assert.ok(names.some((t) => t.startsWith('/tidy')), 'ha filtrato un project');
      assert.ok(names.some((t) => t.startsWith('/dream')), 'ha filtrato un personal');
    """)


def test_the_open_conversation_travels_with_the_request() -> None:
    """Senza la chiave il server non può filtrare, e servirebbe l'elenco intero."""
    _run_js("""
      const chip = new Chip();
      chip._commands = null;
      await chip._load();
      assert.deepEqual(requests, ['websocket:default']);
    """)


def test_changing_conversation_asks_again() -> None:
    """L'elenco in cache appartiene a una conversazione.

    Servire quello di prima dopo un cambio di scope vorrebbe dire offrire una
    voce che il dispatch poi rifiuta — cioè la cosa che la tendina esiste per non
    fare.
    """
    _run_js("""
      const chip = new Chip();
      chip._commands = null;
      await chip._load();
      await chip._load();
      assert.equal(requests.length, 1, 'la stessa conversazione non si richiede');

      sessionManager.currentKey = 'project:patreon';
      await chip._load();
      assert.deepEqual(requests, ['websocket:default', 'project:patreon']);
    """)


def test_the_description_comes_from_the_locale() -> None:
    it = locale("it")
    _run_js(f"""
      const chip = new Chip();
      chip._renderMenu();
      const written = texts(chip.menu);
      assert.ok(written.includes({json.dumps(it["commands"]["new"]["title"])}),
                'la riga di /new non porta la descrizione italiana');
    """)


def test_an_untranslated_command_falls_back_to_the_server_text() -> None:
    """Un comando nuovo nel backend deve comparire **con la sua descrizione**.

    Il ripiego non è cortesia: senza, a schermo resterebbe `commands./foo.title`,
    che è peggio dell'inglese e non lo nota nessuno finché non lo si vede sul
    telefono.
    """
    _run_js("""
      const chip = new Chip();
      chip._commands = [{
        command: '/foo', title: 'Do the foo thing', description: 'x',
        icon: 'command', arg_hint: '', scope: 'any',
      }];
      chip._renderMenu();
      const written = texts(chip.menu);
      assert.ok(written.includes('Do the foo thing'),
                'senza traduzione la riga non ripiega sul testo del server');
      assert.equal(written.some((t) => t.includes('commands./foo')), false,
                   'a schermo è finita una chiave i18n grezza');
    """)


# ── 4. Il guasto si dichiara ────────────────────────────────────────────────


def test_a_failed_load_says_so_instead_of_showing_an_empty_menu() -> None:
    """Una tendina vuota è indistinguibile da «non ci sono comandi», che è falso."""
    it = locale("it")
    _run_js(f"""
      const chip = new Chip();
      chip._commands = [];
      chip._loadFailed = true;
      chip._renderMenu();
      const written = texts(chip.menu);
      assert.ok(written.includes({json.dumps(it["commands"]["loadFailed"])}),
                'il guasto non è dichiarato');
      assert.equal(byClass(chip.menu, 'commands-menu-item').length, 0);
    """)


def test_an_empty_list_says_so_instead_of_opening_blank() -> None:
    """Lettura riuscita, zero comandi: un pannello vuoto si legge come rotto.

    Non è uno stato che si raggiunge finché la tabella del backend ha righe — ed
    è per questo che va tenuto fermo qui: chi un giorno la svuota, o la filtra,
    non passa da `commands-chip.js`, e il difetto che vedrebbe è «la tendina non
    si apre».
    """
    it = locale("it")
    _run_js(f"""
      const chip = new Chip();
      chip._commands = [];
      chip._renderMenu();
      const written = texts(chip.menu);
      assert.ok(written.includes({json.dumps(it["commands"]["empty"])}),
                'la tendina si apre senza dire niente');
      assert.equal(byClass(chip.menu, 'commands-menu-item').length, 0);
    """)


def test_picking_a_row_hands_the_spec_to_its_owner() -> None:
    """La tendina non manda niente: consegna, e chi possiede la chat decide."""
    _run_js("""
      const chip = new Chip();
      chip._renderMenu();
      const rows = byClass(chip.menu, 'commands-menu-item');
      assert.ok(rows[0].handlers.click, 'la riga non ascolta il tocco');
      rows[0].handlers.click();
      assert.deepEqual(chip.picked, ['/new'],
                       'la prima voce deve restare /new, ed essere consegnata così com-è');
    """)
