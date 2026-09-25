"""Il composer flottante è una **pillola corta e centrata**, e lei ci sta sopra.

La barra edge-to-edge si leggeva come un toast di sistema (12:1); il
beccuccio che l'ha seguita era un triangolo da 20 dp su un bordo da 1300 px,
che il clamp spingeva via da sotto di lei. La pillola cambia quattro cose:

1. è larga una frazione dello schermo e centrata — la proporzione della barra
   di Claude o di Spotlight — non a filo dei bordi;
2. il legame con lei non è un beccuccio ma la **posizione**: `parkX` la mette
   in piedi sul cap del suo lato e `parkTop` le tiene i piedi sul bordo alto,
   anche quando il testo va a capo;
3. la pallina d'invio è più piccola del cap (non concentrica) e la freccia è
   disegnata, non un glifo di testo;
4. la pillola ha un'ombra, e il bordo si accende con il testo.

Come i test-fratelli (``test_floating_palette_contract``), non c'è un runner
Kotlin: le asserzioni sono sul sorgente, sui pezzi che nessun compilatore
tiene insieme da sé.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "android/app/src/main/java/com/flagdizero/jenny/FloatingOverlayController.kt"


def _read() -> str:
    return CONTROLLER.read_text(encoding="utf-8")


def _const(source: str, name: str) -> str:
    m = re.search(rf"const val {name}\s*=\s*([^\n]+)", source)
    assert m, f"{name} non è più leggibile"
    return m.group(1).strip()


def _fun(source: str, signature: str) -> str:
    m = re.search(re.escape(signature) + r" \{(.*?)\n    \}", source, re.S)
    assert m, f"{signature} non è più leggibile"
    return m.group(1)


class TestThePill:
    """Corta, centrata, mai a tutto schermo."""

    def test_is_a_fraction_of_the_screen_wide(self):
        source = _read()
        ratio = float(_const(source, "PILL_WIDTH_RATIO").rstrip("f"))
        # Sotto il 50% è troppo stretta per scriverci; sopra il 75% torna una
        # fascia. Il 62% è la proporzione della barra di Claude sul suo schermo.
        assert 0.5 <= ratio <= 0.75, f"PILL_WIDTH_RATIO={ratio}: non è più una pillola"
        assert re.search(
            r"private fun pillWidth\(ctx: Context\): Int =\s*\(screenWidth\(ctx\) \* PILL_WIDTH_RATIO\)",
            source,
        ), "pillWidth non deriva più dallo schermo e dalla frazione"

    def test_the_row_centers_it(self):
        """La pillola non è più `MATCH_PARENT`: ha la larghezza di `pillWidth`
        e il row la centra. Se torna MATCH_PARENT è di nuovo edge-to-edge."""
        source = _read()
        body = _fun(source, "private fun buildInputRow(ctx: Context): View")
        assert "Gravity.CENTER_HORIZONTAL" in body
        assert re.search(r"row\.addView\(bar, LinearLayout\.LayoutParams\(\s*pillWidth\(ctx\)", body), (
            "la barra è tornata MATCH_PARENT nel row: edge-to-edge"
        )

    def test_no_bubble_tail(self):
        source = _read()
        assert "TAIL_W_DP" not in source and "barBubble" not in source, (
            "il beccuccio è tornato: era un glitch alla scala della pillola, "
            "e il legame con lei ora è la posizione"
        )

    def test_has_a_shadow(self):
        """Fondo e bordo vengono dallo stesso tema: senza ombra sono due colori
        vicini su un terzo, e la forma sembra incollata al wallpaper."""
        source = _read()
        body = _fun(source, "private fun buildInputRow(ctx: Context): View")
        assert "elevation = dp(ctx, PILL_ELEVATION_DP)" in body
        assert "clipToPadding = false" in body, "il row taglierebbe l'ombra"

    def test_already_opens_from_pill(self):
        """`applyPill` va chiamata **prima** di `visibility = VISIBLE`, o
        l'utente vede per un frame la geometria vecchia."""
        source = _read()
        m = re.search(
            r"expanded = true(.*?)inputRow\?\.visibility = View\.VISIBLE", source, re.S
        )
        assert m, "il blocco intorno a expanded=true non è più leggibile"
        assert "applyPill(ctx)" in m.group(1)


