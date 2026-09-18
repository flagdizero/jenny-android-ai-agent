"""L'ordine della dock nei docs deve essere quello del DOM.

``docs/using/webui-tour.md`` elenca i cinque slot «in this order», e quell'ordine
non è cosmetico: è anche l'ordine del carosello dello swipe (``_visibleModes``),
quindi la pagina che lo sbaglia insegna la gesture sbagliata. Aveva
Chat·Apps·Wiki dove il DOM ha chat·wiki·apps — Apps sta al centro di proposito,
perché è lo slot più raggiungibile dal pollice.

Si confronta la sequenza, non i nomi presi uno per uno: uno slot spostato è
esattamente il difetto, e un test su «ci sono tutti» non lo vedrebbe.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OFFICINA_HTML = ROOT / "jenny" / "templates" / "ui" / "officina.html"
TOUR_DOC = ROOT / "docs" / "using" / "webui-tour.md"

# ``onboarding`` è il sesto slot, nascosto dopo il primo avvio: la pagina lo
# descrive a parte e non nella tabella, quindi resta fuori dal confronto.
_HIDDEN_MODES = {"onboarding"}

# Come la tabella nomina ciascun mode. La chiave è il ``data-mode`` del DOM.
_DOC_LABELS = {
    "chat": "Chat",
    "graph": "Wiki",
    "apps": "Apps",
    "workspace": "Workspace",
    "settings": "Settings",
}


def _dom_order() -> list[str]:
    """I ``data-mode`` degli slot della dock, nell'ordine in cui stanno in pagina.

    Si estrae il tag e poi l'attributo, non i due in sequenza: l'ordine degli
    attributi dentro il tag non è garantito, e una regex che lo assume perde in
    silenzio lo slot scritto al contrario — è successo con ``chat``, e il test
    accusava il documento invece di sé stesso.
    """
    html = OFFICINA_HTML.read_text("utf-8")
    modes: list[str] = []
    for tag in re.findall(r"<div\b[^>]*>", html):
        classes = re.search(r'class="([^"]*)"', tag)
        # ``dock-item`` come *token*: lo slot attivo porta ``class="dock-item
        # active"``, e un confronto sulla stringa esatta lo perdeva — che è
        # come ``chat`` era sparito, facendo accusare il documento.
        if not classes or "dock-item" not in classes.group(1).split():
            continue
        found = re.search(r'data-mode="([a-z]+)"', tag)
        if found:
            modes.append(found.group(1))
    return [m for m in modes if m not in _HIDDEN_MODES]


def _doc_order() -> list[str]:
    text = TOUR_DOC.read_text("utf-8")
    rows = re.findall(r"^\| *[^|]+ *\| *\*\*([A-Za-z]+)\*\* *\|", text, re.M)
    by_label = {label: mode for mode, label in _DOC_LABELS.items()}
    return [by_label[r] for r in rows if r in by_label]


def test_the_docs_list_the_dock_in_dom_order() -> None:
    dom = _dom_order()
    doc = _doc_order()

    assert dom, "nessuno slot trovato in officina.html: il markup della dock è cambiato"
    assert doc, "nessuna riga riconosciuta nella tabella di webui-tour.md"
    assert doc == dom, (
        f"webui-tour.md elenca {doc}, il DOM ha {dom}. "
        "L'ordine è anche quello del carosello dello swipe: sbagliarlo insegna "
        "la gesture sbagliata."
    )
