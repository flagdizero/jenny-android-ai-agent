package com.flagdizero.jenny

import android.Manifest
import android.animation.Animator
import android.animation.ObjectAnimator
import android.app.Activity
import android.app.AlarmManager
import android.app.PendingIntent
import android.app.role.RoleManager
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.content.res.Configuration
import android.content.res.Resources
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.os.SystemClock
import android.util.Log
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.InputMethodManager
import android.webkit.JavascriptInterface
import android.webkit.RenderProcessGoneDetail
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebChromeClient.FileChooserParams
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.RequiresApi
import androidx.appcompat.app.AppCompatActivity
import androidx.browser.customtabs.CustomTabsIntent
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.core.graphics.ColorUtils
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebMessageCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import java.io.File
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import org.json.JSONArray
import org.json.JSONObject

class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "Jenny"
        private const val GATEWAY_HOST = "127.0.0.1"
        private const val GATEWAY_PORT = 18790
        // Il path — e SOLO quel path — che serve la SPA. Vedi isInternalGatewayUrl():
        // il confronto è per uguaglianza, non per prefisso.
        private const val GATEWAY_PATH = "/html-mobile/"
        private const val GATEWAY_URL = "http://${GATEWAY_HOST}:${GATEWAY_PORT}${GATEWAY_PATH}"
        // L'origine della SPA, nella forma che vogliono le regole di
        // addWebMessageListener (schema://host:porta, niente path). La vista
        // esterna delle Jenny App sta su 127.0.0.1 ma su un'ALTRA porta, e le
        // cornici delle app hanno origine opaca: nessuna delle due combacia.
        private const val GATEWAY_ORIGIN = "http://${GATEWAY_HOST}:${GATEWAY_PORT}"
        // I due nomi con cui il nativo compare nella pagina. Il JS non li usa
        // direttamente: li ricompone `shared/native-bridge.js` in
        // `window.JennyNative`. V. installNativeBridges().
        private const val NATIVE_INFO_JS = "JennyNativeInfo"
        private const val NATIVE_PORT_JS = "JennyNativePort"
        private const val RETRY_DELAY_MS = 500L
        private const val MAX_RETRIES = 30
        private const val PREFS_NAME = "jenny"
        private const val PREF_BOOT_TO_CHAT = "boot_to_chat"
        // L'ultimo tema scelto nella WebUI, come colori già risolti: v.
        // applyBootPalette. Li scrivono setThemeBars e setFloatingPalette.
        private const val PREF_BOOT_BG = "boot_bg"
        private const val PREF_BOOT_LIGHT = "boot_light"
        private const val PREF_BOOT_TEXT = "boot_text"
        private const val PREF_BOOT_ACCENT = "boot_accent"
        private const val PREF_BOOT_ON_ACCENT = "boot_on_accent"
        // Il ripiego quando nessun tema è ancora arrivato (primo avvio, o
        // workspace appena ripristinato): i token di `synthwave`, il tema di
        // default, gli stessi di themes.xml e di activity_main.xml.
        private const val DEFAULT_BG = 0xFF111013.toInt()        // --bg
        private const val DEFAULT_TEXT = 0xFFF2ECFF.toInt()      // --text
        private const val DEFAULT_ACCENT = 0xFFF92AAD.toInt()    // --accent
        private const val DEFAULT_ON_ACCENT = 0xFF0A090B.toInt() // --on-accent
        // Alfa dei testi secondari, sul colore del testo: 0.54. I temi hanno
        // ciascuno il suo --text-muted, ma il nativo riceve solo il testo, e
        // per due righe sotto lo spinner un'alfa basta.
        private const val BOOT_MUTED_ALPHA = 0x8A
        // Gli splash dei sette temi (values-v31/themes.xml). Il colore di
        // ciascuno si legge dallo stile stesso, quindi qui non se ne ripete
        // nessuno: v. syncSplashTheme.
        private val SPLASH_THEMES = intArrayOf(
            R.style.Theme_Jenny_Splash_Chanel,
            R.style.Theme_Jenny_Splash_Synthwave,
            R.style.Theme_Jenny_Splash_Kyoto,
            R.style.Theme_Jenny_Splash_Sticker,
            R.style.Theme_Jenny_Splash_Comic,
            R.style.Theme_Jenny_Splash_Y2k,
            R.style.Theme_Jenny_Splash_Stone,
        )
        // Ultima Build.FINGERPRINT vista: cambia solo con un aggiornamento di
        // sistema, che su Samsung e Xiaomi rimette l'app fra quelle ottimizzate.
        private const val PREF_LAST_FINGERPRINT = "last_build_fingerprint"
        // Quante volte, e quando, hai aperto ogni voce del cassetto. Formato
        // compatto `{"<key>": [conteggio, ultimoMs]}` — lo decide
        // `shared/launcher-rank.js`, qui è una stringa opaca.
        private const val PREF_LAUNCHER_USAGE = "launcher_usage"
        // Chiave del Bundle per pendingExportPath: v. onSaveInstanceState.
        private const val STATE_PENDING_EXPORT = "pending_export_path"
        private const val STATE_BORN_AS_HOME = "born_as_home"
        // First launch pays Chaquopy bootstrap + package extraction inside
        // GatewayService, which can take well beyond the WebView retry window.
        private const val BOOT_POLL_INTERVAL_MS = 250L
        private const val BOOT_POLL_TIMEOUT_MS = 90_000L

        /**
         * Espressione valutata a ogni pressione del tasto Indietro. Ritorna un
         * booleano vero: `true` solo se la SPA è viva e ha ricevuto la
         * pressione. La versione precedente (`if (window.mobileApp) …`) valeva
         * sempre `"null"` — un `if` non è un'espressione in JS — quindi il
         * nativo non poteva distinguere "consumata" da "documento sostituito, il
         * tasto è morto per sempre".
         *
         * Un'eccezione dentro handleHardwareBack NON diventa un ricaricamento:
         * la SPA c'è, la pressione è arrivata. Viene ri-sollevata fuori dallo
         * stack corrente così window.onerror la vede e la segnala come sempre —
         * le rotture rumorose devono restare rumorose.
         */
        private const val BACK_PRESS_JS = """
            (function () {
              var app = window.mobileApp;
              if (!app || typeof app.handleHardwareBack !== 'function') return false;
              try { app.handleHardwareBack(); }
              catch (e) { setTimeout(function () { throw e; }, 0); }
              return true;
            })()
        """

        /**
         * Action con cui NotifierBridge marca il `contentIntent` di un alert
         * proattivo. Senza, l'intent era indistinguibile da un rilancio
         * qualunque dell'activity: `onNewIntent` instrada solo CATEGORY_HOME,
         * quindi il tap portava l'app in primo piano esattamente dov'era —
         * dentro una mini-app, in Wiki, ovunque — senza mostrare il messaggio.
         * Il posto si tiene ancora (dal 10/10/2026): la chat sale sopra.
         */
        const val ACTION_OPEN_CHAT = "com.flagdizero.jenny.action.OPEN_CHAT"

        /**
         * Il gettone che dice «questo [ACTION_OPEN_CHAT] l'abbiamo fatto noi».
         *
         * L'activity è esportata (è il launcher), quindi l'action da sola
         * l'arriva a mandare qualunque app con un intent esplicito — e il tap
         * sull'alert **cancella gli avvisi**: un'altra app poteva far sparire
         * dalla tendina i messaggi proattivi senza che nessuno li avesse letti.
         * Il gettone è un segreto casuale nelle preferenze private dell'app,
         * uguale fra un processo e l'altro (un `PendingIntent` di un alert
         * sopravvive alla morte del processo che l'ha creato); senza, l'intent
         * vale come un avvio qualunque: niente chat forzata, niente avvisi
         * cancellati.
         */
        private const val EXTRA_OPEN_CHAT_TOKEN = "com.flagdizero.jenny.extra.OPEN_CHAT_TOKEN"
        private const val PREF_OPEN_CHAT_TOKEN = "open_chat_token"

        private fun openChatToken(context: Context): String = synchronized(this) {
            val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            prefs.getString(PREF_OPEN_CHAT_TOKEN, null) ?: run {
                val bytes = ByteArray(16).also { java.security.SecureRandom().nextBytes(it) }
                val fresh = bytes.joinToString("") { "%02x".format(it) }
                // commit(): il gettone deve essere quello su disco prima che un
                // PendingIntent lo porti in giro.
                prefs.edit().putString(PREF_OPEN_CHAT_TOKEN, fresh).commit()
                fresh
            }
        }

        /** La chat in cui porta un [ACTION_OPEN_CHAT]: `project:<nome>` per
         *  l'alert di un quaderno, assente per la personale (v.
         *  [AlertConversation]). Fuori dal gettone non conta niente: lo si legge
         *  solo dopo [isOurOpenChat]. */
        private const val EXTRA_OPEN_CHAT_CONVERSATION =
            "com.flagdizero.jenny.extra.OPEN_CHAT_CONVERSATION"

        /** L'intent con cui un nostro alert (o la mascotte) porta in chat:
         *  quella di *conversation*, o la personale. */
        fun openChatIntent(context: Context, conversation: String? = null): Intent =
            Intent(context, MainActivity::class.java)
                .setAction(ACTION_OPEN_CHAT)
                .putExtra(EXTRA_OPEN_CHAT_TOKEN, openChatToken(context))
                .apply { if (conversation != null) putExtra(EXTRA_OPEN_CHAT_CONVERSATION, conversation) }
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

        /** La conversazione chiesta da un [ACTION_OPEN_CHAT] nostro, o `null`
         *  (la personale). Quale quaderno esista lo decide la SPA. */
        private fun openChatConversation(intent: Intent?): String? =
            asConversation(intent?.getStringExtra(EXTRA_OPEN_CHAT_CONVERSATION))

        /** `project:<nome>`, o `null` per qualunque altra cosa: la personale. */
        private fun asConversation(value: String?): String? =
            value?.takeIf { it.startsWith("project:") && it.length > "project:".length }

        /** Un [ACTION_OPEN_CHAT] passato alla home da [redirectToHome]: l'intent
         *  home deve restare `ACTION_MAIN`, quindi la richiesta viaggia qui,
         *  col suo gettone accanto. */
        private const val EXTRA_OPEN_CHAT_VIA_HOME = "com.flagdizero.jenny.extra.OPEN_CHAT_VIA_HOME"

        /** È un [ACTION_OPEN_CHAT] fatto da noi? (v. [EXTRA_OPEN_CHAT_TOKEN]) */
        private fun isOurOpenChat(context: Context, intent: Intent?): Boolean {
            val asked = intent?.action == ACTION_OPEN_CHAT ||
                intent?.getBooleanExtra(EXTRA_OPEN_CHAT_VIA_HOME, false) == true
            if (intent == null || !asked) return false
            val presented = intent.getStringExtra(EXTRA_OPEN_CHAT_TOKEN) ?: return false
            return java.security.MessageDigest.isEqual(
                presented.toByteArray(), openChatToken(context).toByteArray()
            )
        }

        /** Un intent home vero: quello che dà a un'activity il tipo `home`
         *  (`ActivityRecord.isHomeIntent`: `ACTION_MAIN` e la sola categoria
         *  HOME). */
        private fun isHomeIntent(intent: Intent?): Boolean =
            intent?.action == Intent.ACTION_MAIN &&
                intent.categories?.let { it.size == 1 && Intent.CATEGORY_HOME in it } == true

        /** Jenny è la home predefinita del telefono, adesso?
         *
         *  Dal ruolo, non risolvendo l'intent HOME: da Android 11 la visibilità
         *  dei pacchetti nasconde gli altri launcher (le `<queries>` vedono
         *  solo LAUNCHER), e `resolveActivity` trovava solo noi — rispondeva
         *  «sì» anche con Nexus come home, misurato sull'emulatore il
         *  10/10/2026. Sotto Android 10 il ruolo non c'è, ma nemmeno la
         *  visibilità: lì la risoluzione dice il vero. */
        private fun isDefaultHome(context: Context): Boolean {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val roles = context.getSystemService(RoleManager::class.java) ?: return false
                return roles.isRoleAvailable(RoleManager.ROLE_HOME) && roles.isRoleHeld(RoleManager.ROLE_HOME)
            }
            val home = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
            val found = context.packageManager.resolveActivity(home, PackageManager.MATCH_DEFAULT_ONLY)
            return found?.activityInfo?.packageName == context.packageName
        }

        /**
         * La chat è la vista a schermo? È la domanda che separa questo
         * ``onResume`` da quello di una volta (v. ``NotifierBridge.clearAlerts``):
         * il ritorno in primo piano non dice niente su cosa l'utente stia
         * guardando, e in un launcher scatta a ogni pressione di Home.
         *
         * Senza SPA caricata il risultato è ``null``, che non è né
         * ``"personal"`` né un quaderno: nessun alert viene cancellato, ed è la
         * direzione d'errore giusta.
         *
         * Lo chiede a un metodo e non a un campo: leggeva ``app.currentMode``,
         * che esiste solo nell'officina, e nella casa — il guscio di default —
         * la risposta era sempre no e gli alert non si cancellavano mai
         * (24/09/2026). Ogni guscio sa a modo suo cosa vuol dire "la chat è a
         * schermo"; qui si chiede, come per ``openChat``.
         *
         * Risponde **quale** chat: `"personal"`, `"project:<nome>"` o `""`. Un
         * guscio senza ``chatOnScreenKey`` (l'officina) risponde come prima,
         * solo per la personale.
         */
        private const val CHAT_ON_SCREEN_JS = """
            (function () {
              var app = window.mobileApp;
              if (!app) return '';
              if (typeof app.chatOnScreenKey === 'function') return app.chatOnScreenKey() || '';
              return typeof app.isChatOnScreen === 'function' && app.isChatOnScreen() ? 'personal' : '';
            })()
        """

        /**
         * Costringe la pagina a comporre un frame nuovo, iframe compresi.
         *
         * Sul Titan 2, sbloccando con l'impronta a schermo spento, Android fa
         * resume/pause cinque volte in 70 ms prima di accendere lo schermo
         * (logcat del 10/10/2026), e ognuna passa da `webView.onPause()` /
         * `onResume()`. Dopo la raffica la WebView restava vuota — la pagina
         * c'era, una mini-app aperta compresa, e ricompariva al primo tocco:
         * nessuno chiedeva al compositor un frame nuovo.
         *
         * Un'opacità sulla radice per un frame basta a chiederlo e non si vede;
         * un `transform` farebbe lo stesso, ma per quel frame sposterebbe tutto
         * ciò che è `position: fixed`.
         */
        private const val REPAINT_JS = """
            (function () {
              var root = document.documentElement;
              if (!root) return;
              root.style.opacity = '0.999';
              requestAnimationFrame(function () {
                requestAnimationFrame(function () { root.style.opacity = ''; });
              });
            })()
        """

        // Attesa dall'ultimo onResume prima del ridisegno: la raffica dello
        // sblocco dura ~70 ms, e lo schermo si accende subito dopo.
        private const val REPAINT_SETTLE_MS = 300L

        // Finestra minima fra due recuperi della SPA: il ricaricamento non è
        // istantaneo e senza questo una raffica di pressioni lo farebbe ripartire
        // da capo ogni volta, senza mai arrivare in fondo.
        private const val SPA_RECOVERY_MIN_INTERVAL_MS = 3_000L

        // Sotto questa soglia la risposta alla richiesta della posizione è
        // arrivata senza che Android mostrasse un dialog (negata per sempre).
        private const val LOCATION_NO_DIALOG_MS = 400L

        // L'activity è davanti (onResume/onPause). Letto dalla mascotte
        // flottante e da UpdateBridge; NotifierBridge lo legge insieme a
        // [isChatOnScreen]. @Volatile: scritto dal main thread, letto da altri.
        @Volatile
        var isInForeground = false
            private set

        // La chat personale è la cosa che l'utente ha davanti. Da solo
        // isInForeground non lo dice: Jenny è il launcher, e sta davanti anche
        // sulla pagina App, nelle Impostazioni, sopra una bacheca — e lì un
        // promemoria soppresso era un promemoria perso (giro UX del
        // 10/10/2026). Lo dice la SPA a ogni cambio (chatOpened/chatClosed);
        // si spegne quando l'activity va dietro o la pagina riparte, e al
        // rientro lo richiede onResume. Letto da NotifierBridge (thread
        // Python via Chaquopy).
        @Volatile
        var isChatOnScreen = false
            private set

        // Il gemello per i quaderni: `project:<nome>` se a schermo c'è la chat
        // di quel quaderno. Stesse regole di [isChatOnScreen], che resta solo
        // della personale — è quella che zittisce gli avvisi di Jenny.
        @Volatile
        var notebookChatOnScreen: String? = null
            private set

        /** La chat di *conversation* (`null`: la personale) è a schermo? */
        fun chatOnScreen(conversation: String?): Boolean =
            if (conversation == null) isChatOnScreen else notebookChatOnScreen == conversation
    }

    private var retryCount = 0
    private var lastSpaRecoveryAt = 0L
    private var loaded = false
    private var mainFrameError = false
    private var loadingView: FrameLayout? = null
    private var errorView: FrameLayout? = null
    private var webView: WebView? = null

    // Le estensioni native di questo flavor, agganciate a questa Activity
    // (v. [NativeExtension]); vuoto nel flavor `foss`.
    private var nativeExtensions: Map<String, NativeExtension.Attached> = emptyMap()
    // Il callback del tasto Indietro. Abilitato solo mentre la SPA è a schermo:
    // v. onCreate, onPageFinished, showLoading e showError.
    private var backCallback: OnBackPressedCallback? = null
    // Il ridisegno dopo un rientro (v. REPAINT_JS): rimandato a ogni onResume,
    // quindi a una raffica ne segue uno solo, a raffica finita.
    private val repaintHandler = Handler(Looper.getMainLooper())
    private val repaintRunnable = Runnable { forceRepaint() }
    // L'ultimo splash chiesto al sistema da questa activity (v. syncSplashTheme),
    // per non ripetere la chiamata a ogni setThemeBars. 0 = nessuno ancora.
    private var requestedSplashTheme = 0
    // Il tap su una notifica proattiva chiede la chat. Se l'activity era morta
    // la richiesta non passa da onNewIntent ma da onCreate, e allora deve
    // arrivare fino all'URL iniziale: la legge buildGatewayUrl(), che gira sul
    // thread di polling — scritta in onCreate prima che quel thread parta.
    @Volatile
    private var openChatOnLoad = false
    // ...e in quale chat: `project:<nome>` per l'alert di un quaderno, o null.
    @Volatile
    private var openChatConversationOnLoad: String? = null
    // L'activity è passata da onStop dall'ultimo onResume: è stata dietro
    // un'altra app (o a schermo spento). Un Home che arriva così riporta la
    // casa davanti, e non deve anche riportarla alla chat (v. onNewIntent).
    // Main thread soltanto: onStop, onNewIntent e onResume.
    private var cameBackFromBackground = false

    // Nata da un intent home, cioè nel task home? Letto in onCreate e non
    // dopo: onNewIntent sostituisce `intent` con quello di una notifica, e
    // una copia home resta home. V. redirectToHome.
    private var bornAsHome = false
    // Resolved once the gateway socket is confirmed listening (config.json,
    // and therefore the bootstrap secret, is guaranteed to already exist by
    // then — ensure_minimal_config() runs before the port is opened). Falls
    // back to the plain GATEWAY_URL if the secret can't be read for any
    // reason; /webui/bootstrap will then 401, same as if this fix didn't exist.
    private var resolvedGatewayUrl: String = GATEWAY_URL

    // Esito, per questo avvio, del confronto fra la fingerprint corrente e
    // quella dell'ultimo avvio. Va latchato: la prima chiamata consuma la
    // differenza scrivendo la nuova fingerprint, ma la risposta deve restare
    // la stessa per tutte le superfici che la chiedono (impostazioni,
    // onboarding, card Telegram) — altrimenti una sola di loro mostrerebbe
    // l'avviso forte e le altre no. Lo decide latchSystemUpdate() sul thread di
    // polling, prima del caricamento; @Volatile perché lo legge il thread
    // JavaBridge della WebView.
    @Volatile
    private var systemUpdateLatch: Boolean? = null

    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) {
                // The service's first startForeground() call happened before
                // the user granted this permission and silently failed to
                // show; re-issuing the start request makes it retry.
                startGatewayService()
            } else {
                Log.w(TAG, "Notification permission denied; gateway notification stays hidden")
            }
            // Il secondo permesso parte SOLO da qui, mai in parallelo: Activity
            // tiene una sola richiesta per volta (mHasCurrentPermissionsRequest)
            // e scarta la seconda con "Can request only one set of permissions
            // at a time". Chiedendoli entrambi di fila in onCreate, il dialog
            // della posizione non compariva mai al primo avvio — e siccome il
            // codice non distingue "negato" da "mai chiesto", non ricompariva
            // nemmeno dopo. Vale per entrambi i rami: che le notifiche siano
            // state concesse o rifiutate, la posizione va comunque chiesta.
            ensureLocationPermission()
        }

    // Posizione: richiesta all'avvio perché il toggle è ON di default. Se
    // negato, LocationBridge ritorna null e non viene iniettato nulla. Da
    // Mani → Posizione la si può richiedere (`requestLocationPermission`).
    //
    // Precisa **e** approssimativa insieme: il bridge accetta l'una o l'altra
    // (LocationBridge), e da Android 12 chi chiede solo la precisa non offre
    // all'utente la scelta «approssimativa» — che è quella che molti danno.
    private val locationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { result ->
            val granted = result.values.any { it }
            // Una risposta arrivata subito vuol dire che Android non ha
            // mostrato niente: il permesso è negato per sempre, e dall'app si
            // può solo mandare alla scheda di Jenny nelle impostazioni. Solo se
            // la richiesta veniva da un tocco, non da quella d'avvio.
            val askedAt = locationAskedFromUiAt
            locationAskedFromUiAt = 0L
            if (granted) {
                // Il FGS è già partito come specialUse (permesso non ancora
                // concesso all'avvio): ri-avviarlo lo fa ripartire con anche il
                // tipo `location`, necessario per l'app-op location a UI non in
                // primo piano.
                startGatewayService()
            } else {
                Log.w(TAG, "Location permission denied; device location stays unavailable")
                if (askedAt > 0L && SystemClock.elapsedRealtime() - askedAt < LOCATION_NO_DIALOG_MS) {
                    openAppDetailsSettings()
                }
            }
            // La WebUI ridisegna l'avviso di Mani: il dialog di sistema non
            // produce un `visibilitychange` affidabile nella WebView.
            webView?.evaluateJavascript(
                "window.dispatchEvent(new Event('jenny-location-permission'))", null
            )
        }

    /** Quando è partita l'ultima richiesta della posizione nata da un tocco
     *  (`elapsedRealtime`), 0 se nessuna è in volo. */
    private var locationAskedFromUiAt = 0L

    private fun hasLocationPermission(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    private fun launchLocationRequest() {
        locationPermissionLauncher.launch(
            arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION)
        )
    }

    /** La scheda di Jenny nelle impostazioni di Android: dove si concede un
     *  permesso che l'app non può più chiedere. */
    private fun openAppDetailsSettings() {
        try {
            startActivity(
                Intent(
                    android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.parse("package:$packageName")
                ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (e: Exception) {
            Log.e(TAG, "App details settings could not be opened", e)
        }
    }

    // ── Launcher: la griglia app deve seguire i cambi di pacchetto ──
    // Prima la SPA si affidava solo a `visibilitychange`, ma l'uninstaller di
    // sistema è un'activity translucida: la WebView resta visibile, l'evento
    // non scatta e l'icona di un'app appena disinstallata restava nella griglia.
    // Qui si ascolta direttamente il PackageManager. Coda + drain su onResume
    // perché mentre il dialog è davanti la WebView è in pausa: notificare la SPA
    // solo quando è di nuovo attiva rende la consegna deterministica.
    // Receiver e onResume girano entrambi sul main thread: nessun lock serve.
    private val pendingPackageNotices = ArrayDeque<Pair<String, String>>()

    private val packageChangeReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            val action = intent?.action ?: return
            // Un aggiornamento arriva come REMOVED+ADDED con EXTRA_REPLACING:
            // il pacchetto non è sparito, non c'è niente da annunciare.
            if (intent.getBooleanExtra(Intent.EXTRA_REPLACING, false)) return
            val pkg = intent.data?.schemeSpecificPart ?: return
            val kind = when (action) {
                Intent.ACTION_PACKAGE_REMOVED, Intent.ACTION_PACKAGE_FULLY_REMOVED -> "removed"
                Intent.ACTION_PACKAGE_ADDED -> "added"
                else -> return
            }
            // FULLY_REMOVED segue REMOVED per la stessa disinstallazione: il lato
            // JS accorpa i refresh, quindi il doppione non costa una seconda fetch.
            pendingPackageNotices.add(kind to pkg)
            if (isInForeground) flushPackageNotices()
        }
    }

    private fun registerPackageChangeReceiver() {
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_PACKAGE_ADDED)
            addAction(Intent.ACTION_PACKAGE_REMOVED)
            addAction(Intent.ACTION_PACKAGE_FULLY_REMOVED)
            addDataScheme("package")
        }
        ContextCompat.registerReceiver(
            this, packageChangeReceiver, filter, ContextCompat.RECEIVER_NOT_EXPORTED
        )
    }

    private fun flushPackageNotices() {
        if (pendingPackageNotices.isEmpty()) return
        // WebView non ancora pronta: la coda resta in attesa del prossimo flush.
        val wv = webView ?: return
        val batch = pendingPackageNotices.toList()
        pendingPackageNotices.clear()
        batch.forEach { (kind, pkg) ->
            wv.evaluateJavascript(
                "window.mobileApp && window.mobileApp.onPackageChanged && " +
                    "window.mobileApp.onPackageChanged(${JSONObject.quote(kind)}, ${JSONObject.quote(pkg)})",
                null
            )
        }
    }

    // ── Backup: SAF launcher ──
    // Il file .jbk cifrato è preparato dal gateway Python in
    // <filesDir>/backup_staging/; qui si fa solo la copia da/verso l'URI
    // content:// scelto dall'utente (Drive, SD, ecc.). Nessun permesso storage
    // richiesto: la Storage Access Framework delega tutto al picker di sistema.
    //
    // Sopravvive a una ricreazione dell'activity (onSaveInstanceState): il
    // picker di sistema è un'altra activity, e se questa viene ricreata mentre
    // lui è davanti il risultato arriva all'istanza nuova — che senza il path
    // rispondeva "annullato" a un salvataggio che l'utente aveva appena
    // confermato. Il launcher di ActivityResult si ricorda la richiesta nello
    // stesso Bundle, quindi i due sopravvivono o si perdono insieme.
    @Volatile
    private var pendingExportPath: String? = null

    private val exportBackupLauncher =
        registerForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
            val src = pendingExportPath
            pendingExportPath = null
            if (uri == null || src == null) {
                notifyBackupJs("onExportDone", false)
            } else {
                copyExportToUri(File(src), uri)
            }
        }

    private val importBackupLauncher =
        registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            if (uri == null) {
                notifyBackupJs("onImportPicked", false)
            } else {
                copyImportFromUri(uri)
            }
        }

    // ── Composer: picker allegati WebView ──
    // WebChromeClient di default (vedi loadWebView) non implementa
    // onShowFileChooser: un <input type=file> nella WebView non apre nessun
    // picker senza questo bridge. Un solo callback pendente alla volta, come
    // da contratto onShowFileChooser (nessuna selezione concorrente possibile
    // lato composer). Il chooser di sistema offre file/galleria + scatto foto.
    private var filePickerCallback: ValueCallback<Array<Uri>>? = null
    // Uri del file temporaneo (FileProvider su cacheDir) passato alla
    // fotocamera via EXTRA_OUTPUT. Valorizzato solo quando il chooser include
    // lo scatto foto; su result senza dati dal picker documenti, lo scatto ha
    // scritto qui.
    private var pendingCameraUri: Uri? = null

    private val fileChooserLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            val callback = filePickerCallback
            filePickerCallback = null
            val cameraUri = pendingCameraUri
            pendingCameraUri = null
            callback?.onReceiveValue(resolveFileChooserResult(result, cameraUri))
        }

    /** Apre il chooser di sistema: file manager + galleria (ACTION_GET_CONTENT
     *  su qualsiasi MIME, multiplo) e, se disponibile, lo scatto foto come
     *  intent iniziale. Lancia da onShowFileChooser dopo aver registrato il
     *  callback WebView. */
    private fun launchFileChooser() {
        val getContent = Intent(Intent.ACTION_GET_CONTENT).apply {
            type = "*/*"
            addCategory(Intent.CATEGORY_OPENABLE)
            putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
        }
        val chooser = Intent.createChooser(getContent, null)
        buildCameraCaptureIntent()?.let { camera ->
            chooser.putExtra(Intent.EXTRA_INITIAL_INTENTS, arrayOf(camera))
        }
        fileChooserLauncher.launch(chooser)
    }

    /** Intent di scatto foto che scrive su un file temporaneo in cacheDir
     *  esposto via FileProvider. Ritorna null se non c'è un'app fotocamera (il
     *  chooser mostra solo file/galleria). Nessun permesso CAMERA necessario:
     *  la cattura è delegata all'app fotocamera di sistema. */
    private fun buildCameraCaptureIntent(): Intent? {
        val capture = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
        if (capture.resolveActivity(packageManager) == null) return null
        val photoFile = try {
            val dir = File(cacheDir, "camera").apply { mkdirs() }
            File.createTempFile("cap_", ".jpg", dir)
        } catch (e: Exception) {
            Log.w(TAG, "camera temp file failed (${e.javaClass.simpleName})")
            return null
        }
        val uri = try {
            FileProvider.getUriForFile(this, "$packageName.fileprovider", photoFile)
        } catch (e: Exception) {
            Log.w(TAG, "camera FileProvider failed (${e.javaClass.simpleName})")
            photoFile.delete()
            return null
        }
        pendingCameraUri = uri
        capture.putExtra(MediaStore.EXTRA_OUTPUT, uri)
        capture.addFlags(
            Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION
        )
        return capture
    }

    /** Estrae le Uri dal result del chooser. Documenti: clipData (multi) o data
     *  (single). Scatto foto: nessun dato dal picker → si usa la Uri temporanea
     *  se il file è stato scritto. Il temp inutilizzato viene ripulito. */
    private fun resolveFileChooserResult(
        result: androidx.activity.result.ActivityResult,
        cameraUri: Uri?,
    ): Array<Uri>? {
        if (result.resultCode != Activity.RESULT_OK) {
            discardCameraTemp(cameraUri)
            return null
        }
        val data = result.data
        val picked = ArrayList<Uri>()
        val clip = data?.clipData
        if (clip != null) {
            for (i in 0 until clip.itemCount) {
                clip.getItemAt(i)?.uri?.let { picked.add(it) }
            }
        } else {
            data?.data?.let { picked.add(it) }
        }
        if (picked.isNotEmpty()) {
            discardCameraTemp(cameraUri)  // documenti scelti: scatto non usato
            return picked.toTypedArray()
        }
        if (cameraUri != null && cameraTempHasContent(cameraUri)) {
            return arrayOf(cameraUri)
        }
        discardCameraTemp(cameraUri)
        return null
    }

    private fun cameraTempHasContent(uri: Uri): Boolean = try {
        contentResolver.openFileDescriptor(uri, "r")?.use { it.statSize > 0 } ?: false
    } catch (e: Exception) {
        false
    }

    private fun discardCameraTemp(uri: Uri?) {
        if (uri == null) return
        try {
            contentResolver.delete(uri, null, null)
        } catch (e: Exception) {
            // best-effort: i temp in cacheDir vengono comunque ripuliti dal sistema
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Le estensioni del flavor, prima di qualunque `return`: registrano i
        // loro launcher, e l'ordine di registrazione dev'essere sempre lo
        // stesso (v. [NativeExtension.attach]).
        nativeExtensions = Extensions.all.associate { it.name to it.attach(this) }
        // Una ricreazione (onRenderProcessGone, un cambio di configurazione)
        // rilegge `intent`, che onNewIntent può aver sostituito con quello di
        // una notifica: chi è nata home lo resta, e lo dice lo stato salvato.
        bornAsHome = savedInstanceState?.getBoolean(STATE_BORN_AS_HOME) ?: isHomeIntent(intent)
        // Prima di costruire qualunque cosa: se questa copia è di troppo, se ne
        // va senza aver acceso una WebView (v. redirectToHome).
        if (savedInstanceState == null && !bornAsHome && isDefaultHome(this) && redirectToHome()) return
        pendingExportPath = savedInstanceState?.getString(STATE_PENDING_EXPORT)
        setContentView(R.layout.activity_main)

        loadingView = findViewById(R.id.loading_view)
        errorView = findViewById(R.id.error_view)
        webView = findViewById(R.id.webview)

        applyBootPalette()

        findViewById<Button>(R.id.retry_button).setOnClickListener {
            retryCount = 0
            loaded = false
            mainFrameError = false
            showLoading()
            startGatewayAndLoad()
        }

        // Launcher: back is delegated to the SPA. It either consumes it inside
        // an open Jenny App or falls back to natural WebView history.
        //
        // Nasce DISABILITATO. Registrarlo abilitato da qui significa intercettare
        // il tasto Indietro quando la SPA non esiste ancora — per tutta la
        // ripartenza del gateway, che può durare fino a BOOT_POLL_TIMEOUT_MS —
        // e per sempre sulla schermata d'errore, dove l'unico comando è il
        // pulsante Riprova: il tasto non fa niente e non lo fa fare a nessun
        // altro. Da disabilitato la pressione torna al dispatcher di sistema.
        // Si riabilita quando la pagina è davvero a schermo (onPageFinished) e
        // si rispegne ogni volta che la SPA lascia lo schermo (showLoading,
        // showError).
        val backCb = object : OnBackPressedCallback(false) {
            override fun handleOnBackPressed() {
                webView?.evaluateJavascript(BACK_PRESS_JS) { result ->
                    // "true" = la SPA c'è e ha ricevuto la pressione. Qualunque
                    // altra cosa ("false", "null" se il documento non è la SPA)
                    // significa che il back sparirebbe nel vuoto: si recupera.
                    if (result?.trim() != "true") recoverLostSpa()
                }
            }
        }
        onBackPressedDispatcher.addCallback(this, backCb)
        backCallback = backCb

        // Tap sull'alert con l'activity morta: qui non c'è nessuna SPA da
        // instradare, la richiesta deve arrivare all'URL iniziale (v.
        // buildGatewayUrl). La chat si aprirà: gli alert sono consumati. Solo
        // per un intent nostro (v. EXTRA_OPEN_CHAT_TOKEN).
        if (isOurOpenChat(this, intent)) {
            openChatOnLoad = true
            openChatConversationOnLoad = openChatConversation(intent)
            NotifierBridge.clearAlerts(this, "cold-start-alert-tap", openChatConversationOnLoad)
        }

        // Catena, non due chiamate: ensureNotificationPermission() invoca
        // ensureLocationPermission() quando la prima richiesta è conclusa (o
        // quando non c'era niente da chiedere).
        ensureNotificationPermission()
        registerPackageChangeReceiver()
        startGatewayAndLoad()
    }

    override fun onDestroy() {
        try {
            unregisterReceiver(packageChangeReceiver)
        } catch (e: IllegalArgumentException) {
            Log.w(TAG, "packageChangeReceiver already unregistered")
        }
        // I comandi già in coda finiscono; quelli nuovi non entrano più (v.
        // NativeCommandListener, che guarda isShutdown prima di accodare).
        nativeExecutor.shutdown()
        // Un picker di allegati rimasto aperto va chiuso con null (contratto di
        // onShowFileChooser), o la WebView resta in attesa di un callback che
        // non arriverà mai.
        filePickerCallback?.onReceiveValue(null)
        filePickerCallback = null
        // La WebView va distrutta, non abbandonata: tiene il suo renderer, la
        // WebSocket della SPA e i timer JS, e un'activity ricreata ne costruisce
        // un'altra — per un po' giravano due SPA sullo stesso gateway. Prima
        // si toglie dal layout (la documentazione di destroy() lo chiede), e
        // `webView = null` prima di tutto: i callback che arrivano dopo (retry,
        // backup, comandi nativi) leggono `webView?` e trovano niente.
        webView?.let { wv ->
            webView = null
            (wv.parent as? ViewGroup)?.removeView(wv)
            wv.stopLoading()
            wv.destroy()
        }
        super.onDestroy()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        pendingExportPath?.let { outState.putString(STATE_PENDING_EXPORT, it) }
        outState.putBoolean(STATE_BORN_AS_HOME, bornAsHome)
    }

    /**
     * Veste finestra, barre di sistema, loading view ed error view con
     * l'ultimo tema scelto nella WebUI.
     *
     * Il guscio nativo i temi non li conosce: i colori glieli spinge la SPA
     * (setThemeBars, setFloatingPalette), che li calcola dal CSS, e qui si
     * ricordano per il lancio successivo. Senza, la schermata di caricamento
     * aveva una palette sua — un nero bluastro con lo spinner violetto — che
     * non era nessuno dei sette temi. Con un tema chiaro il caricamento è
     * chiaro anch'esso.
     *
     * Il contenuto rientra già da sé (il decor di AppCompat consuma l'inset
     * della status bar: la WebView parte sotto), quindi qui non si tocca il
     * layout — solo il *colore*. Resta fuori la starting window, i pochi
     * istanti prima di onCreate: quella legge themes.xml e basta.
     *
     * **Solo dal thread UI.**
     */
    private fun applyBootPalette() {
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val bg = prefs.getInt(PREF_BOOT_BG, DEFAULT_BG)
        val text = prefs.getInt(PREF_BOOT_TEXT, DEFAULT_TEXT)
        val accent = prefs.getInt(PREF_BOOT_ACCENT, DEFAULT_ACCENT)
        val onAccent = prefs.getInt(PREF_BOOT_ON_ACCENT, DEFAULT_ON_ACCENT)
        val muted = ColorUtils.setAlphaComponent(text, BOOT_MUTED_ALPHA)

        window.setBackgroundDrawable(ColorDrawable(bg))
        window.statusBarColor = bg
        window.navigationBarColor = bg
        applyBarAppearance(prefs.getBoolean(PREF_BOOT_LIGHT, false))

        loadingView?.setBackgroundColor(bg)
        errorView?.setBackgroundColor(bg)
        findViewById<ProgressBar>(R.id.loading_spinner)?.indeterminateTintList =
            ColorStateList.valueOf(accent)
        for (id in intArrayOf(R.id.loading_title, R.id.error_icon, R.id.error_title)) {
            findViewById<TextView>(id)?.setTextColor(text)
        }
        for (id in intArrayOf(R.id.loading_text, R.id.error_text)) {
            findViewById<TextView>(id)?.setTextColor(muted)
        }
        findViewById<Button>(R.id.retry_button)?.apply {
            backgroundTintList = ColorStateList.valueOf(accent)
            setTextColor(onAccent)
        }
    }

    /**
     * Sceglie lo splash di sistema del **prossimo** lancio: quello dei sette
     * (SPLASH_THEMES) il cui sfondo è [bg], il `--bg` del tema attivo.
     *
     * Lo splash parte prima di qualunque codice dell'app, quindi non può
     * leggere i colori salvati come fa applyBootPalette: legge solo uno stile.
     * Da Android 13 `setSplashScreenTheme` permette di cambiarlo, e il sistema
     * lo ricorda fra un lancio e l'altro. Su Android 12 resta lo sfondo fisso
     * di `Theme.Jenny`, cioè il tema di default; sotto il 12 lo splash non c'è.
     *
     * Si confronta il colore e non l'id del tema perché il nativo l'id non lo
     * riceve. Nessuna corrispondenza — un `--bg` cambiato nel CSS senza
     * toccare l'XML, che un test impedisce — torna allo splash di default.
     */
    @RequiresApi(Build.VERSION_CODES.TIRAMISU)
    private fun syncSplashTheme(bg: Int) {
        val match = SPLASH_THEMES.firstOrNull { splashBackground(it) == bg }
        if (match == null) {
            Log.w(TAG, "No splash theme matches the WebUI background, using the default one")
        }
        val wanted = match ?: Resources.ID_NULL
        if (wanted == requestedSplashTheme) return
        splashScreen.setSplashScreenTheme(wanted)
        requestedSplashTheme = wanted
    }

    @RequiresApi(Build.VERSION_CODES.TIRAMISU)
    private fun splashBackground(style: Int): Int {
        val attrs = theme.obtainStyledAttributes(
            style,
            intArrayOf(android.R.attr.windowSplashScreenBackground),
        )
        return try {
            attrs.getColor(0, 0)
        } finally {
            attrs.recycle()
        }
    }

    private fun applyBarAppearance(light: Boolean) {
        WindowInsetsControllerCompat(window, window.decorView).apply {
            isAppearanceLightStatusBars = light
            isAppearanceLightNavigationBars = light
        }
    }

    // ── Inset di gesture obbligatorio ──
    //
    // Quanta WebView cade dentro la fascia in cui la shell di sistema riconosce
    // la gesture di home, in px fisici. È il numero che il cassetto usa per
    // tenerci sopra la propria lista, e **non si può leggere dal CSS**:
    // `env(safe-area-inset-*)` vale 0px su tutti e quattro i lati, perché il
    // decor di AppCompat consuma gli inset delle barre prima della WebView.
    //
    // Nemmeno `navigationBars` andrebbe bene al suo posto: in modalità gesture
    // la soglia di riconoscimento è più alta della barra (misurati 96 px contro
    // 72 su un emulatore Android 17), ed è la soglia che porta via il tocco.
    //
    // Il valore lo decide la shell del dispositivo: non è una costante da
    // cablare da nessuna parte, si rilegge a ogni cambio di geometria o di
    // modalità di navigazione.
    @Volatile
    private var bottomGestureInsetPx: Int = 0

    /**
     * Ricalcola [bottomGestureInsetPx] e, se è cambiato, lo annuncia alla SPA.
     * **Solo dal thread UI**: legge la geometria delle view.
     */
    private fun refreshGestureInsets() {
        val wv = webView ?: return
        val insets = ViewCompat.getRootWindowInsets(window.decorView) ?: return
        val mandatory = insets.getInsets(WindowInsetsCompat.Type.mandatorySystemGestures()).bottom
        val decor = window.decorView
        /* L'inset è misurato dal bordo della **finestra**, la WebView sta più in
           alto (le barre di sistema la inset-ano): quello che serve al CSS è la
           sola sovrapposizione fra le due. Prenderlo intero sprecherebbe la
           fascia della barra di navigazione, che nella WebView non c'è. */
        val wvLoc = IntArray(2)
        val decorLoc = IntArray(2)
        wv.getLocationInWindow(wvLoc)
        decor.getLocationInWindow(decorLoc)
        val wvBottomInDecor = (wvLoc[1] - decorLoc[1]) + wv.height
        val zoneTop = decor.height - mandatory
        val overlap = (wvBottomInDecor - zoneTop).coerceIn(0, wv.height)
        if (overlap == bottomGestureInsetPx) return
        bottomGestureInsetPx = overlap
        // Prima che la SPA esista non c'è nessuno da avvisare: la legge da sé
        // al primo disegno, con getBottomGestureInset().
        if (!loaded) return
        wv.evaluateJavascript(
            "window.dispatchEvent(new Event('jenny-gesture-insets'))",
            null
        )
    }

    /**
     * Aggancia il ricalcolo dell'inset a ciò che lo può cambiare: un nuovo
     * dispatch di inset (cambio di modalità di navigazione, barre che vanno e
     * vengono) e un cambio di geometria della WebView (rotazione, tastiera che
     * ridimensiona la finestra).
     *
     * Il listener sugli inset **restituisce gli inset intatti**: qui si osserva
     * soltanto, consumarli romperebbe chi li usa più sotto.
     */
    private fun observeGestureInsets(wv: WebView) {
        ViewCompat.setOnApplyWindowInsetsListener(wv) { v, insets ->
            refreshGestureInsets()
            // Si osserva soltanto: la gestione di default della view resta la
            // sua. Restituire `insets` e basta la salterebbe.
            ViewCompat.onApplyWindowInsets(v, insets)
        }
        wv.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> refreshGestureInsets() }
    }

    /**
     * Primo anello della catena dei permessi d'avvio. Se non c'è niente da
     * chiedere (sotto API 33 il permesso non esiste, oppure è già concesso)
     * passa subito al successivo; altrimenti lancia la richiesta e il seguito
     * sta nel callback del launcher — le due richieste non possono essere in
     * volo insieme, v. il commento lì.
     */
    private fun ensureNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            ensureLocationPermission()
            return
        }
        val granted = ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        if (!granted) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            ensureLocationPermission()
        }
    }

    // Basta l'una o l'altra: con la sola approssimativa concessa, chiedere di
    // nuovo la precisa a ogni avvio riproponeva il dialog all'infinito.
    private fun ensureLocationPermission() {
        if (!hasLocationPermission()) launchLocationRequest()
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        setIntent(intent)
        // Tap su una notifica proattiva: la chat personale sale sopra la
        // pagina in cui eri, che resta sotto (v. openChat nella casa); quella
        // di un quaderno porta nel quaderno, con la sua chat sopra. Gli
        // alert pendenti si cancellano SOLO qui — cioè solo quando la chat
        // viene davvero aperta. Stavano in onResume, che in un launcher scatta a
        // ogni ritorno alla home: l'alert veniva cancellato comunque, fosse
        // stato letto o no, e il messaggio proattivo restava senza alcun
        // segnale. Solo per un intent nostro: l'action la può scrivere
        // chiunque (v. EXTRA_OPEN_CHAT_TOKEN), e senza gettone l'intent
        // prosegue come un avvio qualunque.
        if (isOurOpenChat(this, intent)) {
            val conversation = openChatConversation(intent)
            webView?.evaluateJavascript(AlertConversation.openChatJs(conversation)) { result ->
                if (result?.trim() == "true") {
                    NotifierBridge.clearAlerts(this@MainActivity, "alert-tap", conversation)
                } else {
                    Log.w(TAG, "Alert tap did not bring the chat on screen; alerts kept")
                }
            }
            return
        }
        // This app is the device HOME launcher (see AndroidManifest: category
        // HOME + DEFAULT), so every Home press re-delivers the HOME intent here
        // instead of a fresh onCreate (launchMode=singleTask) — the one that
        // brings us back from another app too.
        //
        // Solo un Home premuto con la casa GIÀ davanti la riporta alla chat
        // (goHome: chiude la mini-app, esce dal quaderno, torna alla pagina
        // chat). Quello che arriva da un'altra app riporta la casa davanti e
        // basta, dov'era: un quaderno, una pagina della wiki, una mini-app.
        // Fino al 10/10/2026 li trattava uguali, e ogni giro in un'altra app
        // finiva in chat. È la regola dei launcher (Launcher3 guarda
        // FLAG_ACTIVITY_BROUGHT_TO_FRONT per la stessa domanda): il primo Home
        // riporta a casa, il secondo alla pagina di partenza. La domanda qui è
        // posta a onStop e non al flag: è la nostra definizione di «eri via»,
        // schermo spento compreso.
        //
        // Solo per il VERO intent Home (ACTION_MAIN + CATEGORY_HOME): anche
        // l'alarm di restartApp arriva qui via onNewIntent (intent esplicito,
        // senza categoria) e non deve simulare una pressione di Home.
        if (intent?.hasCategory(Intent.CATEGORY_HOME) == true && !cameBackFromBackground) {
            webView?.evaluateJavascript("if (window.mobileApp) window.mobileApp.goHome()") {}
        }
    }

    override fun onStop() {
        super.onStop()
        cameBackFromBackground = true
        // La rete di redirectToHome: una copia nata come app normale quando
        // Jenny non era ancora la home (l'onboarding, che te la fa scegliere
        // proprio da qui). Diventata home, il primo Home ne crea un'altra nel
        // task home, e questa resterebbe ferma dietro, con la sua WebView.
        if (!bornAsHome && !isChangingConfigurations && isDefaultHome(this)) {
            Log.i(TAG, "Standard-task copy left behind by the home: finishing it")
            finishAndRemoveTask()
        }
    }

    /**
     * Una sola Jenny quando Jenny è la home.
     *
     * Android tiene le activity `home` e quelle normali in task diversi, anche
     * con `singleTask` e la stessa affinità: un intent esplicito senza
     * categoria HOME, quando il task home non c'è, crea un task `standard`; il
     * primo Home ne crea poi uno `home`, con una seconda copia di Jenny. Sul
     * Titan 2, il 09/10/2026, erano due WebView e due WebSocket sul gateway
     * nello stesso processo (254 MB di PSS in tutto), e la copia di troppo si
     * rivedeva dai Recenti, ferma da ore. Riprodotto sull'emulatore: un
     * `install -r` con un'altra app davanti toglie il task home e il sistema
     * non lo rimette; poi basta un intent esplicito — l'`am start -n` del
     * nostro flusso di installazione, ma anche la notifica del gateway, la
     * mascotte, un avviso: sono tutti espliciti — e un Home.
     *
     * Se il task home esistesse, `singleTask` ci avrebbe mandato lì via
     * `onNewIntent`: arrivare qui, nati non-home con Jenny home, vuol dire che
     * non c'è. Lo si crea con un intent home **implicito** — con il componente
     * esplicito Android non dà il tipo `home` a un'app che non sia di sistema —
     * ristretto al nostro pacchetto, e questa copia se ne va. Una richiesta di
     * chat viaggia con lui ([EXTRA_OPEN_CHAT_VIA_HOME]).
     */
    private fun redirectToHome(): Boolean {
        val home = Intent(Intent.ACTION_MAIN)
            .addCategory(Intent.CATEGORY_HOME)
            .setPackage(packageName)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (isOurOpenChat(this, intent)) {
            home.putExtra(EXTRA_OPEN_CHAT_VIA_HOME, true)
                .putExtra(EXTRA_OPEN_CHAT_TOKEN, intent.getStringExtra(EXTRA_OPEN_CHAT_TOKEN))
            openChatConversation(intent)?.let { home.putExtra(EXTRA_OPEN_CHAT_CONVERSATION, it) }
        }
        Log.i(TAG, "Started outside the home task while Jenny is the home: redirecting")
        try {
            startActivity(home)
        } catch (e: Exception) {
            // Senza home a cui passare, questa copia resta: meglio due Jenny
            // per un po' che nessuna.
            Log.w(TAG, "Redirect to the home task failed (${e.javaClass.simpleName})")
            return false
        }
        finish()
        return true
    }

    // Il fuoco torna alla finestra (un dialogo, la tendina, un'altra app che
    // se ne va): Android rilega da sé, e qui si controlla che sia bastato.
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) {
            imeBinding.recheck()
            // Lo sblocco dalla schermata di blocco ridà il fuoco qui, dopo
            // l'ultimo onResume: anche questo rientro va ridisegnato.
            scheduleRepaint()
        }
    }

    private fun scheduleRepaint() {
        repaintHandler.removeCallbacks(repaintRunnable)
        repaintHandler.postDelayed(repaintRunnable, REPAINT_SETTLE_MS)
    }

    private fun forceRepaint() {
        if (!isInForeground || !loaded) return
        val wv = webView ?: return
        wv.invalidate()
        wv.evaluateJavascript(REPAINT_JS, null)
    }

    override fun onPause() {
        super.onPause()
        // Copre anche lo schermo spento: da qui in poi i messaggi proattivi
        // possono squillare come notifica di sistema.
        isInForeground = false
        isChatOnScreen = false
        notebookChatOnScreen = null
        // ...e la mascotte flottante può tornare a schermo: l'app non è più
        // davanti, quindi non c'è più il rischio di vederne due.
        FloatingOverlayController.onAppForegroundChanged()
        repaintHandler.removeCallbacks(repaintRunnable)
        // Stop WebView JS/animation processing while backgrounded; the
        // gateway keeps running independently in GatewayService.
        webView?.onPause()
    }

    override fun onResume() {
        super.onResume()
        isInForeground = true
        // Il tasto Home con la casa davanti passa di qui, e Chromium ne esce con
        // la tastiera slegata dal campo che ha ancora il fuoco: v. ImeBinding.
        imeBinding.recheck()
        // La mascotte flottante si toglie di mezzo: questa app è la home del
        // telefono, e sulla schermata iniziale la mascotte c'è già dentro la
        // SPA. Due Jenny sarebbero una di troppo.
        FloatingOverlayController.onAppForegroundChanged()
        webView?.onResume()
        scheduleRepaint()
        // Terzo modo in cui la chat arriva a schermo: il rientro in primo piano
        // con la chat GIÀ attiva. Non passa da nessun cambio vista, quindi
        // ChatController.activate() non viene chiamato e la SPA non ha niente da
        // notificare — questo ramo è l'unico che lo copre.
        //
        // La domanda è ciò che lo rende diverso dall'onResume che c'era prima:
        // non si cancella perché siamo tornati in primo piano (in un launcher
        // vuol dire "ha premuto Home", su qualunque vista), si cancella perché
        // la chat è quello che l'utente ha davanti. Asincrono per forza — la
        // risposta arriva dal thread JS — e va bene: un alert cancellato una
        // frazione di secondo dopo il resume è indistinguibile.
        // La stessa risposta riaccende isChatOnScreen, che onPause ha spento:
        // la SPA lo dice solo ai cambi, e rientrando non cambia niente.
        webView?.evaluateJavascript(CHAT_ON_SCREEN_JS) { result ->
            // Il risultato è la stringa JS in JSON (`"personal"`): la si apre
            // con lo stesso parser, non togliendo le virgolette a mano.
            val key = result?.let { runCatching { JSONArray("[$it]").optString(0) }.getOrNull() }
            val onChat = key == "personal"
            val notebook = asConversation(key)
            isChatOnScreen = onChat
            notebookChatOnScreen = notebook
            if (onChat || notebook != null) NotifierBridge.clearAlerts(this, "resume-on-chat", notebook)
        }
        // Pacchetti installati/disinstallati mentre eravamo dietro (tipicamente
        // l'uninstaller di sistema): ora la SPA può aggiornare la griglia.
        flushPackageNotices()
        // Dopo onNewIntent, che arriva prima di onResume: il Home del rientro
        // ha già visto il flag acceso (v. onNewIntent).
        cameBackFromBackground = false
    }

    private fun startGatewayAndLoad() {
        startGatewayService()
        waitForGatewayThenLoad()
    }

    /** Poll the gateway socket off the main thread, then load the WebView.
     *  The gateway boots on a background thread inside GatewayService, so
     *  loading immediately would race it; the WebView's own error/retry path
     *  stays as the second line of defense once the first load is issued. */
    private fun waitForGatewayThenLoad() {
        Thread {
            val deadline = System.currentTimeMillis() + BOOT_POLL_TIMEOUT_MS
            var ready = false
            while (System.currentTimeMillis() < deadline) {
                try {
                    java.net.Socket().use { socket ->
                        socket.connect(
                            java.net.InetSocketAddress(GATEWAY_HOST, GATEWAY_PORT),
                            BOOT_POLL_INTERVAL_MS.toInt()
                        )
                    }
                    ready = true
                    break
                } catch (_: java.io.IOException) {
                    try {
                        Thread.sleep(BOOT_POLL_INTERVAL_MS)
                    } catch (_: InterruptedException) {
                        return@Thread
                    }
                }
            }
            // Off the main thread: file I/O here is a single small config.json
            // read, done once at startup while we're already blocked polling.
            if (ready) {
                resolvedGatewayUrl = buildGatewayUrl()
                latchSystemUpdate()
            }
            runOnUiThread {
                if (isFinishing || isDestroyed) return@runOnUiThread
                if (ready) {
                    loadWebView()
                } else {
                    Log.e(TAG, "Gateway socket not listening after ${BOOT_POLL_TIMEOUT_MS}ms")
                    showError()
                }
            }
        }.start()
    }

    /* Da qui un avvio rifiutato non risale più: `GatewayStarter` lo scrive nel
       log e l'attesa del socket finisce in `showError()`, che è quel che
       l'utente deve vedere invece di un'app che si chiude. */
    private fun startGatewayService() {
        GatewayStarter.ensureUp(this, reason = "activity")
    }

    /**
     * Build the WebView's initial URL, appending the gateway's bootstrap
     * secret as a URL *fragment* (never a query param): fragments are never
     * sent over the wire by the browser/WebView, so the secret only ever
     * reaches this page's own JS (via `location.hash`), not the HTTP request
     * line, any server access log, or a cross-origin request.
     */
    private fun buildGatewayUrl(): String {
        // Dopo un riavvio da ripristino si riparte in chat, non sull'ultima
        // vista salvata. Il flag viene scritto da restartApp() con commit()
        // sincrono: il localStorage della WebView non sopravvive al kill
        // (persistenza asincrona di Chromium), le SharedPreferences sì.
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val bootToChat = prefs.getBoolean(PREF_BOOT_TO_CHAT, false)
        if (bootToChat) prefs.edit().putBoolean(PREF_BOOT_TO_CHAT, false).apply()
        // `openChatOnLoad` è l'altra sorgente: il tap su un alert arrivato con
        // l'activity morta (v. onCreate). Scritto sul main thread prima che
        // parta il thread di polling che chiama questo metodo.
        val chat = openChatConversationOnLoad?.let { "&chat=${Uri.encode(it)}" }.orEmpty()
        val base = when {
            openChatOnLoad -> "$GATEWAY_URL?mode=chat$chat"
            bootToChat -> "$GATEWAY_URL?mode=chat"
            else -> GATEWAY_URL
        }
        val secret = readBootstrapSecret() ?: return base
        return "$base#bs=${Uri.encode(secret)}"
    }

    /**
     * Read the per-install gateway bootstrap secret directly from
     * `<filesDir>/workspace/config.json` — the same file
     * `jenny.config.bootstrap.ensure_minimal_config` writes
     * `websocket.token_issue_secret` into. Only this app's Android UID can
     * read this file, which is what lets the WebView prove to
     * `/webui/bootstrap` that it is this app and not some other app on the
     * device hitting the same loopback port.
     *
     * The returned value must never be logged; only failure to read it is.
     */
    private fun readBootstrapSecret(): String? {
        return try {
            val configFile = File(filesDir, "workspace/config.json")
            if (!configFile.isFile) return null
            val websocket = JSONObject(configFile.readText()).optJSONObject("websocket")
                ?: return null
            val secret = websocket.optString("token_issue_secret", "")
                .ifEmpty { websocket.optString("tokenIssueSecret", "") }
            secret.ifEmpty { null }
        } catch (e: Exception) {
            Log.w(TAG, "Could not read gateway bootstrap secret (${e.javaClass.simpleName})")
            null
        }
    }

    private fun loadWebView() {
        val wv = webView ?: return
        wv.settings.javaScriptEnabled = true
        wv.settings.domStorageEnabled = true
        wv.settings.setGeolocationEnabled(false)
        // La UI è un launcher: niente zoom. setSupportZoom(false) blocca anche
        // l'opzione accessibilità "Forza attivazione zoom" che scavalca il
        // meta viewport user-scalable=no.
        wv.settings.setSupportZoom(false)
        wv.settings.builtInZoomControls = false
        // NON rimettere `wv.settings.textZoom = 100`. Il commento qui sopra
        // giustifica solo il pinch-zoom: textZoom è un'altra cosa, è la
        // dimensione carattere di sistema, che la WebView eredita apposta.
        // Fissarla a 100 la annullava — e con il pinch disattivato e nessun
        // controllo di dimensione nella WebUI, sul Titan 2 restava zero
        // accomodazione per chi ha vista ridotta. Perché l'aggiornamento
        // arrivi senza ricaricare serve `fontScale` nei configChanges
        // dell'activity (AndroidManifest).
        // La dichiarazione di intenti, per chi legge il codice e per gli strumenti
        // di sistema. Quel che *impedisce* davvero l'autofill sta in
        // NoAutofillWebView, che non consegna nulla da compilare: questo flag da
        // solo non basta, e il commento la sopra spiega perche.
        wv.importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS
        wv.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView?,
                filePathCallback: ValueCallback<Array<Uri>>?,
                fileChooserParams: FileChooserParams?
            ): Boolean {
                // Un callback lasciato pendente da una richiesta precedente va
                // chiuso con null prima di rimpiazzarlo (contratto Android).
                filePickerCallback?.onReceiveValue(null)
                filePickerCallback = filePathCallback
                pendingCameraUri = null
                return try {
                    launchFileChooser()
                    true
                } catch (e: Exception) {
                    Log.e(TAG, "Could not launch file chooser (${e.javaClass.simpleName})")
                    filePickerCallback = null
                    pendingCameraUri = null
                    false
                }
            }
        }
        // Il ponte verso il nativo: due porte con due regole, v.
        // installNativeBridges(). NON è vero che «la WebView carica solo il
        // gateway fidato»: il documento principale sì, ma dentro ci sono le
        // cornici delle Jenny App e la vista esterna servita dal server
        // dell'utente, e un oggetto di addJavascriptInterface arriva a tutte.
        installNativeBridges(wv)
        // L'inset di gesture in fondo, che il CSS non può leggere da sé: v.
        // bottomGestureInsetPx e JennyNativeInfo.getBottomGestureInset().
        observeGestureInsets(wv)

        wv.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView?,
                request: WebResourceRequest?
            ): Boolean {
                val uri = request?.url ?: return false
                // Solo le navigazioni di main frame (il tap su un link lo è
                // sempre) vengono deviate: eventuali sub-frame restano gestiti
                // di default.
                if (request.isForMainFrame != true) return false
                // Le navigazioni verso il gateway locale fidato restano nella
                // WebView (è la SPA stessa). Tutto il resto — link esterni della
                // chat — viene aperto fuori, altrimenti sostituirebbe la SPA
                // senza via di ritorno (nessun back in-app → kill dell'app).
                if (isInternalGatewayUrl(uri)) return false
                // Stessa origine ma un altro path (`/api/…`, un href relativo
                // risolto sotto `/html-mobile/`): non è la SPA e non va aperto
                // né qui né fuori — una Custom Tab sul gateway lo esporrebbe a
                // un altro processo. Si blocca e basta; il lato JS ha già
                // annullato il click, questo è la rete di sicurezza.
                if (isGatewayOrigin(uri)) {
                    Log.w(TAG, "Blocked main-frame navigation to a non-SPA gateway path")
                    return true
                }
                openExternalUrl(uri)
                return true
            }

            /**
             * Il renderer della WebView è morto (Android l'ha ucciso per
             * memoria mentre l'utente stava in un'altra app pesante, o è
             * caduto). Senza questo override la WebView fa cadere l'intero
             * processo — gateway compreso — e si ripartiva da zero, in chat.
             * Qui si ricostruisce solo l'activity: la WebView morta se ne va
             * con onDestroy (che la toglie e la distrugge, come vuole la
             * documentazione), quella nuova ricarica la casa, e la casa
             * riprende il posto che aveva salvato (`home-app.js`
             * `_restorePlace`; giro UX del 10/10/2026).
             */
            override fun onRenderProcessGone(view: WebView?, detail: RenderProcessGoneDetail?): Boolean {
                Log.w(TAG, "WebView renderer gone (crashed=${detail?.didCrash()}): rebuilding the activity")
                if (!isFinishing && !isDestroyed) recreate()
                return true
            }

            override fun onPageStarted(view: WebView?, url: String?, favicon: android.graphics.Bitmap?) {
                super.onPageStarted(view, url, favicon)
                mainFrameError = false
                // Un documento nuovo non ha ancora detto cosa mostra.
                isChatOnScreen = false
                notebookChatOnScreen = null
                if (!loaded) showLoading()
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                if (mainFrameError) return
                if (loaded) return
                loaded = true
                view?.visibility = View.VISIBLE
                // Da qui in poi c'è una SPA a cui consegnare il tasto Indietro.
                backCallback?.isEnabled = true
                hideLoading()
                // La SPA c'è: da qui in poi un cambio di inset la si può
                // avvisare. Il primo valore glielo dà comunque il getter.
                refreshGestureInsets()
                imeBinding.onPageShown()
            }

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?
            ) {
                val isMain = request?.isForMainFrame == true
                if (error != null) {
                    Log.e(TAG, "WebView error: ${error.description} (${error.errorCode}) mainFrame=$isMain")
                }
                if (isMain) {
                    mainFrameError = true
                    if (!loaded) scheduleRetry()
                } else if (error != null) {
                    reportSubframeError(view, request, error)
                }
            }
        }

        Log.i(TAG, "Waiting for gateway on $GATEWAY_URL ...")
        wv.loadUrl(resolvedGatewayUrl)
    }

    /**
     * Consegna alla SPA il fallimento di un sub-frame, che altrimenti non lo
     * saprebbe mai.
     *
     * **Perché serve.** Un iframe che non carica non emette niente di
     * osservabile dal JS della pagina che lo contiene: cross-origin, `onerror`
     * non scatta e `contentDocument` è inaccessibile. Il risultato misurato è un
     * riquadro bianco e zero informazione — per *qualunque* causa: 404, script
     * rotto, o (il caso che ha portato qui) `ERR_CLEARTEXT_NOT_PERMITTED` su una
     * Jenny App che incorniciava un `http://` non-loopback. L'errore esisteva
     * solo in logcat, che l'utente non legge e l'agente non può leggere: sei
     * occorrenze in un'ora senza che niente arrivasse a nessuno.
     *
     * Il canale è lo stesso già usato per `jenny-gesture-insets`
     * (v. refreshGestureInsets): un CustomEvent sulla window della SPA. Il
     * payload passa da [JSONObject] e non da concatenazione di stringhe —
     * l'URL arriva dalla rete e finirebbe dentro codice JS valutato.
     */
    private fun reportSubframeError(
        view: WebView?,
        request: WebResourceRequest?,
        error: WebResourceError
    ) {
        val wv = view ?: webView ?: return
        if (!loaded) return  // la SPA non c'è ancora: non c'è nessuno in ascolto
        val detail = JSONObject().apply {
            put("url", request?.url?.toString() ?: "")
            put("host", request?.url?.host ?: "")
            put("description", error.description?.toString() ?: "")
            put("errorCode", error.errorCode)
        }
        wv.evaluateJavascript(
            "window.dispatchEvent(new CustomEvent('jenny-subframe-error'," +
                "{detail:$detail}))",
            null
        )
    }

    /**
     * True se l'URI punta all'origine del gateway locale: host di loopback sulla
     * porta del gateway. Necessario ma NON sufficiente perché una navigazione
     * resti nella WebView — quello lo decide isInternalGatewayUrl(), che guarda
     * anche il path.
     */
    private fun isGatewayOrigin(uri: Uri): Boolean {
        val scheme = uri.scheme?.lowercase()
        if (scheme != "http" && scheme != "https") return false
        val host = uri.host ?: return false
        val isLoopback = host == GATEWAY_HOST || host == "localhost"
        // La porta di default (-1) non è quella del gateway: consideriamo
        // interne solo le navigazioni esplicite verso GATEWAY_PORT.
        return isLoopback && uri.port == GATEWAY_PORT
    }

    /**
     * Il path è uno dei **documenti-guscio** della WebUI?
     *
     * Le interfacce sono due — la casa (`index.html`, cioè quel che
     * [GATEWAY_PATH] serve) e l'officina (`workshop.html`) — più il primo
     * avvio (`onboarding.html`), a cui rimandano entrambe finché non c'è un
     * provider. Fra l'uno e l'altro si passa con un caricamento di pagina.
     * Senza questo elenco
     * quella navigazione veniva bloccata dalla rete di sicurezza qui sotto e
     * le due porte non si aprivano: misurato sul Titan 2 il 18/09/2026, con
     * "Blocked main-frame navigation to a non-SPA gateway path" in logcat come
     * unica traccia.
     *
     * È un elenco chiuso e non un prefisso: `/html-mobile/qualunque-cosa`
     * resta bloccato, quindi `/api/…` e un href relativo risolto male non
     * passano. Allargarlo a tutto il path sotto il gateway sarebbe rinunciare
     * alla ragione per cui il blocco esiste.
     */
    private fun isShellDocument(path: String): Boolean {
        val base = GATEWAY_PATH.trimEnd('/')
        return path == GATEWAY_PATH ||
            path == base ||
            path == "$base/index.html" ||
            path == "$base/workshop.html" ||
            path == "$base/onboarding.html"
    }

    /**
     * True solo per le pagine della SPA: origine del gateway **e** path esatto
     * di un documento-guscio ([isShellDocument]).
     * Il prefisso non basterebbe — un href relativo scritto dal modello come
     * `[cerca](www.google.com)` risolve in `/html-mobile/www.google.com`, che un
     * confronto `startsWith` accetterebbe: la WebView ricaricherebbe il documento
     * senza il fragment `#bs=` (SPA de-autenticata) o, sotto `/api/`, lo
     * sostituirebbe con un 404 JSON, portandosi via `window.mobileApp` e con lui
     * il tasto Indietro. Query e fragment restano liberi (`?mode=chat#bs=…`).
     */
    private fun isInternalGatewayUrl(uri: Uri): Boolean {
        if (!isGatewayOrigin(uri)) return false
        val path = uri.path ?: return false
        return isShellDocument(path)
    }

    /**
     * Apre un URL esterno in una Chrome Custom Tab (browser in-app con
     * pulsante di chiusura). Se nessun browser gestisce le Custom Tab si
     * ripiega su ACTION_VIEW; se anche quello fallisce si logga soltanto.
     */
    private fun openExternalUrl(uri: Uri) {
        try {
            CustomTabsIntent.Builder()
                .setShowTitle(true)
                .build()
                .launchUrl(this, uri)
        } catch (e: Exception) {
            try {
                startActivity(Intent(Intent.ACTION_VIEW, uri))
            } catch (e2: Exception) {
                Log.w(TAG, "Could not open external URL (${e2.javaClass.simpleName})")
            }
        }
    }

    // ── Ponte nativo: due porte, due regole ──
    //
    // **Perché due.** `addJavascriptInterface` inietta l'oggetto in OGNI frame
    // della WebView, qualunque sia la sua origine (lo dice la documentazione di
    // WebView.addJavascriptInterface), e questa WebView ne ospita di non fidati:
    // le cornici delle Jenny App (`sandbox="allow-scripts"`, origine opaca) e la
    // vista esterna, cioè l'HTML del server dell'utente arrivato in chiaro dal
    // proxy su loopback. Finché il ponte era uno solo, una qualunque di quelle
    // pagine poteva chiamare `saveToDownloads('config.json')` — cioè copiare le
    // chiavi dei provider nella cartella Download condivisa — o `restartApp()`,
    // o leggere il conteggio d'uso del cassetto.
    //
    // - [NATIVE_INFO_JS] (`addJavascriptInterface`): SOLO letture innocue, senza
    //   effetti e senza dati personali. Resta sincrono perché la SPA ne ha
    //   bisogno durante la costruzione (inset di gesture, tastiera fisica) e
    //   dentro render sincroni (card batteria). Che lo veda anche un iframe non
    //   costa niente: è quel che un iframe saprebbe comunque, o quasi.
    // - [NATIVE_PORT_JS] (`WebViewCompat.addWebMessageListener`): tutto il resto.
    //   Chromium inietta l'oggetto solo nei frame la cui origine è
    //   [GATEWAY_ORIGIN] — un'origine opaca o un'altra porta non combaciano — e
    //   [NativeCommandListener] rifiuta in più ogni messaggio che non venga dal
    //   frame principale. È asincrono: le risposte tornano come messaggi.
    //
    // Il lato JS che ricompone le due porte in un solo `window.JennyNative` è
    // `assets/shared/native-bridge.js`: i chiamanti non sanno quale sia quale.
    //
    // **Regola**: un metodo nuovo che scrive, apre qualcosa, legge un file o un
    // dato dell'utente va in [NativeCommands.dispatch], mai su [JennyNativeInfo].

    // Un thread solo, e non il main: onPostMessage arriva sul thread UI, e i
    // comandi fanno I/O (commit delle preferenze, copia in Download). Uno solo
    // perché l'ordine conta — `setLauncherUsage` seguito da `getLauncherUsage`
    // deve rileggere il valore appena scritto, ed è quel che la migrazione in
    // `shared/launcher-usage-store.js` verifica.
    private val nativeExecutor: ExecutorService =
        Executors.newSingleThreadExecutor { r -> Thread(r, "jenny-native-commands") }

    // Una sola installazione per WebView: il pulsante Riprova richiama
    // loadWebView(), e un secondo addWebMessageListener con lo stesso nome
    // solleva.
    private var nativeBridgesInstalled = false

    private val nativeCommands = NativeCommands()

    // Il fuoco della pagina e la tastiera, tenuti d'accordo: v. ImeBinding.kt.
    // `webView` letto a ogni controllo, non catturato: onRenderProcessGone e
    // onDestroy lo azzerano.
    private val imeBinding by lazy {
        ImeBinding(view = { webView }, imm = getSystemService(InputMethodManager::class.java))
    }

    private fun installNativeBridges(wv: WebView) {
        if (nativeBridgesInstalled) return
        nativeBridgesInstalled = true
        wv.addJavascriptInterface(JennyNativeInfo(), NATIVE_INFO_JS)
        if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            WebViewCompat.addWebMessageListener(
                wv, NATIVE_PORT_JS, setOf(GATEWAY_ORIGIN), NativeCommandListener()
            )
        } else {
            // Chiuso, non aperto: su una WebView così vecchia (< M82) i comandi
            // non esistono e la SPA degrada come fuori dal guscio. Ripiegare su
            // addJavascriptInterface riaprirebbe esattamente il buco.
            Log.e(TAG, "WebView lacks WEB_MESSAGE_LISTENER: native commands are disabled")
        }
    }

    /**
     * Riceve i comandi della SPA. Chromium lo chiama solo per i frame di
     * [GATEWAY_ORIGIN]: è **questa** la barriera, e ferma le cornici delle Jenny
     * App (origine opaca) e la vista esterna (altra porta).
     *
     * Il controllo in più sul frame principale è difesa in profondità, non una
     * seconda barriera: nessuna cornice dentro la SPA ha motivo di parlare col
     * nativo, ma una della **stessa origine** non ne ha bisogno — raggiunge
     * `parent.JennyNativePort` e lo chiama da lì, e il messaggio arriva col
     * frame principale come mittente. Una pagina servita da [GATEWAY_ORIGIN] è
     * quindi fidata quanto la SPA, qualunque frame la contenga.
     *
     * Protocollo (JSON in una stringa): `{"m": metodo, "a": [argomenti]}`, più
     * `"id"` quando il chiamante aspetta una risposta, che torna come
     * `{"id", "ok", "r"}` sullo stesso canale.
     */
    private inner class NativeCommandListener : WebViewCompat.WebMessageListener {
        override fun onPostMessage(
            view: WebView,
            message: WebMessageCompat,
            sourceOrigin: Uri,
            isMainFrame: Boolean,
            replyProxy: JavaScriptReplyProxy
        ) {
            if (!isMainFrame || !isGatewayOrigin(sourceOrigin)) {
                Log.w(TAG, "Native command refused: not from the SPA's main frame")
                return
            }
            val request = try {
                JSONObject(message.data ?: return)
            } catch (e: Exception) {
                Log.w(TAG, "Native command refused: malformed message")
                return
            }
            val id = request.optInt("id", 0)
            val method = request.optString("m", "")
            val args = request.optJSONArray("a") ?: JSONArray()
            if (nativeExecutor.isShutdown) return  // activity distrutta: nessuno a cui rispondere
            nativeExecutor.execute {
                val reply = JSONObject().put("id", id)
                try {
                    reply.put("ok", true).put("r", nativeCommands.dispatch(method, args) ?: JSONObject.NULL)
                } catch (e: Exception) {
                    Log.w(TAG, "Native command $method failed (${e.javaClass.simpleName})")
                    reply.put("ok", false)
                }
                if (id <= 0) return@execute
                // Il proxy va usato dal thread UI, e solo finché la pagina che
                // ha chiesto è ancora lì.
                runOnUiThread {
                    if (isDestroyed) return@runOnUiThread
                    try {
                        replyProxy.postMessage(reply.toString())
                    } catch (e: Exception) {
                        Log.w(TAG, "Native reply to $method lost (${e.javaClass.simpleName})")
                    }
                }
            }
        }
    }

    /**
     * La porta sincrona, visibile a **ogni** frame: solo letture innocue.
     *
     * Ogni metodo qui è una domanda sul dispositivo o sulla geometria, senza
     * effetti e senza dati dell'utente — perché una cornice di Jenny App o la
     * pagina di un server qualunque può chiamarlo quanto la SPA (v. il commento
     * sopra [installNativeBridges]). Qualunque cosa scriva, apra o legga un
     * file sta in [NativeCommands].
     */
    inner class JennyNativeInfo {
        /**
         * Quanti px fisici del **fondo** della WebView cadono dentro la fascia
         * in cui la shell di sistema riconosce la gesture di home. Il cassetto ci tiene sopra la propria
         * lista: una passata verso l'alto partita lì dentro non scorrerebbe,
         * chiamerebbe `goHome()` — e siccome Jenny **è** il launcher, non
         * porterebbe via a un'altra app ma smonterebbe tutti gli overlay.
         *
         * Il gemello di questo metodo — escludere quella fascia con
         * `setGestureExclusion` — **non esiste, e di proposito**: v. il commento
         * sopra [NativeCommands.setGestureExclusion].
         *
         * Non tocca la WebView, quindi non serve saltare sul thread UI: legge un
         * campo `@Volatile` che il thread UI tiene aggiornato
         * (`refreshGestureInsets`). Un `runOnUiThread` qui non basterebbe
         * comunque — è asincrono, e questo metodo deve **restituire** un valore.
         *
         * Sotto API 29 il tipo di inset non esiste: la piattaforma torna 0 e il
         * cassetto si comporta come su un dispositivo senza zona di gesture,
         * che è esattamente il caso.
         */
        @JavascriptInterface
        fun getBottomGestureInset(): Int = bottomGestureInsetPx

        /**
         * C'è una tastiera fisica attaccata e aperta? Sul Titan 2 sì, sempre:
         * `qwerty` con `hardKeyboardHidden=NO`. La casa lo chiede per tenere il
         * fuoco sul campo dove scrivi (`home-focus.js`) — con la tastiera a
         * schermo, invece, il fuoco è una tastiera alzata sopra la chat.
         *
         * Letto a ogni chiamata: una tastiera Bluetooth si attacca e si stacca,
         * e `resources.configuration` è una lettura, non tocca la WebView.
         */
        @JavascriptInterface
        fun hasHardwareKeyboard(): Boolean {
            val config = resources.configuration
            return config.keyboard == Configuration.KEYBOARD_QWERTY &&
                config.hardKeyboardHidden == Configuration.HARDKEYBOARDHIDDEN_NO
        }

        /** True se Android concede la posizione, precisa o approssimativa: il
         *  bridge le accetta tutte e due. È quel che Mani mette accanto
         *  all'interruttore, che da solo dice solo la preferenza. */
        @JavascriptInterface
        fun hasLocationPermission(): Boolean = this@MainActivity.hasLocationPermission()

        /** True se l'app è già esente dall'ottimizzazione batteria (doze). */
        @JavascriptInterface
        fun isBatteryExempt(): Boolean {
            val pm = getSystemService(POWER_SERVICE) as android.os.PowerManager
            return pm.isIgnoringBatteryOptimizations(packageName)
        }

        /** True se il device ha cambiato build dall'ultimo avvio dell'app.
         *
         *  Gli aggiornamenti di sistema di Samsung e Xiaomi rimettono l'app
         *  fra quelle ottimizzate senza dirlo a nessuno: l'utente aveva già
         *  concesso l'esenzione e da un giorno all'altro cron e promemoria
         *  ricominciano a slittare. Non esiste un evento per accorgersene, ma
         *  Build.FINGERPRINT cambia a ogni OTA — confrontarla con quella
         *  dell'ultimo avvio è l'unico segnale disponibile lato app.
         *
         *  **Una lettura e basta.** Il confronto (che registra la fingerprint
         *  nuova) lo fa [latchSystemUpdate] prima di caricare la pagina: stava
         *  qui, e con il metodo visibile a ogni frame un iframe che lo chiamava
         *  per primo consumava la differenza — l'avviso non sarebbe più
         *  comparso al prossimo avvio. */
        @JavascriptInterface
        fun systemUpdatedSinceLastRun(): Boolean = systemUpdateLatch == true

        /** Il produttore del telefono, grezzo (`Build.MANUFACTURER`).
         *
         *  Alla WebUI serve per due cose: il nome da mostrare all'utente e lo
         *  slug di dontkillmyapp.com, che ricava minuscolando questa stringa.
         *  Vuota se Android non lo dichiara: là la UI degrada al link generico
         *  invece di costruire un indirizzo inventato. */
        @JavascriptInterface
        fun deviceManufacturer(): String = (Build.MANUFACTURER ?: "").trim()
    }

    /**
     * Confronta la fingerprint corrente con quella dell'ultimo avvio, una volta
     * per processo, e lo ricorda in [systemUpdateLatch].
     *
     * Al primissimo avvio non c'è nessun "prima" da confrontare: si registra la
     * fingerprint e si risponde false, altrimenti ogni installazione nuova
     * aprirebbe con un allarme falso. Gira sul thread di polling, prima che la
     * pagina esista: nessuno può chiederlo prima che sia deciso.
     */
    private fun latchSystemUpdate() = synchronized(this) {
        if (systemUpdateLatch != null) return@synchronized
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val seen = prefs.getString(PREF_LAST_FINGERPRINT, null)
        val current = Build.FINGERPRINT ?: ""
        val changed = seen != null && seen != current
        if (seen != current) {
            prefs.edit().putString(PREF_LAST_FINGERPRINT, current).apply()
        }
        if (changed) Log.i(TAG, "system update detected since last run")
        systemUpdateLatch = changed
    }

    /**
     * I comandi della SPA: tutto ciò che scrive, apre, legge un file o un dato
     * dell'utente. Raggiungibili solo da [NativeCommandListener], cioè dal frame
     * principale del gateway; nessun metodo qui porta `@JavascriptInterface`, ed
     * è questo — non una convenzione — che li tiene lontani dagli iframe.
     *
     * Girano su [nativeExecutor], non sul thread UI: chi tocca finestra o
     * WebView ci salta da sé con `runOnUiThread`, come faceva quando era il
     * thread JavaBridge a chiamarli.
     */
    inner class NativeCommands {
        /**
         * L'elenco chiuso dei comandi. Un nome che non è qui non esiste, e la
         * risposta a chi lo chiede è un errore. `shared/native-bridge.js` ne
         * tiene lo specchio (COMMANDS e QUERIES), e un test li confronta.
         */
        fun dispatch(method: String, a: JSONArray): Any? = when (method) {
            "setGestureExclusion" ->
                setGestureExclusion(a.getInt(0), a.getInt(1), a.getInt(2), a.getInt(3)).let { null }
            "getLauncherUsage" -> getLauncherUsage()
            "setLauncherUsage" -> setLauncherUsage(a.getString(0)).let { null }
            "setMascotSize" -> setMascotSize(a.getInt(0), a.getDouble(1)).let { null }
            "setFloatingPalette" -> setFloatingPalette(
                a.getString(0), a.getString(1), a.getString(2),
                a.getString(3), a.getString(4), a.getString(5)
            ).let { null }
            "setFloatingBubble" -> FloatingOverlayController.setBubbleStyle(a.getString(0)).let { null }
            "chatOpened" -> chatOpened(if (a.isNull(0)) null else a.optString(0)).let { null }
            "chatClosed" -> chatClosed().let { null }
            "setEditableFocus" -> imeBinding.onDomFocus(a.getString(0)).let { null }
            "setThemeBars" -> setThemeBars(a.getString(0), a.getString(1)).let { null }
            "exportBackup" -> exportBackup(a.getString(0), a.getString(1)).let { null }
            "importBackup" -> importBackup().let { null }
            "openFile" -> openFile(a.getString(0))
            "shareFile" -> shareFile(a.getString(0))
            "saveToDownloads" -> saveToDownloads(a.getString(0))
            "restartApp" -> restartApp().let { null }
            "requestBatteryExemption" -> requestBatteryExemption().let { null }
            "requestLocationPermission" -> requestLocationPermission().let { null }
            "requestExactAlarmPermission" -> requestExactAlarmPermission()
            "openBatterySettings" -> openBatterySettings()
            "extensionCall" -> extensionCall(a).let { null }
            else -> throw IllegalArgumentException("unknown native command")
        }

        /**
         * `extensionCall(nome, metodo, …argomenti)`: il comando di
         * un'estensione nativa (v. [NativeExtension]). Uno solo e generico,
         * così questo elenco chiuso non nomina quel che una build non ha.
         */
        private fun extensionCall(a: JSONArray) {
            val extension = nativeExtensions[a.getString(0)]
                ?: throw IllegalArgumentException("unknown native extension")
            val args = JSONArray()
            for (i in 2 until a.length()) args.put(a.get(i))
            extension.call(a.getString(1), args)
        }

        /**
         * Esclude un rettangolo dalle aree gesture di sistema della WebView.
         *
         * **Vale sui bordi verticali, e solo lì.** L'esclusione toglie alla
         * shell il *back* edge-swipe, che è la ragione per cui esiste: la
         * mascotte vive su un bordo laterale e ogni suo trascinamento veniva
         * letto come Indietro.
         *
         * **Sul bordo inferiore non si chiama, e non è una dimenticanza.** La
         * documentazione Android sulla navigazione a gesture è esplicita: da
         * home e quick-switch le app non possono chiamarsi fuori come fanno col
         * Back, e `systemGestureExclusionRects` su quella fascia non le
         * riguarda. Un rettangolo in fondo passerebbe senza errori e non
         * cambierebbe niente — cioè lascerebbe credere a chi legge il codice
         * che il problema sia risolto. Quello che si può fare davvero è
         * **starne fuori**, ed è ciò che fa il cassetto: v.
         * [JennyNativeInfo.getBottomGestureInset].
         */
        fun setGestureExclusion(left: Int, top: Int, right: Int, bottom: Int) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return
            runOnUiThread {
                val wv = webView ?: return@runOnUiThread
                val l = left.coerceAtLeast(0)
                val t = top.coerceAtLeast(0)
                val r = right.coerceAtMost(wv.width)
                val b = bottom.coerceAtMost(wv.height)
                wv.systemGestureExclusionRects =
                    if (r > l && b > t) listOf(android.graphics.Rect(l, t, r, b)) else emptyList()
            }
        }

        /**
         * Il conteggio d'uso del cassetto: letto e scritto **qui**, non nel
         * `localStorage` della WebView.
         *
         * Il dato è `{"<key>": [conteggio, ultimoMs]}` e lo produce
         * `shared/launcher-rank.js`; per il Kotlin è una stringa opaca, e va
         * tenuta tale — la forma la decide chi la sa leggere.
         *
         * **Perché si è spostato.** Stava in `localStorage`, e il commento di
         * [buildGatewayUrl] dice già perché era il posto sbagliato: la
         * persistenza di Chromium è asincrona e non sopravvive a un kill del
         * processo, mentre le SharedPreferences sì. Jenny è il launcher del
         * telefono e il sistema la uccide di routine, quindi l'ordine «più
         * usate» si sbriciolava da sé — un difetto silenzioso, perché un
         * cassetto in ordine sbagliato non sembra rotto, sembra solo inutile.
         *
         * Vuoto vuol dire «mai scritto»: chi legge ricostruisce da zero, che è
         * lo stesso degrado di prima (ordine alfabetico) e non un guasto.
         */
        fun getLauncherUsage(): String =
            try {
                getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                    .getString(PREF_LAUNCHER_USAGE, "") ?: ""
            } catch (e: Exception) {
                Log.w(TAG, "launcher usage unreadable (${e.javaClass.simpleName})")
                ""
            }

        /**
         * Salva il conteggio d'uso.
         *
         * `commit()` e non `apply()`, ed è tutto il punto dello spostamento:
         * questa riga si scrive nell'istante in cui stai **aprendo un'altra
         * app**, cioè esattamente quando Jenny passa in background e diventa
         * uccidibile. Un flush asincrono è la sola cosa su cui qui non si può
         * contare, e affidarcisi rifarebbe in Kotlin il difetto da cui si
         * scappava.
         *
         * Il costo è un'I/O sincrona, e la si può pagare: i comandi girano su
         * [nativeExecutor], non sul thread della UI.
         */
        fun setLauncherUsage(json: String) {
            try {
                getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                    .edit().putString(PREF_LAUNCHER_USAGE, json).commit()
            } catch (e: Exception) {
                // Un ordine che non si ricorda di questo avvio è meno grave di
                // un lancio fallito: la voce è già stata aperta quando
                // arriviamo qui. Stessa scelta di `UsageRanking._write`.
                Log.w(TAG, "launcher usage not saved (${e.javaClass.simpleName})")
            }
        }

        /**
         * La taglia della mascotte scelta in Impostazioni → Personalizzazione.
         *
         * *cssPx* è il lato del canvas quadrato (`MASCOT_SIZES` in
         * `shared/mascot.js`: 120/160/210), *dpr* il `devicePixelRatio` della
         * WebView. Il prodotto è il lato in px fisici, cioè **esattamente**
         * quanto la si vede grande in chat, ed è quello che la mascotte
         * flottante usa per sé: le due non possono divergere perché il numero
         * viene da un posto solo.
         *
         * Il controller la ricorda anche a finestra non montata, quindi questa
         * chiamata vale pure quando la mascotte flottante è spenta.
         */
        fun setMascotSize(cssPx: Int, dpr: Double) {
            val px = (cssPx * (if (dpr > 0.0) dpr else 1.0)).toInt()
            FloatingOverlayController.setMascotSize(px)
        }

        /**
         * I colori del tema attivo per la finestra flottante.
         *
         * Stessa idea della taglia: là dentro non c'è CSS, quindi la
         * tentazione è di scriverci dei colori — ed era esattamente quello che
         * c'era, sette costanti che erano la palette `chanel` per tutti e sette
         * i temi. La SPA spinge i token *calcolati* (`shared/theme.js`), e il
         * Kotlin non ne ha di propri da tenere allineati.
         *
         * I valori arrivano già in `#AARRGGBB`: `Color.parseColor` non legge la
         * forma `rgba(...)`, che è come tre temi su sette scrivono i bordi, e
         * la conversione si fa dove il valore è risolto.
         */
        fun setFloatingPalette(
            surface: String,
            border: String,
            text: String,
            hint: String,
            accent: String,
            onAccent: String,
        ) {
            FloatingOverlayController.setPalette(surface, border, text, hint, accent, onAccent)
            // Testo e accento servono anche alla schermata d'avvio (v.
            // applyBootPalette). Un valore illeggibile lascia quello di prima.
            val edit = getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
            for ((key, value) in listOf(
                PREF_BOOT_TEXT to text,
                PREF_BOOT_ACCENT to accent,
                PREF_BOOT_ON_ACCENT to onAccent,
            )) {
                try {
                    edit.putInt(key, Color.parseColor(value.trim()))
                } catch (e: IllegalArgumentException) {
                    Log.w(TAG, "setFloatingPalette: unparseable boot color for $key")
                }
            }
            edit.apply()
            runOnUiThread { applyBootPalette() }
        }

        /**
         * La chat è arrivata a schermo dentro la SPA: in officina da
         * ``ChatController.activate``, in casa quando la pagina della chat torna
         * a schermo. Secondo dei tre modi in cui la chat arriva a schermo (v.
         * ``NotifierBridge.clearAlerts``), e l'unico che il guscio nativo non
         * può vedere da sé: un cambio vista dentro la WebView non produce
         * nessun callback d'activity.
         *
         * ``NotificationManager`` è thread-safe, quindi non serve saltare sul
         * thread UI come fanno i metodi qui sopra — quelli toccano la WebView e
         * la finestra, questo no.
         *
         * Accende anche [isChatOnScreen]: da qui un avviso proattivo non
         * squilla più, il messaggio lo stai già vedendo.
         *
         * *conversation* è la chat arrivata a schermo: `null` per la personale,
         * `project:<nome>` per quella di un quaderno, che accende
         * [notebookChatOnScreen] e cancella solo gli alert di quel quaderno.
         */
        fun chatOpened(conversation: String?) {
            val notebook = asConversation(conversation)
            isChatOnScreen = notebook == null
            notebookChatOnScreen = notebook
            NotifierBridge.clearAlerts(this@MainActivity, "chat-view-opened", notebook)
        }

        /**
         * La chat personale è uscita di scena dentro la SPA: un'altra pagina,
         * una bacheca, il velo richiuso, un'app aperta sopra. Da qui gli avvisi
         * tornano a squillare anche con Jenny davanti (v. [isChatOnScreen]).
         */
        fun chatClosed() {
            isChatOnScreen = false
            notebookChatOnScreen = null
        }

        /**
         * Allinea le barre di sistema al tema attivo della WebUI: `background`
         * è il valore CSS di `--bg` (#rrggbb), `scheme` è "dark" o "light" e
         * decide il colore delle icone — su un tema chiaro quelle bianche di
         * default sparirebbero. Senza questo la status bar resta del colore
         * fisso di themes.xml, che stona con 6 temi su 7.
         *
         * I due valori si ricordano: al prossimo lancio vestono la schermata
         * d'avvio (v. applyBootPalette), che è anche chi li applica qui, e lo
         * sfondo sceglie lo splash di sistema (v. syncSplashTheme).
         */
        fun setThemeBars(background: String, scheme: String) {
            val color = try {
                Color.parseColor(background.trim())
            } catch (e: IllegalArgumentException) {
                Log.w(TAG, "setThemeBars: unparseable color, bars left as they are")
                return
            }
            getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
                .putInt(PREF_BOOT_BG, color)
                .putBoolean(PREF_BOOT_LIGHT, scheme == "light")
                .apply()
            runOnUiThread {
                applyBootPalette()
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    syncSplashTheme(color)
                }
            }
        }

        // ── Backup e ripristino ──

        /** Apre il picker SAF "salva con nome" per il backup già preparato dal
         *  gateway. Accetta solo file dentro backup_staging (anti-traversal). */
        fun exportBackup(stagedPath: String, suggestedName: String) {
            val stagingRoot = try {
                File(filesDir, "backup_staging").canonicalPath
            } catch (e: Exception) {
                Log.e(TAG, "exportBackup: cannot resolve staging dir (${e.javaClass.simpleName})")
                notifyBackupJs("onExportDone", false); return
            }
            val file = File(stagedPath)
            val canonical = try { file.canonicalPath } catch (e: Exception) { "" }
            if (!canonical.startsWith(stagingRoot + File.separator) || !file.isFile) {
                Log.w(TAG, "exportBackup: rejected path outside staging")
                notifyBackupJs("onExportDone", false); return
            }
            val safeName = if (Regex("^[A-Za-z0-9._-]{1,100}$").matches(suggestedName)) {
                suggestedName
            } else {
                "jenny-backup.jbk"
            }
            pendingExportPath = canonical
            val dropped = {
                pendingExportPath = null
                notifyBackupJs("onExportDone", false)
            }
            launchPicker("exportBackup", dropped) { exportBackupLauncher.launch(safeName) }
        }

        /** Apre il picker SAF di selezione file. Il .jbk non ha un MIME
         *  registrato, quindi il filtro resta aperto. */
        fun importBackup() {
            launchPicker("importBackup", { notifyBackupJs("onImportPicked", false) }) {
                importBackupLauncher.launch(arrayOf("*/*"))
            }
        }

        /** Lancia un picker SAF sul thread UI, ma solo se l'activity è viva.
         *
         *  I comandi già in coda su [nativeExecutor] girano anche dopo
         *  `onDestroy` (lo `shutdown` lascia finire la coda), e a quel punto
         *  i launcher di ActivityResult sono deregistrati: `launch` solleva
         *  `IllegalStateException` sul thread UI, cioè un crash dell'app. Il
         *  controllo sta **dentro** il blocco del thread UI: la distruzione
         *  avviene su quel thread in un solo messaggio, quindi nessun blocco
         *  accodato la può trovare a metà. Il `catch` copre il resto: un
         *  launcher deregistrato per un'altra via, o nessuna app che risponda
         *  al picker (`ActivityNotFoundException`, anche lei un crash). In
         *  ogni caso [onDropped] dice alla SPA che non se ne fa niente, così
         *  il suo flusso non resta ad aspettare. */
        private fun launchPicker(caller: String, onDropped: () -> Unit, launch: () -> Unit) {
            runOnUiThread {
                if (isFinishing || isDestroyed) {
                    Log.w(TAG, "$caller dropped: the activity is gone")
                    onDropped()
                    return@runOnUiThread
                }
                try {
                    launch()
                } catch (e: RuntimeException) {
                    // IllegalStateException o ActivityNotFoundException.
                    Log.w(TAG, "$caller dropped: picker unavailable (${e.javaClass.simpleName})")
                    onDropped()
                }
            }
        }

        /** Risolve un path (assoluto o relativo al workspace) in un file
         *  canonico dentro il **workspace**, per aprirlo, condividerlo o
         *  copiarlo in Download. Ritorna null se il path non è valido.
         *
         *  Il recinto era tutto `filesDir`: ci stanno anche la chiave privata
         *  SSH (`files/ssh/`, fuori dal workspace apposta), lo store degli
         *  snapshot e lo staging dei backup. Ora è il workspace, cioè quel che
         *  l'esploratore mostra e dove stanno gli allegati della chat
         *  (`uploads/`, `.jenny/media/`). Il path canonico risolve i symlink:
         *  un link nel workspace verso `../ssh/` finisce fuori e si rifiuta.
         *  Dentro il workspace si esclude [isWorkspaceSecret]. */
        private fun resolveLocalFile(path: String, caller: String): File? {
            val workspace = try {
                File(filesDir, "workspace").canonicalFile
            } catch (e: Exception) {
                Log.e(TAG, "$caller: cannot resolve the workspace (${e.javaClass.simpleName})")
                return null
            }
            val raw = if (path.startsWith("/")) File(path) else File(workspace, path)
            val canonical = try { raw.canonicalFile } catch (e: Exception) { return null }
            if (!canonical.path.startsWith(workspace.path + File.separator) || !canonical.isFile) {
                Log.w(TAG, "$caller: rejected path outside the workspace")
                return null
            }
            if (isWorkspaceSecret(canonical, workspace)) {
                Log.w(TAG, "$caller: rejected a file that holds secrets")
                return null
            }
            return canonical
        }

        /** `config.json` e i suoi compagni: chiavi dei provider, token di
         *  Telegram, password SSH. Stanno nella radice del workspace, e nessuna
         *  apertura legittima ne ha bisogno — si modificano da Impostazioni,
         *  non si passano a un'altra app.
         *
         *  I compagni sono due famiglie, e hanno le stesse chiavi del file:
         *  - `config.json.*`: la copia `.bak` e i temporanei della scrittura
         *    atomica (`config.json.<uuid>.tmp`);
         *  - `config.corrupt-<data>.json`: la copia in quarantena che il loader
         *    mette da parte quando il file non si legge (`_quarantine` in
         *    `jenny/config/loader.py`). Il nome non comincia con `config.json`,
         *    e il solo prefisso la lasciava aprire e condividere. */
        private fun isWorkspaceSecret(file: File, workspace: File): Boolean {
            if (file.parentFile != workspace) return false
            val name = file.name
            return name.startsWith("config.json") || name.startsWith("config.corrupt-")
        }

        private fun contentUriFor(file: File, caller: String): android.net.Uri? = try {
            androidx.core.content.FileProvider.getUriForFile(
                this@MainActivity, "$packageName.fileprovider", file)
        } catch (e: Exception) {
            Log.e(TAG, "$caller: FileProvider failed (${e.javaClass.simpleName})")
            null
        }

        private fun mimeTypeFor(file: File): String =
            android.webkit.MimeTypeMap.getSingleton()
                .getMimeTypeFromExtension(file.extension.lowercase())
                ?: "application/octet-stream"

        /** Apre un file locale col viewer di sistema (ACTION_VIEW via
         *  FileProvider). Accetta path assoluti o relativi al workspace.
         *  Ritorna false se il path non è valido/apribile. */
        fun openFile(path: String): Boolean {
            val canonical = resolveLocalFile(path, "openFile") ?: return false
            val uri = contentUriFor(canonical, "openFile") ?: return false
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, mimeTypeFor(canonical))
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            runOnUiThread {
                try {
                    startActivity(Intent.createChooser(intent, canonical.name))
                } catch (e: Exception) {
                    Log.e(TAG, "openFile: no viewer available (${e.javaClass.simpleName})")
                }
            }
            return true
        }

        /** Condivide un file locale con lo share sheet di sistema
         *  (ACTION_SEND via FileProvider). Stessa disciplina di openFile. */
        fun shareFile(path: String): Boolean {
            val canonical = resolveLocalFile(path, "shareFile") ?: return false
            val uri = contentUriFor(canonical, "shareFile") ?: return false
            val intent = Intent(Intent.ACTION_SEND).apply {
                type = mimeTypeFor(canonical)
                putExtra(Intent.EXTRA_STREAM, uri)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            runOnUiThread {
                try {
                    startActivity(Intent.createChooser(intent, canonical.name))
                } catch (e: Exception) {
                    Log.e(TAG, "shareFile: no share target available (${e.javaClass.simpleName})")
                }
            }
            return true
        }

        /** Copia un file locale nella cartella Download di sistema via
         *  MediaStore (stile Telegram: il file diventa visibile a file
         *  manager e altre app). Richiede API 29+ (il minSdk è 26: sotto,
         *  risponde `false`). Gira su [nativeExecutor], come ogni comando di
         *  [NativeCommands]: I/O fuori dal thread UI, e l'esito torna al JS
         *  come risposta della Promise, a copia finita. */
        fun saveToDownloads(path: String): Boolean {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
                Log.w(TAG, "saveToDownloads: unsupported below API 29")
                return false
            }
            val canonical = resolveLocalFile(path, "saveToDownloads") ?: return false
            val values = android.content.ContentValues().apply {
                put(android.provider.MediaStore.Downloads.DISPLAY_NAME, canonical.name)
                put(android.provider.MediaStore.Downloads.MIME_TYPE, mimeTypeFor(canonical))
                put(android.provider.MediaStore.Downloads.IS_PENDING, 1)
            }
            val resolver = contentResolver
            val item = try {
                resolver.insert(android.provider.MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            } catch (e: Exception) {
                Log.e(TAG, "saveToDownloads: insert failed (${e.javaClass.simpleName})")
                null
            } ?: return false
            return try {
                val out = resolver.openOutputStream(item)
                    ?: throw java.io.IOException("null output stream")
                out.use { o -> canonical.inputStream().use { it.copyTo(o) } }
                values.clear()
                values.put(android.provider.MediaStore.Downloads.IS_PENDING, 0)
                resolver.update(item, values, null, null)
                true
            } catch (e: Exception) {
                Log.e(TAG, "saveToDownloads: copy failed (${e.javaClass.simpleName})")
                try { resolver.delete(item, null, null) } catch (e2: Exception) { /* best effort */ }
                false
            }
        }

        /** Riavvio completo del processo per applicare un restore pendente.
         *  Python.start() non è ri-eseguibile in-process, quindi l'unica via
         *  pulita è: alarm one-shot che rilancia MainActivity + kill del
         *  processo. Un postDelayed non sopravviverebbe al kill; l'alarm sì.
         *  START_STICKY del GatewayService fa da seconda rete di sicurezza. */
        fun restartApp() {
            Log.i(TAG, "restartApp requested (pending restore)")
            // commit() sincrono (non apply): il processo muore tra ~650ms e la
            // scrittura DEVE essere già su disco. Vedi buildGatewayUrl().
            getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                .edit().putBoolean(PREF_BOOT_TO_CHAT, true).commit()
            // Niente CLEAR_TASK: essendo l'app la HOME, il sistema la rilancia
            // già da solo dopo il kill; l'alarm (inesatto, può arrivare secondi
            // dopo) è solo la rete di sicurezza. Con singleTask un'activity già
            // viva riceve onNewIntent invece di essere ricreata — CLEAR_TASK
            // invece la buttava giù ricaricando la WebView una seconda volta.
            val intent = Intent(this@MainActivity, MainActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            val pending = PendingIntent.getActivity(
                this@MainActivity, 0, intent,
                PendingIntent.FLAG_ONE_SHOT or PendingIntent.FLAG_IMMUTABLE or
                    PendingIntent.FLAG_CANCEL_CURRENT
            )
            val alarm = getSystemService(ALARM_SERVICE) as AlarmManager
            alarm.set(AlarmManager.RTC, System.currentTimeMillis() + 500, pending)
            runOnUiThread {
                stopService(Intent(this@MainActivity, GatewayService::class.java))
                // Piccolo delay: lascia completare la chiamata binder del bridge.
                Handler(Looper.getMainLooper()).postDelayed({
                    Process.killProcess(Process.myPid())
                }, 150)
            }
        }

        /** Chiede la posizione da un tocco (Mani → Posizione). Se Android non
         *  può più chiederla — negata per sempre — il callback del launcher se
         *  ne accorge dalla risposta immediata e apre la scheda dell'app. */
        fun requestLocationPermission() {
            runOnUiThread {
                if (hasLocationPermission()) return@runOnUiThread
                locationAskedFromUiAt = SystemClock.elapsedRealtime()
                launchLocationRequest()
            }
        }

        /** Apre la richiesta di esenzione batteria: senza, il doze differisce
         *  cron, promemoria e heartbeat e rallenta il long-poll Telegram. */
        fun requestBatteryExemption() {
            runOnUiThread {
                try {
                    val intent = Intent(
                        android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                        Uri.parse("package:$packageName")
                    )
                    startActivity(intent)
                } catch (e: Exception) {
                    Log.e(TAG, "Battery exemption request failed", e)
                }
            }
        }

        /** Apre la richiesta del permesso "sveglie precise" (Android 12+).
         *
         *  `SCHEDULE_EXACT_ALARM` è dichiarato nel manifest, ma per un'app che
         *  punta ad API 33 o più Android lo consegna **negato**: dichiararlo
         *  non lo concede. Senza, ogni sveglia degrada a inesatta e il sistema
         *  la accorpa alla finestra di risveglio di qualcun altro — misurato su
         *  un'installazione nuova: +9m il cron, +11m il watchdog, +1h la rete
         *  oraria; concesso a mano, tutte a finestra zero. È il vincolo che
         *  decide se il resto dell'anti-doze serve a qualcosa, e finora
         *  dall'app non c'era modo di rimediare.
         *
         *  Sotto API 31 il permesso non esiste — le sveglie sono già esatte per
         *  tutti — e nemmeno l'azione: là si risponde false invece di far
         *  saltare la WebView con una ActivityNotFoundException.
         *
         *  @return false quando la schermata non risulta raggiungibile: la UI
         *  allora lo dice, invece di lasciare il tap senza conseguenze. */
        fun requestExactAlarmPermission(): Boolean {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return false
            val intent = Intent(
                android.provider.Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
                Uri.parse("package:$packageName")
            )
            val reachable = canResolve(intent)
            runOnUiThread {
                try {
                    startActivity(intent)
                } catch (e: Exception) {
                    Log.e(TAG, "Exact alarm permission request failed", e)
                }
            }
            return reachable
        }

        /** Porta l'utente dove la restrizione si toglie davvero.
         *
         *  Le schermate dei gestori energetici OEM sono API private: non sono
         *  documentate, cambiano da una versione di ROM all'altra e su una
         *  build diversa semplicemente non esistono. Un component name morto
         *  fa `ActivityNotFoundException`, e un crash mentre segnaliamo un
         *  problema sarebbe peggio del problema stesso — quindi ogni tentativo
         *  vive nel suo try/catch e la catena finisce sempre sulla scheda
         *  dell'app nelle impostazioni di sistema, che c'è su ogni Android.
         *
         *  @return false quando nemmeno il ripiego di sistema risulta
         *  raggiungibile: la UI allora si limita al link con le istruzioni. */
        fun openBatterySettings(): Boolean {
            val candidates = batterySettingsCandidates()
            val reachable = candidates.any { canResolve(it) }
            runOnUiThread { startFirstWorking(candidates) }
            return reachable
        }
    }

    // ── Schermate di gestione batteria dei produttori ──

    /**
     * Ordine di preferenza dei tentativi per "portami dove si sblocca l'app":
     * prima le schermate del produttore, dove sta l'interruttore che conta
     * davvero (Samsung "app inattive", MIUI avvio automatico, PowerGenie di
     * Huawei…), poi la scheda dell'app nelle impostazioni di sistema.
     *
     * I component name vengono dalla lista pubblica di dontkillmyapp.com e
     * dalle segnalazioni degli utenti: sono una preferenza, non una promessa —
     * nessuno di questi è garantito su questa ROM, e `startFirstWorking` è
     * scritto aspettandosi che la maggior parte fallisca.
     *
     * Il match guarda `MANUFACTURER` e `BRAND` insieme perché i sotto-marchi
     * non compaiono sempre nello stesso campo: su un POCO il produttore è
     * "Xiaomi" e il brand "POCO", su un Redmi succede il contrario.
     */
    private fun batterySettingsCandidates(): List<Intent> {
        val vendor = "${Build.MANUFACTURER ?: ""} ${Build.BRAND ?: ""}".lowercase()
        fun made(vararg names: String) = names.any { vendor.contains(it) }
        val oem: List<Pair<String, String>> = when {
            made("samsung") -> listOf(
                "com.samsung.android.lool" to "com.samsung.android.sm.battery.ui.BatteryActivity",
                "com.samsung.android.lool" to "com.samsung.android.sm.ui.battery.BatteryActivity",
                "com.samsung.android.sm" to "com.samsung.android.sm.ui.battery.BatteryActivity",
            )
            made("xiaomi", "redmi", "poco") -> listOf(
                "com.miui.securitycenter" to "com.miui.powercenter.PowerSettings",
                "com.miui.securitycenter" to
                    "com.miui.permcenter.autostart.AutoStartManagementActivity",
            )
            made("huawei", "honor") -> listOf(
                "com.huawei.systemmanager" to
                    "com.huawei.systemmanager.optimize.process.ProtectActivity",
                "com.huawei.systemmanager" to
                    "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity",
                "com.huawei.systemmanager" to
                    "com.huawei.systemmanager.appcontrol.activity.StartupAppControlActivity",
            )
            made("oppo", "oneplus", "realme") -> listOf(
                "com.coloros.safecenter" to
                    "com.coloros.safecenter.permission.startup.StartupAppListActivity",
                "com.coloros.safecenter" to
                    "com.coloros.safecenter.startupapp.StartupAppListActivity",
                "com.oppo.safe" to "com.oppo.safe.permission.startup.StartupAppListActivity",
                "com.oneplus.security" to
                    "com.oneplus.security.chainlaunch.view.ChainLaunchAppListActivity",
            )
            made("vivo", "iqoo") -> listOf(
                "com.iqoo.secure" to "com.iqoo.secure.ui.phoneoptimize.BgStartUpManager",
                "com.vivo.permissionmanager" to
                    "com.vivo.permissionmanager.activity.BgStartUpManagerActivity",
                "com.iqoo.secure" to "com.iqoo.secure.safeguard.PurviewTabActivity",
            )
            else -> emptyList()
        }
        val fallback = Intent(
            android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            Uri.parse("package:$packageName")
        )
        return oem.map { Intent().setComponent(ComponentName(it.first, it.second)) } + fallback
    }

    /** True se il package manager sa dire chi apre questo Intent. */
    private fun canResolve(intent: Intent): Boolean = try {
        packageManager.resolveActivity(intent, 0) != null
    } catch (e: Exception) {
        false
    }

    /**
     * Prova i candidati in ordine e si ferma al primo che parte.
     *
     * Si tenta anche quello che `canResolve` dà per irraggiungibile: dal
     * package visibility di Android 11 la query può essere filtrata per un
     * package che l'Activity ce l'ha eccome, e lanciarla resta permesso.
     */
    private fun startFirstWorking(candidates: List<Intent>) {
        for (intent in candidates) {
            try {
                startActivity(intent)
                return
            } catch (e: Exception) {
                Log.w(
                    TAG,
                    "Battery settings screen unavailable: " +
                        "${intent.component ?: intent.action} (${e.javaClass.simpleName})"
                )
            }
        }
        Log.w(TAG, "No battery settings screen could be opened on this device")
    }

    // ── Backup: helper I/O (thread di background + callback JS) ──

    private fun notifyBackupJs(callback: String, ok: Boolean) {
        notifyJs("jennyBackup", callback, ok.toString())
    }

    /**
     * Chiama `window.<global>.<callback>(<argJs>)` nella pagina, se c'è.
     * [argJs] è già JavaScript: un booleano, o una stringa passata da
     * `JSONObject.quote`. Lo usano il backup e le estensioni native.
     */
    internal fun notifyJs(global: String, callback: String, argJs: String) {
        runOnUiThread {
            webView?.evaluateJavascript(
                "window.$global && window.$global.$callback && window.$global.$callback($argJs)",
                null
            )
        }
    }

    private fun copyExportToUri(src: File, uri: Uri) {
        Thread {
            val ok = try {
                contentResolver.openOutputStream(uri, "wt")?.use { out ->
                    src.inputStream().use { it.copyTo(out) }
                } != null
            } catch (e: Exception) {
                Log.e(TAG, "Backup export copy failed (${e.javaClass.simpleName})")
                false
            }
            notifyBackupJs("onExportDone", ok)
        }.start()
    }

    private fun copyImportFromUri(uri: Uri) {
        Thread {
            val ok = try {
                // Path fisso concordato col gateway (BackupManager.import_staged_path).
                val dest = File(filesDir, "backup_staging/import.jbk")
                dest.parentFile?.mkdirs()
                contentResolver.openInputStream(uri)?.use { input ->
                    dest.outputStream().use { input.copyTo(it) }
                } != null
            } catch (e: Exception) {
                Log.e(TAG, "Backup import copy failed (${e.javaClass.simpleName})")
                false
            }
            notifyBackupJs("onImportPicked", ok)
        }.start()
    }

    private fun showLoading() {
        // La SPA non è (più) a schermo: il tasto Indietro non ha nessuno a cui
        // essere consegnato. Non oscilla con onPageFinished: onPageStarted
        // chiama showLoading() solo `if (!loaded)` e onPageFinished esce subito
        // `if (loaded)`, quindi i due non si alternano sulla stessa pagina; a
        // rimettere `loaded = false` è solo il pulsante Riprova, che passa
        // proprio di qui.
        backCallback?.isEnabled = false
        errorView?.visibility = View.GONE
        loadingView?.apply {
            alpha = 1f
            visibility = View.VISIBLE
        }
    }

    private fun hideLoading() {
        val lv = loadingView ?: return
        if (lv.visibility != View.VISIBLE) return
        val fadeOut = ObjectAnimator.ofFloat(lv, "alpha", 1f, 0f)
        fadeOut.duration = 400
        fadeOut.addListener(object : Animator.AnimatorListener {
            override fun onAnimationStart(animator: Animator) {}
            override fun onAnimationEnd(animator: Animator) {
                lv.visibility = View.GONE
                // Overlay sparito e WebView visibile: sblocca le animazioni
                // d'ingresso della WebUI (es. la caduta della mini Jenny
                // nell'onboarding), che altrimenti scorrono dietro il loading.
                webView?.evaluateJavascript(
                    "window.mobileApp && window.mobileApp.onNativeReady && window.mobileApp.onNativeReady()",
                    null
                )
            }
            override fun onAnimationCancel(animator: Animator) {}
            override fun onAnimationRepeat(animator: Animator) {}
        })
        fadeOut.start()
    }

    /**
     * Recupero d'emergenza quando il tasto Indietro non trova la SPA: si
     * ricarica `resolvedGatewayUrl`, che porta con sé il fragment `#bs=` (il
     * segreto di bootstrap si legge una volta sola, al caricamento del modulo:
     * una ricarica senza fragment darebbe una SPA visibile ma de-autenticata).
     * Vale per qualunque modo di perdere il documento, non solo per il link
     * relativo che ha motivato il fix.
     */
    private fun recoverLostSpa() {
        val wv = webView ?: return
        // Solo durante il primo caricamento ci pensa già
        // startGatewayAndLoad()/scheduleRetry(): qui si ripartirebbe da capo.
        // Dopo il boot invece nessun altro recupera: onReceivedError chiama
        // scheduleRetry() soltanto `if (!loaded)`, e mainFrameError torna false
        // solo in onPageStarted — quindi una navigazione di main frame fallita a
        // caricamento avvenuto lascerebbe la pagina d'errore della WebView al
        // posto della SPA per sempre. Il loadUrl qui sotto passa da
        // onPageStarted e azzera mainFrameError.
        if (!loaded) return
        val now = SystemClock.elapsedRealtime()
        if (now - lastSpaRecoveryAt < SPA_RECOVERY_MIN_INTERVAL_MS) return
        lastSpaRecoveryAt = now
        Log.w(TAG, "Back press found no SPA in the WebView, reloading the gateway URL")
        wv.loadUrl(resolvedGatewayUrl)
    }

    private fun scheduleRetry() {
        retryCount++
        if (retryCount > MAX_RETRIES) {
            Log.e(TAG, "Gateway unreachable after $MAX_RETRIES retries")
            showError()
            return
        }
        Log.i(TAG, "Gateway not ready, retry $retryCount/$MAX_RETRIES in ${RETRY_DELAY_MS}ms")
        Handler(Looper.getMainLooper()).postDelayed({
            webView?.loadUrl(resolvedGatewayUrl)
        }, RETRY_DELAY_MS)
    }

    private fun showError() {
        // Schermata d'errore: l'unico comando è Riprova. Tenersi il tasto
        // Indietro qui vuol dire tenerselo e non farne niente, per sempre.
        backCallback?.isEnabled = false
        loadingView?.visibility = View.GONE
        webView?.visibility = View.GONE
        errorView?.apply {
            alpha = 0f
            visibility = View.VISIBLE
            animate().alpha(1f).setDuration(300).start()
        }
    }
}
