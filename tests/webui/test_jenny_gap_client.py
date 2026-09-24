"""Il margine che i messaggi lasciano a Jenny, eseguito davvero sotto node.

`shared/jenny-gap.js` esiste per una misura: i messaggi avevano
`max-width: 88%`, e quel tetto serviva a non finire dietro la mascotte. Solo
che lei sta **in un angolo** — 87,6 px CSS in fondo a destra sul Titan 2 — e il
tetto lo pagavano *tutti* i messaggi, anche quelli in cima dove non c'e'
nessuno: **82,6 px CSS su ogni riga, il 14% dello schermo** (misurato il
20/09/2026, viewport 574,4 px CSS a DPR 2,500).

La parte che conta e' **geometrica e pura**, e sta qui sotto: dove comincia la
figura dentro il suo quadrato, e quali messaggi la toccano. Il resto —
leggere i rettangoli, mettere una classe — e' DOM e non si prova qui.

**Il numero da non confondere**, ed e' un errore gia' fatto una volta in questo
progetto (v. il commento su `.jenny-duo`): il personaggio occupa il **45% in
larghezza** e il **73% in altezza** del canvas quadrato. Scansare il *quadrato*
invece della *figura* vorrebbe dire lasciare 33 px di buco dove non c'e'
nessuno — cioe' rifare, piu' piccolo, il difetto che si stava correggendo.
"""

from __future__ import annotations

from pathlib import Path

from support.js_harness import requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
GAP_JS = ASSETS / "shared" / "jenny-gap.js"
MASCOT_JS = ASSETS / "shared" / "mascot.js"


pytestmark = requires_node


def _run_js(script: str) -> str:
    """`jenny-gap.js` importa da `mascot.js`, che al caricamento tocca
    `localStorage`: sotto node non esiste. Si stura con un finto prima
    dell'import, che e' meno invasivo che spezzare il modulo in due."""
    sorgente = (
        "globalThis.localStorage = { getItem: () => null, setItem: () => {} };\n"
        "globalThis.document = { documentElement: { style: { setProperty: () => {} } } };\n"
        + GAP_JS.read_text(encoding="utf-8").replace(
            "import { ART_HEIGHT_RATIO } from './mascot.js';",
            "const ART_HEIGHT_RATIO = 0.73;",
        )
        + "\nimport assert from 'node:assert/strict';\n"
        + script
    )
    return run_js(sorgente)


def test_the_ratios_still_say_what_this_module_assumes() -> None:
    """Il 45% non e' scritto qui per caso: viene da `mascot.js`.

    `MARGINE_LATERALE` lo ricalcola da quel numero. Se un giorno l'arte
    cambiasse e il rapporto con lei, questo banco lo dice invece di lasciare il
    modulo a scansare il posto sbagliato in silenzio.
    """
    mascot = MASCOT_JS.read_text(encoding="utf-8")
    assert "export const ART_HEIGHT_RATIO = 0.73;" in mascot, (
        "il rapporto in altezza e' cambiato: rivedere la banda di jenny-gap.js"
    )
    assert "il 45% centrale del canvas quadrato" in mascot, (
        "il rapporto in larghezza e' cambiato: rivedere MARGINE_LATERALE"
    )
    gap = GAP_JS.read_text(encoding="utf-8")
    assert "export const MARGINE_LATERALE = (1 - 0.45) / 2;" in gap


