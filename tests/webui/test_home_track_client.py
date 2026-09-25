"""La pista della casa: una fila di pagine, la chat fra le altre.

La casa e' un launcher (tavola `Pagine`). Quattro pagine ci sono sempre — App,
la chat, Quaderni, Impostazioni — e accanto quelle che l'utente aggiunge. Dal
23/09/2026 **si spostano tutte**, la chat compresa: nessun indice vuol dire «la
chat» da solo (v. `.agent/pagine-in-alto-plan.md`). Per questo i casi qui
parlano **per nome di pagina** (`I('p1')`, `CHAT()`) e non per numero: un
banco che scrivesse `index === 1` proverebbe un ordine, non la pista.

**Perche' in node sui file veri.** Il modulo si importa davvero — con il suo
`shared/horizontal-swipe.js` accanto e un finto client API — invece di
ritagliarne il testo: cosi' il banco vede anche gli import, che sono
esattamente la cosa che un refactor rompe in silenzio. Il DOM e' finto, quindi
quel che si prova e' **dove si va**, non come si vede.

Quel che questo banco **non** prova: che il dito ci arrivi. Gli eventi sono
sintetici e scavalcano il hit-testing — v. `driving-touch-gestures-over-adb` in
memoria.
"""

from __future__ import annotations

import json
import re
import shutil
import textwrap
from pathlib import Path

from support.js_harness import member, requires_node, run_js, run_module

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "jenny" / "templates" / "ui"
ASSETS = UI / "assets"

pytestmark = requires_node


_FAKE_DOM = """
const elements = new Map();
function createEl(id, cls) {
  const listeners = {};
  const el = {
    id, listeners,
    className: cls || '',
    style: {},
    dataset: {},
    clientWidth: 400,
    children: [],
    _text: '',
    /* `textContent = ''` svuota i figli, come nel DOM vero: senza, i pallini
       si accumulerebbero a ogni ridisegno e il banco non lo vedrebbe. */
    set textContent(v) { this._text = v; if (v === '') this.children = []; },
    get textContent() { return this._text; },
    setAttribute(k, v) { this.attrs = this.attrs || {}; this.attrs[k] = v; },
    getAttribute(k) { return (this.attrs || {})[k]; },
    /* **Piu' di un ascoltatore per tipo.** Sulla striscia ce ne sono due —
       il «tira su» di questo file e la pressione lunga del modulo condiviso —
       e un finto che ne tiene uno solo li fa sovrascrivere a vicenda: il
       banco vedrebbe verde proprio sul caso in cui i due si pestano i piedi.
       Costato una stesura (22/09/2026). */
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    removeEventListener() {},
    /* **Sposta**, come il DOM vero: un nodo sta in un posto solo. Senza,
       la chat traslocata in una pagina resterebbe anche nel suo pannello, e
       la prova «la chat torna a casa» sarebbe vera comunque — cioe' verde su
       codice che la distrugge. Costato una stesura (22/09/2026). */
    appendChild(c) {
      if (c.parent) c.parent.children = c.parent.children.filter((x) => x !== c);
      this.children.push(c);
      c.parent = this;
      return c;
    },
    append(...cs) { for (const c of cs) this.appendChild(c); },
    /* `<dialog>`: `showModal` e `close` sono quel che serve, piu' `open`. */
    open: false,
    showModal() { this.open = true; },
    close() { this.open = false; },
    click() { for (const fn of this.listeners.click || []) fn(); },
    remove() {
      if (!this.parent) return;
      this.parent.children = this.parent.children.filter((x) => x !== this);
    },
    /* Le classi come le tiene il DOM vero: un elenco di parole. */
    get classList() {
      const el = this;
      const words = () => (el.className || '').split(' ').filter(Boolean);
      return {
        add(c) { if (!words().includes(c)) el.className = [...words(), c].join(' '); },
        remove(c) { el.className = words().filter((x) => x !== c).join(' '); },
        contains(c) { return words().includes(c); },
      };
    },
    /* Solo `.cls`, nei discendenti. Un altro selettore **alza**: rispondere
       a caso e' il modo in cui un finto dice verde su codice rotto. */
    querySelector(sel) {
      if (!/^\\.[a-z-]+$/.test(sel)) throw new Error('selettore che il finto non capisce: ' + sel);
      const cls = sel.slice(1);
      const find = (n) => {
        for (const c of n.children) {
          if ((c.className || '').split(' ').includes(cls)) return c;
          const r = find(c);
          if (r) return r;
        }
        return null;
      };
      return find(this);
    },
    /* Anche questo **sposta**: la pista rimette in fila i pannelli fissi, e
       un finto che li copiasse li lascerebbe in due posti. */
    insertBefore(c, ref) {
      if (c.parent) c.parent.children = c.parent.children.filter((x) => x !== c);
      const i = ref ? this.children.indexOf(ref) : -1;
      if (i < 0) this.children.push(c);
      else this.children.splice(i, 0, c);
      c.parent = this;
      return c;
    },
    querySelectorAll(sel) {
      /* Il selettore si rispetta davvero, non si approssima: un finto che
         risponde «tutti» comunque gli si chieda fa passare una mutazione che
         cancella il pannello della chat. Costato una prima stesura di questo
         banco, verde su codice rotto (22/09/2026). */
      const perId = sel.includes('[data-id]');
      return this.children.filter((c) => (perId ? !!c.dataset.id : true));
    },
  };
  if (id) elements.set(id, el);
  return el;
}
const track = createEl('home-track', 'home-track');
/* I pannelli delle quattro fisse ci sono gia' nell'HTML, in quest'ordine, e
   non hanno `data-id`: il controller li sposta e basta, non li crea e non li
   butta. */
function fixed(name) {
  const p = createEl(null, 'home-page');
  p.dataset.page = name;
  track.appendChild(p);
  return p;
}
const appPanel = fixed('app');
const chatPanel = fixed('chat');
const notebooksPanel = fixed('notebooks');
const settingsPanel = fixed('settings');

/* **Un dito porta due righelli**, come quello vero: `client` e' relativo alla
   finestra di chi ascolta, `screen` allo schermo. Qui sono sfalsati di una
   costante apposta — se qualcuno tornasse a misurare col primo, o peggio
   mescolasse i due, lo scarto salterebbe fuori invece di nascondersi. Il
   modulo condiviso legge **screen**, perche' dentro una Jenny App la finestra
   e' la cornice che la pista sta trascinando (v. la sua testata). */
const OFFSET_X = 1000;
const OFFSET_Y = 500;
function finger(x, y) {
  return { clientX: x, clientY: y, screenX: x + OFFSET_X, screenY: y + OFFSET_Y };
}

globalThis.document = {
  getElementById: (id) => elements.get(id) || null,
  createElement: (t) => createEl(null, ''),
  documentElement: createEl('html', ''),
  body: createEl('body'),
};
/* La finestra ascolta davvero: da qui passano i gesti raccontati da dentro
   una Jenny App, che la pista non la tocca mai. */
globalThis.window = {
  innerWidth: 400,
  listeners: {},
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
};
globalThis.getComputedStyle = () => ({ overflowX: 'visible' });

/* Un gesto completo sulla pista. `direction` +1 = dito a destra (pagina
   precedente), -1 = dito a sinistra (pagina successiva). */
function fire(el, type, event) {
  for (const fn of el.listeners[type] || []) fn(event);
}

function scroll(direction, { short = false } = {}) {
  const x0 = 200;
  // soglia = max(60, 400*0.22) = 88: corto resta sotto, lungo la supera
  const dx = direction * (short ? 20 : 200);
  fire(track, 'touchstart', { touches: [finger(x0, 100)], target: track });
  fire(track, 'touchmove', {
    touches: [finger(x0 + dx, 100)],
    preventDefault() {},
  });
  fire(track, 'touchend', { changedTouches: [finger(x0 + dx, 100)] });
}
/* Il gesto **raccontato da dentro una app**: quel che il kit
   (`apps/jenny-sdk.js`) manda al guscio dalla sua feritoia. Qui non c'e'
   nessun dito, ed e' tutto il punto: la pagina di una app e' tutta l'app, e
   il dito che la tocca al guscio non ci arriva mai. */
function fromApp(detail, { source } = {}) {
  const e = { data: { type: 'jenny:swipe', slug: 'orto', ...detail }, source: source };
  for (const fn of window.listeners.message || []) fn(e);
}
/* La sorgente buona: la finestra della cornice che si sta guardando. */
function liveWindow() {
  const panel = homePages.panelOf(homePages.index);
  return panel?.dataset?.id && panel.children[0] && panel.children[0].contentWindow;
}
function scrollFromApp(direction, { short = false, source } = {}) {
  const dx = direction * (short ? 20 : 200);
  const fromIndex = { source: source === undefined ? liveWindow() : source };
  fromApp({ phase: 'start' }, fromIndex);
  fromApp({ phase: 'move', dx }, fromIndex);
  fromApp({
    phase: 'end',
    direction: dx > 0 ? 'prev' : 'next',
    // soglia = max(60, 400*0.22) = 88, come la calcola il modulo condiviso
    confirm: Math.abs(dx) > 88,
  }, fromIndex);
}
/* Un dito i cui due righelli **non vanno d'accordo**. E' quel che succede
   davvero dentro una Jenny App: la cornice si sposta insieme al dito, quindi
   `client` racconta meno strada di quella fatta — o nessuna. */
function offsetFinger(xc, xs, y) {
  return { clientX: xc, clientY: y, screenX: xs, screenY: y + OFFSET_Y };
}
function scrollOffset(dxClient, dxScreen) {
  const x0 = 200;
  const a = offsetFinger(x0 + dxClient, x0 + dxScreen, 100);
  fire(track, 'touchstart', { touches: [offsetFinger(x0, x0, 100)], target: track });
  fire(track, 'touchmove', { touches: [a], preventDefault() {} });
  fire(track, 'touchend', { changedTouches: [a] });
}
const RIGHT = +1;
const LEFT = -1;
"""


