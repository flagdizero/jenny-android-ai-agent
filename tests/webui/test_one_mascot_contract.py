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

from support import css_levels

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
MASCOT_JS = ASSETS / "shared" / "jenny-mascot.js"
HOME_CSS = ASSETS / "home-style.css"
WORKSHOP_CSS = ASSETS / "mobile-style.css"

_ART = ("jenny-body-front", "jenny-face-front", "jenny-side.webp", "jenny-side-talk")


def _rules(css: str, selector_re: str) -> list[str]:
    """I corpi delle regole il cui selettore contiene ``selector_re``."""
    return [
        body
        for selectors, body in re.findall(r"([^{}]+)\{([^}]*)\}", css)
        if re.search(selector_re, selectors.strip().splitlines()[-1])
    ]


def test_the_art_is_named_in_one_module_only() -> None:
    """Le tabelle degli sprite esistono una volta. Un secondo file che nomina
    un corpo o una faccia e' una seconda Jenny che comincia."""
    naming = sorted(
        str(f.relative_to(ASSETS))
        for f in ASSETS.rglob("*.js")
        if "vendor" not in f.parts and any(a in f.read_text(encoding="utf-8") for a in _ART)
    )
    assert naming == ["shared/jenny-mascot.js"], naming


def test_talking_uses_the_raised_hand() -> None:
    """Il frame con la mano alzata e' il gesto del parlato. Era nella tabella
    della casa e non lo usava nessuno: la tabella da sola non lo prova."""
    src = MASCOT_JS.read_text(encoding="utf-8")
    assert re.search(r"const TALK_BODIES = \[BODY\.idle, BODY\.hand\];", src)
    assert "TALK_BODIES[this._talk.animIdx]" in src


def test_the_house_does_not_drive_her_by_hand() -> None:
    """In casa lei legge i frame da se', con le regole dell'officina. Una
    chiamata a mano da `home-app.js` sarebbe una seconda macchina a stati
    sopra la prima — ed e' esattamente come le due erano divergite."""
    home = (ASSETS / "home-app.js").read_text(encoding="utf-8")
    assert "new JennyMascot(" in home
    drives = re.findall(
        r"this\.jenny\.(thinking|talking|idle|setMood|noteTurn\w*|_set\w+)\(", home
    )
    assert not drives, f"la casa pilota ancora la mascotte: {drives}"


def test_the_house_sheet_only_moves_the_floor() -> None:
    """Il foglio della casa dice dove appoggia i piedi. Nient'altro: ancoraggi,
    specchio, volo, respiro **e livello** sono di `.jenny-duo`, in un foglio
    solo. Il livello era qui (`z-index: 5`) fino a D3, e bastava contro la casa
    ma non contro mini-app e lightbox, che arrivano dall'altro foglio.

    Dal 28/09/2026 il pavimento si dichiara come variabile, `--jenny-floor`, e
    non come `bottom` dello sprite: la leggono anche il fumetto e il pensa della
    minichat, che in casa rifacevano il conto dell'officina (col dock che qui
    non c'e'). Resta una dichiarazione sola, e resta il composer."""
    css = HOME_CSS.read_text(encoding="utf-8")
    sprite = _rules(css, r"\.jenny-duo")
    assert not [c for c in sprite if "bottom:" in c], (
        "la casa riscrive di nuovo il `bottom` dello sprite invece del pavimento"
    )
    floors = [c for c in _rules(css, r"^\.home-shell$") if "--jenny-floor" in c]
    assert len(floors) == 1, f"il pavimento della casa non e' una dichiarazione sola: {floors}"
    declared = {
        d.split(":", 1)[0].strip()
        for d in re.sub(r"/\*.*?\*/", "", floors[0], flags=re.S).split(";")
        if d.strip()
    }
    assert declared == {"--jenny-floor"}, declared
    assert "--home-composer-h" in floors[0], "i piedi non appoggiano piu' sul composer"
    workshop = WORKSHOP_CSS.read_text(encoding="utf-8")
    assert [c for c in _rules(workshop, r"^\.jenny-duo$") if "bottom: var(--jenny-floor)" in c], (
        "lo sprite non legge piu' il pavimento del guscio"
    )
    # Le altre regole che la nominano, in casa, sono di chi le lascia spazio
    # (la riga di lavoro) e non toccano lei.
    assert ".home-jenny {" not in css and ".home-jenny." not in css, (
        "e' tornato lo sprite della casa"
    )


