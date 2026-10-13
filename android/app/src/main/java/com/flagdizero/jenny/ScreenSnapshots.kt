package com.flagdizero.jenny

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Rect
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.accessibility.AccessibilityWindowInfo
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Lo schermo com'era quando l'utente ha chiamato Jenny dalla mascotte.
 *
 * ## Una foto al tocco, e nient'altro
 *
 * [capture] parte da `FloatingOverlayController.expand()` — il tocco che apre
 * la chat flottante — e legge in background l'app che stava sotto: testo,
 * titolo, indirizzo se è un browser, le immagini e uno screenshot della sola
 * finestra di quell'app. Il risultato resta **in memoria**, uno solo alla
 * volta, finché non lo sostituisce il tocco dopo o non scade ([TTL_MS]). Su
 * disco non va niente finché Jenny non lo chiede col tool `screen`, e anche
 * allora solo lo screenshot o l'immagine che ha chiesto.
 *
 * Jenny quindi vede **solo quello che c'era quando l'hai chiamata**: non può
 * guardare lo schermo da sola, non da un promemoria, non da Telegram. Quel
 * vincolo lo tiene anche Python (il tool esiste solo nei turni del fumetto),
 * ma nasce qui: senza il tocco non c'è niente da leggere.
 *
 * ## Perché lo screenshot è della finestra e non del display
 *
 * `takeScreenshotOfWindow` (API 34) fotografa la sola finestra dell'app: la
 * mascotte, la barra di stato e la tastiera — che sono finestre loro — restano
 * fuori, e non serve correre contro l'animazione della chat che si apre.
 * Misurato su `jenny_s22`: 10-23 ms, contro i 90-260 del display intero, che la
 * mascotte invece la includeva. Sotto API 34 la funzione non c'è: il servizio
 * si accende ma la lettura si dichiara non supportata ([supported]).
 *
 * ## FLAG_SECURE: se l'app non si lascia fotografare, non si legge
 *
 * Android blocca i pixel di una finestra sicura (banche, password manager,
 * schede in incognito) ma **non** il suo albero di accessibilità: in incognito
 * lo screenshot torna `ERROR_TAKE_SCREENSHOT_SECURE_WINDOW` e il testo si legge
 * lo stesso, tutto. La regola qui è nostra: una finestra che rifiuta lo
 * screenshot si butta intera, testo compreso. Così le app che proteggono il
 * loro contenuto restano protette senza una lista da tenere aggiornata.
 */
object ScreenSnapshots {

    private const val TAG = "ScreenSnapshots"

    /** Quanto vive una lettura. Abbastanza per una conversazione nel fumetto,
     *  non abbastanza perché resti in giro un pomeriggio. */
    private const val TTL_MS = 10 * 60_000L

    /** Attesa massima dello screenshot di finestra (misurato: ~20 ms). */
    private const val SHOT_TIMEOUT_MS = 2_000L

    /** Quanto la chat flottante aspetta la lettura prima di aprirsi comunque.
     *  Misurato: ~0,3 s per una pagina da 24k caratteri. Oltre il tetto la
     *  lettura continua, ma la chat che si apre copre l'app e quel che resta
     *  da leggere può arrivare a pezzi (v. `FloatingOverlayController.expand`). */
    private const val CAPTURE_BUDGET_MS = 700L

    /** Quanto può durare la visita dell'albero, dall'inizio della lettura: il
     *  resto del tetto serve allo screenshot e all'indirizzo. */
    private const val RENDER_BUDGET_MS = 550L

    /** I pacchetti Chromium e non solo: la barra degli indirizzi ha un id
     *  diverso in ogni browser. Basta il suffisso per tutti i Chromium. */
    private val URL_BAR_IDS = setOf(
        "mozac_browser_toolbar_url_view", // Firefox
        "location_bar_edit_text", // Samsung Internet
    )

