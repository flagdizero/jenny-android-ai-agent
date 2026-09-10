package com.flagdizero.jenny

import android.Manifest
import android.animation.Animator
import android.animation.ObjectAnimator
import android.app.Activity
import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.Color
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
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebChromeClient.FileChooserParams
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.browser.customtabs.CustomTabsIntent
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import java.io.File
import org.json.JSONObject

class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "Jenny"
        private const val GATEWAY_HOST = "127.0.0.1"
        // Da BuildConfig (app/build.gradle.kts): il build type `demo` la
        // cambia per non litigare sul bind con l'installazione vera. `val` e
        // non `const val`: un campo di BuildConfig non e' un'espressione
        // costante per Kotlin.
        private val GATEWAY_PORT = BuildConfig.GATEWAY_PORT
        // Il path — e SOLO quel path — che serve la SPA. Vedi isInternalGatewayUrl():
        // il confronto è per uguaglianza, non per prefisso.
        private const val GATEWAY_PATH = "/html-mobile/"
        private val GATEWAY_URL = "http://${GATEWAY_HOST}:${GATEWAY_PORT}${GATEWAY_PATH}"
        private const val RETRY_DELAY_MS = 500L
        private const val MAX_RETRIES = 30
        private const val PREFS_NAME = "jenny"
        private const val PREF_BOOT_TO_CHAT = "boot_to_chat"
        // Ultima Build.FINGERPRINT vista: cambia solo con un aggiornamento di
        // sistema, che su Samsung e Xiaomi rimette l'app fra quelle ottimizzate.
        private const val PREF_LAST_FINGERPRINT = "last_build_fingerprint"
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
         * dentro una mini-app, in Wiki, ovunque — e non in chat.
         */
        const val ACTION_OPEN_CHAT = "com.flagdizero.jenny.action.OPEN_CHAT"

        /**
         * Porta la WebUI in chat da un tap sull'alert. `goHome()` è l'unico
         * punto che smonta *tutti* i livelli sopra la vista (mini-app compresa,
         * col suo cleanup); lo `switchMode` dopo serve perché la vista "home"
         * è una preferenza e può non essere la chat — qui la chat non è una
         * preferenza, è dove sta il messaggio che l'utente ha appena toccato.
         * È no-op se la chat è già davanti.
         *
         * Ritorna un booleano vero: `false` se la SPA non c'è, e in quel caso
         * gli alert NON vengono cancellati — la chat non si è aperta.
         */
        // La SPA sa da sé cosa comporta "apri la chat" (smontare gli overlay,
        // collassare il sotto-stato delle sezioni, ripartire dalla radice):
        // comporlo qui da goHome + switchMode lasciava la entry di radice a
        // descrivere la vista home mentre a schermo c'era la chat.
        private const val OPEN_CHAT_JS = """
            (function () {
              var app = window.mobileApp;
              if (!app || typeof app.openChat !== 'function') return false;
              return app.openChat() !== false;
            })()
        """

        /**
         * La chat è la vista a schermo? È la domanda che separa questo
         * ``onResume`` da quello di una volta (v. ``NotifierBridge.clearAlerts``):
         * il ritorno in primo piano non dice niente su cosa l'utente stia
         * guardando, e in un launcher scatta a ogni pressione di Home.
         *
         * Senza SPA caricata il risultato è ``null``, che non è ``"true"``:
         * nessun alert viene cancellato, ed è la direzione d'errore giusta.
         */
        private const val CHAT_ON_SCREEN_JS = """
            (function () {
              var app = window.mobileApp;
              return !!(app && app.currentMode === 'chat');
            })()
        """

        // Finestra minima fra due recuperi della SPA: il ricaricamento non è
        // istantaneo e senza questo una raffica di pressioni lo farebbe ripartire
        // da capo ogni volta, senza mai arrivare in fondo.
        private const val SPA_RECOVERY_MIN_INTERVAL_MS = 3_000L

        // Letto da NotifierBridge (thread Python via Chaquopy) per sopprimere
        // gli alert quando l'utente sta già guardando la chat. @Volatile:
        // scritto dal main thread (onResume/onPause), letto da altri thread.
        @Volatile
        var isInForeground = false
            private set
    }

    private var retryCount = 0
    private var lastSpaRecoveryAt = 0L
    private var loaded = false
    private var mainFrameError = false
    private var loadingView: FrameLayout? = null
    private var errorView: FrameLayout? = null
    private var webView: WebView? = null
    // Il callback del tasto Indietro. Abilitato solo mentre la SPA è a schermo:
    // v. onCreate, onPageFinished, showLoading e showError.
    private var backCallback: OnBackPressedCallback? = null
    // Il tap su una notifica proattiva chiede la chat. Se l'activity era morta
    // la richiesta non passa da onNewIntent ma da onCreate, e allora deve
    // arrivare fino all'URL iniziale: la legge buildGatewayUrl(), che gira sul
    // thread di polling — scritta in onCreate prima che quel thread parta.
    @Volatile
    private var openChatOnLoad = false
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
    // l'avviso forte e le altre no. @Volatile: le chiamate del bridge
    // arrivano dal thread JavaBridge della WebView, non dal main.
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
    // negato, LocationBridge ritorna null e non viene iniettato nulla — nessuna
    // azione di recupero necessaria (l'utente può concederlo dalle impostazioni
    // Android in un secondo momento).
    private val locationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) {
                // Il FGS è già partito come specialUse (permesso non ancora
                // concesso all'avvio): ri-avviarlo lo fa ripartire con anche il
                // tipo `location`, necessario per l'app-op location a UI non in
                // primo piano.
                startGatewayService()
            } else {
                Log.w(TAG, "Location permission denied; device location stays unavailable")
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
        setContentView(R.layout.activity_main)

        loadingView = findViewById(R.id.loading_view)
        errorView = findViewById(R.id.error_view)
        webView = findViewById(R.id.webview)

        setupSystemBars()

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
        // buildGatewayUrl). La chat si aprirà: gli alert sono consumati.
        if (intent?.action == ACTION_OPEN_CHAT) {
            openChatOnLoad = true
            NotifierBridge.clearAlerts(this, "cold-start-alert-tap")
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
        super.onDestroy()
    }

    /**
     * Barre di sistema. Il contenuto rientra già da sé (il decor di AppCompat
     * consuma l'inset della status bar: la WebView parte sotto), quindi qui non
     * si tocca il layout — si allinea solo il *colore*. All'avvio valgono i
     * colori del tema Android (themes.xml); appena la SPA è pronta li riallinea
     * al tema attivo della WebUI via JennyGestureBridge.setThemeBars.
     */
    private fun setupSystemBars() {
        applyBarAppearance(light = false)
    }

    private fun applyBarAppearance(light: Boolean) {
        WindowInsetsControllerCompat(window, window.decorView).apply {
            isAppearanceLightStatusBars = light
            isAppearanceLightNavigationBars = light
        }
    }

    // ── Inset di gesture obbligatorio (D8 del piano .agent/apps-drawer-plan.md) ──
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

    private fun ensureLocationPermission() {
        val granted = ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED
        if (!granted) {
            locationPermissionLauncher.launch(Manifest.permission.ACCESS_FINE_LOCATION)
        }
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        setIntent(intent)
        // Tap su una notifica proattiva: si va in chat, chiudendo prima
        // qualunque livello sopra (la mini-app aperta per prima). Gli alert
        // pendenti si cancellano SOLO qui — cioè solo quando la chat viene
        // davvero aperta. Stavano in onResume, che in un launcher scatta a
        // ogni ritorno alla home: l'alert veniva cancellato comunque, fosse
        // stato letto o no, e il messaggio proattivo restava senza alcun
        // segnale.
        if (intent?.action == ACTION_OPEN_CHAT) {
            webView?.evaluateJavascript(OPEN_CHAT_JS) { result ->
                if (result?.trim() == "true") {
                    NotifierBridge.clearAlerts(this@MainActivity, "alert-tap")
                } else {
                    Log.w(TAG, "Alert tap could not reach the SPA; chat not opened")
                }
            }
            return
        }
        // This app is the device HOME launcher (see AndroidManifest: category
        // HOME + DEFAULT). Pressing the system Home button / doing the home
        // swipe gesture while we are already the foreground task re-delivers the
        // HOME intent here instead of a fresh onCreate (launchMode=singleTask).
        // A launcher's Home means "collapse back to the home screen": close any
        // open Jenny mini-app overlay and return the WebUI to chat (✿). It is a
        // no-op when already home. The gateway service is untouched.
        // Solo per il VERO intent Home (ACTION_MAIN + CATEGORY_HOME): anche
        // l'alarm di restartApp arriva qui via onNewIntent (intent esplicito,
        // senza categoria) e non deve simulare una pressione di Home.
        if (intent?.hasCategory(Intent.CATEGORY_HOME) == true) {
            webView?.evaluateJavascript("if (window.mobileApp) window.mobileApp.goHome()") {}
        }
    }

    override fun onPause() {
        super.onPause()
        // Copre anche lo schermo spento: da qui in poi i messaggi proattivi
        // possono squillare come notifica di sistema.
        isInForeground = false
        // Stop WebView JS/animation processing while backgrounded; the
        // gateway keeps running independently in GatewayService.
        webView?.onPause()
    }

    override fun onResume() {
        super.onResume()
        isInForeground = true
        webView?.onResume()
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
        webView?.evaluateJavascript(CHAT_ON_SCREEN_JS) { result ->
            if (result?.trim() == "true") NotifierBridge.clearAlerts(this, "resume-on-chat")
        }
        // Pacchetti installati/disinstallati mentre eravamo dietro (tipicamente
        // l'uninstaller di sistema): ora la SPA può aggiornare la griglia.
        flushPackageNotices()
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

    private fun startGatewayService() {
        ContextCompat.startForegroundService(this, Intent(this, GatewayService::class.java))
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
        val base = if (bootToChat || openChatOnLoad) "$GATEWAY_URL?mode=chat" else GATEWAY_URL
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
        // Bridge minimale per la mascotte Jenny: la WebUI riporta la sua area
        // sullo schermo così da escluderla dalle gesture di sistema (back
        // edge-swipe), altrimenti il drag di Jenny sul bordo triggera il back.
        // Sicuro: la WebView carica solo il gateway locale (127.0.0.1) fidato.
        wv.addJavascriptInterface(JennyGestureBridge(), "JennyNative")
        // L'inset di gesture in fondo, che il CSS non può leggere da sé: v.
        // bottomGestureInsetPx e JennyGestureBridge.getBottomGestureInset().
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

            override fun onPageStarted(view: WebView?, url: String?, favicon: android.graphics.Bitmap?) {
                super.onPageStarted(view, url, favicon)
                mainFrameError = false
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
     * True solo per la pagina della SPA: origine del gateway **e** path esatto.
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
        return path == GATEWAY_PATH || path == GATEWAY_PATH.trimEnd('/')
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

    /**
     * Esposto al JS come `JennyNative`. La WebUI passa il rettangolo di Jenny
     * (in px fisici, già moltiplicati per devicePixelRatio) e noi lo togliamo
     * dalle aree gesture di sistema della WebView. Su < Android Q l'API non
     * esiste: no-op. I metodi girano su un thread binder → post sull'UI thread.
     */
    inner class JennyGestureBridge {
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
         * [getBottomGestureInset].
         */
        @JavascriptInterface
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
         * La SPA è entrata in chat (``ChatController.activate``). Secondo dei tre
         * modi in cui la chat arriva a schermo, e l'unico che il guscio nativo
         * non può vedere da sé: un cambio vista dentro la WebView non produce
         * nessun callback d'activity.
         *
         * ``NotificationManager`` è thread-safe, quindi non serve saltare sul
         * thread UI come fanno i metodi qui sopra — quelli toccano la WebView e
         * la finestra, questo no.
         */
        @JavascriptInterface
        fun chatOpened() {
            NotifierBridge.clearAlerts(this@MainActivity, "chat-view-opened")
        }

        @JavascriptInterface
        fun clearGestureExclusion() {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return
            runOnUiThread { webView?.systemGestureExclusionRects = emptyList() }
        }

        /**
         * Quanti px fisici del **fondo** della WebView cadono dentro la fascia
         * in cui la shell di sistema riconosce la gesture di home (D8 del piano
         * `.agent/apps-drawer-plan.md`). Il cassetto ci tiene sopra la propria
         * lista: una passata verso l'alto partita lì dentro non scorrerebbe,
         * chiamerebbe `goHome()` — e siccome Jenny **è** il launcher, non
         * porterebbe via a un'altra app ma smonterebbe tutti gli overlay.
         *
         * Il gemello di questo metodo — escludere quella fascia con
         * `setGestureExclusion` — **non esiste, e di proposito**: v. il commento
         * sopra `setGestureExclusion`.
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
         * Allinea le barre di sistema al tema attivo della WebUI: `background`
         * è il valore CSS di `--bg` (#rrggbb), `scheme` è "dark" o "light" e
         * decide il colore delle icone — su un tema chiaro quelle bianche di
         * default sparirebbero. Senza questo la status bar resta del colore
         * fisso di themes.xml, che stona con 6 temi su 7.
         */
        @JavascriptInterface
        fun setThemeBars(background: String, scheme: String) {
            val color = try {
                Color.parseColor(background.trim())
            } catch (e: IllegalArgumentException) {
                Log.w(TAG, "setThemeBars: unparseable color, bars left as they are")
                return
            }
            val light = scheme == "light"
            runOnUiThread {
                window.statusBarColor = color
                window.navigationBarColor = color
                applyBarAppearance(light)
            }
        }

        // ── Backup e ripristino ──

        /** Apre il picker SAF "salva con nome" per il backup già preparato dal
         *  gateway. Accetta solo file dentro backup_staging (anti-traversal). */
        @JavascriptInterface
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
            runOnUiThread { exportBackupLauncher.launch(safeName) }
        }

        /** Apre il picker SAF di selezione file. Il .jbk non ha un MIME
         *  registrato, quindi il filtro resta aperto. */
        @JavascriptInterface
        fun importBackup() {
            runOnUiThread { importBackupLauncher.launch(arrayOf("*/*")) }
        }

        /** Risolve un path (assoluto o relativo al workspace) in un file
         *  canonico dentro filesDir (anti-traversal, stessa disciplina di
         *  exportBackup). Ritorna null se il path non è valido. */
        private fun resolveLocalFile(path: String, caller: String): File? {
            val filesRoot = try {
                filesDir.canonicalPath
            } catch (e: Exception) {
                Log.e(TAG, "$caller: cannot resolve filesDir (${e.javaClass.simpleName})")
                return null
            }
            val raw = if (path.startsWith("/")) File(path)
                      else File(File(filesDir, "workspace"), path)
            val canonical = try { raw.canonicalFile } catch (e: Exception) { return null }
            if (!canonical.path.startsWith(filesRoot + File.separator) || !canonical.isFile) {
                Log.w(TAG, "$caller: rejected path outside filesDir")
                return null
            }
            return canonical
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
        @JavascriptInterface
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
        @JavascriptInterface
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
         *  manager e altre app). Richiede API 29+; il runtime target
         *  (Titan 2, Android 11) la soddisfa. Sincrono sul thread binder
         *  del bridge: I/O fuori dall'UI thread, ritorno affidabile al JS. */
        @JavascriptInterface
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
        @JavascriptInterface
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
         *  Al primissimo avvio non c'è nessun "prima" da confrontare: si
         *  registra la fingerprint e si risponde false, altrimenti ogni
         *  installazione nuova aprirebbe con un allarme falso. */
        @JavascriptInterface
        fun systemUpdatedSinceLastRun(): Boolean = synchronized(this@MainActivity) {
            systemUpdateLatch ?: run {
                val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                val seen = prefs.getString(PREF_LAST_FINGERPRINT, null)
                val current = Build.FINGERPRINT ?: ""
                val changed = seen != null && seen != current
                if (seen != current) {
                    prefs.edit().putString(PREF_LAST_FINGERPRINT, current).apply()
                }
                if (changed) Log.i(TAG, "system update detected since last run")
                systemUpdateLatch = changed
                changed
            }
        }

        /** Apre la richiesta di esenzione batteria: senza, il doze differisce
         *  cron, promemoria e heartbeat e rallenta il long-poll Telegram. */
        @JavascriptInterface
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
        @JavascriptInterface
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

        /** Il produttore del telefono, grezzo (`Build.MANUFACTURER`).
         *
         *  Alla WebUI serve per due cose: il nome da mostrare all'utente e lo
         *  slug di dontkillmyapp.com, che ricava minuscolando questa stringa.
         *  Vuota se Android non lo dichiara: là la UI degrada al link generico
         *  invece di costruire un indirizzo inventato. */
        @JavascriptInterface
        fun deviceManufacturer(): String = (Build.MANUFACTURER ?: "").trim()

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
        @JavascriptInterface
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
        runOnUiThread {
            webView?.evaluateJavascript(
                "window.jennyBackup && window.jennyBackup.$callback && window.jennyBackup.$callback($ok)",
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
