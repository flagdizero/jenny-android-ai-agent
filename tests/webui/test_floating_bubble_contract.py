"""Il composer flottante è una **nuvoletta**, non una fascia sotto lo schermo.

La barra edge-to-edge, con altre app sotto, si leggeva come un pezzo di
sistema. Fumetto la cambia in tre punti:

1. la geometria orizzontale della barra dipende dal lato su cui la mascotte è
   parcheggiata — bar a destra se lei è a sinistra, e viceversa;
2. la barra ha un **beccuccio** che le punta addosso;
3. la palina d'invio c'è sempre — spenta è appena visibile, ma non è mai
   assente.

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


class TestIlBeccuccio:
    """La barra è una nuvoletta: pillola più triangolo dal lato di lei."""

    def test_il_beccuccio_ha_le_sue_costanti(self):
        source = _read()
        # I due numeri sono `[TAIL_W_DP]` (quanto sporge) e `[TAIL_H_DP]`
        # (la base). Se uno dei due sparisce, sta cambiando la firma della
        # nuvoletta senza dirlo — questo test è dove si accorge.
        assert re.search(r"const val TAIL_W_DP\s*=\s*\d+", source)
        assert re.search(r"const val TAIL_H_DP\s*=\s*\d+", source)

    def test_il_beccuccio_punta_dove_sta_lei(self):
        """Il vertice cade sotto il centro del suo corpo.

        Il primo giro lo metteva sul *fianco* della barra, come nel mockup —
        dove però lei era disegnata accanto alla barra. Sul telefono
        ``parkTop`` la mette **sopra** la banda, quindi un beccuccio
        orizzontale puntava nel vuoto. La x deve venire da dove sta lei
        davvero, non da un lato scelto a tavolino.
        """
        source = _read()
        assert "fun barBubble(ctx: Context, tailCenterX: Int)" in source
        assert "fun mascotCenterX(" in source
        assert "parkX(ctx, out = true)" in source, (
            "il centro della mascotte non è più preso dalla sua posizione "
            "«out»: il beccuccio punterebbe dove lei non è"
        )
        m = re.search(r"private fun applyBubble\(ctx: Context\) \{(.*?)\n    \}", source, re.S)
        assert m, "applyBubble non è più leggibile"
        assert "mascotCenterX" in m.group(1)

    def test_il_composer_e_staccato_dai_bordi(self):
        """Edge-to-edge era il difetto di partenza: sopra l'app di qualcun
        altro si leggeva come una fascia di sistema."""
        source = _read()
        m = re.search(r"const val COMPOSER_EDGE_DP\s*=\s*(\d+)", source)
        assert m, "COMPOSER_EDGE_DP non è più leggibile"
        assert int(m.group(1)) >= 20, (
            f"la barra è tornata a {m.group(1)} dp dai bordi: a filo di schermo "
            "non si legge come un oggetto che galleggia"
        )

    def test_il_composer_si_apre_gia_come_nuvoletta(self):
        """`applyBubble` va chiamata **prima** di `visibility = VISIBLE`.

        Se corre dopo, l'utente vede per un frame la barra vecchia
        (geometria d'apertura, non del lato attuale) e poi cambia. Un flash
        di 16ms in un composer non si perdona.
        """
        source = _read()
        m = re.search(
            r"expanded = true\s*\n\s*isChatOpen = withInput.*?inputRow\?\.visibility = if \(withInput\)",
            source, re.S,
        )
        assert m, "il blocco intorno a expanded=true non è più leggibile"
        assert "applyBubble(ctx)" in m.group(0), (
            "applyBubble non è chiamata prima di rendere visibile il row: il "
            "beccuccio nascerebbe sul lato di prima"
        )


class TestGliInsetsNonCancellanoLaNuvoletta:
    """Il difetto che ha fatto sembrare tutto peggio di prima.

    Il listener degli insets IME riscriveva **tutti e quattro** i padding del
    row con valori fissi, e scatta nell'istante in cui la finestra prende il
    fuoco — cioè subito dopo ``applyBubble``. Risultato: la geometria della
    nuvoletta veniva cancellata un frame dopo essere stata calcolata, la barra
    tornava edge-to-edge e restava solo un triangolo appiccicato a caso.
    """

    def test_il_listener_ime_tocca_solo_il_fondo(self):
        source = _read()
        m = re.search(
            r"setOnApplyWindowInsetsListener \{(.*?)\n            \}", source, re.S
        )
        assert m, "il listener degli insets non è più leggibile"
        body = m.group(1)
        assert "it.paddingLeft" in body and "it.paddingRight" in body, (
            "il listener degli insets riscrive di nuovo i lati invece di "
            "preservarli: cancella la geometria di applyBubble"
        )


class TestLaPallina:
    """La palina è sempre visibile. Da spenta appena, da accesa forte."""

    def test_la_pallina_ha_sempre_un_fondo(self):
        """Non deve esistere un ramo `background = null`: era il difetto della
        prima versione (freccia nuda a barra vuota, letta come simbolo lasciato
        lì in mezzo). Se qualcuno rimette null, questo test cade."""
        source = _read()
        m = re.search(
            r"private fun syncSend\(bloom: Boolean = true\) \{(.*?)\n    \}",
            source, re.S,
        )
        assert m, "syncSend non è più leggibile"
        body = m.group(1)
        # `background = null` a barra vuota: quello NO.
        assert "background = null" not in body, (
            "syncSend torna a spegnere la pallina: la freccia resta nuda e "
            "brutta, come nella prima versione"
        )
        # Da spenta ha il colore dei bordi, da accesa il colore accento.
        assert "palette.border" in body
        assert "palette.accent" in body


class TestLaGeometriaNonSiMuove:
    """La banda del composer resta alta 70 dp, o la mascotte cade più in
    basso — v. ``parkTop`` che misura da qui."""

    def test_composer_dp_e_ancora_70(self):
        source = _read()
        assert re.search(r"const val COMPOSER_DP\s*=\s*46\s*\+\s*12\s*\+\s*12", source)
