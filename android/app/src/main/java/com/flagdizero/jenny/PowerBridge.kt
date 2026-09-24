package com.flagdizero.jenny

import android.annotation.SuppressLint
import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.util.Log

/**
 * Bridge per il sottosistema energetico (wakelock + sveglie AlarmManager),
 * esposto a Python via Chaquopy (`jclass("com.flagdizero.jenny.PowerBridge")`),
 * mai istanziato da Kotlin — stesso pattern di NotifierBridge / LocationBridge.
 *
 * Perché serve: il foreground service tiene il processo VIVO (non lo fa
 * uccidere dal low-memory killer e lo esenta dai limiti di background), ma
 * NON tiene la CPU accesa. A schermo spento il kernel entra in suspend e un
 * turno dell'agente a metà — richiesta HTTP al provider, tool in esecuzione,
 * scrittura di memoria — resta congelato finché qualcosa non risveglia il
 * dispositivo. L'unico modo di garantire cicli di CPU è un PARTIAL_WAKE_LOCK,
 * e questo bridge è la sua unica porta d'accesso.
 *
 * Politica: qui vive solo la meccanica nativa (PowerManager, AlarmManager). La
 * decisione "quando prendere il wakelock e per quanto" vive in Python, che è
 * l'unico a sapere quando un turno inizia e finisce.
 *
 * Nessun metodo lancia mai verso Python: si logga e si torna un default sicuro
 * (false). Un'eccezione che attraversa il confine Chaquopy abortirebbe il turno
 * proprio nel punto in cui stavamo cercando di proteggerlo.
 */
class PowerBridge(context: Context) {

