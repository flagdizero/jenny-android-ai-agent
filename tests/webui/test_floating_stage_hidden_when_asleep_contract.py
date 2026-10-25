"""Da ferma, la mascotte flottante non copre lo schermo delle altre app.

Il palco della mascotte e' una finestra a schermo intero. Da ferma era trasparente
e non toccabile, ma **visibile**: ogni tocco del telefono la attraversava, e
Android lo segnalava all'app sotto come «schermata coperta»
(``FLAG_WINDOW_IS_OBSCURED``). Le schermate che si difendono dal tapjacking
(``filterTouchesWhenObscured``: «Accedi con Google», le conferme) scartavano ogni
tocco finche' Jenny era a schermo; le app normali no — Claude rispondeva, Accedi
con Google no (15/10/2026). Su jenny_s22 ``dumpsys window`` dava il palco
``fillxfill`` e ``isVisible=true`` con Jenny parcheggiata.

Ora da ferma la vista radice del palco e' ``GONE`` (la finestra diventa
invisibile e smette di coprire), e torna visibile solo in volo e in chat. Il
Kotlin non gira in CI: il contratto e' sul sorgente.
"""

from __future__ import annotations

from support.kotlin_source import function_body, read_code


def _code() -> str:
    return read_code("FloatingOverlayController")


def test_asleep_the_stage_root_is_gone() -> None:
    body = function_body(_code(), "applyStage")
    assert "flags == STAGE_ASLEEP" in body
    assert "View.GONE" in body and "View.VISIBLE" in body
    # Prima dell'uscita anticipata sui flag: anche se non cambiano, la
    # visibilita' si allinea.
    early = body.index("if (lp.flags == wanted && lp.softInputMode == softInput) return")
    assert body.index("View.GONE") < early


def test_the_stage_is_born_hidden() -> None:
    body = function_body(_code(), "attach")
    assert body.index("container.visibility = View.GONE") < body.index("wm.addView(container, lp)"), (
        "il palco nasce fermo: va aggiunto gia' nascosto"
    )


def test_the_flight_handoff_waits_for_the_stage_to_draw() -> None:
    """Il palco riparte da nascosto: spegnere lei «al giro dopo» poteva cadere
    prima del suo primo fotogramma, e per un attimo non c'era nessuna Jenny."""
    body = function_body(_code(), "startFlight")
    assert "afterStageDrawn { if (flight != null) mascot.visibility = View.INVISIBLE }" in body
    assert "main.post { if (flight != null) mascot.visibility = View.INVISIBLE }" not in body
