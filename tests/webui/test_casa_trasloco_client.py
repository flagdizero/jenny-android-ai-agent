"""Il trasloco della chat: una chat sola, che si sposta fra le pagine.

Una pagina «conversazione» non e' una seconda chat: e' una scorciatoia che
cambia la conversazione dell'unica che c'e', travestita da pagina. Il
travestimento e' una **foto**: nelle pagine di chat che la chat non abita c'e'
una copia statica di com'era, e all'arrivo la chat vera ci scivola sotto (v.
`casa-trasloco.js` e `.agent/pagine-conversazione-plan.md`).

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
function corrisponde(el, sel) {
  return sel.split(',').map((s) => s.trim()).some((s) => {
    if (s.startsWith('.')) return el.classList.contains(s.slice(1));
    const attr = s.match(/^\[([a-z-]+)\]$/);
    if (attr) return el.hasAttribute(attr[1]);
    if (/^[a-z]+$/.test(s)) return el.tagName === s.toUpperCase();
    throw new Error('selettore che il finto non capisce: ' + s);
  });
}
function discendenti(el) {
  const tutti = [];
  for (const c of el.children) tutti.push(c, ...discendenti(c));
  return tutti;
}
function crea(tag, { id, cls, testo } = {}) {
  const el = {
    tagName: tag.toUpperCase(),
    children: [],
    parentElement: null,
    attrs: {},
    classi: new Set((cls || '').split(' ').filter(Boolean)),
    value: '',
    testo: testo || '',
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
    insertBefore(c, rif) {
      if (c.parentElement) c.parentElement.children = c.parentElement.children.filter((x) => x !== c);
      const i = rif ? this.children.indexOf(rif) : -1;
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
    cloneNode(profondo) {
      const c = crea(tag, { testo: this.testo });
      c.attrs = { ...this.attrs };
      c.classi = new Set(this.classi);
      c.classList = listaClassi(c);
      c.value = this.value;
      if (profondo) for (const f of this.children) c.appendChild(f.cloneNode(true));
      return c;
    },
    querySelectorAll(sel) { return discendenti(this).filter((d) => corrisponde(d, sel)); },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
  };
  el.classList = listaClassi(el);
  if (id) el.attrs.id = id;
  return el;
}
function listaClassi(el) {
  return {
    add: (c) => el.classi.add(c),
    remove: (c) => el.classi.delete(c),
    contains: (c) => el.classi.has(c),
  };
}
const radice = crea('body');
/* In ordine di documento: se una foto con gli id stesse prima della chat
   vera, sarebbe lei a rispondere. */
globalThis.document = {
  getElementById: (id) => discendenti(radice).find((d) => d.id === id) || null,
};

/* ── La scena: la pista con la pagina 0 e due pagine quaderno ── */
const pista = crea('div', { cls: 'casa-pista' });
radice.appendChild(pista);
const p0 = crea('div', { cls: 'casa-pagina' });
const p1 = crea('div', { cls: 'casa-pagina' });
const p2 = crea('div', { cls: 'casa-pagina' });
pista.append = (...xs) => xs.forEach((x) => pista.appendChild(x));
pista.append(p0, p1, p2);

/* La chat vera, come in `index.html`. */
const chat = crea('div', { id: 'casa-chat', cls: 'casa-chat' });
const filo = crea('div', { id: 'casa-thread', cls: 'casa-thread' });
const vuoto = crea('div', { id: 'casa-empty', cls: 'casa-empty' });
const lavoro = crea('div', { id: 'casa-activity', cls: 'casa-activity' });
const rete = crea('div', { id: 'casa-wire', cls: 'casa-wire' });
const allegati = crea('div', { id: 'casa-pending', cls: 'casa-pending' });
const composer = crea('div', { cls: 'casa-composer' });
const campo = crea('textarea', { id: 'casa-input', cls: 'casa-input' });
const etichetta = crea('label', { cls: 'casa-field' });
etichetta.setAttribute('for', 'casa-input');
composer.appendChild(etichetta);
composer.appendChild(campo);
for (const x of [filo, vuoto, lavoro, rete, allegati, composer]) chat.appendChild(x);
p0.appendChild(chat);

/* Il filo di una conversazione: un messaggio per nome. */
function scrivi(...nomi) {
  for (const m of filo.querySelectorAll('.casa-msg')) m.remove();
  for (const n of nomi) filo.appendChild(crea('div', { cls: 'casa-msg', testo: n }));
}
const messaggiDi = (el) => el.querySelectorAll('.casa-msg').map((m) => m.testo);
const fotoDi = (p) => p.children.find((c) => c.classList.contains('casa-foto')) || null;

/* Il cambio di conversazione, come `mostraConversazione`: la chiave cambia
   **subito**, il filo si svuota subito (la parte sincrona di `reload`), e i
   messaggi nuovi arrivano quando il test lo decide. */
let attuale = 'A';
const storie = { A: ['A1', 'A2'], B: ['B1', 'B2', 'B3'], C: ['C1'] };
const letture = [];
const cambi = [];
function cambia(chiave) {
  cambi.push(chiave);
  attuale = chiave;
  scrivi();
  let finisci;
  const p = new Promise((r) => { finisci = () => { scrivi(...storie[chiave]); r(); }; });
  letture.push({ chiave, finisci });
  return p;
}
let inFondo = 0;
scrivi(...storie.A);
const giro = () => new Promise((r) => setTimeout(r, 0));
"""


