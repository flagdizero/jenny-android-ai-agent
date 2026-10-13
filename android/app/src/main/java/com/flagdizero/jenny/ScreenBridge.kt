package com.flagdizero.jenny

import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.util.Log

/**
 * Bridge della lettura dello schermo, esposto a Python via Chaquopy
 * (`jclass("com.flagdizero.jenny.ScreenBridge")`) — stesso pattern di
 * `FloatingBridge`, mai istanziato da Kotlin.
 *
 * Ogni metodo che restituisce o scrive qualcosa vuole l'**id** della lettura,
 * quello che è arrivato con il testo del fumetto: se nel frattempo un altro
 * tocco ha preso una lettura nuova, o la vecchia è scaduta, si risponde `null`
 * / `false` invece di dare a un turno lo schermo di un altro momento.
 *
 * Niente salto sul main thread: [ScreenSnapshots] è thread-safe e non tocca
 * nessuna `View`.
 */
class ScreenBridge(context: Context) {

    private val appContext = context.applicationContext

    /** `"on"`, `"off"` (servizio spento in Accessibilità) o `"unsupported"`
     *  (Android sotto il 14). */
    fun state(): String = when {
        !ScreenSnapshots.supported() -> "unsupported"
        ScreenSnapshots.connected() -> "on"
        else -> "off"
    }

    /** Testo, indirizzo e immagini della lettura *id*, in JSON; `null` se non
     *  è più quella corrente. */
    fun read(id: String): String? = ScreenSnapshots.get(id)?.contentJson()

    fun writeScreenshot(id: String, path: String): Boolean =
        ScreenSnapshots.writeScreenshot(id, path)

    fun writeImage(id: String, n: Int, path: String): Boolean =
        ScreenSnapshots.writeImage(id, n, path)

    /** Apre la pagina di sistema in cui il servizio si accende. */
    fun openSettings(): Boolean = try {
        appContext.startActivity(
            Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
        true
    } catch (e: Exception) {
        Log.w("ScreenBridge", "Cannot open accessibility settings", e)
        false
    }
}
