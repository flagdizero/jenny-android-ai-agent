package com.flagdizero.jenny

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Context
import android.content.Intent
import android.graphics.Path
import android.graphics.Rect
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Le azioni di Jenny sulle altre app: aprire, toccare, scrivere, scorrere,
 * tornare indietro.
 *
 * ## Prima il nodo, poi il dito
 *
 * Un tocco è `performAction(ACTION_CLICK)` sul nodo dell'elemento: non passa
 * dalle coordinate, quindi non può cadere sulla pillola di Jenny né su una
 * finestra arrivata un attimo prima. Il gesto a coordinate ([gesture]) serve
 * dove un nodo non basta: lo scorrimento libero, e il tocco su un elemento che
 * rifiuta il clic.
 *
 * ## Dopo ogni azione si aspetta
 *
 * [settle] aspetta che lo schermo smetta di cambiare: [HandsService] segna
 * l'ora dell'ultimo evento, e 250 ms di silenzio valgono «fermo». Misurato su
 * `jenny_s22` il 22/11/2026: 120 ms per un clic che non cambia pagina,
 * ~450 ms per Indietro, ~1,1 s per uno scorrimento.
 *
 * ## Cosa non fa
 *
 * Non scrive nei campi password, mai: le credenziali le mette l'utente. La
 * conferma delle azioni gravi (paga, elimina…) non sta qui ma in Python, che
 * decide prima di chiamare: qui si esegue.
 */
object PhoneActions {

    private const val SETTLE_QUIET_MS = 250L
    private const val SETTLE_MAX_MS = 3_000L
    private const val GESTURE_WAIT_MS = 2_000L
    private const val MAX_WAIT_MS = 5_000L

    /** Quanto si aspetta che un'app appena lanciata dia il primo segno. */
    private const val OPEN_WAIT_MS = 3_000L

    /** Le azioni che cadono dentro l'app davanti. */
    private val TOUCHING = setOf("tap", "long_press", "type", "scroll", "swipe")

    /**
     * Quando Jenny ha agito l'ultima volta: la pillola lo usa per distinguere
     * il suo tocco da quello dell'utente.
     *
     * Vale per **ogni** azione, non solo per i gesti a coordinate: anche un
     * `performAction(ACTION_CLICK)` fa arrivare alla pillola un «tocco fuori».
     * Misurato su `jenny_s22` il 22/11/2026 sul tasto «Add alarm» dell'Orologio
     * — il primo passo di una sveglia vera finiva in pausa da solo.
     */
    @Volatile
    var injectedAt = 0L
        private set

    /** Quando è cominciata l'azione in corso: [settle] aspetta un evento da lì. */
    @Volatile
    private var actionStart = 0L

    private fun markOwn() {
        injectedAt = SystemClock.elapsedRealtime()
    }

    /** L'utente ha toccato lo schermo mentre Jenny guidava ([PhonePill]): i
     *  passi aspettano che dica di continuare. */
    @Volatile
    var paused = false
        private set

    /** L'utente ha premuto «Ferma»: nessun altro passo, fino alla prossima guida. */
    @Volatile
    private var stopped = false

    /** Quanto un passo aspetta «Continua» prima di arrendersi. */
    private const val PAUSE_WAIT_MS = 45_000L

    /** Una guida nuova comincia: niente pausa né stop rimasti da prima. */
    fun begin() {
        paused = false
        stopped = false
    }

    fun pause() {
        paused = true
    }

    fun resume() {
        paused = false
    }

    /** «Ferma»: il passo in corso finisce, i successivi no. */
    fun stop() {
        stopped = true
        paused = false
    }

    /** Prima di ogni passo: fermo se l'utente ha detto ferma, in attesa se ha
     *  toccato lo schermo. */
    private fun checkpoint() {
        val t0 = SystemClock.elapsedRealtime()
        while (paused && !stopped && SystemClock.elapsedRealtime() - t0 < PAUSE_WAIT_MS) {
            Thread.sleep(100)
        }
        if (stopped) throw Refusal("the user pressed Stop: do nothing else on the phone")
        if (paused) {
            throw Refusal(
                "the user touched the screen, so I paused, and they have not said to continue. " +
                    "Stop here and tell them where you got to",
            )
        }
    }

    /**
     * Esegue i passi in ordine e si ferma al primo che fallisce. Torna un
     * array JSON, una voce per passo eseguito: `{action, ok, detail}`.
     */
    fun run(svc: AccessibilityService, steps: JSONArray): JSONArray {
        val results = JSONArray()
        for (i in 0 until steps.length()) {
            val step = steps.optJSONObject(i) ?: JSONObject()
            val action = step.optString("action")
            val detail = try {
                checkpoint()
                if (action in TOUCHING && PhoneTree.secureNow(svc)) {
                    throw Refusal(
                        "this app hides its screen from screenshots (banking, passwords, incognito), " +
                            "so I neither read it nor act on it. Tell the user; back and home still work",
                    )
                }
                markOwn()
                actionStart = SystemClock.elapsedRealtime()
                one(svc, action, step).also { markOwn() }
            } catch (e: Refusal) {
                results.put(JSONObject().put("action", action).put("ok", false).put("detail", e.message))
                break
            }
            results.put(JSONObject().put("action", action).put("ok", true).put("detail", detail))
        }
        return results
    }