    companion object {
        private const val TAG = "PowerBridge"

        /** Prefisso di package sul tag del wakelock: è la convenzione che
         *  Android si aspetta (`"pkg:motivo"`) e senza la quale i tool di
         *  diagnostica batteria attribuiscono il consumo a un tag anonimo,
         *  rendendo impossibile capire chi tiene sveglio il telefono. */
        private const val TAG_PREFIX = "jenny:"

        /** Request code riservato alla sveglia di auto-recovery del
         *  GatewayService (vedi `GatewayService.onDestroy`). Python usa request
         *  code arbitrari per le proprie sveglie: se ne riusasse questo,
         *  `cancelWake` da Python smonterebbe in silenzio la rete di sicurezza
         *  del service. Tenere i request code Python sotto 9000.
         *
         *  Gli altri riservati vivono accanto alla rete che li usa: 9002 in
         *  `Watchdog`, 9003 in `AlarmClockFallback`. */
        const val REQUEST_CODE_SERVICE_RESTART = 9001

        /**
         * I wakelock vivono QUI, non nell'istanza. Chaquopy costruisce un
         * `PowerBridge` nuovo ogni volta che Python fa `jclass(...)(context)`, e
         * se la mappa fosse per-istanza due bridge potrebbero prendere lo stesso
         * tag due volte (doppio conteggio) oppure — molto peggio — un bridge
         * ricreato perderebbe il riferimento al lock del precedente, che
         * resterebbe attivo per sempre senza che nessuno possa più rilasciarlo.
         * Con lo stato in companion object il tag è unico di processo.
         *
         * Accesso sempre dentro `synchronized(locks)`: le chiamate arrivano da
         * thread di lavoro Python arbitrari e ogni operazione è un
         * check-then-act (leggi lo stato, decidi, muta), non una singola
         * operazione atomica di mappa — una ConcurrentHashMap non basterebbe.
         */
        private val locks = HashMap<String, PowerManager.WakeLock>()

        /**
         * Extra con cui ogni sveglia si presenta a `WakeReceiver`.
         *
         * Il receiver non ha modo di sapere QUALE sveglia lo ha svegliato: il
         * request code identifica il `PendingIntent` ma non arriva nella
         * callback. Ce lo mettiamo noi, qui, nell'unico punto in cui i
         * PendingIntent nascono, così non può divergere da quello vero.
         *
         * Metterlo non cambia l'identità della sveglia: l'uguaglianza fra
         * `PendingIntent` guarda request code e `Intent.filterEquals` (azione,
         * dati, componente), non gli extra — quindi `cancelWake`, che ricostruisce
         * l'intent con `FLAG_NO_CREATE`, continua a trovare la sveglia giusta.
         */
        const val EXTRA_REQUEST_CODE = "com.flagdizero.jenny.extra.REQUEST_CODE"

        /**
         * Wakelock corto dell'handoff sveglia → service.
         *
         * `onReceive` ha cicli di CPU garantiti solo per la sua durata: appena
         * ritorna, il device può risospendere prima ancora che
         * `onStartCommand` giri, e il tick andrebbe perso proprio nel caso —
         * schermo spento, doze — per cui la sveglia esiste. Il lock copre quel
         * buco. Timeout duro e corto: serve solo al passaggio di consegne, e a
         * rilasciarlo è `GatewayService` una volta consegnato il tick a Python.
         */
        const val HANDOFF_LOCK_TAG = "wake"
        private const val HANDOFF_TIMEOUT_MS = 30_000L

        /** Prende il lock di handoff. Vedi `HANDOFF_LOCK_TAG`. */
        fun acquireHandoffLock(context: Context): Boolean =
            acquireLock(context.applicationContext, HANDOFF_LOCK_TAG, HANDOFF_TIMEOUT_MS)

        /** Rilascia il lock di handoff. `false` se era già scaduto: esito
         *  normale, non un errore. */
        fun releaseHandoffLock(): Boolean = releaseLock(HANDOFF_LOCK_TAG)

        /** Tag del lock a livello di servizio (`power.keepAwake = "always"`).
         *  Distinto dai tag per-turno di Python — che sono corti e descrivono il
         *  lavoro ("turn", "cron", "ssh") — così in `dumpsys power` si legge a
         *  colpo d'occhio se la CPU è tenuta accesa dalla modalità always o da un
         *  turno in corso. Python non usa mai questo tag. */
        const val SERVICE_LOCK_TAG = "gateway"

        /** Handler della rotazione del lock di servizio. Creato pigramente e sul
         *  main Looper: `setServiceLock` arriva da un thread di lavoro Python,
         *  che non ha un Looper proprio a cui agganciarsi. */
        private var serviceHandler: Handler? = null

        /** Callback di rotazione attualmente armata, o null. Serve sia per
         *  disarmarla (`removeCallbacks`) sia come token di identità: un
         *  callback già in coda che si risveglia dopo un `cancelRotation` si
         *  riconosce perché non è più questo, e si spegne invece di
         *  ri-acquisire un lock su un servizio morto. */
        private var rotateRunnable: Runnable? = null

        /**
         * Accende o spegne il wakelock che copre l'INTERA vita del gateway.
         *
         * Il chiamante è Python (`jenny/runtime/power.py::apply_service_lock`),
         * una volta all'avvio del gateway. Per lo spegnimento i chiamanti sono
         * due, e insieme coprono l'invariante "il lock è tenuto se e solo se un
         * thread del gateway vivo lo vuole": `GatewayService.onDestroy`, ma
         * solo a thread del gateway ormai morto, e la coda del thread del
         * gateway stesso quando esce lasciandosi dietro un service vivo. Kotlin
         * non legge `config.json`: la modalità e il periodo
         * di rotazione arrivano già decisi da chi il config lo sa parsare, così
         * la stessa impostazione non finisce interpretata da due linguaggi che
         * possono divergere.
         *
         * `rotateMin > 0` ruota il lock (release + acquire) ogni N minuti. Non è
         * paranoia generica: PowerGenie, il gestore energetico di Honor/Huawei,
         * uccide l'app che tiene un wakelock oltre i 60 minuti con un tag non in
         * whitelist — e questo progetto gira proprio su quei telefoni. La
         * finestra scoperta fra release e acquire è di microsecondi (il kernel
         * non fa in tempo a sospendere), quindi la rotazione non costa nulla e
         * toglie di mezzo l'unico modo documentato di farsi uccidere.
         */
        fun setServiceLock(context: Context, enabled: Boolean, rotateMin: Int): Boolean {
            val appContext = context.applicationContext
            synchronized(locks) {
                // Sempre per prima, anche quando si sta (ri)abilitando: due
                // rotazioni armate sullo stesso tag si pesterebbero i piedi.
                cancelRotation()
                if (!enabled) {
                    val released = releaseLock(SERVICE_LOCK_TAG)
                    Log.i(TAG, "Service wakelock off (was held=$released)")
                    return true
                }
                val acquired = acquireLock(appContext, SERVICE_LOCK_TAG, timeoutMs = null)
                if (acquired && rotateMin > 0) {
                    scheduleRotation(appContext, rotateMin)
                }
                Log.i(TAG, "Service wakelock on (acquired=$acquired, rotateMin=$rotateMin)")
                return acquired
            }
        }

        /** Arma la prossima rotazione. Da chiamare con `locks` già tenuto. */
        private fun scheduleRotation(appContext: Context, rotateMin: Int) {
            val handler = serviceHandler
                ?: Handler(Looper.getMainLooper()).also { serviceHandler = it }
            val periodMs = rotateMin.toLong() * 60_000L
            val runnable = object : Runnable {
                override fun run() {
                    synchronized(locks) {
                        // Disarmata mentre eravamo in coda (service distrutto,
                        // modalità cambiata): non toccare nulla e non riarmare.
                        if (rotateRunnable !== this) return
                        releaseLock(SERVICE_LOCK_TAG)
                        acquireLock(appContext, SERVICE_LOCK_TAG, timeoutMs = null)
                        serviceHandler?.postDelayed(this, periodMs)
                    }
                }
            }
            rotateRunnable = runnable
            handler.postDelayed(runnable, periodMs)
        }

        /** Disarma la rotazione. Da chiamare con `locks` già tenuto. */
        private fun cancelRotation() {
            rotateRunnable?.let { serviceHandler?.removeCallbacks(it) }
            rotateRunnable = null
        }

        /**
         * Implementazione unica di acquire, condivisa dal metodo d'istanza (che
         * passa sempre un timeout, vedi lì il perché) e dal lock di servizio.
         *
         * `timeoutMs = null` significa lock SENZA scadenza, ed è legittimo solo
         * per il lock di servizio: quello vive quanto il `GatewayService` e
         * muore con lui — `onDestroy` lo rilascia, e se il processo viene ucciso
         * di netto è l'OS a riprendersi i wakelock del processo morto. Un lock
         * per-turno di Python invece sopravviverebbe eccome a un `finally` non
         * eseguito, perché il processo resta vivo: per quello il timeout non è
         * negoziabile.
         */
        @SuppressLint("WakelockTimeout")
        private fun acquireLock(context: Context, tag: String, timeoutMs: Long?): Boolean {
            val manager = context.applicationContext
                .getSystemService(Context.POWER_SERVICE) as? PowerManager ?: return false
            return try {
                synchronized(locks) {
                    val existing = locks[tag]
                    if (existing != null) {
                        if (existing.isHeld) return true
                        // Presente ma non più attivo: il timeout dell'OS è scaduto.
                        // Si riusa lo stesso oggetto, che è ancora perfettamente
                        // valido, invece di lasciarne accumulare uno per rinnovo.
                        if (timeoutMs != null) existing.acquire(timeoutMs) else existing.acquire()
                        return true
                    }
                    val lock = manager.newWakeLock(
                        PowerManager.PARTIAL_WAKE_LOCK, TAG_PREFIX + tag
                    ).apply {
                        // Non conteggiato: `acquire` multipli sullo stesso tag non
                        // devono richiedere altrettanti `release` per spegnersi. Il
                        // conteggio vive nella mappa qui sopra, uno per tag, e un
                        // solo `release` deve bastare a garantire che il lock sia
                        // andato — un contatore sbilanciato è esattamente il bug che
                        // lascia la CPU accesa.
                        setReferenceCounted(false)
                    }
                    if (timeoutMs != null) lock.acquire(timeoutMs) else lock.acquire()
                    locks[tag] = lock
                    true
                }
            } catch (e: Exception) {
                Log.e(TAG, "acquire failed for tag=$tag", e)
                false
            }
        }

        /** Implementazione unica di release; vedi il metodo d'istanza. */
        private fun releaseLock(tag: String): Boolean {
            return try {
                synchronized(locks) {
                    val lock = locks.remove(tag) ?: return false
                    if (!lock.isHeld) return false
                    lock.release()
                    true
                }
            } catch (e: Exception) {
                Log.e(TAG, "release failed for tag=$tag", e)
                false
            }
        }

        /**
         * Arma una sveglia che perfora il Doze. Statica perché ha due
         * chiamanti — il metodo d'istanza per Python e `GatewayService` per la
         * propria auto-ripartenza — e la scelta esatto/inesatto con il suo
         * fallback non deve esistere in due copie che divergono.
         */
        fun scheduleWake(context: Context, atMillisSinceEpoch: Long, requestCode: Int): Boolean {
            val appContext = context.applicationContext
            val alarm = appContext.getSystemService(AlarmManager::class.java) ?: return false
            val pending = wakePendingIntent(appContext, requestCode, create = true) ?: return false
            return try {
                if (canScheduleExactAlarms(appContext)) {
                    alarm.setExactAndAllowWhileIdle(
                        AlarmManager.RTC_WAKEUP, atMillisSinceEpoch, pending
                    )
                } else {
                    // Android 12+ senza SCHEDULE_EXACT_ALARM concesso. Inesatta
                    // (il sistema la può far slittare di minuti) ma `AllowWhileIdle`
                    // la fa comunque scattare in Doze: meglio una sveglia in
                    // ritardo che nessuna sveglia.
                    alarm.setAndAllowWhileIdle(
                        AlarmManager.RTC_WAKEUP, atMillisSinceEpoch, pending
                    )
                }
                true
            } catch (e: SecurityException) {
                // Il permesso può essere revocato tra il controllo e la
                // programmazione: si ritenta in modalità inesatta, che non
                // richiede alcun permesso.
                Log.w(TAG, "Exact alarm denied, falling back to inexact", e)
                try {
                    alarm.setAndAllowWhileIdle(
                        AlarmManager.RTC_WAKEUP, atMillisSinceEpoch, pending
                    )
                    true
                } catch (e2: Exception) {
                    Log.e(TAG, "scheduleWake failed", e2)
                    false
                }
            } catch (e: Exception) {
                Log.e(TAG, "scheduleWake failed", e)
                false
            }
        }

        /**
         * Arma una sveglia con `setAlarmClock`: la priorità di consegna più
         * alta che Android offra, ed è l'ultima rete sotto il watchdog.
         *
         * Perché non basta `scheduleWake`: `setExactAndAllowWhileIdle` è
         * soggetta alle quote del sistema e, soprattutto, ai gestori batteria
         * dei produttori, che la sopprimono senza dirlo. `setAlarmClock` è nata
         * per le sveglie da comodino — sopprimerla significherebbe far perdere
         * un treno all'utente — e nessuna ROM se la sente.
         *
         * ATTENZIONE, verificato sul dispositivo il 2026-08-09 e NON come si
         * legge in giro: `setAlarmClock` **richiede** comunque il permesso
         * sveglie esatte (`SCHEDULE_EXACT_ALARM` o `USE_EXACT_ALARM`). Senza,
         * lancia `SecurityException` come qualunque altra sveglia esatta — sul
         * Titan 2, dove il permesso è negato di default a targetSdk 34, è
         * esattamente ciò che succede. Il vantaggio di priorità di consegna
         * vale quindi solo A PERMESSO CONCESSO; senza, questa rete degrada a
         * `scheduleWake` (inesatta) come tutte le altre, e non è più "l'ultima
         * rete" ma una in più allo stesso livello.
         *
         * Ed è quella visibilità il suo prezzo: su parecchie ROM una sveglia in
         * coda accende l'icona della sveglia nella barra di stato. Per questo
         * la rete sta dietro a `power.alarmClockFallback` e scatta tre volte al
         * giorno, non ogni quarto d'ora: a quella frequenza resta sotto la
         * soglia di qualunque euristica "questa app sveglia troppo il sistema".
         *
         * `showIntent` porta a MainActivity: è ciò che si apre toccando
         * l'icona, e mandare l'utente sulla propria app è l'unica risposta
         * onesta a "perché c'è una sveglia?".
         */
        fun scheduleAlarmClock(
            context: Context,
            atMillisSinceEpoch: Long,
            requestCode: Int,
        ): Boolean {
            val appContext = context.applicationContext
            val alarm = appContext.getSystemService(AlarmManager::class.java) ?: return false
            val pending = wakePendingIntent(appContext, requestCode, create = true) ?: return false
            return try {
                val show = PendingIntent.getActivity(
                    appContext,
                    requestCode,
                    Intent(appContext, MainActivity::class.java)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
                )
                alarm.setAlarmClock(AlarmManager.AlarmClockInfo(atMillisSinceEpoch, show), pending)
                true
            } catch (e: SecurityException) {
                // Permesso sveglie esatte negato: è il caso ATTESO su un
                // dispositivo che non l'ha concesso, non un guasto. Riga
                // singola e senza stack trace, altrimenti ogni riarmo (avvio,
                // boot, aggiornamento, re-grant) vomita una traccia identica
                // nel logcat e nasconde i problemi veri. `canScheduleExactAlarms`
                // dice la stessa cosa senza rumore: la si legge nel pannello
                // diagnostico, non qui.
                Log.i(TAG, "Exact alarm permission not granted: alarm clock net degraded to inexact")
                scheduleWake(appContext, atMillisSinceEpoch, requestCode)
            } catch (e: Exception) {
                // Tutto il resto è davvero anomalo: si degrada sulla sveglia
                // ordinaria invece di restare senza rete, ma con la traccia.
                Log.w(TAG, "scheduleAlarmClock failed, falling back to scheduleWake", e)
                scheduleWake(appContext, atMillisSinceEpoch, requestCode)
            }
        }

        /** Annulla la sveglia con quel request code. False se non ce n'era una
         *  (il PendingIntent non esiste): non è un errore, è l'esito normale di
         *  un annullamento ripetuto. */
        fun cancelWake(context: Context, requestCode: Int): Boolean {
            val appContext = context.applicationContext
            val alarm = appContext.getSystemService(AlarmManager::class.java) ?: return false
            return try {
                val pending = wakePendingIntent(appContext, requestCode, create = false)
                    ?: return false
                alarm.cancel(pending)
                pending.cancel()
                true
            } catch (e: Exception) {
                Log.e(TAG, "cancelWake failed", e)
                false
            }
        }

        /** Sotto Android 12 le sveglie esatte non richiedono permesso: sempre
         *  concesse. Da API 31 il permesso è revocabile dall'utente e va
         *  ricontrollato a ogni programmazione, non memorizzato. */
        fun canScheduleExactAlarms(context: Context): Boolean {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return true
            val alarm = context.applicationContext.getSystemService(AlarmManager::class.java)
                ?: return false
            return try {
                alarm.canScheduleExactAlarms()
            } catch (e: Exception) {
                Log.w(TAG, "canScheduleExactAlarms failed", e)
                false
            }
        }

        /** PendingIntent verso `WakeReceiver`, identificato dal solo request
         *  code: due sveglie con lo stesso request code sono per definizione la
         *  stessa sveglia, perché gli extra non contano nell'uguaglianza (vedi
         *  `EXTRA_REQUEST_CODE`, che è lì solo per farsi leggere dal receiver).
         *  `create = false` → FLAG_NO_CREATE, l'unico modo di CHIEDERE se una
         *  sveglia esiste senza crearla come effetto collaterale. */
        private fun wakePendingIntent(
            appContext: Context,
            requestCode: Int,
            create: Boolean,
        ): PendingIntent? {
            val intent = Intent(appContext, WakeReceiver::class.java)
                .putExtra(EXTRA_REQUEST_CODE, requestCode)
            var flags = PendingIntent.FLAG_IMMUTABLE
            flags = flags or if (create) {
                PendingIntent.FLAG_UPDATE_CURRENT
            } else {
                PendingIntent.FLAG_NO_CREATE
            }
            return PendingIntent.getBroadcast(appContext, requestCode, intent, flags)
        }
    }

