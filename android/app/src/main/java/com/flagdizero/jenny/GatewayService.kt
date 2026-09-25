package com.flagdizero.jenny

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.graphics.BitmapFactory
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlin.concurrent.thread

/**
 * Foreground service hosting the jenny gateway thread.
 *
 * Runs independently of MainActivity's lifecycle so the Python gateway (and
 * therefore the WebSocket session) survives the screen turning off or the
 * app being backgrounded. startForeground() must be called before any
 * potentially slow work (Chaquopy/Python startup) to avoid
 * ForegroundServiceDidNotStartInTimeException on Android 14+.
 */
class GatewayService : Service() {

    companion object {
        private const val TAG = "Jenny"
        private const val NOTIFICATION_CHANNEL_ID = "jenny_gateway"
        private const val NOTIFICATION_ID = 1
        // Distinto dai request code degli alert (NotifierBridge usa tag.hashCode()),
        // così i due PendingIntent non si sovrascrivono a vicenda.
        private const val OPEN_UI_REQUEST_CODE = 1001

        /** Quanto aspettare prima di riprovare ad alzare il gateway dopo che il
         *  service è morto. Abbastanza lungo da lasciar finire al sistema il
         *  ciclo di kill (e da non entrare in loop con un OEM che ci uccide
         *  subito), abbastanza corto da non lasciare l'utente senza agente. */
        private const val RESTART_DELAY_MS = 30_000L

        /** Extra con cui `WakeReceiver` segnala che questo avvio è il tick di
         *  una sveglia di lavoro, non un semplice "assicurati che sia su". */
        const val EXTRA_WAKE_TICK = "com.flagdizero.jenny.extra.WAKE_TICK"

        /** Testo scritto dall'utente nella tendina, da consegnare al gateway.
         *  Lo mette `ReplyReceiver`. */
        const val EXTRA_REPLY_TEXT = "com.flagdizero.jenny.extra.REPLY_TEXT"

        /** Tag dell'alert da cui è partita la risposta: serve solo a rimetterlo
         *  nella notifica di mancata consegna, così il "Rimanda" sa a quale
         *  avviso apparteneva. */
        const val EXTRA_REPLY_SOURCE_TAG = "com.flagdizero.jenny.extra.REPLY_SOURCE_TAG"

        /** Le due superfici native da cui può entrare del testo dell'utente.
         *
         *  Valori di protocollo, non etichette: Python li riconosce per stringa
         *  (`_CHANNEL_BY_SOURCE` in `jenny/runtime/native_input.py`, elenco
         *  chiuso) e da lì decide **dove torna la risposta** — un alert per la
         *  tendina, il fumetto per la mascotte. Una stringa che diverge non
         *  rompe la compilazione: fa rifiutare ogni messaggio di quella
         *  superficie, in silenzio. È il legame che
         *  `tests/runtime/test_native_input.py::TestBoundaryWithKotlin` controlla
         *  leggendo questo file. */
        const val NATIVE_SOURCE_NOTIFICATION = "notification"
        const val NATIVE_SOURCE_FLOATING = "floating"

        /** Quanto insistere per consegnare una risposta prima di arrendersi.
         *
         *  Sta **sotto** i 30 s di `PowerBridge.HANDOFF_TIMEOUT_MS` di
         *  proposito: il lock di handoff è ciò che tiene la CPU sveglia mentre
         *  il gateway si alza, e insistere oltre la sua scadenza vorrebbe dire
         *  ritentare su un device che nel frattempo può essersi risospeso.
         *
         *  È un budget e non un numero di tentativi perché la variabile vera è
         *  il tempo di avvio a freddo di Chaquopy, non quante volte si bussa. */
        private const val REPLY_DELIVERY_BUDGET_MS = 25_000L

        /** Lo stesso budget, per la mascotte, ed è molto più corto.
         *
         *  Non è la stessa situazione: si risponde a un alert di ore prima, con
         *  il gateway quasi sempre da rimettere in piedi, mentre la mascotte
         *  esiste **perché** Python ha appena chiamato `setEnabled` — cioè il
         *  gateway è su. Qui il retry copre una finestra stretta (un riavvio del
         *  gateway nello stesso processo), e dall'altra parte c'è un utente che
         *  guarda: cinque secondi di attesa muta sono già tanti, venticinque
         *  sarebbero una mascotte rotta. */
        private const val FLOATING_DELIVERY_BUDGET_MS = 5_000L

        /**
         * Consegna *text* al gateway insistendo finché il budget regge.
         *
         * Il cuore condiviso dalle due superfici native. Sta nel companion — e
         * non sull'istanza — perché la mascotte lo chiama dal proprio
         * controller, che vive nel processo del service ma non ha un'istanza di
         * `Service` a portata di mano.
         *
         * **Va chiamata da un thread di lavoro, mai dal main Looper**:
         * attraversa il confine Chaquopy (JNI + GIL) e può restare bloccata
         * quanto il GIL resta preso da un turno in corso. I due chiamanti
         * aprono entrambi un `thread {}` e questa funzione dorme dentro.
         *
         * Il backoff parte corto (mezzo secondo) perché a gateway già vivo la
         * consegna riesce al primo colpo, e si allarga fino a due secondi perché
         * oltre quel punto quello che si aspetta è l'avvio di Chaquopy, che non
         * si affretta bussando più spesso.
         */
        private fun deliverWithRetry(
            text: String,
            source: String,
            sourceTag: String?,
            budgetMs: Long,
        ): Boolean {
            val deadline = SystemClock.elapsedRealtime() + budgetMs
            var wait = 500L
            var attempts = 0
            while (true) {
                attempts++
                if (tryDeliverNativeText(text, source, sourceTag)) {
                    Log.i(TAG, "Native text delivered (source=$source, attempt $attempts)")
                    return true
                }
                if (SystemClock.elapsedRealtime() + wait >= deadline) {
                    Log.i(TAG, "Native text not delivered within the budget " +
                        "(source=$source, $attempts attempts)")
                    return false
                }
                Thread.sleep(wait)
                wait = minOf(wait * 2, 2_000L)
            }
        }

        /**
         * Un tentativo di consegna. `false` se il gateway non è ancora agganciato.
         *
         * *sourceTag* è il tag della notifica da cui è partita la risposta e
         * serve a una cosa sola: tornare indietro. Python lo rimette nei
         * metadata del turno e il canale ci riporta sopra la risposta, così il
         * discorso resta nella scheda in cui è cominciato. La mascotte non ne ha
         * uno — la sua finestra è una sola — e passa `null`.
         *
         * Non solleva: qualunque errore è un "non adesso", e chi chiama decide
         * se riprovare. `Python.isStarted()` falso significa che `startGateway`
         * sta alzando il runtime in questo momento — è la condizione che il
         * retry esiste per aspettare, non un guasto.
         */
        private fun tryDeliverNativeText(
            text: String,
            source: String,
            sourceTag: String?,
        ): Boolean = try {
            if (!Python.isStarted()) {
                false
            } else {
                Python.getInstance()
                    .getModule("jenny.runtime.native_input")
                    .callAttr("on_native_text", text, source, sourceTag)
                    .toBoolean()
            }
        } catch (e: Exception) {
            Log.i(TAG, "Native text delivery attempt failed: ${e.javaClass.simpleName}")
            false
        }

        /**
         * Consegna il testo scritto nel campo della mascotte flottante.
         *
         * Gemello di `deliverNativeText` ma **senza le sue due code**, e
         * nessuna delle due mancanze è una svista:
         *
         * * niente `releaseHandoffLock`: quel lock lo prende `WakeReceiver` per
         *   tenere sveglia la CPU mentre il gateway si alza, e questa strada non
         *   passa di là. Rilasciarlo qui toglierebbe un lock che appartiene a
         *   qualcun altro;
         * * niente notifica di fallimento: chi ha scritto sta guardando la
         *   mascotte, quindi il fallimento glielo dice lei (`onResult`), che è
         *   dove sta già guardando. Una notifica sarebbe una seconda superficie
         *   per dire una cosa che si vede già.
         */
        fun deliverFloatingText(text: String, onResult: (Boolean) -> Unit) {
            thread(name = "jenny-floating-text") {
                val ok = try {
                    deliverWithRetry(text, NATIVE_SOURCE_FLOATING, null, FLOATING_DELIVERY_BUDGET_MS)
                } catch (e: InterruptedException) {
                    Thread.currentThread().interrupt()
                    Log.i(TAG, "Floating text delivery interrupted")
                    false
                } catch (e: Exception) {
                    Log.e(TAG, "Floating text delivery failed", e)
                    false
                }
                onResult(ok)
            }
        }

        /** Pausa fra l'uscita di `run_gateway` e il tentativo di rilanciarlo
         *  nello stesso thread. Allineata a `RETRY_DELAY_S` di
         *  `jenny/android_entry.py`: è lo stesso ordine di grandezza di attesa
         *  che il lato Python si concede fra due tentativi. */
        private const val SELF_RESTART_DELAY_MS = 5_000L

        /**
         * Distanza minima fra due auto-riavvii del gateway nello stesso thread.
         *
         * È l'unica cosa che separa "recupero immediato" da "loop di riavvii".
         * Quando `run_gateway` esce ha già bruciato i suoi tre tentativi
         * (`MAX_RETRIES` in `android_entry.py`), quindi un guasto che si
         * ripresenta subito non è un incidente: è deterministico, e rilanciarlo
         * a raffica brucerebbe batteria senza mai risalire. Sopra questa soglia
         * il thread si arrende e lascia il campo al watchdog, che è lento ma
         * passa da un ciclo di service completo. Tarata sull'intervallo base
         * del watchdog (15 min): sotto quel valore staremmo solo anticipando un
         * controllo che sarebbe arrivato comunque.
         */
        private const val MIN_SELF_RESTART_INTERVAL_MS = 15 * 60_000L

        /**
         * Il `Service` è istanziato in questo processo?
         *
         * Static di proposito: `Watchdog` deve poterlo chiedere dalla callback
         * di una sveglia, dove non c'è nessun binder e nessuna istanza a portata
         * di mano. Non è mai stale in senso pericoloso — vive nel processo, e se
         * il processo muore muore con lui — ma proprio per questo non dice nulla
         * su cosa girava PRIMA della morte del processo: quel pezzo lo copre il
         * battito su SharedPreferences (vedi `Watchdog.isGatewayAlive`).
         *
         * `@Volatile` perché scritto sul main Looper e letto dal thread di una
         * broadcast.
         */
        @Volatile
        var isRunning: Boolean = false
            private set

        /**
         * Il thread del gateway è vivo? Non un semplice "l'abbiamo avviato": se
         * `run_gateway` esce (retry esauriti, crash del loop) il thread muore e
         * il processo resta su con un service vivo e nessun agente dietro.
         * Rileggere il thread invece di ricordarsi un booleano è ciò che rende
         * riparabile quel caso: il prossimo `onStartCommand` — quello del
         * watchdog — lo rilancia. `@Volatile`: scritto dal thread del gateway,
         * letto dal main.
         *
         * Sta nel companion, cioè vale per PROCESSO e non per istanza del
         * service, per due motivi che vanno insieme:
         *
         * * il thread del gateway sopravvive alla morte del `Service` (è un
         *   thread nudo del processo, non un suo componente). Con il campo
         *   sull'istanza, un `onDestroy` + ricreazione a processo vivo —
         *   riavvio sticky, sveglia di recupero — ripartiva da `null` e
         *   lanciava un SECONDO `run_gateway` nello stesso interprete: due loop
         *   asyncio, due WebSocket sulla stessa porta. Esattamente il guasto
         *   che `startGateway` dichiara di voler evitare, per una strada che
         *   il suo lock non copriva;
         * * `Watchdog` deve poter chiedere questo stato da una callback di
         *   sveglia, dove non c'è nessuna istanza a portata di mano (vedi
         *   `isGatewayThreadDead`).
         */
        @Volatile
        private var gatewayThread: Thread? = null

        /** Lock di `startGateway`. Nel companion perché lo stato che protegge è
         *  del processo: sincronizzare sull'istanza lascerebbe due istanze del
         *  service — che nel tempo esistono davvero — a controllare e assegnare
         *  lo stesso campo senza vedersi. */
        private val startLock = Any()

        /** Quando è avvenuto l'ultimo auto-riavvio in-place del gateway.
         *  Del processo, non del thread: bisogna riconoscere anche il caso in
         *  cui è il watchdog a rifare il thread, che ricrasha subito. */
        @Volatile
        private var lastSelfRestartMs: Long = 0L

        /**
         * Il thread del gateway è PROVATAMENTE uscito?
         *
         * `false` anche quando il thread non è mai partito (`null`): "non lo so"
         * non è "è morto", e al primo avvio — o subito dopo la ricreazione del
         * processo — la domanda giusta la risponde il battito su
         * SharedPreferences, non questo.
         *
         * Il riferimento al thread È il flag: `isAlive` diventa `false` solo
         * quando `run()` è terminato davvero, non serve un booleano parallelo da
         * ricordarsi di azzerare al riavvio (un flag rimasto alzato dopo un
         * riavvio riuscito farebbe riavviare il service ogni tick, per sempre).
         */
        internal val isGatewayThreadDead: Boolean
            get() = gatewayThread?.isAlive == false
    }

