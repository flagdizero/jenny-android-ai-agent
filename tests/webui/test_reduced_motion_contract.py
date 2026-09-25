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
    for nome in SHEETS:
        for selettori, corpo, contesto in css_levels.rules(
            (css_levels.ASSETS / nome).read_text(encoding="utf-8")
        ):
            yield nome, [" ".join(s.split()) for s in selettori.split(",")], corpo, contesto


def test_reduced_motion_stops_every_press_from_shrinking() -> None:
    ridotto = [
        (nome, sel, corpo)
        for nome, sel, corpo, ctx in _rules()
        if any("prefers-reduced-motion: reduce" in at for at in ctx)
    ]
    spenti = {s for _, sel, corpo in ridotto if "transform: none" in corpo for s in sel}

    rimpiccioliscono = [
        (nome, s)
        for nome, sel, corpo, ctx in _rules()
        if "scale(" in corpo and not any("prefers-reduced-motion" in at for at in ctx)
        for s in sel
        if ":active" in s
    ]
    assert len(rimpiccioliscono) > 15, (
        f"la grep sui tocchi che rimpiccioliscono non morde piu' ({len(rimpiccioliscono)})"
    )
    scoperti = [(nome, s) for nome, s in rimpiccioliscono if s not in spenti]
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
        i for i, (sel, corpo, ctx) in enumerate(regole)
        if ":active" in sel and "scale(" in corpo
        and not any("prefers-reduced-motion" in at for at in ctx)
    )
    blocco = max(
        i for i, (sel, corpo, ctx) in enumerate(regole)
        if "transform: none" in corpo and ".btn-icon:active" in sel
        and any("prefers-reduced-motion: reduce" in at for at in ctx)
    )
    assert blocco > ultimo_scale, "il blocco del movimento ridotto non e' piu' in fondo"
    casa = (css_levels.ASSETS / "home-style.css").read_text(encoding="utf-8")
    assert not [
        sel for sel, corpo, _ in css_levels.rules(casa) if ":active" in sel and "scale(" in corpo
    ], "home-style.css rimpicciolisce al tocco: il blocco in mobile-style.css non la copre"
