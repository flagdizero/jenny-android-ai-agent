"""Quel che il giro UX del 10/10/2026 ha reso visibile.

- **Eliminare per sempre** ha un bottone rosso che lo dice («Elimina per
  sempre»), non il «Conferma» color accento di ogni domanda: un quaderno se ne
  va con pagine, bacheca e conversazione, e non torna.
- **Le azioni della pressione lunga** hanno un ⋯ visibile: sulle schede della bacheca, e in fondo alla
  fila delle pagine (la modalita' ordina). Prima, chi non sapeva di poter
  tenere premuto non le trovava. Le righe delle app e le copertine dei
  quaderni no: lì basta la pressione lunga.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"


def _src(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def test_deleting_for_good_asks_with_a_red_button_that_says_so() -> None:
    dialog = _src("shared/dialog.js")
    assert "{ danger = false } = {}" in dialog
    assert "okBtn.classList.toggle('oc-btn-danger', danger)" in dialog, (
        "il colore di pericolo deve spegnersi alla domanda dopo"
    )
    css = _src("mobile-style.css")
    block = css.split(".oc-btn-confirm.oc-btn-danger {", 1)[1].split("}", 1)[0]
    assert "var(--error)" in block
    flow = _src("shared/project-delete.js")
    assert "ok: 'workspace.deleteProjectForever'" in flow
    assert "{ danger: true }" in flow
    assert "{ danger: true }" in _src("home-apps-trash.js"), "anche il cestino elimina per sempre"
    for lang in ("it", "en"):
        words = json.loads((ASSETS / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        ws = words["workspace"]
        assert ws["deleteProjectForever"]
        for key in ("deleteProjectConfirm", "deleteProjectConfirmWithChat"):
            text = ws[key].lower()
            assert ("per sempre" in text) if lang == "it" else ("for good" in text), (lang, key)


def test_every_long_press_has_a_visible_dots_button() -> None:
    who = _src("home-who.js")
    assert "_more(" not in who, "le copertine dei quaderni non hanno il ⋯: basta la pressione lunga"
    board = _src("home-board.js")
    assert "home-more home-board-more" in board
    assert "e.key === 'Enter' && e.shiftKey" in board, "a tastiera la barra della scheda e' ⇧⏎"
    launcher = _src("mobile-launcher.js")
    assert "launcher-row-more" not in launcher, (
        "le righe delle app non hanno il ⋯: basta la pressione lunga, il gesto standard"
    )
    strip = _src("home-strip.js")
    assert "more.addEventListener('click', () => this.openSort())" in strip
    css = _src("home-style.css")
    for selector in (".home-more {", ".home-strip-more {"):
        block = css.split(selector, 1)[1].split("}", 1)[0]
        assert "40px" in block, f"{selector} piu' piccolo di un dito"
