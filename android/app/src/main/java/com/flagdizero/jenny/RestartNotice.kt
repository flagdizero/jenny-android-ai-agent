package com.flagdizero.jenny

import android.app.ForegroundServiceStartNotAllowedException
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat

/**
 * «Jenny è ferma — tocca per riavviarla»: la notifica che chiede all'utente il
 * gesto che nessuna rete di sicurezza può fare al posto suo.
 *
 * Da Android 12 un foreground service non parte da background, salvo
 * un'allowlist temporanea che lo permetta. Senza `SCHEDULE_EXACT_ALARM` e
 * senza esenzione dall'ottimizzazione batteria nessuna delle reti la ottiene:
 * una sveglia inesatta dà `mOptsWithoutFgs`, un job espresso di WorkManager
 * nessuna (v. `GatewayStarter.ALARM_FALLBACK_DELAY_MS`). Prima di questa
 * notifica il gateway morto restava morto finché l'utente non apriva l'app, e
 * niente gli diceva che doveva farlo.
 *
 * **Perché un tocco basta.** `NotificationManagerService`, quando la notifica
 * viene postata, registra ogni suo `PendingIntent` con
 * `TEMPORARY_ALLOWLIST_TYPE_FOREGROUND_SERVICE_ALLOWED`
 * (`REASON_NOTIFICATION_SERVICE`), e `PendingIntentRecord.sendInner` applica
 * quell'allowlist all'uid dell'app *prima* di `startServiceInPackage` (AOSP,
 * `android14-release` e `main`). È l'esenzione che la documentazione chiama
 * «l'utente agisce su un elemento dell'interfaccia legato all'app», e la
 * stessa su cui già conta `ReplyReceiver` per la risposta dalla tendina.
 *
 * **Perché il tocco avvia il service e non apre l'app.** Jenny è anche il
 * launcher: aprirla per riavviarla porterebbe l'utente via da ciò che stava
 * facendo, per un gesto che a lui non chiede altro. Un
 * `PendingIntent.getForegroundService` fa partire il gateway lì dove si è; chi
 * vuole la chat apre Jenny, e anche quello basta a togliere la notifica.
 *
 * **Una sola, e se ne va da sé.** Id fisso, quindi un secondo post la
 * sostituisce; `setOnlyAlertOnce` perché le reti riprovano ogni pochi minuti e
 * ogni tentativo fallito la ripubblica. Non si cancella al tocco
 * (`setAutoCancel(false)`): se il tocco fallisse resterebbe senza più niente
 * che lo dica. La toglie `GatewayService.onStartCommand` quando il service è
 * davvero in foreground, qualunque strada ce l'abbia portato — il tocco, l'app
 * aperta, il boot.
 */
object RestartNotice {

    private const val TAG = "RestartNotice"

    /** Canale suo, non quello del service né quello degli avvisi: chi silenzia
     *  i promemoria di Jenny non deve perdersi il fatto che Jenny non c'è. */
    private const val CHANNEL_ID = "jenny_restart"

    /** 1 è la notifica del service, 2 gli avvisi (con tag), 3 l'aggiornamento
     *  dell'APK. Senza tag: ce n'è una sola. */
    private const val NOTICE_ID = 4

    /** Request code del tocco. Distinto da `GatewayService.OPEN_UI_REQUEST_CODE`
     *  (1001) per la stessa ragione scritta là: due `PendingIntent` con la
     *  stessa identità si sovrascrivono sotto `FLAG_UPDATE_CURRENT`. */
    private const val TAP_REQUEST_CODE = 1002

    /** Extra con cui il tocco si fa riconoscere in `onStartCommand`: serve solo
     *  al log, per distinguere in logcat il riavvio chiesto dall'utente. */
    const val EXTRA_FROM_NOTICE = "com.flagdizero.jenny.extra.FROM_RESTART_NOTICE"

    /**
     * Mostra la notifica se *e* è il rifiuto di un avvio da background. Ritorna
     * `true` se l'ha postata.
     *
     * Solo quel rifiuto: il tocco ripara esattamente lui (concede l'avvio), e
     * dire «tocca per riavviarla» davanti a un guasto diverso sarebbe una
     * promessa che il tocco non mantiene. La classe esiste da API 31, e il
     * controllo di versione la precede; sotto non c'è niente da rifiutare.
     *
     * Non la mostra se il service è vivo in questo processo con il gateway
     * dietro: lì Jenny non è ferma, e la notifica resterebbe a dirlo senza che
     * nessun avvio la tolga.
     */
    fun showIfRefused(context: Context, e: Exception, reason: String): Boolean {
        val refused = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
            e is ForegroundServiceStartNotAllowedException
        if (!refused) return false
        if (GatewayService.isRunning && !GatewayService.isGatewayThreadDead) {
            Log.i(TAG, "FGS start refused but the service is alive: no notice (reason=$reason)")
            return false
        }
        return post(context.applicationContext, reason)
    }