    /** Siamo riusciti ad andare in foreground almeno una volta in questa
     *  istanza? Un `startForeground` fallito NON annulla quello riuscito prima:
     *  senza questa distinzione un ri-post rifiutato in `onStartCommand`
     *  farebbe fermare un service che sta funzionando benissimo. */
    private var isForeground = false

    /**
     * Il foreground in corso include il tipo `location`?
     *
     * Serve a non DEGRADARE ciò che già funziona. `startForeground` SOSTITUISCE
     * l'insieme dei tipi, non lo unisce: se il ri-post di `onStartCommand`
     * arriva da background (tick del watchdog, worker periodico) il tipo
     * `location` viene rifiutato, e ripiegare lì su `specialUse` toglierebbe al
     * service l'app-op di posizione che aveva già — cioè romperebbe la
     * geolocalizzazione a schermo spento, che è l'unico motivo per cui quel tipo
     * esiste. Quando l'abbiamo già ottenuto, un rifiuto successivo si ignora e
     * la notifica non si ri-posta affatto.
     */
    private var hasLocationType = false

    override fun onCreate() {
        super.onCreate()
        isRunning = true
        if (!startForegroundCompat(NOTIFICATION_ID, buildNotification())) {
            // Non siamo in foreground e non lo saremo. Fermarsi ADESSO è
            // l'unica uscita che non uccide il processo: un service avviato con
            // `startForegroundService` che non chiama `startForeground` entro il
            // timeout viene abbattuto con
            // ForegroundServiceDidNotStartInTimeException, e con lui se ne va il
            // gateway. Non è una resa: `onDestroy` arma la sveglia a +30s, che
            // rientra da `WakeReceiver` — cioè da un contesto che l'allowlist
            // temporanea ce l'ha davvero.
            Log.e(TAG, "Could not enter foreground: stopping service, retry is armed in onDestroy")
            // Le altre due reti si armano lo stesso, per la ragione scritta
            // sotto: devono esistere proprio quando l'avvio fallisce. Senza
            // `noteAlive`, che qui sarebbe una bugia — non c'è nessun gateway.
            Watchdog.arm(this)
            AlarmClockFallback.arm(this)
            stopSelf()
            return
        }
        // Prima ancora di far partire Python: la catena del watchdog deve
        // esistere anche se l'avvio del gateway fallisce a metà, perché è
        // proprio quello il guasto che deve saper riparare. `Watchdog.arm`
        // legge le impostazioni dalle prefs (default se Python non le ha mai
        // spinte), quindi non ha bisogno che il runtime sia su.
        Watchdog.noteAlive(this)
        Watchdog.arm(this)
        // Accanto al watchdog e per la stessa ragione: la rete deve esistere
        // anche se l'avvio di Python fallisce a metà, quindi si arma prima di
        // `startGateway` e non da Python. `AlarmClockFallback.arm` legge il flag
        // dalle prefs — default acceso, allineato a `PowerConfig` — e se
        // l'utente l'ha spenta si disarma da sé invece di riarmarsi.
        AlarmClockFallback.arm(this)
        startGateway()
        // Un service ricreato con il gateway ancora vivo non ripassa da Python
        // (`startGateway` non rilancia niente), quindi la mascotte flottante
        // smontata in `onDestroy` la rimette su il nativo, se era voluta.
        FloatingOverlayController.onServiceStarted()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // Re-post on every (re)start so a notification permission granted
        // after the initial startForeground() call actually takes effect.
        if (!startForegroundCompat(NOTIFICATION_ID, buildNotification())) {
            // Stesso ragionamento di `onCreate`, e stessa rete. Qui il caso
            // normale è che fossimo GIÀ in foreground da un avvio precedente:
            // `startForegroundCompat` lo sa e ritorna `true`, quindi non si
            // arriva qui per un semplice ri-post rifiutato.
            Log.e(TAG, "Could not enter foreground on start: stopping service")
            stopSelf()
            return START_NOT_STICKY
        }
        isRunning = true
        Watchdog.noteAlive(this)
        // Idempotente: riparte solo se il thread del gateway non c'è più. È il
        // braccio operativo del watchdog — senza, "riavviare il service" su un
        // processo vivo ma con Python morto non riavvierebbe proprio niente.
        startGateway()
        if (intent?.getBooleanExtra(EXTRA_WAKE_TICK, false) == true) {
            deliverWakeTick()
        }
        // `START_STICKY` riconsegna un intent **nullo** a un riavvio di
        // sistema, mai il nostro: una risposta non può quindi essere consegnata
        // due volte perché il service è stato ucciso e rialzato.
        val replyText = intent?.getStringExtra(EXTRA_REPLY_TEXT)
        if (intent != null && !replyText.isNullOrBlank()) {
            deliverNativeText(replyText, intent.getStringExtra(EXTRA_REPLY_SOURCE_TAG))
        }
        return START_STICKY
    }