    private val appContext = context.applicationContext
    private val pm = appContext.getSystemService(Context.POWER_SERVICE) as? PowerManager

    /**
     * Prende (o rinnova) il PARTIAL_WAKE_LOCK associato a `tag`. True se al
     * ritorno il lock è nostro — compreso il caso "era già preso", che è un
     * no-op volutamente silenzioso: Python può chiamare `acquire` all'inizio di
     * ogni passo di un turno senza doversi ricordare cosa ha già preso.
     *
     * `timeoutMs` NON è opzionale ed è sempre passato a `WakeLock.acquire`.
     * Motivo: un wakelock mai rilasciato — perché il thread Python è morto, il
     * processo è stato ucciso a metà turno o un `finally` non è stato eseguito —
     * tiene la CPU accesa a schermo spento e SCARICA LA BATTERIA FINO A ZERO
     * senza dare all'utente alcuna spiegazione, perché il telefono sembra
     * spento. Con il timeout è l'OS a rilasciarlo comunque, sempre: la peggiore
     * conseguenza di un bug diventa qualche minuto di CPU sprecata.
     */
    fun acquire(tag: String, timeoutMs: Long): Boolean =
        acquireLock(appContext, tag, timeoutMs)

    /**
     * Rilascia il wakelock di `tag`. False — senza lanciare — se quel tag non
     * era preso: rilasciare due volte, o rilasciare dopo che il timeout dell'OS
     * ha già fatto il lavoro, è un esito normale del `finally` di Python, non un
     * errore da propagare.
     */
    fun release(tag: String): Boolean = releaseLock(tag)

