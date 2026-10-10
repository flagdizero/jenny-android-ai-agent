package com.flagdizero.jenny

import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.webkit.WebView

/**
 * Tiene d'accordo il fuoco della pagina e la tastiera.
 *
 * Due testimoni devono dire la stessa cosa: la pagina sa quale campo ha il
 * fuoco, la tastiera (l'IME) sa se sta scrivendo in un campo di testo. Li
 * allinea Chromium, e a volte il colpo si perde. Le lettere arrivano lo stesso,
 * quindi sembra tutto a posto; ma l'IME non le vede come testo e spegne i suoi
 * strumenti — la lettera accentata a pressione lunga di Pastiera per prima.
 * Perso il colpo, Chromium non lo ritrova da solo: per lui lo stato è già
 * giusto. Lo ritrovava solo un dialogo nativo, il cui ritorno di fuoco alla
 * finestra rilega l'IME da capo.
 *
 * Dove si perde, misurato fra il 09 e il 10/10/2026:
 * - **il tasto Home con Jenny davanti**, ogni volta. La pagina passa per
 *   nascosta e torna visibile, il composer non perde il fuoco e non parte
 *   nessun evento, ma Chromium lascia l'IME su `TYPE_NULL`. Sul Titan, dove
 *   Jenny è il launcher, è il gesto più comune che ci sia;
 * - dopo uno swipe della pista, a volte: visto sul Titan 2 (WebView 143,
 *   Pastiera), circa una volta su 50 sull'emulatore (WebView 153, Gboard);
 * - all'avvio, sempre, finché la WebView non ha il fuoco Android (v.
 *   [onPageShown]).
 *
 * Nei primi due casi un `restartInput` da qui non basta: interrogata da capo,
 * Chromium risponde di nuovo «nessun campo», perché il disaccordo è dentro di
 * lui, fra la pagina e il suo stato della tastiera. Lo scioglie la pagina,
 * togliendo e ridando il fuoco al campo nello stesso giro (`refocus()` in
 * `shared/editable-focus.js`). È quindi la prima cura; rilegare da qui è la
 * seconda.
 *
 * La pagina dice dov'è il fuoco (`setEditableFocus`); il rientro in primo piano
 * e il ritorno del fuoco alla finestra sono le altre occasioni di controllo
 * ([recheck]). Dopo [SETTLE_MS] — il tempo che Chromium ha per fare da sé — si
 * chiede ad Android se l'IME sta scrivendo in un campo
 * ([InputMethodManager.isAcceptingText]). Solo se la pagina dice «campo» e
 * l'IME dice «niente» si interviene: nel caso normale non succede nulla, e la
 * barra dell'IME non lampeggia.
 *
 * Tutto gira sul thread UI: [onDomFocus] arriva dall'executor dei comandi e ci
 * salta con un post.
 */
