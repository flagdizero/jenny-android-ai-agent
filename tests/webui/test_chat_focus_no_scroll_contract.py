"""Rientrando in chat il campo si mette a fuoco **senza far scorrere la pagina**.

Segnalato dall'utente il 22/09/2026: passando da Memoria alla chat, per un
istante il footer conteneva **una sola voce**. La sua foto mostrava molto di
piu': tutto il guscio schiacciato a sinistra e tagliato, la chat mozzata, il
composer dimezzato — e il footer, ridotto a quel pezzo, con dentro solo
l'ultima voce.

La catena, misurata sul banco:

1. Entrando in chat da uno scorrimento, `_animateSlideIn` mette alla vista
   `translateX(100%)` e la riporta a zero in 0,2 s. Per tutta la scivolata la
   vista sta **fuori dallo schermo**.
2. In chat lo scroller **e' il documento** (v. il getter `_scroller` in
   `mobile-chat.js`): con la vista spostata, la larghezza scorrevole passa da
   590 a **1180**.
3. `activate()` mette a fuoco il campo di scrittura. Quel campo e' dentro la
   vista, quindi **fuori dallo scrollport** — e mettere a fuoco qualcosa fuori
   vista fa una cosa sola: il browser **scorre per raggiungerlo**.
4. Scorre di lato, e si porta dietro `html`, `body`, `.app`, `.main` e il
   footer. Misura: `.app` a **x -384**. Con `preventScroll: true`: **x 0**.

E spiega il verso unico: uscendo dalla chat nessuno mette a fuoco niente,
quindi chat → Memoria non ha mai avuto il difetto.

**Perche' un controllo sul sorgente.** La prova vera e' una misura di layout
dentro un browser, e girarla a ogni commit vorrebbe dire un browser nella
suite. Qui basta molto meno: la regola e' *quella chiamata porta
`preventScroll`*, e un banco che legge la riga la difende per sempre al costo di
niente.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHAT_JS = ROOT / "jenny" / "templates" / "ui" / "assets" / "mobile-chat.js"


def _activate(src: str) -> str:
    m = re.search(r"\n  activate\(\)\s*\{", src)
    assert m, "activate() non trovato in mobile-chat.js"
    i = src.index("{", m.end() - 1)
    prof, j, stringa = 0, i, None
    while j < len(src):
        c, due = src[j], src[j : j + 2]
        if stringa:
            if c == "\\":
                j += 2
                continue
            if c == stringa:
                stringa = None
        elif due == "//":
            j = src.index("\n", j)
            continue
        elif due == "/*":
            j = src.index("*/", j) + 2
            continue
        elif c in "\"'`":
            stringa = c
        elif c == "{":
            prof += 1
        elif c == "}":
            prof -= 1
            if prof == 0:
                return src[i : j + 1]
        j += 1
    raise AssertionError("graffe sbilanciate in activate()")


def test_entering_the_chat_focuses_without_scrolling() -> None:
    body = _activate(CHAT_JS.read_text(encoding="utf-8"))
    fuochi = re.findall(r"\.focus\(([^)]*)\)", body)
    assert fuochi, "activate() non mette piu' a fuoco il campo"
    for arg in fuochi:
        # `preventScroll: true`, non la parola: `{ preventScroll: false }` la
        # contiene e scorre di lato lo stesso.
        assert re.search(r"\bpreventScroll\s*:\s*true\b", arg), (
            "activate() mette a fuoco il campo senza `preventScroll: true`. "
            "Entrando da uno scorrimento la vista e' ancora fuori schermo, il "
            "documento e' largo il doppio, e il browser scorre di lato per "
            "raggiungere il campo: il guscio si schiaccia a sinistra e il "
            "footer resta con una voce sola."
        )
