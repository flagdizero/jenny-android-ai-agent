"""Il tocco: niente lampo blu, e ogni pulsante risponde da sé.

Il WebView di Android colora di blu ogni elemento toccabile che non dica
`-webkit-tap-highlight-color: transparent`. Fino al 13/10/2026 la casa lo
diceva regola per regola — 48 righe — e ogni pulsante nuovo nasceva blu finche'
qualcuno non se ne accorgeva (le note e il «+» del cassetto, quel giorno).
Adesso lo dice una volta la regola `html` di `mobile-style.css`, che tutti e tre
i documenti caricano: la proprieta' si eredita, quindi copre anche gli elementi
creati dal JS.

Il lampo pero' era, per una quarantina di pulsanti, l'unico segno che il tocco
era arrivato. Il riscontro adesso e' di ciascun pulsante, con un `:active` di
una delle famiglie della casa (si scurisce un pieno, si accende lo sfondo di una
riga o di un tondo, sbiadisce un testo); nessuna rimpicciolisce, v.
`test_reduced_motion_contract.py`. Fa eccezione solo chi cambia stato sotto il
dito, e la ragione sta scritta accanto.
"""

from __future__ import annotations

import re

from support import css_levels

SHEETS = ("mobile-style.css", "home-style.css")

# Il tocco cambia subito qualcosa di visibile: quello e' il riscontro.
STATE_IS_THE_FEEDBACK = {
    ".home-switch": "l'interruttore scatta",
    ".toggle-slider": "l'interruttore scatta",
    ".home-seg-btn": "il segmento scelto si accende",
    ".settings-seg-btn": "il segmento scelto si accende",
    ".home-theme": "il tema scelto prende l'anello",
    ".home-board-block-row.is-check": "la casella si spunta",
    ".format-card": "la scelta si accende",
    ".onboarding-model-item": "la scelta si accende",
    ".home-notes-deck": "la carta davanti sfila via",
    ".home-photos-deck": "la polaroid davanti sfila via",
    ".chat-media img": "la foto si apre a tutto schermo",
    ".chat-content img": "la foto si apre a tutto schermo",
    ".home-media img": "la foto si apre a tutto schermo",
}

_NOT = re.compile(r":not\([^)]*\)")
_PSEUDO = re.compile(r":(?!where\b)[\w-]+")


def _norm(selector: str) -> str:
    return " ".join(_NOT.sub("", selector).split())


def _sheet(name: str) -> str:
    return (css_levels.ASSETS / name).read_text(encoding="utf-8")


def _rules():
    for name in SHEETS:
        for selectors, body, context in css_levels.rules(_sheet(name)):
            for s in selectors.split(","):
                yield name, " ".join(s.split()), body, context


def _pressable() -> set[str]:
    return {
        _norm(s) for _, s, body, _ in _rules()
        if re.search(r"(?:^|;)\s*cursor:\s*pointer", body) and not _PSEUDO.search(_NOT.sub("", s))
    }


def _pressed() -> set[str]:
    """I selettori che hanno un `:active`, fino al composto che lo porta."""
    out = set()
    for _, s, _, _ in _rules():
        if ":active" in s:
            out.add(_norm(s[: s.index(":active")]))
    return out


def test_the_blue_flash_is_off_once_for_the_whole_document() -> None:
    mobile = _sheet("mobile-style.css")
    assert mobile.count("-webkit-tap-highlight-color") == 1
    html = [body for sel, body, _ in css_levels.rules(mobile) if sel.strip() == "html"]
    assert any("-webkit-tap-highlight-color: transparent" in body for body in html)
    assert "-webkit-tap-highlight-color" not in _sheet("home-style.css"), (
        "home-style.css spegne di nuovo il lampo a mano: lo fa gia' `html` in mobile-style.css"
    )


def test_jenny_apps_have_it_off_too() -> None:
    """Un'app gira in un iframe: l'ereditarieta' non passa il confine."""
    kit = _sheet("apps/jenny-kit.css")
    html = [body for sel, body, _ in css_levels.rules(kit) if sel.strip() == "html"]
    assert any("-webkit-tap-highlight-color: transparent" in body for body in html)


def test_every_pressable_answers_the_touch() -> None:
    pressable = _pressable()
    assert len(pressable) > 100, f"la grep sui pulsanti non morde piu' ({len(pressable)})"
    pressed = _pressed()
    # `.a .b` e' coperto anche da un `.b:active`: lo stesso elemento lo prende.
    silent = sorted(
        s for s in pressable - set(STATE_IS_THE_FEEDBACK)
        if s not in pressed and s.split(" ")[-1] not in pressed
    )
    assert not silent, (
        f"pulsanti che al tocco non rispondono: {silent}. Dagli un `:active` di una delle "
        f"famiglie della casa, oppure, se il tocco cambia subito il loro stato, mettili in "
        f"STATE_IS_THE_FEEDBACK con la ragione"
    )


def test_the_exemptions_still_exist() -> None:
    gone = sorted(set(STATE_IS_THE_FEEDBACK) - _pressable())
    assert not gone, f"esenti che non esistono piu' nei fogli: {gone}"


def test_a_long_press_on_a_button_selects_nothing() -> None:
    """Il cassetto lascia selezionare il testo; un pulsante dentro di lui no."""
    rules = css_levels.rules(_sheet("mobile-style.css"))
    body = next(
        body for sel, body, _ in rules
        if sel.strip().startswith(":where(button") and ":focus" not in sel
    )
    assert "user-select: none" in body and "-webkit-user-select: none" in body
