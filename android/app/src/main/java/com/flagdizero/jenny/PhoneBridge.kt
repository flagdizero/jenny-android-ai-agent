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
            .toString()
    }

    /** La lettura dello schermo in JSON ([PhoneTree.Snapshot.json]); `null` a
     *  servizio spento. */
    fun snapshot(filter: String?, maxChars: Int): String? {
        val svc = HandsService.instance ?: return null
        return PhoneTree.snapshot(svc, filter, maxChars).json()
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

    /** Il pacchetto dell'app che il modello ha chiamato per nome, o `null`. */
    fun resolveApp(app: String): String? = PhoneActions.resolvePackage(appContext, app)

    /** L'etichetta di un pacchetto, per le domande all'utente. */
    fun appLabel(packageName: String): String = try {
        val pm = appContext.packageManager
        pm.getApplicationLabel(pm.getApplicationInfo(packageName, 0)).toString()
    } catch (e: Exception) {
        packageName
    }

    /** Apre la pagina di sistema in cui il servizio si accende. */
    fun openSettings(): Boolean = Intents.tryStart(
        appContext,
        Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        "PhoneBridge",
        "accessibility settings",
    )
}
