package com.flagdizero.jenny

import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import org.json.JSONArray
import org.json.JSONObject

/**
 * Bridge delle mani di Jenny, esposto a Python via Chaquopy
 * (`jclass("com.flagdizero.jenny.PhoneBridge")`) — stesso pattern di
 * [ScreenBridge], mai istanziato da Kotlin.
 *
 * Qui non c'è nessuna decisione: se un'azione si può fare lo decide Python
 * (il consenso, le azioni gravi, da dove arriva la richiesta) **prima** di
 * chiamare. Il bridge legge e esegue.
 *
 * Niente salto sul main thread: [PhoneTree] e [PhoneActions] parlano con il
 * servizio di accessibilità, che si può chiamare da qualunque thread, e
 * aspettano (lo schermo che si ferma, un gesto che finisce) — cosa che sul
 * main non si deve fare. Python chiama già da un thread suo.
 */
class PhoneBridge(context: Context) {

    private val appContext = context.applicationContext

    private companion object {
        /** Tetto del wake lock: una guida non dura più di così. */
        const val WAKE_MAX_MS = 10 * 60_000L
        val wakeLockGuard = Any()

        /** Preso da [wake] quando lo schermo era spento, lasciato da [sleepAgain]. */
        var wakeLock: PowerManager.WakeLock? = null
    }

    /** `"on"`, `"off"` (servizio spento in Accessibilità) o `"unsupported"`
     *  (Android sotto il 14: manca lo screenshot di finestra che dice se
     *  un'app vieta di essere letta). */
    fun state(): String = when {
        Build.VERSION.SDK_INT < 34 -> "unsupported"
        HandsService.instance != null -> "on"
        else -> "off"
    }

    /** Schermo acceso? Bloccato? Con un blocco sicuro? In JSON. */
    fun screen(): String {
        val km = appContext.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
        val pm = appContext.getSystemService(Context.POWER_SERVICE) as PowerManager
        return JSONObject()
            .put("on", pm.isInteractive)
            .put("locked", km.isKeyguardLocked)
            .put("secure", km.isDeviceSecure)
            .put("deviceLocked", km.isDeviceLocked)
            .toString()
    }

    /**
     * Rende il telefono usabile se si può fare senza credenziali: accende lo
     * schermo e toglie un blocco che non chiede niente (nessuno, scorrimento, o
     * un blocco sicuro tenuto aperto da Android in un luogo o con un
     * dispositivo fidato).
     *
     * Torna `"ready"` (era già acceso e libero), `"woken"` (l'ha acceso
     * Jenny: [sleepAgain] lo rispegne), `"locked"` (serve PIN, sequenza o
     * impronta: Jenny non li conosce e non li conserva) o `"failed"`.
     *
     * Misurato su `jenny_s22` il 22/11/2026: senza blocco, a schermo acceso si
     * è già nell'app; col blocco a scorrimento basta uno swipe verso l'alto.
     */
    @Suppress("DEPRECATION")
    fun wake(): String {
        val km = appContext.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
        val pm = appContext.getSystemService(Context.POWER_SERVICE) as PowerManager
        if (pm.isInteractive && !km.isKeyguardLocked) return "ready"
        if (km.isDeviceLocked) return "locked"
        val svc = HandsService.instance ?: return "failed"
        synchronized(wakeLockGuard) {
            if (wakeLock == null) {
                // Il wake lock «di schermo» è deprecato ma è l'unico che accende
                // lo schermo senza una schermata nostra davanti; tiene anche
                // acceso finché Jenny lavora. Ha comunque una scadenza.
                wakeLock = pm.newWakeLock(
                    PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP,
                    "jenny:phone-use",
                ).also { it.acquire(WAKE_MAX_MS) }
            }
        }
        if (!waitUntil(2_000) { pm.isInteractive }) return "failed"
        // Lo swipe che toglie il blocco a volte arriva mentre lo schermo sta
        // ancora accendendosi e va a vuoto: su `jenny_s22` il 22/11/2026 il
        // primo tentativo falliva una volta su due. Si riprova.
        repeat(3) {
            if (!km.isKeyguardLocked) return@repeat
            Thread.sleep(400)
            PhoneActions.run(svc, JSONArray().put(JSONObject().put("action", "swipe").put("direction", "up")))
            waitUntil(2_500) { !km.isKeyguardLocked }
        }
        if (km.isKeyguardLocked) return "failed"
        return "woken"
    }

