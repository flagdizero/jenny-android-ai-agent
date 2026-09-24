package com.flagdizero.jenny

import android.content.Context

/**
 * Bridge per la mascotte flottante, esposto a Python via Chaquopy
 * (`jclass("com.flagdizero.jenny.FloatingBridge")`), mai istanziato da Kotlin —
 * stesso pattern di `NotifierBridge`.
 *
 * Due metodi, uno per verso:
 *
 * * `setEnabled` — la volontà, letta da `config.floating.enabled` all'avvio del
 *   gateway (`jenny/runtime/floating.py`);
 * * `showReply` — il testo da aggiungere alla conversazione nella finestra,
 *   chiamato dal canale (`jenny/channels/floating.py`) a ogni risposta. Può
 *   rispondere `false` senza che sia un guasto: a finestra chiusa non si
 *   disegna niente, e non la si riapre.
 *
 * **Il salto sul main thread è qui e non nel controller.** Python entra da un
 * thread di `asyncio.to_thread`, e toccare delle `View` da lì sarebbe un
 * `CalledFromWrongThreadException`. Il latch serve a rispondere davvero — se il
 * risultato tornasse ottimisticamente, Python registrerebbe come mostrato un
 * fumetto che non è mai comparso, e un log che mente su questo costa una
 * diagnosi intera.
 *
 * Il tetto sull'attesa è corto di proposito: dall'altra parte c'è il loop del
 * gateway, fermo su questa chiamata dentro un `wait_for`. Se il main Looper è
 * occupato più di così, la cosa giusta è tornare `false` e lasciar andare il
 * turno — la risposta è comunque nella conversazione.
 */
class FloatingBridge(context: Context) {

    companion object {
        private const val TAG = "FloatingBridge"

        /** Attesa massima per il giro sul main thread. */
        private const val MAIN_HOP_TIMEOUT_MS = 3_000L

        /** Esegue *block* sul main thread (v. [MainHop]); `false` se non ci
         *  riesce in tempo. */
        private fun onMain(block: () -> Boolean): Boolean =
            MainHop.call(MAIN_HOP_TIMEOUT_MS, false, TAG, block)
    }

    private val appContext = context.applicationContext

    /**
     * Accende o spegne la mascotte, e le passa quanto tenere il fumetto.
     *
     * Ritorna `true` se lo stato richiesto è quello effettivo. Un `false` a
     * `on = true` vuol dire quasi sempre una cosa sola: `SYSTEM_ALERT_WINDOW`
     * non è stato concesso. Non è un errore da log rumoroso — è la risposta
     * alla domanda che l'interruttore nelle impostazioni sta facendo.
     */
    fun setEnabled(on: Boolean, replyHoldSeconds: Int): Boolean = onMain {
        FloatingOverlayController.setReplyHoldSeconds(replyHoldSeconds)
        FloatingOverlayController.setEnabled(appContext, on)
    }

    /**
     * La mascotte è accesa e il permesso c'è? Letta dal pannello impostazioni
     * a ogni caricamento, così la riga «Android non la lascia aprire» compare
     * tutte le volte che è vera e non solo appena si tocca l'interruttore.
     */
    fun isActive(): Boolean = onMain { FloatingOverlayController.isActive() }

    /** Disegna la risposta nel fumetto. `false` se non c'è nessuna finestra. */
    fun showReply(text: String): Boolean = onMain {
        FloatingOverlayController.showReply(text)
    }
}
