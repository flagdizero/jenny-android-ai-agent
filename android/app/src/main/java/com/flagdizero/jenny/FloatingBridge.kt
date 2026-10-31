package com.flagdizero.jenny

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import android.util.Log

/**
 * Bridge per la mascotte flottante, esposto a Python via Chaquopy
 * (`jclass("com.flagdizero.jenny.FloatingBridge")`), mai istanziato da Kotlin —
 * stesso pattern di `NotifierBridge`.
 *
 * Due metodi, uno per verso, più `isActive` e `openPermissionSettings` per il
 * pannello impostazioni:
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
 * `CalledFromWrongThreadException`. L'attesa serve a rispondere davvero — se il
 * risultato tornasse ottimisticamente, Python registrerebbe come mostrato un
 * fumetto che non è mai comparso, e un log che mente su questo costa una
 * diagnosi intera.
 *
 * Il tetto sull'attesa è corto di proposito: dall'altra parte c'è un thread di
 * `asyncio.to_thread`, e il turno aspetta la sua risposta. Se il main Looper è
 * occupato più di così si torna `false` e si lascia andare il turno — la
 * risposta è comunque nella conversazione. E `false` resta vero: a tetto
 * scaduto [MainHop] toglie il blocco dalla coda, quindi il fumetto non compare
 * dopo (né la mascotte si accende dopo un «non applicato»). Fino al 26/09/2026
 * il blocco girava lo stesso, in ritardo, e il `false` mentiva nell'altro
 * verso.
 */
class FloatingBridge(context: Context) {

    companion object {
        private const val TAG = "FloatingBridge"

        /** Attesa massima per il giro sul main thread. */
        private const val MAIN_HOP_TIMEOUT_MS = 3_000L

        /** Esegue *block* sul main thread (v. [MainHop]); `false` se non ci
         *  riesce in tempo. */
        private fun onMain(block: () -> Boolean): Boolean =
            MainHop.call(MAIN_HOP_TIMEOUT_MS, false, TAG, block = block)
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

    /**
     * Porta l'utente dove si concede «Mostra sopra altre app»: un'app non può
     * concederselo da sé.
     *
     * Il `package:` chiede la pagina della sola Jenny, ma dal 11 Android lo
     * **ignora** e apre l'elenco di tutte le app, dove lei è una riga. La pagina
     * di una sola app esiste (`android.settings.MANAGE_APP_OVERLAY_PERMISSION`),
     * ma un'app qualunque riceve `SecurityException`: misurato su jenny_s22,
     * Android 16, il 25/10/2026. Il secondo tentativo, senza pacchetto, è per le
     * ROM che rifiutano anche il primo.
     */
    fun openPermissionSettings(): Boolean {
        val tries = listOf(
            Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:${appContext.packageName}"),
            ),
            Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION),
        )
        for (intent in tries) {
            try {
                appContext.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                return true
            } catch (e: Exception) {
                Log.w(TAG, "Cannot open overlay permission settings (${intent.data})", e)
            }
        }
        return false
    }

    /** Disegna la risposta nel fumetto. `false` se non c'è nessuna finestra. */
    fun showReply(text: String): Boolean = onMain {
        FloatingOverlayController.showReply(text)
    }
}