def test_she_stands_where_the_minichat_wants_her() -> None:
    """Il pavimento e' uno: appena sopra il campo della minichat, **aperta o
    chiusa** che sia, in ogni vista dell'officina — console compresa — e in
    ogni pagina della casa senza composer.

    Dall'utente, il 28/09/2026, in tre passi. Nell'officina lei stava
    all'altezza della barra di input anche dove la barra non c'e' (sospesa sopra
    Cervello, Mani e Memoria); poi e' scesa a 20 px e all'apertura della
    minichat saltava su di 46 («il pavimento e' dove appare nella minichat,
    sempre, in ogni schermata»); e in console restava sopra tutta la barra di
    input, alta sopra la riga dei chip («deve essere uguale agli altri»). In casa
    la chat ha il suo composer, e lei ci sta sopra."""
    home = HOME_CSS.read_text(encoding="utf-8")
    workshop = WORKSHOP_CSS.read_text(encoding="utf-8")
    app_js = (ASSETS / "home-app.js").read_text(encoding="utf-8")

    token = re.search(r"--jenny-away-floor:\s*(\d+)px;", workshop)
    assert token, "il pavimento non e' piu' un token"
    const = re.search(r"const FLOOR_NO_COMPOSER = (\d+);", app_js)
    assert const and const.group(1) == token.group(1), (
        "la casa appoggia i piedi a un'altezza diversa dall'officina"
    )

    floors = re.findall(r"--jenny-floor:\s*([^;]+);", workshop)
    assert floors == [
        "calc(var(--dock-height) + var(--jenny-away-floor) - var(--jenny-size) * 0.1224)"
    ], f"l'officina ha di nuovo piu' di un pavimento, o un altro: {floors}"
    house = re.findall(r"--jenny-floor:\s*([^;]+);", home)
    assert len(house) == 1 and "var(--jenny-size) * 0.1224" in house[0], (
        "i due pavimenti non tolgono lo stesso margine sotto i piedi"
    )
    for css in (home, workshop):
        assert "jenny-mc.open)" not in css, (
            "la minichat aperta la sposta di nuovo: il pavimento deve essere sempre quello"
        )
    # I fondi delle stanze le lasciano posto a quell'altezza, non a quella di prima.
    assert "calc(20px + var(--jenny-size)" not in home


def test_she_does_not_sway_against_the_edge() -> None:
    """Il dondolio del pensa e' della Jenny venuta fuori. Il tocco la manda al
    bordo senza cambiare stato, quindi la classe `thinking` puo' restarle
    addosso: e' il CSS a dover chiedere anche `.out`. Mezza Jenny che oscilla
    contro il bordo somiglia a un guasto della pagina (la casa l'aveva gia'
    corretto per se'; adesso vale in tutte e due)."""
    css = WORKSHOP_CSS.read_text(encoding="utf-8")
    sways = [
        selectors.strip().splitlines()[-1]
        for selectors, body in re.findall(r"([^{}]+)\{([^}]*)\}", css)
        if "jenny-think" in body and "@keyframes" not in selectors
    ]
    assert sways, "il pensa non dondola piu'"
    for sel in sways:
        assert ".out" in sel, f"dondola anche dal bordo: {sel}"
        assert ".jenny-art-stack" in sel, f"dondolano anche le pose del volo: {sel}"


