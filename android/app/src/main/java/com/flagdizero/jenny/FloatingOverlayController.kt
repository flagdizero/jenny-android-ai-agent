package com.flagdizero.jenny

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.Editable
import android.text.TextWatcher
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import java.io.File
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * La mascotte flottante: Jenny sopra le altre app, un tap e le parli.
 *
 * ## Una finestra, due taglie
 *
 * Il nodo di qualunque overlay è che **una finestra prende tutti i tocchi dentro
 * i suoi limiti**: a schermo intero renderebbe il telefono inutilizzabile,
 * piccola non basta a contenere un campo di testo e un fumetto. Qui la finestra
 * è una sola e cambia taglia:
 *
 * * `PARKED` — grande quanto lo sprite, non focusable, al bordo dove l'hai
 *   lasciata. È il 99% del tempo;
 * * `CHAT` — schermo intero e focusable, con lo scrim, il campo in basso e il
 *   fumetto sopra la testa.
 *
 * **Il cambio di taglia avviene sempre fra un gesto e l'altro, mai durante.** Il
 * tap si completa e *poi* la finestra cresce; il trascinamento non cambia
 * taglia affatto (la finestra piccola segue il dito). Non esiste un gesto che
 * richieda di ridimensionare a dito abbassato, ed è questo che evita tutta la
 * classe di bug in cui il primo `MOVE` dopo il ridimensionamento arriva con
 * coordinate relative a una cornice diversa.
 *
 * Quando la finestra è intera sta a `(0,0)`, quindi la posizione della mascotte
 * dentro la pagina coincide con la sua posizione sullo schermo: il passaggio
 * fra le due taglie non sposta niente di un pixel.
 *
 * ## Chi possiede cosa
 *
 * Qui dentro c'è **solo la finestra**. Il testo che l'utente scrive esce da
 * `GatewayService.deliverFloatingText` ed entra nella conversazione come
 * qualunque altro canale; la risposta rientra da `FloatingBridge.showReply`,
 * chiamato da Python. Questo file non sa cosa sia una sessione, non parla con
 * l'agente e non contiene una sola stringa mostrata all'utente — stanno tutte
 * in `res/values`.
 *
 * ## Gli sprite
 *
 * Si leggono dalla copia della WebUI estratta in `workspace/ui/assets/`, non da
 * `res/drawable`. Copiarli nelle risorse avrebbe voluto dire tenerne due
 * versioni allineate a mano: quella cartella la riscrive l'estrazione a ogni
 * aggiornamento dell'APK, quindi la mascotte flottante e quella in chat non
 * possono divergere. Se un file manca, la faccia semplicemente non si disegna:
 * una mascotte senza espressione è brutta, una che non parte è rotta.
 */
object FloatingOverlayController {

    private const val TAG = "FloatingOverlay"

    /** Lato dello sprite. La `sm` della WebUI è 120 px CSS; qui è in dp e un
     *  filo più piccola, perché là la mascotte sta dentro una pagina e qui sta
     *  sopra il lavoro di qualcun altro. */
    private const val MASCOT_DP = 96

    /** Quanto sporge fuori dal bordo a riposo. Un terzo dello sprite resta
     *  fuori schermo: si vede che c'è, non ruba spazio, e soprattutto **non
     *  sparisce del tutto** — una mascotte parcheggiata invisibile non si
     *  ritrova più. */
    private const val PARK_OUT_RATIO = 0.33f

    /** Oltre questo spostamento il gesto è un trascinamento e non un tap. */
    private const val DRAG_SLOP_DP = 8

    /** Quanto si aspetta la risposta prima di dire che non arriva. Uguale al
     *  `REPLY_TIMEOUT_MS` della minichat della WebUI: è lo stesso agente, con
     *  gli stessi tempi, e due soglie diverse per la stessa attesa sarebbero
     *  due verità diverse su quando Jenny è in ritardo. */
    private const val REPLY_TIMEOUT_MS = 90_000L