    /**
     * Consegna il tick di sveglia al loop asyncio del gateway.
     *
     * Su un thread di lavoro e mai sul main Looper: la chiamata attraversa il
     * confine Chaquopy (JNI + acquisizione del GIL) e può restare bloccata
     * quanto il GIL resta preso da un turno in corso — sul main sarebbe un ANR.
     *
     * Non è Kotlin a svegliare il cron: `on_wake_tick` fa solo un
     * `call_soon_threadsafe` sul loop del gateway, che è l'unico modo
     * thread-safe di toccare un `asyncio.Event` da fuori. Se il gateway non è
     * ancora su (`Python.isStarted()` falso, oppure loop non ancora agganciato)
     * il tick si perde di proposito: `startGateway` lo sta alzando adesso e il
     * recupero della scadenza mancata è già lavoro di `CronService.start`.
     */
    private fun deliverWakeTick() {
        thread(name = "jenny-wake-tick") {
            try {
                if (!Python.isStarted()) {
                    Log.i(TAG, "Wake tick dropped: python runtime not started yet")
                    return@thread
                }
                val module = Python.getInstance().getModule("jenny.runtime.power")
                val delivered = module.callAttr("on_wake_tick").toBoolean()
                Log.i(TAG, "Wake tick delivered=$delivered")
            } catch (e: Exception) {
                Log.e(TAG, "Wake tick delivery failed", e)
            } finally {
                // Sempre, e solo qui: da questo punto in poi è il foreground
                // service (e, se serve, il wakelock per-turno di Python) a
                // tenere in piedi il lavoro. Vedi PowerBridge.HANDOFF_LOCK_TAG.
                PowerBridge.releaseHandoffLock()
            }
        }
    }

