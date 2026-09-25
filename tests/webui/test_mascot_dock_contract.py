"""I due ancoraggi di Jenny esistono una volta sola.

«Al bordo» e «venuta fuori» sono due frazioni del suo quadrato, e fino al
19/09/2026 erano scritte in tre posti: due volte nel foglio dell'officina, una
nel JS che decide dove far finire la camminata di rientro dopo un lancio. La
casa stava per aggiungerne altre due.

Una copia sbagliata qui non si vede come un errore: si vede come **una Jenny
che scivola oltre il punto in cui doveva fermarsi**, o che torna a piedi verso
un bordo che il CSS ha spostato. Non c'e' un test di comportamento che possa
prenderla — la fisica gira a rAF su rettangoli veri — e sullo schermo sembra
solo un'animazione un po' storta.

Percio' il numero adesso sta in ``shared/mascot.js`` e arriva ai due fogli come
variabile CSS. Questo e' il test che tiene chiusa la porta da cui e' uscito.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
MASCOT_JS = ASSETS / "shared" / "mascot.js"
DRAG_JS = ASSETS / "shared" / "mascot-drag.js"
FOGLI = {
    "officina": ASSETS / "mobile-style.css",
    "casa": ASSETS / "home-style.css",
}


ANCORAGGIO = re.compile(r"(?:left|right):\s*calc\([^;]*--jenny-size[^;]*\);")
SPRITE = (r"\.jenny-duo",)


def _anchors(css: str) -> list[str]:
    """Le dichiarazioni di ancoraggio *della mascotte*.

    Scritte cosi' e non con una grep sul file intero perche' --jenny-size la
    legge anche chi le deve lasciare spazio: il bottone di rientro della chat
    si posiziona sulla sua taglia, e quello non e' un ancoraggio da tenere
    allineato — e' un margine.
    """
    trovate = []
    for blocco in css.split("}"):
        selettore, _, corpo = blocco.rpartition("{")
        if not corpo or not any(re.search(s, selettore) for s in SPRITE):
            continue
        trovate += ANCORAGGIO.findall(corpo)
    return trovate


def _ratio(name: str) -> float:
    m = re.search(rf"export const {name} = ([0-9.]+);", MASCOT_JS.read_text(encoding="utf-8"))
    assert m, f"{name} non e' piu' esportata da shared/mascot.js"
    return float(m.group(1))


def test_the_two_anchors_live_in_the_shared_module() -> None:
    dock, out = _ratio("DOCK_RATIO"), _ratio("OUT_RATIO")
    assert dock > out, (
        "al bordo deve restare fuori dallo schermo piu' quadrato che venuta fuori: "
        f"dock={dock} out={out}. Invertiti, il tocco la nasconde uscendo."
    )
    src = MASCOT_JS.read_text(encoding="utf-8")
    assert "export const OUT_SHIFT_RATIO = DOCK_RATIO - OUT_RATIO;" in src, (
        "lo scarto fra i due ancoraggi e' tornato a essere un numero scritto a mano"
    )


@requires_node
def test_the_anchors_reach_the_css_before_any_sprite_exists() -> None:
    """All'import, e non da un costruttore.

    `mobile-jenny.js` attacca lo sprite al documento **prima** di chiamare
    `applyMascotSize()`: un `calc()` con una variabile non ancora definita non
    e' il valore di prima, e' una dichiarazione invalida. Jenny comparirebbe
    per un frame dove la mette il flusso invece che sul bordo.

    Il modulo si importa davvero e non si chiama niente: e' l'unico modo di
    provare *quando* succede, oltre che cosa.
    """
    sorgente = """
const scritte = new Map();
globalThis.document = {
  documentElement: { style: { setProperty(k, v) { scritte.set(k, v); } } },
};
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
import assert from 'node:assert/strict';
const mod = await import(__URL__);
assert.deepEqual(
  [scritte.get('--jenny-dock'), scritte.get('--jenny-out'), scritte.get('--jenny-art-h')],
  [String(mod.DOCK_RATIO), String(mod.OUT_RATIO), String(mod.ART_HEIGHT_RATIO)],
  'i rapporti non sono arrivati al documento importando il modulo: ' +
    JSON.stringify([...scritte]),
);
""".replace("__URL__", json.dumps(MASCOT_JS.as_uri()))
    run_js(sorgente, timeout=30)


def test_no_stylesheet_spells_the_ratio_out_again() -> None:
    """Ogni ancoraggio della mascotte nomina la variabile. Un `-0.469`
    riapparso qui e' la copia numero due.

    Dal 24/09/2026 lo sprite e' uno solo (`.jenny-duo`, `shared/jenny-mascot.js`)
    e i suoi ancoraggi stanno in un foglio solo, quello dell'officina, che la
    casa carica. La casa non ne dichiara nessuno: se ne ricomparisse uno li',
    sarebbe un secondo posto dove Jenny si ancora — cioe' di nuovo due Jenny."""
    ancoraggi = _anchors(FOGLI["officina"].read_text(encoding="utf-8"))
    # Un bordo solo dal 24/09/2026 (Jenny sta sempre a destra): due ancoraggi,
    # al dock e fuori.
    assert len(ancoraggi) >= 2, (
        f"mi aspetto i due ancoraggi del bordo destro, ne trovo {len(ancoraggi)}"
    )
    for decl in ancoraggi:
        assert "--jenny-dock" in decl or "--jenny-out" in decl, (
            f"ancoraggio con un numero suo invece della variabile: {decl}"
        )
    usate = {v for v in ("--jenny-dock", "--jenny-out") if any(v in d for d in ancoraggi)}
    assert usate == {"--jenny-dock", "--jenny-out"}, (
        f"usa solo {usate or 'nessuno'} — uno dei due stati non e' piu' ancorato"
    )
    assert not _anchors(FOGLI["casa"].read_text(encoding="utf-8")), (
        "la casa ancora Jenny per conto suo: fra i due gusci deve cambiare solo il pavimento"
    )


