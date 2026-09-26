"""Il trasloco della chat: una chat sola, che si sposta fra le pagine.

Una pagina «conversazione» non e' una seconda chat: e' una scorciatoia che
cambia la conversazione dell'unica che c'e', travestita da pagina. Il
travestimento e' una **foto**: nelle pagine di chat che la chat non abita c'e'
una copia statica di com'era, e all'arrivo la chat vera ci scivola sotto (v.
`home-move.js` e `.agent/pagine-conversazione-plan.md`).

**Perche' in node sul file vero, con un DOM finto fatto apposta.** Quel che
questo modulo fa e' spostare, copiare e cercare nodi: un finto che
approssimasse una di queste tre cose direbbe verde sul difetto che deve
prendere. Quindi il finto qui sposta davvero (un nodo sta in un posto solo),
copia davvero (attributi, classi, valore, figli — ma non gli ascoltatori), e
`getElementById` cerca **in ordine di documento**: e' l'unico modo di vedere la
trappola degli id rimasti nella foto, che nel DOM vero fa scrivere un
controller dentro una copia inerte. Un selettore che il finto non capisce
alza, invece di rispondere a caso.

Quel che non prova: come si vede. Lo scorrimento lo prova il telefono.
"""

from __future__ import annotations

import shutil
import tempfile
import textwrap
from pathlib import Path

from support.js_harness import requires_node, run_module

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

pytestmark = requires_node


