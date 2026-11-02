package com.flagdizero.jenny

import android.content.Context
import android.util.Log

/**
 * Ultima rete sotto il watchdog: una sveglia ogni 8 ore programmata con
 * `AlarmManager.setAlarmClock`.
 *
 * Perché proprio `setAlarmClock` e non l'ennesima `setExactAndAllowWhileIdle`:
 * è la priorità di consegna più alta che Android offra. È l'API pensata per le
 * sveglie mattutine, e nessun gestore energetico OEM osa sopprimerla — far
 * suonare in ritardo la sveglia dell'utente è l'unico bug che un firmware non
 * si può permettere.
 *
 * Quello che si legge in giro — che `setAlarmClock` non richiede
 * `SCHEDULE_EXACT_ALARM` — è FALSO, verificato sul dispositivo il 2026-08-09:
 * senza permesso lancia `SecurityException` come qualunque altra sveglia
 * esatta, e `PowerBridge.scheduleAlarmClock` la fa degradare su una sveglia
 * inesatta (vedi il suo `catch`). Il vantaggio di priorità vale quindi solo a
 * permesso concesso; senza, questa rete resta viva ma non è più "l'ultima
 * rete", è una in più allo stesso livello delle altre.
 *
 * Perché 8 ore e non 15 minuti: la stessa priorità che la rende inaffondabile
 * la rende anche vistosa. A ~3 risvegli al giorno si resta ordini di grandezza
 * sotto qualunque euristica "questa app sveglia il sistema troppo spesso" —
 * quelle contano i risvegli, e tre al giorno non sono nemmeno rumore. Non è la
 * rete che ripara in fretta: è quella che garantisce che il down non duri
 * *giorni*. Chi ripara in fretta è il watchdog, sopra.
 *
 * CAVEAT ONESTO, ed è il motivo per cui esiste `config.power.alarmClockFallback`:
 * una `setAlarmClock` pendente fa comparire l'icona della sveglia nella status
 * bar di parecchie ROM. Non c'è modo di evitarlo — è per progetto il segnale
 * che l'utente ha una sveglia impostata. Chi non lo tollera mette il flag a
 * false, e a quel punto la catena va **cancellata**, non semplicemente lasciata
 * scadere: `configure(false)` chiama `cancel`, altrimenti l'icona resterebbe lì
 * fino allo scatto successivo e l'impostazione sembrerebbe non funzionare.
 *
 * Le impostazioni arrivano da Python (`jenny/runtime/power.py::
 * apply_alarm_clock_config`, via `PowerBridge.setAlarmClockFallback`) e vivono
 * in SharedPreferences, non in
 * `config.json`: Kotlin non parsa il config — stessa convenzione di
 * `PowerBridge.setServiceLock` e `Watchdog` — e le prefs sono l'unico posto in
 * cui il valore sopravvive alla morte del processo, che è esattamente lo
 * scenario in cui questa rete deve funzionare senza Python.
 */
object AlarmClockFallback {

    private const val TAG = "AlarmClockFallback"

    /** Request code di questa catena. Sopra 9000 come watchdog (9002) e
     *  auto-recovery del service (9001): i request code di Python stanno sotto
     *  9000, così una `cancel_wake` da Python non può smontare la rete. */
    const val REQUEST_CODE = 9003

    /** Stesso file di preferenze di `Watchdog` e `MainActivity`: una sola
     *  `SharedPreferences` per app, chiavi nuove e basta. */
    private const val KEY_ENABLED = "alarmClockFallbackEnabled"

    /** 8 ore ≈ 3 risvegli al giorno. Vedi il KDoc della classe per il perché
     *  questa rete è deliberatamente lenta. */
    private const val INTERVAL_MS = 8L * 60L * 60L * 1000L

    /** Default allineato a `PowerConfig.alarm_clock_fallback` (schema.py). Vale
     *  finché Python non ha spinto la sua configurazione almeno una volta: al
     *  primissimo avvio e dopo un wipe dei dati. */
    private const val DEFAULT_ENABLED = true

