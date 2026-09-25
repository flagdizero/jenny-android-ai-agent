"""Un solo posto in cui si riconosce un trascinamento orizzontale.

Il 22/09/2026 il gesto del carosello e' uscito da `MobileApp::setupSwipeNav` ed
e' finito in `shared/horizontal-swipe.js`, perche' la casa deve fare **lo
stesso gesto** per cambiare pagina.

**Perche' serve un banco e non basta la buona volonta'.** Le costanti di quel
gesto non sono scelte a occhio, sono state pagate con delle misure:

- `AXIS_THRESHOLD = 24` e non 10, perche' il touch slop di Android e' ~8dp e sotto
  quella soglia `preventDefault()` cade dentro la finestra in cui Chromium sta
  ancora decidendo se la pressione e' un long-press — che viene scartato, e la
  selezione di testo non si apre piu';
- la dominanza `1.5`, che distingue un trascinamento diagonale (chi aggiusta una
  selezione) da uno orizzontale vero;
- `insideHorizontalScrollable`, che cede il gesto a un blocco di codice largo
  invece di rubarglielo.

Una seconda copia in casa partirebbe uguale e divergerebbe al primo aggiustamento,
e la differenza si vedrebbe **solo col dito su un telefono** — cioe' quasi mai.

Il controllo e' grezzo apposta: chi vuole un trascinamento passa di li', e il
modo per dirlo e' che **nessun altro file ascolta `touchmove`**. Misurato il
22/09/2026 subito dopo l'estrazione: era gia' vero, e questo banco lo tiene vero.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
MODULO = ASSETS / "shared" / "horizontal-swipe.js"

# `apps/` e' nell'elenco dal 22/09/2026: da quel giorno il kit che ogni Jenny
# App carica e' anche lui un consumatore del gesto — dentro una app il dito non
# arriva al guscio, e il riconoscimento tocca farlo li'. E' il posto piu'
# probabile in cui un domani comparirebbe una seconda copia.
SORGENTI = sorted(
    [p for p in ASSETS.glob("*.js")]
    + [p for p in (ASSETS / "shared").glob("*.js")]
    + [p for p in (ASSETS / "apps").glob("*.js")]
)

SDK = ASSETS / "apps" / "jenny-sdk.js"


def test_only_the_shared_module_listens_to_touchmove() -> None:
    """Chi vuole trascinare passa dal modulo, non si scrive il proprio."""
    colpevoli = [
        p.relative_to(ASSETS).as_posix()
        for p in SORGENTI
        if p != MODULO and "touchmove" in p.read_text(encoding="utf-8")
    ]
    assert not colpevoli, (
        f"{colpevoli} ascoltano `touchmove` per conto loro. Il riconoscimento di "
        f"un trascinamento sta in shared/horizontal-swipe.js: una seconda copia "
        f"diverge al primo aggiustamento, e la differenza si vede solo col dito."
    )


def test_the_measured_constants_live_in_one_place() -> None:
    """La soglia dell'asse e la dominanza stanno solo nel modulo."""
    src = MODULO.read_text(encoding="utf-8")
    assert "export const AXIS_THRESHOLD = 24;" in src
    assert "Math.abs(dy) * 1.5" in src, "la dominanza orizzontale e' sparita"

    altrove = []
    for p in SORGENTI:
        if p == MODULO:
            continue
        text = p.read_text(encoding="utf-8")
        if re.search(r"Math\.abs\(d[xy]\)", text):
            altrove.append(p.relative_to(ASSETS).as_posix())
    assert not altrove, f"{altrove} decidono un asse per conto loro"


def test_the_module_does_not_know_what_it_moves() -> None:
    """Niente DOM dei gusci qui dentro: e' quel che lo rende riusabile.

    Il modulo puo' toccare `document.body` (gli serve come fondo della risalita
    in `insideHorizontalScrollable`) e `window`, ma **non** deve sapere che
    esistono viste, linguette, cassetti o pagine: quella e' la parte che i due
    gusci hanno diversa, e il giorno che entra qui il modulo smette di essere
    condiviso.
    """
    src = MODULO.read_text(encoding="utf-8")
    proibiti = [
        "getElementById",
        "querySelector",
        "view-",
        "dock-item",
        "home-",
        "switchMode",
    ]
    trovati = [t for t in proibiti if t in src]
    assert not trovati, (
        f"il modulo condiviso nomina {trovati}: sa cosa muove, e non deve."
    )