def test_the_figure_is_not_the_square_it_sits_in() -> None:
    """**La casella che vale il file.**

    Col quadrato di serie (120 px, ancorata «fuori» su un viewport da 574,4) i
    due bordi non coincidono: il riquadro comincia a 484,4, la figura a 517,4.
    Trentatre pixel di differenza — scansare il riquadro li regalerebbe al
    nulla.
    """
    out = _run_js("""
// --jenny-size 120, OUT_RATIO 0.25, viewport 574.4:
// il quadrato sborda di 30 a destra, quindi left = 574.4 + 30 - 120.
const lato = 120;
const quadrato = { left: 484.4, right: 604.4, top: 100, bottom: 220 };
const f = figuraDi(quadrato, lato);
assert.ok(Math.abs(f.left - 517.4) < 0.01, 'left = ' + f.left);
// In altezza: i piedi appoggiano sul fondo del quadrato meno i margini, e la
// figura e' alta il 73% -> il suo bordo alto sta 87,6 sopra il fondo.
assert.ok(Math.abs(f.top - (220 - 87.6)) < 0.01, 'top = ' + f.top);
console.log('ok');
""")
    assert "ok" in out


def test_only_the_messages_in_her_corner_are_marked() -> None:
    """Servono **tutti e due** gli assi.

    Un messaggio alto che le passa sopra non va scansato, e nemmeno uno che sta
    alla sua altezza ma finisce tutto a sinistra. Con un asse solo si
    rimetterebbe il tetto di prima — largo su tutta la colonna, o alto su tutta
    la pagina.
    """
    out = _run_js("""
const figura = { left: 517.4, top: 132.4 };
const casi = [
  // [right, bottom, atteso, perche]
  [556, 220, true,  "in basso e a destra: e il suo angolo"],
  [556, 120, false, "largo ma sopra di lei"],
  [500, 220, false, "in basso ma si ferma prima"],
  [500, 120, false, "nessuno dei due assi"],
  [517.4, 220, false, "tocca il bordo esatto: non si sovrappone"],
];
for (const [right, bottom, atteso, perche] of casi) {
  const avuto = serveScansare({ right, bottom }, figura);
  assert.equal(avuto, atteso, `${perche}: atteso ${atteso}, avuto ${avuto}`);
}
console.log('ok');
""")
    assert "ok" in out


def test_the_margin_is_the_distance_to_her_and_never_negative() -> None:
    """Col telefono vero fa 39 px, non gli 82,6 che il tetto buttava via.

    E se lei fosse tutta fuori dallo schermo (messa via sul bordo, o taglia
    minuscola) il margine e' zero: un numero negativo entrerebbe nel CSS come
    `padding-right: -39px`, che il browser ignora — un difetto che non si
    vedrebbe finche' qualcuno non misura.
    """
    out = _run_js("""
// destra del contenuto del filo = 574.4 - 18 di padding
assert.equal(margineDa({ left: 517.4 }, 556.4), 39);
// tutta fuori: niente margine, e mai un numero negativo
assert.equal(margineDa({ left: 600 }, 556.4), 0);
console.log('ok');
""")
    assert "ok" in out


def test_the_thread_keeps_no_blanket_cap_any_more() -> None:
    """L'altra meta' della correzione: il tetto se n'e' andato davvero.

    Senza questa riga si potrebbe rimettere `max-width` su `.casa-msg-jenny` e
    tutti i banchi qui sopra resterebbero verdi — misurerebbero un margine
    giusto sopra una larghezza sbagliata.
    """
    css = (ASSETS / "casa-style.css").read_text(encoding="utf-8")
    blocco = css.split(".casa-msg-jenny {")[1].split("}")[0]
    assert "max-width" not in blocco, (
        "il tetto e' tornato: il margine condizionale non serve piu' a niente"
    )
    assert ".casa-msg-jenny.is-under-jenny" in css, "manca la regola del margine"


# ── Chi si scansa: tutti e due i lati della conversazione ──────────────────