    /**
     * Toglie la notifica, se c'è. La chiama il service quando è in foreground.
     *
     * Cancella sempre, anche quando non la vede fra quelle attive: costa una
     * chiamata al sistema e chiude la gara con un post appena partito. Il log
     * invece solo se c'era, perché `onStartCommand` gira a ogni tick.
     */
    fun clear(context: Context) {
        try {
            val manager = context.getSystemService(NotificationManager::class.java) ?: return
            val shown = manager.activeNotifications.any { it.id == NOTICE_ID && it.tag == null }
            manager.cancel(NOTICE_ID)
            if (shown) {
                Log.i(TAG, "Restart notice cleared: the gateway service is in foreground")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Could not clear the restart notice", e)
        }
    }

    /**
     * Posta la notifica, o dice nel log perché non può.
     *
     * Senza `POST_NOTIFICATIONS` (API 33+) `notify` non solleva: la scarta in
     * silenzio. Per questo il permesso si guarda prima, e così le notifiche
     * spente o il canale bloccato: in tutti e tre i casi resta una riga `W`,
     * che è l'unica traccia che il gateway non si è potuto rialzare. Ogni altro
     * errore finisce nel `catch`: chi chiama sta già gestendo un guasto e non
     * deve riceverne un secondo.
     */
    private fun post(appContext: Context, reason: String): Boolean {
        if (!JennyChannels.canPostNotifications(appContext)) {
            Log.w(
                TAG,
                "Gateway cannot restart from the background and POST_NOTIFICATIONS is denied: " +
                    "no restart notice (reason=$reason)",
            )
            return false
        }
        return try {
            val manager = ensureChannel(appContext) ?: return false
            val blocked = !manager.areNotificationsEnabled() ||
                manager.getNotificationChannel(CHANNEL_ID)?.importance ==
                NotificationManager.IMPORTANCE_NONE
            if (blocked) {
                Log.w(
                    TAG,
                    "Gateway cannot restart from the background and its notifications are " +
                        "turned off: no restart notice (reason=$reason)",
                )
                return false
            }
            val notification = NotificationCompat.Builder(appContext, CHANNEL_ID)
                .setContentTitle(appContext.getString(R.string.restart_notice_title))
                .setContentText(appContext.getString(R.string.restart_notice_text))
                .setSmallIcon(R.drawable.ic_stat_jenny)
                .setContentIntent(tapIntent(appContext))
                .setAutoCancel(false)
                .setOnlyAlertOnce(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setCategory(NotificationCompat.CATEGORY_ERROR)
                .build()
            manager.notify(NOTICE_ID, notification)
            Log.w(TAG, "Gateway cannot restart from the background: restart notice posted (reason=$reason)")
            true
        } catch (e: Exception) {
            Log.e(TAG, "Could not post the restart notice (reason=$reason)", e)
            false
        }
    }

    /**
     * Il canale, creato a ogni post: è idempotente, e questa notifica nasce
     * quando il service non c'è, cioè quando nessun altro l'ha creato.
     *
     * `IMPORTANCE_LOW`: in tendina e in barra di stato, senza suono. Non è un
     * allarme — chi l'ha davanti non perde niente nei prossimi secondi — ma
     * deve esserci quando l'utente guarda, perché è l'unico che può agire.
     */
    private fun ensureChannel(context: Context): NotificationManager? {
        return JennyChannels.ensure(
            context, CHANNEL_ID, R.string.restart_channel_name,
            R.string.restart_channel_description, NotificationManager.IMPORTANCE_LOW, silent = true,
        )
    }

    /**
     * Il tocco: un avvio del service, immutabile ed esplicito.
     *
     * Immutabile perché niente deve poterci scrivere dentro — è l'opposto del
     * `PendingIntent` della risposta rapida, che deve esserlo per ricevere il
     * testo. Esplicito perché nomina il componente: non c'è risoluzione da
     * dirottare. `getForegroundService` e non `getService`: il sistema deve
     * chiamare `startForegroundService`, altrimenti l'avvio sarebbe di un
     * service normale e rifiutato da background come prima.
     */
    private fun tapIntent(context: Context): PendingIntent {
        val intent = Intent(context, GatewayService::class.java)
            .putExtra(EXTRA_FROM_NOTICE, true)
        return PendingIntent.getForegroundService(
            context,
            TAP_REQUEST_CODE,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }
}
