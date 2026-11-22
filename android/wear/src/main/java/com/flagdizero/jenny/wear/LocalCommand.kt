package com.flagdizero.jenny.wear

/**
 * I comandi che l'orologio capisce da solo, senza chiedere a Jenny: un timer,
 * una sveglia, aprire un'app, farsi portare in un posto, cercarne uno vicino.
 * Funzionano anche col telefono lontano e partono subito, perché la frase non
 * fa il giro fino al modello.
 *
 * Non c'è un modello qui: si riconoscono solo frasi dalla forma prevista, e
 * **solo se la frase intera è il comando**. «Timer di 10 minuti» sì; «metti un
 * timer e ricordami la pasta» no, e va a Jenny come ogni altra domanda. Nel
 * dubbio [parse] rende `null`: un comando non capito è una domanda, non un
 * errore.
 *
 * Niente Android in questo file, così le frasi si provano con un test normale.
 */
sealed interface LocalCommand {
    /** Un conto alla rovescia di [seconds], con l'etichetta detta dopo «per», se c'era. */
    data class Timer(val seconds: Int, val label: String?) : LocalCommand

    /** Una sveglia all'ora [hour]:[minute] (0–23, 0–59). */
    data class Alarm(val hour: Int, val minute: Int, val label: String?) : LocalCommand

    /**
     * Aprire l'app che si chiama [name]. Qui il nome è solo quello detto: se
     * sull'orologio non c'è un'app così, chi esegue lascia la frase a Jenny —
     * «apri il cancello» non è un'app, ed è una domanda per lei.
     */
    data class OpenApp(val name: String) : LocalCommand

    /** La navigazione verso [destination], detta com'è: la capisce la mappa. */
    data class Navigate(val destination: String) : LocalCommand

    /** Cercare sulla mappa un posto vicino: «farmacia», «benzinaio». */
    data class FindPlace(val query: String) : LocalCommand

