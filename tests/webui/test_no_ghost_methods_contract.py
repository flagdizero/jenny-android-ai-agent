"""Nessun `this._qualcosa()` che non esiste.

Il 19/09/2026 l'estrazione del flusso di aggiornamento ha portato via due
metodi — `_updateProgressHtml` e `_paintUpdate` — lasciando in piedi le due
righe che li chiamavano. Il file resta sintatticamente valido, `node --check`
passa, la suite resta verde, e il difetto arriva sul telefono: un `TypeError`
al primo tocco su «Controlla ora», e un altro ogni volta che il backend
annuncia una versione nuova.

**E' la classe di difetto che un refactor produce per costruzione.** Spostare
un metodo e' due mosse — toglierlo di qua, chiamarlo di la' — e fra le due c'e'
una finestra in cui il chiamante e' rimasto indietro. Nessun banco lo vede
perche' nessun banco esercita quel ramo: serve un provider con un
aggiornamento disponibile, cioe' esattamente quel che in prova non c'e' mai.

Il controllo e' statico e grezzo apposta: si leggono le chiamate `this._x(` e
le definizioni `_x(`, e si chiede che ogni chiamata ne trovi una. Un'euristica
che puo' sbagliare in un verso solo — verso il falso allarme — e che si zittisce
dichiarando il nome, non allentando la regola.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

# I file che definiscono una classe con metodi privati: quelli in cui la
# domanda ha senso. `shared/` entra pure lui — `update-flow.js` e'
# esattamente il modulo da cui e' partito il guaio.
SORGENTI = sorted(
    [p for p in ASSETS.glob("*.js")]
    + [p for p in (ASSETS / "shared").glob("*.js")]
)

# Nomi che esistono a runtime senza essere scritti qui: ereditati, o messi
# dall'esterno. Si dichiarano uno per uno — allargare il filtro al posto di
# nominarli rimetterebbe il difetto sotto il tappeto.
CONCESSI = {
    # `AppState` e i controller condividono un paio di ganci che il guscio
    # chiama su di loro (v. mobile-app.js), non definiti nella classe.
    "_onPowerVisible",
}


def _chiamate(src: str) -> set[str]:
    """`this._x(` — solo le chiamate, non le letture di proprieta'."""
    return set(re.findall(r"this\.(_[A-Za-z][A-Za-z0-9]*)\(", src))


def _definiti(src: str) -> set[str]:
    """Metodi della classe, piu' **qualunque** campo a cui si assegni qualcosa.

    Il secondo caso conta e va preso largo: `this._fetch = fetchProjects`,
    `this._generation = generation || (() => 0)`, `this._onOpen = callback`
    sono definizioni a tutti gli effetti per chi poi scrive `this._x()`, e una
    regola che accettasse solo `= () =>` griderebbe al lupo su cinque file
    perfettamente sani (misurato, 20/09/2026).

    Il prezzo e' dichiarato: `this._x = null` seguito da `this._x()` passa. Non
    e' il difetto che questo banco insegue — quello e' il metodo **tolto da un
    refactor**, che non lascia ne' definizione ne' assegnazione.
    """
    metodi = set(re.findall(r"^\s{2}(?:async |\*|get |set )?(_[A-Za-z][A-Za-z0-9]*)\s*\(", src, re.M))
    campi = set(re.findall(r"this\.(_[A-Za-z][A-Za-z0-9]*)\s*(?:\?\?|\|\||&&)?=(?!=)", src))
    return metodi | campi


@pytest.mark.parametrize("sorgente", SORGENTI, ids=lambda p: p.name)
def test_every_method_it_calls_exists(sorgente: Path) -> None:
    src = sorgente.read_text(encoding="utf-8")
    if "class " not in src:
        pytest.skip("nessuna classe qui dentro")
    fantasmi = sorted(_chiamate(src) - _definiti(src) - CONCESSI)
    assert not fantasmi, (
        f"{sorgente.name} chiama metodi che non esistono: {fantasmi}. "
        f"Il file resta valido e la suite verde: il difetto si vede solo sul "
        f"telefono, e solo sul ramo che li usa."
    )


def test_the_check_would_have_caught_the_one_that_shipped() -> None:
    """La prova che questo banco non e' una formalita'.

    Si ricostruisce il difetto vero — le due chiamate senza le loro
    definizioni — e si chiede al controllo di vederlo. Un banco di questo tipo
    scritto *dopo* il fatto e' credibile solo se fallisce sul fatto.
    """
    finto = """
class Prova {
  constructor() {
    this.updates = new UpdateFlow({ onChange: () => this._paintUpdate() });
  }
  _renderUpdateCard(v) {
    return `<div>${this._updateProgressHtml()}</div>`;
  }
}
"""
    fantasmi = _chiamate(finto) - _definiti(finto) - CONCESSI
    assert fantasmi == {"_paintUpdate", "_updateProgressHtml"}, fantasmi
