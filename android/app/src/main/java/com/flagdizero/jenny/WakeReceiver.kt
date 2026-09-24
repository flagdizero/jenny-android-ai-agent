package com.flagdizero.jenny

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Unico destinatario delle sveglie armate da `PowerBridge.scheduleWake`, più il
 * cambio di stato del permesso sulle sveglie esatte.
 *
 * Un receiver solo e non uno per tipo di sveglia: i PendingIntent nascono tutti
 * in `PowerBridge.wakePendingIntent`, e sono già distinti fra loro dal request
 * code — che è anche la loro identità agli occhi dell'AlarmManager. Aggiungere
 * receiver significherebbe duplicare quella fabbrica, la voce nel manifest e la
 * logica "rimetti su il service", con tre copie libere di divergere; qui invece
 * il request code arriva in `EXTRA_REQUEST_CODE` e lo `when` sotto è l'unico
 * punto in cui si decide cosa farne.
 *
 * Avviare un FGS da background è vietato da Android 12, ma una sveglia
 * `setExactAndAllowWhileIdle` mette l'app in allowlist temporanea proprio per
 * la durata di questa callback — ed è il motivo per cui il riavvio passa da un
 * alarm e non da un job differito.
 *
 * Il ri-armo delle catene al cambio di permesso ha DUE porte d'ingresso, e non
 * è ridondanza gratuita: la broadcast di sistema (qui sotto) e
 * `syncExactAlarmState`, che non dipende da nessuna broadcast. Vedi lì il
 * perché la prima, da sola, non basta.
 */
class WakeReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == ACTION_EXACT_ALARM_STATE_CHANGED) {
            // Questa broadcast il sistema la manda SOLO sul fronte di concessione
            // (mai alla revoca): il fatto stesso che sia arrivata è già la notizia,
            // quindi qui non si confronta nulla con lo stato memorizzato e si
            // ri-arma e basta. Lo stato si aggiorna dopo, così il ricontrollo
            // opportunistico non rifà lo stesso lavoro al primo foreground.
            rearmChains(context, reason = "permission-broadcast")
            rememberExactAlarmState(context, PowerBridge.canScheduleExactAlarms(context))
            return
        }
        // Sveglia senza request code: PendingIntent creato da una versione
        // precedente e sopravvissuto all'aggiornamento dell'APK. Trattarla come
        // recovery è il ripiego che non perde nulla (rimette su il service).
        when (intent.getIntExtra(PowerBridge.EXTRA_REQUEST_CODE, RECOVERY_FALLBACK)) {
            Watchdog.REQUEST_CODE -> Watchdog.onAlarm(context)
            // Senza questo ramo la sveglia da 8 ore cadrebbe nell'`else` e
            // verrebbe scambiata per un tick di lavoro: il gateway ripartirebbe
            // anche, ma nessuno ri-armerebbe la catena — e `setAlarmClock` è
            // one-shot, quindi l'ultima rete morirebbe al primo scatto, in
            // silenzio e proprio nel caso in cui è l'unica rimasta.
            AlarmClockFallback.REQUEST_CODE -> AlarmClockFallback.onAlarm(context)
            PowerBridge.REQUEST_CODE_SERVICE_RESTART ->
                GatewayStarter.ensureUp(context, reason = "wake-alarm/restart")
            // Tutto il resto è una sveglia di LAVORO armata da Python (request
            // code sotto 9000): una scadenza cron da onorare adesso.
            // `wakeTick`: v. `GatewayStarter.ensureUp`. Niente `alarmFallback`:
            // siamo già dentro la finestra di allowlist di una sveglia.
            else -> GatewayStarter.ensureUp(context, reason = "wake-alarm/work", wakeTick = true)
        }
    }

    companion object {
        private const val TAG = "Jenny"

        /** Request code fittizio per una sveglia senza extra: vedi `onReceive`. */
        private const val RECOVERY_FALLBACK = PowerBridge.REQUEST_CODE_SERVICE_RESTART

        /** `AlarmManager.ACTION_SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED`
         *  (API 31+), scritta per esteso come le azioni QUICKBOOT di
         *  `BootReceiver`: la stessa stringa deve comparire nel manifest, e su
         *  API precedenti la broadcast semplicemente non arriva mai. */
        private const val ACTION_EXACT_ALARM_STATE_CHANGED =
            "android.app.action.SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED"

        /** Stesso file di preferenze di `Watchdog` e `AlarmClockFallback`: una
         *  sola `SharedPreferences` per app, chiavi nuove e basta. */
        private const val PREFS_NAME = "jenny"
        private const val KEY_EXACT_ALARMS_GRANTED = "exactAlarmsGranted"

        /** Valore che significa "non l'ho mai guardato": distinto sia da
         *  concesso sia da negato, perché al primo giro non sappiamo con quale
         *  natura sono state armate le sveglie già in coda. */
        private const val STATE_UNKNOWN = -1

        /**
         * Ricontrolla il permesso sulle sveglie esatte e ri-arma le catene se è
         * cambiato da quando l'abbiamo guardato l'ultima volta. Ritorna `true`
         * se ha ri-armato.
         *
         * ESISTE PERCHÉ LA BROADCAST NON BASTA, e non è pessimismo generico:
         * verificato sul Titan 2 (Android 16, targetSdk 34) il 2026-08-09.
         * L'utente ha concesso il permesso dalla schermata "Sveglie e
         * promemoria"; `canScheduleExactAlarms()` è passata a true, ma nessuna
         * broadcast è arrivata e ogni sveglia in coda è rimasta inesatta fino al
         * riavvio successivo del gateway. La causa non è il nostro
         * `exported="false"` (vedi il manifest: `BootReceiver`, anch'esso non
         * esportato, riceve regolarmente BOOT_COMPLETED dal sistema su questo
         * stesso telefono) né un filtro sbagliato (il resolver di sistema elenca
         * `.WakeReceiver` sotto quell'azione). È che la broadcast la manda un
         * unico punto di `AlarmManagerService`, su un FRONTE calcolato da uno
         * stato cachato lato system_server, con una regola diversa da quella che
         * decide `canScheduleExactAlarms()`: se quel fronte non viene visto, la
         * notifica non parte e nessuno la rimanda mai più. Dal lato app non c'è
         * modo di renderla affidabile — solo di non dipenderne.
         *
         * Da qui il ricontrollo: costa una lettura di prefs e una chiamata
         * all'AlarmManager, non dipende da nulla che debba arrivarci, e ri-arma
         * solo quando lo stato è DAVVERO cambiato — quindi non c'è ritmo da
         * frenare e non può accumulare sveglie (vedi `rearmChains`).
         *
         * Copre anche il verso opposto, che la broadcast non copre per progetto:
         * alla REVOCA l'AlarmManager butta via le sveglie esatte in coda senza
         * avvisare nessuno, e senza questo giro le catene resterebbero morte
         * fino al prossimo avvio del gateway.
         *
         * Al primissimo giro (`STATE_UNKNOWN`) ri-arma una volta: non sappiamo
         * con quale natura sono state programmate le sveglie già in coda, e il
         * costo di scoprirlo è più alto di quello di rifarle.
         */
        internal fun syncExactAlarmState(context: Context, reason: String): Boolean {
            val appContext = context.applicationContext
            val granted = PowerBridge.canScheduleExactAlarms(appContext)
            val current = if (granted) 1 else 0
            val remembered = try {
                prefs(appContext).getInt(KEY_EXACT_ALARMS_GRANTED, STATE_UNKNOWN)
            } catch (e: Exception) {
                Log.w(TAG, "Could not read the remembered exact alarm state", e)
                STATE_UNKNOWN
            }
            if (remembered == current) {
                return false
            }
            rearmChains(appContext, "$reason/exact-alarms-changed(seen=$remembered)")
            rememberExactAlarmState(appContext, granted)
            return true
        }

        /**
         * Ri-arma ogni catena che il cambio di permesso può aver spento.
         *
         * Idempotente per costruzione, e non per disciplina del chiamante: ogni
         * `arm` passa da `PowerBridge.wakePendingIntent`, che con lo stesso
         * request code ritorna lo STESSO `PendingIntent`, e `AlarmManager.set*`
         * su un PendingIntent già in coda cancella la sveglia precedente prima
         * di mettere la nuova. Ri-armare sostituisce, non somma — che è proprio
         * quello che serve qui: le sveglie già in coda sono nate inesatte e
         * quella natura se la portano dietro, l'unico modo di renderle esatte è
         * riprogrammarle.
         *
         * La sveglia di cron non la si può ri-armare da Kotlin (solo Python sa
         * quand'è la prossima scadenza), quindi si sveglia il gateway con un
         * tick: il giro di `_arm_timer` che ne segue riprograma l'alarm da sé.
         */
        private fun rearmChains(context: Context, reason: String) {
            val appContext = context.applicationContext
            val granted = PowerBridge.canScheduleExactAlarms(appContext)
            Log.i(TAG, "Re-arming alarm chains (reason=$reason, canSchedule=$granted)")
            Watchdog.arm(appContext)
            // Anche la sveglia-sveglia, che senza permesso era degradata a
            // inesatta (`setAlarmClock` quel permesso lo pretende: vedi il
            // `catch` di `PowerBridge.scheduleAlarmClock`). È la rete che
            // guadagna di più dal ri-armo — a permesso concesso torna a essere
            // quella che nessun gestore energetico OEM osa sopprimere — e costa
            // al massimo lo slittamento di un controllo che gira tre volte al
            // giorno.
            AlarmClockFallback.arm(appContext)
            GatewayStarter.ensureUp(appContext, reason = "rearm-chains", wakeTick = true)
        }

        /** Parcheggia lo stato appena osservato. `apply()` e non `commit()`:
         *  gira sul main thread (foreground dell'app) e perderlo non rompe
         *  nulla — al giro dopo il confronto fallisce di nuovo e si ri-arma una
         *  volta di troppo, che è un no-op. */
        private fun rememberExactAlarmState(context: Context, granted: Boolean) {
            try {
                prefs(context.applicationContext).edit()
                    .putInt(KEY_EXACT_ALARMS_GRANTED, if (granted) 1 else 0)
                    .apply()
            } catch (e: Exception) {
                Log.w(TAG, "Could not remember the exact alarm state", e)
            }
        }

        private fun prefs(appContext: Context) =
            appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }
}
