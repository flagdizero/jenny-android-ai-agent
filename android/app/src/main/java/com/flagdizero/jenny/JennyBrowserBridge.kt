package com.flagdizero.jenny

import android.content.Context
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.webkit.JavascriptInterface
import android.webkit.RenderProcessGoneDetail
import android.webkit.ServiceWorkerClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.webkit.Profile
import androidx.webkit.ProfileStore
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import java.io.ByteArrayInputStream
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.Callable
import java.util.concurrent.CountDownLatch
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference

/**
 * WebView di sessione per i tool ``browser_*``: una pagina che **resta aperta**.
 *
 * Perché non riusa quella di [AgenticSearchBridge]: `fetchUrl` fa `loadUrl` sulla
 * stessa istanza, quindi una `web_fetch` durante una sessione porterebbe via la
 * pagina sotto i piedi. Ogni WebView in più costa (~+100 MB misurati sul Titan 2
 * il 29/08, interamente restituiti alla chiusura), ed esiste solo finché la sua
 * sessione è aperta.
 *
 * Un'istanza per sessione, e una sessione per agente (`browser.py`): Jenny e
 * ogni aiutante hanno la loro pagina. [slot] sceglie il profilo — `0` è quello
 * storico, `jenny-browser-session`, gli altri `jenny-browser-session-<slot>` —
 * così la chiusura di una sessione svuota i cookie suoi e non quelli di
 * un'altra aperta. Il costruttore a un argomento resta, e vale lo slot 0: un
 * chiamante che non conosce gli slot continua a funzionare.
 *
 * **Invariante che regge tutto: ogni tocco della WebView passa dal main thread**
 * ([handler], o [MainHop] per i salti che aspettano l'esito).
 * Creazione, `loadUrl`, letture di `url`/`title`, `evaluateJavascript`,
 * `destroy`, e le API del profilo (`ProfileStore` e `Profile` sono
 * `@UiThread`). Non è pignoleria: la WebView pretende il main thread e lo verifica
 * lei stessa (`checkThread`), quindi un accessore che legge lo stato dal thread
 * chiamante non dà un dato sbagliato, fa crashare l'app.
 *
 * Il [WebViewClient] è installato **una volta sola** alla creazione, non
 * riassegnato a ogni chiamata come in `AgenticSearchBridge.evaluateOnPage`: qui
 * deve sopravvivere alle navigazioni, perché è lui che le vede.
 */
class JennyBrowserBridge(context: Context, slot: Int) {

    constructor(context: Context) : this(context, 0)

    companion object {
        private const val TAG = "JennyBrowser"
        private const val DEFAULT_TIMEOUT_SECONDS = 30L
        private const val PROFILE_NAME = "jenny-browser-session"
        private const val SETTLE_QUIET_MS = 400L

        /**
         * Il cancello di [open] e di [evaluate]: chi tocca la pagina (il main
         * thread) e chi ci rinuncia (il chiamante, a tetto scaduto).
         * [GATE_FAILED] e' solo di [open]: il main l'aveva preso, ma `loadUrl`
         * ha sollevato e nessuna pagina e' partita.
         */
        private const val GATE_OPEN = 0
        private const val GATE_LOADING = 1
        private const val GATE_ABANDONED = 2
        private const val GATE_FAILED = 3

        /**
         * Tetto della cache dei verdetti per host. I nomi li sceglie la pagina
         * (`JennyBrowserGuard.blocked` è visibile a ogni frame): senza tetto,
         * una pagina che chiede nomi casuali in un ciclo la fa crescere fino
         * alla fine della sessione. Oltre il tetto esce il meno usato di
         * recente: un verdetto perso si ricalcola, non si sbaglia.
         */
        private const val MAX_HOST_VERDICTS = 256

        /**
         * Quanto il thread JavaBridge aspetta un DNS per la guardia lato
         * pagina. Il costruttore di `WebSocket` è sincrono, quindi la pagina
         * resta ferma finché il nativo non risponde; oltre il tetto si
         * risponde «bloccato» (nel dubbio si blocca) e la risoluzione finisce
         * da sola in cache per la volta dopo.
         */
        private const val GUARD_DNS_TIMEOUT_MS = 2_000L

        /** Un avviso nel log al massimo ogni tanto, per le voci che una pagina
         *  può far ripetere a piacere (v. [warnThrottled]). */
        private const val WARN_INTERVAL_MS = 10_000L

        /**
         * Suffissi pubblici a due livelli, per non ridurre `amazon.co.uk` a
         * `co.uk` — che aprirebbe il perimetro a **tutto** il Regno Unito.
         * E' una lista corta e dichiaratamente parziale: la Public Suffix List
         * completa e' migliaia di voci e non vale il peso qui. Un suffisso che
         * manca rende il perimetro piu' largo, mai piu' stretto, quindi il modo
         * di sbagliare e' permettere troppo e non bloccare a torto.
         */
        private val TWO_LEVEL_SUFFIXES = setOf(
            "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "net.uk",
            "com.au", "net.au", "org.au", "co.jp", "ne.jp", "or.jp",
            "com.br", "com.mx", "com.ar", "com.tr", "com.cn", "com.tw",
            "co.in", "co.nz", "co.za", "co.kr", "com.sg", "com.hk",
        )

        /**
         * Le stesse reti di ``jenny/security/network.py::_BLOCKED_NETWORKS``.
         *
         * Vivono qui e non solo in Python perché con una sessione interattiva
         * **Python l'indirizzo di un link non lo vede mai**: il modello clicca,
         * Chromium naviga. Questo è l'unico strato che vede dove porta un click,
         * un redirect o una sottorisorsa.
         */
        private val BLOCKED_V4 = listOf(
            "0.0.0.0" to 8, "10.0.0.0" to 8, "100.64.0.0" to 10, "127.0.0.0" to 8,
            "169.254.0.0" to 16, "172.16.0.0" to 12, "192.168.0.0" to 16,
            // Il broadcast: non e' mai un server (`_NEVER_A_SERVER` in Python).
            // Il multicast (224.0.0.0/4, ff00::/8) lo dice `isMulticastAddress`.
            "255.255.255.255" to 32,
        )

        /**
         * I profili per cui questo processo ha già tentato di buttare la copia
         * lasciata su disco, uno per slot. Una volta sola per nome perché dopo
         * il primo `getOrCreateProfile` il profilo è «caricato in memoria» e
         * `deleteProfile` solleva sempre, fino alla morte del processo. Solo
         * main thread, quindi senza sincronizzazione.
         */
        private val leftoverProfilesHandled = HashSet<String>()
    }

