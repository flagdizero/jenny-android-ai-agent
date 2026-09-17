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

    /** Lato dello sprite. La `sm` della WebUI è 120 px CSS; qui è in dp e un
     *  filo più piccola, perché là la mascotte sta dentro una pagina e qui sta
     *  sopra il lavoro di qualcun altro. */
    private const val MASCOT_DP = 96

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

    /** «Non lo so ancora»: la prima volta la sua casa è la riga del composer. */
    private const val NO_TOP = Int.MIN_VALUE

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

    /** Su quale bordo si è posata. */
    private const val PREF_RIGHT = "park_right"

    /**
     * ...e a che altezza.
     *
     * Nella prima stesura non si memorizzava: la Y era fissa alla riga sopra
     * la barra di input, copiando l'invariante che `.jenny-duo` dichiara nel
     * CSS. Dentro la SPA quella riga *è* il pavimento; sopra le altre app non
     * c'è niente di disegnato là, e il volo finiva contro un pavimento
     * invisibile a un terzo di schermo dal fondo. Adesso cade fino in fondo e
     * si ferma dove atterra — e la riga sopra il composer resta la sua casa,
     * quella da cui parte e quella a cui la chat la riporta.
     */
    private const val PREF_TOP = "park_top"

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
    /** Il riquadro della mascotte (corpo + faccia). Si chiama così da quando
     *  il fumetto ha smesso di stargli sopra in colonna. */
    private var column: FrameLayout? = null

    private val sprites = HashMap<String, Bitmap?>()

    /** Su quale dei due bordi. Default destra, come la mascotte in chat. */
    private var parkedRight = true

    /** Ordinata del riquadro da parcheggiata, in px schermo. `NO_TOP` finché
     *  non se ne sa niente: allora è la riga sopra il composer. */
    private var parkedTop = NO_TOP
    private var waitingForReply = false

    /** Il respiro in corso (bob o wobble), o `null` se sta ferma. */
    private var breath: ObjectAnimator? = null

    /** Sta scivolando verso un ancoraggio? Finché è vero il respiro aspetta:
     *  animano la stessa `translationY`, e insieme la fanno tremare. */
    private var sliding = false

    /** Il volo in corso, o `null` se sta ferma. */
    private var flight: FloatingFlight? = null

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
        lp.y = parkTop(ctx)
        return lp
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
        val wm = windowManager ?: return
        val view = root ?: return
        val lp = params ?: return
        if (expanded && isChatOpen == withInput) return

        if (!expanded) {
            // La finestra passa a (0,0), quindi la posizione che aveva come
            // origine diventa un margine dentro di essa: stesso pixel sullo
            // schermo, nessun salto.
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
            WindowManager.LayoutParams.SOFT_INPUT_ADJUST_NOTHING or
                WindowManager.LayoutParams.SOFT_INPUT_STATE_VISIBLE
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
        if (!forFlight) {
            // In volo non si scivola all'ancoraggio e non si respira: comanda
            // la fisica, e due animazioni sulla stessa view si contendono la
            // stessa traslazione.
            //
            // Aprire la chat la **solleva**, non la trasloca: rientra dal
            // bordo e, se sta più in basso della riga del composer, sale fin
            // lì per non finirci sotto — è la richiesta «deve risiedere sopra
            // la barra input», detta nell'unico momento in cui la barra c'è.
            // Il posto suo resta quello in cui l'hai lasciata: alla chiusura
            // `collapse` la rimette a `parkTop`, che qui non si tocca.
            syncFace()
            slideTo(ctx, parkX(ctx, out = true), min(parkTop(ctx), composerLineTop(ctx)))
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
        stopBreathing()
        lp.width = size
        lp.height = size
        lp.x = parkX(ctx)
        lp.y = parkTop(ctx)
        lp.flags = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
        lp.softInputMode = WindowManager.LayoutParams.SOFT_INPUT_STATE_UNSPECIFIED
        resetColumn()
        syncFace()
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

        val mascotSize = dp(ctx, MASCOT_DP)
        val mascot = FrameLayout(ctx)
        val body = ImageView(ctx).apply { scaleType = ImageView.ScaleType.FIT_CENTER }
        val face = ImageView(ctx).apply { scaleType = ImageView.ScaleType.FIT_CENTER }
        mascot.addView(body, FrameLayout.LayoutParams(mascotSize, mascotSize))
        mascot.addView(face, FrameLayout.LayoutParams(mascotSize, mascotSize))
        mascotBody = body
        mascotFace = face
        // **Fuori dalla zona del gesto «indietro».** Parcheggiata sporge dal
        // bordo per poco meno di metà quadrato: quel che resta visibile — una
        // ventina di dp — sta tutto dentro la fascia in cui Android legge uno
        // swipe come *back*. Senza questa riga il sistema si prende il gesto
        // al primo movimento, la finestra riceve `ACTION_CANCEL`, lei cade da
        // ferma e chi sta sotto torna indietro di una schermata. La SPA fa
        // esattamente questo via `JennyNative.setGestureExclusion`; qui la view
        // è nostra e il rettangolo è il suo, in coordinate sue, quindi segue
        // da solo margini, traslazioni e specchio. (Tetto di sistema: 200 dp
        // per bordo; 96 ci stanno.)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            mascot.systemGestureExclusionRects =
                listOf(android.graphics.Rect(0, 0, mascotSize, mascotSize))
        }
        bindTouch(ctx, mascot)
        container.addView(mascot, FrameLayout.LayoutParams(mascotSize, mascotSize).apply {
            gravity = Gravity.TOP or Gravity.START
        })
        column = mascot

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
        if (expanded) return
        val wm = windowManager ?: return
        val view = root ?: return
        val lp = params ?: return
        placeColumn(ctx, lp.x, lp.y)
        lp.width = WindowManager.LayoutParams.MATCH_PARENT
        lp.height = WindowManager.LayoutParams.MATCH_PARENT
        lp.x = 0
        lp.y = 0
        try {
            wm.updateViewLayout(view, lp)
        } catch (e: Exception) {
            Log.i(TAG, "Could not open the arena: ${e.javaClass.simpleName}")
            return
        }
        expanded = true
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
        val size = dp(ctx, MASCOT_DP)
        val metrics = ctx.resources.displayMetrics
        val mascotLp = mascot.layoutParams as? FrameLayout.LayoutParams
        val startLeft = (mascotLp?.leftMargin ?: 0).toFloat()
        val startTop = (mascotLp?.topMargin ?: 0).toFloat()

        stopBreathing()
        mascot.animate().cancel()
        cancelTimeout()
        main.removeCallbacks(holdRunnable)
        bubble?.visibility = View.GONE
        // Si può prenderla anche a chat aperta: allora il campo e il velo se
        // ne vanno, perché mentre vola non c'è niente a cui scrivere. La
        // finestra resta grande — è già l'arena — ma smette di prendere il
        // fuoco, così la tastiera non resta appesa a mezz'aria.
        if (isChatOpen) {
            hideKeyboard(ctx)
            isChatOpen = false
            scrim?.visibility = View.GONE
            inputRow?.visibility = View.GONE
            params?.let { lp ->
                lp.flags = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
                lp.softInputMode = WindowManager.LayoutParams.SOFT_INPUT_STATE_UNSPECIFIED
                try {
                    windowManager?.updateViewLayout(root, lp)
                } catch (e: Exception) {
                    Log.i(TAG, "Could not drop focus for the flight: ${e.javaClass.simpleName}")
                }
            }
        }
        mascot.translationX = 0f
        mascot.translationY = 0f
        mascot.pivotX = size * FloatingFlight.PIVOT_X
        mascot.pivotY = size * FloatingFlight.PIVOT_Y
        mascotFace?.visibility = View.GONE

        val width = screenWidth(ctx)
        val floorY = floorTop(ctx).toFloat() + size * FloatingFlight.PIVOT_Y
        val leftDock = -(size * DOCKED_OUT_RATIO) + size * FloatingFlight.PIVOT_X
        val rightDock = width - size + size * DOCKED_OUT_RATIO +
            size * FloatingFlight.PIVOT_X
        flight = FloatingFlight(
            sizePx = size.toFloat(),
            viewportW = width.toFloat(),
            viewportH = screenHeight(ctx).toFloat(),
            density = metrics.density,
            floorPivotY = floorY,
            dockPivotX = leftDock to rightDock,
            onFrame = { left, top, rot, pose, flip -> drawFlight(left, top, rot, pose, flip) },
            onSettled = { right, top -> endFlight(ctx, right, top) },
        ).also {
            it.grab(
                startLeft + size * FloatingFlight.PIVOT_X,
                startTop + size * FloatingFlight.PIVOT_Y,
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
        val mascot = column ?: return
        (mascot.layoutParams as? FrameLayout.LayoutParams)?.let { lp ->
            if (lp.leftMargin != 0 || lp.topMargin != 0) {
                lp.leftMargin = 0
                lp.topMargin = 0
                mascot.layoutParams = lp
            }
        }
        mascot.translationX = left
        mascot.translationY = top
        mascot.rotation = rotationDeg
        mascot.scaleX = if (flip) -1f else 1f
        val name = when (pose) {
            FloatingFlight.Pose.HANG -> "jenny-hang"
            FloatingFlight.Pose.FALL -> "jenny-fall"
            FloatingFlight.Pose.GROUND -> "jenny-ground"
            FloatingFlight.Pose.WALK1 -> "jenny-walk1"
            FloatingFlight.Pose.WALK2 -> "jenny-walk2"
        }
        mascotBody?.setImageBitmap(sprite(name))
    }

    /**
     * Atterrata e riagganciata: torna docked, con l'arte del bordo.
     *
     * **Il posto in cui si è fermata diventa il suo.** Non c'è più una riga
     * fissa a cui tornare: cade fino in fondo, si rialza, cammina fino al
     * bordo più vicino e lì resta, finché non la si riprende o non si apre la
     * chat — che è l'unica cosa che la riporta sopra il composer.
     */
    private fun endFlight(ctx: Context, right: Boolean, top: Float) {
        flight = null
        parkedRight = right
        parkedTop = top.toInt().coerceIn(dp(ctx, 8), floorTop(ctx))
        saveParkPosition(ctx)
        clearTransforms(ctx)
        collapse()
        Log.i(TAG, "Pegman flight settled (right=$right top=$parkedTop)")
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
        val mascot = column ?: return
        val size = dp(ctx, MASCOT_DP)
        // **Le trasformazioni si azzerano qui, sempre.** La posizione a schermo
        // è la somma di tre cose scritte da tre posti diversi — la `x/y` della
        // finestra, i margini del riquadro e la traslazione delle animazioni —
        // e ogni transizione che ne dimenticava una la spostava. Da qui in poi
        // ne esiste una sola: il margine. Chi anima committa nel margine quando
        // ha finito (v. `slideTo`).
        clearTransforms(ctx)
        (mascot.layoutParams as? FrameLayout.LayoutParams)?.let { lp ->
            lp.gravity = Gravity.TOP or Gravity.START
            lp.leftMargin = left
            lp.topMargin = max(top, 0)
            mascot.layoutParams = lp
        }
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
        val mascot = column ?: return
        val lp = mascot.layoutParams as? FrameLayout.LayoutParams ?: return
        val dx = (left - lp.leftMargin).toFloat()
        val dy = (top - lp.topMargin).toFloat()
        clearTransforms(ctx)
        if (dx == 0f && dy == 0f) {
            startBreathing()
            return
        }
        sliding = true
        mascot.animate()
            .translationX(dx)
            .translationY(dy)
            .setDuration(SIDE_SLIDE_MS)
            .setInterpolator(OvershootInterpolator(1.1f))
            .withEndAction {
                // `cancel()` passa di qui esattamente come un arrivo: senza
                // questa riga `clearTransforms` — che cancella — rientrerebbe
                // in `placeColumn`, che richiama `clearTransforms`. Il flag,
                // azzerato prima del cancel, distingue i due casi.
                if (!sliding) return@withEndAction
                sliding = false
                placeColumn(ctx, left, top)
                startBreathing()
            }
            .start()
    }

    /** Ferma ogni animazione sul riquadro e lo rimette dritto e non traslato.
     *  I due pivot tornano al centro: il volo li sposta sulla manica alzata,
     *  e uno specchio o una rotazione attorno a quel punto sposta anche lei. */
    private fun clearTransforms(ctx: Context) {
        val mascot = column ?: return
        sliding = false
        mascot.animate().cancel()
        breath?.cancel()
        breath = null
        val half = dp(ctx, MASCOT_DP) / 2f
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
        val amplitude = -dp(ctx, MASCOT_DP) * (4f / 120f)
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

    private fun resetColumn() {
        appContext?.let { clearTransforms(it) }
        (column?.layoutParams as? FrameLayout.LayoutParams)?.let { lp ->
            lp.gravity = Gravity.TOP or Gravity.START
            lp.leftMargin = 0
            lp.topMargin = 0
            column?.layoutParams = lp
        }
    }

    /**
     * L'ascissa del bordo, docked o *out*.
     *
     * Gli stessi due ancoraggi della mascotte in chat: `-0.469 × lato` a riposo,
     * `-0.25 × lato` quando è attiva, specchiati sul bordo sinistro.
     */
    private fun parkX(ctx: Context, out: Boolean = false): Int {
        val size = dp(ctx, MASCOT_DP)
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
     * La riga appena sopra la barra di input: **la sua casa**.
     *
     * È dove sta appena installata, ed è dove la chat la riporta — la prima
     * delle tre richieste del secondo giro, «deve risiedere sopra la barra
     * input». Si calcola anche a composer nascosto: la banda esiste come
     * misura pure quando non è a schermo, altrimenti la mascotte salterebbe
     * nell'istante in cui compare.
     */
    private fun composerLineTop(ctx: Context): Int {
        val size = dp(ctx, MASCOT_DP)
        val band = dp(ctx, COMPOSER_DP) + dp(ctx, COMPOSER_GAP_DP) + navInset(ctx)
        return max(screenHeight(ctx) - band - size, dp(ctx, 8))
    }

    /** Il pavimento: i **piedi** sul fondo dello schermo, non il bordo del
     *  file. Lo stesso numero che `FloatingFlight` usa per il tonfo. */
    private fun floorTop(ctx: Context): Int {
        val size = dp(ctx, MASCOT_DP)
        return screenHeight(ctx) - (size * FloatingFlight.CONTENT_B).toInt()
    }

    /** Dove sta da parcheggiata: dove l'hai lasciata, o casa la prima volta. */
    private fun parkTop(ctx: Context): Int {
        val top = if (parkedTop == NO_TOP) composerLineTop(ctx) else parkedTop
        return top.coerceIn(dp(ctx, 8), floorTop(ctx))
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
        parkedTop = prefs.getInt(PREF_TOP, NO_TOP)
    }

    private fun saveParkPosition(ctx: Context) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(PREF_RIGHT, parkedRight)
            .putInt(PREF_TOP, parkedTop)
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