    /**
     * Salva l'impostazione e allinea subito la catena.
     *
     * `commit()` e non `apply()`, come in `Watchdog.configure`: succede una
     * volta per avvio del gateway, arriva già da un thread di lavoro, e se il
     * processo morisse subito dopo una scrittura asincrona questa rete
     * ripartirebbe con l'impostazione vecchia — cioè magari accesa dopo che
     * l'utente l'ha spenta proprio perché gli dava fastidio.
     */
    fun configure(context: Context, enabled: Boolean): Boolean {
        val appContext = context.applicationContext
        prefs(appContext).edit().putBoolean(KEY_ENABLED, enabled).commit()
        Log.i(TAG, "Alarm clock fallback config: enabled=$enabled")
        if (!enabled) {
            // Cancellazione ATTIVA, non "smetto di riarmare": una sveglia già in
            // coda scatterebbe comunque fra ore, e fino ad allora l'icona nella
            // status bar resterebbe accesa — il sintomo esatto per cui l'utente
            // ha spento il flag.
            cancel(appContext)
            // Vero: la configurazione richiesta è stata applicata. Il ritorno
            // dice "fatto quel che mi hai chiesto", non "catena armata".
            return true
        }
        return arm(appContext)
    }

    /** Arma la prossima sveglia. No-op (anzi: disarmo) a rete disattivata, così
     *  un `false` spinto da Python spegne davvero la catena invece di lasciarne
     *  girare l'ultimo anello. */
    fun arm(context: Context): Boolean {
        val appContext = context.applicationContext
        if (!prefs(appContext).getBoolean(KEY_ENABLED, DEFAULT_ENABLED)) {
            cancel(appContext)
            return false
        }
        val at = System.currentTimeMillis() + INTERVAL_MS
        val ok = PowerBridge.scheduleAlarmClock(appContext, at, REQUEST_CODE)
        Log.i(TAG, "Alarm clock fallback armed in ${INTERVAL_MS / 60_000L}min (ok=$ok)")
        return ok
    }

    /** Disarma la catena. Il ritorno dice se una sveglia era davvero in coda:
     *  `false` non è un errore, è l'esito normale di un disarmo ripetuto. */
    fun cancel(context: Context): Boolean {
        // `cancelWake` basta anche qui: l'AlarmManager identifica una sveglia
        // dal PendingIntent, non dall'API con cui è stata programmata, e il
        // PendingIntent esce dalla stessa fabbrica (`wakePendingIntent`).
        val cancelled = PowerBridge.cancelWake(context.applicationContext, REQUEST_CODE)
        Log.i(TAG, "Alarm clock fallback cancelled (was armed=$cancelled)")
        return cancelled
    }

    /**
     * Scatto della sveglia: controlla, eventualmente rianima, e RI-ARMA.
     *
     * `setAlarmClock` è one-shot come ogni altra sveglia dell'AlarmManager:
     * senza ri-armo questa rete vivrebbe 8 ore in tutto. Il ri-armo è l'ultima
     * istruzione e sta in `finally`, fuori da ogni ramo — stessa regola di
     * `Watchdog.onAlarm`, e per lo stesso motivo: se stesse nel ramo "tutto
     * bene", un'eccezione in `startForegroundService` spegnerebbe la rete per
     * sempre proprio nel momento in cui serve.
     */
    fun onAlarm(context: Context) {
        val appContext = context.applicationContext
        try {
            // `alarmFallback = false`: siamo già dentro la callback di una
            // sveglia, quindi nell'allowlist temporanea. Se l'avvio del FGS
            // fallisce qui, non lo riparerebbe un'altra sveglia.
            GatewayStarter.ensureUpIfDown(appContext, reason = "alarm-clock")
        } catch (e: Exception) {
            Log.e(TAG, "Alarm clock fallback check failed", e)
        } finally {
            arm(appContext)
        }
    }

    private fun prefs(appContext: Context) = JennyPrefs.of(appContext)
}
