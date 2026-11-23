package com.flagdizero.jenny

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Bundle
import android.util.Log
import android.util.Size
import android.util.TypedValue
import android.view.Gravity
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.Button
import android.widget.FrameLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.google.zxing.qrcode.QRCodeReader
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * Il lettore del QR d'abbinamento fra due Jenny (o fra Jenny e il Mac con
 * `opencode-jenny`). Lo apre solo la pagina Altre Jenny, col comando
 * `scanPairQr` di [MainActivity]; restituisce il link `jenny://pair…` in
 * [EXTRA_LINK], e MainActivity lo fa entrare dallo stesso punto di un QR letto
 * con la fotocamera di sistema. Abbinare resta un tocco dell'utente su
 * «Collega»: questo schermo legge e basta.
 *
 * Il permesso della fotocamera si chiede qui, la prima volta che serve. Un QR
 * che non è di Jenny non chiude niente: lo dice e continua a inquadrare.
 */
class QrScanActivity : ComponentActivity() {

    private lateinit var previewView: PreviewView
    private lateinit var hint: TextView
    private val analyzer: ExecutorService = Executors.newSingleThreadExecutor()
    private val reader = QRCodeReader()
    private var frames = 0L

    @Volatile
    private var done = false

    private val askCamera =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) {
                startCamera()
            } else {
                Toast.makeText(this, R.string.qr_scan_no_camera, Toast.LENGTH_LONG).show()
                finish()
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(buildViews())
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        ) {
            startCamera()
        } else {
            askCamera.launch(Manifest.permission.CAMERA)
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        analyzer.shutdown()
    }

    private fun buildViews(): FrameLayout {
        val root = FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }
        previewView = PreviewView(this).apply {
            scaleType = PreviewView.ScaleType.FILL_CENTER
        }
        root.addView(previewView, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))

        val pad = dp(16)
        hint = TextView(this).apply {
            setText(R.string.qr_scan_hint)
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            gravity = Gravity.CENTER
            setBackgroundColor(0x99000000.toInt())
            setPadding(pad, pad / 2, pad, pad / 2)
        }
        root.addView(
            hint,
            FrameLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT, Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL)
        )
        val close = Button(this).apply {
            setText(R.string.qr_scan_close)
            setOnClickListener { finish() }
        }
        root.addView(
            close,
            FrameLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT, Gravity.TOP or Gravity.END)
        )
        // Da targetSdk 35 la finestra va sotto le barre: testo e tasto stanno
        // dentro la parte libera, l'anteprima resta a tutto schermo.
        ViewCompat.setOnApplyWindowInsetsListener(root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            (hint.layoutParams as FrameLayout.LayoutParams).setMargins(pad, 0, pad, bars.bottom + pad * 2)
            (close.layoutParams as FrameLayout.LayoutParams).setMargins(0, bars.top + pad / 2, pad / 2, 0)
            hint.requestLayout()
            close.requestLayout()
            insets
        }
        return root
    }

    private fun startCamera() {
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            if (isFinishing || isDestroyed) return@addListener
            try {
                val provider = future.get()
                val preview = Preview.Builder().build().also {
                    it.surfaceProvider = previewView.surfaceProvider
                }
                // 720p basta e avanza per un QR a una spanna; di più è solo
                // lavoro in più per ogni fotogramma.
                val analysis = ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .setResolutionSelector(
                        ResolutionSelector.Builder()
                            .setResolutionStrategy(
                                ResolutionStrategy(
                                    Size(1280, 720),
                                    ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER
                                )
                            )
                            .build()
                    )
                    .build()
                analysis.setAnalyzer(analyzer, ::analyze)
                // La posteriore; se il telefono ha solo quella davanti, quella.
                val selector =
                    if (provider.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA)) {
                        CameraSelector.DEFAULT_BACK_CAMERA
                    } else {
                        CameraSelector.DEFAULT_FRONT_CAMERA
                    }
                provider.unbindAll()
                provider.bindToLifecycle(this, selector, preview, analysis)
            } catch (e: Exception) {
                Log.w(TAG, "camera did not start (${e.javaClass.simpleName})")
                Toast.makeText(this, R.string.qr_scan_failed, Toast.LENGTH_LONG).show()
                finish()
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun analyze(image: ImageProxy) {
        try {
            if (done) return
            val text = decode(image) ?: return
            if (PairQrDecoder.isPairLink(text)) {
                done = true
                runOnUiThread {
                    setResult(RESULT_OK, Intent().putExtra(EXTRA_LINK, text.take(MAX_LINK)))
                    finish()
                }
            } else {
                runOnUiThread { hint.setText(R.string.qr_scan_not_jenny) }
            }
        } finally {
            image.close()
        }
    }

    /** Il testo del QR nel fotogramma, o `null`. Basta la luminanza (il piano
     *  Y); un fotogramma sì e uno no la si legge invertita ([PairQrDecoder]). */
    private fun decode(image: ImageProxy): String? {
        val plane = image.planes.firstOrNull() ?: return null
        val buffer = plane.buffer
        val bytes = ByteArray(buffer.remaining()).also { buffer.get(it) }
        frames += 1
        return PairQrDecoder.decode(
            bytes, plane.rowStride, image.width, image.height, inverted = frames % 2 == 0L, reader = reader
        )
    }

    private fun dp(value: Int): Int =
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value.toFloat(), resources.displayMetrics).toInt()

    companion object {
        private const val TAG = "QrScan"
        const val EXTRA_LINK = "com.flagdizero.jenny.extra.PAIR_LINK"
        private const val MAX_LINK = 2048
    }
}
