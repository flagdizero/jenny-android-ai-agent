package com.flagdizero.jenny

import android.content.Context
import android.os.PowerManager
import android.util.Log

/**
 * Catena di sveglie che controlla periodicamente se il gateway è ancora in
 * piedi e, se non lo è, lo rimette su.
 *
 * Perché serve una catena e non una sveglia sola: `setExactAndAllowWhileIdle`
 * è **one-shot** per definizione. Una sveglia non ri-armata scatta una volta e
 * il watchdog è morto per sempre, senza che niente lo dica — nessun log,
 * nessun errore, solo un telefono che un giorno smette di rispondere. Per
 * questo `onAlarm` ri-arma SEMPRE, come ultima istruzione e fuori da ogni
 * ramo condizionale: qualunque cosa sia successo al controllo, la prossima
 * sveglia deve essere già in coda quando questa callback termina.
 *
 * L'altro modo — documentato — di far morire la catena in silenzio è la revoca
 * del permesso sulle sveglie esatte: a ri-armare qui è `WakeReceiver`, che il
 * cambio di permesso lo scopre in due modi, perché la broadcast di sistema da
 * sola non è arrivata sul dispositivo reale (vedi `syncExactAlarmState`).
 *
 * Le impostazioni (`abilitato`, `intervallo`) arrivano da Python
 * (`jenny/runtime/power.py::apply_watchdog_config`) e vengono parcheggiate in
 * SharedPreferences, non lette da `config.json`: Kotlin non parsa il config —
 * stessa convenzione di `PowerBridge.setServiceLock` — e le prefs sono l'unico
 * posto dove quel valore sopravvive alla morte del processo, che è esattamente
 * lo scenario in cui il watchdog deve funzionare senza Python.
 */
object Watchdog {

    private const val TAG = "Watchdog"

    /** Request code della sveglia di watchdog. Sopra 9000 come quello di
     *  auto-recovery del service: i request code Python stanno sotto 9000
     *  (vedi `PowerBridge.REQUEST_CODE_SERVICE_RESTART`), così una
     *  `cancel_wake` da Python non può smontare la catena. */
    const val REQUEST_CODE = 9002

    /** Stesso file di preferenze di MainActivity: una sola `SharedPreferences`
     *  per app, chiavi nuove e basta. Due file separati sarebbero due lock e
     *  due punti da ricordare al momento di un wipe. */
    private const val PREFS_NAME = "jenny"
    private const val KEY_ENABLED = "watchdogEnabled"
    private const val KEY_INTERVAL_MIN = "watchdogIntervalMin"
    private const val KEY_HEARTBEAT_MS = "gatewayHeartbeatMs"

    /** Default allineati a `PowerConfig` (schema.py). Valgono finché Python non
     *  ha spinto la sua configurazione almeno una volta — cioè al primissimo
     *  avvio e dopo un wipe dei dati. */
    private const val DEFAULT_INTERVAL_MIN = 15
    private const val MIN_INTERVAL_MIN = 5
    private const val MAX_INTERVAL_MIN = 120

    /** Moltiplicatori dell'intervallo secondo lo stato energetico del device.
     *  Non è risparmio batteria fine a sé stesso: i gestori energetici OEM
     *  contano i risvegli e marcano come "l'app sveglia il sistema troppo
     *  spesso" chi insiste a intervallo fisso a schermo spento — PowerGenie di
     *  Honor/Huawei è il caso documentato, e questo progetto gira proprio su
     *  quei telefoni. Diradare in doze costa qualche minuto di ritardo sul
     *  recupero e toglie di mezzo il motivo per cui l'OEM ci ucciderebbe. */
    private const val MULTIPLIER_IDLE = 2
    private const val MULTIPLIER_DOZE = 4

    /** Quanti periodi (già moltiplicati per il caso peggiore) può restare
     *  indietro il battito prima di considerare morto il gateway. Generoso di
     *  proposito: un falso positivo costa una `startForegroundService` su un
     *  service vivo, che è un no-op; un falso negativo lascia l'agente giù
     *  finché non se ne accorge l'utente. */
    private const val STALE_PERIODS = 3

    // -- configurazione spinta da Python -------------------------------------