    companion object {
        private val OPEN = Regex("^(?:apri|aprimi|avvia|lancia|open|launch|start) (.+)$")
        private val OPEN_SKIP = listOf(
            "l'applicazione ", "l'app ", "la app ", "the app ", "app ",
            "il ", "lo ", "la ", "i ", "gli ", "le ", "l'", "the ",
        )
        private val TORCH = Regex("^(?:accendi|accendimi) (?:la )?(torcia)$|^turn on the (flashlight)$")

        private val NAVIGATE = listOf(
            Regex("^(?:portami|accompagnami|guidami) (?:a |ad |al |allo |alla |all'|ai |agli |alle |in |da |dal |dallo |dalla |dall'|verso )(.+)$"),
            Regex("^naviga (?:verso |fino a |a |ad |al |alla |all'|in )(.+)$"),
            Regex("^(?:indicazioni|strada|percorso) (?:per|verso) (?:il |lo |la |l')?(.+)$"),
            Regex("^(?:navigate to|take me to|directions to|drive to) (?:the )?(.+)$"),
        )

        // «Dov'è» da solo è una domanda per Jenny («dov'è il mio telefono»):
        // è un posto da cercare solo con «un/una» o con «vicino».
        private const val NEAR = "qui vicino|vicino a me|più vicin[oa]|nei dintorni|nelle vicinanze|in zona"
        private val PLACE = listOf(
            Regex("^(?:dov'è|dove è|dove trovo|dove si trova|dove sta) (?:una |un |uno |un')(.+?)(?: (?:$NEAR))?$"),
            Regex("^(?:dov'è|dove è|dove trovo|dove si trova) (?:il |lo |la |l')(.+?) (?:$NEAR)$"),
            Regex("^(?:cerca|trova|cercami|trovami) (?:una |un |uno |un'|il |lo |la |l')?(.+?) (?:$NEAR|sulla mappa|su maps)$"),
            Regex("^(?:where is|where's|find) (?:the )?(?:nearest|closest) (.+)$"),
            Regex("^(?:where is|where's|find) (?:a |an )?(.+?) (?:nearby|near me|around here)$"),
        )

        private val NUMBERS = mapOf(
            "un" to 1, "uno" to 1, "una" to 1, "un'" to 1, "due" to 2, "tre" to 3, "quattro" to 4,
            "cinque" to 5, "sei" to 6, "sette" to 7, "otto" to 8, "nove" to 9, "dieci" to 10,
            "undici" to 11, "dodici" to 12, "tredici" to 13, "quattordici" to 14, "quindici" to 15,
            "sedici" to 16, "diciassette" to 17, "diciotto" to 18, "diciannove" to 19, "venti" to 20,
            "venticinque" to 25, "trenta" to 30, "quaranta" to 40, "quarantacinque" to 45,
            "cinquanta" to 50, "sessanta" to 60, "novanta" to 90,
            "a" to 1, "an" to 1, "one" to 1, "two" to 2, "three" to 3, "four" to 4, "five" to 5,
            "six" to 6, "seven" to 7, "eight" to 8, "nine" to 9, "ten" to 10, "eleven" to 11,
            "twelve" to 12, "fifteen" to 15, "twenty" to 20, "thirty" to 30, "forty" to 40,
            "forty-five" to 45, "fifty" to 50, "sixty" to 60, "ninety" to 90,
        )

        // Le parole più lunghe prima: «un'» non deve fermarsi a «un».
        private val NUM = "(?:\\d+|" + NUMBERS.keys.sortedByDescending { it.length }
            .joinToString("|") { Regex.escape(it) } + ")"
        private const val HOUR = "ore|ora|h|hours|hour"
        private const val MINUTE = "minuti|minuto|min|minutes|minute|mins"
        private const val SECOND = "secondi|secondo|sec|seconds|second|secs"

        private val TIMER = listOf(
            Regex("^(?:(?:imposta|metti|avvia|fai partire|punta|fammi|crea|setta|attiva)(?:mi)? )?(?:un |il )?(?:timer|conto alla rovescia)(?: (?:di|da|per|a|tra|fra))? (.+)$"),
            Regex("^(?:(?:set|start|create) )?(?:a |the )?(?:timer|countdown)(?: (?:for|of))? (.+)$"),
        )
        private val ALARM = listOf(
            Regex("^(?:(?:imposta|metti|punta|crea|setta|attiva|fammi)(?:mi)? )?(?:una |la )?sveglia(?: per)? (?:alle |all'|a )(.+)$"),
            Regex("^svegliami (?:alle |all'|a )(.+)$"),
            Regex("^(?:(?:set|create) )?(?:an |the )?alarm (?:for|at) (.+)$"),
            Regex("^wake me(?: up)? at (.+)$"),
        )

        private val FIXED = Regex("^(mezz'ora|mezzora|mezza ora|half an hour|un quarto d'ora|a quarter of an hour)")
        private val PART = Regex("^($NUM) ?($HOUR|$MINUTE|$SECOND)(?![a-z])")
        private val BARE = Regex("^(\\d+)$")
        private val HALF = Regex("^ (?:e mezz[ao]|and a half)")
        private val QUARTER = Regex("^ e un quarto")
        private val JOIN = Regex("^(?:,? (?:e|and) |, | )")
        private val LABEL = Regex("^ (?:per|for) (.+)$")

        private val CLOCK = Regex(
            "^($NUM)(?:[:.,](\\d{2})| e ($NUM|mezza|mezzo|un quarto)| (meno un quarto)| (\\d{2}))?" +
                "(?: (?:di |del |della |in the |at )?(mattina|mattino|pomeriggio|sera|notte|morning|afternoon|evening|night)| ?(am|pm|a\\.m\\.|p\\.m\\.))?" +
                "(?: (?:per|for) (.+))?$"
        )

        /** Il comando detto in [raw], o `null` se è una domanda per Jenny. */
        fun parse(raw: String): LocalCommand? {
            val text = raw.lowercase().replace('’', '\'').replace(Regex("\\s+"), " ")
                .trim().trimEnd('.', '!', '?', ' ')
            // Si provano tutte le forme: «timer for…» combacia anche con quella
            // italiana, che poi non sa leggere il resto.
            for (r in TIMER) r.find(text)?.let { m -> timer(m.groupValues[1])?.let { return it } }
            for (r in ALARM) r.find(text)?.let { m -> alarm(m.groupValues[1])?.let { return it } }
            for (r in NAVIGATE) r.find(text)?.let { return Navigate(it.groupValues[1]) }
            for (r in PLACE) r.find(text)?.let { return FindPlace(it.groupValues[1]) }
            TORCH.find(text)?.let { m -> return OpenApp(m.groupValues.drop(1).first { it.isNotEmpty() }) }
            OPEN.find(text)?.let { return openApp(it.groupValues[1]) }
            return null
        }

        /** Il nome dopo «apri», senza articoli. Una frase lunga non è un nome. */
        private fun openApp(spoken: String): OpenApp? {
            var name = spoken
            while (true) {
                val skip = OPEN_SKIP.firstOrNull { name.startsWith(it) } ?: break
                name = name.substring(skip.length)
            }
            if (name.isBlank() || name.length > 30 || name.split(' ').size > 3) return null
            return OpenApp(name)
        }

        /**
         * Quanto [label], il nome di un'app, corrisponde a [name] detto a voce:
         * 3 uguale, 2 uno comincia con l'altro, 1 lo contiene come parola
         * intera, 0 niente. Chi esegue apre l'app col punteggio più alto.
         */
        fun appMatch(label: String, name: String): Int {
            val l = label.lowercase().trim()
            val n = name.lowercase().trim()
            return when {
                l.isEmpty() || n.isEmpty() -> 0
                l == n -> 3
                l.startsWith("$n ") || n.startsWith("$l ") -> 2
                " $l ".contains(" $n ") -> 1
                else -> 0
            }
        }

        private fun number(word: String): Int? = word.toIntOrNull() ?: NUMBERS[word]

        private fun unit(word: String): Int = when (word) {
            in HOUR.split('|') -> 3600
            in MINUTE.split('|') -> 60
            else -> 1
        }

        /** «10 minuti», «un'ora e mezza», «1 ora e 30», «2 minuti e 30 secondi per la pasta». */
        private fun timer(spoken: String): Timer? {
            var rest = spoken
            var total = 0L
            var last = 0
            var label: String? = null
            while (true) {
                val fixed = FIXED.find(rest)
                val part = PART.find(rest)
                val bare = BARE.find(rest)
                when {
                    fixed != null -> {
                        total += if (fixed.value.contains("quart")) 900 else 1800
                        last = 0
                        rest = rest.substring(fixed.value.length)
                    }
                    part != null -> {
                        val n = number(part.groupValues[1]) ?: return null
                        last = unit(part.groupValues[2])
                        total += n.toLong() * last
                        rest = rest.substring(part.value.length)
                    }
                    // «un'ora e 30»: un numero nudo in fondo vale l'unità sotto.
                    bare != null && last > 1 -> {
                        total += bare.groupValues[1].toLong() * (last / 60)
                        rest = ""
                    }
                    else -> return null
                }
                HALF.find(rest)?.takeIf { last > 1 }?.let {
                    total += last / 2
                    rest = rest.substring(it.value.length)
                }
                QUARTER.find(rest)?.takeIf { last == 3600 }?.let {
                    total += 900
                    rest = rest.substring(it.value.length)
                }
                if (rest.isEmpty()) break
                val named = LABEL.find(rest)
                if (named != null) {
                    label = named.groupValues[1]
                    break
                }
                val join = JOIN.find(rest) ?: return null
                rest = rest.substring(join.value.length)
            }
            // Oltre un giorno non è un timer da polso: lo decida Jenny.
            if (total !in 1..86_400) return null
            return Timer(total.toInt(), label)
        }

        /** «7», «7:30», «7 e mezza», «8 meno un quarto», «6 di sera», «mezzogiorno». */
        private fun alarm(spoken: String): Alarm? {
            when (spoken) {
                "mezzogiorno", "noon" -> return Alarm(12, 0, null)
                "mezzanotte", "midnight" -> return Alarm(0, 0, null)
            }
            val m = CLOCK.find(spoken) ?: return null
            var hour = number(m.groupValues[1]) ?: return null
            var minute = when {
                m.groupValues[2].isNotEmpty() -> m.groupValues[2].toInt()
                m.groupValues[3].startsWith("mezz") -> 30
                m.groupValues[3] == "un quarto" -> 15
                m.groupValues[3].isNotEmpty() -> number(m.groupValues[3]) ?: return null
                m.groupValues[5].isNotEmpty() -> m.groupValues[5].toInt()
                else -> 0
            }
            if (m.groupValues[4].isNotEmpty()) {
                hour -= 1
                minute = 45
            }
            val period = m.groupValues[6] + m.groupValues[7].replace(".", "")
            when (period) {
                "pomeriggio", "sera", "afternoon", "evening", "pm" -> if (hour in 1..11) hour += 12
                "notte", "night", "am" -> if (hour == 12) hour = 0
            }
            if (hour !in 0..23 || minute !in 0..59) return null
            return Alarm(hour, minute, m.groupValues[8].ifEmpty { null })
        }
    }
}