    /**
     * Consegna al gateway il testo scritto nella tendina, e non lo perde.
     *
     * Gemello di `deliverWakeTick` per la disciplina — thread di lavoro, mai il
     * main Looper, `releaseHandoffLock` in `finally` — ma con una differenza che
     * è tutto il punto della funzione: **un tick di sveglia perso si recupera da
     * sé, le parole dell'utente no.** Il tick lo rimedia il primo giro di
     * `CronService.start`; una risposta scartata perché il gateway stava ancora
     * partendo sparisce, e sparisce proprio nel caso normale — si risponde a un
     * alert di ore prima, quando il gateway è quasi sempre da rimettere in piedi.
     *
     * Da qui i tre esiti, invece dei due del tick:
     *
     * 1. consegnata — `on_native_text` ha accettato e il turno parte;
     * 2. non ancora — si riprova con backoff, finché il budget regge;
     * 3. non ce l'ha fatta — il testo **torna all'utente** con il pulsante per
     *    rimandarlo (`NotifierBridge.postReplyFailure`). Nessun accodamento al
     *    buio: una consegna a sorpresa tre ore dopo sarebbe peggio di nessuna.
     *
     * L'insistenza vera sta in `deliverWithRetry`, condivisa con la mascotte.
     * Qui restano le due cose che valgono solo per la tendina: il rilascio del
     * lock di handoff — che è `WakeReceiver` a prendere per tenere sveglia la
     * CPU mentre il gateway si alza, e che nessun altro percorso possiede — e
     * la notifica di fallimento, che serve perché chi ha scritto dalla tendina
     * non sta guardando nulla e va raggiunto dove scriveva.
     */
    private fun deliverNativeText(text: String, sourceTag: String?) {
        thread(name = "jenny-native-text") {
            var delivered = false
            try {
                delivered = deliverWithRetry(
                    text, NATIVE_SOURCE_NOTIFICATION, sourceTag, REPLY_DELIVERY_BUDGET_MS
                )
            } catch (e: InterruptedException) {
                Thread.currentThread().interrupt()
                Log.i(TAG, "Reply delivery interrupted")
            } catch (e: Exception) {
                Log.e(TAG, "Reply delivery failed", e)
            } finally {
                // Sempre, e solo qui: da questo punto in poi è il foreground
                // service a tenere in piedi il lavoro. Vedi
                // PowerBridge.HANDOFF_LOCK_TAG.
                PowerBridge.releaseHandoffLock()
            }
            if (!delivered) {
                NotifierBridge.postReplyFailure(applicationContext, text, sourceTag)
            }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    /**
     * Ultimo gesto prima di morire: armare una sveglia a +30s che ci rimette in
     * piedi.
     *
     * START_STICKY non basta e non è ridondanza. Copre il caso in cui è il
     * sistema a fermare il SERVICE tenendo vivo il processo; non copre quello
     * che succede davvero sui firmware aggressivi (e questo progetto gira su
     * telefoni-server lasciati nel cassetto), dove a essere ucciso è l'INTERO
     * processo o l'app viene "congelata" dal gestore batteria dell'OEM: lì il
     * riavvio sticky non arriva mai, e senza questa sveglia il gateway resta
     * giù finché l'utente non riapre l'app a mano — cioè finché non si accorge
     * che Jenny ha smesso di rispondere. L'alarm sopravvive al kill perché vive
     * nell'AlarmManager di sistema, non nel nostro processo.
     *
     * Vale anche quando lo stop è VOLUTO (MainActivity.restartApp ferma il
     * service e uccide il processo per applicare un restore): lì la sveglia è
     * innocua, perché a quel punto il gateway è già ripartito e
     * startForegroundService su un service vivo passa solo da onStartCommand.
     */
    override fun onDestroy() {
        // Per primo, prima di qualunque cosa possa sollevare: da qui in avanti
        // il watchdog deve vedere "giù". Lasciarlo a `true` su un service
        // distrutto è l'unico modo in cui il flag statico può mentire.
        isRunning = false
        // La mascotte flottante non sopravvive al gateway che le dà la voce: è
        // una finestra che accetta domande, e senza nessuno che risponda
        // resterebbe a schermo a raccogliere testo per un agente che non c'è.
        // Smontarla qui e non in `stop()` del canale è deliberato — vive nel
        // processo del service, non nel giro dei canali. Si smonta senza
        // dimenticare che era accesa: la rimette su `onCreate`, se il service
        // rinasce (v. FloatingOverlayController.teardown).
        FloatingOverlayController.teardown()
        // Il wakelock della modalità "always" (e con lui la rotazione) si molla
        // SOLO se dietro non è rimasto un gateway vivo.
        //
        // Il motivo per cui il rilascio esiste resta intatto: il lock lo prende
        // Python all'avvio del gateway
        // (jenny/runtime/power.py::apply_service_lock) e solo il service sa di
        // stare morendo, quindi un lock orfano su un processo che sopravvive al
        // service — è il caso di MainActivity.restartApp — terrebbe la CPU
        // accesa a schermo spento senza che nessuno possa più spegnerlo, e il
        // callback di rotazione lasciato armato, peggio ancora, la
        // ri-acquisirebbe.
        //
        // Ma quel ragionamento vale solo se il gateway se ne sta andando con
        // noi. Se il thread è vivo il lock NON è orfano: ha ancora il suo
        // proprietario, che lo vuole. E rilasciarlo lì lo toglieva **per
        // sempre**, perché nulla lo ri-acquisisce: l'unico ri-acquirente è
        // `apply_service_lock`, che gira solo all'avvio di un `run_gateway`
        // fresco, e alla ricreazione del service `startGateway` corto-circuita
        // proprio perché il thread è sopravvissuto. Sintomo: una riga
        // "Service wakelock off (was held=true)" e poi più niente, con cron e
        // heartbeat che ricominciano a slittare in doze profondo — cioè
        // l'esatta proprietà misurata e rilasciata in 0.6.6.
        //
        // A rilasciarlo, nel caso saltato qui, è chi resta: l'uscita del thread
        // del gateway (vedi la coda di `startGateway`), oppure il prossimo
        // `onDestroy` a thread ormai morto.
        val gatewayAlive = synchronized(startLock) { gatewayThread?.isAlive == true }
        if (gatewayAlive) {
            Log.i(TAG, "Service destroyed with a live gateway thread: keeping the service wakelock")
        } else {
            PowerBridge.setServiceLock(this, false, 0)
        }
        // Unico punto di programmazione sveglie del progetto: la logica
        // esatto/inesatto con il suo fallback vive in PowerBridge, non
        // duplicata qui.
        val armed = PowerBridge.scheduleWake(
            this,
            System.currentTimeMillis() + RESTART_DELAY_MS,
            PowerBridge.REQUEST_CODE_SERVICE_RESTART
        )
        Log.i(TAG, "Service destroyed: restart alarm armed=$armed")
        super.onDestroy()
    }

    /**
     * Avvia il runtime Python, una volta sola.
     *
     * Un lock e non il solo `@Volatile` sul campo: "leggi se il thread è
     * vivo, poi assegnane uno nuovo" è un check-then-act, e `@Volatile` rende
     * atomica la singola lettura, non la coppia. Due chiamate che si
     * incrociassero lì in mezzo vedrebbero entrambe "nessun thread" e
     * farebbero partire DUE `run_gateway` nello stesso interprete: due loop
     * asyncio, due WebSocket sulla stessa porta, due scrittori sugli stessi
     * file di sessione. Un guasto intermittente e distruttivo, del tipo che si
     * riproduce solo sul telefono dell'utente.
     *
     * Oggi non può succedere, perché gli unici chiamanti sono `onCreate` e
     * `onStartCommand`, che il sistema consegna sul main Looper e quindi in
     * fila. Ma è un invariante implicito, mantenuto altrove: ora che le reti di
     * sicurezza che finiscono in `startForegroundService` sono sei, basterebbe
     * una futura chiamata da un thread di lavoro — un handler, una callback di
     * WorkManager — per romperlo senza che nulla lo segnali. Il lock costa un
     * confronto su un percorso che gira una volta per avvio, e il blocco tiene
     * solo il controllo e l'assegnazione: `Python.start` gira già dentro il
     * thread nuovo, quindi nessuno resta in attesa del bootstrap di Chaquopy.
     *
     * Il lock è nel companion — non `@Synchronized`, che avrebbe preso `this` —
     * perché lo stato che protegge è del processo: due istanze del service (una
     * distrutta e una ricreata mentre il thread di Python continua a girare)
     * altrimenti si sincronizzerebbero su due monitor diversi, cioè su niente.
     */
    private fun startGateway(): Unit = synchronized(startLock) {
        if (gatewayThread?.isAlive == true) {
            // Entrambi i rami loggano, ed è il punto: senza, in un dump di
            // logcat "il watchdog ha riavviato il service e un gateway nuovo è
            // partito" e "il watchdog ha riavviato il service e non è successo
            // niente" sono indistinguibili — cioè proprio la domanda a cui si
            // sta cercando di rispondere quando si guarda quel dump.
            Log.i(TAG, "startGateway: gateway thread still alive, not spawning a second one")
            return@synchronized
        }
        Log.i(TAG, "startGateway: no live gateway thread, spawning one")

        // Il context dell'applicazione, preso ADESSO: la coda del thread lo usa
        // per mollare il wakelock di servizio e gira quando questo `Service`
        // può essere già stato distrutto.
        val appContext = applicationContext

        // Chaquopy's first-run bootstrap (Python.start) unpacks the stdlib/
        // site-packages and can take several seconds; it must never run on the
        // main/UI Looper thread, or all touch input and WebView compositing
        // stalls for that entire window (same effect as an ANR).
        gatewayThread = thread(name = "jenny-gateway") {
            try {
                // Provider crittografico: UNA SOLA registrazione per processo, e
                // qui. Questo servizio e l'unico ingresso del runtime — ci si
                // arriva sia da MainActivity sia da BootReceiver (avvio headless
                // al boot, senza activity) — e onCreate gira una volta sola.
                // Prima di Python.start: da quel momento in poi qualunque codice
                // Python puo chiedere un Cipher, e il provider deve essere gia
                // quello definitivo.
                //
                // BouncyCastle viene aggiunto in CODA, non in testa, e non e un
                // dettaglio: misurato su questo dispositivo, in posizione 1
                // cambiava il provider di AES/GCM per TUTTA l'app — compreso il
                // container di backup cifrato
                // (jenny/snapshot/crypto_backends/android.py) — passando dal
                // BoringSSL accelerato in hardware al Java puro di BouncyCastle.
                // In coda serve solo Ed25519, che nessun altro provider offre.
                // Il JSON loggato porta `aesGcmUnchanged`: se diventa false,
                // qualcuno ha rimesso l'inserimento in testa e il backup ha
                // cambiato implementazione senza che nessuno lo chiedesse.
                Log.i(TAG, "SSH crypto provider: ${SshBridge.installProvider()}")
                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(applicationContext))
                }
                val py = Python.getInstance()
                val module = py.getModule("jenny.android_entry")
                runGatewayUntilGivenUp(module)
            } catch (e: Exception) {
                // Solo il bootstrap (provider, Python.start, import del modulo)
                // arriva qui: il ciclo di `run_gateway` cattura per conto suo.
                // Un bootstrap di Chaquopy che fallisce non si ritenta in-place
                // — se la prima unpack della stdlib non è riuscita, rifarla
                // subito nello stesso processo non ha nessun motivo di riuscire.
                Log.e(TAG, "Gateway startup failed", e)
            }
            // Si arriva qui solo se il gateway ha smesso di girare: retry
            // esauriti, ritorno pulito, o auto-riavvio rinunciato. Il service
            // resta vivo (e la notifica pure), ma dietro non c'è più nessun
            // agente: senza questo log l'unico sintomo sarebbe il silenzio. Il
            // thread morto è già di per sé il segnale — `startGateway` sa
            // rilanciarlo e `Watchdog` sa vederlo (`isGatewayThreadDead`).
            Log.e(TAG, "Gateway thread exited: no agent behind the service until restarted")
            // Contropartita esatta del rilascio saltato in `onDestroy`: da qui
            // in poi NESSUN gateway vuole più il wakelock di servizio, quindi a
            // mollarlo tocca a chi esce — e con lui alla rotazione, che
            // altrimenti continuerebbe a ri-acquisirlo ogni N minuti su un
            // processo senza agente dietro. Insieme, le due metà tengono
            // l'invariante: il lock è tenuto se e solo se un thread del gateway
            // vivo lo vuole, senza orfani e senza buchi.
            //
            // Incondizionato, non "solo se il service è giù": è l'unico modo di
            // non lasciare aperta la finestra in cui `onDestroy` ci vede ancora
            // `isAlive` (lo siamo, stiamo eseguendo queste righe) e salta il
            // rilascio mentre noi lo saltiamo a nostra volta perché il service
            // sembrava ancora su. Rilasciare qui non apre il buco opposto: da
            // questo punto non gira più nessun gateway, quindi non c'è nessuno
            // che il lock lo voglia. A ri-acquisirlo sarà il prossimo
            // `apply_service_lock`, cioè il prossimo `run_gateway` — quello che
            // rilancerà `startGateway` (auto-riavvio del service o watchdog)
            // trovando finalmente il thread morto.
            //
            // Dentro `startLock` e solo se il record punta ancora a noi: se nel
            // frattempo fosse partito un altro thread, quel lock è suo.
            // Idempotente per costruzione — se non era tenuto,
            // `setServiceLock(false)` è un no-op che logga "was held=false".
            synchronized(startLock) {
                if (gatewayThread === Thread.currentThread()) {
                    PowerBridge.setServiceLock(appContext, false, 0)
                }
            }
        }
    }