def test_she_is_on_top_of_everything_in_the_workshop() -> None:
    """Il gemello di `test_she_is_on_top_of_everything_in_the_house`, per
    l'officina: il suo livello supera ogni altro del foglio.

    D3 (25/09/2026, dall'utente): «Jenny sempre sopra», anche a mini-app e
    immagini, nella casa e nell'officina. Qui le passavano davanti la lightbox
    (1000) e, col cassetto aperto, foglio e scrim (lei scendeva a 98). Sopra di
    lei restano solo `<dialog>` e toast, che vivono nel top layer, e le
    eccezioni qui sotto, ciascuna col suo perche'.
    """
    css = WORKSHOP_CSS.read_text(encoding="utf-8")
    levels = css_levels.levels(css)
    its = [z for sel, z in levels if sel == ".jenny-duo"]
    assert len(its) == 1, its
    she = its[0]
    allowed = {
        # La sua minichat: il fumetto sopra la sua testa.
        ".jenny-mc",
        # Livelli locali: vivono dentro `.chat-bottom`, che apre un contesto
        # di impilamento suo (controllato qui sotto), quindi nella pagina
        # contano quanto lui.
        ".compose-scope", ".scope-menu", ".commands-menu",
        # L'attesa del primo avvio: vive in `onboarding.html`, dove lei non
        # c'e', quindi non c'e' niente da coprire.
        ".onboarding-loading-overlay",
    }
    above = [(sel, z) for sel, z in levels if z >= she and sel != ".jenny-duo"]
    outside = [(sel, z) for sel, z in above if sel not in allowed]
    assert not outside, f"le passano davanti: {outside} (il suo livello e' {she})"
    tweaks = [
        (sel, z) for sel, z in levels
        if "jenny-duo" in css_levels.key_names(sel) and sel != ".jenny-duo"
    ]
    assert not tweaks, f"qualcuno cambia il suo livello in un caso: {tweaks}"

    # L'eccezione dei livelli locali regge solo finche' il contesto c'e'.
    bottom = [c for sel, c, _ in css_levels.rules(css) if sel == ".chat-bottom"]
    assert any("position: sticky" in c and "z-index" in c for c in bottom), (
        "`.chat-bottom` non apre piu' un contesto di impilamento: chip e "
        "tendina dello scope (121/122) le passerebbero davanti"
    )
    # Sopra la lightbox lei si vede ma non prende tocchi: e' cio' che tiene
    # separati lightbox e minichat nella catena di Indietro.
    assert re.search(
        r":root:has\(\.image-lightbox\) \.jenny-duo \{ pointer-events: none; \}", css
    ), "sopra la lightbox lei ruberebbe il tocco che la chiude"



# La soglia dello schermo basso: la stessa per il dock e per lei.
SHORT_SCREEN = "380px"


def test_a_short_screen_hides_her_only_where_the_dock_goes() -> None:
    """A schermo basso l'officina toglie il dock, e lei con lui. La
    casa il dock non ce l'ha: la regola valeva anche li', e la nascondeva
    senza motivo (misurato il 25/09/2026). E nessun `!important` per
    nasconderla: le regole vincono per ordine, e il commento che diceva il
    contrario era falso."""
    css = WORKSHOP_CSS.read_text(encoding="utf-8")
    short = [
        (sel, body) for sel, body, ctx in css_levels.rules(css)
        if any(f"max-height: {SHORT_SCREEN}" in at for at in ctx) and "jenny" in sel
    ]
    assert short, "la regola che la nasconde a schermo basso non si trova piu'"
    for sel, _ in short:
        for s in sel.split(","):
            assert s.strip().startswith(".app "), f"a schermo basso la nasconde anche in casa: {s}"
    hidden = [
        body for sel, body, _ in css_levels.rules(css)
        if "jenny" in sel and "display: none" in body
    ]
    assert hidden and not [c for c in hidden if "!important" in c], hidden


def test_the_square_emulator_keeps_the_workshop_dock() -> None:
    """Con la soglia a 500 px il dock spariva sul quadrato di `jenny_square`
    (1440 px a 480 dpi: 480 px CSS, 432 tolte barra di stato e barra dei
    gesti), e l'officina restava senza navigazione. La soglia sta sotto quel
    quadrato, ed e' una sola per dock e mascotte."""
    css = WORKSHOP_CSS.read_text(encoding="utf-8")
    dock = {
        at for sel, body, ctx in css_levels.rules(css)
        if sel.strip() == ".dock" and "display: none" in body
        for at in ctx if "max-height" in at
    }
    assert len(dock) == 1, f"il dock sparisce sotto piu' soglie, o nessuna: {dock}"
    (query,) = dock
    m = re.search(r"max-height:\s*(\d+)px", query)
    assert m and int(m.group(1)) < 432, f"il dock sparisce sul quadrato dell'emulatore: {query}"
    assert f"max-height: {SHORT_SCREEN}" in query, (
        "dock e mascotte non spariscono piu' alla stessa altezza"
    )