class ImeBinding(
    private val view: () -> View?,
    private val imm: InputMethodManager?,
    private val handler: Handler = Handler(Looper.getMainLooper()),
) {
    private val policy = ImeBindingPolicy()
    private val check = Runnable { runCheck() }

    /** La pagina ha spostato il fuoco: *state* è `text`, `none` o `frame`. */
    fun onDomFocus(state: String) {
        val dom = ImeBindingPolicy.Dom.parse(state) ?: return
        handler.post {
            policy.domFocus(dom)
            schedule()
        }
    }

    /** Un'occasione di controllo senza un cambio di fuoco: il rientro in
     *  primo piano, il fuoco che torna alla finestra. */
    fun recheck() {
        handler.post {
            policy.occasion()
            schedule()
        }
    }

    /**
     * La pagina è a schermo: la WebView prende il fuoco Android.
     *
     * Era invisibile durante il caricamento, e una vista che compare non lo
     * prende da sola finché non la tocchi o non premi un tasto. Fino ad
     * allora la pagina non ha il fuoco della finestra: il `focus()` con cui la
     * casa rimette il cursore nel composer (con la tastiera fisica) sposta
     * `activeElement` ma **non manda eventi** — misurato sull'emulatore il
     * 10/10/2026: zero `focusin`, zero `focus`, `document.hasFocus()` falso —
     * e l'IME resta senza campo fino al primo tasto, che arriva quindi grezzo.
     * Con il fuoco alla WebView gli eventi partono, e il resto lo fa il giro
     * normale.
     */
    fun onPageShown() {
        handler.post {
            val v = view() ?: return@post
            if (!v.hasFocus()) v.requestFocus()
        }
    }

    private fun schedule() {
        handler.removeCallbacks(check)
        handler.postDelayed(check, SETTLE_MS)
    }

    private fun runCheck() {
        val v = view() ?: return
        val accepting = imm?.isAcceptingText ?: return
        when (policy.check(accepting = accepting, windowFocused = v.hasWindowFocus())) {
            ImeBindingPolicy.Action.NONE -> Unit
            ImeBindingPolicy.Action.REFOCUS -> {
                Log.i(TAG, "IME heal: a text field has the page's focus and the IME has none; refocusing it")
                // Senza il fuoco Android la pagina non riceve eventi di fuoco.
                if (!v.hasFocus()) v.requestFocus()
                (v as? WebView)?.evaluateJavascript(REFOCUS_JS, null)
                schedule()
            }
            ImeBindingPolicy.Action.REBIND -> {
                Log.i(TAG, "IME heal: refocusing was not enough; restarting the input")
                if (!v.hasFocus()) v.requestFocus()
                imm.restartInput(v)
                schedule()
            }
            ImeBindingPolicy.Action.GIVE_UP ->
                Log.w(TAG, "IME still unbound after both heals; leaving it to the next focus change")
        }
    }

    companion object {
        private const val TAG = "JennyIme"

        /** Quanto si lascia a Chromium per rilegare da sé. Il suo
         *  `restartInput` arriva entro pochi ms dal cambio di fuoco: misurato
         *  sul Titan 2 e sull'emulatore, il 09/10/2026. */
        const val SETTLE_MS = 150L

        private const val REFOCUS_JS =
            "window.JennyEditableFocus && window.JennyEditableFocus.refocus()"
    }
}

/**
 * La decisione di [ImeBinding], senza Android: si prova da sola
 * (`ImeBindingPolicyTest`).
 *
 * Per ogni occasione — un fuoco su un campo, un rientro — due cure in ordine,
 * e basta: prima la pagina ridà il fuoco al campo, poi si rilega l'IME. Se
 * dopo tutte e due l'IME non sta ancora scrivendo, Chromium crede davvero che
 * lì non ci sia un campo (uno diventato readonly, per dire): insistere vorrebbe
 * dire girare in tondo. Si scrive una riga, una volta, e si aspetta la
 * prossima occasione.
 */
class ImeBindingPolicy {
    /** Dov'è il fuoco secondo la pagina. [FRAME] è un iframe — una Jenny App:
     *  lì dentro la pagina non vede, e non si interviene alla cieca. */
    enum class Dom {
        TEXT, NONE, FRAME;

        companion object {
            fun parse(s: String): Dom? = when (s) {
                "text" -> TEXT
                "none" -> NONE
                "frame" -> FRAME
                else -> null
            }
        }
    }

    enum class Action { NONE, REFOCUS, REBIND, GIVE_UP }

    private var dom = Dom.NONE
    private var heals = 0
    private var gaveUp = false

    fun domFocus(state: Dom) {
        dom = state
        // Ogni fuoco su un campo è un'occasione nuova, anche sullo stesso
        // campo: il caso dello swipe è proprio un `blur` seguito dal `focus`
        // di prima. Il `refocus` della cura non la conta: lo tace la pagina.
        if (state == Dom.TEXT) occasion()
    }

    /** Un'occasione nuova senza un cambio di fuoco (il rientro in primo
     *  piano): il fuoco della pagina resta quello che era. */
    fun occasion() {
        heals = 0
        gaveUp = false
    }

    fun check(accepting: Boolean, windowFocused: Boolean): Action {
        // Senza il fuoco della finestra decide Android: quando torna, rilega
        // da sé (è il «dialogo nativo che ripara»), e poi si ricontrolla.
        if (dom != Dom.TEXT || accepting || !windowFocused) return Action.NONE
        when (heals) {
            0 -> { heals++; return Action.REFOCUS }
            1 -> { heals++; return Action.REBIND }
        }
        if (gaveUp) return Action.NONE
        gaveUp = true
        return Action.GIVE_UP
    }
}
