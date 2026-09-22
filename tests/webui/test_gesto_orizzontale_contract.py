"""Un solo posto in cui si riconosce un trascinamento orizzontale.

Il 22/09/2026 il gesto del carosello e' uscito da `MobileApp::setupSwipeNav` ed
e' finito in `shared/gesto-orizzontale.js`, perche' la casa deve fare **lo
stesso gesto** per cambiare pagina.

**Perche' serve un banco e non basta la buona volonta'.** Le costanti di quel
gesto non sono scelte a occhio, sono state pagate con delle misure:

- `SOGLIA_ASSE = 24` e non 10, perche' il touch slop di Android e' ~8dp e sotto
  quella soglia `preventDefault()` cade dentro la finestra in cui Chromium sta
  ancora decidendo se la pressione e' un long-press — che viene scartato, e la
  selezione di testo non si apre piu';
- la dominanza `1.5`, che distingue un trascinamento diagonale (chi aggiusta una
  selezione) da uno orizzontale vero;
- `dentroScorrevoleOrizzontale`, che cede il gesto a un blocco di codice largo
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
MODULO = ASSETS / "shared" / "gesto-orizzontale.js"

SORGENTI = sorted(
    [p for p in ASSETS.glob("*.js")] + [p for p in (ASSETS / "shared").glob("*.js")]
)


def test_only_the_shared_module_listens_to_touchmove() -> None:
    """Chi vuole trascinare passa dal modulo, non si scrive il proprio."""
    colpevoli = [
        p.relative_to(ASSETS).as_posix()
        for p in SORGENTI
        if p != MODULO and "touchmove" in p.read_text(encoding="utf-8")
    ]
    assert not colpevoli, (
        f"{colpevoli} ascoltano `touchmove` per conto loro. Il riconoscimento di "
        f"un trascinamento sta in shared/gesto-orizzontale.js: una seconda copia "
        f"diverge al primo aggiustamento, e la differenza si vede solo col dito."
    )


def test_the_measured_constants_live_in_one_place() -> None:
    """La soglia dell'asse e la dominanza stanno solo nel modulo."""
    src = MODULO.read_text(encoding="utf-8")
    assert "export const SOGLIA_ASSE = 24;" in src
    assert "Math.abs(dy) * 1.5" in src, "la dominanza orizzontale e' sparita"

    altrove = []
    for p in SORGENTI:
        if p == MODULO:
            continue
        testo = p.read_text(encoding="utf-8")
        if re.search(r"Math\.abs\(d[xy]\)", testo):
            altrove.append(p.relative_to(ASSETS).as_posix())
    assert not altrove, f"{altrove} decidono un asse per conto loro"


def test_the_module_does_not_know_what_it_moves() -> None:
    """Niente DOM dei gusci qui dentro: e' quel che lo rende riusabile.

    Il modulo puo' toccare `document.body` (gli serve come fondo della risalita
    in `dentroScorrevoleOrizzontale`) e `window`, ma **non** deve sapere che
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
        "casa-",
        "switchMode",
    ]
    trovati = [t for t in proibiti if t in src]
    assert not trovati, (
        f"il modulo condiviso nomina {trovati}: sa cosa muove, e non deve."
    )
