"""In `switchMode` la classe `mode-*` si scrive **prima** di `activate()`.

Lo scroller della chat **e' il documento**, non un `div` interno (v. il getter
`_scroller` in `mobile-chat.js`, e `.agent/chat-selection-root-plan.md` per il
motivo). E il documento scorre soltanto sotto `:root.mode-chat`
(`mobile-style.css:1617`): fuori dalla chat `html` e' `overflow: hidden` e non
scorre affatto.

Fino al 22/09/2026 `switchMode` scriveva quella classe **in fondo al metodo**,
dopo aver notificato i controller. Entrando in chat, quindi, `activate()`
misurava e correggeva la posizione di lettura di una pagina che **in
quell'istante non era ancora scorrevole**: `scrollHeight` valeva quanto il
viewport, «vai in fondo» finiva a zero, e la classe arrivava dopo.

Si vedeva a intermittenza — ed e' il genere di difetto che a intermittenza si
archivia come «sara' stato il telefono» — in due modi che sembravano
scollegati:

- la chat **risaliva in mezzo alla cronologia** invece di restare in fondo;
- il dock **sfarfallava**: e' `position: sticky; bottom: 0`, e un elemento
  appiccicato si incolla solo se il suo contenitore scorre davvero.

Segnalato dall'utente il 22/09/2026 come «quando rolla da ultima a prima pagina
il footer glitcha e la chat torna in alto», e riprodotto sul telefono.

**Perche' un controllo sull'ordine del sorgente.** Non e' una regola di stile:
e' una dipendenza vera fra due righe, e non c'e' nessun modo di accorgersi che
e' stata rotta se non guardando *quale viene prima*. A runtime il sintomo e'
intermittente e dipende da quando il browser ricalcola: un banco che lo
inseguisse sarebbe instabile quanto il difetto.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "jenny" / "templates" / "ui" / "assets" / "mobile-app.js"


def _body(src: str, start: int) -> str:
    """Dalla graffa aperta alla sua chiusa, saltando commenti e stringhe.

    I commenti vanno saltati sul serio: questo repo li scrive in italiano, e un
    `dell'` dentro `//` fa credere a un contatore ingenuo che sia cominciata una
    stringa — da li' in poi le graffe non si contano piu'. Costato una prima
    stesura di questo banco, rossa su codice sano (22/09/2026).
    """
    i = src.index("{", start)
    depth, j, string = 0, i, None
    while j < len(src):
        c = src[j]
        due = src[j : j + 2]
        if string:
            if c == "\\":
                j += 2
                continue
            if c == string:
                string = None
        elif due == "//":
            j = src.index("\n", j)
            continue
        elif due == "/*":
            j = src.index("*/", j) + 2
            continue
        elif c in "\"'`":
            string = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[i : j + 1]
        j += 1
    raise AssertionError("graffe sbilanciate")


def _switch_mode(src: str) -> str:
    m = re.search(r"\n  switchMode\(", src)
    assert m, "switchMode non trovato"
    return _body(src, m.end())


def _position(body: str, pattern: str, what: str) -> int:
    m = re.search(pattern, body)
    assert m, f"{what} non trovato in switchMode"
    return m.start()


def test_the_mode_class_is_written_before_the_controller_wakes_up() -> None:
    body = _switch_mode(APP_JS.read_text(encoding="utf-8"))
    cls = _position(body, r"classList\.add\(`mode-\$\{", "la classe mode-*")
    active = _position(body, r"\.activate\(\)", "activate()")
    assert cls < active, (
        "switchMode notifica il controller prima di scrivere `mode-<modo>` su "
        "<html>. Per la chat quella distanza e' un difetto: il suo scroller e' "
        "il documento, e il documento scorre solo sotto `:root.mode-chat` — "
        "quindi activate() correggerebbe lo scroll di una pagina non ancora "
        "scorrevole. Sintomi: la chat risale in mezzo alla cronologia, e il "
        "dock appiccicato sfarfalla."
    )


def test_the_mode_class_is_written_next_to_the_display() -> None:
    """E sta **accanto** al `display`, non genericamente «prima».

    Sono la stessa informazione — cosa c'e' a schermo — detta in due posti, e
    tenerle vicine e' quel che impedisce alla distanza di riaprirsi un pezzo
    per volta.
    """
    body = _switch_mode(APP_JS.read_text(encoding="utf-8"))
    display = _position(body, r"view\.style\.display = 'flex'", "il display della vista")
    cls = _position(body, r"classList\.add\(`mode-\$\{", "la classe mode-*")
    rows_in_middle = body[display:cls].count("\n")
    assert 0 < rows_in_middle <= 30, (
        f"fra il `display` della vista e la classe `mode-*` ci sono "
        f"{rows_in_middle} righe: sono la stessa informazione e vanno tenute "
        f"vicine (il commento sul posto spiega perche')."
    )


def test_the_drawer_check_still_precedes_everything() -> None:
    """Quel che il riordino **non** doveva spostare.

    `setDrawer` deve restare prima di `activate()` — il commento sul posto lo
    dice: «quello ricarica e ridisegna, e saperlo dopo vorrebbe dire un frame
    col cassetto di prima». Il banco lo ripete qui perche' le due regole
    vivono nello stesso metodo e si spostano a vicenda.
    """
    body = _switch_mode(APP_JS.read_text(encoding="utf-8"))
    drawer = _position(body, r"setDrawer\?\.\(", "setDrawer")
    active = _position(body, r"\.activate\(\)", "activate()")
    assert drawer < active, "setDrawer e' finito dopo activate()"
