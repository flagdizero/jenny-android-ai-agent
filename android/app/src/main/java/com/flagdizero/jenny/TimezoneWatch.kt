package com.flagdizero.jenny

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.util.Log
import androidx.core.content.ContextCompat
import com.chaquo.python.Python
import kotlin.concurrent.thread

/**
 * Il telefono cambia fuso → il gateway lo segue.
 *
 * Python leggeva il fuso una volta sola, all'avvio, e il gateway resta su per
 * giorni: atterrati in un altro paese, un promemoria «ogni giorno alle 7»
 * suonava ancora alle 7 di casa. Android manda `ACTION_TIMEZONE_CHANGED` a ogni
 * cambio (automatico o a mano); qui la si gira a
 * `jenny.runtime.timezone_follow.on_timezone_changed`, che sposta i promemoria.
 *
 * Registrato a runtime dall'`Application` e non nel manifest: serve solo mentre
 * il processo è vivo. A processo morto non c'è niente da avvisare — il prossimo
 * avvio rilegge il fuso, e lo scheduler lo confronta con quello in cui i
 * promemoria sono scritti. Dall'`Application` e non dal `GatewayService`,
 * perché il gateway Python vive nel processo e può sopravvivere al service.
 */
object TimezoneWatch {
    private const val TAG = "TimezoneWatch"
    private var registered = false

    @Synchronized
    fun register(context: Context) {
        if (registered) return
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                // Il fuso lo porta l'intent: `TimeZone.getDefault()` in questo
                // processo viene aggiornato da un messaggio a parte, che può
                // arrivare dopo questa consegna.
                deliver(intent.getStringExtra(Intent.EXTRA_TIMEZONE))
            }
        }
        try {
            // Broadcast protetta di sistema: arriva anche a un receiver non
            // esportato, e nessun'altra app può fingerla.
            ContextCompat.registerReceiver(
                context.applicationContext,
                receiver,
                IntentFilter(Intent.ACTION_TIMEZONE_CHANGED),
                ContextCompat.RECEIVER_NOT_EXPORTED,
            )
            registered = true
        } catch (e: Exception) {
            // Senza, il fuso si riallinea al prossimo avvio del gateway.
            Log.e(TAG, "Could not register the timezone receiver", e)
        }
    }

    /**
     * Su un thread di lavoro e mai sul main Looper, come
     * `GatewayService.deliverWakeTick`: la chiamata attraversa il confine
     * Chaquopy e può aspettare il GIL quanto dura un turno.
     */
    private fun deliver(timezoneId: String?) {
        thread(name = "jenny-timezone") {
            try {
                if (!Python.isStarted()) {
                    Log.i(TAG, "Timezone change dropped: python runtime not started yet")
                    return@thread
                }
                val module = Python.getInstance().getModule("jenny.runtime.timezone_follow")
                val delivered = module.callAttr("on_timezone_changed", timezoneId).toBoolean()
                Log.i(TAG, "Timezone change delivered=$delivered (zone=$timezoneId)")
            } catch (e: Exception) {
                Log.e(TAG, "Timezone change delivery failed", e)
            }
        }
    }
}
