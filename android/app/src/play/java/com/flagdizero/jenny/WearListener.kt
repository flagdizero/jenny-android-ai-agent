package com.flagdizero.jenny

import android.util.Log
import com.flagdizero.jenny.wearprotocol.WearProtocol
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.WearableListenerService

/**
 * Riceve dal Data Layer le domande dette al polso e le porta al gateway.
 *
 * Solo nel flavor `play` (il Data Layer è codice Google). La consegna vera è in
 * `GatewayService.deliverWearText`, che non sa niente di Google: qui si legge
 * il messaggio, si controlla la versione e si risponde all'orologio con la
 * conferma ([WearProtocol.PATH_ACK]) — sì appena il gateway ha accettato, no
 * con il motivo. La risposta di Jenny arriva dopo, da [WearBridge].
 *
 * L'indirizzo di ritorno è `<nodo>/<id>`: il nodo dice a quale orologio, l'id
 * a quale domanda. Il gateway lo porta per tutto il turno come *thread* e lo
 * restituisce al [WearBridge] senza leggerlo.
 */
class WearListener : WearableListenerService() {

    override fun onMessageReceived(event: MessageEvent) {
        if (event.path != WearProtocol.PATH_ASK) return
        val node = event.sourceNodeId
        val ask = WearProtocol.decodeAsk(event.data)
        if (ask == null) {
            // Senza id non c'è a chi dire di no: resta il log.
            Log.w(TAG, "Unreadable question from the watch (${event.data.size} bytes)")
            return
        }
        val error = when {
            ask.version != WearProtocol.VERSION -> WearProtocol.ERR_VERSION
            ask.text.isBlank() -> WearProtocol.ERR_MALFORMED
            ask.text.length > WearProtocol.MAX_TEXT_CHARS -> WearProtocol.ERR_TOO_LONG
            else -> null
        }
        if (error != null) {
            Log.w(TAG, "Question from the watch refused: $error (v=${ask.version})")
            ack(node, ask.id, false, error)
            return
        }
        Log.i(TAG, "Question from the watch (chars=${ask.text.length})")
        GatewayService.deliverWearText(this, ask.text.trim(), "$node/${ask.id}") { ok ->
            ack(node, ask.id, ok, if (ok) null else WearProtocol.ERR_UNREACHABLE)
        }
    }

    private fun ack(node: String, id: String, ok: Boolean, error: String?) {
        WearBridge.send(this, node, WearProtocol.PATH_ACK, WearProtocol.encodeAck(id, ok, error))
    }

    companion object {
        private const val TAG = "WearListener"
    }
}
