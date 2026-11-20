package com.flagdizero.jenny

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Riceve il tocco su un pulsante delle notifiche dell'altra Jenny — «Consenti»
 * o «No» su una conferma, «Annulla» su una richiesta fatta (v.
 * `NotifierBridge.postPeerConfirm`/`postPeerDone`) — e lo passa al gateway.
 *
 * Non esportato nel manifest e senza intent-filter, come `ReplyReceiver`: un
 * broadcast fabbricato da un'altra app non arriva. Lo raggiunge il nostro
 * `PendingIntent`, che è **immutabile** — azione e identificativo li abbiamo
 * scritti noi e nessuno li cambia —, ma chi lo tiene in mano può comunque
 * **mandarlo**: un'app con l'accesso alle notifiche vede le azioni e può
 * premere «Consenti» al posto dell'utente, e il suo `send()` non passa dallo
 * sblocco che `setAuthenticationRequired` chiede al dito. Il confine vero è
 * quindi l'accesso alle notifiche, che l'utente concede app per app;
 * `exported="false"` chiude solo il resto.
 *
 * **Qui dentro non si attraversa Chaquopy**, per la ragione scritta nel KDoc di
 * `ReplyReceiver`: il tocco si impacchetta in un intent per `GatewayService`,
 * che lo consegna da un foreground service, con lo stesso lock di handoff e la
 * stessa attesa che Python sia su.
 */
class PeerActionReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val appContext = context.applicationContext
        val action = intent.getStringExtra(EXTRA_ACTION)
        val ident = intent.getStringExtra(EXTRA_IDENT)
        val kind = kindOf(action)
        if (kind == null || ident == null || !isValidIdent(ident)) {
            Log.w(TAG, "Peer action dropped: malformed extras")
            return
        }
        // I pulsanti d'azione non si auto-cancellano: senza, «Consenti» resta in
        // tendina dopo essere stato premuto, e si può premere di nuovo.
        NotifierBridge.cancelPeerNotice(appContext, kind, ident)

        // Il lock corto di handoff, come in `ReplyReceiver`: lo rilascia
        // `GatewayService` a consegna tentata, in ogni esito.
        PowerBridge.acquireHandoffLock(appContext)
        try {
            val service = Intent(appContext, GatewayService::class.java)
                .putExtra(GatewayService.EXTRA_PEER_ACTION, action)
                .putExtra(GatewayService.EXTRA_PEER_IDENT, ident)
            appContext.startForegroundService(service)
            Log.i(TAG, "Peer action handed to the gateway service (action=$action)")
        } catch (e: Exception) {
            // Il service non partirà, quindi nessuno rilascerà il lock.
            PowerBridge.releaseHandoffLock()
            // Il tocco è perso: un «Consenti» perso non fa uscire niente (la
            // conferma resta senza risposta, e scade lato Python); un
            // «Annulla» perso lascia la richiesta fatta, e lo dice solo questa
            // riga.
            Log.e(TAG, "Could not hand the peer action to the gateway service", e)
        }
    }

    companion object {
        // `Log.i` e non `Log.d`, come in `ReplyReceiver`: sul Titan 2 le righe D
        // di Kotlin non compaiono in logcat.
        private const val TAG = "PeerActionReceiver"

        /** Extra con l'azione del pulsante: uno di `ACTION_*`. */
        internal const val EXTRA_ACTION = "com.flagdizero.jenny.peer.ACTION"

        /** Extra con il `cid` (conferma) o il `qid` (richiesta fatta). */
        internal const val EXTRA_IDENT = "com.flagdizero.jenny.peer.IDENT"

        /** Le tre azioni. Valori di protocollo, gemelli di `PEER_ACTIONS` in
         *  `jenny/runtime/native_input.py`: Python rifiuta tutto il resto. */
        internal const val ACTION_ALLOW = "allow"
        internal const val ACTION_DENY = "deny"
        internal const val ACTION_UNDO = "undo"

        /** `cid` e `qid` sono `uuid4().hex`: la stessa forma che Python esige. */
        private val IDENT = Regex("[0-9a-f]{32}")

        internal fun isValidIdent(ident: String): Boolean = IDENT.matches(ident)

        /** Il tipo di notifica da cui viene *action*, o `null` se non è nostra. */
        private fun kindOf(action: String?): String? = when (action) {
            ACTION_ALLOW, ACTION_DENY -> NotifierBridge.PEER_KIND_CONFIRM
            ACTION_UNDO -> NotifierBridge.PEER_KIND_DONE
            else -> null
        }
    }
}