def _script(body: str, pages: list[dict], view: str = "chat") -> str:
    """Un modulo che monta il DOM finto, poi importa i file veri."""
    return (
        _FAKE_DOM
        + f"\nconst SCHERMATE = {json.dumps(pages)};\n"
        + f"const VIEW = {json.dumps(view)};\n"
        + textwrap.dedent(
            """
            const { HomePages } = await import('./home-pages.js');
            const shell = createEl('home-shell', 'home-shell');
            const app = {
              view: VIEW,
              shell: shell,
              /* La modalita' ordina: finche' e' aperta il dito e' suo. */
              strip: { sorting: false },
              /* La fonte risponde **dopo un giro**, come la rete vera: al
                 momento della domanda `jennyApps` e' ancora vuota. Un finto
                 che risponde subito avrebbe lasciato passare il difetto visto
                 sul telefono il 22 settembre 2026 — «non hai Jenny App» a chi
                 ne aveva quattro. */
              appsSource: () => ({
                ensureLoaded() {},
                jennyApps: [],
                jennyListFailed() { return globalThis.BROKEN_LIST === true; },
                /* Chi si iscrive ai cambi dei dati delle app: il banco li
                   chiama a mano, come farebbe un frame del gateway. */
                onAppDataChanged(fn) {
                  (globalThis.APP_DATA ||= []).push(fn);
                  return () => {};
                },
                awaitJennyApps() {
                  return new Promise((r) => setTimeout(() => {
                    this.jennyApps = [
                      { slug: 'orto', name: 'Orto' },
                      { slug: 'lampo', name: 'Lampo' },
                      { slug: 'rotta', name: 'Rotta', broken: true },
                      { slug: 'fuori', name: 'Fuori', view_kind: 'external' },
                    ];
                    r(this.jennyApps);
                  }, 30));
                },
              }),
            };
            /* Il trasloco finto: registra, e dice se il pannello era ancora
               attaccato quando gli si chiedeva di riprendere la chat — e'
               l'unica domanda che conta per `bringBackHome`. Il trasloco vero
               ha il suo banco (`test_home_move_client.py`). */
            const moves = [];
            app.chatMove = {
              read: Promise.resolve('letto'),
              arrives(p, k) { moves.push({ fa: 'arriva', p, k }); },
              snapshotIfNeeded(p, k) { moves.push({ fa: 'foto', p, k }); },
              bringBackHome(v, c) {
                moves.push({ fa: 'home', v, c, attached: track.children.includes(v) });
              },
            };
            const arrivals = () => moves.filter((x) => x.fa === 'arriva');
            const PERSONAL = 'websocket:default';
            app.currentKey = () => PERSONAL;
            const shown = [];
            app.showConversation = (k) => { shown.push(k); return Promise.resolve('mostrata'); };
            const homePages = new HomePages(app);
            await homePages.load();
            /* Dove sta una pagina, per nome: i casi non contano caselle. */
            const I = (id) => homePages.indexOf(id);
            const CHAT = () => homePages.chatIndex;
            """
        )
        + body
    )


def _run(
    body: str,
    pages: list[dict] | None = None,
    view: str = "chat",
    order: list[str] | None = None,
) -> None:
    """*ordine*: quello salvato sul server; `None` e' chi non ha mai spostato
    niente, e il client lo normalizza come lo schema."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "shared").mkdir()
        shutil.copy(ASSETS / "home-pages.js", root / "home-pages.js")
        # Le regole sui nomi dei quaderni, vere: sono le stesse del gateway, e
        # una copia finta qui direbbe si' a un nome che il server rifiuta.
        shutil.copy(
            ASSETS / "shared" / "conversation-list.js",
            root / "shared" / "conversation-list.js",
        )
        shutil.copy(
            ASSETS / "shared" / "horizontal-swipe.js",
            root / "shared" / "horizontal-swipe.js",
        )
        # Il client API finto: risponde quel che il caso vuole, e ricorda le
        # scritture. Non si tocca la rete e non si tocca `config.json`.
        # La cornice di una Jenny App: qui basta sapere **che** viene
        # costruita e con quale slug — il vero `frameForApp` mette il token
        # e i colori nell'indirizzo, e quello si prova dove vive.
        (root / "shared" / "apps-actions.js").write_text(
            "import { api } from './api-client.js';\n"
            "export function frameForApp(slug) {\n"
            "  const f = document.createElement('iframe');\n"
            "  f.dataset.slug = slug;\n"
            # Il segreto **nel momento in cui** la cornice nasce: quello vero
            # finisce nel suo indirizzo, e dopo non cambia piu'.
            "  f.dataset.secret = api.getSecret();\n"
            # La feritoia: il guscio riconosce chi parla confrontando
            # **questa**, e senza il banco non vedrebbe la guardia.
            # Ricorda anche cosa le si manda: `jenny:data-changed`.
            "  f.contentWindow = { app: slug, mailbox: [], postMessage(m) { this.mailbox.push(m); } };\n"
            "  return f;\n"
            "}\n",
            encoding="utf-8",
        )
        # Gli avvisi: si ricordano, per dire se un guasto e' stato detto.
        (root / "shared" / "utils.js").write_text(
            "export function showToast(text, type) {\n"
            "  (globalThis.NOTICES ||= []).push([text, type]);\n"
            "}\n",
            encoding="utf-8",
        )
        (root / "shared" / "i18n.js").write_text(
            "export const i18n = {\n"
            "  t: (k, v) => k + (v ? ':' + JSON.stringify(v) : ''),\n"

            "};\n",
            encoding="utf-8",
        )
        (root / "shared" / "api-client.js").write_text(
            "export const api = {\n"
            f"  _list: {json.dumps(pages or [])},\n"
            f"  _order: {json.dumps(order)},\n"
            # Le scritture si ricordano in due elenchi: cosa (le schermate) e
            # dove (l'ordine). Il server vero le prende insieme.
            "  writes: [],\n"
            "  ordini: [],\n"
            "  async getPages() {\n"
            "    return { pages: this._list, order: this._order, max: 8,\n"
            "             fixed: ['app', 'chat', 'notebooks', 'settings'] };\n"
            "  },\n"
            # `_refuses`: la prossima scrittura fallisce, come un gateway
            # che risponde 500 o un filo caduto.
            "  _refuses: false,\n"
            "  async savePages(s, o) {\n"
            "    if (this._refuses) throw new Error('Pages write failed: 500');\n"
            "    this.writes.push(s); this.ordini.push(o);\n"
            "    this._list = s; this._order = o;\n"
            "    return { pages: s, order: o };\n"
            "  },\n"
            # Il segreto parte **vuoto**, come al primo avvio: la cornice di
            # un'app se lo porta nell'indirizzo, e chi la costruisce senza
            # aspettarlo produce un 401 e una pagina bianca.
            "  _secret: '',\n"
            "  getSecret() { return this._secret; },\n"
            "  async bootstrap() { this._secret = 'ok'; },\n"
            # Un caso puo' imporre i suoi quaderni — un elenco di nomi, o
            # 'rotto' per una lettura che fallisce — e allora vale quello:
            # serve alla pagina «non c'e' piu'». Altrimenti l'elenco di sotto.
            "  _notebooks: null,\n"
            # I quaderni su disco, come li da' `/api/projects`: `projects` si
            # aprono, `unopenable` no. Rispondono **dopo un giro**, come la rete.
            "  async listProjects() {\n"
            "    if (this._notebooks === 'rotto') throw new Error('500');\n"
            "    if (this._notebooks) return { projects: this._notebooks.map((name) => ({ name })) };\n"
            "    await new Promise((r) => setTimeout(r, 15));\n"
            # `zucca` e' piu' recente di `orto` ma viene dopo in ordine
            # alfabetico: senza, i due ordini coincidono e il banco non li
            # distingue. `a..b` e' un nome che il gateway non apre finito fra i
            # `projects` — un server piu' vecchio, o un difetto dall'altra
            # parte: offrirlo vorrebbe dire un salvataggio rifiutato.
            "    return { projects: [\n"
            "      { name: 'vecchio', modified: 100 },\n"
            "      { name: 'piante', modified: 900 },\n"
            "      { name: 'orto', modified: 500 },\n"
            "      { name: 'zucca', modified: 800 },\n"
            "      { name: 'a..b', modified: 950 },\n"
            "    ], unopenable: [{ name: '.nascosto', reason: 'invalid_name' }] };\n"
            "  },\n"
            "};\n",
            encoding="utf-8",
        )
        entry = root / "prova.mjs"
        entry.write_text(
            "import assert from 'node:assert/strict';\n"
            + _script(body, pages or [], view),
            encoding="utf-8",
        )
        run_module(entry)


ONE = [{"id": "p1", "kind": "app", "ref": "orto"}]
DUE = ONE + [{"id": "p2", "kind": "conversation", "ref": "project:piante"}]
# Due pagine che si **riempiono** entrambe: un quaderno non si riempie mai — ci
# arriva la chat — quindi chi prova «resta viva solo quella che guardi» ne
# vuole due di app.
DUE_APP = ONE + [{"id": "p2", "kind": "app", "ref": "lampo"}]


# ── Le quattro fisse, e la chat fra loro ────────────────────────────────────


def test_a_fresh_home_is_the_four_fixed_pages_opened_on_jenny() -> None:
    """App · Jenny · Quaderni · Impostazioni, e si parte da Jenny."""
    _run(
        "assert.equal(homePages.howMany, 4);\n"
        "assert.deepEqual(homePages.order, ['app', 'chat', 'notebooks', 'settings']);\n"
        "assert.equal(homePages.index, CHAT());\n"
        "assert.equal(CHAT(), 1);"
    )


def test_the_home_starts_on_the_chat_even_before_the_server_answers() -> None:
    """Nell'HTML la chat e' la seconda: senza un `transform` scritto subito il
    primo fotogramma sarebbe il cassetto."""
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api.getPages = () => new Promise(() => {});\n"
        "track.style.transform = undefined;\n"
        "const other = new HomePages(app);\n"
        "assert.equal(track.style.transform, 'translateX(-100%)');\n"
        "assert.equal(other.index, other.chatIndex);"
    )


def test_with_no_pages_added_there_is_still_somewhere_to_go() -> None:
    """Le fisse sono pagine vere: il dito ci va anche in una casa appena
    installata, e ai due capi si ferma."""
    _run(
        "scroll(LEFT);\n"
        "assert.equal(homePages.index, I('notebooks'));\n"
        "scroll(RIGHT); scroll(RIGHT);\n"
        "assert.equal(homePages.index, I('app'));"
    )


def test_the_fixed_panels_are_moved_never_redrawn() -> None:
    """Ridisegnarli vorrebbe dire buttare via la conversazione a ogni
    salvataggio — e il cassetto, i quaderni, le impostazioni con lei.

    Stanno nell'HTML; i pannelli aggiunti si mettono **dove dice l'ordine**, e
    l'ordine nel documento e' l'ordine a schermo.
    """
    _run(
        "const fissi = [appPanel, chatPanel, notebooksPanel, settingsPanel];\n"
        "await homePages.save(SCHERMATE.concat([{id:'p3',kind:'app',ref:'x'}]),\n"
        "  ['p3', 'settings', 'app', 'chat', 'p1', 'p2', 'notebooks']);\n"
        "for (const f of fissi) assert.ok(track.children.includes(f), 'un pannello fisso e stato buttato');\n"
        "assert.equal(track.children.length, 7);\n"
        "assert.deepEqual(track.children.map((c) => c.dataset.page || c.dataset.id), homePages.order);\n"
        "assert.equal(track.children[3], chatPanel);",
        pages=DUE,
    )


def test_the_chat_can_sit_anywhere_and_the_home_still_opens_on_it() -> None:
    """Si sposta come le altre; la casa si apre comunque su di lei."""
    _run(
        "assert.deepEqual(homePages.order, ['p1', 'notebooks', 'app', 'settings', 'chat']);\n"
        "assert.equal(homePages.index, 4);\n"
        "assert.equal(track.style.transform, 'translateX(-400%)');\n"
        "assert.equal(track.children[4], chatPanel);",
        pages=ONE,
        order=["p1", "notebooks", "app", "settings", "chat"],
    )


def test_a_page_missing_from_the_saved_order_goes_right_after_the_chat() -> None:
    """La stessa regola dello schema: mai un ordine che perde una pagina."""
    _run(
        "assert.deepEqual(homePages.order, ['notebooks', 'chat', 'p1', 'app', 'settings']);",
        pages=ONE,
        order=["notebooks", "chat", "app", "settings"],
    )


def test_the_client_order_rule_is_the_schema_one() -> None:
    """Due copie della stessa regola, una per lato: la seconda serve quando il
    server non ha detto l'ordine. Qui si provano gli stessi casi dello schema."""
    _run(
        "const { normalizeOrder } = await import('./home-pages.js');\n"
        "const s = [{ id: 'p1' }, { id: 'p2' }];\n"
        "assert.deepEqual(normalizeOrder(null, s), ['app', 'chat', 'p1', 'p2', 'notebooks', 'settings']);\n"
        "assert.deepEqual(normalizeOrder(['chat', 'x', 'p1', 'chat', 7], s),\n"
        "  ['chat', 'p2', 'p1', 'app', 'notebooks', 'settings']);\n"
        "assert.deepEqual(normalizeOrder(['settings'], []), ['settings', 'app', 'chat', 'notebooks']);"
    )


