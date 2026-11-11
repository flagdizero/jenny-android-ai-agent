package com.flagdizero.jenny

import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Il punto da cui parte `startForegroundService(GatewayService)`: le reti di
 * sicurezza, il boot, il watchdog, le sveglie e l'activity passano tutte da qui.
 * Due eccezioni. `ReplyReceiver`, che consegna una risposta dalla notifica:
 * extra suoi, lock di handoff senza `EXTRA_WAKE_TICK`, e un fallimento che deve
 * tornare all'utente (`postReplyFailure`) invece di finire in un log. E il
 * tocco su [RestartNotice], che è un `PendingIntent` e lo avvia il sistema: è
 * la strada che resta quando da qui l'avvio viene rifiutato.
 *
 * Perché centralizzarlo: le reti di sicurezza anti-doze sono ormai SEI, e sono
 * indipendenti per costruzione — sticky restart, sveglia di `onDestroy`,
 * watchdog, sveglia-sveglia (`AlarmClockFallback`), worker periodico di
 * WorkManager, trigger opportunistici di rete/foreground. Ognuna può scattare
 * mentre le altre stanno già lavorando, ed è esattamente quello che vogliamo:
 * il costo di un tentativo in più è nullo, il costo di un buco è un agente giù
 * per ore. Ma "il costo è nullo" vale solo finché il tentativo passa da qui.
 *
 * Con sei copie di `startForegroundService` sparse nel codice ognuna avrebbe la
 * sua idea su: prendere o no il wakelock di handoff, cosa fare quando Android
 * 12+ rifiuta l'avvio di un FGS da background, se controllare prima la
 * liveness. Sei idee diverse su un percorso che gira solo quando qualcosa è già
 * andato storto — cioè nell'unico momento in cui non si può testare a mano.
 *
 * La domanda "due avvii concorrenti possono far partire DUE gateway Python?" ha
 * risposta in `GatewayService.startGateway`, non qui: questo oggetto si limita
 * a mandare intent, ed è il service a serializzarli.
 */
object GatewayStarter {

    private const val TAG = "GatewayStarter"

    /**
     * Ritardo della sveglia di ripiego quando l'avvio del FGS viene rifiutato.
     *
     * Da Android 12 avviare un foreground service da background lancia
     * `ForegroundServiceStartNotAllowedException`, a meno che l'app non sia in
     * una allowlist temporanea **che permetta un FGS**. Una sveglia esatta
     * (`setExactAndAllowWhileIdle`, `setAlarmClock`) quella allowlist la
     * concede per la durata della sua callback: rientrare da lì è l'unico modo
     * affidabile di riprovare. Corto di proposito — la sveglia serve a cambiare
     * *contesto*, non a rimandare il recupero.
     *
     * **Solo esatta.** Senza `SCHEDULE_EXACT_ALARM` [PowerBridge.scheduleWake]
     * ripiega su `setAndAllowWhileIdle`, e quella l'allowlist la concede *senza*
     * il FGS: `AlarmManagerService.setImpl` le dà `mOptsWithoutFgs`, cioè
     * `TEMPORARY_ALLOWLIST_TYPE_FOREGROUND_SERVICE_NOT_ALLOWED` (AOSP,
     * `android14-release` e `main`; il javadoc di `AlarmManager` promette
     * l'avvio del FGS solo per `setExactAndAllowWhileIdle` e `setAlarmClock`,
     * e `setAlarmClock` è esatta anche lei, stesso permesso). Nemmeno un job
     * espresso di WorkManager basta: `JobServiceContext` lo lega con
     * `BIND_ALMOST_PERCEPTIBLE`, senza allowlist per il FGS. Quindi senza
     * sveglie esatte questa sveglia non si arma: rientrerebbe in un contesto
     * che rifiuta l'avvio esattamente come questo. Restano i trigger che
     * l'avvio lo concedono per conto loro — boot, aggiornamento dell'APK,
     * l'app in primo piano, un tocco su una notifica — e l'app esente
     * dall'ottimizzazione batteria, per cui l'avvio non viene rifiutato affatto.
     * Il tocco lo si chiede: senza sveglia armata `ensureUp` posta
     * [RestartNotice], «Jenny è ferma — tocca per riavviarla».
     */
    private const val ALARM_FALLBACK_DELAY_MS = 10_000L