    /**
     * Tiene su `run_gateway` finché ha senso ritentare, e ritorna quando non ne
     * ha più.
     *
     * Il rilancio sta QUI, nello stesso thread, e non in un `post` al main
     * Looper: dal main dovremmo richiamare `startGateway`, che troverebbe
     * ancora vivo il thread che sta morendo (`isAlive` è vero fino al ritorno di
     * `run()`) e non farebbe niente — un auto-riavvio che si perde per una gara
     * con sé stesso. Una seconda iterata del ciclo, invece, non ha ordini da
     * rispettare: `gatewayThread` continua a puntare al thread giusto, il
     * segnale `isGatewayThreadDead` resta coerente, e non esiste nessuna
     * finestra in cui due `run_gateway` possano coesistere.
     *
     * Perché serve, visto che il watchdog c'è già: il watchdog lo scopre al
     * prossimo tick — 15-60 minuti — e paga un ciclo di `startForegroundService`
     * intero, che da background su Android 12+ può anche essere rifiutato. Qui
     * il recupero costa cinque secondi e nessun permesso.
     */
    private fun runGatewayUntilGivenUp(module: PyObject) {
        while (true) {
            try {
                module.callAttr("run_gateway", filesDir.absolutePath, applicationContext)
                // RITORNO = uscita PULITA, e i due esiti non vanno scambiati:
                // `jenny/android_entry.py` ritorna solo dopo un `asyncio.run`
                // finito da sé (riga 184, `return  # clean exit`); i retry
                // esauriti fanno `raise` (riga 211) e finiscono nel `catch`.
                // Alle 3 di notte la riga sbagliata manda a cercare un crash
                // che non c'è stato, o viceversa.
                Log.e(TAG, "run_gateway returned: the python side shut down cleanly, no agent left")
            } catch (e: Exception) {
                // `PyException` compresa: un'eccezione che sfugge al lato Python
                // arriva fin qui e vale esattamente quanto un ritorno — in
                // entrambi i casi non c'è più nessun agente. È anche il ramo su
                // cui atterrano i retry esauriti (`raise` finale di
                // `run_gateway`).
                Log.e(TAG, "run_gateway raised: the python side gave up (retries exhausted)", e)
            }
            if (!shouldSelfRestart()) return
            try {
                Thread.sleep(SELF_RESTART_DELAY_MS)
            } catch (e: InterruptedException) {
                Log.w(TAG, "Self-restart wait interrupted: giving up")
                Thread.currentThread().interrupt()
                return
            }
            Log.w(TAG, "Self-restarting the gateway in place")
        }
    }