# ── Dove si va ──────────────────────────────────────────────────────────────


def test_swiping_left_moves_to_the_next_page() -> None:
    _run("scroll(LEFT); assert.equal(homePages.index, I('p1'));", pages=DUE)


def test_swiping_right_comes_back() -> None:
    _run(
        "scroll(LEFT); scroll(LEFT);\n"
        "assert.equal(homePages.index, I('p2'));\n"
        "scroll(RIGHT);\n"
        "assert.equal(homePages.index, I('p1'));",
        pages=DUE,
    )


def test_the_ends_do_not_wrap_around() -> None:
    """Le linguette dell'officina si richiudono in cerchio, le pagine no.

    Li' le voci sono quattro e note; qui quante siano lo decide l'utente, e
    girando in tondo fra otto pagine non si sa piu' dove si e'.
    """
    _run("scroll(RIGHT); scroll(RIGHT); assert.equal(homePages.index, 0);", pages=DUE)
    _run(
        "for (let i = 0; i < 9; i += 1) scroll(LEFT);\n"
        "assert.equal(homePages.index, homePages.howMany - 1);",
        pages=DUE,
    )


def test_a_short_drag_springs_back() -> None:
    _run("scroll(LEFT, {short: true}); assert.equal(homePages.index, CHAT());", pages=DUE)


# ── Le guardie ──────────────────────────────────────────────────────────────


def test_inside_a_room_the_rooms_are_in_charge() -> None:
    """Dalle pagine di un quaderno il dito non deve cambiare pagina sotto la stanza."""
    _run("scroll(LEFT); assert.equal(homePages.index, CHAT());", pages=DUE, view="pages")


def test_while_the_pages_are_being_moved_the_finger_is_theirs() -> None:
    """In modalita' ordina il dito trascina pastiglie, non la pista."""
    _run(
        "app.strip.sorting = true;\n"
        "scroll(LEFT);\n"
        "assert.equal(homePages.index, CHAT());",
        pages=DUE,
    )


# ── Il salvataggio ──────────────────────────────────────────────────────────


def test_saving_sends_the_whole_list(tmp_path: Path) -> None:
    """Aggiungere, togliere e spostare sono la stessa scrittura."""
    _run(
        "await homePages.save([SCHERMATE[1]], ['p2', 'app', 'chat', 'notebooks', 'settings']);\n"
        "const api = (await import('./shared/api-client.js')).api;\n"
        "assert.equal(api.writes.length, 1);\n"
        "assert.deepEqual(api.writes[0], [SCHERMATE[1]]);\n"
        "assert.deepEqual(api.ordini[0], ['p2', 'app', 'chat', 'notebooks', 'settings']);\n"
        "assert.deepEqual(homePages.order, api.ordini[0]);\n"
        "assert.equal(homePages.howMany, 5);",
        pages=DUE,
    )


def test_moving_a_page_keeps_you_on_the_page_you_were_on() -> None:
    """Spostare le pagine non ti sposta: resti dove guardavi, ovunque sia finita."""
    _run(
        "homePages.goToId('p2');\n"
        "await homePages.save(homePages.pages, ['p2', 'chat', 'p1', 'app', 'notebooks', 'settings']);\n"
        "assert.equal(homePages.index, 0);\n"
        "assert.equal(homePages.entry(homePages.index).id, 'p2');",
        pages=DUE,
    )


def test_removing_the_page_you_are_on_takes_you_to_the_chat() -> None:
    """Non una casella che non c'e' piu', e nemmeno una vicina a caso: la chat,
    che e' dove Indietro ti porterebbe comunque."""
    _run(
        "scroll(LEFT); scroll(LEFT);\n"
        "assert.equal(homePages.index, I('p2'));\n"
        "await homePages.save([SCHERMATE[0]], homePages.order.filter((x) => x !== 'p2'));\n"
        "assert.equal(homePages.howMany, 5);\n"
        "assert.equal(homePages.index, CHAT());",
        pages=DUE,
    )


def test_a_read_that_fails_leaves_the_chat_standing() -> None:
    """Una casa che non apre perche' non ha saputo leggere le sue pagine e'
    peggio di una casa con le sole quattro."""
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api.getPages = async () => { throw new Error('rete giu'); };\n"
        "const other = new HomePages(app);\n"
        "await other.load();\n"
        "assert.equal(other.howMany, 4);\n"
        "assert.equal(other.index, other.chatIndex);",
        pages=DUE,
    )


# ── La forma nel documento ──────────────────────────────────────────────────


def test_the_chat_lives_inside_a_page_panel() -> None:
    html = (UI / "index.html").read_text(encoding="utf-8")
    track = html.split('class="home-track"', 1)[1].split("</div>\n\n  <!--", 1)[0]
    for piece in ("home-thread", "home-empty", "home-activity", "home-composer"):
        assert piece in track, f"{piece} e' rimasto fuori dalla pista"


def test_one_rule_hides_the_track_not_six_pieces() -> None:
    """Sei righe diventate una.

    Se ne fosse dimenticata una, quel pezzo resterebbe a occupare spazio dentro
    una stanza — ed e' il tipo di difetto che si vede solo entrando in quella
    stanza precisa.
    """
    css = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    assert ".home-shell:not([data-view='chat']) .home-showcase { display: none; }" in css
    for piece in ("home-thread", "home-composer", "home-activity"):
        assert f":not([data-view='chat']) .{piece}" not in css


def test_the_panel_is_positioned_so_the_empty_state_stays_put() -> None:
    """`.home-empty` e' `position:absolute; inset:0`.

    Appena la pista prende un `transform` il contenitore di riferimento cambia
    — un elemento trasformato ne crea uno — e senza un `relative` dichiarato
    qui lo stato vuoto salterebbe a meta' gesto.
    """
    css = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    rule = css.split(".home-page {", 1)[1].split("}", 1)[0]
    assert "position: relative" in rule