def _run(corpo: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        radice = Path(tmp)
        shutil.copy(ASSETS / "casa-trasloco.js", radice / "casa-trasloco.js")
        entry = radice / "prova.mjs"
        entry.write_text(
            "import assert from 'node:assert/strict';\n"
            + _DOM
            + textwrap.dedent(
                """
                const { Trasloco, TETTO_FOTO_MS, FOTO_MESSAGGI } = await import('./casa-trasloco.js');
                const t = new Trasloco({
                  chat,
                  cambia,
                  chiaveAttuale: () => attuale,
                  inFondo: () => { inFondo += 1; },
                });
                """
            )
            + corpo,
            encoding="utf-8",
        )
        run_module(entry)


# ── L'arrivo ────────────────────────────────────────────────────────────────


def test_the_chat_moves_to_the_page_and_changes_conversation() -> None:
    """Una chat sola: si sposta, non si copia."""
    _run(
        "t.arriva(p1, 'B');\n"
        "assert.equal(chat.parentElement, p1, 'la chat non e arrivata');\n"
        "assert.deepEqual(cambi, ['B']);\n"
        "assert.equal(document.getElementById('casa-chat'), chat);\n"
    )


def test_the_page_it_leaves_keeps_a_photo_of_what_it_was() -> None:
    """Scattata **prima** del cambio: dopo, sarebbe la foto di quella d'arrivo.

    Il filo si svuota nella parte sincrona di `reload`, cioe' dentro la
    chiamata stessa al cambio: una foto scattata un istante dopo e' una foto
    vuota — o, a lettura finita, la foto dell'altra conversazione appesa nella
    pagina sbagliata.
    """
    _run(
        "t.arriva(p1, 'B');\n"
        "const foto = fotoDi(p0);\n"
        "assert.ok(foto, 'la pagina 0 e rimasta senza foto');\n"
        "assert.deepEqual(messaggiDi(foto), ['A1', 'A2']);\n"
    )


def test_the_photo_has_no_ids_so_nobody_writes_into_it() -> None:
    """**La trappola.** La pagina 0 sta prima nel documento.

    Se la foto si portasse dietro `#casa-thread`, `getElementById` —
    che cerca in ordine di documento — restituirebbe lei, e il filo
    comincerebbe a scrivere i messaggi dentro una copia inerte, fuori schermo.
    """
    _run(
        "t.arriva(p1, 'B');\n"
        "const foto = fotoDi(p0);\n"
        "assert.equal(foto.getAttribute('id'), null);\n"
        "assert.equal(foto.querySelectorAll('[id]').length, 0, 'id rimasti nella foto');\n"
        "assert.equal(foto.querySelectorAll('[for]').length, 0, 'for rimasti nella foto');\n"
        "assert.equal(document.getElementById('casa-thread'), filo);\n"
        "assert.equal(document.getElementById('casa-input'), campo);\n"
    )


def test_the_photo_is_inert_and_silent() -> None:
    """Il dito non ci fa niente, e chi legge lo schermo non la sente."""
    _run(
        "t.arriva(p1, 'B');\n"
        "const foto = fotoDi(p0);\n"
        "assert.ok(foto.hasAttribute('inert'));\n"
        "assert.equal(foto.getAttribute('aria-hidden'), 'true');\n"
        "assert.ok(foto.classList.contains('casa-foto'));\n"
    )


def test_what_belongs_to_a_moment_stays_out_of_the_photo() -> None:
    """La riga di lavoro, la rete, gli allegati: congelati direbbero il falso.

    E la bozza: e' della conversazione di chi scatta, e in una copia
    mostrerebbe testo che non e' di quella pagina.
    """
    _run(
        "campo.value = 'mezza frase';\n"
        "t.arriva(p1, 'B');\n"
        "const foto = fotoDi(p0);\n"
        "for (const sel of ['.casa-activity', '.casa-wire', '.casa-pending']) {\n"
        "  assert.ok(foto.querySelector(sel).hasAttribute('hidden'), sel);\n"
        "}\n"
        "assert.equal(foto.querySelector('textarea').value, '');\n"
        "assert.equal(campo.value, 'mezza frase', 'la bozza vera e stata toccata');\n"
    )


def test_a_long_conversation_is_photographed_short() -> None:
    """Solo gli ultimi: la chat sta in fondo, e il resto non si vede comunque."""
    _run(
        "scrivi(...Array.from({ length: FOTO_MESSAGGI + 15 }, (_, i) => 'm' + i));\n"
        "t.arriva(p1, 'B');\n"
        "const tenuti = messaggiDi(fotoDi(p0));\n"
        "assert.equal(tenuti.length, FOTO_MESSAGGI);\n"
        "assert.equal(tenuti[tenuti.length - 1], 'm' + (FOTO_MESSAGGI + 14), 'non sono gli ultimi');\n"
    )


def test_the_photo_starts_at_the_bottom_like_the_chat() -> None:
    """Un clone parte dall'alto: senza, entrerebbero i messaggi vecchi."""
    _run(
        "t.arriva(p1, 'B');\n"
        "const f = fotoDi(p0).querySelector('.casa-thread');\n"
        "assert.equal(f.scrollTop, f.scrollHeight);\n"
    )


# ── La foto sopra la chat, finche' la lettura non e' finita ─────────────────


def test_the_chat_arrives_under_a_cover_and_the_cover_goes_when_read() -> None:
    """Il vuoto fra lo svuotare e il rileggere non si deve vedere.

    E' la parte che fa sembrare vero lo scorrimento: senza, la pagina
    entrerebbe e *poi* si svuoterebbe e si riempirebbe sotto gli occhi.
    """
    _run(
        "const arrivo = t.arriva(p1, 'B');\n"
        "assert.equal(p1.children[p1.children.length - 1].classList.contains('casa-foto'), true,\n"
        "  'la foto deve stare sopra: ultimo figlio del pannello');\n"
        "assert.equal(p1.children[0], chat, 'la chat deve stare sotto');\n"
        "letture[0].finisci();\n"
        "await arrivo;\n"
        "assert.equal(fotoDi(p1), null, 'finita la lettura la foto doveva andarsene');\n"
        "assert.deepEqual(messaggiDi(chat), ['B1', 'B2', 'B3']);\n"
        "assert.ok(inFondo > 0, 'il filo non e stato riportato in fondo');\n"
    )


def test_a_read_that_never_ends_does_not_leave_the_cover_forever() -> None:
    """Al tetto la foto se ne va comunque: una foto inerte sembra un difetto."""
    _run(
        "const prima = Date.now();\n"
        "await t.arriva(p1, 'B');\n"
        "assert.equal(fotoDi(p1), null);\n"
        "assert.ok(Date.now() - prima >= TETTO_FOTO_MS - 20, 'se n e andata prima del tetto');\n"
    )


def test_a_fast_finger_the_last_arrival_wins() -> None:
    """A → B → A → B prima che la prima lettura finisca.

    Il «finito» della prima lettura di B non deve scoprire la chat mentre
    quella della seconda sta ancora leggendo. Stessa famiglia del doppio
    montaggio delle app (22/09/2026): un segno per tentativo.
    """
    _run(
        "t.arriva(p1, 'B');\n"
        "t.arriva(p0, 'A');\n"
        "t.arriva(p1, 'B');\n"
        "assert.equal(chat.parentElement, p1);\n"
        "letture[0].finisci();\n"
        "await giro(); await giro();\n"
        "assert.ok(fotoDi(p1), 'la prima lettura ha scoperto la chat della terza');\n"
        "letture[2].finisci();\n"
        "await giro(); await giro();\n"
        "assert.equal(fotoDi(p1), null);\n"
    )


def test_a_half_read_chat_is_not_photographed() -> None:
    """Si torna su B, e si riparte prima che la lettura finisca.

    In quel momento il filo e' vuoto: fotografarlo vorrebbe dire rimpiazzare
    la foto buona di B con una vuota, fino alla visita dopo.
    """
    _run(
        "let a = t.arriva(p1, 'B'); letture[0].finisci(); await a;\n"
        "a = t.arriva(p0, 'A'); letture[1].finisci(); await a;\n"
        "t.arriva(p1, 'B');\n"          # lettura di B in corso: filo vuoto
        "t.arriva(p0, 'A');\n"          # e si riparte subito
        "assert.deepEqual(messaggiDi(fotoDi(p1)), ['B1', 'B2', 'B3'],\n"
        "  'la foto di B e stata rifatta a meta lettura');\n"
    )


# ── I casi di confine ───────────────────────────────────────────────────────


def test_the_same_conversation_on_two_pages_just_moves_the_chat() -> None:
    """La pagina 0 e la pagina del quaderno possono mostrare lo stesso quaderno.

    Deciso dall'utente il 23/09/2026: dal titolo della pagina 0 un quaderno si
    apre li', anche se ha una pagina sua. Arrivare sull'altra non cambia niente:
    si sposta la chat, e basta.
    """
    _run(
        "attuale = 'B'; scrivi(...storie.B);\n"
        "await t.arriva(p1, 'B');\n"
        "assert.equal(chat.parentElement, p1);\n"
        "assert.deepEqual(cambi, [], 'ha cambiato conversazione senza motivo');\n"
        "assert.equal(fotoDi(p1), null);\n"
        "assert.ok(inFondo > 0, 'spostata, la chat perde lo scroll: va riportata in fondo');\n"
    )


def test_arriving_where_it_already_is_does_nothing() -> None:
    _run(
        "await t.arriva(p0, 'A');\n"
        "assert.deepEqual(cambi, []);\n"
        "assert.equal(fotoDi(p0), null);\n"
    )


def test_a_notebook_never_seen_arrives_as_an_empty_chat() -> None:
    """La prima volta non c'e' foto: entra la chat senza messaggi.

    Accettato dall'utente il 23/09/2026. Senza nemmeno lo stato vuoto: «non
    c'e' niente qui» sarebbe falso — c'e', solo non l'abbiamo ancora letto.
    """
    _run(
        "t.fotoSeServe(p2, 'C');\n"
        "const f = fotoDi(p2);\n"
        "assert.ok(f, 'nessuna foto');\n"
        "assert.deepEqual(messaggiDi(f), []);\n"
        "assert.ok(f.querySelector('.casa-empty').hasAttribute('hidden'));\n"
    )


def test_a_photo_already_there_is_not_printed_again() -> None:
    """Passa a ogni cambio di pagina: ristamparla a ogni giro costerebbe."""
    _run(
        "t.fotoSeServe(p2, 'C');\n"
        "const prima = fotoDi(p2);\n"
        "t.fotoSeServe(p2, 'C');\n"
        "assert.equal(fotoDi(p2), prima);\n"
    )


def test_where_the_chat_is_parked_there_is_no_photo_over_it() -> None:
    """Parcheggiata mentre guardi un'app, resta lei: viva e' meglio che in foto."""
    _run(
        "let a = t.arriva(p1, 'B'); letture[0].finisci(); await a;\n"
        "t.fotoSeServe(p1, 'B');\n"
        "assert.equal(fotoDi(p1), null);\n"
    )


def test_a_panel_about_to_be_thrown_away_gives_the_chat_back_first() -> None:
    """Ridisegnare le pagine non deve portare via la chat col pannello.

    E torna **sotto** la foto della pagina 0, se c'e': cosi' non si vede la
    conversazione sbagliata in quella pagina.
    """
    _run(
        "let a = t.arriva(p1, 'B'); letture[0].finisci(); await a;\n"
        "t.riportaACasa(p1, p0);\n"
        "assert.equal(chat.parentElement, p0);\n"
        "assert.equal(p0.children[0], chat, 'la chat deve stare sotto la foto');\n"
        "p1.remove();\n"
        "assert.equal(document.getElementById('casa-thread'), filo, 'la chat e andata via col pannello');\n"
    )


def test_giving_back_from_a_panel_that_does_not_hold_it_does_nothing() -> None:
    _run(
        "t.riportaACasa(p2, p0);\n"
        "assert.equal(chat.parentElement, p0);\n"
        "assert.equal(p0.children.filter((c) => c === chat).length, 1);\n"
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
        "let a = t.arriva(p1, 'B'); letture[0].finisci(); await a;\n"
        "assert.deepEqual(messaggiDi(fotoDi(p0)), ['A1', 'A2']);\n"
        "t.riportaACasa(p1, p0);\n"            # il ridisegno
        "await t.arriva(p2, 'B');\n"           # la pagina del quaderno, rifatta
        "assert.equal(chat.parentElement, p2);\n"
        "assert.deepEqual(messaggiDi(fotoDi(p0)), ['A1', 'A2'],\n"
        "  'la pagina 0 ha preso la foto del quaderno');\n"
    )


def test_coming_home_out_of_place_then_arriving_home_still_switches() -> None:
    """E se dopo il ridisegno si torna proprio alla pagina 0, la chat cambia:
    era a casa, ma con la conversazione sbagliata sotto la foto."""
    _run(
        "let a = t.arriva(p1, 'B'); letture[0].finisci(); await a;\n"
        "t.riportaACasa(p1, p0);\n"
        "a = t.arriva(p0, 'A');\n"
        "assert.ok(fotoDi(p0), 'la chat sbagliata e scoperta durante la lettura');\n"
        "letture[1].finisci(); await a;\n"
        "assert.deepEqual(cambi, ['B', 'A']);\n"
        "assert.equal(fotoDi(p0), null);\n"
        "assert.deepEqual(messaggiDi(chat), ['A1', 'A2']);\n"
    )


def test_the_out_of_place_mark_lasts_one_arrival_only() -> None:
    """Il segno vale per l'arrivo dopo il ridisegno, e poi si spegne.

    Se restasse, la pagina 0 non aggiornerebbe piu' la sua foto: la
    conversazione va avanti, e scorrendo entrerebbe quella di ieri.
    """
    _run(
        "let a = t.arriva(p1, 'B'); letture[0].finisci(); await a;\n"
        "t.riportaACasa(p1, p0);\n"
        "await t.arriva(p2, 'B');\n"                         # consuma il segno
        "storie.A = ['A1', 'A2', 'A3 nuovo'];\n"
        "a = t.arriva(p0, 'A'); letture[1].finisci(); await a;\n"
        "a = t.arriva(p2, 'B'); letture[2].finisci(); await a;\n"
        "assert.deepEqual(messaggiDi(fotoDi(p0)), ['A1', 'A2', 'A3 nuovo'],\n"
        "  'la pagina 0 ha smesso di aggiornare la sua foto');\n"
    )


# I timer del tetto ancora armati, contati dal banco.
_TIMER_VIVI = (
    "const vivi = new Set();\n"
    "const arma = globalThis.setTimeout, spegni = globalThis.clearTimeout;\n"
    "globalThis.setTimeout = (fn, ms) => {\n"
    "  const id = arma(() => { vivi.delete(id); fn(); }, ms);\n"
    "  if (ms === TETTO_FOTO_MS) vivi.add(id);\n"
    "  return id;\n"
    "};\n"
    "globalThis.clearTimeout = (id) => { vivi.delete(id); spegni(id); };\n"
)


def test_a_newer_arrival_expires_the_older_cover_timer() -> None:
    """Un arrivo superato non ha piu' niente da togliere (`mio !== _arrivi`):
    il suo tetto scade subito invece di restare armato fino in fondo."""
    _run(
        _TIMER_VIVI
        + "t.arriva(p1, 'B');\n"
        "t.arriva(p0, 'A');\n"
        "assert.equal(vivi.size, 1, 'il tetto dell\\u2019arrivo superato e\\u2019 ancora armato');\n"
        "t.riportaACasa(p0, p1);\n"
        "assert.equal(vivi.size, 0, 'riportata a casa, il tetto dell\\u2019arrivo in volo resta');\n"
    )


def test_a_read_that_ends_first_switches_the_cover_timer_off() -> None:
    """Il tetto della foto restava armato 600 ms anche quando la lettura aveva
    gia' vinto: un timer per niente a ogni arrivo, e un processo che non
    poteva finire prima (questo banco ci metteva undici secondi)."""
    _run(
        _TIMER_VIVI
        + "const arrivo = t.arriva(p1, 'B');\n"
        "assert.equal(vivi.size, 1, 'il tetto non e\\u2019 armato');\n"
        "letture[0].finisci();\n"
        "await arrivo;\n"
        "assert.equal(vivi.size, 0, 'la lettura ha vinto e il tetto e\\u2019 rimasto armato');\n"
    )
