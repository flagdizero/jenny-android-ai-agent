package com.flagdizero.jenny

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView

/**
 * La pillola: quello che si vede mentre Jenny usa il telefono.
 *
 * Una finestra piccola in alto, sopra l'app che sta guidando, con tre facce:
 *
 * * **domanda** — «Jenny sta per usare WhatsApp», con No · Sempre · Ok;
 * * **guida** — cosa sta facendo, con Ferma. Se l'utente tocca lo schermo si
 *   mette **in pausa** (Continua · Ferma): le mani sono due, e sono le sue;
 * * **esito** — cosa ha fatto, che sparisce da sola.
 *
 * La domanda sta sopra alle altre due: finché non ha una risposta, né una
 * pausa né un aggiornamento della guida la tolgono da schermo. Sul Titan, il
 * 22/11/2026, un tocco dell'utente durante «Ok?» la sostituiva con la pausa, e
 * «Continua» rimetteva la guida: la domanda restava aperta senza un posto in
 * cui rispondere, se non la notifica.
 *
 * ## Perché è una finestra del servizio di accessibilità
 *
 * `TYPE_ACCESSIBILITY_OVERLAY` e non l'overlay della mascotte: non serve il
 * permesso «sopra altre app», e — misurato su `jenny_s22` il 22/11/2026 — con
 * questa finestra a schermo l'app sotto resta leggibile dal servizio, cosa che
 * la chat flottante a schermo intero non permette.
 *
 * ## Il tocco di Jenny e il tocco dell'utente
 *
 * `FLAG_WATCH_OUTSIDE_TOUCH` consegna un evento per ogni tocco fuori dalla
 * pillola, anche quelli che inietta Jenny. Si distinguono con l'orologio:
 * quello di Jenny arriva entro un millisecondo dall'iniezione
 * ([PhoneActions.injectedAt]), e i tocchi fatti con `performAction` non ne
 * producono affatto.
 *
 * ## I tasti
 *
 * Un tasto non chiama Python da qui: manda la stessa azione che manderebbe il
 * tasto di una notifica ([PeerActionReceiver]), con lo stesso identificativo.
 * Una sola strada per la risposta, da qualunque posto arrivi.
 */
object PhonePill {

    private const val TAG = "PhonePill"

    /** Un tocco entro questo tempo da un gesto iniettato è di Jenny. */
    private const val OWN_TOUCH_MS = 600L

    /** La home che arriva entro questo tempo dal passo `home` di Jenny è sua. */
    private const val OWN_SWITCH_MS = 3_000L

    /** Quanto resta a schermo l'esito: il tempo di leggere tre o quattro
     *  righe. A 8 s, sull'emulatore il 22/11/2026, spariva a metà lettura. */
    private const val DONE_MS = 20_000L

    private val main = Handler(Looper.getMainLooper())
    private var view: LinearLayout? = null
    private var host: AccessibilityService? = null
    private var params: WindowManager.LayoutParams? = null

    /** L'identificativo della guida in corso, o `null`. */
    @Volatile
    private var driving: String? = null
    private var drivingText = ""

    /** La domanda a schermo, finché non ha una risposta ([askDone]). */
    private class Question(val cid: String, val title: String, val body: String, val always: Boolean)

    private var question: Question? = null
    private val hide = Runnable { remove() }

    /** I tasti a schermo adesso, per [debugPress]. */
    private var shownButtons: List<Pair<String, () -> Unit>> = emptyList()

    /** Preme il tasto con quell'etichetta, come farebbe un dito. Solo per le
     *  prove da adb ([DebugPhoneReceiver]): falso se quel tasto non c'è. */
    fun debugPress(label: String): Boolean = onMain {
        val hit = shownButtons.firstOrNull { it.first.equals(label, ignoreCase = true) }
        hit?.second?.invoke()
        hit != null
    }

    /** Mostra la domanda *cid*. */
    fun ask(cid: String, title: String, body: String, always: Boolean): Boolean = onMain {
        HandsService.instance ?: return@onMain false
        question = Question(cid, title, body, always)
        // La domanda riguarda il passo che sta per partire: dopo la risposta
        // la guida mostra quello, non il testo del passo di prima.
        val goal = body.trim().removeSurrounding("«", "»").trim()
        if (driving != null && goal.isNotEmpty()) drivingText = goal
        draw()
    }

    /** Toglie la domanda e torna alla guida, se ce n'è una. */
    fun askDone(): Boolean = onMain {
        question = null
        if (driving == null) remove() else draw()
        true
    }

    /** Jenny sta lavorando: *text* dice a cosa, e «Ferma» manda `stop` con *id*. */
    fun driving(id: String, text: String): Boolean = onMain {
        if (driving != id) {
            PhoneActions.begin()
            FloatingOverlayController.hideForDriving(true)
        }
        driving = id
        drivingText = text
        draw()
    }

