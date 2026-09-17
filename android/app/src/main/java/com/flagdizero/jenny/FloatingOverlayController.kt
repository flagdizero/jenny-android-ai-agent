package com.flagdizero.jenny

import android.animation.ObjectAnimator
import android.animation.ValueAnimator
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
import android.view.VelocityTracker
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import android.view.animation.AccelerateDecelerateInterpolator
import android.view.animation.OvershootInterpolator
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

    /**
     * Lato dello sprite finché la SPA non ha detto la sua, in dp.
     *
     * È la `sm` della WebUI — 120 px CSS — perché su questa WebView un px CSS
     * vale un dp, e questa è la taglia con cui la mascotte in chat nasce. Non
     * è un valore scelto qui: è il default di `MASCOT_SIZES` in
     * `shared/mascot.js`, e serve solo al primo avvio, prima che
     * `setMascotSize` porti la misura vera.
     */
    private const val MASCOT_FALLBACK_DP = 120

    /** Tetti di sicurezza sulla taglia spinta dalla SPA, in px. */
    private const val MASCOT_MIN_PX = 48
    private const val MASCOT_MAX_PX = 600

    /**
     * I due ancoraggi orizzontali, in frazioni del lato dello sprite.
     *
     * Copiati da `mobile-style.css` (`.jenny-duo` e `.jenny-duo.out`), dove la
     * mascotte in chat vive con gli stessi due numeri: a riposo poco meno di
     * metà quadrato resta fuori schermo, e quando è attiva rientra a un quarto.
     * Sono frazioni e non pixel per la stessa ragione scritta là: la stessa
     * camminata deve finire esattamente sul bordo a ogni taglia.
     */
    private const val DOCKED_OUT_RATIO = 0.469f
    private const val OUT_RATIO = 0.25f

    /** Quanto dura lo scivolamento fra i due ancoraggi. `.jenny-duo.side-left`
     *  usa 0,3 s con questa curva, ed è la stessa transizione. */
    private const val SIDE_SLIDE_MS = 300L

    /**
     * L'altezza della banda del composer, in dp.
     *
     * Non è una stima: `buildInputRow` mette un tasto d'invio da 46 dp e 12 dp
     * di padding sopra e sotto, e l'altezza di una `LinearLayout` è quella del
     * figlio più alto più i padding. Il campo, con i suoi 12+12 attorno a una
     * riga di testo, resta sotto i 46.
     */
    private const val COMPOSER_DP = 46 + 12 + 12

    /** Aria fra la testa e la barra di input. */
    private const val COMPOSER_GAP_DP = 8

    /** Ripiego per l'altezza della barra di navigazione, se il sistema non la
     *  dice: una finestra `FLAG_NOT_FOCUSABLE` può non ricevere insets. */
    private const val NAV_FALLBACK_DP = 24

    /** Oltre questo spostamento il gesto è un trascinamento e non un tap. */
    private const val DRAG_SLOP_DP = 8

    /** Quanto si aspetta la risposta prima di dire che non arriva. Uguale al
     *  `REPLY_TIMEOUT_MS` della minichat della WebUI: è lo stesso agente, con
     *  gli stessi tempi, e due soglie diverse per la stessa attesa sarebbero
     *  due verità diverse su quando Jenny è in ritardo. */
    private const val REPLY_TIMEOUT_MS = 90_000L

    /** Ripiego se Python non ha ancora spinto la config. */
    private const val DEFAULT_REPLY_HOLD_S = 20

    /** Il palco che dorme: si vede, non si tocca, non prende il fuoco. */
    private const val STAGE_ASLEEP = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
        WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
        WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS

    /** ...e sveglio: prende i tocchi ma non il fuoco (il solo fumetto). */
    private const val STAGE_TOUCHABLE = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
        WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS

    /** ...e in chat: tocchi e fuoco, per il campo di testo. */
    private const val STAGE_CHAT = WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS

    private const val PREFS = "jenny_floating"

    /**
     * L'unica cosa che si ricorda: su quale bordo si è posata.
     *
     * **L'altezza no.** È la riga sopra la barra di input, in ogni stato —
     * l'invariante che `.jenny-duo` dichiara nel CSS («Non deve mai cambiare
     * in Y») — ed è anche il pavimento del volo, come `fs.y0` in JS. Per un
     * giro (17/09) si è provato a farla cadere fino in fondo e restare dove
     * atterrava: finiva sempre in un angolo, mezza fuori, sotto le icone del
     * dock di chiunque. La UI ha una riga sola, e questa è quella.
     */
    private const val PREF_RIGHT = "park_right"

    /** La taglia spinta dalla SPA, in px. */
    private const val PREF_SIZE = "mascot_px"

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

    /** Il **palco**: quello che si vede. Schermo intero, immobile. */
    private var root: FrameLayout? = null
    private var params: WindowManager.LayoutParams? = null

    /**
     * La finestra **di lei**: grande quanto lo sprite, toccabile (quindi mai
     * tappata in opacità), e **non si ridimensiona mai** — si sposta e basta.
     */
    private var mascotWin: FrameLayout? = null
    private var mascotWinParams: WindowManager.LayoutParams? = null

    /** La **maniglia**: quello che si tocca. Trasparente e vuota, ed è l'unica
     *  che cambia taglia — a schermo intero è l'arena del volo. */
    private var grip: View? = null
    private var gripParams: WindowManager.LayoutParams? = null

    /** L'arte del volo, nel palco: l'unica cosa che ci si disegna. */
    private var flightArt: ImageView? = null
    private var mascotBody: ImageView? = null
    private var mascotFace: ImageView? = null
    private var bubble: TextView? = null
    private var scrim: View? = null
    private var inputRow: View? = null
    private var input: EditText? = null
    /** Il riquadro della mascotte (corpo + faccia). Si chiama così da quando
     *  il fumetto ha smesso di stargli sopra in colonna. */
    private var column: FrameLayout? = null

    private val sprites = HashMap<String, Bitmap?>()

    /** Su quale dei due bordi. Default destra, come la mascotte in chat. */
    private var parkedRight = true

    /**
     * Lato dello sprite in px, così com'è nella WebUI. `0` = non lo so ancora.
     *
     * Lo scrive la SPA (`shared/mascot.js` → `JennyNative.setMascotSize`) con
     * la taglia scelta in Impostazioni → Personalizzazione, già moltiplicata
     * per il `devicePixelRatio` della WebView: così qui non si stima niente e
     * le due mascotte sono grandi uguali per costruzione, non per taratura.
     * Si memorizza perché la finestra vive nel processo del service e può
     * comparire prima che la SPA abbia caricato.
     */
    private var mascotPx = 0

    private var waitingForReply = false

    /** Il respiro in corso (bob o wobble), o `null` se sta ferma. */
    private var breath: ObjectAnimator? = null

    /** Sta scivolando verso un ancoraggio? Finché è vero il respiro aspetta:
     *  animano la stessa `translationY`, e insieme la fanno tremare. */
    private var sliding = false

    /** Lo scivolamento fra i due ancoraggi: muove la **finestra**. */
    private var slide: ValueAnimator? = null

    /** Il volo in corso, o `null` se sta ferma. */
    private var flight: FloatingFlight? = null

    /** Dove sta la maniglia da ferma: segue il riquadro (v. `placeColumn`). */
    private var gripX = 0
    private var gripY = 0

    /** Dove il dito ha toccato per ultimo, in coordinate schermo. */
    private var downFingerX = 0f
    private var downFingerY = 0f

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
        startBreathing()
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

    /**
     * La taglia della mascotte, in px, spinta dalla **WebUI**.
     *
     * Non è un'impostazione a sé: è *la* taglia, quella scelta in Impostazioni
     * → Personalizzazione (`sm`/`md`/`lg` → 120/160/210 px CSS in
     * `shared/mascot.js`), già moltiplicata per il `devicePixelRatio` della
     * WebView da chi chiama. Due mascotte della stessa persona non possono
     * essere grandi in due modi, e l'unico modo perché non lo siano è che il
     * numero venga da un posto solo.
     *
     * Si può chiamare da qualunque thread e in qualunque momento, anche a
     * finestra non montata: il valore si ricorda e vale al prossimo montaggio.
     */
    fun setMascotSize(px: Int) {
        val wanted = px.coerceIn(MASCOT_MIN_PX, MASCOT_MAX_PX)
        main.post {
            if (wanted == mascotPx) return@post
            mascotPx = wanted
            appContext?.let { ctx ->
                ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                    .edit().putInt(PREF_SIZE, wanted).apply()
                applyMascotSize(ctx)
            }
            Log.i(TAG, "Mascot size set to ${wanted}px")
        }
    }

    /**
     * Rimisura il riquadro e tutto ciò che ne dipende.
     *
     * In volo non si tocca niente: la fisica è stata costruita con il lato di
     * partenza, e cambiarlo a metà caduta la farebbe saltare. La taglia nuova
     * è già memorizzata, e il volo successivo nasce con quella.
     */
    private fun applyMascotSize(ctx: Context) {
        if (flight != null) return
        if (column == null) return
        val size = mascotSize(ctx)
        for (layer in listOfNotNull(mascotBody, mascotFace, flightArt)) {
            layer.layoutParams = layer.layoutParams?.also {
                it.width = size
                it.height = size
            }
        }
        mascotWinParams?.let { lp ->
            lp.width = size
            lp.height = size
            mascotWin?.let {
                try {
                    windowManager?.updateViewLayout(it, lp)
                } catch (e: Exception) {
                    Log.i(TAG, "Could not resize her: ${e.javaClass.simpleName}")
                }
            }
        }
        placeColumn(ctx, parkX(ctx, out = expanded), parkTop(ctx))
        setGrip(ctx, arena = false)
        if (expanded) startBreathing()
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
            val lp = stageParams()
            wm.addView(container, lp)
            windowManager = wm
            root = container
            params = lp
            // Ordine di inserimento = ordine di sovrapposizione: palco in
            // fondo, lei in mezzo, maniglia sopra. I tocchi li prende sempre
            // la maniglia, che è l'unica a poter crescere fino a coprire lo
            // schermo senza che si veda.
            val box = buildMascotWindow(ctx)
            val blp = mascotWinParams(ctx)
            wm.addView(box, blp)
            mascotWin = box
            mascotWinParams = blp
            val handle = buildGrip(ctx)
            val glp = gripParams(ctx)
            wm.addView(handle, glp)
            grip = handle
            gripParams = glp
            // **Dopo** `buildMascotWindow`: è lì che nascono i due livelli
            // dell'arte, e un `syncFace` prima di loro non disegna niente —
            // cioè una mascotte accesa e invisibile.
            syncFace()
            placeColumn(ctx, parkX(ctx), parkTop(ctx))
            Log.i(TAG, "Floating mascot attached (x=${glp.x} y=${glp.y})")
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
        // Un volo lasciato aperto qui tornerebbe a mordere al prossimo
        // montaggio: `startFlight` nasconde la mascotte ferma con un `post`
        // che guarda `flight != null`, e la spegnerebbe appena riaccesa.
        flight?.cancel()
        flight = null
        val wm = windowManager
        if (wm != null) {
            for (v in listOfNotNull(grip, mascotWin, root)) {
                try {
                    wm.removeView(v)
                } catch (e: Exception) {
                    Log.i(TAG, "Floating mascot already detached: ${e.javaClass.simpleName}")
                }
            }
        }
        grip = null
        gripParams = null
        mascotWin = null
        mascotWinParams = null
        root = null
        params = null
        windowManager = null
        flightArt = null
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

    /**
     * Il palco: **schermo intero, a (0,0), per sempre**.
     *
     * È la riga che chiude il difetto misurato il 17/09 fotogramma per
     * fotogramma. Prima la finestra visibile cambiava taglia a ogni apertura e
     * chiusura, e le due cose che decidono dove sta la mascotte — la `x/y`
     * della finestra e il margine del riquadro dentro di essa — non atterrano
     * nello stesso fotogramma: il margine è layout locale e arriva subito, il
     * ridimensionamento passa dal WindowManager e arriva dopo. Nel mezzo si
     * vedeva un fotogramma con il riquadro a `(0,0)` di una finestra ancora
     * intera, cioè la mascotte **nell'angolo in alto a sinistra**, e da lì
     * partiva la discesa: misurata a `t=16,807 s`, riquadro all'angolo, e 290
     * ms di scivolata fino al bordo.
     *
     * Con il palco immobile quel fotogramma non esiste: il margine del riquadro
     * **è** la sua posizione sullo schermo, sempre, in ogni stato.
     *
     * Non è toccabile di suo — a schermo intero si mangerebbe ogni tocco del
     * telefono. Il tocco ce l'ha [gripParams], che è trasparente.
     */
    private fun stageParams(): WindowManager.LayoutParams {
        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.MATCH_PARENT,
            overlayType(),
            STAGE_ASLEEP,
            PixelFormat.TRANSLUCENT
        )
        lp.gravity = Gravity.TOP or Gravity.START
        lp.x = 0
        lp.y = 0
        return lp
    }

    /**
     * La maniglia: un riquadro trasparente grande quanto lo sprite, sopra di
     * lei, che porta il `setOnTouchListener`.
     *
     * È l'unica finestra che cambia taglia — piccola da parcheggiata, intera
     * durante il volo (l'arena) — e siccome non disegna niente, cambiarla non
     * si vede. Tutta la classe di difetti «la mascotte salta quando la
     * finestra cambia» muore qui.
     */
    /** La finestra di lei. Toccabile di proposito: v. [buildMascotWindow]. */
    private fun mascotWinParams(ctx: Context): WindowManager.LayoutParams {
        val size = mascotSize(ctx)
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
        lp.y = parkTop(ctx)
        return lp
    }

    private fun gripParams(ctx: Context): WindowManager.LayoutParams {
        val size = mascotSize(ctx)
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
        lp.y = parkTop(ctx)
        return lp
    }

    /** Il palco riceve tocchi e fuoco solo quando c'è qualcosa da toccare. */
    private fun applyStage(flags: Int, softInput: Int) {
        val wm = windowManager ?: return
        val view = root ?: return
        val lp = params ?: return
        if (lp.flags == flags && lp.softInputMode == softInput) return
        lp.flags = flags
        lp.softInputMode = softInput
        try {
            wm.updateViewLayout(view, lp)
        } catch (e: Exception) {
            Log.i(TAG, "Could not change the stage: ${e.javaClass.simpleName}")
        }
    }

    /**
     * Sposta/ridimensiona la maniglia. *arena* la porta a schermo intero per
     * il volo; *touchable* la spegne quando è il palco a prendere i tocchi.
     */
    private fun setGrip(ctx: Context, arena: Boolean) {
        val wm = windowManager ?: return
        val view = grip ?: return
        val lp = gripParams ?: return
        val size = mascotSize(ctx)
        val w = if (arena) WindowManager.LayoutParams.MATCH_PARENT else size
        val x = if (arena) 0 else gripX
        val y = if (arena) 0 else gripY
        if (lp.width == w && lp.x == x && lp.y == y) return
        lp.width = w
        lp.height = w
        lp.x = x
        lp.y = y
        try {
            wm.updateViewLayout(view, lp)
        } catch (e: Exception) {
            Log.i(TAG, "Could not resize the grip: ${e.javaClass.simpleName}")
        }
    }

    /**
     * Allarga la finestra a schermo intero.
     *
     * Con *withInput* prende anche il **fuoco**. Tre cose lo rendono vero, e
     * tutte e tre sono state pagate sul telefono il 17/09:
     *
     * **`FLAG_LAYOUT_NO_LIMITS` non si toglie mai.** Il primo giro lo toglieva
     * in CHAT, per far mordere `ADJUST_RESIZE`, e con quello si portava via
     * l'unica cosa che qui conta davvero: senza il flag la finestra viene
     * insettata dalle barre di sistema, quindi la sua origine **non è più**
     * l'angolo dello schermo. Tutte le posizioni di questo file sono in px
     * schermo; una cornice che si sposta di una status bar fra uno stato e
     * l'altro è esattamente la mascotte che schizza e torna a ogni tocco. Il
     * flag resta su in tutti gli stati, e origine finestra = origine schermo
     * per definizione.
     *
     * **La tastiera si schiva con gli insets, non col ridimensionamento.** È
     * `SOFT_INPUT_ADJUST_NOTHING`: la finestra non si muove di un pixel, e il
     * listener in `buildViews` trasforma l'inset dell'IME nel padding basso
     * della riga di input. Meno flag, e nessuno che sposti la mascotte.
     *
     * **Il fuoco si chiede dopo il relayout, non nello stesso giro.** Togliere
     * `FLAG_NOT_FOCUSABLE` non dà il fuoco all'istante: il sistema deve
     * rifare il layout e riassegnarlo. Chiedendolo subito si misurava
     * `mCurrentFocus=null` a finestra già `fillxfill` e focusable, l'`EditText`
     * senza input connection e `mImeWindowVis=0` — cioè la tastiera non si
     * apriva mai, che è tutto il punto della funzione. Ora lo chiede
     * `onWindowFocusChanged` del contenitore, che scatta quando il fuoco
     * c'è davvero.
     *
     * Senza *withInput* la finestra resta intera ma **non focusable**: serve
     * al solo fumetto, e non ruba all'app sotto un fuoco che nessuno le ha
     * chiesto di cedere.
     */
    private fun expand(withInput: Boolean, forFlight: Boolean = false) {
        val ctx = appContext ?: return
        if (expanded && isChatOpen == withInput) return

        // Il palco non si muove e non cambia taglia: cambia solo *cosa
        // accetta*. La maniglia resta toccabile e resta dov'è — è piccola e
        // copre solo lei, quindi il velo e il campo li raggiungi lo stesso, e
        // un tocco su di lei è comunque un tocco su di lei.
        applyStage(
            if (withInput) STAGE_CHAT else STAGE_TOUCHABLE,
            if (withInput) {
                WindowManager.LayoutParams.SOFT_INPUT_ADJUST_NOTHING or
                    WindowManager.LayoutParams.SOFT_INPUT_STATE_VISIBLE
            } else {
                WindowManager.LayoutParams.SOFT_INPUT_STATE_UNSPECIFIED
            },
        )
        expanded = true
        isChatOpen = withInput
        scrim?.visibility = if (withInput) View.VISIBLE else View.GONE
        inputRow?.visibility = if (withInput) View.VISIBLE else View.GONE
        if (!forFlight) {
            // In volo non si scivola all'ancoraggio e non si respira: comanda
            // la fisica, e due animazioni sulla stessa view si contendono la
            // stessa traslazione.
            //
            // Rientra dall'ancoraggio docked a quello *out*, solo in
            // orizzontale: è già alla sua riga, e la barra compare sotto di lei.
            syncFace()
            slideTo(ctx, parkX(ctx, out = true), parkTop(ctx))
        }
        if (withInput) input?.let { it.post { focusTheField(ctx) } }
        armHold()
        Log.i(TAG, "Floating mascot expanded (input=$withInput)")
    }

    /**
     * Mette il fuoco sul campo e alza la tastiera.
     *
     * Chiamata due volte di proposito — subito dopo il relayout e di nuovo da
     * `onWindowFocusChanged` — perché quale delle due arriva buona dipende da
     * quanto ci mette il sistema a riassegnare il fuoco, e non è una cosa su
     * cui valga la pena scommettere. Idempotente: a fuoco già preso
     * `requestFocus` è un no-op e `showSoftInput` su una tastiera già alzata
     * pure.
     */
    private fun focusTheField(ctx: Context) {
        val field = input ?: return
        if (!isChatOpen) return
        field.isFocusableInTouchMode = true
        field.requestFocus()
        val imm = ctx.getSystemService(InputMethodManager::class.java) ?: return
        if (!imm.showSoftInput(field, InputMethodManager.SHOW_IMPLICIT)) {
            // Il primo tentativo può cadere se la finestra non ha ancora il
            // fuoco: si riprova al giro successivo del Looper invece di
            // lasciare un campo che lampeggia il cursore e non scrive.
            field.postDelayed({
                if (isChatOpen) imm.showSoftInput(field, InputMethodManager.SHOW_IMPLICIT)
            }, 120)
        }
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
        stopBreathing()
        syncFace()

        applyStage(STAGE_ASLEEP, WindowManager.LayoutParams.SOFT_INPUT_STATE_UNSPECIFIED)
        // Torna al bordo **scivolando**, com'è uscita: stessa curva e stessa
        // durata dell'uscita, dentro lo stesso palco fermo. La maniglia la
        // raggiunge a fine corsa (`slideTo` → `placeColumn` → `syncGrip`).
        slideTo(ctx, parkX(ctx), parkTop(ctx))
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

            /** Il momento in cui il fuoco c'è **davvero**.
             *
             *  Togliere `FLAG_NOT_FOCUSABLE` non lo consegna all'istante, e
             *  chiederlo prima di qui lasciava la tastiera chiusa con il
             *  cursore che lampeggiava (misurato: `mCurrentFocus=null` a
             *  finestra già focusable). */
            override fun onWindowFocusChanged(hasWindowFocus: Boolean) {
                super.onWindowFocusChanged(hasWindowFocus)
                if (hasWindowFocus && isChatOpen) focusTheField(ctx)
            }
        }

        // Un velo, non un blackout: trasparente in alto — quello che stavi
        // guardando resta leggibile — e sempre più scuro verso il campo, che è
        // dove deve andare l'occhio. Il grigio uniforme al 40% del primo giro
        // sembrava un difetto di rendering, non una scelta.
        val dim = View(ctx).apply {
            background = GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM,
                intArrayOf(0x00000000, 0x40000000, 0xCC000000.toInt())
            )
            visibility = View.GONE
            setOnClickListener { collapse() }
        }
        container.addView(dim, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT
        ))
        scrim = dim

        // Il fumetto è ancorato **per il basso**, appena sopra la testa: così
        // cresce verso l'alto quando il testo è lungo e la mascotte non si
        // sposta di un pixel. Tenerli in una colonna verticale era il difetto
        // del 17/09 — per far stare il fumetto bisognava riservargli lo spazio
        // *prima*, e la mascotte saltava su di 120 dp nell'istante in cui la si
        // toccava.
        val side = mascotSize(ctx)
        val speech = TextView(ctx).apply {
            setTextColor(0xFFF5F0E8.toInt())
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            maxLines = 10
            visibility = View.GONE
            background = bubbleBackground()
            val padH = dp(ctx, 14)
            val padV = dp(ctx, 11)
            setPadding(padH, padV, padH, padV)
            // Il fumetto porta alla conversazione vera: è l'unico posto in cui
            // c'è tutto il resto, dato che qui si vede solo l'ultima risposta.
            setOnClickListener { openChat(ctx) }
        }
        // Largo quanto il testo, non quanto lo schermo: «ciao» in una striscia
        // nera da bordo a bordo non somiglia a qualcuno che parla, somiglia a
        // un banner. Il tetto serve alle risposte lunghe, che altrimenti
        // uscirebbero dallo schermo.
        speech.maxWidth = (screenWidth(ctx) * 0.78f).toInt()
        container.addView(speech, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT
        ).apply { gravity = Gravity.BOTTOM or Gravity.START })
        bubble = speech

        // Nel palco ci sta **solo l'arte del volo**, e solo mentre vola.
        //
        // La mascotte ferma vive nella maniglia, non qui, per una ragione di
        // sistema: un overlay non fidato con `FLAG_NOT_TOUCHABLE` viene tappato
        // da Android a 0,8 di opacità (protezione anti-tapjacking, si legge in
        // `dumpsys` come `alpha=0.8`), e il palco da fermo *deve* essere
        // `NOT_TOUCHABLE` o si mangerebbe ogni tocco del telefono. Disegnarla
        // là vorrebbe dire una Jenny semitrasparente, sempre.
        //
        // In volo il problema non c'è: l'arena è la maniglia, il palco può
        // essere toccabile (nessuno lo raggiunge, la maniglia gli sta sopra) e
        // quindi opaco. E a schermo intero l'oscillazione non viene ritagliata.
        val flight = ImageView(ctx).apply {
            scaleType = ImageView.ScaleType.FIT_CENTER
            visibility = View.GONE
        }
        container.addView(flight, FrameLayout.LayoutParams(side, side).apply {
            gravity = Gravity.TOP or Gravity.START
        })
        flightArt = flight

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
                    // Gli stessi valori di ``buildInputRow``: questo ramo ne
                    // riscrive il padding, e due numeri diversi per la stessa
                    // riga si notano solo quando la tastiera si alza.
                    val padH = dp(ctx, 14)
                    val padV = dp(ctx, 12)
                    it.setPadding(padH, padV, padH, padV + max(ime, bars))
                }
                insets
            }
        }

        syncFace()
        return container
    }

    /**
     * La maniglia: il riquadro che si tocca — **e in cui lei vive**.
     *
     * Grande quanto lo sprite, toccabile, quindi fuori dal tetto di opacità
     * che Android mette agli overlay `NOT_TOUCHABLE`: è l'unico posto in cui
     * si può disegnare a piena opacità qualcosa che sta sempre a schermo.
     */
    @SuppressLint("ClickableViewAccessibility")
    private fun buildGrip(ctx: Context): View {
        val handle = View(ctx)
        bindTouch(ctx, handle)
        return handle
    }

    /**
     * La finestra di lei: i due livelli dell'arte, e niente altro.
     *
     * **Non si ridimensiona mai.** È la regola che tiene in piedi tutto il
     * resto: dove sta a schermo è la `x/y` di questa finestra, punto, quindi
     * non esiste un fotogramma in cui due contabilità divergono. Il volo non
     * la fa crescere — per quello c'è la maniglia, che è trasparente — e la
     * scivolata fra gli ancoraggi muove lei, non un figlio dentro di lei.
     *
     * È **toccabile** anche se non riceve i tocchi (glieli prende la maniglia,
     * che le sta sopra): un overlay non fidato con `FLAG_NOT_TOUCHABLE` lo
     * paga in opacità — Android lo tappa a 0,8 contro il tapjacking — e una
     * Jenny semitrasparente non è una Jenny.
     */
    private fun buildMascotWindow(ctx: Context): FrameLayout {
        val side = mascotSize(ctx)
        val box = FrameLayout(ctx)
        val body = ImageView(ctx).apply { scaleType = ImageView.ScaleType.FIT_CENTER }
        val face = ImageView(ctx).apply { scaleType = ImageView.ScaleType.FIT_CENTER }
        box.addView(body, FrameLayout.LayoutParams(side, side))
        box.addView(face, FrameLayout.LayoutParams(side, side))
        mascotBody = body
        mascotFace = face
        column = box
        // **Fuori dalla zona del gesto «indietro».** Parcheggiata sporge dal
        // bordo per poco meno di metà quadrato: quel che resta visibile sta
        // tutto nella fascia in cui Android legge uno swipe come *back*, e
        // senza questa riga il sistema si prende il gesto al primo movimento.
        // Sta qui e non sulla maniglia perché questa finestra è sempre grande
        // quanto lei: la maniglia, in arena, coprirebbe tutto lo schermo.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            box.addOnLayoutChangeListener { v, _, _, _, _, _, _, _, _ ->
                v.systemGestureExclusionRects =
                    listOf(android.graphics.Rect(0, 0, v.width, v.height))
            }
        }
        return box
    }

    /** La maniglia si muove: **la finestra**, non il contenuto. Dentro è grande
     *  quanto lei, quindi una traslazione del riquadro verrebbe ritagliata. */
    private fun moveGrip(left: Int, top: Int) {
        val wm = windowManager ?: return
        // Lei e la maniglia sono la stessa cosa in due strati: si spostano
        // insieme, o il dito finisce per cercarla dove non è più.
        mascotWinParams?.let { lp ->
            if (lp.x != left || lp.y != top) {
                lp.x = left
                lp.y = top
                mascotWin?.let {
                    try {
                        wm.updateViewLayout(it, lp)
                    } catch (e: Exception) {
                        Log.i(TAG, "Could not move her: ${e.javaClass.simpleName}")
                    }
                }
            }
        }
        val view = grip ?: return
        val lp = gripParams ?: return
        if (lp.width != mascotWinParams?.width) return  // in arena non la segue
        if (lp.x == left && lp.y == top) return
        lp.x = left
        lp.y = top
        try {
            wm.updateViewLayout(view, lp)
        } catch (e: Exception) {
            Log.i(TAG, "Could not move the grip: ${e.javaClass.simpleName}")
        }
    }

    /**
     * Il composer: un campo tondo e un tasto d'invio, non una striscia di testo.
     *
     * La prima versione era un `EditText` nudo su una banda scura, e sul
     * telefono si leggeva come una cosa rotta: nessun bordo, nessun bottone,
     * niente che dicesse «si scrive qui». Le forme sono quelle del composer
     * della chat, perché è la stessa cosa in un posto diverso.
     */
    private fun buildInputRow(ctx: Context): View {
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            val padH = dp(ctx, 14)
            val padV = dp(ctx, 12)
            setPadding(padH, padV, padH, padV)
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
            isFocusableInTouchMode = true
            background = GradientDrawable().apply {
                setColor(0xF01C1A18.toInt())
                cornerRadius = dp(ctx, 24).toFloat()
                setStroke(dp(ctx, 1), 0x33F5F0E8)
            }
            val padH = dp(ctx, 18)
            val padV = dp(ctx, 12)
            setPadding(padH, padV, padH, padV)
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

        val sendButton = TextView(ctx).apply {
            text = "\u2191"
            gravity = Gravity.CENTER
            setTextColor(0xFF141210.toInt())
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 20f)
            background = GradientDrawable().apply {
                setColor(0xFFF5F0E8.toInt())
                shape = GradientDrawable.OVAL
            }
            setOnClickListener { send() }
        }
        val side = dp(ctx, 46)
        row.addView(sendButton, LinearLayout.LayoutParams(side, side).apply {
            leftMargin = dp(ctx, 10)
        })

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
     * Tap e presa sulla mascotte.
     *
     * Il tap apre la chat, ma solo dopo che il dito si è alzato. Oltre la
     * soglia il gesto diventa una **presa**, e da lì comanda il volo Pegman
     * (`FloatingFlight`): la finestra passa a schermo intero — che è l'arena
     * del volo — e lei penzola dalla mano fino al rilascio.
     *
     * La promozione della finestra a dito abbassato è sicura per una ragione
     * misurata: il gesto è tutto in coordinate **schermo** (`rawX`/`rawY`),
     * quindi ridimensionare la finestra sotto il dito non sposta di un pixel
     * la matematica.
     */
    @SuppressLint("ClickableViewAccessibility")
    private fun bindTouch(ctx: Context, view: View) {
        val slop = dp(ctx, DRAG_SLOP_DP)
        var downRawX = 0f
        var downRawY = 0f
        var grabbed = false
        var tracker: VelocityTracker? = null
        view.setOnTouchListener { _, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    if (flight?.isFlying == true) return@setOnTouchListener true
                    downRawX = event.rawX
                    downRawY = event.rawY
                    grabbed = false
                    tracker = VelocityTracker.obtain()
                    tracker?.addMovement(event)
                    // L'arena si apre **subito**, col dito ancora fermo.
                    //
                    // Non è un'ottimizzazione: la finestra parcheggiata è
                    // grande quanto lo sprite, e questa ROM **annulla il
                    // gesto** appena il dito ne esce — che è ciò che succede al
                    // primo strattone, perché la molla la fa restare indietro.
                    // Misurato il 17/09: `ACTION_CANCEL` al secondo evento, con
                    // la mascotte che cadeva da ferma. A schermo intero il dito
                    // non può uscire da nessuna parte.
                    openArena(ctx)
                    armHold()
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    tracker?.addMovement(event)
                    if (!grabbed) {
                        val dx = event.rawX - downRawX
                        val dy = event.rawY - downRawY
                        if (abs(dx) <= slop && abs(dy) <= slop) return@setOnTouchListener true
                        grabbed = true
                        downFingerX = event.rawX
                        downFingerY = event.rawY
                        startFlight(ctx)
                    }
                    flight?.moveTo(event.rawX, event.rawY)
                    true
                }
                MotionEvent.ACTION_UP -> {
                    if (grabbed) {
                        tracker?.computeCurrentVelocity(1000)
                        flight?.release(tracker?.xVelocity ?: 0f, tracker?.yVelocity ?: 0f)
                    } else {
                        when {
                            // Un tap a chat aperta la richiude: è il gesto
                            // inverso di quello che l'ha aperta.
                            isChatOpen -> collapse()
                            // Finestra grande ma solo per il fumetto: il tap
                            // qui vuol dire «rispondo», non «via».
                            else -> expand(withInput = true)
                        }
                    }
                    tracker?.recycle()
                    tracker = null
                    restGrip(ctx)
                    true
                }
                MotionEvent.ACTION_CANCEL -> {
                    if (grabbed) {
                        flight?.release(0f, 0f)
                    } else if (!isChatOpen) {
                        // Il sistema si è preso il gesto (un bordo, una
                        // notifica). L'arena era già aperta dal `DOWN` e a
                        // schermo intero **inghiotte ogni tocco**: lasciarla lì
                        // fino allo scadere del timer è un telefono morto per
                        // venti secondi. Si richiude adesso.
                        collapse()
                    }
                    tracker?.recycle()
                    tracker = null
                    restGrip(ctx)
                    true
                }
                else -> false
            }
        }
    }

    /**
     * Porta la finestra a schermo intero senza cambiare nient'altro.
     *
     * Serve al tocco, non all'aspetto: è l'arena in cui il dito può muoversi e
     * la mascotte può volare. Resta **non focusable** — nessuna tastiera
     * rubata a chi sta sotto — e la mascotte resta esattamente dov'era, perché
     * la posizione che aveva come origine della finestra diventa un margine
     * dentro di essa.
     */
    private fun openArena(ctx: Context) {
        // A chat aperta l'arena c'è già: è il palco, intero e toccabile.
        // Crescere qui vorrebbe dire mettere una finestra nuova sotto un dito
        // già appoggiato, e il gesto arriva a destinazione come `CANCEL`.
        if (isChatOpen) return
        // Cresce la **maniglia**, che è trasparente: il dito non può più
        // uscirne, e non si vede niente cambiare. Il palco resta com'è.
        setGrip(ctx, arena = true)
    }

    /**
     * Fine del gesto: la maniglia torna quella che deve essere.
     *
     * Vale per ogni uscita, `UP` e `CANCEL`, perché una maniglia rimasta
     * grande è trasparente **e si mangia ogni tocco del telefono** — il guasto
     * più silenzioso che questa finestra possa produrre. In volo no: là
     * l'arena serve fino all'atterraggio.
     */
    private fun restGrip(ctx: Context) {
        if (flight != null) return
        setGrip(ctx, arena = false)
    }

    /**
     * La presa: da qui in poi disegna il volo.
     *
     * **La finestra resta della taglia dello sprite e si muove**, un fotogramma
     * alla volta, esattamente come faceva il trascinamento. La prima versione
     * la promuoveva a schermo intero per avere l'arena, ed è stata smentita dal
     * telefono nel modo più netto: ridimensionare una finestra sotto il dito
     * **annulla il gesto**. Misurato il 17/09 — `ACTION_CANCEL` cinque
     * millisecondi dopo la presa, e la mascotte che cadeva da ferma senza
     * essersi mossa. Spostarla, invece, il tocco lo tiene: è la differenza fra
     * `updateViewLayout` che cambia `x`/`y` e uno che cambia `width`/`height`.
     *
     * L'oscillazione ci sta dentro lo stesso: il personaggio occupa circa il
     * 45% del canvas quadrato e il resto è margine trasparente, quindi anche
     * inclinata di 78° resta dentro il suo riquadro.
     */
    private fun startFlight(ctx: Context) {
        val mascot = column ?: return
        val art = flightArt ?: return
        val size = mascotSize(ctx)
        val metrics = ctx.resources.displayMetrics
        val startLeft = gripX.toFloat()
        val startTop = gripY.toFloat()

        stopBreathing()
        mascot.animate().cancel()
        cancelTimeout()
        main.removeCallbacks(holdRunnable)
        bubble?.visibility = View.GONE
        // Si può prenderla anche a chat aperta: allora il campo e il velo se
        // ne vanno, perché mentre vola non c'è niente a cui scrivere. La
        // finestra resta grande — è già l'arena — ma smette di prendere il
        // fuoco, così la tastiera non resta appesa a mezz'aria.
        if (isChatOpen) hideKeyboard(ctx)
        isChatOpen = false
        expanded = false
        scrim?.visibility = View.GONE
        inputRow?.visibility = View.GONE
        // **In volo il palco è toccabile**, e quindi opaco: l'arena è la
        // maniglia, che gli sta sopra a schermo intero, quindi nessun tocco
        // arriva davvero qui — ma un palco `NOT_TOUCHABLE` la disegnerebbe
        // all'80%. È anche l'unico posto in cui l'oscillazione non viene
        // ritagliata dai bordi del suo riquadro.
        applyStage(STAGE_TOUCHABLE, WindowManager.LayoutParams.SOFT_INPUT_STATE_UNSPECIFIED)

        // Passaggio di consegne: prima si accende l'arte del volo esattamente
        // dov'è lei, e **solo al giro dopo** si spegne quella ferma. Un
        // fotogramma in cui si sovrappongono non si vede; uno in cui manca sì.
        art.pivotX = size * FloatingFlight.PIVOT_X
        art.pivotY = size * FloatingFlight.PIVOT_Y
        art.translationX = startLeft
        art.translationY = startTop
        art.rotation = 0f
        art.scaleX = 1f
        art.setImageBitmap(sprite("jenny-hang"))
        art.visibility = View.VISIBLE
        main.post { if (flight != null) mascot.visibility = View.INVISIBLE }

        val width = screenWidth(ctx)
        val dockY = parkTop(ctx).toFloat() + size * FloatingFlight.PIVOT_Y
        val leftDock = -(size * DOCKED_OUT_RATIO) + size * FloatingFlight.PIVOT_X
        val rightDock = width - size + size * DOCKED_OUT_RATIO +
            size * FloatingFlight.PIVOT_X
        flight = FloatingFlight(
            sizePx = size.toFloat(),
            viewportW = width.toFloat(),
            viewportH = screenHeight(ctx).toFloat(),
            density = metrics.density,
            dockPivotY = dockY,
            dockPivotX = leftDock to rightDock,
            onFrame = { left, top, rot, pose, flip -> drawFlight(left, top, rot, pose, flip) },
            onSettled = { right -> endFlight(ctx, right) },
        ).also {
            it.grab(
                startLeft + size * FloatingFlight.PIVOT_X,
                startTop + size * FloatingFlight.PIVOT_Y,
                downFingerX,
            )
            it.moveTo(downFingerX, downFingerY)
        }
        Log.i(TAG, "Pegman flight started")
    }

    private fun drawFlight(
        left: Float,
        top: Float,
        rotationDeg: Float,
        pose: FloatingFlight.Pose,
        flip: Boolean,
    ) {
        val art = flightArt ?: return
        art.translationX = left
        art.translationY = top
        art.rotation = rotationDeg
        art.scaleX = if (flip) -1f else 1f
        val name = when (pose) {
            FloatingFlight.Pose.HANG -> "jenny-hang"
            FloatingFlight.Pose.FALL -> "jenny-fall"
            FloatingFlight.Pose.GROUND -> "jenny-ground"
            FloatingFlight.Pose.WALK1 -> "jenny-walk1"
            FloatingFlight.Pose.WALK2 -> "jenny-walk2"
        }
        art.setImageBitmap(sprite(name))
    }

    /** Atterrata e riagganciata alla sua riga: torna docked, con l'arte del
     *  bordo. La camminata finisce esattamente sull'ancoraggio docked, quindi
     *  il passaggio alla finestra piccola non la sposta di un pixel. */
    private fun endFlight(ctx: Context, right: Boolean) {
        flight = null
        parkedRight = right
        saveParkPosition(ctx)
        syncFace()
        // La camminata finisce esattamente sull'ancoraggio docked: la maniglia
        // ci si rimette sopra e torna piccola, e lei riappare lì dentro.
        placeColumn(ctx, parkX(ctx), parkTop(ctx))
        setGrip(ctx, arena = false)
        column?.visibility = View.VISIBLE
        // Consegna al contrario: si spegne l'arte del volo un giro dopo, per
        // non lasciare un fotogramma senza nessuna delle due.
        main.post {
            if (flight == null) {
                flightArt?.visibility = View.GONE
                applyStage(STAGE_ASLEEP, WindowManager.LayoutParams.SOFT_INPUT_STATE_UNSPECIFIED)
            }
        }
        Log.i(TAG, "Pegman flight settled (right=$right)")
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
        startBreathing()
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

    /**
     * L'arte giusta per lo stato, con la stessa regola della mascotte in chat.
     *
     * **Al bordo non va la faccia frontale.** Docked resta fuori schermo poco
     * meno di metà quadrato, e di una faccia si vedrebbe un occhio e mezza
     * bocca: è esattamente il motivo per cui `jenny-side` — l'arte diagonale,
     * con la faccia già disegnata dentro — esiste. Quando è *out* torna la
     * pila a due livelli, corpo × faccia, che è dove le espressioni si leggono.
     *
     * Lo specchio è quello di `.jenny-duo.side-left .jenny-art-stack`: l'arte
     * nasce guardando verso sinistra, cioè giusta sul bordo destro, e si
     * ribalta sull'altro.
     */
    private fun syncFace(sad: Boolean = false) {
        column?.scaleX = if (parkedRight) 1f else -1f
        if (!expanded) {
            mascotBody?.setImageBitmap(sprite("jenny-side"))
            mascotFace?.visibility = View.GONE
            return
        }
        mascotFace?.visibility = View.VISIBLE
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

    /**
     * Mette la mascotte, dentro la finestra grande, **dove stava** in quella
     * piccola: stesso pixel sullo schermo, nessun salto all'apertura.
     *
     * L'unica correzione è verso l'interno: da parcheggiata sporge oltre il
     * bordo, e a finestra intera si tira dentro del tutto — lì sta per
     * parlare, e una mascotte mezza fuori mentre risponde è solo scomoda.
     */
    private fun placeColumn(ctx: Context, left: Int, top: Int) {
        val size = mascotSize(ctx)
        // **Una sola cosa decide dove sta: la `x/y` della sua finestra.** Non
        // ci sono margini né traslazioni da tenere d'accordo, ed è per questo
        // che non esiste più un fotogramma in cui le due contabilità
        // divergono. Le trasformazioni si azzerano comunque: sono del respiro,
        // e il respiro riparte da zero.
        clearTransforms(ctx)
        gripX = left
        gripY = max(top, 0)
        if (flight == null) moveGrip(gripX, gripY)
        // Il fumetto finisce dove comincia la testa e cresce all'insù, e sta
        // dal lato in cui lei sta: parcheggiata a destra parla verso sinistra,
        // e viceversa. Il ritaglio dello sprite lascia dell'aria sopra la
        // testa, quindi si scende un po' dentro il riquadro invece di
        // ancorarsi al suo bordo — altrimenti il fumetto sembra staccato.
        (bubble?.layoutParams as? FrameLayout.LayoutParams)?.let { lp ->
            val headroom = (size * 0.22f).toInt()
            lp.gravity = Gravity.BOTTOM or if (parkedRight) Gravity.END else Gravity.START
            lp.bottomMargin = max(
                screenHeight(ctx) - max(top, 0) - headroom, dp(ctx, 8)
            )
            lp.leftMargin = dp(ctx, 14)
            lp.rightMargin = dp(ctx, 14)
            bubble?.layoutParams = lp
        }
    }

    /**
     * Scivola fino a *(left, top)* dentro la finestra grande, e **ci resta**.
     *
     * La curva e la durata sono quelle di `.jenny-duo.side-left` nel CSS —
     * 0,3 s con un rimbalzino finale — così il gesto è lo stesso che si vede
     * in chat. La differenza importante è la fine: la traslazione viene
     * *committata* nel margine e azzerata, altrimenti resta addosso al
     * riquadro e la transizione successiva la vede come uno scarto da
     * recuperare — cioè uno scatto.
     *
     * Il respiro parte solo dopo, perché anima la stessa `translationY`.
     */
    private fun slideTo(ctx: Context, left: Int, top: Int) {
        val fromX = gripX
        val fromY = gripY
        clearTransforms(ctx)
        if (fromX == left && fromY == top) {
            placeColumn(ctx, left, top)
            startBreathing()
            return
        }
        sliding = true
        slide?.cancel()
        slide = ValueAnimator.ofFloat(0f, 1f).apply {
            duration = SIDE_SLIDE_MS
            interpolator = OvershootInterpolator(1.1f)
            addUpdateListener { a ->
                val k = a.animatedValue as Float
                moveGrip(
                    (fromX + (left - fromX) * k).toInt(),
                    (fromY + (top - fromY) * k).toInt(),
                )
            }
            addListener(object : android.animation.AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: android.animation.Animator) {
                    if (!sliding) return
                    sliding = false
                    placeColumn(ctx, left, top)
                    startBreathing()
                }
            })
            start()
        }
    }

    /** Ferma ogni animazione sul riquadro e lo rimette dritto e non traslato.
     *  I due pivot tornano al centro: il volo li sposta sulla manica alzata,
     *  e uno specchio o una rotazione attorno a quel punto sposta anche lei. */
    private fun clearTransforms(ctx: Context) {
        val mascot = column ?: return
        sliding = false
        slide?.cancel()
        slide = null
        mascot.animate().cancel()
        breath?.cancel()
        breath = null
        val half = mascotSize(ctx) / 2f
        mascot.pivotX = half
        mascot.pivotY = half
        mascot.translationX = 0f
        mascot.translationY = 0f
        mascot.rotation = 0f
    }

    /**
     * Il respiro: `jenny-bob` quando è fuori, `jenny-wobble` mentre pensa.
     *
     * Stessi tempi e stesse origini del CSS. L'ampiezza però **non** si copia
     * in pixel: là sono 4 px su uno sprite da 120, qui il lato è un altro, e un
     * respiro copiato in pixel sarebbe un respiro più corto. Si porta il
     * rapporto.
     */
    private fun startBreathing() {
        val ctx = appContext ?: return
        val mascot = column ?: return
        if (sliding || flight != null || !expanded) return
        stopBreathing()
        if (waitingForReply) {
            mascot.pivotX = mascot.width / 2f
            mascot.pivotY = mascot.height * 0.9f
            breath = ObjectAnimator.ofFloat(mascot, View.ROTATION, -2.5f, 2.5f).apply {
                duration = 1_100
                repeatCount = ValueAnimator.INFINITE
                repeatMode = ValueAnimator.REVERSE
                interpolator = AccelerateDecelerateInterpolator()
                start()
            }
            return
        }
        val amplitude = -mascotSize(ctx) * (4f / 120f)
        breath = ObjectAnimator.ofFloat(mascot, View.TRANSLATION_Y, 0f, amplitude).apply {
            duration = 3_400
            repeatCount = ValueAnimator.INFINITE
            repeatMode = ValueAnimator.REVERSE
            interpolator = AccelerateDecelerateInterpolator()
            start()
        }
    }

    /** Ferma il respiro e rimette la posa a zero. Docked e in mano sta ferma. */
    private fun stopBreathing() {
        breath?.cancel()
        breath = null
        column?.let {
            it.translationY = 0f
            it.rotation = 0f
        }
    }


    /**
     * L'ascissa del bordo, docked o *out*.
     *
     * Gli stessi due ancoraggi della mascotte in chat: `-0.469 × lato` a riposo,
     * `-0.25 × lato` quando è attiva, specchiati sul bordo sinistro.
     */
    /** Il lato dello sprite: quello della WebUI, o il suo default. */
    private fun mascotSize(ctx: Context): Int =
        if (mascotPx > 0) mascotPx else dp(ctx, MASCOT_FALLBACK_DP)

    private fun parkX(ctx: Context, out: Boolean = false): Int {
        val size = mascotSize(ctx)
        val hidden = (size * if (out) OUT_RATIO else DOCKED_OUT_RATIO).toInt()
        return if (parkedRight) screenWidth(ctx) - size + hidden else -hidden
    }

    /**
     * Le misure dello schermo, **sempre da qui**.
     *
     * `displayMetrics` di un contesto applicativo può riportare la finestra
     * dell'ultima Activity invece del display, e una geometria che sbaglia di
     * qualche decina di pixel si vede come una mascotte che si ferma prima del
     * bordo. `currentWindowMetrics.bounds` è il display, insets compresi — la
     * stessa cornice in cui vive una finestra `FLAG_LAYOUT_NO_LIMITS`.
     */
    private fun screenWidth(ctx: Context): Int = screenBounds(ctx).first

    private fun screenHeight(ctx: Context): Int = screenBounds(ctx).second

    private fun screenBounds(ctx: Context): Pair<Int, Int> {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val bounds = ctx.getSystemService(WindowManager::class.java)
                ?.currentWindowMetrics?.bounds
            if (bounds != null && bounds.width() > 0 && bounds.height() > 0) {
                return bounds.width() to bounds.height()
            }
        }
        val metrics = ctx.resources.displayMetrics
        return metrics.widthPixels to metrics.heightPixels
    }

    /**
     * L'ordinata, **derivata e mai memorizzata**: la riga appena sopra la barra
     * di input — `bottom: dock-height + 58px + scope-row` nel CSS, che qui è
     * la banda del composer più un filo d'aria.
     *
     * È l'invariante di `.jenny-duo` («Non deve mai cambiare in Y»), è dove
     * *risiede*, è il pavimento del volo e la riga a cui la camminata torna.
     * Si calcola anche a composer nascosto: la banda esiste come misura pure
     * quando non è a schermo, altrimenti la mascotte salterebbe nell'istante
     * in cui compare.
     */
    private fun parkTop(ctx: Context): Int {
        val size = mascotSize(ctx)
        val band = dp(ctx, COMPOSER_DP) + dp(ctx, COMPOSER_GAP_DP) + navInset(ctx)
        return max(screenHeight(ctx) - band - size, dp(ctx, 8))
    }

    /**
     * L'altezza della barra di navigazione.
     *
     * Letta dalle metriche della finestra e non dagli insets consegnati: una
     * finestra `FLAG_NOT_FOCUSABLE` può non riceverne affatto, e una geometria
     * che dipende da qualcosa che può non arrivare mai è il modo in cui una
     * mascotte finisce sotto la barra.
     */
    private fun navInset(ctx: Context): Int {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val insets = ctx.getSystemService(WindowManager::class.java)
                ?.currentWindowMetrics?.windowInsets
                ?.getInsets(WindowInsets.Type.navigationBars())
            // Uno zero qui è una risposta, non un silenzio: su questo telefono
            // la navigazione è a gesti e la barra non c'è. Il primo giro lo
            // trattava come «non lo so» e regalava 24 dp a una barra che non
            // esiste. Il ripiego resta per il solo ramo pre-R.
            if (insets != null) return insets.bottom
        }
        return dp(ctx, NAV_FALLBACK_DP)
    }

    private fun loadParkPosition(ctx: Context) {
        val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        parkedRight = prefs.getBoolean(PREF_RIGHT, true)
        if (mascotPx <= 0) mascotPx = prefs.getInt(PREF_SIZE, 0)
    }

    private fun saveParkPosition(ctx: Context) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(PREF_RIGHT, parkedRight)
            // La taglia può essere arrivata dalla SPA prima che ci fosse un
            // contesto con cui scriverla: qui c'è di sicuro.
            .apply { if (mascotPx > 0) putInt(PREF_SIZE, mascotPx) }
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