    /** Ripiego se Python non ha ancora spinto la config. */
    private const val DEFAULT_REPLY_HOLD_S = 20

    private const val PREFS = "jenny_floating"
    private const val PREF_Y = "park_y"

    private val main = Handler(Looper.getMainLooper())

    /** Stato voluto da Python (`config.floating.enabled`). Separato dal fatto
     *  che la finestra sia a schermo: con l'app in primo piano la mascotte si
     *  nasconde pur restando accesa. */
    @Volatile
    private var enabled = false

    @Volatile
    private var replyHoldMs = DEFAULT_REPLY_HOLD_S * 1000L

    /** La finestra è a schermo intero? Vero anche per il solo fumetto: una
     *  risposta arrivata a finestra già richiusa deve poter essere disegnata,
     *  e in 96dp non ci sta. */
    @Volatile
    private var expanded = false

    /** ...e dentro quella finestra c'è anche il campo, con il fuoco e la
     *  tastiera? Le due cose sono separate perché il fumetto da solo **non**
     *  deve rubare il fuoco all'app sotto: nessuno ha chiesto di scrivere. */
    @Volatile
    var isChatOpen = false
        private set

    private var appContext: Context? = null
    private var windowManager: WindowManager? = null
    private var root: FrameLayout? = null
    private var params: WindowManager.LayoutParams? = null

    private var mascotBody: ImageView? = null
    private var mascotFace: ImageView? = null
    private var bubble: TextView? = null
    private var scrim: View? = null
    private var inputRow: View? = null
    private var input: EditText? = null
    private var column: LinearLayout? = null

    private val sprites = HashMap<String, Bitmap?>()

    /** Ultima altezza a cui l'utente l'ha lasciata, in px. `-1` = mai scelta. */
    private var parkedY = -1
    private var waitingForReply = false

    private val timeoutRunnable = Runnable { onReplyTimeout() }
    private val holdRunnable = Runnable { collapse() }

    // ------------------------------------------------------------------ //
    // Superficie chiamata da Python (via FloatingBridge) e dal service      //
    // ------------------------------------------------------------------ //

    /**
     * Accende o spegne la mascotte. Idempotente; solo dal main thread.
     *
     * Ritorna se la mascotte è **davvero** accesa, ed è il valore che
     * l'interruttore nelle impostazioni mostra all'utente. Da qui la risposta
     * non è «la finestra è a schermo adesso»: nel momento esatto in cui si
     * tocca quell'interruttore l'app è per forza in primo piano, quindi la
     * finestra è per forza nascosta ([`applyVisibility`]) — e rispondere di no
     * vorrebbe dire dire «non ci riesco» a qualcosa che funziona benissimo.
     *
     * La domanda vera è l'altra: **Android la lascerebbe aprire?** Misurato sul
     * Titan 2 il 17/09/2026, ed è il difetto che questa riga chiude: prima si
     * ritornava il risultato di `applyVisibility`, che con l'app davanti esce
     * dal ramo «nascondi» senza nemmeno guardare il permesso. L'interruttore
     * rispondeva «accesa» e trenta secondi dopo, passando in background, il log
     * diceva `SYSTEM_ALERT_WINDOW is not granted`. Cioè la cosa che questo
     * valore esiste per raccontare era esattamente quella che non raccontava.
     */
    fun setEnabled(context: Context, on: Boolean): Boolean {
        val ctx = context.applicationContext
        appContext = ctx
        enabled = on
        applyVisibility()
        return on && canDrawOverlays(ctx)
    }