def test_the_track_adds_no_z_index() -> None:
    """Il foglio della casa non dichiara livelli — nemmeno quello di Jenny, che
    sta in `.jenny-duo` di mobile-style.css (D3): lei sta sopra la chat e sopra
    un'app, e i pannelli si sovrappongono con l'ordine del DOM."""
    css = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    assert not [r for r in css.splitlines() if r.strip().startswith("z-index:")]


# ── I pallini se ne sono andati (23/09/2026) ────────────────────────────────


def test_the_dots_are_gone_and_the_row_took_their_place() -> None:
    """I pallini dicevano quante pagine c'erano, non che cosa: li ha sostituiti
    la fila dei nomi in alto (`home-strip.js`), che ha il suo banco. Qui si
    prova che non ne resta un pezzo — una striscia vuota da 26 px e' spazio
    tolto a ogni pagina per niente."""
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert "home-dots" not in html
    assert 'id="home-strip"' in html
    for name in ("home-pages.js", "home-app.js", "home-style.css"):
        assert "casa-pallin" not in (ASSETS / name).read_text(encoding="utf-8"), name


def test_the_strip_does_not_try_to_open_the_drawer() -> None:
    """**La tavola voleva «tira su», e sul telefono non si puo'.**

    Misurato il 22 settembre 2026 sul Titan 2, con la navigazione a gesti
    accesa (`navigation_mode = 2`): uno swipe verso l'alto dal bordo basso lo
    prende Android per il gesto di home, e all'app arriva `touchcancel` — mai
    `touchend`. Provato con una build diagnostica che apriva il foglio proprio
    su `touchcancel`: il foglio si apriva, cioe' il gesto arrivava annullato.

    La zona del gesto di home **non e' escludibile**:
    `setSystemGestureExclusionRects` vale per il gesto indietro, sui bordi
    laterali, non per quello. Quindi il cassetto e' tornato al suo bottone, e
    la striscia fa quel che funziona: dire dove sei, e cambiare pagina
    toccando. (Per un giro la si poteva anche tenere premuta per aprire il
    foglio delle pagine; il foglio e' uscito il 23/09/2026.)

    Il banco tiene il codice **onesto**: niente ascoltatori che aspettano un
    evento che il sistema non manda mai.
    """
    source = (ASSETS / "home-pages.js").read_text(encoding="utf-8")
    assert "openLauncher" not in source, (
        "la striscia prova di nuovo ad aprire il cassetto: quel gesto non "
        "arriva mai all'app con la navigazione a gesti"
    )
    assert "touchend" not in source, "un ascoltatore che il sistema non fa scattare"


# ── Cosa c'e' dentro una pagina ─────────────────────────────────────────────


def test_only_the_page_you_look_at_is_alive() -> None:
    """«Resta viva solo la pagina che guardi: le altre si spengono, o te le
    paghi in batteria» (tavola `PagineGestione`).

    Non «la corrente piu' le due vicine»: tre `<iframe>` che girano insieme su
    un telefono sono tre app vive.
    """
    _run(
        # `full` porta il **numero del tentativo**, non un `1`: serve a
        # riconoscere il proprio giro dopo un'attesa. Qui basta che sia
        # valorizzato.
        "const pannelli = () => track.children.filter((c) => c.dataset.id);\n"
        "const live = () => pannelli().filter((p) => p.dataset.full);\n"
        "assert.equal(live().length, 0,\n"
        "  'una pagina si e riempita senza che nessuno la guardi');\n"
        "scroll(LEFT);\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "assert.equal(live().length, 1);\n"
        "assert.equal(live()[0].dataset.id, 'p1');\n"
        "scroll(LEFT);\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "assert.equal(live().length, 1, 'la pagina di prima e rimasta accesa');\n"
        "assert.equal(live()[0].dataset.id, 'p2');",
        pages=DUE_APP,
    )


def test_going_back_to_the_chat_shuts_every_page_down() -> None:
    _run(
        "scroll(LEFT);\n"
        "scroll(RIGHT);\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "const live = track.children.filter((c) => c.dataset.full);\n"
        "assert.equal(live.length, 0);",
        pages=DUE,
    )


def test_an_app_page_mounts_that_app_frame() -> None:
    _run(
        "scroll(LEFT);\n"
        # Il montaggio aspetta il segreto, quindi non e' finito al ritorno
        # del gesto: un giro di eventi e c'e'.
        "await new Promise((r) => setTimeout(r, 20));\n"
        "const page = track.children.find((c) => c.dataset.id === 'p1');\n"
        "assert.equal(page.children.length, 1);\n"
        "assert.equal(page.children[0].dataset.slug, 'orto');",
        pages=DUE,
    )


def test_the_app_page_you_look_at_hears_that_its_data_changed() -> None:
    """Jenny gira un'azione dell'app in chat: la pagina di quell'app si rilegge.

    `jenny:data-changed` e' cio' che `jenny-sdk.js` ascolta. Arriva solo alla
    pagina corrente — l'unica viva — e solo se e' l'app di cui si parla.
    """
    _run(
        "homePages.goToId('p1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "assert.equal(globalThis.APP_DATA?.length, 1, 'la pagina app non si e iscritta');\n"
        "globalThis.APP_DATA[0]('lampo');\n"
        "assert.deepEqual(liveWindow().mailbox, [], 'avvisata per un\\'altra app');\n"
        "globalThis.APP_DATA[0]('orto');\n"
        "assert.deepEqual(liveWindow().mailbox,\n"
        "  [{ type: 'jenny:data-changed', slug: 'orto' }]);\n"
        # Una seconda pagina app non iscrive una seconda volta: lo stesso
        # frame arriverebbe due volte alla stessa cornice.
        "homePages.goToId('p2');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "assert.equal(globalThis.APP_DATA.length, 1, 'iscritta due volte');\n",
        DUE_APP,
    )


def test_a_notebook_page_mounts_no_app() -> None:
    """Una pagina quaderno non deve montare una cornice d'app per sbaglio.

    Ci arriva la chat, portata dal trasloco. Un `<iframe>` su
    `/apps/project:piante/index.html` sarebbe un 404 a tutta pagina, e da fuori
    somiglierebbe a un'app che non parte.
    """
    _run(
        "scroll(LEFT); scroll(LEFT);\n"
        "const page = track.children.find((c) => c.dataset.id === 'p2');\n"
        "assert.ok(!page.children.some((c) => c.dataset.slug), 'monta una cornice di app');",
        pages=DUE,
    )


def test_the_name_of_a_page_comes_from_its_kind() -> None:
    _run(
        "assert.equal(homePages.nameOf({kind: 'app', ref: 'orto'}), 'orto');\n"
        "assert.equal(homePages.nameOf(null), '', 'senza pagina il titolo deve restare vuoto');"
    )


def test_the_shell_says_which_page_is_on_from_the_first_frame() -> None:
    """Come `data-view`, e per lo stesso motivo.

    Su una pagina di lato la vista **e' ancora** `chat`: le regole scritte solo
    su `data-view` lasciavano acceso il chevron della tendina sopra il nome di
    un'app — un comando che si vede, e' disabilitato, e non fa niente. Serviva
    un secondo segnale, e sta dove sta l'altro.
    """
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert '<main class="home-shell" data-view="chat" data-page="chat">' in html, (
        "il guscio non nasce dichiarando su che pagina e'"
    )
    # Il valore, non la presenza della stringa: fino al 25/09/2026 bastava che
    # «data-page» comparisse nel metodo, e un attributo scritto con l'id
    # sbagliato (o sempre uguale) passava verde.
    app_js = (ASSETS / "home-app.js").read_text(encoding="utf-8")
    run_js(
        "import assert from 'node:assert/strict';\n"
        "class Guscio {\n"
        "  constructor() {\n"
        "    this.shell = { attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };\n"
        "  }\n"
        "  _haComposer() { return false; }\n"
        "  _placeJenny() {}\n"
        "  _applyHead() {}\n"
        "  _reportChatOnScreen() {}\n  "
        + member(app_js, "onPageChanged")
        + "\n}\n"
        "const g = new Guscio();\n"
        "g.onPageChanged(2, { id: 'notebooks', kind: 'notebooks', fixed: true });\n"
        "assert.equal(g.shell.attrs['data-page'], 'notebooks');\n"
        "g.onPageChanged(4, { id: 'p1', kind: 'app', ref: 'orto', fixed: false });\n"
        "assert.equal(g.shell.attrs['data-page'], 'p1');\n"
        "g.onPageChanged(9, null);\n"
        "assert.equal(g.shell.attrs['data-page'], '', 'fuori dalla pista resta la pagina di prima');\n"
    )


def test_the_app_frame_is_not_built_without_the_secret() -> None:
    """Il token viaggia **nell'indirizzo** della cornice.

    Costruirla prima che il segreto ci sia vuol dire `token=undefined`, cioe'
    un 401 e una pagina bianca. `openApp` questa guardia ce l'ha da sempre; si
    era persa estraendo la cornice, e qui si prova che c'e' di nuovo.

    Il finto `frameForApp` si segna il segreto **al momento della chiamata**:
    fino al 25/09/2026 il banco guardava solo che alla fine il segreto ci
    fosse, e una cornice costruita prima del bootstrap passava verde.
    """
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "assert.equal(api.getSecret(), '', 'il finto parte senza segreto');\n"
        "scroll(LEFT);\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "assert.equal(api.getSecret(), 'ok', 'non ha atteso il segreto');\n"
        "const page = track.children.find((c) => c.dataset.id === 'p1');\n"
        "assert.equal(page.children.length, 1, 'la cornice non e stata montata');\n"
        "assert.equal(page.children[0].dataset.secret, 'ok',\n"
        "  'la cornice e\\u2019 nata prima del segreto: token=undefined');",
        pages=DUE,
    )


