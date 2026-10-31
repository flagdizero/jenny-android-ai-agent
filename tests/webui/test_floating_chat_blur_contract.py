"""A chat aperta, l'app sotto la mascotte flottante è sfocata — o, dove Android
non sfoca, velata più scura.

Sopra una pagina fitta la conversazione non si leggeva (25/10/2026). La
sfocatura fra finestre c'è dal 12 ed è ``FLAG_BLUR_BEHIND`` sul palco, ma
Android la può spegnere quando vuole (risparmio energetico, emulatori senza
GPU): il velo deve saperlo, e cambiare con lei anche a chat aperta. Il Kotlin
non gira in CI: il contratto è sul sorgente.
"""

from __future__ import annotations

from support.kotlin_source import function_body, read_code


def _code() -> str:
    return read_code("FloatingOverlayController")


def test_only_the_open_chat_blurs_what_is_behind() -> None:
    body = function_body(_code(), "applyStage")
    assert "if (flags == STAGE_CHAT) flags or blurFlag() else flags" in body
    assert "lp.flags = wanted" in body
    assert "FLAG_BLUR_BEHIND" in function_body(_code(), "blurFlag")
    assert "lp.blurBehindRadius" in function_body(_code(), "stageParams")


def test_the_scrim_follows_the_blur_android_gives() -> None:
    code = _code()
    watch = function_body(code, "watchBlur")
    assert "isCrossWindowBlurEnabled" in watch
    assert "addCrossWindowBlurEnabledListener" in watch
    assert "scrim?.background = scrimFor(on)" in function_body(code, "setBlurOn")
    assert "watchBlur(ctx, wm)" in function_body(code, "attach")
    # Smontata la finestra, il listener se ne va con lei: tenerlo vorrebbe dire
    # un velo vecchio vestito sulla prossima.
    assert "unwatchBlur(wm)" in function_body(code, "detach")


def test_without_blur_the_scrim_starts_dark() -> None:
    """Senza sfocatura la cima del velo non può essere trasparente: lì sotto
    l'app si leggerebbe nitida, che è il difetto."""
    code = _code()
    # Funzione a espressione, senza graffe di corpo: si legge fino alla prossima.
    start = code.index("private fun scrimFor(")
    body = code[start:code.index("private fun", start + 1)]
    with_blur, without = body.split("} else {")
    assert "0x1A000000" in with_blur
    assert "0xD9000000" in without