    /**
     * Salva le impostazioni del watchdog e allinea subito la catena.
     *
     * Chiamata da Python via `PowerBridge.setWatchdog` all'avvio del gateway.
     * `commit()` e non `apply()`: è raro (una volta per avvio), arriva già da
     * un thread di lavoro, e se il processo venisse ucciso subito dopo una
     * scrittura asincrona il watchdog ripartirebbe con l'impostazione vecchia.
     */
    fun configure(context: Context, enabled: Boolean, intervalMin: Int): Boolean {
        val appContext = context.applicationContext
        val clamped = intervalMin.coerceIn(MIN_INTERVAL_MIN, MAX_INTERVAL_MIN)
        prefs(appContext).edit()
            .putBoolean(KEY_ENABLED, enabled)
            .putInt(KEY_INTERVAL_MIN, clamped)
            .commit()
        // Che Python sia arrivato fin qui è la prova migliore che il gateway è
        // vivo: il battito parte da adesso, non dall'ultimo avvio del service.
        noteAlive(appContext)
        Log.i(TAG, "Watchdog config: enabled=$enabled interval=${clamped}min")
        if (!enabled) {
            cancel(appContext)
            // Vero: la configurazione richiesta è stata applicata. Il valore di
            // ritorno dice "fatto quel che mi hai chiesto", non "catena armata".
            return true
        }
        return arm(appContext)
    }

    /** Segna che il gateway è vivo adesso. `apply()`: frequente e ricostruibile
     *  (nel peggiore dei casi si perde un battito e il watchdog fa un riavvio
     *  a vuoto, che è un no-op). */
    fun noteAlive(context: Context) {
        prefs(context.applicationContext).edit()
            .putLong(KEY_HEARTBEAT_MS, System.currentTimeMillis())
            .apply()
    }

    // -- catena ---------------------------------------------------------------

    /** Arma il prossimo controllo. No-op (anzi: disarmo) se il watchdog è
     *  spento, così un `watchdogEnabled=false` spinto da Python spegne davvero
     *  la catena invece di lasciarne girare l'ultimo anello. */
    fun arm(context: Context): Boolean {
        val appContext = context.applicationContext
        if (!prefs(appContext).getBoolean(KEY_ENABLED, true)) {
            cancel(appContext)
            return false
        }
        val minutes = effectiveIntervalMin(appContext)
        val at = System.currentTimeMillis() + minutes * 60_000L
        val ok = PowerBridge.scheduleWake(appContext, at, REQUEST_CODE)
        Log.i(TAG, "Watchdog armed in ${minutes}min (ok=$ok)")
        return ok
    }

    /** Disarma la catena. Ritorna se una sveglia era davvero in coda: `false`
     *  non è un errore, è l'esito normale di un disarmo ripetuto. */
    fun cancel(context: Context): Boolean {
        val cancelled = PowerBridge.cancelWake(context.applicationContext, REQUEST_CODE)
        Log.i(TAG, "Watchdog chain cancelled (was armed=$cancelled)")
        return cancelled
    }

    /**
     * Scatto della sveglia: controlla, eventualmente rianima, e RI-ARMA.
     *
     * L'ordine non è negoziabile. Il ri-armo è l'ultima istruzione e vive fuori
     * dall'`if`: se stesse dentro il ramo "tutto bene", un riavvio andato
     * storto — o semplicemente un'eccezione in `startForegroundService` —
     * spegnerebbe il watchdog per sempre proprio nel momento in cui serve.
     */
    fun onAlarm(context: Context) {
        val appContext = context.applicationContext
        try {
            if (isGatewayAlive(appContext)) {
                // Il controllo di adesso È il battito: finché la catena gira e
                // trova il gateway su, il timestamp resta fresco da solo.
                noteAlive(appContext)
            } else {
                Log.w(
                    TAG,
                    "Gateway looks down (serviceRunning=${GatewayService.isRunning}, " +
                        "gatewayThreadDead=${GatewayService.isGatewayThreadDead}, " +
                        "heartbeatAgeMs=${heartbeatAgeMs(appContext)}): restarting"
                )
                GatewayStarter.ensureUp(appContext, reason = "watchdog")
            }
        } catch (e: Exception) {
            Log.e(TAG, "Watchdog check failed", e)
        } finally {
            arm(appContext)
        }
    }

    // -- diagnosi --------------------------------------------------------------

