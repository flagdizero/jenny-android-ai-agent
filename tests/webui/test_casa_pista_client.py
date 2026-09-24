"""La pista della casa: una fila di pagine, la chat fra le altre.

La casa e' un launcher (tavola `Pagine`). Quattro pagine ci sono sempre — App,
la chat, Quaderni, Impostazioni — e accanto quelle che l'utente aggiunge. Dal
23/09/2026 **si spostano tutte**, la chat compresa: nessun indice vuol dire «la
chat» da solo (v. `.agent/pagine-in-alto-plan.md`). Per questo i casi qui
parlano **per nome di pagina** (`I('p1')`, `CHAT()`) e non per numero: un
banco che scrivesse `indice === 1` proverebbe un ordine, non la pista.

**Perche' in node sui file veri.** Il modulo si importa davvero — con il suo
`shared/gesto-orizzontale.js` accanto e un finto client API — invece di
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
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "jenny" / "templates" / "ui"
ASSETS = UI / "assets"

_NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


_FINTO_DOM = """
const elementi = new Map();
function creaEl(id, cls) {
  const ascolto = {};
  const el = {
    id, ascolto,
    className: cls || '',
    style: {},
    dataset: {},
    clientWidth: 400,
    children: [],
    _testo: '',
    /* `textContent = ''` svuota i figli, come nel DOM vero: senza, i pallini
       si accumulerebbero a ogni ridisegno e il banco non lo vedrebbe. */
    set textContent(v) { this._testo = v; if (v === '') this.children = []; },
    get textContent() { return this._testo; },
    setAttribute(k, v) { this.attrs = this.attrs || {}; this.attrs[k] = v; },
    getAttribute(k) { return (this.attrs || {})[k]; },
    /* **Piu' di un ascoltatore per tipo.** Sulla striscia ce ne sono due —
       il «tira su» di questo file e la pressione lunga del modulo condiviso —
       e un finto che ne tiene uno solo li fa sovrascrivere a vicenda: il
       banco vedrebbe verde proprio sul caso in cui i due si pestano i piedi.
       Costato una stesura (22/09/2026). */
    addEventListener(t, fn) { (ascolto[t] = ascolto[t] || []).push(fn); },
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
    click() { for (const fn of this.ascolto.click || []) fn(); },
    remove() {
      if (!this.parent) return;
      this.parent.children = this.parent.children.filter((x) => x !== this);
    },
    /* Le classi come le tiene il DOM vero: un elenco di parole. */
    get classList() {
      const el = this;
      const parole = () => (el.className || '').split(' ').filter(Boolean);
      return {
        add(c) { if (!parole().includes(c)) el.className = [...parole(), c].join(' '); },
        remove(c) { el.className = parole().filter((x) => x !== c).join(' '); },
        contains(c) { return parole().includes(c); },
      };
    },
    /* Solo `.classe`, nei discendenti. Un altro selettore **alza**: rispondere
       a caso e' il modo in cui un finto dice verde su codice rotto. */
    querySelector(sel) {
      if (!/^\\.[a-z-]+$/.test(sel)) throw new Error('selettore che il finto non capisce: ' + sel);
      const cls = sel.slice(1);
      const cerca = (n) => {
        for (const c of n.children) {
          if ((c.className || '').split(' ').includes(cls)) return c;
          const r = cerca(c);
          if (r) return r;
        }
        return null;
      };
      return cerca(this);
    },
    /* Anche questo **sposta**: la pista rimette in fila i pannelli fissi, e
       un finto che li copiasse li lascerebbe in due posti. */
    insertBefore(c, rif) {
      if (c.parent) c.parent.children = c.parent.children.filter((x) => x !== c);
      const i = rif ? this.children.indexOf(rif) : -1;
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
  if (id) elementi.set(id, el);
  return el;
}
const pista = creaEl('casa-pista', 'casa-pista');
/* I pannelli delle quattro fisse ci sono gia' nell'HTML, in quest'ordine, e
   non hanno `data-id`: il controller li sposta e basta, non li crea e non li
   butta. */
function fisso(nome) {
  const p = creaEl(null, 'casa-pagina');
  p.dataset.pagina = nome;
  pista.appendChild(p);
  return p;
}
const pannelloApp = fisso('app');
const pannelloChat = fisso('chat');
const pannelloQuaderni = fisso('quaderni');
const pannelloImpostazioni = fisso('impostazioni');

/* **Un dito porta due righelli**, come quello vero: `client` e' relativo alla
   finestra di chi ascolta, `screen` allo schermo. Qui sono sfalsati di una
   costante apposta — se qualcuno tornasse a misurare col primo, o peggio
   mescolasse i due, lo scarto salterebbe fuori invece di nascondersi. Il
   modulo condiviso legge **screen**, perche' dentro una Jenny App la finestra
   e' la cornice che la pista sta trascinando (v. la sua testata). */
const SFALSO_X = 1000;
const SFALSO_Y = 500;
function dito(x, y) {
  return { clientX: x, clientY: y, screenX: x + SFALSO_X, screenY: y + SFALSO_Y };
}

globalThis.document = {
  getElementById: (id) => elementi.get(id) || null,
  createElement: (t) => creaEl(null, ''),
  documentElement: creaEl('html', ''),
  body: creaEl('body'),
};
/* La finestra ascolta davvero: da qui passano i gesti raccontati da dentro
   una Jenny App, che la pista non la tocca mai. */
globalThis.window = {
  innerWidth: 400,
  ascolto: {},
  addEventListener(t, fn) { (this.ascolto[t] = this.ascolto[t] || []).push(fn); },
};
globalThis.getComputedStyle = () => ({ overflowX: 'visible' });

/* Un gesto completo sulla pista. `verso` +1 = dito a destra (pagina
   precedente), -1 = dito a sinistra (pagina successiva). */
function lancia(el, tipo, evento) {
  for (const fn of el.ascolto[tipo] || []) fn(evento);
}

function scorri(verso, { corto = false } = {}) {
  const x0 = 200;
  // soglia = max(60, 400*0.22) = 88: corto resta sotto, lungo la supera
  const dx = verso * (corto ? 20 : 200);
  lancia(pista, 'touchstart', { touches: [dito(x0, 100)], target: pista });
  lancia(pista, 'touchmove', {
    touches: [dito(x0 + dx, 100)],
    preventDefault() {},
  });
  lancia(pista, 'touchend', { changedTouches: [dito(x0 + dx, 100)] });
}
/* Il gesto **raccontato da dentro una app**: quel che il kit
   (`apps/jenny-sdk.js`) manda al guscio dalla sua feritoia. Qui non c'e'
   nessun dito, ed e' tutto il punto: la pagina di una app e' tutta l'app, e
   il dito che la tocca al guscio non ci arriva mai. */
function daApp(dettaglio, { sorgente } = {}) {
  const e = { data: { type: 'jenny:gesto', slug: 'orto', ...dettaglio }, source: sorgente };
  for (const fn of window.ascolto.message || []) fn(e);
}
/* La sorgente buona: la finestra della cornice che si sta guardando. */
function finestraViva() {
  const pannello = pagine.pannelloDi(pagine.indice);
  return pannello?.dataset?.id && pannello.children[0] && pannello.children[0].contentWindow;
}
function scorriDaApp(verso, { corto = false, sorgente } = {}) {
  const dx = verso * (corto ? 20 : 200);
  const da = { sorgente: sorgente === undefined ? finestraViva() : sorgente };
  daApp({ fase: 'inizio' }, da);
  daApp({ fase: 'muove', dx }, da);
  daApp({
    fase: 'fine',
    verso: dx > 0 ? 'prev' : 'next',
    // soglia = max(60, 400*0.22) = 88, come la calcola il modulo condiviso
    conferma: Math.abs(dx) > 88,
  }, da);
}
/* Un dito i cui due righelli **non vanno d'accordo**. E' quel che succede
   davvero dentro una Jenny App: la cornice si sposta insieme al dito, quindi
   `client` racconta meno strada di quella fatta — o nessuna. */
function ditoSfalsato(xc, xs, y) {
  return { clientX: xc, clientY: y, screenX: xs, screenY: y + SFALSO_Y };
}
function scorriSfalsato(dxClient, dxScreen) {
  const x0 = 200;
  const a = ditoSfalsato(x0 + dxClient, x0 + dxScreen, 100);
  lancia(pista, 'touchstart', { touches: [ditoSfalsato(x0, x0, 100)], target: pista });
  lancia(pista, 'touchmove', { touches: [a], preventDefault() {} });
  lancia(pista, 'touchend', { changedTouches: [a] });
}
const DESTRA = +1;
const SINISTRA = -1;
"""


def _script(corpo: str, schermate: list[dict], vista: str = "chat") -> str:
    """Un modulo che monta il DOM finto, poi importa i file veri."""
    return (
        _FINTO_DOM
        + f"\nconst SCHERMATE = {json.dumps(schermate)};\n"
        + f"const VISTA = {json.dumps(vista)};\n"
        + textwrap.dedent(
            """
            const { CasaPagine } = await import('./casa-pagine.js');
            const guscio = creaEl('casa-shell', 'casa-shell');
            const app = {
              view: VISTA,
              shell: guscio,
              /* La modalita' ordina: finche' e' aperta il dito e' suo. */
              fila: { ordinando: false },
              /* La fonte risponde **dopo un giro**, come la rete vera: al
                 momento della domanda `jennyApps` e' ancora vuota. Un finto
                 che risponde subito avrebbe lasciato passare il difetto visto
                 sul telefono il 22 settembre 2026 — «non hai Jenny App» a chi
                 ne aveva quattro. */
              appsSource: () => ({
                ensureLoaded() {},
                jennyApps: [],
                jennyListFailed() { return globalThis.LISTA_ROTTA === true; },
                /* Chi si iscrive ai cambi dei dati delle app: il banco li
                   chiama a mano, come farebbe un frame del gateway. */
                onAppDataChanged(fn) {
                  (globalThis.DATI_APP ||= []).push(fn);
                  return () => {};
                },
                attendiJennyApps() {
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
               l'unica domanda che conta per `riportaACasa`. Il trasloco vero
               ha il suo banco (`test_casa_trasloco_client.py`). */
            const traslochi = [];
            app.trasloco = {
              lettura: Promise.resolve('letto'),
              arriva(p, k) { traslochi.push({ fa: 'arriva', p, k }); },
              fotoSeServe(p, k) { traslochi.push({ fa: 'foto', p, k }); },
              riportaACasa(v, c) {
                traslochi.push({ fa: 'casa', v, c, attaccato: pista.children.includes(v) });
              },
            };
            const arrivi = () => traslochi.filter((x) => x.fa === 'arriva');
            const PERSONALE = 'websocket:default';
            app.chiaveAttuale = () => PERSONALE;
            const mostrate = [];
            app.mostraConversazione = (k) => { mostrate.push(k); return Promise.resolve('mostrata'); };
            const pagine = new CasaPagine(app);
            await pagine.carica();
            /* Dove sta una pagina, per nome: i casi non contano caselle. */
            const I = (id) => pagine.indiceDi(id);
            const CHAT = () => pagine.indiceChat;
            """
        )
        + corpo
    )


def _run(
    corpo: str,
    schermate: list[dict] | None = None,
    vista: str = "chat",
    ordine: list[str] | None = None,
) -> None:
    """*ordine*: quello salvato sul server; `None` e' chi non ha mai spostato
    niente, e il client lo normalizza come lo schema."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        radice = Path(tmp)
        (radice / "shared").mkdir()
        shutil.copy(ASSETS / "casa-pagine.js", radice / "casa-pagine.js")
        # Le regole sui nomi dei quaderni, vere: sono le stesse del gateway, e
        # una copia finta qui direbbe si' a un nome che il server rifiuta.
        shutil.copy(
            ASSETS / "shared" / "conversation-list.js",
            radice / "shared" / "conversation-list.js",
        )
        shutil.copy(
            ASSETS / "shared" / "gesto-orizzontale.js",
            radice / "shared" / "gesto-orizzontale.js",
        )
        # Il client API finto: risponde quel che il caso vuole, e ricorda le
        # scritture. Non si tocca la rete e non si tocca `config.json`.
        # La cornice di una Jenny App: qui basta sapere **che** viene
        # costruita e con quale slug — il vero `cornicePerApp` mette il token
        # e i colori nell'indirizzo, e quello si prova dove vive.
        (radice / "shared" / "apps-actions.js").write_text(
            "export function cornicePerApp(slug) {\n"
            "  const f = document.createElement('iframe');\n"
            "  f.dataset.slug = slug;\n"
            # La feritoia: il guscio riconosce chi parla confrontando
            # **questa**, e senza il banco non vedrebbe la guardia.
            # Ricorda anche cosa le si manda: `jenny:data-changed`.
            "  f.contentWindow = { app: slug, posta: [], postMessage(m) { this.posta.push(m); } };\n"
            "  return f;\n"
            "}\n",
            encoding="utf-8",
        )
        (radice / "shared" / "i18n.js").write_text(
            "export const i18n = {\n"
            "  t: (k, v) => k + (v ? ':' + JSON.stringify(v) : ''),\n"

            "};\n",
            encoding="utf-8",
        )
        (radice / "shared" / "api-client.js").write_text(
            "export const api = {\n"
            f"  _elenco: {json.dumps(schermate or [])},\n"
            f"  _ordine: {json.dumps(ordine)},\n"
            # Le scritture si ricordano in due elenchi: cosa (le schermate) e
            # dove (l'ordine). Il server vero le prende insieme.
            "  scritture: [],\n"
            "  ordini: [],\n"
            "  async getSchermate() {\n"
            "    return { schermate: this._elenco, ordine: this._ordine, max: 8,\n"
            "             fisse: ['app', 'chat', 'quaderni', 'impostazioni'] };\n"
            "  },\n"
            "  async salvaPagine(s, o) {\n"
            "    this.scritture.push(s); this.ordini.push(o);\n"
            "    this._elenco = s; this._ordine = o;\n"
            "    return { schermate: s, ordine: o };\n"
            "  },\n"
            # Il segreto parte **vuoto**, come al primo avvio: la cornice di
            # un'app se lo porta nell'indirizzo, e chi la costruisce senza
            # aspettarlo produce un 401 e una pagina bianca.
            "  _segreto: '',\n"
            "  getSecret() { return this._segreto; },\n"
            "  async bootstrap() { this._segreto = 'ok'; },\n"
            # Un caso puo' imporre i suoi quaderni — un elenco di nomi, o
            # 'rotto' per una lettura che fallisce — e allora vale quello:
            # serve alla pagina «non c'e' piu'». Altrimenti l'elenco di sotto.
            "  _quaderni: null,\n"
            # I quaderni su disco, come li da' `/api/projects`: `projects` si
            # aprono, `unopenable` no. Rispondono **dopo un giro**, come la rete.
            "  async listProjects() {\n"
            "    if (this._quaderni === 'rotto') throw new Error('500');\n"
            "    if (this._quaderni) return { projects: this._quaderni.map((name) => ({ name })) };\n"
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
        entry = radice / "prova.mjs"
        entry.write_text(
            "import assert from 'node:assert/strict';\n"
            + _script(corpo, schermate or [], vista),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [str(_NODE), str(entry)], capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 0, proc.stderr or proc.stdout


UNA = [{"id": "p1", "kind": "app", "ref": "orto"}]
DUE = UNA + [{"id": "p2", "kind": "conversazione", "ref": "project:piante"}]
# Due pagine che si **riempiono** entrambe: un quaderno non si riempie mai — ci
# arriva la chat — quindi chi prova «resta viva solo quella che guardi» ne
# vuole due di app.
DUE_APP = UNA + [{"id": "p2", "kind": "app", "ref": "lampo"}]


# ── Le quattro fisse, e la chat fra loro ────────────────────────────────────


def test_a_fresh_home_is_the_four_fixed_pages_opened_on_jenny() -> None:
    """App · Jenny · Quaderni · Impostazioni, e si parte da Jenny."""
    _run(
        "assert.equal(pagine.quante, 4);\n"
        "assert.deepEqual(pagine.ordine, ['app', 'chat', 'quaderni', 'impostazioni']);\n"
        "assert.equal(pagine.indice, CHAT());\n"
        "assert.equal(CHAT(), 1);"
    )


def test_the_home_starts_on_the_chat_even_before_the_server_answers() -> None:
    """Nell'HTML la chat e' la seconda: senza un `transform` scritto subito il
    primo fotogramma sarebbe il cassetto."""
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api.getSchermate = () => new Promise(() => {});\n"
        "pista.style.transform = undefined;\n"
        "const altre = new CasaPagine(app);\n"
        "assert.equal(pista.style.transform, 'translateX(-100%)');\n"
        "assert.equal(altre.indice, altre.indiceChat);"
    )


def test_with_no_pages_added_there_is_still_somewhere_to_go() -> None:
    """Le fisse sono pagine vere: il dito ci va anche in una casa appena
    installata, e ai due capi si ferma."""
    _run(
        "scorri(SINISTRA);\n"
        "assert.equal(pagine.indice, I('quaderni'));\n"
        "scorri(DESTRA); scorri(DESTRA);\n"
        "assert.equal(pagine.indice, I('app'));"
    )


def test_the_fixed_panels_are_moved_never_redrawn() -> None:
    """Ridisegnarli vorrebbe dire buttare via la conversazione a ogni
    salvataggio — e il cassetto, i quaderni, le impostazioni con lei.

    Stanno nell'HTML; i pannelli aggiunti si mettono **dove dice l'ordine**, e
    l'ordine nel documento e' l'ordine a schermo.
    """
    _run(
        "const fissi = [pannelloApp, pannelloChat, pannelloQuaderni, pannelloImpostazioni];\n"
        "await pagine.salva(SCHERMATE.concat([{id:'p3',kind:'app',ref:'x'}]),\n"
        "  ['p3', 'impostazioni', 'app', 'chat', 'p1', 'p2', 'quaderni']);\n"
        "for (const f of fissi) assert.ok(pista.children.includes(f), 'un pannello fisso e stato buttato');\n"
        "assert.equal(pista.children.length, 7);\n"
        "assert.deepEqual(pista.children.map((c) => c.dataset.pagina || c.dataset.id), pagine.ordine);\n"
        "assert.equal(pista.children[3], pannelloChat);",
        schermate=DUE,
    )


def test_the_chat_can_sit_anywhere_and_the_home_still_opens_on_it() -> None:
    """Si sposta come le altre; la casa si apre comunque su di lei."""
    _run(
        "assert.deepEqual(pagine.ordine, ['p1', 'quaderni', 'app', 'impostazioni', 'chat']);\n"
        "assert.equal(pagine.indice, 4);\n"
        "assert.equal(pista.style.transform, 'translateX(-400%)');\n"
        "assert.equal(pista.children[4], pannelloChat);",
        schermate=UNA,
        ordine=["p1", "quaderni", "app", "impostazioni", "chat"],
    )


def test_a_page_missing_from_the_saved_order_goes_right_after_the_chat() -> None:
    """La stessa regola dello schema: mai un ordine che perde una pagina."""
    _run(
        "assert.deepEqual(pagine.ordine, ['quaderni', 'chat', 'p1', 'app', 'impostazioni']);",
        schermate=UNA,
        ordine=["quaderni", "chat", "app", "impostazioni"],
    )


def test_the_client_order_rule_is_the_schema_one() -> None:
    """Due copie della stessa regola, una per lato: la seconda serve quando il
    server non ha detto l'ordine. Qui si provano gli stessi casi dello schema."""
    _run(
        "const { ordineNormale } = await import('./casa-pagine.js');\n"
        "const s = [{ id: 'p1' }, { id: 'p2' }];\n"
        "assert.deepEqual(ordineNormale(null, s), ['app', 'chat', 'p1', 'p2', 'quaderni', 'impostazioni']);\n"
        "assert.deepEqual(ordineNormale(['chat', 'x', 'p1', 'chat', 7], s),\n"
        "  ['chat', 'p2', 'p1', 'app', 'quaderni', 'impostazioni']);\n"
        "assert.deepEqual(ordineNormale(['impostazioni'], []), ['impostazioni', 'app', 'chat', 'quaderni']);"
    )


# ── Dove si va ──────────────────────────────────────────────────────────────


def test_swiping_left_moves_to_the_next_page() -> None:
    _run("scorri(SINISTRA); assert.equal(pagine.indice, I('p1'));", schermate=DUE)


def test_swiping_right_comes_back() -> None:
    _run(
        "scorri(SINISTRA); scorri(SINISTRA);\n"
        "assert.equal(pagine.indice, I('p2'));\n"
        "scorri(DESTRA);\n"
        "assert.equal(pagine.indice, I('p1'));",
        schermate=DUE,
    )


def test_the_ends_do_not_wrap_around() -> None:
    """Le linguette dell'officina si richiudono in cerchio, le pagine no.

    Li' le voci sono quattro e note; qui quante siano lo decide l'utente, e
    girando in tondo fra otto pagine non si sa piu' dove si e'.
    """
    _run("scorri(DESTRA); scorri(DESTRA); assert.equal(pagine.indice, 0);", schermate=DUE)
    _run(
        "for (let i = 0; i < 9; i += 1) scorri(SINISTRA);\n"
        "assert.equal(pagine.indice, pagine.quante - 1);",
        schermate=DUE,
    )


def test_a_short_drag_springs_back() -> None:
    _run("scorri(SINISTRA, {corto: true}); assert.equal(pagine.indice, CHAT());", schermate=DUE)


# ── Le guardie ──────────────────────────────────────────────────────────────


def test_inside_a_room_the_rooms_are_in_charge() -> None:
    """Dalle pagine di un quaderno il dito non deve cambiare pagina sotto la stanza."""
    _run("scorri(SINISTRA); assert.equal(pagine.indice, CHAT());", schermate=DUE, vista="pages")


def test_while_the_pages_are_being_moved_the_finger_is_theirs() -> None:
    """In modalita' ordina il dito trascina pastiglie, non la pista."""
    _run(
        "app.fila.ordinando = true;\n"
        "scorri(SINISTRA);\n"
        "assert.equal(pagine.indice, CHAT());",
        schermate=DUE,
    )


# ── Il salvataggio ──────────────────────────────────────────────────────────


def test_saving_sends_the_whole_list(tmp_path: Path) -> None:
    """Aggiungere, togliere e spostare sono la stessa scrittura."""
    _run(
        "await pagine.salva([SCHERMATE[1]], ['p2', 'app', 'chat', 'quaderni', 'impostazioni']);\n"
        "const api = (await import('./shared/api-client.js')).api;\n"
        "assert.equal(api.scritture.length, 1);\n"
        "assert.deepEqual(api.scritture[0], [SCHERMATE[1]]);\n"
        "assert.deepEqual(api.ordini[0], ['p2', 'app', 'chat', 'quaderni', 'impostazioni']);\n"
        "assert.deepEqual(pagine.ordine, api.ordini[0]);\n"
        "assert.equal(pagine.quante, 5);",
        schermate=DUE,
    )


def test_moving_a_page_keeps_you_on_the_page_you_were_on() -> None:
    """Spostare le pagine non ti sposta: resti dove guardavi, ovunque sia finita."""
    _run(
        "pagine.vaiAId('p2');\n"
        "await pagine.salva(pagine.schermate, ['p2', 'chat', 'p1', 'app', 'quaderni', 'impostazioni']);\n"
        "assert.equal(pagine.indice, 0);\n"
        "assert.equal(pagine.voce(pagine.indice).id, 'p2');",
        schermate=DUE,
    )


def test_removing_the_page_you_are_on_takes_you_to_the_chat() -> None:
    """Non una casella che non c'e' piu', e nemmeno una vicina a caso: la chat,
    che e' dove Indietro ti porterebbe comunque."""
    _run(
        "scorri(SINISTRA); scorri(SINISTRA);\n"
        "assert.equal(pagine.indice, I('p2'));\n"
        "await pagine.salva([SCHERMATE[0]], pagine.ordine.filter((x) => x !== 'p2'));\n"
        "assert.equal(pagine.quante, 5);\n"
        "assert.equal(pagine.indice, CHAT());",
        schermate=DUE,
    )


def test_a_read_that_fails_leaves_the_chat_standing() -> None:
    """Una casa che non apre perche' non ha saputo leggere le sue pagine e'
    peggio di una casa con le sole quattro."""
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api.getSchermate = async () => { throw new Error('rete giu'); };\n"
        "const altre = new CasaPagine(app);\n"
        "await altre.carica();\n"
        "assert.equal(altre.quante, 4);\n"
        "assert.equal(altre.indice, altre.indiceChat);",
        schermate=DUE,
    )


# ── La forma nel documento ──────────────────────────────────────────────────


def test_the_chat_lives_inside_a_page_panel() -> None:
    html = (UI / "index.html").read_text(encoding="utf-8")
    pista = html.split('class="casa-pista"', 1)[1].split("</div>\n\n  <!--", 1)[0]
    for pezzo in ("casa-thread", "casa-empty", "casa-activity", "casa-composer"):
        assert pezzo in pista, f"{pezzo} e' rimasto fuori dalla pista"


def test_one_rule_hides_the_track_not_six_pieces() -> None:
    """Sei righe diventate una.

    Se ne fosse dimenticata una, quel pezzo resterebbe a occupare spazio dentro
    una stanza — ed e' il tipo di difetto che si vede solo entrando in quella
    stanza precisa.
    """
    css = (ASSETS / "casa-style.css").read_text(encoding="utf-8")
    assert ".casa-shell:not([data-view='chat']) .casa-vetrina { display: none; }" in css
    for pezzo in ("casa-thread", "casa-composer", "casa-activity"):
        assert f":not([data-view='chat']) .{pezzo}" not in css


def test_the_panel_is_positioned_so_the_empty_state_stays_put() -> None:
    """`.casa-empty` e' `position:absolute; inset:0`.

    Appena la pista prende un `transform` il contenitore di riferimento cambia
    — un elemento trasformato ne crea uno — e senza un `relative` dichiarato
    qui lo stato vuoto salterebbe a meta' gesto.
    """
    css = (ASSETS / "casa-style.css").read_text(encoding="utf-8")
    regola = css.split(".casa-pagina {", 1)[1].split("}", 1)[0]
    assert "position: relative" in regola


def test_the_track_adds_no_z_index() -> None:
    """Quello di Jenny resta l'unico: lei sta sopra la chat e sopra un'app."""
    css = (ASSETS / "casa-style.css").read_text(encoding="utf-8")
    assert len([r for r in css.splitlines() if r.strip().startswith("z-index:")]) == 1


# ── I pallini se ne sono andati (23/09/2026) ────────────────────────────────


def test_the_dots_are_gone_and_the_row_took_their_place() -> None:
    """I pallini dicevano quante pagine c'erano, non che cosa: li ha sostituiti
    la fila dei nomi in alto (`casa-fila.js`), che ha il suo banco. Qui si
    prova che non ne resta un pezzo — una striscia vuota da 26 px e' spazio
    tolto a ogni pagina per niente."""
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert "casa-pallini" not in html
    assert 'id="casa-fila"' in html
    for nome in ("casa-pagine.js", "casa-app.js", "casa-style.css"):
        assert "casa-pallin" not in (ASSETS / nome).read_text(encoding="utf-8"), nome


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
    sorgente = (ASSETS / "casa-pagine.js").read_text(encoding="utf-8")
    assert "openLauncher" not in sorgente, (
        "la striscia prova di nuovo ad aprire il cassetto: quel gesto non "
        "arriva mai all'app con la navigazione a gesti"
    )
    assert "touchend" not in sorgente, "un ascoltatore che il sistema non fa scattare"


# ── Cosa c'e' dentro una pagina ─────────────────────────────────────────────


def test_only_the_page_you_look_at_is_alive() -> None:
    """«Resta viva solo la pagina che guardi: le altre si spengono, o te le
    paghi in batteria» (tavola `PagineGestione`).

    Non «la corrente piu' le due vicine»: tre `<iframe>` che girano insieme su
    un telefono sono tre app vive.
    """
    _run(
        # `pieno` porta il **numero del tentativo**, non un `1`: serve a
        # riconoscere il proprio giro dopo un'attesa. Qui basta che sia
        # valorizzato.
        "const pannelli = () => pista.children.filter((c) => c.dataset.id);\n"
        "const vivi = () => pannelli().filter((p) => p.dataset.pieno);\n"
        "assert.equal(vivi().length, 0,\n"
        "  'una pagina si e riempita senza che nessuno la guardi');\n"
        "scorri(SINISTRA);\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "assert.equal(vivi().length, 1);\n"
        "assert.equal(vivi()[0].dataset.id, 'p1');\n"
        "scorri(SINISTRA);\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "assert.equal(vivi().length, 1, 'la pagina di prima e rimasta accesa');\n"
        "assert.equal(vivi()[0].dataset.id, 'p2');",
        schermate=DUE_APP,
    )


def test_going_back_to_the_chat_shuts_every_page_down() -> None:
    _run(
        "scorri(SINISTRA);\n"
        "scorri(DESTRA);\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "const vivi = pista.children.filter((c) => c.dataset.pieno);\n"
        "assert.equal(vivi.length, 0);",
        schermate=DUE,
    )


def test_an_app_page_mounts_that_app_frame() -> None:
    _run(
        "scorri(SINISTRA);\n"
        # Il montaggio aspetta il segreto, quindi non e' finito al ritorno
        # del gesto: un giro di eventi e c'e'.
        "await new Promise((r) => setTimeout(r, 20));\n"
        "const pagina = pista.children.find((c) => c.dataset.id === 'p1');\n"
        "assert.equal(pagina.children.length, 1);\n"
        "assert.equal(pagina.children[0].dataset.slug, 'orto');",
        schermate=DUE,
    )


def test_the_app_page_you_look_at_hears_that_its_data_changed() -> None:
    """Jenny gira un'azione dell'app in chat: la pagina di quell'app si rilegge.

    `jenny:data-changed` e' cio' che `jenny-sdk.js` ascolta. Arriva solo alla
    pagina corrente — l'unica viva — e solo se e' l'app di cui si parla.
    """
    _run(
        "pagine.vaiAId('p1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "assert.equal(globalThis.DATI_APP?.length, 1, 'la pagina app non si e iscritta');\n"
        "globalThis.DATI_APP[0]('lampo');\n"
        "assert.deepEqual(finestraViva().posta, [], 'avvisata per un\\'altra app');\n"
        "globalThis.DATI_APP[0]('orto');\n"
        "assert.deepEqual(finestraViva().posta,\n"
        "  [{ type: 'jenny:data-changed', slug: 'orto' }]);\n"
        # Una seconda pagina app non iscrive una seconda volta: lo stesso
        # frame arriverebbe due volte alla stessa cornice.
        "pagine.vaiAId('p2');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "assert.equal(globalThis.DATI_APP.length, 1, 'iscritta due volte');\n",
        DUE_APP,
    )


def test_a_notebook_page_mounts_no_app() -> None:
    """Una pagina quaderno non deve montare una cornice d'app per sbaglio.

    Ci arriva la chat, portata dal trasloco. Un `<iframe>` su
    `/apps/project:piante/index.html` sarebbe un 404 a tutta pagina, e da fuori
    somiglierebbe a un'app che non parte.
    """
    _run(
        "scorri(SINISTRA); scorri(SINISTRA);\n"
        "const pagina = pista.children.find((c) => c.dataset.id === 'p2');\n"
        "assert.ok(!pagina.children.some((c) => c.dataset.slug), 'monta una cornice di app');",
        schermate=DUE,
    )


def test_the_name_of_a_page_comes_from_its_kind() -> None:
    _run(
        "assert.equal(pagine.nomeDi({kind: 'app', ref: 'orto'}), 'orto');\n"
        "assert.equal(pagine.nomeDi(null), '', 'senza pagina il titolo deve restare vuoto');"
    )


def test_the_shell_says_which_page_is_on_from_the_first_frame() -> None:
    """Come `data-view`, e per lo stesso motivo.

    Su una pagina di lato la vista **e' ancora** `chat`: le regole scritte solo
    su `data-view` lasciavano acceso il chevron della tendina sopra il nome di
    un'app — un comando che si vede, e' disabilitato, e non fa niente. Serviva
    un secondo segnale, e sta dove sta l'altro.
    """
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert '<main class="casa-shell" data-view="chat" data-pagina="chat">' in html, (
        "il guscio non nasce dichiarando su che pagina e'"
    )
    app_js = (ASSETS / "casa-app.js").read_text(encoding="utf-8")
    cambio = app_js.split("onPaginaCambiata(", 1)[1].split("_applyHead();", 1)[0]
    assert "data-pagina" in cambio, "l'attributo non viene aggiornato al cambio pagina"


def test_the_app_frame_is_not_built_without_the_secret() -> None:
    """Il token viaggia **nell'indirizzo** della cornice.

    Costruirla prima che il segreto ci sia vuol dire `token=undefined`, cioe'
    un 401 e una pagina bianca. `openApp` questa guardia ce l'ha da sempre; si
    era persa estraendo la cornice, e qui si prova che c'e' di nuovo.
    """
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "assert.equal(api.getSecret(), '', 'il finto parte senza segreto');\n"
        "scorri(SINISTRA);\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "assert.equal(api.getSecret(), 'ok', 'non ha atteso il segreto');\n"
        "const pagina = pista.children.find((c) => c.dataset.id === 'p1');\n"
        "assert.equal(pagina.children.length, 1, 'la cornice non e stata montata');",
        schermate=DUE,
    )


def test_a_fast_finger_does_not_mount_two_frames() -> None:
    """Segnare la pagina come piena **prima** dell'attesa.

    Senza, due `vaiA` ravvicinati entrano tutti e due nel montaggio e la
    pagina finisce con due cornici — due volte la stessa app, viva due volte.
    """
    _run(
        "scorri(SINISTRA);\n"
        "pagine.vaiA(CHAT()); pagine.vaiAId('p1'); pagine.vaiA(CHAT()); pagine.vaiAId('p1');\n"
        "await new Promise((r) => setTimeout(r, 30));\n"
        "const pagina = pista.children.find((c) => c.dataset.id === 'p1');\n"
        "assert.equal(pagina.children.length, 1, `cornici montate: ${pagina.children.length}`);",
        schermate=DUE,
    )


def test_a_page_already_full_is_not_filled_again() -> None:
    """La guardia d'ingresso guarda **se** e' pieno, non se vale `1`.

    Da quando `pieno` porta il numero del tentativo, un confronto con `'1'`
    lascerebbe passare ogni rientro dal secondo in poi.
    """
    _run(
        "scorri(SINISTRA);\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "const pagina = pista.children.find((c) => c.dataset.id === 'p1');\n"
        "const segno = pagina.dataset.pieno;\n"
        "await pagine._riempi(pagina);\n"
        "await pagine._riempi(pagina);\n"
        "assert.equal(pagina.dataset.pieno, segno, 'il segno e cambiato: e rientrato');\n"
        "assert.equal(pagina.children.length, 1, `cornici: ${pagina.children.length}`);",
        schermate=DUE,
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
    css = (ASSETS / "casa-style.css").read_text(encoding="utf-8")
    vetrina = css.split("\n.casa-vetrina {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden" in vetrina, "l'involucro non ritaglia piu'"
    pista = css.split("\n.casa-pista {", 1)[1].split("}", 1)[0]
    assert "overflow" not in pista, (
        "la pista ritaglia di nuovo: con il transform addosso, un iframe dentro "
        "una pagina si carica e non dipinge"
    )
    guscio = css.split(".casa-shell {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden" not in guscio, (
        "il ritaglio e' salito al guscio: taglia Jenny, che sporge apposta"
    )
    html = (UI / "index.html").read_text(encoding="utf-8")
    i = html.index('class="casa-vetrina"')
    j = html.index('class="casa-pista"')
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
        "pagine.vaiAId('p1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "assert.ok(finestraViva(), 'la cornice non ha una finestra');\n"
        "scorriDaApp(DESTRA);\n"
        "assert.equal(pagine.indice, CHAT(), 'da dentro l app non si torna alla chat');\n",
        UNA,
    )


def test_a_short_forwarded_gesture_stays_put() -> None:
    """Sotto la soglia si torna dov'eri: la soglia la calcola la app, col suo
    schermo, ed e' la stessa del modulo condiviso."""
    _run(
        "pagine.vaiAId('p1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "scorriDaApp(DESTRA, { corto: true });\n"
        "assert.equal(pagine.indice, I('p1'));\n",
        UNA,
    )


def test_only_the_page_you_are_looking_at_may_move_the_track() -> None:
    """Chi parla si riconosce dalla finestra, non dal fatto che parli.

    Quel che arriva da un frame e' scritto da codice dell'app: senza questa
    guardia, qualunque cosa sappia fare `postMessage` muoverebbe la casa.
    """
    _run(
        "pagine.vaiAId('p1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "scorriDaApp(DESTRA, { sorgente: { app: 'qualcun-altro' } });\n"
        "assert.equal(pagine.indice, I('p1'), 'una finestra estranea ha mosso la pista');\n",
        UNA,
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
        "pagine.vaiAId('p2');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "scorriDaApp(DESTRA, { sorgente: null });\n"
        "assert.equal(pagine.indice, I('p2'), 'un messaggio senza sorgente ha mosso la pista');\n",
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
        "pagine.vaiAId('p1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "app.fila.ordinando = true;\n"
        "scorriDaApp(DESTRA);\n"
        "assert.equal(pagine.indice, I('p1'), 'con le pagine da spostare la pista si e mossa');\n"
        "app.fila.ordinando = false;\n"
        "scorriDaApp(DESTRA);\n"
        "assert.equal(pagine.indice, CHAT(), 'chiusa la modalita, il gesto non torna');\n",
        UNA,
    )


def test_a_forwarded_drag_without_its_start_moves_nothing() -> None:
    """Un `muove` orfano userebbe una larghezza mai misurata."""
    _run(
        "pagine.vaiAId('p1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "pista.style.transform = 'segno';\n"
        "daApp({ fase: 'muove', dx: 120 }, { sorgente: finestraViva() });\n"
        "assert.equal(pista.style.transform, 'segno', 'un muove orfano ha mosso la pista');\n",
        UNA,
    )


def test_a_broken_number_never_reaches_the_track() -> None:
    """`translateX(NaN)` e la pista sparisce — e i numeri li scrive l'app."""
    _run(
        "pagine.vaiAId('p1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "const da = { sorgente: finestraViva() };\n"
        "daApp({ fase: 'inizio' }, da);\n"
        "pista.style.transform = 'segno';\n"
        "daApp({ fase: 'muove', dx: 'boh' }, da);\n"
        "assert.equal(pista.style.transform, 'segno', 'un dx non numerico e passato');\n",
        UNA,
    )


def test_a_cancelled_forwarded_gesture_snaps_back() -> None:
    """Il sistema si riprende il gesto a meta': la pagina resta quella."""
    _run(
        "pagine.vaiAId('p1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "const da = { sorgente: finestraViva() };\n"
        "daApp({ fase: 'inizio' }, da);\n"
        "daApp({ fase: 'muove', dx: 150 }, da);\n"
        "daApp({ fase: 'annulla' }, da);\n"
        "assert.equal(pagine.indice, I('p1'));\n"
        "assert.equal(pista.style.transform, `translateX(${-I('p1') * 100}%)`, 'non e tornata a posto');\n",
        UNA,
    )


# ── Quale righello ─────────────────────────────────────────────────────────


def test_the_finger_is_measured_against_the_screen() -> None:
    """La cornice si sposta insieme al dito, quindi il suo righello mente.

    Dentro una Jenny App la finestra di chi ascolta **e'** la cornice che la
    pista sta trascinando: al limite il dito si muove di 200 e `client` dice
    zero. L'utente lo ha visto come una vibrazione — avanti, indietro, avanti —
    e la misura su Chrome del telefono l'ha confermato riga per riga
    (22/09/2026, v. la testata di `shared/gesto-orizzontale.js`).
    """
    _run(
        "scorriSfalsato(0, -200);\n"
        "assert.equal(pagine.indice, I('p1'), 'col righello dello schermo non si e mossa');\n",
        UNA,
    )


def test_a_finger_that_only_the_window_saw_moves_nothing() -> None:
    """L'altro verso della stessa regola, ed e' quello che uccide la mutazione.

    Se si tornasse a misurare con la finestra, questo gesto — 200 per lei,
    zero per lo schermo — cambierebbe pagina pur non essendo mai esistito.
    """
    _run(
        "scorriSfalsato(-200, 0);\n"
        "assert.equal(pagine.indice, CHAT(), 'un gesto che lo schermo non ha visto ha cambiato pagina');\n",
        UNA,
    )


# ── Le pagine conversazione (23/09/2026) ────────────────────────────────────
#
# Una scorciatoia che cambia la conversazione dell'unica chat, travestita da
# pagina. Qui si prova l'aggancio: che le pagine chiamino il trasloco nei
# momenti giusti, con i pannelli giusti, e la regola che le tiene insieme —
# **una pagina quaderno mostra solo il suo quaderno**. Il trasloco vero, con
# la foto e i suoi tranelli, ha il banco suo.

MISTE = [
    {"id": "a1", "kind": "app", "ref": "orto"},
    {"id": "q1", "kind": "conversazione", "ref": "project:piante"},
    {"id": "a2", "kind": "app", "ref": "lampo"},
]


def test_arriving_on_a_notebook_page_brings_the_chat_there() -> None:
    """Col pannello che la pista mostra davvero, e con la chiave del quaderno."""
    _run(
        "traslochi.length = 0;\n"
        "pagine.vaiAId('q1');\n"
        "const a = arrivi();\n"
        "assert.equal(a.length, 1);\n"
        "assert.equal(a[0].k, 'project:piante');\n"
        "assert.equal(a[0].p, pagine.pannelloDi(I('q1')));\n"
        "assert.equal(a[0].p.dataset.id, 'q1');\n",
        MISTE,
    )


def test_back_on_the_chat_page_the_chat_goes_home_with_its_own_conversation() -> None:
    """La pagina chat ha la sua, e il trasloco la rimette."""
    _run(
        "pagine.vaiAId('q1');\n"
        "traslochi.length = 0;\n"
        "pagine.vaiA(CHAT());\n"
        "const a = arrivi();\n"
        "assert.equal(a.length, 1);\n"
        "assert.equal(a[0].p, pannelloChat);\n"
        "assert.equal(a[0].k, PERSONALE);\n",
        MISTE,
    )


def test_passing_through_apps_does_not_touch_the_chat() -> None:
    """Attraversarle non cambia conversazione: la chat resta dov'era."""
    _run(
        "traslochi.length = 0;\n"
        "pagine.vaiAId('a1'); pagine.vaiAId('a2');\n"
        "assert.equal(arrivi().length, 0, 'una pagina senza conversazione ha chiamato il trasloco');\n",
        MISTE,
    )


def test_a_notebook_page_off_screen_keeps_its_photo_and_is_never_emptied() -> None:
    """Spenta, ma non vuota: la foto e' quel che si vede entrare scorrendo.

    E soprattutto non si svuota col `textContent = ''` delle altre: se la
    chat e' parcheggiata li' mentre guardi un'app, porterebbe via la chat.
    """
    _run(
        "const q = pagine.pannelloDi(I('q1'));\n"
        "const parcheggiata = creaEl(null, 'casa-chat');\n"
        "q.appendChild(parcheggiata);\n"
        "traslochi.length = 0;\n"
        "pagine.vaiAId('a1');\n"
        "const f = traslochi.filter((x) => x.fa === 'foto');\n"
        "assert.equal(f.length, 1);\n"
        "assert.equal(f[0].p, q);\n"
        "assert.equal(f[0].k, 'project:piante');\n"
        "assert.ok(q.children.includes(parcheggiata), 'la pagina quaderno e stata svuotata');\n",
        MISTE,
    )


def test_redrawing_the_pages_takes_the_chat_back_before_throwing_a_panel() -> None:
    """Salvare l'elenco ridisegna i pannelli: la chat torna a casa **prima**.

    Il finto registra se il pannello era ancora attaccato al momento della
    domanda: dopo, sarebbe troppo tardi — la chat sarebbe gia' andata via con
    lui.
    """
    _run(
        "traslochi.length = 0;\n"
        "await pagine.salva(SCHERMATE);\n"
        "const c = traslochi.filter((x) => x.fa === 'casa');\n"
        "assert.equal(c.length, 3, 'non ha chiesto per ogni pannello');\n"
        "assert.ok(c.every((x) => x.attaccato), 'ha chiesto dopo aver buttato il pannello');\n"
        "assert.ok(c.every((x) => x.c === pannelloChat), 'la casa e il pannello della chat');\n",
        MISTE,
    )


def test_a_notebook_page_is_named_after_its_notebook() -> None:
    """Il nome, non la chiave: `project:piante` in testa sarebbe gergo."""
    _run(
        "assert.equal(pagine.nomeDi(SCHERMATE[1]), 'piante');\n",
        MISTE,
    )


# ── Da fuori: i Quaderni, Home, un avviso ────────────────────────────────────


def test_from_the_chat_page_a_notebook_opens_right_there() -> None:
    """Anche se ha una pagina sua. «Fai come ora, non scorrere» — l'utente, 23/09."""
    _run(
        "const r = await pagine.apriConversazione('project:piante');\n"
        "assert.deepEqual(mostrate, ['project:piante']);\n"
        "assert.equal(pagine.indice, CHAT(), 'e scorso alla pagina del quaderno');\n"
        "assert.equal(pagine.conversazioneCasa, 'project:piante');\n"
        "assert.equal(r, 'mostrata', 'la promessa del cambio non torna a chi chiama');\n",
        MISTE,
    )


def test_a_notebook_page_only_ever_shows_its_notebook() -> None:
    """**L'invariante.** Da una pagina quaderno un'altra conversazione si apre
    nella pagina chat, e ci si va.

    E chi chiama riceve la lettura del trasloco, non un «fatto» anticipato: ci
    manda subito dopo un messaggio, e deve finire nella conversazione giusta.
    """
    _run(
        "pagine.vaiAId('q1');\n"
        "traslochi.length = 0;\n"
        "const r = await pagine.apriConversazione(PERSONALE);\n"
        "assert.equal(pagine.indice, CHAT());\n"
        "assert.equal(pagine.conversazioneCasa, PERSONALE);\n"
        "assert.deepEqual(mostrate, [], 'ha cambiato la chat dentro la pagina del quaderno');\n"
        "const a = arrivi();\n"
        "assert.equal(a.length, 1);\n"
        "assert.equal(a[0].p, pannelloChat);\n"
        "assert.equal(a[0].k, PERSONALE);\n"
        "assert.equal(r, 'letto', 'chi chiama non aspetta la lettura');\n",
        MISTE,
    )


def test_asking_a_notebook_page_for_its_own_notebook_stays_there() -> None:
    """«Parlane» e «Segnala» dalle pagine del quaderno chiedono proprio quello."""
    _run(
        "pagine.vaiAId('q1');\n"
        "await pagine.apriConversazione('project:piante');\n"
        "assert.equal(pagine.indice, I('q1'));\n"
        "assert.deepEqual(mostrate, ['project:piante']);\n"
        "assert.equal(pagine.conversazioneCasa, PERSONALE, 'la pagina chat ha preso il quaderno');\n",
        MISTE,
    )


def test_from_an_app_page_a_conversation_opens_on_the_chat_page() -> None:
    """Home da una pagina di lato: si torna alla chat, come ogni launcher."""
    _run(
        "pagine.vaiAId('a1');\n"
        "await pagine.apriConversazione(PERSONALE);\n"
        "assert.equal(pagine.indice, CHAT());\n"
        "assert.deepEqual(mostrate, []);\n",
        MISTE,
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
    css = (ASSETS / "casa-style.css").read_text(encoding="utf-8")
    pista = css.split(".casa-pista {", 1)[1].split("}", 1)[0]
    # La dichiarazione, non la parola: il commento sopra la nomina, e un
    # `in` sul testo del blocco era verde anche togliendo la riga — l'ha detto
    # la mutazione.
    assert re.search(r"^\s*min-width:\s*0\s*;", pista, re.M), (
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
        "const fatto = await pagine.appendi('app', 'orto');\n"
        "assert.equal(fatto, true);\n"
        "const api = (await import('./shared/api-client.js')).api;\n"
        "assert.deepEqual(api.scritture.at(-1).map((s) => [s.kind, s.ref]), [['app', 'orto']]);\n"
        "const nuova = api.scritture.at(-1)[0].id;\n"
        "assert.equal(pagine.indice, I(nuova), 'non si e atterrati sulla pagina nuova');\n"
        "assert.equal(I(nuova), CHAT() + 1, 'la prima pagina aggiunta non sta dopo la chat');\n"
        "assert.equal(pagine.appesa('app', 'orto'), true);\n"
    )


def test_a_new_page_goes_after_the_last_one_added() -> None:
    """Le aggiunte restano vicine fra loro, anche dopo che l'utente ha spostato
    le fisse — e anche se le ha messe **prima** della chat."""
    _run(
        "await pagine.appendi('app', 'lampo');\n"
        "const api = (await import('./shared/api-client.js')).api;\n"
        "const nuova = api.scritture.at(-1).at(-1).id;\n"
        "assert.deepEqual(pagine.ordine, ['p1', nuova, 'impostazioni', 'chat', 'app', 'quaderni']);\n",
        UNA,
        ordine=["p1", "impostazioni", "chat", "app", "quaderni"],
    )


def test_pinning_twice_is_one_page() -> None:
    """Due pagine sulla stessa cosa sono una di troppo."""
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "const prima = api.scritture.length;\n"
        "assert.equal(await pagine.appendi('app', 'orto'), false);\n"
        "assert.equal(api.scritture.length, prima, 'ha scritto una pagina doppia');\n"
        "assert.equal(pagine.quante, 5);\n",
        UNA,
    )


def test_with_the_ceiling_full_nothing_is_pinned() -> None:
    piene = [{"id": f"p{i}", "kind": "app", "ref": f"x{i}"} for i in range(8)]
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "assert.equal(await pagine.appendi('app', 'orto'), false);\n"
        "assert.equal(api.scritture.length, 0, 'ha scritto oltre il tetto');\n",
        piene,
    )


def test_the_same_ref_under_another_kind_is_another_page() -> None:
    """`appesa` guarda specie **e** riferimento: un'app e un quaderno possono
    chiamarsi uguale senza essere la stessa pagina."""
    _run(
        "assert.equal(pagine.appesa('app', 'orto'), true);\n"
        "assert.equal(pagine.appesa('conversazione', 'orto'), false);\n",
        UNA,
    )


def test_unpinning_saves_the_rest() -> None:
    _run(
        "assert.equal(await pagine.stacca('app', 'orto'), true);\n"
        "const api = (await import('./shared/api-client.js')).api;\n"
        "assert.deepEqual(api.scritture.at(-1).map((s) => s.id), ['p2']);\n"
        "assert.ok(!api.ordini.at(-1).includes('p1'), 'la pagina staccata e rimasta nell ordine');\n"
        "assert.equal(await pagine.stacca('app', 'orto'), false, 'ha staccato due volte');\n",
        DUE,
    )


def test_rereading_keeps_you_on_your_page_when_it_is_still_there() -> None:
    """Dopo una cancellazione fatta altrove la tua pagina puo' esserci ancora:
    rileggere non deve riportarti alla chat, come farebbe `carica()`."""
    _run(
        "pagine.vaiAId('p2');\n"
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api._elenco = [SCHERMATE[1]];\n"
        "await pagine.ricarica();\n"
        "assert.equal(pagine.quante, 5);\n"
        "assert.equal(pagine.indice, I('p2'), 'non e rimasta sulla sua pagina');\n"
        "assert.equal(pagine.schermate[0].id, 'p2');\n",
        DUE,
    )


def test_rereading_when_your_page_is_gone_takes_you_to_the_chat() -> None:
    _run(
        "pagine.vaiAId('p2');\n"
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api._elenco = [SCHERMATE[0]];\n"
        "await pagine.ricarica();\n"
        "assert.equal(pagine.quante, 5);\n"
        "assert.equal(pagine.indice, CHAT());\n",
        DUE,
    )


def test_pinning_from_a_sheet_closes_what_is_above_first() -> None:
    """Si appende dal cassetto o dalla tendina: atterrare sotto un cassetto
    aperto vorrebbe dire non vedere di aver fatto niente. E il giro che chiude
    gli strati ha un tetto, perche' `_closeOverlays` torna vero anche quando
    delega la chiusura."""
    app_js = (ASSETS / "casa-app.js").read_text(encoding="utf-8")
    porta = app_js.split("portaPagine() {", 1)[1].split("\n  }\n", 1)[0]
    appendi = porta.split("appendi:", 1)[1].split("stacca:", 1)[0]
    assert "this._closeOverlays()" in appendi
    assert appendi.index("_closeOverlays") < appendi.index("this.pagine.appendi")
    assert "i < 8" in appendi, "il giro che chiude gli strati non ha piu' un tetto"


# ── La pagina di una cosa che non c'e' piu' (23/09/2026) ────────────────────
#
# Cancellata dalla sua scheda, la cosa si porta via la pagina (lo fa il
# gateway). Sparita per altre strade, la pagina resta — toglierla da se' sarebbe
# una decisione presa al posto dell'utente — e lo dice, con il bottone per
# toglierla. Col foglio delle pagine andato, e' l'unico posto da cui si toglie
# una pagina verso un quaderno che nella tendina non c'e' piu'.

SPARITA = [{"id": "g1", "kind": "app", "ref": "svanita"}]
QUADERNO = [{"id": "q1", "kind": "conversazione", "ref": "project:piante"}]


def _sparita_in(pagina: str) -> str:
    return (
        f"const pannello = pagine.pannelloDi(I('{pagina}'));\n"
        "const avviso = () => pannello.children.find((c) => c.className === 'casa-pagina-sparita');\n"
    )


def test_an_app_that_is_gone_says_so_instead_of_a_blank_frame() -> None:
    _run(
        "pagine.vaiAId('g1');\n"
        "await new Promise((r) => setTimeout(r, 60));\n"
        + _sparita_in('g1')
        + "assert.ok(avviso(), 'nessun avviso sulla pagina di un app sparita');\n"
        "assert.ok(!pannello.children.some((c) => c.dataset?.slug), 'la cornice verso il nulla e rimasta');\n"
        "assert.match(avviso().children[0].textContent, /casa\\.pagine\\.appSparita.*svanita/);\n",
        SPARITA,
    )


def test_the_gone_page_can_remove_itself() -> None:
    _run(
        "pagine.vaiAId('g1');\n"
        "await new Promise((r) => setTimeout(r, 60));\n"
        + _sparita_in('g1')
        + "avviso().children[1].click();\n"
        "await new Promise((r) => setTimeout(r, 10));\n"
        "const api = (await import('./shared/api-client.js')).api;\n"
        "assert.deepEqual(api.scritture.at(-1), []);\n"
        "assert.equal(pagine.quante, 4);\n",
        SPARITA,
    )


def test_an_app_that_is_there_mounts_as_always() -> None:
    _run(
        "pagine.vaiAId('p1');\n"
        "await new Promise((r) => setTimeout(r, 60));\n"
        + _sparita_in('p1')
        + "assert.equal(avviso(), undefined);\n"
        "assert.equal(pannello.children[0].dataset.slug, 'orto');\n",
        UNA,
    )


def test_a_list_that_could_not_be_read_marks_nothing() -> None:
    """«Non lo so» non e' «sparita»: con la lista rotta, dire a un'app che
    c'e' che non c'e' piu' sarebbe peggio di tacere."""
    _run(
        "globalThis.LISTA_ROTTA = true;\n"
        "pagine.vaiAId('g1');\n"
        "await new Promise((r) => setTimeout(r, 60));\n"
        + _sparita_in('g1')
        + "assert.equal(avviso(), undefined);\n"
        "assert.equal(pannello.children[0].dataset.slug, 'svanita');\n",
        SPARITA,
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
        "pagine.vaiAId('g1');\n"
        "await new Promise((r) => setTimeout(r, 5));\n"
        "assert.ok(pagine.pannelloDi(I('g1')).children.some((c) => c.dataset?.slug), 'la cornice non e montata');\n"
        "pagine.vaiA(CHAT());\n"
        "await new Promise((r) => setTimeout(r, 60));\n"
        + _sparita_in('g1')
        + "assert.equal(avviso(), undefined);\n",
        SPARITA,
    )


def test_a_notebook_that_is_gone_is_covered_not_emptied() -> None:
    """Sopra la chat e non al suo posto: sotto c'e' il trasloco, con le sue
    regole. E coprendola non si scrive a una conversazione sparita."""
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api._quaderni = ['altro'];\n"
        "pagine.vaiAId('q1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        + _sparita_in('q1')
        + "assert.ok(avviso(), 'nessun avviso sulla pagina di un quaderno sparito');\n"
        "assert.match(avviso().children[0].textContent, /casa\\.pagine\\.quadernoSparito.*piante/);\n",
        QUADERNO,
    )


def test_a_notebook_that_is_there_is_left_alone() -> None:
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api._quaderni = ['piante'];\n"
        "pagine.vaiAId('q1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        + _sparita_in('q1')
        + "assert.equal(avviso(), undefined);\n",
        QUADERNO,
    )


def test_a_notebook_list_that_fails_marks_nothing() -> None:
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api._quaderni = 'rotto';\n"
        "pagine.vaiAId('q1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        + _sparita_in('q1')
        + "assert.equal(avviso(), undefined);\n",
        QUADERNO,
    )


def test_a_notebook_that_comes_back_loses_its_notice() -> None:
    """E un avviso solo, mai due: ogni arrivo ricomincia da capo."""
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api._quaderni = [];\n"
        "pagine.vaiAId('q1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "pagine.vaiA(CHAT()); pagine.vaiAId('q1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        + _sparita_in('q1')
        + "assert.equal(pannello.children.filter((c) => c.className === 'casa-pagina-sparita').length, 1);\n"
        "api._quaderni = ['piante'];\n"
        "pagine.vaiA(CHAT()); pagine.vaiAId('q1');\n"
        "await new Promise((r) => setTimeout(r, 20));\n"
        "assert.equal(avviso(), undefined, 'l avviso e rimasto su un quaderno tornato');\n",
        QUADERNO,
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
    css = (ASSETS / "casa-style.css").read_text(encoding="utf-8")
    assert "casa-foglio" not in css
    pagine_js = (ASSETS / "casa-pagine.js").read_text(encoding="utf-8")
    assert "osservaGestoOrizzontale(this.striscia" not in pagine_js, (
        "i pallini hanno di nuovo una pressione lunga: nessuno la troverebbe"
    )
    for lingua in ("it", "en"):
        voci = json.loads((ASSETS / "i18n" / f"{lingua}.json").read_text(encoding="utf-8"))
        assert "foglio" not in voci["casa"], f"{lingua}: casa.foglio e' rimasto"
    app_js = (ASSETS / "casa-app.js").read_text(encoding="utf-8")
    assert "casa-pagine-dialog" not in app_js


def test_the_shared_gesture_no_longer_does_a_long_press() -> None:
    """Aveva un solo chiamante, i pallini, ed e' uscita con lui."""
    src = (ASSETS / "shared" / "gesto-orizzontale.js").read_text(encoding="utf-8")
    assert "PRESSIONE_LUNGA_MS" not in src and "onPressioneLunga" not in src


def test_the_chat_page_follows_a_renamed_notebook_only_if_it_was_its_own() -> None:
    """La conversazione della pagina chat non sta nell'elenco salvato: le
    pagine appese le rinomina il gateway, questa no."""
    _run(
        "pagine.conversazioneCasa = 'project:viaggio';\n"
        "pagine.rinominaConversazione('project:altro', 'project:nuovo');\n"
        "assert.equal(pagine.conversazioneCasa, 'project:viaggio', 'ha seguito un altro quaderno');\n"
        "pagine.rinominaConversazione('project:viaggio', 'project:viaggi');\n"
        "assert.equal(pagine.conversazioneCasa, 'project:viaggi');\n"
    )


# ── Le pagine fisse si accendono quando le guardi (23/09/2026) ──────────────


def test_a_fixed_page_lights_up_when_you_arrive_and_goes_dark_when_you_leave() -> None:
    """Il cassetto prende la tastiera solo mentre lo guardi; i Quaderni e le
    Impostazioni si rileggono quando ci arrivi."""
    _run(
        "const visto = [];\n"
        "pagine.registra('app', { accendi: () => visto.push('app+'), spegni: () => visto.push('app-') });\n"
        "pagine.registra('quaderni', { accendi: () => visto.push('q+') });\n"
        "pagine.vaiAId('app');\n"
        "pagine.vaiAId('app');\n"
        "pagine.vaiA(CHAT());\n"
        "pagine.vaiAId('quaderni');\n"
        "pagine.vaiAId('app');\n"
        "assert.deepEqual(visto, ['app+', 'app-', 'q+', 'app+']);\n"
    )


def test_a_page_registered_while_you_look_at_it_lights_up_at_once() -> None:
    """Il guscio registra i ganci dopo aver costruito la pista: se la casa
    fosse gia' su quella pagina, aspettare il prossimo `vaiA` la lascerebbe
    spenta."""
    _run(
        "pagine.vaiAId('impostazioni');\n"
        "let accesa = 0;\n"
        "pagine.registra('impostazioni', { accendi: () => { accesa += 1; } });\n"
        "assert.equal(accesa, 1);\n"
    )


def test_a_fixed_page_has_no_app_window_to_listen_to() -> None:
    """La finestra da cui un gesto puo' arrivare e' solo quella di un'app
    appesa: il cassetto e' un pannello del guscio, non una cornice."""
    _run(
        # Qualcosa con una finestra dentro il pannello del cassetto: senza, la
        # guardia sulla specie non si vedrebbe — un pannello vuoto non ha
        # finestre comunque, e la mutazione che la toglie sopravvivrebbe.
        "const finta = creaEl(null, '');\n"
        "finta.contentWindow = { app: 'dentro-il-cassetto' };\n"
        "pannelloApp.appendChild(finta);\n"
        "pagine.vaiAId('app');\n"
        "assert.equal(pagine._finestraPagina(), null);\n"
        "scorriDaApp(SINISTRA, { sorgente: finta.contentWindow });\n"
        "assert.equal(pagine.indice, I('app'), 'una finestra nel cassetto ha mosso la pista');\n",
        UNA,
    )