    /** True solo se quel tag è preso ADESSO: un lock scaduto per timeout resta
     *  in mappa ma risponde false, che è la verità che interessa al chiamante. */
    fun isHeld(tag: String): Boolean {
        return try {
            synchronized(locks) { locks[tag]?.isHeld == true }
        } catch (e: Exception) {
            Log.w(TAG, "isHeld failed for tag=$tag", e)
            false
        }
    }

    /** True se l'utente ha concesso l'esenzione dall'ottimizzazione batteria.
     *  Senza esenzione il Doze rinvia comunque rete e job a finestre di
     *  manutenzione, wakelock o no: è la diagnostica che spiega a Python perché
     *  un turno "sveglio" può restare lo stesso bloccato sulla rete. */
    fun isBatteryExempt(): Boolean {
        val manager = pm ?: return false
        return try {
            manager.isIgnoringBatteryOptimizations(appContext.packageName)
        } catch (e: Exception) {
            Log.w(TAG, "isBatteryExempt failed", e)
            false
        }
    }

    /** Sveglia one-shot a `atMillisSinceEpoch` (epoch UTC, RTC_WAKEUP) che fa
     *  ripartire il gateway via `WakeReceiver`. Vedi il gemello statico. */
    fun scheduleWake(atMillisSinceEpoch: Long, requestCode: Int): Boolean =
        Companion.scheduleWake(appContext, atMillisSinceEpoch, requestCode)