    /**
     * Disegna *text* nel fumetto. `false` se non c'è nessuna finestra.
     *
     * Se nel frattempo la finestra è tornata piccola — l'utente ha richiuso
     * mentre aspettava — si riallarga per il **solo fumetto**: senza fuoco,
     * senza scrim, senza tastiera. Nessuno ha chiesto di scrivere; ha chiesto
     * di leggere, e in 96dp non ci sta niente da leggere.
     */
    fun showReply(text: String): Boolean {
        val view = bubble ?: return false
        cancelTimeout()
        waitingForReply = false
        view.text = text
        view.visibility = View.VISIBLE
        syncFace()
        if (!expanded) expand(withInput = false)
        armHold()
        return true
    }

    /**
     * La mascotte è accesa **e** Android la lascia esistere?
     *
     * Domanda distinta da «la finestra è a schermo adesso»: con l'app davanti
     * è nascosta di proposito, e quello non è un rifiuto. Serve al pannello
     * impostazioni, che deve poter mostrare la riga sul permesso ogni volta che
     * è vera — non solo nel secondo successivo al tocco dell'interruttore.
     */
    fun isActive(): Boolean {
        val ctx = appContext ?: return false
        return enabled && canDrawOverlays(ctx)
    }

    /** Smonta tutto. Chiamata da `GatewayService.onDestroy`. */
    fun teardown() {
        main.post {
            enabled = false
            detach()
        }
    }

    /**
     * L'app è passata in primo piano (o ne è uscita).
     *
     * Jenny è la home del telefono: senza questo, sulla schermata iniziale ci
     * sarebbero **due** mascotte, una dentro la SPA e una sopra. Si nasconde e
     * non si smonta — rimontare una finestra a ogni passaggio in foreground
     * costerebbe più che tenerla ferma.
     */
    fun onAppForegroundChanged() {
        main.post { applyVisibility() }
    }

    /** Millisecondi di permanenza del fumetto, spinti da Python con la config. */
    fun setReplyHoldSeconds(seconds: Int) {
        replyHoldMs = seconds.coerceIn(5, 120) * 1000L
    }

    // ------------------------------------------------------------------ //
    // Montaggio e smontaggio                                              //
    // ------------------------------------------------------------------ //

    private fun applyVisibility(): Boolean {
        val ctx = appContext ?: return false
        val wanted = enabled && !MainActivity.isInForeground
        if (!wanted) {
            if (root != null) detach()
            return enabled
        }
        if (root != null) return true
        if (!canDrawOverlays(ctx)) {
            Log.i(TAG, "Overlay requested but SYSTEM_ALERT_WINDOW is not granted")
            return false
        }
        return attach(ctx)
    }

    private fun canDrawOverlays(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.M || Settings.canDrawOverlays(ctx)

    @SuppressLint("ClickableViewAccessibility")
    private fun attach(ctx: Context): Boolean {
        val wm = ctx.getSystemService(WindowManager::class.java) ?: return false
        return try {
            loadParkPosition(ctx)
            expanded = false
            isChatOpen = false
            val container = buildViews(ctx)
            val lp = parkedParams(ctx)
            wm.addView(container, lp)
            windowManager = wm
            root = container
            params = lp
            Log.i(TAG, "Floating mascot attached (y=${lp.y})")
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to attach the floating mascot", e)
            detach()
            false
        }
    }

    private fun detach() {
        cancelTimeout()
        main.removeCallbacks(holdRunnable)
        expanded = false
        isChatOpen = false
        waitingForReply = false
        val wm = windowManager
        val view = root
        if (wm != null && view != null) {
            try {
                wm.removeView(view)
            } catch (e: Exception) {
                Log.i(TAG, "Floating mascot already detached: ${e.javaClass.simpleName}")
            }
        }
        root = null
        params = null
        windowManager = null
        mascotBody = null
        mascotFace = null
        bubble = null
        scrim = null
        inputRow = null
        input = null
        column = null
    }

    // ------------------------------------------------------------------ //
    // Le due taglie della finestra                                        //
    // ------------------------------------------------------------------ //

    private fun overlayType(): Int =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }

    private fun parkedParams(ctx: Context): WindowManager.LayoutParams {
        val size = dp(ctx, MASCOT_DP)
        val lp = WindowManager.LayoutParams(
            size,
            size,
            overlayType(),
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT
        )
        lp.gravity = Gravity.TOP or Gravity.START
        lp.x = parkX(ctx)
        lp.y = parkY(ctx)
        return lp
    }

    /**
     * Allarga la finestra a schermo intero.
     *
     * Con *withInput* prende anche il **fuoco**: togliere `FLAG_NOT_FOCUSABLE`
     * è tutto ciò che serve perché una finestra overlay accetti la tastiera, e
     * `SOFT_INPUT_ADJUST_RESIZE` — più gli insets IME letti in `buildViews` —
     * è ciò che tiene il campo sopra di essa invece che sotto. Senza, la
     * finestra resta intera ma **non focusable**: serve al solo fumetto, e non
     * ruba all'app sotto un fuoco che nessuno le ha chiesto di cedere.
     *
     * Il fuoco si restituisce appena si collassa: una finestra overlay
     * focusable lasciata lì si mangerebbe ogni tasto del telefono.
     */
    private fun expand(withInput: Boolean) {
        val ctx = appContext ?: return
        val wm = windowManager ?: return
        val view = root ?: return
        val lp = params ?: return
        if (expanded && isChatOpen == withInput) return

        if (!expanded) {
            // La mascotte deve restare allo stesso pixel attraversando il
            // cambio di taglia: la finestra passa a (0,0), quindi la posizione
            // che aveva come origine della finestra diventa un margine dentro
            // di essa.
            placeColumn(ctx, lp.x, lp.y)
            lp.width = WindowManager.LayoutParams.MATCH_PARENT
            lp.height = WindowManager.LayoutParams.MATCH_PARENT
            lp.x = 0
            lp.y = 0
        }
        lp.flags = if (withInput) {
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
        } else {
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
        }
        lp.softInputMode = if (withInput) {
            WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE
        } else {
            WindowManager.LayoutParams.SOFT_INPUT_STATE_UNSPECIFIED
        }
        try {
            wm.updateViewLayout(view, lp)
        } catch (e: Exception) {
            Log.e(TAG, "Could not expand the floating mascot", e)
            return
        }
        expanded = true
        isChatOpen = withInput
        scrim?.visibility = if (withInput) View.VISIBLE else View.GONE
        inputRow?.visibility = if (withInput) View.VISIBLE else View.GONE
        if (withInput) {
            input?.let {
                it.requestFocus()
                val imm = ctx.getSystemService(InputMethodManager::class.java)
                imm?.showSoftInput(it, InputMethodManager.SHOW_IMPLICIT)
            }
        }
        armHold()
        Log.i(TAG, "Floating mascot expanded (input=$withInput)")
    }

    /**
     * Ritorno a parcheggiata: via il fuoco, via lo scrim, via il fumetto.
     *
     * Quello che **non** si butta è il testo già scritto nel campo. Una frase a
     * metà è lavoro dell'utente, e ritrovarla al tocco dopo costa meno che
     * riscriverla; il timer di inattività, del resto, non scatta nemmeno
     * finché c'è del testo lì dentro (v. `armHold`).
     */
    private fun collapse() {
        val ctx = appContext ?: return
        val wm = windowManager ?: return
        val view = root ?: return
        val lp = params ?: return
        main.removeCallbacks(holdRunnable)
        bubble?.visibility = View.GONE
        if (!expanded) return
        hideKeyboard(ctx)
        expanded = false
        isChatOpen = false
        scrim?.visibility = View.GONE
        inputRow?.visibility = View.GONE
        cancelTimeout()
        waitingForReply = false
        syncFace()

        val size = dp(ctx, MASCOT_DP)
        lp.width = size
        lp.height = size
        lp.x = parkX(ctx)
        lp.y = parkY(ctx)
        lp.flags = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
        lp.softInputMode = WindowManager.LayoutParams.SOFT_INPUT_STATE_UNSPECIFIED
        resetColumn()
        try {
            wm.updateViewLayout(view, lp)
        } catch (e: Exception) {
            Log.e(TAG, "Could not collapse the floating mascot", e)
        }
        Log.i(TAG, "Floating mascot collapsed")
    }

