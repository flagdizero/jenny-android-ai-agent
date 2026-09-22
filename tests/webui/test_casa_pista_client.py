"""La pista della casa: di lato alla chat ci sono le pagine.

La casa e' un launcher (tavola `Pagine`), e la chat e' la **pagina 0**: c'e'
sempre, non si sposta e non si toglie.

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
    appendChild(c) { this.children.push(c); c.parent = this; return c; },
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
/* Il pannello della chat c'e' gia' nell'HTML e non ha `data-id`: e' la
   pagina 0, e il controller non deve toccarlo mai. */
const pannelloChat = creaEl(null, 'casa-pagina');
pannelloChat.dataset.pagina = 'chat';
pista.appendChild(pannelloChat);
/* La striscia dei pallini: dove sei, e la presa del cassetto. */
const striscia = creaEl('casa-pallini', 'casa-pallini');
striscia.textContent = '';
/* Il foglio «Le pagine di casa» e i nodi che riempie. */
const foglio = creaEl('casa-pagine-dialog', 'casa-foglio-pagine');
const elenco = creaEl('casa-foglio-elenco', 'casa-foglio-elenco');
for (const id of ['casa-foglio-titolo', 'casa-foglio-occhiello',
                  'casa-foglio-nota-cassetto', 'casa-foglio-nota-viva']) creaEl(id, '');

/* Tenere premuto: giu', mezzo secondo, su. Il timer e' quello vero del
   modulo condiviso, quindi si aspetta davvero. */
async function tieniPremuto({ muovi = 0 } = {}) {
  lancia(striscia, 'touchstart', { touches: [{ clientX: 200, clientY: 550 }], target: striscia });
  if (muovi) {
    lancia(striscia, 'touchmove', {
      touches: [{ clientX: 200 + muovi, clientY: 550 }], preventDefault() {},
    });
  }
  await new Promise((r) => setTimeout(r, 620));
  lancia(striscia, 'touchend', { changedTouches: [{ clientX: 200 + muovi, clientY: 550 }] });
}
globalThis.document = {
  getElementById: (id) => elementi.get(id) || null,
  createElement: (t) => creaEl(null, ''),
  documentElement: creaEl('html', ''),
  body: creaEl('body'),
};
globalThis.window = { innerWidth: 400 };
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
  lancia(pista, 'touchstart', { touches: [{ clientX: x0, clientY: 100 }], target: pista });
  lancia(pista, 'touchmove', {
    touches: [{ clientX: x0 + dx, clientY: 100 }],
    preventDefault() {},
  });
  lancia(pista, 'touchend', { changedTouches: [{ clientX: x0 + dx, clientY: 100 }] });
}
/* Un gesto verticale sulla striscia. `su` in pixel. */
function tiraSu(su, { obliquo = 0 } = {}) {
  lancia(striscia, 'touchstart', { touches: [{ clientX: 200, clientY: 500 }] });
  lancia(striscia, 'touchend', {
    changedTouches: [{ clientX: 200 + obliquo, clientY: 500 - su }],
  });
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
            const app = {
              view: VISTA,
              launcher: { isOpen: () => false },
              appsSource: () => ({
                ensureLoaded() {},
                jennyApps: [
                  { slug: 'orto', name: 'Orto' },
                  { slug: 'rotta', name: 'Rotta', broken: true },
                  { slug: 'fuori', name: 'Fuori', view_kind: 'external' },
                ],
              }),
            };
            const pagine = new CasaPagine(app);
            await pagine.carica();
            """
        )
        + corpo
    )


