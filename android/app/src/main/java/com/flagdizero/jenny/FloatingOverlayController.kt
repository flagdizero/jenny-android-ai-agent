package com.flagdizero.jenny

import android.animation.ObjectAnimator
import android.animation.ValueAnimator
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.PorterDuff
import android.graphics.PorterDuffColorFilter
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.Editable
import android.text.TextWatcher
import android.text.util.Linkify
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
import android.widget.ScrollView
import android.widget.TextView
import io.noties.markwon.AbstractMarkwonPlugin
import io.noties.markwon.Markwon
import io.noties.markwon.MarkwonConfiguration
import io.noties.markwon.SoftBreakAddsNewLinePlugin
import io.noties.markwon.core.MarkwonTheme
import io.noties.markwon.ext.strikethrough.StrikethroughPlugin
import io.noties.markwon.ext.tables.TablePlugin
import io.noties.markwon.ext.tables.TableTheme
import io.noties.markwon.ext.tasklist.TaskListPlugin
import io.noties.markwon.html.HtmlPlugin
import io.noties.markwon.linkify.LinkifyPlugin
import java.io.File
import kotlin.math.abs
import kotlin.math.max

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
 * chiamato da Python — e ci rientra **solo se la finestra è aperta**: questa
 * non si riapre da sola. Questo file non sa cosa sia una sessione, non parla con
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

    /** Quanto è larga la pillola, in frazione dello schermo. Su 574 dp fa 356. */
    private const val PILL_WIDTH_RATIO = 0.62f

    /**
     * Il lato della pallina d'invio, dentro la pillola.
     *
     * Più piccola del cap della pillola di proposito: a 38 in 46 erano due
     * cerchi a 4 dp l'uno dall'altro, quasi concentrici, e si leggeva come il
     * pomello di un interruttore, non come un tasto.
     */
    private const val SEND_DP = 30

    /** L'aria fra la pallina e il bordo della pillola. */
    private const val BAR_PAD_DP = 7

    /**
     * La pillola del composer, in dp: [SEND_DP] più [BAR_PAD_DP] sopra e sotto.
     *
     * Non è più una fascia a tutto schermo ma una pillola **corta e
     * centrata**, larga [PILL_WIDTH_RATIO] dello schermo — la proporzione
     * della barra di Claude o di Spotlight sul loro schermo — e lei la usa
     * come pavimento: sta in piedi sul cap del suo lato (v. [parkX] e
     * [parkTop]). A una riga: 30 + 7 + 7 = 44.
     */
    private const val PILL_DP = SEND_DP + 2 * BAR_PAD_DP

    /** Metà di [PILL_DP]: a una riga la pillola è esatta, e in multiriga gli
     *  angoli restano a 22 (rettangolo arrotondato, come `.compose-pill`). */
    private const val BAR_RADIUS_DP = 22

    /** Il padding del testo dal cap sinistro. */
    private const val BAR_TEXT_PAD_DP = 16

    /**
     * Dove stanno i suoi piedi e il suo asse nello sprite, in frazioni del lato.
     *
     * Misurati sui pixel opachi di `jenny-body-front-idle` (768 px): i piedi
     * finiscono alla riga 668 e il corpo occupa le colonne 229–572, quindi il
     * suo asse sta a 0,52 del quadrato — non a metà — con l'arte che guarda a
     * sinistra, cioè non specchiata; specchiata, l'asse è a 1 − 0,52. Servono
     * a metterla **in piedi sul cap** della pillola invece che al centro del
     * suo quadrato trasparente.
     *
     * [HEAD_RATIO] è l'altra estremità: serve a sapere quanto è alta la sua
     * parte *visibile*, che è lo spazio da lasciarle fra la pillola e la
     * conversazione — v. [standHeight]. Fra i due c'è il 72% del quadrato, il
     * resto è margine trasparente.
     *
     * **0,15 e non 0,33.** Il primo giro leggeva la cima dal *ciuffo*, a
     * occhio su uno screenshot, e si perdeva l'antenna dei capelli — che è
     * sottile e antialiasata, ma c'è. Misurato sul canale alfa dei tre
     * accoppiamenti corpo×faccia che questa finestra usa (riga 114 di 768 in
     * tutti e tre), l'antenna finiva sotto la bolla più bassa: 22 dp di
     * sovrapposizione che sul telefono si vedono.
     */
    private const val FEET_RATIO = 0.87f
    private const val HEAD_RATIO = 0.15f
    private const val AXIS_RATIO = 0.52f

    /** L'aria fra i suoi piedi e il bordo alto della pillola. */
    private const val FEET_GAP_DP = 2

    /** I due padding verticali della banda attorno alla pillola. Sopra poco,
     *  perché sopra c'è lei; sotto i 12 di sempre, più la tastiera quando c'è. */
    private const val COMPOSER_PAD_TOP_DP = 4
    private const val COMPOSER_PAD_BOTTOM_DP = 12

    /**
     * L'ombra sotto la pillola.
     *
     * È quel che la stacca dal wallpaper: fondo e bordo vengono dallo stesso
     * tema e sono due colori vicini su un terzo, e senza un'ombra la forma
     * sembra incollata sopra l'app di sotto. Il row lascia spazio attorno
     * (`clipToPadding = false`) perché l'ombra ha dove cadere.
     */
    private const val PILL_ELEVATION_DP = 8

    /**
     * Quanti scambi resta a schermo la conversazione.
     *
     * Quattro, poi i più vecchi cadono. Non c'è un «carica altro» e non è una
     * mancanza: oltre questi non esiste niente da caricare qui dentro. La
     * conversazione intera è quella dell'app, e il chip in cima alla lista è
     * il modo di arrivarci.
     */
    private const val HISTORY_MAX_TURNS = 4

    /**
     * Quanto respiro lasciare fra la cima della lista e il bordo alto dello
     * schermo. **È l'unico limite che ha**: per il resto la conversazione
     * prende tutto lo spazio che resta sopra di lei.
     *
     * C'era un tetto fisso da 300 dp, e teneva la lista a metà schermo con
     * mezzo telefono vuoto sopra. La conversazione è la cosa che si legge:
     * sale fin quasi in cima, e sotto resta solo lei e la pillola.
     */
    private const val LIST_TOP_MARGIN_DP = 24

    /**
     * Il margine laterale della lista, molto più stretto di quello della
     * pillola.
     *
     * Le bolle stavano larghe quanto la pillola — 62% dello schermo — e a una
     * conversazione servono i bordi: con questo una bolla corta arriva a
     * [LIST_EDGE_DP] più il padding della lista dal bordo dello schermo. La
     * pillola invece resta al 62%, che è la sua proporzione giusta.
     */
    private const val LIST_EDGE_DP = 10

    /** Il padding della lista: lo spazio in cui cadono le ombre delle bolle. */
    private const val LIST_PAD_DP = 6

    /** La sfumatura del bordo alto della lista: dice «c'è dell'altro sopra»
     *  senza disegnare niente, ed è quella di Android (`fadingEdge`). */
    private const val LIST_FADE_DP = 36

    /** L'aria fra i suoi piedi e la lista, sopra la sua testa. */
    private const val STAND_GAP_DP = 8

    /**
     * Quanto sta il chip sopra la pillola **a conversazione vuota**.
     *
     * Lì non c'è niente da cui stare lontani e lei è di lato, sul cap: il chip
     * è largo ~150 dp al centro e la sua parte visibile comincia una
     * quarantina di dp più in là, quindi può scendere fin qui senza toccarla.
     */
    private const val CHIP_GAP_DP = 10

    /** La bolla: raggio pieno, e l'angolo stretto in basso dal lato di chi
     *  parla — lo stesso `--radius-bubble` della chat, semplificato. */
    private const val BUBBLE_RADIUS_DP = 16
    private const val BUBBLE_CORNER_DP = 5

    /** Larghezza massima di una bolla, in frazione della lista: anche la più
     *  lunga lascia vedere da che parte sta chi l'ha scritta. */
    private const val BUBBLE_MAX_RATIO = 0.78f

    private const val BUBBLE_GAP_DP = 8

    /** Più leggera di [PILL_ELEVATION_DP]: la pillola resta il piano davanti. */
    private const val BUBBLE_ELEVATION_DP = 4

    /** Il lato nominale delle due icone disegnate (freccia d'invio e chip). */
    private const val SEND_ICON_DP = 14
    private const val CHIP_ICON_DP = 13

    /** Quanto dura la fioritura del tasto. È la `sendEnable` di
     *  `mobile-style.css`, che fa la stessa cosa nel composer della chat. */
    private const val SEND_BLOOM_MS = 400L

    /** Quanto sono opache le superfici di questa finestra. Galleggiano sopra
     *  l'app di qualcun altro: un filo di velo dice che non sono sue. */
    private const val SURFACE_ALPHA = 0xF0

    /** Ripiego per l'altezza della barra di navigazione, se il sistema non la
     *  dice: una finestra `FLAG_NOT_FOCUSABLE` può non ricevere insets. */
    private const val NAV_FALLBACK_DP = 24

    /** Ripiego per l'altezza della status bar, se il sistema non la dice. */
    private const val STATUS_FALLBACK_DP = 24

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

    /** ...e i colori del tema, nella stessa forma: sei interi separati da
     *  virgola, nell'ordine di [Palette]. */
    private const val PREF_PALETTE = "palette"

    /**
     * I colori della finestra flottante, presi dal tema scelto nell'app.
     *
     * Erano sette costanti esadecimali scritte qui dentro, ed erano la palette
     * `chanel`: chi sceglieva Synthwave si ritrovava la barra avorio sopra la
     * propria app rosa. Adesso arrivano dalla WebUI, che legge i token
     * *calcolati* del tema attivo — la stessa lettura che `shared/theme.js` fa
     * già per le barre di sistema e per le mini-app.
     *
     * Sei valori, non sette: [surface] veste sia la barra che il fumetto, ed è
     * la stessa coppia `--surface`/`--text` su cui si regge ogni superficie
     * della SPA. Il contrasto viene dal tema, non da una scelta fatta qui.
     */
    /** Una riga della conversazione. `mine` = l'ha scritta l'utente. */
    private data class Line(val mine: Boolean, val text: String)

    /**
     * Una `ScrollView` con un tetto d'altezza, che `ScrollView` non ha.
     *
     * Serve perché la colonna è ancorata in basso e cresce verso l'alto: senza
     * tetto, quattro scambi lunghi la farebbero uscire dallo schermo dalla
     * parte opposta alla pillola. Il tetto non è una costante — con la tastiera
     * alzata lo spazio è molto meno — quindi è una `var` che [syncListCap]
     * rimette a ogni cambio di geometria.
     *
     * La sfumatura in cima **non** è la sua: v. [historyVeil]. Il
     * `fadingEdge` di Android qui non serve, e non basterebbe — rende
     * trasparenti i pixel della bolla, e quello che affiora sotto è l'app di
     * sotto.
     */
    private class CappedScrollView(ctx: Context) : ScrollView(ctx) {
        /** `-1` = non ancora calcolato. **Zero è un tetto valido**: è il caso
         *  in cui non c'è spazio, e confonderlo con «nessun tetto» faceva
         *  crescere la lista fuori dal bordo alto proprio lì. */
        var maxHeight = -1

        override fun onMeasure(widthSpec: Int, heightSpec: Int) {
            val spec = if (maxHeight >= 0) {
                MeasureSpec.makeMeasureSpec(maxHeight, MeasureSpec.AT_MOST)
            } else {
                heightSpec
            }
            super.onMeasure(widthSpec, spec)
        }
    }

    private data class Palette(
        val surface: Int,
        val border: Int,
        val text: Int,
        val hint: Int,
        val accent: Int,
        val onAccent: Int,
    )

    /**
     * Il ripiego: `chanel`, cioè il tema di default della WebUI.
     *
     * Non sono i colori «di prima» ritoccati a mano — sono esattamente i token
     * che la SPA spingerebbe con quel tema (`mobile-style.css`, blocco
     * `:root`), quindi non esiste un montaggio in cui la finestra si veda
     * diversa da come si vedrà un istante dopo.
     */
    private val CHANEL = Palette(
        surface = 0xFF1E1E1E.toInt(),   // --surface
        border = 0x47F4F1EA,            // --border-strong: rgba(244,241,234,.28)
        text = 0xFFF4F1EA.toInt(),      // --text
        hint = 0x52F4F1EA,              // --text-faint: rgba(244,241,234,.32)
        accent = 0xFFF4F1EA.toInt(),    // --accent
        onAccent = 0xFF141414.toInt(),  // --on-accent
    )

    private val main = Handler(Looper.getMainLooper())

    /** Stato voluto da Python (`config.floating.enabled`). Separato dal fatto
     *  che la finestra sia a schermo: con l'app in primo piano la mascotte si
     *  nasconde pur restando accesa. */
    @Volatile
    private var enabled = false

    @Volatile
    private var replyHoldMs = DEFAULT_REPLY_HOLD_S * 1000L

    /**
     * La finestra è aperta? — e non c'è più un secondo flag accanto.
     *
     * Ce n'erano due, questo e `isChatOpen`, perché una risposta poteva aprire
     * la finestra **senza** il campo: si leggeva e basta, e il fuoco restava
     * all'app sotto. Da quando la risposta non riapre più niente da sé (v.
     * [showReply]) quella seconda apertura non ha più chiamanti, e i due flag
     * cambiavano comunque sempre insieme: tenerli separati voleva dire due
     * nomi per lo stesso stato e una dozzina di rami che nessun test poteva
     * raggiungere.
     */
    @Volatile
    private var expanded = false

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
    private var scrim: View? = null
    private var inputRow: View? = null
    /** La barra tonda: il bordo è suo, non del campo. */
    private var inputBar: LinearLayout? = null

    /** La conversazione a schermo, e le tre view che la disegnano. */
    private val history = ArrayList<Line>()
    private var historyScroll: CappedScrollView? = null
    private var historyList: LinearLayout? = null

    /** Il riquadro vuoto in cui lei sta in piedi, fra lista e pillola. */
    private var stand: View? = null

    /** Il passaggio all'app: primo figlio della lista, sempre presente. */
    private var openChip: TextView? = null

    /**
     * Il renderer markdown, costruito **dalla palette**, quindi buttato quando
     * la palette cambia: i colori del codice, delle citazioni e dei link sono
     * cotti dentro l'istanza, e riusarla dopo un cambio tema lascerebbe quelli
     * di prima in mezzo a una bolla vestita di nuovo. `null` = da ricostruire.
     */
    private var markdown: Markwon? = null

    /**
     * La fascia nera in cima alla lista: **sopra** le bolle, non dietro.
     *
     * Il `fadingEdge` di Android rende trasparenti i pixel della bolla più
     * vecchia, e sopra l'app di qualcun altro quello che affiora è l'app: la
     * frase si scioglieva in mezzo alle icone del launcher invece di
     * dissolversi. Dietro non si può rimediare — la sfumatura si applica
     * *dopo* `onDraw`, quindi cancellerebbe anche il nero — e allora il nero
     * sta davanti, e la sua opacità segue lo scorrimento.
     */
    private var historyVeil: View? = null

    /** L'altezza **misurata** della pillola, in px; `0` finché non ha fatto un
     *  layout. È quel che fa salire lei quando il testo va a capo: v. [parkTop]. */
    private var pillHeightPx = 0

    /** Quanto la tastiera (o la barra di sistema) alza il composer, in px. Vale
     *  solo a chat aperta, che è l'unico momento in cui arrivano gli insets. */
    private var chatBottomInsetPx = 0
    private var input: EditText? = null
    private var sendButton: ImageView? = null

    /** Il tasto è acceso? Serve solo a far fiorire la molla una volta sola. */
    private var sendLit = false

    /** I colori in vigore. Si ricordano come la taglia, e per la stessa
     *  ragione: la finestra vive nel processo del service e può comparire
     *  prima che la SPA abbia caricato. */
    @Volatile
    private var palette = CHANEL
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
     * Aggiunge *text* alla conversazione. `false` se non c'è niente a cui
     * aggiungerlo.
     *
     * **Non si riapre da sola**, ed è una scelta e non una mancanza: se nel
     * frattempo l'utente ha chiuso la finestra, l'ha chiusa apposta — far
     * saltare su un pannello sopra l'app che sta usando è esattamente ciò che
     * una mascotte non deve fare. La risposta non si perde: il canale è la
     * conversazione dell'app, e lì c'è già (v. `channels/floating.py`).
     *
     * Il `false` che torna qui non è un errore per nessuno: il canale lo
     * annota e tira dritto.
     *
     * **L'attesa però finisce lo stesso**, e questo va fatto *prima* del
     * `return`: la risposta è arrivata, che ci sia o no un posto dove
     * disegnarla. Tenendolo dopo, un volo preso mentre aspettava (che annulla
     * il timeout ma non lo stato) lasciava [waitingForReply] acceso per
     * sempre — faccia che pensa a ogni riapertura, e [armHold] che non arma
     * più niente perché il suo primo guardiano è proprio quello.
     */
    fun showReply(text: String): Boolean {
        cancelTimeout()
        waitingForReply = false
        if (!expanded || historyList == null) {
            // Non si disegna, ma la posa sì: non sta più aspettando.
            syncFace()
            return false
        }
        appendLine(mine = false, text = text)
        syncFace()
        startBreathing()
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
     * I colori del tema attivo, spinti dalla **WebUI**.
     *
     * Sei valori in `#AARRGGBB`. La conversione dalla forma funzionale la fa il
     * JavaScript, dove il valore è già risolto, e non è pignoleria: tre temi su
     * sette scrivono i bordi come `rgba(244, 241, 234, 0.28)`, e
     * `Color.parseColor` quella forma non la legge — solleva e basta.
     *
     * Un valore illeggibile non porta giù gli altri cinque: al suo posto resta
     * quello di [CHANEL], che è anche il ripiego dell'intera palette.
     *
     * Si può chiamare da qualunque thread e a finestra non montata: il valore
     * si ricorda e vale al prossimo montaggio.
     */
    fun setPalette(
        surface: String,
        border: String,
        text: String,
        hint: String,
        accent: String,
        onAccent: String,
    ) {
        val wanted = Palette(
            surface = color(surface, CHANEL.surface),
            border = color(border, CHANEL.border),
            text = color(text, CHANEL.text),
            hint = color(hint, CHANEL.hint),
            accent = color(accent, CHANEL.accent),
            onAccent = color(onAccent, CHANEL.onAccent),
        )
        main.post {
            if (wanted == palette) return@post
            palette = wanted
            appContext?.let { ctx ->
                ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                    .edit().putString(PREF_PALETTE, wanted.store()).apply()
            }
            applyPalette()
            Log.i(TAG, "Floating palette updated")
        }
    }

    private fun color(value: String, fallback: Int): Int = try {
        Color.parseColor(value.trim())
    } catch (e: IllegalArgumentException) {
        Log.w(TAG, "Floating palette: cannot read '$value', keeping the default")
        fallback
    }

    private fun Palette.store(): String =
        listOf(surface, border, text, hint, accent, onAccent).joinToString(",")

    private fun storedPalette(raw: String?): Palette? {
        val parts = raw?.split(',')?.map { it.trim().toIntOrNull() } ?: return null
        if (parts.size != 6 || parts.any { it == null }) return null
        return Palette(parts[0]!!, parts[1]!!, parts[2]!!, parts[3]!!, parts[4]!!, parts[5]!!)
    }

    /**
     * Ridipinge quello che è già a schermo.
     *
     * Il tema si cambia in Impostazioni → Personalizzazione, cioè con la
     * mascotte accesa e magari con il fumetto aperto: senza questo, i colori
     * nuovi arriverebbero solo al montaggio dopo.
     */
    private fun applyPalette() {
        val ctx = appContext ?: return
        // Prima di ridisegnare: l'istanza porta i colori vecchi cotti dentro.
        markdown = null
        applyPill(ctx)
        input?.let {
            it.setTextColor(palette.text)
            it.setHintTextColor(palette.hint)
        }
        syncSend(bloom = false)
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
        scrim = null
        inputRow = null
        input = null
        column = null
        historyScroll = null
        historyList = null
        historyVeil = null
        stand = null
        openChip = null
        markdown = null
        history.clear()
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
     * Prende anche il **fuoco**. Tre cose lo rendono vero, e
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
     */
    private fun expand() {
        val ctx = appContext ?: return
        if (expanded) return

        // Il palco non si muove e non cambia taglia: cambia solo *cosa
        // accetta*. La maniglia resta toccabile e resta dov'è — è piccola e
        // copre solo lei, quindi il velo e il campo li raggiungi lo stesso, e
        // un tocco su di lei è comunque un tocco su di lei.
        applyStage(
            STAGE_CHAT,
            WindowManager.LayoutParams.SOFT_INPUT_ADJUST_NOTHING or
                WindowManager.LayoutParams.SOFT_INPUT_STATE_VISIBLE,
        )
        expanded = true
        scrim?.visibility = View.VISIBLE
        // La pillola si sistema **ora**, prima di comparire: se lo schermo è
        // cambiato dal montaggio (rotazione), la larghezza va ricalcolata, e
        // l'utente non deve vedere per un frame quella vecchia.
        applyPill(ctx)
        inputRow?.visibility = View.VISIBLE
        // Dal bordo va a mettersi **in piedi sul cap della pillola** dal suo
        // lato: v. `parkX`.
        syncFace()
        slideTo(ctx, parkX(ctx, out = true), parkTop(ctx))
        input?.let { it.post { focusTheField(ctx) } }
        armHold()
        Log.i(TAG, "Floating mascot expanded")
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
        if (!expanded) return
        field.isFocusableInTouchMode = true
        field.requestFocus()
        val imm = ctx.getSystemService(InputMethodManager::class.java) ?: return
        if (!imm.showSoftInput(field, InputMethodManager.SHOW_IMPLICIT)) {
            // Il primo tentativo può cadere se la finestra non ha ancora il
            // fuoco: si riprova al giro successivo del Looper invece di
            // lasciare un campo che lampeggia il cursore e non scrive.
            field.postDelayed({
                if (expanded) imm.showSoftInput(field, InputMethodManager.SHOW_IMPLICIT)
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
        if (!expanded) return
        hideKeyboard(ctx)
        expanded = false
        scrim?.visibility = View.GONE
        inputRow?.visibility = View.GONE
        // Chiudere è un gesto, e vale come «ho finito»: la conversazione se ne
        // va con la finestra, e la prossima apertura riparte da zero.
        clearHistory()
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
                    event.action == KeyEvent.ACTION_UP && expanded
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
                if (hasWindowFocus && expanded) focusTheField(ctx)
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

        // La conversazione non vive più qui: sta dentro il composer, che è una
        // colonna sola (lista, lo spazio in cui lei sta, pillola) ancorata in
        // basso — v. [buildInputRow]. Tenerla qui, ancorata alla sua testa,
        // voleva dire due contabilità della stessa `y`: quella del fumetto e
        // quella di lei.
        val side = mascotSize(ctx)

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
        // `flightArt`, non `flight`: la proprietà [flight] è il *volo*, e una
        // locale con quel nome la ombreggiava dentro il listener degli insets
        // — dove `flight == null` diventava «sempre falso» (una ImageView non
        // è mai null) e la mascotte non seguiva più la tastiera. Trovato dal
        // compilatore, non da un occhio.
        val art = ImageView(ctx).apply {
            scaleType = ImageView.ScaleType.FIT_CENTER
            visibility = View.GONE
        }
        container.addView(art, FrameLayout.LayoutParams(side, side).apply {
            gravity = Gravity.TOP or Gravity.START
        })
        flightArt = art

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
                val bottom = max(ime, bars)
                inputRow?.let {
                    // **Solo il fondo.** Gli altri tre lati sono geometria
                    // della pillola, e questo listener scatta nell'istante in
                    // cui la finestra prende il fuoco — cioè subito dopo
                    // `applyPill`. Riscrivendo tutti e quattro (com'era) la
                    // cancellava un frame dopo che era stata calcolata.
                    it.setPadding(
                        it.paddingLeft,
                        it.paddingTop,
                        it.paddingRight,
                        dp(ctx, COMPOSER_PAD_BOTTOM_DP) + bottom,
                    )
                }
                syncListCap(ctx)
                if (bottom != chatBottomInsetPx) {
                    // La pillola è salita sopra la tastiera: lei ci sta in
                    // piedi sopra, quindi sale con lei — o resterebbe dietro
                    // la tastiera. `post`, perché `parkTop` legge dove la
                    // pillola *è*, e il padding nuovo si vede solo dopo il
                    // layout che questo stesso passaggio sta per fare.
                    chatBottomInsetPx = bottom
                    inputRow?.post {
                        if (expanded && flight == null) {
                            slideTo(ctx, parkX(ctx, out = true), parkTop(ctx))
                        }
                    }
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
     * Il composer: **una pillola corta, centrata, su cui lei sta in piedi.**
     *
     * La prima versione era un `EditText` nudo su una banda scura, e sul
     * telefono si leggeva come una cosa rotta. La seconda aveva bordo e
     * bottone, ma il bottone stava **fuori**. La terza l'aveva messo dentro,
     * ma la barra era rimasta edge-to-edge — 12:1, si leggeva come un toast di
     * sistema — e la quarta ci aveva attaccato un beccuccio da 20 dp su un
     * bordo da 1300 px, che il clamp spingeva via da sotto di lei: un glitch,
     * non un fumetto.
     *
     * Ora è una pillola larga [PILL_WIDTH_RATIO] dello schermo e centrata,
     * come la barra di Claude o di Spotlight; il legame con lei non è un
     * triangolo ma la posizione: [parkX] la mette **in piedi sul cap** dal
     * suo lato e [parkTop] le tiene i piedi a [FEET_GAP_DP] dal bordo alto,
     * anche quando il testo va a capo e la pillola cresce. Tre piani invece di
     * tre viola uguali: l'ombra sotto, il bordo che vira all'accento appena
     * c'è testo, la pallina che si accende dello stesso accento.
     */
    private fun buildInputRow(ctx: Context): View {
        val row = LinearLayout(ctx).apply {
            // **Verticale**: lista, lo spazio in cui lei sta, pillola. Ancorata
            // in basso, quindi la conversazione cresce verso l'alto e la
            // pillola non si muove di un pixel quando arriva un messaggio.
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL or Gravity.BOTTOM
            setPadding(0, dp(ctx, COMPOSER_PAD_TOP_DP), 0, dp(ctx, COMPOSER_PAD_BOTTOM_DP))
            // L'ombra della pillola cade nel padding, e questo glielo permette.
            //
            // **`clipChildren = false` no**, per quanto sembri la coppia
            // naturale: toglie il ritaglio a *tutta* la discendenza, e la
            // lista è una `ScrollView` — che senza ritaglio disegna le bolle
            // fuori dal suo riquadro, addosso a lei e sopra la pillola.
            // Misurato sul Titan 2 il 18/09: l'ultima bolla finiva 180 px
            // sotto il fondo della lista.
            clipToPadding = false
            visibility = View.GONE
        }
        // La barra: il bordo tondo è suo, non del campo. È tutta la differenza
        // fra «un campo e un bottone» e «una cosa in cui si scrive e si manda».
        val bar = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            // Fino a quattro righe il campo cresce verso l'alto e il tasto
            // resta in fondo, dove il pollice l'ha lasciato.
            gravity = Gravity.BOTTOM
            // ...**e questa riga è quella che glielo permette.** Una
            // `LinearLayout` orizzontale allinea i figli per la linea di base
            // del testo, non per il bordo, e lo fa di default: la freccia
            // finiva incollata alla *prima* riga del campo, cioè in cima, mezza
            // tagliata fuori dalla barra. Con tre righe scritte si vede subito;
            // con una riga sola le due regole danno lo stesso risultato, ed è
            // il motivo per cui un difetto così si scopre tardi.
            isBaselineAligned = false
            val pad = dp(ctx, BAR_PAD_DP)
            setPadding(dp(ctx, BAR_TEXT_PAD_DP), pad, pad, pad)
            background = barBackground(ctx)
            elevation = dp(ctx, PILL_ELEVATION_DP).toFloat()
            // Quando il testo va a capo la pillola cresce verso l'alto, e lei
            // — che ci sta in piedi sopra — sale con la pillola: `parkTop`
            // legge l'altezza misurata da qui.
            addOnLayoutChangeListener { _, _, t, _, b, _, _, _, _ ->
                val h = b - t
                if (h <= 0 || h == pillHeightPx) return@addOnLayoutChangeListener
                pillHeightPx = h
                if (expanded && flight == null) {
                    slideTo(ctx, parkX(ctx, out = true), parkTop(ctx))
                }
            }
        }
        val field = EditText(ctx).apply {
            hint = ctx.getString(R.string.floating_input_hint)
            // Il fondo ce l'ha la barra: due sfondi tondi uno dentro l'altro si
            // vedono, e male.
            background = null
            setTextColor(palette.text)
            setHintTextColor(palette.hint)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            gravity = Gravity.CENTER_VERTICAL
            // Alto quanto il tasto: a una riga sola i due si allineano da sé,
            // senza padding calcolati a mano che sbagliano di un dp.
            minHeight = dp(ctx, SEND_DP)
            setPadding(0, 0, dp(ctx, 8), 0)
            maxLines = 4
            setSingleLine(false)
            imeOptions = EditorInfo.IME_ACTION_SEND
            isFocusableInTouchMode = true
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
                override fun afterTextChanged(s: Editable?) {
                    armHold()
                    syncSend()
                }
            })
        }
        bar.addView(field, LinearLayout.LayoutParams(
            0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f
        ))

        val button = ImageView(ctx).apply {
            setImageDrawable(arrowIcon(ctx))
            scaleType = ImageView.ScaleType.FIT_CENTER
            contentDescription = ctx.getString(R.string.floating_send)
            setOnClickListener { send() }
        }
        val side = dp(ctx, SEND_DP)
        bar.addView(button, LinearLayout.LayoutParams(side, side))

        // La lista delle bolle. Il padding è dove cadono le loro ombre: la
        // `ScrollView` ritaglia (le deve ritagliare, o la sfumatura del bordo
        // alto non avrebbe senso), quindi lo spazio glielo si dà dentro.
        val list = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            val pad = dp(ctx, LIST_PAD_DP)
            setPadding(pad, pad, pad, pad)
            // Le ombre delle bolle cadono qui dentro: il padding è lo spazio,
            // questa riga è il permesso di disegnarci.
            clipToPadding = false
        }
        val fade = dp(ctx, LIST_FADE_DP)
        val scroll = CappedScrollView(ctx).apply {
            addView(list, FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
            ))
            isVerticalScrollBarEnabled = false
            overScrollMode = View.OVER_SCROLL_IF_CONTENT_SCROLLS
        }
        // Il velo: una `View` sorella, disegnata dopo, quindi davanti. Un
        // taglio netto sopra l'app di qualcun altro sembra un difetto di
        // rendering; questo dice «c'è dell'altro sopra» e basta.
        val veil = View(ctx).apply {
            background = GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM,
                intArrayOf(Color.BLACK, Color.TRANSPARENT),
            )
            alpha = 0f
        }
        val frame = FrameLayout(ctx).apply {
            addView(scroll, FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
            ))
            addView(veil, FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, fade
            ).apply { gravity = Gravity.TOP })
        }
        scroll.setOnScrollChangeListener { _, _, y, _, _ -> syncVeil(y) }
        row.addView(frame, LinearLayout.LayoutParams(
            listWidth(ctx), LinearLayout.LayoutParams.WRAP_CONTENT
        ))

        // Il riquadro vuoto in cui lei sta in piedi. Vuoto davvero: lei è in
        // un'altra finestra, e questo serve solo a non farle scrivere addosso.
        val standView = View(ctx)
        row.addView(standView, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, dp(ctx, CHIP_GAP_DP)
        ))

        row.addView(bar, LinearLayout.LayoutParams(
            pillWidth(ctx), LinearLayout.LayoutParams.WRAP_CONTENT
        ))

        input = field
        sendButton = button
        inputBar = bar
        inputRow = row
        historyScroll = scroll
        historyList = list
        historyVeil = veil
        stand = standView
        openChip = buildChip(ctx)
        renderHistory()
        // Il testo sopravvive a un collapse (v. `collapse`): il tasto può
        // nascere già acceso, e deve nascere come lo si è lasciato.
        syncSend(bloom = false)
        return row
    }

    /**
     * Il tasto si accende quando c'è qualcosa da mandare.
     *
     * La pallina c'è sempre: da spenta è del colore dei bordi (`--border-strong`)
     * al 60%, appena visibile — dice «questo è il tasto» senza gridarlo. Alla
     * prima lettera fiorisce in `--accent` con la stessa molla (`sendEnable`)
     * del composer in chat, e *solo* alla prima: [sendLit] tiene il conto, o la
     * freccia rimbalzerebbe a ogni carattere. Con lei si accende anche il
     * bordo della pillola (v. [strokeColor]).
     *
     * La prima versione lasciava la freccia nuda quando la barra era vuota:
     * senza cerchio, letta come un simbolo abbandonato in mezzo alla riga.
     */
    private fun syncSend(bloom: Boolean = true) {
        val button = sendButton ?: return
        val lit = !(input?.text?.toString()?.trim()).isNullOrEmpty()
        val changed = lit != sendLit
        sendLit = lit
        button.isEnabled = lit
        // `SRC_IN`, non il tint: la freccia è un Drawable disegnato a mano, e
        // il filtro colora i suoi pixel tenendone l'antialias.
        button.setColorFilter(
            if (lit) palette.onAccent else faded(palette.text, 0.55f),
            PorterDuff.Mode.SRC_IN,
        )
        button.background = GradientDrawable().apply {
            setColor(if (lit) palette.accent else faded(palette.border, 0.6f))
            shape = GradientDrawable.OVAL
        }
        appContext?.let { ctx ->
            (inputBar?.background as? GradientDrawable)?.setStroke(dp(ctx, 1), strokeColor())
        }
        if (lit && changed && bloom) {
            button.scaleX = 0.85f
            button.scaleY = 0.85f
            button.animate()
                .scaleX(1f).scaleY(1f)
                .setDuration(SEND_BLOOM_MS)
                .setInterpolator(OvershootInterpolator(1.6f))
                .start()
        }
    }

    /**
     * Il fondo della pillola: `--surface` dietro, il bordo attorno.
     *
     * La stessa pillola di `.compose-pill`, con in più il velo che tutte le
     * superfici di questa finestra portano: galleggiano sopra l'app di
     * qualcun altro, e un filo di trasparenza dice che non sono sue.
     */
    private fun barBackground(ctx: Context): GradientDrawable = GradientDrawable().apply {
        setColor(veiled(palette.surface))
        cornerRadius = dp(ctx, BAR_RADIUS_DP).toFloat()
        setStroke(dp(ctx, 1), strokeColor())
    }

    /**
     * Il bordo della pillola: `--border-strong` a vuoto, e appena c'è testo
     * vira per metà verso `--accent`.
     *
     * È il `focus-within` di `.compose-pill`, ma legato al testo e non al
     * fuoco: qui il fuoco c'è sempre, finché la chat è aperta, e un bordo
     * sempre acceso non direbbe niente.
     */
    private fun strokeColor(): Int =
        if (sendLit) blend(palette.accent, palette.border, 0.55f) else palette.border

    /**
     * Un'icona **disegnata**: un tratto tondo dentro una griglia di 14 unità,
     * centrata nei bounds.
     *
     * Le due che servono qui — la freccia d'invio e il quadrato del chip — non
     * esistono come risorsa e non vale la pena importare una libreria per due
     * path. Prima erano glifi di testo (`↑`): sottili, con la punta da
     * carattere, e seduti sulla linea di base invece che al centro del disco.
     * Un'icona fatta con un carattere si vede sempre.
     *
     * Il colore lo dà `setColorFilter` da fuori (v. [syncSend]): qui la
     * vernice è bianca piena, così il filtro ha una forma opaca da colorare.
     */
    private fun strokeIcon(
        ctx: Context,
        sizeDp: Int,
        strokeDp: Float,
        build: (Path, Float) -> Unit,
    ): Drawable {
        val stroke = dpf(ctx, strokeDp)
        val unit = dp(ctx, sizeDp) / 14f
        return object : Drawable() {
            private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                color = Color.WHITE
                style = Paint.Style.STROKE
                strokeWidth = stroke
                strokeCap = Paint.Cap.ROUND
                strokeJoin = Paint.Join.ROUND
            }
            override fun draw(canvas: Canvas) {
                val b = bounds
                if (b.isEmpty) return
                val path = Path().also { build(it, unit) }
                canvas.save()
                canvas.translate(
                    b.exactCenterX() - 7f * unit,
                    b.exactCenterY() - 7f * unit,
                )
                canvas.drawPath(path, paint)
                canvas.restore()
            }
            override fun setAlpha(a: Int) { paint.alpha = a }
            override fun setColorFilter(c: ColorFilter?) { paint.colorFilter = c }
            @Deprecated("Deprecated in Java")
            override fun getOpacity() = PixelFormat.TRANSLUCENT
        }
    }

    /** La freccia d'invio: un'asta e due bracci, tratto 2 dp. */
    private fun arrowIcon(ctx: Context): Drawable =
        strokeIcon(ctx, SEND_ICON_DP, 2f) { path, u ->
            path.moveTo(7f * u, 13f * u)
            path.lineTo(7f * u, 1f * u)
            path.moveTo(1.5f * u, 6.5f * u)
            path.lineTo(7f * u, 1f * u)
            path.lineTo(12.5f * u, 6.5f * u)
        }

    /** Il quadrato con la freccia che esce: «questo porta fuori di qui». */
    private fun openIcon(ctx: Context): Drawable =
        strokeIcon(ctx, CHIP_ICON_DP, 1.5f) { path, u ->
            // Il riquadro aperto nell'angolo da cui esce la freccia.
            path.moveTo(7f * u, 2f * u)
            path.lineTo(2f * u, 2f * u)
            path.lineTo(2f * u, 12f * u)
            path.lineTo(12f * u, 12f * u)
            path.lineTo(12f * u, 7f * u)
            // ...e la freccia, in diagonale.
            path.moveTo(8.5f * u, 1.5f * u)
            path.lineTo(12.5f * u, 1.5f * u)
            path.lineTo(12.5f * u, 5.5f * u)
            path.moveTo(12.5f * u, 1.5f * u)
            path.lineTo(7f * u, 7f * u)
        }

    /** Quanto è larga la pillola, in px: [PILL_WIDTH_RATIO] dello schermo. */
    private fun pillWidth(ctx: Context): Int = (screenWidth(ctx) * PILL_WIDTH_RATIO).toInt()

    /** ...e quanto è larga la lista: quasi tutto lo schermo. Le due misure
     *  sono diverse di proposito — v. [LIST_EDGE_DP]. */
    private fun listWidth(ctx: Context): Int = screenWidth(ctx) - 2 * dp(ctx, LIST_EDGE_DP)

    /**
     * Sistema la pillola: larga [pillWidth] e centrata dal row, col fondo del
     * tema corrente e il bordo nello stato giusto.
     *
     * Va chiamata **prima** di far comparire il row, così l'utente non vede
     * per un frame la geometria vecchia; e di nuovo quando la palette cambia
     * — v. [applyPalette].
     */
    private fun applyPill(ctx: Context) {
        val bar = inputBar ?: return
        (bar.layoutParams as? LinearLayout.LayoutParams)?.let {
            val w = pillWidth(ctx)
            if (it.width != w) {
                it.width = w
                bar.layoutParams = it
            }
        }
        (historyScroll?.parent as? View)?.let { frame ->
            (frame.layoutParams as? LinearLayout.LayoutParams)?.let {
                val w = listWidth(ctx)
                if (it.width != w) {
                    it.width = w
                    frame.layoutParams = it
                }
            }
        }
        bar.background = barBackground(ctx)
        syncSend(bloom = false)
        renderHistory()
    }

    // ------------------------------------------------------------------ //
    // La conversazione                                                    //
    // ------------------------------------------------------------------ //

    /**
     * Aggiunge una riga e ridisegna.
     *
     * La potatura tiene le ultime [HISTORY_MAX_TURNS] coppie: due bolle per
     * scambio. Cadono dalla testa, che è il posto giusto — quello che si vuole
     * vedere è sempre il fondo.
     */
    private fun appendLine(mine: Boolean, text: String) {
        // **A finestra chiusa non entra niente**, e vale per tutti e tre i
        // chiamanti. I due percorsi d'errore arrivano da una callback che
        // nessuno annulla: consegna fallita mentre la si lancia via, e la
        // riga finiva in una conversazione appena azzerata — per ricomparire
        // alla prossima apertura, che invece deve essere vuota.
        if (!expanded) return
        val clean = text.trim()
        if (clean.isEmpty()) return
        history.add(Line(mine, clean))
        while (history.size > HISTORY_MAX_TURNS * 2) history.removeAt(0)
        renderHistory()
        // Dopo il layout, non durante: prima di misurare non si sa dove sia il
        // fondo.
        historyScroll?.post {
            historyScroll?.fullScroll(View.FOCUS_DOWN)
            syncVeil(historyScroll?.scrollY ?: 0)
        }
    }

    /** L'opacità del velo: piena quando sopra c'è almeno una fascia intera di
     *  conversazione, zero quando la lista è in cima e non c'è niente da dire. */
    private fun syncVeil(scrollY: Int) {
        val ctx = appContext ?: return
        val veil = historyVeil ?: return
        val fade = dp(ctx, LIST_FADE_DP).toFloat()
        veil.alpha = if (fade <= 0f) 0f else (scrollY / fade).coerceIn(0f, 1f)
    }

    /**
     * Azzera la conversazione. **La chiama solo una chiusura voluta.**
     *
     * Non c'è nessun timer che ci arriva: finché ci sono bolle, [armHold] non
     * arma niente. Chiudere è un gesto — un tocco fuori, Indietro, o lanciarla
     * via — e il gesto vale come «ho finito». Alla riapertura si riparte da
     * zero, che è la regola della finestra: la conversazione che dura è quella
     * dell'app.
     */
    private fun clearHistory() {
        if (history.isEmpty()) return
        history.clear()
        renderHistory()
    }

    /** Ricostruisce le bolle dal modello. È l'unico posto che tocca le view
     *  della lista: un cambio di tema o di lato ripassa di qui. */
    private fun renderHistory() {
        val ctx = appContext ?: return
        val list = historyList ?: return
        val chip = openChip ?: return
        list.removeAllViews()
        styleChip(ctx, chip)
        list.addView(chip, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { gravity = Gravity.CENTER_HORIZONTAL })
        for (line in history) {
            val bubble = bubbleView(ctx, line)
            // Il tuo messaggio sta dal lato dove lei **non** sta, il suo dal
            // suo: è tutto il legame fra la conversazione e chi la tiene.
            val atStart = if (line.mine) parkedRight else !parkedRight
            list.addView(bubble, LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply {
                gravity = if (atStart) Gravity.START else Gravity.END
                topMargin = dp(ctx, BUBBLE_GAP_DP)
            })
        }
        syncVeil(historyScroll?.scrollY ?: 0)
        stand?.let { view ->
            val h = standHeight(ctx)
            if (view.layoutParams.height != h) {
                view.layoutParams = view.layoutParams.apply { height = h }
            }
        }
        syncListCap(ctx)
    }

    /**
     * Il renderer markdown delle sue bolle, costruito una volta per palette.
     *
     * Le bolle mostravano il **sorgente** — `1. Scegli **una** cosa` — mentre
     * la WebUI renderizza: la stessa risposta si leggeva in due modi a seconda
     * di dove la guardavi. Qui c'è lo stesso GFM che `marked` fa in chat, meno
     * le tre cose che in una bolla non esistono (colori della sintassi,
     * formule, diagrammi): per quelle c'è il tasto «Continua su app».
     */
    private fun markdownRenderer(ctx: Context): Markwon = markdown ?: Markwon.builder(ctx)
        // **Il `breaks: true` della chat.** Senza, le sue liste scritte a righe
        // singole si fondono in un paragrafo solo: è la differenza più visibile
        // fra le due viste, non un dettaglio di resa.
        .usePlugin(SoftBreakAddsNewLinePlugin.create())
        // Il pezzo GFM che CommonMark non ha, ed è quello che il modello scrive
        // davvero.
        .usePlugin(StrikethroughPlugin.create())
        .usePlugin(TaskListPlugin.create(palette.accent, palette.border, palette.onAccent))
        .usePlugin(
            TablePlugin.create(
                TableTheme.buildWithDefaults(ctx)
                    .tableBorderColor(palette.border)
                    .tableBorderWidth(dp(ctx, 1))
                    .tableCellPadding(dp(ctx, 4))
                    .tableHeaderRowBackgroundColor(faded(palette.border, 0.5f))
                    .tableOddRowBackgroundColor(faded(palette.border, 0.25f))
                    .build()
            )
        )
        // **Non `Linkify.ALL`**: quello trasforma anche numeri e indirizzi, e
        // un promemoria con un orario dentro diventerebbe un link al telefono.
        .usePlugin(LinkifyPlugin.create(Linkify.WEB_URLS or Linkify.EMAIL_ADDRESSES))
        // L'HTML grezzo che il modello a volte emette. Qui non c'è la superficie
        // XSS della WebUI — in una `TextView` non gira niente, ed è il motivo
        // per cui là serve DOMPurify e qui no: i tag che non conosce li ignora.
        .usePlugin(HtmlPlugin.create())
        .usePlugin(object : AbstractMarkwonPlugin() {
            override fun configureTheme(builder: MarkwonTheme.Builder) {
                builder
                    .codeTextColor(palette.text)
                    .codeBackgroundColor(faded(palette.border, 0.5f))
                    .codeBlockTextColor(palette.text)
                    .codeBlockBackgroundColor(faded(palette.border, 0.35f))
                    .codeBlockMargin(dp(ctx, 8))
                    .blockQuoteColor(palette.accent)
                    .blockQuoteWidth(dp(ctx, 3))
                    .bulletWidth(dp(ctx, 4))
                    .headingBreakColor(palette.border)
                    .thematicBreakColor(palette.border)
                    .linkColor(palette.accent)
            }

            override fun configureConfiguration(builder: MarkwonConfiguration.Builder) {
                builder.linkResolver { _, link -> openLink(ctx, link) }
            }
        })
        .build()
        .also { markdown = it }

    /**
     * Apre un link toccato in una bolla.
     *
     * Il `LinkResolverDef` di Markwon farebbe `startActivity` con il contesto
     * che gli si dà, e qui è quello **applicativo**: senza
     * `FLAG_ACTIVITY_NEW_TASK` l'apertura solleva. E la finestra si chiude
     * prima, come fa già [openChat]: lasciare l'overlay sopra il browser che si
     * è appena chiesto di aprire non ha senso.
     */
    private fun openLink(ctx: Context, url: String) {
        collapse()
        try {
            ctx.startActivity(
                Intent(Intent.ACTION_VIEW, Uri.parse(url))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (e: Exception) {
            Log.i(TAG, "Could not open the link: ${e.javaClass.simpleName}")
        }
    }

    /** Una bolla: il fondo, l'angolo stretto dal lato di chi parla, il testo. */
    private fun bubbleView(ctx: Context, line: Line): TextView {
        val atStart = if (line.mine) parkedRight else !parkedRight
        val r = dp(ctx, BUBBLE_RADIUS_DP).toFloat()
        val c = dp(ctx, BUBBLE_CORNER_DP).toFloat()
        // topLeft, topRight, bottomRight, bottomLeft — due valori ciascuno.
        val corners = floatArrayOf(
            r, r,
            r, r,
            if (atStart) r else c, if (atStart) r else c,
            if (atStart) c else r, if (atStart) c else r,
        )
        return TextView(ctx).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            setTextColor(if (line.mine) palette.onAccent else palette.text)
            maxWidth = (listWidth(ctx) * BUBBLE_MAX_RATIO).toInt()
            setPadding(dp(ctx, 13), dp(ctx, 9), dp(ctx, 13), dp(ctx, 9))
            background = GradientDrawable().apply {
                cornerRadii = corners
                if (line.mine) {
                    setColor(palette.accent)
                } else {
                    // Come la pillola: stessa superficie, stesso velo, stesso
                    // bordo. Quello che dice «è lei» è il lato, non il colore.
                    setColor(veiled(palette.surface))
                    setStroke(dp(ctx, 1), palette.border)
                }
            }
            elevation = dp(ctx, BUBBLE_ELEVATION_DP).toFloat()
            // **Il markdown è solo suo.** Quello che l'utente ha scritto si
            // mostra com'è scritto: se ha digitato `*ciao*` intendeva gli
            // asterischi. È anche quel che fa la SPA, che riempie la bolla
            // utente con `textContent` e la sua con `renderMarkdown`.
            //
            // Ultimo, dopo i colori: `setMarkdown` scrive il testo e attacca il
            // movement method dei link.
            if (line.mine) {
                text = line.text
            } else {
                markdownRenderer(ctx).setMarkdown(this, line.text)
            }
        }
    }

    /** Il chip che porta all'app. Costruito una volta sola: cambia etichetta,
     *  non identità. */
    @SuppressLint("SetTextI18n")
    private fun buildChip(ctx: Context): TextView = TextView(ctx).apply {
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
        gravity = Gravity.CENTER_VERTICAL
        compoundDrawablePadding = dp(ctx, 6)
        setPadding(dp(ctx, 13), dp(ctx, 6), dp(ctx, 13), dp(ctx, 6))
        setOnClickListener { openChat(ctx) }
    }

    /**
     * Veste il chip per lo stato corrente.
     *
     * L'etichetta è l'unica cosa che cambia fra conversazione vuota e piena:
     * a vuoto è un invito ad aprire l'app, con delle bolle sopra è la
     * continuazione di quello che si sta leggendo.
     */
    private fun styleChip(ctx: Context, chip: TextView) {
        val tint = faded(palette.text, 0.78f)
        val icon = openIcon(ctx).apply {
            val side = dp(ctx, CHIP_ICON_DP)
            setBounds(0, 0, side, side)
            colorFilter = PorterDuffColorFilter(tint, PorterDuff.Mode.SRC_IN)
        }
        chip.setCompoundDrawablesRelative(icon, null, null, null)
        chip.text = ctx.getString(
            if (history.isEmpty()) R.string.floating_open_app else R.string.floating_continue_app
        )
        chip.setTextColor(tint)
        chip.background = GradientDrawable().apply {
            setColor(faded(veiled(palette.surface), 0.92f))
            cornerRadius = dp(ctx, 999).toFloat()
            setStroke(dp(ctx, 1), palette.border)
        }
    }

    /**
     * Lo spazio in cui lei sta in piedi, fra la conversazione e la pillola.
     *
     * È la sua parte **visibile** ([HEAD_RATIO]–[FEET_RATIO] del quadrato), non
     * il quadrato: il resto è margine trasparente, e riservarlo lascerebbe un
     * buco. A conversazione vuota scende a [CHIP_GAP_DP], perché lì sopra c'è
     * solo il chip e lei è di lato, sul cap.
     */
    private fun standHeight(ctx: Context): Int =
        if (history.isEmpty()) {
            dp(ctx, CHIP_GAP_DP)
        } else {
            (mascotSize(ctx) * (FEET_RATIO - HEAD_RATIO)).toInt() + dp(ctx, STAND_GAP_DP)
        }

    /**
     * Rimette il tetto della lista allo spazio che c'è davvero.
     *
     * Non c'è un tetto in dp: la lista arriva fin dove c'è posto, cioè a
     * [LIST_TOP_MARGIN_DP] dal bordo alto. Quel posto però **cambia** — la
     * pillola cresce, la tastiera si alza — e senza questo conto le bolle
     * uscirebbero dal bordo di sopra, dove la colonna cresce e non c'è nessuno
     * a fermarle.
     */
    private fun syncListCap(ctx: Context) {
        val scroll = historyScroll ?: return
        val pill = max(inputBar?.height ?: 0, dp(ctx, PILL_DP))
        val bottom = max(chatBottomInsetPx, navInset(ctx))
        val cap = max(
            screenHeight(ctx) - bottom - dp(ctx, COMPOSER_PAD_BOTTOM_DP) - pill -
                standHeight(ctx) - dp(ctx, COMPOSER_PAD_TOP_DP) - dp(ctx, LIST_TOP_MARGIN_DP),
            0,
        )
        if (cap == scroll.maxHeight) return
        scroll.maxHeight = cap
        scroll.requestLayout()
    }

    /** `color-mix(in srgb, a k, b)`: canale per canale, alpha compresa. */
    private fun blend(a: Int, b: Int, k: Float): Int {
        fun ch(shift: Int): Int {
            val x = (a shr shift) and 0xFF
            val y = (b shr shift) and 0xFF
            return (x * k + y * (1f - k)).toInt().coerceIn(0, 255)
        }
        return (ch(24) shl 24) or (ch(16) shl 16) or (ch(8) shl 8) or ch(0)
    }

    /** Lo stesso colore con l'alpha moltiplicata per `k`. */
    private fun faded(color: Int, k: Float): Int {
        val a = (((color ushr 24) and 0xFF) * k).toInt().coerceIn(0, 255)
        return (color and 0x00FFFFFF) or (a shl 24)
    }

    /** Lo stesso `0xF0` che le due bande portavano quando i colori erano
     *  scritti a mano: il tema decide il colore, non quanto si vede attraverso. */
    private fun veiled(color: Int): Int =
        (color and 0x00FFFFFF) or (SURFACE_ALPHA shl 24)

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
                        // Un tap a finestra aperta la richiude: è il gesto
                        // inverso di quello che l'ha aperta.
                        if (expanded) collapse() else expand()
                    }
                    tracker?.recycle()
                    tracker = null
                    restGrip(ctx)
                    true
                }
                MotionEvent.ACTION_CANCEL -> {
                    if (grabbed) {
                        flight?.release(0f, 0f)
                    } else if (!expanded) {
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
        if (expanded) return
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
        // Lanciarla via è una chiusura come le altre.
        clearHistory()
        // Si può prenderla anche a chat aperta: allora il campo e il velo se
        // ne vanno, perché mentre vola non c'è niente a cui scrivere. La
        // finestra resta grande — è già l'arena — ma smette di prendere il
        // fuoco, così la tastiera non resta appesa a mezz'aria.
        if (expanded) hideKeyboard(ctx)
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
        // La domanda entra nella conversazione **subito**, senza aspettare il
        // gateway: è la sola cosa che dice «è partita».
        appendLine(mine = true, text = text)
        waitingForReply = true
        syncFace()
        startBreathing()
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
        appendLine(mine = false, text = ctx.getString(R.string.floating_not_running))
        armHold()
    }

    private fun onReplyTimeout() {
        val ctx = appContext ?: return
        waitingForReply = false
        syncFace(sad = true)
        appendLine(mine = false, text = ctx.getString(R.string.floating_no_reply))
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
     *   frase è esattamente ciò che somiglia di più all'inattività;
     * * **c'è una conversazione a schermo** — venti secondi erano la misura di
     *   un fumetto solo. Quattro scambi non si leggono in venti secondi, e
     *   sparendo si porterebbero via anche sé stessi ([clearHistory]). Da qui
     *   in poi chiude solo un gesto.
     *
     * Resta armato nell'unico caso che conta davvero: composer aperto, vuoto e
     * mai toccato. Un tocco per sbaglio non lascia un pannello sull'app.
     */
    private fun armHold() {
        main.removeCallbacks(holdRunnable)
        if (waitingForReply) return
        if (!input?.text.isNullOrBlank()) return
        if (history.isNotEmpty()) return
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
        // Da dov'è **adesso**, non dall'ultimo posto in cui si è fermata: se
        // uno scivolamento è in corso (la pillola è cresciuta a metà corsa) il
        // nuovo riparte dal fotogramma corrente invece di saltare indietro.
        val fromX = mascotWinParams?.x ?: gripX
        val fromY = mascotWinParams?.y ?: gripY
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

    /**
     * L'ascissa del suo riquadro.
     *
     * Parcheggiata sta al bordo, per [DOCKED_OUT_RATIO] fuori schermo. «Out»
     * con la chat aperta sta **in piedi sul cap della pillola** dal suo lato:
     * l'asse del corpo ([AXIS_RATIO]) cade sul centro del cap — la pallina
     * d'invio a destra, il suo specchio a sinistra. «Out» senza chat — il
     * solo fumetto di risposta — rientra a un quarto dal bordo, come prima:
     * lì la pillola non c'è, e non c'è niente su cui stare.
     */
    private fun parkX(ctx: Context, out: Boolean = false): Int {
        val size = mascotSize(ctx)
        val width = screenWidth(ctx)
        if (out && expanded) {
            val pillW = pillWidth(ctx)
            val pillLeft = (width - pillW) / 2
            val cap = dp(ctx, BAR_PAD_DP) + dp(ctx, SEND_DP) / 2
            return if (parkedRight) {
                pillLeft + pillW - cap - (size * AXIS_RATIO).toInt()
            } else {
                pillLeft + cap - (size * (1f - AXIS_RATIO)).toInt()
            }
        }
        val hidden = (size * if (out) OUT_RATIO else DOCKED_OUT_RATIO).toInt()
        return if (parkedRight) width - size + hidden else -hidden
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
     * L'ordinata, **derivata e mai memorizzata**: quella con cui i suoi piedi
     * poggiano [FEET_GAP_DP] sopra il bordo alto della pillola.
     *
     * È l'invariante di `.jenny-duo` («Non deve mai cambiare in Y»), è dove
     * *risiede*, è il pavimento del volo e la riga a cui la camminata torna.
     * Si calcola anche a composer nascosto — la pillola esiste come misura
     * pure quando non è a schermo — con l'altezza a una riga. A chat aperta
     * segue invece l'altezza **misurata** della pillola ([pillHeightPx]) e la
     * tastiera ([chatBottomInsetPx]): quando il testo va a capo lei sale con
     * la pillola, invece di finirci dietro.
     */
    private fun parkTop(ctx: Context): Int {
        val size = mascotSize(ctx)
        val pillTop = measuredPillTop() ?: run {
            // La pillola non è a schermo (o non ha ancora fatto un layout):
            // si stima dove *comparirà*, a una riga.
            val pill = if (expanded && pillHeightPx > 0) pillHeightPx else dp(ctx, PILL_DP)
            val bottom = if (expanded) max(chatBottomInsetPx, navInset(ctx)) else navInset(ctx)
            screenHeight(ctx) - bottom - dp(ctx, COMPOSER_PAD_BOTTOM_DP) - pill
        }
        val feet = (size * FEET_RATIO).toInt()
        // **Meno la status bar.** La `y` di una finestra overlay — anche con
        // `LAYOUT_NO_LIMITS` — è relativa al frame padre, che qui comincia
        // sotto la status bar (`parent=[0,60]…` nel dumpsys), mentre tutto il
        // conto sopra è in coordinate schermo. Senza questa riga lei atterra
        // 60 px più in basso di dove la si è messa: misurato sul Titan 2, con
        // i piedi 24 dp *dentro* la pillola. Era così anche prima, solo che
        // con la banda a tutto schermo l'errore si leggeva come «aria a caso».
        return max(pillTop - dp(ctx, FEET_GAP_DP) - feet - statusInset(ctx), dp(ctx, 8))
    }

    /**
     * Dov'è **davvero** il bordo alto della pillola, in coordinate schermo — o
     * `null` se la pillola non è a schermo.
     *
     * Misurato dalla view, non ricostruito dagli insets: il frame del palco
     * in chat viene ristretto dal sistema di una quindicina di px in fondo
     * che nessun inset dichiara (`navigationBars` dice 0 su questo telefono,
     * eppure `frame=[0,60][1436,1425]`), e la stima cadeva 6 dp più in basso
     * del vero. I piedi di lei devono stare sulla pillola *disegnata*, non su
     * quella calcolata.
     */
    private fun measuredPillTop(): Int? {
        val bar = inputBar ?: return null
        if (!expanded || bar.height <= 0 || inputRow?.visibility != View.VISIBLE) return null
        val loc = IntArray(2)
        bar.getLocationOnScreen(loc)
        return loc[1]
    }

    /** L'altezza della status bar, letta come [navInset]: dalle metriche della
     *  finestra, non dagli insets consegnati. */
    private fun statusInset(ctx: Context): Int {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val insets = ctx.getSystemService(WindowManager::class.java)
                ?.currentWindowMetrics?.windowInsets
                ?.getInsets(WindowInsets.Type.statusBars())
            if (insets != null) return insets.top
        }
        return dp(ctx, STATUS_FALLBACK_DP)
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
        storedPalette(prefs.getString(PREF_PALETTE, null))?.let { palette = it }
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

    /** Come [dp] ma in virgola mobile: i tratti delle icone non sono interi,
     *  e arrotondarli a 1 o a 2 px si vede. */
    private fun dpf(ctx: Context, value: Float): Float =
        value * ctx.resources.displayMetrics.density
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
