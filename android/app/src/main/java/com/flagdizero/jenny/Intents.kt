package com.flagdizero.jenny

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import android.util.Log

/**
 * Le aperture di un'altra schermata (un'app, una pagina delle impostazioni di
 * Android) che la UI chiede e che non devono far cadere chi le chiede.
 *
 * Erano otto copie dello stesso `try { startActivity } catch { log; false }`,
 * e due della scheda «Info app» con flag diversi: quella di `MainActivity`
 * aveva il solo `NEW_TASK`, che [appDetails] spiega perché non basta.
 */
object Intents {

    /** `startActivity` che non solleva: vero se è partita, falso — con una riga
     *  nel log sotto [tag] — se Android l'ha rifiutata o non sa a chi darla. */
    fun tryStart(ctx: Context, intent: Intent, tag: String, what: String): Boolean = try {
        ctx.startActivity(intent)
        true
    } catch (e: Exception) {
        Log.w(tag, "Cannot open $what", e)
        false
    }

    /**
     * La scheda «Info app» di [packageName], in un task suo. Con il solo
     * NEW_TASK l'affinità la faceva entrare nel task di Impostazioni già
     * aperto, e Indietro tornava alla schermata lasciata lì (sul Titan 2,
     * «Rete e Internet») invece che a Jenny. NEW_DOCUMENT le dà un task per
     * pacchetto: Indietro lo chiude e torna a chi l'ha aperta, e riaprirla non
     * ne accumula altri.
     */
    fun appDetails(packageName: String): Intent =
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NEW_DOCUMENT)
}