def _run(corpo: str, schermate: list[dict] | None = None, vista: str = "chat") -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        radice = Path(tmp)
        (radice / "shared").mkdir()
        shutil.copy(ASSETS / "casa-pagine.js", radice / "casa-pagine.js")
        shutil.copy(
            ASSETS / "shared" / "gesto-orizzontale.js",
            radice / "shared" / "gesto-orizzontale.js",
        )
        # Il client API finto: risponde quel che il caso vuole, e ricorda le
        # scritture. Non si tocca la rete e non si tocca `config.json`.
        (radice / "shared" / "i18n.js").write_text(
            "export const i18n = {\n"
            "  t: (k, v) => k + (v ? ':' + JSON.stringify(v) : ''),\n"
            "  onLocaleChange() {},\n"
            "};\n",
            encoding="utf-8",
        )
        (radice / "shared" / "api-client.js").write_text(
            "export const api = {\n"
            f"  _elenco: {json.dumps(schermate or [])},\n"
            "  scritture: [],\n"
            "  async getSchermate() { return { schermate: this._elenco, max: 8 }; },\n"
            "  async setSchermate(s) { this.scritture.push(s); this._elenco = s; return s; },\n"
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
DUE = UNA + [{"id": "p2", "kind": "stanza", "ref": "pages"}]


# ── La chat e' la pagina 0 ──────────────────────────────────────────────────


def test_a_fresh_home_is_just_the_chat() -> None:
    _run("assert.equal(pagine.quante, 1); assert.equal(pagine.indice, 0);")


def test_with_no_pages_the_gesture_does_not_even_start() -> None:
    """Un elastico che risponde a vuoto sembra un difetto, non un confine.

    Con la sola chat non c'e' nessun posto dove andare: meglio che il dito non
    muova niente, invece di far tremare la conversazione per dire «di la' non
    c'e' nulla» quando di la' non c'e' nulla **mai**.
    """
    _run(
        "pista.style.transform = undefined;\n"
        "scorri(SINISTRA);\n"
        "assert.equal(pagine.indice, 0);\n"
        # `vaiA` scrive sempre un transform: se il gesto fosse partito e poi
        # tornato indietro, qui ce ne sarebbe uno. Che resti `undefined` vuol
        # dire che il dito non ha armato proprio niente.
        "assert.equal(pista.style.transform, undefined, 'il gesto e partito');"
    )


def test_the_chat_panel_is_never_redrawn() -> None:
    """Ridisegnarlo vorrebbe dire buttare via la conversazione a ogni salvataggio.

    Il pannello della chat sta nell'HTML — dentro ci vivono il filo e il
    composer — e i pannelli aggiunti gli si mettono accanto.
    """
    _run(
        "assert.equal(pista.children[0], pannelloChat);\n"
        "await pagine.salva(SCHERMATE.concat([{id:'p3',kind:'app',ref:'x'}]));\n"
        "assert.equal(pista.children[0], pannelloChat, 'la chat e stata ridisegnata');\n"
        "assert.equal(pista.children.length, 4);",
        schermate=DUE,
    )


# ── Dove si va ──────────────────────────────────────────────────────────────


def test_swiping_left_moves_to_the_next_page() -> None:
    _run("scorri(SINISTRA); assert.equal(pagine.indice, 1);", schermate=DUE)


def test_swiping_right_comes_back() -> None:
    _run(
        "scorri(SINISTRA); scorri(SINISTRA);\n"
        "assert.equal(pagine.indice, 2);\n"
        "scorri(DESTRA);\n"
        "assert.equal(pagine.indice, 1);",
        schermate=DUE,
    )


def test_the_ends_do_not_wrap_around() -> None:
    """Le linguette dell'officina si richiudono in cerchio, le pagine no.

    Li' le voci sono quattro e note; qui quante siano lo decide l'utente, e
    girando in tondo fra otto pagine non si sa piu' dove si e'.
    """
    _run("scorri(DESTRA); assert.equal(pagine.indice, 0);", schermate=DUE)
    _run(
        "scorri(SINISTRA); scorri(SINISTRA); scorri(SINISTRA);\n"
        "assert.equal(pagine.indice, 2);",
        schermate=DUE,
    )


def test_a_short_drag_springs_back() -> None:
    _run("scorri(SINISTRA, {corto: true}); assert.equal(pagine.indice, 0);", schermate=DUE)


# ── Le guardie ──────────────────────────────────────────────────────────────


def test_inside_a_room_the_rooms_are_in_charge() -> None:
    """Da «Tu e Jenny» il dito non deve cambiare pagina sotto la stanza."""
    _run("scorri(SINISTRA); assert.equal(pagine.indice, 0);", schermate=DUE, vista="tu")


def test_an_open_drawer_owns_the_gesture() -> None:
    _run(
        "app.launcher.isOpen = () => true;\n"
        "scorri(SINISTRA);\n"
        "assert.equal(pagine.indice, 0);",
        schermate=DUE,
    )


# ── Il salvataggio ──────────────────────────────────────────────────────────


def test_saving_sends_the_whole_list(tmp_path: Path) -> None:
    """Aggiungere, togliere e spostare sono la stessa scrittura."""
    _run(
        "await pagine.salva([SCHERMATE[1]]);\n"
        "const api = (await import('./shared/api-client.js')).api;\n"
        "assert.equal(api.scritture.length, 1);\n"
        "assert.deepEqual(api.scritture[0], [SCHERMATE[1]]);\n"
        "assert.equal(pagine.quante, 2);",
        schermate=DUE,
    )


def test_removing_the_page_you_are_on_does_not_strand_you() -> None:
    """Si resta dentro la pista, non su una casella che non c'e' piu'."""
    _run(
        "scorri(SINISTRA); scorri(SINISTRA);\n"
        "assert.equal(pagine.indice, 2);\n"
        "await pagine.salva([SCHERMATE[0]]);\n"
        "assert.equal(pagine.quante, 2);\n"
        "assert.ok(pagine.indice <= 1, `indice fuori pista: ${pagine.indice}`);",
        schermate=DUE,
    )


def test_a_read_that_fails_leaves_the_chat_standing() -> None:
    """Una casa che non apre perche' non ha saputo leggere le sue pagine e'
    peggio di una casa con la sola chat."""
    _run(
        "const api = (await import('./shared/api-client.js')).api;\n"
        "api.getSchermate = async () => { throw new Error('rete giu'); };\n"
        "const altre = new CasaPagine(app);\n"
        "await altre.carica();\n"
        "assert.equal(altre.quante, 1);",
        schermate=DUE,
    )


# ── La forma nel documento e nel foglio ─────────────────────────────────────


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
    assert ".casa-shell:not([data-view='chat']) .casa-pista,\n.casa-shell:not([data-view='chat']) .casa-pallini { display: none; }" in css
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


# ── La striscia: dove sei, e la presa del cassetto ──────────────────────────


def test_the_strip_is_there_even_with_only_the_chat() -> None:
    """E' l'unica cosa a schermo che annunci i gesti.

    Ha sostituito il pulsante del cassetto: se sparisse quando non ci sono
    pagine, con una casa appena installata non resterebbe **nessun** ingresso
    al cassetto e nessun indizio che il gesto esista.
    """
    _run("assert.equal(striscia.children.length, 1);")


def test_one_dot_per_page_and_the_current_one_is_marked() -> None:
    _run(
        "assert.equal(striscia.children.length, 3);\n"
        "assert.equal(striscia.children[0].attrs['aria-selected'], 'true');\n"
        "scorri(SINISTRA);\n"
        "assert.equal(striscia.children.length, 3, 'i pallini si sono accumulati');\n"
        "assert.equal(striscia.children[1].attrs['aria-selected'], 'true');\n"
        "assert.equal(striscia.children[0].attrs['aria-selected'], 'false');",
        schermate=DUE,
    )


def test_every_dot_is_a_real_button_that_moves() -> None:
    """Chi i gesti non li fa — o non puo' farli — cambia pagina toccando."""
    _run(
        "assert.equal(striscia.children[2].attrs.role, 'tab');\n"
        "striscia.children[2].click();\n"
        "assert.equal(pagine.indice, 2);",
        schermate=DUE,
    )


def test_pulling_up_opens_the_drawer() -> None:
    _run(
        "let aperto = 0;\n"
        "app.openLauncher = () => { aperto += 1; };\n"
        "tiraSu(60);\n"
        "assert.equal(aperto, 1);"
    )


def test_a_small_nudge_is_not_a_pull() -> None:
    _run(
        "let aperto = 0;\n"
        "app.openLauncher = () => { aperto += 1; };\n"
        "tiraSu(10);\n"
        "assert.equal(aperto, 0);"
    )


def test_a_sideways_drag_across_the_strip_is_not_a_pull() -> None:
    """Sulla striscia passa anche il dito che sta cambiando pagina.

    Senza il confronto fra verticale e orizzontale, cambiare pagina con un
    dito basso aprirebbe il cassetto a meta' gesto.
    """
    _run(
        "let aperto = 0;\n"
        "app.openLauncher = () => { aperto += 1; };\n"
        "tiraSu(40, {obliquo: 200});\n"
        "assert.equal(aperto, 0);"
    )


def test_the_old_drawer_button_is_gone_from_the_home() -> None:
    """Due ingressi sarebbero uno di troppo, e la striscia non insegnerebbe piu'
    niente: il gesto resterebbe sconosciuto perche' il bottone basta."""
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert 'id="casa-drawer"' not in html
    assert 'id="casa-pallini"' in html
    # L'officina tiene il suo: e' un elemento diverso sullo stesso foglio.
    officina = (UI / "officina.html").read_text(encoding="utf-8")
    assert 'id="btn-launcher"' in officina


def test_jenny_stands_above_the_strip_not_on_it() -> None:
    """Il pavimento della mascotte conta anche la striscia.

    Senza, Jenny si appoggerebbe sopra la presa con cui si tira su il
    cassetto — cioe' sopra il comando che ha appena sostituito un bottone.
    """
    app_js = (ASSETS / "casa-app.js").read_text(encoding="utf-8")
    misura = app_js.split("const measure = () => {", 1)[1].split("};", 1)[0]
    assert "casa-pallini" in misura, "la striscia non entra nel pavimento"
    assert "h + striscia" in misura


# ── Il foglio «Le pagine di casa» ───────────────────────────────────────────


def test_holding_the_dots_opens_the_sheet() -> None:
    _run("await tieniPremuto(); assert.equal(foglio.open, true);")


def test_a_finger_that_moves_is_not_a_hold() -> None:
    """Chi cambia pagina, o tira su il cassetto, non si ritrova il foglio.

    La pressione la riconosce il modulo condiviso proprio per questo:
    annullarla quando il dito si muove vuol dire guardare i movimenti, e i
    movimenti si guardano in un posto solo.
    """
    _run("await tieniPremuto({muovi: 120}); assert.equal(foglio.open, false);")


def test_a_pull_up_is_not_a_hold() -> None:
    """Il verso opposto dell'altra prova: tirare su non apre il foglio.

    Qui non salva la guardia sull'orizzontale — un dito verticale non arma
    quel ramo — ma `azzera()`, che disarma la pressione quando la dominanza
    non passa. Senza una delle due, tirare su lentamente aprirebbe il foglio
    invece del cassetto.
    """
    _run(
        "lancia(striscia, 'touchstart', {touches: [{clientX: 200, clientY: 550}], target: striscia});\n"
        "lancia(striscia, 'touchmove', {touches: [{clientX: 200, clientY: 480}], preventDefault(){}});\n"
        "await new Promise((r) => setTimeout(r, 620));\n"
        "lancia(striscia, 'touchend', {changedTouches: [{clientX: 200, clientY: 480}]});\n"
        "assert.equal(foglio.open, false, 'tirare su ha aperto il foglio');"
    )


def test_a_hold_after_a_tap_still_needs_its_own_half_second() -> None:
    """Ogni tocco riparte da zero: il contatore del precedente non lo aiuta.

    Il primo tocco arma un contatore. Se non venisse disarmato al tocco
    successivo resterebbe pendente, e la pressione che arriva subito dopo si
    aprirebbe **in anticipo** — al mezzo secondo del *primo* tocco, non del
    suo. Non e' un'apertura spuria (la guardia su `inAscolto` copre il caso in
    cui il dito non c'e' piu'): e' mezzo gesto contato due volte, che dal dito
    si sente come un foglio che scatta prima del dovuto.

    Misurato dopo che la mutazione «`azzera()` non disarma» era sopravvissuta a
    una prima stesura che raccontava un difetto piu' grosso di quello vero.
    """
    _run(
        "lancia(striscia, 'touchstart', {touches: [{clientX: 200, clientY: 550}], target: striscia});\n"
        "lancia(striscia, 'touchend', {changedTouches: [{clientX: 200, clientY: 550}]});\n"
        "await new Promise((r) => setTimeout(r, 120));\n"
        "lancia(striscia, 'touchstart', {touches: [{clientX: 200, clientY: 550}], target: striscia});\n"
        "await new Promise((r) => setTimeout(r, 400));\n"
        "assert.equal(foglio.open, false, 'il foglio si e aperto col contatore del tocco prima');\n"
        "await new Promise((r) => setTimeout(r, 220));\n"
        "assert.equal(foglio.open, true, 'la pressione vera non ha aperto niente');"
    )


def test_holding_does_not_also_open_the_drawer() -> None:
    """I due gesti convivono sulla striscia e non devono sommarsi."""
    _run(
        "let aperto = 0;\n"
        "app.openLauncher = () => { aperto += 1; };\n"
        "await tieniPremuto();\n"
        "assert.equal(foglio.open, true);\n"
        "assert.equal(aperto, 0, 'la pressione ha aperto anche il cassetto');"
    )


def test_the_sheet_lists_the_pages_and_offers_the_next_one() -> None:
    _run(
        "await tieniPremuto();\n"
        "const righe = elenco.children.filter((c) => c.className === 'casa-foglio-riga');\n"
        "assert.equal(righe.length, 2);\n"
        "const libera = elenco.children.filter((c) => c.className === 'casa-foglio-libera');\n"
        "assert.equal(libera.length, 1, 'manca la riga vuota con la scelta');",
        schermate=DUE,
    )


def test_the_three_choices_are_the_boards_three() -> None:
    """E il cassetto non e' fra queste: ce l'hai gia' tirando su."""
    _run(
        "await tieniPremuto();\n"
        "const libera = elenco.children.find((c) => c.className === 'casa-foglio-libera');\n"
        "const scelte = libera.children[1].children.map((b) => b.dataset.kind);\n"
        "assert.deepEqual(scelte, ['app', 'stanza', 'conversazione']);",
        schermate=UNA,
    )


def test_with_the_ceiling_full_there_is_nothing_to_add() -> None:
    piene = [{"id": f"p{i}", "kind": "app", "ref": "x"} for i in range(8)]
    _run(
        "await tieniPremuto();\n"
        "assert.equal(elenco.children.filter((c) => c.className === 'casa-foglio-libera').length, 0);",
        schermate=piene,
    )


def test_removing_a_page_saves_the_rest() -> None:
    _run(
        "await tieniPremuto();\n"
        "const riga = elenco.children.find((c) => c.dataset.id === 'p1');\n"
        "riga.children[2].click();\n"
        "await new Promise((r) => setTimeout(r, 10));\n"
        "const api = (await import('./shared/api-client.js')).api;\n"
        "assert.deepEqual(api.scritture[0].map((x) => x.id), ['p2']);",
        schermate=DUE,
    )


# ── Il secondo passo: quale ─────────────────────────────────────────────────


def test_a_broken_or_external_app_cannot_become_a_page() -> None:
    """Una pagina fissa rotta resterebbe li' a non funzionare tutti i giorni, e
    un'app esterna apre un indirizzo che il guscio non controlla."""
    _run(
        "await tieniPremuto();\n"
        "const voci = await pagine._voci('app');\n"
        "assert.deepEqual(voci.map((v) => v.ref), ['orto']);"
    )


def test_the_notebooks_room_is_not_offered() -> None:
    """`openPages()` legge il quaderno **dalla conversazione corrente**.

    Appesa a una pagina mostrerebbe cose diverse a seconda di dov'eri prima.
    La tavola la disegna come esempio, ma quella stanza fissa non esiste: chi
    vuole un quaderno sotto il pollice ci mette la sua conversazione.
    """
    _run(
        "const voci = await pagine._voci('stanza');\n"
        "assert.ok(!voci.some((v) => v.ref === 'pages'), 'i quaderni sono in elenco');\n"
        "assert.ok(voci.some((v) => v.ref === 'tu'));"
    )


def test_room_labels_are_the_rooms_own() -> None:
    """Due copie dello stesso nome divergono, e la seconda si scopre quando
    qualcuno rinomina la prima."""
    _run(
        "const voci = await pagine._voci('stanza');\n"
        "for (const v of voci) assert.match(v.nome, /^casa\\.[a-z]+\\.title$/);"
    )


def test_choosing_lands_you_on_the_new_page() -> None:
    """Chi l'ha appena aggiunta vuole vederla: restare sulla chat gli farebbe
    credere che non sia successo niente."""
    _run(
        "await tieniPremuto();\n"
        "await pagine._aggiungi('app', 'orto');\n"
        "assert.equal(pagine.quante, 2);\n"
        "assert.equal(pagine.indice, 1);\n"
        "assert.equal(foglio.open, false, 'il foglio e rimasto aperto');"
    )