    /**
     * Il gateway è vivo?
     *
     * Tre segnali, e servono tutti perché rispondono a domande diverse:
     *
     * * `GatewayService.isRunning` è uno static, quindi vale solo DENTRO questo
     *   processo. Se il processo era stato ucciso e la sveglia lo ha appena
     *   fatto ricreare, il flag è `false` perché la classe è appena stata
     *   caricata — che è la risposta giusta (il service non c'è) ma per il
     *   motivo sbagliato, e non dice nulla su cosa girava prima.
     * * `GatewayService.isGatewayThreadDead` copre il caso che `isRunning` non
     *   vede: `Service` ancora istanziato — notifica in barra compresa — ma
     *   runtime Python morto, perché `run_gateway` è uscito. Lì `isRunning`
     *   dice "vivo" e mente, e finché questo segnale non esisteva la bugia
     *   reggeva fino a quando il battito diventava stantio: fino a TRE ORE
     *   (`staleAfterMs`), su un controllo che gira ogni 15-60 minuti.
     * * Il battito su SharedPreferences sopravvive alla morte del processo ed è
     *   l'unico che dice qualcosa su ciò che girava PRIMA. Resta la misura
     *   giusta per un gateway vivo ma lentissimo o in doze — non per uno che si
     *   sa già essere uscito, ed è per questo che la soglia generosa non è
     *   stata toccata: ha semplicemente smesso di essere l'unico strumento per
     *   un guasto che non era il suo.
     *
     * Il riavvio ripara davvero anche il secondo caso perché
     * `GatewayService.onStartCommand` richiama `startGateway()`, che riparte se
     * il thread del gateway non c'è più. (Il service ci prova anche da solo, al
     * massimo una volta ogni 15 minuti: vedi `shouldSelfRestart`. Quando quel
     * tentativo riesce non si arriva neanche qui.)
     */
    // `internal` e non `private`: la diagnosi la usa anche `GatewayStarter`
    // (`ensureUpIfDown`), e deve restare UNA. Duplicarla là significherebbe due
    // letture della stessa domanda che divergono al primo cambio di uno dei due
    // segnali. Resta comunque interna al modulo: non è API per Python.
    internal fun isGatewayAlive(appContext: Context): Boolean {
        if (!GatewayService.isRunning) return false
        // Prima del battito, e senza appello: qui non stiamo stimando, sappiamo.
        // Il thread del gateway è terminato in questo processo, quindi dietro al
        // service non c'è nessun agente — per quanto fresco sia il timestamp
        // (`onStartCommand` ne scrive uno a ogni avvio, gateway o no).
        if (GatewayService.isGatewayThreadDead) return false
        val age = heartbeatAgeMs(appContext)
        // Battito mai scritto (primo avvio, dati cancellati): il flag statico è
        // già una prova sufficiente, non si riavvia per una prefs vuota.
        if (age == null) return true
        // Orologio spostato indietro (fuso, sync NTP): un'età negativa non è
        // "vecchissimo", è "non misurabile". Si crede al flag.
        if (age < 0) return true
        return age <= staleAfterMs(appContext)
    }

    private fun heartbeatAgeMs(appContext: Context): Long? {
        val beat = prefs(appContext).getLong(KEY_HEARTBEAT_MS, 0L)
        if (beat <= 0L) return null
        return System.currentTimeMillis() - beat
    }

    /** Soglia di obsolescenza calcolata sul moltiplicatore PEGGIORE, non su
     *  quello attuale: fra un controllo e il successivo il device può essere
     *  entrato in doze e aver diradato la catena, e misurare col moltiplicatore
     *  di adesso dichiarerebbe morto un gateway sanissimo. */
    private fun staleAfterMs(appContext: Context): Long =
        baseIntervalMin(appContext).toLong() * MULTIPLIER_DOZE * STALE_PERIODS * 60_000L

    private fun baseIntervalMin(appContext: Context): Int =
        prefs(appContext).getInt(KEY_INTERVAL_MIN, DEFAULT_INTERVAL_MIN)
            .coerceIn(MIN_INTERVAL_MIN, MAX_INTERVAL_MIN)

    /** Intervallo adattivo: pieno a schermo acceso, ×2 a schermo spento, ×4 in
     *  doze profondo. Vedi i moltiplicatori per il perché. */
    private fun effectiveIntervalMin(appContext: Context): Int {
        val base = baseIntervalMin(appContext)
        val pm = appContext.getSystemService(Context.POWER_SERVICE) as? PowerManager
            ?: return base
        return try {
            when {
                pm.isDeviceIdleMode -> base * MULTIPLIER_DOZE
                !pm.isInteractive -> base * MULTIPLIER_IDLE
                else -> base
            }
        } catch (e: Exception) {
            Log.w(TAG, "Could not read power state, using base interval", e)
            base
        }
    }

    private fun prefs(appContext: Context) =
        appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