    /** Rispegne lo schermo, se a svegliarlo era stata [wake]. Vero se l'ha fatto. */
    fun sleepAgain(): Boolean {
        val held = synchronized(wakeLockGuard) { wakeLock.also { wakeLock = null } } ?: return false
        runCatching { if (held.isHeld) held.release() }
        return HandsService.instance
            ?.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_LOCK_SCREEN)
            ?: false
    }

    private fun waitUntil(timeoutMs: Long, done: () -> Boolean): Boolean {
        val t0 = android.os.SystemClock.elapsedRealtime()
        while (android.os.SystemClock.elapsedRealtime() - t0 < timeoutMs) {
            if (done()) return true
            Thread.sleep(80)
        }
        return done()
    }

    /** La lettura dello schermo in JSON ([PhoneTree.Snapshot.json]); `null` a
     *  servizio spento. */
    fun snapshot(filter: String?, maxChars: Int): String? {
        val svc = HandsService.instance ?: return null
        return PhoneTree.snapshot(svc, filter, maxChars).json()
    }

    /** La foto dello schermo in *path* ([PhoneShot.take]), in JSON; `null` se
     *  non si è potuta fare. */
    fun screenshot(path: String, maxSide: Int): String? {
        val svc = HandsService.instance ?: return null
        return PhoneShot.take(svc, path, maxSide)?.toString()
    }

    /** Il nome di quello che sta nel punto (x, y) dell'ultima foto; vuoto se
     *  non ha un nome, `null` se non c'è una foto o il servizio è spento. */
    fun nameAt(x: Double, y: Double): String? {
        val svc = HandsService.instance ?: return null
        if (!PhoneShot.ready) return null
        val (sx, sy) = PhoneShot.toScreen(x, y)
        return PhoneTree.nameAt(svc, sx.toInt(), sy.toInt())
    }

    /** Esegue *stepsJson* (un array di passi) e torna gli esiti in JSON; `null`
     *  a servizio spento. */
    fun act(stepsJson: String): String? {
        val svc = HandsService.instance ?: return null
        return PhoneActions.run(svc, JSONArray(stepsJson)).toString()
    }

    /** Il pacchetto dell'app in primo piano, o `null` a servizio spento. È
     *  quella su cui cadrebbe un tocco: Python ci chiede il consenso. */
    fun foreground(): String? =
        HandsService.instance?.rootInActiveWindow?.packageName?.toString()
            // Jenny non usa se stessa: le sue finestre non le legge nemmeno.
            ?.takeIf { it != appContext.packageName }

    /** Il pacchetto dell'app che il modello ha chiamato per nome, o `null`. */
    fun resolveApp(app: String): String? = PhoneActions.resolvePackage(appContext, app)

    /** L'etichetta di un pacchetto, per le domande all'utente. */
    fun appLabel(packageName: String): String = try {
        val pm = appContext.packageManager
        pm.getApplicationLabel(pm.getApplicationInfo(packageName, 0)).toString()
    } catch (e: Exception) {
        packageName
    }

    /** La domanda *cid* sulla pillola ([PhonePill.ask]). */
    fun pillAsk(cid: String, title: String, body: String, always: Boolean, where: String): Boolean =
        PhonePill.ask(cid, title, body, always, where)

    /** La domanda ha avuto risposta: la pillola torna alla guida o sparisce. */
    fun pillAskDone(): Boolean = PhonePill.askDone()

    /** «Jenny sta usando il telefono» sulla pillola, con «Ferma» per la guida *id*. */
    fun pillDriving(id: String, text: String): Boolean = PhonePill.driving(id, text)

    /** Fine della guida; con *text* resta l'esito per qualche secondo. */
    fun pillDone(text: String?): Boolean = PhonePill.done(text)

    /** «Ferma»: i passi non ancora fatti non si fanno. */
    fun stop(): Boolean {
        PhoneActions.stop()
        return true
    }

    /** Riporta davanti la chat di Jenny, a lavoro finito. */
    fun openChat(): Boolean =
        Intents.tryStart(appContext, MainActivity.openChatIntent(appContext), "PhoneBridge", "the chat")

    /** Apre la pagina di sistema in cui il servizio si accende. */
    fun openSettings(): Boolean = Intents.tryStart(
        appContext,
        Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        "PhoneBridge",
        "accessibility settings",
    )
}