    /** Annulla la sveglia con quel request code. */
    fun cancelWake(requestCode: Int): Boolean = Companion.cancelWake(appContext, requestCode)

    /** Accende/spegne il lock di servizio. Vedi il gemello statico: è Python a
     *  decidere, perché è l'unico che legge `config.power`. */
    fun setServiceLock(enabled: Boolean, rotateMin: Int): Boolean =
        Companion.setServiceLock(appContext, enabled, rotateMin)

    /**
     * Spinge a Kotlin le impostazioni del watchdog (`config.power.watchdog*`) e
     * allinea la catena di sveglie.
     *
     * Stessa divisione dei compiti di `setServiceLock`: `config.json` lo parsa
     * solo Python, Kotlin riceve valori già decisi. Il watchdog però deve poter
     * girare anche quando Python non c'è più — è tutto il suo scopo — quindi
     * `Watchdog` parcheggia questi valori in SharedPreferences invece di
     * tenerli in memoria.
     */
    fun setWatchdog(enabled: Boolean, intervalMin: Int): Boolean =
        Watchdog.configure(appContext, enabled, intervalMin)

    /**
     * Spinge a Kotlin `config.power.alarmClockFallback` e allinea l'ultima rete.
     *
     * Terzo membro della stessa famiglia di `setServiceLock` e `setWatchdog`,
     * con la stessa divisione dei compiti: il config lo parsa solo Python,
     * Kotlin riceve un valore già deciso e lo parcheggia in SharedPreferences,
     * perché questa rete deve saper girare quando Python non c'è più.
     *
     * Il caso che rende obbligatorio spingere anche un `false`: la sveglia vive
     * nell'AlarmManager di sistema e sopravvive al riavvio del gateway, quindi
     * "smettere di riarmare" non basta — resterebbe in coda fino allo scatto
     * successivo, e con lei l'icona della sveglia nella barra di stato, cioè il
     * sintomo esatto per cui l'utente ha spento il flag. `configure` cancella.
     */
    fun setAlarmClockFallback(enabled: Boolean): Boolean =
        AlarmClockFallback.configure(appContext, enabled)

    /** True se il sistema ci lascia programmare sveglie ESATTE. False non è un
     *  errore: `scheduleWake` ricade su una sveglia inesatta. */
    fun canScheduleExactAlarms(): Boolean = Companion.canScheduleExactAlarms(appContext)
}