    class Snapshot(
        val id: String,
        val takenAtMs: Long,
        val packageName: String,
        val app: String,
        val title: String?,
        val url: String?,
        val secure: Boolean,
        val text: String,
        val truncated: Boolean,
        val images: List<ScreenText.Image>,
        val windowBounds: Rect,
        val screenshot: Bitmap?,
    ) {
        fun summaryJson(): String = JSONObject().apply {
            put("id", id)
            put("takenAt", takenAtMs)
            put("app", app)
            put("package", packageName)
            title?.let { put("title", it) }
            url?.let { put("url", it) }
            put("secure", secure)
            put("chars", text.length)
            put("images", images.size)
            put("screenshot", screenshot != null)
        }.toString()

        fun contentJson(): String = JSONObject(summaryJson()).apply {
            put("text", text)
            put("truncated", truncated)
            put("images", JSONArray().apply {
                images.forEachIndexed { i, img ->
                    put(JSONObject().apply {
                        put("n", i + 1)
                        put("label", img.label)
                        img.url?.let { put("url", it) }
                        put("onScreen", !img.bounds.isEmpty)
                    })
                }
            })
        }.toString()
    }

    @Volatile
    private var service: AccessibilityService? = null

    private val lock = Any()
    private var latest: Snapshot? = null

    /** L'ultima lettura è scaduta ([TTL_MS]) senza che un tocco ne prendesse
     *  un'altra: Jenny deve poterlo dire, invece di credere lo schermo spento. */
    private var expired = false
    private var pending: CountDownLatch? = null
    private val worker = Executors.newSingleThreadExecutor { Thread(it, "jenny-screen") }

    /** Le callback dello screenshot non possono arrivare su [worker]: è il
     *  thread che le sta aspettando. Ne serve uno a parte. */
    private val callbacks = Executors.newSingleThreadExecutor { Thread(it, "jenny-screen-shot") }

    /** La lettura esiste su questo telefono? Serve `takeScreenshotOfWindow`. */
    fun supported(): Boolean = Build.VERSION.SDK_INT >= 34

    /** Il servizio è acceso dall'utente in Accessibilità? */
    fun connected(): Boolean = service != null

    fun attach(svc: AccessibilityService) {
        service = svc
    }

    fun detach(svc: AccessibilityService) {
        if (service === svc) service = null
        clear()
    }

    /** Butta la lettura in memoria, se c'è. */
    fun clear() = synchronized(lock) {
        latest = null
        expired = false
    }

    /**
     * Legge lo schermo, in background, e poi chiama *then* sul main thread —
     * una volta sola: a lettura finita, o allo scadere di [CAPTURE_BUDGET_MS]
     * se la lettura si attarda. Chiamata al tocco che apre la chat flottante,
     * che si apre in *then*: v. `FloatingOverlayController.expand` per il
     * perché dell'ordine. Non blocca mai il chiamante.
     *
     * A servizio spento (o sotto API 34) *then* parte subito, e l'eventuale
     * lettura vecchia si butta: il tocco nuovo dice che lo schermo è un altro.
     */
    fun capture(context: Context, then: () -> Unit) {
        val main = Handler(Looper.getMainLooper())
        val svc = service
        if (svc == null || !supported()) {
            clear()
            main.post(then)
            return
        }
        val own = context.packageName
        val done = CountDownLatch(1)
        val fired = AtomicBoolean(false)
        val proceed = Runnable { if (fired.compareAndSet(false, true)) then() }
        synchronized(lock) {
            latest = null
            expired = false
            pending = done
        }
        main.postDelayed(proceed, CAPTURE_BUDGET_MS)
        worker.execute {
            try {
                val snap = read(svc, own)
                synchronized(lock) { if (pending === done) latest = snap }
            } catch (e: Throwable) {
                Log.w(TAG, "Screen capture failed", e)
            } finally {
                done.countDown()
                main.post(proceed)
            }
        }
    }