def test_a_fast_finger_does_not_mount_two_frames() -> None:
    """Segnare la pagina come piena **prima** dell'attesa.

    Senza, due `goTo` ravvicinati entrano tutti e due nel montaggio e la
    pagina finisce con due cornici — due volte la stessa app, viva due volte.
    """
    _run(
        "scroll(LEFT);\n"
        "homePages.goTo(CHAT()); homePages.goToId('p1'); homePages.goTo(CHAT()); homePages.goToId('p1');\n"
        "await new Promise((r) => setTimeout(r, 30));\n"
        "const page = track.children.find((c) => c.dataset.id === 'p1');\n"
        "assert.equal(page.children.length, 1, `cornici montate: ${page.children.length}`);",
        pages=DUE,
    )


def test_a_page_already_full_is_not_filled_again() -> None:
    """La guardia d'ingresso guarda **se** e' pieno, non se vale `1`.

    Da quando `full` porta il numero del tentativo, un confronto con `'1'`
    lascerebbe passare ogni rientro dal secondo in poi.
    """
    _run(
        "scroll(LEFT);\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "const page = track.children.find((c) => c.dataset.id === 'p1');\n"
        "const mark = page.dataset.full;\n"
        "await homePages._fill(page);\n"
        "await homePages._fill(page);\n"
        "assert.equal(page.dataset.full, mark, 'il segno e cambiato: e rientrato');\n"
        "assert.equal(page.children.length, 1, `cornici: ${page.children.length}`);",
        pages=DUE,
    )


