package com.flagdizero.jenny.wear

/**
 * Il testo di una risposta com'è da dire a voce: senza emoji e senza i segni
 * con cui un modello impagina (`**grassetto**`, titoli con `#`, elenchi con
 * `-`, trattini doppi, tabelle, link). A schermo quei segni si leggono o si
 * disegnano; una voce li pronuncia — «asterisco asterisco» — o ci inciampa.
 *
 * Kotlin puro, senza Android: si prova con un test normale.
 */
object Spoken {

    /** Emoji e simboli da disegno: facce, frecce, forme, bandiere, e i loro pezzi di colla. */
    private val EMOJI = Regex(
        "[\\x{1F000}-\\x{1FAFF}\\x{2190}-\\x{21FF}\\x{2300}-\\x{23FF}\\x{25A0}-\\x{27BF}" +
            "\\x{2B00}-\\x{2BFF}\\x{E0020}-\\x{E007F}\\x{FE0F}\\x{200D}\\x{20E3}\\x{3030}\\x{303D}\\x{3297}\\x{3299}]"
    )
    private val LINK = Regex("!?\\[([^\\]]*)]\\([^)]*\\)")
    private val URL = Regex("https?://\\S+")
    private val RULE = Regex("^\\s*([-*_=]\\s*){3,}$")
    private val TABLE_RULE = Regex("^[\\s|:-]+$")
    private val LINE_START = Regex("^\\s*(#{1,6}\\s+|>+\\s*|[-*+•·]\\s+)+")
    private val DASHES = Regex("\\s*(-{2,}|[—–])\\s*")
    private val MARKS = Regex("[*`~]+|(?<![\\p{L}\\p{N}])_+|_+(?![\\p{L}\\p{N}])")
    private val ENDS = Regex("[.!?:;,…]$")
    private val SPACES = Regex("\\s+")
    private val SPACE_BEFORE = Regex("\\s+([.,;:!?])")
    private val DOUBLED = Regex("([,;:])(\\s*[,;:.])+")

    fun clean(text: String): String =
        text.lineSequence()
            .filterNot { RULE.matches(it) || (it.contains('|') && TABLE_RULE.matches(it)) }
            .map { line ->
                line.replace(LINK, "$1").replace(URL, "")
                    .replace(LINE_START, "")
                    .replace(EMOJI, " ")
                    .replace('|', ',')
                    .replace(DASHES, ", ")
                    .replace(MARKS, "")
                    .trim().trim(',').trim()
            }
            .filter { it.isNotEmpty() }
            // Una riga di elenco o un titolo non finiscono con un punto: senza,
            // la voce li legge attaccati alla riga dopo.
            .joinToString(" ") { if (ENDS.containsMatchIn(it)) it else "$it." }
            .replace(SPACES, " ")
            .replace(SPACE_BEFORE, "$1")
            .replace(DOUBLED, "$1")
            .trim()

    private val SENTENCE_END = Regex("(?<=[.!?…])\\s+")

    /**
     * [text] a pezzi da dire uno dopo l'altro, tagliati a fine frase. [limits]
     * è la lunghezza massima del primo pezzo, del secondo e così via; l'ultima
     * vale per tutti quelli dopo. I primi si tengono corti perché la voce parta
     * presto e il pezzo dopo arrivi prima che quello in corso finisca. Una
     * frase più lunga del limite si taglia a una virgola, o a uno spazio.
     */
    fun pieces(text: String, vararg limits: Int): List<String> {
        val out = mutableListOf<String>()
        var current = ""
        fun limit() = limits[minOf(out.size, limits.lastIndex)]
        fun flush() {
            if (current.isNotEmpty()) out += current
            current = ""
        }
        for (sentence in text.trim().split(SENTENCE_END).filter { it.isNotBlank() }) {
            var rest = sentence
            if (current.isNotEmpty() && current.length + 1 + rest.length > limit()) flush()
            while (rest.length > limit()) {
                val cut = rest.lastIndexOf(", ", limit() - 1).takeIf { it > 0 }?.plus(1)
                    ?: rest.lastIndexOf(' ', limit()).takeIf { it > 0 }
                    ?: limit()
                out += rest.substring(0, cut).trim()
                rest = rest.substring(cut).trim()
            }
            current = if (current.isEmpty()) rest else "$current $rest"
        }
        flush()
        return out
    }
}