_DOM = r"""
/* ── Un DOM piccolo, ma che non mente sulle tre cose che contano ── */
function matches(el, sel) {
  return sel.split(',').map((s) => s.trim()).some((s) => {
    if (s.startsWith('.')) return el.classList.contains(s.slice(1));
    const attr = s.match(/^\[([a-z-]+)\]$/);
    if (attr) return el.hasAttribute(attr[1]);
    if (/^[a-z]+$/.test(s)) return el.tagName === s.toUpperCase();
    throw new Error('selettore che il finto non capisce: ' + s);
  });
}
function descendants(el) {
  const all = [];
  for (const c of el.children) all.push(c, ...descendants(c));
  return all;
}
function create(tag, { id, cls, text } = {}) {
  const el = {
    tagName: tag.toUpperCase(),
    children: [],
    parentElement: null,
    attrs: {},
    classes: new Set((cls || '').split(' ').filter(Boolean)),
    value: '',
    text: text || '',
    scrollTop: 0,
    get scrollHeight() { return 1000 + this.children.length * 10; },
    get id() { return this.attrs.id || ''; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    hasAttribute(k) { return k in this.attrs; },
    removeAttribute(k) { delete this.attrs[k]; },
    classList: null,
    appendChild(c) { return this.insertBefore(c, null); },
    /* **Sposta**: un nodo sta in un posto solo. */
    insertBefore(c, ref) {
      if (c.parentElement) c.parentElement.children = c.parentElement.children.filter((x) => x !== c);
      const i = ref ? this.children.indexOf(ref) : -1;
      if (i < 0) this.children.push(c); else this.children.splice(i, 0, c);
      c.parentElement = this;
      return c;
    },
    remove() {
      if (!this.parentElement) return;
      this.parentElement.children = this.parentElement.children.filter((x) => x !== this);
      this.parentElement = null;
    },
    /* **Copia davvero**: attributi, classi, valore, figli. Non gli
       ascoltatori, come il DOM vero. */
    cloneNode(deep) {
      const c = create(tag, { text: this.text });
      c.attrs = { ...this.attrs };
      c.classes = new Set(this.classes);
      c.classList = makeClassList(c);
      c.value = this.value;
      if (deep) for (const f of this.children) c.appendChild(f.cloneNode(true));
      return c;
    },
    querySelectorAll(sel) { return descendants(this).filter((d) => matches(d, sel)); },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
  };
  el.classList = makeClassList(el);
  if (id) el.attrs.id = id;
  return el;
}
function makeClassList(el) {
  return {
    add: (c) => el.classes.add(c),
    remove: (c) => el.classes.delete(c),
    contains: (c) => el.classes.has(c),
  };
}
const root = create('body');
/* In ordine di documento: se una foto con gli id stesse prima della chat
   vera, sarebbe lei a rispondere. */
globalThis.document = {
  getElementById: (id) => descendants(root).find((d) => d.id === id) || null,
};

/* ── La scena: la pista con la pagina 0 e due pagine quaderno ── */
const track = create('div', { cls: 'home-track' });
root.appendChild(track);
const p0 = create('div', { cls: 'home-page' });
const p1 = create('div', { cls: 'home-page' });
const p2 = create('div', { cls: 'home-page' });
track.append = (...xs) => xs.forEach((x) => track.appendChild(x));
track.append(p0, p1, p2);

/* La chat vera, come in `index.html`. */
const chat = create('div', { id: 'home-chat', cls: 'home-chat' });
const thread = create('div', { id: 'home-thread', cls: 'home-thread' });
const empty = create('div', { id: 'home-empty', cls: 'home-empty' });
const work = create('div', { id: 'home-activity', cls: 'home-activity' });
const network = create('div', { id: 'home-wire', cls: 'home-wire' });
const attachments = create('div', { id: 'home-pending', cls: 'home-pending' });
const composer = create('div', { cls: 'home-composer' });
const field = create('textarea', { id: 'home-input', cls: 'home-input' });
const label = create('label', { cls: 'home-field' });
label.setAttribute('for', 'home-input');
composer.appendChild(label);
composer.appendChild(field);
for (const x of [thread, empty, work, network, attachments, composer]) chat.appendChild(x);
p0.appendChild(chat);

/* Il filo di una conversazione: un messaggio per nome. */
function write(...names) {
  for (const m of thread.querySelectorAll('.home-msg')) m.remove();
  for (const n of names) thread.appendChild(create('div', { cls: 'home-msg', text: n }));
}
const messagesOf = (el) => el.querySelectorAll('.home-msg').map((m) => m.text);
const snapshotOf = (p) => p.children.find((c) => c.classList.contains('home-snapshot')) || null;

/* Il cambio di conversazione, come `showConversation`: la chiave cambia
   **subito**, il filo si svuota subito (la parte sincrona di `reload`), e i
   messaggi nuovi arrivano quando il test lo decide. */
let current = 'A';
const histories = { A: ['A1', 'A2'], B: ['B1', 'B2', 'B3'], C: ['C1'] };
const reads = [];
const changes = [];
function change(key) {
  changes.push(key);
  current = key;
  write();
  let finish;
  let resolve;
  const p = new Promise((r) => {
    finish = () => { write(...histories[key]); r(); };
    /* Una lettura superata: quella vera la scarta la generazione, e non
       scrive niente nel filo — ma la sua promessa si risolve lo stesso. */
    resolve = r;
  });
  reads.push({ key, finish, resolve });
  return p;
}
let atBottom = 0;
write(...histories.A);
const tick = () => new Promise((r) => setTimeout(r, 0));
"""