def test_the_clipping_and_the_moving_are_two_different_elements() -> None:
    """**Il difetto piu' caro di questo giro, e il piu' difficile da vedere.**

    Con `overflow: hidden` sulla pista — l'elemento che porta anche il
    `transform` — un `<iframe>` dentro una pagina **si carica e non dipinge**.
    Misurato sul telefono il 22 settembre 2026, un passo per volta: l'evento
    `load` arriva, l'elemento e' `visible`, opacita' 1, `display: block`, misura
    574x450, sta nel documento e ha un `contentWindow`. E a schermo resta nero.

    La stessa cornice spostata nel corpo del documento si vede subito. Tolto
    `overflow` alla pista, pure. **Non** bastano i rimedi soliti: un piano di
    composizione proprio (`translateZ(0)`) sulla cornice o sul pannello non
    cambia niente, e nemmeno montarla a scivolata finita invece che durante.

    Il ritaglio non puo' nemmeno salire al guscio: Jenny e' `position:absolute`
    con un `right` negativo — sporge apposta dal bordo — e li' verrebbe
    tagliata. Quindi ci vuole un elemento in mezzo: uno ritaglia, l'altro si
    muove.

    Un banco sul CSS e non sul comportamento, perche' il comportamento lo puo'
    dire solo un telefono: in node l'iframe non esiste, e su un desktop il
    difetto non si riproduce.
    """
    css = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    showcase = css.split("\n.home-showcase {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden" in showcase, "l'involucro non ritaglia piu'"
    track = css.split("\n.home-track {", 1)[1].split("}", 1)[0]
    assert "overflow" not in track, (
        "la pista ritaglia di nuovo: con il transform addosso, un iframe dentro "
        "una pagina si carica e non dipinge"
    )
    shell = css.split(".home-shell {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden" not in shell, (
        "il ritaglio e' salito al guscio: taglia Jenny, che sporge apposta"
    )
    html = (UI / "index.html").read_text(encoding="utf-8")
    i = html.index('class="home-showcase"')
    j = html.index('class="home-track"')
    assert i < j, "l'involucro non sta piu' attorno alla pista"


# ── Il gesto che arriva da dentro una app ───────────────────────────────────
#
# La pagina di una Jenny App e' **tutta** l'app, intestazione compresa: il dito
# che la tocca al guscio non ci arriva mai, e lo scorrimento fra pagine — che
# ovunque altro nella casa funziona — li' dentro non esisteva. Misurato sul
# telefono il 22/09/2026: in nessuna delle due direzioni, non solo in una.
#
# Da allora il gesto lo riconosce la app (solo li' dentro si vede il suo DOM, e
# quindi si puo' cedere il gesto a una sua tabella larga) e lo **decide** il
# guscio (solo lui sa se una pagina di fianco c'e'). Quel che segue prova la
# meta' del guscio: che dia retta a chi deve, e a nessun altro.


def test_a_gesture_forwarded_from_an_app_page_changes_page() -> None:
    """Il dito e' dentro l'app, la pista si muove lo stesso."""
    _run(
        "homePages.goToId('p1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "assert.ok(liveWindow(), 'la cornice non ha una finestra');\n"
        "scrollFromApp(RIGHT);\n"
        "assert.equal(homePages.index, CHAT(), 'da dentro l app non si torna alla chat');\n",
        ONE,
    )


def test_a_short_forwarded_gesture_stays_put() -> None:
    """Sotto la soglia si torna dov'eri: la soglia la calcola la app, col suo
    schermo, ed e' la stessa del modulo condiviso."""
    _run(
        "homePages.goToId('p1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "scrollFromApp(RIGHT, { short: true });\n"
        "assert.equal(homePages.index, I('p1'));\n",
        ONE,
    )


def test_only_the_page_you_are_looking_at_may_move_the_track() -> None:
    """Chi parla si riconosce dalla finestra, non dal fatto che parli.

    Quel che arriva da un frame e' scritto da codice dell'app: senza questa
    guardia, qualunque cosa sappia fare `postMessage` muoverebbe la casa.
    """
    _run(
        "homePages.goToId('p1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "scrollFromApp(RIGHT, { source: { app: 'qualcun-altro' } });\n"
        "assert.equal(homePages.index, I('p1'), 'una finestra estranea ha mosso la pista');\n",
        ONE,
    )


def test_a_page_that_is_not_an_app_has_no_window_to_listen_to() -> None:
    """Una stanza e' un elemento del guscio: una `contentWindow` non ce l'ha.

    **La sorgente e' `null`, non `undefined`, e la differenza e' tutto il
    banco.** `MessageEvent.source` e' nullabile per specifica, e su una pagina
    che non e' una app anche la finestra cercata e' `null`: confrontarle senza
    chiedersi prima se la finestra esiste vuol dire `null !== null`, cioe'
    falso, cioe' passa. Scritto la prima volta con `undefined` il banco era
    verde anche togliendo la guardia — l'ha detto la mutazione, non la
    rilettura (22/09/2026).
    """
    _run(
        "homePages.goToId('p2');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "scrollFromApp(RIGHT, { source: null });\n"
        "assert.equal(homePages.index, I('p2'), 'un messaggio senza sorgente ha mosso la pista');\n",
        DUE,
    )


def test_the_shell_decides_at_every_gesture_whether_it_can_move() -> None:
    """Fra un gesto e l'altro la modalita' ordina puo' aprirsi, e allora il
    dito e' suo.

    La app non lo sa e non puo' saperlo: se la risposta se la portasse dietro
    dalla costruzione del frame, sarebbe quella di allora e non quella di
    adesso.
    """
    _run(
        "homePages.goToId('p1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "app.strip.sorting = true;\n"
        "scrollFromApp(RIGHT);\n"
        "assert.equal(homePages.index, I('p1'), 'con le pagine da spostare la pista si e mossa');\n"
        "app.strip.sorting = false;\n"
        "scrollFromApp(RIGHT);\n"
        "assert.equal(homePages.index, CHAT(), 'chiusa la modalita, il gesto non torna');\n",
        ONE,
    )


def test_a_forwarded_drag_without_its_start_moves_nothing() -> None:
    """Un `move` orfano userebbe una larghezza mai misurata."""
    _run(
        "homePages.goToId('p1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "track.style.transform = 'segno';\n"
        "fromApp({ phase: 'move', dx: 120 }, { source: liveWindow() });\n"
        "assert.equal(track.style.transform, 'segno', 'un muove orfano ha mosso la pista');\n",
        ONE,
    )


def test_a_broken_number_never_reaches_the_track() -> None:
    """`translateX(NaN)` e la pista sparisce — e i numeri li scrive l'app."""
    _run(
        "homePages.goToId('p1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "const fromIndex = { source: liveWindow() };\n"
        "fromApp({ phase: 'start' }, fromIndex);\n"
        "track.style.transform = 'segno';\n"
        "fromApp({ phase: 'move', dx: 'boh' }, fromIndex);\n"
        "assert.equal(track.style.transform, 'segno', 'un dx non numerico e passato');\n",
        ONE,
    )


def test_a_cancelled_forwarded_gesture_snaps_back() -> None:
    """Il sistema si riprende il gesto a meta': la pagina resta quella."""
    _run(
        "homePages.goToId('p1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "const fromIndex = { source: liveWindow() };\n"
        "fromApp({ phase: 'start' }, fromIndex);\n"
        "fromApp({ phase: 'move', dx: 150 }, fromIndex);\n"
        "fromApp({ phase: 'cancel' }, fromIndex);\n"
        "assert.equal(homePages.index, I('p1'));\n"
        "assert.equal(track.style.transform, `translateX(${-I('p1') * 100}%)`, 'non e tornata a posto');\n",
        ONE,
    )


# ── Quale righello ─────────────────────────────────────────────────────────


def test_the_finger_is_measured_against_the_screen() -> None:
    """La cornice si sposta insieme al dito, quindi il suo righello mente.

    Dentro una Jenny App la finestra di chi ascolta **e'** la cornice che la
    pista sta trascinando: al limite il dito si muove di 200 e `client` dice
    zero. L'utente lo ha visto come una vibrazione — avanti, indietro, avanti —
    e la misura su Chrome del telefono l'ha confermato riga per riga
    (22/09/2026, v. la testata di `shared/horizontal-swipe.js`).
    """
    _run(
        "scrollOffset(0, -200);\n"
        "assert.equal(homePages.index, I('p1'), 'col righello dello schermo non si e mossa');\n",
        ONE,
    )


def test_a_finger_that_only_the_window_saw_moves_nothing() -> None:
    """L'altro verso della stessa regola, ed e' quello che uccide la mutazione.

    Se si tornasse a misurare con la finestra, questo gesto — 200 per lei,
    zero per lo schermo — cambierebbe pagina pur non essendo mai esistito.
    """
    _run(
        "scrollOffset(-200, 0);\n"
        "assert.equal(homePages.index, CHAT(), 'un gesto che lo schermo non ha visto ha cambiato pagina');\n",
        ONE,
    )


# ── Le pagine conversazione (23/09/2026) ────────────────────────────────────
#
# Una scorciatoia che cambia la conversazione dell'unica chat, travestita da
# pagina. Qui si prova l'aggancio: che le pagine chiamino il trasloco nei
# momenti giusti, con i pannelli giusti, e la regola che le tiene insieme —
# **una pagina quaderno mostra solo il suo quaderno**. Il trasloco vero, con
# la foto e i suoi tranelli, ha il banco suo.

MIXED = [
    {"id": "a1", "kind": "app", "ref": "orto"},
    {"id": "q1", "kind": "conversation", "ref": "project:piante"},
    {"id": "a2", "kind": "app", "ref": "lampo"},
]


def test_arriving_on_a_notebook_page_brings_the_chat_there() -> None:
    """Col pannello che la pista mostra davvero, e con la chiave del quaderno."""
    _run(
        "moves.length = 0;\n"
        "homePages.goToId('q1');\n"
        "const a = arrivals();\n"
        "assert.equal(a.length, 1);\n"
        "assert.equal(a[0].k, 'project:piante');\n"
        "assert.equal(a[0].p, homePages.panelOf(I('q1')));\n"
        "assert.equal(a[0].p.dataset.id, 'q1');\n",
        MIXED,
    )


def test_back_on_the_chat_page_the_chat_goes_home_with_its_own_conversation() -> None:
    """La pagina chat ha la sua, e il trasloco la rimette."""
    _run(
        "homePages.goToId('q1');\n"
        "moves.length = 0;\n"
        "homePages.goTo(CHAT());\n"
        "const a = arrivals();\n"
        "assert.equal(a.length, 1);\n"
        "assert.equal(a[0].p, chatPanel);\n"
        "assert.equal(a[0].k, PERSONAL);\n",
        MIXED,
    )


def test_passing_through_apps_does_not_touch_the_chat() -> None:
    """Attraversarle non cambia conversazione: la chat resta dov'era."""
    _run(
        "moves.length = 0;\n"
        "homePages.goToId('a1'); homePages.goToId('a2');\n"
        "assert.equal(arrivals().length, 0, 'una pagina senza conversazione ha chiamato il trasloco');\n",
        MIXED,
    )


def test_a_notebook_page_off_screen_keeps_its_photo_and_is_never_emptied() -> None:
    """Spenta, ma non vuota: la foto e' quel che si vede entrare scorrendo.

    E soprattutto non si svuota col `textContent = ''` delle altre: se la
    chat e' parcheggiata li' mentre guardi un'app, porterebbe via la chat.
    """
    _run(
        "const q = homePages.panelOf(I('q1'));\n"
        "const parcheggiata = createEl(null, 'home-chat');\n"
        "q.appendChild(parcheggiata);\n"
        "moves.length = 0;\n"
        "homePages.goToId('a1');\n"
        "const f = moves.filter((x) => x.fa === 'foto');\n"
        "assert.equal(f.length, 1);\n"
        "assert.equal(f[0].p, q);\n"
        "assert.equal(f[0].k, 'project:piante');\n"
        "assert.ok(q.children.includes(parcheggiata), 'la pagina quaderno e stata svuotata');\n",
        MIXED,
    )


def test_redrawing_the_pages_takes_the_chat_back_before_throwing_a_panel() -> None:
    """Salvare l'elenco ridisegna i pannelli: la chat torna a casa **prima**.

    Il finto registra se il pannello era ancora attaccato al momento della
    domanda: dopo, sarebbe troppo tardi — la chat sarebbe gia' andata via con
    lui.
    """
    _run(
        "moves.length = 0;\n"
        "await homePages.save(SCHERMATE);\n"
        "const c = moves.filter((x) => x.fa === 'home');\n"
        "assert.equal(c.length, 3, 'non ha chiesto per ogni pannello');\n"
        "assert.ok(c.every((x) => x.attached), 'ha chiesto dopo aver buttato il pannello');\n"
        "assert.ok(c.every((x) => x.c === chatPanel), 'la casa e il pannello della chat');\n",
        MIXED,
    )


def test_a_notebook_page_is_named_after_its_notebook() -> None:
    """Il nome, non la chiave: `project:piante` in testa sarebbe gergo."""
    _run(
        "assert.equal(homePages.nameOf(SCHERMATE[1]), 'piante');\n",
        MIXED,
    )


# ── Da fuori: i Quaderni, Home, un avviso ────────────────────────────────────


def test_from_the_chat_page_a_notebook_opens_right_there() -> None:
    """Anche se ha una pagina sua. «Fai come ora, non scorrere» — l'utente, 23/09."""
    _run(
        "const r = await homePages.openConversation('project:piante');\n"
        "assert.deepEqual(shown, ['project:piante']);\n"
        "assert.equal(homePages.index, CHAT(), 'e scorso alla pagina del quaderno');\n"
        "assert.equal(homePages.homeConversation, 'project:piante');\n"
        "assert.equal(r, 'mostrata', 'la promessa del cambio non torna a chi chiama');\n",
        MIXED,
    )


def test_a_notebook_page_only_ever_shows_its_notebook() -> None:
    """**L'invariante.** Da una pagina quaderno un'altra conversazione si apre
    nella pagina chat, e ci si va.

    E chi chiama riceve la lettura del trasloco, non un «fatto» anticipato: ci
    manda subito dopo un messaggio, e deve finire nella conversazione giusta.
    """
    _run(
        "homePages.goToId('q1');\n"
        "moves.length = 0;\n"
        "const r = await homePages.openConversation(PERSONAL);\n"
        "assert.equal(homePages.index, CHAT());\n"
        "assert.equal(homePages.homeConversation, PERSONAL);\n"
        "assert.deepEqual(shown, [], 'ha cambiato la chat dentro la pagina del quaderno');\n"
        "const a = arrivals();\n"
        "assert.equal(a.length, 1);\n"
        "assert.equal(a[0].p, chatPanel);\n"
        "assert.equal(a[0].k, PERSONAL);\n"
        "assert.equal(r, 'letto', 'chi chiama non aspetta la lettura');\n",
        MIXED,
    )


def test_asking_a_notebook_page_for_its_own_notebook_stays_there() -> None:
    """«Parlane» e «Segnala» dalle pagine del quaderno chiedono proprio quello."""
    _run(
        "homePages.goToId('q1');\n"
        "await homePages.openConversation('project:piante');\n"
        "assert.equal(homePages.index, I('q1'));\n"
        "assert.deepEqual(shown, ['project:piante']);\n"
        "assert.equal(homePages.homeConversation, PERSONAL, 'la pagina chat ha preso il quaderno');\n",
        MIXED,
    )


def test_from_an_app_page_a_conversation_opens_on_the_chat_page() -> None:
    """Home da una pagina di lato: si torna alla chat, come ogni launcher."""
    _run(
        "homePages.goToId('a1');\n"
        "await homePages.openConversation(PERSONAL);\n"
        "assert.equal(homePages.index, CHAT());\n"
        "assert.deepEqual(shown, []);\n",
        MIXED,
    )


# ── La larghezza ────────────────────────────────────────────────────────────


def test_a_word_that_does_not_wrap_cannot_widen_every_page() -> None:
    """**Trovato sul telefono il 23/09/2026**, aprendo il quaderno «piante».

    La pista e' un elemento flessibile della vetrina, e senza `min-width: 0` la
    sua larghezza minima e' quella del suo contenuto: un percorso lungo in un
    messaggio (`entities/...`) la allargava oltre lo schermo, e con lei ogni
    pagina, che e' larga il 100% della pista. Chat tagliata a destra, tasto
    d'invio fuori schermo — sulla pagina 0 come su quella del quaderno.
    Provato con una build che cambiava solo questa riga: sistemato tutto.
    """
    css = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    track = css.split(".home-track {", 1)[1].split("}", 1)[0]
    # La dichiarazione, non la parola: il commento sopra la nomina, e un
    # `in` sul testo del blocco era verde anche togliendo la riga — l'ha detto
    # la mutazione.
    assert re.search(r"^\s*min-width:\s*0\s*;", track, re.M), (
        "la pista ha perso `min-width: 0`: una parola che non va a capo "
        "allarga di nuovo tutte le pagine oltre lo schermo"
    )


# ── Appendere e staccare (23/09/2026) ───────────────────────────────────────
#
# Una cosa si appende **dal posto dove vive** — l'app dal cassetto, il quaderno
# dalla tendina — e queste tre sono l'unica porta. V. `.agent/pagine-dal-posto-plan.md`.


def test_pinning_saves_the_page_and_lands_on_it() -> None:
    """Chi l'ha appena aggiunta vuole vederla."""
    _run(
        "const done = await homePages.append('app', 'orto');\n"
        "assert.equal(done, true);\n"
        "const api = (await import('./shared/api-client.js')).api;\n"
        "assert.deepEqual(api.writes.at(-1).map((s) => [s.kind, s.ref]), [['app', 'orto']]);\n"
        "const fresh = api.writes.at(-1)[0].id;\n"
        "assert.equal(homePages.index, I(fresh), 'non si e atterrati sulla pagina nuova');\n"
        "assert.equal(I(fresh), CHAT() + 1, 'la prima pagina aggiunta non sta dopo la chat');\n"
        "assert.equal(homePages.pending('app', 'orto'), true);\n"
    )


def test_a_new_page_goes_after_the_last_one_added() -> None:
    """Le aggiunte restano vicine fra loro, anche dopo che l'utente ha spostato
    le fisse — e anche se le ha messe **prima** della chat."""
    _run(
        "await homePages.append('app', 'lampo');\n"
        "const api = (await import('./shared/api-client.js')).api;\n"
        "const fresh = api.writes.at(-1).at(-1).id;\n"
        "assert.deepEqual(homePages.order, ['p1', fresh, 'settings', 'chat', 'app', 'notebooks']);\n",
        ONE,
        order=["p1", "settings", "chat", "app", "notebooks"],
    )


def test_pinning_twice_is_one_page() -> None:
    """Due pagine sulla stessa cosa sono una di troppo."""
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "const before = api.writes.length;\n"
        "assert.equal(await homePages.append('app', 'orto'), false);\n"
        "assert.equal(api.writes.length, before, 'ha scritto una pagina doppia');\n"
        "assert.equal(homePages.howMany, 5);\n",
        ONE,
    )


def test_with_the_ceiling_full_nothing_is_pinned() -> None:
    full = [{"id": f"p{i}", "kind": "app", "ref": f"x{i}"} for i in range(8)]
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "assert.equal(await homePages.append('app', 'orto'), false);\n"
        "assert.equal(api.writes.length, 0, 'ha scritto oltre il tetto');\n",
        full,
    )