    /** La pillola si può vedere adesso? Servizio acceso, schermo acceso e non
     *  bloccato. Lo chiede [NotifierBridge]: con la pillola a schermo la
     *  notifica della stessa domanda arriva in silenzio. */
    fun canShow(context: Context): Boolean {
        if (HandsService.instance == null) return false
        val pm = context.getSystemService(Context.POWER_SERVICE) as android.os.PowerManager
        val km = context.getSystemService(Context.KEYGUARD_SERVICE) as android.app.KeyguardManager
        return pm.isInteractive && !km.isKeyguardLocked
    }

    /** Fine della guida: con *text* resta l'esito per qualche secondo. */
    fun done(text: String?): Boolean = onMain {
        driving = null
        question = null
        FloatingOverlayController.hideForDriving(false)
        val svc = HandsService.instance
        if (text.isNullOrBlank() || svc == null) {
            remove()
        } else {
            // L'esito è un testo da leggere, non un titolo: va nel corpo, che
            // tiene più righe. Toccarlo porta alla chat, dove c'è intero.
            show(svc, null, text, listOf("✕" to { remove() })) {
                remove()
                Intents.tryStart(svc, MainActivity.openChatIntent(svc), TAG, "the chat")
            }
            main.postDelayed(hide, DONE_MS)
        }
        true
    }

    /** Il servizio si spegne: via tutto. */
    fun detach() {
        main.post {
            driving = null
            question = null
            FloatingOverlayController.hideForDriving(false)
            remove()
        }
    }

    /** Nasconde la pillola per il tempo di una foto ([PhoneShot]): resta dov'è,
     *  solo non si vede. Con lei la mascotte, se non è già nascosta dalla
     *  guida: una foto fatta prima del primo passo la troverebbe sopra l'app.
     *  Aspetta un fotogramma, così la foto le trova già via. */
    fun hidden(hide: Boolean) {
        onMain {
            view?.alpha = if (hide) 0f else 1f
            if (driving == null) FloatingOverlayController.hideForDriving(hide)
            true
        }
        if (hide) Thread.sleep(80)
    }

    /** Sposta la pillola in basso se il punto (x, y) ci cade sopra: un gesto
     *  di Jenny non deve finire sul suo stesso tasto «Ferma». */
    fun dodge(x: Float, y: Float) {
        val v = view ?: return
        val at = IntArray(2).also { v.getLocationOnScreen(it) }
        val box = Rect(at[0], at[1], at[0] + v.width, at[1] + v.height)
        if (!box.contains(x.toInt(), y.toInt())) return
        onMain {
            val p = params ?: return@onMain false
            p.gravity = (if (p.gravity and Gravity.TOP == Gravity.TOP) Gravity.BOTTOM else Gravity.TOP) or
                Gravity.CENTER_HORIZONTAL
            runCatching { wm(host ?: return@onMain false).updateViewLayout(v, p) }
            true
        }
        Thread.sleep(120)
    }

    /** Disegna la faccia giusta per lo stato di adesso: la domanda, se ce n'è
     *  una; altrimenti la pausa o la guida. */
    private fun draw(): Boolean {
        val svc = HandsService.instance ?: return false
        question?.let { q ->
            val buttons = buildList {
                add(svc.getString(R.string.phone_no_label) to { send(svc, PeerActionReceiver.ACTION_DENY, q.cid) })
                if (q.always) {
                    add(svc.getString(R.string.phone_always_label) to { send(svc, PeerActionReceiver.ACTION_ALWAYS, q.cid) })
                }
                add(svc.getString(R.string.phone_ok_label) to { send(svc, PeerActionReceiver.ACTION_ALLOW, q.cid) })
            }
            return show(svc, q.title, q.body, buttons)
        }
        val id = driving ?: return false
        return if (PhoneActions.paused) {
            show(
                svc, svc.getString(R.string.phone_paused), null,
                listOf(
                    svc.getString(R.string.phone_stop_label) to { send(svc, PeerActionReceiver.ACTION_STOP, id) },
                    svc.getString(R.string.phone_continue_label) to {
                        PhoneActions.resume()
                        draw()
                    },
                ),
            )
        } else {
            show(
                svc, drivingText, null,
                listOf(svc.getString(R.string.phone_stop_label) to { send(svc, PeerActionReceiver.ACTION_STOP, id) }),
            )
        }
    }

    private fun send(context: Context, action: String, ident: String) {
        context.sendBroadcast(
            Intent(context, PeerActionReceiver::class.java)
                .putExtra(PeerActionReceiver.EXTRA_ACTION, action)
                .putExtra(PeerActionReceiver.EXTRA_IDENT, ident),
        )
        Log.i(TAG, "Pill button: $action")
    }

