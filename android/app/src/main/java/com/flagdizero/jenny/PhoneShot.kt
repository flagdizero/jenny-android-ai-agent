package com.flagdizero.jenny

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import android.os.Build
import android.util.Log
import android.view.Display
import org.json.JSONObject
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * La foto dello schermo, per le app che non si lasciano leggere.
 *
 * Alcune app disegnano tutto da sole e all'albero di accessibilità danno solo
 * riquadri senza nome: su Telegram X, sul Titan il 22/11/2026, la lettura era
 * quattordici «button ""» e nessuna riga di testo. Lì l'unico modo di vedere è
 * una foto, e l'unico modo di toccare è un punto.
 *
 * La foto viene ridotta ([take], *maxSide*) e il modello ragiona nei suoi
 * pixel: [toScreen] riporta un suo punto sullo schermo vero. Il fattore è
 * quello dell'ultima foto fatta — un punto senza una foto non ha un senso, e
 * [PhoneActions] lo rifiuta.
 *
 * Un'app che vieta gli screenshot non viene fotografata: stessa regola della
 * lettura ([PhoneTree.secureNow]).
 */
object PhoneShot {

    private const val TAG = "PhoneShot"
    private const val SHOT_WAIT_MS = 3_000L

    /** Android accetta una foto ogni ~333 ms, e la prova «è sicura?» ne è una. */
    private const val SHOT_INTERVAL_MS = 400L
    private const val JPEG_QUALITY = 80

    private val callbacks = Executors.newSingleThreadExecutor { Thread(it, "jenny-phone-photo") }

    /** Pixel della foto per pixel di schermo, dall'ultima [take]; 0 = nessuna foto. */
    @Volatile
    private var scale = 0f

    /** C'è una foto a cui riferire un punto? */
    val ready: Boolean get() = scale > 0f

    /** Da un punto della foto al punto dello schermo. */
    fun toScreen(x: Double, y: Double): Pair<Float, Float> = (x / scale).toFloat() to (y / scale).toFloat()

    /** Niente foto a cui riferirsi (servizio spento, guida nuova). */
    fun clear() {
        scale = 0f
    }

    /**
     * Fotografa lo schermo in *path* (JPEG), col lato lungo ridotto a
     * *maxSide*. Torna `{width, height}` della foto, `{secure: true}` se
     * l'app vieta gli screenshot, o `null` se non si è potuto.
     */
    fun take(svc: AccessibilityService, path: String, maxSide: Int): JSONObject? {
        if (Build.VERSION.SDK_INT < 34) return null
        if (PhoneTree.secureNow(svc)) return JSONObject().put("secure", true)
        // La pillola sta sopra l'app: nella foto coprirebbe quello che c'è sotto.
        PhonePill.hidden(true)
        try {
            Thread.sleep(SHOT_INTERVAL_MS)
            val full = grab(svc) ?: run {
                Thread.sleep(SHOT_INTERVAL_MS)
                grab(svc)
            } ?: return null
            val factor = (maxSide.toFloat() / maxOf(full.width, full.height)).coerceAtMost(1f)
            val small = if (factor < 1f) {
                Bitmap.createScaledBitmap(
                    full, (full.width * factor).toInt(), (full.height * factor).toInt(), true,
                ).also { full.recycle() }
            } else {
                full
            }
            val file = File(path)
            file.parentFile?.mkdirs()
            file.outputStream().use { small.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, it) }
            scale = factor
            return JSONObject().put("width", small.width).put("height", small.height)
                .also { small.recycle() }
        } catch (e: Exception) {
            Log.w(TAG, "Cannot save the screenshot", e)
            return null
        } finally {
            PhonePill.hidden(false)
        }
    }

    private fun grab(svc: AccessibilityService): Bitmap? {
        val done = CountDownLatch(1)
        var bitmap: Bitmap? = null
        svc.takeScreenshot(
            Display.DEFAULT_DISPLAY, callbacks,
            object : AccessibilityService.TakeScreenshotCallback {
                override fun onSuccess(result: AccessibilityService.ScreenshotResult) {
                    val buffer = result.hardwareBuffer
                    // Una copia in memoria normale: quella sull'hardware non si
                    // può né ridurre né comprimere.
                    bitmap = Bitmap.wrapHardwareBuffer(buffer, result.colorSpace)
                        ?.copy(Bitmap.Config.ARGB_8888, false)
                    buffer.close()
                    done.countDown()
                }

                override fun onFailure(errorCode: Int) {
                    Log.i(TAG, "Screenshot refused (code=$errorCode)")
                    done.countDown()
                }
            },
        )
        done.await(SHOT_WAIT_MS, TimeUnit.MILLISECONDS)
        return bitmap
    }
}
