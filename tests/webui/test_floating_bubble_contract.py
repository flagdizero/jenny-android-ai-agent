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

    def test_il_beccuccio_esce_dal_lato_giusto(self):
        """Il fondo della barra sa da che lato mettere il beccuccio.

        `parkedRight` è false se la mascotte è a sinistra: il beccuccio va a
        sinistra (`tailOnLeft = true`). Vero se è a destra: beccuccio a destra.
        Il legame `tailOnLeft = !parkedRight` deve esistere, o Fumetto punta
        nel vuoto quando l'utente la sposta dall'altro lato.
        """
        source = _read()
        assert "fun barBubble(ctx: Context, tailOnLeft: Boolean)" in source
        assert "!parkedRight" in source, (
            "il lato del beccuccio non è più derivato da parkedRight: se la "
            "mascotte cambia bordo, il beccuccio finisce a puntare al niente"
        )

    def test_il_composer_lascia_spazio_alla_mascotte(self):
        """La barra sta *accanto* a lei, non sotto: il row ha padding
        asimmetrico che rispecchia il suo lato."""
        source = _read()
        assert "fun applyBubble(" in source
        assert "fun mascotBandInset(" in source
        # Il padding del row cambia in base al lato: il pattern `if (tailOnLeft)`
        # deve comparire almeno una volta dentro applyBubble.
        m = re.search(r"private fun applyBubble\(ctx: Context\) \{(.*?)\n    \}", source, re.S)
        assert m, "applyBubble non è più leggibile"
        assert "tailOnLeft" in m.group(1)
        assert "mascotBandInset" in m.group(1)

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