def _con_dom(script: str) -> str:
    """`aggiorna()` con un DOM finto, che e' l'unico modo di provare *quali*
    nodi la classe la prendono. La geometria qui sopra si prova pura; questo
    invece e' l'aggancio, ed e' dove stava il difetto."""
    return _run_js(
        """
function nodo(classi, rect) {
  const set = new Set(classi.split(' '));
  return {
    classi: set,
    classList: {
      add: (c) => set.add(c),
      remove: (c) => set.delete(c),
      contains: (c) => set.has(c),
    },
    getBoundingClientRect: () => rect,
  };
}
globalThis.getComputedStyle = () => ({ paddingRight: '18px' });
const mascotte = {
  hidden: false,
  getBoundingClientRect: () => (
    { left: 484.4, right: 604.4, top: 100, bottom: 220, width: 120 }),
};
function filoCon(nodi) {
  return {
    style: { setProperty: (k, v) => { filoCon.scritto = [k, v]; } },
    getBoundingClientRect: () => ({ right: 574.4 }),
    querySelectorAll: (sel) => nodi.filter((n) => sel
      .split(',').map((s) => s.trim())
      .some((s) => n.classi.has(s.slice(1)))),
  };
}
"""
        + script
    )


def test_a_bubble_of_ours_in_her_corner_dodges_too() -> None:
    """Il difetto vero: si scansavano solo le risposte.

    Le bolle di chi scrive sono `align-self: flex-end` — incollate al bordo
    destro, che e' la colonna di Jenny — e la piu' recente e' anche la piu' in
    basso. Cioe' l'unica cosa che lei copriva sempre era **quello che hai
    appena scritto tu**. Con il selettore vecchio (`.casa-msg-jenny`) questo
    banco e' rosso.
    """
    out = _con_dom("""
const risposta = nodo('casa-msg casa-msg-jenny', { right: 540, bottom: 200 });
const mia      = nodo('casa-msg casa-msg-user',  { right: 556.4, bottom: 300 });
const vecchia  = nodo('casa-msg casa-msg-user',  { right: 556.4, bottom: 90 });
const filo = filoCon([vecchia, risposta, mia]);
new JennyGap(filo, mascotte).aggiorna();
assert.ok(mia.classi.has(CLASSE), 'la bolla nel suo angolo non si e scansata');
assert.ok(risposta.classi.has(CLASSE), 'la risposta nel suo angolo non si e scansata');
assert.ok(!vecchia.classi.has(CLASSE), 'una bolla sopra di lei non deve scansarsi');
assert.deepEqual(filoCon.scritto, ['--jenny-gap', '39px']);
console.log('ok');
""")
    assert "ok" in out


def test_a_bubble_that_stops_dodging_gets_cleaned_up() -> None:
    """Scorri, e chi era nel suo angolo non ci sta piu'.

    Senza il giro di `remove` la bolla si porterebbe dietro il margine per
    sempre: uno scalino a destra su un messaggio in mezzo al filo, dove non
    c'e' nessuno da scansare.
    """
    out = _con_dom("""
const mia = nodo('casa-msg casa-msg-user is-under-jenny', { right: 556.4, bottom: 90 });
new JennyGap(filoCon([mia]), mascotte).aggiorna();
assert.ok(!mia.classi.has(CLASSE), 'il margine e rimasto attaccato');
console.log('ok');
""")
    assert "ok" in out


def test_our_bubble_moves_aside_it_does_not_hollow_out() -> None:
    """Le due forme si scansano in modo diverso, e non e' un dettaglio.

    La risposta di Jenny non ha sfondo: stringerle il testo con `padding` non
    si vede. La bolla ce l'ha — con `padding` si allungherebbe fin sotto di
    lei con dentro il vuoto, cioe' il testo si sposta e la pelle della bolla
    resta coperta lo stesso. Deve muoversi tutta intera: `margin`.
    """
    css = (ASSETS / "casa-style.css").read_text(encoding="utf-8")
    assert ".casa-msg-user.is-under-jenny" in css, "le bolle non si scansano affatto"
    blocco = css.split(".casa-msg-user.is-under-jenny {")[1].split("}")[0]
    assert "margin-right: var(--jenny-gap" in blocco, blocco
    assert "padding-right" not in blocco, (
        "con padding la bolla si svuota a destra invece di spostarsi"
    )