    /**
     * L'ultima lettura, aspettando fino a *timeoutMs* se è ancora in corso.
     * Chiamata dal thread che consegna il testo del fumetto, mai dal main.
     */
    fun await(timeoutMs: Long): Snapshot? {
        val waiting = synchronized(lock) { pending }
        waiting?.await(timeoutMs, TimeUnit.MILLISECONDS)
        return current()
    }

    /**
     * Il riassunto da mandare col testo del fumetto quando una lettura non c'è:
     * `{"off": true}` se il servizio è spento, `{"expired": true}` se l'ultima
     * lettura è scaduta. Così Jenny dice cosa fare invece di un vago «non vedo
     * niente» — o di un falso «lo schermo è spento» per una lettura solo vecchia
     * (visti tutti e due su `jenny_s22` il 13/10/2026). `null` negli altri casi
     * — Android sotto il 14, o una lettura che non ha trovato nessuna app sotto.
     */
    fun offSummary(): String? = when {
        !supported() -> null
        !connected() -> JSONObject().put("off", true).toString()
        synchronized(lock) { expired } -> JSONObject().put("expired", true).toString()
        else -> null
    }

    /** L'ultima lettura ancora valida, o `null`. Una lettura scaduta si butta
     *  qui, screenshot compreso: non resta in memoria fino al tocco dopo. */
    fun current(): Snapshot? = synchronized(lock) {
        val snap = latest ?: return@synchronized null
        if (System.currentTimeMillis() - snap.takenAtMs < TTL_MS) return@synchronized snap
        latest = null
        expired = true
        null
    }

    /** La lettura con quell'id, se è ancora quella corrente. */
    fun get(id: String): Snapshot? = current()?.takeIf { it.id == id }

    private fun read(svc: AccessibilityService, own: String): Snapshot? {
        val t0 = SystemClock.elapsedRealtime()
        val window = targetWindow(svc, own) ?: run {
            Log.i(TAG, "No app window to read")
            return null
        }
        val root = window.root ?: return null
        val pkg = root.packageName?.toString() ?: return null
        val bounds = Rect().also { window.getBoundsInScreen(it) }
        val shot = screenshot(svc, window.id)
        val secure = shot.secure
        // La visita si ferma prima che la chat si apra (v. [CAPTURE_BUDGET_MS]),
        // con un margine per il resto della lettura e per il salto sul main.
        val rendered = if (secure) null else ScreenText.render(root, t0 + RENDER_BUDGET_MS)
        val url = if (secure) null else findUrl(root)
        val snap = Snapshot(
            id = UUID.randomUUID().toString(),
            takenAtMs = System.currentTimeMillis(),
            packageName = pkg,
            app = appLabel(svc, pkg),
            title = window.title?.toString()?.takeIf { it.isNotBlank() },
            url = url,
            secure = secure,
            text = rendered?.text.orEmpty(),
            truncated = rendered?.truncated ?: false,
            images = rendered?.images.orEmpty(),
            windowBounds = bounds,
            screenshot = shot.bitmap,
        )
        Log.i(
            TAG,
            "Screen read: pkg=$pkg secure=$secure chars=${snap.text.length} " +
                "truncated=${snap.truncated} " +
                "images=${snap.images.size} shot=${shot.bitmap != null} " +
                "ms=${SystemClock.elapsedRealtime() - t0}",
        )
        return snap
    }

    /** La finestra dell'app sotto la mascotte: la prima finestra applicativa
     *  di un altro pacchetto, preferendo quella attiva. */
    private fun targetWindow(svc: AccessibilityService, own: String): AccessibilityWindowInfo? {
        val apps = svc.windows.filter {
            it.type == AccessibilityWindowInfo.TYPE_APPLICATION &&
                it.root?.packageName?.toString().let { pkg -> pkg != null && pkg != own }
        }
        return apps.firstOrNull { it.isActive } ?: apps.firstOrNull { it.isFocused } ?: apps.firstOrNull()
    }

