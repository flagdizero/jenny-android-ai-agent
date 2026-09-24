package com.flagdizero.jenny

import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Il punto da cui parte `startForegroundService(GatewayService)`: le reti di
 * sicurezza, il boot, il watchdog, le sveglie e l'activity passano tutte da qui.
 * L'unica eccezione è `ReplyReceiver`, che consegna una risposta dalla notifica:
 * extra suoi, lock di handoff senza `EXTRA_WAKE_TICK`, e un fallimento che deve
 * tornare all'utente (`postReplyFailure`) invece di finire in un log.
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
     * una allowlist temporanea. Una sveglia `setExactAndAllowWhileIdle` quella
     * allowlist la concede per la durata della sua callback: rientrare da lì è
     * l'unico modo affidabile di riprovare. Corto di proposito — la sveglia
     * serve a cambiare *contesto*, non a rimandare il recupero.
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
     * foreground dell'app. Chi arriva da una sveglia la allowlist ce l'ha già, e
     * riarmarne un'altra sullo stesso request code non farebbe che spostare in
     * avanti la rete di sicurezza del service.
     */
    fun ensureUp(
        context: Context,
        reason: String,
        wakeTick: Boolean = false,
        alarmFallback: Boolean = false,
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
            if (alarmFallback) {
                val armed = PowerBridge.scheduleWake(
                    appContext,
                    System.currentTimeMillis() + ALARM_FALLBACK_DELAY_MS,
                    PowerBridge.REQUEST_CODE_SERVICE_RESTART,
                )
                Log.i(TAG, "Recovery alarm armed after refused FGS start (ok=$armed)")
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