def test_the_same_ref_under_another_kind_is_another_page() -> None:
    """`pending` guarda specie **e** riferimento: un'app e un quaderno possono
    chiamarsi uguale senza essere la stessa pagina."""
    _run(
        "assert.equal(homePages.pending('app', 'orto'), true);\n"
        "assert.equal(homePages.pending('conversation', 'orto'), false);\n",
        ONE,
    )


def test_unpinning_saves_the_rest() -> None:
    _run(
        "assert.equal(await homePages.detach('app', 'orto'), true);\n"
        "const api = (await import('./shared/api-client.js')).api;\n"
        "assert.deepEqual(api.writes.at(-1).map((s) => s.id), ['p2']);\n"
        "assert.ok(!api.ordini.at(-1).includes('p1'), 'la pagina staccata e rimasta nell ordine');\n"
        "assert.equal(await homePages.detach('app', 'orto'), false, 'ha staccato due volte');\n",
        DUE,
    )


def test_only_the_page_on_screen_can_be_reached() -> None:
    """Le pagine accanto sono fuori schermo ma nel documento: il Tab della
    tastiera fisica e chi legge lo schermo ci finivano dentro. Tutte `inert`
    tranne quella che si guarda, fisse comprese."""
    _run(
        "const raggiungibili = () => track.children.filter((p) => !p.inert);\n"
        "assert.deepEqual(raggiungibili(), [chatPanel]);\n"
        "homePages.goToId('settings');\n"
        "assert.deepEqual(raggiungibili(), [settingsPanel]);\n"
        "homePages.goToId('p1');\n"
        "assert.deepEqual(raggiungibili(), [homePages.panelOf(I('p1'))]);\n"
        "assert.equal(chatPanel.inert, true);\n",
        ONE,
    )


def test_a_refused_write_says_so_and_changes_nothing() -> None:
    """Un salvataggio rifiutato saliva a chi chiamava, e nessuno lo prendeva:
    «Metti come pagina» e «Togli» fallivano in silenzio. Ora lo dice un
    avviso, torna `false` a chi chiede, e la pista resta com'era."""
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api._refuses = true;\n"
        "const before = [...homePages.order];\n"
        "assert.equal(await homePages.append('app', 'lampo'), false, 'un appendere fallito dice di esserci riuscito');\n"
        "assert.equal(await homePages.detach('app', 'orto'), false, 'uno staccare fallito dice di esserci riuscito');\n"
        "assert.equal(await homePages.save([], []), false);\n"
        "assert.deepEqual(homePages.order, before, 'la pista e\\u2019 cambiata senza che il server abbia salvato');\n"
        "assert.equal(globalThis.NOTICES?.length, 3, 'un guasto non e\\u2019 stato detto');\n"
        "assert.deepEqual(globalThis.NOTICES[0], ['home.pages.saveFailed', 'error']);\n",
        ONE,
    )


def test_the_gone_page_button_survives_a_refused_write() -> None:
    """Il bottone «Togli la pagina» chiama `detach` da un gestore di click:
    un rifiuto non preso diventava un errore non gestito nella pagina."""
    _run(
        "homePages.goToId('g1');\n"
        "await new Promise((r) => setTimeout(r, 60));\n"
        + _gone_in('g1')
        + "const api = (await import('./shared/api-client.js')).api;\n"
        "api._refuses = true;\n"
        "notice().children[1].click();\n"
        "await new Promise((r) => setTimeout(r, 10));\n"
        "assert.equal(homePages.howMany, 5, 'la pagina e\\u2019 sparita senza che il server l\\u2019abbia tolta');\n"
        "assert.deepEqual(globalThis.NOTICES, [['home.pages.saveFailed', 'error']]);\n",
        GONE,
    )


def test_rereading_keeps_you_on_your_page_when_it_is_still_there() -> None:
    """Dopo una cancellazione fatta altrove la tua pagina puo' esserci ancora:
    rileggere non deve riportarti alla chat, come farebbe `load()`."""
    _run(
        "homePages.goToId('p2');\n"
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api._list = [SCHERMATE[1]];\n"
        "await homePages.reload();\n"
        "assert.equal(homePages.howMany, 5);\n"
        "assert.equal(homePages.index, I('p2'), 'non e rimasta sulla sua pagina');\n"
        "assert.equal(homePages.pages[0].id, 'p2');\n",
        DUE,
    )


def test_rereading_when_your_page_is_gone_takes_you_to_the_chat() -> None:
    _run(
        "homePages.goToId('p2');\n"
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api._list = [SCHERMATE[0]];\n"
        "await homePages.reload();\n"
        "assert.equal(homePages.howMany, 5);\n"
        "assert.equal(homePages.index, CHAT());\n",
        DUE,
    )


def test_pinning_from_a_sheet_closes_what_is_above_first() -> None:
    """Si appende dal cassetto o dalla tendina: atterrare sotto un cassetto
    aperto vorrebbe dire non vedere di aver fatto niente. E il giro che chiude
    gli strati ha un tetto, perche' `_closeOverlays` torna vero anche quando
    delega la chiusura.

    Il giro e' quello di Home (`_closeAllOverlays`, provato in
    `test_home_switch_client.py`): qui si controlla che l'appendere ci passi."""
    app_js = (ASSETS / "home-app.js").read_text(encoding="utf-8")
    door = app_js.split("pagesPort() {", 1)[1].split("\n  }\n", 1)[0]
    append = door.split("append:", 1)[1].split("detach:", 1)[0]
    assert "this._closeAllOverlays()" in append
    assert append.index("_closeAllOverlays") < append.index("this.homePages.append")
    all = member(app_js, "_closeAllOverlays")
    assert "i < 8" in all, "il giro che chiude gli strati non ha piu' un tetto"


# ── La pagina di una cosa che non c'e' piu' (23/09/2026) ────────────────────
#
# Cancellata dalla sua scheda, la cosa si porta via la pagina (lo fa il
# gateway). Sparita per altre strade, la pagina resta — toglierla da se' sarebbe
# una decisione presa al posto dell'utente — e lo dice, con il bottone per
# toglierla. Col foglio delle pagine andato, e' l'unico posto da cui si toglie
# una pagina verso un quaderno che nella tendina non c'e' piu'.

GONE = [{"id": "g1", "kind": "app", "ref": "svanita"}]
NOTEBOOK = [{"id": "q1", "kind": "conversation", "ref": "project:piante"}]


def _gone_in(page: str) -> str:
    return (
        f"const panel = homePages.panelOf(I('{page}'));\n"
        "const notice = () => panel.children.find((c) => c.className === 'home-page-gone');\n"
    )


def test_an_app_that_is_gone_says_so_instead_of_a_blank_frame() -> None:
    _run(
        "homePages.goToId('g1');\n"
        "await new Promise((r) => setTimeout(r, 60));\n"
        + _gone_in('g1')
        + "assert.ok(notice(), 'nessun avviso sulla pagina di un app sparita');\n"
        "assert.ok(!panel.children.some((c) => c.dataset?.slug), 'la cornice verso il nulla e rimasta');\n"
        "assert.match(notice().children[0].textContent, /home\\.pages\\.appGone.*svanita/);\n",
        GONE,
    )


def test_the_gone_page_can_remove_itself() -> None:
    _run(
        "homePages.goToId('g1');\n"
        "await new Promise((r) => setTimeout(r, 60));\n"
        + _gone_in('g1')
        + "notice().children[1].click();\n"
        "await new Promise((r) => setTimeout(r, 10));\n"
        "const api = (await import('./shared/api-client.js')).api;\n"
        "assert.deepEqual(api.writes.at(-1), []);\n"
        "assert.equal(homePages.howMany, 4);\n",
        GONE,
    )


