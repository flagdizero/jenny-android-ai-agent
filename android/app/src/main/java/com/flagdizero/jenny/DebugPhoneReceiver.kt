package com.flagdizero.jenny

import android.app.KeyguardManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.graphics.Color
import android.graphics.PixelFormat
import android.os.PowerManager
import android.os.SystemClock
import android.provider.Settings
import android.util.Base64
import android.util.Log
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.TextView
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
 * base64>`, `state`, `wake`, `launch --es arg <pacchetto>`, `pill --es arg app|a11y|off`.
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
        if (cmd == "pill") {
            pill(app, arg)
            return
        }
        if (cmd == "wake") {
            wake(app)
            return
        }
        if (cmd == "launch") {
            val launch = app.packageManager.getLaunchIntentForPackage(arg)
                ?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            val ok = launch != null && Intents.tryStart(app, launch, TAG, arg)
            Log.i(TAG, "launch $arg ok=$ok")
            return
        }
        val svc = HandsService.instance
        if (svc == null) {
            Log.i(TAG, "$cmd: hands service is off")
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

    @Suppress("DEPRECATION")
    private fun wake(app: Context) {
        val pm = app.getSystemService(Context.POWER_SERVICE) as PowerManager
        val lock = pm.newWakeLock(
            PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP,
            "jenny:phone-spike",
        )
        lock.acquire(10_000)
        state(app)
    }

    /** Una pillola di prova: finestra piccola, non a schermo intero, che sente
     *  i tocchi fuori da sé. `app` = overlay dell'app, `a11y` = overlay del
     *  servizio di accessibilità. */
    private fun pill(app: Context, kind: String) {
        pillView?.let { (wm, view) ->
            runCatching { wm.removeView(view) }
            pillView = null
        }
        if (kind == "off") {
            Log.i(TAG, "pill off")
            return
        }
        val host: Context = if (kind == "a11y") HandsService.instance ?: return else app
        val wm = host.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val view = TextView(host).apply {
            text = "Jenny · Ferma"
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.argb(230, 30, 30, 30))
            setPadding(40, 24, 40, 24)
            setOnClickListener { Log.i(TAG, "pill: tapped") }
            setOnTouchListener { _: View, e: MotionEvent ->
                if (e.actionMasked == MotionEvent.ACTION_OUTSIDE) {
                    val since = SystemClock.elapsedRealtime() - PhoneActions.injectedAt
                    Log.i(TAG, "pill: outside touch, ${since}ms after the last injected gesture")
                }
                false
            }
        }
        val type = if (kind == "a11y") {
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
        } else {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        }
        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            type,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
            y = 160
        }
        val ok = runCatching { wm.addView(view, params) }
            .onFailure { Log.w(TAG, "pill $kind failed", it) }.isSuccess
        if (ok) pillView = wm to view
        Log.i(TAG, "pill $kind shown=$ok")
    }

    private companion object {
        const val TAG = "PhoneDebug"
        var pillView: Pair<WindowManager, View>? = null
    }
}
