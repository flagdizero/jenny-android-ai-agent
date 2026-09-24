"""Una Jenny sola: la casa e l'officina disegnano la stessa mascotte.

Fino al 24/09/2026 le mascotte erano due — `mobile-jenny.js` e `casa-mascot.js`
— con la fisica in comune e il cervello scritto due volte. Una copia la teneva
onesta solo sui nomi dei file (un test ne confrontava le tabelle), e intanto le
due si erano allontanate dove nessuno guardava: in casa il gesto della mano nel
parlato non c'era, un umore vivo congelava la bocca, l'errore non aveva la sua
faccia, un avviso proattivo la fermava a meta' risposta.

Adesso il cervello e' `shared/jenny-mascot.js` e **fra i due gusci cambia solo
il pavimento**. Qui si tiene ferma quella frase: ciascuna asserzione e' una
porta da cui la seconda Jenny potrebbe rientrare. Il comportamento vero —
stati, parlato, umore, turni — lo provano in node `test_mascot_mood_client.py`
e `test_live_turn_boundary_client.py`, sul modulo condiviso: quindi per tutti e
due i gusci.
"""

from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
MASCOT_JS = ASSETS / "shared" / "jenny-mascot.js"
CASA_CSS = ASSETS / "casa-style.css"
OFFICINA_CSS = ASSETS / "mobile-style.css"

_ART = ("jenny-body-front", "jenny-face-front", "jenny-side.webp", "jenny-side-talk")


def _rules(css: str, selector_re: str) -> list[str]:
    """I corpi delle regole il cui selettore contiene ``selector_re``."""
    return [
        corpo
        for selettori, corpo in re.findall(r"([^{}]+)\{([^}]*)\}", css)
        if re.search(selector_re, selettori.strip().splitlines()[-1])
    ]


def test_the_art_is_named_in_one_module_only() -> None:
    """Le tabelle degli sprite esistono una volta. Un secondo file che nomina
    un corpo o una faccia e' una seconda Jenny che comincia."""
    nominano = sorted(
        str(f.relative_to(ASSETS))
        for f in ASSETS.rglob("*.js")
        if "vendor" not in f.parts and any(a in f.read_text(encoding="utf-8") for a in _ART)
    )
    assert nominano == ["shared/jenny-mascot.js"], nominano


def test_talking_uses_the_raised_hand() -> None:
    """Il frame con la mano alzata e' il gesto del parlato. Era nella tabella
    della casa e non lo usava nessuno: la tabella da sola non lo prova."""
    src = MASCOT_JS.read_text(encoding="utf-8")
    assert re.search(r"const TALK_BODIES = \[BODY\.idle, BODY\.hand\];", src)
    assert "TALK_BODIES[this._talk.animIdx]" in src


def test_the_house_does_not_drive_her_by_hand() -> None:
    """In casa lei legge i frame da se', con le regole dell'officina. Una
    chiamata a mano da `casa-app.js` sarebbe una seconda macchina a stati
    sopra la prima — ed e' esattamente come le due erano divergite."""
    casa = (ASSETS / "casa-app.js").read_text(encoding="utf-8")
    assert "new JennyMascot(" in casa
    pilotaggi = re.findall(
        r"this\.jenny\.(thinking|talking|idle|setMood|noteTurn\w*|_set\w+)\(", casa
    )
    assert not pilotaggi, f"la casa pilota ancora la mascotte: {pilotaggi}"


def test_the_house_sheet_only_moves_the_floor() -> None:
    """Il foglio della casa dice dove appoggia i piedi e a che livello sta nel
    suo documento. Nient'altro: ancoraggi, specchio, volo e respiro sono di
    `.jenny-duo`, in un foglio solo."""
    css = CASA_CSS.read_text(encoding="utf-8")
    regole = _rules(css, r"\.jenny-duo")
    proprie = [c for c in regole if "bottom:" in c]
    assert len(proprie) == 1, f"il pavimento della casa non e' una regola sola: {proprie}"
    dichiarate = {
        d.split(":", 1)[0].strip()
        for d in re.sub(r"/\*.*?\*/", "", proprie[0], flags=re.S).split(";")
        if d.strip()
    }
    assert dichiarate == {"bottom", "z-index"}, dichiarate
    assert "--casa-composer-h" in proprie[0], "i piedi non appoggiano piu' sul composer"
    # Le altre regole che la nominano, in casa, sono di chi le lascia spazio
    # (la riga di lavoro) e non toccano lei.
    assert ".casa-jenny {" not in css and ".casa-jenny." not in css, (
        "e' tornato lo sprite della casa"
    )


def test_she_does_not_sway_against_the_edge() -> None:
    """Il dondolio del pensa e' della Jenny venuta fuori. Il tocco la manda al
    bordo senza cambiare stato, quindi la classe `thinking` puo' restarle
    addosso: e' il CSS a dover chiedere anche `.out`. Mezza Jenny che oscilla
    contro il bordo somiglia a un guasto della pagina (la casa l'aveva gia'
    corretto per se'; adesso vale in tutte e due)."""
    css = OFFICINA_CSS.read_text(encoding="utf-8")
    dondoli = [
        selettori.strip().splitlines()[-1]
        for selettori, corpo in re.findall(r"([^{}]+)\{([^}]*)\}", css)
        if "jenny-wobble" in corpo and "@keyframes" not in selettori
    ]
    assert dondoli, "il pensa non dondola piu'"
    for sel in dondoli:
        assert ".out" in sel, f"dondola anche dal bordo: {sel}"
        assert ".jenny-art-stack" in sel, f"dondolano anche le pose del volo: {sel}"