    /**
     * Rilanciare `run_gateway` adesso, o lasciar morire il thread?
     *
     * Due sole ragioni per dire di sì, ed è deliberato che siano poche: questo
     * è il percorso su cui un loop di riavvii costerebbe la batteria di una
     * notte senza rimettere su niente.
     */
    private fun shouldSelfRestart(): Boolean {
        if (!isRunning) {
            // Il service sta scendendo (o è già sceso). Rilanciare Python qui
            // significherebbe lasciarne uno orfano in un processo senza
            // foreground: a rimettere su tutto è la sveglia di `onDestroy`.
            Log.i(TAG, "Not self-restarting: the service is going down")
            return false
        }
        val now = System.currentTimeMillis()
        val since = now - lastSelfRestartMs
        // `since < 0` = orologio spostato indietro (fuso, sync NTP): l'intervallo
        // non è misurabile, e si sceglie di riprovare. Stessa convenzione di
        // `Watchdog.isGatewayAlive`, e il caso peggiore è UN riavvio in più.
        if (lastSelfRestartMs != 0L && since in 0 until MIN_SELF_RESTART_INTERVAL_MS) {
            Log.e(
                TAG,
                "Gateway died ${since}ms after the last self-restart: giving up, " +
                    "recovery is left to the watchdog"
            )
            return false
        }
        lastSelfRestartMs = now
        return true
    }

