"""Le stanze che ``ui_view`` sa descrivere sono quelle che la casa sa aprire.

``ROOM_ELEMENTS`` in ``home-ui-query.js`` dice, per ogni stanza, quale elemento
mandare a Jenny quando chiede «cosa vedi?». Una stanza che manca o che punta a un
id sparito non fa errore: manda un HTML vuoto, e Jenny risponde a vuoto. E'
successo — fino al 05/10/2026 la tabella aveva ancora ``pages`` (le pagine del
quaderno, sostituite dalla bacheca) e non aveva ne' la bacheca ne' il cassetto.

Qui la tabella si confronta con le due cose che non possono mentire: le stanze
di ``BACK_TO`` in ``home-app.js`` (tutte e sole quelle da cui si torna) e gli id
veri di ``index.html``.
"""

from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"
ASSETS = UI / "assets"


def _table(src: str, name: str) -> dict[str, str]:
    body = re.search(rf"const {name} = \{{(.*?)\n\}};", src, re.S)
    assert body, f"{name} non trovata"
    return dict(re.findall(r"^\s*(\w+):\s*'([\w-]+)',", body.group(1), re.M))


def test_every_room_jenny_can_be_in_is_described() -> None:
    rooms = _table((ASSETS / "home-ui-query.js").read_text(encoding="utf-8"), "ROOM_ELEMENTS")
    back_to = _table((ASSETS / "home-app.js").read_text(encoding="utf-8"), "BACK_TO")
    assert set(rooms) == set(back_to), (
        f"stanze senza descrizione: {sorted(set(back_to) - set(rooms))}; "
        f"descrizioni di stanze che non esistono: {sorted(set(rooms) - set(back_to))}"
    )


def test_every_described_room_is_a_real_element() -> None:
    rooms = _table((ASSETS / "home-ui-query.js").read_text(encoding="utf-8"), "ROOM_ELEMENTS")
    html = (UI / "index.html").read_text(encoding="utf-8")
    missing = [el for el in rooms.values() if f'id="{el}"' not in html]
    assert not missing, f"ROOM_ELEMENTS punta a id che index.html non ha: {missing}"
