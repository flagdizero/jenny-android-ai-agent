package com.flagdizero.jenny

import android.app.KeyguardManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.os.PowerManager
import android.os.SystemClock
import android.provider.Settings
import android.util.Base64
import android.util.Log
import org.json.JSONArray

/**
 * Guida le mani di Jenny da adb, per provarle senza passare dal modello.
 *
 * ```
 * adb shell am broadcast -n com.flagdizero.jenny/.DebugPhoneReceiver \
 *     -a com.flagdizero.jenny.DEBUG_PHONE --es cmd dump
 * ```
 *
 * Comandi (`--es cmd`): `snapshot [--es arg <filtro>]`, `do --es b64 <passi JSON in
 * base64>`, `press --es arg <etichetta di un tasto della pillola>`, `drive [--es arg off]`,
 * `ask` (una domanda di prova sulla pillola), `shot` (la foto dello schermo, nella
 * cache dell'app), `name --es arg <x,y>` (cosa c'è sotto un punto dello schermo), `state`.
 * Gli esiti vanno nel log sotto `PhoneDebug`.
 *
 * Nelle build non debuggabili non fa niente; il manifest lo protegge con `DUMP`,
 * che ha la shell e nessuna app normale.
 */
class DebugPhoneReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val debuggable = (context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0
        if (!debuggable) return
        val cmd = intent.getStringExtra("cmd").orEmpty()
        // `b64` porta un argomento che la shell rovinerebbe (il JSON dei passi).
        val arg = intent.getStringExtra("b64")
            ?.let { String(Base64.decode(it, Base64.DEFAULT)) }
            ?: intent.getStringExtra("arg").orEmpty()
        val app = context.applicationContext
        val svc = HandsService.instance
        if (svc == null) {
            Log.i(TAG, "$cmd: hands service is off")
            return
        }
        if (cmd == "drive") {
            // La pillola di guida senza un turno: per provare pausa e tocchi.
            val shown = if (arg == "off") PhonePill.done(null) else PhonePill.driving("0".repeat(32), "debug")
            Log.i(TAG, "drive $arg: $shown")
            return
        }
        if (cmd == "ask") {
            // Una domanda senza un turno: per provare che una pausa non la copre.
            // Pillola e notifica, come fa il consenso: per provare anche che
            // con la pillola a schermo la notifica arriva in silenzio.
            val cid = "0".repeat(32)
            val shown = if (arg == "off") {
                NotifierBridge(app).clearPeerNotice("confirm", cid)
                PhonePill.askDone()
            } else {
                val quiet = PhonePill.canShow(app)
                NotifierBridge(app).postPhoneAsk(cid, "debug question", "«debug»", true)
                Log.i(TAG, "ask: notification posted, quiet=$quiet")
                PhonePill.ask(cid, "debug question", "«debug»", true)
            }
            Log.i(TAG, "ask $arg: $shown")
            return
        }
        if (cmd == "press") {
            Log.i(TAG, "press $arg: ${PhonePill.debugPress(arg)}")
            return
        }
        if (cmd == "state") {
            state(app)
            return
        }
        Thread({ run(svc, cmd, arg) }, "jenny-phone-debug").start()
    }

    private fun run(svc: HandsService, cmd: String, arg: String) {
        try {
            when (cmd) {
                "snapshot" -> {
                    val t0 = SystemClock.elapsedRealtime()
                    val snap = PhoneTree.snapshot(svc, arg.ifEmpty { null }, 4_000)
                    Log.i(
                        TAG,
                        "snapshot v${snap.version}: app=${snap.app} (${snap.packageName}) title=${snap.title} " +
                            "secure=${snap.secure} refs=${snap.elements.size} chars=${snap.text.length} " +
                            "truncated=${snap.truncated} ms=${SystemClock.elapsedRealtime() - t0}",
                    )
                    snap.text.lines().forEach { Log.i(TAG, "| $it") }
                }
                "do" -> {
                    val results = PhoneActions.run(svc, JSONArray(arg))
                    for (i in 0 until results.length()) Log.i(TAG, "step $i: ${results.getJSONObject(i)}")
                }
                "name" -> {
                    // Che cosa c'è sotto un punto dello schermo, come lo vede il consenso.
                    val (x, y) = arg.split(",").map { it.trim().toInt() }
                    Log.i(TAG, "name at ($x, $y): \"${PhoneTree.nameAt(svc, x, y)}\"")
                }
                "shot" -> {
                    val path = java.io.File(svc.cacheDir, "debug-shot.jpg").path
                    Log.i(TAG, "shot: ${PhoneShot.take(svc, path, 1024)} at $path")
                }
                else -> Log.i(TAG, "unknown cmd $cmd")
            }
        } catch (e: Throwable) {
            Log.w(TAG, "$cmd failed", e)
        }
    }

    private fun state(app: Context) {
        val km = app.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
        val pm = app.getSystemService(Context.POWER_SERVICE) as PowerManager
        Log.i(
            TAG,
            "state: interactive=${pm.isInteractive} keyguardLocked=${km.isKeyguardLocked} " +
                "deviceLocked=${km.isDeviceLocked} secure=${km.isDeviceSecure} " +
                "overlay=${Settings.canDrawOverlays(app)} hands=${HandsService.instance != null}",
        )
    }

    private companion object {
        const val TAG = "PhoneDebug"
    }
}
