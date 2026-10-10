package com.flagdizero.jenny

/**
 * In quale conversazione porta il tocco su un alert: la chat personale o un
 * quaderno.
 *
 * La risposta sta **nel tag** dell'alert, che Python scrive come
 * `project:<nome>|<tag>` per un messaggio consegnato nella chat di un quaderno
 * (`jenny/runtime/notifier.py::alert_fields`) e lascia com'è per la personale.
 * Una fonte sola: chi ripubblica, converte in conversazione o rinfresca un alert
 * riparte dal tag, e la destinazione lo segue senza un dato in più da salvare.
 *
 * Funzioni pure e fuori da [NotifierBridge] apposta: si provano sulla JVM
 * (`AlertConversationTest`), senza un `NotificationManager`.
 */
internal object AlertConversation {
    /** Valore di protocollo, gemello di `CONVERSATION_TAG_SEPARATOR` in Python. */
    const val SEPARATOR = "|"
    private const val PROJECT_PREFIX = "project:"

    /** `project:<nome>` se *tag* è di un quaderno, `null` per la chat personale. */
    fun ofTag(tag: String?): String? {
        if (tag == null || !tag.startsWith(PROJECT_PREFIX)) return null
        val end = tag.indexOf(SEPARATOR)
        if (end <= PROJECT_PREFIX.length) return null
        return tag.substring(0, end)
    }

    /**
     * Il JS che chiede alla SPA di aprire la chat di *conversation* (`null`: la
     * personale), e risponde `true` se l'ha presa in carico.
     *
     * Come lo fa lo decide la SPA: la casa apre la personale sopra la pagina in
     * cui eri, senza togliertela, e per l'alert di un quaderno entra nel
     * quaderno con la sua chat sopra; l'officina torna alla casa e risponde
     * `false`. `false` anche se la SPA non c'è o non ha potuto aprirla (una
     * domanda aperta): allora gli alert NON vengono cancellati. Comporlo qui
     * da goHome + switchMode lasciava la entry di radice a descrivere la vista
     * home mentre a schermo c'era la chat.
     *
     * Il nome finisce dentro `evaluateJavascript`, quindi si scrive come
     * letterale JS con ogni carattere che non sia una lettera o una cifra in
     * `\uXXXX`: niente apici, barre o `</script>` che possano chiudere la
     * stringa. Che il nome sia un quaderno vero lo decide la SPA
     * (`isOpenableProjectName`), non questo lato.
     */
    fun openChatJs(conversation: String?): String {
        val arg = conversation?.let(::jsString) ?: "null"
        return """
            (function () {
              var app = window.mobileApp;
              if (!app || typeof app.openChat !== 'function') return false;
              return app.openChat($arg) !== false;
            })()
        """.trimIndent()
    }

    fun jsString(value: String): String = buildString {
        append('"')
        for (c in value) {
            if (c in 'a'..'z' || c in 'A'..'Z' || c in '0'..'9') append(c)
            else append("\\u%04x".format(c.code))
        }
        append('"')
    }
}
