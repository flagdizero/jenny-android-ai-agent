"""Due classi della casa che una regola nuova aveva allargato a tutti.

`home-style.css` e' un foglio solo per tutta la casa, e una regola scritta per
un posto con un selettore nudo vale ovunque quella classe compaia, anche dove
chi la scriveva non guardava:

- `.home-board-form-label` (l'etichetta di ogni campo dei moduli della bacheca,
  in colonna e in grigio) era stata ridefinita il 01/11/2026 per «Controlla
  ogni», su una riga e in inchiostro: tutte le etichette dei moduli erano
  diventate cosi'.
- `.home-drawer-chip` era il nome di due cose: il bottone-link verso una pagina
  nel cassetto e il chip di un valore letto da una micro-app. La seconda regola
  ristilava anche i bottoni.
"""

from __future__ import annotations

from pathlib import Path

from tests.support.css_levels import rules

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"


def _top_rules(selector: str) -> list[str]:
    css = (ASSETS / "home-style.css").read_text(encoding="utf-8")
    return [
        body for sels, body, at in rules(css)
        if not at and selector in (s.strip() for s in sels.split(","))
    ]


def test_the_form_label_is_defined_once_and_check_every_is_scoped() -> None:
    bare = _top_rules(".home-board-form-label")
    assert len(bare) == 1, bare
    assert "flex-direction: column" in bare[0]
    scoped = _top_rules(".home-board-row.is-every > .home-board-form-label")
    assert scoped and "nowrap" in scoped[0]