def test_an_app_that_is_there_mounts_as_always() -> None:
    _run(
        "homePages.goToId('p1');\n"
        "await new Promise((r) => setTimeout(r, 60));\n"
        + _gone_in('p1')
        + "assert.equal(notice(), undefined);\n"
        "assert.equal(panel.children[0].dataset.slug, 'orto');\n",
        ONE,
    )


def test_a_list_that_could_not_be_read_marks_nothing() -> None:
    """«Non lo so» non e' «sparita»: con la lista rotta, dire a un'app che
    c'e' che non c'e' piu' sarebbe peggio di tacere."""
    _run(
        "globalThis.BROKEN_LIST = true;\n"
        "homePages.goToId('g1');\n"
        "await new Promise((r) => setTimeout(r, 60));\n"
        + _gone_in('g1')
        + "assert.equal(notice(), undefined);\n"
        "assert.equal(panel.children[0].dataset.slug, 'svanita');\n",
        GONE,
    )


def test_leaving_before_the_answer_draws_nothing() -> None:
    """Sei gia' uscito quando l'elenco risponde: la pagina che hai lasciato
    non si riempie di un avviso che nessuno guarda.

    Si esce **dopo** che la cornice e' montata e **prima** che l'elenco
    risponda (il finto risponde dopo 30 ms). Uscire subito, com'era scritto la
    prima volta, provava la guardia sull'attesa del segreto e non questa: la
    mutazione che la toglieva sopravviveva (23/09/2026).
    """
    _run(
        "homePages.goToId('g1');\n"
        "await new Promise((r) => setTimeout(r, 5));\n"
        "assert.ok(homePages.panelOf(I('g1')).children.some((c) => c.dataset?.slug), 'la cornice non e montata');\n"
        "homePages.goTo(CHAT());\n"
        "await new Promise((r) => setTimeout(r, 60));\n"
        + _gone_in('g1')
        + "assert.equal(notice(), undefined);\n",
        GONE,
    )


def test_a_notebook_that_is_gone_is_covered_not_emptied() -> None:
    """Sopra la chat e non al suo posto: sotto c'e' il trasloco, con le sue
    regole. E coprendola non si scrive a una conversazione sparita."""
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api._notebooks = ['altro'];\n"
        "homePages.goToId('q1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        + _gone_in('q1')
        + "assert.ok(notice(), 'nessun avviso sulla pagina di un quaderno sparito');\n"
        "assert.match(notice().children[0].textContent, /home\\.pages\\.notebookGone.*piante/);\n",
        NOTEBOOK,
    )


def test_a_notebook_that_is_there_is_left_alone() -> None:
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api._notebooks = ['piante'];\n"
        "homePages.goToId('q1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        + _gone_in('q1')
        + "assert.equal(notice(), undefined);\n",
        NOTEBOOK,
    )


def test_a_notebook_that_is_gone_tells_the_shell_to_take_the_keyboard_away() -> None:
    """Coprire la chat ferma il dito, non la tastiera fisica: il guscio aveva
    gia' rimesso il fuoco sul campo all'arrivo, e i tasti scrivevano al
    quaderno cancellato. La pagina lo segna, e glielo dice — anche quando il
    quaderno torna, perche' allora il campo si puo' riprendere."""
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "const notices = [];\n"
        "app.onGoneChanged = (p) => notices.push([p, homePages.goneHere()]);\n"
        "api._notebooks = ['altro'];\n"
        "homePages.goToId('q1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        + _gone_in('q1')
        + "assert.equal(homePages.goneHere(), true, 'la pagina sparita non lo dice');\n"
        "assert.deepEqual(notices, [[panel, true]], 'il guscio non sa che il campo e\\u2019 coperto');\n"
        "homePages.goTo(CHAT());\n"
        "assert.equal(homePages.goneHere(), false, 'la pagina chat si e\\u2019 presa il segno');\n"
        "api._notebooks = ['piante'];\n"
        "homePages.goToId('q1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "assert.equal(homePages.goneHere(), false, 'il quaderno tornato resta segnato');\n"
        "assert.deepEqual(notices.at(-1), [panel, false]);\n",
        NOTEBOOK,
    )


def test_a_notebook_list_that_fails_marks_nothing() -> None:
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api._notebooks = 'rotto';\n"
        "homePages.goToId('q1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        + _gone_in('q1')
        + "assert.equal(notice(), undefined);\n",
        NOTEBOOK,
    )


def test_a_notebook_that_comes_back_loses_its_notice() -> None:
    """E un avviso solo, mai due: ogni arrivo ricomincia da capo."""
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api._notebooks = [];\n"
        "homePages.goToId('q1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "homePages.goTo(CHAT()); homePages.goToId('q1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        + _gone_in('q1')
        + "assert.equal(panel.children.filter((c) => c.className === 'home-page-gone').length, 1);\n"
        "api._notebooks = ['piante'];\n"
        "homePages.goTo(CHAT()); homePages.goToId('q1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "assert.equal(notice(), undefined, 'l avviso e rimasto su un quaderno tornato');\n",
        NOTEBOOK,
    )



# ── «Your home pages» non c'e' piu' (23/09/2026) ────────────────────────────


def test_the_pages_sheet_is_gone_with_every_trace_of_it() -> None:
    """Tutti i suoi mestieri si sono spostati sulla cosa: aggiungere e togliere
    dal cassetto e dalla tendina, «non c'e' piu'» sulla pagina stessa.

    Un pezzo rimasto — il markup, una chiave, un ascoltatore sui pallini — e' un
    foglio che non si apre ma che chi legge il codice crede vivo.
    """
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert "casa-pagine-dialog" not in html and "casa-foglio" not in html
    css = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    assert "casa-foglio" not in css
    pages_js = (ASSETS / "home-pages.js").read_text(encoding="utf-8")
    assert "watchHorizontalSwipe(this.striscia" not in pages_js, (
        "i pallini hanno di nuovo una pressione lunga: nessuno la troverebbe"
    )
    for language in ("it", "en"):
        entries = json.loads((ASSETS / "i18n" / f"{language}.json").read_text(encoding="utf-8"))
        assert "sheet" not in entries["home"], f"{language}: casa.sheet e' rimasto"
    app_js = (ASSETS / "home-app.js").read_text(encoding="utf-8")
    assert "casa-pagine-dialog" not in app_js


def test_the_shared_gesture_no_longer_does_a_long_press() -> None:
    """Aveva un solo chiamante, i pallini, ed e' uscita con lui."""
    src = (ASSETS / "shared" / "horizontal-swipe.js").read_text(encoding="utf-8")
    assert "PRESSIONE_LUNGA_MS" not in src and "onPressioneLunga" not in src


def test_the_chat_page_follows_a_renamed_notebook_only_if_it_was_its_own() -> None:
    """La conversazione della pagina chat non sta nell'elenco salvato: le
    pagine appese le rinomina il gateway, questa no."""
    _run(
        "homePages.homeConversation = 'project:viaggio';\n"
        "homePages.renameConversation('project:altro', 'project:nuovo');\n"
        "assert.equal(homePages.homeConversation, 'project:viaggio', 'ha seguito un altro quaderno');\n"
        "homePages.renameConversation('project:viaggio', 'project:viaggi');\n"
        "assert.equal(homePages.homeConversation, 'project:viaggi');\n"
    )


# ── Le pagine fisse si accendono quando le guardi (23/09/2026) ──────────────


def test_a_fixed_page_lights_up_when_you_arrive_and_goes_dark_when_you_leave() -> None:
    """Il cassetto prende la tastiera solo mentre lo guardi; i Quaderni e le
    Impostazioni si rileggono quando ci arrivi."""
    _run(
        "const visto = [];\n"
        "homePages.register('app', { activate: () => visto.push('app+'), deactivate: () => visto.push('app-') });\n"
        "homePages.register('notebooks', { activate: () => visto.push('q+') });\n"
        "homePages.goToId('app');\n"
        "homePages.goToId('app');\n"
        "homePages.goTo(CHAT());\n"
        "homePages.goToId('notebooks');\n"
        "homePages.goToId('app');\n"
        "assert.deepEqual(visto, ['app+', 'app-', 'q+', 'app+']);\n"
    )


def test_a_page_registered_while_you_look_at_it_lights_up_at_once() -> None:
    """Il guscio registra i ganci dopo aver costruito la pista: se la casa
    fosse gia' su quella pagina, aspettare il prossimo `goTo` la lascerebbe
    spenta."""
    _run(
        "homePages.goToId('settings');\n"
        "let active = 0;\n"
        "homePages.register('settings', { activate: () => { active += 1; } });\n"
        "assert.equal(active, 1);\n"
    )


def test_a_fixed_page_has_no_app_window_to_listen_to() -> None:
    """La finestra da cui un gesto puo' arrivare e' solo quella di un'app
    appesa: il cassetto e' un pannello del guscio, non una cornice."""
    _run(
        # Qualcosa con una finestra dentro il pannello del cassetto: senza, la
        # guardia sulla specie non si vedrebbe — un pannello vuoto non ha
        # finestre comunque, e la mutazione che la toglie sopravvivrebbe.
        "const finta = createEl(null, '');\n"
        "finta.contentWindow = { app: 'dentro-il-cassetto' };\n"
        "appPanel.appendChild(finta);\n"
        "homePages.goToId('app');\n"
        "assert.equal(homePages._pageWindow(), null);\n"
        "scrollFromApp(LEFT, { source: finta.contentWindow });\n"
        "assert.equal(homePages.index, I('app'), 'una finestra nel cassetto ha mosso la pista');\n",
        ONE,
    )