    private class Refusal(message: String) : Exception(message)

    private fun one(svc: AccessibilityService, action: String, step: JSONObject): String = when (action) {
        "open_app" -> openApp(svc, step.optString("app"))
        "open_link" -> openLink(svc, step.optString("url"))
        "tap" -> tap(svc, node(step), long = false)
        "long_press" -> tap(svc, node(step), long = true)
        "type" -> type(svc, step)
        "scroll" -> scroll(svc, step)
        "swipe" -> swipe(svc, step.optString("direction", "up"))
        "back" -> global(svc, AccessibilityService.GLOBAL_ACTION_BACK)
        "home" -> global(svc, AccessibilityService.GLOBAL_ACTION_HOME)
        "recents" -> global(svc, AccessibilityService.GLOBAL_ACTION_RECENTS)
        "notifications" -> global(svc, AccessibilityService.GLOBAL_ACTION_NOTIFICATIONS)
        "wait" -> {
            val ms = step.optLong("ms", 1_000).coerceIn(0, MAX_WAIT_MS)
            Thread.sleep(ms)
            "waited ${ms}ms"
        }
        else -> throw Refusal("unknown action \"$action\"")
    }

    /** Il nodo del `ref` del passo, ancora vivo, o un rifiuto che dice perché. */
    private fun node(step: JSONObject): PhoneTree.Element {
        val ref = step.optString("ref")
        if (ref.isEmpty()) throw Refusal("this action needs a ref from phone_snapshot")
        val snap = PhoneTree.current() ?: throw Refusal("no snapshot yet: run phone_snapshot first")
        val version = ref.substringBefore(':').toIntOrNull()
        if (version != snap.version) {
            throw Refusal("ref \"$ref\" is from an older snapshot, the current one is v${snap.version} — the screen has changed, use the refs of the latest snapshot")
        }
        val element = snap.elements[ref] ?: throw Refusal("ref \"$ref\" is unknown in this snapshot")
        if (!element.node.refresh()) throw Refusal("ref \"$ref\" is no longer on screen")
        return element
    }

    private fun tap(svc: AccessibilityService, element: PhoneTree.Element, long: Boolean): String {
        val what = if (long) AccessibilityNodeInfo.ACTION_LONG_CLICK else AccessibilityNodeInfo.ACTION_CLICK
        var target: AccessibilityNodeInfo? = element.node
        while (target != null && !(if (long) target.isLongClickable else target.isClickable)) {
            target = target.parent
        }
        if (target != null && target.performAction(what)) {
            markOwn()
            return "${describe(element)} — settled in ${settle()}ms"
        }
        // Il nodo non accetta il clic: si tocca dov'è.
        val b = Rect().also { element.node.getBoundsInScreen(it) }
        if (b.isEmpty) throw Refusal("${describe(element)} cannot be tapped: it has no place on screen")
        PhonePill.dodge(b.exactCenterX(), b.exactCenterY())
        val path = Path().apply { moveTo(b.exactCenterX(), b.exactCenterY()) }
        if (!gesture(svc, path, if (long) 700 else 60)) {
            throw Refusal("${describe(element)} did not take the tap")
        }
        return "${describe(element)} (by touch) — settled in ${settle()}ms"
    }

