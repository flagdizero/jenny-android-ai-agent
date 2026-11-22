package com.flagdizero.jenny

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.os.SystemClock
import android.util.Log
import android.view.accessibility.AccessibilityEvent

/**
 * Le mani di Jenny: il servizio di accessibilità che le lascia toccare,
 * scrivere e scorrere nelle altre app. È un servizio a parte da
 * [ScreenService], che resta in sola lettura: chi vuole solo gli occhi non
 * accende questo, e senza questo acceso Jenny non può agire per costruzione.
 *
 * Qui dentro c'è solo il guscio, come in [ScreenService]: la lettura la fa
 * [PhoneTree], le azioni [PhoneActions]. Gli eventi servono a una cosa sola,
 * sapere quando lo schermo ha smesso di cambiare dopo un'azione
 * ([PhoneActions.settle]); il loro contenuto non si guarda.
 */
class HandsService : AccessibilityService() {

    override fun onServiceConnected() {
        instance = this
        // Chi ha acceso le mani non deve accendere anche gli occhi: la lettura
        // al tocco sulla mascotte passa da qui, se l'altro servizio è spento.
        if (!ScreenSnapshots.connected()) ScreenSnapshots.attach(this)
        Log.i(TAG, "Hands service connected")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        lastEventAt = SystemClock.elapsedRealtime()
    }

    override fun onInterrupt() = Unit

    override fun onUnbind(intent: Intent?): Boolean {
        if (instance === this) instance = null
        PhonePill.detach()
        ScreenSnapshots.detach(this)
        PhoneTree.clear()
        Log.i(TAG, "Hands service unbound")
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    companion object {
        private const val TAG = "HandsService"

        /** Il servizio acceso, o `null`. */
        @Volatile
        var instance: HandsService? = null

        /** Quando è arrivato l'ultimo evento, in `elapsedRealtime`. */
        @Volatile
        var lastEventAt = 0L
    }
}