    /**
     * Manda su il gateway senza chiedersi se ci sia già.
     *
     * `startForegroundService` su un service vivo non crea una seconda
     * istanza: passa solo da `onStartCommand`. Chiamarlo a vuoto è quindi un
     * no-op, ed è il motivo per cui tutte le reti possono permettersi di essere
     * ottimiste.
     *
     * `wakeTick = true` significa "questa non è una verifica, è una scadenza da
     * onorare adesso": si prende PRIMA il wakelock corto di handoff, perché fra
     * l'uscita da `onReceive` e `onStartCommand` il device può risospendere e
     * il tick arriverebbe minuti dopo — cioè proprio il ritardo che la sveglia
     * doveva eliminare. A rilasciarlo è `GatewayService` a consegna avvenuta.
     *
     * `alarmFallback = true` va passato dai chiamanti che NON stanno già girando
     * dentro una finestra di allowlist: worker di WorkManager, callback di rete,
     * foreground dell'app. Chi arriva da una sveglia la allowlist ce l'ha già
     * (con il FGS solo se la sveglia era esatta, v. [ALARM_FALLBACK_DELAY_MS]),
     * e riarmarne un'altra sullo stesso request code non farebbe che spostare
     * in avanti la rete di sicurezza del service. Senza sveglie esatte il
     * ripiego non si arma affatto: non concederebbe niente.
     *
     * Ogni rifiuto dell'avvio che non lascia armata una sveglia esatta — con o
     * senza `alarmFallback` — posta [RestartNotice]: è l'unico modo rimasto di
     * far sapere all'utente che serve un suo tocco.
     */
    fun ensureUp(
        context: Context,
        reason: String,
        wakeTick: Boolean = false,
        alarmFallback: Boolean = false,
        wakeCode: Int = 0,
    ): Boolean {
        val appContext = context.applicationContext
        Log.i(TAG, "Ensuring gateway is up (reason=$reason, wakeTick=$wakeTick)")
        if (wakeTick) {
            PowerBridge.acquireHandoffLock(appContext)
        }
        return try {
            val intent = Intent(appContext, GatewayService::class.java)
            if (wakeTick) {
                intent.putExtra(GatewayService.EXTRA_WAKE_TICK, true)
                intent.putExtra(GatewayService.EXTRA_WAKE_CODE, wakeCode)
            }
            appContext.startForegroundService(intent)
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start gateway (reason=$reason)", e)
            // Il service non partirà, quindi nessuno rilascerà il lock: farlo
            // qui evita di lasciare la CPU accesa fino allo scadere del timeout.
            if (wakeTick) {
                PowerBridge.releaseHandoffLock()
            }
            var recoveryArmed = false
            if (alarmFallback) {
                if (PowerBridge.canScheduleExactAlarms(appContext)) {
                    recoveryArmed = PowerBridge.scheduleWake(
                        appContext,
                        System.currentTimeMillis() + ALARM_FALLBACK_DELAY_MS,
                        PowerBridge.REQUEST_CODE_SERVICE_RESTART,
                    )
                    Log.i(TAG, "Recovery alarm armed after refused FGS start (ok=$recoveryArmed)")
                } else {
                    // V. ALARM_FALLBACK_DELAY_MS: un'inesatta non concede il FGS.
                    Log.w(
                        TAG,
                        "FGS start refused and no exact alarms: no alarm can grant one, " +
                            "waiting for a trigger that does (boot, app in foreground, notification)",
                    )
                }
            }
            // Nessuna sveglia esatta riproverà: resta il tocco dell'utente, e
            // glielo si chiede. Con la sveglia armata invece si tace: fra dieci
            // secondi riprova da sé, e se fallisce anche lì (chiamata senza
            // `alarmFallback`) torna qui con `recoveryArmed` falso. Il rifiuto
            // lo riconosce `RestartNotice`: un'eccezione diversa non si ripara
            // con un tocco.
            if (!recoveryArmed) {
                RestartNotice.showIfRefused(appContext, e, reason)
            }
            false
        }
    }

    /**
     * Manda su il gateway solo se sembra giù. Ritorna `true` se ha provato a
     * rianimarlo, `false` se lo ha trovato sano.
     *
     * La diagnosi vive in `Watchdog.isGatewayAlive` e non è duplicata qui: è
     * l'unico punto che conosce sia il flag di processo sia il battito su
     * SharedPreferences, e due letture separate della stessa domanda
     * divergerebbero al primo cambio di uno dei due segnali.
     *
     * Il controllo NON è un'ottimizzazione della `startForegroundService` (che
     * costerebbe comunque poco): serve a non far ripartire un service che il
     * sistema ha appena fermato *volutamente* — vedi `MainActivity.restartApp`,
     * che lo ferma e poi uccide il processo per applicare un restore.
     */
    fun ensureUpIfDown(
        context: Context,
        reason: String,
        alarmFallback: Boolean = false,
    ): Boolean {
        val appContext = context.applicationContext
        if (Watchdog.isGatewayAlive(appContext)) {
            Log.i(TAG, "Gateway looks alive (reason=$reason): nothing to do")
            return false
        }
        Log.w(TAG, "Gateway looks down (reason=$reason): restarting")
        ensureUp(appContext, reason, wakeTick = false, alarmFallback = alarmFallback)
        return true
    }
}