class TestSheStandsOnTop:
    """Il legame è la posizione: in piedi sul cap del suo lato."""

    def test_park_x_points_at_the_cap_with_chat_open(self):
        source = _read()
        body = _fun(source, "private fun parkX(ctx: Context, out: Boolean = false): Int")
        assert "out && expanded" in body, (
            "parkX non distingue più la finestra aperta: da parcheggiata lei "
            "andrebbe in mezzo allo schermo su una pillola che non c'è"
        )
        assert "pillWidth(ctx)" in body and "AXIS_RATIO" in body
        # Il cap è la pallina: padding più mezza pallina.
        assert "dp(ctx, BAR_PAD_DP) + dp(ctx, SEND_DP) / 2" in body

    def test_park_top_follows_the_measured_pill(self):
        """Quando il testo va a capo la pillola cresce: lei deve salire con la
        pillola, non finirci dietro. Quindi `parkTop` legge l'altezza misurata,
        e la tastiera, ma solo a chat aperta — parcheggiata la riga è quella a
        una riga, o salterebbe quando il composer compare."""
        source = _read()
        body = _fun(source, "private fun parkTop(ctx: Context): Int")
        assert "expanded && pillHeightPx > 0" in body
        assert "chatBottomInsetPx" in body
        assert "FEET_RATIO" in body and "FEET_GAP_DP" in body

    def test_the_growing_pill_pushes_it_up(self):
        source = _read()
        body = _fun(source, "private fun buildInputRow(ctx: Context): View")
        assert "addOnLayoutChangeListener" in body
        assert "pillHeightPx = h" in body
        assert "slideTo(ctx, parkX(ctx, out = true), parkTop(ctx))" in body

    def test_the_sprite_fractions_are_the_measured_ones(self):
        """0,87 e 0,52 vengono dai pixel opachi di `jenny-body-front-idle`:
        piedi alla riga 668/768, corpo fra le colonne 229–572. Cambiare lo
        sprite senza rimisurarle la mette a galleggiare o a sprofondare."""
        source = _read()
        assert _const(source, "FEET_RATIO") == "0.87f"
        assert _const(source, "AXIS_RATIO") == "0.52f"


class TestTheInsetsDoNotEraseThePill:
    """Il listener degli insets IME scatta nell'istante in cui la finestra
    prende il fuoco, cioè subito dopo `applyPill`: se riscrive tutti e quattro
    i padding cancella la geometria un frame dopo che è stata calcolata."""

    def test_the_ime_listener_touches_only_the_bottom(self):
        source = _read()
        m = re.search(
            r"setOnApplyWindowInsetsListener \{(.*?)\n            \}", source, re.S
        )
        assert m, "il listener degli insets non è più leggibile"
        body = m.group(1)
        assert "it.paddingLeft" in body and "it.paddingRight" in body

    def test_the_keyboard_pushes_it_up(self):
        source = _read()
        m = re.search(
            r"setOnApplyWindowInsetsListener \{(.*?)\n            \}", source, re.S
        )
        assert m
        assert "chatBottomInsetPx = bottom" in m.group(1)
        assert "slideTo(ctx, parkX(ctx, out = true), parkTop(ctx))" in m.group(1)


class TestTheBall:
    """Più piccola del cap, sempre visibile, freccia disegnata."""

    def test_is_not_concentric_with_the_cap(self):
        """A 38 in 46 erano due cerchi a 4 dp: si leggeva come il pomello di
        un interruttore. Il disco deve lasciare almeno 6 dp d'aria."""
        source = _read()
        pad = int(_const(source, "BAR_PAD_DP"))
        assert pad >= 6, f"BAR_PAD_DP={pad}: la pallina torna concentrica al cap"
        assert _const(source, "PILL_DP") == "SEND_DP + 2 * BAR_PAD_DP"

    def test_always_has_a_background(self):
        source = _read()
        body = _fun(source, "private fun syncSend(bloom: Boolean = true)")
        assert "background = null" not in body
        assert "palette.border" in body and "palette.accent" in body

    def test_the_arrow_is_drawn(self):
        """Il glifo `↑` è sottile, con la punta da carattere, e siede sulla
        linea di base invece che al centro del disco."""
        source = _read()
        assert "\\u2191" not in source, "la freccia è tornata un carattere di testo"
        # Disegnata da `strokeIcon`, che dal 18/09/2026 fa anche l'icona del
        # chip: due path, un solo `Drawable` di boilerplate.
        assert "private fun arrowIcon(ctx: Context): Drawable" in source
        assert "strokeCap = Paint.Cap.ROUND" in source

    def test_the_border_lights_up_with_the_text(self):
        source = _read()
        body = _fun(source, "private fun syncSend(bloom: Boolean = true)")
        assert "strokeColor()" in body
        assert "if (sendLit) blend(palette.accent, palette.border" in source
