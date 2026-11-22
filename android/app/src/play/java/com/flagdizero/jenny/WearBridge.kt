package com.flagdizero.jenny

import android.content.Context
import android.util.Log
import com.flagdizero.jenny.wearprotocol.WearProtocol
import com.google.android.gms.tasks.Tasks
import com.google.android.gms.wearable.Wearable
import java.util.concurrent.TimeUnit

/**
 * La strada di ritorno verso l'orologio: Python la chiama per nome via Chaquopy
 * (`jenny/runtime/wear.py`), come `FloatingBridge` per il fumetto.
 *
 * Solo nel flavor `play`, perché il Data Layer è codice Google. Nella `foss`
 * la classe non esiste, Python non la risolve e la risposta resta in chat:
 * è giusto così, perché in quella build nessuna domanda è arrivata dal polso.
 *
 * L'indirizzo è quello con cui la domanda è entrata (`<nodo>/<id>`, lo compone
 * [WearListener]): Python non lo legge, lo riporta e basta.
 */
class WearBridge(context: Context) {
    private val app = context.applicationContext

    /** Manda la risposta all'orologio. `false` se non è partita (orologio fuori
     *  portata, indirizzo rotto). Bloccante: Python la chiama da un thread. */
    fun sendReply(address: String, text: String): Boolean {
        val slash = address.indexOf('/')
        if (slash <= 0 || slash == address.length - 1) {
            Log.w(TAG, "Reply dropped: malformed watch address")
            return false
        }
        val node = address.substring(0, slash)
        val id = address.substring(slash + 1)
        return send(app, node, WearProtocol.PATH_REPLY, WearProtocol.encodeReply(id, text))
    }

    companion object {
        private const val TAG = "WearBridge"

        /** Sotto i 10 s di `_BRIDGE_TIMEOUT_S` in `jenny/runtime/wear.py`: chi
         *  scade per primo dev'essere il Data Layer, non Chaquopy. */
        private const val SEND_TIMEOUT_MS = 8_000L

        /** Un messaggio a un nodo, aspettando l'esito. Mai solleva. */
        fun send(context: Context, node: String, path: String, data: ByteArray): Boolean = try {
            Tasks.await(
                Wearable.getMessageClient(context.applicationContext).sendMessage(node, path, data),
                SEND_TIMEOUT_MS,
                TimeUnit.MILLISECONDS,
            )
            Log.i(TAG, "Sent $path to the watch (${data.size} bytes)")
            true
        } catch (e: Exception) {
            Log.w(TAG, "Could not send $path to the watch: ${e.javaClass.simpleName}")
            false
        }
    }
}
