package com.flagdizero.jenny.wear

import org.junit.Assert.assertEquals
import org.junit.Test

class SpokenTest {

    @Test
    fun plainTextStaysAsItIs() {
        assertEquals("Sono le 5 e fuori ci sono 20°.", Spoken.clean("Sono le 5 e fuori ci sono 20°."))
        assertEquals("Costa 3,50 €, cioè l'1% in più.", Spoken.clean("Costa 3,50 €, cioè l'1% in più."))
    }

    @Test
    fun emphasisMarksGo() {
        assertEquals("Domani piove di sicuro.", Spoken.clean("**Domani** piove _di sicuro_."))
        assertEquals("Usa il comando ls qui.", Spoken.clean("Usa il comando `ls` ~~qui~~."))
    }

    @Test
    fun underscoresInsideAWordStay() {
        assertEquals("Il file è nota_spesa.", Spoken.clean("Il file è nota_spesa."))
    }

    @Test
    fun emojiGo() {
        assertEquals("Fatto! Ci vediamo dopo.", Spoken.clean("Fatto! ✅ Ci vediamo dopo 👋🏽."))
        assertEquals("Vai in Italia, poi a destra.", Spoken.clean("Vai in Italia 🇮🇹, poi ➡️ a destra."))
    }

    @Test
    fun listsAndTitlesBecomeSentences() {
        assertEquals(
            "Da comprare. latte. pane integrale. uova.",
            Spoken.clean("## Da comprare\n\n- latte\n- **pane** integrale\n* uova\n"),
        )
        assertEquals("1. apri l'app. 2. premi invio.", Spoken.clean("1. apri l'app\n2. premi invio"))
    }

    @Test
    fun dashesBecomeAPause() {
        assertEquals("Il treno, quello delle 8, è in ritardo.", Spoken.clean("Il treno -- quello delle 8 — è in ritardo."))
        assertEquals("Prima. Dopo.", Spoken.clean("Prima.\n\n---\n\nDopo."))
        assertEquals("Un pianoforte a mezza-coda.", Spoken.clean("Un pianoforte a mezza-coda."))
    }

    @Test
    fun linksKeepTheirWords() {
        assertEquals("Leggi la guida.", Spoken.clean("Leggi [la guida](https://example.com/a_b)."))
        assertEquals("Il sito è", Spoken.clean("Il sito è https://example.com/pagina").trimEnd('.'))
    }

    @Test
    fun tablesAreReadRowByRow() {
        assertEquals(
            "Giorno, Tempo. Lunedì, sole.",
            Spoken.clean("| Giorno | Tempo |\n|---|---|\n| Lunedì | sole |"),
        )
    }

    @Test
    fun onlyDecorationLeavesNothing() {
        assertEquals("", Spoken.clean("✨ **  ** ---"))
    }

    @Test
    fun aShortTextIsOnePiece() {
        assertEquals(listOf("Sono le 5. Buona giornata!"), Spoken.pieces("Sono le 5. Buona giornata!", 40, 80))
    }

    @Test
    fun piecesEndWhereASentenceEnds() {
        val text = "Prima frase corta. Seconda frase, un poco più lunga della prima. Terza! E la quarta chiude?"
        val pieces = Spoken.pieces(text, 20, 50)
        assertEquals(
            listOf("Prima frase corta.", "Seconda frase, un poco più lunga della prima.", "Terza! E la quarta chiude?"),
            pieces,
        )
        assertEquals(text, pieces.joinToString(" "))
    }

    @Test
    fun aSentenceTooLongIsCutAtACommaOrASpace() {
        val pieces = Spoken.pieces("Uno due tre, quattro cinque sei sette otto nove dieci undici dodici.", 15, 30)
        assertEquals("Uno due tre,", pieces.first())
        assertEquals(true, pieces.all { it.length <= 30 })
        assertEquals(
            "Uno due tre, quattro cinque sei sette otto nove dieci undici dodici.",
            pieces.joinToString(" "),
        )
    }

    @Test
    fun aWordWithNoSpacesIsStillCut() {
        val pieces = Spoken.pieces("a".repeat(70), 20, 30)
        assertEquals(listOf(20, 30, 20), pieces.map { it.length })
    }

    @Test
    fun nothingToSayIsNoPieces() {
        assertEquals(emptyList<String>(), Spoken.pieces("  ", 20, 30))
    }
}