def test_the_walk_home_uses_the_same_number_as_the_css() -> None:
    """La camminata di rientro finisce esattamente sul bordo perche' anticipa
    lo scarto fra i due ancoraggi. Se lo scarto non e' quello del CSS, lei
    arriva e poi scivola: la parte che sembra «l'animazione e' storta»."""
    src = DRAG_JS.read_text(encoding="utf-8")
    assert "from './mascot.js'" in src
    # Importarla non basta: la riga che calcola lo scarto deve *nominarla*.
    # Una costante importata e poi non usata e' esattamente cio' che resta
    # quando qualcuno rimette il numero a mano una riga piu' giu'.
    usi = [
        riga for riga in src.splitlines()
        if "OUT_SHIFT_RATIO" in riga and "import" not in riga
    ]
    assert usi, "OUT_SHIFT_RATIO e' importata ma non la usa nessuno"
    assert not re.search(r"0\.469|0\.25\b|0\.219", src), (
        "la fisica si e' riscritta in casa i numeri degli ancoraggi"
    )


def test_neither_shell_is_the_exception_any_more() -> None:
    """`hasOut` esisteva per dire «in casa lo stato non c'e'». Adesso c'e' in
    tutti e due, e un interruttore con un valore solo e' un ramo morto che il
    prossimo lettore prende per una possibilita' vera."""
    for f in (DRAG_JS, ASSETS / "mobile-jenny.js", ASSETS / "shared" / "jenny-mascot.js"):
        corpo = "\n".join(
            riga for riga in f.read_text(encoding="utf-8").splitlines()
            if "hasOut" in riga and not riga.lstrip().startswith((" *", "*", "//", "/*"))
        )
        assert not corpo, f"{f.name} parla ancora di hasOut:\n{corpo}"


def test_both_shells_answer_the_tap() -> None:
    """Il tocco secco e' l'unico modo *scopribile* di metterla via — lo swipe
    lo trova chi lo cerca. Se la mascotte smette di passare `onTap`, il modulo
    condiviso ha un default che non fa niente: si perde in silenzio.

    La risposta al tocco sta nella mascotte condivisa, e i due gusci l'hanno
    perche' usano quella: l'officina la estende, la casa la crea."""
    src = (ASSETS / "shared" / "jenny-mascot.js").read_text(encoding="utf-8")
    assert re.search(r"onTap:.*'out'", src), "la mascotte non gira piu' lo stato al tocco"
    assert "isOut:" in src and "setOut:" in src, "la mascotte non dichiara piu' lo stato"
    officina = (ASSETS / "mobile-jenny.js").read_text(encoding="utf-8")
    assert "class JennyCompanion extends JennyMascot" in officina
    assert "bindMascotDrag" not in officina, "l'officina lega di nuovo la fisica per conto suo"
    casa = (ASSETS / "home-app.js").read_text(encoding="utf-8")
    assert "new JennyMascot(" in casa
    assert not (ASSETS / "casa-mascot.js").exists(), "e' tornata la seconda mascotte"


def test_the_room_that_leaves_her_space_uses_her_height_and_not_her_width() -> None:
    """Il fondo delle stanze di impostazioni e' alto quanto lei.

    E «quanto lei» e' il 73%, non il 45%: la larghezza e l'altezza dell'arte
    sono due numeri diversi, ed e' un errore gia' fatto una volta. Scritto come
    variabile vale anche alla taglia che nessuno ha ancora aggiunto — e se il
    modulo smettesse di scriverla, il `calc()` diventerebbe invalido e il fondo
    sparirebbe **in silenzio**: nessun errore, solo la nota finita dietro la
    sua testa.
    """
    css = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    m = re.search(r"\.casa-tu-scroll \{[^}]*?padding: [^;]*;", css, re.S)
    assert m, "il fondo della stanza non c'e' piu'"
    assert "var(--jenny-art-h)" in m.group(0), (
        f"il fondo non nomina l'altezza dell'arte: {m.group(0)}"
    )
    assert "0.73" not in m.group(0), "il rapporto e' stato riscritto a mano"
    assert "var(--jenny-size)" in m.group(0), "il fondo non segue piu' la taglia"