    private fun type(svc: AccessibilityService, step: JSONObject): String {
        val text = step.optString("text")
        val element = if (step.optString("ref").isEmpty()) null else node(step)
        val target = element?.node ?: svc.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
            ?: throw Refusal("no text field has the cursor: tap one first, or give its ref")
        if (target.isPassword) {
            throw Refusal("this is a password field. The user enters credentials on the phone, not me")
        }
        if (!target.isEditable) throw Refusal("that element is not a text field")
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        if (!target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) {
            throw Refusal("the field did not take the text")
        }
        var note = "typed ${text.length} characters"
        if (step.optBoolean("enter", false)) {
            val sent = target.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)
            note += if (sent) ", then Enter" else ", but the field has no Enter action — tap its send or search button"
        }
        return "$note — settled in ${settle()}ms"
    }

    private fun scroll(svc: AccessibilityService, step: JSONObject): String {
        val down = step.optString("direction", "down") != "up"
        val action = if (down) AccessibilityNodeInfo.ACTION_SCROLL_FORWARD else AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
        if (step.optString("ref").isNotEmpty()) {
            val element = node(step)
            if (element.node.performAction(action)) return "scrolled ${describe(element)} — settled in ${settle()}ms"
            throw Refusal("${describe(element)} does not scroll that way: it is at the end")
        }
        // Senza ref: come un dito che trascina a metà schermo.
        return swipe(svc, if (down) "up" else "down")
    }

    private fun swipe(svc: AccessibilityService, direction: String): String {
        val dm = svc.resources.displayMetrics
        val w = dm.widthPixels.toFloat()
        val h = dm.heightPixels.toFloat()
        val path = Path()
        when (direction) {
            "down" -> { path.moveTo(w / 2, h * 0.35f); path.lineTo(w / 2, h * 0.75f) }
            "left" -> { path.moveTo(w * 0.8f, h / 2); path.lineTo(w * 0.2f, h / 2) }
            "right" -> { path.moveTo(w * 0.2f, h / 2); path.lineTo(w * 0.8f, h / 2) }
            "up" -> { path.moveTo(w / 2, h * 0.75f); path.lineTo(w / 2, h * 0.35f) }
            else -> throw Refusal("unknown direction \"$direction\": use up, down, left or right")
        }
        if (!gesture(svc, path, 300)) throw Refusal("the swipe was cancelled")
        return "swiped $direction — settled in ${settle()}ms"
    }

    private fun global(svc: AccessibilityService, action: Int): String {
        if (!svc.performGlobalAction(action)) throw Refusal("Android refused that")
        return "done — settled in ${settle()}ms"
    }

    private fun openApp(svc: AccessibilityService, app: String): String {
        if (app.isBlank()) throw Refusal("open_app needs the app's name or package")
        val pkg = resolvePackage(svc, app) ?: throw Refusal("no installed app is called \"$app\"")
        val launch = svc.packageManager.getLaunchIntentForPackage(pkg)
            ?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            ?: throw Refusal("\"$app\" has no screen to open")
        if (!Intents.tryStart(svc, launch, "PhoneActions", pkg)) throw Refusal("Android refused to open \"$app\"")
        return "opened $pkg — settled in ${settle(OPEN_WAIT_MS)}ms"
    }

    private fun openLink(svc: AccessibilityService, url: String): String {
        if (url.isBlank()) throw Refusal("open_link needs a url")
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (!Intents.tryStart(svc, intent, "PhoneActions", "link")) throw Refusal("no app opens that link")
        return "opened the link — settled in ${settle(OPEN_WAIT_MS)}ms"
    }

    /**
     * Da un nome scritto dal modello («WhatsApp», «orologio») al pacchetto:
     * prima il pacchetto esatto, poi l'etichetta uguale, poi quella che lo
     * contiene. Serve anche a Python, che chiede il consenso per pacchetto.
     */
    fun resolvePackage(context: Context, app: String): String? {
        val pm = context.packageManager
        val wanted = app.trim()
        if (pm.getLaunchIntentForPackage(wanted) != null) return wanted
        val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val apps = pm.queryIntentActivities(launcher, 0).map {
            it.activityInfo.packageName to it.loadLabel(pm).toString()
        }
        return apps.firstOrNull { it.second.equals(wanted, ignoreCase = true) }?.first
            ?: apps.firstOrNull { it.second.contains(wanted, ignoreCase = true) }?.first
    }

    private fun describe(element: PhoneTree.Element): String =
        "${element.role} \"${if (element.password) "" else element.name.take(40)}\""

    /** Inietta un gesto e aspetta che finisca. Vero se è arrivato in fondo. */
    private fun gesture(svc: AccessibilityService, path: Path, durationMs: Long): Boolean {
        val done = CountDownLatch(1)
        var completed = false
        injectedAt = SystemClock.elapsedRealtime()
        val sent = svc.dispatchGesture(
            GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(path, 0, durationMs))
                .build(),
            object : AccessibilityService.GestureResultCallback() {
                override fun onCompleted(g: GestureDescription?) {
                    completed = true
                    done.countDown()
                }

                override fun onCancelled(g: GestureDescription?) {
                    done.countDown()
                }
            },
            null,
        )
        if (!sent) return false
        done.await(durationMs + GESTURE_WAIT_MS, TimeUnit.MILLISECONDS)
        injectedAt = SystemClock.elapsedRealtime()
        return completed
    }

    /**
     * Aspetta che lo schermo cambi e poi si fermi; torna quanto ha aspettato.
     *
     * Prima aspetta il **primo** evento dopo l'inizio dell'azione, fino a
     * *expectChangeMs*: senza, «nessun evento da 250 ms» era vero subito — gli
     * eventi vecchi erano già lontani — e un'app appena lanciata risultava
     * «ferma» dopo 120 ms, con sotto ancora la schermata di prima (misurato su
     * `jenny_s22` il 22/11/2026 su tredici app). Poi aspetta il silenzio.
     */
    fun settle(expectChangeMs: Long = 500L): Long {
        val t0 = SystemClock.elapsedRealtime()
        while (HandsService.lastEventAt < actionStart &&
            SystemClock.elapsedRealtime() - t0 < expectChangeMs
        ) {
            Thread.sleep(30)
        }
        Thread.sleep(120)
        while (SystemClock.elapsedRealtime() - t0 < SETTLE_MAX_MS + expectChangeMs) {
            if (SystemClock.elapsedRealtime() - HandsService.lastEventAt >= SETTLE_QUIET_MS) break
            Thread.sleep(40)
        }
        return SystemClock.elapsedRealtime() - t0
    }
}