    // ------------------------------------------------------------------ //
    // Le view                                                             //
    // ------------------------------------------------------------------ //

    @SuppressLint("ClickableViewAccessibility", "SetTextI18n")
    private fun buildViews(ctx: Context): FrameLayout {
        val container = object : FrameLayout(ctx) {
            /** Indietro chiude la chat invece di cadere nel vuoto. Arriva qui
             *  solo quando la finestra ha il fuoco, cioè esattamente quando c'è
             *  qualcosa da chiudere. */
            override fun dispatchKeyEvent(event: KeyEvent): Boolean {
                if (event.keyCode == KeyEvent.KEYCODE_BACK &&
                    event.action == KeyEvent.ACTION_UP && isChatOpen
                ) {
                    collapse()
                    return true
                }
                return super.dispatchKeyEvent(event)
            }
        }

        val dim = View(ctx).apply {
            setBackgroundColor(0x66000000)
            visibility = View.GONE
            setOnClickListener { collapse() }
        }
        container.addView(dim, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT
        ))
        scrim = dim

        val col = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
        }
        val speech = TextView(ctx).apply {
            setTextColor(0xFFF5F0E8.toInt())
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            maxLines = 8
            visibility = View.GONE
            background = bubbleBackground()
            val padH = dp(ctx, 12)
            val padV = dp(ctx, 9)
            setPadding(padH, padV, padH, padV)
            // Il fumetto porta alla conversazione vera: è l'unico posto in cui
            // c'è tutto il resto, dato che qui si vede solo l'ultima risposta.
            setOnClickListener { openChat(ctx) }
        }
        col.addView(speech, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply { bottomMargin = dp(ctx, 6) })
        bubble = speech

        val mascotSize = dp(ctx, MASCOT_DP)
        val mascot = FrameLayout(ctx)
        val body = ImageView(ctx).apply { scaleType = ImageView.ScaleType.FIT_CENTER }
        val face = ImageView(ctx).apply { scaleType = ImageView.ScaleType.FIT_CENTER }
        mascot.addView(body, FrameLayout.LayoutParams(mascotSize, mascotSize))
        mascot.addView(face, FrameLayout.LayoutParams(mascotSize, mascotSize))
        mascotBody = body
        mascotFace = face
        bindTouch(ctx, mascot)
        col.addView(mascot, LinearLayout.LayoutParams(mascotSize, mascotSize))

        container.addView(col, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT
        ))
        column = col

        container.addView(buildInputRow(ctx), FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.WRAP_CONTENT
        ).apply { gravity = Gravity.BOTTOM })

        // La tastiera sopra il campo, invece che sotto.
        //
        // `SOFT_INPUT_ADJUST_RESIZE` da solo non basta più: è deprecato da API
        // 30 e su Android recenti una finestra overlay può non essere
        // ridimensionata affatto: il campo resterebbe **dietro** la tastiera,
        // cioè invisibile proprio mentre ci si scrive. Qui si legge l'inset
        // dell'IME e lo si trasforma in padding del campo, che è la strada che
        // non dipende da quel flag.
        //
        // Gli insets arrivano solo mentre la finestra ha il fuoco (cioè in
        // CHAT), ed è esattamente quando servono: parcheggiata non c'è nessuna
        // tastiera da schivare. Il ramo pre-R non tenta ripieghi — là
        // `ADJUST_RESIZE` è ancora onorato.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            container.setOnApplyWindowInsetsListener { _, insets ->
                val ime = insets.getInsets(WindowInsets.Type.ime()).bottom
                val bars = insets.getInsets(WindowInsets.Type.systemBars()).bottom
                inputRow?.let {
                    val pad = dp(ctx, 10)
                    it.setPadding(pad, pad, pad, pad + max(ime, bars))
                }
                insets
            }
        }

        syncFace()
        return container
    }

    private fun buildInputRow(ctx: Context): View {
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(0xF0141210.toInt())
            val pad = dp(ctx, 10)
            setPadding(pad, pad, pad, pad)
            visibility = View.GONE
        }
        val field = EditText(ctx).apply {
            hint = ctx.getString(R.string.floating_input_hint)
            setTextColor(0xFFF5F0E8.toInt())
            setHintTextColor(0xFF8A8378.toInt())
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            maxLines = 4
            setSingleLine(false)
            imeOptions = EditorInfo.IME_ACTION_SEND
            setBackgroundColor(Color.TRANSPARENT)
            setOnEditorActionListener { _, actionId, _ ->
                if (actionId == EditorInfo.IME_ACTION_SEND) {
                    send()
                    true
                } else {
                    false
                }
            }
            // Chi sta scrivendo non è un utente assente: il timer di chiusura
            // si riarma a ogni carattere. Un collapse a metà frase è la peggior
            // cosa che questa finestra possa fare.
            addTextChangedListener(object : TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) = Unit
                override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) = Unit
                override fun afterTextChanged(s: Editable?) = armHold()
            })
        }
        row.addView(field, LinearLayout.LayoutParams(
            0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f
        ))
        input = field
        inputRow = row
        return row
    }

    private fun bubbleBackground(): GradientDrawable = GradientDrawable().apply {
        setColor(0xF0141210.toInt())
        cornerRadius = 28f
    }

    // ------------------------------------------------------------------ //
    // Gesti                                                               //
    // ------------------------------------------------------------------ //

    /**
     * Tap e trascinamento sulla mascotte.
     *
     * Il trascinamento sposta la **finestra** con le coordinate schermo
     * (`rawY`), e non cambia taglia: è seguire un dito, non fisica. Il tap
     * apre la chat, ma solo dopo che il dito si è alzato — il cambio di taglia
     * non avviene mai a gesto in corso.
     */
    @SuppressLint("ClickableViewAccessibility")
    private fun bindTouch(ctx: Context, view: View) {
        val slop = dp(ctx, DRAG_SLOP_DP)
        var downRawY = 0f
        var startY = 0
        var dragged = false
        view.setOnTouchListener { _, event ->
            val lp = params ?: return@setOnTouchListener false
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downRawY = event.rawY
                    startY = lp.y
                    dragged = false
                    armHold()
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    if (expanded) return@setOnTouchListener true
                    val dy = event.rawY - downRawY
                    if (dragged || abs(dy) > slop) {
                        dragged = true
                        lp.y = clampY(ctx, startY + dy.toInt())
                        try {
                            windowManager?.updateViewLayout(root, lp)
                        } catch (e: Exception) {
                            Log.i(TAG, "Drag update failed: ${e.javaClass.simpleName}")
                        }
                    }
                    true
                }
                MotionEvent.ACTION_UP -> {
                    when {
                        dragged -> {
                            parkedY = lp.y
                            saveParkPosition(ctx)
                        }
                        // Un tap sulla mascotte a chat aperta la richiude: è il
                        // gesto inverso di quello che l'ha aperta.
                        isChatOpen -> collapse()
                        // Finestra grande ma solo per il fumetto (una risposta
                        // arrivata dopo il collasso): il tap qui vuol dire
                        // «rispondo», non «via».
                        else -> expand(withInput = true)
                    }
                    true
                }
                MotionEvent.ACTION_CANCEL -> true
                else -> false
            }
        }
    }

    // ------------------------------------------------------------------ //
    // Invio e risposta                                                    //
    // ------------------------------------------------------------------ //

    private fun send() {
        val ctx = appContext ?: return
        val field = input ?: return
        val text = field.text?.toString()?.trim().orEmpty()
        if (text.isEmpty()) return
        field.setText("")
        waitingForReply = true
        syncFace()
        bubble?.visibility = View.GONE
        main.removeCallbacks(holdRunnable)
        cancelTimeout()
        main.postDelayed(timeoutRunnable, REPLY_TIMEOUT_MS)
        GatewayService.deliverFloatingText(text) { ok ->
            if (!ok) main.post { onDeliveryFailed(ctx) }
        }
    }

    /** Il gateway non ha preso il testo: si dice subito, non fra 90 secondi. */
    private fun onDeliveryFailed(ctx: Context) {
        cancelTimeout()
        waitingForReply = false
        syncFace()
        bubble?.let {
            it.text = ctx.getString(R.string.floating_not_running)
            it.visibility = View.VISIBLE
        }
        armHold()
    }

    private fun onReplyTimeout() {
        val ctx = appContext ?: return
        waitingForReply = false
        syncFace(sad = true)
        bubble?.let {
            it.text = ctx.getString(R.string.floating_no_reply)
            it.visibility = View.VISIBLE
        }
        armHold()
    }

    private fun cancelTimeout() = main.removeCallbacks(timeoutRunnable)

    /**
     * Riarma la chiusura per inattività, o la sospende.
     *
     * Due casi in cui non si chiude affatto, e sono le due volte in cui
     * sparire sarebbe peggio di restare:
     *
     * * **si sta aspettando una risposta** — il timeout ha già la sua scadenza,
     *   e sparire a metà attesa lascerebbe l'utente senza sapere se la domanda
     *   è partita;
     * * **c'è del testo nel campo** — chi sta scrivendo non è un utente
     *   assente. Il timer si riarma a ogni carattere, ma fra un carattere e il
     *   successivo possono passare venti secondi: pensare a come finire la
     *   frase è esattamente ciò che somiglia di più all'inattività.
     */
    private fun armHold() {
        main.removeCallbacks(holdRunnable)
        if (waitingForReply) return
        if (!input?.text.isNullOrBlank()) return
        main.postDelayed(holdRunnable, replyHoldMs)
    }

    // ------------------------------------------------------------------ //
    // Sprite, geometria, preferenze                                       //
    // ------------------------------------------------------------------ //

    private fun syncFace(sad: Boolean = false) {
        val body = if (waitingForReply) "jenny-body-front-think" else "jenny-body-front-idle"
        val face = when {
            sad -> "jenny-face-front-sad"
            waitingForReply -> "jenny-face-front-thinking"
            else -> "jenny-face-front-normal"
        }
        mascotBody?.setImageBitmap(sprite(body))
        mascotFace?.setImageBitmap(sprite(face))
    }

    private fun sprite(name: String): Bitmap? = sprites.getOrPut(name) {
        val ctx = appContext ?: return@getOrPut null
        val file = File(ctx.filesDir, "workspace/ui/assets/$name.webp")
        if (!file.isFile) {
            Log.i(TAG, "Sprite not found: ${file.name}")
            return@getOrPut null
        }
        try {
            BitmapFactory.decodeFile(file.absolutePath)
        } catch (e: Exception) {
            Log.i(TAG, "Sprite could not be decoded: ${file.name}")
            null
        }
    }

    /** Colonna fumetto+mascotte posizionata dove sta la finestra parcheggiata. */
    private fun placeColumn(ctx: Context, left: Int, top: Int) {
        val col = column ?: return
        val lp = col.layoutParams as? FrameLayout.LayoutParams ?: return
        val metrics = ctx.resources.displayMetrics
        // Il fumetto sta sopra la testa, quindi la colonna comincia più in alto
        // della mascotte: se non ci sta, si scende invece di uscire dallo schermo.
        val bubbleRoom = dp(ctx, 120)
        lp.gravity = Gravity.TOP or Gravity.START
        lp.leftMargin = min(max(left, 0), max(metrics.widthPixels - dp(ctx, MASCOT_DP), 0))
        lp.topMargin = max(top - bubbleRoom, dp(ctx, 8))
        col.layoutParams = lp
    }

    private fun resetColumn() {
        val col = column ?: return
        val lp = col.layoutParams as? FrameLayout.LayoutParams ?: return
        lp.gravity = Gravity.TOP or Gravity.START
        lp.leftMargin = 0
        lp.topMargin = 0
        col.layoutParams = lp
    }

    /** Sempre a destra: il trascinamento oggi è solo verticale, quindi un
     *  lato da ricordare sarebbe una preferenza che nessuno può cambiare. */
    private fun parkX(ctx: Context): Int {
        val size = dp(ctx, MASCOT_DP)
        val out = (size * PARK_OUT_RATIO).toInt()
        return ctx.resources.displayMetrics.widthPixels - size + out
    }

    private fun parkY(ctx: Context): Int {
        if (parkedY >= 0) return clampY(ctx, parkedY)
        val height = ctx.resources.displayMetrics.heightPixels
        return clampY(ctx, (height * 0.45f).toInt())
    }

    /**
     * Tiene la mascotte dentro la parte utile dello schermo.
     *
     * I margini si leggono dalle metriche e **non** dagli insets della finestra:
     * una finestra `FLAG_NOT_FOCUSABLE` può non riceverne affatto, e una
     * geometria che dipende da qualcosa che può non arrivare mai è il modo in
     * cui una mascotte finisce sotto la tacca.
     */
    private fun clampY(ctx: Context, y: Int): Int {
        val size = dp(ctx, MASCOT_DP)
        val height = ctx.resources.displayMetrics.heightPixels
        val top = dp(ctx, 48)
        val bottom = height - size - dp(ctx, 48)
        return min(max(y, top), max(bottom, top))
    }

    private fun loadParkPosition(ctx: Context) {
        parkedY = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getInt(PREF_Y, -1)
    }

    private fun saveParkPosition(ctx: Context) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putInt(PREF_Y, parkedY)
            .apply()
    }

    private fun openChat(ctx: Context) {
        collapse()
        try {
            val intent = Intent(ctx, MainActivity::class.java)
                .setAction(MainActivity.ACTION_OPEN_CHAT)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            ctx.startActivity(intent)
        } catch (e: Exception) {
            Log.i(TAG, "Could not open the chat: ${e.javaClass.simpleName}")
        }
    }

    private fun hideKeyboard(ctx: Context) {
        val field = input ?: return
        val imm = ctx.getSystemService(InputMethodManager::class.java) ?: return
        imm.hideSoftInputFromWindow(field.windowToken, 0)
        field.clearFocus()
    }

    private fun dp(ctx: Context, value: Int): Int =
        (value * ctx.resources.displayMetrics.density).toInt()
}

// Nota deliberata, perché la tentazione di aggiungerla è forte: gli alert di
// sistema **non** vengono soppressi mentre la chat della mascotte è aperta.
//
// Sembrerebbe simmetrico al gate `MainActivity.isInForeground` di
// `NotifierBridge.postAlert`, e sarebbe sbagliato per due ragioni che si
// sommano. La prima è che il doppio squillo che quel gate evita qui non esiste
// già: la risposta a una domanda fatta dal fumetto viaggia sul canale
// `floating`, e il mirror sulla vista WebUI la marca con `origin_channel` —
// `ws_sender` non squilla sui messaggi con origin. La seconda è che gli alert
// che arriverebbero in quel momento sono **proattivi** (cron, heartbeat,
// aggiornamenti), cioè parole che la mascotte non mostra: sopprimerli
// significherebbe farli sparire del tutto, non evitare di ripeterli.