    private fun buildNotification(): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        val channel = NotificationChannel(
            NOTIFICATION_CHANNEL_ID,
            getString(R.string.gateway_channel_name),
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = getString(R.string.gateway_channel_description)
            setSound(null, null)
            enableVibration(false)
        }
        manager.createNotificationChannel(channel)

        return NotificationCompat.Builder(this, NOTIFICATION_CHANNEL_ID)
            .setContentTitle(getString(R.string.gateway_notification_title))
            .setContentText(getString(R.string.gateway_notification_text))
            .setSmallIcon(R.drawable.ic_stat_jenny)
            .setLargeIcon(BitmapFactory.decodeResource(resources, R.drawable.ic_notification_large))
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(openUiIntent())
            .build()
    }

    /**
     * Tap sulla notifica → apre Jenny. È l'unica via di rientro sempre visibile
     * quando l'app non è il launcher attivo, e senza questo intent la notifica
     * persistente non fa assolutamente niente al tocco.
     *
     * Intent esplicito e senza categoria: MainActivity è `singleTask` e riporta
     * a galla il task esistente via `onNewIntent`, che simula la pressione di
     * Home (tornando in chat) solo per gli intent con `CATEGORY_HOME`. Toccare
     * la notifica lascia quindi l'utente dov'era.
     */
    private fun openUiIntent(): PendingIntent {
        val intent = Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return PendingIntent.getActivity(
            this,
            OPEN_UI_REQUEST_CODE,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    /**
     * Porta il service in foreground. Ritorna se al ritorno CI SIAMO — non se
     * questo tentativo è riuscito: un rifiuto non annulla un foreground già
     * attivo, e il chiamante deve fermarsi solo nel primo caso.
     *
     * **Il tipo `location` non si può decidere con `checkSelfPermission`**, ed è
     * il bug che ha ucciso il processo il 2026-08-09 alle 01:50:42, al primo
     * avvio dopo un aggiornamento dell'APK (record `data_app_crash` nel dropbox
     * del dispositivo: `uidState: RCVR`, `getFgsAllowWiu=DENIED`,
     * `startForegroundCount=0`, stack dentro `startForegroundCompat` chiamato da
     * `onCreate`). ACCESS_FINE_LOCATION è un permesso "mentre l'app è in uso":
     * `checkSelfPermission` risponde GRANTED sempre, ma per un FGS di tipo
     * `location` Android 14 pretende in più che il chiamante sia in uno stato
     * ELEGGIBILE, cioè che l'app-op while-in-use sia attivo adesso. Un avvio a
     * freddo da broadcast non lo è mai — `MY_PACKAGE_REPLACED`,
     * `BOOT_COMPLETED`, sveglia del watchdog, worker: lì il processo è in stato
     * "receiver", non foreground. E l'allowlist temporanea che concede l'AVVIO
     * del FGS **non** concede la capability while-in-use: sono due controlli
     * distinti, e il dump di ActivityManager li stampa come due righe diverse
     * (`getFgsAllowStart=PACKAGE_REPLACED` accanto a `getFgsAllowWiu=DENIED`).
     *
     * Conseguenza di lasciarla scappare: `SecurityException` non catturata
     * dentro `onCreate` → `RuntimeInit$KillApplicationHandler` → morte
     * dell'intero processo. Il "Timeout executing service ... waited 20001ms"
     * che compare in logcat venti secondi dopo NON è un ANR da main thread
     * bloccato: è ActivityManager che scade sul `executeNesting` di un service
     * il cui processo era già morto — infatti la riga accanto è "Crashing app
     * skipping ANR".
     *
     * Nessun pre-controllo è migliore del tentativo: l'app-op si risolve contro
     * lo stato di processo dell'istante, quindi qualunque check anticipato
     * sarebbe comunque in corsa con la `startForeground` vera. Si prova, e si
     * ripiega su `specialUse` — il gateway vive lo stesso, perde solo la
     * posizione a schermo spento finché non si torna in uno stato eleggibile.
     *
     * Il recupero del tipo `location` resta quello di prima e continua a
     * funzionare: `MainActivity.startGatewayAndLoad` chiama
     * `startForegroundService` a ogni apertura dell'app, cioè da uno stato TOP,
     * e questo ri-post lo riottiene.
     */
    private fun startForegroundCompat(id: Int, notification: Notification): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && hasLocationPermission()) {
            try {
                startForeground(
                    id,
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE or
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION,
                )
                isForeground = true
                hasLocationType = true
                return true
            } catch (e: SecurityException) {
                // Riga singola e senza stack trace: è l'esito ATTESO di ogni
                // avvio headless, non un guasto, e una traccia identica a ogni
                // tick del watchdog seppellirebbe i problemi veri. Stessa scelta
                // già fatta in `PowerBridge.scheduleAlarmClock` per le sveglie
                // esatte negate.
                Log.i(TAG, "FGS location type refused (caller not in a while-in-use state)")
                // Vedi `hasLocationType`: ripiegare qui declasserebbe un
                // foreground che il tipo ce l'ha già.
                if (hasLocationType) return true
            }
        }
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(id, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
            } else {
                startForeground(id, notification)
            }
            isForeground = true
            hasLocationType = false
            true
        } catch (e: Exception) {
            // Anche il ripiego può essere rifiutato: da Android 12 la stessa
            // `startForeground` lancia ForegroundServiceStartNotAllowedException
            // se l'allowlist che aveva autorizzato l'avvio è scaduta nel
            // frattempo (quella di `MY_PACKAGE_REPLACED` dura 20 secondi). È
            // l'ultimo punto in cui possiamo evitare che diventi un crash.
            Log.e(TAG, "startForeground refused", e)
            isForeground
        }
    }

    private fun hasLocationPermission(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED
}
