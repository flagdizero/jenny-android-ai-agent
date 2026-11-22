package com.flagdizero.jenny.wear

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.SystemClock
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService
import android.speech.RecognitionService
import android.speech.SpeechRecognizer
import android.util.Log

// Jenny come assistente dell'orologio: la pressione lunga del tasto la apre al
// posto di Gemini, già in ascolto.
//
// Il ruolo non si chiede con un popup: lo si sceglie nelle impostazioni
// dell'orologio, o con `adb shell cmd role add-role-holder
// android.app.role.ASSISTANT com.flagdizero.jenny`. Per tenerlo servono due
// cose, e mancandone una non funziona:
//
// 1. un'activity esportata con `ACTION_ASSIST` ([AskActivity]): su un
//    dispositivo con poca RAM è l'unica cosa che rende un'app idonea al ruolo;
// 2. un servizio vocale completo, con il servizio di sessione e quello di
//    riconoscimento: è quello che il sistema apre davvero alla pressione lunga.

private const val TAG = "JennyWear"

/**
 * Il servizio che il sistema tiene vivo finché Jenny ha il ruolo. Da solo non
 * mostra niente: alla pressione lunga il sistema chiede una sessione a
 * [AssistSessionService].
 *
 * Niente parola d'attivazione: il rilevatore di sistema è riservato alle app di
 * sistema, e un'app normale non compila nemmeno contro quelle classi.
 */
class AssistService : VoiceInteractionService() {
    override fun onReady() {
        super.onReady()
        Log.i(TAG, "Assistant role held: voice interaction service ready")
    }

    override fun onLaunchVoiceAssistFromKeyguard() {
        showSession(Bundle(), 0)
    }
}

class AssistSessionService : VoiceInteractionSessionService() {
    override fun onNewSession(args: Bundle?): VoiceInteractionSession = AssistSession(this)
}

/**
 * La sessione non disegna niente di suo: apre subito [AskActivity] in ascolto e
 * si chiude, così la pressione lunga e il tocco su Jenny portano alla stessa
 * schermata, con lo stesso codice.
 */
private class AssistSession(context: Context) : VoiceInteractionSession(context) {
    private var lastShow = 0L

    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        // `onShow` può arrivare due volte per una pressione sola, a un paio di
        // decimi di distanza; il doppione arriva dopo `hide()` e riaprirebbe la
        // sessione sopra l'app appena lanciata: va richiusa, non ignorata.
        val now = SystemClock.elapsedRealtime()
        val duplicate = now - lastShow < 1000
        lastShow = now
        if (!duplicate) {
            Log.i(TAG, "Assistant invoked: opening the question screen")
            try {
                startAssistantActivity(
                    Intent(context, AskActivity::class.java).putExtra(AskActivity.EXTRA_LISTEN, true)
                )
            } catch (e: RuntimeException) {
                Log.w(TAG, "Could not open the question screen from the session: ${e.javaClass.simpleName}")
            }
        }
        hide()
    }
}

/**
 * Esiste solo perché il sistema non accetta un servizio vocale senza. Non
 * riconosce niente e lo dice subito: a trascrivere resta il riconoscitore
 * dell'orologio, e [AskActivity] questo lo scarta quando ne cerca uno.
 */
class NoRecognitionService : RecognitionService() {
    override fun onStartListening(recognizerIntent: Intent?, listener: Callback) {
        try {
            listener.error(SpeechRecognizer.ERROR_CLIENT)
        } catch (_: android.os.RemoteException) {
        }
    }

    override fun onCancel(listener: Callback?) {}
    override fun onStopListening(listener: Callback?) {}
}
