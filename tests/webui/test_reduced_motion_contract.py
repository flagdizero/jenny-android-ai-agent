"""Movimento ridotto: nessun bottone rimpicciolisce al tocco.

La regola `*` della sezione «Reduced Motion» di `mobile-style.css` azzera
durate e animazioni. Un `transform: scale(...)` su `:active` non e' ne' l'una
ne' l'altra, quindi sopravvive — e fino al 25/09/2026 lo spegnevano solo le tre
pillole del composer, mentre una ventina di bottoni rimpicciolivano lo stesso
(revisione di quel giorno). La scelta e' quella che le pillole avevano gia'
fatto: a movimento ridotto il tocco non rimpicciolisce, mai.

Il banco lo legge dai fogli invece di elencare i bottoni: un bottone nuovo con
`scale()` al tocco che non passa dal blocco in fondo al foglio lo fa fallire.
"""

from __future__ import annotations

from support import css_levels

SHEETS = ("mobile-style.css", "home-style.css")


def _rules():
    for name in SHEETS:
        for selettori, body, contesto in css_levels.rules(
            (css_levels.ASSETS / name).read_text(encoding="utf-8")
        ):
            yield name, [" ".join(s.split()) for s in selettori.split(",")], body, contesto


def test_reduced_motion_stops_every_press_from_shrinking() -> None:
    ridotto = [
        (name, sel, body)
        for name, sel, body, ctx in _rules()
        if any("prefers-reduced-motion: reduce" in at for at in ctx)
    ]
    spenti = {s for _, sel, body in ridotto if "transform: none" in body for s in sel}

    rimpiccioliscono = [
        (name, s)
        for name, sel, body, ctx in _rules()
        if "scale(" in body and not any("prefers-reduced-motion" in at for at in ctx)
        for s in sel
        if ":active" in s
    ]
    assert len(rimpiccioliscono) > 15, (
        f"la grep sui tocchi che rimpiccioliscono non morde piu' ({len(rimpiccioliscono)})"
    )
    scoperti = [(name, s) for name, s in rimpiccioliscono if s not in spenti]
    assert not scoperti, (
        f"a movimento ridotto rimpiccioliscono ancora: {scoperti}. Aggiungili al blocco "
        f"«Movimento ridotto: il tocco non rimpicciolisce» in fondo a mobile-style.css"
    )


def test_the_block_that_stops_them_comes_after_every_press() -> None:
    """A pari specificita' vince chi viene dopo: il blocco deve seguire ogni
    regola che rimpicciolisce, e home-style.css (caricato dopo) non ne ha."""
    css = (css_levels.ASSETS / "mobile-style.css").read_text(encoding="utf-8")
    regole = css_levels.rules(css)
    ultimo_scale = max(
        i for i, (sel, body, ctx) in enumerate(regole)
        if ":active" in sel and "scale(" in body
        and not any("prefers-reduced-motion" in at for at in ctx)
    )
    block = max(
        i for i, (sel, body, ctx) in enumerate(regole)
        if "transform: none" in body and ".btn-icon:active" in sel
        and any("prefers-reduced-motion: reduce" in at for at in ctx)
    )
    assert block > ultimo_scale, "il blocco del movimento ridotto non e' piu' in fondo"
    home = (css_levels.ASSETS / "home-style.css").read_text(encoding="utf-8")
    assert not [
        sel for sel, body, _ in css_levels.rules(home) if ":active" in sel and "scale(" in body
    ], "home-style.css rimpicciolisce al tocco: il blocco in mobile-style.css non la copre"
