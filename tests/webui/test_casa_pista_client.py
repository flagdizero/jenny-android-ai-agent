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
    /* **Sposta**, come il DOM vero: un nodo sta in un posto solo. Senza,
       una stanza prestata a una pagina resterebbe anche nel guscio, e la
       prova «la stanza torna a casa» sarebbe vera comunque — cioe' verde su
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
            /* Il guscio finto: la stanza e' un elemento vero che vive nel
               guscio, cosi' il banco vede se viene restituito o distrutto. */
            const guscio = creaEl('casa-shell', 'casa-shell');
            const stanzaTu = creaEl('casa-tu', 'casa-tu');
            guscio.appendChild(stanzaTu);
            let aperture = 0;
            const app = {
              view: VISTA,
              shell: guscio,
              launcher: { isOpen: () => false },
              prestaStanza: (ref) => {
                if (ref !== 'tu') return null;
                aperture += 1;
                return stanzaTu;
              },
              restituisciStanza: (el) => { guscio.appendChild(el); },
              /* La fonte risponde **dopo un giro**, come la rete vera: al
                 momento della domanda `jennyApps` e' ancora vuota. Un finto
                 che risponde subito avrebbe lasciato passare il difetto visto
                 sul telefono il 22 settembre 2026 — «non hai Jenny App» a chi
                 ne aveva quattro. */
              appsSource: () => ({
                ensureLoaded() {},
                jennyApps: [],
                attendiJennyApps() {
                  return new Promise((r) => setTimeout(() => {
                    this.jennyApps = [
                      { slug: 'orto', name: 'Orto' },
                      { slug: 'rotta', name: 'Rotta', broken: true },
                      { slug: 'fuori', name: 'Fuori', view_kind: 'external' },
                    ];
                    r(this.jennyApps);
                  }, 30));
                },
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
        # La cornice di una Jenny App: qui basta sapere **che** viene
        # costruita e con quale slug — il vero `cornicePerApp` mette il token
        # e i colori nell'indirizzo, e quello si prova dove vive.
        (radice / "shared" / "apps-actions.js").write_text(
            "export function cornicePerApp(slug) {\n"
            "  const f = document.createElement('iframe');\n"
            "  f.dataset.slug = slug;\n"
            "  return f;\n"
            "}\n",
            encoding="utf-8",
        )
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
            # Il segreto parte **vuoto**, come al primo avvio: la cornice di
            # un'app se lo porta nell'indirizzo, e chi la costruisce senza
            # aspettarlo produce un 401 e una pagina bianca.
            "  _segreto: '',\n"
            "  getSecret() { return this._segreto; },\n"
            "  async bootstrap() { this._segreto = 'ok'; },\n"
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
DUE = UNA + [{"id": "p2", "kind": "stanza", "ref": "tu"}]


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
    la striscia fa le due cose che funzionano: dire dove sei, e aprire il
    foglio se la tieni premuta.

    Il banco tiene il codice **onesto**: niente ascoltatori che aspettano un
    evento che il sistema non manda mai.
    """
    sorgente = (ASSETS / "casa-pagine.js").read_text(encoding="utf-8")
    assert "openLauncher" not in sorgente, (
        "la striscia prova di nuovo ad aprire il cassetto: quel gesto non "
        "arriva mai all'app con la navigazione a gesti"
    )
    assert "touchend" not in sorgente, "un ascoltatore che il sistema non fa scattare"


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


def test_the_choices_are_two_and_the_missing_ones_are_decided() -> None:
    """La tavola ne disegna tre; qui sono due, e le assenze hanno un motivo.

    Il **cassetto** ce l'hai gia' tirando su — lo dice la tavola stessa. Una
    **conversazione** no perche' la chat in casa e' una sola: un filo, un campo
    di scrittura, un collegamento. Una pagina cosi' non avrebbe contenuto
    proprio, potrebbe solo far cambiare conversazione a quella che c'e' gia'
    — e cambiarla e' gia' un tocco sul titolo.
    """
    _run(
        "await tieniPremuto();\n"
        "const libera = elenco.children.find((c) => c.className === 'casa-foglio-libera');\n"
        "const scelte = libera.children[1].children.map((b) => b.dataset.kind);\n"
        "assert.deepEqual(scelte, ['app', 'stanza']);",
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
        schermate=DUE,
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


def test_a_room_page_mounts_no_app() -> None:
    """Una stanza non deve montare una cornice d'app per sbaglio.

    Un `<iframe>` su `/apps/tu/index.html` sarebbe un 404 a tutta pagina, e da
    fuori somiglierebbe a un'app che non parte.
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
        "assert.equal(pagine.nomeDi({kind: 'stanza', ref: 'tu'}), 'casa.tu.title');\n"
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
    assert '<main class="casa-shell" data-view="chat" data-pagina="0">' in html, (
        "il guscio non nasce dichiarando su che pagina e'"
    )
    css = (ASSETS / "casa-style.css").read_text(encoding="utf-8")
    assert ".casa-shell:not([data-pagina='0']) .casa-who-open .ti-chevron-down" in css
    app_js = (ASSETS / "casa-app.js").read_text(encoding="utf-8")
    cambio = app_js.split("onPaginaCambiata(", 1)[1].split("_applyHead();", 1)[0]
    assert "data-pagina" in cambio, "l'attributo non viene aggiornato al cambio pagina"


# ── Le stanze: prestate, non copiate ────────────────────────────────────────


def test_a_room_page_borrows_the_real_room() -> None:
    """Un elemento solo: i controller l'hanno preso per id alla costruzione, e
    duplicarlo vorrebbe dire due nodi con lo stesso id."""
    _run(
        "scorri(SINISTRA); scorri(SINISTRA);\n"
        "const pagina = pista.children.find((c) => c.dataset.id === 'p2');\n"
        "assert.equal(pagina.children[0], stanzaTu, 'la pagina non ha la stanza vera');\n"
        "assert.equal(aperture, 1, 'la stanza non e stata riempita');",
        schermate=DUE,
    )


def test_leaving_a_room_page_gives_the_room_back() -> None:
    """**Il difetto che questa prova esiste per impedire.**

    `textContent = ''` su una pagina che tiene una stanza prestata non svuota
    un contenitore: cancella la stanza vera. Da quel momento aprirla dal
    percorso normale — «Tu e Jenny» dal menu — non mostra piu' niente, e il
    guasto si vede una schermata dopo, dove non somiglia affatto alla causa.
    """
    _run(
        "scorri(SINISTRA); scorri(SINISTRA);\n"
        "scorri(DESTRA); scorri(DESTRA);\n"
        "assert.equal(guscio.children.includes(stanzaTu), true,\n"
        "  'la stanza non e tornata nel guscio: aprirla dal menu non mostrerebbe niente');\n"
        "const pagina = pista.children.find((c) => c.dataset.id === 'p2');\n"
        "assert.equal(pagina.children.length, 0);",
        schermate=DUE,
    )


def test_the_css_lights_the_room_inside_the_page() -> None:
    """Le stanze sono `display:none` e le accende la regola della vista.

    Dentro una pagina la vista e' ancora `chat`: senza una seconda via la
    pagina resterebbe vuota, e sembrerebbe che il prestito non abbia
    funzionato mentre l'elemento e' li'.
    """
    css = (ASSETS / "casa-style.css").read_text(encoding="utf-8")
    assert ".casa-pagina[data-kind='stanza'] > * { display: flex; }" in css


def test_the_notebook_rooms_are_not_lendable() -> None:
    """`pages` e `reader` dipendono da dove eri prima."""
    app_js = (ASSETS / "casa-app.js").read_text(encoding="utf-8")
    tabella = app_js.split("const STANZE_IN_PAGINA = {", 1)[1].split("};", 1)[0]
    assert "pages:" not in tabella and "reader:" not in tabella
    for stanza in ("tu", "jenny", "model", "updates", "backup"):
        assert f"{stanza}:" in tabella


def test_the_app_list_is_waited_for_not_just_started() -> None:
    """**Il difetto visto sul telefono il 22 settembre 2026.**

    `ensureLoaded()` non e' asincrona: avvia le due fetch e torna subito.
    Chi ci mette un `await` davanti aspetta `undefined`, cioe' niente — e
    decide che l'elenco e' vuoto un istante prima che arrivi. A schermo:
    «non hai Jenny App» a un utente che ne aveva quattro.

    Il banco lo prende perche' la fonte finta risponde **dopo un giro**, come
    la rete vera.
    """
    _run(
        "const voci = await pagine._voci('app');\n"
        "assert.deepEqual(voci.map((v) => v.ref), ['orto'],\n"
        "  'l elenco e stato letto prima che arrivasse');"
    )


def test_the_empty_message_is_only_for_a_real_empty_list() -> None:
    """Aprire la scelta e trovare «non hai Jenny App» mentre le hai e' peggio
    di aspettare: si crede a quel che c'e' scritto."""
    _run(
        "await tieniPremuto();\n"
        "await pagine._apriScelta('app');\n"
        "const testi = elenco.children.map((c) => c.className);\n"
        "assert.ok(!testi.includes('casa-foglio-nota'),\n"
        "  'ha detto che non ci sono app');\n"
        "assert.equal(elenco.children.length, 2, 'Indietro piu una app');"
    )


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
        "pagine.vaiA(0); pagine.vaiA(1); pagine.vaiA(0); pagine.vaiA(1);\n"
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
    vetrina = css.split(".casa-vetrina {", 1)[1].split("}", 1)[0]
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
