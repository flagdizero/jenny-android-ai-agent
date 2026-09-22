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
    addEventListener(t, fn) { ascolto[t] = fn; },
    removeEventListener() {},
    appendChild(c) { this.children.push(c); c.parent = this; },
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
globalThis.document = {
  getElementById: (id) => elementi.get(id) || null,
  createElement: (t) => creaEl(null, ''),
  body: creaEl('body'),
};
globalThis.window = { innerWidth: 400 };
globalThis.getComputedStyle = () => ({ overflowX: 'visible' });

/* Un gesto completo sulla pista. `verso` +1 = dito a destra (pagina
   precedente), -1 = dito a sinistra (pagina successiva). */
function scorri(verso, { corto = false } = {}) {
  const x0 = 200;
  // soglia = max(60, 400*0.22) = 88: corto resta sotto, lungo la supera
  const dx = verso * (corto ? 20 : 200);
  pista.ascolto.touchstart?.({ touches: [{ clientX: x0, clientY: 100 }], target: pista });
  pista.ascolto.touchmove?.({
    touches: [{ clientX: x0 + dx, clientY: 100 }],
    preventDefault() {},
  });
  pista.ascolto.touchend?.({ changedTouches: [{ clientX: x0 + dx, clientY: 100 }] });
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
            const app = { view: VISTA, launcher: { isOpen: () => false } };
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
    assert ".casa-shell:not([data-view='chat']) .casa-pista { display: none; }" in css
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
