package com.flagdizero.jenny

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.util.Log
import android.view.accessibility.AccessibilityEvent

/**
 * Gli occhi di Jenny: il servizio di accessibilità che le lascia leggere lo
 * schermo **quando la chiami dalla mascotte flottante**, e mai da sola.
 *
 * Qui dentro non c'è niente, ed è il punto. Il servizio esiste perché Android
 * dà la lettura delle finestre delle altre app solo a un `AccessibilityService`
 * acceso dall'utente; la lettura vera la fa [ScreenSnapshots], che il servizio
 * si limita a registrare quando nasce e a staccare quando muore.
 *
 * **Sola lettura, per costruzione e non per promessa.** La configurazione
 * (`res/xml/screen_service.xml`) non chiede `canPerformGestures` e il codice non
 * chiama mai `performAction`: il servizio non può toccare, scrivere o premere
 * niente. Non ascolta nemmeno: nessun tipo di evento, quindi niente scorre di
 * qua mentre l'utente usa il telefono. Legge una volta, al tocco sulla
 * mascotte, e basta.
 */
class ScreenService : AccessibilityService() {

    override fun onServiceConnected() {
        ScreenSnapshots.attach(this)
        Log.i(TAG, "Screen service connected")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit

    override fun onInterrupt() = Unit

    override fun onUnbind(intent: Intent?): Boolean {
        ScreenSnapshots.detach(this)
        Log.i(TAG, "Screen service unbound")
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        ScreenSnapshots.detach(this)
        super.onDestroy()
    }

    private companion object {
        const val TAG = "ScreenService"
    }
}