def test_the_app_kit_borrows_the_gesture_instead_of_writing_one() -> None:
    """Dentro una app il dito non arriva al guscio, quindi il gesto si fa li'.

    Ed e' esattamente il posto in cui una seconda copia sarebbe comoda: il kit
    e' un file classico — le app lo caricano con `<script src>` e trasformarlo
    in modulo le romperebbe tutte — quindi la strada corta sarebbe ricopiarci
    dentro le tre soglie. L'import dinamico e' la strada lunga, ed e' quella
    che tiene le soglie in un posto solo.
    """
    src = SDK.read_text(encoding="utf-8")
    assert "shared/horizontal-swipe.js" in src, (
        "il kit delle app non nomina piu' il modulo condiviso: se il gesto "
        "ora se lo scrive da solo, le soglie misurate sono diventate due."
    )
    assert "watchHorizontalSwipe" in src


def test_the_app_kit_imports_by_a_whole_address() -> None:
    """Un percorso li' dentro non si risolve, e il modulo non arriva mai.

    Il kit e' servito da un'altra origine e senza `crossorigin`, quindi per
    Chromium e' uno «script CORS-cross-origin»: la sua base per `import()` e'
    `about:blank`, e `import('/qualcosa.js')` muore su «Failed to resolve
    module specifier» prima di toccare la rete. Misurato il 22/09/2026 su
    Chrome del telefono — lo stesso motore della WebView — dopo che sul
    telefono lo scorrimento dentro le app non faceva niente e i banchi erano
    tutti verdi: **nessun banco puo' vedere questo, tranne questo qui.**
    """
    src = SDK.read_text(encoding="utf-8")
    assert re.search(r"new URL\(\s*'/html-mobile/assets/shared/horizontal-swipe\.js'", src), (
        "l'indirizzo del modulo non e' piu' costruito intero"
    )
    assert not re.search(r"import\(\s*['\"]/", src), (
        "`import()` ha di nuovo un percorso invece di un indirizzo: dentro una "
        "app non si risolve, e lo scorrimento smette di funzionare in silenzio."
    )


def test_the_app_kit_only_tells_what_the_finger_did() -> None:
    """Il kit racconta, il guscio decide.

    Dentro una app non si puo' sapere se una pagina di fianco c'e' — e neanche
    se il cassetto e' aperto. Il giorno che il kit provasse a deciderlo, la
    risposta sarebbe quella di quando il frame e' stato costruito, non quella
    di adesso.
    """
    src = SDK.read_text(encoding="utf-8")
    for phase in ("'start'", "'move'", "'end'", "'cancel'"):
        assert f"phase: {phase}" in src, f"il kit non manda piu' la fase {phase}"
    assert "jenny:swipe" in src


def test_the_finger_is_read_off_the_screen_ruler() -> None:
    """Un righello solo, e non e' quello della finestra.

    `clientX` e' relativo alla finestra di chi ascolta. Officina e guscio della
    casa non se ne accorgono — la loro finestra sta ferma — ma dentro una Jenny
    App la finestra **e'** la cornice che la pista trascina: il righello si
    muove insieme al dito e il gesto insegue se stesso. L'utente l'ha visto
    come la schermata che vibra; i due righelli fianco a fianco su Chrome del
    telefono l'hanno mostrato riga per riga (22/09/2026).

    Il rimedio e' una regola sola per tutti e tre i posti, non due con
    un'eccezione: questo banco e' la regola.
    """
    src = MODULO.read_text(encoding="utf-8")
    assert "t.screenX" in src and "t.screenY" in src
    for letto in ("t.clientX", "t.clientY", "cambiato.clientX", "cambiato.clientY"):
        assert letto not in src, (
            f"il modulo legge di nuovo {letto}: dentro una cornice trascinata "
            f"quel righello si muove insieme al dito, e la schermata vibra."
        )