    private fun show(
        svc: AccessibilityService,
        title: String?,
        body: String?,
        buttons: List<Pair<String, () -> Unit>>,
        onTap: (() -> Unit)? = null,
    ): Boolean {
        remove()
        shownButtons = buttons
        Log.i(TAG, "Pill: ${(title ?: body).orEmpty().take(70)} ${buttons.map { it.first }}")
        val pad = dp(svc, 14)
        val column = LinearLayout(svc).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, dp(svc, 10), pad, dp(svc, 10))
            background = GradientDrawable().apply {
                cornerRadius = dp(svc, 22).toFloat()
                setColor(Color.rgb(28, 28, 30))
            }
            if (!title.isNullOrBlank()) addView(label(svc, title, 14f, bold = true))
            if (!body.isNullOrBlank()) addView(label(svc, body, 13f, bold = false))
            if (onTap != null) setOnClickListener { onTap() }
            addView(LinearLayout(svc).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.END
                for ((text, onTap) in buttons) addView(button(svc, text, onTap))
            })
            setOnTouchListener { _: View, e: MotionEvent ->
                if (e.actionMasked == MotionEvent.ACTION_OUTSIDE) outsideTouch()
                false
            }
        }
        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
            y = dp(svc, 48)
        }
        return try {
            wm(svc).addView(column, lp)
            view = column
            host = svc
            params = lp
            true
        } catch (e: Exception) {
            Log.w(TAG, "Cannot show the pill", e)
            false
        }
    }

    /**
     * Davanti è arrivata un'altra finestra. Se è la home o Jenny stessa, e
     * non l'ha portata lei, l'utente ha premuto Home (o ha aperto Jenny): è
     * come un tocco, si mette in pausa. Home è un tasto, non un tocco, e la
     * pillola non lo sentiva: sul Titan, il 23/11/2026, Jenny continuava a
     * scorrere sulla propria chat credendo di essere ancora in Files.
     */
    fun foregroundChanged(pkg: String?, cls: String?) {
        if (driving == null || pkg == null) return
        val svc = HandsService.instance ?: return
        // Pillola e mascotte sono finestre di Jenny anche loro, e comparire le
        // fa annunciare: di Jenny conta solo la chat, cioè l'attività.
        val jenny = pkg == svc.packageName && cls == MainActivity::class.java.name
        if (!jenny && (pkg == svc.packageName || pkg != homePackage(svc))) return
        main.post {
            if (driving == null || question != null || PhoneActions.paused) return@post
            // L'unico passo con cui Jenny porta lei la home è `home`. Una
            // finestra dopo ogni passo era troppo larga: un Home premuto meno
            // di 3 s dopo un tocco di Jenny passava per suo (Titan, 23/11/2026).
            val since = SystemClock.elapsedRealtime() - PhoneActions.ownHomeAt
            if (since < OWN_SWITCH_MS) return@post
            PhoneActions.pause()
            Log.i(TAG, "The user went to $pkg: paused")
            draw()
        }
    }

    private fun homePackage(context: Context): String? =
        context.packageManager.resolveActivity(
            Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME), 0,
        )?.activityInfo?.packageName

    /** Un tocco fuori dalla pillola mentre Jenny guida: se non è suo, pausa. */
    private fun outsideTouch() {
        if (driving == null) return
        // Con una domanda a schermo Jenny è ferma ad aspettare: non c'è un
        // gesto da interrompere, e la pausa coprirebbe la domanda.
        if (question != null || PhoneActions.paused) return
        val since = SystemClock.elapsedRealtime() - PhoneActions.injectedAt
        Log.i(TAG, "Touch outside the pill, ${since}ms after Jenny's last gesture")
        if (since < OWN_TOUCH_MS) return
        PhoneActions.pause()
        Log.i(TAG, "The user touched the screen: paused")
        draw()
    }

    private fun remove() {
        main.removeCallbacks(hide)
        val v = view ?: return
        runCatching { wm(host ?: return).removeView(v) }
        shownButtons = emptyList()
        view = null
        params = null
    }

    private fun label(context: Context, text: String, sizeSp: Float, bold: Boolean) =
        TextView(context).apply {
            this.text = text
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, sizeSp)
            maxLines = if (bold) 2 else 6
            ellipsize = android.text.TextUtils.TruncateAt.END
            maxWidth = dp(context, 300)
            if (bold) setTypeface(typeface, android.graphics.Typeface.BOLD)
        }

    private fun button(context: Context, text: String, onTap: () -> Unit) =
        TextView(context).apply {
            this.text = text
            setTextColor(Color.rgb(140, 200, 255))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(dp(context, 14), dp(context, 10), dp(context, 14), dp(context, 6))
            setOnClickListener { onTap() }
        }

    private fun wm(context: Context) = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager

    private fun dp(context: Context, value: Int): Int =
        (value * context.resources.displayMetrics.density).toInt()

    private fun onMain(block: () -> Boolean): Boolean =
        MainHop.call(3_000L, false, TAG, block = block)
}