    private class Shot(val bitmap: Bitmap?, val secure: Boolean)

    private fun screenshot(svc: AccessibilityService, windowId: Int): Shot {
        if (Build.VERSION.SDK_INT < 34) return Shot(null, false)
        val done = CountDownLatch(1)
        var bitmap: Bitmap? = null
        var secure = false
        svc.takeScreenshotOfWindow(windowId, callbacks, object : AccessibilityService.TakeScreenshotCallback {
            override fun onSuccess(result: AccessibilityService.ScreenshotResult) {
                try {
                    bitmap = Bitmap.wrapHardwareBuffer(result.hardwareBuffer, result.colorSpace)
                        ?.copy(Bitmap.Config.ARGB_8888, false)
                } finally {
                    result.hardwareBuffer.close()
                    done.countDown()
                }
            }

            override fun onFailure(errorCode: Int) {
                secure = errorCode == AccessibilityService.ERROR_TAKE_SCREENSHOT_SECURE_WINDOW
                Log.i(TAG, "Window screenshot refused: code=$errorCode")
                done.countDown()
            }
        })
        done.await(SHOT_TIMEOUT_MS, TimeUnit.MILLISECONDS)
        return Shot(bitmap, secure)
    }

    private fun findUrl(root: android.view.accessibility.AccessibilityNodeInfo): String? {
        val stack = ArrayDeque<android.view.accessibility.AccessibilityNodeInfo>()
        stack.add(root)
        var seen = 0
        while (stack.isNotEmpty() && seen < 400) {
            val node = stack.removeLast()
            seen++
            val id = node.viewIdResourceName
            if (id != null && (id.endsWith(":id/url_bar") || URL_BAR_IDS.any { id.endsWith(":id/$it") })) {
                return node.text?.toString()?.trim()?.takeIf { it.isNotEmpty() }
            }
            // La barra sta nell'interfaccia del browser, non nella pagina:
            // dentro la WebView non si scende.
            if (node.className?.toString() == "android.webkit.WebView") continue
            for (i in node.childCount - 1 downTo 0) node.getChild(i)?.let { stack.add(it) }
        }
        return null
    }

    private fun appLabel(context: Context, pkg: String): String = try {
        val pm = context.packageManager
        pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
    } catch (e: Exception) {
        pkg
    }

    /** Scrive lo screenshot della lettura *id* in PNG. */
    fun writeScreenshot(id: String, path: String): Boolean {
        val bmp = get(id)?.screenshot ?: return false
        return writePng(bmp, File(path))
    }

    /**
     * Scrive in PNG l'immagine *n* (da 1) della lettura *id*, ritagliata dallo
     * screenshot. Solo se era a schermo quando l'utente ha chiamato Jenny:
     * di un'immagine fuori schermo non ci sono pixel, al massimo l'indirizzo.
     */
    fun writeImage(id: String, n: Int, path: String): Boolean {
        val snap = get(id) ?: return false
        val bmp = snap.screenshot ?: return false
        val img = snap.images.getOrNull(n - 1) ?: return false
        if (img.bounds.isEmpty) return false
        val crop = Rect(img.bounds).apply {
            offset(-snap.windowBounds.left, -snap.windowBounds.top)
            if (!intersect(0, 0, bmp.width, bmp.height)) return false
        }
        if (crop.width() < 2 || crop.height() < 2) return false
        return writePng(Bitmap.createBitmap(bmp, crop.left, crop.top, crop.width(), crop.height()), File(path))
    }

    private fun writePng(bmp: Bitmap, file: File): Boolean = try {
        file.parentFile?.mkdirs()
        file.outputStream().use { bmp.compress(Bitmap.CompressFormat.PNG, 100, it) }
    } catch (e: Exception) {
        Log.w(TAG, "Cannot write ${file.name}", e)
        false
    }
}