def _run(body: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        shutil.copy(ASSETS / "home-move.js", root / "home-move.js")
        entry = root / "prova.mjs"
        entry.write_text(
            "import assert from 'node:assert/strict';\n"
            + _DOM
            + textwrap.dedent(
                """
                const { ChatMove, SNAPSHOT_CAP_MS, SNAPSHOT_MESSAGES } = await import('./home-move.js');
                const t = new ChatMove({
                  chat,
                  change,
                  currentKey: () => current,
                  atBottom: () => { atBottom += 1; },
                });
                """
            )
            + body,
            encoding="utf-8",
        )
        run_module(entry)


# ── L'arrivo ────────────────────────────────────────────────────────────────


def test_the_chat_moves_to_the_page_and_changes_conversation() -> None:
    """Una chat sola: si sposta, non si copia."""
    _run(
        "t.arrives(p1, 'B');\n"
        "assert.equal(chat.parentElement, p1, 'la chat non e arrivata');\n"
        "assert.deepEqual(changes, ['B']);\n"
        "assert.equal(document.getElementById('home-chat'), chat);\n"
    )


def test_the_page_it_leaves_keeps_a_photo_of_what_it_was() -> None:
    """Scattata **prima** del cambio: dopo, sarebbe la foto di quella d'arrivo.

    Il filo si svuota nella parte sincrona di `reload`, cioe' dentro la
    chiamata stessa al cambio: una foto scattata un istante dopo e' una foto
    vuota — o, a lettura finita, la foto dell'altra conversazione appesa nella
    pagina sbagliata.
    """
    _run(
        "t.arrives(p1, 'B');\n"
        "const snapshot = snapshotOf(p0);\n"
        "assert.ok(snapshot, 'la pagina 0 e rimasta senza foto');\n"
        "assert.deepEqual(messagesOf(snapshot), ['A1', 'A2']);\n"
    )


def test_the_photo_has_no_ids_so_nobody_writes_into_it() -> None:
    """**La trappola.** La pagina 0 sta prima nel documento.

    Se la foto si portasse dietro `#home-thread`, `getElementById` —
    che cerca in ordine di documento — restituirebbe lei, e il filo
    comincerebbe a scrivere i messaggi dentro una copia inerte, fuori schermo.
    """
    _run(
        "t.arrives(p1, 'B');\n"
        "const snapshot = snapshotOf(p0);\n"
        "assert.equal(snapshot.getAttribute('id'), null);\n"
        "assert.equal(snapshot.querySelectorAll('[id]').length, 0, 'id rimasti nella foto');\n"
        "assert.equal(snapshot.querySelectorAll('[for]').length, 0, 'for rimasti nella foto');\n"
        "assert.equal(document.getElementById('home-thread'), thread);\n"
        "assert.equal(document.getElementById('home-input'), field);\n"
    )


def test_the_photo_is_inert_and_silent() -> None:
    """Il dito non ci fa niente, e chi legge lo schermo non la sente."""
    _run(
        "t.arrives(p1, 'B');\n"
        "const snapshot = snapshotOf(p0);\n"
        "assert.ok(snapshot.hasAttribute('inert'));\n"
        "assert.equal(snapshot.getAttribute('aria-hidden'), 'true');\n"
        "assert.ok(snapshot.classList.contains('home-snapshot'));\n"
    )


def test_what_belongs_to_a_moment_stays_out_of_the_photo() -> None:
    """La riga di lavoro, la rete, gli allegati: congelati direbbero il falso.

    E la bozza: e' della conversazione di chi scatta, e in una copia
    mostrerebbe testo che non e' di quella pagina.
    """
    _run(
        "field.value = 'mezza frase';\n"
        "t.arrives(p1, 'B');\n"
        "const snapshot = snapshotOf(p0);\n"
        "for (const sel of ['.home-activity', '.home-wire', '.home-pending']) {\n"
        "  assert.ok(snapshot.querySelector(sel).hasAttribute('hidden'), sel);\n"
        "}\n"
        "assert.equal(snapshot.querySelector('textarea').value, '');\n"
        "assert.equal(field.value, 'mezza frase', 'la bozza vera e stata toccata');\n"
    )


def test_a_long_conversation_is_photographed_short() -> None:
    """Solo gli ultimi: la chat sta in fondo, e il resto non si vede comunque."""
    _run(
        "write(...Array.from({ length: SNAPSHOT_MESSAGES + 15 }, (_, i) => 'm' + i));\n"
        "t.arrives(p1, 'B');\n"
        "const tenuti = messagesOf(snapshotOf(p0));\n"
        "assert.equal(tenuti.length, SNAPSHOT_MESSAGES);\n"
        "assert.equal(tenuti[tenuti.length - 1], 'm' + (SNAPSHOT_MESSAGES + 14), 'non sono gli ultimi');\n"
    )


def test_the_photo_starts_at_the_bottom_like_the_chat() -> None:
    """Un clone parte dall'alto: senza, entrerebbero i messaggi vecchi."""
    _run(
        "t.arrives(p1, 'B');\n"
        "const f = snapshotOf(p0).querySelector('.home-thread');\n"
        "assert.equal(f.scrollTop, f.scrollHeight);\n"
    )


# ── La foto sopra la chat, finche' la lettura non e' finita ─────────────────


def test_the_chat_arrives_under_a_cover_and_the_cover_goes_when_read() -> None:
    """Il vuoto fra lo svuotare e il rileggere non si deve vedere.

    E' la parte che fa sembrare vero lo scorrimento: senza, la pagina
    entrerebbe e *poi* si svuoterebbe e si riempirebbe sotto gli occhi.
    """
    _run(
        "const arrivo = t.arrives(p1, 'B');\n"
        "assert.equal(p1.children[p1.children.length - 1].classList.contains('home-snapshot'), true,\n"
        "  'la foto deve stare sopra: ultimo figlio del pannello');\n"
        "assert.equal(p1.children[0], chat, 'la chat deve stare sotto');\n"
        "reads[0].finish();\n"
        "await arrivo;\n"
        "assert.equal(snapshotOf(p1), null, 'finita la lettura la foto doveva andarsene');\n"
        "assert.deepEqual(messagesOf(chat), ['B1', 'B2', 'B3']);\n"
        "assert.ok(atBottom > 0, 'il filo non e stato riportato in fondo');\n"
    )


def test_a_read_that_never_ends_does_not_leave_the_cover_forever() -> None:
    """Al tetto la foto se ne va comunque: una foto inerte sembra un difetto."""
    _run(
        "const before = Date.now();\n"
        "await t.arrives(p1, 'B');\n"
        "assert.equal(snapshotOf(p1), null);\n"
        "assert.ok(Date.now() - before >= SNAPSHOT_CAP_MS - 20, 'se n e andata prima del tetto');\n"
    )


def test_a_fast_finger_the_last_arrival_wins() -> None:
    """A → B → A → B prima che la prima lettura finisca.

    Il «finito» della prima lettura di B non deve scoprire la chat mentre
    quella della seconda sta ancora leggendo. Stessa famiglia del doppio
    montaggio delle app (22/09/2026): un segno per tentativo.
    """
    _run(
        "t.arrives(p1, 'B');\n"
        "t.arrives(p0, 'A');\n"
        "t.arrives(p1, 'B');\n"
        "assert.equal(chat.parentElement, p1);\n"
        "reads[0].finish();\n"
        "await tick(); await tick();\n"
        "assert.ok(snapshotOf(p1), 'la prima lettura ha scoperto la chat della terza');\n"
        "reads[2].finish();\n"
        "await tick(); await tick();\n"
        "assert.equal(snapshotOf(p1), null);\n"
    )


def test_a_half_read_chat_is_not_photographed() -> None:
    """Si torna su B, e si riparte prima che la lettura finisca.

    In quel momento il filo e' vuoto: fotografarlo vorrebbe dire rimpiazzare
    la foto buona di B con una vuota, fino alla visita dopo.

    **E una lettura vecchia che finisce dopo una nuova non garantisce per
    lei.** Fino al 25/09/2026 il banco si fermava qui sopra, e le letture
    arrivavano sempre in ordine: il controllo che solo l'*ultima* lettura
    renda la chat di nuovo fotografabile si poteva togliere, e restava verde.
    Qui la lettura di B (superata) finisce mentre quella di A e' ancora in
    corso: se B dicesse «affidabile», lasciare A la fotograferebbe vuota.
    """
    _run(
        "let a = t.arrives(p1, 'B'); reads[0].finish(); await a;\n"
        "a = t.arrives(p0, 'A'); reads[1].finish(); await a;\n"
        "t.arrives(p1, 'B');\n"          # lettura di B in corso: filo vuoto
        "t.arrives(p0, 'A');\n"          # e si riparte subito
        "assert.deepEqual(messagesOf(snapshotOf(p1)), ['B1', 'B2', 'B3'],\n"
        "  'la foto di B e stata rifatta a meta lettura');\n"
        # La lettura di B, gia' superata, finisce adesso: come quella vera,
        # scartata dalla generazione, non scrive nel filo — ma si risolve.
        "reads[2].resolve();\n"
        "await tick();\n"
        "t.arrives(p1, 'B');\n"          # si lascia A mentre la sua lettura e' in corso
        "assert.deepEqual(messagesOf(snapshotOf(p0)), ['A1', 'A2'],\n"
        "  'una lettura vecchia finita tardi ha fatto fotografare A a meta lettura');\n"
    )


# ── I casi di confine ───────────────────────────────────────────────────────


def test_the_same_conversation_on_two_pages_just_moves_the_chat() -> None:
    """La pagina 0 e la pagina del quaderno possono mostrare lo stesso quaderno.

    Deciso dall'utente il 23/09/2026: dal titolo della pagina 0 un quaderno si
    apre li', anche se ha una pagina sua. Arrivare sull'altra non cambia niente:
    si sposta la chat, e basta.
    """
    _run(
        "current = 'B'; write(...histories.B);\n"
        "await t.arrives(p1, 'B');\n"
        "assert.equal(chat.parentElement, p1);\n"
        "assert.deepEqual(changes, [], 'ha cambiato conversazione senza motivo');\n"
        "assert.equal(snapshotOf(p1), null);\n"
        "assert.ok(atBottom > 0, 'spostata, la chat perde lo scroll: va riportata in fondo');\n"
    )


def test_arriving_where_it_already_is_does_nothing() -> None:
    _run(
        "await t.arrives(p0, 'A');\n"
        "assert.deepEqual(changes, []);\n"
        "assert.equal(snapshotOf(p0), null);\n"
    )


def test_a_notebook_never_seen_arrives_as_an_empty_chat() -> None:
    """La prima volta non c'e' foto: entra la chat senza messaggi.

    Accettato dall'utente il 23/09/2026. Senza nemmeno lo stato vuoto: «non
    c'e' niente qui» sarebbe falso — c'e', solo non l'abbiamo ancora letto.
    """
    _run(
        "t.snapshotIfNeeded(p2, 'C');\n"
        "const f = snapshotOf(p2);\n"
        "assert.ok(f, 'nessuna foto');\n"
        "assert.deepEqual(messagesOf(f), []);\n"
        "assert.ok(f.querySelector('.home-empty').hasAttribute('hidden'));\n"
    )


def test_a_photo_already_there_is_not_printed_again() -> None:
    """Passa a ogni cambio di pagina: ristamparla a ogni giro costerebbe."""
    _run(
        "t.snapshotIfNeeded(p2, 'C');\n"
        "const before = snapshotOf(p2);\n"
        "t.snapshotIfNeeded(p2, 'C');\n"
        "assert.equal(snapshotOf(p2), before);\n"
    )


def test_where_the_chat_is_parked_there_is_no_photo_over_it() -> None:
    """Parcheggiata mentre guardi un'app, resta lei: viva e' meglio che in foto."""
    _run(
        "let a = t.arrives(p1, 'B'); reads[0].finish(); await a;\n"
        "t.snapshotIfNeeded(p1, 'B');\n"
        "assert.equal(snapshotOf(p1), null);\n"
    )


def test_a_panel_about_to_be_thrown_away_gives_the_chat_back_first() -> None:
    """Ridisegnare le pagine non deve portare via la chat col pannello.

    E torna **sotto** la foto della pagina 0, se c'e': cosi' non si vede la
    conversazione sbagliata in quella pagina.
    """
    _run(
        "let a = t.arrives(p1, 'B'); reads[0].finish(); await a;\n"
        "t.bringBackHome(p1, p0);\n"
        "assert.equal(chat.parentElement, p0);\n"
        "assert.equal(p0.children[0], chat, 'la chat deve stare sotto la foto');\n"
        "p1.remove();\n"
        "assert.equal(document.getElementById('home-thread'), thread, 'la chat e andata via col pannello');\n"
    )


def test_giving_back_from_a_panel_that_does_not_hold_it_does_nothing() -> None:
    _run(
        "t.bringBackHome(p2, p0);\n"
        "assert.equal(chat.parentElement, p0);\n"
        "assert.equal(p0.children.filter((c) => c === chat).length, 1);\n"
    )


def test_leaving_a_panel_leaves_no_photo_over_what_is_underneath() -> None:
    """Un quaderno chiuso nei Quaderni: sotto torna l'elenco, e una foto della
    chat lo coprirebbe. La chat torna a casa e prende la conversazione di casa.
    (26/09/2026)"""
    _run(
        "let a = t.arrives(p1, 'B'); reads[0].finish(); await a;\n"
        "a = t.leaves(p1, p0, 'A');\n"
        "assert.equal(chat.parentElement, p0);\n"
        "assert.equal(snapshotOf(p1), null, 'l\u2019elenco e\u2019 coperto da una foto');\n"
        "reads[1].finish(); await a;\n"
        "assert.deepEqual(changes, ['B', 'A']);\n"
        "assert.equal(snapshotOf(p0), null);\n"
    )


def test_leaving_keeps_the_negative_for_the_next_visit() -> None:
    """Riaprendo lo stesso quaderno, la foto che copre la lettura e' quella di
    com'era, non uno scheletro vuoto."""
    _run(
        "let a = t.arrives(p1, 'B'); reads[0].finish(); await a;\n"
        "a = t.leaves(p1, p0, 'A'); reads[1].finish(); await a;\n"
        "a = t.arrives(p1, 'B');\n"
        "assert.deepEqual(messagesOf(snapshotOf(p1)), ['B1', 'B2', 'B3']);\n"
        "reads[2].finish(); await a;\n"
    )


def test_leaving_a_panel_the_chat_is_not_in_only_clears_its_photo() -> None:
    _run(
        "let a = t.arrives(p1, 'B'); reads[0].finish(); await a;\n"
        "await t.arrives(p0, 'A').then(() => {});\n"
        "reads[1].finish();\n"
        "assert.ok(snapshotOf(p1), 'la pagina lasciata non ha la sua foto');\n"
        "await t.leaves(p1, p0, 'A');\n"
        "assert.equal(snapshotOf(p1), null);\n"
        "assert.equal(chat.parentElement, p0);\n"
        "assert.deepEqual(changes, ['B', 'A'], 'ha cambiato conversazione per niente');\n"
    )


def test_after_coming_home_out_of_place_page_zero_keeps_its_own_photo() -> None:
    """**Trovato rileggendo, dopo il giro sul telefono del 23/09/2026.**

    Sei sulla pagina di un quaderno e aggiungi o togli una pagina: il
    ridisegno riporta la chat a casa **cosi' com'e'**, cioe' mostrando il
    quaderno, sotto la foto della pagina 0. Subito dopo la pista torna sulla
    pagina del quaderno, e la chat riparte da casa. Se a quel punto il trasloco
    fotografasse la chat e appendesse la foto nella pagina che lascia, la
    pagina 0 riceverebbe la foto **del quaderno**: la si vedrebbe entrare a
    meta' scorrimento, e il trucco si vedrebbe.
    """
    _run(
        "let a = t.arrives(p1, 'B'); reads[0].finish(); await a;\n"
        "assert.deepEqual(messagesOf(snapshotOf(p0)), ['A1', 'A2']);\n"
        "t.bringBackHome(p1, p0);\n"            # il ridisegno
        "await t.arrives(p2, 'B');\n"           # la pagina del quaderno, rifatta
        "assert.equal(chat.parentElement, p2);\n"
        "assert.deepEqual(messagesOf(snapshotOf(p0)), ['A1', 'A2'],\n"
        "  'la pagina 0 ha preso la foto del quaderno');\n"
    )


def test_coming_home_out_of_place_then_arriving_home_still_switches() -> None:
    """E se dopo il ridisegno si torna proprio alla pagina 0, la chat cambia:
    era a casa, ma con la conversazione sbagliata sotto la foto."""
    _run(
        "let a = t.arrives(p1, 'B'); reads[0].finish(); await a;\n"
        "t.bringBackHome(p1, p0);\n"
        "a = t.arrives(p0, 'A');\n"
        "assert.ok(snapshotOf(p0), 'la chat sbagliata e scoperta durante la lettura');\n"
        "reads[1].finish(); await a;\n"
        "assert.deepEqual(changes, ['B', 'A']);\n"
        "assert.equal(snapshotOf(p0), null);\n"
        "assert.deepEqual(messagesOf(chat), ['A1', 'A2']);\n"
    )


def test_the_out_of_place_mark_lasts_one_arrival_only() -> None:
    """Il segno vale per l'arrivo dopo il ridisegno, e poi si spegne.

    Se restasse, la pagina 0 non aggiornerebbe piu' la sua foto: la
    conversazione va avanti, e scorrendo entrerebbe quella di ieri.
    """
    _run(
        "let a = t.arrives(p1, 'B'); reads[0].finish(); await a;\n"
        "t.bringBackHome(p1, p0);\n"
        "await t.arrives(p2, 'B');\n"                         # consuma il segno
        "histories.A = ['A1', 'A2', 'A3 nuovo'];\n"
        "a = t.arrives(p0, 'A'); reads[1].finish(); await a;\n"
        "a = t.arrives(p2, 'B'); reads[2].finish(); await a;\n"
        "assert.deepEqual(messagesOf(snapshotOf(p0)), ['A1', 'A2', 'A3 nuovo'],\n"
        "  'la pagina 0 ha smesso di aggiornare la sua foto');\n"
    )


# I timer del tetto ancora armati, contati dal banco.
_LIVE_TIMERS = (
    "const live = new Set();\n"
    "const arma = globalThis.setTimeout, deactivate = globalThis.clearTimeout;\n"
    "globalThis.setTimeout = (fn, ms) => {\n"
    "  const id = arma(() => { live.delete(id); fn(); }, ms);\n"
    "  if (ms === SNAPSHOT_CAP_MS) live.add(id);\n"
    "  return id;\n"
    "};\n"
    "globalThis.clearTimeout = (id) => { live.delete(id); deactivate(id); };\n"
)


def test_a_newer_arrival_expires_the_older_cover_timer() -> None:
    """Un arrivo superato non ha piu' niente da togliere (`mine !== _arrivals`):
    il suo tetto scade subito invece di restare armato fino in fondo."""
    _run(
        _LIVE_TIMERS
        + "t.arrives(p1, 'B');\n"
        "t.arrives(p0, 'A');\n"
        "assert.equal(live.size, 1, 'il tetto dell\\u2019arrivo superato e\\u2019 ancora armato');\n"
        "t.bringBackHome(p0, p1);\n"
        "assert.equal(live.size, 0, 'riportata a casa, il tetto dell\\u2019arrivo in volo resta');\n"
    )


def test_a_read_that_ends_first_switches_the_cover_timer_off() -> None:
    """Il tetto della foto restava armato 600 ms anche quando la lettura aveva
    gia' vinto: un timer per niente a ogni arrivo, e un processo che non
    poteva finire prima (questo banco ci metteva undici secondi)."""
    _run(
        _LIVE_TIMERS
        + "const arrivo = t.arrives(p1, 'B');\n"
        "assert.equal(live.size, 1, 'il tetto non e\\u2019 armato');\n"
        "reads[0].finish();\n"
        "await arrivo;\n"
        "assert.equal(live.size, 0, 'la lettura ha vinto e il tetto e\\u2019 rimasto armato');\n"
    )