    private val handler = Handler(Looper.getMainLooper())
    private val profileName = if (slot == 0) PROFILE_NAME else "$PROFILE_NAME-$slot"
    private val appContext = context.applicationContext
    private var webView: WebView? = null

    // Il profilo separato agganciato alla WebView di questa sessione, o `null`
    // se non c'e' (WebView senza MULTI_PROFILE, aggancio fallito, sessione
    // chiusa). Scritto solo dal main thread; @Volatile perche' `isIsolated`
    // lo legge dal thread di Python.
    @Volatile private var profile: Profile? = null

    private val generation = AtomicInteger(0)
    private val loading = AtomicBoolean(false)
    private val lastError = AtomicReference<String?>(null)
    private val lastFinishAt = AtomicReference(0L)
    // LRU con tetto (v. MAX_HOST_VERDICTS); letta e scritta da più thread
    // (IO di Chromium, JavaBridge, Python), sempre sotto il suo monitor.
    private val hostVerdicts = object : LinkedHashMap<String, Boolean>(64, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Boolean>?) =
            size > MAX_HOST_VERDICTS
    }

    // I DNS chiesti dalla guardia lato pagina, fuori dal thread JavaBridge (v.
    // GUARD_DNS_TIMEOUT_MS). Due thread e una coda corta: una pagina che
    // chiede mille nomi non crea mille thread, e una coda piena vuol dire
    // «bloccato».
    private val guardDns = ThreadPoolExecutor(
        2, 2, 30L, TimeUnit.SECONDS, ArrayBlockingQueue(32),
    ) { r -> Thread(r, "jenny-browser-guard-dns").apply { isDaemon = true } }
        .apply { allowCoreThreadTimeOut(true) }

    private val lastWarnAt = AtomicLong(0L)
    private val suppressedWarnings = AtomicInteger(0)

    // L'ultimo indirizzo rifiutato dalla guardia. Serve a **dirlo**: un blocco
    // muto lascia il modello davanti a un about:blank vuoto, che sembra un sito
    // rotto e invita a riprovare. Misurato sul telefono il 29/08 con un redirect
    // di httpbin verso 192.168.1.1: fermato correttamente, e raccontato come
    // "0 elementi".
    private val lastBlocked = AtomicReference<String?>(null)

    // Il dominio su cui la sessione e' stata aperta. Una navigazione che ne esce
    // viene fermata: e' la difesa piu' forte contro una pagina che prova a
    // portare la sessione altrove, ed e' anche l'unica che vede un click,
    // perche' l'indirizzo di destinazione Python non lo conosce mai.
    // Uscire resta possibile, ma come atto esplicito: un browser_open sul nuovo
    // indirizzo, che ripassa dalla validazione e sposta il perimetro.
    private val scopeDomain = AtomicReference<String?>(null)

    // Vero mentre un browser_open sta caricando. Il perimetro non vale sulla
    // catena di redirect di un'apertura esplicita: chiedere una pagina
    // significa accettare dove quella pagina dice di andare per mostrarsi.
    // Misurato il 29/08: senza questo, la pagina di accesso di Wikipedia non si
    // apre — rimbalza su auth.wikimedia.org, e il recinto appena piantato la
    // ferma. Il guardiano sugli indirizzi privati invece resta attivo sempre:
    // quello non e' una preferenza sull'ambito, e' il confine della rete di casa.
    private val openInFlight = AtomicBoolean(false)

    // R.raw.browser_agent e non getIdentifier("browser_agent"): la build di
    // release offusca i nomi delle risorse (nell'APK il file diventa `res/XX.js`),
    // quindi cercarlo per nome a runtime funziona in debug e fallisce dove conta.
    // La costante e risolta a compile time e sopravvive all'offuscamento.
    private val agentJs: String by lazy {
        appContext.resources.openRawResource(R.raw.browser_agent)
            .bufferedReader().use { it.readText() }
    }

    // La guardia di rete lato pagina: v. installNetworkGuardOnMain. Stesso
    // motivo di R.raw qui sopra.
    private val networkGuardJs: String by lazy {
        appContext.resources.openRawResource(R.raw.browser_network_guard)
            .bufferedReader().use { it.readText() }
    }

    /** Dominio registrabile, per confronto di perimetro. Euristica, v. sopra. */
    private fun registrable(host: String): String {
        val labels = host.lowercase().trimEnd('.').split('.')
        if (labels.size <= 2) return labels.joinToString(".")
        val lastTwo = labels.takeLast(2).joinToString(".")
        val take = if (lastTwo in TWO_LEVEL_SUFFIXES) 3 else 2
        return labels.takeLast(take).joinToString(".")
    }

    // ------------------------------------------------------------------ guardia

    /**
     * L'IPv4 che un indirizzo IPv6 porta dentro, nelle forme che arrivano a un
     * IPv4 vero: IPv4-mapped (`::ffff:a.b.c.d`), NAT64 (`64:ff9b::a.b.c.d`,
     * che un DNS64 restituisce per un nome solo IPv4) e 6to4 (`2002:AABB:CCDD::`,
     * l'IPv4 nei due gruppi dopo il prefisso). Per ognuna il verdetto e' quello
     * dell'IPv4 incapsulato: `64:ff9b::7f00:1` e' il loopback, e `2002:c0a8:101::`
     * la LAN. `null` se l'indirizzo non ne porta.
     */
    private fun embeddedIpv4(b: ByteArray): ByteArray? {
        fun zero(range: IntRange) = range.all { b[it] == 0.toByte() }
        return when {
            zero(0..9) && b[10] == 0xFF.toByte() && b[11] == 0xFF.toByte() -> b.copyOfRange(12, 16)
            b[0] == 0x00.toByte() && b[1] == 0x64.toByte() && b[2] == 0xFF.toByte() &&
                b[3] == 0x9B.toByte() && zero(4..11) -> b.copyOfRange(12, 16)
            b[0] == 0x20.toByte() && b[1] == 0x02.toByte() -> b.copyOfRange(2, 6)
            else -> null
        }
    }

    private fun isBlockedAddress(addr: InetAddress): Boolean {
        if (addr.isLoopbackAddress || addr.isLinkLocalAddress ||
            addr.isSiteLocalAddress || addr.isAnyLocalAddress || addr.isMulticastAddress) return true
        when (addr) {
            is Inet4Address -> {
                val b = addr.address
                val v = ((b[0].toInt() and 0xFF) shl 24) or ((b[1].toInt() and 0xFF) shl 16) or
                    ((b[2].toInt() and 0xFF) shl 8) or (b[3].toInt() and 0xFF)
                for ((net, bits) in BLOCKED_V4) {
                    val n = InetAddress.getByName(net).address
                    val nv = ((n[0].toInt() and 0xFF) shl 24) or ((n[1].toInt() and 0xFF) shl 16) or
                        ((n[2].toInt() and 0xFF) shl 8) or (n[3].toInt() and 0xFF)
                    val mask = if (bits == 0) 0 else (-1 shl (32 - bits))
                    if ((v and mask) == (nv and mask)) return true
                }
            }
            is Inet6Address -> {
                val b = addr.address
                // fc00::/7 (unique local) — fe80::/10 lo copre già isLinkLocalAddress
                if ((b[0].toInt() and 0xFE) == 0xFC) return true
                // ::/96: `::` e le IPv4-compatibili, deprecate. `::127.0.0.1` e'
                // il loopback in una forma che `isLoopbackAddress` non riconosce
                // (vale solo per `::1`); come in Python, l'intera /96 non e' mai
                // un server.
                if ((0..11).all { b[it] == 0.toByte() }) return true
                // ::ffff:0:0:0/96, la forma «tradotta» di SIIT (::ffff:0:a.b.c.d):
                // non e' una IPv4-mapped, e fuori da un traduttore non porta a
                // nessun server. Come in Python, bloccata intera.
                if ((0..7).all { b[it] == 0.toByte() } && b[8] == 0xFF.toByte() &&
                    b[9] == 0xFF.toByte() && b[10] == 0.toByte() && b[11] == 0.toByte()) return true
                // 64:ff9b:1::/48, il NAT64 di uso locale: il prefisso li' non ha
                // lunghezza fissa, quindi l'IPv4 dentro non si sa estrarre.
                if (b[0] == 0x00.toByte() && b[1] == 0x64.toByte() && b[2] == 0xFF.toByte() &&
                    b[3] == 0x9B.toByte() && b[4] == 0x00.toByte() && b[5] == 0x01.toByte()) return true
                embeddedIpv4(b)?.let { return isBlockedAddress(InetAddress.getByAddress(it)) }
            }
        }
        return false
    }

    /** Verdetto per hostname, con cache. Risolve: **mai dal main thread**. */
    private fun isBlockedHost(host: String): Boolean {
        cachedVerdict(host)?.let { return it }
        val verdict = try {
            InetAddress.getAllByName(host).any { isBlockedAddress(it) }
        } catch (e: Exception) {
            // Il nome lo sceglie la pagina: un avviso per ogni nome nuovo
            // sarebbe un log che la pagina scrive a piacere.
            warnThrottled("DNS failed (${e.javaClass.simpleName}): host blocked")
            true   // in dubbio si blocca
        }
        synchronized(hostVerdicts) { hostVerdicts[host] = verdict }
        return verdict
    }

    private fun cachedVerdict(host: String): Boolean? = synchronized(hostVerdicts) { hostVerdicts[host] }

    /**
     * Un `Log.w` al massimo ogni [WARN_INTERVAL_MS], con il numero di quelli
     * taciuti nel frattempo. Per le voci che una pagina visitata può far
     * ripetere quanto vuole: nomi irrisolvibili, connessioni dirette rifiutate.
     */
    private fun warnThrottled(message: String) {
        val now = System.currentTimeMillis()
        val last = lastWarnAt.get()
        if (now - last < WARN_INTERVAL_MS || !lastWarnAt.compareAndSet(last, now)) {
            suppressedWarnings.incrementAndGet()
            return
        }
        val skipped = suppressedWarnings.getAndSet(0)
        Log.w(TAG, if (skipped > 0) "$message (+$skipped similar suppressed)" else message)
    }

    /** Controllo sincrono, senza DNS: è tutto ciò che si può fare sul main thread. */
    private fun isBlockedLiteral(uri: Uri): Boolean {
        val host = uri.host ?: return true
        val scheme = (uri.scheme ?: "").lowercase()
        if (scheme != "http" && scheme != "https") return true
        if (!host.any { it.isDigit() || it == ':' }) return false   // non è un IP letterale
        return try { isBlockedAddress(InetAddress.getByName(host)) } catch (e: Exception) { false }
    }

    // ------------------------------------------------------------------ ciclo di vita

    private fun ensureWebViewOnMain() {
        if (webView != null) return
        val wv = HiddenWebView.create(appContext, TAG).apply {
            webViewClient = sessionClient()
        }
        // Incognito: cookie e storage separati dal barattolo globale che usa
        // web_fetch, e svuotati alla chiusura ([wipeProfileOnMain]). Verificato
        // supportato sul Titan 2 (WebView 143) il 29/08; dove non c'è, la
        // sessione resta sul profilo di default e lo diciamo a Python invece di
        // fingere isolamento.
        if (WebViewFeature.isFeatureSupported(WebViewFeature.MULTI_PROFILE)) {
            try {
                val store = ProfileStore.getInstance()
                discardLeftoverProfileOnMain(store)
                val p = store.getOrCreateProfile(profileName)
                WebViewCompat.setProfile(wv, p.name)
                profile = p
                guardServiceWorkersOnMain(p)
            } catch (e: Exception) {
                Log.e(TAG, "Browser profile not attached", e)
            }
        }
        installNetworkGuardOnMain(wv)
        webView = wv
    }

    /**
     * La domanda che la guardia lato pagina fa prima di aprire un WebSocket (o
     * un WebTransport): questo host sta dentro la rete del telefono? È lo
     * **stesso** verdetto, con la stessa cache, di `shouldInterceptRequest`.
     *
     * Visibile a ogni pagina che la sessione apre, ed è voluto: dice soltanto
     * «bloccato o irrisolvibile» di un nome che la pagina ha già in mano, cioè
     * quel che una `fetch` verso quel nome le direbbe comunque.
     *
     * Gira sul thread JavaBridge, e il JS della pagina aspetta la risposta
     * (il costruttore di `WebSocket` è sincrono): un DNS lento lì congelava la
     * pagina per tutta la sua durata. Il verdetto in cache risponde subito;
     * altrimenti il DNS va su [guardDns] e si aspetta al massimo
     * [GUARD_DNS_TIMEOUT_MS] — poi «bloccato», e la risoluzione arriva in
     * cache da sola. Coda piena: «bloccato» anche lì.
     */
    private inner class NetworkGuard {
        @JavascriptInterface
        fun blocked(host: String): Boolean {
            val h = host.trim().removePrefix("[").removeSuffix("]")
            if (h.isEmpty()) return true
            val verdict = cachedVerdict(h) ?: try {
                guardDns.submit(Callable { isBlockedHost(h) })
                    .get(GUARD_DNS_TIMEOUT_MS, TimeUnit.MILLISECONDS)
            } catch (e: Exception) {
                // Timeout, coda piena o interruzione: nel dubbio si blocca.
                true
            }
            if (verdict) warnThrottled("direct connection refused (WebSocket/WebTransport)")
            return verdict
        }
    }

    /**
     * Le connessioni che `shouldInterceptRequest` non vede: WebSocket,
     * WebTransport, WebRTC. Chromium le apre fuori dal percorso delle richieste
     * HTTP, quindi il filtro sugli indirizzi privati non le fermava — una
     * pagina visitata dall'agente poteva parlare coi servizi della LAN su
     * `ws://192.168.x.y`.
     *
     * Rimedio parziale e dichiarato come tale: uno script iniettato a inizio
     * documento (`res/raw/browser_network_guard.js`) avvolge i costruttori nei
     * frame della pagina. Non copre i Worker né un frame in cui lo script non
     * arriva — v. il commento in testa allo script, che elenca i limiti.
     * Senza `DOCUMENT_START_SCRIPT` la guardia non c'è, e lo si scrive nel log
     * invece di fingere.
     */
    private fun installNetworkGuardOnMain(wv: WebView) {
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
            Log.w(TAG, "No DOCUMENT_START_SCRIPT: WebSocket/WebRTC to the LAN stay unguarded")
            return
        }
        try {
            wv.addJavascriptInterface(NetworkGuard(), "JennyBrowserGuard")
            WebViewCompat.addDocumentStartJavaScript(wv, networkGuardJs, setOf("*"))
        } catch (e: Exception) {
            Log.e(TAG, "Browser network guard not installed", e)
        }
    }

    /**
     * Le richieste fatte da un service worker non passano dal [WebViewClient]
     * della WebView ma dal `ServiceWorkerClient` del profilo: senza questo, una
     * pagina che registra un worker aveva un secondo modo di arrivare in LAN,
     * via HTTP stavolta. Sul profilo **separato** della sessione, e solo lì: il
     * controller del profilo di default è quello della SPA e di `web_fetch`.
     * Senza profilo separato (MULTI_PROFILE assente o aggancio fallito) il buco
     * resta: è un limite noto e accettato, non un difetto da scoprire.
     */
    private fun guardServiceWorkersOnMain(p: Profile) {
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.SERVICE_WORKER_SHOULD_INTERCEPT_REQUEST)) {
            Log.w(TAG, "No SERVICE_WORKER_SHOULD_INTERCEPT_REQUEST: service worker requests unguarded")
            return
        }
        try {
            p.serviceWorkerController.setServiceWorkerClient(object : ServiceWorkerClient() {
                override fun shouldInterceptRequest(request: WebResourceRequest): WebResourceResponse? =
                    blockedResponseFor(request.url, isMainFrame = false)
            })
        } catch (e: Exception) {
            Log.e(TAG, "Browser service worker guard not installed", e)
        }
    }

    /**
     * Il verdetto sulle richieste HTTP, in un posto solo: la pagina
     * ([sessionClient]) e i suoi service worker ([guardServiceWorkersOnMain]).
     * `null` vuol dire «lascia passare». Gira su un thread di lavoro: qui il
     * DNS si può risolvere.
     */
    private fun blockedResponseFor(uri: Uri, isMainFrame: Boolean): WebResourceResponse? {
        val scheme = (uri.scheme ?: "").lowercase()
        if (scheme != "http" && scheme != "https") {
            return WebResourceResponse("text/plain", "utf-8", ByteArrayInputStream(ByteArray(0)))
        }
        val host = uri.host ?: return null
        if (isBlockedHost(host)) {
            // Una pagina può chiedere mille sotto-risorse private in un ciclo.
            warnThrottled("request to a private or unresolvable host blocked")
            if (isMainFrame) lastBlocked.set(uri.toString())
            return WebResourceResponse("text/plain", "utf-8", ByteArrayInputStream(ByteArray(0)))
        }
        return null
    }

    /**
     * Butta il profilo rimasto su disco da un processo precedente, **prima**
     * che questo processo lo carichi.
     *
     * È l'unico momento in cui `deleteProfile` può riuscire: la documentazione
     * di `ProfileStore` lo fa sollevare sia con WebView vive sul profilo, sia
     * con un profilo già caricato in memoria da `getOrCreateProfile` — cioè
     * sempre, dopo la prima sessione. Senza questo passaggio un processo
     * ucciso prima di `browser_close` (o un APK vecchio, dove la cancellazione
     * non riusciva mai) lascerebbe cookie e login alla sessione successiva.
     */
    private fun discardLeftoverProfileOnMain(store: ProfileStore) {
        if (!leftoverProfilesHandled.add(profileName)) return
        try {
            if (store.deleteProfile(profileName)) {
                Log.i(TAG, "Discarded browser profile left by a previous process")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Could not discard leftover browser profile", e)
        }
    }

    /**
     * Svuota il profilo della sessione: cookie e storage web (la cache HTTP
     * la svuota [close] dalla WebView, prima di distruggerla). Main thread,
     * **dopo** `destroy()` della WebView.
     *
     * Non basta `deleteProfile`: è `@UiThread` (chiamato dal thread di Python
     * solleva) e, anche sul main, rifiuta un profilo già caricato in memoria —
     * che dopo `getOrCreateProfile` lo è fino alla morte del processo. Il
     * `catch` muto che c'era prima inghiottiva proprio questo, e cookie e
     * login sopravvivevano a `browser_close`. Qui si svuota quello che il
     * profilo espone, e la cancellazione si tenta lo stesso: se una WebView
     * futura la permette, tanto meglio; se no lo si scrive nel log.
     */
    private fun wipeProfileOnMain(p: Profile) {
        try {
            p.cookieManager.removeAllCookies(null)
            p.cookieManager.flush()
            p.webStorage.deleteAllData()
        } catch (e: Exception) {
            Log.e(TAG, "Browser profile wipe failed", e)
        }
        try {
            ProfileStore.getInstance().deleteProfile(profileName)
        } catch (e: IllegalStateException) {
            // Il caso atteso nello stesso processo: il profilo resta caricato
            // (vuoto) e si butta dal disco all'avvio del prossimo processo.
            Log.i(TAG, "Browser profile stays loaded until process exit (wiped): ${e.message}")
        } catch (e: Exception) {
            Log.e(TAG, "Browser profile delete failed", e)
        }
    }

    private fun sessionClient(): WebViewClient = object : WebViewClient() {
        // Il renderer è condiviso da tutte le WebView dell'app: se muore e una
        // sola risponde false, cade il processo intero, gateway compreso. La
        // sessione del browser finisce qui; la prossima apertura ne fa una nuova.
        override fun onRenderProcessGone(view: WebView?, detail: RenderProcessGoneDetail?): Boolean {
            Log.w(TAG, "WebView renderer gone: the browser session ends")
            if (webView === view) webView = null
            view?.destroy()
            return true
        }

        override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
            val uri = request?.url ?: return false
            if (isBlockedLiteral(uri)) {
                Log.w(TAG, "navigazione bloccata (letterale): $uri")
                if (request.isForMainFrame) lastBlocked.set(uri.toString())
                return true
            }
            if (request.isForMainFrame && !openInFlight.get()) {
                val scope = scopeDomain.get()
                val host = uri.host
                if (scope != null && host != null && registrable(host) != scope) {
                    Log.w(TAG, "fuori perimetro ($scope): $uri")
                    lastBlocked.set("PERIMETRO|$scope|$uri")
                    return true
                }
            }
            return false
        }

        // Gira su un thread di lavoro: qui il DNS si può risolvere. Vede ogni
        // richiesta **HTTP** della pagina, main frame compreso — non le
        // connessioni WebSocket/WebTransport/WebRTC, che Chromium apre fuori da
        // questo percorso (le copre, in parte, installNetworkGuardOnMain), né
        // quelle dei service worker (guardServiceWorkersOnMain).
        override fun shouldInterceptRequest(
            view: WebView?, request: WebResourceRequest?
        ): WebResourceResponse? {
            val uri = request?.url ?: return null
            return blockedResponseFor(uri, request.isForMainFrame)
        }

        override fun onPageStarted(view: WebView?, url: String?, favicon: android.graphics.Bitmap?) {
            generation.incrementAndGet()
            loading.set(true)
            lastError.set(null)
        }

        override fun onPageFinished(view: WebView?, url: String?) {
            loading.set(false)
            lastFinishAt.set(System.currentTimeMillis())
        }

        override fun onReceivedError(
            view: WebView?, request: WebResourceRequest?, error: WebResourceError?
        ) {
            if (request?.isForMainFrame == true) {
                lastError.set("WebView error: ${error?.description ?: "unknown"}")
                loading.set(false)
                lastFinishAt.set(System.currentTimeMillis())
            }
        }
    }

    /**
     * Distrugge la sessione e ne svuota il profilo (cookie, storage, cache).
     *
     * Tutto il lavoro sulla WebView e sul profilo sta nel salto sul main: le
     * API di `ProfileStore` e `Profile` sono `@UiThread`. È l'unico salto
     * che chiede `runLate`: a tetto scaduto il blocco resta in coda e gira
     * appena il main si libera — la pulizia arriva in ritardo, non si perde.
     * Lo stato di questo lato (verdetti, recinto, avvisi) si butta subito
     * comunque.
     */
    fun close(): String {
        MainHop.call(10_000L, Unit, TAG, runLate = true) {
            val wv = webView
            webView = null
            val p = profile
            profile = null
            // La cache si svuota dalla WebView, quindi prima di distruggerla;
            // cookie e storage dal profilo, dopo: `deleteProfile` rifiuta un
            // profilo con WebView vive. Il `finally` tiene la distruzione
            // anche se uno dei due passi prima solleva: la WebView non e' piu'
            // in `webView`, e senza `destroy` resterebbe viva e irraggiungibile.
            try {
                wv?.stopLoading()
                if (p != null) wv?.clearCache(true)
            } finally {
                wv?.destroy()
            }
            if (p != null) wipeProfileOnMain(p)
        }
        synchronized(hostVerdicts) { hostVerdicts.clear() }
        scopeDomain.set(null)
        lastBlocked.set(null)
        return """{"ok":true}"""
    }

    /** Traduce un blocco della guardia in una frase per il modello. */
    private fun describeBlock(raw: String): String {
        if (raw.startsWith("PERIMETRO|")) {
            val parts = raw.split("|", limit = 3)
            return "navigation stopped: the session is open on ${parts[1]} and this " +
                "leads outside it (${parts.getOrElse(2) { "?" }}). If you really mean to go " +
                "there, call browser_open on that address: it is an explicit act and moves " +
                "the perimeter."
        }
        return "navigation refused: $raw is a private or local network address. " +
            "If a redirect brought you here, the starting site is pointing into the " +
            "phone's own network."
    }

    /** Restituisce e consuma l'ultimo blocco, per chi non passa da open(). */
    fun takeNotice(): String {
        val raw = lastBlocked.getAndSet(null) ?: return """{"notice":""}"""
        return """{"notice":${quote(describeBlock(raw))}}"""
    }

    /**
     * La WebView di questa sessione è agganciata al profilo separato? Lo chiede
     * ``browser_open`` (``browser.py``), dopo l'apertura, per avvisare il
     * modello quando i cookie sono quelli di ``web_fetch``.
     *
     * Legge l'aggancio, non il supporto: prima rendeva solo se la WebView
     * supporta `MULTI_PROFILE`, e diceva «isolata» anche quando l'aggancio in
     * [ensureWebViewOnMain] era fallito e la sessione stava sul profilo di
     * default. Prima che la WebView nasca, e dopo [close], è `false`.
     */
    fun isIsolated(): Boolean = profile != null

    // ------------------------------------------------------------------ attese

    /**
     * Aspetta la fine del caricamento **più una finestra di quiete**: senza, su
     * una pagina che si ridisegna da sola si fotografa uno stato a metà.
     */
    private fun awaitSettled(timeoutSeconds: Long): Boolean {
        val deadline = System.currentTimeMillis() + timeoutSeconds * 1000
        while (System.currentTimeMillis() < deadline) {
            if (!loading.get()) {
                val quiet = System.currentTimeMillis() - lastFinishAt.get()
                if (quiet >= SETTLE_QUIET_MS) return true
            }
            Thread.sleep(50)
        }
        return false
    }

    /**
     * Esegue [js] nella pagina e ne aspetta il valore.
     *
     * Lo stesso cancello di [open]. Qui il salto non passa da [MainHop] —
     * l'attesa finisce in una callback — quindi un tetto scaduto non toglie
     * il blocco dalla coda del main, che gira dopo, e il cancello è l'unica
     * cosa che lo ferma. Per uno snapshot
     * sarebbe solo lavoro buttato; per un `act` sarebbe un **click tardivo**,
     * eseguito dopo che il modello ha sentito «timeout» e magari mentre sta
     * già facendo altro. Il main prende il cancello prima di toccare la
     * pagina, questo thread lo chiude prima di rispondere «timeout»: uno dei
     * due soltanto.
     */
    private fun evaluate(js: String, timeoutSeconds: Long): String {
        val out = AtomicReference("")
        val done = CountDownLatch(1)
        val gate = AtomicInteger(GATE_OPEN)
        handler.post {
            try {
                val wv = webView
                if (wv == null || !gate.compareAndSet(GATE_OPEN, GATE_LOADING)) {
                    done.countDown()
                    return@post
                }
                wv.evaluateJavascript(js) { v -> out.set(v ?: ""); done.countDown() }
            } catch (e: Exception) {
                // Sul main thread un'eccezione senza `try` abbatte il processo.
                Log.e(TAG, "evaluateJavascript failed on the main thread", e)
                done.countDown()
            }
        }
        if (!done.await(timeoutSeconds, TimeUnit.SECONDS)) {
            if (gate.compareAndSet(GATE_OPEN, GATE_ABANDONED)) {
                return """{"error":"evaluateJavascript timed out after ${timeoutSeconds}s: the page was not touched"}"""
            }
            // Il cancello l'ha gia' preso il main: lo script e' partito, e un
            // `act` puo' aver cliccato. Dirlo, invece di un «timeout» che
            // suona come «non e' successo niente».
            return """{"error":"evaluateJavascript timed out after ${timeoutSeconds}s: the script started without answering, the page may have changed"}"""
        }
        return out.get()
    }

    private fun runAgent(argsJson: String, timeoutSeconds: Long): String =
        evaluate(agentJs.replace("__ARGS__", argsJson), timeoutSeconds)

    private fun currentUrlAndTitle(): Pair<String, String> =
        MainHop.call(5_000L, Pair("", ""), TAG) {
            Pair(webView?.url ?: "", webView?.title ?: "")
        }

    // ------------------------------------------------------------------ API

    /** Apre [url] e aspetta che la pagina si sia posata. */
    fun open(url: String, timeoutSeconds: Long = DEFAULT_TIMEOUT_SECONDS): String {
        val uri = Uri.parse(url)
        if (isBlockedLiteral(uri)) return """{"error":"address not allowed"}"""
        lastBlocked.set(null)
        // Un browser_open e' un atto esplicito: il perimetro si rifa' **dopo**,
        // sull'indirizzo dove la pagina si e' posata davvero. Fino ad allora il
        // recinto vecchio resta al suo posto: durante l'apertura lo sospende
        // `openInFlight`, e un'apertura che fallisce lascia la pagina di prima
        // (o una pagina d'errore), che deve restare recintata. Azzerarlo qui
        // lasciava, a ogni ritorno d'errore, la pagina precedente caricata e
        // senza perimetro.
        openInFlight.set(true)
        // Se la WebView non nasce (il costruttore solleva, per esempio mentre
        // Android aggiorna il provider WebView) o il main thread non risponde
        // entro il tetto, nessuna pagina e' partita: dirlo, invece di
        // aspettare un caricamento che non c'e' e rispondere "ok" con indirizzo
        // e titolo vuoti.
        //
        // Un tetto scaduto oggi toglie il blocco dalla coda (MainHop lo
        // abbandona, e se era gia' partito ne aspetta l'esito). Il cancello
        // resta lo stesso, perche' e' lui a dire **una volta**, per tutti e due
        // i thread, se il caricamento c'e': se un blocco girasse dopo il "non
        // e' partita", la pagina partirebbe senza che nessuno la aspetti o la
        // descriva. Il main lo prende prima di `loadUrl`, questo thread lo
        // chiude prima di dire di no. Chi arriva secondo si adegua.
        val gate = AtomicInteger(GATE_OPEN)
        val started = MainHop.call(10_000L, false, TAG) {
            // Gia' abbandonato: niente WebView da costruire per una pagina che non
            // partira'. Il `compareAndSet` qui sotto resta quello che decide;
            // questa riga risparmia solo il lavoro (visto con la sonda sul
            // telefono il 25/09: restava una WebView viva, `url=null`).
            if (gate.get() == GATE_ABANDONED) return@call false
            ensureWebViewOnMain()
            val wv = webView ?: return@call false
            if (!gate.compareAndSet(GATE_OPEN, GATE_LOADING)) return@call false
            loading.set(true)
            lastError.set(null)
            try {
                wv.loadUrl(url)
            } catch (e: Exception) {
                // Il cancello e' preso ma nessuna pagina parte: lo si scrive
                // nel cancello stesso, e si spegne `loading`, o l'attesa qui
                // sotto durerebbe il tetto intero per un caricamento che non c'e'.
                gate.set(GATE_FAILED)
                loading.set(false)
                throw e   // MainHop lo scrive nel log e vale `false`
            }
            true
        }
        // Un "no" che non riesce a chiudere il cancello vuol dire che il blocco
        // l'ha gia' preso. O la pagina sta partendo (il tetto e' scaduto a
        // meta' strada), e la si aspetta come le altre; o `loadUrl` ha sollevato
        // (GATE_FAILED), e allora l'attesa torna subito perche' `loading` e'
        // spento — il cancello si rilegge dopo, cosi' vale anche per un
        // `loadUrl` che solleva quando questo thread sta gia' aspettando.
        if (!started && gate.compareAndSet(GATE_OPEN, GATE_ABANDONED)) {
            openInFlight.set(false)
            return """{"error":"the browser did not open: the page never started loading"}"""
        }
        val settled = awaitSettled(timeoutSeconds)
        openInFlight.set(false)
        if (gate.get() == GATE_FAILED) {
            return """{"error":"the browser did not open: the page never started loading"}"""
        }
        lastBlocked.getAndSet(null)?.let {
            return """{"error":${quote(describeBlock(it))}}"""
        }
        lastError.get()?.let { return """{"error":${quote(it)}}""" }
        val (u, t) = currentUrlAndTitle()
        // Il recinto si pianta dove si e' finiti, non dove si era chiesto, e
        // solo qui: la pagina nuova si e' posata. Senza un host (lettura
        // scaduta) resta quello di prima, che sbaglia per eccesso di chiusura.
        Uri.parse(u).host?.let { scopeDomain.set(registrable(it)) }
        return """{"ok":true,"settled":$settled,"url":${quote(u)},"title":${quote(t)}}"""
    }

    fun snapshot(
        mode: String, filter: String, maxChars: Int,
        timeoutSeconds: Long = DEFAULT_TIMEOUT_SECONDS,
    ): String {
        // La versione e' quella del documento: sale quando la pagina cambia, non
        // quando la si guarda. Un ref resta buono finche' il suo elemento e'
        // ancora attaccato al documento in cui e' nato.
        val v = generation.get()
        val args = """{"op":"snapshot","mode":${quote(mode)},"filter":${quote(filter)},""" +
            """"maxChars":$maxChars,"version":$v}"""
        return runAgent(args, timeoutSeconds)
    }

    /**
     * Esegue i passi di [stepsJson]; se qualcosa ha navigato, aspetta la nuova
     * pagina prima di tornare, così lo snapshot successivo non descrive quella
     * vecchia.
     */
    fun act(stepsJson: String, timeoutSeconds: Long = DEFAULT_TIMEOUT_SECONDS): String {
        val before = generation.get()
        val raw = runAgent("""{"op":"act","steps":$stepsJson}""", timeoutSeconds)
        val deadline = System.currentTimeMillis() + 1200
        while (System.currentTimeMillis() < deadline && generation.get() == before) {
            Thread.sleep(50)
        }
        if (generation.get() != before) awaitSettled(timeoutSeconds)
        return raw
    }

    fun read(ref: String, maxChars: Int, timeoutSeconds: Long = DEFAULT_TIMEOUT_SECONDS): String {
        val args = """{"op":"read","ref":${quote(ref)},"maxChars":$maxChars}"""
        return runAgent(args, timeoutSeconds)
    }

    private fun quote(s: String): String = org.json.JSONObject.quote(s)
}
